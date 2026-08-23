"""RuleEngine behaviour tests (names follow spec D7)."""

import pytest
from conftest import T0, forecast, heartbeat, queue_snap, shelf_change, sync_status
from retailsense_contracts.alerts import ACTIONS_BY_KIND, StockoutAlert
from retailsense_contracts.enums import AckAction, AckBy, AlertKind, AlertStatus, LinkState, Severity, ShelfState
from retailsense_contracts.events import AlertRaised, AlertResolved, OrderRequested, make_event
from retailsense_contracts.interfaces import RuleEngine as RuleEngineProtocol

from retailsense_edgerules import RuleEngine
from retailsense_edgerules.feedback import FalsePositive


def _types(obs):
    return [o.type for o in obs]


def test_protocol_conformance(engine):
    assert isinstance(engine, RuleEngineProtocol)


# ---------------------------------------------------------------- shelf_gap
def test_shelf_gap_raise_once_and_resolve_with_recovered(cfg, engine):
    ch, view = shelf_change(cfg, "shelf-A", ShelfState.PARTIAL, ShelfState.EMPTY, ts=T0, gap_minutes=2.0)
    out = engine.on_shelf_change(ch, view, T0)
    assert _types(out) == ["alert.raised"]
    alert = out[0].payload.alert
    assert alert.kind == AlertKind.SHELF_GAP and alert.subject_id == "shelf-A"
    assert alert.status == AlertStatus.OPEN
    assert alert.actions == ACTIONS_BY_KIND[AlertKind.SHELF_GAP]
    assert alert.impact is not None and alert.impact.lost_sales_inr > 0
    assert "0.31" in alert.impact.basis  # basis cites the factor
    assert isinstance(alert.details, StockoutAlert)

    # duplicate empty notification -> no second alert (one open per (kind, subject))
    again = engine.on_shelf_change(ch, view, T0 + 30)
    assert again == []
    assert len(engine.open_alerts()) == 1

    # owner says restocked -> ACKED, not resolved yet
    acked = engine.on_ack(alert.alert_id, AckAction.RESTOCKED, AckBy.WHATSAPP_SIM, T0 + 60)
    assert _types(acked) == ["alert.acked"]
    assert engine.get(alert.alert_id).status == AlertStatus.ACKED
    assert len(engine.open_alerts()) == 1

    # camera confirms the shelf is stocked after a 7 minute gap -> resolved with recovered rupees
    ch2, view2 = shelf_change(cfg, "shelf-A", ShelfState.EMPTY, ShelfState.STOCKED, ts=T0 + 300, gap_minutes=7.0)
    res = engine.on_shelf_change(ch2, view2, T0 + 300)
    assert _types(res) == ["alert.resolved"]
    p: AlertResolved = res[0].payload
    assert p.reason == "restocked_observed"
    assert p.final_gap_minutes == 7.0
    assert p.impact_final is not None and p.impact_final.lost_sales_inr > 0
    assert p.recovered is not None and p.recovered.lost_sales_inr > p.impact_final.lost_sales_inr
    assert "120" in p.recovered.basis  # baseline unattended gap cited
    assert engine.open_alerts() == []
    assert engine.get(alert.alert_id).status == AlertStatus.RESOLVED

    # a fresh gap can be raised again after resolution
    ch3, view3 = shelf_change(cfg, "shelf-A", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0 + 900, gap_minutes=1.0)
    assert _types(engine.on_shelf_change(ch3, view3, T0 + 900)) == ["alert.raised"]


def test_shelf_gap_resolve_without_ack_has_no_recovered(cfg, engine):
    ch, view = shelf_change(cfg, "shelf-B", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=3.0)
    engine.on_shelf_change(ch, view, T0)
    ch2, view2 = shelf_change(cfg, "shelf-B", ShelfState.EMPTY, ShelfState.PARTIAL, ts=T0 + 600, gap_minutes=13.0)
    res = engine.on_shelf_change(ch2, view2, T0 + 600)
    assert res[0].payload.reason == "restocked_observed"
    assert res[0].payload.recovered is None  # nobody acted; we do not claim savings


def test_shelf_gap_empty_to_unknown_keeps_alert_open(cfg, engine):
    ch, view = shelf_change(cfg, "shelf-C", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=5.0)
    engine.on_shelf_change(ch, view, T0)
    ch2, view2 = shelf_change(cfg, "shelf-C", ShelfState.EMPTY, ShelfState.UNKNOWN, ts=T0 + 60, gap_minutes=6.0)
    assert engine.on_shelf_change(ch2, view2, T0 + 60) == []
    assert len(engine.open_alerts()) == 1


