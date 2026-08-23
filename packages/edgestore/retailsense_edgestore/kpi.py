"""Store-day KPI roll-over and day-over-day deltas.

The store day follows the *store's* timezone (``Asia/Kolkata`` by default), not
UTC: a kirana that closes at 22:00 IST must see "today" reset at local midnight,
which is 18:30 UTC. ``zoneinfo`` (via ``retailsense_contracts.clock``) does the
conversion, so DST-free IST and any future chain store in another zone behave
the same.

``KpiAggregator`` is driven by the edge app's periodic task: call
``maybe_rollover(now_ts)`` every few seconds; when the local date changes it
freezes the previous day's ``KpiToday`` into ``kpi_daily`` so tomorrow's
``deltas`` ("vs yesterday") have a baseline.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Protocol

from retailsense_contracts.api import KpiDaily, KpiToday
from retailsense_contracts.clock import DEFAULT_TZ, date_to_ts, day_start_ts, store_date

# metrics whose day-over-day delta the board shows (KpiTile arrows)
DELTA_METRICS = ("footfall_in", "visual_transactions", "osa_pct", "lost_sales_inr", "recovered_inr", "avg_wait_s")


class _StoreLike(Protocol):
    tz: str

    def kpi_today(self, ts: float) -> KpiToday: ...
    def upsert_kpi_daily(self, row: KpiDaily, **kw: Any) -> None: ...
    def kpi_daily(self, date: str) -> KpiDaily | None: ...


def deltas_vs_yesterday(today: KpiToday, yesterday: KpiDaily | None) -> dict[str, float | None]:
    """``metric -> today - yesterday`` for ``DELTA_METRICS``; ``None`` when either side is unknown."""
    if yesterday is None:
        return {}
    out: dict[str, float | None] = {}
    for m in DELTA_METRICS:
        a, b = getattr(today, m), getattr(yesterday, m)
        if a is None or b is None:
            out[m] = None
        else:
            nd = 1 if m == "avg_wait_s" else 2
            out[m] = round(float(a) - float(b), nd)
    return out


def to_daily(k: KpiToday, *, shrink_inr: float = 0.0) -> KpiDaily:
    """Freeze a ``KpiToday`` snapshot into the ``KpiDaily`` row shape."""
    return KpiDaily(
        store_id=k.store_id,
        date=k.date,
        footfall_in=k.footfall_in,
        footfall_out=k.footfall_out,
        visual_transactions=k.visual_transactions,
        conversion_pct=k.conversion_pct,
        atv_inr=k.atv_inr,
        osa_pct=k.osa_pct,
        gap_minutes_total=k.gap_minutes_total,
        avg_wait_s=k.avg_wait_s,
        max_wait_s=k.max_wait_s,
        abandoned=k.abandoned,
        lost_sales_inr=k.lost_sales_inr,
        recovered_inr=k.recovered_inr,
        shrink_inr=shrink_inr,
        alerts_total=k.alerts_today,
    )


class KpiAggregator:
    """Detects store-day boundaries and persists ``kpi_daily`` rows.

    ``maybe_rollover(now_ts)`` returns the frozen ``KpiDaily`` when a boundary was
    crossed since the last call (or ``None``).  ``snapshot(now_ts)`` upserts the
    *current* day (idempotent; useful every few minutes so a crash at 23:59 does
    not lose the whole day).
    """

    def __init__(self, store: _StoreLike, tz: str | None = None, *, shrink_inr_source: Any = None):
        self.store = store
        self.tz = tz or getattr(store, "tz", None) or DEFAULT_TZ
        self._last_date: str | None = None
        self._shrink_source = shrink_inr_source  # optional callable(date) -> rupees of shrink for the day

    # -- day arithmetic ------------------------------------------------------
    def date_of(self, ts: float) -> str:
        return store_date(ts, self.tz)

    def day_start(self, ts: float) -> float:
        return day_start_ts(ts, self.tz)

    def day_end(self, ts: float) -> float:
        """Final instant of the store day containing ``ts``: next local midnight minus 1 ms."""
        nxt = (_dt.date.fromisoformat(self.date_of(ts)) + _dt.timedelta(days=1)).isoformat()
        return date_to_ts(nxt, self.tz) - 0.001

    def yesterday(self, ts: float) -> str:
        return (_dt.date.fromisoformat(self.date_of(ts)) - _dt.timedelta(days=1)).isoformat()

    # -- persistence ---------------------------------------------------------
    def _shrink_for(self, date: str) -> float:
        if self._shrink_source is None:
            return 0.0
        try:
            return float(self._shrink_source(date) or 0.0)
        except Exception:  # a Tally hiccup must never block the rollover
            return 0.0

    def snapshot(self, now_ts: float) -> KpiDaily:
        """Upsert the current day's row from ``kpi_today(now_ts)``."""
        today = self.store.kpi_today(now_ts)
        row = to_daily(today, shrink_inr=self._shrink_for(today.date))
        self.store.upsert_kpi_daily(row, lost_margin_inr=today.lost_margin_inr)
        return row

    def rollover(self, prev_day_ts: float) -> KpiDaily:
        """Freeze the store day containing ``prev_day_ts`` at its final instant."""
        return self.snapshot(self.day_end(prev_day_ts))

    def maybe_rollover(self, now_ts: float) -> KpiDaily | None:
        """Call periodically. The first call only records the date; afterwards a changed
        local date triggers ``rollover`` of the previous day."""
        today = self.date_of(now_ts)
        if self._last_date is None:
            self._last_date = today
            return None
        if today == self._last_date:
            return None
        prev_ts = date_to_ts(self._last_date, self.tz, "12:00")
        row = self.rollover(prev_ts)
        self._last_date = today
        return row

    def deltas(self, now_ts: float) -> dict[str, float | None]:
        """Today's KPIs vs the persisted ``kpi_daily`` row for yesterday."""
        return deltas_vs_yesterday(self.store.kpi_today(now_ts), self.store.kpi_daily(self.yesterday(now_ts)))


__all__ = ["DELTA_METRICS", "KpiAggregator", "deltas_vs_yesterday", "to_daily"]
