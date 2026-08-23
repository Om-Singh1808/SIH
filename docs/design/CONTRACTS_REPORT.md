# A01 report — `retailsense_contracts` v1.0.0 (frozen)

**Status:** `pip install -e packages/contracts` works with only pydantic/numpy/pyyaml/sqlalchemy/tzdata · `python -m pytest packages/contracts` → **144 passed** · `ruff check packages/contracts tools/gen_ts_types.py` clean · `python tools/gen_ts_types.py --check` → up to date (104 `schemas/*.json` incl. `_all.json`, `ts/types.gen.ts` 31 KB, engine = `npx json-schema-to-typescript@15`).

## Files

```
packages/contracts/pyproject.toml, README.md
packages/contracts/retailsense_contracts/
  __init__.py version.py enums.py ids.py hlc.py clock.py geometry.py logging.py settings.py
  events.py alerts.py impact.py config.py api.py ws.py topics.py db.py manifest.py i18n.py
  interfaces.py registry.py testing.py synthetic.py privacy.py py.typed
  examples/store_demo.yaml (verbatim C.5) · examples/manifest_demo.json · examples/festivals_in.csv (27 rows, 2026-27, `verified` column)
  schemas/*.json (generated, committed)
packages/contracts/ts/types.gen.ts (generated) · ts/index.ts (hand-written: constants, type guards, fmtInr/inr/fmtDuration/fmtClock, sortAlerts)
packages/contracts/tests/  conftest, test_events, test_ids_hlc_clock, test_geometry, test_config, test_impact_i18n,
  test_registry_manifest, test_db, test_fakes, test_store_fake, test_history_sync, test_ts_types
tools/gen_ts_types.py   (--check for CI, --engine auto|npx|builtin)
```

Every test named in spec D1 exists: `test_events_roundtrip` (parametrised over all 16 EventTypes), `test_event_type_mismatch_rejected`, `test_ulid_sortable`, `test_hlc_monotonic_and_receive`, `test_geometry_side_and_crossing` ((90,330)→−1, (90,300)→+1), `test_point_in_polygon_demo_zones`, `test_config_demo_validates_and_hash_stable`, `test_config_rejects_dangling_ids` (12 cases), `test_impact_formula` (₹50.22, basis contains "0.31"), `test_i18n_all_keys_both_langs`, `test_render_never_raises`, `test_registry_fake_fallback` (exactly one WARNING per call), `test_db_create_all_both`, `test_insert_ignore_dedup` (sqlite) + postgres-dialect compile test, `test_manifest_assignment_deterministic`, `test_ts_types_up_to_date` (skips if npx missing), `test_fake_satisfies_protocol` (19 fakes × `runtime_checkable` isinstance).

## Import lines for other agents

