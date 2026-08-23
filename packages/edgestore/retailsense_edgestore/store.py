"""SQLite-backed ``EdgeStore``: the edge device's durable event log + transactional outbox.

Design rationale
----------------
* **One transaction per ``append()``.**  Observations produced by the CV/rules
  pipeline are stamped (``seq``, ``hlc``, ``event_id``) and written to *both*
  ``events`` and ``outbox`` inside a single ``BEGIN IMMEDIATE`` transaction.  A
  power cut can therefore never leave an event without its outbox row (or the
  reverse), and ``seq`` stays gap-free because the ``device_state.seq_next``
  counter is updated in that same transaction.
* **Durability over throughput.**  ``retailsense_contracts.db.sqlite_engine``
  applies ``journal_mode=WAL`` + ``synchronous=FULL`` + ``busy_timeout=5000``;
  this module adds the SQLAlchemy "BEGIN IMMEDIATE" recipe (pysqlite's legacy
  transaction handling is disabled and we emit BEGIN ourselves) so the write
  lock is taken up-front and readers in the API thread never block writers.
* **Single writer.**  The asyncio loop thread is the only writer; readers
  (REST handlers) may use ``engine.connect()`` concurrently thanks to WAL.
* **Store-and-forward policy lives in the contracts.**  ``topics.EXPIRY_S``
  decides ``outbox.expires_ts`` per event class; ``topics.EVICTABLE`` decides
  what ``evict_overflow`` may drop.  ALERT/TXN rows never expire nor evict
  ("RejectNewData" semantics: they are the money-relevant rows).
* **Views are tables, not caches.**  ``alerts``/``shelf_state``/``queue_state``/
  ``heatmap_cells`` are upserted by the pipeline and read by the API, so the
  edge dashboard survives a process restart.  ``kpi_today`` is *computed* from
  those tables (never stored) and memoised for ``kpi_cache_s`` seconds because
  the board polls it every 5 s from several tabs.

The behaviour mirrors ``retailsense_contracts.testing.InMemoryEdgeStore`` (the
reference semantics) so the edge app can swap between them freely.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import and_, delete, event as sa_event, func, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine

from retailsense_contracts.alerts import Alert
from retailsense_contracts.api import HeatCell, HeatmapResponse, KpiDaily, KpiToday, ShelfStateView
from retailsense_contracts.clock import day_start_ts, store_date
from retailsense_contracts.config import ShelfReference, StoreConfig
from retailsense_contracts.db import (
    alerts as t_alerts,
    create_all,
    device_state as t_state,
    events as t_events,
    heatmap_cells as t_heat,
    kpi_daily as t_kpi,
    outbox as t_outbox,
    queue_state as t_queue,
    shelf_state as t_shelf,
    sqlite_engine,
)
from retailsense_contracts.enums import AlertStatus, Direction, EventClass, LineKind, ShelfState
from retailsense_contracts.events import Event, HeatmapTiles, Observation, QueueForecast, QueueSnapshot, make_event
from retailsense_contracts.hlc import HLC
from retailsense_contracts.ids import new_ulid
from retailsense_contracts.privacy import RetentionPolicy
from retailsense_contracts.topics import EVICTABLE, expires_ts

# device_state keys (spec C.10: seq_next, hlc_last, link_state, replay stats, config_version)
KEY_SEQ_NEXT = "seq_next"
KEY_HLC_LAST = "hlc_last"

_EVENT_COLUMNS = (
    "event_id",
    "store_id",
    "device_id",
    "camera_id",
    "ts",
    "hlc",
    "seq",
    "type",
    "cls",
    "version",
    "payload",
    "created_ts",
)


def _install_begin_immediate(engine: Engine) -> None:
    """SQLAlchemy's documented recipe for real SQLite transactions.

    pysqlite only emits ``BEGIN`` lazily before the first DML and never for
    ``SELECT``; disabling that (``isolation_level=None``) and emitting
    ``BEGIN IMMEDIATE`` ourselves acquires the RESERVED lock at transaction
    start, so a writer can't be starved by a concurrent reader upgrading.
    """

    @sa_event.listens_for(engine, "connect")
    def _autocommit(dbapi_conn, _rec) -> None:  # pragma: no cover - driver hook
        dbapi_conn.isolation_level = None

    @sa_event.listens_for(engine, "begin")
    def _begin(conn: Connection) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")


def _row_to_event(row: Any) -> Event:
    data = {k: row._mapping[k] for k in _EVENT_COLUMNS}
    if isinstance(data["payload"], str):  # defensive: JSON stored as text
        data["payload"] = json.loads(data["payload"])
    return Event.model_validate(data)


class EdgeStore:
    """Durable edge store (see module docstring).

    Parameters
    ----------
    cfg:
        Store config; ``cfg.device.db_path`` is the SQLite file (``":memory:"`` allowed
        for tests - note WAL is skipped for in-memory DBs).
    db_path:
        Optional override of ``cfg.device.db_path``.
    clock:
        Wall-clock callable used for ``created_ts``/``enqueued_ts``; injectable for tests.
    """

    kpi_cache_s: float = 2.0

    def __init__(
        self,
        cfg: StoreConfig,
        *,
        db_path: str | Path | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.cfg = cfg
        self.store_id = cfg.store.store_id
        self.device_id = cfg.device.device_id
        self.tz = cfg.store.tz
        self.db_path = str(db_path if db_path is not None else cfg.device.db_path)
        self._clock = clock or time.time
        self._write_lock = threading.RLock()  # belt-and-braces: we *are* single-writer, but cheap to enforce
        self._hlc = HLC(self.device_id, _CallableClock(self._clock))
        self._kpi_cache: tuple[float, KpiToday] | None = None
        self._kpi_cache_key: tuple[str, str] | None = None

        # ":memory:" must share one connection or every connect() sees an empty DB.
        kwargs: dict[str, Any] = {}
        if self.db_path == ":memory:":
            from sqlalchemy.pool import StaticPool

            kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
        self.engine: Engine = sqlite_engine(self.db_path, **kwargs)
        _install_begin_immediate(self.engine)
        create_all(self.engine, "edge")
        self._seq_next = self._recover_seq()

    # ------------------------------------------------------------------ boot
    def _recover_seq(self) -> int:
        """Next seq = max(device_state.seq_next, max(events.seq)+1). Both survive a crash;
        taking the max guards against a partially-applied upgrade or a hand-edited DB."""
        with self.engine.begin() as conn:
            persisted = conn.execute(select(t_state.c.value).where(t_state.c.key == KEY_SEQ_NEXT)).scalar()
            max_seq = conn.execute(
                select(func.max(t_events.c.seq)).where(t_events.c.device_id == self.device_id)
            ).scalar()
            hlc_last = conn.execute(select(t_state.c.value).where(t_state.c.key == KEY_HLC_LAST)).scalar()
        if hlc_last:
            self._hlc.restore(hlc_last)
        return max(int(persisted or 1), int(max_seq or 0) + 1)

    # ---------------------------------------------------------------- append
    def append(self, observations: list[Observation]) -> list[Event]:
        """Stamp + persist observations in ONE ``BEGIN IMMEDIATE`` transaction.

        Returns the stamped ``Event`` objects in input order (seq ascending).
        Empty input is a no-op that still returns ``[]`` without touching the DB.
        """
        if not observations:
            return []
        now = self._clock()
        with self._write_lock, self.engine.begin() as conn:
            # Re-read the persisted counter inside the write lock: authoritative on every call.
            persisted = conn.execute(select(t_state.c.value).where(t_state.c.key == KEY_SEQ_NEXT)).scalar()
            seq = max(self._seq_next, int(persisted or 1))
            events: list[Event] = []
            ev_rows: list[dict[str, Any]] = []
            ob_rows: list[dict[str, Any]] = []
            for obs in observations:
                ev = make_event(
                    obs,
                    store_id=self.store_id,
                    device_id=self.device_id,
                    seq=seq,
                    hlc=self._hlc.now(),
                    created_ts=now,
                    event_id=new_ulid(now),
                )
                seq += 1
                events.append(ev)
                ev_rows.append(
                    {
                        "event_id": ev.event_id,
                        "store_id": ev.store_id,
                        "device_id": ev.device_id,
                        "camera_id": ev.camera_id,
                        "ts": ev.ts,
                        "hlc": ev.hlc,
                        "seq": ev.seq,
                        "type": ev.type,
                        "cls": str(ev.cls),
                        "version": ev.version,
                        "payload": ev.payload.model_dump(mode="json"),
                        "created_ts": ev.created_ts,
                    }
                )
                ob_rows.append(
                    {
                        "event_id": ev.event_id,
                        "cls": str(ev.cls),
                        "enqueued_ts": now,
                        "expires_ts": expires_ts(ev.cls, now),
                        "attempts": 0,
                    }
                )
            conn.execute(insert(t_events), ev_rows)
            # Insert outbox rows one by one so AUTOINCREMENT ids follow event seq order
            # (executemany with a multi-VALUES statement would also preserve order, but
            # SQLite does not *guarantee* it; explicit per-row inserts do).
            for row in ob_rows:
                conn.execute(insert(t_outbox).values(**row))
            self._set_state_in(conn, KEY_SEQ_NEXT, str(seq))
            self._set_state_in(conn, KEY_HLC_LAST, self._hlc.last)
            self._seq_next = seq
        self._kpi_cache = None
        return events

    # ---------------------------------------------------------------- outbox
    def _pending_where(self, now: float | None = None):
        now = self._clock() if now is None else now
        return and_(
            t_outbox.c.sent_ts.is_(None),
            t_outbox.c.evicted_ts.is_(None),
            or_(t_outbox.c.expires_ts.is_(None), t_outbox.c.expires_ts > now),
        )

    def pending(self, limit: int) -> list[tuple[int, Event]]:
        """Unsent, unexpired, unevicted rows ordered by outbox id (== seq order)."""
        limit = max(0, int(limit))
        if limit == 0:
            return []
        stmt = (
            select(t_outbox.c.id, *[t_events.c[c] for c in _EVENT_COLUMNS])
            .select_from(t_outbox.join(t_events, t_outbox.c.event_id == t_events.c.event_id))
            .where(self._pending_where())
            .order_by(t_outbox.c.id)
            .limit(limit)
        )
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [(int(r._mapping["id"]), _row_to_event(r)) for r in rows]

    def mark_sent(self, outbox_ids: list[int], ts: float) -> None:
        if not outbox_ids:
            return
        with self._write_lock, self.engine.begin() as conn:
            conn.execute(
                update(t_outbox)
                .where(t_outbox.c.id.in_(list(outbox_ids)), t_outbox.c.sent_ts.is_(None))
                .values(sent_ts=ts, last_error=None)
            )

    def mark_failed(self, outbox_ids: list[int], error: str) -> None:
        if not outbox_ids:
            return
        with self._write_lock, self.engine.begin() as conn:
            conn.execute(
                update(t_outbox)
                .where(t_outbox.c.id.in_(list(outbox_ids)))
                .values(attempts=t_outbox.c.attempts + 1, last_error=str(error)[:500])
            )

    def backlog(self) -> dict[str, int]:
        """Pending counts for all five classes (sum them for ``SyncStatus.backlog``)."""
        counts = {str(c): 0 for c in EventClass}
        stmt = select(t_outbox.c.cls, func.count()).where(self._pending_where()).group_by(t_outbox.c.cls)
        with self.engine.connect() as conn:
            for cls, n in conn.execute(stmt):
                counts[str(cls)] = int(n)
        return counts

    def evict_overflow(self, max_rows: int) -> int:
        """Keep the pending outbox under ``max_rows`` by evicting the oldest TELEMETRY/AGGREGATE rows.

        ALERT/TXN/CONFIG rows are never touched, so when the backlog is made of
        alerts only, the outbox is allowed to exceed ``max_rows`` (RejectNewData
        semantics apply upstream, not here).
        """
        now = self._clock()
        with self._write_lock, self.engine.begin() as conn:
            total = conn.execute(select(func.count()).where(self._pending_where(now))).scalar() or 0
            excess = int(total) - int(max_rows)
            if excess <= 0:
                return 0
            victims = conn.execute(
                select(t_outbox.c.id)
                .where(self._pending_where(now), t_outbox.c.cls.in_([str(c) for c in EVICTABLE]))
                .order_by(t_outbox.c.id)
                .limit(excess)
            ).scalars().all()
            if not victims:
                return 0
            conn.execute(update(t_outbox).where(t_outbox.c.id.in_(victims)).values(evicted_ts=now))
            return len(victims)

    def expire(self, now_ts: float) -> int:
        """Mark rows whose class TTL has elapsed as evicted. ALERT/TXN have ``expires_ts IS NULL``."""
        with self._write_lock, self.engine.begin() as conn:
            res = conn.execute(
                update(t_outbox)
                .where(
                    t_outbox.c.sent_ts.is_(None),
                    t_outbox.c.evicted_ts.is_(None),
                    t_outbox.c.expires_ts.is_not(None),
                    t_outbox.c.expires_ts <= now_ts,
                )
                .values(evicted_ts=now_ts)
            )
            return int(res.rowcount or 0)

    def outbox_rows(self) -> list[dict[str, Any]]:
        """Raw outbox rows (diagnostics / tests)."""
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(select(t_outbox).order_by(t_outbox.c.id))]

    def event_count(self) -> int:
        with self.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(t_events)).scalar() or 0)

    def events_between(self, from_ts: float, to_ts: float, types: list[str] | None = None) -> list[Event]:
        stmt = select(*[t_events.c[c] for c in _EVENT_COLUMNS]).where(
            t_events.c.ts >= from_ts, t_events.c.ts <= to_ts
        )
        if types:
            stmt = stmt.where(t_events.c.type.in_(types))
        stmt = stmt.order_by(t_events.c.seq)
        with self.engine.connect() as conn:
            return [_row_to_event(r) for r in conn.execute(stmt)]

    # ----------------------------------------------------------------- state
    @staticmethod
    def _set_state_in(conn: Connection, key: str, value: str) -> None:
        from sqlalchemy.dialects.sqlite import insert as sq_insert

        stmt = sq_insert(t_state).values(key=key, value=str(value))
        conn.execute(stmt.on_conflict_do_update(index_elements=[t_state.c.key], set_={"value": str(value)}))

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self.engine.connect() as conn:
            val = conn.execute(select(t_state.c.value).where(t_state.c.key == key)).scalar()
        return default if val is None else str(val)

    def set_state(self, key: str, value: str) -> None:
        with self._write_lock, self.engine.begin() as conn:
            self._set_state_in(conn, key, value)

    # ---------------------------------------------------------------- alerts
    def upsert_alert(self, a: Alert) -> None:
        from sqlalchemy.dialects.sqlite import insert as sq_insert

        row = {
            "alert_id": a.alert_id,
            "store_id": a.store_id,
            "device_id": a.device_id,
            "origin": str(a.origin),
            "kind": str(a.kind),
            "severity": str(a.severity),
            "status": str(a.status),
            "subject_id": a.subject_id,
            "raised_ts": a.raised_ts,
            "acked_ts": a.acked_ts,
            "resolved_ts": a.resolved_ts,
            "ack_action": None if a.ack_action is None else str(a.ack_action),
            "ack_by": None if a.ack_by is None else str(a.ack_by),
            "lost_sales_inr": None if a.impact is None else a.impact.lost_sales_inr,
            "doc": a.model_dump(mode="json"),
        }
        stmt = sq_insert(t_alerts).values(**row)
        with self._write_lock, self.engine.begin() as conn:
            conn.execute(stmt.on_conflict_do_update(index_elements=[t_alerts.c.alert_id], set_=row))
        self._kpi_cache = None

    def set_alert_recovered(self, alert_id: str, recovered_inr: float) -> None:
        """Record the ₹ recovered when an alert resolves (feeds ``kpi_today.recovered_inr``)."""
        with self._write_lock, self.engine.begin() as conn:
            conn.execute(update(t_alerts).where(t_alerts.c.alert_id == alert_id).values(recovered_inr=recovered_inr))
        self._kpi_cache = None

    def alerts(self, status: AlertStatus | None, limit: int = 100) -> list[Alert]:
        stmt = select(t_alerts.c.doc).order_by(t_alerts.c.raised_ts.desc()).limit(int(limit))
        if status is not None:
            stmt = stmt.where(t_alerts.c.status == str(status))
        with self.engine.connect() as conn:
            return [Alert.model_validate(d) for d in conn.execute(stmt).scalars()]

    def alert(self, alert_id: str) -> Alert | None:
        with self.engine.connect() as conn:
            doc = conn.execute(select(t_alerts.c.doc).where(t_alerts.c.alert_id == alert_id)).scalar()
        return None if doc is None else Alert.model_validate(doc)

    # --------------------------------------------------------------- shelves
    def upsert_shelf(self, v: ShelfStateView, reference: ShelfReference | None) -> None:
        from sqlalchemy.dialects.sqlite import insert as sq_insert

        with self._write_lock, self.engine.begin() as conn:
            prev = conn.execute(select(t_shelf).where(t_shelf.c.shelf_id == v.shelf_id)).first()
            prev_m = prev._mapping if prev is not None else {}
            gap_today = float(prev_m.get("gap_minutes_today") or 0.0)
            # Close out a gap: when the shelf leaves EMPTY, bank the finished gap into today's total.
            if prev_m.get("state") == ShelfState.EMPTY and v.state != ShelfState.EMPTY:
                gap_today += _gap_minutes(prev_m.get("gap_started_ts"), prev_m.get("last_scan_ts"))
            ref_json = reference.model_dump(mode="json") if reference is not None else prev_m.get("reference")
            row = {
                "shelf_id": v.shelf_id,
                "sku_id": v.sku_id,
                "state": str(v.state),
                "coverage": v.coverage,
                "facings": v.facings,
                "consecutive_empty_scans": v.consecutive_empty_scans,
                "persistence_required": v.persistence_required,
                "gap_started_ts": v.gap_started_ts,
                "last_scan_ts": v.last_scan_ts,
                "gap_minutes_today": round(gap_today, 3),
                "fp_count": max(0, v.persistence_required - self.cfg.rules.persistence_scans),
                "reference": ref_json,
            }
            stmt = sq_insert(t_shelf).values(**row)
            conn.execute(stmt.on_conflict_do_update(index_elements=[t_shelf.c.shelf_id], set_=row))
        self._kpi_cache = None

    def shelves(self) -> list[dict]:
        """Rows of ``shelf_state`` enriched with config names so they round-trip into
        ``ShelfStateMachine.restore(rows)`` and ``ShelfStateView`` alike."""
        with self.engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(select(t_shelf).order_by(t_shelf.c.shelf_id))]
        out = []
        for r in rows:
            shelf = self.cfg.shelf(r["shelf_id"]) if hasattr(self.cfg, "shelf") else None
            sku = self.cfg.sku(r["sku_id"]) if r.get("sku_id") else None
            gap_min = _gap_minutes(r.get("gap_started_ts"), r.get("last_scan_ts")) if r.get("state") == "empty" else None
            r.update(
                {
                    "name": shelf.name if shelf is not None else r["shelf_id"],
                    "sku_name": (sku.name_en if sku is not None else (r.get("sku_id") or "")),
                    "capacity_facings": shelf.capacity_facings if shelf is not None else 0,
                    "min_facings": shelf.min_facings if shelf is not None else 0,
                    "gap_minutes": gap_min,
                    "occluded": False,
                    "impact_open": None,
                    "has_reference": r.get("reference") is not None,
                }
            )
            out.append(r)
        return out

    # ---------------------------------------------------------------- queues
    def upsert_queue(self, counter_id: str, snap: QueueSnapshot | None, fc: QueueForecast | None) -> None:
        from sqlalchemy.dialects.sqlite import insert as sq_insert

        with self._write_lock, self.engine.begin() as conn:
            prev = conn.execute(select(t_queue).where(t_queue.c.counter_id == counter_id)).first()
            prev_m = prev._mapping if prev is not None else {}
            row = {
                "counter_id": counter_id,
                "snapshot": snap.model_dump(mode="json") if snap is not None else prev_m.get("snapshot"),
                "forecast": fc.model_dump(mode="json") if fc is not None else prev_m.get("forecast"),
                "updated_ts": self._clock(),
            }
            stmt = sq_insert(t_queue).values(**row)
            conn.execute(stmt.on_conflict_do_update(index_elements=[t_queue.c.counter_id], set_=row))
        self._kpi_cache = None

    def queues(self) -> list[dict]:
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(select(t_queue).order_by(t_queue.c.counter_id))]

    # --------------------------------------------------------------- heatmap
    def heat_add(self, camera_id: str, tiles: HeatmapTiles) -> None:
        from sqlalchemy.dialects.sqlite import insert as sq_insert

        if not tiles.tiles:
            return
        with self._write_lock, self.engine.begin() as conn:
            for t in tiles.tiles:
                stmt = sq_insert(t_heat).values(
                    camera_id=camera_id,
                    cell_x=t.cell_x,
                    cell_y=t.cell_y,
                    hour_bucket=t.hour_bucket,
                    dwell_s=t.dwell_s,
                    visits=t.visits,
                )
                conn.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[t_heat.c.camera_id, t_heat.c.cell_x, t_heat.c.cell_y, t_heat.c.hour_bucket],
                        set_={
                            "dwell_s": t_heat.c.dwell_s + stmt.excluded.dwell_s,
                            "visits": t_heat.c.visits + stmt.excluded.visits,
                        },
                    )
                )

    def heat_query(self, camera_id: str | None, from_ts: float, to_ts: float) -> HeatmapResponse:
        lo, hi = int(from_ts // 3600), int(to_ts // 3600)
        stmt = (
            select(t_heat.c.cell_x, t_heat.c.cell_y, func.sum(t_heat.c.dwell_s), func.sum(t_heat.c.visits))
            .where(t_heat.c.hour_bucket >= lo, t_heat.c.hour_bucket <= hi)
            .group_by(t_heat.c.cell_x, t_heat.c.cell_y)
            .order_by(t_heat.c.cell_x, t_heat.c.cell_y)
        )
        if camera_id is not None:
            stmt = stmt.where(t_heat.c.camera_id == camera_id)
        with self.engine.connect() as conn:
            cells = [
                HeatCell(x=int(cx), y=int(cy), dwell_s=round(float(d or 0.0), 3), visits=int(v or 0))
                for cx, cy, d, v in conn.execute(stmt)
            ]
        cell_px = self.cfg.floorplan.heat_cell_px
        return HeatmapResponse(
            camera_id=camera_id,
            cell_px=cell_px,
            width_cells=math.ceil(self.cfg.floorplan.width_px / cell_px),
            height_cells=math.ceil(self.cfg.floorplan.height_px / cell_px),
            from_ts=from_ts,
            to_ts=to_ts,
            cells=cells,
            max_dwell_s=max((c.dwell_s for c in cells), default=0.0),
        )

    # ------------------------------------------------------------------ KPIs
    def kpi_today(self, ts: float) -> KpiToday:
        """Compute today's KPIs from the tables; memoised for ``kpi_cache_s`` wall seconds.

        Sources (spec D8): footfall from ``footfall.crossing`` events today; queue wait and
        abandons from ``queue_state``; OSA and gap minutes from ``shelf_state``; lost ₹
        from alerts raised today; recovered ₹ from ``alert.resolved`` events today.
        """
        date = store_date(ts, self.tz)
        key = (date, f"{ts:.0f}")
        now_wall = time.monotonic()
        if (
            self._kpi_cache is not None
            and self._kpi_cache_key == key
            and now_wall - self._kpi_cache[0] < self.kpi_cache_s
        ):
            return self._kpi_cache[1]
        result = self._compute_kpi_today(ts, date)
        self._kpi_cache = (now_wall, result)
        self._kpi_cache_key = key
        return result

    def _compute_kpi_today(self, ts: float, date: str) -> KpiToday:
        start = day_start_ts(ts, self.tz)
        fin = fout = tx = 0
        rec = 0.0
        with self.engine.connect() as conn:
            ev_stmt = (
                select(t_events.c.type, t_events.c.payload)
                .where(
                    t_events.c.ts >= start,
                    t_events.c.ts <= ts,
                    t_events.c.type.in_(["footfall.crossing", "alert.resolved"]),
                )
                .order_by(t_events.c.seq)
            )
            for etype, payload in conn.execute(ev_stmt):
                p = json.loads(payload) if isinstance(payload, str) else payload
                if etype == "footfall.crossing":
                    n = int(p.get("count", 1))
                    if p.get("line_kind") == LineKind.ENTRANCE:
                        if p.get("direction") == Direction.IN:
                            fin += n
                        else:
                            fout += n
                    elif p.get("line_kind") == LineKind.COUNTER and p.get("direction") == Direction.IN:
                        tx += n
                else:
                    r = p.get("recovered")
                    if r:
                        rec += float(r.get("lost_sales_inr", 0.0))
            queues = [dict(r._mapping) for r in conn.execute(select(t_queue))]
            shelves = [dict(r._mapping) for r in conn.execute(select(t_shelf))]
            alerts_open = int(
                conn.execute(select(func.count()).where(t_alerts.c.status != str(AlertStatus.RESOLVED))).scalar() or 0
            )
            today_alerts = conn.execute(
                select(t_alerts.c.lost_sales_inr, t_alerts.c.doc).where(
                    t_alerts.c.raised_ts >= start, t_alerts.c.raised_ts <= ts
                )
            ).all()
        waits: list[float] = []
        abandoned = 0
        for q in queues:
            s = q.get("snapshot")
            if s:
                waits.append(float(s["est_wait_s"]))
                abandoned = max(abandoned, int(s.get("abandoned_total", 0)))
        gap_total = 0.0
        empty = 0
        for sh in shelves:
            gap_total += float(sh.get("gap_minutes_today") or 0.0)
            if sh.get("state") == ShelfState.EMPTY:
                empty += 1
                gap_total += _gap_minutes(sh.get("gap_started_ts"), sh.get("last_scan_ts"))
        osa = 100.0 if not shelves else round(100.0 * (1 - empty / len(shelves)), 2)
        lost = margin = 0.0
        for lost_col, doc in today_alerts:
            imp = (doc or {}).get("impact")
            if imp:
                lost += float(imp.get("lost_sales_inr", lost_col or 0.0))
                margin += float(imp.get("lost_margin_inr", 0.0))
        today = KpiToday(
            store_id=self.store_id,
            date=date,
            as_of_ts=ts,
            footfall_in=fin,
            footfall_out=fout,
            occupancy_now=max(0, fin - fout),
            visual_transactions=tx,
            conversion_pct=round(100.0 * tx / fin, 2) if fin else None,
            atv_inr=self.cfg.impact.atv_inr,
            osa_pct=osa,
            gap_minutes_total=round(gap_total, 2),
            avg_wait_s=round(sum(waits) / len(waits), 1) if waits else None,
            max_wait_s=max(waits) if waits else None,
            abandoned=abandoned,
            lost_sales_inr=round(lost, 2),
            lost_margin_inr=round(margin, 2),
            recovered_inr=round(rec, 2),
            alerts_open=alerts_open,
            alerts_today=len(today_alerts),
        )
        yesterday = (_dt.date.fromisoformat(date) - _dt.timedelta(days=1)).isoformat()
        prev = self.kpi_daily(yesterday)
        if prev is not None:
            from .kpi import deltas_vs_yesterday

            today.deltas = deltas_vs_yesterday(today, prev)
        return today

    def upsert_kpi_daily(self, row: KpiDaily, *, lost_margin_inr: float | None = None) -> None:
        from sqlalchemy.dialects.sqlite import insert as sq_insert

        values = row.model_dump(mode="json")
        values["lost_margin_inr"] = lost_margin_inr
        values["updated_ts"] = self._clock()
        stmt = sq_insert(t_kpi).values(**values)
        with self._write_lock, self.engine.begin() as conn:
            conn.execute(stmt.on_conflict_do_update(index_elements=[t_kpi.c.store_id, t_kpi.c.date], set_=values))
        self._kpi_cache = None

    def kpi_daily(self, date: str) -> KpiDaily | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(t_kpi).where(t_kpi.c.store_id == self.store_id, t_kpi.c.date == date)).first()
        if r is None:
            return None
        m = dict(r._mapping)
        return KpiDaily.model_validate({k: m[k] for k in KpiDaily.model_fields if k in m})

    def kpi_daily_range(self, from_date: str, to_date: str) -> list[KpiDaily]:
        stmt = (
            select(t_kpi)
            .where(t_kpi.c.store_id == self.store_id, t_kpi.c.date >= from_date, t_kpi.c.date <= to_date)
            .order_by(t_kpi.c.date)
        )
        with self.engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(stmt)]
        return [KpiDaily.model_validate({k: m[k] for k in KpiDaily.model_fields if k in m}) for m in rows]

    # ------------------------------------------------------------- retention
    def purge(self, policy: RetentionPolicy, now_ts: float) -> dict[str, int]:
        """Apply the ``RetentionPolicy`` (DPDP storage limitation). Returns per-bucket counts.

        Events still referenced by a *pending* outbox row are never deleted: they
        have not reached the cloud yet.  Sent/evicted outbox rows older than
        ``sent_outbox_hours`` go first so the FK from outbox -> events is satisfied.
        """
        counts = {
            "telemetry_events": 0,
            "aggregate_events": 0,
            "thumbnails": 0,
            "sent_outbox": 0,
            "heatmap_cells": 0,
            "alerts": 0,
        }
        tel_cut = now_ts - policy.telemetry_hours * 3600
        agg_cut = now_ts - policy.aggregate_days * 86400
        thumb_cut = now_ts - policy.thumbnails_days * 86400
        sent_cut = now_ts - policy.sent_outbox_hours * 3600
        heat_cut = int((now_ts - policy.heatmap_days * 86400) // 3600)
        alert_cut = now_ts - policy.alerts_days * 86400
        with self._write_lock, self.engine.begin() as conn:
            # 1. old sent / evicted outbox rows
            res = conn.execute(
                delete(t_outbox).where(
                    or_(
                        and_(t_outbox.c.sent_ts.is_not(None), t_outbox.c.sent_ts < sent_cut),
                        and_(t_outbox.c.evicted_ts.is_not(None), t_outbox.c.evicted_ts < sent_cut),
                    )
                )
            )
            counts["sent_outbox"] = int(res.rowcount or 0)
            # 2. telemetry / aggregate events past their window (unless still pending)
            pending_ids = select(t_outbox.c.event_id).where(self._pending_where(now_ts))
            for bucket, cls, cut in (
                ("telemetry_events", EventClass.TELEMETRY, tel_cut),
                ("aggregate_events", EventClass.AGGREGATE, agg_cut),
            ):
                victims = conn.execute(
                    select(t_events.c.event_id).where(
                        t_events.c.cls == str(cls), t_events.c.ts < cut, t_events.c.event_id.not_in(pending_ids)
                    )
                ).scalars().all()
                if victims:
                    conn.execute(delete(t_outbox).where(t_outbox.c.event_id.in_(victims)))
                    conn.execute(delete(t_events).where(t_events.c.event_id.in_(victims)))
                counts[bucket] = len(victims)
            # 3. null out shelf thumbnails older than thumbnails_days (privacy: no imagery lingers)
            rows = conn.execute(
                select(t_events.c.event_id, t_events.c.payload).where(
                    t_events.c.type == "shelf.scan", t_events.c.ts < thumb_cut
                )
            ).all()
            for eid, payload in rows:
                p = json.loads(payload) if isinstance(payload, str) else dict(payload)
                if p.get("thumb_b64") is None:
                    continue
                p["thumb_b64"] = None
                conn.execute(update(t_events).where(t_events.c.event_id == eid).values(payload=p))
                counts["thumbnails"] += 1
            # 4. heatmap cells
            res = conn.execute(delete(t_heat).where(t_heat.c.hour_bucket < heat_cut))
            counts["heatmap_cells"] = int(res.rowcount or 0)
            # 5. resolved alerts older than alerts_days
            res = conn.execute(
                delete(t_alerts).where(t_alerts.c.status == str(AlertStatus.RESOLVED), t_alerts.c.raised_ts < alert_cut)
            )
            counts["alerts"] = int(res.rowcount or 0)
        self._kpi_cache = None
        return counts

    # ------------------------------------------------------------------ misc
    def close(self) -> None:
        self.engine.dispose()

    def __enter__(self) -> EdgeStore:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class _CallableClock:
    """Adapts a ``Callable[[], float]`` to the contracts ``Clock`` protocol for the HLC."""

    def __init__(self, fn: Callable[[], float]):
        self._fn = fn

    def now(self) -> float:
        return self._fn()


def _gap_minutes(gap_started_ts: float | None, last_scan_ts: float | None) -> float:
    if gap_started_ts is None or last_scan_ts is None:
        return 0.0
    return max(0.0, (float(last_scan_ts) - float(gap_started_ts)) / 60.0)


__all__ = ["KEY_HLC_LAST", "KEY_SEQ_NEXT", "EdgeStore"]
