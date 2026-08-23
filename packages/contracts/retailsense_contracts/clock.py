"""Clocks and store-day helpers.

Everything in RetailSense that needs "now" takes a ``Clock`` so the synthetic
store can run at 10x (``SimClock``) while the edge, rules, forecaster and tests
stay oblivious.  Timestamps are epoch seconds UTC as float; store-day arithmetic
uses the store's IANA timezone (``Asia/Kolkata`` by default) via ``zoneinfo``
(the ``tzdata`` wheel makes this work on Windows).
"""

import datetime as _dt
import time
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Kolkata"


@runtime_checkable
class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    """Wall clock (``time.time``)."""

    def now(self) -> float:
        return time.time()


class SimClock:
    """A clock that starts at ``start_ts`` and runs ``factor`` times faster than real time.

    ``set()`` jumps to an absolute simulated time; ``advance()`` moves forward by
    simulated seconds.  Both are used by tests to make time deterministic.
    """

    def __init__(self, start_ts: float, factor: float = 1.0):
        self.start_ts = float(start_ts)
        self.factor = float(factor)
        self._anchor_sim = float(start_ts)
        self._anchor_real = time.monotonic()

    def now(self) -> float:
        return self._anchor_sim + (time.monotonic() - self._anchor_real) * self.factor

    def set(self, ts: float) -> None:
        self._anchor_sim = float(ts)
        self._anchor_real = time.monotonic()

    def advance(self, dt: float) -> None:
        self.set(self.now() + float(dt))

    def set_factor(self, factor: float) -> None:
        """Change speed without jumping in simulated time."""
        self.set(self.now())
        self.factor = float(factor)


class FrozenClock:
    """A clock that only moves when told to (pure unit tests)."""

    def __init__(self, ts: float = 0.0):
        self._ts = float(ts)

    def now(self) -> float:
        return self._ts

    def set(self, ts: float) -> None:
        self._ts = float(ts)

    def advance(self, dt: float) -> None:
        self._ts += float(dt)


def tz(name: str | None = None) -> ZoneInfo:
    return ZoneInfo(name or DEFAULT_TZ)


def store_date(ts: float, tz_name: str = DEFAULT_TZ) -> str:
    """ISO ``YYYY-MM-DD`` of ``ts`` in the store's timezone."""
    return _dt.datetime.fromtimestamp(ts, tz(tz_name)).date().isoformat()


def day_start_ts(ts: float, tz_name: str = DEFAULT_TZ) -> float:
    """Epoch seconds of local midnight that starts the store-day containing ``ts``."""
    local = _dt.datetime.fromtimestamp(ts, tz(tz_name))
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()


def date_to_ts(date: str, tz_name: str = DEFAULT_TZ, hhmm: str = "00:00") -> float:
    """Epoch seconds of ``date`` at ``hhmm`` local time."""
    h, m = (int(x) for x in hhmm.split(":"))
    local = _dt.datetime.fromisoformat(date).replace(hour=h, minute=m, tzinfo=tz(tz_name))
    return local.timestamp()


def hour_bucket(ts: float) -> int:
    """Heatmap hour bucket: ``floor(ts / 3600)`` (UTC hours)."""
    return int(ts // 3600)


__all__ = [
    "DEFAULT_TZ",
    "Clock",
    "FrozenClock",
    "SimClock",
    "SystemClock",
    "date_to_ts",
    "day_start_ts",
    "hour_bucket",
    "store_date",
    "tz",
]
