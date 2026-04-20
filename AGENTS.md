# AGENTS.md

Guide for AI coding agents working on this repository. Follows the [agentsmd.org](https://agentsmd.org) convention.

## 1. Project Overview

`crack-tcaptcha` is a pure-HTTP, Python-based automated solver for Tencent's
T-Sec TCaptcha (TCaptcha 2.0, `turing.captcha.qcloud.com`). It supports four
challenge types (`slider`, `icon_click`, `word_click`, `image_select`) and
does **not** drive a real browser — it replays the official JavaScript
fingerprint / behavior collector (`TDC.js`) inside a Node.js + jsdom
subprocess and speaks the captcha HTTP protocol directly with Chrome-TLS
impersonation (via `scrapling` + `curl_cffi`).

See `docs/` for user-facing documentation and `docs/architecture.md` for the
layered architecture diagram.

## 2. Build / Test / Run Commands

Python >= 3.10, `uv` is the canonical package manager.

```bash
# Install deps (no extras)
uv sync

# Install with optional extras
uv sync --extra icon-click   # adds ddddocr + onnxruntime (needed for icon_click and word_click)
uv sync --extra dev          # pytest, respx, ruff, hypothesis
uv sync --extra docs         # mkdocs-material

# Node.js side (TDC.js bridge) — required the first time
cd src/crack_tcaptcha/tdc/js && npm install

# Tests
uv run pytest                             # full suite (offline)
uv run pytest -m "not network"            # default (network tests already marked)
uv run pytest tests/pipelines/ -q         # a single directory

# Lint / format
uv run ruff check .
uv run ruff format .

# CLI
uv run crack-tcaptcha solve --appid YOUR_APPID --entry-url https://your-site.example/login

# Docs
uv run mkdocs serve
```

## 3. Architecture Map

```
src/crack_tcaptcha/
├── __init__.py          # public API: solve()
├── captcha_type.py      # pure-function classifier (dyn_show_info → type)
├── cli.py               # argparse entry point
├── client.py            # HTTP three-phase + JSONP unwrap (scrapling / curl_cffi)
├── exceptions.py        # NetworkError, SolveError, PowError, TDCError
├── models.py            # pydantic models for prehandle / verify responses
├── pow.py               # MD5 PoW solver with calc_time shaping
├── settings.py          # pydantic-settings (TCAPTCHA_* env vars, .env)
├── trajectory.py        # slide/click trajectory generation
├── pipelines/
│   ├── _common.py       # run_async, finish_with_verify (shared tail)
│   ├── slide.py         # NCC template match
│   ├── icon_click.py    # ddddocr detect + template match
│   ├── word_click.py    # ddddocr detect + LLM vision (+ OCR fallback)
│   └── image_select.py  # LLM region matching
├── solvers/
│   └── llm_vision.py    # OpenAI-compatible vision client
└── tdc/
    ├── provider.py      # TDCProvider Protocol (DI point)
    ├── nodejs_jsdom.py  # Node.js subprocess implementation
    └── js/              # tdc_executor.js + vendored tdc.js
```

Dependency direction is strictly top-down: `pipelines/` depends on
`solvers/`, `tdc/`, `client.py`, `pow.py`, `trajectory.py`. `solvers/` and
`tdc/` are independent of each other and must not import from `pipelines/`.

## 4. Key Conventions

- **Type hints everywhere.** `from __future__ import annotations` at the top
  of every module. PEP 604 unions (`str | None`) are fine because Python 3.10+.
- **Config via pydantic-settings.** Don't read env vars directly; use
  `crack_tcaptcha.settings.settings`. New settings go in `settings.py` with
  a `TCAPTCHA_` prefix and sensible defaults.
- **Data models via pydantic v2.** Response shapes live in `models.py`; never
  pass raw dicts across module boundaries.
- **Logging over prints.** Use `log = logging.getLogger(__name__)` and
  log at INFO for pipeline milestones, DEBUG for inner workings, WARNING
  for recoverable failures. No `print()` in library code.
- **Exceptions.** Raise the typed exceptions in `exceptions.py`
  (`SolveError`, `NetworkError`, `PowError`, `TDCError`). `SolveError`
  specifically is the "this attempt failed, caller may retry" signal.
- **Line length 120** (ruff). `ruff.lint.select = ["E","F","I","UP","B","SIM"]`.
- **No new top-level deps without discussion.** Optional features go behind
  an `extras_require` group (see `pyproject.toml` `[project.optional-dependencies]`).

## 5. Gotchas

- **TLS fingerprint is mandatory.** Plain `httpx` / `requests` / `urllib`
  get `403` from `turing.captcha.qcloud.com`. `client.py` uses
  `scrapling.fetchers.Fetcher` (curl_cffi under the hood) to impersonate
  Chrome. Don't "simplify" this to `httpx`.
- **TDC.js runs in Node, not Python.** The jsdom window must have
  `pretendToBeVisual: true`, `runScripts: "dangerously"`, plus patches for
  `screen`, `innerWidth/Height`, `devicePixelRatio`, `navigator.webdriver`.
  Breaking any of these breaks `collect` / `eks`.
- **PoW calc_time matters.** Python `hashlib` is too fast; reporting a
  sub-200 ms `pow_calc_time` gets flagged. Use `solve_pow(prefix, md5,
  min_ms=300, max_ms=500)` (the default every pipeline uses) to shape the
  reported time.
- **`entry_url` sets Referer/Origin.** Passing it is strongly recommended
  when integrating into a real site; omitting it still works but is more
  likely to get soft-blocked.
- **Classifier rule order is load-bearing.** `word_click` must be checked
  before `icon_click` because both set `DynAnswerType_POS`; the
  distinguishing signal is presence/absence of `fg_elem_list`.
- **JSONP unwrap required.** All prehandle responses are wrapped
  `_aq_000001({...})` — use `client.parse_jsonp`, not raw `json.loads`.
- **`word_click` answer format.** `elem_id` 1..N by instruction order,
  `DynAnswerType_POS`, `data="x,y"`. `image_select` is different:
  `DynAnswerType_UC`, `elem_id=""`, `data="<region_id>"`.
- **Trajectory jitter.** Ease-in-out cubic with ±1 px jitter currently
  passes. Perfectly smooth trajectories get detected.
- **LLM retry semantics.** `locate_chars` / `match_region` each retry once
  internally on transport errors. Outer retries are the pipeline's
  `max_retries` (entire prehandle → verify loop).

## 6. Testing Guidelines

- Tests live in `tests/`, mirroring `src/` layout (`tests/pipelines/`,
  `tests/solvers/`, etc.). Pytest is configured in `pyproject.toml`.
- Network tests are marked `@pytest.mark.network` and opt-in:
  `pytest -m network`. Default run excludes them via `-m "not network"`
  (the CI convention — check `pyproject.toml`).
- **Mock HTTP with `respx`, not hand-rolled stubs.** Real captcha endpoint
  responses are captured into fixtures under `tests/fixtures/`.
- **Never mock the database / TDC in integration tests.** If you're
  exercising `tdc/nodejs_jsdom.py`, let the subprocess actually run —
  that's the point of the test.
- Use `hypothesis` for solvers where input shape varies (NCC, trajectory
  generation).
- Async tests: `pytest-asyncio` in `asyncio_mode = "auto"` (set in
  `pyproject.toml`), so `async def test_...` functions just work.

## 7. Do / Don't

**Do**

- Add new captcha types as a new file under `pipelines/` plus a rule in
  `captcha_type.py` (see section 9 for the full recipe).
- Add new TDC bridges (e.g. Puppeteer) as a new file under `tdc/`
  implementing the `TDCProvider` protocol.
- Funnel PoW + trajectory + TDC collect + verify through
  `pipelines/_common.finish_with_verify`. It's the single "tail" every
  pipeline shares.
- Respect `TCAPTCHA_PROXY` / `settings.proxy` when adding new HTTP calls.

**Don't**

- Don't introduce Selenium, Playwright, undetected-chromedriver, or any
  real browser. The project's whole point is no-browser operation.
- Don't call `json.loads` on prehandle bodies directly — use `parse_jsonp`.
- Don't swallow exceptions into bare strings; use the typed exceptions.
- Don't hardcode captcha endpoint URLs; use `settings.base_url`.
- Don't put reusable logic inside a pipeline file — extract to
  `pipelines/_common.py`, `solvers/`, `trajectory.py`, or `client.py`.
- Don't commit `.env` or real API keys. `.env.example` shows the shape.

## 8. External Dependencies

- **Node.js >= 18** for the TDC.js bridge (`tdc/js/tdc_executor.js`,
  runs `tdc.js` inside jsdom). Install deps with `cd src/crack_tcaptcha/tdc/js && npm install`.
- **`ddddocr`** (optional extra `icon-click`) for icon/character
  detection. Required by `icon_click` and `word_click` pipelines. Pulls
  in `onnxruntime`.
- **OpenAI-compatible LLM relay** for `word_click` (recommended) and
  `image_select` (required). Configure via `TCAPTCHA_LLM_API_KEY`,
  `TCAPTCHA_LLM_BASE_URL`, `TCAPTCHA_LLM_MODEL`, `TCAPTCHA_LLM_TIMEOUT`
  in `.env`. Any `/v1/chat/completions` endpoint that accepts
  `image_url` content blocks works.
- **`scrapling`** (required) for Chrome-TLS HTTP; do not replace with
  plain `httpx`.

## 9. How to Add a New Captcha Type

1. **Observe.** Dump `dyn_show_info` from a real prehandle response for
   the new challenge and note distinguishing fields.
2. **Add a classifier rule** in `src/crack_tcaptcha/captcha_type.py`:
   - Write a `_is_<new_type>(dyn) -> bool` predicate
   - Insert a `_TypeRule` in the `_RULES` tuple at the correct priority
     (earlier rules win; remember the `word_click` vs `icon_click` order
     lesson)
   - Add the new type name to `CAPTCHA_TYPES`
3. **Write the pipeline** at `src/crack_tcaptcha/pipelines/<new_type>.py`:
   - Export `solve_one_attempt(client, pre, tdc_provider) -> VerifyResp`
   - Build `ans_json`, `pow_answer`, `trajectory`; then call
     `pipelines._common.finish_with_verify(...)` for the shared tail
   - Raise `SolveError` on recoverable failures; let unexpected
     exceptions bubble
4. **Register in dispatch.** Add the new type → pipeline mapping in
   `pipelines/__init__.py` (`dispatch` function).
5. **Add / extend a solver** under `solvers/` if the new type needs a
   novel solving strategy (don't inline non-trivial algorithms in the
   pipeline file).
6. **Tests.** Add a fixture-based test under `tests/pipelines/` using
   `respx` to stub HTTP and a recorded prehandle JSON.
7. **Docs.** Add `docs/<new-type>.md` and register it in `mkdocs.yml`
   under "验证码类型"; update `docs/index.md` and `docs/architecture.md`
   pipeline ↔ solver table.
