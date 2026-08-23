"""Protocol conformance of every fake (runtime_checkable spot checks) and their behaviour."""

import asyncio

import numpy as np
import pytest

from retailsense_contracts import interfaces as I  # noqa: N812
from retailsense_contracts.api import Command, IngestBatch, OutboundMessage, ReorderSuggestion, SyncStatus
from retailsense_contracts.enums import (
    AckAction,
    AckBy,
    AlertKind,
    AlertStatus,
    Direction,
    LinkState,
    ShelfState,
    UplinkMode,
)
from retailsense_contracts.events import CameraHealth, DeviceHeartbeat, QueueForecast, ShelfScan
from retailsense_contracts.ids import new_ulid
from retailsense_contracts.interfaces import Detection, Track
from retailsense_contracts.synthetic import SyntheticPalette
from retailsense_contracts.testing import (
    SAMPLE_TS,
    FakeCoverageEstimator,
    FakeDetector,
    FakeEdgeForecaster,
    FakeErp,
    FakeForecaster,
    FakeFrameSource,
    FakeNotifier,
    FakeOndc,
    FakeQueueAnalyzer,
    FakeRuleEngine,
    FakeShelfStateMachine,
    FakeSkuIdentifier,
    FakeTracker,
    FakeUplink,
    FakeZoneEngine,
    IdentityMapper,
    InMemoryEdgeStore,
    SimpleLinkController,
    fake_reconcile,
    fake_render_floorplan,
    fake_suggest_reorder,
    sample_alert,
    sample_events_all,
    sample_payload,
    whatsapp_message_for,
)

# ---------------------------------------------------------------------------
# protocol conformance
# ---------------------------------------------------------------------------


def _zone_engine(cfg):
    return FakeZoneEngine(cfg.cameras[0], cfg.zones, cfg.lines, IdentityMapper(), cfg.rules, cfg.floorplan)


@pytest.mark.parametrize(
    "proto, make",
    [
        (I.FrameSource, lambda cfg: FakeFrameSource()),
        (I.Detector, lambda cfg: FakeDetector()),
        (I.Tracker, lambda cfg: FakeTracker()),
        (I.PointMapper, lambda cfg: IdentityMapper()),
        (I.ZoneEngine, _zone_engine),
        (I.QueueAnalyzer, lambda cfg: FakeQueueAnalyzer(cfg.counters[0], cfg.rules, SAMPLE_TS)),
        (I.EdgeQueueForecaster, lambda cfg: FakeEdgeForecaster()),
        (I.CoverageEstimator, lambda cfg: FakeCoverageEstimator()),
        (I.ShelfStateMachine, lambda cfg: FakeShelfStateMachine(cfg.shelves, cfg.skus, cfg.rules, cfg.impact)),
        (I.SkuIdentifier, lambda cfg: FakeSkuIdentifier()),
        (I.RuleEngine, lambda cfg: FakeRuleEngine(cfg)),
        (I.EdgeStore, lambda cfg: InMemoryEdgeStore(cfg)),
        (I.Uplink, lambda cfg: FakeUplink()),
        (I.LinkController, lambda cfg: SimpleLinkController()),
        (I.Notifier, lambda cfg: FakeNotifier()),
        (I.ErpClient, lambda cfg: FakeErp()),
        (I.OndcPublisher, lambda cfg: FakeOndc()),
        (I.CloudQueueForecaster, lambda cfg: FakeForecaster()),
        (I.CloudFootfallForecaster, lambda cfg: FakeForecaster()),
    ],
)
def test_fake_satisfies_protocol(cfg, proto, make):
    obj = make(cfg)
    assert isinstance(obj, proto), f"{type(obj).__name__} does not satisfy {proto.__name__}"


def test_fake_frame_source_is_not_synthetic_control():
    # so /demo/* endpoints correctly 404 when wired with fakes
    assert not isinstance(FakeFrameSource(), I.SyntheticControl)


# ---------------------------------------------------------------------------
# capture / perception
# ---------------------------------------------------------------------------


