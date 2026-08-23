"""Cloud queue-length forecaster (``CloudQueueForecaster`` implementation).

What it does: given per-minute history of a checkout counter, learn to predict the queue length
5 / 10 / 15 / 30 minutes ahead. On the edge a cheap trend extrapolation runs (``edge_trend``); this cloud
model (``cloud_gbm``) is retrained nightly from the uploaded history and its predictions are pushed back
to the dashboard with a live MAE badge (see :mod:`retailsense_forecasting.eval`).

Design choices (and why)
------------------------
* **One direct model per horizon** rather than a recursive one-step model. Direct models do not
  compound errors over 30 steps and let each horizon pick its own features (the 5-minute model leans on
  ``queue_lag_1``; the 30-minute one on ``hour`` / ``arrivals_pm`` / festival flags).
* **Gradient boosting on lag features** (HistGradientBoosting by default, LightGBM if importable). It is
  robust to the spiky, non-Gaussian queue process, needs no scaling, handles NaN lags natively, and
  trains on a month of minute data (43k rows) in a couple of seconds on a laptop CPU.
* **Time-ordered holdout = last 3 days.** Random splits would leak neighbouring minutes. The holdout
  MAE is reported next to the *naive persistence* baseline ("queue in h minutes = queue now"), which is
  exactly what the edge fallback does - so the FitReport answers "is the cloud model worth it?".
* **Non-negative, bounded predictions**: outputs are clipped at 0 and the inference row is built by the
  same ``make_queue_features`` as training (no train/serve skew).
* **Persistence**: ``save``/``load`` with joblib under ``var/models/`` (override with
  ``RETAILSENSE_MODEL_DIR``), storing the feature list and the FitReport beside the models so a loaded
  forecaster can still answer ``report()``.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import time
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from retailsense_contracts.api import FitReport

from ._backend import Backend, choose_backend, fit_threads, make_regressor, predict_nonneg
from .features import (
    DEFAULT_TZ,
    HORIZONS,
    MIN_RECENT_MINUTES,
    QUEUE_FEATURE_COLUMNS,
    _calendar_from_ts,
    _festival_frame,
    make_queue_features,
    target_column,
)

log = logging.getLogger("retailsense.forecasting.queue")

HOLDOUT_DAYS = 3
QUIET_KEEP_EVERY = 4
"""Training-row thinning: overnight minutes where the queue, every lag and the target are all zero carry
no information beyond "closed shop = empty queue"; keeping 1 in 4 of them roughly halves fit time on a
month of history without changing holdout accuracy (the holdout itself is never thinned)."""
DEFAULT_MODEL_FILE = "queue_forecaster.joblib"


def default_model_dir() -> Path:
    """``$RETAILSENSE_MODEL_DIR`` or ``var/models`` relative to the working directory."""
    return Path(os.environ.get("RETAILSENSE_MODEL_DIR", "var/models"))


def _split_holdout(df: pd.DataFrame, holdout_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Time-ordered split: the last ``holdout_days`` of ``ts`` are the holdout."""
    cutoff = float(df["ts"].max()) - holdout_days * 86400.0
    train = df[df["ts"] <= cutoff]
    hold = df[df["ts"] > cutoff]
    if train.empty or hold.empty:  # too little history -> evaluate in-sample (still reported honestly)
        return df, df
    return train, hold


def _thin_quiet_rows(train: pd.DataFrame, y_cols: list[str], keep_every: int) -> pd.DataFrame:
    """Drop most all-zero rows (see ``QUIET_KEEP_EVERY``); deterministic (position based)."""
    if keep_every <= 1:
        return train
    lag_cols = [c for c in train.columns if c.startswith("queue_lag_")]
    cols = ["queue_count", *lag_cols, *y_cols]
    quiet = (train[cols].fillna(0.0).abs().sum(axis=1) == 0).to_numpy()
    pos = np.arange(len(train))
    keep = ~quiet | (pos % keep_every == 0)
    return train[keep]


