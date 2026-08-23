"""Shared fixtures: one synthetic 30-day history and one fitted forecaster per session (fast tests)."""

from __future__ import annotations

import logging

import pytest
import sklearn  # noqa: F401  - pay the ~3 s import once, outside any timed assertion
from retailsense_contracts.testing import fake_history, sample_store_config

from retailsense_forecasting import FootfallForecaster, QueueForecaster

logging.disable(logging.CRITICAL)

END_DATE = "2026-11-10"  # window covers Dhanteras (6 Nov) + Diwali (8 Nov) -> festival features exercised


@pytest.fixture(scope="session")
def cfg():
    return sample_store_config()


@pytest.fixture(scope="session")
def history(cfg):
    minute_df, daily_df = fake_history(30, cfg, end_date=END_DATE)
    return minute_df, daily_df


@pytest.fixture(scope="session")
def fitted_queue(history):
    qf = QueueForecaster(backend="sklearn")
    report = qf.fit(history[0])
    return qf, report


@pytest.fixture(scope="session")
def fitted_footfall(history):
    ff = FootfallForecaster(backend="sklearn")
    report = ff.fit(history[1])
    return ff, report
