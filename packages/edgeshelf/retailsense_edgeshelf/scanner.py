"""ShelfScanner: one call per scan interval turns a frame into ``shelf.scan`` payloads.

Composition of the module's parts, in the order a reader would expect:

1. **occlusion** - :func:`occluded_by` checks whether any tracked person overlaps
   the shelf polygon by at least ``rules.occlusion_skip_overlap`` of their box.
   Such scans are emitted with ``occluded=True`` (and no thumbnail) so the state
   machine ignores them and the board can show "person in front".
2. **coverage** - the injected :class:`CoverageEstimator` (classical by default,
   any Protocol implementation in tests) against the shelf's calibration
   reference, if one exists.
3. **SKU** - the injected :class:`SkuIdentifier` confirms which SKU is on the
   shelf (the tag, or CLIP when enrolled).
4. **state_raw** - the same threshold rule the state machine uses, so a scan is
   self-describing on the wire.
5. **thumbnail** - only when ``privacy.shelf_thumbnails`` is on.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from retailsense_contracts.config import PrivacyConfig, RulesConfig, ShelfPolygon, ShelfReference
from retailsense_contracts.enums import ShelfState
from retailsense_contracts.events import ShelfScan
from retailsense_contracts.geometry import bbox_polygon_overlap
from retailsense_contracts.interfaces import CoverageEstimator, SkuIdentifier, Track

from .state import raw_state
from .thumbs import shelf_crop, shelf_thumbnail


def occluded_by(tracks: list[Track], shelf: ShelfPolygon, min_overlap: float = 0.30) -> bool:
    """True when any track's box lies at least ``min_overlap`` inside the shelf polygon."""
    return any(bbox_polygon_overlap(t.bbox, shelf.polygon) >= min_overlap for t in tracks)


class ShelfScanner:
    """Produce a :class:`ShelfScan` per configured shelf from one frame + current tracks."""

    def __init__(
        self,
        shelves: list[ShelfPolygon],
        estimator: CoverageEstimator,
        identifier: SkuIdentifier,
        rules: RulesConfig,
        privacy: PrivacyConfig,
        thumbnailer: Callable[[np.ndarray, ShelfPolygon], str | None] = shelf_thumbnail,
    ) -> None:
        self.shelves = list(shelves)
        self.estimator = estimator
        self.identifier = identifier
        self.rules = rules
        self.privacy = privacy
        self.thumbnailer = thumbnailer
        self.references: dict[str, ShelfReference] = {s.shelf_id: s.reference for s in shelves if s.reference}
        self._last: dict[str, ShelfScan] = {}

    def calibrate(self, image: np.ndarray, shelf_id: str) -> ShelfReference:
        """Record the current frame as the shelf's "full" reference (owner action / first demo scan)."""
        shelf = next(s for s in self.shelves if s.shelf_id == shelf_id)
        ref = self.estimator.calibrate(image, shelf)
        self.references[shelf_id] = ref
        return ref

    def scan_one(
        self, image: np.ndarray, tracks: list[Track], shelf: ShelfPolygon, ref: ShelfReference | None
    ) -> ShelfScan:
        if occluded_by(tracks, shelf, self.rules.occlusion_skip_overlap):
            last = self._last.get(shelf.shelf_id)
            return ShelfScan(
                shelf_id=shelf.shelf_id,
                sku_id=shelf.sku_id,
                coverage=last.coverage if last else 0.0,
                facings=last.facings if last else 0,
                capacity_facings=shelf.capacity_facings,
                state_raw=ShelfState.UNKNOWN,
                occluded=True,
                method=last.method if last else "classical",
            )
        res = self.estimator.estimate(image, shelf, ref)
        crop = shelf_crop(image, shelf)
        sku_id, _conf = self.identifier.identify(crop if crop is not None else image, shelf.sku_id)
        thumb = self.thumbnailer(image, shelf) if self.privacy.shelf_thumbnails else None
        scan = ShelfScan(
            shelf_id=shelf.shelf_id,
            sku_id=sku_id,
            coverage=res.coverage,
            facings=res.facings,
            capacity_facings=shelf.capacity_facings,
            state_raw=raw_state(res.coverage, res.facings, shelf.min_facings, self.rules),
            occluded=False,
            method=res.method,
            thumb_b64=thumb,
        )
        self._last[shelf.shelf_id] = scan
        return scan

    def scan(
        self,
        image: np.ndarray,
        tracks: list[Track],
        ts: float,
        references: dict[str, ShelfReference] | None = None,
    ) -> list[ShelfScan]:
        """Scan every shelf. ``references`` (per shelf_id) override the scanner's own."""
        refs = {**self.references, **(references or {})}
        return [self.scan_one(image, tracks, shelf, refs.get(shelf.shelf_id)) for shelf in self.shelves]


__all__ = ["ShelfScanner", "occluded_by"]