def test_severity_rules(cfg, engine):
    # Parle-G: 10 x 8/h x 0.31 = 24.8 INR/h -> WARN
    ch, view = shelf_change(cfg, "shelf-B", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=5.0)
    assert engine.on_shelf_change(ch, view, T0)[0].payload.alert.severity == Severity.WARN
    # Amul Taaza: 27 x 18/h x 0.31 = 150.66 INR/h -> HIGH
    ch, view = shelf_change(cfg, "shelf-A", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=5.0)
    assert engine.on_shelf_change(ch, view, T0)[0].payload.alert.severity == Severity.HIGH
    # gap already >= 60 min at raise -> CRITICAL regardless of rate
    ch, view = shelf_change(cfg, "shelf-C", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=61.0)
    assert engine.on_shelf_change(ch, view, T0)[0].payload.alert.severity == Severity.CRITICAL
    # queue: count >= max_queue (8) -> CRITICAL, otherwise WARN
    snap = queue_snap("counter-1", 8, long_since_ts=T0 - 120)
    assert engine.on_queue(snap, None, T0)[0].payload.alert.severity == Severity.CRITICAL
    # camera down -> HIGH ; sync backlog -> INFO ; forecast -> INFO
    assert engine.on_health(heartbeat("stale", age_s=20), T0)[0].payload.alert.severity == Severity.HIGH
    st = sync_status(LinkState.DOWN, backlog=5000, down_since_ts=T0 - 600)
    assert engine.on_sync(st, T0)[0].payload.alert.severity == Severity.INFO


def test_shelf_gap_unmapped_sku_zero_impact(cfg, engine):
    shelf = cfg.shelf("shelf-B")
    ch, view = shelf_change(cfg, "shelf-B", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=5.0)
    ch = ch.model_copy(update={"sku_id": None})
    view = view.model_copy(update={"sku_id": None, "sku_name": shelf.name})
    a = engine.on_shelf_change(ch, view, T0)[0].payload.alert
    assert a.impact is not None and a.impact.lost_sales_inr == 0.0 and a.impact.basis
    assert a.severity == Severity.WARN
    assert shelf.name in a.title_en


# ---------------------------------------------------------------- queues
def test_queue_long_raise_resolve_hysteresis(cfg, engine):
    n = cfg.rules.queue_long_count  # 4
    # count >= N but not yet for queue_long_s -> nothing
    assert engine.on_queue(queue_snap("counter-1", n, long_since_ts=T0), None, T0 + 10) == []
    # sustained for 60 s -> WARN alert with queue_abandon_risk impact
    out = engine.on_queue(queue_snap("counter-1", n + 1, long_since_ts=T0), None, T0 + 60)
    assert _types(out) == ["alert.raised"]
    a = out[0].payload.alert
    assert a.kind == AlertKind.QUEUE_LONG and a.severity == Severity.WARN
    assert a.impact is not None and a.impact.factor == cfg.impact.queue_abandon_factor
    assert "0.32" in a.impact.basis
    # still long -> no duplicate
    assert engine.on_queue(queue_snap("counter-1", n + 2, long_since_ts=T0), None, T0 + 70) == []
    # count drops to N-1 (dead band) -> nothing, no resolve timer
    assert engine.on_queue(queue_snap("counter-1", n - 1), None, T0 + 80) == []
    # count < N-1 but only for 10 s -> still open (hysteresis)
    assert engine.on_queue(queue_snap("counter-1", n - 2), None, T0 + 90) == []
    assert engine.on_queue(queue_snap("counter-1", n - 2), None, T0 + 100) == []
    # bounces back up to N-1: timer reset
    assert engine.on_queue(queue_snap("counter-1", n - 1), None, T0 + 105) == []
    assert engine.on_queue(queue_snap("counter-1", 0), None, T0 + 110) == []
    assert engine.on_queue(queue_snap("counter-1", 0), None, T0 + 130) == []  # 20 s < queue_resolve_s
    res = engine.on_queue(queue_snap("counter-1", 0), None, T0 + 140)  # 30 s -> resolved
    assert _types(res) == ["alert.resolved"]
    assert res[0].payload.reason == "condition_cleared"
    assert engine.open_alerts() == []


