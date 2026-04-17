# Spec — Captcha Type Router + image_select Pipeline

**Date**: 2026-04-17
**Owner**: lifefloating
**Status**: Draft for review
**Reference project**: `/Users/gehonglu/remote-code/flashTCaptcha` (specs A/B/C)

## 1. Problem

`uv run crack-tcaptcha solve --appid 2046626712` currently fails with 3 retried attempts of `SolveError: No fg_elem_list in prehandle response`. Root causes identified by comparing to flashTCaptcha:

1. **Endpoint is wrong**. `settings.base_url = "https://t.captcha.qq.com"` is the legacy domain. The modern Tencent captcha API lives at `https://turing.captcha.qcloud.com`. flashTCaptcha and the reverse-engineering paper both use this host, and the old host causes Tencent to route requests to an older behavior surface that, among other quirks, hands out `click_image_uncheck` challenges the current codebase has no handler for.

2. **No type routing**. `slider/pipeline.py` unconditionally assumes the response shape is slide (requires `fg_elem_list`). The log shows Tencent returned `show_type=click_image_uncheck`, `data_type=['DynAnswerType_UC']`, `fg_elems=0` — this is a 6-grid **image_select** captcha, a type the codebase does not handle at all. The code walks into the slide branch, finds no `fg_elem_list`, and blows up.

3. **Dead foreground image requests**. For image_select Tencent only serves the background image (`img_index=1`). The code still calls `get_image(fg_url)` and gets `HTTP 200, 0 bytes`, which silently flows into solvers before surfacing as a confusing downstream error.

4. **Legacy code rot**. `icon_click/_legacy_solver.py` and the split `slider/` + `icon_click/` package layout predate any type-routing plan. Adding `image_select` on top with if/else branches compounds the debt.

## 2. Goal

Fix the immediate bug (`appid=2046626712` solves end-to-end for image_select) and reshape the code so adding shape_click / silent later is a one-file drop-in:

- Migrate base URL to `turing.captcha.qcloud.com` across prehandle, image download (referer), and verify.
- Introduce a **pure-function captcha type classifier** (`captcha_type.py`) that turns a parsed `dyn_show_info` into one of `{slide, icon_click, image_select, unknown}`.
- Introduce a **pipeline registry** (`pipelines/__init__.py`) keyed by the classifier's output. Each pipeline is its own module.
- Port the two working pipelines (slide, icon_click) into the new layout and **delete the old `slider/` and `icon_click/` packages** (including `_legacy_solver.py`).
- Ship a new **image_select pipeline** that calls GPT-5.4 via an OpenAI-compatible relay to pick the matching region, builds a `DynAnswerType_UC` ans envelope, and completes verify.
- Load LLM credentials from `.env` via pydantic-settings. Ship `.env.example`.
- Keep CLI unchanged: `uv run crack-tcaptcha solve --appid X` auto-classifies and dispatches.

After this spec, `solve --appid 2046626712` returns a ticket, and `solve --appid <any slide appid>` still works.

## 3. Non-goals

- shape_click, silent, spatial_vtt pipelines. The classifier returns `unknown` for them; dispatch raises `UnsupportedCaptchaType`. They are the next iteration's concern.
- Reworking TDC, PoW, or XTEA layers. The current `tdc/` stack is untouched. If TDC breaks under the new endpoint, that's a separate fix.
- Writing tests for the new flow. Explicitly deferred by the user. Existing tests in `tests/` get their imports patched but are not expanded.
- Local Chinese-CLIP fallback for image_select. GPT-5.4 via relay is the sole path this iteration.
- Retry tuning beyond "each attempt reruns prehandle". No `new_sess` reuse, no backoff, no type-specific retry policies.
- Artifacts dumping / harvest tooling. Add later when we debug failures.

## 4. Evidence

### 4.1 Real failing response (2046626712, user log + flashTCaptcha `artifacts/harvest/user_failing_2046626712_2046626712_1.json`)

