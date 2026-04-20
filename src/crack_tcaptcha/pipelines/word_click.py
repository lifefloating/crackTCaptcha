"""word-click pipeline (2.0 turing.captcha.qcloud.com).

Challenge format (captured from real Chrome + TJCaptcha.js 2.0):
  - instruction: "请依次点击：X Y Z " (each X/Y/Z is one Chinese character)
  - data_type: ["DynAnswerType_POS"]
  - fg_elem_list: [] (hint chars are inline in instruction text)
  - bg_elem_cfg.size_2d: [672, 480]

Solver pipeline:
  1. ddddocr detection → candidate char bboxes on bg
  2. LLM vision (primary): sends annotated bg + target chars → gets {char: bbox_idx}
  3. ddddocr OCR (fallback): if LLM unavailable or returns 0/miss for a char,
     run per-bbox ddddocr classification and substring-match.

Answer format (confirmed against real Chrome verify body):
  [{"elem_id": 1, "type": "DynAnswerType_POS", "data": "x,y"}, ...]
"""

from __future__ import annotations

import json
import logging
import re

from crack_tcaptcha.client import TCaptchaClient
from crack_tcaptcha.exceptions import SolveError
from crack_tcaptcha.models import PrehandleResp, VerifyResp
from crack_tcaptcha.pipelines._common import finish_with_verify
from crack_tcaptcha.pow import solve_pow
from crack_tcaptcha.settings import settings
from crack_tcaptcha.tdc.provider import TDCProvider
from crack_tcaptcha.trajectory import generate_click_trajectory, merge_trajectories

log = logging.getLogger(__name__)


def _parse_target_chars(instruction: str) -> list[str]:
    """Extract target CJK chars from '请依次点击：X Y Z '."""
    after = instruction.split("：", 1)[1] if "：" in instruction else instruction
    return re.findall(r"[\u4e00-\u9fff]", after)


def _bbox_center(bbox: tuple[int, int, int, int]) -> tuple[int, int]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def _fallback_match_by_ocr(
    bg_bytes: bytes,
    bboxes: list[tuple[int, int, int, int]],
    targets: list[str],
    already_assigned: dict[str, int],
) -> dict[str, int]:
    """Per-bbox ddddocr classify + substring match for chars LLM missed."""
    from crack_tcaptcha._legacy.icon_match import _get_ocr
    import io
    from PIL import Image

    missing = [ch for ch in targets if already_assigned.get(ch, 0) <= 0]
    if not missing:
        return already_assigned

    ocr = _get_ocr()
    bg_img = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
    bg_w, bg_h = bg_img.size
    used_indices = {v for v in already_assigned.values() if v > 0}
    bbox_ocr: dict[int, str] = {}
    for i, (x1, y1, x2, y2) in enumerate(bboxes, start=1):
        if i in used_indices:
            continue
        pad = 2
        crop = bg_img.crop((max(0, x1 - pad), max(0, y1 - pad), min(bg_w, x2 + pad), min(bg_h, y2 + pad)))
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        try:
            text = ocr.classification(buf.getvalue()) or ""
        except Exception as e:  # pragma: no cover - defensive
            log.warning("word_click fallback ocr error on bbox %d: %s", i, e)
            text = ""
        text = re.sub(r"[^\u4e00-\u9fff]", "", text)
        bbox_ocr[i] = text
    log.info("word_click fallback ocr on %d unused bboxes: %s", len(bbox_ocr), bbox_ocr)

    result = dict(already_assigned)
    for ch in missing:
        for i, text in bbox_ocr.items():
            if i in used_indices:
                continue
            if ch in text:
                result[ch] = i
                used_indices.add(i)
                log.info("word_click fallback: %r → bbox %d via ocr=%r", ch, i, text)
                break
    # Final fallback: assign any remaining char to first unused bbox (visible click
    # better than (0,0) which is guaranteed wrong).
    for ch in targets:
        if result.get(ch, 0) > 0:
            continue
        for i in range(1, len(bboxes) + 1):
            if i not in used_indices:
                result[ch] = i
                used_indices.add(i)
                log.info("word_click fallback: %r → bbox %d (last-resort)", ch, i)
                break
    return result


def solve_one_attempt(
    client: TCaptchaClient,
    pre: PrehandleResp,
    tdc_provider: TDCProvider,
) -> VerifyResp:
    """One word-click attempt. Raises SolveError on unrecoverable failures."""
    targets = _parse_target_chars(pre.instruction)
    if not targets:
        raise SolveError(f"word_click: no CJK chars in instruction={pre.instruction!r}")

    bg_bytes = client.get_image(pre.bg_elem_cfg.img_url)
    log.info(
        "word_click: instruction=%r targets=%s bg=%d bytes",
        pre.instruction, targets, len(bg_bytes),
    )

    try:
        from crack_tcaptcha._legacy.icon_match import detect_icons
    except ImportError as e:
        raise SolveError(
            "word_click requires ddddocr: `uv sync --extra icon-click`"
        ) from e

    bboxes = detect_icons(bg_bytes)
    if len(bboxes) < len(targets):
        log.warning(
            "word_click: only %d bboxes detected for %d targets",
            len(bboxes), len(targets),
        )
    if not bboxes:
        raise SolveError("word_click: detector returned 0 bboxes")
    log.info("word_click detection: %d bboxes=%s", len(bboxes), bboxes)

    # Primary: LLM vision (more reliable than ddddocr OCR on captcha fonts)
    char_to_box: dict[str, int] = {}
    llm_ok = bool(settings.llm_api_key and settings.llm_base_url)
    if llm_ok:
        try:
            from crack_tcaptcha.solvers.llm_vision import locate_chars

            char_to_box = locate_chars(bg_bytes, targets=targets, bboxes=bboxes)
        except SolveError as e:
            log.warning("word_click: LLM locate_chars failed, falling back: %s", e)
    else:
        log.info("word_click: LLM not configured, using ddddocr fallback only")

    # Fallback for any char LLM returned 0 / miss
    char_to_box = _fallback_match_by_ocr(bg_bytes, bboxes, targets, char_to_box)

    click_coords: list[tuple[int, int]] = []
    for ch in targets:
        idx = char_to_box.get(ch, 0)
        if 1 <= idx <= len(bboxes):
            click_coords.append(_bbox_center(bboxes[idx - 1]))
        else:
            # Should not happen after fallback, but be safe.
            cx, cy = _bbox_center(bboxes[0])
            click_coords.append((cx, cy))
            log.warning("word_click: char %r unresolved, using bbox 1", ch)
    log.info("word_click click_coords=%s for targets=%s", click_coords, targets)

    pow_answer, pow_calc_time = solve_pow(pre.pow_cfg.prefix, pre.pow_cfg.target_md5)

    ans_list = [
        {
            "elem_id": i + 1,
            "type": "DynAnswerType_POS",
            "data": f"{cx},{cy}",
        }
        for i, (cx, cy) in enumerate(click_coords)
    ]
    ans = json.dumps(ans_list)

    traj_segments = []
    prev_x, prev_y = 0, 0
    for cx, cy in click_coords:
        traj_segments.append(generate_click_trajectory(prev_x, prev_y, cx, cy))
        prev_x, prev_y = cx, cy
    combined = merge_trajectories(traj_segments)
    combined = combined.model_copy(update={"kind": "click"})

    return finish_with_verify(
        client, pre, tdc_provider,
        ans_json=ans,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        trajectory=combined,
    )


__all__ = ["solve_one_attempt"]
