"""QueueAnalyzer tests: join/served/abandoned accounting, wait-estimate fallback chain, cadence, day reset."""

from __future__ import annotations

import pytest
from conftest import T0, upd
from retailsense_contracts.config import Counter, RulesConfig
from retailsense_contracts.events import QueueSnapshot
from retailsense_contracts.interfaces import QueueAnalyzer as QueueAnalyzerProtocol

from retailsense_edgequeue.queue import QueueAnalyzer

STEP = 0.5  # seconds between scripted frames (sub-tolerance, so +/-2 s correlation is exercised)


def run_scenario(qa: QueueAnalyzer, counter: Counter, until_s: float, script) -> list[QueueSnapshot]:
    """Drive ``qa`` from T0 to T0+until_s in STEP increments; ``script(t)`` -> (members, served_ids)."""
    snaps: list[QueueSnapshot] = []
    t = 0.0
    while t <= until_s + 1e-9:
        members, served = script(t)
        snap = qa.update(upd(T0 + t, counter, members, served))
        if snap is not None:
            snaps.append(snap)
        t += STEP
    return snaps


def _scripted(t: float) -> tuple[list[int], list[int]]:
    """5 joins; 3 served via crossings (one late, one in-zone, one early); 1 abandon; 1 transient (2 s)."""
    members: list[int] = []
    served: list[int] = []
    if t < 20:
        members.append(1)  # leaves at 20, crossing at 21 (late by 1 s)
    if t == 21:
        served.append(1)
    if t < 40 or (t == 40):
        members.append(2)  # crossing at 40 while still inside the polygon
    if t == 40:
        served.append(2)
    if t == 59:
        served.append(3)  # crossing 1 s *before* leaving the polygon
    if t < 60:
        members.append(3)
    if 5 <= t < 80:
        members.append(4)  # joins at 5, leaves at 80 with no crossing -> abandoned (age 75 s)
    if 10 <= t < 12:
        members.append(5)  # transient: 2 s < queue_min_age_s -> ignored
    return members, served


def test_join_served_abandoned_transient(counter: Counter, rules: RulesConfig) -> None:
    qa = QueueAnalyzer(counter, rules, T0)
    snaps = run_scenario(qa, counter, 90, _scripted)
    final = qa.state()
    assert final.served_total == 3
    assert final.abandoned_total == 1
    assert final.count == 0
    assert final.served_window == 3 and final.abandoned_window == 1
    # the transient never shows up as an abandonment and the totals never go backwards
    totals = [(s.served_total, s.abandoned_total) for s in snaps]
    assert totals == sorted(totals)
    assert max(s.count for s in snaps) == 5  # all five were counted while inside


def test_little_law_service_rate(counter: Counter, rules: RulesConfig) -> None:
    qa = QueueAnalyzer(counter, rules, T0)
    snaps = run_scenario(qa, counter, 90, _scripted)
    # Before anyone is served, the default service time is the only basis.
    assert snaps[0].method == "default_service"
    # Once >= 2 served (at t=40 two have been served within 1 min -> 2/min >= 0.2/min) the law applies.
    after_two = [s for s in snaps if s.served_total >= 2]
    assert after_two and all(s.method == "little_service" for s in after_two)
    s = after_two[0]
    assert s.service_rate_pm >= 0.2
    assert s.est_wait_s == pytest.approx(s.count * 60.0 / s.service_rate_pm, abs=0.2)
    # W = L / mu: the estimate is monotonic in the count at a fixed service rate.
    waits = [qa._estimate_wait(n, 1.5)[0] for n in range(0, 8)]
    assert waits == sorted(waits) and waits[-1] > waits[0]


def test_fallback_methods_order(counter: Counter, rules: RulesConfig) -> None:
    # Day started an hour ago, so the rolling window (600 s) is full and rates are served / 10 min.
    day_start = T0 - 3600
    qa = QueueAnalyzer(counter, rules, day_start)
    # 3 in the queue, nobody served yet -> default_service = count x default_service_s
    s = qa.update(upd(T0, counter, [1, 2, 3]))
    assert s is not None and s.method == "default_service"
    assert s.est_wait_s == pytest.approx(3 * counter.default_service_s)
    # track 1 served after 30 s (crossing while inside): 1 served / 10 min = 0.1/min < 0.2 -> observed_wait
    s = qa.update(upd(T0 + 30, counter, [1, 2, 3], served=[1]))
    assert s is not None and s.method == "observed_wait"
    assert s.service_rate_pm == pytest.approx(0.1)
    # observed wait 30 s at position 1 -> 30 s per head -> 2 x 30
    assert s.est_wait_s == pytest.approx(2 * 30.0)
    # second served -> 0.2/min -> little_service
    s = qa.update(upd(T0 + 60, counter, [2, 3], served=[2]))
    assert s is not None and s.method == "little_service"
    assert s.est_wait_s == pytest.approx(1 * 60 / 0.2)
    # all three methods are monotonic in count
    for rate in (0.0, 0.1, 1.0):
        ws = [qa._estimate_wait(n, rate)[0] for n in range(6)]
        assert ws == sorted(ws)


