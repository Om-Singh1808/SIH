"""Constants shared by the synthetic store (sim), the colour-blob detector (edgecv)
and the classical shelf estimator (edgeshelf).

The simulator *renders* with these colours and the CV side *detects* with the
same HSV window, so the synthetic store is both the demo and the test oracle.
Colours are BGR (OpenCV order).
"""

SHOPPER_SIZE_PX = 20  # rendered shopper square side
SIM_DT_S = 0.25  # simulation step per frame (4 fps)
SHOPPER_SPEED_PX_S = 40
QUEUE_SPACING_PX = 26
MIN_SEPARATION_PX = 22


class SyntheticPalette:
    """BGR colours of the synthetic store. Shoppers are magenta; nothing else is."""

    FLOOR = (235, 235, 235)
    WALL = (60, 60, 60)
    SHELF_BACKING = (110, 110, 110)
    COUNTER = (80, 120, 170)
    CASHIER = (200, 200, 0)
    SHOPPER = (255, 0, 255)
    # OpenCV HSV (H 0-179): magenta ~150. Per-shopper V jitter 180-255 stays inside.
    SHOPPER_HSV_LO = (140, 120, 120)
    SHOPPER_HSV_HI = (165, 255, 255)
    FACING_COLOURS = {
        "AMUL-TAAZA-500": (230, 200, 60),
        "PARLE-G-70": (40, 120, 230),
        "FORTUNE-OIL-1L": (40, 160, 240),
    }
    TEXT = (255, 255, 255)


def is_shopper_bgr(b: int, g: int, r: int) -> bool:
    """Cheap BGR test equivalent to the HSV window for palette-rendered pixels (no cv2 needed)."""
    return r >= 150 and b >= 150 and g <= 80


__all__ = [
    "MIN_SEPARATION_PX",
    "QUEUE_SPACING_PX",
    "SHOPPER_SIZE_PX",
    "SHOPPER_SPEED_PX_S",
    "SIM_DT_S",
    "SyntheticPalette",
    "is_shopper_bgr",
]