def test_frame_source_draws_magenta_and_detector_finds_it():
    script = [(SAMPLE_TS + i * 0.25, [(100 + 8 * i, 200, 120 + 8 * i, 220), (400, 100, 420, 120)]) for i in range(6)]
    src = FakeFrameSource(n_frames=8, size=(640, 360), script=script, camera_id="cam-synth")
    assert src.size == (640, 360) and src.nominal_fps == 4.0 and src.camera_id == "cam-synth"
    frames = src.frames()
    assert len(frames) == 8 and src.read() is None
    f0 = frames[0]
    assert f0.image.shape == (360, 640, 3) and f0.image.dtype == np.uint8 and f0.ts == SAMPLE_TS and f0.seq == 0
    assert tuple(f0.image[0, 0]) == SyntheticPalette.FLOOR
    assert tuple(f0.image[210, 110]) == SyntheticPalette.SHOPPER
    det = FakeDetector()
    boxes = det.detect(f0.image)
    assert sorted(d.bbox for d in boxes) == [(100.0, 200.0, 120.0, 220.0), (400.0, 100.0, 420.0, 120.0)]
    assert all(d.conf == 0.99 and d.cls == 0 for d in boxes)
    assert det.detect(frames[7].image) == []  # beyond script: empty frame
    assert frames[7].ts == pytest.approx(SAMPLE_TS + 7 / 4)
    det.warmup()
    assert det.warmed and det.name == "fake"
    scripted = FakeDetector(script=[[(1, 2, 3, 4)], []])
    assert scripted.detect(f0.image)[0].bbox == (1.0, 2.0, 3.0, 4.0)
    assert scripted.detect(f0.image) == []
    assert scripted.detect(f0.image)[0].bbox == (1.0, 2.0, 3.0, 4.0)  # cycles


def test_fake_tracker_keeps_ids_and_never_reuses():
    tr = FakeTracker(max_dist=60, max_age=2)
    ids = []
    for i in range(10):
        tracks = tr.update(
            [Detection((100 + 8 * i, 200, 120 + 8 * i, 220), 0.9), Detection((400, 100, 420, 120), 0.9)],
            SAMPLE_TS + i * 0.25,
        )
        ids.append(sorted(t.track_id for t in tracks))
        assert all(isinstance(t, Track) and t.confirmed for t in tracks)
    assert all(x == [1, 2] for x in ids)
    # lose both for > max_age frames -> new ids are 3, 4 (never reused)
    for i in range(4):
        tr.update([], SAMPLE_TS + 10 + i)
    tracks = tr.update([Detection((0, 0, 10, 10), 0.9), Detection((300, 300, 310, 310), 0.9)], SAMPLE_TS + 20)
    assert sorted(t.track_id for t in tracks) == [3, 4]
    assert tracks[0].anchor("center") == (5.0, 5.0) and tracks[0].anchor("bottom_center") == (5.0, 10.0)
    tr.reset()
    assert tr.update([Detection((0, 0, 1, 1), 0.5)], 0.0)[0].track_id == 1


def test_identity_mapper():
    m = IdentityMapper()
    pts = np.array([[1.0, 2.0], [3.0, 4.0]])
    assert np.array_equal(m.to_floor(pts), pts) and np.array_equal(m.to_image(pts), pts)
    assert m.to_floor(pts) is not pts


# ---------------------------------------------------------------------------
# analytics
# ---------------------------------------------------------------------------


def _track(tid, x, y, size=20):
    return Track(
        track_id=tid,
        bbox=(x - size / 2, y - size / 2, x + size / 2, y + size / 2),
        conf=0.9,
        age=5,
        hits=5,
        time_since_update=0,
        confirmed=True,
    )


