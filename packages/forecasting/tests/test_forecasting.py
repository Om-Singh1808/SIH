"""D12 acceptance tests for retailsense_forecasting (names match the spec)."""

from __future__ import annotations

import datetime as dt
import sys
import time

import numpy as np
import pandas as pd
import pytest
from retailsense_contracts.api import FitReport, FootfallForecast, FootfallForecastDay
from retailsense_contracts.interfaces import CloudFootfallForecaster, CloudQueueForecaster
from retailsense_contracts.testing import fake_suggest_reorder

from retailsense_forecasting import (
    HORIZONS,
    QUEUE_FEATURE_COLUMNS,
    FootfallForecaster,
    QueueForecaster,
    festival_features,
    is_salary_week,
    load_festivals,
    make_queue_features,
    rolling_mae,
    suggest_reorder,
    target_column,
)
from retailsense_forecasting import _backend
from retailsense_forecasting.features import QUEUE_LAGS, make_daily_features
from retailsense_forecasting.reorder import footfall_uplift, open_hours_per_day

# ---------------------------------------------------------------------------
# festivals


def test_festival_features_and_days_to():
    fests = load_festivals()
    names = {f.name for f in fests}
    assert any("Diwali" in n for n in names) and any("Holi" in n for n in names)
    assert all(f.verified in (True, False) for f in fests)
    # Unverified lunar dates (Eid) are flagged, not hidden.
    assert any(not f.verified and "Eid" in f.name for f in fests)

    is_f, w, d2n, name = festival_features("2026-11-08")
    assert is_f and w == 1.0 and d2n == 0 and name == "Diwali"
    is_f, w, d2n, name = festival_features(dt.date(2026, 11, 5))
    assert not is_f and w == 0.0 and name is None
    assert d2n == 1  # Dhanteras 6 Nov is the next one
    # After the calendar ends the feature saturates instead of failing.
    assert festival_features("2031-01-01")[2] == 365
    # datetime inputs work too
    assert festival_features(dt.datetime(2026, 3, 4, 17, 0))[3] == "Holi"


def test_salary_week():
    assert is_salary_week("2026-11-01")
    assert is_salary_week(dt.date(2026, 11, 7))
    assert not is_salary_week("2026-11-08")
    assert not is_salary_week(dt.datetime(2026, 11, 30, 23, 59))


# ---------------------------------------------------------------------------
# features


def test_queue_features_shapes_no_leak(history):
    minute_df, _ = history
    feat = make_queue_features(minute_df, with_targets=True)
    assert len(feat) == len(minute_df)
    for c in QUEUE_FEATURE_COLUMNS:
        assert c in feat.columns, c
    for h in HORIZONS:
        assert target_column(h) in feat.columns

    q = feat["queue_count"].to_numpy("float64")
    # lags look strictly backwards ...
    for k in QUEUE_LAGS:
        lag = feat[f"queue_lag_{k}"].to_numpy()
        assert np.isnan(lag[:k]).all()
        assert np.allclose(lag[k:], q[:-k])
    # ... rolling means use only t and earlier ...
    roll5 = feat["queue_roll_5"].to_numpy()
    assert np.isclose(roll5[10], q[6:11].mean())
    # ... and targets look strictly forwards (shift -h), NaN at the tail.
    for h in HORIZONS:
        y = feat[target_column(h)].to_numpy()
        assert np.isnan(y[-h:]).all()
        assert np.allclose(y[:-h], q[h:])
    # No target columns on the inference path.
    inf = make_queue_features(minute_df.tail(30))
    assert not any(c.startswith("y_") for c in inf.columns)

    # Multi-counter: lags never cross counters.
    two = pd.concat([minute_df.head(100), minute_df.head(100).assign(counter_id="counter-2")])
    f2 = make_queue_features(two)
    assert f2.groupby("counter_id")["queue_lag_1"].apply(lambda s: s.isna().sum()).eq(1).all()

    # Calendar + festival columns are derived from ts when absent (live API frames are minimal).
    minimal = minute_df[["ts", "queue_count"]].tail(60)
    f3 = make_queue_features(minimal)
    assert set(QUEUE_FEATURE_COLUMNS) <= set(f3.columns)
    assert (f3["hour"].to_numpy() == minute_df["hour"].tail(60).to_numpy()).all()
    assert (f3["is_festival"].to_numpy() == minute_df["is_festival"].tail(60).astype(int).to_numpy()).all()


def test_daily_features_lagged(history):
    _, daily = history
    f = make_daily_features(daily, with_target=True)
    fi = f["footfall_in"].to_numpy("float64")
    assert np.allclose(f["footfall_lag_1"].to_numpy()[1:], fi[:-1])
    assert np.allclose(f["footfall_lag_7"].to_numpy()[7:], fi[:-7])
    assert np.isclose(f["footfall_roll_7"].iloc[8], fi[1:8].mean())
    assert np.allclose(f["y"].to_numpy(), fi)


# ---------------------------------------------------------------------------
# queue forecaster


