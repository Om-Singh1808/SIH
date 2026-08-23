"""Pure queueing math shared by the analyzer and the forecaster. No I/O, no state, easy to unit test.

Why ``W = L / mu`` and not ``W = L / lambda``
--------------------------------------------
Little's Law states ``L = lambda * W`` for a *stable* system observed over a long window: the average number
in the system equals the arrival rate times the average time spent. Rearranged, ``W = L / lambda``. On a shop
floor that form is misleading for the question the owner actually asks ("how long will the person joining
*now* wait?"):

* When a rush starts, ``lambda`` spikes while the cashier's throughput ``mu`` does not, so ``L / lambda``
  *shrinks* exactly when waits are growing.
* When arrivals stop (closing time) ``lambda -> 0`` and ``L / lambda`` explodes although the queue drains at
  the cashier's normal pace.

The quantity that governs how fast the line in front of a new arrival disappears is the *service* rate:
with ``L`` people ahead and the cashier completing ``mu`` customers per minute, the expected wait is
``L / mu``. That is the "little_service" estimate. It is still Little's Law -- applied to the sub-system
"people ahead of me" whose departure rate is ``mu`` -- but it uses the rate that is causally linked to
the wait. ``lambda`` is still reported in the snapshot (``arrival_rate_pm``) because ``lambda - mu`` is the
growth rate the forecaster extrapolates.

All rates here are per minute and all waits in seconds, matching the ``QueueSnapshot`` contract.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

# Below this many completions per minute (one every five minutes) the service rate is too noisy to divide by;
# the analyzer falls back to observed waits or the configured default service time.
MIN_SERVICE_RATE_PM = 0.2

# Damping applied per minute of horizon to the (arrival - service) imbalance: rushes do not persist
# unchanged, cashiers speed up, extra counters open. 0.85 ** 30 ~ 0.008, so the 30-minute value is
# essentially "current count + slope".
TREND_DAMPING = 0.85


def little_wait_s(count: int, service_rate_pm: float) -> float:
    """Expected wait in seconds for ``count`` people ahead served at ``service_rate_pm`` per minute.

    ``W = L / mu`` (see module docstring). Raises ``ValueError`` when the rate is not usable; callers should
    check :data:`MIN_SERVICE_RATE_PM` first and fall back instead of catching.
    """
    if service_rate_pm <= 0 or not math.isfinite(service_rate_pm):
        raise ValueError("service_rate_pm must be positive and finite")
    return max(0, count) * 60.0 / service_rate_pm


def rolling_rate_pm(event_ts: Iterable[float], now_ts: float, window_s: float, elapsed_s: float) -> tuple[int, float]:
    """Count events inside ``(now - window_s, now]`` and convert to a per-minute rate.

    ``elapsed_s`` is how long the observation has been running (time since the store day started). Before the
    window has filled the denominator is the elapsed time rather than the full window, so that three customers
    served in the first two minutes read as 1.5/min and not 0.3/min. The denominator is floored at one minute
    to avoid absurd rates in the first seconds of the day.

    Returns ``(events_in_window, rate_per_minute)``.
    """
    in_window = sum(1 for t in event_ts if now_ts - window_s < t <= now_ts)
    denom_min = max(1.0, min(window_s, max(elapsed_s, 0.0)) / 60.0)
    return in_window, in_window / denom_min


def linear_slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Least-squares slope of ``ys`` over ``xs`` (0.0 when fewer than two distinct x)."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / sxx


def trend_forecast(
    count: float,
    arrival_rate_pm: float,
    service_rate_pm: float,
    slope_per_min: float,
    horizon_min: float,
    damping: float = TREND_DAMPING,
) -> float:
    """Damped fluid-flow extrapolation of the queue length ``h`` minutes ahead.

    ``L(h) = max(0, L + (lambda - mu) * h * damping**h + slope * h)``

    * ``(lambda - mu) * h`` is the fluid queue growth if current rates persisted; ``damping**h`` decays that
      assumption with horizon.
    * ``slope * h`` carries the recently *observed* count trend, which already embeds effects the rates miss
      (e.g. a second counter opening).
    * The queue cannot be negative; the floor is applied per horizon so the series stays well-formed.
    """
    h = max(0.0, horizon_min)
    imbalance = (arrival_rate_pm - service_rate_pm) * h * (damping**h)
    return max(0.0, count + imbalance + slope_per_min * h)
