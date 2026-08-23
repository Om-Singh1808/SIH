"""``generate_history``: 30 days of minute-level queue/footfall rows for the cloud forecaster.

Why a separate generator instead of running the agent model for 30 days?  The
agent model steps 4x per sim-second; a month would be 10 million steps.  The
forecaster only needs *minute* rows with the right structure, so this module
produces them directly from the same ingredients the live model uses:

* the same hour-of-day arrival curve (``scenarios.arrival_rate_pm``),
* festival weights from ``examples/festivals_in.csv`` (``is_festival``,
  ``festival_weight``, ``days_to_festival`` - demand ramps up 2 days before),
* the salary-week bump (days 1-7 of the month),
* weekend bump, a rain flag that dents footfall,
* a discrete-time queue: arrivals Poisson per minute, service capacity
  ``60 / default_service_s`` per minute, queue = carried + arrivals - served,
  with abandonment once the queue passes 6 (matching the live patience rule).

Columns follow ``retailsense_contracts.interfaces.HISTORY_MINUTE_COLUMNS`` /
``HISTORY_DAILY_COLUMNS`` exactly.  Everything is vectorised per day with numpy,
so 43,200 rows take ~0.2 s.  Deterministic for ``(seed, end_date)``.
"""

from __future__ import annotations

import datetime as _dt
from typing import TYPE_CHECKING, Any

import numpy as np

from retailsense_contracts.clock import date_to_ts
from retailsense_contracts.config import StoreConfig
from retailsense_contracts.interfaces import HISTORY_DAILY_COLUMNS, HISTORY_MINUTE_COLUMNS
from retailsense_contracts.testing import load_festivals_csv

from .scenarios import arrival_rate_pm

if TYPE_CHECKING:
    import pandas as pd

SALARY_WEEK_MULT = 1.15
WEEKEND_MULT = 1.10
RAIN_MULT = 0.75
PRE_FESTIVAL_MULT = 1.25  # 1-2 days before a festival
QUEUE_ABANDON_OVER = 6  # shoppers beyond this many in line give up
MINUTES = 1440


def _festival_rows(festivals: list[Any] | None) -> dict[str, float]:
    """Accept the contracts dict rows, ``Festival``-like objects or ``(date, weight)`` tuples."""
    rows = festivals if festivals is not None else load_festivals_csv()
    out: dict[str, float] = {}
    for f in rows:
        if isinstance(f, dict):
            out[str(f["date"])] = float(f.get("weight", 1.0))
        elif isinstance(f, tuple | list):
            out[str(f[0])] = float(f[1]) if len(f) > 1 else 1.0
        else:
            out[str(getattr(f, "date"))] = float(getattr(f, "weight", 1.0))
    return out


def _minute_curve(open_hours: tuple[str, str]) -> np.ndarray:
    """Baseline arrivals per minute for each minute of the day (0 outside opening hours)."""
    hours = np.arange(MINUTES) / 60.0
    curve = np.array([arrival_rate_pm(h) for h in hours])
    h0, m0 = (int(x) for x in open_hours[0].split(":"))
    h1, m1 = (int(x) for x in open_hours[1].split(":"))
    open_mask = (np.arange(MINUTES) >= h0 * 60 + m0) & (np.arange(MINUTES) < h1 * 60 + m1)
    return np.where(open_mask, curve, 0.0)