```python
from retailsense_contracts import VERSION
from retailsense_contracts.enums import EventClass, Severity, AlertKind, AlertStatus, AckAction, AckBy, ShelfState, LinkState, UplinkMode, ZoneKind, LineKind, Direction, DetectorKind, Anchor, Lang, Origin
from retailsense_contracts.events import Event, Observation, EventType, EVENT_TYPES, EVENT_CLASS, PAYLOAD_CLASSES, Payload, make_event, FootfallCrossing, ZoneOccupancy, DwellSample, HeatmapTile, HeatmapTiles, QueueSnapshot, QueueForecast, ShelfScan, ShelfStateChange, AlertRaised, AlertAcked, AlertResolved, CameraHealth, DeviceHeartbeat, StockReconciled, OrderRequested, ConfigApplied, SimTruth
from retailsense_contracts.alerts import Alert, ImpactInr, AlertDetails, StockoutAlert, QueueAlertDetails, CameraAlertDetails, SyncAlertDetails, DeviceAlertDetails, ShrinkAlertDetails, FootfallAlertDetails, ACTIONS_BY_KIND, AlertAckRequest
from retailsense_contracts.impact import ImpactConfig, rate_per_hour, lost_sales, recovered, queue_abandon_risk, zero_impact
from retailsense_contracts.config import StoreConfig, StoreInfo, DeviceConfig, UplinkConfig, MqttConfig, Floorplan, HomographyConfig, CameraConfig, Zone, Line, Counter, ShelfPolygon, ShelfReference, SKU, RulesConfig, PrivacyConfig, RetentionPolicy, TallyConfig, OndcConfig, WhatsAppConfig, IntegrationsConfig, DemoConfig, load_store_config, dump_store_config
from retailsense_contracts.api import SyncStatus, HealthStatus, KpiToday, KpiDaily, Series, SeriesPoint, ShelfStateView, QueueView, HeatCell, HeatmapResponse, ZonesUpdate, ShelvesUpdate, LinkRequest, ScenarioRequest, ScenarioStatus, ChaosRequest, WhatsAppReply, SkuEnrolResponse, DailySummary, ModelStatus, Command, IngestBatch, IngestAck, Store, DeviceStatus, FleetView, ChainRank, ChainRankRow, KpiRange, FitReport, FootfallForecastDay, FootfallForecast, ReorderSuggestion, ReconcileRow, ReconcileReport, OndcPublishRequest, OndcAck, OutboundMessage, DeliveryReceipt, DailyReport, IntegrationsStatus, ManifestPublishRequest, RolloutRequest, ErrorResponse
from retailsense_contracts.ws import WsMessage, WsKind, WS_KINDS
from retailsense_contracts.topics import topic, status_topic, cmd_topic, parse_topic, EXPIRY_S, EVICTABLE, expires_ts, QOS, MQTT_VERSION, CLEAN_START
from retailsense_contracts.db import edge_metadata, cloud_metadata, create_all, insert_ignore, sqlite_engine, sqlite_url, EDGE_TABLES, CLOUD_TABLES, events, outbox, device_state, alerts, shelf_state, queue_state, heatmap_cells, kpi_daily, sku_enrolment, cloud_events, cloud_alerts, stores, devices, ingest_log, commands, notifications, series_5m, agg_cursor, stock_recon, forecasts, model_manifests, festivals, ondc_log
from retailsense_contracts.manifest import ModelManifest, ModelEntry, ModelIO, RolloutPolicy, assigned_version, device_bucket, version_key
from retailsense_contracts.i18n import TEMPLATES, render, fmt_inr, fmt_num, action_label, action_labels
from retailsense_contracts.interfaces import Frame, Detection, Track, Crossing, AnalyticsUpdate, CoverageResult, SourceError, FrameSource, SyntheticControl, Detector, Tracker, PointMapper, ZoneEngine, QueueAnalyzer, EdgeQueueForecaster, CoverageEstimator, ShelfStateMachine, SkuIdentifier, RuleEngine, EdgeStore, Uplink, LinkController, Notifier, ErpClient, OndcPublisher, CloudQueueForecaster, CloudFootfallForecaster, HISTORY_MINUTE_COLUMNS, HISTORY_DAILY_COLUMNS
from retailsense_contracts.registry import resolve, is_real, Unavailable, IMPLEMENTATIONS, FAKES, NO_FAKE, set_override, clear_overrides, status
from retailsense_contracts.testing import sample_store_config, sample_event, sample_observation, sample_payload, sample_alert, sample_events_all, sample_manifest, load_festivals_csv, example_path, SAMPLE_TS, FakeFrameSource, FakeDetector, FakeTracker, IdentityMapper, FakeZoneEngine, FakeQueueAnalyzer, FakeEdgeForecaster, FakeCoverageEstimator, FakeShelfStateMachine, FakeSkuIdentifier, FakeRuleEngine, InMemoryEdgeStore, FakeRetentionJob, FakeUplink, SimpleLinkController, FakeSyncWorker, FakeNotifier, FakeErp, FakeOndc, FakeForecaster, fake_history, fake_reconcile, fake_suggest_reorder, fake_annotate_frame, fake_shelf_thumbnail, fake_render_floorplan, whatsapp_message_for, draw_rect, magenta_mask, blobs_from_mask
from retailsense_contracts.geometry import point_in_polygon, points_in_polygon, side_of_line, segments_intersect, iou, polygon_bbox, polygon_long_axis, polygon_area, polygon_centroid, polygon_mask, bbox_polygon_overlap, bbox_center, bbox_bottom_center
from retailsense_contracts.ids import new_ulid, ulid_timestamp, is_ulid
from retailsense_contracts.hlc import HLC
from retailsense_contracts.clock import Clock, SystemClock, SimClock, FrozenClock, store_date, day_start_ts, date_to_ts, hour_bucket, DEFAULT_TZ
from retailsense_contracts.synthetic import SyntheticPalette, SHOPPER_SIZE_PX, SIM_DT_S, SHOPPER_SPEED_PX_S, QUEUE_SPACING_PX, MIN_SEPARATION_PX, is_shopper_bgr
from retailsense_contracts.privacy import RetentionPolicy, PrivacyManifest, default_privacy_manifest
from retailsense_contracts.settings import DEFAULTS, get, get_int, get_float, get_bool, snapshot
from retailsense_contracts.logging import get_logger, configure
```

