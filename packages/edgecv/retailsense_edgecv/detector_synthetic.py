"""Weight-free person detector for the synthetic store.

The simulator renders shoppers as magenta squares (``SyntheticPalette.SHOPPER``)
and nothing else in the scene is magenta, so a colour threshold *is* a perfect
detector.  This is deliberate: the synthetic pipeline must run on any laptop
with no GPU, no weights and no internet, and the same tracker / analytics
code runs unchanged when the detector is swapped for the ONNX YOLO model.

Pipeline per frame::

    BGR -> HSV -> inRange(SHOPPER_HSV_LO..HI) -> morphological open 3x3
        -> connectedComponentsWithStats -> boxes with area >= min_area
        -> blobs much bigger than one shopper are split along their long axis

The split heuristic matters for queues: the sim keeps shoppers >= 22 px apart
but two shoppers brushing past each other briefly merge into one blob.  A blob
of ``> split_factor x nominal`` area is cut into ``round(area / nominal)``
equal boxes along its longer side, which keeps the tracker's ids stable.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from retailsense_contracts.interfaces import Detection
from retailsense_contracts.synthetic import SHOPPER_SIZE_PX, SyntheticPalette

_KERNEL = np.ones((3, 3), dtype=np.uint8)


@dataclass
class SyntheticDetector:
    """HSV colour-blob detector satisfying the ``Detector`` Protocol."""

    min_area: int = 120  # px^2; a shopper is ~20x20 = 400
    nominal_area: int = SHOPPER_SIZE_PX * SHOPPER_SIZE_PX
    split_factor: float = 1.7
    conf: float = 0.99
    name: str = "synthetic"
    model_version: str = "hsv-1.0"

    # Detector Protocol -----------------------------------------------------
    def detect(self, image: np.ndarray) -> list[Detection]:
        if image is None or image.ndim != 3 or image.shape[2] != 3:
            return []
        mask = self.mask(image)
        boxes = self.boxes_from_mask(mask)
        return [Detection(bbox=b, conf=self.conf, cls=0) for b in boxes]

    def warmup(self) -> None:  # nothing to load; run once to fault-in cv2 kernels
        self.detect(np.zeros((8, 8, 3), dtype=np.uint8))

    # helpers ----------------------------------------------------------------
    @staticmethod
    def mask(image: np.ndarray) -> np.ndarray:
        """Binary (0/255) mask of shopper-coloured pixels after a 3x3 opening."""
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        lo = np.array(SyntheticPalette.SHOPPER_HSV_LO, dtype=np.uint8)
        hi = np.array(SyntheticPalette.SHOPPER_HSV_HI, dtype=np.uint8)
        mask = cv2.inRange(hsv, lo, hi)
        return cv2.morphologyEx(mask, cv2.MORPH_OPEN, _KERNEL)

    def boxes_from_mask(self, mask: np.ndarray) -> list[tuple[float, float, float, float]]:
        n, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask, connectivity=8)
        out: list[tuple[float, float, float, float]] = []
        for i in range(1, n):  # 0 is background
            x, y, w, h, area = (int(v) for v in stats[i])
            if area < self.min_area:
                continue
            out.extend(self._split((x, y, x + w, y + h), area))
        return out

    def _split(self, box: tuple[int, int, int, int], area: int) -> list[tuple[float, float, float, float]]:
        """Cut an oversized blob into ``k = round(area / nominal)`` boxes along its long axis."""
        x0, y0, x1, y1 = box
        k = int(round(area / float(self.nominal_area)))
        if area <= self.split_factor * self.nominal_area or k < 2:
            return [(float(x0), float(y0), float(x1), float(y1))]
        w, h = x1 - x0, y1 - y0
        parts: list[tuple[float, float, float, float]] = []
        if w >= h:  # split along x
            edges = np.linspace(x0, x1, k + 1)
            for a, b in zip(edges[:-1], edges[1:], strict=True):
                parts.append((float(a), float(y0), float(b), float(y1)))
        else:
            edges = np.linspace(y0, y1, k + 1)
            for a, b in zip(edges[:-1], edges[1:], strict=True):
                parts.append((float(x0), float(a), float(x1), float(b)))
        return parts


__all__ = ["SyntheticDetector"]
