"""Daily footfall forecaster (``CloudFootfallForecaster`` implementation).

Predicts store entries per day for the next *n* days from the daily history frame
(``HISTORY_DAILY_COLUMNS``). The forecast feeds two things: the owner's "next 7 days" chart with festival
markers, and :func:`retailsense_forecasting.reorder.suggest_reorder`, which scales the supplier order by
``forecast / average footfall``.

Design choices
--------------
* Features: day-of-week, weekend, festival flag/weight/days-to-next, salary week, rain flag, day of
  month and footfall lags 1 / 7 / 14 plus a 7-day rolling mean (all lagged, see ``make_daily_features``).
* Model: HistGradientBoosting (or LightGBM if importable) with a *small* ``min_samples_leaf`` because a
  kirana typically has 30-90 days of history - a month of data is still only 30 rows.
* Holdout: the last 3 days, evaluated against the naive "same as yesterday" baseline.
* Uncertainty: an 80% band of ``predicted +/- 1.28 x holdout MAE`` (1.28 = z for the 90th percentile;
  assuming roughly symmetric errors that covers ~80% of outcomes). It is deliberately simple and
  explainable on a phone screen - "between 210 and 290 people".
* Multi-day horizon: recursive - day *d+1*'s lags use the prediction for *d* when no actual exists.
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
from retailsense_contracts.api import FitReport, FootfallForecastDay

from ._backend import Backend, choose_backend, fit_threads, make_regressor, predict_nonneg
from .features import DAILY_FEATURE_COLUMNS, DAILY_LAGS, make_daily_features
from .festivals import festival_features, is_salary_week

log = logging.getLogger("retailsense.forecasting.footfall")

HOLDOUT_DAYS = 3
Z_80 = 1.28
DEFAULT_MODEL_FILE = "footfall_forecaster.joblib"


class FootfallForecaster:
    """Daily gradient-boosting footfall model with an 80% MAE band. See module docstring."""

    model_name = "cloud_gbm_daily"

    def __init__(
        self,
        *,
        backend: Backend | Literal["auto"] = "auto",
        max_iter: int = 150,
        holdout_days: int = HOLDOUT_DAYS,
        seed: int = 0,
    ) -> None:
        self.backend: Backend = choose_backend(backend)
        self.max_iter = max_iter
        self.holdout_days = holdout_days
        self.seed = seed
        self.features: list[str] = list(DAILY_FEATURE_COLUMNS)
        self.model: Any = None
        self.mae_holdout: float = 0.0
        self.mae_baseline: float = 0.0
        self._history: pd.DataFrame | None = None  # date -> footfall_in (+ rain_flag) for lags
        self._report: FitReport | None = None

    # ------------------------------------------------------------------ fit
    def _make_model(self) -> Any:
        # Tiny datasets: allow small leaves, keep boosting short and slow to avoid memorising.
        if self.backend == "lightgbm":
            return make_regressor("lightgbm", max_iter=self.max_iter, seed=self.seed, min_child_samples=3)
        return make_regressor(
            "sklearn", max_iter=self.max_iter, seed=self.seed, min_samples_leaf=3, learning_rate=0.05
        )

    def fit(self, daily: pd.DataFrame) -> FitReport:
        t0 = time.perf_counter()
        feat = make_daily_features(daily, with_target=True)
        n = len(feat)
        n_hold = self.holdout_days if n > self.holdout_days + 7 else 0
        train = feat.iloc[: n - n_hold] if n_hold else feat
        hold = feat.iloc[n - n_hold :] if n_hold else feat

        self.model = self._make_model()
        with fit_threads():
            self.model.fit(train[self.features], train["y"].to_numpy())
        pred = predict_nonneg(self.model, hold[self.features])
        actual = hold["y"].to_numpy("float64")
        self.mae_holdout = float(np.mean(np.abs(pred - actual)))
        base = hold["footfall_lag_1"].fillna(hold["y"]).to_numpy("float64")
        self.mae_baseline = float(np.mean(np.abs(base - actual)))

        self._history = feat[["_date", "footfall_in", "rain_flag"]].copy()
        self._report = FitReport(
            model=f"{self.model_name}:{self.backend}",
            target="footfall_in",
            trained_ts=time.time(),
            n_rows=int(n),
            mae_holdout=round(self.mae_holdout, 3),
            mae_baseline=round(self.mae_baseline, 3),
            features=list(self.features),
            horizons=[],
        )
        log.info(
            "footfall fit: rows=%d mae=%.1f baseline=%.1f in %.2fs",
            n,
            self.mae_holdout,
            self.mae_baseline,
            time.perf_counter() - t0,
        )
        return self._report

    def report(self) -> FitReport | None:
        return self._report

    # -------------------------------------------------------------- predict
    def predict_days(self, start_date: str, n: int) -> list[FootfallForecastDay]:
        """Forecast ``n`` days from ``start_date`` (ISO). Recursive over unknown days."""
        if self.model is None or self._history is None:
            raise RuntimeError("FootfallForecaster.predict_days called before fit()/load()")
        if n <= 0:
            return []
        start = _dt.date.fromisoformat(start_date)
        known: dict[_dt.date, float] = {
            d: float(v) for d, v in zip(self._history["_date"], self._history["footfall_in"], strict=True)
        }
        rain: dict[_dt.date, int] = {
            d: int(v) for d, v in zip(self._history["_date"], self._history["rain_flag"], strict=True)
        }
        band = Z_80 * self.mae_holdout
        out: list[FootfallForecastDay] = []
        for i in range(n):
            d = start + _dt.timedelta(days=i)
            is_f, w, dtn, name = festival_features(d)
            lags = {k: known.get(d - _dt.timedelta(days=k), np.nan) for k in DAILY_LAGS}
            last7 = [known[d - _dt.timedelta(days=k)] for k in range(1, 8) if (d - _dt.timedelta(days=k)) in known]
            row = pd.DataFrame(
                [
                    {
                        "dow": d.weekday(),
                        "is_weekend": int(d.weekday() >= 5),
                        "is_festival": int(is_f),
                        "festival_weight": w,
                        "days_to_festival": dtn,
                        "is_salary_week": int(is_salary_week(d)),
                        "rain_flag": rain.get(d, 0),
                        "day_of_month": d.day,
                        **{f"footfall_lag_{k}": v for k, v in lags.items()},
                        "footfall_roll_7": float(np.mean(last7)) if last7 else np.nan,
                    }
                ]
            )
            pred = float(predict_nonneg(self.model, row[self.features])[0])
            known.setdefault(d, pred)  # feed forward for the next day's lags (never overwrite actuals)
            out.append(
                FootfallForecastDay(
                    date=d.isoformat(),
                    predicted=round(pred, 1),
                    lower=round(max(0.0, pred - band), 1),
                    upper=round(pred + band, 1),
                    is_festival=is_f,
                    festival_name=name,
                    days_to_festival=dtn,
                )
            )
        return out

    def average_daily_footfall(self, last_days: int = 14) -> float:
        """Mean actual footfall over the last ``last_days`` days of training history (reorder scaling)."""
        if self._history is None or self._history.empty:
            return 0.0
        return float(self._history["footfall_in"].tail(last_days).mean())

    # ------------------------------------------------------------ persist
    def save(self, path: str | Path | None = None) -> Path:
        import joblib

        p = Path(path) if path else Path(os.environ.get("RETAILSENSE_MODEL_DIR", "var/models")) / DEFAULT_MODEL_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "kind": "FootfallForecaster",
                "backend": self.backend,
                "max_iter": self.max_iter,
                "holdout_days": self.holdout_days,
                "seed": self.seed,
                "features": self.features,
                "model": self.model,
                "mae_holdout": self.mae_holdout,
                "mae_baseline": self.mae_baseline,
                "history": self._history,
                "report": self._report.model_dump() if self._report else None,
            },
            p,
        )
        return p

    @classmethod
    def load(cls, path: str | Path | None = None) -> FootfallForecaster:
        import joblib

        p = Path(path) if path else Path(os.environ.get("RETAILSENSE_MODEL_DIR", "var/models")) / DEFAULT_MODEL_FILE
        payload = joblib.load(p)
        if payload.get("kind") != "FootfallForecaster":
            raise ValueError(f"{p} is not a FootfallForecaster artefact")
        obj = cls(
            backend=payload["backend"],
            max_iter=payload["max_iter"],
            holdout_days=payload["holdout_days"],
            seed=payload["seed"],
        )
        obj.features = list(payload["features"])
        obj.model = payload["model"]
        obj.mae_holdout = payload["mae_holdout"]
        obj.mae_baseline = payload["mae_baseline"]
        obj._history = payload["history"]
        obj._report = FitReport.model_validate(payload["report"]) if payload.get("report") else None
        return obj


__all__ = ["DEFAULT_MODEL_FILE", "HOLDOUT_DAYS", "Z_80", "FootfallForecaster"]
