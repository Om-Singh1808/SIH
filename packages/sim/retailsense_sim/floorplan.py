"""Static floorplan background rendered from ``StoreConfig`` geometry.

``render_floorplan`` is registered as ``floorplan_renderer`` in the contracts registry
and is used by three consumers:

* ``VideoGenerator`` caches it once and stamps the dynamic layer (shelf facings,
  cashier, shoppers) on a copy every frame - the reason rendering is cheap;
* SenseBoard's zone editor requests it through the edge API as the canvas;
* ``edgeshelf`` tests calibrate against the empty ``SHELF_BACKING`` rectangles.

Every colour comes from ``SyntheticPalette``.  Nothing here is magenta, so the
colour-blob detector only ever sees shoppers.
"""

from __future__ import annotations

import cv2
import numpy as np

from retailsense_contracts.config import Counter, Line, StoreConfig
from retailsense_contracts.enums import LineKind
from retailsense_contracts.synthetic import SyntheticPalette

WALL_PX = 8
ZONE_OUTLINE = (185, 185, 185)  # light grey, well outside the magenta HSV window


def _poly(points: list[list[float]]) -> np.ndarray:
    return np.asarray(points, dtype=np.int32).reshape(-1, 1, 2)


def entrance_line(cfg: StoreConfig) -> Line | None:
    return next((ln for ln in cfg.lines if ln.kind == LineKind.ENTRANCE), None)


def counter_desk_rect(cfg: StoreConfig, counter: Counter) -> tuple[int, int, int, int]:
    """Desk rectangle (xyxy) for a counter: left of its line, just below the served-exit lane.

    The queue head walks *through* the lane spanned by the counter line when served, so
    the desk must not sit on it.  For the demo geometry (line x=532, y 98..142) this
    yields [468, 146, 530, 200].
    """
    ln = cfg.line(counter.counter_line_id)
    (x0, y0), (x1, y1) = ln.start, ln.end
    if abs(x1 - x0) <= abs(y1 - y0):  # vertical line: served shoppers move horizontally
        x = int(min(x0, x1))
        y_lo = int(max(y0, y1))
        return (x - 64, y_lo + 4, x - 2, y_lo + 58)
    y = int(min(y0, y1))
    x_lo = int(max(x0, x1))
    return (x_lo + 4, y - 64, x_lo + 58, y - 2)


def cashier_rect(cfg: StoreConfig, counter: Counter) -> tuple[int, int, int, int]:
    """The cashier's 20x20 square inside the desk (drawn in ``CASHIER`` colour)."""
    x0, y0, x1, y1 = counter_desk_rect(cfg, counter)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    return (cx - 10, cy - 10, cx + 10, cy + 10)


def render_floorplan(cfg: StoreConfig, *, with_zones: bool = True) -> np.ndarray:
    """Floor + walls + door gap + empty shelf backings + counter desks (+ zone/line outlines).

    Shelf facings are *not* drawn here: an empty shelf is exactly what the coverage
    estimator calibrates against, and ``VideoGenerator`` overlays facings per frame.
    """
    fp = cfg.floorplan
    w, h = fp.width_px, fp.height_px
    img = np.empty((h, w, 3), dtype=np.uint8)
    img[:] = SyntheticPalette.FLOOR

    # walls around the border; the door is a gap in the wall where the entrance line is.
    cv2.rectangle(img, (0, 0), (w - 1, h - 1), SyntheticPalette.WALL, WALL_PX * 2)
    door = entrance_line(cfg)
    if door is not None:
        xa, xb = sorted((int(door.start[0]), int(door.end[0])))
        ya, yb = sorted((int(door.start[1]), int(door.end[1])))
        if xb - xa >= yb - ya:  # horizontal entrance line -> door in top/bottom wall
            wall_y0 = 0 if ya < h // 2 else h - WALL_PX
            cv2.rectangle(img, (xa, wall_y0), (xb, wall_y0 + WALL_PX - 1), SyntheticPalette.FLOOR, -1)
        else:
            wall_x0 = 0 if xa < w // 2 else w - WALL_PX
            cv2.rectangle(img, (wall_x0, ya), (wall_x0 + WALL_PX - 1, yb), SyntheticPalette.FLOOR, -1)

    for shelf in cfg.shelves:
        cv2.fillPoly(img, [_poly(shelf.polygon)], SyntheticPalette.SHELF_BACKING)

    for counter in cfg.counters:
        x0, y0, x1, y1 = counter_desk_rect(cfg, counter)
        cv2.rectangle(img, (x0, y0), (x1, y1), SyntheticPalette.COUNTER, -1)

    if with_zones:
        for z in cfg.zones:
            cv2.polylines(img, [_poly(z.polygon)], True, ZONE_OUTLINE, 1)
        for ln in cfg.lines:
            p0 = (int(ln.start[0]), int(ln.start[1]))
            p1 = (int(ln.end[0]), int(ln.end[1]))
            cv2.line(img, p0, p1, SyntheticPalette.COUNTER, 2)
    return img


__all__ = ["WALL_PX", "cashier_rect", "counter_desk_rect", "entrance_line", "render_floorplan"]
