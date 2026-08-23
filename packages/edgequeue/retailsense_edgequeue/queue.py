"""``QueueAnalyzer``: from per-frame zone membership + line crossings to ``QueueSnapshot``s.

Inputs are the ``AnalyticsUpdate``s produced by the zone engine (one per processed frame). The analyzer is
deliberately stateless about *pixels*: it only sees *which track ids are inside the queue polygon* and
*which tracks crossed the counter line*. That keeps it testable with scripted updates and keeps track ids on
the edge (they never appear in a snapshot).

Lifecycle of one shopper (track id)
-----------------------------------
1. **join** -- the id first appears in ``zone_members[queue_zone_id]``. Entry time is recorded and an arrival
   is logged for the rolling arrival rate.
2. **leave** -- the id disappears from the members list. We do *not* decide yet: the counter line sits just
   outside the queue polygon, so the "served" crossing typically lands a few frames *after* the polygon exit
   (and sometimes a frame before, when the polygon and the line overlap). The departure is parked for up to
   ``CROSSING_TOLERANCE_S`` (2 s).
3. **resolve** -- the parked departure becomes

   * **served** if a ``Crossing(counter_line, IN)`` for that id exists within +/-2 s of the leave time
     (``wait = leave - entry``; the wait is recorded for the observed-wait fallback);
   * **abandoned** if no such crossing arrived and the shopper had been in the zone at least
     ``rules.queue_min_age_s`` (the min-age guard);
   * **ignored** otherwise -- a transient track that brushed the polygon (tracker jitter, someone walking past).

A crossing for an id *still* in the members list (polygon overlaps the line) is a served departure right away.

Wait estimate (``est_wait_s`` / ``method``)
-------------------------------------------
``little_service`` -> ``observed_wait`` -> ``default_service`` fallback chain, in that order:

* ``little_service``: ``count * 60 / service_rate_pm`` when the service rate is at least 0.2/min
  (see ``little.py`` for why the *service* rate is used and not the arrival rate).
* ``observed_wait``: when the cashier's rate cannot be measured yet, use the last five served shoppers: each
  gives a per-head service time ``wait / queue_position_at_join``; ``est = count * mean(per_head)``. Dividing by
  the position is what keeps the estimate monotonic in ``count`` (a bare mean of waits would not be).
* ``default_service``: ``count * counter.default_service_s`` from config.

Snapshot cadence
----------------
``update()`` returns a snapshot every ``rules.snapshot_interval_s``, or immediately when the count moved by
two or more since the last snapshot, or when a served/abandoned resolution happened (so totals and waits are
timely for the rule engine). Otherwise it returns ``None``; ``state()`` always gives the latest view.

``long_since_ts`` is the timestamp at which ``count >= rules.queue_long_count`` began and is ``None`` as soon
as the count drops below it (the rule engine applies its own ``queue_long_s`` / hysteresis on top).
``reset_day()`` zeroes the cumulative totals and windows at the store-day boundary without dropping shoppers
who are currently standing in line.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

from retailsense_contracts.config import Counter, RulesConfig
from retailsense_contracts.enums import Direction
from retailsense_contracts.events import QueueSnapshot
from retailsense_contracts.interfaces import AnalyticsUpdate, Crossing

from .little import MIN_SERVICE_RATE_PM, little_wait_s, rolling_rate_pm

#: Max gap (seconds) between a polygon exit and a counter-line crossing attributed to the same shopper.
CROSSING_TOLERANCE_S = 2.0
#: Number of most recent observed waits used by the ``observed_wait`` fallback.
OBSERVED_WAIT_SAMPLES = 5
#: Absolute count change between snapshots that forces an immediate snapshot.
DELTA_TRIGGER = 2

Method = Literal["little_service", "observed_wait", "default_service"]


@dataclass(slots=True)
class _Member:
    """A shopper currently inside the queue polygon."""

    entry_ts: float
    position_at_join: int  # people in the zone including this one at join time (>= 1)


@dataclass(slots=True)
class _Departure:
    """A shopper who left the polygon and is waiting for crossing correlation."""

    entry_ts: float
    leave_ts: float
    position_at_join: int


class QueueAnalyzer:
    """Per-counter queue accounting. Satisfies ``retailsense_contracts.interfaces.QueueAnalyzer``."""

    def __init__(self, counter: Counter, rules: RulesConfig, day_start_ts: float) -> None:
        self.counter = counter
        self.rules = rules
        self.day_start_ts = day_start_ts

        self._members: dict[int, _Member] = {}
        self._pending: dict[int, _Departure] = {}
        # Recent counter-line IN crossings keyed by track id (crossing seen *before* the polygon exit).
        self._recent_crossings: dict[int, float] = {}

        # Event timestamps for the rolling windows (pruned to the window length).
        self._joins: deque[float] = deque()
        self._served: deque[float] = deque()
        self._abandoned: deque[float] = deque()
        # (wait_s, position_at_join) of the most recent served shoppers.
        self._observed: deque[tuple[float, int]] = deque(maxlen=OBSERVED_WAIT_SAMPLES)

        self.served_total = 0
        self.abandoned_total = 0

        self._long_since: float | None = None
        self._last_snapshot: QueueSnapshot | None = None
        self._last_snapshot_ts: float | None = None
        self._last_snapshot_count = 0
        self._last_ts = day_start_ts

    # ------------------------------------------------------------------ public API

    def update(self, upd: AnalyticsUpdate) -> QueueSnapshot | None:
        """Ingest one analytics update; return a snapshot when one is due, else ``None``."""
        ts = upd.ts
        self._last_ts = ts
        members_now = set(upd.zone_members.get(self.counter.queue_zone_id, ()))
        crossings = [c for c in upd.crossings if self._is_served_crossing(c)]

        resolved, served_now = self._apply_crossings(crossings)
        # A track served by a crossing while still inside the polygon must not be re-joined in the same frame.
        self._apply_joins(members_now - served_now, ts)
        resolved |= self._apply_leaves(members_now, ts)
        resolved |= self._resolve_pending(ts)
        self._prune(ts)

        count = len(self._members)
        self._track_long(count, ts)

        due = (
            self._last_snapshot_ts is None
            or ts - self._last_snapshot_ts >= self.rules.snapshot_interval_s
            or abs(count - self._last_snapshot_count) >= DELTA_TRIGGER
            or resolved
        )
        if not due:
            return None
        snap = self._snapshot(ts)
        self._last_snapshot = snap
        self._last_snapshot_ts = ts
        self._last_snapshot_count = count
        return snap

    def state(self) -> QueueSnapshot:
        """Latest emitted snapshot, or one computed from the current state if none was emitted yet."""
        if self._last_snapshot is not None:
            return self._last_snapshot
        return self._snapshot(self._last_ts)

    def reset_day(self, day_start_ts: float) -> None:
        """Store-day rollover: zero totals and windows; shoppers currently in line keep their entry time."""
        self.day_start_ts = day_start_ts
        self.served_total = 0
        self.abandoned_total = 0
        self._joins.clear()
        self._served.clear()
        self._abandoned.clear()
        self._observed.clear()
        self._last_snapshot = None
        self._last_snapshot_ts = None
        self._last_snapshot_count = len(self._members)
        self._last_ts = max(self._last_ts, day_start_ts)

    # ------------------------------------------------------------------ lifecycle steps

    def _is_served_crossing(self, c: Crossing) -> bool:
        return c.line_id == self.counter.counter_line_id and c.direction == Direction.IN

    def _apply_crossings(self, crossings: list[Crossing]) -> tuple[bool, set[int]]:
        """Counter-line IN crossings: settle parked departures, serve members still in-polygon, remember others.

        Returns ``(something_resolved, ids_served_from_inside_the_polygon)``.
        """
        resolved = False
        served_now: set[int] = set()
        for c in crossings:
            tid = c.track_id
            if tid in self._pending:
                dep = self._pending.pop(tid)
                if abs(c.ts - dep.leave_ts) <= CROSSING_TOLERANCE_S:
                    self._serve(dep.entry_ts, dep.leave_ts, dep.position_at_join)
                    resolved = True
                else:
                    resolved |= self._abandon_or_ignore(dep)
            elif tid in self._members:
                m = self._members.pop(tid)
                self._serve(m.entry_ts, c.ts, m.position_at_join)
                served_now.add(tid)
                resolved = True
            else:
                # Crossing first, polygon exit a few frames later: keep it for correlation.
                self._recent_crossings[tid] = c.ts
        return resolved, served_now

    def _apply_joins(self, members_now: set[int], ts: float) -> None:
        for tid in sorted(members_now - self._members.keys()):
            if tid in self._pending:
                # Flickered out and back within the tolerance window: same visit, keep the entry time.
                dep = self._pending.pop(tid)
                self._members[tid] = _Member(dep.entry_ts, dep.position_at_join)
                continue
            self._members[tid] = _Member(entry_ts=ts, position_at_join=len(self._members) + 1)
            self._joins.append(ts)

    def _apply_leaves(self, members_now: set[int], ts: float) -> bool:
        resolved = False
        for tid in [t for t in self._members if t not in members_now]:
            m = self._members.pop(tid)
            cross_ts = self._recent_crossings.pop(tid, None)
            if cross_ts is not None and abs(ts - cross_ts) <= CROSSING_TOLERANCE_S:
                self._serve(m.entry_ts, ts, m.position_at_join)
                resolved = True
            else:
                self._pending[tid] = _Departure(m.entry_ts, ts, m.position_at_join)
        return resolved

    def _resolve_pending(self, ts: float) -> bool:
        """Departures older than the tolerance window without a crossing -> abandoned or ignored."""
        resolved = False
        for tid in [t for t, d in self._pending.items() if ts - d.leave_ts > CROSSING_TOLERANCE_S]:
            resolved |= self._abandon_or_ignore(self._pending.pop(tid))
        return resolved

    def _serve(self, entry_ts: float, leave_ts: float, position: int) -> None:
        self._served.append(leave_ts)
        self.served_total += 1
        self._observed.append((max(0.0, leave_ts - entry_ts), max(1, position)))

    def _abandon_or_ignore(self, dep: _Departure) -> bool:
        """Min-age guard: short visits are tracker noise, not abandonments. Returns True if abandoned."""
        if dep.leave_ts - dep.entry_ts >= self.rules.queue_min_age_s:
            self._abandoned.append(dep.leave_ts)
            self.abandoned_total += 1
            return True
        return False

    def _prune(self, ts: float) -> None:
        horizon = ts - self.rules.queue_window_s
        for dq in (self._joins, self._served, self._abandoned):
            while dq and dq[0] <= horizon:
                dq.popleft()
        self._recent_crossings = {t: c for t, c in self._recent_crossings.items() if ts - c <= CROSSING_TOLERANCE_S}

    def _track_long(self, count: int, ts: float) -> None:
        if count >= self.rules.queue_long_count:
            if self._long_since is None:
                self._long_since = ts
        else:
            self._long_since = None

    # ------------------------------------------------------------------ snapshot

    def _estimate_wait(self, count: int, service_rate_pm: float) -> tuple[float, Method]:
        if service_rate_pm >= MIN_SERVICE_RATE_PM:
            return little_wait_s(count, service_rate_pm), "little_service"
        if self._observed:
            per_head = sum(w / p for w, p in self._observed) / len(self._observed)
            return count * per_head, "observed_wait"
        return count * self.counter.default_service_s, "default_service"

    def _snapshot(self, ts: float) -> QueueSnapshot:
        window = self.rules.queue_window_s
        elapsed = ts - self.day_start_ts
        _, arrival_pm = rolling_rate_pm(self._joins, ts, window, elapsed)
        served_w, service_pm = rolling_rate_pm(self._served, ts, window, elapsed)
        abandoned_w, _ = rolling_rate_pm(self._abandoned, ts, window, elapsed)

        count = len(self._members)
        dwells = [ts - m.entry_ts for m in self._members.values()]
        est, method = self._estimate_wait(count, service_pm)

        return QueueSnapshot(
            counter_id=self.counter.counter_id,
            zone_id=self.counter.queue_zone_id,
            count=count,
            avg_dwell_s=round(sum(dwells) / len(dwells), 2) if dwells else 0.0,
            max_dwell_s=round(max(dwells), 2) if dwells else 0.0,
            arrival_rate_pm=round(arrival_pm, 3),
            service_rate_pm=round(service_pm, 3),
            est_wait_s=round(est, 1),
            method=method,
            served_window=served_w,
            abandoned_window=abandoned_w,
            window_s=int(window),
            served_total=self.served_total,
            abandoned_total=self.abandoned_total,
            long_since_ts=self._long_since,
        )
