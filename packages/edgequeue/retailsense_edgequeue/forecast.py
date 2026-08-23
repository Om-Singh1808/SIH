"""``TrendForecaster``: the edge's own short-horizon queue forecast.

The cloud trains a gradient-boosted model on weeks of history (``retailsense_forecasting``); the edge cannot,
and it must keep warning the owner when the uplink is down. This forecaster is the always-available baseline:

* **Ring buffer** of the last 30 minutes of snapshots, reduced to 1-minute means of count / arrival rate /
  service rate, so that bursts of frame-rate snapshots do not dominate the trend.
* **Extrapolation** ``L(h) = max(0, L + (lambda - mu) * h * 0.85**h + slope * h)`` for h in 5/10/15/30
  minutes, with ``slope`` the least-squares slope (people per minute) of the minute means over the last
  10 minutes (see ``little.trend_forecast``). Every horizon is clamped at zero.
* **Self-scoring**: every edge prediction is stored with its target time; when a later snapshot reaches that
  time the absolute error is recorded, and ``mae_recent`` is the mean of the last ``MAE_SAMPLES`` errors.
  The board shows it next to the forecast so the owner can see how trustworthy the number is.
* **Cloud override**: ``set_cloud_forecast()`` installs the cloud's forecast and ``predict()`` returns it
  (``model="cloud_gbm"``) while it is younger than ``CLOUD_TTL_S`` (2 min). After that the edge silently
  falls back to its own trend, so an uplink cut never freezes a stale cloud number on the dashboard.

Time handling
-------------
``QueueSnapshot`` carries no timestamp (the ``Event`` envelope does) and the ``EdgeQueueForecaster`` Protocol's
``observe(snap)`` has no ``ts`` argument. The forecaster therefore takes a ``Clock`` (``SystemClock`` by
default, the demo's ``SimClock`` when wired) to stamp ``observe()`` calls, and offers ``observe_at(ts, snap)``
for callers that already know the observation time (preferred; deterministic in tests). The cloud lease is
measured against the ``ts`` passed to ``predict()``, starting from the ``received_ts`` given to
``set_cloud_forecast`` (default: the clock's now). Keep the clock and the ``predict`` timestamps on the same
time base.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from retailsense_contracts.clock import Clock, SystemClock
from retailsense_contracts.events import QueueForecast, QueueSnapshot

from .little import linear_slope, trend_forecast

HORIZONS_MIN: tuple[int, ...] = (5, 10, 15, 30)
BUFFER_S = 30 * 60  # ring-buffer span (seconds)
SLOPE_WINDOW_MIN = 10  # minutes of minute-means used for the slope
CLOUD_TTL_S = 120.0  # cloud forecast lease (seconds)
MAE_SAMPLES = 20  # errors averaged into mae_recent
MAX_PENDING = 400  # safety bound on stored, not yet scored predictions


@dataclass(slots=True)
class _Sample:
    ts: float
    count: float
    arrival_pm: float
    service_pm: float


@dataclass(slots=True)
class _Prediction:
    target_ts: float
    value: float


class TrendForecaster:
    """Satisfies ``retailsense_contracts.interfaces.EdgeQueueForecaster``."""

    def __init__(
        self,
        clock: Clock | None = None,
        cloud_ttl_s: float = CLOUD_TTL_S,
        buffer_s: float = BUFFER_S,
    ) -> None:
        self.clock: Clock = clock or SystemClock()
        self.cloud_ttl_s = cloud_ttl_s
        self.buffer_s = buffer_s
        self._buf: deque[_Sample] = deque()
        self._last: QueueSnapshot | None = None
        self._pending: deque[_Prediction] = deque()
        self._errors: deque[float] = deque(maxlen=MAE_SAMPLES)
        self._cloud: QueueForecast | None = None
        self._cloud_received_ts: float | None = None

    # ------------------------------------------------------------------ protocol

    def observe(self, snap: QueueSnapshot) -> None:
        """Protocol entry point: observe ``snap`` at the clock's current time."""
        self.observe_at(self.clock.now(), snap)

    def predict(self, ts: float) -> QueueForecast | None:
        """Forecast at ``ts``: the cloud's while its lease is valid, else the edge trend; ``None`` before data."""
        cloud = self._valid_cloud(ts)
        if cloud is not None:
            return cloud
        if self._last is None or not self._buf:
            return None
        horizons = self._edge_horizons()
        for h, value in horizons.items():
            self._pending.append(_Prediction(ts + int(h) * 60.0, value))
        while len(self._pending) > MAX_PENDING:
            self._pending.popleft()
        return QueueForecast(
            counter_id=self._last.counter_id,
            made_ts=ts,
            horizons=horizons,
            model="edge_trend",
            mae_recent=self.mae_recent,
        )

    def set_cloud_forecast(self, fc: QueueForecast, received_ts: float | None = None) -> None:
        """Install a cloud forecast; it takes precedence for ``cloud_ttl_s`` seconds from ``received_ts``."""
        if fc.model != "cloud_gbm":
            # Only a genuine cloud forecast may override; an edge_trend echo would mask our own self-scoring.
            fc = fc.model_copy(update={"model": "cloud_gbm"})
        self._cloud = fc
        self._cloud_received_ts = self.clock.now() if received_ts is None else received_ts

    # ------------------------------------------------------------------ extra API

    def observe_at(self, ts: float, snap: QueueSnapshot) -> None:
        """Observe a snapshot with an explicit timestamp; scores predictions whose target time has passed."""
        self._last = snap
        self._buf.append(_Sample(ts, float(snap.count), snap.arrival_rate_pm, snap.service_rate_pm))
        horizon = ts - self.buffer_s
        while self._buf and self._buf[0].ts < horizon:
            self._buf.popleft()
        self._score(ts, float(snap.count))

    @property
    def mae_recent(self) -> float | None:
        """Mean absolute error over the most recent scored predictions (``None`` until one is scored)."""
        if not self._errors:
            return None
        return round(sum(self._errors) / len(self._errors), 3)

    @property
    def cloud_active_until(self) -> float | None:
        """Timestamp at which the current cloud lease expires, ``None`` when no cloud forecast is installed."""
        if self._cloud is None or self._cloud_received_ts is None:
            return None
        return self._cloud_received_ts + self.cloud_ttl_s

    # ------------------------------------------------------------------ internals

    def _valid_cloud(self, ts: float) -> QueueForecast | None:
        if self._cloud is None or self._cloud_received_ts is None:
            return None
        if ts - self._cloud_received_ts >= self.cloud_ttl_s:
            self._cloud = None
            self._cloud_received_ts = None
            return None
        return self._cloud

    def _minute_means(self) -> list[_Sample]:
        """Collapse the buffer into per-minute means, ordered by time."""
        buckets: dict[int, list[_Sample]] = {}
        for s in self._buf:
            buckets.setdefault(int(s.ts // 60), []).append(s)
        out: list[_Sample] = []
        for minute in sorted(buckets):
            xs = buckets[minute]
            n = len(xs)
            out.append(
                _Sample(
                    ts=minute * 60.0,
                    count=sum(x.count for x in xs) / n,
                    arrival_pm=sum(x.arrival_pm for x in xs) / n,
                    service_pm=sum(x.service_pm for x in xs) / n,
                )
            )
        return out

    def _edge_horizons(self) -> dict[str, float]:
        recent = self._minute_means()[-SLOPE_WINDOW_MIN:]
        slope = linear_slope([m.ts / 60.0 for m in recent], [m.count for m in recent])
        latest = self._buf[-1]
        return {
            str(h): round(trend_forecast(latest.count, latest.arrival_pm, latest.service_pm, slope, h), 2)
            for h in HORIZONS_MIN
        }

    def _score(self, ts: float, realised: float) -> None:
        """Score every stored prediction whose target time has been reached.

        Predictions are appended per ``predict()`` call (four horizons at once) so the deque is not sorted by
        target time; scan it fully rather than only its head.
        """
        if not self._pending:
            return
        due = [p for p in self._pending if p.target_ts <= ts]
        if not due:
            return
        self._pending = deque(p for p in self._pending if p.target_ts > ts)
        for p in sorted(due, key=lambda p: p.target_ts):
            self._errors.append(abs(p.value - realised))