def test_queue_forecast_superseded(cfg, engine):
    thr = cfg.rules.queue_forecast_threshold  # 6
    # forecast crosses threshold while queue is short -> INFO forecast alert
    out = engine.on_queue(queue_snap("counter-1", 2), forecast("counter-1", thr + 1, ts=T0), T0)
    assert _types(out) == ["alert.raised"]
    fc_alert = out[0].payload.alert
    assert fc_alert.kind == AlertKind.QUEUE_FORECAST and fc_alert.severity == Severity.INFO
    assert fc_alert.details.forecast == thr + 1 and fc_alert.details.horizon_min == cfg.rules.queue_forecast_horizon_min
    # forecast in the band [thr-1, thr) -> unchanged
    assert engine.on_queue(queue_snap("counter-1", 2), forecast("counter-1", thr - 0.5, ts=T0), T0 + 10) == []
    # the real queue becomes long -> forecast superseded, queue_long raised, in that order
    out = engine.on_queue(queue_snap("counter-1", 5, long_since_ts=T0), forecast("counter-1", thr + 2, ts=T0), T0 + 60)
    assert _types(out) == ["alert.resolved", "alert.raised"]
    assert out[0].payload.alert_id == fc_alert.alert_id and out[0].payload.reason == "superseded"
    assert out[1].payload.alert.kind == AlertKind.QUEUE_LONG
    # while queue_long is open a high forecast never raises a new forecast alert
    assert engine.on_queue(queue_snap("counter-1", 5, long_since_ts=T0), forecast("counter-1", 9, ts=T0), T0 + 70) == []
    assert [a.kind for a in engine.open_alerts()] == [AlertKind.QUEUE_LONG]


def test_queue_forecast_resolves_below_threshold_minus_one(cfg, engine):
    thr = cfg.rules.queue_forecast_threshold
    engine.on_queue(queue_snap("counter-1", 1), forecast("counter-1", thr, ts=T0), T0)
    assert engine.on_queue(queue_snap("counter-1", 1), forecast("counter-1", thr - 1, ts=T0), T0 + 10) == []
    res = engine.on_queue(queue_snap("counter-1", 1), forecast("counter-1", thr - 1.5, ts=T0), T0 + 20)
    assert _types(res) == ["alert.resolved"] and res[0].payload.reason == "condition_cleared"


def test_queue_forecast_ignores_missing_horizon(cfg, engine):
    fc = forecast("counter-1", 10, ts=T0, horizon=30).model_copy(update={"horizons": {"30": 10.0}})
    assert engine.on_queue(queue_snap("counter-1", 1), fc, T0) == []


# ---------------------------------------------------------------- health / sync
def test_camera_down_and_back(engine):
    assert engine.on_health(heartbeat("ok"), T0) == []
    out = engine.on_health(heartbeat("stale", age_s=20.0), T0 + 20)
    assert _types(out) == ["alert.raised"]
    a = out[0].payload.alert
    assert a.kind == AlertKind.CAMERA_DOWN and a.subject_id == "cam-synth" and a.actions == [AckAction.CHECKED]
    assert "cam-synth" in a.message_hi
    # black frames while stale alert open -> still one alert
    assert engine.on_health(heartbeat("black"), T0 + 30) == []
    # owner checked -> acked, stays open until frames flow again
    assert _types(engine.on_ack(a.alert_id, AckAction.CHECKED, AckBy.WHATSAPP_SIM, T0 + 40)) == ["alert.acked"]
    assert engine.get(a.alert_id).status == AlertStatus.ACKED
    res = engine.on_health(heartbeat("ok"), T0 + 50)
    assert _types(res) == ["alert.resolved"] and res[0].payload.reason == "device_back"
    assert engine.open_alerts() == []


