"""Shared fixtures: the canonical demo store, a ZoneEngine factory and a track-walk helper."""

from __future__ import annotations

import numpy as np
import pytest
from retailsense_contracts.config import StoreConfig
from retailsense_contracts.interfaces import Track
from retailsense_contracts.testing import IdentityMapper, sample_store_config

from retailsense_edgeanalytics import ZoneEngine

DT = 0.25  # synthetic camera frame interval (4 fps)
T0 = 1_700_000_000.0


def track(tid: int, x: float, y: float, *, size: float = 20.0, confirmed: bool = True) -> Track:
    """A confirmed track whose *centre* anchor is at (x, y) (demo camera uses anchor=center)."""
    h = size / 2
    return Track(
        track_id=tid,
        bbox=(x - h, y - h, x + h, y + h),
        conf=0.9,
        age=5,
        hits=5,
        time_since_update=0,
        confirmed=confirmed,
    )


@pytest.fixture(scope="session")
def cfg() -> StoreConfig:
    return sample_store_config()


@pytest.fixture
def make_engine(cfg):
    def _make(mapper=None, **kw) -> ZoneEngine:
        return ZoneEngine(
            cfg.cameras[0], cfg.zones, cfg.lines, mapper or IdentityMapper(), cfg.rules, cfg.floorplan, **kw
        )

    return _make


def walk(engine: ZoneEngine, points: list[tuple[float, float]], *, tid: int = 1, t0: float = T0, dt: float = DT):
    """Feed one track through ``points`` one frame apart; returns every AnalyticsUpdate."""
    return [engine.update([track(tid, x, y)], t0 + i * dt) for i, (x, y) in enumerate(points)]


class ScaleMapper:
    """PointMapper that scales image px by a constant (stands in for a homography)."""

    def __init__(self, s: float) -> None:
        self.s = float(s)

    def to_floor(self, pts: np.ndarray) -> np.ndarray:
        return np.asarray(pts, dtype=np.float64).reshape(-1, 2) * self.s

    def to_image(self, pts: np.ndarray) -> np.ndarray:
        return np.asarray(pts, dtype=np.float64).reshape(-1, 2) / self.s
