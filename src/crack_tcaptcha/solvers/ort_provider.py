"""ONNX Runtime execution-provider selection.

Honours the ``TCAPTCHA_ORT_BACKEND`` env var (``cuda`` / ``rocm`` / ``dml`` /
``coreml`` / ``cpu`` / ``auto``, default ``auto``). Falls back to CPU when the
requested backend is unavailable.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_BACKEND_MAP = {
    "cuda": "CUDAExecutionProvider",
    "rocm": "ROCMExecutionProvider",
    "dml": "DmlExecutionProvider",
    "coreml": "CoreMLExecutionProvider",
    "cpu": "CPUExecutionProvider",
}

_AUTO_PRIORITY = (
    "CUDAExecutionProvider",
    "ROCMExecutionProvider",
    "DmlExecutionProvider",
    "CoreMLExecutionProvider",
    "CPUExecutionProvider",
)


def resolve_providers() -> list[str]:
    """Return an ORT providers list, always terminated by CPU as a fallback."""
    import onnxruntime as ort

    available = set(ort.get_available_providers())
    pref = os.environ.get("TCAPTCHA_ORT_BACKEND", "auto").strip().lower()

    wanted = _BACKEND_MAP.get(pref)
    if wanted:
        if wanted in available:
            if wanted == "CPUExecutionProvider":
                return [wanted]
            return [wanted, "CPUExecutionProvider"]
        log.warning(
            "TCAPTCHA_ORT_BACKEND=%s requested %s, not available (%s) — falling back to auto",
            pref,
            wanted,
            sorted(available),
        )

    for p in _AUTO_PRIORITY:
        if p in available:
            if p == "CPUExecutionProvider":
                return [p]
            return [p, "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


__all__ = ["resolve_providers"]