def test_sync_backlog(cfg, engine):
    after = cfg.rules.sync_backlog_after_s
    warn = cfg.rules.sync_backlog_warn
    # link down but a short blip, or a small backlog -> silent
    assert engine.on_sync(sync_status(LinkState.DOWN, backlog=warn + 10, down_since_ts=T0), T0 + after / 2) == []
    assert engine.on_sync(sync_status(LinkState.DOWN, backlog=warn - 1, down_since_ts=T0), T0 + after + 1) == []
    out = engine.on_sync(sync_status(LinkState.DOWN, backlog=warn, down_since_ts=T0), T0 + after + 60)
    assert _types(out) == ["alert.raised"]
    a = out[0].payload.alert
    assert a.kind == AlertKind.SYNC_BACKLOG and a.severity == Severity.INFO and a.subject_id == cfg.device.device_id
    assert a.actions == [] and str(warn) in a.message_en and "6" in a.message_en  # 360 s ~ 6 min
    assert engine.on_sync(sync_status(LinkState.DOWN, backlog=warn * 2, down_since_ts=T0), T0 + after + 120) == []
    res = engine.on_sync(sync_status(LinkState.UP, backlog=warn * 2, down_since_ts=None), T0 + after + 180)
    assert _types(res) == ["alert.resolved"] and res[0].payload.reason == "condition_cleared"
    # link up with nothing open -> nothing
    assert engine.on_sync(sync_status(LinkState.UP, backlog=0, down_since_ts=None), T0 + after + 200) == []


# ---------------------------------------------------------------- acks
def test_ack_false_positive_feedback(cfg, engine):
    seen: list[str] = []
    engine.feedback = seen.append
    generic: list[FalsePositive] = []
    engine.on_false_positive(generic.append)

    ch, view = shelf_change(cfg, "shelf-A", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=4.0)
    a = engine.on_shelf_change(ch, view, T0)[0].payload.alert
    out = engine.on_ack(a.alert_id, AckAction.FALSE_POSITIVE, AckBy.WHATSAPP_SIM, T0 + 30)
    assert _types(out) == ["alert.acked", "alert.resolved"]
    assert out[1].payload.reason == "false_positive"
    assert seen == ["shelf-A"]
    assert len(generic) == 1 and generic[0].kind == AlertKind.SHELF_GAP and generic[0].alert_id == a.alert_id
    got = engine.get(a.alert_id)
    assert got.status == AlertStatus.RESOLVED and got.ack_action == AckAction.FALSE_POSITIVE and got.ack_by == AckBy.WHATSAPP_SIM
    assert engine.open_alerts() == []
    # a second ack on the resolved alert is ignored, and unknown ids are ignored
    assert engine.on_ack(a.alert_id, AckAction.RESTOCKED, AckBy.BOARD, T0 + 40) == []
    assert engine.on_ack("nope", AckAction.RESTOCKED, AckBy.BOARD, T0 + 40) == []


def test_feedback_listener_error_does_not_break_ack(cfg, engine):
    def boom(_shelf_id: str) -> None:
        raise RuntimeError("learner crashed")

    engine.feedback = boom
    ch, view = shelf_change(cfg, "shelf-A", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=4.0)
    a = engine.on_shelf_change(ch, view, T0)[0].payload.alert
    out = engine.on_ack(a.alert_id, AckAction.FALSE_POSITIVE, AckBy.BOARD, T0 + 1)
    assert _types(out) == ["alert.acked", "alert.resolved"]


def test_ack_order_emits_order_requested(cfg, engine):
    ch, view = shelf_change(cfg, "shelf-A", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=4.0)
    a = engine.on_shelf_change(ch, view, T0)[0].payload.alert
    out = engine.on_ack(a.alert_id, AckAction.ORDER, AckBy.WHATSAPP_SIM, T0 + 30)
    assert _types(out) == ["alert.acked", "order.requested"]
    order: OrderRequested = out[1].payload
    sku = cfg.sku("AMUL-TAAZA-500")
    assert order.sku_id == sku.sku_id and order.alert_id == a.alert_id and order.channel == AckBy.WHATSAPP_SIM
    # velocity 18/h x 14 trading hours x 1 day lead = 252, already a multiple of 4 units/facing
    assert order.qty == 252
    assert order.est_cost_inr == round(252 * 27 * (1 - 0.08), 2)
    # alert remains ACKED (resolution waits for the shelf to be observed stocked)
    assert engine.get(a.alert_id).status == AlertStatus.ACKED
    assert len(engine.open_alerts()) == 1


