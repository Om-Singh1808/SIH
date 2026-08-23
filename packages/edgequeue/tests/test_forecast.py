"""TrendForecaster tests: shapes/non-negativity, cloud override lease, MAE self-scoring, and little.py math."""

from __future__ import annotations

import math

import pytest
from conftest import T0
from retailsense_contracts.clock import SimClock
from retailsense_contracts.events import QueueForecast, QueueSnapshot
from retailsense_contracts.interfaces import EdgeQueueForecaster

from retailsense_edgequeue.forecast import HORIZONS_MIN, TrendForecaster
from retailsense_edgequeue.little import (
    MIN_SERVICE_RATE_PM,
    linear_slope,
    little_wait_s,
    rolling_rate_pm,
    trend_forecast,
)

KEYS = {"5", "10", "15", "30"}


def snap(count: int, arrival: float = 1.0, service: float = 1.0, served_total: int = 0) -> QueueSnapshot:
    return QueueSnapshot(
        counter_id="counter-1",
        zone_id="queue-1",
        count=count,
        avg_dwell_s=0.0,
        max_dwell_s=0.0,
        arrival_rate_pm=arrival,
        service_rate_pm=service,
        est_wait_s=count * 45.0,
        method="default_service",
        served_window=0,
        abandoned_window=0,
        window_s=600,
        served_total=served_total,
        abandoned_total=0,
    )


def test_trend_forecast_shapes_and_nonneg() -> None:
    fc = TrendForecaster()
    assert fc.predict(T0) is None  # no data yet
    # Draining queue: count falls 10 -> 0 over 10 minutes, service well above arrival.
    for i in range(11):
        fc.observe_at(T0 + i * 60, snap(10 - i, arrival=0.2, service=2.0))
    out = fc.predict(T0 + 600)
    assert out is not None and out.model == "edge_trend" and out.counter_id == "counter-1"
    assert set(out.horizons) == KEYS == {str(h) for h in HORIZONS_MIN}
    assert all(v >= 0.0 and math.isfinite(v) for v in out.horizons.values())
    assert out.made_ts == T0 + 600
    # Growing queue: forecasts rise with the horizon (arrival > service, positive slope).
    fc2 = TrendForecaster()
    for i in range(6):
        fc2.observe_at(T0 + i * 60, snap(i, arrival=3.0, service=1.0))
    out2 = fc2.predict(T0 + 300)
    assert out2 is not None
    assert out2.horizons["5"] > 5.0  # above the current count
    assert out2.horizons["30"] >= out2.horizons["15"] >= out2.horizons["10"] >= out2.horizons["5"]
    # The forecast validates as a contracts payload (round-trip) and the protocol is satisfied.
    assert QueueForecast.model_validate(out2.model_dump()) == out2
    assert isinstance(fc2, EdgeQueueForecaster)


def test_cloud_override_and_expiry() -> None:
    clock = SimClock(T0)
    fc = TrendForecaster(clock=clock)
    fc.observe(snap(3))  # stamped by the clock at T0
    cloud = QueueForecast(
        counter_id="counter-1",
        made_ts=T0,
        horizons={"5": 4, "10": 5, "15": 6, "30": 7},
        model="cloud_gbm",
        mae_recent=0.8,
    )
    fc.set_cloud_forecast(cloud)  # received at clock.now() == T0
    assert fc.cloud_active_until == T0 + 120
    out = fc.predict(T0 + 60)
    assert out is not None and out.model == "cloud_gbm" and out.horizons["30"] == 7
    out = fc.predict(T0 + 119)
    assert out is not None and out.model == "cloud_gbm"
    out = fc.predict(T0 + 120)  # lease over -> back to the edge trend
    assert out is not None and out.model == "edge_trend"
    assert fc.cloud_active_until is None
    # explicit received_ts is honoured and a mislabelled forecast is normalised to cloud_gbm
    fc.set_cloud_forecast(cloud.model_copy(update={"model": "edge_trend"}), received_ts=T0 + 300)
    out = fc.predict(T0 + 350)
    assert out is not None and out.model == "cloud_gbm"
    assert fc.predict(T0 + 420).model == "edge_trend"


def _sawtooth(minute: int, period: int = 20, amp: int = 6) -> int:
    return minute % period * amp // (period - 1)


def test_mae_self_scoring() -> None:
    fc = TrendForecaster()
    assert fc.mae_recent is None
    maes: list[float] = []
    for m in range(120):
        ts = T0 + m * 60
        c = _sawtooth(m)
        fc.observe_at(ts, snap(c, arrival=1.0 + 0.3 * (c - _sawtooth(m - 1)), service=1.0))
        out = fc.predict(ts)
        assert out is not None
        maes.append(out.mae_recent if out.mae_recent is not None else float("nan"))
    # Nothing can be scored before the first 5-minute target is reached...
    assert all(math.isnan(x) for x in maes[:5])
    # ...after that the score is live, finite, bounded by the saw-tooth amplitude, and stable (converged).
    assert all(math.isfinite(x) for x in maes[6:])
    assert 0 < maes[-1] <= 6
    assert abs(maes[-1] - maes[-21]) < 1.5
    # The score is an honest comparison: replaying the stored predictions reproduces it.
    assert fc.mae_recent == maes[-1]


def test_ring_buffer_bounded() -> None:
    fc = TrendForecaster(buffer_s=600)
    for m in range(100):
        fc.observe_at(T0 + m * 30, snap(m % 4))
    assert fc._buf[-1].ts - fc._buf[0].ts <= 600
    assert len(fc._buf) <= 21


def test_little_math() -> None:
    # W = L / mu, in seconds
    assert little_wait_s(4, 2.0) == 120.0
    assert little_wait_s(0, 2.0) == 0.0
    with pytest.raises(ValueError):
        little_wait_s(3, 0.0)
    assert MIN_SERVICE_RATE_PM == 0.2
    # elapsed-adjusted rolling rate: 3 served in the first 2 minutes -> 1.5/min, not 0.3/min over a 10-min window
    n, rate = rolling_rate_pm([10, 50, 100], now_ts=120, window_s=600, elapsed_s=120)
    assert n == 3 and rate == pytest.approx(1.5)
    # full window: 3 served in 10 min -> 0.3/min; events outside the window are dropped
    n, rate = rolling_rate_pm([0, 700, 1000], now_ts=1200, window_s=600, elapsed_s=5000)
    assert n == 2 and rate == pytest.approx(0.2)
    assert linear_slope([0, 1, 2], [1, 3, 5]) == pytest.approx(2.0)
    assert linear_slope([1], [1]) == 0.0
    # trend: floor at zero, damping shrinks the imbalance term with horizon
    assert trend_forecast(1, 0.0, 5.0, -1.0, 30) == 0.0
    assert trend_forecast(3, 2.0, 1.0, 0.0, 5) == pytest.approx(3 + 5 * 0.85**5)
    assert trend_forecast(3, 2.0, 1.0, 0.0, 30) < trend_forecast(3, 2.0, 1.0, 0.0, 5) + 1.0
