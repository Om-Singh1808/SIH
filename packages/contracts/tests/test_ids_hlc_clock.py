"""ULID ordering, HLC monotonicity/merge, clocks and store-day helpers."""

import time

from retailsense_contracts.clock import (
    FrozenClock,
    SimClock,
    SystemClock,
    date_to_ts,
    day_start_ts,
    hour_bucket,
    store_date,
)
from retailsense_contracts.hlc import HLC
from retailsense_contracts.ids import CROCKFORD, is_ulid, new_ulid, ulid_timestamp


def test_ulid_sortable():
    ids = [new_ulid() for _ in range(2000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert all(len(i) == 26 and all(c in CROCKFORD for c in i) for i in ids)
    assert all(is_ulid(i) for i in ids)


def test_ulid_timestamp_roundtrip():
    ts = 1_700_000_000.123
    u = new_ulid(ts)
    assert abs(ulid_timestamp(u) - ts) < 0.002
    later = new_ulid(ts + 1)
    assert later > u


def test_hlc_monotonic_and_receive():
    clk = FrozenClock(1000.0)
    h = HLC("EDGE-001", clk)
    stamps = [h.now() for _ in range(50)]
    assert stamps == sorted(stamps) and len(set(stamps)) == 50
    assert stamps[0].endswith("-EDGE_001")
    phys, logical, node = HLC.parse(stamps[-1])
    assert phys == 1_000_000 and logical == 49 and node == "EDGE_001"
    # physical clock moves -> logical resets
    clk.advance(1.0)
    s = h.now()
    assert HLC.parse(s)[:2] == (1_001_000, 0)
    # receive a stamp from the future: local jumps ahead and stays monotonic
    remote = "0000001005000-0003-cloud"
    merged = h.receive(remote)
    assert merged > remote and merged > s
    assert HLC.parse(merged)[:2] == (1_005_000, 4)
    # receive an old stamp: still strictly increasing
    older = h.receive("0000000001000-0000-x")
    assert older > merged
    # clock going backwards never produces a smaller stamp
    clk.set(0.0)
    assert h.now() > older


def test_hlc_restore():
    h = HLC("d", FrozenClock(0.0))
    h.restore("0000000009000-0007-d")
    assert HLC.parse(h.now())[:2] == (9_000, 8)


def test_sim_clock():
    c = SimClock(1000.0, factor=10.0)
    t0 = c.now()
    time.sleep(0.05)
    assert c.now() - t0 >= 0.4  # 10x real time (with slack)
    c.set(5000.0)
    assert abs(c.now() - 5000.0) < 1.0
    c.advance(60)
    assert c.now() >= 5060.0
    assert SystemClock().now() > 1.7e9


def test_store_day_helpers():
    ts = date_to_ts("2026-08-23", "Asia/Kolkata", "17:00")
    assert store_date(ts) == "2026-08-23"
    assert day_start_ts(ts) == date_to_ts("2026-08-23", "Asia/Kolkata", "00:00")
    assert ts - day_start_ts(ts) == 17 * 3600
    # 00:30 IST is still yesterday in UTC but today in store time
    early = date_to_ts("2026-08-23", "Asia/Kolkata", "00:30")
    assert store_date(early) == "2026-08-23"
    assert hour_bucket(3600 * 5 + 10) == 5
