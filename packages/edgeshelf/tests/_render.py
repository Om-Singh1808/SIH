"""Shared fixtures: palette-rendered shelves (no dependency on the simulator package)."""

from __future__ import annotations

import numpy as np

from retailsense_contracts.config import ShelfPolygon
from retailsense_contracts.geometry import polygon_bbox, polygon_long_axis
from retailsense_contracts.synthetic import SyntheticPalette
from retailsense_contracts.testing import draw_rect

FRAME_SIZE = (360, 640)  # H, W of the demo camera


def blank_frame() -> np.ndarray:
    img = np.empty((*FRAME_SIZE, 3), dtype=np.uint8)
    img[:] = SyntheticPalette.FLOOR
    return img


def render_shelf(
    image: np.ndarray,
    shelf: ShelfPolygon,
    visible: list[int] | int,
    gap_px: int = 2,
    margin_px: int = 3,
) -> None:
    """Draw the shelf backing and the given facings the way the simulator does.

    ``visible`` is either a count (first N positions) or the explicit positions.
    Facing pitch is ``shelf.facing_width_px`` along the polygon's long axis;
    each facing is ``pitch - gap_px`` wide with ``margin_px`` cross-axis margin.
    """
    x0, y0, x1, y1 = polygon_bbox(shelf.polygon)
    draw_rect(image, (x0, y0, x1, y1), SyntheticPalette.SHELF_BACKING)
    colour = SyntheticPalette.FACING_COLOURS.get(shelf.sku_id or "", (0, 0, 200))
    pitch = int(shelf.facing_width_px or 15)
    positions = list(range(visible)) if isinstance(visible, int) else list(visible)
    along_x = polygon_long_axis(shelf.polygon) == "x"
    for i in positions:
        a0 = (x0 if along_x else y0) + i * pitch + gap_px // 2
        a1 = a0 + pitch - gap_px
        if along_x:
            draw_rect(image, (a0, y0 + margin_px, a1, y1 - margin_px), colour)
        else:
            draw_rect(image, (x0 + margin_px, a0, x1 - margin_px, a1), colour)


