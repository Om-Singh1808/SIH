"""SKU identification, tagged backend.

In a kirana store the owner draws each shelf polygon and *tells us* which SKU
lives there (``ShelfPolygon.sku_id``). That tag is the most reliable identifier
available - far better than any classifier on a 640×360 frame - so the default
backend simply trusts it with confidence 1.0. ``enrol`` records how many images
were offered (for the ``SKU.enrolled_images`` counter in the UI) but stores no
pixels: there is nothing to learn and nothing to leak.

The CLIP backend in :mod:`sku_clip` is the P2 upgrade for shelves shared by
several SKUs.
"""

from __future__ import annotations

import numpy as np


class TaggedSkuIdentifier:
    """Implements :class:`retailsense_contracts.interfaces.SkuIdentifier` with ``backend="tagged"``."""

    backend = "tagged"

    def __init__(self) -> None:
        self.enrolled: dict[str, int] = {}

    def enrol(self, sku_id: str, images: list[np.ndarray]) -> int:
        """Count the images for the UI; no pixels are kept. Returns the running total."""
        self.enrolled[sku_id] = self.enrolled.get(sku_id, 0) + len(images)
        return self.enrolled[sku_id]

    def identify(self, crop: np.ndarray, hint_sku_id: str | None) -> tuple[str | None, float]:
        """The shelf tag *is* the answer: ``(hint, 1.0)``; ``(None, 0.0)`` for untagged shelves."""
        return (hint_sku_id, 1.0) if hint_sku_id else (None, 0.0)


__all__ = ["TaggedSkuIdentifier"]
