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
