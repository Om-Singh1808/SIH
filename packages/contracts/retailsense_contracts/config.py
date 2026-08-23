"""``store.yaml`` schema: one file describes a store, its camera(s), zones, lines,
counters, shelves, SKUs, rule thresholds, impact factors, privacy and integrations.

``examples/store_demo.yaml`` is the canonical demo store; its geometry is
normative for the simulator, the edge tests and the dashboard.  ``StoreConfig``
validates referential integrity (no dangling ids) so a typo in the zone editor
fails at load time, not at 2 am on stage.
"""

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import Anchor, DetectorKind, Lang, LineKind, UplinkMode, ZoneKind
from .impact import ImpactConfig
from .privacy import RetentionPolicy

# ---------------------------------------------------------------------------
# store / device
# ---------------------------------------------------------------------------


class StoreInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: str
    name: str
    lang: Lang = Lang.HI
    tz: str = "Asia/Kolkata"
    tier: Literal["kirana", "mini", "chain"] = "kirana"
    owner_whatsapp: str
    open_hours: tuple[str, str] = ("08:00", "22:00")
    address: str | None = None


class MqttConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "localhost"
    port: int = 1883
    ws_port: int = 9001
    username: str | None = None
    password: str | None = None
    session_expiry_s: int = 604800


class UplinkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: UplinkMode = UplinkMode.HTTP
    batch_size: int = 500
    interval_s: float = 2.0
    heartbeat_s: float = 10.0
    max_outbox_rows: int = 50000
    mqtt: MqttConfig = MqttConfig()


class DeviceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str
    token: str = "demo-token"
    edge_port: int = 8001
    cloud_url: str = "http://localhost:8000"
    db_path: str = "var/senseedge.db"
    uplink: UplinkConfig = UplinkConfig()


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------


class Floorplan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width_px: int = 640
    height_px: int = 360
    scale_m_per_px: float = 0.02
    image: str | None = None
    heat_cell_px: int = 20


class HomographyConfig(BaseModel):
    """>= 4 image/floor point pairs; ``None`` on the camera means identity."""

    model_config = ConfigDict(extra="forbid")

    image_points: list[list[float]]
    floor_points: list[list[float]]

    @model_validator(mode="after")
    def _pairs(self) -> "HomographyConfig":
        if len(self.image_points) < 4 or len(self.image_points) != len(self.floor_points):
            raise ValueError("homography needs >= 4 image/floor point pairs of equal length")
        return self


class CameraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    camera_id: str
    source: str  # "rtsp://..", "file:var/demo_store.mp4", "webcam:0", "synthetic:evening_rush"
    width: int = 640
    height: int = 360
    fps_sample: float = 4.0
    detector: DetectorKind = DetectorKind.AUTO  # auto => synthetic for synthetic:/synthetic files, else onnx
    anchor: Anchor = Anchor.BOTTOM_CENTER
    shelf_scan_interval_s: float = 60.0
    homography: HomographyConfig | None = None  # None => identity (image == floorplan)
    preview_blur_people: bool = True
    loop_file: bool = True

    @property
    def is_synthetic(self) -> bool:
        return self.source.startswith("synthetic:")

    @property
    def scenario(self) -> str | None:
        return self.source.split(":", 1)[1] if self.is_synthetic else None


def _check_polygon(poly: list[list[float]]) -> list[list[float]]:
    if len(poly) < 3:
        raise ValueError("polygon needs >= 3 points")
    for p in poly:
        if len(p) != 2:
            raise ValueError("polygon points must be [x, y]")
    return poly


class Zone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone_id: str
    camera_id: str
    kind: ZoneKind
    polygon: list[list[float]]
    name: str | None = None

    @field_validator("polygon")
    @classmethod
    def _poly(cls, v: list[list[float]]) -> list[list[float]]:
        return _check_polygon(v)


class Line(BaseModel):
    """Directed line; +1 side (left of start->end, y down) is IN. See geometry.py."""

    model_config = ConfigDict(extra="forbid")

    line_id: str
    camera_id: str
    kind: LineKind
    start: list[float]
    end: list[float]
    name: str | None = None

    @model_validator(mode="after")
    def _points(self) -> "Line":
        if len(self.start) != 2 or len(self.end) != 2:
            raise ValueError("line start/end must be [x, y]")
        if self.start == self.end:
            raise ValueError("line start and end must differ")
        return self


class Counter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    counter_id: str
    name: str
    queue_zone_id: str
    counter_line_id: str
    max_queue: int = 8
    default_service_s: float = 45.0


