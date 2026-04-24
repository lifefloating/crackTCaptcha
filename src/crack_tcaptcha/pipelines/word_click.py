"""word-click pipeline (2.0 turing.captcha.qcloud.com).

Challenge format (captured from real Chrome + TJCaptcha.js 2.0):
  - instruction: "请依次点击：X Y Z " (each X/Y/Z is one Chinese character)
  - data_type: ["DynAnswerType_POS"]
  - fg_elem_list: [] (hint chars are inline in instruction text)
  - bg_elem_cfg.size_2d: [672, 480]

Solver pipeline (primary path, local, ~50-200ms CPU):
  1. YOLOv8 detection → candidate char bboxes on bg
  2. Siamese similarity: render each target char with the bundled font
     and match it against every bbox crop; pick highest-scoring unused bbox.

Fallback (when the siamese extra is not installed or the YOLO stage
returns 0 bboxes): the legacy ddddocr detection + per-bbox OCR path.

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
from crack_tcaptcha.tdc.provider import TDCProvider
from crack_tcaptcha.trajectory import generate_click_trajectory, merge_trajectories

log = logging.getLogger(__name__)


def _parse_target_chars(instruction: str) -> list[str]:
    """Extract target CJK chars from '请依次点击：X Y Z '."""
    after = instruction.split("：", 1)[1] if "：" in instruction else instruction
    return re.findall(r"[\u4e00-\u9fff]", after)


def _fallback_ddddocr(bg_bytes: bytes, targets: list[str]) -> list[tuple[int, int]]:
    """Legacy ddddocr detect + per-bbox OCR fallback.

    Only used if the primary siamese path is unavailable (onnxruntime/cv2 not
    installed, model files missing, or YOLO produces no bboxes).
    """
    from crack_tcaptcha._legacy.icon_match import match_words

    log.info("word_click: falling back to ddddocr match_words")
    return match_words(bg_bytes, targets)


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
        pre.instruction,
        targets,
        len(bg_bytes),
    )

    # Primary path: local YOLO + Siamese (fast, no network, no API cost)
    click_coords: list[tuple[int, int]]
    try:
        from crack_tcaptcha.solvers.word_ocr import locate_chars_by_siamese

        click_coords = locate_chars_by_siamese(bg_bytes, targets)
    except SolveError as e:
        log.warning("word_click siamese path failed: %s — using ddddocr fallback", e)
        click_coords = _fallback_ddddocr(bg_bytes, targets)

    log.info("word_click click_coords=%s for targets=%s", click_coords, targets)

    pow_answer, pow_calc_time = solve_pow(
        pre.pow_cfg.prefix,
        pre.pow_cfg.target_md5,
        min_ms=300,
        max_ms=500,
    )

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
        client,
        pre,
        tdc_provider,
        ans_json=ans,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        trajectory=combined,
    )


__all__ = ["solve_one_attempt"]
