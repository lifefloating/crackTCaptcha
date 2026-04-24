"""word-click solver: YOLOv8 detection + Siamese similarity matching.

Replaces the slow LLM-vision path for ``word_click``. Uses two ONNX models
shipped with the package:

* ``yolo_word.onnx`` — YOLOv8 detector, finds candidate character bboxes on
  the bg image.
* ``siamese_word.onnx`` — Siamese network, takes two 52×52 RGB crops and
  returns a similarity score.

Target characters are rendered with the bundled ``font.ttf`` into 52×52
reference images and compared against every detected bbox crop. The
highest-scoring unused bbox is picked for each target, in order.

Performance notes:

* Sessions are module-level singletons (one load per Python process).
* ``SessionOptions`` enables all graph optimisations and pins thread count
  to a sensible default.
* Siamese inference is **batched**: for each target char we stack all
  candidate crops into one call (N forward passes → 1 call), which is the
  main win over a naïve loop.
* ``warmup()`` can be called at startup to amortise first-inference cost
  (CoreML / CUDA graph compile).
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np

from crack_tcaptcha.exceptions import SolveError
from crack_tcaptcha.solvers.ort_provider import resolve_providers

log = logging.getLogger(__name__)

_MODEL_DIR = Path(__file__).resolve().parent / "models"
_YOLO_PATH = _MODEL_DIR / "word_click_detector.onnx"
_SIAMESE_PATH = _MODEL_DIR / "word_click_matcher.onnx"
_FONT_PATH = _MODEL_DIR / "font.ttf"

_YOLO_CONFIDENCE = 0.5
_YOLO_IOU = 0.7
_SIAMESE_INPUT = (52, 52)
_CHAR_RENDER_SIZE = 52
_CHAR_RENDER_FONT_SIZE = 40
_CHAR_RENDER_COLOR = (227, 178, 56)  # BGR order when fed to siamese


# --- lazy model singletons ---------------------------------------------------

_yolo_lock = threading.Lock()
_siamese_lock = threading.Lock()
_yolo_session = None
_siamese_session = None
_siamese_input_names: tuple[str, str] | None = None
_siamese_batch_supported: bool | None = None


def _import_onnx():
    try:
        import onnxruntime  # noqa: F401
    except ImportError as e:  # pragma: no cover - import guard
        raise SolveError(
            "word_click requires onnxruntime: `uv sync --extra word-click`"
        ) from e


def _import_cv2():
    try:
        import cv2
    except ImportError as e:  # pragma: no cover - import guard
        raise SolveError(
            "word_click requires opencv-python-headless: `uv sync --extra word-click`"
        ) from e
    return cv2


def _make_session_options():
    """Build a tuned SessionOptions instance."""
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    # The siamese net is tiny (52×52 input) — beyond ~4 intra-op threads we
    # measurably regress from scheduling overhead. Cap at 4 by default;
    # override via TCAPTCHA_ORT_INTRA_OP_THREADS for specific hardware.
    env_threads = os.environ.get("TCAPTCHA_ORT_INTRA_OP_THREADS")
    if env_threads and env_threads.isdigit() and int(env_threads) > 0:
        threads = int(env_threads)
    else:
        try:
            threads = min(4, os.cpu_count() or 4)
        except Exception:
            threads = 4
    so.intra_op_num_threads = max(1, threads)
    so.inter_op_num_threads = 1
    so.log_severity_level = 3  # ERROR only
    return so


def _get_yolo_session():
    global _yolo_session
    if _yolo_session is not None:
        return _yolo_session
    with _yolo_lock:
        if _yolo_session is not None:
            return _yolo_session
        _import_onnx()
        import onnxruntime as ort

        if not _YOLO_PATH.is_file():
            raise SolveError(f"word_click: missing yolo model at {_YOLO_PATH}")
        so = _make_session_options()
        _yolo_session = ort.InferenceSession(
            str(_YOLO_PATH), sess_options=so, providers=resolve_providers()
        )
        log.info("word_click yolo session providers=%s", _yolo_session.get_providers())
    return _yolo_session


def _get_siamese_session():
    global _siamese_session, _siamese_input_names
    if _siamese_session is not None:
        return _siamese_session
    with _siamese_lock:
        if _siamese_session is not None:
            return _siamese_session
        _import_onnx()
        import onnxruntime as ort

        if not _SIAMESE_PATH.is_file():
            raise SolveError(f"word_click: missing siamese model at {_SIAMESE_PATH}")
        so = _make_session_options()
        _siamese_session = ort.InferenceSession(
            str(_SIAMESE_PATH), sess_options=so, providers=resolve_providers()
        )
        inputs = _siamese_session.get_inputs()
        _siamese_input_names = (inputs[0].name, inputs[1].name)
        log.info(
            "word_click siamese session providers=%s inputs=%s",
            _siamese_session.get_providers(),
            [(i.name, i.shape) for i in inputs],
        )
    return _siamese_session


# --- image helpers -----------------------------------------------------------


def _bytes_to_bgr(byte_data: bytes) -> np.ndarray:
    cv2 = _import_cv2()
    arr = np.frombuffer(byte_data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise SolveError("word_click: failed to decode bg image")
    if len(img.shape) == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[-1] == 4:
        alpha = img[..., 3:4].astype(np.float32) / 255.0
        rgb = img[..., :3].astype(np.float32)
        white = np.full_like(rgb, 255.0)
        return (rgb * alpha + white * (1 - alpha)).astype(np.uint8)
    return img


def _render_char(char: str) -> np.ndarray:
    """Render one CJK char to a 52×52 BGR image using the bundled font."""
    from PIL import Image, ImageDraw, ImageFont

    if not _FONT_PATH.is_file():
        raise SolveError(f"word_click: missing font at {_FONT_PATH}")
    img = Image.new("RGB", (_CHAR_RENDER_SIZE, _CHAR_RENDER_SIZE), color="white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(_FONT_PATH), _CHAR_RENDER_FONT_SIZE)
    bbox = font.getbbox(char)
    text_w = bbox[2] - bbox[0]
    x = (_CHAR_RENDER_SIZE - text_w) // 2
    y = -3
    draw.text((x, y), char, fill=_CHAR_RENDER_COLOR, font=font)
    cv2 = _import_cv2()
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


# --- YOLOv8 detection --------------------------------------------------------


def _letterbox(
    img: np.ndarray,
    new_shape: tuple[int, int],
) -> tuple[np.ndarray, tuple[float, float], tuple[float, float]]:
    cv2 = _import_cv2()
    h, w = img.shape[:2]
    r = min(new_shape[0] / h, new_shape[1] / w)
    new_unpad = (int(round(w * r)), int(round(h * r)))
    dw = (new_shape[1] - new_unpad[0]) / 2
    dh = (new_shape[0] - new_unpad[1]) / 2
    resized = cv2.resize(img, new_unpad, interpolation=cv2.INTER_LINEAR)
    top = int(round(dh - 0.1))
    bottom = int(round(dh + 0.1))
    left = int(round(dw - 0.1))
    right = int(round(dw + 0.1))
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    return padded, (r, r), (left, top)


def _yolo_detect(bg_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Run YOLOv8 and return [(x1, y1, x2, y2), ...] on the original image."""
    cv2 = _import_cv2()
    sess = _get_yolo_session()
    inp = sess.get_inputs()[0]
    _, _, ih, iw = inp.shape

    letter, (rx, ry), (pad_x, pad_y) = _letterbox(bg_bgr, (ih, iw))
    rgb = cv2.cvtColor(letter, cv2.COLOR_BGR2RGB)
    data = (np.array(rgb) / 255.0).transpose(2, 0, 1)[None].astype(np.float32)

    outputs = sess.run(None, {inp.name: data})[0]
    preds = np.transpose(np.squeeze(outputs))

    boxes: list[list[float]] = []
    scores: list[float] = []
    for row in preds:
        class_scores = row[4:]
        max_score = float(np.amax(class_scores))
        if max_score < _YOLO_CONFIDENCE:
            continue
        x, y, w, h = row[0], row[1], row[2], row[3]
        left = int(((x - w / 2) - pad_x) / rx)
        top = int(((y - h / 2) - pad_y) / ry)
        width = int(w / rx)
        height = int(h / ry)
        boxes.append([left, top, width, height])
        scores.append(max_score)

    if not boxes:
        return []

    indices = cv2.dnn.NMSBoxes(boxes, scores, _YOLO_CONFIDENCE, _YOLO_IOU)
    bh, bw = bg_bgr.shape[:2]
    out: list[tuple[int, int, int, int]] = []
    for i in np.array(indices).flatten():
        x, y, w, h = boxes[int(i)]
        x1 = int(max(0, x))
        y1 = int(max(1, y))  # mirror tx-word quirk: y1<=0 clamped to 1
        x2 = int(min(bw, x + w))
        y2 = int(min(bh, y + h))
        if x2 > x1 and y2 > y1:
            out.append((x1, y1, x2, y2))
    return out


