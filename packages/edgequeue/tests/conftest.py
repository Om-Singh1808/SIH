"""Shared fixtures: the canonical demo counter/rules from the contracts and a tiny scenario scripting helper."""

from __future__ import annotations

import pytest
from retailsense_contracts.config import Counter, RulesConfig
from retailsense_contracts.enums import Direction, LineKind
from retailsense_contracts.interfaces import AnalyticsUpdate, Crossing
from retailsense_contracts.testing import sample_store_config

T0 = 1_700_000_000.0  # arbitrary store-day start (epoch seconds)
CAM = "cam-synth"


@pytest.fixture
def counter() -> Counter:
    cfg = sample_store_config()
    return cfg.counters[0]


@pytest.fixture
def rules() -> RulesConfig:
    return sample_store_config().rules


def upd(ts: float, counter: Counter, members: list[int], served: list[int] | None = None) -> AnalyticsUpdate:
    """Build an AnalyticsUpdate with ``members`` inside the queue zone and counter-line IN crossings for ``served``."""
    crossings = [
        Crossing(
            line_id=counter.counter_line_id,
            line_kind=LineKind.COUNTER,
            track_id=tid,
            direction=Direction.IN,
            ts=ts,
        )
        for tid in (served or [])
    ]
    return AnalyticsUpdate(
        ts=ts, camera_id=CAM, zone_members={counter.queue_zone_id: list(members)}, crossings=crossings
    )
