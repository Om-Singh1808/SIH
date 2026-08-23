"""Shared fixtures: the canonical demo store config plus small payload builders."""

import pytest
from retailsense_contracts.api import ShelfStateView, SyncStatus
from retailsense_contracts.config import StoreConfig
from retailsense_contracts.enums import LinkState, ShelfState, UplinkMode
from retailsense_contracts.events import CameraHealth, DeviceHeartbeat, QueueForecast, QueueSnapshot, ShelfStateChange
from retailsense_contracts.testing import sample_store_config

from retailsense_edgerules import RuleEngine

T0 = 1_750_000_000.0  # fixed epoch so ULIDs / timestamps are deterministic


@pytest.fixture
def cfg() -> StoreConfig:
    return sample_store_config()


@pytest.fixture
def engine(cfg: StoreConfig) -> RuleEngine:
    return RuleEngine(cfg)


def shelf_change(
    cfg: StoreConfig,
    shelf_id: str,
    from_state: ShelfState,
    to_state: ShelfState,
    *,
    ts: float,
    gap_minutes: float | None = None,
    scans: int = 3,
) -> tuple[ShelfStateChange, ShelfStateView]:
    shelf = cfg.shelf(shelf_id)
    assert shelf is not None
    sku = cfg.sku(shelf.sku_id)
    gap_started = ts - (gap_minutes or 0.0) * 60.0 if to_state == ShelfState.EMPTY or from_state == ShelfState.EMPTY else None
    ch = ShelfStateChange(
        shelf_id=shelf_id,
        sku_id=shelf.sku_id,
        from_state=from_state,
        to_state=to_state,
        gap_started_ts=gap_started,
        gap_minutes=gap_minutes,
        consecutive_empty_scans=scans if to_state == ShelfState.EMPTY else 0,
    )
    view = ShelfStateView(
        shelf_id=shelf_id,
        name=shelf.name,
        sku_id=shelf.sku_id,
        sku_name=sku.name_en if sku else shelf.name,
        state=to_state,
        coverage=0.1 if to_state == ShelfState.EMPTY else 0.9,
        facings=0 if to_state == ShelfState.EMPTY else shelf.capacity_facings,
        capacity_facings=shelf.capacity_facings,
        min_facings=shelf.min_facings,
        consecutive_empty_scans=ch.consecutive_empty_scans,
        persistence_required=cfg.rules.persistence_scans,
        gap_started_ts=gap_started,
        gap_minutes=gap_minutes,
        last_scan_ts=ts,
        occluded=False,
        impact_open=None,
        has_reference=True,
    )
    return ch, view


def queue_snap(counter_id: str, count: int, *, long_since_ts: float | None = None, est_wait_s: float = 120.0) -> QueueSnapshot:
    return QueueSnapshot(
        counter_id=counter_id,
        zone_id="queue-1",
        count=count,
        avg_dwell_s=60.0,
        max_dwell_s=120.0,
        arrival_rate_pm=2.0,
        service_rate_pm=1.3,
        est_wait_s=est_wait_s,
        method="little_service",
        served_window=5,
        abandoned_window=0,
        served_total=40,
        abandoned_total=1,
        long_since_ts=long_since_ts,
    )


def forecast(counter_id: str, value: float, *, ts: float, horizon: int = 15) -> QueueForecast:
    return QueueForecast(counter_id=counter_id, made_ts=ts, horizons={str(horizon): value, "5": value / 2}, model="edge_trend")


def heartbeat(status: str, *, camera_id: str = "cam-synth", age_s: float = 0.2) -> DeviceHeartbeat:
    return DeviceHeartbeat(
        uptime_s=100.0,
        fps=4.0,
        infer_ms_p50=10.0,
        infer_ms_p95=20.0,
        detector="synthetic",
        model_version="synthetic-1",
        backlog=0,
        link=LinkState.UP,
        cameras=[CameraHealth(camera_id=camera_id, status=status, fps=4.0, last_frame_age_s=age_s, detector="synthetic")],
        contracts_version="1.0.0",
    )


def sync_status(link: LinkState, *, backlog: int, down_since_ts: float | None) -> SyncStatus:
    return SyncStatus(
        link=link,
        uplink=UplinkMode.HTTP,
        cloud_reachable=link == LinkState.UP,
        backlog=backlog,
        backlog_by_class={"aggregate": backlog},
        last_ack_ts=None,
        last_ack_seq=None,
        replayed_since_restore=0,
        replay_total_at_restore=0,
        seq_ok=True,
        down_since_ts=down_since_ts,
    )
