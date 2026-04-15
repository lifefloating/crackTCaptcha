"""NCC (Normalized Cross-Correlation) two-phase template matching for slider captcha.

Algorithm:
    1. Coarse search: stride=4 along init_y row  →  ~168 evaluations.
    2. Fine search: ±6px X, ±5px Y around coarse peak  →  ~143 evaluations.

    Total ≈311 vs full-image 262,080  →  ~842× speed-up.
    Accuracy: MAE ≤ 1.0px on real samples.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from crack_tcaptcha.models import FgElem


class SliderSolver:
    """Solve a TCaptcha slider challenge via NCC template matching."""

    def __init__(self, *, y_search_range: int = 5):
        self.y_search_range = y_search_range

    def solve(
        self,
        bg_bytes: bytes,
        fg_bytes: bytes,
        piece_elem: FgElem,
    ) -> tuple[int, int, float]:
        """Return ``(target_x, target_y, ncc_score)`` for the piece destination.

        *target_x/y* are absolute pixel coordinates on the 672×390 background.
        """
        bg_arr = np.array(Image.open(io.BytesIO(bg_bytes)).convert("RGB"))
        fg_img = Image.open(io.BytesIO(fg_bytes))  # RGBA sprite

        px, py = piece_elem.sprite_pos
        pw, ph = piece_elem.size_2d
        piece_rgba = np.array(fg_img.crop((px, py, px + pw, py + ph)))

        init_x, init_y = piece_elem.init_pos
        gap_x, gap_y, ncc = self._ncc_match(bg_arr, piece_rgba, init_y, pw, ph)
        return gap_x, gap_y, ncc

    # ------------------------------------------------------------------

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
        bg: np.ndarray,
        mask: np.ndarray,
        piece_centered: np.ndarray,
        piece_norm: float,
        x: int,
        y: int,
        pw: int,
        ph: int,
    ) -> float:
        region = bg[y : y + ph, x : x + pw]
        region_flat = region[mask]
        rc = region_flat - region_flat.mean()
        rn = float(np.sqrt((rc**2).sum())) + 1e-8
        return float((piece_centered * rc).sum() / (piece_norm * rn))