def test_ack_ignore_suppresses_renag_until_condition_clears(cfg, engine):
    snap = queue_snap("counter-1", 5, long_since_ts=T0 - 100)
    a = engine.on_queue(snap, None, T0)[0].payload.alert
    engine.on_ack(a.alert_id, AckAction.IGNORE, AckBy.BOARD, T0 + 5)
    assert engine.get(a.alert_id).status == AlertStatus.ACKED
    assert engine.on_queue(snap, None, T0 + 10) == []  # no nagging
    engine.on_queue(queue_snap("counter-1", 0), None, T0 + 20)
    res = engine.on_queue(queue_snap("counter-1", 0), None, T0 + 60)
    assert _types(res) == ["alert.resolved"]


# ---------------------------------------------------------------- i18n
def test_messages_hi_en_contain_inr_and_menu(cfg, engine):
    ch, view = shelf_change(cfg, "shelf-A", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=20.0)
    a = engine.on_shelf_change(ch, view, T0)[0].payload.alert
    assert a.impact is not None
    # 27 x 18 x (20/60) x 0.31 = 50.22 -> rendered "50"
    assert a.impact.lost_sales_inr == pytest.approx(50.22)
    for msg in (a.message_en, a.message_hi):
        assert "₹50" in msg
        assert "1 =" in msg and "2 =" in msg and "3 =" in msg  # digit menu == actions[i-1]
        assert "20" in msg  # gap minutes
    assert len(a.actions) == 3
    assert "Amul Taaza 500ml" in a.title_en and "Amul Taaza 500ml" in a.message_en
    assert "अमूल ताज़ा 500ml" in a.title_hi and "अमूल ताज़ा 500ml" in a.message_hi
    assert "खाली" in a.message_hi and "empty" in a.message_en
    assert a.impact.basis in a.message_en  # English message cites the basis
    assert "?" not in a.message_en and "?" not in a.message_hi  # every template param was supplied

    q = engine.on_queue(queue_snap("counter-1", 6, long_since_ts=T0 - 100, est_wait_s=150), None, T0)[0].payload.alert
    # max(0, 6-4+1)=3 x 0.32 x 180 = 172.8 -> "₹173"
    for msg in (q.message_en, q.message_hi):
        assert "₹173" in msg and "1 =" in msg and "2 =" in msg and "Main counter" in msg
        assert "?" not in msg.replace("counter?", "").replace("खोलें?", "")  # only the template's own question mark
    assert "2.5" in q.message_en  # ~2.5 min wait


def test_all_kinds_render_without_placeholders(cfg, engine):
    engine.on_queue(queue_snap("counter-1", 1), forecast("counter-1", 7, ts=T0), T0)
    engine.on_health(heartbeat("error"), T0)
    engine.on_sync(sync_status(LinkState.DOWN, backlog=2000, down_since_ts=T0 - 1000), T0)
    for a in engine.open_alerts():
        for text in (a.title_en, a.title_hi, a.message_en, a.message_hi):
            assert text and "{" not in text, (a.kind, text)
            assert "?" not in text.replace("counter?", "").replace("खोलें?", ""), (a.kind, text)


# ---------------------------------------------------------------- restore
def test_restore_open_alerts(cfg, engine):
    ch, view = shelf_change(cfg, "shelf-A", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=4.0)
    a1 = engine.on_shelf_change(ch, view, T0)[0].payload.alert
    a2 = engine.on_queue(queue_snap("counter-1", 5, long_since_ts=T0 - 100), None, T0)[0].payload.alert
    engine.on_ack(a2.alert_id, AckAction.IGNORE, AckBy.BOARD, T0 + 1)
    ch2, view2 = shelf_change(cfg, "shelf-B", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=4.0)
    a3 = engine.on_shelf_change(ch2, view2, T0)[0].payload.alert
    engine.on_ack(a3.alert_id, AckAction.FALSE_POSITIVE, AckBy.BOARD, T0 + 2)

    persisted = [engine.get(a1.alert_id), engine.get(a2.alert_id), engine.get(a3.alert_id)]
    fresh = RuleEngine(cfg)  # simulated restart
    fresh.restore(persisted)

    assert {a.alert_id for a in fresh.open_alerts()} == {a1.alert_id, a2.alert_id}
    assert fresh.get(a3.alert_id).status == AlertStatus.RESOLVED
    assert fresh.get(a2.alert_id).status == AlertStatus.ACKED
    # dedupe survives the restart: the same shelf gap does not raise again ...
    assert fresh.on_shelf_change(ch, view, T0 + 100) == []
    # ... but its resolution works and a new gap on the FP'd shelf raises
    res = fresh.on_shelf_change(*shelf_change(cfg, "shelf-A", ShelfState.EMPTY, ShelfState.STOCKED, ts=T0 + 200, gap_minutes=8.0), T0 + 200)
    assert _types(res) == ["alert.resolved"]
    assert _types(fresh.on_shelf_change(ch2, view2, T0 + 300)) == ["alert.raised"]
    # restore does not alias the caller's objects
    assert fresh.get(a1.alert_id) is not persisted[0]


