"""Cloud alert book-keeping.

Two sources feed the ``alerts`` table:

1. **Mirrored edge alerts.**  The edge is the source of truth for shelf/queue/camera
   alerts (it renders Hindi + English text offline).  ``alert.raised`` /
   ``alert.acked`` / ``alert.resolved`` events are folded into the table so the
   cloud dashboard and the WhatsApp dispatcher see the same objects.
2. **Cloud-only rules** that the edge cannot evaluate about itself:
   * ``device_offline`` - no batch/heartbeat for ``device_offline_s`` (60 s) ->
     HIGH alert, auto-resolved (reason ``device_back``) on the next ingest.
   * ``shrink_suspect`` - a reconcile report row flagged by the integrations
     package (visual vs ERP stock) -> alert with ``ShrinkAlertDetails``.
   * ``sync_backlog_cloud`` is P2 and intentionally not implemented.

Invariant: one non-resolved alert per ``(store_id, kind, subject_id)`` - enforced by
the contracts' partial unique index and honoured here by superseding the older
open alert before inserting a new one.
"""

from dataclasses import dataclass, field

from sqlalchemy import and_, select

from retailsense_contracts import db as cdb
from retailsense_contracts.alerts import (
    ACTIONS_BY_KIND,
    Alert,
    AlertAckRequest,
    DeviceAlertDetails,
    ShrinkAlertDetails,
)
from retailsense_contracts.api import ReconcileReport
from retailsense_contracts.clock import Clock, store_date
from retailsense_contracts.enums import AckAction, AckBy, AlertKind, AlertStatus, Origin, Severity
from retailsense_contracts.events import Event
from retailsense_contracts.i18n import render
from retailsense_contracts.ids import new_ulid
from retailsense_contracts.logging import get_logger

from .db import Database

log = get_logger("sensecloud.alerting")

CLOUD_DEVICE_ID = "cloud"


@dataclass
class AlertDelta:
    """What changed during one alerting pass (drives the dispatcher and the WS fan-out)."""

    raised: list[Alert] = field(default_factory=list)
    updated: list[Alert] = field(default_factory=list)

    def extend(self, other: "AlertDelta") -> None:
        self.raised.extend(other.raised)
        self.updated.extend(other.updated)