def test_fake_zone_engine_entrance_in_out_and_dwell(cfg):
    ze = _zone_engine(cfg)
    ts = SAMPLE_TS
    # walk up through the entrance line at x=90 (IN), browse aisle-1, walk back down (OUT)
    ys_up = [340, 330, 320, 310, 300, 290]
    footfall = []
    for i, y in enumerate(ys_up):
        upd = ze.update([_track(1, 90, y)], ts + i * 0.25)
        footfall += upd.footfall
    assert [(f.line_id, f.direction) for f in footfall] == [("entrance", Direction.IN)]
    t = ts + 2
    for i in range(12):  # in aisle-1 from ts+2; leaves at ts+6 -> 4 s dwell
        upd = ze.update([_track(1, 200, 120)], t + i * 0.25)
        assert upd.zone_members.get("aisle-1") == [1]
    t = ts + 6
    upd = ze.update([_track(1, 90, 300)], t)  # still above the line (side +1): no crossing, but left the aisle
    assert upd.footfall == [] and [d.zone_id for d in upd.dwell_samples] == ["aisle-1"]
    assert upd.dwell_samples[0].dwell_s == pytest.approx(4.0)
    upd2 = ze.update([_track(1, 90, 330)], t + 0.25)  # walks down through the entrance: OUT
    assert [f.direction for f in upd2.footfall] == [Direction.OUT]
    assert ze.in_total == 1 and ze.out_total == 1
    # occupancy cadence + flush with heat
    upd4 = ze.update([_track(1, 200, 120)], t + 0.5 + cfg.rules.occupancy_interval_s)
    assert {o.zone_id for o in upd4.occupancy} == {z.zone_id for z in cfg.zones}
    fl = ze.flush(t + 100)
    assert fl.heat is not None and sum(tile.dwell_s for tile in fl.heat.tiles) > 0
    assert fl.heat.cell_px == 20 and fl.heat.width_cells == 32 and fl.heat.height_cells == 18
    assert all(o.type for o in fl.observations())


def test_fake_queue_analyzer_served_and_abandoned(cfg):
    qa = FakeQueueAnalyzer(cfg.counters[0], cfg.rules, SAMPLE_TS)
    ze = _zone_engine(cfg)
    ts = SAMPLE_TS
    # two shoppers queue; shopper 1 is served (crosses counter line leftwards), shopper 2 abandons after 10 s
    snaps = []
    for i in range(8):
        upd = ze.update([_track(1, 576, 120), _track(2, 576, 146)], ts + i)
        s = qa.update(upd)
        if s:
            snaps.append(s)
    assert snaps and snaps[-1].count == 2
    upd = ze.update([_track(1, 500, 120), _track(2, 576, 146)], ts + 9)  # 1 crosses x=532 to the left
    assert [(c.line_id, c.direction) for c in upd.crossings] == [("counter-1-line", Direction.IN)]
    s = qa.update(upd)
    assert s is not None and s.served_total == 1 and s.count == 1
    upd = ze.update([_track(2, 576, 330)], ts + 12)  # 2 leaves the zone downward without crossing
    s = qa.update(upd)
    assert s.abandoned_total == 1 and s.count == 0 and s.method in ("default_service", "little_service")
    qa.reset_day(ts + 86400)
    assert qa.state().served_total == 0


def test_fake_edge_forecaster():
    fc = FakeEdgeForecaster()
    assert fc.predict(SAMPLE_TS) is None
    fc.observe(sample_payload("queue.snapshot"))
    p = fc.predict(SAMPLE_TS)
    assert (
        p is not None
        and set(p.horizons) == {"5", "10", "15", "30"}
        and p.model == "edge_trend"
        and p.horizons["15"] == 5.0
    )
    cloud = QueueForecast(
        counter_id="counter-1", made_ts=SAMPLE_TS, horizons={"5": 1, "10": 1, "15": 9, "30": 2}, model="cloud_gbm"
    )
    fc.set_cloud_forecast(cloud)
    assert fc.predict(SAMPLE_TS + 1) is cloud


# ---------------------------------------------------------------------------
# shelf
# ---------------------------------------------------------------------------