def test_queue_forecaster_beats_baseline(history):
    minute_df, _ = history
    qf = QueueForecaster(backend="sklearn")
    t0 = time.perf_counter()
    report = qf.fit(minute_df)
    elapsed = time.perf_counter() - t0
    assert isinstance(report, FitReport)
    assert report.mae_holdout < report.mae_baseline, (report.mae_holdout, report.mae_baseline)
    assert report.mae_holdout <= 1.0  # "MAE <= 1.0 customers on the sim history"
    assert all(qf.mae_by_horizon[h] < qf.baseline_by_horizon[h] for h in HORIZONS)
    assert report.horizons == list(HORIZONS) and report.n_rows == len(minute_df)
    assert report.model.startswith("cloud_gbm")
    assert elapsed < 5.0, f"fit took {elapsed:.1f}s"
    assert isinstance(qf, CloudQueueForecaster)
    assert qf.report() is report


def test_predict_keys_and_nonneg(fitted_queue, history):
    qf, _ = fitted_queue
    minute_df, _ = history
    recent = minute_df.tail(30)
    now_ts = float(recent["ts"].iloc[-1])
    out = qf.predict(recent, now_ts)
    assert set(out) == {"5", "10", "15", "30"}
    assert all(isinstance(v, float) and v >= 0.0 for v in out.values())

    # Busy evening window in the middle of history -> a sane, non-negative, non-trivial forecast.
    evening = minute_df[(minute_df["hour"] == 19)].iloc[-60:-30]
    out2 = qf.predict(evening, float(evening["ts"].iloc[-1]))
    assert all(v >= 0.0 for v in out2.values())
    assert out2["5"] <= evening["queue_count"].max() + 5

    # Minimal frame without ts/calendar columns still works.
    out3 = qf.predict(pd.DataFrame({"queue_count": [2, 3, 3, 4]}), now_ts)
    assert set(out3) == {"5", "10", "15", "30"} and all(v >= 0 for v in out3.values())

    with pytest.raises(RuntimeError):
        QueueForecaster(backend="sklearn").predict(recent, now_ts)


def test_queue_forecaster_save_load(fitted_queue, history, tmp_path):
    qf, report = fitted_queue
    minute_df, _ = history
    p = qf.save(tmp_path / "models" / "q.joblib")
    assert p.exists()
    loaded = QueueForecaster.load(p)
    recent = minute_df.tail(30)
    now_ts = float(recent["ts"].iloc[-1])
    assert loaded.predict(recent, now_ts) == qf.predict(recent, now_ts)
    assert loaded.report() == report


def test_predict_frame_holdout_mae_matches_report(fitted_queue, history):
    """Batch evaluation on the holdout reproduces the FitReport number (no train/serve skew)."""
    qf, report = fitted_queue
    minute_df, _ = history
    cutoff = minute_df["ts"].max() - 3 * 86400
    hold = minute_df[minute_df["ts"] > cutoff]
    # predict_frame needs the preceding 15 minutes for lags, so take a bit more and trim.
    ctx = minute_df[minute_df["ts"] > cutoff - 15 * 60]
    pf = qf.predict_frame(ctx)
    pf = pf[pf["ts"] > cutoff].reset_index(drop=True)
    q = hold["queue_count"].to_numpy("float64")
    pred5 = pf["pred_5"].to_numpy()[:-5]
    mae5 = float(np.mean(np.abs(pred5 - q[5:])))
    assert abs(mae5 - qf.mae_by_horizon[5]) < 0.02


# ---------------------------------------------------------------------------
# footfall forecaster


def test_footfall_forecaster_band(fitted_footfall, history):
    ff, report = fitted_footfall
    _, daily = history
    assert isinstance(ff, CloudFootfallForecaster)
    assert report.target == "footfall_in" and report.n_rows == len(daily)
    assert report.mae_holdout >= 0

    days = ff.predict_days("2026-11-11", 7)
    assert len(days) == 7 and all(isinstance(d, FootfallForecastDay) for d in days)
    band = 1.28 * ff.mae_holdout
    for d in days:
        assert d.predicted >= 0 and d.lower <= d.predicted <= d.upper
        assert d.upper - d.predicted == pytest.approx(band, abs=0.11)
        assert d.predicted - d.lower == pytest.approx(min(band, d.predicted), abs=0.11)
    assert [d.date for d in days][0] == "2026-11-11"
    # Festival flags ride along for the chart markers.
    chhath = ff.predict_days("2026-11-15", 1)[0]
    assert chhath.is_festival and chhath.festival_name == "Chhath Puja" and chhath.days_to_festival == 0
    # Forecast magnitude is in the right ballpark of the history.
    mean_hist = daily["footfall_in"].mean()
    assert 0.4 * mean_hist < np.mean([d.predicted for d in days]) < 2.0 * mean_hist
    assert ff.average_daily_footfall() > 0
    assert ff.predict_days("2026-11-11", 0) == []


def test_footfall_save_load(fitted_footfall, tmp_path):
    ff, _ = fitted_footfall
    p = ff.save(tmp_path / "f.joblib")
    loaded = FootfallForecaster.load(p)
    assert [d.predicted for d in loaded.predict_days("2026-11-11", 3)] == [
        d.predicted for d in ff.predict_days("2026-11-11", 3)
    ]


