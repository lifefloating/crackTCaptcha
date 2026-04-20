"""GPT-5.4 vision client (OpenAI-compatible relay) for image_select + word_click captcha.

Expects TCAPTCHA_LLM_API_KEY, TCAPTCHA_LLM_BASE_URL, TCAPTCHA_LLM_MODEL in env / .env.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from typing import Any

import httpx
from PIL import Image, ImageDraw

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


# ---------------------------------------------------------------------------
# word_click: locate target chars on bg given detector bboxes
# ---------------------------------------------------------------------------


def _annotate_bg(bg_bytes: bytes, bboxes: list[tuple[int, int, int, int]]) -> bytes:
    """Return bg PNG with each bbox outlined + labeled 1..N to help the LLM."""
    img = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    for i, (x1, y1, x2, y2) in enumerate(bboxes, start=1):
        draw.rectangle((x1, y1, x2, y2), outline=(255, 0, 0), width=3)
        lx, ly = x1 + 2, max(0, y1 - 18)
        draw.rectangle((lx, ly, lx + 22, ly + 18), fill=(255, 0, 0))
        draw.text((lx + 5, ly + 2), str(i), fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _build_word_click_prompt(targets: list[str], bboxes: list[tuple[int, int, int, int]]) -> str:
    lines = [
        "The image shows several Chinese characters, each highlighted with a red box labeled 1, 2, 3, ...",
        f"There are {len(bboxes)} labeled boxes on the image.",
        "",
        "TASK: For each target character below, identify which labeled box contains that character.",
        f"Target characters (in order): {targets}",
        "",
        "Respond with ONLY a JSON object mapping each target character to its box number. Example:",
        '{"' + targets[0] + '": 2, "' + (targets[1] if len(targets) > 1 else "X") + '": 1}',
        "If a target character does NOT appear in any box, map it to 0.",
    ]
    return "\n".join(lines)


def _parse_char_to_box(text: str, targets: list[str], n_boxes: int) -> dict[str, int]:
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    candidate = m.group(0) if m else text
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError:
        try:
            obj = json.loads(candidate.replace("'", '"'))
        except json.JSONDecodeError as e:
            raise SolveError(f"LLM word_click: unparseable JSON: {text[:200]!r}") from e
    result: dict[str, int] = {}
    for ch in targets:
        v = obj.get(ch)
        try:
            iv = int(v)
        except (TypeError, ValueError):
            iv = 0
        if iv < 0 or iv > n_boxes:
            iv = 0
        result[ch] = iv
    return result


def locate_chars(
    bg_bytes: bytes,
    *,
    targets: list[str],
    bboxes: list[tuple[int, int, int, int]],
) -> dict[str, int]:
    """Ask the LLM which labeled bbox contains each target char.

    Returns dict {char: box_index_1_based}. 0 means "LLM says not found".
    Raises SolveError on config / transport / parse failure.
    """
    if not settings.llm_api_key or not settings.llm_base_url:
        raise SolveError("LLM not configured: set TCAPTCHA_LLM_API_KEY and TCAPTCHA_LLM_BASE_URL")
    if not bboxes:
        raise SolveError("LLM word_click: empty bboxes")

    annotated = _annotate_bg(bg_bytes, bboxes)
    prompt = _build_word_click_prompt(targets, bboxes)
    b64 = base64.b64encode(annotated).decode()

    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 128,
        "temperature": 0,
    }

    url = f"{settings.llm_base_url.rstrip('/')}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    last_err: Exception | None = None
    for attempt in (1, 2):
        try:
            with httpx.Client(timeout=settings.llm_timeout) as http:
                resp = http.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise SolveError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            log.info("LLM word_click raw (attempt %d): %s", attempt, content[:300])
            mapping = _parse_char_to_box(content, targets, len(bboxes))
            log.info("LLM word_click mapping: %s", mapping)
            return mapping
        except SolveError:
            raise
        except Exception as e:
            last_err = e
            log.warning("LLM word_click attempt %d failed: %s", attempt, e)
    raise SolveError(f"LLM word_click failed after 2 attempts: {last_err}")


__all__ = ["match_region", "locate_chars"]
