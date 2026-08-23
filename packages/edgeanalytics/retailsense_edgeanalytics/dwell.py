"""Zone membership with inertia and dwell accounting (``ZoneTracker``).

Design rationale
----------------
A person standing on a zone boundary produces an anchor that flickers in and
out of the polygon every frame.  Without damping this would emit a burst of
sub-second dwell samples and make occupancy counts jump.  ``ZoneTracker``
applies a symmetric **2-frame inertia**: a track becomes a member only after
``inertia`` consecutive frames inside the polygon and stops being a member
only after ``inertia`` consecutive frames outside.

Dwell is measured from the *first* inside frame to the *first* outside frame
(not from/to the confirmation frames), so that a sample equals
``frames_in_zone x dt`` within one frame -- the acceptance criterion in spec
D5.  Samples shorter than ``min_dwell_s`` are dropped (door-step flickers are
not "visits"), and only zone kinds where dwell is meaningful (``aisle``,
``queue``, ``custom``) produce samples.  ``DwellSample`` deliberately carries
no track id (privacy: track ids never leave the edge).

Track loss (the tracker stops reporting an id) closes every membership of
that track immediately using the current timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass

from retailsense_contracts.config import Zone
from retailsense_contracts.enums import ZoneKind
from retailsense_contracts.events import DwellSample
from retailsense_contracts.geometry import Point, point_in_polygon

#: Zone kinds for which a finished visit is reported as a DwellSample.
DWELL_KINDS: frozenset[ZoneKind] = frozenset({ZoneKind.AISLE, ZoneKind.QUEUE, ZoneKind.CUSTOM})
DEFAULT_INERTIA = 2
DEFAULT_MIN_DWELL_S = 1.0


@dataclass
class _Membership:
    """State of one track with respect to one zone."""

    member: bool = False
    streak_in: int = 0  # consecutive frames inside
    streak_out: int = 0  # consecutive frames outside
    entered_ts: float | None = None  # first inside frame of the current visit
    first_out_ts: float | None = None  # first outside frame of the current exit streak


class ZoneTracker:
    """Maintains membership per ``(zone, track)`` and emits :class:`DwellSample` on exit or loss."""

    def __init__(
        self,
        zones: list[Zone],
        *,
        inertia: int = DEFAULT_INERTIA,
        min_dwell_s: float = DEFAULT_MIN_DWELL_S,
    ) -> None:
        self.inertia = int(inertia)
        self.min_dwell_s = float(min_dwell_s)
        self.zones: list[Zone] = []
        self._state: dict[str, dict[int, _Membership]] = {}
        self.reload(zones)

    # ------------------------------------------------------------------ config

    def reload(self, zones: list[Zone]) -> None:
        """Replace the zone list; membership state survives for zone ids that still exist."""
        self.zones = list(zones)
        ids = {z.zone_id for z in self.zones}
        for zid in [z for z in self._state if z not in ids]:
            del self._state[zid]
        for zid in ids:
            self._state.setdefault(zid, {})

    # ------------------------------------------------------------------ update

    def update(self, anchors: dict[int, Point], ts: float) -> tuple[dict[str, list[int]], list[DwellSample]]:
        """Apply one frame of anchors ``{track_id: (x, y)}``.

        Returns ``(members_by_zone, dwell_samples)`` where members are the ids
        that are *confirmed* inside each zone after inertia.
        """
        members: dict[str, list[int]] = {}
        samples: list[DwellSample] = []
        for z in self.zones:
            zstate = self._state[z.zone_id]
            for tid, pt in anchors.items():
                ms = zstate.setdefault(tid, _Membership())
                inside = point_in_polygon(pt, z.polygon)
                sample = self._step(ms, inside, ts, z)
                if sample is not None:
                    samples.append(sample)
                if ms.member:
                    members.setdefault(z.zone_id, []).append(tid)
            # Track loss: every remembered track that did not appear this frame.
            for tid in [t for t in zstate if t not in anchors]:
                ms = zstate.pop(tid)
                if ms.member:
                    sample = self._finish(ms, ts, z)
                    if sample is not None:
                        samples.append(sample)
        return members, samples

    def _step(self, ms: _Membership, inside: bool, ts: float, zone: Zone) -> DwellSample | None:
        if inside:
            ms.streak_out, ms.first_out_ts = 0, None
            ms.streak_in += 1
            if ms.entered_ts is None:
                ms.entered_ts = ts
            if not ms.member and ms.streak_in >= self.inertia:
                ms.member = True
            return None
        # outside
        ms.streak_in = 0
        ms.streak_out += 1
        if ms.first_out_ts is None:
            ms.first_out_ts = ts
        if ms.member:
            if ms.streak_out >= self.inertia:
                return self._finish(ms, ms.first_out_ts, zone)
            return None
        # Not (yet) a member: an interrupted entry streak is forgotten.
        ms.entered_ts = None
        return None

    def _finish(self, ms: _Membership, exited_ts: float, zone: Zone) -> DwellSample | None:
        """Close the visit held in ``ms``; returns a sample when it qualifies."""
        entered = ms.entered_ts
        ms.member, ms.streak_in, ms.streak_out = False, 0, 0
        ms.entered_ts, ms.first_out_ts = None, None
        if entered is None or zone.kind not in DWELL_KINDS:
            return None
        dwell = exited_ts - entered
        if dwell < self.min_dwell_s:
            return None
        return DwellSample(zone_id=zone.zone_id, dwell_s=round(dwell, 3), entered_ts=entered, exited_ts=exited_ts)

    # ------------------------------------------------------------------ flush / queries

    def flush(self, ts: float) -> list[DwellSample]:
        """Close every open visit at ``ts`` (end-of-day / shutdown) and clear all state."""
        samples: list[DwellSample] = []
        for z in self.zones:
            zstate = self._state[z.zone_id]
            for ms in zstate.values():
                if ms.member:
                    sample = self._finish(ms, ts, z)
                    if sample is not None:
                        samples.append(sample)
            zstate.clear()
        return samples

    def members(self, zone_id: str) -> list[int]:
        return [tid for tid, ms in self._state.get(zone_id, {}).items() if ms.member]

    def is_member(self, zone_id: str, track_id: int) -> bool:
        ms = self._state.get(zone_id, {}).get(track_id)
        return bool(ms and ms.member)


__all__ = ["DEFAULT_INERTIA", "DEFAULT_MIN_DWELL_S", "DWELL_KINDS", "ZoneTracker"]