```
prehandle dyn_show_info keys=[lang, instruction, bg_elem_cfg,
  verify_trigger_cfg, color_scheme, json_payload, watermark, show_type]
instruction="蓝色的蝴蝶"         # wrapped in U+201C / U+201D smart quotes
show_type=click_image_uncheck
bg_elem_cfg.click_cfg={"mark_style": "mask", "data_type": ["DynAnswerType_UC"]}
json_payload.select_region_list[0]={"id": 1, "range": [0, 34, 220, 254]}
n_regions=6
```

- `show_type=click_image_uncheck` and `data_type=[DynAnswerType_UC]` are the strong image_select discriminators.
- 6 regions in `json_payload.select_region_list`, each with `id: 1..6` and `range: [x1, y1, x2, y2]`.
- `select_region_list` entries carry **no `elem_id` field** — only `id`, `range`. The ans envelope therefore emits `elem_id: ""`.
- `fg_elems=0` — no piece list, no sprite URL.
- Image download log: `img_index=1` returned 56602 bytes; `img_index=0` returned **0 bytes**. For image_select we must not request fg at all.

### 4.1a Full type cross-reference (flashTCaptcha `artifacts/harvest/compare.json`)

| Type          | show_type            | instruction                | click_cfg.data_type | fg_binding_list | sprite_url | ins_elem_cfg |
|---------------|----------------------|----------------------------|---------------------|-----------------|------------|--------------|
| slide (real)  | —                    | "拖动下方滑块完成拼图"       | —                   | **present**     | present    | —            |
| icon_click    | —                    | "请依次点击：尝 稠 车 "      | `[POS]`             | —               | —          | —            |
| shape_click   | —                    | "请依次点击："               | `[POS]`             | —               | present    | **present**  |
| silent        | —                    | null                        | —                   | —               | —          | —            |
| spatial_vtt   | —                    | null (dyn empty)            | —                   | —               | —          | —            |
| image_select  | **click_image_uncheck** | "蓝色的蝴蝶"              | `[UC]`              | —               | —          | —            |

Observation that forced the classifier rewrite: `subsid=3` in the harvest is **labeled** slide but actually returned icon_click (instruction `"请依次点击：趁 乘 呈 "`). Real slide came from the `default_preload` (subsid=1) sample. Conclusion: **subsid does not deterministically pick type**; the classifier must work off response structure alone, and `fg_binding_list` — not `fg_elem_list` — is the exclusive slide marker.

### 4.2 Endpoint evidence from flashTCaptcha

```
flashtcaptcha/net/prehandle.py:37  PREHANDLE_URL = "https://t.captcha.qq.com/cap_union_prehandle"
flashtcaptcha/net/verify.py:32     VERIFY_URL    = "https://turing.captcha.qcloud.com/cap_union_new_verify"
flashtcaptcha/net/get_seed.py:22   CAPTCHA_ORIGIN = "https://turing.captcha.qcloud.com"
flashtcaptcha/tdc/loader.py:23     CAPTCHA_ORIGIN = "https://turing.captcha.qcloud.com"
```

flashTCaptcha verify + image fetch + tdc loader all use `turing.captcha.qcloud.com`. Its prehandle URL is still `t.captcha.qq.com` (this host also works for prehandle specifically), but image and verify paths consistently use `turing`. We standardize on `turing.captcha.qcloud.com` for the whole pipeline — it's the domain the captcha iframe script issues sub-requests against. Referer for image fetches becomes `https://turing.captcha.qcloud.com/`.

### 4.3 Universal ans envelope

From `crackTCaptcha/src/crack_tcaptcha/client.py:249-257` and flashTCaptcha Spec B §4.2:

```json
[{"elem_id": <id>, "type": "DynAnswerType_POS" | "DynAnswerType_UC", "data": "<string>"}, ...]
```

- `DynAnswerType_POS` — data = `"<x>,<y>"` (integer source-image coords). Used by slide (1 entry) and icon_click (N entries).
- `DynAnswerType_UC` — data = `"<region_id>"`. Used by image_select (1 entry).

Verify POST body fields (`ans`, `sess`, `pow_answer`, `pow_calc_time`, `collect`, `tlg`, `eks`) are unchanged.

