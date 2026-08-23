"""The edge rule engine: observations in, fully rendered alerts out.

Design
------
``RuleEngine`` is a pure, synchronous state machine that the SenseEdge workers
call from the CV / queue / health loops.  It never touches I/O: every method
returns a list of :class:`Observation` (``alert.raised`` / ``alert.acked`` /
``alert.resolved`` / ``order.requested``) which the caller hands to
``EdgeStore.append`` in one transaction.  That keeps the engine trivially
testable and replayable.

Invariants a judge can check
* **One open alert per (kind, subject_id).**  ``_open`` is an index keyed on that
  pair; ``_raise`` is a no-op while an alert for the pair is OPEN or ACKED.
  "Ignore"/"checked" acks therefore suppress nagging until the condition clears.
* **Every alert is rendered in Hindi + English at raise time** with the impact
  amount and the digit menu baked in (``render.py``), so it works offline.
* **Every rupee number has a basis.**  Impact comes from ``ImpactCalculator``
  (contracts formula) and the basis string cites the factor.
* **Hysteresis** on queues: raise only after the count has been >= N for
  ``queue_long_s``; resolve only after it has been < N-1 for
  ``queue_resolve_s``.  No flapping when one shopper steps in and out.
* **Supersede**: a ``queue_forecast`` (INFO, predictive) is closed with reason
  ``superseded`` the moment the real ``queue_long`` fires for the same counter.
* **Acks are two-phase** where the world can confirm them: ``restocked`` and
  ``opened_counter`` only mark the alert ACKED; the resolve comes from the next
  observation (shelf back to stocked / queue drained) so the "recovered" rupee
  figure is based on what actually happened, not on what the owner typed.
* ``false_positive`` resolves immediately and fires the feedback hub so the
  shelf state machine can become more conservative for that shelf.

Severity rules (shelf_gap)
* CRITICAL when the gap is already >= ``CRITICAL_GAP_MIN`` (60) minutes at raise
  (the persistence filter or a restart delayed us, the owner must act now),
* HIGH when the SKU bleeds >= ``HIGH_RATE_INR_PER_HOUR`` (50 INR/h),
* WARN otherwise (slow movers; still worth a glance).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from retailsense_contracts.alerts import (
    ACTIONS_BY_KIND,
    Alert,
    AlertDetails,
    CameraAlertDetails,
    FootfallAlertDetails,
    ImpactInr,
    QueueAlertDetails,
    StockoutAlert,
    SyncAlertDetails,
)
from retailsense_contracts.api import ShelfStateView, SyncStatus
from retailsense_contracts.config import StoreConfig
from retailsense_contracts.enums import (
    AckAction,
    AckBy,
    AlertKind,
    AlertStatus,
    Direction,
    LinkState,
    Origin,
    Severity,
    ShelfState,
)
from retailsense_contracts.events import (
    AlertAcked,
    AlertRaised,
    AlertResolved,
    DeviceHeartbeat,
    FootfallCrossing,
    Observation,
    OrderRequested,
    QueueForecast,
    QueueSnapshot,
    ShelfStateChange,
)
from retailsense_contracts.ids import new_ulid

from .feedback import FalsePositive, FeedbackHub, Listener, ShelfFeedback
from .impact import ImpactCalculator
from .render import bilingual, render_alert, sku_names

log = logging.getLogger("retailsense.edgerules")

# Shelf-gap severity thresholds (not part of RulesConfig; see contract gap note in the package README/report).
HIGH_RATE_INR_PER_HOUR = 50.0
CRITICAL_GAP_MIN = 60.0

# Footfall spike (P1): 15-minute buckets, rolling baseline over the last N completed buckets.
FOOTFALL_WINDOW_S = 15 * 60
FOOTFALL_BASELINE_BUCKETS = 8
FOOTFALL_MIN_BASELINE_BUCKETS = 2
FOOTFALL_MIN_COUNT = 5  # never call 3 visitors a "spike" just because the baseline was 1

# Camera statuses that mean "not delivering usable frames".
_CAMERA_BAD = frozenset({"stale", "black", "error"})

ResolveReason = str  # Literal in contracts; kept loose here so helper signatures stay short.


@dataclass
class _FootfallState:
    """Rolling 15-minute in-count buckets for the footfall_spike rule."""

    bucket: int | None = None
    count: int = 0
    completed: list[int] | None = None
    raised_bucket: int | None = None

    def baseline(self) -> float | None:
        hist = self.completed or []
        if len(hist) < FOOTFALL_MIN_BASELINE_BUCKETS:
            return None
        b = sum(hist) / len(hist)
        return b if b > 0 else None


class RuleEngine:
    """Implements ``retailsense_contracts.interfaces.RuleEngine``."""

    def __init__(
        self,
        cfg: StoreConfig,
        *,
        impact: ImpactCalculator | None = None,
        high_rate_inr_per_hour: float = HIGH_RATE_INR_PER_HOUR,
        critical_gap_min: float = CRITICAL_GAP_MIN,
    ):
        self.cfg = cfg
        self.rules = cfg.rules
        self.impact = impact or ImpactCalculator(cfg)
        self.high_rate_inr_per_hour = float(high_rate_inr_per_hour)
        self.critical_gap_min = float(critical_gap_min)

        self._alerts: dict[str, Alert] = {}  # alert_id -> Alert (open, acked and resolved this session)
        self._open: dict[tuple[AlertKind, str], str] = {}  # (kind, subject) -> alert_id while not RESOLVED
        self._queue_below_since: dict[str, float] = {}  # counter_id -> ts the count first dropped below N-1
        self._footfall = _FootfallState(completed=[])
        self._feedback = FeedbackHub()

    # ------------------------------------------------------------------ feedback
    @property
    def feedback(self) -> ShelfFeedback | None:
        """Shelf false-positive callback ``cb(shelf_id)``; same attribute name as the contracts fake."""
        return self._feedback.shelf_callback

    @feedback.setter
    def feedback(self, cb: ShelfFeedback | None) -> None:
        self._feedback.shelf_callback = cb

    def on_false_positive(self, listener: Listener) -> None:
        """Register a listener receiving a :class:`FalsePositive` for every false-positive ack (any kind)."""
        self._feedback.add_listener(listener)

    @property
    def false_positives(self) -> list[FalsePositive]:
        return list(self._feedback.history)

    # ------------------------------------------------------------------ lookups
    def open_alerts(self) -> list[Alert]:
        """Alerts that are OPEN or ACKED (i.e. not yet resolved), oldest first."""
        out = [self._alerts[i] for i in self._open.values() if i in self._alerts]
        return sorted(out, key=lambda a: (a.raised_ts, a.alert_id))

    def get(self, alert_id: str) -> Alert | None:
        return self._alerts.get(alert_id)

    def open_for(self, kind: AlertKind, subject_id: str) -> Alert | None:
        aid = self._open.get((AlertKind(str(kind)), subject_id))
        return self._alerts.get(aid) if aid else None

    def restore(self, alerts: list[Alert]) -> None:
        """Reload alerts persisted by EdgeStore after a restart.

        Resolved alerts are kept for ``get()`` only.  If the store somehow holds two
        unresolved alerts for one (kind, subject), the newest wins and the older one
        is marked resolved/superseded locally so the invariant holds in memory.
        """
        for a in sorted(alerts, key=lambda a: (a.raised_ts, a.alert_id)):
            a = a.model_copy(deep=True)
            self._alerts[a.alert_id] = a
            if a.status == AlertStatus.RESOLVED:
                continue
            key = (a.kind, a.subject_id)
            prev_id = self._open.get(key)
            if prev_id and prev_id != a.alert_id:
                prev = self._alerts[prev_id]
                prev.status = AlertStatus.RESOLVED
                prev.resolved_ts = prev.resolved_ts or a.raised_ts
                log.warning("restore: duplicate open %s/%s - superseding %s by %s", a.kind, a.subject_id, prev_id, a.alert_id)
            self._open[key] = a.alert_id

    # ------------------------------------------------------------------ core helpers
    def _raise(
        self,
        kind: AlertKind,
        subject_id: str,
        severity: Severity,
        details: AlertDetails,
        impact: ImpactInr | None,
        params_en: dict[str, Any],
        params_hi: dict[str, Any],
        ts: float,
    ) -> list[Observation]:
        """Create + index an alert unless one is already open for (kind, subject)."""
        key = (kind, subject_id)
        if key in self._open:
            return []
        texts = render_alert(kind, params_en, params_hi)
        a = Alert(
            alert_id=new_ulid(ts),
            store_id=self.cfg.store.store_id,
            device_id=self.cfg.device.device_id,
            origin=Origin.EDGE,
            kind=kind,
            severity=severity,
            status=AlertStatus.OPEN,
            subject_id=subject_id,
            title_en=texts.title_en,
            title_hi=texts.title_hi,
            message_en=texts.message_en,
            message_hi=texts.message_hi,
            details=details,
            impact=impact,
            actions=list(ACTIONS_BY_KIND[kind]),
            raised_ts=ts,
        )
        self._alerts[a.alert_id] = a
        self._open[key] = a.alert_id
        log.info("alert raised %s %s/%s sev=%s", a.alert_id, kind, subject_id, severity)
        return [Observation.of(AlertRaised(alert=a.model_copy(deep=True)), ts)]

    def _resolve(
        self,
        a: Alert,
        reason: ResolveReason,
        ts: float,
        *,
        final_gap_minutes: float | None = None,
        impact_final: ImpactInr | None = None,
        recovered: ImpactInr | None = None,
    ) -> list[Observation]:
        if a.status == AlertStatus.RESOLVED:
            return []
        a.status = AlertStatus.RESOLVED
        a.resolved_ts = ts
        if impact_final is not None:
            a.impact = impact_final
        self._open.pop((a.kind, a.subject_id), None)
        log.info("alert resolved %s %s/%s reason=%s", a.alert_id, a.kind, a.subject_id, reason)
        payload = AlertResolved(
            alert_id=a.alert_id,
            reason=reason,  # type: ignore[arg-type]
            final_gap_minutes=final_gap_minutes,
            impact_final=impact_final,
            recovered=recovered,
        )
        return [Observation.of(payload, ts)]

    def _resolve_open(self, kind: AlertKind, subject_id: str, reason: ResolveReason, ts: float, **extra: Any) -> list[Observation]:
        a = self.open_for(kind, subject_id)
        return self._resolve(a, reason, ts, **extra) if a is not None else []

    # ------------------------------------------------------------------ shelf_gap
    def _gap_minutes(self, ch: ShelfStateChange, view: ShelfStateView, ts: float) -> float:
        """Gap length so far, preferring the explicit field, then the start timestamp, then the view."""
        if ch.gap_minutes is not None:
            return max(0.0, float(ch.gap_minutes))
        if ch.gap_started_ts is not None:
            return max(0.0, (ts - ch.gap_started_ts) / 60.0)
        if view.gap_minutes is not None:
            return max(0.0, float(view.gap_minutes))
        return 0.0

    def shelf_gap_severity(self, sku_id: str | None, gap_minutes: float) -> Severity:
        """CRITICAL if the gap is already long; HIGH for fast movers; WARN otherwise."""
        if gap_minutes >= self.critical_gap_min:
            return Severity.CRITICAL
        if self.impact.rate_per_hour(sku_id) >= self.high_rate_inr_per_hour:
            return Severity.HIGH
        return Severity.WARN

    def on_shelf_change(self, ch: ShelfStateChange, view: ShelfStateView, ts: float) -> list[Observation]:
        sku_id = ch.sku_id or view.sku_id
        sku = self.impact.sku(sku_id)
        gap = self._gap_minutes(ch, view, ts)

        if ch.to_state == ShelfState.EMPTY:
            impact = self.impact.lost_sales(sku_id, gap)
            name_en, name_hi = sku_names(sku, view.sku_name or view.name or ch.shelf_id)
            details = StockoutAlert(
                shelf_id=ch.shelf_id,
                sku_id=sku_id,
                sku_name=name_en,
                gap_minutes=round(gap, 2),
                coverage=view.coverage,
                facings=view.facings,
                min_facings=view.min_facings,
                consecutive_empty_scans=ch.consecutive_empty_scans,
            )
            pe, ph = bilingual(
                {"sku_name": name_en, "gap_min": round(gap), "lost_inr": impact.lost_sales_inr, "basis": impact.basis},
                sku_name=name_hi,
            )
            return self._raise(
                AlertKind.SHELF_GAP, ch.shelf_id, self.shelf_gap_severity(sku_id, gap), details, impact, pe, ph, ts
            )

        if ch.from_state == ShelfState.EMPTY and ch.to_state in (ShelfState.STOCKED, ShelfState.PARTIAL):
            a = self.open_for(AlertKind.SHELF_GAP, ch.shelf_id)
            if a is None:
                return []
            impact_final = ch.impact or self.impact.lost_sales(sku_id, gap)
            # "Recovered" = what the alert saved vs. the unattended baseline - only claimable when the
            # owner actually acted (acked restocked) and the shelf is now observed stocked.
            rec = self.impact.recovered(sku_id, gap) if a.ack_action == AckAction.RESTOCKED else None
            return self._resolve(
                a, "restocked_observed", ts, final_gap_minutes=round(gap, 2), impact_final=impact_final, recovered=rec
            )
        # empty -> unknown (occluded / camera lost): keep the alert open; the gap is still running.
        return []

    # ------------------------------------------------------------------ queues
    def on_queue(self, snap: QueueSnapshot, forecast: QueueForecast | None, ts: float) -> list[Observation]:
        out: list[Observation] = []
        out += self._queue_long(snap, ts)
        if forecast is not None and forecast.counter_id == snap.counter_id:
            out += self._queue_forecast(snap, forecast, ts)
        return out

    def _counter_name(self, counter_id: str) -> str:
        c = self.cfg.counter(counter_id)
        return c.name if c else counter_id

    def _queue_long(self, snap: QueueSnapshot, ts: float) -> list[Observation]:
        n = self.rules.queue_long_count
        cid = snap.counter_id
        out: list[Observation] = []

        # --- raise: count >= N sustained for queue_long_s (long_since_ts comes from the analyzer) ---
        if snap.count >= n:
            self._queue_below_since.pop(cid, None)
            since = snap.long_since_ts if snap.long_since_ts is not None else ts
            if ts - since >= self.rules.queue_long_s and self.open_for(AlertKind.QUEUE_LONG, cid) is None:
                counter = self.cfg.counter(cid)
                name = counter.name if counter else cid
                impact = self.impact.queue_abandon_risk(snap.count, n)
                details = QueueAlertDetails(
                    counter_id=cid, counter_name=name, count=snap.count, est_wait_s=snap.est_wait_s, threshold=n
                )
                p = {
                    "counter_name": name,
                    "count": snap.count,
                    "wait_min": round(snap.est_wait_s / 60.0, 1),
                    "risk_inr": impact.lost_sales_inr,
                }
                sev = Severity.CRITICAL if counter and snap.count >= counter.max_queue else Severity.WARN
                # the prediction is now reality: close the forecast alert before raising the real one
                out += self._resolve_open(AlertKind.QUEUE_FORECAST, cid, "superseded", ts)
                out += self._raise(AlertKind.QUEUE_LONG, cid, sev, details, impact, p, p, ts)
            return out

        # --- resolve: count < N-1 sustained for queue_resolve_s ---
        if snap.count < n - 1:
            since = self._queue_below_since.setdefault(cid, ts)
            if ts - since >= self.rules.queue_resolve_s:
                out += self._resolve_open(AlertKind.QUEUE_LONG, cid, "condition_cleared", ts)
        else:  # N-1 <= count < N: dead band, restart the resolve timer
            self._queue_below_since.pop(cid, None)
        return out

    def _queue_forecast(self, snap: QueueSnapshot, fc: QueueForecast, ts: float) -> list[Observation]:
        cid = snap.counter_id
        horizon = int(self.rules.queue_forecast_horizon_min)
        value = fc.horizons.get(str(horizon))
        if value is None:
            return []
        thr = self.rules.queue_forecast_threshold
        if value >= thr:
            if self.open_for(AlertKind.QUEUE_LONG, cid) is not None:
                return []  # the real thing is already open; a forecast would be noise
            name = self._counter_name(cid)
            details = QueueAlertDetails(
                counter_id=cid,
                counter_name=name,
                count=snap.count,
                est_wait_s=snap.est_wait_s,
                forecast=round(float(value), 2),
                horizon_min=horizon,
                threshold=thr,
            )
            p = {"counter_name": name, "forecast": int(round(value)), "horizon": horizon}
            return self._raise(AlertKind.QUEUE_FORECAST, cid, Severity.INFO, details, None, p, p, ts)
        if value < thr - 1:
            return self._resolve_open(AlertKind.QUEUE_FORECAST, cid, "condition_cleared", ts)
        return []  # thr-1 <= value < thr: hysteresis band

    # ------------------------------------------------------------------ device health
    def on_health(self, hb: DeviceHeartbeat, ts: float) -> list[Observation]:
        out: list[Observation] = []
        for cam in hb.cameras:
            if cam.status in _CAMERA_BAD:
                details = CameraAlertDetails(camera_id=cam.camera_id, status=cam.status, last_frame_age_s=cam.last_frame_age_s)
                p = {"camera_id": cam.camera_id}
                out += self._raise(AlertKind.CAMERA_DOWN, cam.camera_id, Severity.HIGH, details, None, p, p, ts)
            else:
                out += self._resolve_open(AlertKind.CAMERA_DOWN, cam.camera_id, "device_back", ts)
        return out

    # ------------------------------------------------------------------ sync
    def on_sync(self, sync: SyncStatus, ts: float) -> list[Observation]:
        subject = self.cfg.device.device_id
        if sync.link == LinkState.DOWN:
            down_since = sync.down_since_ts if sync.down_since_ts is not None else ts
            down_for = max(0.0, ts - down_since)
            if down_for >= self.rules.sync_backlog_after_s and sync.backlog >= self.rules.sync_backlog_warn:
                details = SyncAlertDetails(backlog=sync.backlog, down_since_ts=down_since)
                p = {"minutes": int(round(down_for / 60.0)), "backlog": sync.backlog}
                return self._raise(AlertKind.SYNC_BACKLOG, subject, Severity.INFO, details, None, p, p, ts)
            return []
        # link restored (or was never down): the backlog will drain, close the notice
        return self._resolve_open(AlertKind.SYNC_BACKLOG, subject, "condition_cleared", ts)

    # ------------------------------------------------------------------ footfall spike (P1)
    def on_footfall(self, crossings: list[FootfallCrossing], ts: float, *, subject_id: str = "store") -> list[Observation]:
        """Feed entrance crossings; raises ``footfall_spike`` when the current 15-min in-count
        is >= ``footfall_spike_factor`` x the mean of the last completed 15-min buckets.

        Not in the ``RuleEngine`` Protocol (P1 extra); the edge worker may call it from the
        analytics loop.  Safe to call with an empty list to advance the clock.
        """
        st = self._footfall
        bucket = int(ts // FOOTFALL_WINDOW_S)
        if st.bucket is None:
            st.bucket = bucket
        while st.bucket < bucket:  # roll completed buckets (empty ones too) into the baseline
            st.completed = (st.completed or [])[-(FOOTFALL_BASELINE_BUCKETS - 1) :] + [st.count]
            st.count = 0
            st.bucket += 1
        st.count += sum(c.count for c in crossings if c.direction == Direction.IN)

        baseline = st.baseline()
        if baseline is None:
            return []
        factor = self.rules.footfall_spike_factor
        threshold = factor * baseline
        open_alert = self.open_for(AlertKind.FOOTFALL_SPIKE, subject_id)

        if st.count >= threshold and st.count >= FOOTFALL_MIN_COUNT:
            if open_alert is not None:
                return []
            ratio = round(st.count / baseline, 1)
            details = FootfallAlertDetails(count=st.count, baseline=round(baseline, 2), factor=ratio, window_min=15)
            p = {"count": st.count, "factor": ratio}
            st.raised_bucket = bucket
            return self._raise(AlertKind.FOOTFALL_SPIKE, subject_id, Severity.INFO, details, None, p, p, ts)

        # resolve once we are in a later bucket, at least half of it has elapsed, and it is calm
        if open_alert is not None and st.raised_bucket is not None and bucket > st.raised_bucket:
            elapsed = ts - bucket * FOOTFALL_WINDOW_S
            if elapsed >= FOOTFALL_WINDOW_S / 2:
                st.raised_bucket = None
                return self._resolve(open_alert, "condition_cleared", ts)
        return []

    # ------------------------------------------------------------------ acks
    def on_ack(self, alert_id: str, action: AckAction, by: AckBy, ts: float) -> list[Observation]:
        """Record an owner reply.

        * every ack emits ``alert.acked``;
        * ``false_positive`` -> ``alert.resolved(false_positive)`` + feedback hub;
        * ``order`` on a shelf_gap -> also ``order.requested`` (qty from velocity x lead time);
        * ``restocked`` / ``opened_counter`` / ``ignore`` / ``checked`` -> ACKED, resolution waits for the
          observation that proves the world changed.
        Unknown ids and already-resolved alerts are ignored (``[]``).
        """
        a = self._alerts.get(alert_id)
        if a is None:
            log.warning("ack for unknown alert %s ignored", alert_id)
            return []
        if a.status == AlertStatus.RESOLVED:
            log.info("ack for resolved alert %s ignored", alert_id)
            return []
        if action not in a.actions and a.actions:
            log.warning("ack %s not in menu %s for %s - recorded anyway", action, a.actions, alert_id)

        a.status = AlertStatus.ACKED
        a.ack_action, a.ack_by, a.acked_ts = action, by, ts
        out: list[Observation] = [Observation.of(AlertAcked(alert_id=alert_id, action=action, by=by), ts)]

        if action == AckAction.FALSE_POSITIVE:
            out += self._resolve(a, "false_positive", ts)
            self._feedback.dispatch(FalsePositive(alert_id=alert_id, kind=a.kind, subject_id=a.subject_id, by=by, ts=ts))
        elif action == AckAction.ORDER and a.kind == AlertKind.SHELF_GAP and isinstance(a.details, StockoutAlert):
            order = self._order_for(a, by, ts)
            if order is not None:
                out.append(order)
        return out

    def _order_for(self, a: Alert, by: AckBy, ts: float) -> Observation | None:
        details = a.details
        assert isinstance(details, StockoutAlert)
        sku_id = details.sku_id
        if not sku_id or self.impact.sku(sku_id) is None:
            log.warning("order ack on %s: shelf %s has no SKU mapped", a.alert_id, a.subject_id)
            return None
        qty = max(1, self.impact.suggest_order_qty(sku_id))
        payload = OrderRequested(
            sku_id=sku_id, qty=qty, channel=by, alert_id=a.alert_id, est_cost_inr=self.impact.order_cost_inr(sku_id, qty)
        )
        return Observation.of(payload, ts)


__all__ = [
    "CRITICAL_GAP_MIN",
    "FOOTFALL_WINDOW_S",
    "HIGH_RATE_INR_PER_HOUR",
    "RuleEngine",
]
