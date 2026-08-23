"""YOLOv8/YOLO11-style ONNX person detector.

Runs ``models/yolo11n.onnx`` (exported by ``tools/fetch_models.py``) through
onnxruntime.  The three numeric stages are pure functions so they can be unit
tested on synthetic tensors without any weights:

* :func:`letterbox`   - resize keeping aspect ratio and pad to ``imgsz`` x ``imgsz``
                        with grey (114) borders; returns the scale/offset needed
                        to map boxes back to the original image.
* :func:`decode_yolov8` - turn the raw ``[1, 4 + n_classes, N]`` output
                        (cx, cy, w, h, class scores...) into xyxy boxes and
                        scores for class 0 (person), thresholded on ``conf``.
* :func:`nms`         - ``cv2.dnn.NMSBoxes`` wrapper (IoU suppression).

Execution provider: CUDA when the installed onnxruntime exposes it, else CPU.
``onnxruntime`` is imported lazily in ``__init__`` so that merely importing
this module (e.g. through the registry) never fails on a box without it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from retailsense_contracts.interfaces import Detection
from retailsense_contracts.registry import Unavailable

PAD_VALUE = 114


# ---------------------------------------------------------------------------
# pure pre/post-processing
# ---------------------------------------------------------------------------


def letterbox(image: np.ndarray, imgsz: int = 640) -> tuple[np.ndarray, float, tuple[float, float]]:
    """Resize ``image`` (HxWx3) to fit ``imgsz`` square, padding the rest with grey.

    Returns ``(padded, scale, (pad_x, pad_y))`` where ``orig = (padded_xy - pad) / scale``.
    """
    h, w = image.shape[:2]
    scale = min(imgsz / float(h), imgsz / float(w))
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR) if (nw, nh) != (w, h) else image
    pad_x = (imgsz - nw) / 2.0
    pad_y = (imgsz - nh) / 2.0
    left, top = int(round(pad_x - 0.1)), int(round(pad_y - 0.1))
    out = np.full((imgsz, imgsz, 3), PAD_VALUE, dtype=image.dtype)
    out[top : top + nh, left : left + nw] = resized
    return out, scale, (float(left), float(top))


def to_input_tensor(padded_bgr: np.ndarray) -> np.ndarray:
    """BGR uint8 HxWx3 -> float32 [1,3,H,W] RGB in 0..1 (YOLO convention)."""
    rgb = padded_bgr[:, :, ::-1]
    chw = np.ascontiguousarray(rgb.transpose(2, 0, 1), dtype=np.float32) / 255.0
    return chw[None, ...]


def decode_yolov8(
    output: np.ndarray, conf: float, scale: float, pad: tuple[float, float], person_class: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """Decode a YOLOv8-style head output into ``(boxes_xyxy[N,4], scores[N])`` in original-image px.

    ``output`` may be ``[1, 4+C, N]`` (ultralytics export) or ``[1, N, 4+C]``; both are handled.
    Only ``person_class`` is kept - RetailSense never needs other COCO classes.
    """
    arr = np.asarray(output, dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[0]
    if arr.shape[0] < arr.shape[1]:  # [4+C, N] -> [N, 4+C]
        arr = arr.T
    if arr.shape[1] < 5 + person_class:
        raise ValueError(f"unexpected YOLO output shape {output.shape}")
    scores = arr[:, 4 + person_class]
    keep = scores >= conf
    if not np.any(keep):
        return np.zeros((0, 4), dtype=np.float32), np.zeros((0,), dtype=np.float32)
    sel = arr[keep]
    cx, cy, w, h = sel[:, 0], sel[:, 1], sel[:, 2], sel[:, 3]
    boxes = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad[0]) / scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad[1]) / scale
    return boxes.astype(np.float32), scores[keep].astype(np.float32)


def nms(boxes: np.ndarray, scores: np.ndarray, conf: float, iou_thresh: float) -> list[int]:
    """Indices kept after IoU non-maximum suppression (``cv2.dnn.NMSBoxes``), highest score first."""
    if len(boxes) == 0:
        return []
    xywh = [[float(b[0]), float(b[1]), float(b[2] - b[0]), float(b[3] - b[1])] for b in boxes]
    idx = cv2.dnn.NMSBoxes(xywh, [float(s) for s in scores], float(conf), float(iou_thresh))
    if idx is None or len(idx) == 0:
        return []
    return [int(i) for i in np.asarray(idx).reshape(-1)]


def available_providers() -> list[str]:
    """Preferred EP list: CUDA first when onnxruntime-gpu is installed, else CPU."""
    try:
        import onnxruntime as ort  # noqa: WPS433 - lazy on purpose
    except ImportError:
        return ["CPUExecutionProvider"]
    have = set(ort.get_available_providers())
    out = [p for p in ("CUDAExecutionProvider",) if p in have]
    out.append("CPUExecutionProvider")
    return out


# ---------------------------------------------------------------------------
# detector
# ---------------------------------------------------------------------------


class OnnxPersonDetector:
    """``Detector`` Protocol implementation over an onnxruntime session."""

    name = "onnx"

    def __init__(
        self,
        model_path: str | Path,
        imgsz: int = 640,
        conf: float = 0.35,
        iou: float = 0.5,
        providers: list[str] | None = None,
        *,
        model_version: str | None = None,
        session: Any | None = None,
    ):
        self.model_path = Path(model_path)
        self.imgsz = int(imgsz)
        self.conf = float(conf)
        self.iou = float(iou)
        self.providers = providers or available_providers()
        self.model_version = model_version or self.model_path.stem
        self.last_infer_ms = 0.0
        if session is not None:
            self.session = session
        else:
            if not self.model_path.exists():
                raise Unavailable(
                    f"ONNX weights not found at {self.model_path}; run `python tools/fetch_models.py` to export them"
                )
            try:
                import onnxruntime as ort
            except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
                raise Unavailable("onnxruntime is not installed: pip install onnxruntime (or onnxruntime-gpu)") from exc
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.session = ort.InferenceSession(str(self.model_path), so, providers=self.providers)
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    @property
    def active_provider(self) -> str:
        try:
            return self.session.get_providers()[0]
        except Exception:  # pragma: no cover - stub sessions in tests
            return self.providers[0]

    # Detector Protocol -----------------------------------------------------
    def detect(self, image: np.ndarray) -> list[Detection]:
        import time

        t0 = time.perf_counter()
        padded, scale, pad = letterbox(image, self.imgsz)
        tensor = to_input_tensor(padded)
        raw = self.session.run([self.output_name], {self.input_name: tensor})[0]
        boxes, scores = decode_yolov8(raw, self.conf, scale, pad)
        keep = nms(boxes, scores, self.conf, self.iou)
        h, w = image.shape[:2]
        dets: list[Detection] = []
        for i in keep:
            x0, y0, x1, y1 = boxes[i]
            x0, x1 = float(np.clip(x0, 0, w)), float(np.clip(x1, 0, w))
            y0, y1 = float(np.clip(y0, 0, h)), float(np.clip(y1, 0, h))
            if x1 - x0 < 1 or y1 - y0 < 1:
                continue
            dets.append(Detection(bbox=(x0, y0, x1, y1), conf=float(scores[i]), cls=0))
        self.last_infer_ms = (time.perf_counter() - t0) * 1000.0
        return dets

    def warmup(self) -> None:
        self.detect(np.full((self.imgsz, self.imgsz, 3), PAD_VALUE, dtype=np.uint8))


__all__ = [
    "OnnxPersonDetector",
    "available_providers",
    "decode_yolov8",
    "letterbox",
    "nms",
    "to_input_tensor",
]