### 4.4 flashTCaptcha retry model

`flashtcaptcha/orchestrator.py:318-346`: `for attempt in range(retries + 1)` re-runs prehandle every attempt. `new_sess` returned in a failed verify is only logged, never reused. Non-retryable TDC errors break the loop early. This matches the current crackTCaptcha shape; we keep it.

## 5. Design

### 5.1 Module layout

```
src/crack_tcaptcha/
  captcha_type.py        NEW — pure classifier
  client.py              MODIFIED — base_url, referer, empty-body guard
  settings.py            MODIFIED — new base_url default; LLM_* fields
  models.py              MODIFIED — add Classification dataclass
  exceptions.py          MODIFIED — add UnsupportedCaptchaType
  cli.py                 MODIFIED — solve command → pipelines.dispatch
  pow.py                 unchanged
  trajectory.py          unchanged
  tdc/                   unchanged
  pipelines/             NEW
    __init__.py            REGISTRY + dispatch()
    _common.py             run_with_retry(), build_verify_inputs()
    slide.py               ported from slider/pipeline.py + solver.py
    icon_click.py          ported from icon_click/pipeline.py + solver.py
    image_select.py        NEW — GPT-5.4 vision solver
  solvers/               NEW
    __init__.py
    llm_vision.py          OpenAI-compatible client for GPT-5.4 relay
  slider/                DELETED (contents moved to pipelines/slide.py)
  icon_click/            DELETED (contents moved to pipelines/icon_click.py;
                                   _legacy_solver.py removed)
```

### 5.2 Data flow

```
cli.solve(appid, ...)
  → pipelines.dispatch(appid, tdc_provider, max_retries)
      for attempt in 1..max_retries:
          client = TCaptchaClient()
          pre = client.prehandle(appid, subsid=1)
          cls = captcha_type.classify(pre.raw["data"]["dyn_show_info"])
          if cls.captcha_type == "unknown":
              raise UnsupportedCaptchaType(cls, dyn_keys)
          pipeline = REGISTRY[cls.captcha_type]
          verify_resp = pipeline.solve_one_attempt(client, pre, tdc_provider)
          if verify_resp.ok:
              return SolveResult(ok=True, ...)
      return SolveResult(ok=False, ...)
```

Key properties:
- Each pipeline module exports `solve_one_attempt(client, pre, tdc_provider) -> VerifyResp`. No retry loop inside the pipeline — that lives in `_common.run_with_retry`.
- `pipelines.dispatch` is the only symbol `cli.py` imports. The registry lookup is the only routing decision.
- `UnsupportedCaptchaType` short-circuits the retry loop (breaking, not retrying) because rerunning prehandle with the same appid + subsid=1 will return the same type.

### 5.3 Captcha type classifier

`captcha_type.py`:

```python
from dataclasses import dataclass
from typing import Any, Callable

CAPTCHA_TYPES = ("slide", "icon_click", "image_select", "unknown")

@dataclass(frozen=True, slots=True)
class Classification:
    captcha_type: str
    matched_rule: str

@dataclass(frozen=True, slots=True)
class _TypeRule:
    name: str
    captcha_type: str
    predicate: Callable[[dict[str, Any]], bool]

def classify(dyn: dict[str, Any]) -> Classification: ...
```

Rules evaluated in order; first match wins:

| # | name | predicate | → type |
|---|---|---|---|
| 1 | `image_select_show_type` | `dyn.get("show_type") == "click_image_uncheck"` | image_select |
| 2 | `image_select_uc` | `"DynAnswerType_UC" in dyn.get("bg_elem_cfg", {}).get("click_cfg", {}).get("data_type", [])` | image_select |
| 3 | `slide_fg_binding` | `"fg_binding_list" in dyn` | slide |
| 4 | `icon_click_pos` | `"DynAnswerType_POS" in click_cfg.data_type` AND `"ins_elem_cfg" not in dyn` AND `instruction.startswith("请依次点击")` AND post-colon text is non-empty | icon_click |
| — | `fallback_unknown` | always | unknown |

