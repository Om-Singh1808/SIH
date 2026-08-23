"""Batch ingest: the one write path from edge devices into the cloud.

Guarantees (and how they are achieved)
--------------------------------------
* **Idempotent.**  ``events.event_id`` (ULID) is the primary key and ``(device_id,
  seq)`` is unique; rows are written with ``INSERT .. ON CONFLICT DO NOTHING`` so a
  batch re-sent after a lost ack counts as ``duplicates`` and inserts nothing.
* **Order provable.**  Every device numbers its events 1, 2, 3, ... without gaps.
  After the insert we look at the contiguous range ``[last_seq+1, max_seq]`` and
  report every number that is *not* in the table as a gap.  Because the check is
  against the table (not just the batch), a gap that is filled by a later replay
  batch disappears on its own.
* **Auth.**  ``X-Device-Token`` must match the device's registered token, except
  when ``SENSECLOUD_DEV=1`` (demo): unknown devices are then auto-registered.
* **Everything the device needs comes back in the ack:** pending ``Command`` rows
  are attached and marked delivered in the same transaction.
* Side effects after the commit (aggregation, alert mirroring, device_offline
  auto-resolve) are returned as an ``IngestResult`` so the router can fan them
  out over WebSocket and hand new alerts to the dispatcher.

Performance: 500 events = one validation pass + two chunked multi-row inserts +
a handful of single-row upserts - well under 300 ms on SQLite.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, select

from retailsense_contracts import db as cdb
from retailsense_contracts.api import IngestAck, IngestBatch
from retailsense_contracts.clock import Clock
from retailsense_contracts.events import Event
from retailsense_contracts.logging import get_logger

from .aggregator import AggResult, Aggregator
from .alerting import AlertDelta, Alerting
from .db import Database
from .fleet import Fleet

log = get_logger("sensecloud.ingest")

MAX_BATCH = 500


class IngestError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class IngestResult:
    ack: IngestAck
    store_id: str
    device_id: str
    new_events: list[Event] = field(default_factory=list)
    alerts: AlertDelta = field(default_factory=AlertDelta)
    agg: AggResult | None = None
    device_row: dict[str, Any] | None = None


class Ingestor:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        fleet: Fleet,
        alerting: Alerting,
        aggregator: Aggregator,
        *,
        dev: bool = False,
    ):
        self.db = db
        self.clock = clock
        self.fleet = fleet
        self.alerting = alerting
        self.aggregator = aggregator
        self.dev = dev

    # ------------------------------------------------------------------ auth
    def authenticate(self, batch: IngestBatch, token: str | None) -> dict[str, Any]:
        """Return the device row, auto-registering in DEV mode. Raises IngestError(401)."""
        row = self.fleet.device_row(batch.device_id)
        if row is None:
            if not self.dev:
                raise IngestError(401, f"unknown device {batch.device_id}")
            self._ensure_store(batch.store_id)
            self.fleet.register_device(batch.device_id, batch.store_id, token or "")
            row = self.fleet.device_row(batch.device_id) or {}
            return row
        if not self.dev and (token is None or token != row.get("token")):
            raise IngestError(401, "bad device token")
        return row

    def _ensure_store(self, store_id: str) -> None:
        if self.db.store_row(store_id) is not None:
            return
        with self.db.tx() as conn:
            self.db.upsert(
                conn,
                cdb.stores,
                {
                    "store_id": store_id,
                    "name": store_id,
                    "tier": "kirana",
                    "lang": "hi",
                    "tz": "Asia/Kolkata",
                    "config": None,
                    "registered_ts": self.clock.now(),
                },
            )

    # --------------------------------------------------------------- process
    def process(self, batch: IngestBatch, token: str | None) -> IngestResult:
        if len(batch.events) > MAX_BATCH:
            raise IngestError(413, f"batch has {len(batch.events)} events; max {MAX_BATCH}")
        device = self.authenticate(batch, token)
        now = self.clock.now()

        rejected: list[dict[str, str]] = []
        rows: list[dict[str, Any]] = []
        good: list[Event] = []
        for ev in batch.events:
            if ev.device_id != batch.device_id or ev.store_id != batch.store_id:
                rejected.append({"event_id": ev.event_id, "reason": "device/store mismatch"})
                continue
            good.append(ev)
            rows.append(ev.model_dump(mode="json"))

        prev_last = int(device.get("last_seq") or 0)
        with self.db.tx() as conn:
            # 1. idempotent insert (the pre-check on the PK tells us *which* rows are new, for fan-out)
            existing = self._existing_ids(conn, [e.event_id for e in good])
            accepted = self.db.insert_ignore(conn, cdb.cloud_events, rows)
            duplicates = len(rows) - accepted
            # 2. seq-gap detection against the table
            max_seq = max((e.seq for e in good), default=None)
            gaps: list[int] = []
            last_seq: int | None = max(prev_last, max_seq or 0) or None
            if max_seq is not None and max_seq > prev_last:
                present = {
                    r["seq"]
                    for r in self.db.rows(
                        conn,
                        select(cdb.cloud_events.c.seq).where(
                            and_(
                                cdb.cloud_events.c.device_id == batch.device_id,
                                cdb.cloud_events.c.seq > prev_last,
                                cdb.cloud_events.c.seq <= max_seq,
                            )
                        ),
                    )
                }
                gaps = [s for s in range(prev_last + 1, max_seq + 1) if s not in present]
            seq_ok = not gaps
            # 3. device row: last_seen / last_seq / heartbeat fields
            dev_row: dict[str, Any] = dict(device)
            dev_row.update(
                device_id=batch.device_id,
                store_id=batch.store_id,
                last_seen_ts=now,
                last_seq=last_seq,
                backlog=batch.backlog,
                status="online",
            )
            hb = next((e for e in reversed(good) if e.type == "device.heartbeat"), None)
            if hb is not None:
                p = hb.payload
                dev_row.update(model_version=p.model_version, fps=p.fps, link=str(p.link), uptime_s=p.uptime_s)
            self.db.upsert(conn, cdb.devices, dev_row)
            # 4. ingest log (batch_id is the PK: a re-sent batch overwrites its own line)
            self.db.upsert(
                conn,
                cdb.ingest_log,
                {
                    "batch_id": batch.batch_id,
                    "device_id": batch.device_id,
                    "received_ts": now,
                    "accepted": accepted,
                    "duplicates": duplicates,
                    "first_seq": min((e.seq for e in good), default=None),
                    "last_seq": max_seq,
                    "seq_ok": 1 if seq_ok else 0,
                },
            )
            # 5. commands for the device, marked delivered in the same transaction
            commands = self.fleet.pending(batch.device_id, mark_delivered=True, conn=conn)

        ack = IngestAck(
            batch_id=batch.batch_id,
            accepted=accepted,
            duplicates=duplicates,
            rejected=rejected,
            last_seq=last_seq,
            seq_ok=seq_ok,
            seq_gaps=gaps,
            commands=commands,
            server_ts=now,
        )
        if gaps:
            log.warning("seq gaps from %s: %s", batch.device_id, gaps[:20])

        result = IngestResult(ack=ack, store_id=batch.store_id, device_id=batch.device_id, device_row=dev_row)
        if accepted:
            result.new_events = [e for e in good if e.event_id not in existing]
            result.alerts = self.alerting.mirror(result.new_events)
            result.agg = self.aggregator.run(batch.store_id)
        result.alerts.extend(self.alerting.device_back(batch.store_id, batch.device_id, now))
        return result

    def _existing_ids(self, conn, ids: list[str]) -> set[str]:
        out: set[str] = set()
        for i in range(0, len(ids), 400):
            chunk = ids[i : i + 400]
            rows = self.db.rows(conn, select(cdb.cloud_events.c.event_id).where(cdb.cloud_events.c.event_id.in_(chunk)))
            out.update(r["event_id"] for r in rows)
        return out


__all__ = ["MAX_BATCH", "IngestError", "IngestResult", "Ingestor"]