class Alerting:
    def __init__(self, db: Database, clock: Clock, *, device_offline_s: float = 60.0):
        self.db = db
        self.clock = clock
        self.device_offline_s = device_offline_s

    # ----------------------------------------------------------------- store
    def upsert(self, alert: Alert) -> None:
        with self.db.tx() as conn:
            self._upsert(conn, alert)

    def _upsert(self, conn, alert: Alert) -> None:
        """Insert/update; an older open alert with the same (kind, subject) is superseded first."""
        a = cdb.cloud_alerts
        if alert.status != AlertStatus.RESOLVED:
            clash = self.db.one(
                conn,
                select(a.c.alert_id, a.c.doc).where(
                    and_(
                        a.c.store_id == alert.store_id,
                        a.c.kind == str(alert.kind),
                        a.c.subject_id == alert.subject_id,
                        a.c.status != str(AlertStatus.RESOLVED),
                        a.c.alert_id != alert.alert_id,
                    )
                ),
            )
            if clash:
                old = Alert.model_validate(clash["doc"]).model_copy(
                    update={"status": AlertStatus.RESOLVED, "resolved_ts": alert.raised_ts}
                )
                self.db.upsert(conn, a, self.db.alert_row(old))
        self.db.upsert(conn, a, self.db.alert_row(alert))

    def get(self, alert_id: str) -> Alert | None:
        return self.db.alert(alert_id)

    def list(self, store_id: str, status: str | None = "open", limit: int = 100) -> list[Alert]:
        a = cdb.cloud_alerts
        cond = [a.c.store_id == store_id]
        if status and status != "all":
            cond.append(a.c.status == status)
        with self.db.read() as conn:
            rows = self.db.rows(conn, select(a.c.doc).where(and_(*cond)).order_by(a.c.raised_ts.desc()).limit(limit))
        return [Alert.model_validate(r["doc"]) for r in rows]

    def open_for(self, store_id: str, kind: AlertKind, subject_id: str) -> Alert | None:
        a = cdb.cloud_alerts
        with self.db.read() as conn:
            row = self.db.one(
                conn,
                select(a.c.doc).where(
                    and_(
                        a.c.store_id == store_id,
                        a.c.kind == str(kind),
                        a.c.subject_id == subject_id,
                        a.c.status != str(AlertStatus.RESOLVED),
                    )
                ),
            )
        return Alert.model_validate(row["doc"]) if row else None

    # ---------------------------------------------------------------- mirror
    def mirror(self, events: list[Event]) -> AlertDelta:
        """Fold alert.* events from an ingest batch into the cloud alerts table."""
        delta = AlertDelta()
        ordered = sorted((e for e in events if e.type.startswith("alert.")), key=lambda e: (e.ts, e.seq))
        with self.db.tx() as conn:
            for ev in ordered:
                p = ev.payload
                if ev.type == "alert.raised":
                    alert = p.alert
                    self._upsert(conn, alert)
                    delta.raised.append(alert)
                    continue
                row = self.db.one(
                    conn, select(cdb.cloud_alerts.c.doc).where(cdb.cloud_alerts.c.alert_id == p.alert_id)
                )
                if row is None:
                    continue  # ack/resolve for an alert we never saw (ordering across batches); ignore
                alert = Alert.model_validate(row["doc"])
                if ev.type == "alert.acked":
                    alert = alert.model_copy(
                        update={
                            "status": AlertStatus.ACKED
                            if alert.status != AlertStatus.RESOLVED
                            else AlertStatus.RESOLVED,
                            "acked_ts": ev.ts,
                            "ack_action": p.action,
                            "ack_by": p.by,
                        }
                    )
                elif ev.type == "alert.resolved":
                    alert = alert.model_copy(update={"status": AlertStatus.RESOLVED, "resolved_ts": ev.ts})
                    self._upsert(conn, alert)
                    if p.recovered is not None:
                        conn.execute(
                            cdb.cloud_alerts.update()
                            .where(cdb.cloud_alerts.c.alert_id == alert.alert_id)
                            .values(recovered_inr=p.recovered.lost_sales_inr)
                        )
                    delta.updated.append(alert)
                    continue
                self._upsert(conn, alert)
                delta.updated.append(alert)
        return delta

    # ------------------------------------------------------------------- ack
    def ack(self, alert_id: str, req: AlertAckRequest, ts: float | None = None) -> Alert | None:
        """Cloud-side ack: status -> acked (false_positive also resolves). Returns the updated alert."""
        ts = self.clock.now() if ts is None else ts
        alert = self.get(alert_id)
        if alert is None:
            return None
        if req.action not in alert.actions and alert.actions:
            log.warning("ack action %s not offered by alert %s (%s)", req.action, alert_id, alert.actions)
        update = {"acked_ts": ts, "ack_action": req.action, "ack_by": req.by}
        if alert.status != AlertStatus.RESOLVED:
            update["status"] = AlertStatus.ACKED
        if req.action == AckAction.FALSE_POSITIVE:
            update["status"] = AlertStatus.RESOLVED
            update["resolved_ts"] = ts
        alert = alert.model_copy(update=update)
        self.upsert(alert)
        return alert

    # -------------------------------------------------------- device_offline
    def check_devices(self, now: float | None = None) -> AlertDelta:
        """Raise device_offline for devices silent > device_offline_s; flip their status."""
        now = self.clock.now() if now is None else now
        delta = AlertDelta()
        with self.db.read() as conn:
            rows = self.db.rows(conn, select(cdb.devices))
        for d in rows:
            last = d.get("last_seen_ts")
            if last is None or now - float(last) <= self.device_offline_s:
                continue
            if self.open_for(d["store_id"], AlertKind.DEVICE_OFFLINE, d["device_id"]) is not None:
                continue
            alert = self._device_offline_alert(d["store_id"], d["device_id"], float(last), now)
            with self.db.tx() as conn:
                self._upsert(conn, alert)
                conn.execute(
                    cdb.devices.update().where(cdb.devices.c.device_id == d["device_id"]).values(status="offline")
                )
            delta.raised.append(alert)
            log.info("device_offline raised for %s (silent %.0f s)", d["device_id"], now - float(last))
        return delta

    def device_back(self, store_id: str, device_id: str, now: float | None = None) -> AlertDelta:
        """Auto-resolve an open device_offline alert when the device talks to us again."""
        now = self.clock.now() if now is None else now
        delta = AlertDelta()
        alert = self.open_for(store_id, AlertKind.DEVICE_OFFLINE, device_id)
        if alert is not None:
            alert = alert.model_copy(update={"status": AlertStatus.RESOLVED, "resolved_ts": now})
            self.upsert(alert)
            delta.updated.append(alert)
            log.info("device_offline resolved for %s (device_back)", device_id)
        return delta

    def _device_offline_alert(self, store_id: str, device_id: str, last_seen_ts: float, now: float) -> Alert:
        tz = self.db.store_tz(store_id)
        since = store_date(last_seen_ts, tz)
        params = {"device_id": device_id, "since": since}
        return Alert(
            alert_id=new_ulid(now),
            store_id=store_id,
            device_id=CLOUD_DEVICE_ID,
            origin=Origin.CLOUD,
            kind=AlertKind.DEVICE_OFFLINE,
            severity=Severity.HIGH,
            subject_id=device_id,
            title_en=render("device_offline.title", "en", **params),
            title_hi=render("device_offline.title", "hi", **params),
            message_en=render("device_offline.msg", "en", **params),
            message_hi=render("device_offline.msg", "hi", **params),
            details=DeviceAlertDetails(device_id=device_id, last_seen_ts=last_seen_ts),
            impact=None,
            actions=list(ACTIONS_BY_KIND[AlertKind.DEVICE_OFFLINE]),
            raised_ts=now,
        )

    # --------------------------------------------------------- shrink_suspect
    def on_reconcile(self, report: ReconcileReport, now: float | None = None) -> AlertDelta:
        """Persist stock_recon rows and raise shrink_suspect for flagged SKUs."""
        now = self.clock.now() if now is None else now
        delta = AlertDelta()
        cfg = self.db.store_config(report.store_id)
        with self.db.tx() as conn:
            for row in report.rows:
                conn.execute(
                    cdb.stock_recon.insert().values(
                        id=new_ulid(now),
                        store_id=report.store_id,
                        sku_id=row.sku_id,
                        shelf_id=row.shelf_id,
                        ts=report.ts,
                        visual_units=row.visual_units,
                        system_units=row.system_units,
                        delta_units=row.delta_units,
                        delta_inr=row.delta_inr,
                        source=report.source,
                    )
                )
                if not row.flagged:
                    continue
                if self.open_for(report.store_id, AlertKind.SHRINK_SUSPECT, row.sku_id) is not None:
                    continue
                sku = cfg.sku(row.sku_id) if cfg else None
                name_en = sku.name_en if sku else row.name
                name_hi = sku.name_hi if sku else row.name
                base = {
                    "system_units": row.system_units,
                    "visual_units": row.visual_units,
                    "delta_inr": abs(row.delta_inr),
                }
                alert = Alert(
                    alert_id=new_ulid(now),
                    store_id=report.store_id,
                    device_id=CLOUD_DEVICE_ID,
                    origin=Origin.CLOUD,
                    kind=AlertKind.SHRINK_SUSPECT,
                    severity=Severity.WARN,
                    subject_id=row.sku_id,
                    title_en=render("shrink_suspect.title", "en", sku_name=name_en),
                    title_hi=render("shrink_suspect.title", "hi", sku_name=name_hi),
                    message_en=render("shrink_suspect.msg", "en", sku_name=name_en, **base),
                    message_hi=render("shrink_suspect.msg", "hi", sku_name=name_hi, **base),
                    details=ShrinkAlertDetails(
                        sku_id=row.sku_id,
                        sku_name=name_en,
                        visual_units=row.visual_units,
                        system_units=row.system_units,
                        delta_units=row.delta_units,
                        delta_inr=row.delta_inr,
                    ),
                    impact=None,
                    actions=list(ACTIONS_BY_KIND[AlertKind.SHRINK_SUSPECT]),
                    raised_ts=now,
                )
                self._upsert(conn, alert)
                delta.raised.append(alert)
        return delta


def ack_by_for_channel(channel: str) -> AckBy:
    if "telegram" in channel:
        return AckBy.TELEGRAM
    if channel in ("whatsapp", "whatsapp_cloud", "cloud_api"):
        return AckBy.WHATSAPP
    return AckBy.WHATSAPP_SIM


__all__ = ["CLOUD_DEVICE_ID", "AlertDelta", "Alerting", "ack_by_for_channel"]