`fg_binding_list` is the **sole** slide discriminator. Verified against `artifacts/harvest/resp_default_199999861.jsonp` (real slide): `dyn_keys = [bg_elem_cfg, color_scheme, fg_binding_list, fg_elem_list, instruction, lang, sprite_url]`, no `click_cfg`. No other type in the 7-sample harvest carries `fg_binding_list`.

Icon_click rule (rule 4) reference implementation:

```python
def _is_icon_click(dyn):
    click = dyn.get("bg_elem_cfg", {}).get("click_cfg", {})
    if "DynAnswerType_POS" not in click.get("data_type", []):
        return False
    # Exclude shape_click: it carries ins_elem_cfg and an empty-after-colon instruction.
    if "ins_elem_cfg" in dyn:
        return False
    instr = dyn.get("instruction", "")
    if not instr.startswith("请依次点击"):
        return False
    after = instr.split("：", 1)[1] if "：" in instr else ""
    return bool(after.strip())
```

Rule ordering rationale:
- Rule 1 uses the strongest signal when present. Tencent populates `show_type` only for image_select in our observations.
- Rule 2 is the backstop for image_select responses that omit `show_type`. Safe because UC type is exclusive to image_select.
- Rule 3 catches slide via the exclusive `fg_binding_list` key.
- Rule 4 guards against shape_click (which also has `POS` but empty post-colon instruction and carries `ins_elem_cfg`). Both the `ins_elem_cfg` absence and the non-empty-after-colon check are required — either alone is insufficient, since evidence shows shape_click's instruction is literally `"请依次点击："` (same prefix, empty tail).

Output logged at INFO: `classified type=<t> rule=<r> instruction=<i>`. Unknown logged at WARNING with `dyn_keys=<list>` so future rules can be written without rerunning the appid.

### 5.4 image_select pipeline

`pipelines/image_select.py`:

```python
def solve_one_attempt(client, pre, tdc_provider) -> VerifyResp:
    # 1. fetch bg only — no fg for image_select
    bg_bytes = client.get_image(pre.bg_elem_cfg.img_url)

    # 2. LLM picks region
    region_id = llm_vision.match_region(
        bg_bytes,
        instruction=pre.instruction,
        regions=pre.select_regions,
        bg_size=(pre.bg_elem_cfg.width, pre.bg_elem_cfg.height),
    )

    # 3. ans envelope. elem_id is fixed "" for image_select — the harvest
    # sample's json_payload carries no elem_id field; only per-region id.
    ans = json.dumps([{"elem_id": "", "type": "DynAnswerType_UC",
                       "data": str(region_id)}])

    # 4. PoW
    pow_answer, pow_calc_time = solve_pow(pre.pow_cfg.prefix, pre.pow_cfg.target_md5)

    # 5. trajectory: single click at region center
    region = next(r for r in pre.select_regions if r.id == region_id)
    cx, cy = (region.range[0] + region.range[2]) // 2, (region.range[1] + region.range[3]) // 2
    traj = generate_click_trajectory(cx, cy)

    # 6. TDC + verify via _common
    return _common.finish_with_verify(client, pre, tdc_provider, ans,
                                      pow_answer, pow_calc_time, traj)
```

**LLM client** (`solvers/llm_vision.py`):

```python
def match_region(
    bg_bytes: bytes,
    *,
    instruction: str,
    regions: list[SelectRegion],
    bg_size: tuple[int, int],
) -> int: ...
```

Implementation notes:
- Strip instruction smart quotes and whitespace before building the prompt:
  `instruction.strip().strip("\u201c\u201d\"'")`. Harvest shows Tencent wraps
  the target phrase in U+201C/U+201D (e.g. `"蓝色的蝴蝶"`) — passing those
  through hurts recognition.
