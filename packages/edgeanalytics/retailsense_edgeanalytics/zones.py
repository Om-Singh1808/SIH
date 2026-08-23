"""``ZoneEngine`` -- the per-camera analytics pipeline (registry key ``zone_engine``).

This is the class the edge app resolves via ``registry.resolve("zone_engine")``.
It satisfies :class:`retailsense_contracts.interfaces.ZoneEngine` and composes
the four focused components of this package:

* :class:`~retailsense_edgeanalytics.dwell.ZoneTracker` -- membership with
  2-frame inertia and :class:`DwellSample` on exit / track loss;
* :class:`~retailsense_edgeanalytics.lines.LineCrosser` -- normative line
  crossings with segment test, persistence and cooldown;
* :class:`~retailsense_edgeanalytics.heatmap.HeatmapAccumulator` -- floor
  heatmap deltas through the camera's :class:`PointMapper`;
* :class:`~retailsense_edgeanalytics.footfall.FootfallCounter` -- door totals
  that drive the ``store`` zone's occupancy (``in - out >= 0``).

Each ``update(tracks, ts)`` call returns one :class:`AnalyticsUpdate` whose
``observations()`` the caller persists.  Only **confirmed** tracks are
analysed: tentative tracks (one-frame detections) are exactly the false
positives the tracker exists to suppress.  The anchor point is taken per
``camera.anchor`` (bottom-centre for real cameras where feet touch the floor,
centre for the top-down synthetic camera).

Cadences (from ``RulesConfig``):

* ``zone.occupancy`` for every zone every ``occupancy_interval_s`` (and on
  the first frame so a freshly started edge reports immediately);
* ``heatmap.tiles`` deltas every ``heat_flush_s`` when anything accumulated.

``reload(zones, lines)`` swaps geometry in place (zone editor / OTA config)
while keeping per-track state for ids that survive, so a config push in the
middle of a visit neither double-counts nor loses the dwell in progress.
``flush(ts)`` closes all visits and returns remaining heat -- end of day or
process exit.
"""

from __future__ import annotations

from retailsense_contracts.clock import DEFAULT_TZ
from retailsense_contracts.config import CameraConfig, Floorplan, Line, RulesConfig, Zone
from retailsense_contracts.enums import LineKind, ZoneKind
from retailsense_contracts.events import FootfallCrossing, ZoneOccupancy
from retailsense_contracts.geometry import Point
from retailsense_contracts.interfaces import AnalyticsUpdate, Crossing, PointMapper, Track

from .dwell import DEFAULT_INERTIA, DEFAULT_MIN_DWELL_S, ZoneTracker
from .footfall import FootfallCounter
from .heatmap import HeatmapAccumulator
from .lines import DEFAULT_COOLDOWN_S, DEFAULT_MIN_FRAMES, LineCrosser