def test_fake_coverage_estimator_on_palette(cfg):
    shelf = cfg.shelf("shelf-A")
    img = fake_render_floorplan(cfg)
    x0, y0, y1 = 130, 30, 62
    assert tuple(img[40, 200]) == SyntheticPalette.SHELF_BACKING
    est = FakeCoverageEstimator()
    empty = est.estimate(img, shelf, None)
    assert empty.coverage == 0.0 and empty.facings == 0
    # draw 9 facings of 15 px with 1 px gaps -> full
    full = img.copy()
    for i in range(9):
        full[y0 + 2 : y1 - 2, x0 + 1 + i * 15 : x0 + 1 + i * 15 + 14] = SyntheticPalette.FACING_COLOURS[
            "AMUL-TAAZA-500"
        ]
    ref = est.calibrate(full, shelf)
    assert (
        ref.shelf_id == "shelf-A"
        and ref.raw_coverage_full > 0.8
        and ref.backing_bgr == list(SyntheticPalette.SHELF_BACKING)
    )
    r = est.estimate(full, shelf, ref)
    assert r.coverage == pytest.approx(1.0) and r.facings == 9
    half = img.copy()
    for i in range(4):
        half[y0 + 2 : y1 - 2, x0 + 1 + i * 15 : x0 + 1 + i * 15 + 14] = SyntheticPalette.FACING_COLOURS[
            "AMUL-TAAZA-500"
        ]
    r = est.estimate(half, shelf, ref)
    assert 0.35 <= r.coverage <= 0.55 and r.facings == 4
    # vertical shelf (shelf-C) uses the y axis
    c = cfg.shelf("shelf-C")
    vimg = img.copy()
    vimg[130:215, 32:60] = SyntheticPalette.FACING_COLOURS["FORTUNE-OIL-1L"]
    rv = est.estimate(vimg, c, None)
    assert 0.4 <= rv.coverage <= 0.6


def test_fake_shelf_state_machine_persistence(cfg):
    sm = FakeShelfStateMachine(cfg.shelves, cfg.skus, cfg.rules, cfg.impact)
    ts = SAMPLE_TS

    def scan(cov, facings, occluded=False):
        return ShelfScan(
            shelf_id="shelf-A",
            sku_id="AMUL-TAAZA-500",
            coverage=cov,
            facings=facings,
            capacity_facings=9,
            state_raw=ShelfState.EMPTY if cov < 0.25 else ShelfState.STOCKED,
            occluded=occluded,
        )

    ch = sm.apply(scan(1.0, 9), ts)
    assert ch is not None and ch.to_state == ShelfState.STOCKED and ch.from_state == ShelfState.UNKNOWN
    assert sm.apply(scan(0.1, 0), ts + 30) is None
    assert sm.apply(scan(0.1, 0, occluded=True), ts + 45) is None  # occluded: ignored, no reset
    assert sm.apply(scan(0.1, 0), ts + 60) is None
    v = sm.view("shelf-A")
    assert v.consecutive_empty_scans == 2 and v.state == ShelfState.STOCKED and v.persistence_required == 3
    ch = sm.apply(scan(0.1, 0), ts + 90)
    assert ch is not None and ch.to_state == ShelfState.EMPTY and ch.consecutive_empty_scans == 3
    assert (
        ch.gap_started_ts == ts + 30
        and ch.gap_minutes == 1.0
        and ch.impact is not None
        and ch.impact.lost_sales_inr > 0
    )
    assert sm.apply(scan(0.1, 0), ts + 120) is None  # stays empty, no new transition
    v = sm.view("shelf-A")
    assert v.state == ShelfState.EMPTY and v.gap_minutes == 1.5 and v.impact_open is not None
    assert sm.gap_minutes_today(ts + 150) == pytest.approx(2.0) and sm.osa_pct(ts + 150) < 100
    ch = sm.apply(scan(0.95, 9), ts + 180)
    assert (
        ch is not None
        and ch.from_state == ShelfState.EMPTY
        and ch.to_state == ShelfState.STOCKED
        and ch.gap_minutes == 2.5
    )
    assert sm.gap_minutes_today(ts + 400) == pytest.approx(2.5)
    # false-positive feedback raises persistence
    assert sm.feedback_false_positive("shelf-A") == 4
    assert sm.view("shelf-A").persistence_required == 4
    assert len(sm.views()) == 3
    sm.restore(
        [{"shelf_id": "shelf-B", "state": "empty", "fp_count": 1, "consecutive_empty_scans": 5, "gap_started_ts": ts}]
    )
    assert sm.view("shelf-B").state == ShelfState.EMPTY and sm.view("shelf-B").persistence_required == 4


