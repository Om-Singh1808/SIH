"""InMemoryEdgeStore: the reference semantics every real EdgeStore must match."""

import pytest

from retailsense_contracts.alerts import AlertKind
from retailsense_contracts.api import KpiDaily, ShelfStateView
from retailsense_contracts.clock import date_to_ts, store_date
from retailsense_contracts.enums import AlertStatus, Direction, EventClass, LineKind, ShelfState
from retailsense_contracts.events import (
    EVENT_TYPES,
    AlertResolved,
    FootfallCrossing,
    HeatmapTile,
    HeatmapTiles,
    Observation,
)
from retailsense_contracts.impact import lost_sales
from retailsense_contracts.privacy import RetentionPolicy
from retailsense_contracts.testing import SAMPLE_TS, InMemoryEdgeStore, sample_alert, sample_observation, sample_payload


@pytest.fixture
def store(cfg):
    now = [SAMPLE_TS]
    s = InMemoryEdgeStore(cfg, clock=lambda: now[0])
    s._now = now  # type: ignore[attr-defined]
    return s


def test_append_stamps_seq_hlc_outbox(store):
    evs = store.append([sample_observation(t) for t in EVENT_TYPES])
    assert [e.seq for e in evs] == list(range(1, 17))
    assert all(e.store_id == "STR-DL-001" and e.device_id == "EDGE-001" for e in evs)
    hlcs = [e.hlc for e in evs]
    assert hlcs == sorted(hlcs) and len(set(hlcs)) == 16
    assert store.get_state("seq_next") == "17" and store.get_state("hlc_last") == hlcs[-1]
    more = store.append([sample_observation("footfall.crossing")])
    assert more[0].seq == 17
    pend = store.pending(100)
    assert [oid for oid, _ in pend] == list(range(1, 18))
    assert [e.seq for _, e in pend] == list(range(1, 18))
    assert store.pending(3)[-1][0] == 3
    by_cls = store.backlog()
    assert by_cls == {"telemetry": 3, "aggregate": 8, "alert": 3, "txn": 2, "config": 1}


def test_mark_sent_failed_and_expiry_policy(store):
    evs = store.append([sample_observation(t) for t in EVENT_TYPES])
    rows = {r["event_id"]: r for r in store.outbox}
    for e in evs:
        r = rows[e.event_id]
        if e.cls in (EventClass.ALERT, EventClass.TXN):
            assert r["expires_ts"] is None
        elif e.cls == EventClass.TELEMETRY:
            assert r["expires_ts"] == pytest.approx(SAMPLE_TS + 3600)
        else:
            assert r["expires_ts"] == pytest.approx(SAMPLE_TS + 86400)
    store.mark_failed([1, 2], "timeout")
    assert store.outbox[0]["attempts"] == 1 and store.outbox[0]["last_error"] == "timeout"
    store.mark_sent([1, 2, 3], SAMPLE_TS + 5)
    assert len(store.pending(100)) == 13
    # telemetry expires after an hour; alerts/txn never
    assert store.expire(SAMPLE_TS + 3601) == 3  # heatmap.tiles, device.heartbeat, sim.truth
    left = {e.cls for _, e in store.pending(100)}
    assert EventClass.TELEMETRY not in left
    assert store.expire(SAMPLE_TS + 10 * 86400) == 5  # 4 unsent aggregates + config.applied (24 h policy)
    remaining = [e.cls for _, e in store.pending(100)]
    assert set(remaining) == {EventClass.ALERT, EventClass.TXN} and len(remaining) == 5


def test_evict_overflow_never_touches_alert_txn(store):
    obs = (
        [sample_observation("heatmap.tiles") for _ in range(5)]
        + [sample_observation("alert.raised") for _ in range(3)]
        + [sample_observation("zone.occupancy") for _ in range(4)]
    )
    store.append(obs)
    assert store.evict_overflow(100) == 0
    evicted = store.evict_overflow(4)
    assert evicted == 8  # oldest evictable first (5 telemetry, then 3 aggregates) until 4 rows remain
    pend = [e.cls for _, e in store.pending(100)]
    assert pend.count(EventClass.ALERT) == 3 and len(pend) == 4
    assert store.evict_overflow(1) == 1 and [e.cls for _, e in store.pending(100)] == [EventClass.ALERT] * 3


