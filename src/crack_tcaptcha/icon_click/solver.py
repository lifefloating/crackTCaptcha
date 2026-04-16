"""Solver for click_image_uncheck captcha — pick the image matching a text instruction.

Uses Chinese-CLIP (cn-clip) to compute text-image similarity scores and select the
best-matching region.  Falls back to random selection if CLIP is unavailable.
"""

from __future__ import annotations

import io
import logging
import random

import numpy as np
from PIL import Image

from crack_tcaptcha.models import SelectRegion

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy CLIP loader
# ---------------------------------------------------------------------------

_clip_model = None
_clip_preprocess = None
_clip_tokenizer = None
_clip_available: bool | None = None


def _ensure_clip():
    """Lazily load Chinese-CLIP model. Returns True if available."""
    global _clip_model, _clip_preprocess, _clip_tokenizer, _clip_available

    if _clip_available is not None:
        return _clip_available

    try:
        import cn_clip.clip as clip
        from cn_clip.clip import load_from_name

        model, preprocess = load_from_name("ViT-B-16", device="cpu", download_root=None)
        model.eval()
        _clip_model = model
        _clip_preprocess = preprocess
        _clip_tokenizer = clip.tokenize
        _clip_available = True
        log.info("Chinese-CLIP loaded successfully")
    except Exception as e:
        log.warning("Chinese-CLIP not available (%s), will use random fallback", e)
        _clip_available = False

    return _clip_available


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def crop_regions(bg_bytes: bytes, regions: list[SelectRegion]) -> list[Image.Image]:
    """Crop sub-images from the background image according to region definitions."""
    bg_img = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
    crops = []
    for r in regions:
        x1, y1, x2, y2 = r.range
        crops.append(bg_img.crop((x1, y1, x2, y2)))
    return crops


def select_best_match(
    bg_bytes: bytes,
    regions: list[SelectRegion],
    instruction: str,
) -> int:
    """Select the region index (0-based) whose image best matches *instruction*.

    Returns the 0-based index into *regions*.
    """
    crops = crop_regions(bg_bytes, regions)

    # Strip surrounding quotes from instruction like '"足球"'
    keyword = instruction.strip().strip('""\u201c\u201d')

    if _ensure_clip():
        return _clip_select(crops, keyword)

    # Fallback: random
    log.warning("Using random selection (no CLIP model) for instruction: %s", keyword)
    return random.randint(0, len(regions) - 1)


def _clip_select(crops: list[Image.Image], keyword: str) -> int:
    """Use Chinese-CLIP to pick the best-matching crop for *keyword*."""
    import torch

    model = _clip_model
    preprocess = _clip_preprocess
    tokenize = _clip_tokenizer

    # Prepare images
    images = torch.stack([preprocess(img) for img in crops])

    # Prepare text
    text = tokenize([keyword])

    with torch.no_grad():
        image_features = model.encode_image(images)
        text_features = model.encode_text(text)

        # Normalize
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)

        # Cosine similarity
        similarities = (image_features @ text_features.T).squeeze(1)

    scores = similarities.cpu().numpy()
    best_idx = int(np.argmax(scores))
    log.info(
        "CLIP scores for '%s': %s → selected region %d (score=%.3f)",
        keyword,
        [f"{s:.3f}" for s in scores],
        best_idx,
        scores[best_idx],
    )
    return best_idx
