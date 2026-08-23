"""Image-pixel -> floorplan-pixel mapping (``PointMapper`` Protocol).

A camera looking down an aisle sees a trapezoid; the owner draws zones on a
top-down floorplan.  Four or more (image, floor) point pairs from the zone
editor give a planar homography H (``cv2.findHomography``, RANSAC when more
than 4 pairs are supplied) and shopper anchor points are mapped through it.
When a camera has no calibration (``HomographyConfig`` is ``None``) the map is
the identity, i.e. the floorplan *is* the camera image - exactly the synthetic
top-down demo view.
"""

import cv2
import numpy as np

from retailsense_contracts.config import HomographyConfig


class Homography:
    """Projective mapper built from an explicit 3x3 matrix (identity by default)."""

    def __init__(self, matrix: np.ndarray | None = None):
        self.H = np.eye(3, dtype=float) if matrix is None else np.asarray(matrix, dtype=float).reshape(3, 3)
        self.H_inv = np.linalg.inv(self.H)

    @classmethod
    def from_config(cls, h: HomographyConfig | None) -> "Homography":
        if h is None:
            return cls()
        src = np.asarray(h.image_points, dtype=np.float32).reshape(-1, 1, 2)
        dst = np.asarray(h.floor_points, dtype=np.float32).reshape(-1, 1, 2)
        method = cv2.RANSAC if len(src) > 4 else 0
        H, _mask = cv2.findHomography(src, dst, method, 3.0)
        if H is None:
            raise ValueError("homography could not be estimated from the supplied point pairs (degenerate?)")
        return cls(H)

    @property
    def is_identity(self) -> bool:
        return bool(np.allclose(self.H, np.eye(3)))

    @staticmethod
    def _apply(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
        p = np.asarray(pts, dtype=float).reshape(-1, 2)
        if p.shape[0] == 0:
            return p
        hom = np.concatenate([p, np.ones((p.shape[0], 1))], axis=1) @ H.T
        w = hom[:, 2:3]
        w = np.where(np.abs(w) < 1e-12, 1e-12, w)
        return hom[:, :2] / w

    # PointMapper Protocol --------------------------------------------------
    def to_floor(self, pts: np.ndarray) -> np.ndarray:
        return self._apply(self.H, pts)

    def to_image(self, pts: np.ndarray) -> np.ndarray:
        return self._apply(self.H_inv, pts)


__all__ = ["Homography"]