def test_alerts_shelves_queues_heat(store, cfg):
    a = sample_alert(AlertKind.SHELF_GAP)
    store.upsert_alert(a)
    a.status = AlertStatus.ACKED  # the store holds a copy
    assert store.alert(a.alert_id).status == AlertStatus.OPEN
    b = sample_alert(AlertKind.QUEUE_LONG, ts=SAMPLE_TS + 10)
    b.status = AlertStatus.RESOLVED
    store.upsert_alert(b)
    assert [x.alert_id for x in store.alerts(None)] == [b.alert_id, a.alert_id]
    assert [x.alert_id for x in store.alerts(AlertStatus.OPEN)] == [a.alert_id]
    assert store.alert("nope") is None
    view = ShelfStateView(
        shelf_id="shelf-A",
        name="Dairy",
        sku_id="AMUL-TAAZA-500",
        sku_name="Amul Taaza 500ml",
        state=ShelfState.EMPTY,
        coverage=0.1,
        facings=0,
        capacity_facings=9,
        min_facings=2,
        consecutive_empty_scans=3,
        persistence_required=4,
        gap_started_ts=SAMPLE_TS - 600,
        gap_minutes=10.0,
        last_scan_ts=SAMPLE_TS,
        occluded=False,
        impact_open=None,
        has_reference=True,
    )
    store.upsert_shelf(view, None)
    row = store.shelves()[0]
    assert row["shelf_id"] == "shelf-A" and row["fp_count"] == 1 and row["reference"] is None
    store.upsert_queue("counter-1", sample_payload("queue.snapshot"), None)
    store.upsert_queue("counter-1", None, sample_payload("queue.forecast"))
    q = store.queues()[0]
    assert q["snapshot"]["count"] == 5 and q["forecast"]["model"] == "edge_trend"
    hb = int(SAMPLE_TS // 3600)
    store.heat_add(
        "cam-synth",
        HeatmapTiles(
            cell_px=20,
            width_cells=32,
            height_cells=18,
            tiles=[HeatmapTile(cell_x=1, cell_y=2, hour_bucket=hb, dwell_s=2.0, visits=1)],
        ),
    )
    store.heat_add(
        "cam-synth",
        HeatmapTiles(
            cell_px=20,
            width_cells=32,
            height_cells=18,
            tiles=[
                HeatmapTile(cell_x=1, cell_y=2, hour_bucket=hb, dwell_s=3.0, visits=2),
                HeatmapTile(cell_x=5, cell_y=5, hour_bucket=hb - 48, dwell_s=9.0, visits=1),
            ],
        ),
    )
    hm = store.heat_query("cam-synth", SAMPLE_TS - 60, SAMPLE_TS + 60)
    assert (
        [(c.x, c.y, c.dwell_s, c.visits) for c in hm.cells] == [(1, 2, 5.0, 3)]
        and hm.max_dwell_s == 5.0
        and hm.width_cells == 32
    )
    assert len(store.heat_query(None, SAMPLE_TS - 3 * 86400, SAMPLE_TS).cells) == 2
    assert store.heat_query("other", 0, SAMPLE_TS).cells == []


def test_kpi_today_from_events(store, cfg):
    ts = SAMPLE_TS
    obs = []
    for i in range(10):
        obs.append(
            Observation.of(
                FootfallCrossing(line_id="entrance", line_kind=LineKind.ENTRANCE, direction=Direction.IN), ts + i
            )
        )
    for i in range(6):
        obs.append(
            Observation.of(
                FootfallCrossing(line_id="entrance", line_kind=LineKind.ENTRANCE, direction=Direction.OUT), ts + 20 + i
            )
        )
    for i in range(4):
        obs.append(
            Observation.of(
                FootfallCrossing(line_id="counter-1-line", line_kind=LineKind.COUNTER, direction=Direction.IN),
                ts + 30 + i,
            )
        )
    amul = cfg.sku("AMUL-TAAZA-500")
    obs.append(
        Observation.of(
            AlertResolved(alert_id="x", reason="restocked_observed", recovered=lost_sales(amul, 100, cfg.impact)),
            ts + 40,
        )
    )
    # yesterday's crossing must not count
    obs.append(
        Observation.of(
            FootfallCrossing(line_id="entrance", line_kind=LineKind.ENTRANCE, direction=Direction.IN), ts - 86400
        )
    )
    store.append(obs)
    store.upsert_alert(sample_alert(AlertKind.SHELF_GAP, ts=ts + 5))
    store.upsert_queue("counter-1", sample_payload("queue.snapshot"), None)
    k = store.kpi_today(ts + 100)
    assert k.date == store_date(ts) and k.footfall_in == 10 and k.footfall_out == 6 and k.occupancy_now == 4
    assert k.visual_transactions == 4 and k.conversion_pct == 40.0
    assert k.avg_wait_s == 230.0 and k.max_wait_s == 230.0 and k.abandoned == 3
    assert k.alerts_open == 1 and k.alerts_today == 1 and k.lost_sales_inr > 0 and k.lost_margin_inr > 0
    assert k.recovered_inr == lost_sales(amul, 100, cfg.impact).lost_sales_inr
    assert k.osa_pct == 100.0 and k.deltas == {}
    yesterday = store_date(ts - 86400)
    store.upsert_kpi_daily(
        KpiDaily(
            store_id="STR-DL-001",
            date=yesterday,
            footfall_in=7,
            footfall_out=7,
            visual_transactions=3,
            conversion_pct=42.0,
            atv_inr=180,
            osa_pct=98.0,
            gap_minutes_total=5,
            avg_wait_s=200,
            max_wait_s=300,
            abandoned=1,
            lost_sales_inr=10,
            recovered_inr=0,
            shrink_inr=0,
            alerts_total=2,
        )
    )
    assert store.kpi_daily(yesterday).footfall_in == 7 and store.kpi_daily("1999-01-01") is None
    k2 = store.kpi_today(ts + 100)
    assert k2.deltas["footfall_in"] == 3.0 and k2.deltas["osa_pct"] == 2.0 and k2.deltas["avg_wait_s"] == 30.0


def test_purge_retention(store):
    ts = SAMPLE_TS
    old = ts - 40 * 86400
    obs = [
        sample_observation("heatmap.tiles", ts=old),  # telemetry, old
        sample_observation("zone.occupancy", ts=old),  # aggregate, old
        sample_observation("alert.raised", ts=old),  # alert, kept
        sample_observation("shelf.scan", ts=ts - 10 * 86400),  # thumbnail stripped
        sample_observation("zone.occupancy", ts=ts),  # fresh
    ]
    obs[3] = obs[3].model_copy(update={"payload": obs[3].payload.model_copy(update={"thumb_b64": "abc"})})
    evs = store.append(obs)
    store.mark_sent([r["id"] for r in store.outbox], ts - 3 * 86400)
    counts = store.purge(RetentionPolicy(), ts)
    assert counts == {
        "telemetry_events": 1,
        "aggregate_events": 1,
        "thumbnails": 1,
        "sent_outbox": 5,
        "heatmap_cells": 0,
    }
    assert set(store.events) == {evs[2].event_id, evs[3].event_id, evs[4].event_id}
    assert store.events[evs[3].event_id].payload.thumb_b64 is None
    assert store.outbox == [] and store.pending(10) == []
    store.close()
    assert store.closed


def test_day_boundary_uses_store_tz(cfg):
    # 23:30 IST of day D and 00:30 IST of D+1 are different store days even though both are the same UTC date
    s = InMemoryEdgeStore(cfg)
    t1 = date_to_ts("2026-08-23", "Asia/Kolkata", "23:30")
    t2 = date_to_ts("2026-08-24", "Asia/Kolkata", "00:30")
    s.append(
        [Observation.of(FootfallCrossing(line_id="entrance", line_kind=LineKind.ENTRANCE, direction=Direction.IN), t1)]
    )
    assert s.kpi_today(t1).footfall_in == 1
    assert s.kpi_today(t2).footfall_in == 0 and s.kpi_today(t2).date == "2026-08-24"