def _day_queue(
    arrivals: np.ndarray, service_pm: float, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Discrete-time single-server queue per minute: returns (queue_count, served, service_rate_pm)."""
    q = 0.0
    counts = np.empty(MINUTES, dtype=np.int64)
    served_arr = np.empty(MINUTES, dtype=np.float64)
    svc_arr = np.empty(MINUTES, dtype=np.float64)
    cap_noise = rng.normal(1.0, 0.15, MINUTES)
    for m in range(MINUTES):
        a = arrivals[m]
        cap = max(0.0, service_pm * cap_noise[m]) if a > 0 or q > 0 else 0.0
        served = min(q + a, cap)
        q = q + a - served
        if q > QUEUE_ABANDON_OVER:
            q -= (q - QUEUE_ABANDON_OVER) * 0.5  # half of the excess walks away
        counts[m] = int(round(q))
        served_arr[m] = served
        svc_arr[m] = cap
    return counts, served_arr, svc_arr


def generate_history(
    cfg: StoreConfig | None = None,
    days: int = 30,
    seed: int = 42,
    festivals: list[Any] | None = None,
    *,
    end_date: str | None = None,
    counter_id: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(minute_df, daily_df)`` per the History DataFrame contract.

    ``days x 1440`` minute rows ending on ``end_date`` (today in the store tz by default).
    """
    import pandas as pd

    if cfg is None:
        from retailsense_contracts.testing import sample_store_config

        cfg = sample_store_config()
    rng = np.random.default_rng(seed)
    tz_name = cfg.store.tz
    store_id = cfg.store.store_id
    counter = cfg.counters[0] if cfg.counters else None
    counter_id = counter_id or (counter.counter_id if counter else "counter-1")
    service_pm = 60.0 / (counter.default_service_s if counter else 45.0)
    fest = _festival_rows(festivals)
    fest_dates = sorted(_dt.date.fromisoformat(d) for d in fest)

    end = (
        _dt.date.fromisoformat(end_date)
        if end_date
        else _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30))).date()
    )
    dates = [end - _dt.timedelta(days=days - 1 - i) for i in range(days)]
    base_curve = _minute_curve(cfg.store.open_hours)
    minute_idx = np.arange(MINUTES)

    minute_frames: list[pd.DataFrame] = []
    daily_rows: list[tuple[Any, ...]] = []
    for d in dates:
        iso = d.isoformat()
        day_ts = date_to_ts(iso, tz_name)
        weight = fest.get(iso, 0.0)
        is_fest = iso in fest
        nxt = next((f for f in fest_dates if f >= d), None)
        dtf = (nxt - d).days if nxt else 365
        salary = d.day <= 7
        rain = bool(rng.random() < 0.12)
        mult = 1.0 + 0.8 * weight
        mult *= SALARY_WEEK_MULT if salary else 1.0
        mult *= WEEKEND_MULT if d.weekday() >= 5 else 1.0
        mult *= PRE_FESTIVAL_MULT if 0 < dtf <= 2 else 1.0
        mult *= RAIN_MULT if rain else 1.0
        mult *= float(rng.normal(1.0, 0.08))  # day-level mood

        lam = base_curve * mult
        arrivals = rng.poisson(lam).astype(np.float64)
        q_counts, served, svc = _day_queue(arrivals, service_pm, rng)
        fin_15 = np.convolve(arrivals, np.ones(15), mode="full")[:MINUTES]
        # occupancy ~ arrivals over the last ~6 minutes (avg visit) plus the queue
        occupancy = np.convolve(arrivals, np.ones(6), mode="full")[:MINUTES] + q_counts
        dow = d.weekday()
        frame = pd.DataFrame(
            {
                "ts": day_ts + minute_idx * 60.0,
                "store_id": store_id,
                "counter_id": counter_id,
                "queue_count": q_counts,
                "arrivals_pm": arrivals,
                "service_pm": np.round(svc, 3),
                "footfall_in_15m": fin_15.astype(np.int64),
                "occupancy": occupancy.astype(np.int64),
                "hour": (minute_idx // 60).astype(np.int64),
                "dow": dow,
                "minute_of_day": minute_idx,
                "is_festival": is_fest,
                "festival_weight": weight,
                "days_to_festival": dtf,
                "is_salary_week": salary,
            }
        )
        minute_frames.append(frame)
        daily_rows.append(
            (iso, store_id, int(arrivals.sum()), int(round(served.sum())), dow, is_fest, weight, dtf, salary, rain)
        )

    minute_df = pd.concat(minute_frames, ignore_index=True)[list(HISTORY_MINUTE_COLUMNS)]
    daily_df = pd.DataFrame(daily_rows, columns=list(HISTORY_DAILY_COLUMNS))
    return minute_df, daily_df


def write_history(minute_df: pd.DataFrame, daily_df: pd.DataFrame, out: str) -> tuple[str, str]:
    """Write ``<out>`` (minute rows) and ``<out stem>_daily.<ext>`` as parquet or csv; returns both paths."""
    from pathlib import Path

    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    daily_path = p.with_name(p.stem + "_daily" + p.suffix)
    if p.suffix.lower() == ".parquet":
        try:
            minute_df.to_parquet(p, index=False)
            daily_df.to_parquet(daily_path, index=False)
            return str(p), str(daily_path)
        except ImportError:  # no pyarrow/fastparquet: fall back to csv next to it
            p = p.with_suffix(".csv")
            daily_path = daily_path.with_suffix(".csv")
    minute_df.to_csv(p, index=False)
    daily_df.to_csv(daily_path, index=False)
    return str(p), str(daily_path)


__all__ = ["generate_history", "write_history"]
