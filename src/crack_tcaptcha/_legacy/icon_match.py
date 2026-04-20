"""Legacy icon-click solver — ddddocr-based icon matching (for fg_elem_list captchas)."""

from __future__ import annotations

import io
import logging
import re
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    import ddddocr

log = logging.getLogger(__name__)

_det: ddddocr.DdddOcr | None = None
_ocr: ddddocr.DdddOcr | None = None


def _get_det() -> ddddocr.DdddOcr:
    global _det
    if _det is None:
        import ddddocr

        _det = ddddocr.DdddOcr(det=True, show_ad=False)
    return _det


def _get_ocr() -> ddddocr.DdddOcr:
    global _ocr
    if _ocr is None:
        import ddddocr

        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def detect_icons(bg_bytes: bytes) -> list[tuple[int, int, int, int]]:
    det = _get_det()
    bboxes = det.detection(bg_bytes)
    return [(int(b[0]), int(b[1]), int(b[2]), int(b[3])) for b in bboxes]


def match_words(
    bg_bytes: bytes,
    target_chars: list[str],
) -> list[tuple[int, int]]:
    """Locate each target character on bg via det+ocr.

    Flow: detection → crop each bbox → OCR → map char → bbox center.
    For each target char, prefers exact single-char match; falls back to
    the highest-confidence bbox whose OCR contains the char.

    Returns a list of (cx, cy) in the order of `target_chars`. If a char
    cannot be found, its coord falls back to bg center.
    """
    bboxes = detect_icons(bg_bytes)
    if not bboxes:
        log.warning("word-click: no bboxes detected on bg")
        return [(0, 0)] * len(target_chars)

    bg_img = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
    bg_w, bg_h = bg_img.size
    ocr = _get_ocr()

    # Pad each bbox slightly; ddddocr OCR works better with some margin.
    pad = 2
    results: list[dict] = []  # each: {bbox, center, ocr_text}
    for x1, y1, x2, y2 in bboxes:
        px1 = max(0, x1 - pad)
        py1 = max(0, y1 - pad)
        px2 = min(bg_w, x2 + pad)
        py2 = min(bg_h, y2 + pad)
        crop = bg_img.crop((px1, py1, px2, py2))
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        try:
            text = ocr.classification(buf.getvalue()) or ""
        except Exception as e:  # pragma: no cover - defensive
            log.warning("word-click: ocr failed on bbox %s: %s", (x1, y1, x2, y2), e)
            text = ""
        # Keep only CJK chars from OCR output; ddddocr sometimes returns stray punctuation.
        text = re.sub(r"[^\u4e00-\u9fff]", "", text)
        results.append(
            {
                "bbox": (x1, y1, x2, y2),
                "center": ((x1 + x2) // 2, (y1 + y2) // 2),
                "ocr": text,
            }
        )

    log.info(
        "word-click det+ocr: %d bboxes, ocr=%s",
        len(results),
        [(r["bbox"], r["ocr"]) for r in results],
    )

    used: set[int] = set()
    click_coords: list[tuple[int, int]] = []
    for ch in target_chars:
        pick_idx = -1
        # 1) exact single-char match
        for i, r in enumerate(results):
            if i in used:
                continue
            if r["ocr"] == ch:
                pick_idx = i
                break
        # 2) char appears in multi-char ocr
        if pick_idx < 0:
            for i, r in enumerate(results):
                if i in used:
                    continue
                if ch in r["ocr"]:
                    pick_idx = i
                    break
        # 3) fallback: first unused bbox (better than (0,0); visible click)
        if pick_idx < 0:
            for i in range(len(results)):
                if i not in used:
                    pick_idx = i
                    break
        if pick_idx < 0:
            # All used — reuse any. Shouldn't happen if len(bboxes) >= len(targets).
            pick_idx = 0
        used.add(pick_idx)
        click_coords.append(results[pick_idx]["center"])
        log.info(
            "word-click: target=%r → bbox=%s ocr=%r center=%s",
            ch,
            results[pick_idx]["bbox"],
            results[pick_idx]["ocr"],
            results[pick_idx]["center"],
        )

    return click_coords


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
