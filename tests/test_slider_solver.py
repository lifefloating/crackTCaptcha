"""Tests for slider/solver.py — NCC template matching on synthetic images.

Real TCaptcha: the bg still shows the gap as a shadow/outline. The piece
extracted from the sprite matches the *original* region (before darkening).
We simulate this by: keeping the original bg pixels, placing the piece at
a known location, and verifying NCC recovers that location.

Strategy: we put the piece (a distinctive pattern) into the bg at the gap
location, then NCC should find it with high confidence.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from crack_tcaptcha.models import FgElem
from crack_tcaptcha.pipelines.slide import SliderSolver


def _make_synthetic_pair(
    bg_w: int = 672,
    bg_h: int = 390,
    pw: int = 110,
    ph: int = 110,
    gap_x: int = 300,
    gap_y: int = 150,
    seed: int = 42,
) -> tuple[bytes, bytes, FgElem]:
    """Create a synthetic pair where the piece pattern is present in the bg.

    The bg has a unique gradient+noise block at (gap_x, gap_y). The piece
    extracted from the sprite has the same pattern. NCC must find it.
    """
    rng = np.random.default_rng(seed)

    # smooth gradient bg so most of it looks different from the piece
    yy, xx = np.mgrid[:bg_h, :bg_w]
    bg = np.stack(
        [
            ((xx / bg_w) * 180 + 20).astype(np.uint8),
            ((yy / bg_h) * 160 + 40).astype(np.uint8),
            np.full((bg_h, bg_w), 100, dtype=np.uint8),
        ],
        axis=-1,
    )

    # create a distinctive piece pattern (checkerboard + noise)
    piece_pat = np.zeros((ph, pw, 3), dtype=np.uint8)
    for r in range(ph):
        for c in range(pw):
            if (r // 10 + c // 10) % 2 == 0:
                piece_pat[r, c] = [200, 50, 50]
            else:
                piece_pat[r, c] = [50, 200, 50]
    piece_pat = piece_pat.astype(np.int16) + rng.integers(-10, 10, piece_pat.shape, dtype=np.int16)
    piece_pat = np.clip(piece_pat, 0, 255).astype(np.uint8)

    # embed piece pattern into bg at gap location
    bg[gap_y : gap_y + ph, gap_x : gap_x + pw] = piece_pat

    # bg → PNG bytes
    bg_img = Image.fromarray(bg, "RGB")
    buf_bg = io.BytesIO()
    bg_img.save(buf_bg, "PNG")
    bg_bytes = buf_bg.getvalue()

    # fg sprite: piece RGBA at (0, 0), rest transparent
    sprite_w, sprite_h = 682, 620
    sprite = np.zeros((sprite_h, sprite_w, 4), dtype=np.uint8)
    sprite[:ph, :pw, :3] = piece_pat
    sprite[:ph, :pw, 3] = 255  # opaque
    fg_img = Image.fromarray(sprite, "RGBA")
    buf_fg = io.BytesIO()
    fg_img.save(buf_fg, "PNG")
    fg_bytes = buf_fg.getvalue()

    elem = FgElem(
        elem_id=1,
        sprite_pos=(0, 0),
        size_2d=(pw, ph),
        init_pos=(30, gap_y),
    )
    return bg_bytes, fg_bytes, elem


class TestSliderSolver:
    def test_synthetic_accuracy(self):
        """NCC should locate the gap within ≤3 px on a synthetic sample."""
        true_x, true_y = 300, 150
        bg, fg, elem = _make_synthetic_pair(gap_x=true_x, gap_y=true_y)
        solver = SliderSolver()
        found_x, found_y, ncc = solver.solve(bg, fg, elem)
        assert abs(found_x - true_x) <= 3, f"X off by {abs(found_x - true_x)}"
        assert abs(found_y - true_y) <= 5, f"Y off by {abs(found_y - true_y)}"
        assert ncc > 0.5

    def test_different_positions(self):
        for i, gx in enumerate((100, 250, 450)):
            bg, fg, elem = _make_synthetic_pair(gap_x=gx, gap_y=150, seed=100 + i)
            solver = SliderSolver()
            found_x, _, ncc = solver.solve(bg, fg, elem)
            assert abs(found_x - gx) <= 3, f"gap_x={gx}, found={found_x}"
            assert ncc > 0.5

    def test_high_ncc_confidence(self):
        bg, fg, elem = _make_synthetic_pair()
        solver = SliderSolver()
        _, _, ncc = solver.solve(bg, fg, elem)
        assert ncc > 0.9, f"NCC too low: {ncc}"