TypeScript (SenseBoard): `import { Alert, KpiToday, Event, Payload, fmtInr, inr, isAlertKind, isPayload, ACTIONS_BY_KIND, EVENT_CLASS, CONTRACTS_VERSION } from "@contracts/types"` → alias to `packages/contracts/ts/index.ts`.

## Public names exported from `retailsense_contracts/__init__.py` (215), by module

- **version**: `VERSION`
- **sub-modules**: `alerts api clock config db enums events geometry hlc i18n ids impact interfaces manifest privacy registry settings synthetic testing topics ws`
- **enums**: AckAction, AckBy, AlertKind, AlertStatus, Anchor, DetectorKind, Direction, EventClass, Lang, LineKind, LinkState, Origin, Severity, ShelfState, UplinkMode, ZoneKind
- **events**: EVENT_CLASS, EVENT_TYPES, PAYLOAD_CLASSES, AlertAcked, AlertRaised, AlertResolved, CameraHealth, ConfigApplied, DeviceHeartbeat, DwellSample, Event, EventType, FootfallCrossing, HeatmapTile, HeatmapTiles, Observation, OrderRequested, Payload, QueueForecast, QueueSnapshot, ShelfScan, ShelfStateChange, SimTruth, StockReconciled, ZoneOccupancy, make_event
- **alerts**: ACTIONS_BY_KIND, Alert, AlertAckRequest, AlertDetails, CameraAlertDetails, DeviceAlertDetails, FootfallAlertDetails, ImpactInr, QueueAlertDetails, ShrinkAlertDetails, StockoutAlert, SyncAlertDetails
- **impact**: ImpactConfig, lost_sales, queue_abandon_risk, rate_per_hour, recovered
- **config**: SKU, CameraConfig, Counter, DemoConfig, DeviceConfig, Floorplan, HomographyConfig, IntegrationsConfig, Line, MqttConfig, OndcConfig, PrivacyConfig, RulesConfig, ShelfPolygon, ShelfReference, StoreConfig, StoreInfo, TallyConfig, UplinkConfig, WhatsAppConfig, Zone, dump_store_config, load_store_config
- **api**: ChainRank, ChainRankRow, ChaosRequest, Command, DailyReport, DailySummary, DeliveryReceipt, DeviceStatus, ErrorResponse, FitReport, FleetView, FootfallForecast, FootfallForecastDay, HealthStatus, HeatCell, HeatmapResponse, IngestAck, IngestBatch, IntegrationsStatus, KpiDaily, KpiRange, KpiToday, LinkRequest, ManifestPublishRequest, ModelStatus, OndcAck, OndcPublishRequest, OutboundMessage, QueueView, ReconcileReport, ReconcileRow, ReorderSuggestion, RolloutRequest, ScenarioRequest, ScenarioStatus, Series, SeriesPoint, ShelfStateView, ShelvesUpdate, SkuEnrolResponse, Store, SyncStatus, WhatsAppReply, ZonesUpdate
- **ws**: WsKind, WsMessage
- **topics**: EVICTABLE, EXPIRY_S, cmd_topic, status_topic, topic
- **manifest**: ModelEntry, ModelIO, ModelManifest, RolloutPolicy, assigned_version
- **i18n**: TEMPLATES, fmt_inr, render
- **interfaces**: AnalyticsUpdate, CloudFootfallForecaster, CloudQueueForecaster, CoverageEstimator, CoverageResult, Crossing, Detection, Detector, EdgeQueueForecaster, EdgeStore, ErpClient, Frame, FrameSource, LinkController, Notifier, OndcPublisher, PointMapper, QueueAnalyzer, RuleEngine, ShelfStateMachine, SkuIdentifier, SourceError, SyntheticControl, Track, Tracker, Uplink, ZoneEngine
- **registry**: FAKES, IMPLEMENTATIONS, Unavailable, is_real, resolve
- **geometry**: bbox_polygon_overlap, iou, point_in_polygon, polygon_area, polygon_bbox, polygon_long_axis, segments_intersect, side_of_line
- **ids / hlc / clock**: new_ulid · HLC · Clock, FrozenClock, SimClock, SystemClock, day_start_ts, store_date
- **synthetic / privacy / logging**: SyntheticPalette · PrivacyManifest, RetentionPolicy · get_logger

