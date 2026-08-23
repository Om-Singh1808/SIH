"""Indian festival calendar features.

Why a festival calendar at all: kirana footfall in India is driven far more by Diwali, Holi, Eid or
Onam than by the day of the week. A forecaster that does not know "Dhanteras is in two days" will
systematically under-predict the pre-festival rush, which is exactly when the owner needs the queue and
reorder predictions most. The calendar itself is owned by the contracts package
(``retailsense_contracts/examples/festivals_in.csv``, with a ``verified`` column so unverified lunar
dates such as Eid are visible); this module only loads it and turns a date into model features.

Design notes
------------
* ``load_festivals`` is cached per path - the CSV is tiny and read by every feature call otherwise.
* ``festival_features(date)`` returns ``(is_festival, weight, days_to_next, name)`` which is the exact
  feature triple used by both the minute-level queue model and the daily footfall model, so the two
  models never disagree about what a "festival" is.
* ``days_to_next`` saturates at :data:`DAYS_TO_NEXT_CAP` (365) past the end of the calendar so the
  feature stays finite and the tree models see "no festival in sight" as one bucket.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from retailsense_contracts.testing import load_festivals_csv

DAYS_TO_NEXT_CAP = 365
"""Upper bound for ``days_to_next`` when the calendar has no future entry."""

SALARY_WEEK_LAST_DAY = 7
"""Days 1..7 of a month are the Indian salary week (most employers pay on the 1st; pensions by the 7th)."""


@dataclass(frozen=True, slots=True)
class Festival:
    """One calendar entry; ``weight`` in [0, 1] is the expected demand uplift strength."""

    date: _dt.date
    name: str
    region: str
    weight: float
    verified: bool


def _coerce_date(value: str | _dt.date | _dt.datetime) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    return _dt.date.fromisoformat(str(value)[:10])


@lru_cache(maxsize=8)
def _load_cached(path: str | None) -> tuple[Festival, ...]:
    rows = load_festivals_csv(path)
    fests = [
        Festival(
            date=_dt.date.fromisoformat(r["date"]),
            name=str(r["name"]),
            region=str(r["region"]),
            weight=float(r["weight"]),
            verified=bool(r["verified"]),
        )
        for r in rows
    ]
    return tuple(sorted(fests, key=lambda f: f.date))


def load_festivals(csv: str | Path | None = None) -> list[Festival]:
    """Return the festival calendar sorted by date (default: contracts ``examples/festivals_in.csv``)."""
    return list(_load_cached(str(csv) if csv else None))


def festival_features(
    date: str | _dt.date | _dt.datetime,
    festivals: list[Festival] | None = None,
) -> tuple[bool, float, int, str | None]:
    """``(is_festival, weight, days_to_next, name)`` for ``date``.

    * ``is_festival`` / ``weight`` / ``name`` describe a festival falling on *this* date (weight 0 if none).
    * ``days_to_next`` counts days until the next festival **including today** (0 on the festival itself),
      capped at :data:`DAYS_TO_NEXT_CAP` when the calendar runs out.
    """
    d = _coerce_date(date)
    fests = festivals if festivals is not None else load_festivals()
    today = [f for f in fests if f.date == d]
    nxt = next((f for f in fests if f.date >= d), None)
    days_to = min(DAYS_TO_NEXT_CAP, (nxt.date - d).days) if nxt else DAYS_TO_NEXT_CAP
    if today:
        # If two festivals share a date (e.g. Holika Dahan regional + national), take the heavier one.
        best = max(today, key=lambda f: f.weight)
        return True, best.weight, days_to, best.name
    return False, 0.0, days_to, None


def is_salary_week(date: str | _dt.date | _dt.datetime) -> bool:
    """True for the 1st..7th of the month - the post-salary spending bump seen in Indian retail."""
    return _coerce_date(date).day <= SALARY_WEEK_LAST_DAY


__all__ = [
    "DAYS_TO_NEXT_CAP",
    "SALARY_WEEK_LAST_DAY",
    "Festival",
    "festival_features",
    "is_salary_week",
    "load_festivals",
]
