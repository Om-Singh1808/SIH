"""Incremental aggregation: raw events -> series_5m, kpi_daily and the view tables.

Why incremental *and* recomputed
--------------------------------
Events arrive out of order (a device replays its outbox after an internet cut), so
two strategies are combined:

* ``series_5m`` and the view tables (``shelf_state``, ``queue_state``,
  ``heatmap_cells``) are updated **incrementally** from a per-store cursor
  (``agg_cursor.last_event_seq`` = ``{device_id: seq}``).  Every event is folded
  exactly once, whatever order it arrived in.
* ``kpi_daily`` rows are **recomputed** for every store-day a new event touched.
  A day has at most a few thousand events, the rollup is a single pass, and
  recomputing is the only way to get time-weighted OSA and open-gap losses right.

KPI definitions (normative for the cloud, mirroring D10)
--------------------------------------------------------
* ``footfall_in/out``   = entrance-line crossings by direction
* ``visual_transactions`` = counter-line IN crossings; ``conversion_pct`` = tx / footfall_in
* ``osa_pct``           = 100 x (1 - shelf-empty-minutes / (n_shelves x elapsed open minutes))
* ``gap_minutes_total`` = closed gaps (``gap_minutes``) + still-open gaps as of ``as_of``
* ``lost_sales_inr``    = the edge's ``ShelfStateChange.impact`` when present, otherwise
  ``retailsense_contracts.impact.lost_sales`` (same formula, same citation)
* ``recovered_inr``     = sum of ``AlertResolved.recovered``
* ``abandoned``         = max ``QueueSnapshot.abandoned_total`` per counter (cumulative on the edge)
* ``atv_inr``           = Tally ``sales_today`` when the store has Tally enabled (ErpClient via registry)
* ``shrink_inr``        = ``stock.reconciled`` rows where the system shows more than the shelf

Series metrics are either *gauges* (mean within the 5-minute bucket, ``n`` samples)
or *counters* (sum): queue_count, est_wait_s, occupancy, osa_pct are gauges;
footfall_in, footfall_out, gap_minutes, lost_sales_inr are counters.
"""

import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, func, select

from retailsense_contracts import db as cdb
from retailsense_contracts.alerts import ImpactInr
from retailsense_contracts.api import (
    HeatCell,
    HeatmapResponse,
    KpiDaily,
    KpiToday,
    QueueView,
    Series,
    SeriesPoint,
    ShelfStateView,
)
from retailsense_contracts.clock import Clock, date_to_ts, day_start_ts, store_date
from retailsense_contracts.config import StoreConfig
from retailsense_contracts.enums import AlertStatus, Direction, LineKind, ShelfState, ZoneKind
from retailsense_contracts.events import Event, QueueForecast, QueueSnapshot
from retailsense_contracts.impact import lost_sales
from retailsense_contracts.logging import get_logger
from retailsense_contracts.registry import resolve

from .db import Database

log = get_logger("sensecloud.aggregator")

BUCKET_S = 300
GAUGES = frozenset({"queue_count", "est_wait_s", "occupancy", "osa_pct"})
COUNTERS = frozenset({"footfall_in", "footfall_out", "gap_minutes", "lost_sales_inr"})
SERIES_METRICS = tuple(sorted(GAUGES | COUNTERS))
DEFAULT_TZ = "Asia/Kolkata"