# --- Siamese matching --------------------------------------------------------


def _prep_siamese(img: np.ndarray) -> np.ndarray:
    """Preprocess a single BGR crop to (1, 3, 52, 52) float32 [0,1]."""
    cv2 = _import_cv2()
    resized = cv2.resize(img, _SIAMESE_INPUT)
    arr = np.transpose(resized, (2, 0, 1)).astype(np.float32) / 255.0
    return arr[None, ...]


def _siamese_score_batch(crops: list[np.ndarray], ref: np.ndarray) -> list[float]:
    """Score every crop against the ref in one (or as few as possible) ORT calls.

    Fast paths, in order:

    1. True batched inference (if the exported graph has a dynamic batch
       dim). Cached after first attempt.
    2. Thread-pool parallel per-pair calls. ``session.run`` releases the
       GIL, so this gives real parallelism on CPU EP with
       ``intra_op_num_threads>=2``.
    """
    global _siamese_batch_supported

    sess = _get_siamese_session()
    assert _siamese_input_names is not None
    n0, n1 = _siamese_input_names

    if not crops:
        return []

    ref_prepped = _prep_siamese(ref)  # (1,3,52,52)

    # 1) Try batched inference (only once — decision is cached).
    if _siamese_batch_supported is not False:
        try:
            batch = np.concatenate([_prep_siamese(c) for c in crops], axis=0)  # (N,3,52,52)
            refs = np.repeat(ref_prepped, batch.shape[0], axis=0)  # (N,3,52,52)
            pred = sess.run(None, {n0: batch, n1: refs})[0]
            arr = np.asarray(pred).reshape(-1)
            if arr.size == batch.shape[0]:
                _siamese_batch_supported = True
                return [float(v) for v in arr]
        except Exception as e:
            log.info("word_click siamese batch not supported, using per-pair: %s", e)
            _siamese_batch_supported = False

    # 2) Per-pair path. Preprocess everything up front (numpy ops, cheap),
    # then let ORT's own intra-op thread pool do the heavy lifting.
    # An outer ThreadPoolExecutor causes oversubscription vs intra_op_num_threads
    # and measurably slows things down — don't add one.
    prepped = [_prep_siamese(c) for c in crops]
    out: list[float] = []
    for p in prepped:
        pred = sess.run(None, {n0: p, n1: ref_prepped})[0]
        out.append(float(np.asarray(pred).reshape(-1)[0]))
    return out