class ShelfReference(BaseModel):
    """Calibration snapshot of a full shelf (what 100 % coverage looks like)."""

    model_config = ConfigDict(extra="forbid")

    shelf_id: str
    calibrated_ts: float
    raw_coverage_full: float
    backing_bgr: list[int]
    profile: list[float] | None = None
    method: str = "classical"


class ShelfPolygon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shelf_id: str
    camera_id: str
    name: str
    polygon: list[list[float]]
    sku_id: str | None = None
    capacity_facings: int = 8
    min_facings: int = 2
    facing_width_px: float | None = None
    reference: ShelfReference | None = None

    @field_validator("polygon")
    @classmethod
    def _poly(cls, v: list[list[float]]) -> list[list[float]]:
        return _check_polygon(v)


class SKU(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: str
    name_en: str
    name_hi: str
    mrp_inr: float
    margin_pct: float = 10.0
    velocity_units_per_hr: float
    units_per_facing: int = 4
    lead_time_days: int = 2
    tally_item_name: str | None = None
    ondc_item_id: str | None = None
    enrolled_images: int = 0

    def name(self, lang: str) -> str:
        return self.name_hi if str(lang) == "hi" else self.name_en


# ---------------------------------------------------------------------------
# rules / privacy / integrations / demo
# ---------------------------------------------------------------------------


class RulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shelf_partial_coverage: float = 0.80
    shelf_empty_coverage: float = 0.25
    persistence_scans: int = 3
    max_persistence_scans: int = 6
    queue_long_count: int = 4
    queue_long_s: float = 60
    queue_resolve_s: float = 30
    queue_forecast_threshold: int = 6
    queue_forecast_horizon_min: int = 15
    queue_min_age_s: float = 5.0
    queue_window_s: int = 600
    snapshot_interval_s: float = 10
    occupancy_interval_s: float = 10
    heat_flush_s: float = 60
    camera_down_s: float = 15
    black_frame_std: float = 3.0
    sync_backlog_warn: int = 1000
    sync_backlog_after_s: float = 300
    shrink_min_units: int = 3
    shrink_min_inr: float = 200
    occlusion_skip_overlap: float = 0.30
    footfall_spike_factor: float = 2.5


class PrivacyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_blur_people: bool = True
    shelf_thumbnails: bool = True
    retention: RetentionPolicy = RetentionPolicy()
    statement: str = "No face recognition; no raw video persisted; track IDs never leave the edge."


class TallyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    url: str = "http://localhost:9000"
    company: str | None = None


class OndcConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    gateway_url: str = "http://localhost:8000/mock/ondc"
    bpp_id: str = "demo.bpp"
    signing: Literal["none", "ed25519"] = "none"


class WhatsAppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["simulator", "cloud_api", "telegram", "none"] = "simulator"
    to: str | None = None
    phone_number_id: str | None = None
    token_env: str = "WHATSAPP_TOKEN"
    telegram_chat_id: str | None = None
    telegram_token_env: str = "TELEGRAM_TOKEN"


class IntegrationsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tally: TallyConfig = TallyConfig()
    ondc: OndcConfig = OndcConfig()
    whatsapp: WhatsAppConfig = WhatsAppConfig()


class DemoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    clock_factor: float = 10.0
    default_scenario: str = "baseline"
    start_time: str = "17:00"
    seed_history_days: int = 30
    auto_calibrate_first_scan: bool = True


# ---------------------------------------------------------------------------
# root
# ---------------------------------------------------------------------------


def _dupes(ids: list[str]) -> set[str]:
    seen: set[str] = set()
    dup: set[str] = set()
    for i in ids:
        if i in seen:
            dup.add(i)
        seen.add(i)
    return dup


class StoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    config_version: int = 1
    store: StoreInfo
    device: DeviceConfig
    floorplan: Floorplan = Floorplan()
    cameras: list[CameraConfig]
    zones: list[Zone] = Field(default_factory=list)
    lines: list[Line] = Field(default_factory=list)
    counters: list[Counter] = Field(default_factory=list)
    shelves: list[ShelfPolygon] = Field(default_factory=list)
    skus: list[SKU] = Field(default_factory=list)
    rules: RulesConfig = RulesConfig()
    impact: ImpactConfig = ImpactConfig()
    privacy: PrivacyConfig = PrivacyConfig()
    integrations: IntegrationsConfig = IntegrationsConfig()
    demo: DemoConfig = DemoConfig()

    # -- referential integrity -------------------------------------------
    @model_validator(mode="after")
    def _integrity(self) -> "StoreConfig":
        if not self.cameras:
            raise ValueError("at least one camera is required")
        for label, ids in (
            ("camera_id", [c.camera_id for c in self.cameras]),
            ("zone_id", [z.zone_id for z in self.zones]),
            ("line_id", [ln.line_id for ln in self.lines]),
            ("counter_id", [c.counter_id for c in self.counters]),
            ("shelf_id", [s.shelf_id for s in self.shelves]),
            ("sku_id", [s.sku_id for s in self.skus]),
        ):
            dup = _dupes(ids)
            if dup:
                raise ValueError(f"duplicate {label}: {sorted(dup)}")
        cams = {c.camera_id for c in self.cameras}
        for z in self.zones:
            if z.camera_id not in cams:
                raise ValueError(f"zone {z.zone_id!r} references unknown camera {z.camera_id!r}")
        for ln in self.lines:
            if ln.camera_id not in cams:
                raise ValueError(f"line {ln.line_id!r} references unknown camera {ln.camera_id!r}")
        for s in self.shelves:
            if s.camera_id not in cams:
                raise ValueError(f"shelf {s.shelf_id!r} references unknown camera {s.camera_id!r}")
        zones = {z.zone_id: z for z in self.zones}
        lines = {ln.line_id: ln for ln in self.lines}
        for c in self.counters:
            if c.queue_zone_id not in zones:
                raise ValueError(f"counter {c.counter_id!r} references unknown zone {c.queue_zone_id!r}")
            if c.counter_line_id not in lines:
                raise ValueError(f"counter {c.counter_id!r} references unknown line {c.counter_line_id!r}")
        skus = {s.sku_id for s in self.skus}
        for s in self.shelves:
            if s.sku_id is not None and s.sku_id not in skus:
                raise ValueError(f"shelf {s.shelf_id!r} references unknown sku {s.sku_id!r}")
            if s.reference is not None and s.reference.shelf_id != s.shelf_id:
                raise ValueError(f"shelf {s.shelf_id!r} has a reference for {s.reference.shelf_id!r}")
        return self

    # -- lookups ------------------------------------------------------------
    def sku(self, sku_id: str | None) -> SKU | None:
        if sku_id is None:
            return None
        return next((s for s in self.skus if s.sku_id == sku_id), None)

    def camera(self, camera_id: str) -> CameraConfig:
        for c in self.cameras:
            if c.camera_id == camera_id:
                return c
        raise KeyError(camera_id)

    def zone(self, zone_id: str) -> Zone | None:
        return next((z for z in self.zones if z.zone_id == zone_id), None)

    def line(self, line_id: str) -> Line | None:
        return next((ln for ln in self.lines if ln.line_id == line_id), None)

    def counter(self, counter_id: str) -> Counter | None:
        return next((c for c in self.counters if c.counter_id == counter_id), None)

    def shelf(self, shelf_id: str) -> ShelfPolygon | None:
        return next((s for s in self.shelves if s.shelf_id == shelf_id), None)

    def zones_for(self, camera_id: str) -> list[Zone]:
        return [z for z in self.zones if z.camera_id == camera_id]

    def lines_for(self, camera_id: str) -> list[Line]:
        return [ln for ln in self.lines if ln.camera_id == camera_id]

    def shelves_for(self, camera_id: str) -> list[ShelfPolygon]:
        return [s for s in self.shelves if s.camera_id == camera_id]

    @property
    def synthetic_camera(self) -> CameraConfig | None:
        return next((c for c in self.cameras if c.is_synthetic), None)

    # -- hashing ------------------------------------------------------------
    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def config_hash(self) -> str:
        """sha256 of the canonical JSON (first 16 hex chars); stable across load/dump cycles."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()[:16]


def load_store_config(path: str | Path) -> StoreConfig:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return StoreConfig.model_validate(data)


def dump_store_config(cfg: StoreConfig, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump(mode="json", exclude_none=True)
    with open(p, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)


__all__ = [
    "SKU",
    "CameraConfig",
    "Counter",
    "DemoConfig",
    "DeviceConfig",
    "Floorplan",
    "HomographyConfig",
    "ImpactConfig",
    "IntegrationsConfig",
    "Line",
    "MqttConfig",
    "OndcConfig",
    "PrivacyConfig",
    "RetentionPolicy",
    "RulesConfig",
    "ShelfPolygon",
    "ShelfReference",
    "StoreConfig",
    "StoreInfo",
    "TallyConfig",
    "UplinkConfig",
    "WhatsAppConfig",
    "Zone",
    "dump_store_config",
    "load_store_config",
]
