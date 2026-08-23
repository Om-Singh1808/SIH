"""On-Shelf Availability (OSA) arithmetic.

OSA is the retail KPI a kirana owner actually understands: *"for what share of
today was every shelf stocked?"*. We define it over shelf-minutes:

    osa_pct(ts) = 100 × (1 − gap_minutes_today / (n_shelves × minutes_since_day_start))

where ``gap_minutes_today`` sums, over all shelves, the minutes each shelf was in
a **confirmed** EMPTY state today - including the open gap of a shelf that is
still empty right now (up to ``ts``). Gaps that straddle midnight only count
the part inside the current store-day. The functions here are pure so the
formula is unit-testable without the state machine.
"""

from __future__ import annotations


def gap_minutes_in_day(gap_started_ts: float, until_ts: float, day_start_ts: float) -> float:
    """Minutes of an open gap that fall inside the store-day starting at ``day_start_ts``."""
    start = max(gap_started_ts, day_start_ts)
    return max(0.0, until_ts - start) / 60.0


def osa_pct(gap_minutes_today: float, n_shelves: int, minutes_since_day_start: float) -> float:
    """100 % minus the share of shelf-minutes lost to confirmed gaps (clamped to 0..100)."""
    if n_shelves <= 0:
        return 100.0
    minutes = max(1.0, minutes_since_day_start)  # avoid /0 right at midnight
    frac = gap_minutes_today / (n_shelves * minutes)
    return round(100.0 * min(1.0, max(0.0, 1.0 - frac)), 2)


__all__ = ["gap_minutes_in_day", "osa_pct"]