# --- public API --------------------------------------------------------------


def warmup() -> None:
    """Load both sessions and run one dummy inference each.

    Call this once at process start (e.g. CLI entry point) to amortise the
    first-request cost (graph optimisation, kernel JIT, CoreML compile).
    """
    try:
        _get_yolo_session()
        _get_siamese_session()
    except SolveError as e:
        log.warning("word_click warmup: sessions unavailable (%s)", e)
        return

    try:
        # Dummy 672×480 bg to exercise yolo preprocess/infer path.
        dummy_bg = np.full((480, 672, 3), 255, dtype=np.uint8)
        _yolo_detect(dummy_bg)
        dummy_crop = np.full((52, 52, 3), 200, dtype=np.uint8)
        dummy_ref = np.full((52, 52, 3), 100, dtype=np.uint8)
        _siamese_score_batch([dummy_crop], dummy_ref)
        log.info("word_click warmup: done")
    except Exception as e:  # defensive; warmup must never break the caller
        log.warning("word_click warmup: dummy inference failed: %s", e)


def locate_chars_by_siamese(
    bg_bytes: bytes,
    targets: list[str],
) -> list[tuple[int, int]]:
    """Return click (cx, cy) for each target char, in order.

    Raises ``SolveError`` if YOLO finds zero bboxes. If YOLO finds fewer
    bboxes than targets, each target still gets its best pick; the caller
    decides whether to retry or accept.

    Strategy: compute the full ``(targets × crops)`` score matrix once,
    then greedily pick the best unused crop per target in input order.
    This avoids redundant ORT calls when multiple targets share the same
    candidate pool.
    """
    bg = _bytes_to_bgr(bg_bytes)
    bboxes = _yolo_detect(bg)
    if not bboxes:
        raise SolveError("word_click: yolo returned 0 bboxes")
    log.info("word_click yolo: %d bboxes %s", len(bboxes), bboxes)

    # pre-crop
    crops: list[np.ndarray] = []
    centers: list[tuple[int, int]] = []
    for x1, y1, x2, y2 in bboxes:
        crop = bg[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        crops.append(crop)
        centers.append((int((x1 + x2) // 2), int((y1 + y2) // 2)))

    if not crops:
        raise SolveError("word_click: all yolo bboxes produced empty crops")

    # Full score matrix: rows = targets (in order), cols = crop indices.
    score_matrix: list[list[float]] = []
    for ch in targets:
        ref = _render_char(ch)
        score_matrix.append(_siamese_score_batch(crops, ref))

    # Greedy assignment in target order. (Hungarian would be optimal, but
    # targets rarely collide and instruction order is the actual click
    # order — greedy matches tx-word's proven approach.)
    result: list[tuple[int, int]] = []
    used: set[int] = set()
    for ti, ch in enumerate(targets):
        scores = score_matrix[ti]
        best_idx = -1
        best_score = -1.0
        for i, s in enumerate(scores):
            if i in used:
                continue
            if s > best_score:
                best_score = s
                best_idx = i
        if best_idx < 0:
            # all already used — reuse best overall
            for i, s in enumerate(scores):
                if s > best_score:
                    best_score = s
                    best_idx = i
        if best_idx < 0:
            raise SolveError(f"word_click: no candidate for target {ch!r}")
        used.add(best_idx)
        result.append(centers[best_idx])
        log.info("word_click: %r → %s (score=%.3f)", ch, centers[best_idx], best_score)
    return result


__all__ = ["locate_chars_by_siamese", "warmup"]
