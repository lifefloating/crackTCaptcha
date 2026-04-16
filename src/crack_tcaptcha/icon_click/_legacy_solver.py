"""Legacy icon-click solver — ddddocr-based icon matching (for fg_elem_list captchas)."""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    import ddddocr

log = logging.getLogger(__name__)

_det: ddddocr.DdddOcr | None = None


def _get_det() -> ddddocr.DdddOcr:
    global _det
    if _det is None:
        import ddddocr

        _det = ddddocr.DdddOcr(det=True, show_ad=False)
    return _det


def detect_icons(bg_bytes: bytes) -> list[tuple[int, int, int, int]]:
    det = _get_det()
    bboxes = det.detection(bg_bytes)
    return [(int(b[0]), int(b[1]), int(b[2]), int(b[3])) for b in bboxes]


def match_icons(
    bg_bytes: bytes,
    hint_images: list[bytes],
) -> list[tuple[int, int]]:
    """Match each hint icon to its best candidate on the bg, returning click coordinates."""
    bboxes = detect_icons(bg_bytes)
    if not bboxes:
        log.warning("No candidates detected on bg")
        return []

    bg_arr = np.array(Image.open(io.BytesIO(bg_bytes)).convert("RGB"))
    results: list[tuple[int, int]] = []

    for hint_bytes in hint_images:
        hint_arr = np.array(Image.open(io.BytesIO(hint_bytes)).convert("RGB"))
        best_score = -1.0
        best_center = (0, 0)

        for x1, y1, x2, y2 in bboxes:
            candidate = bg_arr[y1:y2, x1:x2]
            if candidate.size == 0:
                continue
            hint_resized = np.array(Image.fromarray(hint_arr).resize((x2 - x1, y2 - y1), Image.LANCZOS))
            h = hint_resized.astype(np.float32).ravel()
            c = candidate.astype(np.float32).ravel()
            h_c = h - h.mean()
            c_c = c - c.mean()
            denom = (np.sqrt((h_c**2).sum()) * np.sqrt((c_c**2).sum())) + 1e-8
            score = float((h_c * c_c).sum() / denom)

            if score > best_score:
                best_score = score
                best_center = ((x1 + x2) // 2, (y1 + y2) // 2)

        results.append(best_center)
        log.debug("icon matched at %s with score %.3f", best_center, best_score)

    return results
