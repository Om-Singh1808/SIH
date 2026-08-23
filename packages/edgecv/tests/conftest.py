"""Shared fixtures: palette-rendered frames drawn with the contracts helpers (no sim package needed)."""

import numpy as np
import pytest

from retailsense_contracts.synthetic import SHOPPER_SIZE_PX, SyntheticPalette
from retailsense_contracts.testing import draw_rect


def floor_frame(size=(640, 360)) -> np.ndarray:
    w, h = size
    img = np.empty((h, w, 3), dtype=np.uint8)
    img[:] = SyntheticPalette.FLOOR
    return img


def shopper_box(cx: float, cy: float, s: int = SHOPPER_SIZE_PX) -> tuple[float, float, float, float]:
    return (cx - s / 2, cy - s / 2, cx + s / 2, cy + s / 2)


def render(boxes, size=(640, 360), v_jitter: int | None = None) -> np.ndarray:
    img = floor_frame(size)
    for b in boxes:
        colour = SyntheticPalette.SHOPPER if v_jitter is None else (v_jitter, 0, v_jitter)
        draw_rect(img, b, colour)
    return img


@pytest.fixture
def frame_two_shoppers() -> tuple[np.ndarray, list]:
    boxes = [shopper_box(100, 100), shopper_box(400, 250)]
    return render(boxes), boxes