def test_long_since_ts_set_and_cleared(counter: Counter, rules: RulesConfig) -> None:
    assert rules.queue_long_count == 4
    qa = QueueAnalyzer(counter, rules, T0)
    assert qa.update(upd(T0, counter, [1, 2, 3])).long_since_ts is None
    s = qa.update(upd(T0 + 1, counter, [1, 2, 3, 4]))  # delta 1 -> no snapshot, but state updated
    assert s is None
    assert qa.update(upd(T0 + 2, counter, [1, 2, 3, 4, 5, 6])).long_since_ts == T0 + 1
    assert qa.update(upd(T0 + 12, counter, [1, 2, 3, 4, 5])).long_since_ts == T0 + 1  # still long, same start
    assert qa.update(upd(T0 + 13, counter, [1, 2, 3])).long_since_ts is None  # dropped below -> cleared
    s = qa.update(upd(T0 + 30, counter, [1, 2, 3, 4]))
    assert s is not None and s.long_since_ts == T0 + 30  # re-armed with the new start


def test_snapshot_cadence_and_delta_trigger(counter: Counter, rules: RulesConfig) -> None:
    qa = QueueAnalyzer(counter, rules, T0)
    assert qa.update(upd(T0, counter, [1])) is not None  # first update always snapshots
    for i in range(1, 10):
        assert qa.update(upd(T0 + i, counter, [1])) is None  # nothing changed, interval not reached
    assert qa.update(upd(T0 + 10, counter, [1])) is not None  # snapshot_interval_s reached
    assert qa.update(upd(T0 + 11, counter, [1, 2])) is None  # |delta| = 1 -> wait for cadence
    assert qa.update(upd(T0 + 12, counter, [1, 2, 3])) is not None  # |delta| = 2 since last snapshot
    assert qa.update(upd(T0 + 13, counter, [1, 2, 3])) is None
    # state() always reflects the latest emitted snapshot
    assert qa.state().count == 3


def test_day_reset_totals(counter: Counter, rules: RulesConfig) -> None:
    qa = QueueAnalyzer(counter, rules, T0)
    run_scenario(qa, counter, 90, _scripted)
    qa.update(upd(T0 + 100, counter, [7, 8]))
    assert qa.state().served_total == 3 and qa.state().count == 2
    day2 = T0 + 86_400
    qa.reset_day(day2)
    s = qa.state()
    assert s.served_total == 0 and s.abandoned_total == 0
    assert s.served_window == 0 and s.abandoned_window == 0
    assert s.count == 2  # people in line survive the rollover
    s = qa.update(upd(day2 + 5, counter, [8], served=[7]))
    assert s is not None and s.served_total == 1
    assert s.method == "little_service"  # elapsed floor (1 min) -> 1/min


def test_transient_below_min_age_ignored_even_with_late_resolution(counter: Counter, rules: RulesConfig) -> None:
    qa = QueueAnalyzer(counter, rules, T0)
    qa.update(upd(T0, counter, [1]))
    qa.update(upd(T0 + 2, counter, []))
    qa.update(upd(T0 + 5, counter, []))  # past the crossing tolerance, still no crossing
    assert qa.state().abandoned_total == 0 and qa.state().served_total == 0


def test_crossing_without_join_is_not_counted(counter: Counter, rules: RulesConfig) -> None:
    qa = QueueAnalyzer(counter, rules, T0)
    qa.update(upd(T0, counter, [], served=[42]))
    qa.update(upd(T0 + 10, counter, []))
    assert qa.state().served_total == 0


def test_protocol_and_registry(counter: Counter, rules: RulesConfig) -> None:
    from retailsense_contracts.registry import resolve

    assert isinstance(QueueAnalyzer(counter, rules, T0), QueueAnalyzerProtocol)
    assert resolve("queue_analyzer") is QueueAnalyzer