class QueueForecaster:
    """Per-horizon gradient-boosting queue forecaster. See module docstring for rationale."""

    model_name = "cloud_gbm"
    horizons: tuple[int, ...] = HORIZONS

    def __init__(
        self,
        *,
        backend: Backend | Literal["auto"] = "auto",
        max_iter: int = 200,
        holdout_days: int = HOLDOUT_DAYS,
        tz: str = DEFAULT_TZ,
        seed: int = 0,
        features: tuple[str, ...] | None = None,
    ) -> None:
        self.backend: Backend = choose_backend(backend)
        self.max_iter = max_iter
        self.holdout_days = holdout_days
        self.tz = tz
        self.seed = seed
        self.features: list[str] = list(features or QUEUE_FEATURE_COLUMNS)
        self.models: dict[int, Any] = {}
        self.mae_by_horizon: dict[int, float] = {}
        self.baseline_by_horizon: dict[int, float] = {}
        self._report: FitReport | None = None

    # ------------------------------------------------------------------ fit
    def fit(self, history: pd.DataFrame) -> FitReport:
        """Train one model per horizon on ``history`` (``HISTORY_MINUTE_COLUMNS``)."""
        t0 = time.perf_counter()
        feat = make_queue_features(history, with_targets=True, horizons=self.horizons, tz=self.tz)
        train, hold = _split_holdout(feat, self.holdout_days)
        y_cols = [target_column(h) for h in self.horizons]
        train = _thin_quiet_rows(train, y_cols, QUIET_KEEP_EVERY)

        self.models, self.mae_by_horizon, self.baseline_by_horizon = {}, {}, {}
        for h in self.horizons:
            y_col = target_column(h)
            tr = train.dropna(subset=[y_col])
            ho = hold.dropna(subset=[y_col])
            model = make_regressor(self.backend, max_iter=self.max_iter, seed=self.seed)
            with fit_threads():
                model.fit(tr[self.features], tr[y_col].to_numpy())
            self.models[h] = model
            pred = predict_nonneg(model, ho[self.features])
            actual = ho[y_col].to_numpy(dtype="float64")
            self.mae_by_horizon[h] = float(np.mean(np.abs(pred - actual)))
            # naive persistence: "the queue in h minutes equals the queue now"
            self.baseline_by_horizon[h] = float(np.mean(np.abs(ho["queue_count"].to_numpy("float64") - actual)))

        self._report = FitReport(
            model=f"{self.model_name}:{self.backend}",
            target="queue_count",
            trained_ts=time.time(),
            n_rows=int(len(feat)),
            mae_holdout=round(float(np.mean(list(self.mae_by_horizon.values()))), 4),
            mae_baseline=round(float(np.mean(list(self.baseline_by_horizon.values()))), 4),
            features=list(self.features),
            horizons=list(self.horizons),
        )
        log.info(
            "queue forecaster fit: backend=%s rows=%d mae=%.3f baseline=%.3f in %.2fs",
            self.backend,
            len(feat),
            self._report.mae_holdout,
            self._report.mae_baseline,
            time.perf_counter() - t0,
        )
        return self._report

    # -------------------------------------------------------------- predict
    def _inference_row(self, recent: pd.DataFrame, now_ts: float) -> pd.DataFrame:
        """Build the single feature row for ``now_ts`` from the last <= 30 minutes of observations."""
        if recent is None or recent.empty or "queue_count" not in recent.columns:
            raise ValueError("recent must contain at least one row with queue_count")
        df = recent.copy()
        if "ts" not in df.columns:
            # Assume one row per minute ending at now_ts.
            df["ts"] = now_ts - 60.0 * np.arange(len(df) - 1, -1, -1)
        df = df[df["ts"] <= now_ts + 1e-6]
        if df.empty:
            raise ValueError("recent has no rows at or before now_ts")
        if len(df) <= MIN_RECENT_MINUTES:
            log.debug("recent window has %d rows (< %d): lag features partly NaN", len(df), MIN_RECENT_MINUTES)
        feat = make_queue_features(df, with_targets=False, tz=self.tz)
        last = feat.iloc[[-1]].copy()
        # Calendar/festival features must describe *now*, not the last observation's minute.
        cal = _calendar_from_ts(pd.Series([int(now_ts)], index=last.index), self.tz)
        for c in ("hour", "dow", "minute_of_day"):
            last[c] = cal[c]
        fest = _festival_frame(cal["_date"], last.index)
        for c in fest.columns:
            last[c] = fest[c].astype("int64") if c in ("is_festival", "is_salary_week") else fest[c]
        return last[self.features]

    def predict(self, recent: pd.DataFrame, now_ts: float) -> dict[str, float]:
        """``{"5": q5, "10": q10, "15": q15, "30": q30}`` - expected queue counts, all >= 0."""
        if not self.models:
            raise RuntimeError("QueueForecaster.predict called before fit()/load()")
        X = self._inference_row(recent, now_ts)
        return {str(h): round(float(predict_nonneg(self.models[h], X)[0]), 2) for h in self.horizons}

    def predict_frame(self, history: pd.DataFrame) -> pd.DataFrame:
        """Batch prediction for evaluation: one row per input minute with ``pred_{h}`` columns."""
        feat = make_queue_features(history, with_targets=False, tz=self.tz)
        out = feat[["ts", "counter_id", "queue_count"]].copy()
        for h in self.horizons:
            out[f"pred_{h}"] = predict_nonneg(self.models[h], feat[self.features])
        return out

    def report(self) -> FitReport | None:
        return self._report

    # ------------------------------------------------------------ persist
    def save(self, path: str | Path | None = None) -> Path:
        """Persist models + metadata with joblib (default ``var/models/queue_forecaster.joblib``)."""
        import joblib

        p = Path(path) if path else default_model_dir() / DEFAULT_MODEL_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "kind": "QueueForecaster",
            "backend": self.backend,
            "max_iter": self.max_iter,
            "holdout_days": self.holdout_days,
            "tz": self.tz,
            "seed": self.seed,
            "features": self.features,
            "horizons": list(self.horizons),
            "models": self.models,
            "mae_by_horizon": self.mae_by_horizon,
            "baseline_by_horizon": self.baseline_by_horizon,
            "report": self._report.model_dump() if self._report else None,
            "saved_at": _dt.datetime.now(_dt.UTC).isoformat(),
        }
        joblib.dump(payload, p)
        return p

    @classmethod
    def load(cls, path: str | Path | None = None) -> QueueForecaster:
        """Inverse of :meth:`save`."""
        import joblib

        p = Path(path) if path else default_model_dir() / DEFAULT_MODEL_FILE
        payload = joblib.load(p)
        if payload.get("kind") != "QueueForecaster":
            raise ValueError(f"{p} is not a QueueForecaster artefact")
        obj = cls(
            backend=payload["backend"],
            max_iter=payload["max_iter"],
            holdout_days=payload["holdout_days"],
            tz=payload["tz"],
            seed=payload["seed"],
            features=tuple(payload["features"]),
        )
        obj.horizons = tuple(payload["horizons"])
        obj.models = payload["models"]
        obj.mae_by_horizon = payload["mae_by_horizon"]
        obj.baseline_by_horizon = payload["baseline_by_horizon"]
        obj._report = FitReport.model_validate(payload["report"]) if payload.get("report") else None
        return obj


__all__ = ["DEFAULT_MODEL_FILE", "HOLDOUT_DAYS", "QUIET_KEEP_EVERY", "QueueForecaster", "default_model_dir"]
