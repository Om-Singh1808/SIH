"""Footfall counting and spike detection (``FootfallCounter``).

Design rationale
----------------
The entrance line gives a stream of IN/OUT crossings.  This helper turns it
into the three numbers the dashboard and rule engine need:

* ``in_total`` / ``out_total`` since the start of the store day (store
  timezone, via :func:`retailsense_contracts.clock.day_start_ts`);
* ``occupancy = max(0, in_total - out_total)`` -- bounded below because
  missed OUT crossings are far more common than missed IN crossings (people
  leave in groups, occluding each other) and a negative store occupancy is
  never meaningful;
* a sliding 15-minute window of IN crossings, compared against a baseline
  supplied by the caller (typically the same quarter-hour from the history
  seed or yesterday) to flag a :class:`FootfallAlertDetails` spike when
  ``count >= factor x baseline`` (default factor from ``rules.footfall_spike_factor``).

The counter is deliberately independent of the zone engine so that
``apps/senseedge`` can also rebuild it from persisted ``footfall.crossing``
events after a restart (``restore()``).
"""

from __future__ import annotations

from collections import deque

from retailsense_contracts.alerts import FootfallAlertDetails
from retailsense_contracts.clock import DEFAULT_TZ, day_start_ts
from retailsense_contracts.enums import Direction

WINDOW_15M_S = 15 * 60
#: Minimum IN count in the window before a spike can be raised, whatever the baseline.
MIN_SPIKE_COUNT = 5


class FootfallCounter:
    """Day totals, live occupancy and 15-minute spike detection from entrance crossings."""

    def __init__(
        self,
        *,
        tz: str = DEFAULT_TZ,
        spike_factor: float = 2.5,
        window_s: float = WINDOW_15M_S,
        min_spike_count: int = MIN_SPIKE_COUNT,
    ) -> None:
        self.tz = tz
        self.spike_factor = float(spike_factor)
        self.window_s = float(window_s)
        self.min_spike_count = int(min_spike_count)
        self.in_total = 0
        self.out_total = 0
        self._day_start: float | None = None
        self._ins: deque[float] = deque()  # timestamps of IN crossings within the window

    # ------------------------------------------------------------------ recording

    def record(self, direction: Direction | str, ts: float, count: int = 1) -> None:
        """Register ``count`` crossings in ``direction`` at ``ts`` (rolls the day over if needed)."""
        self._roll_day(ts)
        if Direction(str(direction)) == Direction.IN:
            self.in_total += count
            self._ins.extend([ts] * count)
        else:
            self.out_total += count
        self._trim(ts)

    def restore(self, in_total: int, out_total: int, day_start: float | None = None) -> None:
        """Seed the day totals from persisted state (after a process restart)."""
        self.in_total, self.out_total = int(in_total), int(out_total)
        self._day_start = day_start

    def reset_day(self, ts: float) -> None:
        """Start a new store day at ``ts``; totals go back to zero."""
        self._day_start = day_start_ts(ts, self.tz)
        self.in_total = self.out_total = 0
        self._ins.clear()

    def _roll_day(self, ts: float) -> None:
        start = day_start_ts(ts, self.tz)
        if self._day_start is None:
            self._day_start = start
        elif start > self._day_start:
            self.reset_day(ts)

    def _trim(self, ts: float) -> None:
        while self._ins and ts - self._ins[0] > self.window_s:
            self._ins.popleft()

    # ------------------------------------------------------------------ queries

    @property
    def occupancy(self) -> int:
        """People currently in the store according to the door: ``max(0, in - out)``."""
        return max(0, self.in_total - self.out_total)

    @property
    def day_start(self) -> float | None:
        return self._day_start

    def window_count(self, ts: float) -> int:
        """IN crossings in the last ``window_s`` seconds ending at ``ts``."""
        self._trim(ts)
        return len(self._ins)

    def spike(self, ts: float, baseline: float, *, factor: float | None = None) -> FootfallAlertDetails | None:
        """Return spike details when the 15-minute IN count is ``>= factor x baseline``.

        ``baseline`` is the expected IN count for the same window (callers take
        it from the history seed / yesterday).  Returns ``None`` otherwise.
        """
        f = self.spike_factor if factor is None else float(factor)
        count = self.window_count(ts)
        if count < self.min_spike_count or baseline <= 0 or count < f * baseline:
            return None
        return FootfallAlertDetails(
            count=count,
            baseline=round(float(baseline), 2),
            factor=round(count / baseline, 2),
            window_min=int(round(self.window_s / 60)),
        )


__all__ = ["MIN_SPIKE_COUNT", "WINDOW_15M_S", "FootfallCounter"]
