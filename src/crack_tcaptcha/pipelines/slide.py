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
    """Solve a TCaptcha slider challenge via NCC template matching."""

    def __init__(self, *, y_search_range: int = 5) -> None:
        self.y_search_range = y_search_range

    def solve(self, bg_bytes: bytes, fg_bytes: bytes, piece: FgElem) -> tuple[int, int, float]:
        bg_arr = np.array(Image.open(io.BytesIO(bg_bytes)).convert("RGB"))
        fg_img = Image.open(io.BytesIO(fg_bytes))  # RGBA sprite

        px, py = piece.sprite_pos
        pw, ph = piece.size_2d
        piece_rgba = np.array(fg_img.crop((px, py, px + pw, py + ph)))

        init_x, init_y = piece.init_pos
        gap_x, gap_y, ncc = self._ncc_match(bg_arr, piece_rgba, init_y, pw, ph)
        return gap_x, gap_y, ncc

    def _ncc_match(
        self,
        bg: np.ndarray,
        piece_rgba: np.ndarray,
        init_y: int,
        pw: int,
        ph: int,
    ) -> tuple[int, int, float]:
        piece_rgb = piece_rgba[:, :, :3].astype(np.float32)
        alpha = piece_rgba[:, :, 3]
        mask = alpha > 128

        if mask.sum() < 100:
            return 0, init_y, -1.0

        piece_flat = piece_rgb[mask]
        piece_centered = piece_flat - piece_flat.mean()
        piece_norm = float(np.sqrt((piece_centered**2).sum())) + 1e-8

        bg_f = bg[:, :, :3].astype(np.float32)
        bh, bw = bg_f.shape[:2]
        x_max = bw - pw
        y_min = max(0, init_y - self.y_search_range)
        y_max = min(bh - ph, init_y + self.y_search_range)

        # --- Phase 1: coarse (stride=4 on init_y row) ---
        coarse_x = 0
        coarse_ncc = -2.0
        for x in range(0, x_max + 1, 4):
            ncc = self._ncc_at(bg_f, mask, piece_centered, piece_norm, x, init_y, pw, ph)
            if ncc > coarse_ncc:
                coarse_ncc = ncc
                coarse_x = x

        # --- Phase 2: fine (±6 X, ±5 Y around coarse) ---
        fine_x_min = max(0, coarse_x - 6)
        fine_x_max = min(x_max, coarse_x + 7)
        best_x, best_y = coarse_x, init_y
        best_ncc = coarse_ncc
        for y in range(y_min, y_max + 1):
            for x in range(fine_x_min, fine_x_max):
                ncc = self._ncc_at(bg_f, mask, piece_centered, piece_norm, x, y, pw, ph)
                if ncc > best_ncc:
                    best_ncc = ncc
                    best_x = x
                    best_y = y

        return best_x, best_y, float(best_ncc)

    @staticmethod
    def _ncc_at(
        bg_f: np.ndarray,
        mask: np.ndarray,
        piece_centered: np.ndarray,
        piece_norm: float,
        x: int,
        y: int,
        pw: int,
        ph: int,
    ) -> float:
        patch = bg_f[y : y + ph, x : x + pw]
        patch_flat = patch[mask]
        patch_centered = patch_flat - patch_flat.mean()
        patch_norm = float(np.sqrt((patch_centered**2).sum())) + 1e-8
        return float((patch_centered * piece_centered).sum() / (patch_norm * piece_norm))


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

    pow_answer, pow_calc_time = solve_pow(
        pre.pow_cfg.prefix,
        pre.pow_cfg.target_md5,
        min_ms=300,
        max_ms=500,
    )

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
        client,
        pre,
        tdc_provider,
        ans_json=ans,
        pow_answer=pow_answer,
        pow_calc_time=pow_calc_time,
        trajectory=traj,
    )


__all__ = ["solve_one_attempt", "SliderSolver"]