- Uses `httpx.Client` (sync) against `{TCAPTCHA_LLM_BASE_URL}/v1/chat/completions` with `Authorization: Bearer {TCAPTCHA_LLM_API_KEY}`.
- Model = `TCAPTCHA_LLM_MODEL` (default `"gpt-5.4"`).
- Single user message with the instruction text + a content part of type `image_url` containing `data:image/jpeg;base64,<b64>` of the bg.
- System prompt encodes the region grid: "图像尺寸 {W}×{H}，被分为 6 个区域，id=1..6，坐标分别为…" followed by each region's `(x1,y1,x2,y2)`. Answer format: return a JSON object `{"region_id": N}` with N in 1..6, nothing else. `response_format={"type":"json_object"}` when the relay supports it, else regex extract the first integer 1..6 in the response.
- Timeout from `settings.llm_timeout` (default 30s).
- One internal retry on network error; JSON parse failure raises `SolveError`. Outer attempt loop retries.

### 5.5 Common helper (`pipelines/_common.py`)

Extracts shared steps from the 3 pipeline modules:

```python
def run_with_retry(appid: str, tdc_provider: TDCProvider, max_retries: int,
                   client: TCaptchaClient | None = None) -> SolveResult:
    """Top-level loop: prehandle → classify → dispatch → verify, N times."""

def finish_with_verify(client, pre, tdc_provider, ans_json, pow_answer,
                       pow_calc_time, traj) -> VerifyResp:
    """Shared TDC collect + verify POST. Pipeline-agnostic."""
```

`finish_with_verify` handles:
- Resolving `tdc_path` to absolute URL using `turing.captcha.qcloud.com`.
- Calling `tdc_provider.collect(...)` async.
- POSTing verify with `ans/sess/pow_answer/pow_calc_time/collect/tlg/eks`.

### 5.6 Endpoint migration

`settings.py`:

```python
base_url: str = "https://turing.captcha.qcloud.com"
llm_api_key: str = ""
llm_base_url: str = ""
llm_model: str = "gpt-5.4"
llm_timeout: float = 30.0
```

`client.py` changes:
- `get_image`: referer → `https://turing.captcha.qcloud.com/`. If URL is absolute keep it; else prefix `settings.base_url`.
- `get_image`: if `len(resp.body) == 0` raise `NetworkError("empty image body")`.
- `get_fg_image_url`: unchanged (still useful for slide).
- `_BASE` module-level constant removed; reference `settings.base_url` each call (since tests may override).

### 5.7 .env support

`pydantic-settings` already active (via `BaseSettings` with `env_prefix="TCAPTCHA_"`). Add `.env` loading:

```python
class TCaptchaSettings(BaseSettings):
    model_config = {
        "env_prefix": "TCAPTCHA_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }
```

`.env.example` at repo root:

```
TCAPTCHA_BASE_URL=https://turing.captcha.qcloud.com
TCAPTCHA_LLM_API_KEY=sk-xxx
TCAPTCHA_LLM_BASE_URL=https://your-relay.example.com
TCAPTCHA_LLM_MODEL=gpt-5.4
TCAPTCHA_LLM_TIMEOUT=30
```

`.env` stays in `.gitignore` (already excluded).

### 5.8 Exceptions

`exceptions.py` adds:

```python
class UnsupportedCaptchaType(TCaptchaError):
    def __init__(self, captcha_type: str, dyn_keys: list[str]):
        super().__init__(
            f"unsupported captcha type {captcha_type!r}; dyn keys={dyn_keys}"
        )
        self.captcha_type = captcha_type
        self.dyn_keys = dyn_keys
```

`SolveError` and `NetworkError` stay. `pipelines.dispatch` treats `UnsupportedCaptchaType` as terminal (no retry); everything else retries per `max_retries`.

### 5.9 CLI

