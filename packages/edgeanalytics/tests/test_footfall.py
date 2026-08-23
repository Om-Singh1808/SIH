"""FootfallCounter: day totals, bounded occupancy, day rollover and 15-minute spike detection."""

from __future__ import annotations

from retailsense_contracts.alerts import FootfallAlertDetails
from retailsense_contracts.clock import date_to_ts, day_start_ts
from retailsense_contracts.enums import Direction

from retailsense_edgeanalytics import FootfallCounter

T = date_to_ts("2026-08-23") + 17 * 3600  # 17:00 IST


def test_totals_and_bounded_occupancy():
    fc = FootfallCounter()
    for _ in range(3):
        fc.record(Direction.IN, T)
    fc.record("out", T + 1)
    assert (fc.in_total, fc.out_total, fc.occupancy) == (3, 1, 2)
    for _ in range(5):
        fc.record(Direction.OUT, T + 2)
    assert fc.out_total == 6 and fc.occupancy == 0


def test_day_rollover_resets_totals():
    fc = FootfallCounter()
    fc.record(Direction.IN, T)
    assert fc.day_start == day_start_ts(T)
    next_day = date_to_ts("2026-08-24") + 60
    fc.record(Direction.IN, next_day)
    assert fc.in_total == 1 and fc.day_start == day_start_ts(next_day)
    fc.restore(40, 10, day_start_ts(next_day))
    assert fc.occupancy == 30


def test_spike_15m_window():
    fc = FootfallCounter(spike_factor=2.5)
    # 12 arrivals spread over the last 10 minutes; baseline for this quarter hour is 4.
    for i in range(12):
        fc.record(Direction.IN, T - 600 + i * 50)
    assert fc.window_count(T) == 12
    d = fc.spike(T, baseline=4.0)
    assert isinstance(d, FootfallAlertDetails)
    assert d.count == 12 and d.baseline == 4.0 and d.factor == 3.0 and d.window_min == 15
    # Not a spike against a busier baseline, nor with no baseline, nor below the minimum count.
    assert fc.spike(T, baseline=6.0) is None
    assert fc.spike(T, baseline=0.0) is None
    assert fc.spike(T, baseline=6.0, factor=1.5) is not None
    # Fifteen minutes later the window has drained.
    assert fc.window_count(T + 16 * 60) == 0
    assert fc.spike(T + 16 * 60, baseline=1.0) is None
    small = FootfallCounter()
    for i in range(3):
        small.record(Direction.IN, T + i)
    assert small.spike(T + 3, baseline=0.5) is None  # 3 < MIN_SPIKE_COUNT