def bucket_of(ts: float) -> float:
    return float(int(ts // BUCKET_S) * BUCKET_S)


@dataclass
class AggResult:
    store_id: str
    events: int = 0
    touched_dates: set[str] = field(default_factory=set)
    kpi: dict[str, KpiDaily] = field(default_factory=dict)


@dataclass
class _Gap:
    started_ts: float
    sku_id: str | None


class Aggregator:
    def __init__(self, db: Database, clock: Clock):
        self.db = db
        self.clock = clock
        self._cfg_cache: dict[str, StoreConfig | None] = {}
        self._erp: Any = None
        self._atv_cache: dict[str, tuple[float, float | None]] = {}

    # ------------------------------------------------------------------ config
    def config(self, store_id: str) -> StoreConfig | None:
        if store_id not in self._cfg_cache:
            self._cfg_cache[store_id] = self.db.store_config(store_id)
        return self._cfg_cache[store_id]

    def invalidate(self, store_id: str | None = None) -> None:
        if store_id is None:
            self._cfg_cache.clear()
        else:
            self._cfg_cache.pop(store_id, None)

    def tz(self, store_id: str) -> str:
        cfg = self.config(store_id)
        return cfg.store.tz if cfg else DEFAULT_TZ

    # ------------------------------------------------------------- incremental
    def run(self, store_id: str, *, limit: int = 20000) -> AggResult:
        """Fold every event newer than the cursor into series/views; recompute touched kpi_daily rows."""
        result = AggResult(store_id=store_id)
        tz = self.tz(store_id)
        with self.db.tx() as conn:
            cur_row = self.db.one(conn, select(cdb.agg_cursor).where(cdb.agg_cursor.c.store_id == store_id))
            cursor: dict[str, int] = dict(cur_row["last_event_seq"] or {}) if cur_row else {}
            devices = [
                r["device_id"]
                for r in self.db.rows(
                    conn,
                    select(cdb.cloud_events.c.device_id).where(cdb.cloud_events.c.store_id == store_id).distinct(),
                )
            ]
            rows: list[dict[str, Any]] = []
            for dev in devices:
                stmt = (
                    select(cdb.cloud_events)
                    .where(and_(cdb.cloud_events.c.device_id == dev, cdb.cloud_events.c.seq > int(cursor.get(dev, 0))))
                    .order_by(cdb.cloud_events.c.seq)
                    .limit(limit)
                )
                rows.extend(self.db.rows(conn, stmt))
            if not rows:
                return result
            rows.sort(key=lambda r: (r["ts"], r["seq"]))
            events = [Event.model_validate(r) for r in rows]

            series: dict[tuple[str, float], list[float]] = defaultdict(lambda: [0.0, 0.0])  # (sum, n)
            shelf_states = self._load_shelf_states(conn, store_id)
            cfg = self.config(store_id)
            persistence = cfg.rules.persistence_scans if cfg else 3
            heat_delta: dict[tuple[str, int, int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])

            for ev in events:
                result.events += 1
                result.touched_dates.add(store_date(ev.ts, tz))
                b = bucket_of(ev.ts)
                p = ev.payload
                t = ev.type
                if t == "footfall.crossing" and p.line_kind == LineKind.ENTRANCE:
                    key = "footfall_in" if p.direction == Direction.IN else "footfall_out"
                    series[(key, b)][0] += p.count
                    series[(key, b)][1] += 1
                elif t == "queue.snapshot":
                    self._gauge(series, "queue_count", b, p.count)
                    self._gauge(series, "est_wait_s", b, p.est_wait_s)
                    self._upsert_queue(conn, store_id, p.counter_id, snapshot=p, ts=ev.ts)
                elif t == "queue.forecast":
                    self._upsert_queue(conn, store_id, p.counter_id, forecast=p, ts=ev.ts)
                elif t == "zone.occupancy" and p.zone_kind == ZoneKind.STORE:
                    self._gauge(series, "occupancy", b, p.count)
                elif t == "shelf.scan":
                    st = shelf_states.setdefault(p.shelf_id, {"store_id": store_id, "shelf_id": p.shelf_id})
                    st.update(
                        sku_id=p.sku_id or st.get("sku_id"),
                        coverage=p.coverage,
                        facings=p.facings,
                        last_scan_ts=ev.ts,
                        persistence_required=st.get("persistence_required") or persistence,
                    )
                    st.setdefault("state", str(p.state_raw))
                elif t == "shelf.state":
                    st = shelf_states.setdefault(p.shelf_id, {"store_id": store_id, "shelf_id": p.shelf_id})
                    st.update(
                        sku_id=p.sku_id or st.get("sku_id"),
                        state=str(p.to_state),
                        consecutive_empty_scans=p.consecutive_empty_scans,
                        persistence_required=st.get("persistence_required") or persistence,
                    )
                    if p.to_state == ShelfState.EMPTY:
                        st["gap_started_ts"] = p.gap_started_ts or ev.ts
                    else:
                        st["gap_started_ts"] = None
                        if p.from_state == ShelfState.EMPTY:
                            minutes = p.gap_minutes if p.gap_minutes is not None else 0.0
                            st["gap_minutes_today"] = float(st.get("gap_minutes_today") or 0.0) + minutes
                            series[("gap_minutes", b)][0] += minutes
                            series[("gap_minutes", b)][1] += 1
                            impact = p.impact or self._impact_for(cfg, p.sku_id, minutes)
                            if impact:
                                series[("lost_sales_inr", b)][0] += impact.lost_sales_inr
                                series[("lost_sales_inr", b)][1] += 1
                    n_sh = len(shelf_states)
                    empty = sum(1 for s in shelf_states.values() if s.get("state") == "empty")
                    self._gauge(series, "osa_pct", b, 100.0 * (1 - empty / n_sh) if n_sh else 100.0)
                elif t == "heatmap.tiles":
                    cam = ev.camera_id or "floor"
                    for tile in p.tiles:
                        cell = heat_delta[(cam, tile.cell_x, tile.cell_y, tile.hour_bucket)]
                        cell[0] += tile.dwell_s
                        cell[1] += tile.visits
                cursor[ev.device_id] = max(int(cursor.get(ev.device_id, 0)), ev.seq)

            self._flush_series(conn, store_id, series)
            for st in shelf_states.values():
                self.db.upsert(conn, cdb.cloud_shelf_state, st)
            self._flush_heat(conn, store_id, heat_delta)
            self.db.upsert(
                conn,
                cdb.agg_cursor,
                {"store_id": store_id, "last_event_seq": cursor, "updated_ts": self.clock.now()},
            )
        for date in sorted(result.touched_dates):
            kpi = self.compute_daily(store_id, date, as_of=self.clock.now())
            self.upsert_kpi_daily(kpi)
            result.kpi[date] = kpi
        return result

    @staticmethod
    def _gauge(series: dict[tuple[str, float], list[float]], metric: str, b: float, value: float) -> None:
        series[(metric, b)][0] += float(value)
        series[(metric, b)][1] += 1

    def _impact_for(self, cfg: StoreConfig | None, sku_id: str | None, minutes: float | None) -> ImpactInr | None:
        if cfg is None or sku_id is None or not minutes or minutes <= 0:
            return None
        sku = cfg.sku(sku_id)
        return lost_sales(sku, minutes, cfg.impact) if sku else None

    def _load_shelf_states(self, conn, store_id: str) -> dict[str, dict[str, Any]]:
        rows = self.db.rows(conn, select(cdb.cloud_shelf_state).where(cdb.cloud_shelf_state.c.store_id == store_id))
        return {r["shelf_id"]: r for r in rows}

    def _upsert_queue(
        self,
        conn,
        store_id: str,
        counter_id: str,
        *,
        snapshot: QueueSnapshot | None = None,
        forecast: QueueForecast | None = None,
        ts: float,
    ) -> None:
        existing = self.db.one(
            conn,
            select(cdb.cloud_queue_state).where(
                and_(cdb.cloud_queue_state.c.store_id == store_id, cdb.cloud_queue_state.c.counter_id == counter_id)
            ),
        )
        row = {
            "store_id": store_id,
            "counter_id": counter_id,
            "snapshot": snapshot.model_dump(mode="json") if snapshot else (existing or {}).get("snapshot"),
            "forecast": forecast.model_dump(mode="json") if forecast else (existing or {}).get("forecast"),
            "updated_ts": ts,
        }
        self.db.upsert(conn, cdb.cloud_queue_state, row)

    def _flush_series(self, conn, store_id: str, series: dict[tuple[str, float], list[float]]) -> None:
        for (metric, b), (total, n) in series.items():
            existing = self.db.one(
                conn,
                select(cdb.series_5m).where(
                    and_(
                        cdb.series_5m.c.store_id == store_id,
                        cdb.series_5m.c.metric == metric,
                        cdb.series_5m.c.bucket_ts == b,
                    )
                ),
            )
            old_v = float(existing["value"] or 0.0) if existing else 0.0
            old_n = int(existing["n"] or 0) if existing else 0
            if metric in GAUGES:
                value = (old_v * old_n + total) / (old_n + n) if (old_n + n) else 0.0
            else:
                value = old_v + total
            self.db.upsert(
                conn,
                cdb.series_5m,
                {"store_id": store_id, "metric": metric, "bucket_ts": b, "value": round(value, 4), "n": old_n + int(n)},
            )

    def _flush_heat(self, conn, store_id: str, heat: dict[tuple[str, int, int, int], list[float]]) -> None:
        t = cdb.cloud_heatmap_cells
        for (cam, cx, cy, hb), (dwell, visits) in heat.items():
            existing = self.db.one(
                conn,
                select(t).where(
                    and_(
                        t.c.store_id == store_id,
                        t.c.camera_id == cam,
                        t.c.cell_x == cx,
                        t.c.cell_y == cy,
                        t.c.hour_bucket == hb,
                    )
                ),
            )
            self.db.upsert(
                conn,
                t,
                {
                    "store_id": store_id,
                    "camera_id": cam,
                    "cell_x": cx,
                    "cell_y": cy,
                    "hour_bucket": hb,
                    "dwell_s": float((existing or {}).get("dwell_s") or 0.0) + dwell,
                    "visits": int((existing or {}).get("visits") or 0) + int(visits),
                },
            )

    # ----------------------------------------------------------------- daily
    def day_events(self, store_id: str, date: str, as_of: float | None = None) -> list[Event]:
        tz = self.tz(store_id)
        start = date_to_ts(date, tz)
        end = date_to_ts((_dt.date.fromisoformat(date) + _dt.timedelta(days=1)).isoformat(), tz)
        if as_of is not None:
            end = min(end, as_of)
        e = cdb.cloud_events
        with self.db.read() as conn:
            rows = self.db.rows(
                conn,
                select(e)
                .where(and_(e.c.store_id == store_id, e.c.ts >= start, e.c.ts <= end))
                .order_by(e.c.ts, e.c.seq),
            )
        return [Event.model_validate(r) for r in rows]

    def compute_daily(self, store_id: str, date: str, *, as_of: float | None = None) -> KpiDaily:
        """One pass over the day's events. Pure given the events (and the store config)."""
        cfg = self.config(store_id)
        tz = cfg.store.tz if cfg else DEFAULT_TZ
        events = self.day_events(store_id, date, as_of)
        day_start = date_to_ts(date, tz)
        day_end = day_start + 86400
        now = min(as_of if as_of is not None else self.clock.now(), day_end)
        fin = fout = tx = 0
        open_gaps: dict[str, _Gap] = {}
        gap_minutes = 0.0
        lost = margin = recovered = 0.0
        abandoned: dict[str, int] = {}
        waits: list[float] = []
        shrink = 0.0
        alerts_total = 0
        shelf_ids: set[str] = {s.shelf_id for s in cfg.shelves} if cfg else set()
        for ev in events:
            p = ev.payload
            t = ev.type
            if t == "footfall.crossing":
                if p.line_kind == LineKind.ENTRANCE:
                    if p.direction == Direction.IN:
                        fin += p.count
                    else:
                        fout += p.count
                elif p.line_kind == LineKind.COUNTER and p.direction == Direction.IN:
                    tx += p.count
            elif t == "shelf.state":
                shelf_ids.add(p.shelf_id)
                if p.to_state == ShelfState.EMPTY:
                    open_gaps[p.shelf_id] = _Gap(started_ts=p.gap_started_ts or ev.ts, sku_id=p.sku_id)
                elif p.from_state == ShelfState.EMPTY:
                    g = open_gaps.pop(p.shelf_id, None)
                    if p.gap_minutes is not None:
                        minutes = p.gap_minutes
                    else:
                        minutes = max(0.0, ev.ts - g.started_ts) / 60 if g else 0.0
                    gap_minutes += minutes
                    impact = p.impact or self._impact_for(cfg, p.sku_id or (g.sku_id if g else None), minutes)
                    if impact:
                        lost += impact.lost_sales_inr
                        margin += impact.lost_margin_inr
            elif t == "alert.resolved":
                if p.recovered is not None:
                    recovered += p.recovered.lost_sales_inr
            elif t == "alert.raised":
                alerts_total += 1
            elif t == "queue.snapshot":
                waits.append(float(p.est_wait_s))
                abandoned[p.counter_id] = max(abandoned.get(p.counter_id, 0), int(p.abandoned_total))
            elif t == "stock.reconciled":
                if p.system_units > p.visual_units:
                    shrink += abs(p.delta_inr)
        # gaps still open at as_of
        for g in open_gaps.values():
            minutes = max(0.0, now - g.started_ts) / 60
            gap_minutes += minutes
            impact = self._impact_for(cfg, g.sku_id, minutes)
            if impact:
                lost += impact.lost_sales_inr
                margin += impact.lost_margin_inr
        osa = self._osa(cfg, date, tz, now, len(shelf_ids), gap_minutes)
        conversion = round(100.0 * tx / fin, 2) if fin else None
        return KpiDaily(
            store_id=store_id,
            date=date,
            footfall_in=fin,
            footfall_out=fout,
            visual_transactions=tx,
            conversion_pct=conversion,
            atv_inr=self._atv(store_id, cfg),
            osa_pct=osa,
            gap_minutes_total=round(gap_minutes, 2),
            avg_wait_s=round(sum(waits) / len(waits), 1) if waits else None,
            max_wait_s=round(max(waits), 1) if waits else None,
            abandoned=sum(abandoned.values()),
            lost_sales_inr=round(lost, 2),
            recovered_inr=round(recovered, 2),
            shrink_inr=round(shrink, 2),
            alerts_total=alerts_total,
        )

    @staticmethod
    def _osa(cfg: StoreConfig | None, date: str, tz: str, now: float, n_shelves: int, gap_minutes: float) -> float:
        """Time-weighted on-shelf availability over the elapsed part of the opening hours."""
        if n_shelves == 0:
            return 100.0
        open_from = cfg.store.open_hours[0] if cfg else "08:00"
        open_to = cfg.store.open_hours[1] if cfg else "22:00"
        start = date_to_ts(date, tz, open_from)
        end = min(now, date_to_ts(date, tz, open_to))
        elapsed_min = max(1.0, (end - start) / 60, gap_minutes / n_shelves)
        return round(max(0.0, min(100.0, 100.0 * (1 - gap_minutes / (n_shelves * elapsed_min)))), 2)

    def _atv(self, store_id: str, cfg: StoreConfig | None) -> float | None:
        """Average transaction value from the ERP (Tally) when the store has it enabled; cached 60 s."""
        if cfg is None or not cfg.integrations.tally.enabled:
            return None
        now = self.clock.now()
        cached = self._atv_cache.get(store_id)
        if cached and now - cached[0] < 60:
            return cached[1]
        atv: float | None = None
        try:
            if self._erp is None:
                self._erp = resolve("erp.tally")()
            sales = self._erp.sales_today()
            txn = float(sales.get("transactions") or 0)
            atv = round(float(sales.get("sales_inr", 0.0)) / txn, 2) if txn > 0 else None
        except Exception as exc:  # ERP unreachable -> unknown, never fail the KPI
            log.warning("atv lookup failed: %s", exc)
        self._atv_cache[store_id] = (now, atv)
        return atv

    def upsert_kpi_daily(self, kpi: KpiDaily) -> None:
        row = kpi.model_dump()
        row["updated_ts"] = self.clock.now()
        with self.db.tx() as conn:
            self.db.upsert(conn, cdb.cloud_kpi_daily, row)

    def kpi_daily(self, store_id: str, date: str) -> KpiDaily | None:
        with self.db.read() as conn:
            row = self.db.one(
                conn,
                select(cdb.cloud_kpi_daily).where(
                    and_(cdb.cloud_kpi_daily.c.store_id == store_id, cdb.cloud_kpi_daily.c.date == date)
                ),
            )
        return self._kpi_from_row(row) if row else None

    @staticmethod
    def _kpi_from_row(row: dict[str, Any]) -> KpiDaily:
        fields = set(KpiDaily.model_fields)
        data = {k: v for k, v in row.items() if k in fields}
        for k in ("footfall_in", "footfall_out", "visual_transactions", "abandoned", "alerts_total"):
            data[k] = int(data.get(k) or 0)
        for k in ("osa_pct", "gap_minutes_total", "lost_sales_inr", "recovered_inr", "shrink_inr"):
            data[k] = float(data.get(k) or 0.0)
        return KpiDaily.model_validate(data)

    def kpi_range(self, store_id: str, days: int, as_of: float) -> list[KpiDaily]:
        tz = self.tz(store_id)
        today = _dt.date.fromisoformat(store_date(as_of, tz))
        first = (today - _dt.timedelta(days=days - 1)).isoformat()
        with self.db.read() as conn:
            rows = self.db.rows(
                conn,
                select(cdb.cloud_kpi_daily)
                .where(and_(cdb.cloud_kpi_daily.c.store_id == store_id, cdb.cloud_kpi_daily.c.date >= first))
                .order_by(cdb.cloud_kpi_daily.c.date),
            )
        out = [self._kpi_from_row(r) for r in rows]
        if out and out[-1].date == today.isoformat():
            out[-1] = self._daily_for_today(store_id, today.isoformat(), as_of) or out[-1]
        return out

    def _daily_for_today(self, store_id: str, date: str, as_of: float) -> KpiDaily | None:
        """Live rollup when the day has events; otherwise the stored (seeded) row."""
        if self._has_events(store_id, date, as_of):
            return self.compute_daily(store_id, date, as_of=as_of)
        return self.kpi_daily(store_id, date)

    def _has_events(self, store_id: str, date: str, as_of: float) -> bool:
        tz = self.tz(store_id)
        start = date_to_ts(date, tz)
        e = cdb.cloud_events
        with self.db.read() as conn:
            n = conn.execute(
                select(func.count())
                .select_from(e)
                .where(and_(e.c.store_id == store_id, e.c.ts >= start, e.c.ts <= as_of))
            ).scalar()
        return bool(n)

    # ----------------------------------------------------------------- today
    def kpi_today(self, store_id: str, ts: float | None = None) -> KpiToday:
        ts = self.clock.now() if ts is None else ts
        cfg = self.config(store_id)
        tz = cfg.store.tz if cfg else DEFAULT_TZ
        date = store_date(ts, tz)
        daily = self._daily_for_today(store_id, date, ts)
        if daily is None:
            daily = self.compute_daily(store_id, date, as_of=ts)
        margin = round(daily.lost_sales_inr * self._margin_share(cfg), 2)
        day_start = day_start_ts(ts, tz)
        a = cdb.cloud_alerts
        e = cdb.cloud_events
        with self.db.read() as conn:
            alerts_open = conn.execute(
                select(func.count())
                .select_from(a)
                .where(and_(a.c.store_id == store_id, a.c.status != str(AlertStatus.RESOLVED)))
            ).scalar()
            alerts_today = conn.execute(
                select(func.count())
                .select_from(a)
                .where(and_(a.c.store_id == store_id, a.c.raised_ts >= day_start, a.c.raised_ts <= ts))
            ).scalar()
            occ_row = self.db.one(
                conn,
                select(e.c.payload)
                .where(
                    and_(
                        e.c.store_id == store_id,
                        e.c.type == "zone.occupancy",
                        e.c.ts >= day_start,
                        e.c.ts <= ts,
                    )
                )
                .order_by(e.c.ts.desc()),
            )
        if occ_row:
            occupancy = int(occ_row["payload"].get("count", 0))
        else:
            occupancy = max(0, daily.footfall_in - daily.footfall_out)
        today = KpiToday(
            store_id=store_id,
            date=date,
            as_of_ts=ts,
            footfall_in=daily.footfall_in,
            footfall_out=daily.footfall_out,
            occupancy_now=occupancy,
            visual_transactions=daily.visual_transactions,
            conversion_pct=daily.conversion_pct,
            atv_inr=daily.atv_inr,
            osa_pct=daily.osa_pct,
            gap_minutes_total=daily.gap_minutes_total,
            avg_wait_s=daily.avg_wait_s,
            max_wait_s=daily.max_wait_s,
            abandoned=daily.abandoned,
            lost_sales_inr=daily.lost_sales_inr,
            lost_margin_inr=margin,
            recovered_inr=daily.recovered_inr,
            alerts_open=int(alerts_open or 0),
            alerts_today=int(alerts_today or 0),
        )
        yesterday = (_dt.date.fromisoformat(date) - _dt.timedelta(days=1)).isoformat()
        prev = self.kpi_daily(store_id, yesterday)
        if prev is not None:
            today.deltas = {
                "footfall_in": float(today.footfall_in - prev.footfall_in),
                "visual_transactions": float(today.visual_transactions - prev.visual_transactions),
                "osa_pct": round(today.osa_pct - prev.osa_pct, 2),
                "lost_sales_inr": round(today.lost_sales_inr - prev.lost_sales_inr, 2),
                "recovered_inr": round(today.recovered_inr - prev.recovered_inr, 2),
                "avg_wait_s": None
                if today.avg_wait_s is None or prev.avg_wait_s is None
                else round(today.avg_wait_s - prev.avg_wait_s, 1),
            }
        return today

    @staticmethod
    def _margin_share(cfg: StoreConfig | None) -> float:
        if not cfg or not cfg.skus:
            return 0.10
        return sum(s.margin_pct for s in cfg.skus) / len(cfg.skus) / 100.0

    # ---------------------------------------------------------------- series
    def series(self, store_id: str, metric: str, from_ts: float, to_ts: float) -> Series:
        t = cdb.series_5m
        with self.db.read() as conn:
            rows = self.db.rows(
                conn,
                select(t.c.bucket_ts, t.c.value)
                .where(
                    and_(
                        t.c.store_id == store_id,
                        t.c.metric == metric,
                        t.c.bucket_ts >= from_ts,
                        t.c.bucket_ts <= to_ts,
                    )
                )
                .order_by(t.c.bucket_ts),
            )
        return Series(
            metric=metric,
            bucket_s=BUCKET_S,
            points=[SeriesPoint(ts=float(r["bucket_ts"]), value=float(r["value"] or 0.0)) for r in rows],
        )

    # ----------------------------------------------------------------- views
    def shelves(self, store_id: str, ts: float | None = None) -> list[ShelfStateView]:
        ts = self.clock.now() if ts is None else ts
        cfg = self.config(store_id)
        by_id = {s.shelf_id: s for s in cfg.shelves} if cfg else {}
        t = cdb.cloud_shelf_state
        with self.db.read() as conn:
            rows = self.db.rows(conn, select(t).where(t.c.store_id == store_id).order_by(t.c.shelf_id))
        known = {r["shelf_id"]: r for r in rows}
        out: list[ShelfStateView] = []
        for shelf_id in sorted(set(by_id) | set(known)):
            r = known.get(shelf_id, {})
            poly = by_id.get(shelf_id)
            sku_id = r.get("sku_id") or (poly.sku_id if poly else None)
            sku = cfg.sku(sku_id) if (cfg and sku_id) else None
            state = ShelfState(r.get("state") or "unknown")
            gap_started = r.get("gap_started_ts")
            gap_min = max(0.0, (ts - gap_started) / 60) if (state == ShelfState.EMPTY and gap_started) else None
            impact = self._impact_for(cfg, sku_id, gap_min) if gap_min else None
            out.append(
                ShelfStateView(
                    shelf_id=shelf_id,
                    name=poly.name if poly else shelf_id,
                    sku_id=sku_id,
                    sku_name=sku.name_en if sku else (sku_id or ""),
                    state=state,
                    coverage=float(r.get("coverage") or 0.0),
                    facings=int(r.get("facings") or 0),
                    capacity_facings=poly.capacity_facings if poly else 0,
                    min_facings=poly.min_facings if poly else 0,
                    consecutive_empty_scans=int(r.get("consecutive_empty_scans") or 0),
                    persistence_required=int(
                        r.get("persistence_required") or (cfg.rules.persistence_scans if cfg else 3)
                    ),
                    gap_started_ts=gap_started,
                    gap_minutes=round(gap_min, 2) if gap_min is not None else None,
                    last_scan_ts=r.get("last_scan_ts"),
                    occluded=False,
                    impact_open=impact,
                    has_reference=bool(r.get("reference")) or bool(poly and poly.reference),
                )
            )
        return out

    def queues(self, store_id: str) -> list[QueueView]:
        cfg = self.config(store_id)
        names = {c.counter_id: c.name for c in cfg.counters} if cfg else {}
        a = cdb.cloud_alerts
        with self.db.read() as conn:
            rows = self.db.rows(
                conn, select(cdb.cloud_queue_state).where(cdb.cloud_queue_state.c.store_id == store_id)
            )
            open_rows = self.db.rows(
                conn,
                select(a.c.subject_id, a.c.alert_id).where(
                    and_(
                        a.c.store_id == store_id,
                        a.c.kind.in_(["queue_long", "queue_forecast"]),
                        a.c.status != str(AlertStatus.RESOLVED),
                    )
                ),
            )
        open_by_counter = {r["subject_id"]: r["alert_id"] for r in open_rows}
        known = {r["counter_id"]: r for r in rows}
        out = []
        for cid in sorted(set(names) | set(known)):
            r = known.get(cid, {})
            out.append(
                QueueView(
                    counter_id=cid,
                    name=names.get(cid, cid),
                    snapshot=QueueSnapshot.model_validate(r["snapshot"]) if r.get("snapshot") else None,
                    forecast=QueueForecast.model_validate(r["forecast"]) if r.get("forecast") else None,
                    open_alert_id=open_by_counter.get(cid),
                )
            )
        return out

    def heatmap(self, store_id: str, camera_id: str | None, from_ts: float, to_ts: float) -> HeatmapResponse:
        cfg = self.config(store_id)
        cell_px = cfg.floorplan.heat_cell_px if cfg else 20
        width = (cfg.floorplan.width_px if cfg else 640) // cell_px
        height = (cfg.floorplan.height_px if cfg else 360) // cell_px
        t = cdb.cloud_heatmap_cells
        cond = [
            t.c.store_id == store_id,
            t.c.hour_bucket >= int(from_ts // 3600),
            t.c.hour_bucket <= int(to_ts // 3600),
        ]
        if camera_id:
            cond.append(t.c.camera_id == camera_id)
        with self.db.read() as conn:
            rows = self.db.rows(
                conn,
                select(
                    t.c.cell_x,
                    t.c.cell_y,
                    func.sum(t.c.dwell_s).label("dwell_s"),
                    func.sum(t.c.visits).label("visits"),
                )
                .where(and_(*cond))
                .group_by(t.c.cell_x, t.c.cell_y),
            )
        cells = [
            HeatCell(
                x=int(r["cell_x"]), y=int(r["cell_y"]), dwell_s=float(r["dwell_s"] or 0), visits=int(r["visits"] or 0)
            )
            for r in rows
        ]
        return HeatmapResponse(
            camera_id=camera_id,
            cell_px=cell_px,
            width_cells=width,
            height_cells=height,
            from_ts=from_ts,
            to_ts=to_ts,
            cells=cells,
            max_dwell_s=max((c.dwell_s for c in cells), default=0.0),
        )


__all__ = ["BUCKET_S", "COUNTERS", "GAUGES", "SERIES_METRICS", "AggResult", "Aggregator", "bucket_of"]