def test_fake_sku_identifier():
    s = FakeSkuIdentifier()
    assert s.backend == "fake" and s.enrol("A", [np.zeros((4, 4, 3), np.uint8)] * 3) == 3
    assert s.identify(np.zeros((4, 4, 3), np.uint8), "A") == ("A", 1.0)
    assert s.identify(np.zeros((4, 4, 3), np.uint8), None) == (None, 0.0)


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------


def test_fake_rule_engine_shelf_gap_lifecycle(cfg):
    re_ = FakeRuleEngine(cfg)
    sm = FakeShelfStateMachine(cfg.shelves, cfg.skus, cfg.rules, cfg.impact)
    ts = SAMPLE_TS
    fed = []
    re_.feedback = fed.append
    empty = ShelfScan(
        shelf_id="shelf-A",
        sku_id="AMUL-TAAZA-500",
        coverage=0.1,
        facings=0,
        capacity_facings=9,
        state_raw=ShelfState.EMPTY,
    )
    ch = None
    for i in range(3):
        ch = sm.apply(empty, ts + 30 * i) or ch
    obs = re_.on_shelf_change(ch, sm.view("shelf-A"), ts + 60)
    assert [o.type for o in obs] == ["alert.raised"]
    a = obs[0].payload.alert
    assert a.kind == AlertKind.SHELF_GAP and a.subject_id == "shelf-A" and "अमूल" in a.message_hi and "₹" in a.message_en
    assert a.actions == [AckAction.RESTOCKED, AckAction.ORDER, AckAction.FALSE_POSITIVE] and a.impact.factor == 0.31
    assert re_.on_shelf_change(ch, sm.view("shelf-A"), ts + 90) == []  # dedupe: one open per (kind, subject)
    assert [x.alert_id for x in re_.open_alerts()] == [a.alert_id]
    # ack restocked then observe restock -> resolved with recovered
    acked = re_.on_ack(a.alert_id, AckAction.RESTOCKED, AckBy.WHATSAPP_SIM, ts + 100)
    assert [o.type for o in acked] == ["alert.acked"] and re_.get(a.alert_id).status == AlertStatus.ACKED
    stocked = ShelfScan(
        shelf_id="shelf-A",
        sku_id="AMUL-TAAZA-500",
        coverage=1.0,
        facings=9,
        capacity_facings=9,
        state_raw=ShelfState.STOCKED,
    )
    ch2 = sm.apply(stocked, ts + 120)
    res = re_.on_shelf_change(ch2, sm.view("shelf-A"), ts + 120)
    assert (
        [o.type for o in res] == ["alert.resolved"]
        and res[0].payload.reason == "restocked_observed"
        and res[0].payload.recovered.lost_sales_inr > 0
    )
    assert re_.open_alerts() == []
    # order ack emits order.requested; false positive resolves + feedback
    obs = re_.on_shelf_change(ch, sm.view("shelf-A"), ts + 200)
    a2 = obs[0].payload.alert
    order = re_.on_ack(a2.alert_id, AckAction.ORDER, AckBy.BOARD, ts + 210)
    assert [o.type for o in order] == ["alert.acked", "order.requested"] and order[1].payload.qty > 0
    fp = re_.on_ack(a2.alert_id, AckAction.FALSE_POSITIVE, AckBy.WHATSAPP_SIM, ts + 220)
    assert [o.type for o in fp] == ["alert.acked", "alert.resolved"] and fed == ["shelf-A"]
    re_.restore([sample_alert(AlertKind.CAMERA_DOWN)])
    assert len(re_.open_alerts()) == 1


