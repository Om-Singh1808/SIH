"""Floor-space heatmap accumulation (``HeatmapAccumulator``).

Design rationale
----------------
The heatmap is the one analytics product that is *not* tied to a hand-drawn
zone: it tells the owner where people actually spend time.  Positions are
projected from image pixels into floorplan pixels through the camera's
:class:`PointMapper` (homography, or identity for the synthetic camera), then
binned into square cells of ``floorplan.heat_cell_px``.

Two quantities are accumulated per ``(cell_x, cell_y, hour_bucket)``:

* ``dwell_s`` -- the frame interval ``dt`` is credited to the cell under each
  confirmed track, so the sum over all tiles equals total confirmed-track
  time (acceptance criterion in spec D5);
* ``visits`` -- incremented when a track moves into a *different* cell than
  the one it occupied on the previous frame (first sighting counts as one).

``flush()`` returns only the **deltas** accumulated since the previous flush
and clears them, so the store can ``INSERT ... ON CONFLICT ADD`` without any
risk of double counting after a restart.  Points that project outside the
floorplan are clamped to the border cell rather than dropped, so mapping
noise at the edge of the camera view does not silently lose dwell time.
"""

from __future__ import annotations

import math

import numpy as np

from retailsense_contracts.clock import hour_bucket
from retailsense_contracts.config import Floorplan
from retailsense_contracts.events import HeatmapTile, HeatmapTiles
from retailsense_contracts.geometry import Point
from retailsense_contracts.interfaces import PointMapper

Cell = tuple[int, int]


class HeatmapAccumulator:
    """Accumulates per-cell dwell/visit deltas in floorplan coordinates."""

    def __init__(self, mapper: PointMapper, floorplan: Floorplan) -> None:
        self.mapper = mapper
        self.floorplan = floorplan
        self.cell_px = int(floorplan.heat_cell_px)
        self.width_cells = max(1, math.ceil(floorplan.width_px / self.cell_px))
        self.height_cells = max(1, math.ceil(floorplan.height_px / self.cell_px))
        # (cell_x, cell_y, hour_bucket) -> [dwell_s, visits]
        self._acc: dict[tuple[int, int, int], list[float]] = {}
        self._last_cell: dict[int, Cell] = {}

    # ------------------------------------------------------------------ mapping

    def cell_of(self, image_pt: Point) -> Cell:
        """Floor cell for an image-space point: ``floor(floor_pt / cell_px)`` clamped into the grid."""
        floor = self.mapper.to_floor(np.asarray([image_pt], dtype=np.float64))[0]
        cx = int(math.floor(float(floor[0]) / self.cell_px))
        cy = int(math.floor(float(floor[1]) / self.cell_px))
        cx = min(max(cx, 0), self.width_cells - 1)
        cy = min(max(cy, 0), self.height_cells - 1)
        return cx, cy

    # ------------------------------------------------------------------ update

    def add(self, track_id: int, image_pt: Point, dt: float, ts: float) -> None:
        """Credit ``dt`` seconds of dwell (and possibly a visit) to the cell under ``image_pt``."""
        cell = self.cell_of(image_pt)
        key = (cell[0], cell[1], hour_bucket(ts))
        acc = self._acc.setdefault(key, [0.0, 0.0])
        acc[0] += max(0.0, dt)
        if self._last_cell.get(track_id) != cell:
            acc[1] += 1
            self._last_cell[track_id] = cell

    def retain(self, live_track_ids: set[int]) -> None:
        """Forget last-cell memory of tracks the tracker no longer reports."""
        for tid in [t for t in self._last_cell if t not in live_track_ids]:
            del self._last_cell[tid]

    # ------------------------------------------------------------------ flush

    @property
    def pending(self) -> bool:
        return bool(self._acc)

    def pending_dwell_s(self) -> float:
        """Sum of un-flushed dwell seconds (diagnostics / tests)."""
        return float(sum(v[0] for v in self._acc.values()))

    def flush(self) -> HeatmapTiles:
        """Return the deltas accumulated since the last flush and reset them."""
        tiles = [
            HeatmapTile(cell_x=cx, cell_y=cy, hour_bucket=hb, dwell_s=round(v[0], 3), visits=int(v[1]))
            for (cx, cy, hb), v in sorted(self._acc.items())
        ]
        self._acc.clear()
        return HeatmapTiles(
            cell_px=self.cell_px, width_cells=self.width_cells, height_cells=self.height_cells, tiles=tiles
        )

    def reset(self) -> None:
        self._acc.clear()
        self._last_cell.clear()


__all__ = ["Cell", "HeatmapAccumulator"]
