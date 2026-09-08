"""Safe preview fallback for installations without the optional CV package."""

import numpy as np

from .interfaces import Track


def unavailable_preview(image: np.ndarray, tracks: list[Track], factor: int = 12) -> np.ndarray:
    """Withhold image pixels when the face redactor cannot be imported."""
    return np.zeros_like(image)