def test_fake_rule_engine_queue_health_sync(cfg):
    re_ = FakeRuleEngine(cfg)
    ts = SAMPLE_TS
    snap = sample_payload("queue.snapshot").model_copy(update={"count": 6, "long_since_ts": ts - 120})
    obs = re_.on_queue(snap, None, ts)
    assert (
        len(obs) == 1
        and obs[0].payload.alert.kind == AlertKind.QUEUE_LONG
        and obs[0].payload.alert.impact.lost_sales_inr == pytest.approx(3 * 0.32 * 180)
    )
    assert re_.on_queue(snap, None, ts + 10) == []
    calm = snap.model_copy(update={"count": 1, "long_since_ts": None})
    res = re_.on_queue(calm, None, ts + 60)
    assert [o.type for o in res] == ["alert.resolved"]
    fc = QueueForecast(
        counter_id="counter-1", made_ts=ts, horizons={"5": 3, "10": 5, "15": 7, "30": 4}, model="edge_trend"
    )
    obs = re_.on_queue(calm, fc, ts + 70)
    assert obs and obs[0].payload.alert.kind == AlertKind.QUEUE_FORECAST and "7" in obs[0].payload.alert.message_hi
    hb = DeviceHeartbeat(
        uptime_s=1,
        fps=0,
        infer_ms_p50=0,
        infer_ms_p95=0,
        detector="x",
        model_version="y",
        backlog=0,
        link=LinkState.UP,
        cameras=[CameraHealth(camera_id="cam-synth", status="stale", fps=0, last_frame_age_s=30, detector="x")],
        contracts_version="1.0.0",
    )
    obs = re_.on_health(hb, ts)
    assert obs and obs[0].payload.alert.kind == AlertKind.CAMERA_DOWN
    hb_ok = hb.model_copy(update={"cameras": [hb.cameras[0].model_copy(update={"status": "ok"})]})
    assert [o.payload.reason for o in re_.on_health(hb_ok, ts + 5)] == ["device_back"]
    sync = SyncStatus(
        link=LinkState.DOWN,
        uplink=UplinkMode.HTTP,
        cloud_reachable=False,
        backlog=1500,
        backlog_by_class={},
        last_ack_ts=None,
        last_ack_seq=None,
        replayed_since_restore=0,
        replay_total_at_restore=0,
        seq_ok=True,
        down_since_ts=ts - 400,
    )
    obs = re_.on_sync(sync, ts)
    assert (
        obs
        and obs[0].payload.alert.kind == AlertKind.SYNC_BACKLOG
        and "1500 records" in obs[0].payload.alert.message_en
        and "7 min" in obs[0].payload.alert.message_en
    )
    up = sync.model_copy(update={"link": LinkState.UP, "down_since_ts": None})
    assert [o.type for o in re_.on_sync(up, ts + 1)] == ["alert.resolved"]


# ---------------------------------------------------------------------------
# uplink / link / cloud fakes
# ---------------------------------------------------------------------------


def _batch(events, device_id="EDGE-001", cursor=0):
    return IngestBatch(
        batch_id=new_ulid(),
        device_id=device_id,
        store_id="STR-DL-001",
        sent_ts=SAMPLE_TS,
        cursor=cursor,
        events=events,
        backlog=0,
        contracts_version="1.0.0",
    )


def test_fake_uplink_acks_duplicates_and_seq():
    up = FakeUplink()
    evs = sample_events_all()
    assert not up.connected

    async def run():
        await up.connect()
        assert up.connected
        a1 = await up.send(_batch(evs[:8]))
        assert (a1.accepted, a1.duplicates, a1.last_seq, a1.seq_ok) == (8, 0, 8, True)
        a2 = await up.send(_batch(evs[:8]))  # resend -> all duplicates
        assert (a2.accepted, a2.duplicates) == (0, 8)
        a3 = await up.send(_batch(evs[10:]))  # skipped seq 9, 10
        assert a3.seq_ok is False and a3.seq_gaps == [9, 10] and a3.accepted == 6
        a4 = await up.send(_batch(evs[8:10]))
        assert a4.accepted == 2 and a4.last_seq == 16
        up.queue_command(Command(command_id="c1", device_id="EDGE-001", kind="ping", payload={}, created_ts=1.0))
        a5 = await up.send(_batch([]))
        assert [c.command_id for c in a5.commands] == ["c1"]
        await up.close()

    asyncio.run(run())
    assert len(up.events) == 16 and len(up.batches) == 5 and not up.connected


