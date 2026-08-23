"""Constant-velocity Kalman filter on a bounding box ``(cx, cy, w, h)``.

State x = [cx, cy, w, h, vx, vy, vw, vh].  Pure numpy (8x8 matrices) so it
costs microseconds per track; OpenCV's ``cv2.KalmanFilter`` would work but is
harder to unit test and to read.  Noise scales follow the SORT/ByteTrack
convention of tying position/velocity std to the box height so that big
(near) boxes are allowed to move more than small (far) ones.
"""

import numpy as np

BBox = tuple[float, float, float, float]


def xyxy_to_cxcywh(b: BBox) -> np.ndarray:
    x0, y0, x1, y1 = b
    return np.array([(x0 + x1) / 2.0, (y0 + y1) / 2.0, x1 - x0, y1 - y0], dtype=float)


def cxcywh_to_xyxy(v: np.ndarray) -> BBox:
    cx, cy, w, h = (float(x) for x in v[:4])
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


class KalmanBox:
    """Single-box constant-velocity Kalman filter (SORT-style)."""

    ndim = 4

    def __init__(self, bbox: BBox, *, std_pos: float = 1.0 / 20, std_vel: float = 1.0 / 160):
        self._std_pos = std_pos
        self._std_vel = std_vel
        z = xyxy_to_cxcywh(bbox)
        self.x = np.zeros(8, dtype=float)
        self.x[:4] = z
        self.F = np.eye(8)
        self.F[:4, 4:] = np.eye(4)  # x += v * dt (dt = 1 frame)
        self.H = np.eye(4, 8)
        h = max(z[3], 1.0)
        std = [2 * std_pos * h] * 4 + [10 * std_vel * h] * 4
        self.P = np.diag(np.square(std))

    # ------------------------------------------------------------------
    def predict(self) -> BBox:
        """Advance one frame; returns predicted xyxy."""
        h = max(self.x[3], 1.0)
        q = np.square(np.array([self._std_pos * h] * 4 + [self._std_vel * h] * 4))
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + np.diag(q)
        # sizes must stay positive after prediction
        self.x[2] = max(self.x[2], 1.0)
        self.x[3] = max(self.x[3], 1.0)
        return self.bbox

    def update(self, bbox: BBox) -> None:
        z = xyxy_to_cxcywh(bbox)
        h = max(self.x[3], 1.0)
        R = np.diag(np.square(np.array([self._std_pos * h] * 4)))
        S = self.H @ self.P @ self.H.T + R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (z - self.H @ self.x)
        self.P = (np.eye(8) - K @ self.H) @ self.P

    # ------------------------------------------------------------------
    @property
    def bbox(self) -> BBox:
        return cxcywh_to_xyxy(self.x)

    @property
    def center(self) -> tuple[float, float]:
        return float(self.x[0]), float(self.x[1])

    @property
    def velocity(self) -> tuple[float, float]:
        return float(self.x[4]), float(self.x[5])


__all__ = ["KalmanBox", "cxcywh_to_xyxy", "xyxy_to_cxcywh"]