class ZoneEngine:
    """Zone membership, line crossings, dwell, occupancy and heatmap for one camera."""

    def __init__(
        self,
        camera: CameraConfig,
        zones: list[Zone],
        lines: list[Line],
        mapper: PointMapper,
        rules: RulesConfig,
        floorplan: Floorplan,
        *,
        tz: str = DEFAULT_TZ,
        inertia: int = DEFAULT_INERTIA,
        min_dwell_s: float = DEFAULT_MIN_DWELL_S,
        line_min_frames: int = DEFAULT_MIN_FRAMES,
        line_cooldown_s: float = DEFAULT_COOLDOWN_S,
    ) -> None:
        self.camera = camera
        self.rules = rules
        self.floorplan = floorplan
        self.mapper = mapper
        self.zones: list[Zone] = []
        self.lines: list[Line] = []
        self._zone_tracker = ZoneTracker([], inertia=inertia, min_dwell_s=min_dwell_s)
        self._crosser = LineCrosser([], min_frames=line_min_frames, cooldown_s=line_cooldown_s)
        self._heat = HeatmapAccumulator(mapper, floorplan)
        self.footfall = FootfallCounter(tz=tz, spike_factor=rules.footfall_spike_factor)
        self._last_ts: float | None = None
        self._last_occ_ts: float | None = None
        self._last_flush_ts: float | None = None
        self.reload(zones, lines)

    # ------------------------------------------------------------------ config

    def reload(self, zones: list[Zone], lines: list[Line]) -> None:
        """Hot-swap geometry; per-track state for surviving zone/line ids is preserved."""
        self.zones = [z for z in zones if z.camera_id == self.camera.camera_id]
        self.lines = [ln for ln in lines if ln.camera_id == self.camera.camera_id]
        self._zone_tracker.reload(self.zones)
        self._crosser.reload(self.lines)

    @property
    def has_entrance_line(self) -> bool:
        return any(ln.kind == LineKind.ENTRANCE for ln in self.lines)

    # ------------------------------------------------------------------ update

    def _anchors(self, tracks: list[Track]) -> dict[int, Point]:
        return {tr.track_id: tr.anchor(self.camera.anchor) for tr in tracks if tr.confirmed}

    def update(self, tracks: list[Track], ts: float) -> AnalyticsUpdate:
        """Process one frame's worth of tracks observed at ``ts``."""
        upd = AnalyticsUpdate(ts=ts, camera_id=self.camera.camera_id)
        dt = 0.0 if self._last_ts is None else max(0.0, ts - self._last_ts)
        first = self._last_ts is None
        self._last_ts = ts
        if self._last_occ_ts is None:
            self._last_occ_ts = ts
        if self._last_flush_ts is None:
            self._last_flush_ts = ts

        anchors = self._anchors(tracks)
        live = set(anchors)

        # 1. Zones: membership with inertia, dwell samples on exit / loss.
        upd.zone_members, upd.dwell_samples = self._zone_tracker.update(anchors, ts)

        # 2. Lines: crossings -> FootfallCrossing payloads and door totals.
        for tid, pt in anchors.items():
            for cr in self._crosser.update(tid, pt, ts):
                self._on_crossing(cr, upd)
        self._crosser.retain(live)

        # 3. Heatmap: credit dt to the floor cell under every confirmed track.
        for tid, pt in anchors.items():
            self._heat.add(tid, pt, dt, ts)
        self._heat.retain(live)

        # 4. Cadenced outputs.
        if first or ts - self._last_occ_ts >= self.rules.occupancy_interval_s:
            self._last_occ_ts = ts
            upd.occupancy = self._occupancy(upd.zone_members)
        if ts - self._last_flush_ts >= self.rules.heat_flush_s:
            self._last_flush_ts = ts
            if self._heat.pending:
                upd.heat = self._heat.flush()
        return upd

    def _on_crossing(self, cr: Crossing, upd: AnalyticsUpdate) -> None:
        upd.crossings.append(cr)
        upd.footfall.append(FootfallCrossing(line_id=cr.line_id, line_kind=cr.line_kind, direction=cr.direction))
        if cr.line_kind == LineKind.ENTRANCE:
            self.footfall.record(cr.direction, cr.ts)

    def _occupancy(self, members: dict[str, list[int]]) -> list[ZoneOccupancy]:
        """One ZoneOccupancy per zone; the store zone is counted at the door when an entrance line exists."""
        out: list[ZoneOccupancy] = []
        for z in self.zones:
            count = len(members.get(z.zone_id, []))
            if z.kind == ZoneKind.STORE and self.has_entrance_line:
                count = self.footfall.occupancy
            out.append(
                ZoneOccupancy(
                    zone_id=z.zone_id, zone_kind=z.kind, count=count, window_s=float(self.rules.occupancy_interval_s)
                )
            )
        return out

    # ------------------------------------------------------------------ flush

    def flush(self, ts: float) -> AnalyticsUpdate:
        """Close every open visit and emit remaining heat deltas (end-of-day / shutdown)."""
        upd = AnalyticsUpdate(ts=ts, camera_id=self.camera.camera_id)
        upd.dwell_samples = self._zone_tracker.flush(ts)
        if self._heat.pending:
            upd.heat = self._heat.flush()
        self._last_flush_ts = ts
        self._crosser.reset()
        self._heat.reset()
        return upd

    # ------------------------------------------------------------------ introspection

    @property
    def in_total(self) -> int:
        return self.footfall.in_total

    @property
    def out_total(self) -> int:
        return self.footfall.out_total

    def members(self, zone_id: str) -> list[int]:
        return self._zone_tracker.members(zone_id)


__all__ = ["ZoneEngine"]