(`db`, `settings`, `testing` are reachable as sub-modules; their names are not flattened to keep the root namespace free of table objects and fakes.)

## Deviations from spec (with reasons)

1. **No `from __future__ import annotations` in model modules.** Direct imports (`events.py` imports `Alert`/`ImpactInr` from `alerts.py`) resolve every annotation at class-creation time, so the discriminated `Payload` union and all "forward refs" work without string annotations. `__init__.py` still calls `model_rebuild()` on the envelope models as a belt-and-braces step. `impact.py` references `SKU` only under `TYPE_CHECKING` (config.py imports ImpactConfig, so this avoids a cycle).
2. **`RetentionPolicy` lives in `privacy.py`** and is re-exported from `config.py` — one class, two import paths (spec lists it in both files).
3. **Added (not in spec, additive only):** `FootfallAlertDetails` (member of `AlertDetails`, so `footfall_spike` alerts have typed details); `ErrorResponse`; `Observation.of()`, `Observation.cls`, `Event.to_observation()`; `StoreConfig.zone/line/counter/shelf/zones_for/lines_for/shelves_for/synthetic_camera`, `CameraConfig.is_synthetic/scenario`; `Alert.title(lang)/message(lang)/action_for_digit()`; `HLC.restore()/parse()`; `FrozenClock`, `date_to_ts`, `hour_bucket`; `geometry.points_in_polygon/polygon_mask/polygon_centroid`; `topics.expires_ts/parse_topic/subscribe_all_events`; `registry.set_override/clear_overrides/status/NO_FAKE`; extra fakes `FakeSyncWorker`, `FakeRetentionJob`, `fake_annotate_frame`, `fake_shelf_thumbnail`, `fake_render_floorplan`, `fake_suggest_reorder` so *every* registry key except `tally_mock_app`/`integrations_router` (need FastAPI) has a fake; `impact.zero_impact()`; `ModelEntry.sha256` validated as 64 hex chars.
4. **`version.py`** holds `VERSION` (testing.py imports it without a circular import through `__init__`).
5. **`manifest.assigned_version` canary rule** applies only when `rollout.channel == "canary"` and `canary_pct > 0`; bucket = `sha1(device_id) % 100` (Python `hash()` is salted per process). "Newest" = max by `version_key` (numeric chunks compare numerically, so `1.10 > 1.9`). Unknown `model_id` → newest of `versions_available`.
6. **JSON schemas are dumped in pydantic *serialization* mode and post-processed** so every field is `required` (pydantic always serialises defaults), `prefixItems` tuples become `[string, string]`, and `EventType`/`WsKind`/`Payload`/`AlertDetails` are named types. This is what the board actually receives; spec wording said `TypeAdapter(X).json_schema()` (validation mode) which would make every defaulted field optional in TS.
7. **`impact.queue_abandon_risk` margin**: no SKU → `lost_margin_inr = risk × 10 %` (`DEFAULT_MARGIN_PCT`, same as `SKU.margin_pct` default). Spec only defined the sales number.
8. **`settings.SENSECLOUD_DEV` defaults to "0"** (demo.py sets it to 1 per §E boot order); `SENSECLOUD_SEED_HISTORY` also "0". Extra names: `RS_LOG_LEVEL`, `RS_LOG_PLAIN`, `SENSECLOUD_PORT`.
9. **`gen_ts_types.py` engine choice**: `auto` tries `npx --yes json-schema-to-typescript@15`, falls back to the built-in emitter; the header line records `engine=`; `--check` re-runs the *committed* engine and skips the TS comparison (not the schema comparison) if that engine is npx and npx is absent. Committed output was produced by npx.

