"""Pure-numpy 2-D geometry used by the zone engine, queue analyzer, shelf scanner and sim.

No OpenCV here on purpose: contracts must install with nothing but numpy, and
the same functions must behave identically in the simulator (ground truth) and
on the edge (measurement).

Coordinate convention: image pixels, x to the right, **y down**.

Normative line-crossing rule
----------------------------
``side_of_line(pt, start, end)`` returns the sign of
``cross(end - start, pt - start)``.  ``+1`` is the LEFT of ``start -> end`` in
image coordinates.  A track *crosses* a line when its anchor moves from side
``-1`` to ``+1`` (``Direction.IN``) or from ``+1`` to ``-1`` (``Direction.OUT``).
Config authors orient lines so that IN is the wanted direction; the demo
entrance line ``(120,315) -> (60,315)`` makes "walking up the image" IN.
"""

from typing import Literal

import numpy as np

Point = tuple[float, float]
Polygon = list[list[float]]


def _as_poly(poly) -> np.ndarray:
    arr = np.asarray(poly, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 2 or arr.shape[0] < 3:
        raise ValueError("polygon must be an [N>=3, 2] array of points")
    return arr


def points_in_polygon(pts: np.ndarray, poly) -> np.ndarray:
    """Vectorised even-odd ray casting. ``pts`` is [N,2]; returns bool [N]."""
    p = _as_poly(poly)
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    x, y = pts[:, 0], pts[:, 1]
    xi, yi = p[:, 0], p[:, 1]
    xj, yj = np.roll(xi, 1), np.roll(yi, 1)
    inside = np.zeros(len(pts), dtype=bool)
    for k in range(len(p)):
        cond = (yi[k] > y) != (yj[k] > y)
        dy = yj[k] - yi[k]
        with np.errstate(divide="ignore", invalid="ignore"):
            x_cross = (xj[k] - xi[k]) * (y - yi[k]) / np.where(dy == 0, 1.0, dy) + xi[k]
        inside ^= cond & (x < x_cross)
    return inside


def point_in_polygon(pt: Point, poly: Polygon) -> bool:
    """True if ``pt`` lies inside ``poly`` (even-odd rule)."""
    return bool(points_in_polygon(np.asarray([pt], dtype=np.float64), poly)[0])


def side_of_line(pt: Point, start: Point, end: Point) -> int:
    """Sign of ``cross(end - start, pt - start)``: +1 LEFT of start->end (y down), -1 right, 0 on the line."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    px, py = pt[0] - start[0], pt[1] - start[1]
    cross = dx * py - dy * px
    if cross > 0:
        return 1
    if cross < 0:
        return -1
    return 0


def _orient(a, b, c) -> int:
    return side_of_line(c, a, b)


def _on_segment(a, b, c) -> bool:
    return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])


def segments_intersect(a0: Point, a1: Point, b0: Point, b1: Point) -> bool:
    """True if segment a0-a1 intersects segment b0-b1 (touching counts)."""
    o1, o2 = _orient(a0, a1, b0), _orient(a0, a1, b1)
    o3, o4 = _orient(b0, b1, a0), _orient(b0, b1, a1)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(a0, a1, b0):
        return True
    if o2 == 0 and _on_segment(a0, a1, b1):
        return True
    if o3 == 0 and _on_segment(b0, b1, a0):
        return True
    if o4 == 0 and _on_segment(b0, b1, a1):
        return True
    return False


def iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise IoU of xyxy boxes: ``a`` [N,4] x ``b`` [M,4] -> [N,M] float32."""
    a = np.asarray(a, dtype=np.float64).reshape(-1, 4)
    b = np.asarray(b, dtype=np.float64).reshape(-1, 4)
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)), dtype=np.float32)
    x0 = np.maximum(a[:, None, 0], b[None, :, 0])
    y0 = np.maximum(a[:, None, 1], b[None, :, 1])
    x1 = np.minimum(a[:, None, 2], b[None, :, 2])
    y1 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(union > 0, inter / union, 0.0)
    return out.astype(np.float32)


def polygon_bbox(poly: Polygon) -> tuple[int, int, int, int]:
    """Integer ``(x0, y0, x1, y1)`` bounding box (x1/y1 exclusive-ish: ceil)."""
    p = _as_poly(poly)
    x0, y0 = np.floor(p.min(axis=0))
    x1, y1 = np.ceil(p.max(axis=0))
    return int(x0), int(y0), int(x1), int(y1)


def polygon_long_axis(poly: Polygon) -> Literal["x", "y"]:
    """Axis along which the polygon is longer; shelf facings are counted along it."""
    x0, y0, x1, y1 = polygon_bbox(poly)
    return "x" if (x1 - x0) >= (y1 - y0) else "y"


def polygon_area(poly: Polygon) -> float:
    """Shoelace area in px^2."""
    p = _as_poly(poly)
    x, y = p[:, 0], p[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def polygon_centroid(poly: Polygon) -> Point:
    p = _as_poly(poly)
    c = p.mean(axis=0)
    return float(c[0]), float(c[1])


def bbox_polygon_overlap(bbox_xyxy, poly: Polygon) -> float:
    """Fraction of the bbox's area that lies inside ``poly`` (rasterised at 1 px)."""
    x0, y0, x1, y1 = (float(v) for v in bbox_xyxy)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    xs = np.arange(np.floor(x0), np.ceil(x1)) + 0.5
    ys = np.arange(np.floor(y0), np.ceil(y1)) + 0.5
    xs = xs[(xs >= x0) & (xs <= x1)]
    ys = ys[(ys >= y0) & (ys <= y1)]
    if len(xs) == 0 or len(ys) == 0:
        # Sub-pixel box: test its centre.
        return 1.0 if point_in_polygon(((x0 + x1) / 2, (y0 + y1) / 2), poly) else 0.0
    gx, gy = np.meshgrid(xs, ys)
    pts = np.stack([gx.ravel(), gy.ravel()], axis=1)
    return float(points_in_polygon(pts, poly).mean())


def polygon_mask(poly: Polygon, height: int, width: int) -> np.ndarray:
    """Boolean HxW raster of the polygon (used by coverage estimators / thumbnails)."""
    x0, y0, x1, y1 = polygon_bbox(poly)
    x0, y0 = max(x0, 0), max(y0, 0)
    x1, y1 = min(x1, width), min(y1, height)
    mask = np.zeros((height, width), dtype=bool)
    if x1 <= x0 or y1 <= y0:
        return mask
    xs = np.arange(x0, x1) + 0.5
    ys = np.arange(y0, y1) + 0.5
    gx, gy = np.meshgrid(xs, ys)
    pts = np.stack([gx.ravel(), gy.ravel()], axis=1)
    mask[y0:y1, x0:x1] = points_in_polygon(pts, poly).reshape(y1 - y0, x1 - x0)
    return mask


def bbox_center(bbox_xyxy) -> Point:
    x0, y0, x1, y1 = bbox_xyxy
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def bbox_bottom_center(bbox_xyxy) -> Point:
    x0, _y0, x1, y1 = bbox_xyxy
    return ((x0 + x1) / 2.0, float(y1))


__all__ = [
    "Point",
    "Polygon",
    "bbox_bottom_center",
    "bbox_center",
    "bbox_polygon_overlap",
    "iou",
    "point_in_polygon",
    "points_in_polygon",
    "polygon_area",
    "polygon_bbox",
    "polygon_centroid",
    "polygon_long_axis",
    "polygon_mask",
    "segments_intersect",
    "side_of_line",
]