`cli.py::solve` changes:
- Import `from crack_tcaptcha.pipelines import dispatch`.
- Call `dispatch(appid, tdc_provider=provider, max_retries=settings.max_retries)`.
- Remove the current "guess slider vs icon_click" logic (there isn't much today, but any remnants go).
- Exit code: 0 on `result.ok`, 1 otherwise.
- Print final ticket + randstr or final error.

### 5.10 Deletions

After ports:
- `src/crack_tcaptcha/slider/__init__.py`, `pipeline.py`, `solver.py` → removed.
- `src/crack_tcaptcha/icon_click/__init__.py`, `pipeline.py`, `solver.py`, `_legacy_solver.py` → removed.
- `__pycache__` directories cleaned.

Tests in `tests/test_pipeline_slider.py`, `test_slider_solver.py`: update imports to `crack_tcaptcha.pipelines.slide`. If the solver function signature shifts (it shouldn't), fix the test. No new tests added.

## 6. Error handling summary

| Failure | Where surfaced | Retry? |
|---|---|---|
| prehandle HTTP != 200 | `client.prehandle` → `NetworkError` | yes |
| prehandle JSONP parse fail | `client.prehandle` | yes |
| classify → unknown | `dispatch` → `UnsupportedCaptchaType` | **no** (break) |
| image download 0 bytes | `client.get_image` → `NetworkError` | yes |
| LLM JSON parse fail | `llm_vision.match_region` → `SolveError` | yes |
| LLM HTTP fail | `llm_vision` → `SolveError` after 1 internal retry | yes |
| PoW timeout | `pow.solve_pow` | yes |
| TDC collect fail | `tdc_provider.collect` | yes (unless TDC says non-retryable) |
| verify `errorCode != 0` | returned `VerifyResp.ok = False` | yes |

All retryable errors drain `max_retries` before returning `SolveResult(ok=False, error=...)`.

## 7. Risks

- **LLM answer format drift**. GPT-5.4 might return narrative text despite the JSON instruction. Mitigation: regex fallback extracts first `1..6` integer. If both fail, SolveError → retry. Worst case: fail all retries, user sees clear LLM error in logs.
- **Endpoint regression**. Some appids might still work only under `t.captcha.qq.com`. Mitigation: if reported, make `TCAPTCHA_BASE_URL` env override do the job — no code change needed.
- **Referer change breaks image fetch**. If `turing.captcha.qcloud.com` referer blocks image fetch for some reason, revert to `captcha.gtimg.com` (current value) via a single line. Evidence says it works; we'll confirm on first live run.
- **`elem_id` assumption for image_select**. Harvest shows `json_payload` carries `select_region_list` (per-region `id`) and `prompt_id`, but no top-level `elem_id`. We emit `elem_id: ""` in the ans envelope. If Tencent later changes this, the failure mode is a predictable verify errorCode and we adjust.
- **`sprite_pos` field unused**. bg_elem_cfg carries a `sprite_pos: [0,0]` for image_select that we ignore. No solver needs it; documenting here so future readers don't chase it.

## 8. Implementation order

1. Write `captcha_type.py` with classifier rules (no network, easy to sanity-check manually).
2. Update `settings.py` (base_url + LLM fields + env_file) and ship `.env.example`.
3. Update `client.py` (endpoint, referer, empty-body guard).
4. Create `pipelines/` skeleton: `__init__.py` with registry, `_common.py` with the retry loop and verify helper.
5. Port `slide.py` (move code from `slider/` and register).
6. Port `icon_click.py` (move from `icon_click/`, drop `_legacy_solver.py`).
7. Create `solvers/llm_vision.py`.
8. Create `pipelines/image_select.py` using the LLM client.
9. Wire `cli.py` to `pipelines.dispatch`.
10. Delete `slider/` and `icon_click/` directories.
11. Fix test imports so the test suite still collects (no expansion).
12. Manual end-to-end run: `uv run crack-tcaptcha solve --appid 2046626712` → ticket expected.

## 9. Open decisions

None. All questions closed during brainstorming:
- Scope = fix + type router + image_select only (user's "方案 1").
- image_select solver = GPT-5.4 via relay (user has one).
- Endpoint = `turing.captcha.qcloud.com` uniformly.
- Routing = new classifier module + registry.
- LLM config = `.env` + pydantic-settings.
- Slide behavior = reorganized, minor bugs fixed opportunistically.
- Legacy code = deleted.
- Tests = deferred.
- Retries = same as flashTCaptcha (rerun prehandle, no `new_sess` reuse, subsid fixed at 1).
