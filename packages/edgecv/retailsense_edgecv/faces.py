"""Face-only preview redaction. No recognition, embeddings or persisted images.

YOLO and tracking continue to use the original image. YuNet searches the whole
frame, then person crops at a larger scale to recover small/partially occluded
faces. Only detected face rectangles (with a small margin) are mosaicked.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import cv2
import numpy as np
from retailsense_contracts.interfaces import BBox, Track
from retailsense_contracts.registry import Unavailable

log = logging.getLogger(__name__)
MODEL_NAME = "face_detection_yunet_2023mar.onnx"
_local = threading.local()


def model_path() -> Path:
    configured = os.environ.get("RS_FACE_MODEL")
    if configured:
        return Path(configured)
    candidates = [Path.cwd() / "models" / MODEL_NAME, Path(__file__).resolve().parents[3] / "models" / MODEL_NAME]
    return next((p for p in candidates if p.is_file()), candidates[0])


def clip_box(box: BBox, width: int, height: int) -> tuple[int, int, int, int] | None:
    if not np.isfinite(box).all():
        return None
    # Avoid a spurious extra pixel from transforms such as 94.00000000000001.
    box = np.round(box, decimals=6)
    x0, y0 = max(0, int(np.floor(box[0]))), max(0, int(np.floor(box[1])))
    x1, y1 = min(width, int(np.ceil(box[2]))), min(height, int(np.ceil(box[3])))
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


class FaceRedactor:
    """One mutable OpenCV detector per calling thread; coordinates stay in RAM."""

    def __init__(self, path: str | Path | None = None, *, detector=None, confidence: float = 0.6):
        if detector is None:
            path = Path(path) if path is not None else model_path()
            if not path.is_file():
                raise Unavailable(f"Face weights missing: {path}; run python tools/fetch_models.py --faces-only")
            detector = cv2.FaceDetectorYN.create(str(path), "", (320, 320), confidence, 0.3, 5000)
        self.detector = detector
        self.confidence = confidence

    def _detect(self, image: np.ndarray, limit: int) -> list[BBox]:
        h, w = image.shape[:2]
        scale = limit / max(h, w)
        nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
        resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
        # YuNet expects dimensions divisible by 32. Padding preserves aspect ratio.
        pw, ph = (nw + 31) // 32 * 32, (nh + 31) // 32 * 32
        padded = cv2.copyMakeBorder(resized, 0, ph - nh, 0, pw - nw, cv2.BORDER_CONSTANT)
        self.detector.setInputSize((pw, ph))
        _, faces = self.detector.detect(padded)
        boxes: list[BBox] = []
        if faces is not None:
            for face in faces:
                if not np.isfinite(face).all() or face[-1] < self.confidence:
                    continue
                x, y, fw, fh = (float(v) for v in face[:4])
                if fw <= 0 or fh <= 0:
                    continue
                # 10% covers the boundary of the face without obscuring the torso.
                box = (
                    (x - 0.1 * fw) * w / nw,
                    (y - 0.1 * fh) * h / nh,
                    (x + 1.1 * fw) * w / nw,
                    (y + 1.1 * fh) * h / nh,
                )
                clipped = clip_box(box, w, h)
                if clipped is not None:
                    boxes.append(clipped)
        return boxes

    def face_boxes(self, image: np.ndarray, tracks: list[Track]) -> list[BBox]:
        # Do not gate detection on confirmed people: faces may be visible first.
        boxes = self._detect(image, 960)
        h, w = image.shape[:2]
        for track in tracks:
            crop = clip_box(track.bbox, w, h)
            if crop is None:
                continue
            x0, y0, x1, y1 = crop
            for a, b, c, d in self._detect(image[y0:y1, x0:x1], 320):
                boxes.append((a + x0, b + y0, c + x0, d + y0))
        return boxes

    def redact(self, image: np.ndarray, tracks: list[Track], factor: int = 12) -> np.ndarray:
        if factor < 2:
            raise ValueError("pixelation factor must be at least 2")
        boxes = self.face_boxes(image, tracks)
        out = image.copy()
        for box in boxes:
            x0, y0, x1, y1 = box
            x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
            # At most 8 cells across a face, even for large foreground faces.
            roi = out[y0:y1, x0:x1]
            small = cv2.resize(
                roi,
                (max(1, min(8, (x1 - x0) // factor)), max(1, min(8, (y1 - y0) // factor))),
                interpolation=cv2.INTER_AREA,
            )
            out[y0:y1, x0:x1] = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
        return out


def redact_faces(image: np.ndarray, tracks: list[Track], factor: int = 12) -> np.ndarray:
    """Redact a copy; withhold the preview if face detection is unavailable/fails."""
    try:
        path = model_path()
        if getattr(_local, "path", None) != path:
            _local.redactor = FaceRedactor(path)
            _local.path = path
        out = _local.redactor.redact(image, tracks, factor)
        _local.warned = False
        return out
    except (Unavailable, cv2.error) as exc:
        if not getattr(_local, "warned", False):
            log.error("Preview withheld: face redaction unavailable (%s)", exc)
            _local.warned = True
        return np.zeros_like(image)