## Gotchas for implementers

- **`registry.resolve(key)`** logs **one WARNING per call** when it falls back; call it once at wiring time, not per frame. `resolve(key, allow_fake=False)` raises `Unavailable`. Tests inject doubles with `registry.set_override("edge_store", MyStore)` / `clear_overrides()`. `is_real(key)` tells the boot banner what is real.
- **`InMemoryEdgeStore` stamping**: `append(observations)` assigns `seq` from 1 (gap-free, persisted in `get_state("seq_next")`), `hlc` from an `HLC(device_id)` (dashes in node ids become underscores), ULID `event_id`, `created_ts` from the injected `clock`; one outbox row per event with `expires_ts = enqueued + EXPIRY_S[cls]` (None for ALERT/TXN). `pending(limit)` returns `(outbox_id, Event)` in id order; `backlog()` returns counts for **all five classes** (sum them for `SyncStatus.backlog`); `expire()`/`evict_overflow()` set `evicted_ts` and never touch ALERT/TXN; `kpi_today(ts)` uses the store timezone day (`clock.store_date`/`day_start_ts`) and fills `deltas` only when `kpi_daily(yesterday)` exists.
- **`FakeUplink`** acks like the cloud: duplicates on `event_id`, `seq_gaps`/`seq_ok` per device; `drop_every=N` raises `TimeoutError` **after** recording (resend → duplicates). `queue_command()` puts a `Command` in the next ack. `FakeSyncWorker(store, uplink, link, cfg, on_status, on_command)` + `await worker.tick()` is the reference replay loop (`replayed_since_restore`/`replay_total_at_restore` set on `link.restore()`).
- **Line crossing**: `side_of_line` +1 = LEFT of start→end in image coords; IN = −1→+1. Demo entrance `(120,315)→(60,315)`: walking up is IN; counter line `(532,98)→(532,142)`: moving left is IN (served).
- **`ShelfScan.thumb_b64`** is validated ≤ 16384 chars; `StoreConfig` rejects dangling camera/zone/line/sku ids, duplicate ids, polygons < 3 points, extra keys (`extra="forbid"` everywhere except `params`/`details` dicts).
- **`i18n.render`** formats any param whose name ends in `_inr` with Indian grouping (`₹` is in the template); floats print with ≤ 1 decimal; missing → `"?"`; unknown key returns the key. Hindi text in logs is safe: `get_logger()` reconfigures stdout to UTF-8 (`errors="replace"`).
- **`new_ulid(ts)`** with an explicit `ts` encodes that timestamp (fixtures/replay); without `ts` it is wall-clock monotonic.
- **`db.insert_ignore`** chunks 400 rows per statement and returns the real inserted count on sqlite (`ON CONFLICT DO NOTHING`) and postgres; the `ux_alert_open` partial unique index exists on both edge and cloud (`(kind, subject_id)` resp. `(store_id, kind, subject_id)` WHERE `status <> 'resolved'`). `sqlite_engine(path)` creates parent dirs and applies WAL/FULL/busy_timeout/foreign_keys on every connect.
- **pandas is not a contracts dependency**: `fake_history()` and `FakeForecaster` import it lazily; `fake_history(days, cfg, seed, end_date=...)` defaults `end_date` to today (IST) — pass a fixed `end_date` in tests.
- The package is **frozen at 1.0.0**; changes only via PR to A01 with A14 sign-off. Regenerate TS after any model change: `python tools/gen_ts_types.py` (CI: `--check`).