def test_fake_uplink_fail_and_drop():
    evs = sample_events_all()

    async def run():
        down = FakeUplink(fail=True)
        with pytest.raises(ConnectionError):
            await down.send(_batch(evs[:2]))
        flaky = FakeUplink(drop_every=2)
        await flaky.send(_batch(evs[:2]))
        with pytest.raises(TimeoutError):
            await flaky.send(_batch(evs[2:4]))  # recorded, ack lost
        ack = await flaky.send(_batch(evs[2:4]))  # resend shows as duplicates
        assert ack.duplicates == 2 and ack.accepted == 0 and len(flaky.events) == 4

    asyncio.run(run())


def test_simple_link_controller():
    lc = SimpleLinkController()
    seen = []
    lc.subscribe(seen.append)
    assert lc.state == LinkState.UP and lc.up
    lc.cut()
    lc.cut()
    assert lc.state == LinkState.DOWN and lc.down_since_ts is not None
    lc.restore()
    assert seen == [LinkState.DOWN, LinkState.UP] and lc.down_since_ts is None


def test_cloud_fakes(cfg):
    n = FakeNotifier()
    msg = whatsapp_message_for(sample_alert(AlertKind.SHELF_GAP), "hi")
    assert isinstance(msg, OutboundMessage) and msg.buttons == ["भर दिया", "ऑर्डर करो", "गलत अलर्ट"]
    r = asyncio.run(n.send(msg))
    assert r.status == "sent" and n.outbox("STR-DL-001") == [msg]
    assert asyncio.run(FakeNotifier(fail=True).send(msg)).status == "failed"
    erp = FakeErp()
    assert erp.stock_summary()["Amul Taaza 500ml"] == 48 and erp.sales_today()["transactions"] == 70
    assert erp.post_stock_journal({"Amul Taaza 500ml": -7}) and erp.stock_summary()["Amul Taaza 500ml"] == 41
    sug = fake_suggest_reorder(cfg, None, erp.stock_summary(), None)
    assert [s.sku_id for s in sug] == ["AMUL-TAAZA-500", "PARLE-G-70", "FORTUNE-OIL-1L"]
    amul = sug[0]
    assert amul.forecast_units_lead == 18 * 14 * 1 and amul.suggest_qty == max(
        0, -(-(18 * 14 + 0.5 * 18 * 14 - 41) // 1)
    )
    assert erp.post_purchase_order(sug).startswith("PO-FAKE-")
    assert isinstance(sug[0], ReorderSuggestion)
    ondc = FakeOndc()
    ack = asyncio.run(ondc.publish_availability("STR-DL-001", "I-AMUL-500", False, 0))
    assert ack.ok and ack.item_id == "I-AMUL-500" and not ack.available and len(ondc.published) == 1


def test_fake_reconcile_flags_amul(cfg):
    sm = FakeShelfStateMachine(cfg.shelves, cfg.skus, cfg.rules, cfg.impact)
    scans = {
        "shelf-A": (0.8, 10),
        "shelf-B": (1.0, 30),
        "shelf-C": (1.0, 9),
    }  # Amul 10 facings * 4 = 40 units visual vs 48 system
    for sid, (cov, fac) in scans.items():
        shelf = cfg.shelf(sid)
        sm.apply(
            ShelfScan(
                shelf_id=sid,
                sku_id=shelf.sku_id,
                coverage=cov,
                facings=fac,
                capacity_facings=shelf.capacity_facings,
                state_raw=ShelfState.STOCKED,
            ),
            SAMPLE_TS,
        )
    rep = fake_reconcile(cfg, FakeErp(), sm.views(), cfg.rules, cfg.impact)
    by = {r.sku_id: r for r in rep.rows}
    assert (
        by["AMUL-TAAZA-500"].delta_units == 8
        and by["AMUL-TAAZA-500"].delta_inr == 216.0
        and by["AMUL-TAAZA-500"].flagged
    )
    assert not by["PARLE-G-70"].flagged and not by["FORTUNE-OIL-1L"].flagged
    assert rep.shrink_inr_total == 216.0 and rep.alerts_raised == 1 and rep.source == "fake"
