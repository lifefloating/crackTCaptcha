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