# ---------------------------------------------------------------------------
# reorder


def test_reorder_math(cfg):
    stock = {"Amul Taaza 500ml": 48, "Parle-G 70g": 120, "Fortune Sunflower 1L": 18}
    fc = FootfallForecast(store_id=cfg.store.store_id, made_ts=0.0, days=[], mae_holdout=None)
    out = suggest_reorder(cfg, fc, stock, None)
    by = {s.sku_id: s for s in out}
    assert open_hours_per_day(cfg) == 14
    amul = by["AMUL-TAAZA-500"]
    # Amul: 18/hr x 14 h x 1 d x 1.0 = 252 demand, safety 0.5 day = 126, minus 48 -> 330
    assert amul.forecast_units_lead == 252.0 and amul.safety_stock == 126.0
    assert amul.suggest_qty == 330 and amul.system_units == 48 and amul.visual_units is None
    assert amul.est_cost_inr == pytest.approx(330 * 27 * 0.92, abs=0.01)
    assert "18/hr" in amul.reason and "स" not in amul.reason
    assert amul.name_hi.startswith("अमूल")
    # Same numbers as the contracts fake when uplift is 1.0.
    fake = {s.sku_id: s.suggest_qty for s in fake_suggest_reorder(cfg, fc, stock, None)}
    assert fake == {k: v.suggest_qty for k, v in by.items()}

    # Overstock -> zero, never negative.
    out2 = suggest_reorder(cfg, None, {"Amul Taaza 500ml": 10_000}, None)
    assert {s.sku_id: s.suggest_qty for s in out2}["AMUL-TAAZA-500"] == 0

    # Visual count is the fallback when the ERP is not connected.
    out3 = suggest_reorder(cfg, None, None, {"AMUL-TAAZA-500": 12})
    a3 = {s.sku_id: s for s in out3}["AMUL-TAAZA-500"]
    assert a3.visual_units == 12 and a3.system_units is None and a3.suggest_qty == 252 + 126 - 12

    # Festival uplift scales demand: forecast 1.5x the average footfall -> demand 378.
    days = [
        FootfallForecastDay(
            date="2026-11-08", predicted=1500.0, lower=0, upper=0, is_festival=True, festival_name="Diwali", days_to_festival=0
        )
    ]
    fc2 = FootfallForecast(store_id="s", made_ts=0.0, days=days, mae_holdout=None)
    assert footfall_uplift(fc2, 1, 1000.0) == 1.5
    a4 = {s.sku_id: s for s in suggest_reorder(cfg, fc2, stock, None, avg_daily_footfall=1000.0)}["AMUL-TAAZA-500"]
    assert a4.forecast_units_lead == 378.0 and a4.suggest_qty == 378 + 126 - 48


# ---------------------------------------------------------------------------
# optional backend


def test_lightgbm_optional(monkeypatch):
    # Default never hard-requires lightgbm ...
    assert "lightgbm" not in sys.modules or _backend.lightgbm_available()
    assert _backend.choose_backend("sklearn") == "sklearn"
    # ... "auto" resolves to whichever is importable ...
    auto = _backend.choose_backend("auto")
    assert auto == ("lightgbm" if _backend.lightgbm_available() else "sklearn")
    # ... and explicitly asking for an unavailable lightgbm fails loudly, never silently.
    monkeypatch.setattr(_backend, "lightgbm_available", lambda: False)
    assert _backend.choose_backend("auto") == "sklearn"
    with pytest.raises(ImportError):
        _backend.choose_backend("lightgbm")
    qf = QueueForecaster(backend="auto")
    assert qf.backend == "sklearn"
    with pytest.raises(ValueError):
        _backend.choose_backend("xgboost")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# eval


def test_rolling_mae():
    rows = [
        {"made_ts": 100.0, "horizon_min": 5, "predicted": 3.0, "actual": 2.0, "model": "cloud_gbm"},
        {"made_ts": 100.0, "horizon_min": 10, "predicted": 4.0, "actual": 6.0, "model": "cloud_gbm"},
        {"made_ts": 10.0, "horizon_min": 5, "predicted": 0.0, "actual": 10.0, "model": "edge_trend"},
        {"made_ts": 100.0, "horizon_min": 5, "predicted": 1.0, "actual": None, "model": "cloud_gbm"},  # unscored
    ]
    r = rolling_mae(rows)
    assert r["n"] == 3 and r["mae"] == pytest.approx((1 + 2 + 10) / 3)
    assert r["by_horizon"] == {"5": 5.5, "10": 2.0}
    assert r["by_model"]["edge_trend"] == 10.0
    assert rolling_mae(rows, window_s=50, now_ts=120.0)["n"] == 2
    assert rolling_mae(rows, model="cloud_gbm", horizon_min=5)["mae"] == 1.0
    assert rolling_mae([])["mae"] is None

    class Row:  # attribute-style rows (dataclass / ORM)
        def __init__(self, **kw):
            self.__dict__.update(kw)

    assert rolling_mae([Row(made_ts=1, horizon_min=5, predicted=2, actual=3, model="m")])["mae"] == 1.0
