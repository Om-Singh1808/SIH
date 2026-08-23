"""Fallback person detector that drives ``ultralytics`` directly (P2).

Used only when there is no exported ONNX file but the ``ultralytics`` package
(and its ``yolo11n.pt`` weights) happen to be installed.  The import is lazy
and wrapped: a missing package raises the registry's ``Unavailable`` with an
install hint instead of an ImportError at module load, so ``registry.resolve``
and ``select_detector`` can fall through cleanly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from retailsense_contracts.interfaces import Detection
from retailsense_contracts.registry import Unavailable

INSTALL_HINT = "pip install ultralytics  (or run `python tools/fetch_models.py` to export an ONNX model instead)"


class UltralyticsDetector:
    """``Detector`` Protocol over ``ultralytics.YOLO``; person class only."""

    name = "ultralytics"

    def __init__(self, weights: str | Path = "yolo11n.pt", imgsz: int = 640, conf: float = 0.35, iou: float = 0.5):
        try:
            from ultralytics import YOLO  # type: ignore[import-not-found]
        except ImportError as exc:
            raise Unavailable(f"ultralytics not installed: {INSTALL_HINT}") from exc
        self.weights = str(weights)
        self.imgsz = int(imgsz)
        self.conf = float(conf)
        self.iou = float(iou)
        self.model: Any = YOLO(self.weights)
        self.model_version = Path(self.weights).stem

    def detect(self, image: np.ndarray) -> list[Detection]:
        results = self.model.predict(image, imgsz=self.imgsz, conf=self.conf, iou=self.iou, classes=[0], verbose=False)
        dets: list[Detection] = []
        for r in results:
            if r.boxes is None:
                continue
            xyxy = r.boxes.xyxy.cpu().numpy()
            confs = r.boxes.conf.cpu().numpy()
            for b, c in zip(xyxy, confs, strict=True):
                dets.append(Detection(bbox=(float(b[0]), float(b[1]), float(b[2]), float(b[3])), conf=float(c), cls=0))
        return dets

    def warmup(self) -> None:
        self.detect(np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8))


__all__ = ["INSTALL_HINT", "UltralyticsDetector"]
