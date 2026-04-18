# Captcha Type Router + image_select Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `appid=2046626712` (image_select / click_image_uncheck) while restructuring the codebase so adding more captcha types later is a one-file drop-in.

**Architecture:** Pure-function classifier (`captcha_type.py`) + type-keyed pipeline registry (`pipelines/__init__.py`). Each captcha type lives in its own module with a common `solve_one_attempt(client, pre, tdc_provider) -> VerifyResp` interface. Endpoint migrates from `t.captcha.qq.com` to `turing.captcha.qcloud.com`. GPT-5.4 vision (via user's relay) picks the matching region for image_select.

**Tech Stack:** Python 3.10+, scrapling (curl_cffi), pydantic-settings (.env), httpx (LLM client), existing TDC/PoW/trajectory modules.

**Spec:** `docs/superpowers/specs/2026-04-17-captcha-type-router-design.md`

---

## File Structure

**New files:**
- `src/crack_tcaptcha/captcha_type.py` — pure classifier
- `src/crack_tcaptcha/pipelines/__init__.py` — REGISTRY + dispatch()
- `src/crack_tcaptcha/pipelines/_common.py` — shared retry loop + verify helper
- `src/crack_tcaptcha/pipelines/slide.py` — slide solver (ported)
- `src/crack_tcaptcha/pipelines/icon_click.py` — icon_click solver (ported)
- `src/crack_tcaptcha/pipelines/image_select.py` — NEW
- `src/crack_tcaptcha/solvers/__init__.py` — package marker
- `src/crack_tcaptcha/solvers/llm_vision.py` — GPT-5.4 relay client
- `.env.example` — config template

**Modified files:**
- `src/crack_tcaptcha/settings.py` — new `base_url` default + LLM fields + .env loading
- `src/crack_tcaptcha/client.py` — endpoint via `settings.base_url`, new referer, empty-body guard
- `src/crack_tcaptcha/exceptions.py` — add `UnsupportedCaptchaType`
- `src/crack_tcaptcha/__init__.py` — switch `solve()` to dispatch through pipelines
- `src/crack_tcaptcha/cli.py` — drop `--type`, auto-route
- `tests/test_pipeline_slider.py`, `tests/test_slider_solver.py` — update imports (move-only)

**Deleted files:**
- `src/crack_tcaptcha/slider/` (whole package)
- `src/crack_tcaptcha/icon_click/` (whole package, including `_legacy_solver.py`)

---

## Task 1: Add new configuration fields and `.env` loading

**Files:**
- Modify: `src/crack_tcaptcha/settings.py`
- Create: `.env.example`

- [ ] **Step 1: Rewrite `settings.py` with new fields + env_file**

Replace entire file contents with:

```python
"""Global settings via pydantic-settings (env / .env / constructor)."""

from __future__ import annotations

import pathlib

from pydantic_settings import BaseSettings, SettingsConfigDict


class TCaptchaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TCAPTCHA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    base_url: str = "https://turing.captcha.qcloud.com"
    timeout: float = 15.0
    max_retries: int = 3
    tdc_js_dir: pathlib.Path = pathlib.Path(__file__).resolve().parent / "tdc" / "js"
    tdc_timeout: float = 60.0
    proxy: str | None = None

    # LLM vision solver (used by image_select pipeline)
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "gpt-5.4"
    llm_timeout: float = 30.0


settings = TCaptchaSettings()
```

- [ ] **Step 2: Create `.env.example`**

Create `/Users/gehonglu/remote-code/crackTCaptcha/.env.example` with:

```
# Copy to .env and fill in. .env is gitignored.
TCAPTCHA_BASE_URL=https://turing.captcha.qcloud.com
TCAPTCHA_LLM_API_KEY=sk-your-relay-key-here
TCAPTCHA_LLM_BASE_URL=https://your-relay.example.com
TCAPTCHA_LLM_MODEL=gpt-5.4
TCAPTCHA_LLM_TIMEOUT=30
```

- [ ] **Step 3: Verify settings load without error**

Run: `uv run python -c "from crack_tcaptcha.settings import settings; print(settings.base_url, settings.llm_model)"`

Expected: `https://turing.captcha.qcloud.com gpt-5.4`

- [ ] **Step 4: Verify `.env` is already gitignored**

Run: `grep -E '^\.env$|^\.env\s' .gitignore`

Expected: at least one matching line. If nothing matches, append `.env` to `.gitignore`.

- [ ] **Step 5: Commit**

```bash
git add src/crack_tcaptcha/settings.py .env.example .gitignore
git commit -m "feat(settings): migrate base_url to turing.captcha.qcloud.com and add LLM fields via .env"
```

---

## Task 2: Add `UnsupportedCaptchaType` exception

**Files:**
- Modify: `src/crack_tcaptcha/exceptions.py`

- [ ] **Step 1: Append new exception class**

Replace the entire contents of `src/crack_tcaptcha/exceptions.py` with:

```python
"""crack_tcaptcha exceptions."""


class TCaptchaError(Exception):
    """Base exception for all crack_tcaptcha errors."""


class NetworkError(TCaptchaError):
    """HTTP / connectivity error."""


class SolveError(TCaptchaError):
    """Failed to solve the challenge (NCC / icon matching / LLM)."""


class TDCError(TCaptchaError):
    """TDC.js execution failed."""


class PowError(TCaptchaError):
    """PoW brute-force exceeded the search limit."""


class UnsupportedCaptchaType(TCaptchaError):
    """Classifier returned 'unknown' or dispatch found no matching pipeline."""

    def __init__(self, captcha_type: str, dyn_keys: list[str]):
        super().__init__(
            f"unsupported captcha type {captcha_type!r}; dyn keys={dyn_keys}"
        )
        self.captcha_type = captcha_type
        self.dyn_keys = dyn_keys
```

- [ ] **Step 2: Verify import works**

Run: `uv run python -c "from crack_tcaptcha.exceptions import UnsupportedCaptchaType; e = UnsupportedCaptchaType('x', ['a']); print(e)"`

Expected: `unsupported captcha type 'x'; dyn keys=['a']`

- [ ] **Step 3: Commit**

```bash
git add src/crack_tcaptcha/exceptions.py
git commit -m "feat(exceptions): add UnsupportedCaptchaType"
```

---

## Task 3: Implement the captcha type classifier

**Files:**
- Create: `src/crack_tcaptcha/captcha_type.py`

- [ ] **Step 1: Create `captcha_type.py` with rules**

Create `src/crack_tcaptcha/captcha_type.py` with:

```python
"""Pure-function classifier: dyn_show_info → captcha type string.

No I/O, no state, deterministic. First matching rule wins. When no rule
matches returns Classification(captcha_type="unknown", matched_rule="fallback_unknown").
"""

from __future__ import annotations

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


def _is_image_select_show_type(dyn: dict[str, Any]) -> bool:
    return dyn.get("show_type") == "click_image_uncheck"


def _is_image_select_uc(dyn: dict[str, Any]) -> bool:
    click = dyn.get("bg_elem_cfg", {}).get("click_cfg", {})
    return "DynAnswerType_UC" in click.get("data_type", [])


def _is_slide(dyn: dict[str, Any]) -> bool:
    return "fg_binding_list" in dyn


def _is_icon_click(dyn: dict[str, Any]) -> bool:
    click = dyn.get("bg_elem_cfg", {}).get("click_cfg", {})
    if "DynAnswerType_POS" not in click.get("data_type", []):
        return False
    if "ins_elem_cfg" in dyn:
        return False
    instr = dyn.get("instruction", "")
    if not instr.startswith("请依次点击"):
        return False
    after = instr.split("：", 1)[1] if "：" in instr else ""
    return bool(after.strip())


_RULES: tuple[_TypeRule, ...] = (
    _TypeRule("image_select_show_type", "image_select", _is_image_select_show_type),
    _TypeRule("image_select_uc", "image_select", _is_image_select_uc),
    _TypeRule("slide_fg_binding", "slide", _is_slide),
    _TypeRule("icon_click_pos", "icon_click", _is_icon_click),
)


def classify(dyn: dict[str, Any]) -> Classification:
    for rule in _RULES:
        if rule.predicate(dyn):
            return Classification(captcha_type=rule.captcha_type, matched_rule=rule.name)
    return Classification(captcha_type="unknown", matched_rule="fallback_unknown")


__all__ = ["classify", "Classification", "CAPTCHA_TYPES"]
```

- [ ] **Step 2: Hand-verify against 6 harvest samples**

Run:

```bash
uv run python <<'PY'
from crack_tcaptcha.captcha_type import classify

# image_select (2046626712, user failing case)
assert classify({
    "show_type": "click_image_uncheck",
    "bg_elem_cfg": {"click_cfg": {"data_type": ["DynAnswerType_UC"]}},
    "instruction": "\u201c蓝色的蝴蝶\u201d",
}).captcha_type == "image_select"

# image_select fallback path (no show_type, only UC)
assert classify({
    "bg_elem_cfg": {"click_cfg": {"data_type": ["DynAnswerType_UC"]}},
}).captcha_type == "image_select"

# slide (default_preload sample)
assert classify({
    "fg_binding_list": [{"some": "val"}],
    "fg_elem_list": [{"elem_id": 1}],
    "instruction": "拖动下方滑块完成拼图",
}).captcha_type == "slide"

# icon_click (subsid=5 sample)
assert classify({
    "bg_elem_cfg": {"click_cfg": {"data_type": ["DynAnswerType_POS"]}},
    "instruction": "请依次点击：尝 稠 车 ",
}).captcha_type == "icon_click"

# shape_click (has ins_elem_cfg, empty instruction after colon) → unknown
assert classify({
    "bg_elem_cfg": {"click_cfg": {"data_type": ["DynAnswerType_POS"]}},
    "ins_elem_cfg": [{"id": 1}],
    "instruction": "请依次点击：",
}).captcha_type == "unknown"

# silent (empty dyn) → unknown
assert classify({"color_scheme": "#000"}).captcha_type == "unknown"

print("all 6 harvest cases pass")
PY
```

Expected output: `all 6 harvest cases pass`

- [ ] **Step 3: Commit**

```bash
git add src/crack_tcaptcha/captcha_type.py
git commit -m "feat(captcha_type): add pure-function classifier with 4 rules"
```

---

## Task 4: Update `client.py` — endpoint, referer, empty-body guard

**Files:**
- Modify: `src/crack_tcaptcha/client.py`

- [ ] **Step 1: Replace module-level `_BASE` constant with runtime lookups**

Open `src/crack_tcaptcha/client.py`. Find this line near the top:

```python
_BASE = settings.base_url
```

Delete that line. It caches the URL at import time and breaks runtime overrides.

- [ ] **Step 2: Replace `url = f"{_BASE}/cap_union_prehandle"` in `prehandle`**

Find the line in the `prehandle` method:

```python
url = f"{_BASE}/cap_union_prehandle"
```

Replace with:

```python
url = f"{settings.base_url}/cap_union_prehandle"
```

- [ ] **Step 3: Update `get_image` — referer, absolute URL resolution, empty-body guard**

Find the `get_image` method body. Replace the whole method (keep the docstring) with:

```python
    def get_image(self, img_url: str) -> bytes:
        """Download a captcha image (bg or fg sprite)."""
        full = img_url if img_url.startswith("http") else f"{settings.base_url}{img_url}"
        img_kw = {
            **self._fetch_kw,
            "headers": {
                **self._fetch_kw["headers"],
                "Referer": f"{settings.base_url}/",
            },
        }
        try:
            resp = Fetcher.get(full, **img_kw)
            log = logging.getLogger(__name__)
            log.info(
                "image download: %s → HTTP %d, %d bytes",
                full[:100], resp.status, len(resp.body),
            )
            if resp.status != 200:
                raise NetworkError(f"image download failed: HTTP {resp.status}")
            if len(resp.body) == 0:
                raise NetworkError(f"image download returned empty body: {full[:120]}")
        except NetworkError:
            raise
        except Exception as e:
            raise NetworkError(f"image download failed: {e}") from e
        return resp.body
```

- [ ] **Step 4: Update `get_fg_image_url` to use runtime base_url**

Find the `get_fg_image_url` method. Replace the whole method body (keep the docstring) with:

```python
    def get_fg_image_url(self, bg_img_url: str) -> str:
        """Derive the foreground sprite URL from the background URL (img_index=1 → 0)."""
        full = bg_img_url if bg_img_url.startswith("http") else f"{settings.base_url}{bg_img_url}"
        parsed = urllib.parse.urlparse(full)
        qs = urllib.parse.parse_qs(parsed.query)
        qs["img_index"] = ["0"]
        new_query = urllib.parse.urlencode({k: v[0] for k, v in qs.items()})
        return urllib.parse.urlunparse(parsed._replace(query=new_query))
```

- [ ] **Step 5: Update `verify` to use runtime base_url**

Find in the `verify` method:

```python
url = f"{_BASE}/cap_union_new_verify"
```

Replace with:

```python
url = f"{settings.base_url}/cap_union_new_verify"
```

- [ ] **Step 6: Verify import still works**

Run: `uv run python -c "from crack_tcaptcha.client import TCaptchaClient; print('ok')"`

Expected: `ok` (no ImportError, no NameError on `_BASE`).

- [ ] **Step 7: Commit**

```bash
git add src/crack_tcaptcha/client.py
git commit -m "feat(client): use runtime settings.base_url, add empty-body guard, update referer"
```

---

## Task 5: Create `pipelines/` package + `_common.py`

**Files:**
- Create: `src/crack_tcaptcha/pipelines/__init__.py` (placeholder, filled in Task 9)
- Create: `src/crack_tcaptcha/pipelines/_common.py`

- [ ] **Step 1: Create empty package init**

Create `src/crack_tcaptcha/pipelines/__init__.py` with placeholder content (will be filled in Task 9):

```python
"""Pipeline registry and dispatch. See dispatch() for entry point."""
```

- [ ] **Step 2: Create `_common.py` with shared helpers**

Create `src/crack_tcaptcha/pipelines/_common.py` with:

```python
"""Shared helpers for all captcha type pipelines.

- run_async: bridge async TDC call from sync pipeline code
- resolve_tdc_url: turn relative tdc_path into absolute URL
- finish_with_verify: TDC collect → verify POST, pipeline-agnostic
"""

from __future__ import annotations

import asyncio
import logging

from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.models import PrehandleResp, VerifyResp
from crack_tcaptcha.settings import settings
from crack_tcaptcha.tdc.provider import TDCProvider

log = logging.getLogger(__name__)


def run_async(coro):
    """Run an async coroutine from sync context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def resolve_tdc_url(tdc_path: str) -> str:
    """Turn a relative ``/tdc.js?...`` into an absolute URL on the captcha host."""
    if not tdc_path:
        return ""
    if tdc_path.startswith("http"):
        return tdc_path
    return f"{settings.base_url}{tdc_path}"


def finish_with_verify(
    client: TCaptchaClient,
    pre: PrehandleResp,
    tdc_provider: TDCProvider,
    *,
    ans_json: str,
    pow_answer: str,
    pow_calc_time: int,
    trajectory,
) -> VerifyResp:
    """TDC collect + verify POST. Shared across all pipelines."""
    tdc_url = resolve_tdc_url(pre.tdc_path)
    tdc_result = run_async(tdc_provider.collect(tdc_url, trajectory, settings.user_agent))
    return client.verify(
        pre.sess,
        ans=ans_json,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        collect=tdc_result.collect,
        tlg=len(tdc_result.collect),
        eks=tdc_result.eks,
    )
```

- [ ] **Step 3: Verify import**

Run: `uv run python -c "from crack_tcaptcha.pipelines._common import finish_with_verify, run_async, resolve_tdc_url; print('ok')"`

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/crack_tcaptcha/pipelines/__init__.py src/crack_tcaptcha/pipelines/_common.py
git commit -m "feat(pipelines): add package scaffold with _common helpers"
```

---

## Task 6: Port slide pipeline

**Files:**
- Create: `src/crack_tcaptcha/pipelines/slide.py`

Port logic from `src/crack_tcaptcha/slider/pipeline.py` and `src/crack_tcaptcha/slider/solver.py`. The old files stay for now; they'll be deleted in Task 11 after everything wires up.

- [ ] **Step 1: Create `pipelines/slide.py`**

Create `src/crack_tcaptcha/pipelines/slide.py` with:

```python
"""Slide captcha pipeline: NCC template match → drag trajectory."""

from __future__ import annotations

import io
import json
import logging

import numpy as np
from PIL import Image

from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.exceptions import SolveError
from crack_tcaptcha.models import FgElem, PrehandleResp, VerifyResp
from crack_tcaptcha.pipelines._common import finish_with_verify
from crack_tcaptcha.pow import solve_pow
from crack_tcaptcha.tdc.provider import TDCProvider
from crack_tcaptcha.trajectory import generate_slide_trajectory

log = logging.getLogger(__name__)


class SliderSolver:
    """NCC two-phase template matcher. Returns (target_x, target_y, score)."""

    def __init__(self, *, y_search_range: int = 5) -> None:
        self.y_search_range = y_search_range

    def solve(self, bg_bytes: bytes, fg_bytes: bytes, piece: FgElem) -> tuple[int, int, float]:
        bg_arr = np.array(Image.open(io.BytesIO(bg_bytes)).convert("RGB"))
        fg_img = Image.open(io.BytesIO(fg_bytes))
        px, py = piece.sprite_pos
        pw, ph = piece.size_2d
        piece_arr = np.array(
            fg_img.crop((px, py, px + pw, py + ph)).convert("RGB"), dtype=np.float32
        )
        bg_f = bg_arr.astype(np.float32)

        H, W, _ = bg_f.shape
        best = (0, 0, -1.0)
        init_y = piece.init_pos[1]

        # coarse search on init_y row, stride 4
        y0 = max(0, init_y - ph // 2)
        for x in range(0, W - pw, 4):
            patch = bg_f[y0:y0 + ph, x:x + pw]
            score = _ncc(patch, piece_arr)
            if score > best[2]:
                best = (x, y0, score)

        # fine search ±6 X, ±self.y_search_range Y around coarse peak
        cx, cy, _ = best
        for x in range(max(0, cx - 6), min(W - pw, cx + 7)):
            for y in range(max(0, cy - self.y_search_range), min(H - ph, cy + self.y_search_range + 1)):
                patch = bg_f[y:y + ph, x:x + pw]
                score = _ncc(patch, piece_arr)
                if score > best[2]:
                    best = (x, y, score)

        return best


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a_mean = a.mean()
    b_mean = b.mean()
    a_c = a - a_mean
    b_c = b - b_mean
    denom = float(np.sqrt((a_c * a_c).sum() * (b_c * b_c).sum()))
    if denom == 0:
        return 0.0
    return float((a_c * b_c).sum() / denom)


def solve_one_attempt(
    client: TCaptchaClient,
    pre: PrehandleResp,
    tdc_provider: TDCProvider,
) -> VerifyResp:
    """Execute one slide attempt. Raises SolveError on hard failures."""
    if not pre.fg_elem_list:
        raise SolveError("slide: prehandle has no fg_elem_list")

    bg_bytes = client.get_image(pre.bg_elem_cfg.img_url)
    fg_url = client.get_fg_image_url(pre.bg_elem_cfg.img_url)
    fg_bytes = client.get_image(fg_url)

    piece = pre.fg_elem_list[0]
    solver = SliderSolver()
    target_x, target_y, ncc = solver.solve(bg_bytes, fg_bytes, piece)
    log.info("slide NCC: target=(%d,%d) ncc=%.4f", target_x, target_y, ncc)

    pow_answer, pow_calc_time = solve_pow(pre.pow_cfg.prefix, pre.pow_cfg.target_md5)

    ans = json.dumps(
        [
            {
                "elem_id": piece.elem_id,
                "type": "DynAnswerType_POS",
                "data": f"{target_x},{target_y}",
            }
        ]
    )

    init_x, init_y = piece.init_pos
    traj = generate_slide_trajectory(init_x, init_y, target_x, target_y)

    return finish_with_verify(
        client, pre, tdc_provider,
        ans_json=ans,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        trajectory=traj,
    )


__all__ = ["solve_one_attempt", "SliderSolver"]
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from crack_tcaptcha.pipelines.slide import solve_one_attempt, SliderSolver; print('ok')"`

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/crack_tcaptcha/pipelines/slide.py
git commit -m "feat(pipelines): port slide solver to pipelines/slide.py"
```

---

## Task 7: Port icon_click pipeline

**Files:**
- Create: `src/crack_tcaptcha/pipelines/icon_click.py`

Ports the `_solve_legacy_icon_click` path from `src/crack_tcaptcha/icon_click/pipeline.py`. The click_image_uncheck branch is dropped — it's replaced by `image_select.py`.

- [ ] **Step 1: Create `pipelines/icon_click.py`**

Create `src/crack_tcaptcha/pipelines/icon_click.py` with:

```python
"""Legacy icon_click pipeline: fg_elem_list-based character click via ddddocr."""

from __future__ import annotations

import io
import json
import logging

from PIL import Image

from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.exceptions import SolveError
from crack_tcaptcha.models import PrehandleResp, VerifyResp
from crack_tcaptcha.pipelines._common import finish_with_verify
from crack_tcaptcha.pow import solve_pow
from crack_tcaptcha.tdc.provider import TDCProvider
from crack_tcaptcha.trajectory import generate_click_trajectory, merge_trajectories

log = logging.getLogger(__name__)


def solve_one_attempt(
    client: TCaptchaClient,
    pre: PrehandleResp,
    tdc_provider: TDCProvider,
) -> VerifyResp:
    """Execute one icon_click attempt. Raises SolveError when fg_elem_list missing."""
    if not pre.fg_elem_list:
        raise SolveError("icon_click: prehandle has no fg_elem_list")

    bg_bytes = client.get_image(pre.bg_elem_cfg.img_url)
    fg_url = client.get_fg_image_url(pre.bg_elem_cfg.img_url)
    fg_bytes = client.get_image(fg_url)

    # Crop each hint icon from the fg sprite for matching.
    fg_img = Image.open(io.BytesIO(fg_bytes))
    hint_images: list[bytes] = []
    for elem in pre.fg_elem_list:
        px, py = elem.sprite_pos
        pw, ph = elem.size_2d
        crop = fg_img.crop((px, py, px + pw, py + ph))
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        hint_images.append(buf.getvalue())

    # ddddocr-based matcher lives in the legacy solver module. Import lazily
    # so the package still loads when ddddocr is not installed.
    try:
        from crack_tcaptcha.icon_click._legacy_solver import match_icons  # noqa: E501
    except ImportError as e:
        raise SolveError(
            "icon_click requires ddddocr: `uv sync --extra icon-click`"
        ) from e

    click_coords = match_icons(bg_bytes, hint_images)
    if len(click_coords) != len(pre.fg_elem_list):
        raise SolveError(
            f"icon_click expected {len(pre.fg_elem_list)} matches, got {len(click_coords)}"
        )

    pow_answer, pow_calc_time = solve_pow(pre.pow_cfg.prefix, pre.pow_cfg.target_md5)

    ans_list = []
    for elem, (cx, cy) in zip(pre.fg_elem_list, click_coords, strict=True):
        ans_list.append(
            {
                "elem_id": elem.elem_id,
                "type": "DynAnswerType_POS",
                "data": f"{cx},{cy}",
            }
        )
    ans = json.dumps(ans_list)

    traj_segments = []
    prev_x, prev_y = 0, 0
    for cx, cy in click_coords:
        traj_segments.append(generate_click_trajectory(prev_x, prev_y, cx, cy))
        prev_x, prev_y = cx, cy
    combined = merge_trajectories(traj_segments)

    return finish_with_verify(
        client, pre, tdc_provider,
        ans_json=ans,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        trajectory=combined,
    )


__all__ = ["solve_one_attempt"]
```

Note: this module imports `crack_tcaptcha.icon_click._legacy_solver.match_icons` at runtime. That path still exists right now (the package is deleted in Task 11). **Before** Task 11, copy `_legacy_solver.py` into the new layout:

```bash
mkdir -p src/crack_tcaptcha/_legacy
mv src/crack_tcaptcha/icon_click/_legacy_solver.py src/crack_tcaptcha/_legacy/icon_match.py
touch src/crack_tcaptcha/_legacy/__init__.py
```

Then update the import in `pipelines/icon_click.py`:

```python
from crack_tcaptcha._legacy.icon_match import match_icons
```

- [ ] **Step 2: Move `_legacy_solver.py` into `_legacy/icon_match.py`**

Run:

```bash
mkdir -p src/crack_tcaptcha/_legacy
git mv src/crack_tcaptcha/icon_click/_legacy_solver.py src/crack_tcaptcha/_legacy/icon_match.py
touch src/crack_tcaptcha/_legacy/__init__.py
```

Then edit `src/crack_tcaptcha/pipelines/icon_click.py` Step 1 content: replace

```python
from crack_tcaptcha.icon_click._legacy_solver import match_icons  # noqa: E501
```

with

```python
from crack_tcaptcha._legacy.icon_match import match_icons
```

- [ ] **Step 3: Verify import**

Run: `uv run python -c "from crack_tcaptcha.pipelines.icon_click import solve_one_attempt; print('ok')"`

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/crack_tcaptcha/pipelines/icon_click.py src/crack_tcaptcha/_legacy/
git commit -m "feat(pipelines): port icon_click legacy path; move _legacy_solver → _legacy/icon_match"
```

---

## Task 8: Create LLM vision solver

**Files:**
- Create: `src/crack_tcaptcha/solvers/__init__.py`
- Create: `src/crack_tcaptcha/solvers/llm_vision.py`
- Modify: `pyproject.toml` (add `httpx` as a runtime dep)

- [ ] **Step 1: Create `solvers/__init__.py`**

Create `src/crack_tcaptcha/solvers/__init__.py` with:

```python
"""Solvers for captcha content extraction (LLM, OCR, CV)."""
```

- [ ] **Step 2: Add `httpx` to runtime dependencies**

Open `pyproject.toml`. Find the `dependencies` list and append `"httpx>=0.27"`. After:

```toml
dependencies = [
    "scrapling[fetchers]>=0.4.3",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "numpy>=1.24",
    "Pillow>=10.0",
    "httpx>=0.27",
]
```

- [ ] **Step 3: Sync dependencies**

Run: `uv sync`

Expected: `httpx` installed without error.

- [ ] **Step 4: Create `solvers/llm_vision.py`**

Create `src/crack_tcaptcha/solvers/llm_vision.py` with:

```python
"""GPT-5.4 vision client (OpenAI-compatible relay) for image_select captcha.

Expects TCAPTCHA_LLM_API_KEY, TCAPTCHA_LLM_BASE_URL, TCAPTCHA_LLM_MODEL in env / .env.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx

from crack_tcaptcha.exceptions import SolveError
from crack_tcaptcha.models import SelectRegion
from crack_tcaptcha.settings import settings

log = logging.getLogger(__name__)

_SMART_QUOTES = "\u201c\u201d\"'"


def _strip_instruction(instruction: str) -> str:
    """Remove leading/trailing smart quotes and whitespace."""
    return instruction.strip().strip(_SMART_QUOTES).strip()


def _build_prompt(instruction: str, regions: list[SelectRegion], bg_w: int, bg_h: int) -> str:
    lines = [
        f"Image size: {bg_w}x{bg_h} pixels. It is divided into {len(regions)} regions.",
        "Each region is identified by an integer id. Coordinates are (x1, y1, x2, y2):",
    ]
    for r in regions:
        x1, y1, x2, y2 = r.range
        lines.append(f"  region {r.id}: ({x1}, {y1}, {x2}, {y2})")
    lines.append("")
    lines.append(f"Pick the SINGLE region whose content best matches: {instruction}")
    lines.append('Respond with ONLY a JSON object: {"region_id": N} where N is 1..' + str(len(regions)) + ".")
    return "\n".join(lines)


def _extract_region_id(text: str, n_regions: int) -> int:
    try:
        obj = json.loads(text)
        rid = int(obj.get("region_id"))
        if 1 <= rid <= n_regions:
            return rid
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    # fallback: first integer in 1..n_regions
    for m in re.finditer(r"\d+", text):
        v = int(m.group())
        if 1 <= v <= n_regions:
            return v
    raise SolveError(f"LLM returned unparseable output: {text[:200]!r}")


def match_region(
    bg_bytes: bytes,
    *,
    instruction: str,
    regions: list[SelectRegion],
    bg_size: tuple[int, int],
) -> int:
    """Return the region id (1..N) whose content matches the instruction."""
    if not settings.llm_api_key or not settings.llm_base_url:
        raise SolveError("LLM not configured: set TCAPTCHA_LLM_API_KEY and TCAPTCHA_LLM_BASE_URL")

    cleaned = _strip_instruction(instruction)
    prompt = _build_prompt(cleaned, regions, bg_size[0], bg_size[1])
    b64 = base64.b64encode(bg_bytes).decode()

    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 64,
        "temperature": 0,
    }

    url = f"{settings.llm_base_url.rstrip('/')}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    last_err: Exception | None = None
    for attempt in (1, 2):  # one internal retry on network error
        try:
            with httpx.Client(timeout=settings.llm_timeout) as http:
                resp = http.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise SolveError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            log.info("LLM raw reply (attempt %d): %s", attempt, content[:200])
            rid = _extract_region_id(content, len(regions))
            log.info("LLM picked region_id=%d for instruction=%r", rid, cleaned)
            return rid
        except SolveError:
            raise  # parse failure is terminal for this attempt
        except Exception as e:
            last_err = e
            log.warning("LLM call attempt %d failed: %s", attempt, e)
    raise SolveError(f"LLM call failed after 2 attempts: {last_err}")


__all__ = ["match_region"]
```

- [ ] **Step 5: Verify import**

Run: `uv run python -c "from crack_tcaptcha.solvers.llm_vision import match_region; print('ok')"`

Expected: `ok`

- [ ] **Step 6: Smoke-test instruction stripping and region_id extraction**

Run:

```bash
uv run python <<'PY'
from crack_tcaptcha.solvers.llm_vision import _strip_instruction, _extract_region_id

assert _strip_instruction("\u201c蓝色的蝴蝶\u201d") == "蓝色的蝴蝶"
assert _strip_instruction('"hello"') == "hello"
assert _strip_instruction("  foo  ") == "foo"

assert _extract_region_id('{"region_id": 3}', 6) == 3
assert _extract_region_id("the answer is 4", 6) == 4
assert _extract_region_id('```json\n{"region_id":2}\n```', 6) == 2
try:
    _extract_region_id("no numbers here", 6)
    assert False, "should have raised"
except Exception as e:
    assert "unparseable" in str(e)
print("llm_vision smoke checks pass")
PY
```

Expected: `llm_vision smoke checks pass`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/crack_tcaptcha/solvers/__init__.py src/crack_tcaptcha/solvers/llm_vision.py
git commit -m "feat(solvers): add GPT-5.4 vision client for image_select"
```

---

## Task 9: Create image_select pipeline + wire registry

**Files:**
- Create: `src/crack_tcaptcha/pipelines/image_select.py`
- Modify: `src/crack_tcaptcha/pipelines/__init__.py`

- [ ] **Step 1: Create `pipelines/image_select.py`**

Create `src/crack_tcaptcha/pipelines/image_select.py` with:

```python
"""image_select (click_image_uncheck) pipeline: LLM picks one of 6 regions."""

from __future__ import annotations

import json
import logging

from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.exceptions import SolveError
from crack_tcaptcha.models import PrehandleResp, VerifyResp
from crack_tcaptcha.pipelines._common import finish_with_verify
from crack_tcaptcha.pow import solve_pow
from crack_tcaptcha.solvers.llm_vision import match_region
from crack_tcaptcha.tdc.provider import TDCProvider
from crack_tcaptcha.trajectory import generate_click_trajectory

log = logging.getLogger(__name__)


def solve_one_attempt(
    client: TCaptchaClient,
    pre: PrehandleResp,
    tdc_provider: TDCProvider,
) -> VerifyResp:
    """Execute one image_select attempt."""
    if not pre.select_regions:
        raise SolveError("image_select: prehandle has no select_regions")
    if not pre.instruction:
        raise SolveError("image_select: prehandle has no instruction")

    bg_bytes = client.get_image(pre.bg_elem_cfg.img_url)
    log.info(
        "image_select: instruction=%r, %d regions, bg=%d bytes",
        pre.instruction, len(pre.select_regions), len(bg_bytes),
    )

    region_id = match_region(
        bg_bytes,
        instruction=pre.instruction,
        regions=pre.select_regions,
        bg_size=(pre.bg_elem_cfg.width, pre.bg_elem_cfg.height),
    )

    ans = json.dumps(
        [{"elem_id": "", "type": "DynAnswerType_UC", "data": str(region_id)}]
    )

    pow_answer, pow_calc_time = solve_pow(pre.pow_cfg.prefix, pre.pow_cfg.target_md5)

    selected = next(r for r in pre.select_regions if r.id == region_id)
    x1, y1, x2, y2 = selected.range
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    traj = generate_click_trajectory(cx, cy)
    log.info("image_select click center=(%d,%d) for region %d", cx, cy, region_id)

    return finish_with_verify(
        client, pre, tdc_provider,
        ans_json=ans,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        trajectory=traj,
    )


__all__ = ["solve_one_attempt"]
```

- [ ] **Step 2: Replace `pipelines/__init__.py` with the registry + dispatch**

Replace the contents of `src/crack_tcaptcha/pipelines/__init__.py` with:

```python
"""Pipeline registry and dispatch. One attempt loop; classifier decides pipeline."""

from __future__ import annotations

import logging
from typing import Callable

from crack_tcaptcha.captcha_type import classify
from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.exceptions import TCaptchaError, UnsupportedCaptchaType
from crack_tcaptcha.models import PrehandleResp, SolveResult, VerifyResp
from crack_tcaptcha.pipelines import icon_click, image_select, slide
from crack_tcaptcha.settings import settings
from crack_tcaptcha.tdc.provider import TDCProvider

log = logging.getLogger(__name__)

_SolveFn = Callable[[TCaptchaClient, PrehandleResp, TDCProvider], VerifyResp]

REGISTRY: dict[str, _SolveFn] = {
    "slide": slide.solve_one_attempt,
    "icon_click": icon_click.solve_one_attempt,
    "image_select": image_select.solve_one_attempt,
}


def dispatch(
    appid: str,
    *,
    tdc_provider: TDCProvider,
    max_retries: int | None = None,
    client: TCaptchaClient | None = None,
    entry_url: str = "",
) -> SolveResult:
    """Top-level solve: prehandle → classify → run matching pipeline, N retries."""
    retries = max_retries if max_retries is not None else settings.max_retries
    own_client = client is None
    if own_client:
        client = TCaptchaClient()

    last_error = ""
    try:
        for attempt in range(1, retries + 1):
            try:
                pre = client.prehandle(appid, subsid=1, entry_url=entry_url)
                dyn = pre.raw.get("data", {}).get("dyn_show_info", {})
                cls = classify(dyn)
                log.info(
                    "classified type=%s rule=%s instruction=%r",
                    cls.captcha_type, cls.matched_rule, dyn.get("instruction", ""),
                )
                if cls.captcha_type == "unknown":
                    raise UnsupportedCaptchaType(cls.captcha_type, sorted(dyn.keys()))

                solve_fn = REGISTRY[cls.captcha_type]
                verify_resp = solve_fn(client, pre, tdc_provider)
                if verify_resp.ok:
                    return SolveResult(
                        ok=True,
                        ticket=verify_resp.ticket,
                        randstr=verify_resp.randstr,
                        attempts=attempt,
                    )
                last_error = verify_resp.error_msg or f"errorCode={verify_resp.error_code}"
                log.info("attempt %d failed: %s", attempt, last_error)
            except UnsupportedCaptchaType as e:
                # Unknown type won't change on rerun; abort retry loop.
                return SolveResult(ok=False, error=str(e), attempts=attempt)
            except TCaptchaError as e:
                last_error = str(e)
                log.warning("attempt %d error: %s", attempt, e)
    finally:
        if own_client:
            client.close()

    return SolveResult(ok=False, error=last_error, attempts=retries)


__all__ = ["dispatch", "REGISTRY"]
```

- [ ] **Step 3: Verify the registry imports and exposes dispatch**

Run:

```bash
uv run python -c "from crack_tcaptcha.pipelines import dispatch, REGISTRY; print(sorted(REGISTRY.keys()))"
```

Expected: `['icon_click', 'image_select', 'slide']`

- [ ] **Step 4: Commit**

```bash
git add src/crack_tcaptcha/pipelines/image_select.py src/crack_tcaptcha/pipelines/__init__.py
git commit -m "feat(pipelines): add image_select and wire dispatch registry"
```

---

## Task 10: Rewire top-level `solve()` and CLI

**Files:**
- Modify: `src/crack_tcaptcha/__init__.py`
- Modify: `src/crack_tcaptcha/cli.py`

- [ ] **Step 1: Replace `__init__.py` to route through dispatch**

Replace contents of `src/crack_tcaptcha/__init__.py` with:

```python
"""crack_tcaptcha — Automated TCaptcha solver.

Public API::

    from crack_tcaptcha import solve

    result = solve(appid="...")
    if result.ok:
        print(result.ticket, result.randstr)
"""

from __future__ import annotations

import os

from crack_tcaptcha.models import SolveResult

__all__ = ["solve", "SolveResult"]


def _build_tdc_provider():
    """Select TDC provider via TCAPTCHA_TDC_PROVIDER env (``scrapling`` | ``nodejs``)."""
    choice = os.environ.get("TCAPTCHA_TDC_PROVIDER", "scrapling").lower()
    if choice == "nodejs":
        from crack_tcaptcha.tdc.nodejs_jsdom import NodeJsdomProvider
        return NodeJsdomProvider()
    from crack_tcaptcha.tdc.scrapling_browser import ScraplingBrowserProvider
    return ScraplingBrowserProvider()


def solve(appid: str, *, max_retries: int | None = None, entry_url: str = "") -> SolveResult:
    """Auto-classify the captcha and route to the matching pipeline."""
    from crack_tcaptcha.pipelines import dispatch

    tdc = _build_tdc_provider()
    return dispatch(
        appid,
        tdc_provider=tdc,
        max_retries=max_retries,
        entry_url=entry_url,
    )
```

- [ ] **Step 2: Replace `cli.py` — drop `--type`, auto-route**

Replace contents of `src/crack_tcaptcha/cli.py` with:

```python
"""CLI entry point for crack-tcaptcha."""

from __future__ import annotations

import argparse
import json
import logging
import sys


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s: %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(prog="crack-tcaptcha", description="TCaptcha automated solver")
    sub = parser.add_subparsers(dest="command")

    solve_p = sub.add_parser("solve", help="Solve a TCaptcha challenge")
    solve_p.add_argument("--appid", required=True, help="TCaptcha APP_ID")
    solve_p.add_argument("--retries", type=int, default=3, help="Max retry attempts")
    solve_p.add_argument("--entry-url", default="", help="Parent page URL (optional)")
    solve_p.add_argument("--json", action="store_true", dest="as_json", help="Output as JSON")

    args = parser.parse_args(argv)

    if args.command != "solve":
        parser.print_help()
        sys.exit(1)

    from crack_tcaptcha import solve

    result = solve(appid=args.appid, max_retries=args.retries, entry_url=args.entry_url)

    if args.as_json:
        print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    else:
        if result.ok:
            print(f"OK  ticket={result.ticket}  randstr={result.randstr}  attempts={result.attempts}")
        else:
            print(f"FAIL  error={result.error}  attempts={result.attempts}", file=sys.stderr)
            sys.exit(2)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify CLI imports**

Run: `uv run crack-tcaptcha --help`

Expected: usage output listing the `solve` subcommand, no `--type` flag.

- [ ] **Step 4: Commit**

```bash
git add src/crack_tcaptcha/__init__.py src/crack_tcaptcha/cli.py
git commit -m "feat(cli): auto-route via pipelines.dispatch; drop --type flag"
```

---

## Task 11: Delete legacy packages

**Files:**
- Delete: `src/crack_tcaptcha/slider/`
- Delete: `src/crack_tcaptcha/icon_click/`

- [ ] **Step 1: Remove the legacy directories**

Run:

```bash
rm -rf src/crack_tcaptcha/slider src/crack_tcaptcha/icon_click
```

- [ ] **Step 2: Verify nothing else imports them**

Run: `grep -rn "from crack_tcaptcha.slider\|from crack_tcaptcha.icon_click\|crack_tcaptcha\.slider\|crack_tcaptcha\.icon_click" src/ tests/`

Expected: no hits in `src/`. Test files may still reference old paths — those will be fixed in Task 12. If `src/` has any hit, delete or update that reference before continuing.

- [ ] **Step 3: Confirm the public API still works**

Run: `uv run python -c "from crack_tcaptcha import solve; from crack_tcaptcha.pipelines import dispatch, REGISTRY; print(sorted(REGISTRY))"`

Expected: `['icon_click', 'image_select', 'slide']`

- [ ] **Step 4: Commit**

```bash
git add -A src/crack_tcaptcha/
git commit -m "refactor: delete legacy slider/ and icon_click/ packages"
```

---

## Task 12: Fix test imports

**Files:**
- Modify: `tests/test_pipeline_slider.py`
- Modify: `tests/test_slider_solver.py`
- (Others as needed)

The spec doesn't require new tests, but the existing tests must still collect. If a test references the removed `crack_tcaptcha.slider` module, update the import.

- [ ] **Step 1: Identify broken test imports**

Run: `grep -rn "from crack_tcaptcha.slider\|from crack_tcaptcha.icon_click" tests/`

Note each match.

- [ ] **Step 2: Update imports in each listed file**

For each file in step 1, change:
- `from crack_tcaptcha.slider.pipeline import solve_slider` → remove (no longer exported; tests that called it must skip or be updated to `from crack_tcaptcha import solve`)
- `from crack_tcaptcha.slider.solver import SliderSolver` → `from crack_tcaptcha.pipelines.slide import SliderSolver`
- `from crack_tcaptcha.slider.pipeline import _one_attempt` → replace with `from crack_tcaptcha.pipelines.slide import solve_one_attempt` (note name change; update any call sites accordingly)
- `from crack_tcaptcha.icon_click.*` → the only thing we kept is `solve_one_attempt` in `pipelines.icon_click`; drop tests that depended on `_legacy_solver` or internal helpers.

If a test depends on a function that no longer exists (e.g. `select_best_match` for the old click_image branch), mark the whole test file as skip: add at the top `import pytest; pytestmark = pytest.mark.skip(reason="removed with legacy icon_click click_image path")`.

- [ ] **Step 3: Run the test suite (collection only)**

Run: `uv run pytest --collect-only -q`

Expected: collection succeeds without ImportError. Test pass/fail is not the goal here — collection is.

- [ ] **Step 4: Run the non-network tests**

Run: `uv run pytest tests/test_pow.py tests/test_trajectory.py tests/test_models.py -v`

Expected: all pass (these don't depend on removed modules).

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: update imports after pipelines/ migration; skip tests tied to removed paths"
```

---

## Task 13: End-to-end manual run (appid=2046626712)

**Files:** (none modified — this is verification)

This task is the acceptance gate. It requires:
- `.env` filled in at repo root with real `TCAPTCHA_LLM_API_KEY` + `TCAPTCHA_LLM_BASE_URL`
- Network access to `turing.captcha.qcloud.com` and the LLM relay

- [ ] **Step 1: Verify `.env` is present and populated**

Run: `test -f .env && grep -E '^TCAPTCHA_LLM_(API_KEY|BASE_URL)=.+' .env | wc -l`

Expected: `2` (both fields non-empty). If not, stop and have the user fill them in.

- [ ] **Step 2: Run the failing appid**

Run: `uv run crack-tcaptcha solve --appid 2046626712 --retries 3`

Expected (happy path): `OK  ticket=t03_xxxx...  randstr=xxxx  attempts=1..3`

Expected log hallmarks along the way:
- `classified type=image_select rule=image_select_show_type instruction='...蓝色的蝴蝶...'` (or similar)
- `image download: https://turing.captcha.qcloud.com/... → HTTP 200, >0 bytes` for `img_index=1` (and **no** `img_index=0` fetch)
- `LLM picked region_id=N for instruction=...`
- `verify response: {"errorCode":"0", ...}`

- [ ] **Step 3: If it fails, capture diagnostic**

If the output is `FAIL`, rerun with debug logging:

```bash
TCAPTCHA_LOG_LEVEL=DEBUG uv run crack-tcaptcha solve --appid 2046626712 --retries 1 2>&1 | tee /tmp/crack-debug.log
```

Inspect `/tmp/crack-debug.log` for:
- classifier output (must be `image_select`)
- LLM raw reply (may show format drift or region_id out-of-range)
- verify response `errorCode` and `errMessage`

Do NOT commit speculative fixes — diagnose first.

- [ ] **Step 4: Spot-check a slide appid (no regression)**

If you have a known-working slide appid on hand, run:

```bash
uv run crack-tcaptcha solve --appid <slide-appid>
```

Expected: `classified type=slide rule=slide_fg_binding ...` followed by `OK  ticket=...`

If no slide appid available, skip this step.

- [ ] **Step 5: Final commit (docs / notes if needed)**

If the run succeeded without any code change, nothing to commit. If you found a bug that required a fix in one of the earlier tasks, amend or cherry-commit the fix with a clear message.

---

## Notes for the implementer

- **DRY check:** `finish_with_verify` is the only place that calls `client.verify`. Pipelines must not duplicate that logic.
- **YAGNI check:** No shape_click / silent / spatial_vtt code. No artifacts dumper. No concurrent retries.
- **TDD note:** The spec defers new unit tests. You are not blocked from adding one if a particular step feels fragile — keep it minimal and don't invent new dependencies.
- **Frequent commits:** each task ends with a commit. If a task's implementation takes multiple real-world iterations, add intermediate commits before the task's final commit.
- **Don't reintroduce the old `subsid` increment.** Every attempt uses `subsid=1`. The classifier decides the pipeline; subsid doesn't.
