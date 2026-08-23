"""Feature engineering for the queue (minute) and footfall (daily) history frames.

Leakage is the #1 way a time-series model looks great offline and fails live, so the rules here are
strict and tested (``test_queue_features_shapes_no_leak``):

* every feature at row *t* is computed from rows ``<= t`` only (``shift(k)`` with ``k >= 1`` for lags,
  ``rolling(...)`` windows that end at *t*);
* every target ``y_h`` is ``queue_count`` shifted **backwards** by ``h`` minutes (``shift(-h)``) and is
  *only* produced when ``with_targets=True`` - the prediction path never builds targets;
* lags/rollings are computed per ``counter_id`` so a multi-counter store never leaks across lanes;
* rows with missing lag history (the first 15 minutes of each counter) are kept with NaN - the
  histogram gradient-boosting backend handles NaN natively, and the forecaster drops NaN *targets*.

The same function builds the inference frame from the last 30 minutes, so training and serving share
one code path (no train/serve skew).
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .festivals import festival_features, is_salary_week

HORIZONS: tuple[int, ...] = (5, 10, 15, 30)
"""Queue forecast horizons in minutes (mirrors ``QueueForecast.horizons`` keys in the contracts)."""

QUEUE_LAGS: tuple[int, ...] = (1, 2, 3, 5, 10, 15)
QUEUE_ROLLING: tuple[int, ...] = (5, 15)
MIN_RECENT_MINUTES = max(max(QUEUE_LAGS), max(QUEUE_ROLLING))
"""Minutes of history needed for a complete feature row (15)."""

DEFAULT_TZ = "Asia/Kolkata"

QUEUE_FEATURE_COLUMNS: tuple[str, ...] = (
    "queue_count",
    *[f"queue_lag_{k}" for k in QUEUE_LAGS],
    *[f"queue_roll_{w}" for w in QUEUE_ROLLING],
    "queue_diff_1",
    "arrivals_pm",
    "service_pm",
    "footfall_in_15m",
    "hour",
    "dow",
    "minute_of_day",
    "is_festival",
    "festival_weight",
    "days_to_festival",
    "is_salary_week",
)

DAILY_LAGS: tuple[int, ...] = (1, 7, 14)
DAILY_FEATURE_COLUMNS: tuple[str, ...] = (
    "dow",
    "is_weekend",
    "is_festival",
    "festival_weight",
    "days_to_festival",
    "is_salary_week",
    "rain_flag",
    "day_of_month",
    *[f"footfall_lag_{k}" for k in DAILY_LAGS],
    "footfall_roll_7",
)


def target_column(h: int) -> str:
    """Name of the shifted target for horizon ``h`` minutes."""
    return f"y_{h}"


# ---------------------------------------------------------------------------
# calendar helpers


def _calendar_from_ts(ts: pd.Series, tz: str) -> pd.DataFrame:
    """hour / dow / minute_of_day / date derived from epoch seconds in the store timezone."""
    local = pd.to_datetime(ts.astype("int64"), unit="s", utc=True).dt.tz_convert(ZoneInfo(tz))
    return pd.DataFrame(
        {
            "hour": local.dt.hour.astype("int64"),
            "dow": local.dt.dayofweek.astype("int64"),
            "minute_of_day": (local.dt.hour * 60 + local.dt.minute).astype("int64"),
            "_date": local.dt.date,
        },
        index=ts.index,
    )


def _festival_frame(dates: Iterable[_dt.date], index: pd.Index) -> pd.DataFrame:
    """Festival lookup with one ``festival_features`` call per *distinct* date (cheap for 43k rows)."""
    dates = list(dates)
    cache: dict[_dt.date, tuple[bool, float, int, bool]] = {}
    for d in set(dates):
        is_f, w, dtn, _ = festival_features(d)
        cache[d] = (is_f, w, dtn, is_salary_week(d))
    rows = [cache[d] for d in dates]
    return pd.DataFrame(
        rows, columns=["is_festival", "festival_weight", "days_to_festival", "is_salary_week"], index=index
    )


def _ensure_calendar(df: pd.DataFrame, tz: str) -> pd.DataFrame:
    """Fill any missing calendar/festival columns from ``ts`` so partial frames (live API) still work."""
    cal_cols = ("hour", "dow", "minute_of_day")
    fest_cols = ("is_festival", "festival_weight", "days_to_festival", "is_salary_week")
    need_cal = any(c not in df.columns for c in cal_cols)
    need_fest = any(c not in df.columns for c in fest_cols)
    if not (need_cal or need_fest):
        return df
    cal = _calendar_from_ts(df["ts"], tz)
    out = df.copy()
    for c in cal_cols:
        if c not in out.columns:
            out[c] = cal[c]
    if need_fest:
        fest = _festival_frame(cal["_date"], df.index)
        for c in fest_cols:
            if c not in out.columns:
                out[c] = fest[c]
    return out


# ---------------------------------------------------------------------------
# queue (minute) features


def make_queue_features(
    minute_df: pd.DataFrame,
    *,
    with_targets: bool = False,
    horizons: Iterable[int] = HORIZONS,
    tz: str = DEFAULT_TZ,
) -> pd.DataFrame:
    """Return ``minute_df`` + lag/rolling features (+ ``y_h`` targets when ``with_targets``).

    Input follows ``HISTORY_MINUTE_COLUMNS``; only ``ts`` and ``queue_count`` are mandatory - the rest
    are derived (calendar/festival) or filled with 0 (``arrivals_pm`` etc.) when absent. Rows are sorted
    by (counter_id, ts); lags and rollings never cross counters.
    """
    if minute_df.empty:
        raise ValueError("minute_df is empty")
    df = minute_df.copy()
    if "counter_id" not in df.columns:
        df["counter_id"] = "counter-1"
    df = df.sort_values(["counter_id", "ts"], kind="stable").reset_index(drop=True)
    df = _ensure_calendar(df, tz)
    for col in ("arrivals_pm", "service_pm", "footfall_in_15m"):
        if col not in df.columns:
            df[col] = 0.0

    q = df["queue_count"].astype("float64")
    g = q.groupby(df["counter_id"], sort=False)
    for k in QUEUE_LAGS:
        df[f"queue_lag_{k}"] = g.shift(k)
    for w in QUEUE_ROLLING:
        # rolling window ends at t (inclusive) -> uses only past/present values
        df[f"queue_roll_{w}"] = g.transform(lambda s, w=w: s.rolling(w, min_periods=1).mean())
    df["queue_diff_1"] = q - df["queue_lag_1"]

    for bcol in ("is_festival", "is_salary_week"):
        df[bcol] = df[bcol].astype("int64")

    if with_targets:
        for h in horizons:
            df[target_column(h)] = g.shift(-h)
    return df


def recent_window_ok(recent: pd.DataFrame) -> bool:
    """True when ``recent`` has enough minutes for a full feature row (> 15 rows)."""
    return len(recent) > MIN_RECENT_MINUTES


def ts_from_iso_date(date: str | _dt.date, tz: str = DEFAULT_TZ) -> float:
    """Epoch seconds at local midnight for ``date`` (helper shared by tests and the footfall model)."""
    d = _dt.date.fromisoformat(date) if isinstance(date, str) else date
    return _dt.datetime(d.year, d.month, d.day, tzinfo=ZoneInfo(tz)).timestamp()


# ---------------------------------------------------------------------------
# daily (footfall) features


def make_daily_features(daily_df: pd.DataFrame, *, with_target: bool = False) -> pd.DataFrame:
    """Daily footfall features following ``HISTORY_DAILY_COLUMNS``.

    Lags 1/7/14 and a 7-day rolling mean of ``footfall_in`` are computed **from the previous day
    backwards** (``shift(1)`` before rolling) so the row for day *d* never sees its own footfall. With
    ``with_target`` the target ``y`` is ``footfall_in`` of the same row (the features are already lagged).
    """
    if daily_df.empty:
        raise ValueError("daily_df is empty")
    df = daily_df.copy()
    df["_date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("_date", kind="stable").reset_index(drop=True)

    if "dow" not in df.columns:
        df["dow"] = [d.weekday() for d in df["_date"]]
    fest_missing = [
        c for c in ("is_festival", "festival_weight", "days_to_festival", "is_salary_week") if c not in df
    ]
    if fest_missing:
        fest = _festival_frame(df["_date"], df.index)
        for c in fest_missing:
            df[c] = fest[c]
    if "rain_flag" not in df.columns:
        df["rain_flag"] = 0
    df["is_weekend"] = (df["dow"] >= 5).astype("int64")
    df["day_of_month"] = [d.day for d in df["_date"]]
    for bcol in ("is_festival", "is_salary_week", "rain_flag"):
        df[bcol] = df[bcol].astype("int64")

    if "footfall_in" in df.columns:
        f = df["footfall_in"].astype("float64")
        for k in DAILY_LAGS:
            df[f"footfall_lag_{k}"] = f.shift(k)
        df["footfall_roll_7"] = f.shift(1).rolling(7, min_periods=1).mean()
        if with_target:
            df["y"] = f
    else:
        for k in DAILY_LAGS:
            df[f"footfall_lag_{k}"] = np.nan
        df["footfall_roll_7"] = np.nan
    return df


__all__ = [
    "DAILY_FEATURE_COLUMNS",
    "DAILY_LAGS",
    "DEFAULT_TZ",
    "HORIZONS",
    "MIN_RECENT_MINUTES",
    "QUEUE_FEATURE_COLUMNS",
    "QUEUE_LAGS",
    "QUEUE_ROLLING",
    "make_daily_features",
    "make_queue_features",
    "recent_window_ok",
    "target_column",
    "ts_from_iso_date",
]