def test_restore_duplicate_open_keeps_newest(cfg):
    e1 = RuleEngine(cfg)
    ch, view = shelf_change(cfg, "shelf-A", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=4.0)
    old = e1.on_shelf_change(ch, view, T0)[0].payload.alert
    new = RuleEngine(cfg).on_shelf_change(ch, view, T0 + 10)[0].payload.alert
    e = RuleEngine(cfg)
    e.restore([new, old])
    assert [a.alert_id for a in e.open_alerts()] == [new.alert_id]
    assert e.get(old.alert_id).status == AlertStatus.RESOLVED


# ---------------------------------------------------------------- footfall spike (P1)
def test_footfall_spike(cfg, engine):
    from retailsense_contracts.enums import Direction, LineKind
    from retailsense_contracts.events import FootfallCrossing

    def ins(n: int):
        return [FootfallCrossing(line_id="entrance", line_kind=LineKind.ENTRANCE, direction=Direction.IN) for _ in range(n)]

    t = (T0 // 900) * 900  # bucket-aligned
    # three calm buckets of 4 visitors each
    for b in range(3):
        assert engine.on_footfall(ins(4), t + b * 900 + 10) == []
    # fourth bucket: 12 visitors >= 2.5 x 4 -> spike
    assert engine.on_footfall(ins(9), t + 3 * 900 + 10) == []
    out = engine.on_footfall(ins(3), t + 3 * 900 + 20)
    assert _types(out) == ["alert.raised"]
    a = out[0].payload.alert
    assert a.kind == AlertKind.FOOTFALL_SPIKE and a.details.count == 12 and "12" in a.message_hi
    assert engine.on_footfall(ins(2), t + 3 * 900 + 30) == []  # no duplicate
    # next bucket, calm again, after half the window -> resolved
    assert engine.on_footfall([], t + 4 * 900 + 100) == []
    res = engine.on_footfall(ins(1), t + 4 * 900 + 500)
    assert _types(res) == ["alert.resolved"]


# ---------------------------------------------------------------- contracts round-trip
def test_all_observations_validate_as_events(cfg, engine):
    obs = []
    ch, view = shelf_change(cfg, "shelf-A", ShelfState.STOCKED, ShelfState.EMPTY, ts=T0, gap_minutes=4.0)
    obs += engine.on_shelf_change(ch, view, T0)
    a = obs[0].payload.alert
    obs += engine.on_queue(queue_snap("counter-1", 2), forecast("counter-1", 8, ts=T0), T0)
    obs += engine.on_queue(queue_snap("counter-1", 9, long_since_ts=T0 - 100), forecast("counter-1", 8, ts=T0), T0 + 1)
    obs += engine.on_health(heartbeat("stale", age_s=30), T0 + 2)
    obs += engine.on_sync(sync_status(LinkState.DOWN, backlog=1500, down_since_ts=T0 - 400), T0 + 3)
    obs += engine.on_ack(a.alert_id, AckAction.ORDER, AckBy.WHATSAPP_SIM, T0 + 4)
    obs += engine.on_shelf_change(*shelf_change(cfg, "shelf-A", ShelfState.EMPTY, ShelfState.STOCKED, ts=T0 + 5, gap_minutes=9), T0 + 5)
    assert {o.type for o in obs} == {"alert.raised", "alert.resolved", "alert.acked", "order.requested"}
    for i, o in enumerate(obs, start=1):
        ev = make_event(o, store_id=cfg.store.store_id, device_id=cfg.device.device_id, seq=i, hlc=f"{i:013d}-0000-EDGE")
        ev2 = type(ev).model_validate_json(ev.model_dump_json())
        assert ev2.payload == o.payload
    # emitted AlertRaised payloads are snapshots, not aliases of the mutable engine state
    raised = [o.payload for o in obs if isinstance(o.payload, AlertRaised)]
    assert raised[0].alert.status == AlertStatus.OPEN and engine.get(a.alert_id).status == AlertStatus.RESOLVED
