# RetailSense — Final Implementation Spec (v1.0, frozen)

Lead-architect synthesis of the winning proposal + all three judges' advice. Every HARD CONSTRAINT is honoured; every judge cut is applied (HTTP batch sync primary, synthetic detector primary, classical shelf estimator only, sklearn default, hi+en only, explicit P0/P1/P2 cut-line, 14 disjoint agent directories, contracts with fakes so nobody waits on anybody).

Environment verified on the dev box: Windows 11, Python 3.11.0, Node 22.18, numpy 2.4, opencv-python 5.0, onnxruntime 1.24 **CPU build** (`CUDAExecutionProvider` not present; torch is CPU-only; RTX 4060 exists but is unused unless `onnxruntime-gpu` is installed — treat GPU as optional P2), scipy 1.16, scikit-learn 1.7, pandas 2.3, SQLAlchemy 2.0, pydantic 2.11, paho-mqtt 2.1 (v2 callback API), fastapi 0.116, httpx, websockets, PyYAML, pytest 8. Not installed: ultralytics, lightgbm, make.

---

## A. System summary + innovation list

**RetailSense** turns a kirana's existing CCTV (or an old phone, or — on stage — a synthetic store) into rupee-quantified shelf, queue and footfall intelligence. **SenseEdge** (one Python process per store) pulls frames from RTSP/file/webcam/synthetic sources, runs a pluggable person detector (colour-blob detector for the synthetic store, YOLO11n ONNX on CPU for real video, Ultralytics as fallback) and a ByteTrack-style tracker, then derives zone occupancy, line-crossing footfall, dwell, floorplan heatmaps, queue length/wait/abandonment (Little's Law on a measured *service* rate), and shelf coverage with a 3-scan persistence filter; a rule engine converts these into alerts carrying `impact_inr` with a cited basis string, pre-rendered in Hindi and English. Everything is written to SQLite (WAL, `synchronous=FULL`) together with a transactional outbox row; a sync worker ships batches to **SenseCloud** over HTTP (`POST /v1/ingest/batch`, idempotent on `event_id`, ordered by per-device `seq`), with MQTT 5 as an optional uplink, and a "Cable kaat do" link toggle proves store-and-forward on stage ("312/312 replayed, seq ordered"). SenseCloud aggregates KPIs, trains festival-aware queue/footfall forecasters on simulator history, dispatches WhatsApp/Telegram notifications (simulator on stage), reconciles visual stock against a Tally XML mock to expose shrink in rupees, publishes availability to an ONDC stub, and runs a fleet registry with an OTA model manifest. **SenseBoard** (Vite + React 19 + TS + Tailwind) is a Hindi/English, phone-first dashboard ("Aaj ka hisaab") with ops, insights/heatmap, chain/fleet views, a zone editor and an on-stage WhatsApp phone panel. No faces, no raw video persisted, only anonymous track IDs (never leaving the edge) and aggregates.

**Innovation list (10)**

1. **Rupee on every alert** — `ImpactInr{lost_sales_inr, lost_margin_inr, basis, factor, source}`; factor 0.31 cited (Gruen, Corsten & Bharadwaj 2002, GMA/FMI, 71k shoppers: 31% buy elsewhere) and editable in config.
2. **WhatsApp-native 1-tap ops loop** (1 = bhar diya, 2 = order, 3 = galat alert) — simulator on stage, same `Notifier` interface for Meta Cloud API and Telegram; "3" auto-raises per-shelf persistence (self-tuning false-positive control).
3. **True offline-first with live proof** — events + outbox in one SQLite WAL transaction, per-class expiry (alerts/txn never expire), per-device monotonic `seq`, idempotent cloud ingest, "Cable kaat do" toggle and a "N/N replayed, seq ordered" badge.
4. **Persistence-filtered shelf gaps + Shelf Gap Duration as a first-class KPI** — 3 consecutive 30-60 s scans, occlusion-aware (shopper in front → scan skipped), classical coverage estimator that needs no trained weights.
5. **Synthetic store as test oracle and demo** — agent-based shoppers/queues/shelves render to video frames consumed by the *real* CV pipeline; ground truth (`sim.truth`) lets tests assert accuracy; headless mode fakes extra stores for the chain view.
6. **Service-rate Little's Law + abandonment** — wait = L / measured service rate from counter-line crossings (arrival rate only for forecasting), min-age guard against ID switches.
7. **15-min queue forecast with live MAE** — edge trend model offline, cloud gradient boosting (festival + salary-week features) online, MAE shown on the board.
8. **Visual-vs-Tally shrink reconciliation in rupees** — Tally XML mock server, `StockReconciled` txn events, shrink table.
9. **Zero-hardware onboarding** — RTSP/webcam/file source abstraction, zone editor on a live frame, one-click shelf reference calibration.
10. **Privacy by design** — appearance-free tracking, track IDs never leave the edge, 96×96 shelf-only thumbnails, preview pixelation, DPDP retention purge job, and a fleet/OTA manifest for model governance.

**MVP cut-line (integration acceptance = the 3-minute script in §E).**
P0 (must demo): contracts, sim (video + scenarios), edgecv (synthetic detector + tracker + file/webcam sources), edgeshelf (classical + state machine), edgeanalytics, edgequeue (analytics + trend forecast), edgerules, edgestore, edgeuplink (HTTP + link toggle), senseedge, sensecloud (ingest/KPIs/alerts/WS/whatsapp-sim/tally-mock/recon), senseboard (/owner, /ops, DemoControls, SyncBadge, PhonePanel), demo.py, two integration tests.
P1 (should): ONNX YOLO11n detector, cloud GBM forecasters + reorder, /insights heatmap + shrink table, /chain + fleet + manifest, Telegram notifier, MQTT uplink, docker-compose, CI.
P2 (stretch, stubbed behind interfaces): CLIP SKU identifier, Ultralytics detector, ONDC signing, zone editor polish, Prometheus metrics, TTS, Tamil/Telugu.

---

## B. Monorepo tree

Repo root = `C:/Users/OMEN/sih`. Python import names in parentheses. `[Axx]` = owning agent (see §F). Paths are disjoint per agent.

```
sih/
├── README.md                                   [A14]
├── Makefile                                    [A14]  thin wrappers over `python -m retailsense <cmd>`
├── pyproject.toml                              [A14]  root: pytest/ruff config + dev extras (no package code)
├── package.json                                [A14]  npm workspaces: ["apps/senseboard"]
├── docker-compose.yml                          [A14]
├── .github/workflows/ci.yml                    [A14]
├── .gitignore                                  [A14]  var/, models/*.onnx, node_modules, dist, *.db
├── retailsense/__init__.py, __main__.py        [A14]  CLI: demo|edge|cloud|sim|board|test|setup|types|video|fetch-models|lint
├── tools/
│   ├── demo.py                                 [A14]  one-command supervisor (Windows-safe process tree)
│   ├── setup_dev.py                            [A14]  pip install -e every package, npm ci, optional model fetch
│   ├── ports.py                                [A14]  free-port checks
│   ├── gen_ts_types.py                         [A01]  pydantic -> JSON Schema -> packages/contracts/ts/types.gen.ts
│   ├── fetch_models.py                         [A03]  ultralytics export -> models/yolo11n.onnx (+ sha256 into manifest)
│   └── make_demo_video.py                      [A02]  renders var/demo_store.mp4 (60 s synthetic) for the file: path
├── tests/                                      [A14]  integration only
│   ├── conftest.py
│   ├── test_e2e_synthetic.py
│   ├── test_offline_replay.py
│   ├── test_demo_script.py      (slow)
│   └── test_demo_boot.py        (smoke)
├── deploy/                                     [A14]
│   ├── edge.Dockerfile, cloud.Dockerfile, board.Dockerfile
│   ├── mosquitto.conf, nginx.conf
├── models/
│   ├── manifest.json                           [A03]  ModelManifest instance (OTA)
│   └── *.onnx                                  (gitignored; fetched)
├── var/                                        (runtime: sqlite dbs, logs, mp4; gitignored)
├── packages/
│   ├── contracts/                              [A01]  (retailsense_contracts)
│   │   ├── pyproject.toml
│   │   ├── retailsense_contracts/
│   │   │   ├── __init__.py            VERSION = "1.0.0"; re-exports
│   │   │   ├── ids.py                 new_ulid()
│   │   │   ├── hlc.py                 HLC hybrid logical clock
│   │   │   ├── clock.py               Clock protocol, SystemClock, SimClock
│   │   │   ├── geometry.py            point_in_polygon, side_of_line, segments_intersect, iou, polygon_bbox, long_axis
│   │   │   ├── logging.py             JSON logger factory, UTF-8 safe on Windows
│   │   │   ├── settings.py            env var names + defaults (RS_*, SENSECLOUD_*)
│   │   │   ├── enums.py               EventClass, Severity, AlertKind, AlertStatus, ShelfState, LinkState, ...
│   │   │   ├── events.py              Event envelope, Observation, all payloads, EVENT_CLASS, Payload union
│   │   │   ├── alerts.py              Alert, ImpactInr, AlertAck, details models
│   │   │   ├── config.py              StoreConfig and children; load_store_config(path)
│   │   │   ├── impact.py              lost_sales(), queue_abandon_risk(), recovered() — THE formula
│   │   │   ├── api.py                 REST request/response models (edge + cloud)
│   │   │   ├── ws.py                  WsMessage envelope
│   │   │   ├── topics.py              MQTT topic builders + EXPIRY policy table
│   │   │   ├── db.py                  SQLAlchemy Core tables: edge_metadata, cloud_metadata; insert_ignore()
│   │   │   ├── manifest.py            ModelManifest, ModelEntry, RolloutPolicy, assignment rule
│   │   │   ├── i18n.py                templates hi/en + render(key, lang, **params)
│   │   │   ├── interfaces.py          ALL Protocols (FrameSource, Detector, Tracker, ZoneEngine, ...)
│   │   │   ├── registry.py            IMPLEMENTATIONS map + resolve(key) with fake fallback
│   │   │   ├── testing.py             Fakes: FakeFrameSource, FakeDetector, FakeTracker, InMemoryEdgeStore, FakeUplink, ...
│   │   │   ├── synthetic.py           SyntheticPalette + demo geometry constants shared by sim/edgecv/edgeshelf
│   │   │   ├── privacy.py             RetentionPolicy, PrivacyManifest
│   │   │   ├── examples/store_demo.yaml, manifest_demo.json, festivals_in.csv
│   │   │   └── schemas/*.json         generated by gen_ts_types.py (committed)
│   │   ├── ts/types.gen.ts            generated (committed); ts/index.ts hand-written helpers
│   │   └── tests/
│   ├── sim/                                    [A02]  (retailsense_sim)
│   │   └── retailsense_sim/{store_model.py, shopper.py, video.py, floorplan.py, scenarios.py, chaos.py, history.py, headless.py, cli.py}
│   ├── edgecv/                                 [A03]  (retailsense_edgecv)
│   │   └── retailsense_edgecv/{source.py, detector_synthetic.py, detector_onnx.py, detector_ultralytics.py, tracker.py, kalman.py, homography.py, annotate.py, pipeline.py, models.py}
│   ├── edgeshelf/                              [A04]  (retailsense_edgeshelf)
│   │   └── retailsense_edgeshelf/{coverage.py, state.py, osa.py, sku.py, sku_clip.py, thumbs.py}
│   ├── edgeanalytics/                          [A05]  (retailsense_edgeanalytics)
│   │   └── retailsense_edgeanalytics/{zones.py, lines.py, dwell.py, heatmap.py, footfall.py}
│   ├── edgequeue/                              [A06]  (retailsense_edgequeue)
│   │   └── retailsense_edgequeue/{queue.py, little.py, forecast.py}
│   ├── edgerules/                              [A07]  (retailsense_edgerules)
│   │   └── retailsense_edgerules/{engine.py, impact.py, render.py, feedback.py, rules_default.yaml}
│   ├── edgestore/                              [A08]  (retailsense_edgestore)
│   │   └── retailsense_edgestore/{store.py, outbox.py, retention.py, kpi.py}
│   ├── edgeuplink/                             [A08]  (retailsense_edgeuplink)
│   │   └── retailsense_edgeuplink/{http.py, mqtt.py, link.py, sync.py, commands.py}
│   ├── forecasting/                            [A12]  (retailsense_forecasting)
│   │   └── retailsense_forecasting/{features.py, festivals.py, queue_forecaster.py, footfall_forecaster.py, reorder.py, eval.py}
│   └── integrations/                           [A11]  (retailsense_integrations)
│       └── retailsense_integrations/{tally.py, tally_mock.py, tally_xml.py, reconcile.py, whatsapp.py, telegram.py, ondc.py, routers.py}
├── apps/
│   ├── senseedge/                              [A09]  (senseedge)
│   │   └── senseedge/{main.py, app.py, wi
│   │   └── senseedge/{main.py, app.py, wiring.py, workers.py, consumer.py, ws.py, preview.py, routers/{health,config,kpis,alerts,queues,shelves,heatmap,calibrate,demo,sync,models}.py, metrics.py}
│   │   ├── pyproject.toml, tests/
│   ├── sensecloud/                             [A10]  (sensecloud)
│   │   └── sensecloud/{main.py, app.py, db.py, ingest.py, aggregator.py, alerting.py, dispatcher.py, fleet.py, reports.py, ws.py, mqtt_bridge.py, seed.py, routers/{ingest,stores,kpis,alerts,queues,shelves,heatmap,forecast,reports,integrations,chain,fleet,whatsapp,mock_ondc,ws}.py}
│   │   ├── pyproject.toml, tests/
│   └── senseboard/                             [A13]  Vite + React 19 + TS + Tailwind
│       ├── package.json, vite.config.ts, tsconfig.json, tailwind.config.ts, index.html, vitest.config.ts
│       └── src/{main.tsx, App.tsx, api/{edge.ts, cloud.ts, ws.ts, types.ts (re-export of @contracts/types)}, i18n/{hi.json, en.json, useT.ts}, store/{live.ts, settings.ts}, pages/{Owner,Ops,Insights,Chain,Zones}.tsx, components/{KpiTile,AlertCard,QueueLane,ShelfGrid,PhonePanel,SyncBadge,FreshnessBadge,LangToggle,Heatmap,PowerHours,ShrinkTable,ForecastChart,FleetTable,RankTable,ZoneCanvas,DemoControls,PreviewTile}.tsx, lib/{format.ts, geometry.ts, colors.ts}, tests/*.test.tsx}
└── docs/
    ├── research/ (existing)
    ├── ARCHITECTURE.md, CONTRACTS.md, DECK.md, BMC.md, PRIVACY.md        [A01]
    └── DEMO_SCRIPT.md, RUNBOOK.md                                         [A14]
```

Ownership rule: an agent edits only its listed paths (including that package's `pyproject.toml` and `tests/`). `packages/contracts` is frozen once A01 marks `VERSION = "1.0.0"`; later changes only by PR to A01 with A14 sign-off. Apps resolve sibling packages **only** through `retailsense_contracts.registry.resolve()` (fake fallback), so every agent can run its own tests with nothing else installed.

---

## C. Shared contracts (`packages/contracts/retailsense_contracts`)

All code below is normative. Timestamps everywhere are **epoch seconds UTC as `float`** (fields end in `_ts` or are named `ts`); dates are ISO `YYYY-MM-DD` strings in store tz (`Asia/Kolkata`). Coordinates are image pixels `[x, y]` of the owning camera; floorplan coordinates are pixels of the floorplan canvas. Money is INR float rounded to 2 dp on render. Every model uses `model_config = ConfigDict(extra="forbid")` except `*.params/details` dicts.

### C.1 enums.py

```python
from enum import StrEnum
class EventClass(StrEnum): TELEMETRY="telemetry"; AGGREGATE="aggregate"; ALERT="alert"; TXN="txn"; CONFIG="config"
class Severity(StrEnum): INFO="info"; WARN="warn"; HIGH="high"; CRITICAL="critical"
class AlertKind(StrEnum):
    SHELF_GAP="shelf_gap"; QUEUE_LONG="queue_long"; QUEUE_FORECAST="queue_forecast"; CAMERA_DOWN="camera_down"
    SYNC_BACKLOG="sync_backlog"; DEVICE_OFFLINE="device_offline"; SHRINK_SUSPECT="shrink_suspect"; FOOTFALL_SPIKE="footfall_spike"
class AlertStatus(StrEnum): OPEN="open"; ACKED="acked"; RESOLVED="resolved"
class AckAction(StrEnum): RESTOCKED="restocked"; ORDER="order"; FALSE_POSITIVE="false_positive"; OPENED_COUNTER="opened_counter"; IGNORE="ignore"; CHECKED="checked"; INVESTIGATE="investigate"
class AckBy(StrEnum): WHATSAPP="whatsapp"; WHATSAPP_SIM="whatsapp_sim"; BOARD="board"; AUTO="auto"; TELEGRAM="telegram"
class ShelfState(StrEnum): STOCKED="stocked"; PARTIAL="partial"; EMPTY="empty"; UNKNOWN="unknown"
class LinkState(StrEnum): UP="up"; DOWN="down"
class UplinkMode(StrEnum): HTTP="http"; MQTT="mqtt"; NONE="none"
class ZoneKind(StrEnum): AISLE="aisle"; QUEUE="queue"; ENTRANCE="entrance"; COUNTER="counter"; STORE="store"; CUSTOM="custom"
class LineKind(StrEnum): ENTRANCE="entrance"; COUNTER="counter"; CUSTOM="custom"
class Direction(StrEnum): IN="in"; OUT="out"
class DetectorKind(StrEnum): AUTO="auto"; SYNTHETIC="synthetic"; ONNX="onnx"; ULTRALYTICS="ultralytics"; FAKE="fake"
class Anchor(StrEnum): BOTTOM_CENTER="bottom_center"; CENTER="center"
class Lang(StrEnum): HI="hi"; EN="en"
class Origin(StrEnum): EDGE="edge"; CLOUD="cloud"
```

### C.2 ids.py / hlc.py / clock.py / geometry.py

```python
def new_ulid() -> str            # 26-char Crockford base32, time-ordered, no external dep
class Clock(Protocol):  def now(self) -> float
class SystemClock: ...            # time.time()
class SimClock:  def __init__(self, start_ts: float, factor: float=1.0); def now(); def set(ts); def advance(dt)
class HLC:                        # "{physical_ms:013d}-{logical:04d}-{node}", monotonic, receive(remote) merges
    def __init__(self, node: str, clock: Clock | None = None); def now(self) -> str; def receive(self, remote: str) -> str
# geometry.py (pure numpy, no cv2):
def point_in_polygon(pt: tuple[float,float], poly: list[list[float]]) -> bool
def side_of_line(pt, start, end) -> int          # sign of cross((end-start),(pt-start)); +1 = LEFT of start->end (image coords, y down)
def segments_intersect(a0,a1,b0,b1) -> bool
def iou(a: np.ndarray, b: np.ndarray) -> np.ndarray   # [N,4]x[M,4] xyxy -> [N,M]
def polygon_bbox(poly) -> tuple[int,int,int,int]; def polygon_long_axis(poly) -> Literal["x","y"]; def polygon_area(poly) -> float
def bbox_polygon_overlap(bbox_xyxy, poly) -> float   # fraction of bbox area inside polygon (rasterised 1px)
```
**Line-crossing rule (normative):** a track crosses a line when its anchor moves from side −1 to side +1 (→ `Direction.IN`) or +1 to −1 (→ `OUT`). +1 is the left side of `start→end`. Config authors orient lines so IN is the wanted direction; the zone editor draws the arrow.

### C.3 events.py — envelope, Observation, payloads

```python
EventType = Literal[
  "footfall.crossing","zone.occupancy","dwell.sample","heatmap.tiles","queue.snapshot","queue.forecast",
  "shelf.scan","shelf.state","alert.raised","alert.acked","alert.resolved","device.heartbeat",
  "stock.reconciled","order.requested","config.applied","sim.truth"]

EVENT_CLASS: dict[str, EventClass] = {
  "footfall.crossing": AGGREGATE, "zone.occupancy": AGGREGATE, "dwell.sample": AGGREGATE, "heatmap.tiles": TELEMETRY,
  "queue.snapshot": AGGREGATE, "queue.forecast": AGGREGATE, "shelf.scan": AGGREGATE, "shelf.state": AGGREGATE,
  "alert.raised": ALERT, "alert.acked": ALERT, "alert.resolved": ALERT, "device.heartbeat": TELEMETRY,
  "stock.reconciled": TXN, "order.requested": TXN, "config.applied": CONFIG, "sim.truth": TELEMETRY}

class FootfallCrossing(BaseModel):
    type: Literal["footfall.crossing"] = "footfall.crossing"
    line_id: str; line_kind: LineKind; direction: Direction
    count: int = 1                       # always 1 on edge; headless sims may batch
class ZoneOccupancy(BaseModel):
    type: Literal["zone.occupancy"] = "zone.occupancy"
    zone_id: str; zone_kind: ZoneKind; count: int; window_s: float
class DwellSample(BaseModel):
    type: Literal["dwell.sample"] = "dwell.sample"
    zone_id: str; dwell_s: float; entered_ts: float; exited_ts: float          # no track id (privacy)
class HeatmapTile(BaseModel):
    cell_x: int; cell_y: int; hour_bucket: int; dwell_s: float; visits: int     # hour_bucket = floor(ts/3600)
class HeatmapTiles(BaseModel):
    type: Literal["heatmap.tiles"] = "heatmap.tiles"
    cell_px: int; width_cells: int; height_cells: int; tiles: list[HeatmapTile]   # deltas since last flush, floor coords
class QueueSnapshot(BaseModel):
    type: Literal["queue.snapshot"] = "queue.snapshot"
    counter_id: str; zone_id: str; count: int
    avg_dwell_s: float; max_dwell_s: float; arrival_rate_pm: float; service_rate_pm: float
    est_wait_s: float; method: Literal["little_service","observed_wait","default_service"]
    served_window: int; abandoned_window: int; window_s: int = 300
    served_total: int; abandoned_total: int           # cumulative since store-day start
    long_since_ts: float | None = None                # when count>=queue_long_count started, else None
class QueueForecast(BaseModel):
    type: Literal["queue.forecast"] = "queue.forecast"
    counter_id: str; made_ts: float; horizons: dict[str, float]   # keys "5","10","15","30" -> expected count
    model: Literal["edge_trend","cloud_gbm"]; mae_recent: float | None = None
class ShelfScan(BaseModel):
    type: Literal["shelf.scan"] = "shelf.scan"
    shelf_id: str; sku_id: str | None; coverage: float; facings: int; capacity_facings: int
    state_raw: ShelfState; occluded: bool = False; method: str = "classical"
    thumb_b64: str | None = None      # JPEG <= 96x96, shelf polygon only; validator: len <= 16384
class ShelfStateChange(BaseModel):
    type: Literal["shelf.state"] = "shelf.state"
    shelf_id: str; sku_id: str | None; from_state: ShelfState; to_state: ShelfState
    gap_started_ts: float | None = None; gap_minutes: float | None = None; consecutive_empty_scans: int
    impact: "ImpactInr | None" = None
class AlertRaised(BaseModel):
    type: Literal["alert.raised"] = "alert.raised"; alert: "Alert"
class AlertAcked(BaseModel):
    type: Literal["alert.acked"] = "alert.acked"; alert_id: str; action: AckAction; by: AckBy; note: str | None = None
class AlertResolved(BaseModel):
    type: Literal["alert.resolved"] = "alert.resolved"; alert_id: str
    reason: Literal["condition_cleared","restocked_observed","false_positive","superseded","timeout","device_back"]
    final_gap_minutes: float | None = None; impact_final: "ImpactInr | None" = None; recovered: "ImpactInr | None" = None
class CameraHealth(BaseModel):
    camera_id: str; status: Literal["ok","stale","black","error"]; fps: float; last_frame_age_s: float; detector: str
class DeviceHeartbeat(BaseModel):
    type: Literal["device.heartbeat"] = "device.heartbeat"
    uptime_s: float; fps: float; infer_ms_p50: float; infer_ms_p95: float; detector: str; model_version: str
    backlog: int; link: LinkState; cameras: list[CameraHealth]; contracts_version: str
    clock_factor: float = 1.0; sim_ts: float | None = None; cpu_pct: float | None = None; mem_mb: float | None = None
class StockReconciled(BaseModel):
    type: Literal["stock.reconciled"] = "stock.reconciled"
    sku_id: str; shelf_id: str | None; visual_units: int; system_units: int; delta_units: int; delta_inr: float
    source: Literal["tally","zoho","manual","mock"]
class OrderRequested(BaseModel):
    type: Literal["order.requested"] = "order.requested"
    sku_id: str; qty: int; channel: AckBy; alert_id: str | None = None; est_cost_inr: float | None = None
class ConfigApplied(BaseModel):
    type: Literal["config.applied"] = "config.applied"; config_version: int; config_hash: str
class SimTruth(BaseModel):
    type: Literal["sim.truth"] = "sim.truth"
    in_store: int; queue_counts: dict[str, int]; shelf_units: dict[str, int]; shelf_facings: dict[str, int]
    served_total: int; abandoned_total: int; footfall_in_total: int; scenario: str

Payload = Annotated[Union[FootfallCrossing, ZoneOccupancy, DwellSample, HeatmapTiles, QueueSnapshot, QueueForecast,
    ShelfScan, ShelfStateChange, AlertRaised, AlertAcked, AlertResolved, DeviceHeartbeat, StockReconciled,
    OrderRequested, ConfigApplied, SimTruth], Field(discriminator="type")]

class Observation(BaseModel):          # produced by CV thread / rules; stamped by EdgeStore.append()
    type: EventType; ts: float; camera_id: str | None = None; payload: Payload
    @model_validator(mode="after") def _same_type(self): assert self.type == self.payload.type; return self

class Event(BaseModel):                # the wire/storage envelope
    event_id: str                      # ULID
    store_id: str; device_id: str; camera_id: str | None = None
    ts: float                          # observation time (frame ts / sim ts)
    hlc: str; seq: int                 # per-device monotonic, gap-free, starts at 1
    type: EventType; cls: EventClass; version: int = 1
    payload: Payload
    created_ts: float                  # wall clock when stamped
    @model_validator(mode="after") def _check(self): assert self.type == self.payload.type and self.cls == EVENT_CLASS[self.type]; return self

def make_event(obs: Observation, *, store_id, device_id, seq: int, hlc: str, created_ts: float | None=None) -> Event
```

### C.4 alerts.py + impact.py

```python
class ImpactInr(BaseModel):
    lost_sales_inr: float; lost_margin_inr: float
    basis: str        # "₹27 × 18/hr × 0.37 h × 0.31" — always filled
    factor: float     # multiplier actually used
    source: str       # citation string from ImpactConfig
class StockoutAlert(BaseModel):          # details for kind=shelf_gap
    shelf_id: str; sku_id: str | None; sku_name: str; gap_minutes: float; coverage: float; facings: int; min_facings: int; consecutive_empty_scans: int
class QueueAlertDetails(BaseModel):      # queue_long / queue_forecast
    counter_id: str; counter_name: str; count: int; est_wait_s: float; forecast: float | None = None; horizon_min: int | None = None; threshold: int
class CameraAlertDetails(BaseModel): camera_id: str; status: str; last_frame_age_s: float
class SyncAlertDetails(BaseModel): backlog: int; down_since_ts: float
class DeviceAlertDetails(BaseModel): device_id: str; last_seen_ts: float
class ShrinkAlertDetails(BaseModel): sku_id: str; sku_name: str; visual_units: int; system_units: int; delta_units: int; delta_inr: float
AlertDetails = Union[StockoutAlert, QueueAlertDetails, CameraAlertDetails, SyncAlertDetails, DeviceAlertDetails, ShrinkAlertDetails]

class Alert(BaseModel):
    alert_id: str; store_id: str; device_id: str; origin: Origin
    kind: AlertKind; severity: Severity; status: AlertStatus = AlertStatus.OPEN
    subject_id: str                      # shelf_id | counter_id | camera_id | device_id | sku_id  (one OPEN alert per (kind, subject_id))
    title_en: str; title_hi: str; message_en: str; message_hi: str    # pre-rendered on edge -> works offline
    details: AlertDetails; impact: ImpactInr | None = None
    actions: list[AckAction]             # digit i on WhatsApp == actions[i-1]
    raised_ts: float; acked_ts: float | None = None; resolved_ts: float | None = None
    ack_action: AckAction | None = None; ack_by: AckBy | None = None
ACTIONS_BY_KIND = {SHELF_GAP:[RESTOCKED, ORDER, FALSE_POSITIVE], QUEUE_LONG:[OPENED_COUNTER, IGNORE], QUEUE_FORECAST:[OPENED_COUNTER, IGNORE],
                   CAMERA_DOWN:[CHECKED], SYNC_BACKLOG:[], DEVICE_OFFLINE:[], SHRINK_SUSPECT:[INVESTIGATE, FALSE_POSITIVE], FOOTFALL_SPIKE:[]}
class AlertAckRequest(BaseModel): action: AckAction; by: AckBy = AckBy.BOARD; note: str | None = None

# impact.py — single source of truth, used by edgerules, sensecloud aggregator and sim
class ImpactConfig(BaseModel):
    lost_sale_factor: float = 0.31                      # share of OOS shoppers who buy elsewhere
    lost_sale_source: str = "Gruen, Corsten & Bharadwaj 2002 (GMA/FMI, 71k shoppers, 29 countries): 31% buy elsewhere, 9% abandon"
    queue_abandon_factor: float = 0.32; queue_abandon_source: str = "Retail queue studies: 32% abandon after long lines; tolerance 5-8 min"
    atv_inr: float = 180.0                              # avg basket; overridden by Tally sales when connected
    baseline_unattended_gap_min: float = 120.0          # assumption: unmonitored gap lasts ~2 h until next manual walk
def rate_per_hour(sku: "SKU", cfg: ImpactConfig) -> float             # mrp × velocity × factor
def lost_sales(sku, gap_minutes: float, cfg) -> ImpactInr             # mrp × velocity × gap_h × factor; margin = × margin_pct/100
def recovered(sku, actual_gap_minutes, cfg) -> ImpactInr              # rate × max(0, baseline − actual)/60
def queue_abandon_risk(count: int, threshold: int, cfg) -> ImpactInr  # max(0,count−threshold+1) × abandon_factor × atv
```

### C.5 config.py (store.yaml schema)

```python
class StoreInfo(BaseModel): store_id: str; name: str; lang: Lang = "hi"; tz: str = "Asia/Kolkata"; tier: Literal["kirana","mini","chain"]="kirana"; owner_whatsapp: str; open_hours: tuple[str,str] = ("08:00","22:00"); address: str | None = None
class MqttConfig(BaseModel): host: str = "localhost"; port: int = 1883; ws_port: int = 9001; username: str | None = None; password: str | None = None; session_expiry_s: int = 604800
class UplinkConfig(BaseModel): mode: UplinkMode = "http"; batch_size: int = 500; interval_s: float = 2.0; heartbeat_s: float = 10.0; max_outbox_rows: int = 50000; mqtt: MqttConfig = MqttConfig()
class DeviceConfig(BaseModel): device_id: str; token: str = "demo-token"; edge_port: int = 8001; cloud_url: str = "http://localhost:8000"; db_path: str = "var/senseedge.db"; uplink: UplinkConfig = UplinkConfig()
class Floorplan(BaseModel): width_px: int = 640; height_px: int = 360; scale_m_per_px: float = 0.02; image: str | None = None; heat_cell_px: int = 20
class HomographyConfig(BaseModel): image_points: list[list[float]]; floor_points: list[list[float]]   # >= 4 pairs
class CameraConfig(BaseModel):
    camera_id: str; source: str                     # "rtsp://..", "file:var/demo_store.mp4", "webcam:0", "synthetic:evening_rush"
    width: int = 640; height: int = 360; fps_sample: float = 4.0
    detector: DetectorKind = "auto"                 # auto => synthetic if source is synthetic:/file of synthetic, else onnx
    anchor: Anchor = "bottom_center"; shelf_scan_interval_s: float = 60.0
    homography: HomographyConfig | None = None      # None => identity (image == floorplan)
    preview_blur_people: bool = True; loop_file: bool = True
class Zone(BaseModel): zone_id: str; camera_id: str; kind: ZoneKind; polygon: list[list[float]]; name: str | None = None
class Line(BaseModel): line_id: str; camera_id: str; kind: LineKind; start: list[float]; end: list[float]; name: str | None = None
class Counter(BaseModel): counter_id: str; name: str; queue_zone_id: str; counter_line_id: str; max_queue: int = 8; default_service_s: float = 45.0
class ShelfReference(BaseModel): shelf_id: str; calibrated_ts: float; raw_coverage_full: float; backing_bgr: list[int]; profile: list[float] | None = None; method: str = "classical"
class ShelfPolygon(BaseModel): shelf_id: str; camera_id: str; name: str; polygon: list[list[float]]; sku_id: str | None = None; capacity_facings: int = 8; min_facings: int = 2; facing_width_px: float | None = None; reference: ShelfReference | None = None
class SKU(BaseModel): sku_id: str; name_en: str; name_hi: str; mrp_inr: float; margin_pct: float = 10.0; velocity_units_per_hr: float; units_per_facing: int = 4; lead_time_days: int = 2; tally_item_name: str | None = None; ondc_item_id: str | None = None; enrolled_images: int = 0
class RulesConfig(BaseModel):
    shelf_partial_coverage: float = 0.80; shelf_empty_coverage: float = 0.25; persistence_scans: int = 3; max_persistence_scans: int = 6
    queue_long_count: int = 4; queue_long_s: float = 60; queue_resolve_s: float = 30; queue_forecast_threshold: int = 6; queue_forecast_horizon_min: int = 15
    queue_min_age_s: float = 5.0; queue_window_s: int = 600; snapshot_interval_s: float = 10; occupancy_interval_s: float = 10; heat_flush_s: float = 60
    camera_down_s: float = 15; black_frame_std: float = 3.0; sync_backlog_warn: int = 1000; sync_backlog_after_s: float = 300
    shrink_min_units: int = 3; shrink_min_inr: float = 200; occlusion_skip_overlap: float = 0.30; footfall_spike_factor: float = 2.5
class RetentionPolicy(BaseModel): telemetry_hours: int = 24; aggregate_days: int = 30; thumbnails_days: int = 7; heatmap_days: int = 90; alerts_days: int = 365; sent_outbox_hours: int = 24
class PrivacyConfig(BaseModel): preview_blur_people: bool = True; shelf_thumbnails: bool = True; retention: RetentionPolicy = RetentionPolicy(); statement: str = "No face recognition; no raw video persisted; track IDs never leave the edge."
class TallyConfig(BaseModel): enabled: bool = False; url: str = "http://localhost:9000"; company: str | None = None
class OndcConfig(BaseModel): enabled: bool = False; gateway_url: str = "http://localhost:8000/mock/ondc"; bpp_id: str = "demo.bpp"; signing: Literal["none","ed25519"] = "none"
class WhatsAppConfig(BaseModel): mode: Literal["simulator","cloud_api","telegram","none"] = "simulator"; to: str | None = None; phone_number_id: str | None = None; token_env: str = "WHATSAPP_TOKEN"; telegram_chat_id: str | None = None; telegram_token_env: str = "TELEGRAM_TOKEN"
class IntegrationsConfig(BaseModel): tally: TallyConfig = TallyConfig(); ondc: OndcConfig = OndcConfig(); whatsapp: WhatsAppConfig = WhatsAppConfig()
class DemoConfig(BaseModel): enabled: bool = False; clock_factor: float = 10.0; default_scenario: str = "baseline"; start_time: str = "17:00"; seed_history_days: int = 30; auto_calibrate_first_scan: bool = True
class StoreConfig(BaseModel):
    schema_version: int = 1; config_version: int = 1
    store: StoreInfo; device: DeviceConfig; floorplan: Floorplan = Floorplan()
    cameras: list[CameraConfig]; zones: list[Zone] = []; lines: list[Line] = []; counters: list[Counter] = []
    shelves: list[ShelfPolygon] = []; skus: list[SKU] = []
    rules: RulesConfig = RulesConfig(); impact: ImpactConfig = ImpactConfig(); privacy: PrivacyConfig = PrivacyConfig()
    integrations: IntegrationsConfig = IntegrationsConfig(); demo: DemoConfig = DemoConfig()
    # validators: unique ids; zone/line/shelf camera_ids exist; counters reference existing zone+line; shelf sku_ids exist; polygons >= 3 points
    def sku(self, sku_id) -> SKU | None; def camera(self, camera_id) -> CameraConfig; def config_hash(self) -> str
def load_store_config(path: str | Path) -> StoreConfig; def dump_store_config(cfg, path) -> None
```

**Canonical demo config** `examples/store_demo.yaml` (geometry is normative; sim, edge and board tests use these numbers):

```yaml
schema_version: 1
store: {store_id: STR-DL-001, name: "Ramesh General Store", lang: hi, tz: Asia/Kolkata, tier: kirana, owner_whatsapp: "+919999900001"}
device: {device_id: EDGE-001, token: demo-token-001, edge_port: 8001, cloud_url: "http://localhost:8000", db_path: var/senseedge.db,
         uplink: {mode: http, batch_size: 500, interval_s: 2.0, heartbeat_s: 10}}
floorplan: {width_px: 640, height_px: 360, scale_m_per_px: 0.02, heat_cell_px: 20}
cameras:
  - {camera_id: cam-synth, source: "synthetic:baseline", width: 640, height: 360, fps_sample: 4, detector: auto, anchor: center, shelf_scan_interval_s: 30, preview_blur_people: false}
zones:
  - {zone_id: store,   camera_id: cam-synth, kind: store, polygon: [[20,20],[620,20],[620,340],[20,340]]}
  - {zone_id: aisle-1, camera_id: cam-synth, kind: aisle, name: "Biscuits & Dairy aisle", polygon: [[130,70],[430,70],[430,200],[130,200]]}
  - {zone_id: aisle-2, camera_id: cam-synth, kind: aisle, name: "Oil aisle", polygon: [[70,130],[125,130],[125,300],[70,300]]}
  - {zone_id: queue-1, camera_id: cam-synth, kind: queue, name: "Counter queue", polygon: [[540,98],[612,98],[612,300],[540,300]]}
lines:
  - {line_id: entrance,       camera_id: cam-synth, kind: entrance, start: [120,315], end: [60,315]}    # IN = moving up (y decreasing)
  - {line_id: counter-1-line, camera_id: cam-synth, kind: counter,  start: [532,98],  end: [532,142]}   # IN = served, moving left out of queue head
counters:
  - {counter_id: counter-1, name: "Main counter", queue_zone_id: queue-1, counter_line_id: counter-1-line, max_queue: 8, default_service_s: 45}
shelves:
  - {shelf_id: shelf-A, camera_id: cam-synth, name: "Dairy",    polygon: [[130,30],[270,30],[270,62],[130,62]], sku_id: AMUL-TAAZA-500, capacity_facings: 9, min_facings: 2, facing_width_px: 15}
  - {shelf_id: shelf-B, camera_id: cam-synth, name: "Biscuits", polygon: [[290,30],[430,30],[430,62],[290,62]], sku_id: PARLE-G-70,     capacity_facings: 9, min_facings: 2, facing_width_px: 15}
  - {shelf_id: shelf-C, camera_id: cam-synth, name: "Oil",      polygon: [[30,130],[62,130],[62,300],[30,300]], sku_id: FORTUNE-OIL-1L, capacity_facings: 7, min_facings: 1, facing_width_px: 22}
skus:
  - {sku_id: AMUL-TAAZA-500, name_en: "Amul Taaza 500ml", name_hi: "अमूल ताज़ा 500ml", mrp_inr: 27, margin_pct: 8,  velocity_units_per_hr: 18, units_per_facing: 4, lead_time_days: 1, tally_item_name: "Amul Taaza 500ml", ondc_item_id: I-AMUL-500}
  - {sku_id: PARLE-G-70,     name_en: "Parle-G 70g",      name_hi: "पारले-जी 70g",     mrp_inr: 10, margin_pct: 12, velocity_units_per_hr: 8,  units_per_facing: 4, lead_time_days: 2, tally_item_name: "Parle-G 70g",      ondc_item_id: I-PARLEG-70}
  - {sku_id: FORTUNE-OIL-1L, name_en: "Fortune Sunflower Oil 1L", name_hi: "फॉर्च्यून तेल 1L", mrp_inr: 150, margin_pct: 6, velocity_units_per_hr: 2, units_per_facing: 2, lead_time_days: 3, tally_item_name: "Fortune Sunflower 1L", ondc_item_id: I-FORTUNE-1L}
rules: {persistence_scans: 3, queue_long_count: 4, queue_long_s: 60, queue_forecast_threshold: 6}
impact: {lost_sale_factor: 0.31, atv_inr: 180, baseline_unattended_gap_min: 120}
privacy: {preview_blur_people: true, shelf_thumbnails: true}
integrations: {tally: {enabled: true, url: "http://localhost:9000"}, ondc: {enabled: true, gateway_url: "http://localhost:8000/mock/ondc", bpp_id: ramesh-store.ondc.demo}, whatsapp: {mode: simulator, to: "+919999900001"}}
demo: {enabled: true, clock_factor: 10, default_scenario: baseline, start_time: "17:00", seed_history_days: 30, auto_calibrate_first_scan: true}
```

### C.6 synthetic.py (shared sim ↔ CV constants)

```python
class SyntheticPalette:           # BGR
    FLOOR=(235,235,235); WALL=(60,60,60); SHELF_BACKING=(110,110,110); COUNTER=(80,120,170); CASHIER=(200,200,0)
    SHOPPER=(255,0,255); SHOPPER_HSV_LO=(140,120,120); SHOPPER_HSV_HI=(165,255,255)   # magenta; per-shopper V jitter 180-255
    FACING_COLOURS={"AMUL-TAAZA-500":(230,200,60),"PARLE-G-70":(40,120,230),"FORTUNE-OIL-1L":(40,160,240)}
    TEXT=(255,255,255)
SHOPPER_SIZE_PX=20; SIM_DT_S=0.25; SHOPPER_SPEED_PX_S=40; QUEUE_SPACING_PX=26; MIN_SEPARATION_PX=22
```

### C.7 api.py — REST models

```python
class SyncStatus(BaseModel): link: LinkState; uplink: UplinkMode; cloud_reachable: bool; backlog: int; backlog_by_class: dict[str,int]; last_ack_ts: float | None; last_ack_seq: int | None; replayed_since_restore: int; replay_total_at_restore: int; seq_ok: bool; down_since_ts: float | None
class HealthStatus(BaseModel): status: Literal["ok","degraded","starting"]; store_id: str; device_id: str; uptime_s: float; contracts_version: str; detector: str; model_version: str; cameras: list[CameraHealth]; sync: SyncStatus; sim_ts: float | None; clock_factor: float; fps: float; infer_ms_p50: float
class KpiToday(BaseModel):
    store_id: str; date: str; as_of_ts: float; footfall_in: int; footfall_out: int; occupancy_now: int; visual_transactions: int; conversion_pct: float | None
    atv_inr: float | None; osa_pct: float; gap_minutes_total: float; avg_wait_s: float | None; max_wait_s: float | None; abandoned: int
    lost_sales_inr: float; lost_margin_inr: float; recovered_inr: float; alerts_open: int; alerts_today: int; deltas: dict[str, float | None] = {}   # metric -> delta vs yesterday
class KpiDaily(BaseModel): store_id: str; date: str; footfall_in: int; footfall_out: int; visual_transactions: int; conversion_pct: float | None; atv_inr: float | None; osa_pct: float; gap_minutes_total: float; avg_wait_s: float | None; max_wait_s: float | None; abandoned: int; lost_sales_inr: float; recovered_inr: float; shrink_inr: float; alerts_total: int
class SeriesPoint(BaseModel): ts: float; value: float
class Series(BaseModel): metric: str; bucket_s: int; points: list[SeriesPoint]
class ShelfStateView(BaseModel): shelf_id: str; name: str; sku_id: str | None; sku_name: str; state: ShelfState; coverage: float; facings: int; capacity_facings: int; min_facings: int; consecutive_empty_scans: int; persistence_required: int; gap_started_ts: float | None; gap_minutes: float | None; last_scan_ts: float | None; occluded: bool; impact_open: ImpactInr | None; has_reference: bool
class QueueView(BaseModel): counter_id: str; name: str; snapshot: QueueSnapshot | None; forecast: QueueForecast | None; open_alert_id: str | None
class HeatCell(BaseModel): x: int; y: int; dwell_s: float; visits: int
class HeatmapResponse(BaseModel): camera_id: str | None; cell_px: int; width_cells: int; height_cells: int; from_ts: float; to_ts: float; cells: list[HeatCell]; max_dwell_s: float
class ZonesUpdate(BaseModel): zones: list[Zone]; lines: list[Line]; counters: list[Counter]
class ShelvesUpdate(BaseModel): shelves: list[ShelfPolygon]
class LinkRequest(BaseModel): state: LinkState
class ScenarioRequest(BaseModel): name: str; params: dict[str, Any] = {}
class ScenarioStatus(BaseModel): active: str; since_ts: float; params: dict[str, Any]; available: list[str]; clock_factor: float; sim_ts: float
class ChaosRequest(BaseModel): kind: Literal["freeze","drop","blackout","noise"]; enabled: bool; seconds: float | None = None; p: float | None = None
class WhatsAppReply(BaseModel): alert_id: str; digit: int; from_number: str | None = None
class SkuEnrolResponse(BaseModel): sku_id: str; enrolled: int; backend: str
class DailySummary(BaseModel): store_id: str; date: str; lang: Lang; text: str; kpis: KpiToday
class ModelStatus(BaseModel): local: "ModelManifest | None"; remote: "ModelManifest | None"; active_model_id: str; active_version: str; update_available: bool; assigned_version: str | None
class Command(BaseModel): command_id: str; device_id: str; kind: Literal["ack_alert","apply_config","set_link","set_scenario","model_update","ping"]; payload: dict[str, Any]; created_ts: float
class IngestBatch(BaseModel): batch_id: str; device_id: str; store_id: str; sent_ts: float; cursor: int; events: list[Event] = Field(max_length=500); backlog: int; contracts_version: str
class IngestAck(BaseModel): batch_id: str; accepted: int; duplicates: int; rejected: list[dict[str,str]]; last_seq: int | None; seq_ok: bool; seq_gaps: list[int]; commands: list[Command]; server_ts: float
class Store(BaseModel): store_id: str; name: str; tier: str; lang: Lang; tz: str; device_ids: list[str]; registered_ts: float; config: StoreConfig | None = None
class DeviceStatus(BaseModel): device_id: str; store_id: str; status: Literal["online","offline","never"]; last_seen_ts: float | None; model_version: str | None; assigned_version: str | None; version_drift: bool; fps: float | None; backlog: int | None; link: LinkState | None; uptime_s: float | None
class FleetView(BaseModel): devices: list[DeviceStatus]; online: int; offline: int; manifest_version: str | None
class ChainRankRow(BaseModel): store_id: str; name: str; value: float; rank: int; footfall_in: int; normalised: float | None
class ChainRank(BaseModel): metric: str; date: str; rows: list[ChainRankRow]
class KpiRange(BaseModel): today: KpiToday; daily: list[KpiDaily]
class FitReport(BaseModel): model: str; target: str; trained_ts: float; n_rows: int; mae_holdout: float; mae_baseline: float; features: list[str]; horizons: list[int] = []
class FootfallForecastDay(BaseModel): date: str; predicted: float; lower: float; upper: float; is_festival: bool; festival_name: str | None; days_to_festival: int | None
class FootfallForecast(BaseModel): store_id: str; made_ts: float; days: list[FootfallForecastDay]; mae_holdout: float | None
class ReorderSuggestion(BaseModel): sku_id: str; name_en: str; name_hi: str; system_units: int | None; visual_units: int | None; forecast_units_lead: float; safety_stock: float; suggest_qty: int; est_cost_inr: float; reason: str
class ReconcileRow(BaseModel): sku_id: str; name: str; shelf_id: str | None; visual_units: int; system_units: int; delta_units: int; delta_inr: float; flagged: bool
class ReconcileReport(BaseModel): store_id: str; ts: float; source: str; rows: list[ReconcileRow]; shrink_inr_total: float; alerts_raised: int
class OndcPublishRequest(BaseModel): sku_id: str; available: bool; qty: int | None = None
class OndcAck(BaseModel): ok: bool; message_id: str; item_id: str; available: bool; ts: float; signed: bool = False
class OutboundMessage(BaseModel): message_id: str; channel: str; to: str; text: str; buttons: list[str]; alert_id: str | None; store_id: str; created_ts: float; status: Literal["queued","sent","delivered","failed"]; delivered_ts: float | None = None
class DeliveryReceipt(BaseModel): message_id: str; status: Literal["sent","failed"]; detail: str | None = None
class DailyReport(BaseModel): store_id: str; date: str; kpis: KpiDaily; top_alerts: list[Alert]; gap_minutes_by_shelf: dict[str,float]; queue_by_hour: dict[str,float]; forecast_mae: float | None; whatsapp_text_hi: str; whatsapp_text_en: str
class IntegrationsStatus(BaseModel): tally: dict[str, Any]; ondc: dict[str, Any]; whatsapp: dict[str, Any]
class ManifestPublishRequest(BaseModel): manifest: "ModelManifest"
class RolloutRequest(BaseModel): model_id: str; version: str; canary_pct: int
```

### C.8 ws.py

```python
WsKind = Literal["hello","event","alert","kpi","health","sync","scenario","notification","device","forecast"]
class WsMessage(BaseModel): kind: WsKind; ts: float; store_id: str | None = None; data: dict[str, Any]
# edge /ws/live emits: hello{device_id,store_id,contracts_version}, event{Event} (all non-telemetry events), alert{Alert}, kpi{KpiToday} (every 5 s),
#   health{HealthStatus} (10 s), sync{SyncStatus} (on change + 2 s while replaying), scenario{ScenarioStatus}, forecast{QueueForecast}
# cloud /v1/ws emits: hello, alert, kpi, device{DeviceStatus}, notification{OutboundMessage}, forecast, sync{device_id, last_seq, seq_ok, accepted}
```

### C.9 topics.py (MQTT, optional uplink)

```python
def topic(store_id, device_id, cls: EventClass) -> str   # "rs/v1/{store}/{device}/{cls}"   payload = Event JSON (one per message)
def status_topic(store_id, device_id) -> str            # "rs/v1/{store}/{device}/status" retained LWT "online"|"offline"
def cmd_topic(store_id, device_id) -> str               # "rs/v1/{store}/{device}/cmd"   payload = Command JSON
EXPIRY_S: dict[EventClass, int | None] = {TELEMETRY: 3600, AGGREGATE: 86400, ALERT: None, TXN: None, CONFIG: 86400}   # None = never
EVICTABLE = (TELEMETRY, AGGREGATE)        # outbox overflow evicts these oldest-first; ALERT/TXN are never evicted (RejectNewData semantics)
QOS = 1; MQTT_VERSION = 5; CLEAN_START = False
```

### C.10 db.py — DDL (SQLAlchemy Core tables; SQLite on edge and demo cloud, Postgres via compose)

```sql
-- EDGE (edge_metadata) ---------------------------------------------------------
CREATE TABLE events (event_id TEXT PRIMARY KEY, store_id TEXT NOT NULL, device_id TEXT NOT NULL, camera_id TEXT, ts REAL NOT NULL,
  hlc TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL, cls TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  payload JSON NOT NULL, created_ts REAL NOT NULL);
CREATE UNIQUE INDEX ux_events_device_seq ON events(device_id, seq);  CREATE INDEX ix_events_ts ON events(store_id, ts);  CREATE INDEX ix_events_type_ts ON events(type, ts);
CREATE TABLE outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL REFERENCES events(event_id), cls TEXT NOT NULL,
  enqueued_ts REAL NOT NULL, expires_ts REAL, sent_ts REAL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, evicted_ts REAL);
CREATE INDEX ix_outbox_pending ON outbox(sent_ts, evicted_ts, id);
CREATE TABLE device_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);          -- seq_next, hlc_last, link_state, replay stats, config_version
CREATE TABLE alerts (alert_id TEXT PRIMARY KEY, store_id TEXT, device_id TEXT, origin TEXT, kind TEXT, severity TEXT, status TEXT, subject_id TEXT,
  raised_ts REAL, acked_ts REAL, resolved_ts REAL, ack_action TEXT, ack_by TEXT, lost_sales_inr REAL, recovered_inr REAL, doc JSON NOT NULL);
CREATE INDEX ix_alerts_status ON alerts(status, raised_ts);  CREATE UNIQUE INDEX ux_alert_open ON alerts(kind, subject_id) WHERE status <> 'resolved';  -- partial index; SQLite+PG both support
CREATE TABLE shelf_state (shelf_id TEXT PRIMARY KEY, sku_id TEXT, state TEXT, coverage REAL, facings INTEGER, consecutive_empty_scans INTEGER,
  persistence_required INTEGER, gap_started_ts REAL, last_scan_ts REAL, gap_minutes_today REAL DEFAULT 0, fp_count INTEGER DEFAULT 0, reference JSON);
CREATE TABLE queue_state (counter_id TEXT PRIMARY KEY, snapshot JSON, forecast JSON, updated_ts REAL);
CREATE TABLE heatmap_cells (camera_id TEXT, cell_x INTEGER, cell_y INTEGER, hour_bucket INTEGER, dwell_s REAL, visits INTEGER, PRIMARY KEY(camera_id, cell_x, cell_y, hour_bucket));
CREATE TABLE kpi_daily (store_id TEXT, date TEXT, footfall_in INTEGER, footfall_out INTEGER, visual_transactions INTEGER, conversion_pct REAL, atv_inr REAL,
  osa_pct REAL, gap_minutes_total REAL, avg_wait_s REAL, max_wait_s REAL, abandoned INTEGER, lost_sales_inr REAL, lost_margin_inr REAL, recovered_inr REAL,
  shrink_inr REAL, alerts_total INTEGER, updated_ts REAL, PRIMARY KEY(store_id, date));
CREATE TABLE sku_enrolment (sku_id TEXT, idx INTEGER, embedding BLOB, PRIMARY KEY(sku_id, idx));
-- CLOUD (cloud_metadata): same events/alerts(+store index)/heatmap_cells(+store_id)/kpi_daily/shelf_state(+store_id)/queue_state(+store_id) PLUS:
CREATE TABLE stores (store_id TEXT PRIMARY KEY, name TEXT, tier TEXT, lang TEXT, tz TEXT, config JSON, registered_ts REAL);
CREATE TABLE devices (device_id TEXT PRIMARY KEY, store_id TEXT, token TEXT, last_seen_ts REAL, last_seq INTEGER, model_version TEXT, fps REAL, backlog INTEGER, link TEXT, uptime_s REAL, status TEXT);
CREATE TABLE ingest_log (batch_id TEXT PRIMARY KEY, device_id TEXT, received_ts REAL, accepted INTEGER, duplicates INTEGER, first_seq INTEGER, last_seq INTEGER, seq_ok INTEGER);
CREATE TABLE commands (command_id TEXT PRIMARY KEY, device_id TEXT, kind TEXT, payload JSON, created_ts REAL, delivered_ts REAL);
CREATE TABLE notifications (message_id TEXT PRIMARY KEY, store_id TEXT, channel TEXT, to_addr TEXT, text TEXT, buttons JSON, alert_id TEXT, status TEXT, created_ts REAL, delivered_ts REAL);
CREATE TABLE series_5m (store_id TEXT, metric TEXT, bucket_ts REAL, value REAL, n INTEGER, PRIMARY KEY(store_id, metric, bucket_ts));
CREATE TABLE agg_cursor (store_id TEXT PRIMARY KEY, last_event_seq JSON, updated_ts REAL);
CREATE TABLE stock_recon (id TEXT PRIMARY KEY, store_id TEXT, sku_id TEXT, shelf_id TEXT, ts REAL, visual_units INTEGER, system_units INTEGER, delta_units INTEGER, delta_inr REAL, source TEXT);
CREATE TABLE forecasts (id TEXT PRIMARY KEY, store_id TEXT, counter_id TEXT, made_ts REAL, horizon_min INTEGER, predicted REAL, actual REAL, model TEXT);
CREATE TABLE model_manifests (version TEXT PRIMARY KEY, doc JSON, published_ts REAL, active INTEGER);
CREATE TABLE festivals (date TEXT, name TEXT, region TEXT, weight REAL, PRIMARY KEY(date, name));
CREATE TABLE ondc_log (message_id TEXT PRIMARY KEY, store_id TEXT, item_id TEXT, available INTEGER, qty INTEGER, ts REAL, payload JSON);
```
```python
edge_metadata: MetaData; cloud_metadata: MetaData
def create_all(engine, which: Literal["edge","cloud"]) -> None
def insert_ignore(conn, table, rows: list[dict]) -> int     # sqlite: INSERT OR IGNORE; postgresql: ON CONFLICT DO NOTHING; returns inserted count
def sqlite_engine(path, *, wal=True, synchronous_full=True) -> Engine   # applies PRAGMAs on connect
```

### C.11 manifest.py (OTA)

```python
class ModelIO(BaseModel): shape: list[int]; layout: str = "NCHW"; normalize: str = "0-1"; letterbox: bool = True
class ModelEntry(BaseModel): model_id: str; version: str; task: Literal["person_detect","shelf_gap","sku_embed"]; format: Literal["onnx","pt","tflite"]; file: str; sha256: str; size_bytes: int; input: ModelIO; output_format: Literal["yolov8","yolox","synthetic","none"]; classes: list[str]; source_url: str | None; license: str; min_runtime: str; notes: str = ""
class RolloutPolicy(BaseModel): channel: Literal["canary","stable"] = "stable"; canary_pct: int = 10; abort_failure_pct: int = 5; pinned_devices: dict[str, str] = {}   # device_id -> version
class ModelManifest(BaseModel): manifest_version: int = 1; version: str; generated_ts: float; models: list[ModelEntry]; rollout: RolloutPolicy = RolloutPolicy()
def assigned_version(manifest: ModelManifest, device_id: str, model_id: str, versions_available: list[str]) -> str   # pinned > canary hash(device_id)%100 < canary_pct -> newest, else stable
```

### C.12 i18n.py

```python
TEMPLATES: dict[str, dict[Lang, str]] = {
 "shelf_gap.title": {"en": "{sku_name} shelf empty", "hi": "{sku_name} की शेल्फ खाली"},
 "shelf_gap.msg":   {"en": "⚠️ {sku_name} shelf has been empty for {gap_min} min. Est. lost sales ₹{lost_inr} ({basis}). Reply 1 = restocked, 2 = order from distributor, 3 = false alert",
                     "hi": "⚠️ {sku_name} की शेल्फ {gap_min} मिनट से खाली है। अनुमानित नुकसान ₹{lost_inr}। जवाब दें: 1 = भर दिया, 2 = डिस्ट्रीब्यूटर को ऑर्डर, 3 = गलत अलर्ट"},
 "queue_long.title": {"en": "Long queue at {counter_name}", "hi": "{counter_name} पर लंबी लाइन"},
 "queue_long.msg":  {"en": "🧾 {count} customers waiting at {counter_name} (~{wait_min} min). Risk ₹{risk_inr}. Open a second counter? Reply 1 = opened, 2 = ignore",
                     "hi": "🧾 {counter_name} पर {count} ग्राहक लाइन में (~{wait_min} मिनट)। जोखिम ₹{risk_inr}। दूसरा काउंटर खोलें? जवाब: 1 = खोल दिया, 2 = रहने दो"},
 "queue_forecast.title": {"en": "Queue build-up expected", "hi": "लाइन बढ़ने वाली है"},
 "queue_forecast.msg": {"en": "⏱️ Queue at {counter_name} expected to reach {forecast} in {horizon} min. Get a second counter ready. Reply 1 = ready, 2 = ignore",
                        "hi": "⏱️ {horizon} मिनट में {counter_name} पर लाइन {forecast} तक पहुँच सकती है। दूसरा काउंटर तैयार रखें। जवाब: 1 = तैयार, 2 = रहने दो"},
 "camera_down.title": {"en": "Camera {camera_id} down", "hi": "कैमरा {camera_id} बंद"},
 "camera_down.msg": {"en": "📷 Camera {camera_id} is not sending frames. Check power/cable. Reply 1 = checked", "hi": "📷 कैमरा {camera_id} से वीडियो नहीं आ रहा। बिजली/केबल जाँचें। जवाब: 1 = देख लिया"},
 "sync_backlog.title": {"en": "Internet down, data safe", "hi": "इंटरनेट बंद, डेटा सुरक्षित"},
 "sync_backlog.msg": {"en": "📡 Internet down for {minutes} min; {backlog} records saved locally, nothing lost.", "hi": "📡 इंटरनेट {minutes} मिनट से बंद; {backlog} रिकॉर्ड लोकल सेव हैं, कुछ नहीं खोया।"},
 "device_offline.title": {"en": "Edge device offline", "hi": "एज डिवाइस ऑफ़लाइन"},
 "device_offline.msg": {"en": "🔌 {device_id} offline since {since}.", "hi": "🔌 {device_id} {since} से ऑफ़लाइन है।"},
 "shrink_suspect.title": {"en": "Stock mismatch: {sku_name}", "hi": "स्टॉक में अंतर: {sku_name}"},
 "shrink_suspect.msg": {"en": "🔎 {sku_name}: system shows {system_units}, shelf shows {visual_units}. Gap worth ₹{delta_inr}. Reply 1 = investigate, 2 = false alert",
                        "hi": "🔎 {sku_name}: सिस्टम में {system_units} यूनिट, शेल्फ पर {visual_units}। ₹{delta_inr} का अंतर। जवाब: 1 = जाँच करो, 2 = गलत अलर्ट"},
 "footfall_spike.title": {"en": "Footfall spike", "hi": "भीड़ बढ़ी"},
 "footfall_spike.msg": {"en": "📈 {count} visitors in the last 15 min ({factor}x usual).", "hi": "📈 पिछले 15 मिनट में {count} ग्राहक (सामान्य से {factor}x)।"},
 "daily_summary.msg": {"en": "📊 {store_name} {date}: {footfall_in} visitors, {visual_transactions} billed ({conversion_pct}%), OSA {osa_pct}%, avg wait {avg_wait_min} min, lost ₹{lost_inr}, saved ₹{recovered_inr}. Tomorrow's order: {order_lines}",
                       "hi": "📊 {store_name} {date}: {footfall_in} ग्राहक आए, {visual_transactions} बिल ({conversion_pct}%), OSA {osa_pct}%, औसत इंतज़ार {avg_wait_min} मिनट, नुकसान ₹{lost_inr}, बचाया ₹{recovered_inr}। कल का ऑर्डर: {order_lines}"},
 "action.restocked": {"en":"Restocked","hi":"भर दिया"}, "action.order": {"en":"Order","hi":"ऑर्डर करो"}, "action.false_positive": {"en":"False alert","hi":"गलत अलर्ट"},
 "action.opened_counter": {"en":"Opened counter","hi":"काउंटर खोल दिया"}, "action.ignore": {"en":"Ignore","hi":"रहने दो"}, "action.checked": {"en":"Checked","hi":"देख लिया"}, "action.investigate": {"en":"Investigate","hi":"जाँच करो"},
}
def render(key: str, lang: Lang, **params) -> str        # missing param -> "?" never raises; numbers formatted ₹ with Indian grouping via fmt_inr()
def fmt_inr(x: float) -> str                             # 1234567.8 -> "12,34,568"
```

### C.13 interfaces.py — Protocols (exact signatures)

```python
@dataclass class Frame: ts: float; camera_id: str; image: np.ndarray; seq: int            # BGR uint8 HxWx3
@dataclass class Detection: bbox: tuple[float,float,float,float]; conf: float; cls: int = 0
@dataclass class Track: track_id: int; bbox: tuple[float,float,float,float]; conf: float; age: int; hits: int; time_since_update: int; confirmed: bool
    def anchor(self, kind: Anchor) -> tuple[float,float]
@dataclass class Crossing: line_id: str; line_kind: LineKind; track_id: int; direction: Direction; ts: float
@dataclass class AnalyticsUpdate: ts: float; camera_id: str; zone_members: dict[str, list[int]]; crossings: list[Crossing]; dwell_samples: list[DwellSample]; occupancy: list[ZoneOccupancy]; footfall: list[FootfallCrossing]; heat: HeatmapTiles | None
@dataclass class CoverageResult: coverage: float; facings: int; raw_coverage: float; method: str; debug: dict[str, float]
class SourceError(Exception)

class FrameSource(Protocol):
    camera_id: str
    def open(self) -> None; def read(self) -> Frame | None; def close(self) -> None      # read() blocks; None = end of stream; raises SourceError if fatal
    @property def size(self) -> tuple[int,int]; @property def nominal_fps(self) -> float
class SyntheticControl(Protocol):                      # also implemented by synthetic FrameSource
    def apply_scenario(self, name: str, params: dict) -> ScenarioStatus; def scenario_status(self) -> ScenarioStatus
    def restock(self, shelf_id: str, units: int | None = None) -> None; def set_clock_factor(self, factor: float) -> None
    def chaos(self, req: ChaosRequest) -> None; def truth(self) -> SimTruth
class Detector(Protocol):
    name: str; model_version: str
    def detect(self, image: np.ndarray) -> list[Detection]; def warmup(self) -> None
class Tracker(Protocol):
    def update(self, detections: list[Detection], ts: float) -> list[Track]; def reset(self) -> None     # returns confirmed + tentative; confirmed flag set
class PointMapper(Protocol):
    def to_floor(self, pts: np.ndarray) -> np.ndarray   # [N,2] image px -> floor px
    def to_image(self, pts: np.ndarray) -> np.ndarray
class ZoneEngine(Protocol):
    def __init__(self, camera: CameraConfig, zones: list[Zone], lines: list[Line], mapper: PointMapper, rules: RulesConfig, floorplan: Floorplan): ...
    def update(self, tracks: list[Track], ts: float) -> AnalyticsUpdate; def flush(self, ts: float) -> AnalyticsUpdate   # flush = end-of-day/exit
class QueueAnalyzer(Protocol):
    def __init__(self, counter: Counter, rules: RulesConfig, day_start_ts: float): ...
    def update(self, upd: AnalyticsUpdate) -> QueueSnapshot | None; def state(self) -> QueueSnapshot; def reset_day(self, day_start_ts) -> None
class EdgeQueueForecaster(Protocol):
    def observe(self, snap: QueueSnapshot) -> None; def predict(self, ts: float) -> QueueForecast | None; def set_cloud_forecast(self, fc: QueueForecast) -> None
class CoverageEstimator(Protocol):
    def calibrate(self, image: np.ndarray, shelf: ShelfPolygon) -> ShelfReference
    def estimate(self, image: np.ndarray, shelf: ShelfPolygon, ref: ShelfReference | None) -> CoverageResult
class ShelfStateMachine(Protocol):
    def __init__(self, shelves: list[ShelfPolygon], skus: list[SKU], rules: RulesConfig, impact: ImpactConfig): ...
    def apply(self, scan: ShelfScan, ts: float) -> ShelfStateChange | None; def view(self, shelf_id) -> ShelfStateView; def views(self) -> list[ShelfStateView]
    def feedback_false_positive(self, shelf_id) -> int; def restore(self, rows: list[dict]) -> None; def osa_pct(self, ts) -> float; def gap_minutes_today(self, ts) -> float
class SkuIdentifier(Protocol):
    backend: str
    def enrol(self, sku_id: str, images: list[np.ndarray]) -> int; def identify(self, crop: np.ndarray, hint_sku_id: str | None) -> tuple[str | None, float]
class RuleEngine(Protocol):
    def __init__(self, cfg: StoreConfig): ...
    def on_shelf_change(self, ch: ShelfStateChange, view: ShelfStateView, ts: float) -> list[Observation]
    def on_queue(self, snap: QueueSnapshot, forecast: QueueForecast | None, ts: float) -> list[Observation]
    def on_health(self, hb: DeviceHeartbeat, ts: float) -> list[Observation]
    def on_sync(self, sync: SyncStatus, ts: float) -> list[Observation]
    def on_ack(self, alert_id: str, action: AckAction, by: AckBy, ts: float) -> list[Observation]     # returns alert.acked (+alert.resolved if FP)
    def open_alerts(self) -> list[Alert]; def get(self, alert_id) -> Alert | None; def restore(self, alerts: list[Alert]) -> None
class EdgeStore(Protocol):
    def __init__(self, cfg: StoreConfig): ...
    def append(self, observations: list[Observation]) -> list[Event]       # ONE transaction: stamps seq/hlc/event_id, inserts events + outbox rows
    def pending(self, limit: int) -> list[tuple[int, Event]]; def mark_sent(self, outbox_ids: list[int], ts: float) -> None; def mark_failed(self, outbox_ids, error: str) -> None
    def backlog(self) -> dict[str,int]; def evict_overflow(self, max_rows: int) -> int; def expire(self, now_ts) -> int
    def get_state(self, key, default=None) -> str | None; def set_state(self, key, value: str) -> None
    def upsert_alert(self, a: Alert); def alerts(self, status: AlertStatus | None, limit=100) -> list[Alert]; def alert(self, alert_id) -> Alert | None
    def upsert_shelf(self, v: ShelfStateView, reference: ShelfReference | None); def shelves(self) -> list[dict]
    def upsert_queue(self, counter_id, snap: QueueSnapshot | None, fc: QueueForecast | None); def queues(self) -> list[dict]
    def heat_add(self, camera_id, tiles: HeatmapTiles); def heat_query(self, camera_id | None, from_ts, to_ts) -> HeatmapResponse
    def kpi_today(self, ts: float) -> KpiToday; def upsert_kpi_daily(self, row: KpiDaily); def kpi_daily(self, date) -> KpiDaily | None
    def purge(self, policy: RetentionPolicy, now_ts: float) -> dict[str,int]; def close(self) -> None
class Uplink(Protocol):
    mode: UplinkMode
    async def connect(self) -> None; async def send(self, batch: IngestBatch) -> IngestAck; async def close(self) -> None; @property def connected(self) -> bool
class LinkController(Protocol):
    @property def state(self) -> LinkState; def cut(self) -> None; def restore(self) -> None; def subscribe(self, cb: Callable[[LinkState], None]) -> None
class Notifier(Protocol):
    channel: str
    async def send(self, msg: OutboundMessage) -> DeliveryReceipt
class ErpClient(Protocol):
    source: str
    def stock_summary(self) -> dict[str, int]; def sales_today(self) -> dict[str, float]   # {"sales_inr":..,"transactions":..}
    def post_stock_journal(self, adjustments: dict[str, int]) -> bool; def post_purchase_order(self, lines: list[ReorderSuggestion]) -> str
class OndcPublisher(Protocol):
    async def publish_availability(self, store_id: str, item_id: str, available: bool, qty: int | None) -> OndcAck
class CloudQueueForecaster(Protocol):
    def fit(self, history: "pd.DataFrame") -> FitReport; def predict(self, recent: "pd.DataFrame", now_ts: float) -> dict[str, float]; def report(self) -> FitReport | None
class CloudFootfallForecaster(Protocol):
    def fit(self, daily: "pd.DataFrame") -> FitReport; def predict_days(self, start_date: str, n: int) -> list[FootfallForecastDay]
```
**History DataFrame contract** (sim → forecasting; both agents code to this): minute-level columns `ts, store_id, counter_id, queue_count, arrivals_pm, service_pm, footfall_in_15m, occupancy, hour, dow, minute_of_day, is_festival, festival_weight, days_to_festival, is_salary_week`; daily columns `date, store_id, footfall_in, transactions, dow, is_festival, festival_weight, days_to_festival, is_salary_week, rain_flag`.

### C.14 registry.py + testing.py

```python
IMPLEMENTATIONS = {
 "frame_source.file":"retailsense_edgecv.source:FileFrameSource", "frame_source.rtsp":"retailsense_edgecv.source:RtspFrameSource", "frame_source.webcam":"retailsense_edgecv.source:WebcamFrameSource",
 "frame_source.synthetic":"retailsense_sim.video:SyntheticFrameSource",
 "detector.synthetic":"retailsense_edgecv.detector_synthetic:SyntheticDetector", "detector.onnx":"retailsense_edgecv.detector_onnx:OnnxPersonDetector", "detector.ultralytics":"retailsense_edgecv.detector_ultralytics:UltralyticsDetector",
 "tracker":"retailsense_edgecv.tracker:ByteTrackLite", "homography":"retailsense_edgecv.homography:Homography", "annotator":"retailsense_edgecv.annotate:annotate_frame",
 "zone_engine":"retailsense_edgeanalytics.zones:ZoneEngine", "queue_analyzer":"retailsense_edgequeue.queue:QueueAnalyzer", "queue_forecaster.edge":"retailsense_edgequeue.forecast:TrendForecaster",
 "coverage_estimator":"retailsense_edgeshelf.coverage:ClassicalCoverageEstimator", "shelf_state_machine":"retailsense_edgeshelf.state:ShelfStateMachine", "sku_identifier":"retailsense_edgeshelf.sku:TaggedSkuIdentifier", "shelf_thumb":"retailsense_edgeshelf.thumbs:shelf_thumbnail",
 "rule_engine":"retailsense_edgerules.engine:RuleEngine", "edge_store":"retailsense_edgestore.store:EdgeStore", "retention":"retailsense_edgestore.retention:RetentionJob",
 "uplink.http":"retailsense_edgeuplink.http:HttpUplink", "uplink.mqtt":"retailsense_edgeuplink.mqtt:MqttUplink", "link_controller":"retailsense_edgeuplink.link:LinkController", "sync_worker":"retailsense_edgeuplink.sync:SyncWorker",
 "forecaster.queue":"retailsense_forecasting.queue_forecaster:QueueForecaster", "forecaster.footfall":"retailsense_forecasting.footfall_forecaster:FootfallForecaster", "reorder":"retailsense_forecasting.reorder:suggest_reorder",
 "history_generator":"retailsense_sim.history:generate_history", "floorplan_renderer":"retailsense_sim.floorplan:render_floorplan",
 "notifier.simulator":"retailsense_integrations.whatsapp:WhatsAppSimulator", "notifier.cloud_api":"retailsense_integrations.whatsapp:WhatsAppCloudNotifier", "notifier.telegram":"retailsense_integrations.telegram:TelegramNotifier",
 "erp.tally":"retailsense_integrations.tally:TallyClient", "tally_mock_app":"retailsense_integrations.tally_mock:create_app", "ondc":"retailsense_integrations.ondc:OndcStubPublisher",
 "reconcile":"retailsense_integrations.reconcile:reconcile", "integrations_router":"retailsense_integrations.routers:build_router",
}
FAKES = {"frame_source.*":"retailsense_contracts.testing:FakeFrameSource", "detector.*":"retailsense_contracts.testing:FakeDetector", "tracker":"...:FakeTracker", "homography":"...:IdentityMapper", "zone_engine":"...:FakeZoneEngine", "queue_analyzer":"...:FakeQueueAnalyzer", "queue_forecaster.edge":"...:FakeEdgeForecaster", "coverage_estimator":"...:FakeCoverageEstimator", "shelf_state_machine":"...:FakeShelfStateMachine", "sku_identifier":"...:FakeSkuIdentifier", "rule_engine":"...:FakeRuleEngine", "edge_store":"...:InMemoryEdgeStore", "uplink.*":"...:FakeUplink", "link_controller":"...:SimpleLinkController", "notifier.*":"...:FakeNotifier", "erp.*":"...:FakeErp", "ondc":"...:FakeOndc", "forecaster.*":"...:FakeForecaster", "history_generator":"...:fake_history", "reconcile":"...:fake_reconcile"}
class Unavailable(RuntimeError)
def resolve(key: str, *, allow_fake: bool = True) -> Any        # import real; on ImportError -> fake (logs WARNING "using fake for {key}"); else raise Unavailable
def is_real(key) -> bool
```
`testing.py` fakes must satisfy the Protocols and be deterministic: `FakeFrameSource(n_frames, size, script=[(ts, [bbox...])])` draws magenta boxes so even the real `SyntheticDetector` works on it; `FakeDetector(script)` returns scripted detections per frame index; `FakeTracker` assigns ids by nearest-centroid; `InMemoryEdgeStore` implements the full `EdgeStore` protocol with dicts (seq stamping, outbox, backlog); `FakeUplink(fail=False, drop_every=0)` records batches and returns acks with duplicates computed on `event_id`; `SimpleLinkController` is the real-enough reference implementation; `fake_history(days)` returns a synthetic DataFrame matching the history contract; `sample_store_config()` loads `examples/store_demo.yaml`; `sample_event(type)` builds valid events for every type; `sample_alert(kind)`.

### C.15 TypeScript mirror (`packages/contracts/ts/types.gen.ts`, generated; core subset shown, hand-written `ts/index.ts` re-exports + helpers)

```ts
export type EventType = "footfall.crossing"|"zone.occupancy"|"dwell.sample"|"heatmap.tiles"|"queue.snapshot"|"queue.forecast"|"shelf.scan"|"shelf.state"|"alert.raised"|"alert.acked"|"alert.resolved"|"device.heartbeat"|"stock.reconciled"|"order.requested"|"config.applied"|"sim.truth";
export type EventClass = "telemetry"|"aggregate"|"alert"|"txn"|"config";
export type Severity = "info"|"warn"|"high"|"critical"; export type AlertStatus = "open"|"acked"|"resolved"; export type ShelfState = "stocked"|"partial"|"empty"|"unknown"; export type LinkState = "up"|"down"; export type Lang = "hi"|"en";
export type AlertKind = "shelf_gap"|"queue_long"|"queue_forecast"|"camera_down"|"sync_backlog"|"device_offline"|"shrink_suspect"|"footfall_spike";
export type AckAction = "restocked"|"order"|"false_positive"|"opened_counter"|"ignore"|"checked"|"investigate";
export interface ImpactInr { lost_sales_inr: number; lost_margin_inr: number; basis: string; factor: number; source: string }
export interface FootfallCrossing { type: "footfall.crossing"; line_id: string; line_kind: "entrance"|"counter"|"custom"; direction: "in"|"out"; count: number }
export interface QueueSnapshot { type: "queue.snapshot"; counter_id: string; zone_id: string; count: number; avg_dwell_s: number; max_dwell_s: number; arrival_rate_pm: number; service_rate_pm: number; est_wait_s: number; method: "little_service"|"observed_wait"|"default_service"; served_window: number; abandoned_window: number; window_s: number; served_total: number; abandoned_total: number; long_since_ts: number|null }
export interface QueueForecast { type: "queue.forecast"; counter_id: string; made_ts: number; horizons: Record<string, number>; model: "edge_trend"|"cloud_gbm"; mae_recent: number|null }
export interface ShelfScan { type: "shelf.scan"; shelf_id: string; sku_id: string|null; coverage: number; facings: number; capacity_facings: number; state_raw: ShelfState; occluded: boolean; method: string; thumb_b64: string|null }
export interface ShelfStateChange { type: "shelf.state"; shelf_id: string; sku_id: string|null; from_state: ShelfState; to_state: ShelfState; gap_started_ts: number|null; gap_minutes: number|null; consecutive_empty_scans: number; impact: ImpactInr|null }
export interface DeviceHeartbeat { type: "device.heartbeat"; uptime_s: number; fps: number; infer_ms_p50: number; infer_ms_p95: number; detector: string; model_version: string; backlog: number; link: LinkState; cameras: CameraHealth[]; contracts_version: string; clock_factor: number; sim_ts: number|null; cpu_pct: number|null; mem_mb: number|null }
export type Payload = FootfallCrossing|ZoneOccupancy|DwellSample|HeatmapTiles|QueueSnapshot|QueueForecast|ShelfScan|ShelfStateChange|AlertRaised|AlertAcked|AlertResolved|DeviceHeartbeat|StockReconciled|OrderRequested|ConfigApplied|SimTruth;
export interface Event { event_id: string; store_id: string; device_id: string; camera_id: string|null; ts: number; hlc: string; seq: number; type: EventType; cls: EventClass; version: number; payload: Payload; created_ts: number }
export interface Alert { alert_id: string; store_id: string; device_id: string; origin: "edge"|"cloud"; kind: AlertKind; severity: Severity; status: AlertStatus; subject_id: string; title_en: string; title_hi: string; message_en: string; message_hi: string; details: AlertDetails; impact: ImpactInr|null; actions: AckAction[]; raised_ts: number; acked_ts: number|null; resolved_ts: number|null; ack_action: AckAction|null; ack_by: string|null }
export interface SyncStatus { link: LinkState; uplink: "http"|"mqtt"|"none"; cloud_reachable: boolean; backlog: number; backlog_by_class: Record<string, number>; last_ack_ts: number|null; last_ack_seq: number|null; replayed_since_restore: number; replay_total_at_restore: number; seq_ok: boolean; down_since_ts: number|null }
export interface KpiToday { store_id: string; date: string; as_of_ts: number; footfall_in: number; footfall_out: number; occupancy_now: number; visual_transactions: number; conversion_pct: number|null; atv_inr: number|null; osa_pct: number; gap_minutes_total: number; avg_wait_s: number|null; max_wait_s: number|null; abandoned: number; lost_sales_inr: number; lost_margin_inr: number; recovered_inr: number; alerts_open: number; alerts_today: number; deltas: Record<string, number|null> }
export interface ShelfStateView { shelf_id: string; name: string; sku_id: string|null; sku_name: string; state: ShelfState; coverage: number; facings: number; capacity_facings: number; min_facings: number; consecutive_empty_scans: number; persistence_required: number; gap_started_ts: number|null; gap_minutes: number|null; last_scan_ts: number|null; occluded: boolean; impact_open: ImpactInr|null; has_reference: boolean }
export interface QueueView { counter_id: string; name: string; snapshot: QueueSnapshot|null; forecast: QueueForecast|null; open_alert_id: string|null }
export interface HealthStatus { status: "ok"|"degraded"|"starting"; store_id: string; device_id: string; uptime_s: number; contracts_version: string; detector: string; model_version: string; cameras: CameraHealth[]; sync: SyncStatus; sim_ts: number|null; clock_factor: number; fps: number; infer_ms_p50: number }
export interface WsMessage { kind: "hello"|"event"|"alert"|"kpi"|"health"|"sync"|"scenario"|"notification"|"device"|"forecast"; ts: number; store_id: string|null; data: Record<string, unknown> }
export interface StoreConfig { /* mirrors C.5 */ }  export interface Zone {...} export interface Line {...} export interface ShelfPolygon {...} export interface Counter {...}
export interface HeatmapResponse { camera_id: string|null; cell_px: number; width_cells: number; height_cells: number; from_ts: number; to_ts: number; cells: {x:number;y:number;dwell_s:number;visits:number}[]; max_dwell_s: number }
export interface ScenarioStatus { active: string; since_ts: number; params: Record<string, unknown>; available: string[]; clock_factor: number; sim_ts: number }
export interface OutboundMessage { message_id: string; channel: string; to: string; text: string; buttons: string[]; alert_id: string|null; store_id: string; created_ts: number; status: "queued"|"sent"|"delivered"|"failed"; delivered_ts: number|null }
export interface DeviceStatus {...} export interface FleetView {...} export interface ChainRank {...} export interface ReorderSuggestion {...} export interface ReconcileReport {...} export interface FootfallForecast {...} export interface FitReport {...} export interface DailyReport {...}
```
`tools/gen_ts_types.py`: dumps `TypeAdapter(X).json_schema()` for every exported model into `schemas/`, then runs `npx --yes json-schema-to-typescript` (dev dep pinned in senseboard) to emit `ts/types.gen.ts`; CI fails if regenerated output differs from the committed file. SenseBoard imports via alias `@contracts/types` → `../../packages/contracts/ts/index.ts` (vite `server.fs.allow: ['../..']`).

### C.16 REST endpoints

**SenseEdge :8001 (LAN, zero internet)** — all JSON; CORS `*`.

| Method & path | Request → Response |
|---|---|
| GET /health | → HealthStatus |
| GET /config · PUT /config/zones · PUT /config/shelves · PUT /config/rules | ZonesUpdate / ShelvesUpdate / RulesConfig → StoreConfig (bumps config_version, persists yaml, hot-reloads analytics; emits config.applied) |
| GET /kpis/today · GET /kpis/series?metric=queue_count\|est_wait_s\|footfall_in\|occupancy\|osa_pct&minutes=60 | → KpiToday / Series |
| GET /alerts?status=open\|acked\|resolved\|all&limit=100 · POST /alerts/{id}/ack | AlertAckRequest → Alert |
| GET /queues · GET /shelves · GET /shelves/{id}/thumb.jpg | → list[QueueView] / list[ShelfStateView] / image |
| GET /heatmap?camera_id=&from_ts=&to_ts= | → HeatmapResponse (floor coords) |
| GET /floorplan.png · GET /preview/{camera_id}.mjpg · GET /preview/{camera_id}.jpg?annotate=1 | images, never persisted; people pixelated if privacy.preview_blur_people |
| POST /calibrate/shelves/{shelf_id}/reference · POST /calibrate/shelves/reference-all | → ShelfReference / list[ShelfReference] |
| POST /sku/enrol (multipart sku_id, images[]) | → SkuEnrolResponse |
| GET /sync · POST /sync/flush · POST /demo/link | LinkRequest → SyncStatus |
| GET /demo/scenarios · POST /demo/scenario · POST /demo/chaos · POST /demo/restock/{shelf_id} · GET /demo/truth | ScenarioRequest/ChaosRequest → ScenarioStatus / SimTruth (404 when no synthetic camera) |
| POST /demo/whatsapp/reply | WhatsAppReply → Alert (maps digit→actions[digit−1], by=whatsapp_sim) |
| GET /summary/daily?lang=hi | → DailySummary |
| GET /models · POST /models/check | → ModelStatus (compares models/manifest.json with cloud GET /v1/fleet/manifest) |
| GET /metrics | Prometheus text (P2) |
| WS /ws/live | WsMessage stream (C.8) |

**SenseCloud :8000**

| Method & path | Request → Response |
|---|---|
| POST /v1/ingest/batch (header X-Device-Token) | IngestBatch → IngestAck (idempotent on event_id; 401 bad token unless SENSECLOUD_DEV=1; 413 > 500 events) |
| POST /v1/stores (StoreConfig) · GET /v1/stores · GET /v1/stores/{id} | → Store |
| GET /v1/stores/{id}/kpis?range=today\|7d\|30d · GET /v1/stores/{id}/series?metric=&range=today\|7d | → KpiRange / Series |
| GET /v1/stores/{id}/alerts?status= · POST /v1/alerts/{id}/ack | AlertAckRequest → Alert (also enqueues Command ack_alert for the device) |
| GET /v1/stores/{id}/queues · /shelves · /heatmap?from_ts&to_ts | as edge views, from cloud tables |
| GET /v1/stores/{id}/forecast/queue?counter_id= · /forecast/footfall?days=7 · /forecast/eval | → QueueForecast / FootfallForecast / list[FitReport] |
| GET /v1/stores/{id}/reorder | → list[ReorderSuggestion] |
| GET /v1/stores/{id}/reports/daily?date=&format=json\|csv\|whatsapp&lang= | → DailyReport / text |
| GET /v1/stores/{id}/recon · POST /v1/stores/{id}/integrations/tally/reconcile · POST /v1/stores/{id}/integrations/ondc/publish · GET /v1/stores/{id}/integrations/status | → list[StockReconciled] / ReconcileReport / OndcAck / IntegrationsStatus |
| POST /v1/stores/{id}/orders (OrderRequested) | → {po_id} via ErpClient.post_purchase_order |
| GET /v1/chain/rank?metric=osa_pct\|avg_wait_s\|lost_sales_inr\|footfall_in\|conversion_pct&date= | → ChainRank (normalised = value per 100 visitors where meaningful) |
| GET /v1/fleet · GET /v1/fleet/manifest?device_id= · POST /v1/fleet/manifest · POST /v1/fleet/rollout · GET /v1/devices/{id}/commands | → FleetView / ModelManifest / ... / list[Command] |
| GET /v1/whatsapp/outbox?store_id=&limit= · POST /v1/whatsapp/webhook (WhatsAppReply or Meta payload) · GET /v1/notifications?store_id= | → list[OutboundMessage] / {ok, alert_id, action} |
| POST /mock/ondc/on_update · GET /mock/ondc/log | Beckn-shaped JSON → {ack:{status:"ACK"}} |
| GET /health · WS /v1/ws?store_id= | |

**Tally mock :9000** (`python -m retailsense_integrations.tally_mock`): `POST /` Tally XML envelope (Export → StockSummary / Sales vouchers; Import → Stock Journal, Purchase Order) · `GET/PUT /mock/state` JSON `{items: {name: {qty, rate, sold_today}}}`.

---

## D. Module specs

Common format: **Owner** · **Purpose** · **Public API** · **Depends** (always: contracts only; runtime resolution via registry) · **Acceptance** (P0 unless noted) · **Tests** (pytest in the package's `tests/`).

### D1. contracts — `packages/contracts`, `tools/gen_ts_types.py`, `docs/{ARCHITECTURE,CONTRACTS,DECK,BMC,PRIVACY}.md` [A01]
Purpose: everything in §C, fakes, schemas, TS types, then docs.
Acceptance: `pip install -e packages/contracts` works on Windows with no deps beyond pydantic/numpy/pyyaml/sqlalchemy/tzdata; every model in §C exists with these names; `examples/store_demo.yaml` validates; `resolve()` falls back to fakes with a single WARNING; `gen_ts_types.py` regenerates identical committed output; HLC/ULID monotonic; `insert_ignore` works on sqlite and (unit-mocked) postgres dialect; `create_all` builds both metadata sets on sqlite; fakes satisfy Protocols (`typing.runtime_checkable` spot checks). Deliver within the first 45 minutes: `VERSION`, enums, events, alerts, config, api, interfaces, registry, testing; then db/topics/manifest/i18n/impact, schemas/TS, docs.
Tests: `test_events_roundtrip` (every EventType via `sample_event` → JSON → Event), `test_event_type_mismatch_rejected`, `test_ulid_sortable`, `test_hlc_monotonic_and_receive`, `test_geometry_side_and_crossing` (entrance line demo numbers: point (90,330) side −1, (90,300) side +1), `test_point_in_polygon_demo_zones`, `test_config_demo_validates_and_hash_stable`, `test_config_rejects_dangling_ids`, `test_impact_formula` (Amul 18/hr, 20 min → 27×18×(20/60)×0.31 = ₹50.22; basis contains "0.31"), `test_i18n_all_keys_both_langs`, `test_render_never_raises`, `test_registry_fake_fallback`, `test_db_create_all_both`, `test_insert_ignore_dedup`, `test_manifest_assignment_deterministic`, `test_ts_types_up_to_date` (skips if npx missing).

### D2. simulator — `packages/sim`, `tools/make_demo_video.py` [A02]
Purpose: agent-based store model, synthetic video, scenarios, chaos, history generator, headless fake edges.
```python
class StoreModel:
    def __init__(self, cfg: StoreConfig, seed: int = 42, start_ts: float | None = None)
    def step(self, dt: float = SIM_DT_S) -> SimState            # shoppers, cashier, shelf units, queue slots
    def apply_scenario(self, name, params) -> ScenarioStatus; def restock(shelf_id, units=None); def truth() -> SimTruth; @property ts
class Shopper: id, pos, state ∈ {ENTERING, BROWSING, TO_QUEUE, QUEUEING, SERVICE, EXITING, ABANDONING}, waypoints, patience_s, basket
class VideoGenerator:  def __init__(self, cfg, palette=SyntheticPalette, draw_overlays=False); def render(self, state: SimState) -> np.ndarray
def render_floorplan(cfg: StoreConfig, *, with_zones=True) -> np.ndarray          # registry "floorplan_renderer"
class SyntheticFrameSource(FrameSource, SyntheticControl):  def __init__(self, camera: CameraConfig, cfg: StoreConfig, clock_factor: float, start_ts: float | None, seed=42)
SCENARIOS = {"baseline","quiet","evening_rush","diwali","stockout":{shelf_id, over_s}, "restock":{shelf_id}, "open_counter","close_counter","camera_blackout":{seconds},"freeze":{seconds},"footfall_spike","seed_history"}
def generate_history(cfg, days=30, seed=42, festivals: list[Festival] | None=None) -> tuple[pd.DataFrame, pd.DataFrame]   # (minute_df, daily_df) per C.13 contract, <2 s
class FakeEdge:  def __init__(self, cfg: StoreConfig, cloud_url, clock_factor, seed); async def run(self, stop: asyncio.Event)   # headless: emits queue.snapshot/footfall.crossing/zone.occupancy/shelf.state/device.heartbeat batches via POST /v1/ingest/batch with its own seq
cli: python -m retailsense_sim video --scenario evening_rush --seconds 60 --out var/demo_store.mp4 | headless --stores 2 --cloud http://localhost:8000 | history --days 30 --out var/history.parquet
```
Behaviour (normative): arrivals Poisson with hour-of-day curve (peaks 08–10, 17–21; evening base 1.2/min); shoppers enter through the door, cross the entrance line upward, browse 1–3 shelf fronts (dwell 4–20 s), buy with probability calibrated so expected units/hr ≈ `sku.velocity_units_per_hr × arrival multiplier`, then join `queue-1` (slot spacing 26 px from head (576,120) downward), advance, service time N(45,15) s at head, then cross `counter-1-line` leftwards at y≈120 and walk to the door (exit crossing downward). Abandon if queue ≥ patience (4–7) on arrival or wait > patience_s (120–300 s): leave zone downward without crossing the counter line. Shoppers with empty baskets exit directly (bounce). Soft repulsion keeps ≥ 22 px separation. `open_counter` doubles service rate. Shelves draw `ceil(units/units_per_facing)` facings left-to-right (top-to-bottom for shelf-C) over `SHELF_BACKING`; `stockout` drains the shelf to 0 over `over_s` (default 30 s sim) and disables auto restock. Sim clock: `dt` 0.25 s per frame; `SyntheticFrameSource.read()` paces to `clock_factor/dt` real fps (cap 60; if the consumer is slower, sim simply runs slower and `effective_clock_factor` is reported). `camera_blackout` yields black frames; `freeze` repeats the last frame; `drop` skips frames with prob p.
Acceptance: 60 s of `evening_rush` renders > 200 fps on CPU; `SyntheticDetector` from A03 (or the contracts `FakeDetector` colour fallback in tests) finds every shopper ±1 in ≥ 95 % of frames; truth footfall equals number of entrance crossings; `generate_history(30)` has 43,200 minute rows, festival flags set from `festivals_in.csv`, salary-week flag days 1–7; `make_demo_video.py` writes an mp4 playable by cv2 (`mp4v`); FakeEdge ingests against the contracts `FakeUplink` and (in A14 tests) against the real cloud.
Tests: `test_arrivals_rate_matches_curve`, `test_shopper_path_crosses_entrance_then_counter`, `test_abandon_never_crosses_counter_line`, `test_stockout_scenario_drains_shelf_and_facings`, `test_restock_restores`, `test_render_shapes_and_palette` (shopper pixels in HSV range; shelf backing visible when empty), `test_frame_source_protocol_and_pacing`, `test_history_contract_columns`, `test_fake_edge_batches_have_monotonic_seq`, `test_scenarios_list_matches_status`.

### D3. edge-capture + edge-perception — `packages/edgecv`, `tools/fetch_models.py`, `models/manifest.json` [A03]
```python
def open_source(camera: CameraConfig, *, store_cfg: StoreConfig | None = None, clock_factor: float = 1.0) -> FrameSource   # parses "rtsp://", "file:", "webcam:N", "synthetic:<scenario>" via registry
class FileFrameSource(camera, path, loop=True, sample_fps)   # ts = start_ts + frame_idx/fps; start_ts = now − duration on open
class RtspFrameSource(camera, url, sample_fps, reconnect_s=3)  # cv2.VideoCapture(url, CAP_FFMPEG); drops to latest frame
class WebcamFrameSource(camera, index, sample_fps)             # CAP_DSHOW on Windows
class SyntheticDetector(Detector): HSV inRange(SHOPPER_HSV_LO..HI) → open 3×3 → connectedComponentsWithStats → boxes (area ≥ 120 px²); blobs > 1.7× nominal area are split along the long axis into round(area/nominal) boxes; conf = 0.99
class OnnxPersonDetector(Detector): __init__(model_path, imgsz=640, conf=0.35, iou=0.5, providers=auto)  # letterbox, YOLOv8-style [1,84,8400] decode, class 0 only, cv2.dnn.NMSBoxes; CUDA EP if present else CPU; warmup()
class UltralyticsDetector(Detector): lazy-imports ultralytics; ImportError → Unavailable
def select_detector(camera: CameraConfig, manifest_path="models/manifest.json") -> Detector   # auto: synthetic source or file whose name contains "synthetic"/"demo_store" → SyntheticDetector; else onnx if weights present else ultralytics else raise Unavailable with install hint
class KalmanBox  # constant-velocity on (cx, cy, w, h)
class ByteTrackLite(Tracker): __init__(high_thresh=0.5, low_thresh=0.1, match_thresh=0.8 (1−IoU), max_age=30, min_hits=2, centroid_gate_px=60)  # two-stage association with scipy.optimize.linear_sum_assignment on IoU of Kalman-predicted boxes; unmatched high-conf dets fall back to centroid gating; ids never reused
class Homography(PointMapper): @classmethod from_config(h: HomographyConfig | None) (None → identity); uses cv2.findHomography
def annotate_frame(frame, tracks, cfg_view: dict, *, blur_people: bool) -> np.ndarray      # boxes+ids, zone polygons by kind, lines with IN arrow, shelves coloured by state, queue count text; pixelates person boxes (down 12× / up) when blur_people
class CvPipeline:  def __init__(self, camera, store_cfg, detector, tracker, on_result: Callable[[FrameResult], None]); def run(self, stop: threading.Event); FrameResult = (frame, detections, tracks, infer_ms)
class ModelManager: load_local(manifest_path) → ModelManifest; verify_sha(entry); compare(remote) → ModelStatus
tools/fetch_models.py: pip-installs ultralytics if missing, exports yolo11n.pt → models/yolo11n.onnx (opset 12, imgsz 640), writes sha256/size into models/manifest.json; --yolox downloads yolox_nano.onnx (Apache-2.0) as licence-clean alternative (P2)
```
Acceptance: synthetic path needs no weights/GPU; on A02's rendered frames the detector+tracker hold a stable id for a shopper across its full path (ID switches < 5 % of tracks at 4 fps sim); OnnxPersonDetector runs on CPU at ≤ 120 ms/frame at 640 and detects people in a webcam/file stream (manual check + unit test on a bundled 2-frame fixture of drawn-in boxes is not meaningful — test pre/post-processing math with a tiny stub ONNX model built in-test via `onnx.helper` is P1; P0 test = letterbox/decode/NMS unit tests on synthetic tensors); preview annotation never writes to disk; `select_detector` honours `camera.detector`.
Tests: `test_synthetic_detector_boxes_from_palette_frame`, `test_blob_split_two_touching_shoppers`, `test_tracker_keeps_id_linear_motion` (30 frames, 8 px/frame), `test_tracker_two_crossing_objects_no_swap` (with Kalman), `test_tracker_ids_never_reused`, `test_letterbox_and_decode_roundtrip`, `test_nms`, `test_homography_identity_and_4pt`, `test_file_source_timestamps_and_loop`, `test_open_source_parses_specs`, `test_annotate_blur_changes_person_pixels_only`, `test_manifest_sha_verify`.

### D4. edge-shelf — `packages/edgeshelf` [A04]
```python
class ClassicalCoverageEstimator(CoverageEstimator): __init__(std_tau=8.0, colour_tau=28.0, covered_col_frac=0.35, backing_bgr: list[int] | None = None)
  # crop polygon bbox + mask; Lab; productness(pixel) = local std(5×5) > std_tau OR ΔE(backing) > colour_tau; backing = ref.backing_bgr or median of low-variance pixels at calibration or SyntheticPalette.SHELF_BACKING when method hint synthetic
  # profile along long axis: col_covered = mean productness in column > covered_col_frac; raw_coverage = covered cols / cols; coverage = clip(raw / max(ref.raw_coverage_full, 0.05), 0, 1)
  # facings = runs of covered columns ≥ 0.6×facing_width_px (if set) else round(coverage × capacity_facings)
class ShelfStateMachine(ShelfStateMachine protocol): per shelf: state, consecutive_empty_scans, gap_started_ts, persistence_required = rules.persistence_scans + fp_count (≤ max_persistence_scans)
  # state_raw from coverage thresholds (≥ partial_coverage → stocked; > empty_coverage → partial; else empty) OR facings < min_facings → empty-equivalent
  # empty confirmed when run ≥ persistence_required → transition (gap_started_ts = first empty scan ts); stocked/partial scan while confirmed empty → resolve with gap_minutes; occluded scans ignored (no reset); partial transitions immediate, informational
  # feedback_false_positive(shelf) → fp_count += 1, resets run, returns new persistence_required
  # osa_pct(ts) = 100 × (1 − Σ gap_minutes_today / (n_shelves × minutes_since_day_start)); gap_minutes_today includes open gap so far
def shelf_thumbnail(image, shelf: ShelfPolygon) -> str | None   # 96×96 JPEG q=70 of polygon bbox masked; base64; None if > 16 KB
def occluded_by(tracks: list[Track], shelf: ShelfPolygon, min_overlap=0.30) -> bool
class TaggedSkuIdentifier(SkuIdentifier): backend="tagged"; identify() → (hint_sku_id, 1.0); enrol() stores count only
class ClipSkuIdentifier(SkuIdentifier): backend="clip_onnx"; loads models/clip_vitb32.onnx if present else Unavailable; cosine k-NN over enrolled embeddings (numpy)   # P2
class ShelfScanner: __init__(shelves, estimator, identifier, rules, privacy); scan(image, tracks, ts, references) -> list[ShelfScan]   # respects occlusion + thumbnails flag
```
Acceptance: on A02 synthetic frames (rendered in-test via the contracts `FakeFrameSource` drawing + `SyntheticPalette`, not by importing sim) coverage is within ±0.1 of `facings_visible/capacity`; facings count exact for ≥ 2 px gaps; state machine emits exactly one `shelf.state` empty transition after 3 consecutive empty scans and none for 2; occlusion skip; FP feedback raises requirement to 4; OSA math; thumbnails ≤ 96×96 and ≤ 16 KB; real-image sanity: a photo-like fixture generated in-test (random texture vs flat backing) yields coverage < 0.3 when flat.
Tests: `test_coverage_full_partial_empty_synthetic`, `test_coverage_vertical_shelf_long_axis`, `test_calibration_normalises`, `test_facings_runs`, `test_state_machine_persistence_3_scans`, `test_state_machine_resolve_gap_minutes`, `test_occluded_scan_ignored`, `test_false_positive_feedback`, `test_osa_and_gap_minutes_today`, `test_thumbnail_size_limits`, `test_tagged_sku_identifier`, `test_clip_unavailable_without_weights`.

### D5. edge-analytics — `packages/edgeanalytics` [A05]
```python
class ZoneEngine(ZoneEngine protocol):
  # membership with inertia 2 frames in/out (anchor per camera.anchor); dwell accumulates per (track, zone) → DwellSample on exit or track loss (min 1 s), only for zone kinds aisle/custom/queue
  # LineCrosser: per track & line keeps last side + frames on that side; crossing when side flips, the anchor path segment intersects the line (extended 10 %), both sides held ≥ 2 frames; 2 s cooldown per (track, line); emits Crossing + FootfallCrossing(line_kind, direction)
  # occupancy every rules.occupancy_interval_s per zone (ZoneOccupancy); store-zone occupancy = in − out bounded ≥ 0 (resets at day start)
  # HeatmapAccumulator: floor coords via mapper; cell = floor(pt / heat_cell_px); dwell_s += dt per confirmed track; visits += 1 on cell change; flush HeatmapTiles every rules.heat_flush_s (deltas)
  def update(tracks, ts) -> AnalyticsUpdate; def flush(ts) -> AnalyticsUpdate; def reload(zones, lines) -> None
class FootfallCounter: in_total, out_total, occupancy, spike(window_15m, baseline) helpers
```
Acceptance: demo entrance line counts IN for an upward track at x=90 and OUT for downward; no double count on jitter across the line; dwell sample equals frames-in-zone × dt ±1 frame; heat tiles sum of dwell equals total confirmed-track time; hot-reload keeps track state.
Tests: `test_line_in_out_demo_entrance`, `test_line_jitter_no_double_count`, `test_line_cooldown`, `test_zone_inertia`, `test_dwell_sample_on_exit_and_loss`, `test_occupancy_interval`, `test_heatmap_accumulate_and_flush_deltas`, `test_homography_applied_to_heat`, `test_reload_preserves_tracks`.

### D6. edge-queue — `packages/edgequeue` [A06]
```python
class QueueAnalyzer(QueueAnalyzer protocol):
  # join: track appears in queue zone members (after inertia) → entry_ts; leave: if a Crossing(counter_line, IN) for the track within ±2 s → served (wait = leave − entry); elif in-zone age ≥ rules.queue_min_age_s → abandoned; else ignored
  # windows (rules.queue_window_s=600): arrival_rate_pm = joins/min; service_rate_pm = served/min (elapsed-adjusted before window fills)
  # est_wait_s: service_rate_pm ≥ 0.2 → count×60/service_rate_pm ("little_service"); else mean of last 5 observed waits ("observed_wait"); else count×default_service_s ("default_service")
  # snapshot every rules.snapshot_interval_s or when |Δcount| ≥ 2; long_since_ts set while count ≥ queue_long_count
class TrendForecaster(EdgeQueueForecaster): ring buffer 30 min of snapshots; 1-min means; L(h) = max(0, L + (arrival−service)×h×0.85^h + slope×h); self-scoring mae_recent by comparing stored predictions with realised counts; set_cloud_forecast() overrides predict() while cloud forecast age < 2 min (model="cloud_gbm")
```
Acceptance: scripted AnalyticsUpdates (5 joins, 3 served via crossings, 1 abandon after 20 s, 1 transient 2 s) → served_total 3, abandoned_total 1, method little_service once ≥ 2 served; est_wait monotonic in count; forecast never negative, returns all four horizons; MAE self-score converges on a synthetic saw-tooth.
Tests: `test_join_served_abandoned_transient`, `test_little_law_service_rate`, `test_fallback_methods_order`, `test_long_since_ts_set_and_cleared`, `test_snapshot_cadence_and_delta_trigger`, `test_day_reset_totals`, `test_trend_forecast_shapes_and_nonneg`, `test_cloud_override_and_expiry`, `test_mae_self_scoring`.

### D7. edge-rules — `packages/edgerules` [A07]
```python
class RuleEngine(RuleEngine protocol): open alerts keyed (kind, subject_id); emits Observation(type="alert.raised"|"alert.acked"|"alert.resolved")
  # shelf_gap: on to_state==empty → raise (severity HIGH if rate_per_hour ≥ ₹50 else WARN; CRITICAL if gap ≥ 60 min at raise); impact = lost_sales(sku, gap_so_far) ; on empty→stocked/partial → resolve(restocked_observed, final impact, recovered if acked restocked)
  # queue_long: count ≥ N and long_since ≥ T → raise WARN (CRITICAL if count ≥ max_queue), impact = queue_abandon_risk; resolve when count < N−1 for rules.queue_resolve_s
  # queue_forecast: forecast[horizon] ≥ threshold and no open queue_long → raise INFO; resolve when forecast < threshold−1 or superseded by queue_long
  # camera_down: camera status stale/black → raise HIGH; resolve on ok. sync_backlog: link down > sync_backlog_after_s and backlog ≥ warn → INFO; resolve on restore
  # footfall_spike: 15-min in-count ≥ factor × rolling baseline (P1)
  # on_ack: false_positive → resolved(false_positive) + feedback callback; restocked/opened_counter → status acked (resolution waits for observation); order → also emits order.requested(qty = suggest from velocity × lead_time)
  # every alert rendered via i18n into title/message hi+en at raise; actions = ACTIONS_BY_KIND[kind]
class ImpactCalculator: thin wrapper over contracts.impact with store ImpactConfig + SKU lookup; atv from Tally when provided
def load_rules_yaml(path) -> RulesConfig   # rules_default.yaml mirrors RulesConfig defaults with comments for on-stage editing
```
Acceptance: one open alert per (kind, subject); Hindi and English messages contain ₹ amount and digit menu; false-positive ack resolves and calls feedback; impact basis string cites the factor; queue resolve hysteresis; all events validate against contracts.
Tests: `test_shelf_gap_raise_once_and_resolve_with_recovered`, `test_severity_rules`, `test_queue_long_raise_resolve_hysteresis`, `test_queue_forecast_superseded`, `test_camera_down_and_back`, `test_sync_backlog`, `test_ack_false_positive_feedback`, `test_ack_order_emits_order_requested`, `test_messages_hi_en_contain_inr_and_menu`, `test_restore_open_alerts`.

### D8. edge-store + edge-uplink — `packages/edgestore`, `packages/edgeuplink` [A08]
```python
class EdgeStore(EdgeStore protocol): sqlite via contracts.db.sqlite_engine (WAL, synchronous=FULL, busy_timeout 5 s); single-writer (asyncio loop thread only)
  # append(): BEGIN IMMEDIATE; seq from device_state.seq_next; hlc from HLC; insert events + outbox (expires_ts from EXPIRY_S); COMMIT; returns stamped Events
  # pending(): unsent, unexpired, unevicted ordered by id; expire()/evict_overflow() per topics policy (never ALERT/TXN)
  # kpi_today(): computed from tables (footfall from events type footfall.crossing today; queue from queue_state + events; OSA from shelf_state; lost/recovered from alerts) — cached 2 s
class RetentionJob: run(store, policy, now_ts) -> dict   # purges telemetry events > 24 h, aggregates > 30 d, thumbnails (nulls thumb_b64 in shelf.scan payloads > 7 d), sent outbox rows > 24 h, heatmap > 90 d
class KpiAggregator: helpers for kpi_daily rollover at store-day boundary (Asia/Kolkata) and deltas vs yesterday
class HttpUplink(Uplink): httpx.AsyncClient, POST {cloud_url}/v1/ingest/batch, header X-Device-Token, timeout 5 s, gzip; connected = last probe of GET /health ok
class MqttUplink(Uplink): paho 2.1 CallbackAPIVersion.VERSION2, MQTTv5, clean_start=False, session expiry, LWT retained status, publish QoS1 with MessageExpiryInterval per class, waits for PUBACK → ack; subscribes cmd topic → Command queue   (P1, behind uplink.mode=mqtt)
class LinkController: state, cut()/restore(), subscribers; cut() also makes HttpUplink raise LinkDown before any socket use
class SyncWorker: async run(store, uplink, link, cfg, on_status: Callable[[SyncStatus],None], on_command: Callable[[Command],None], stop)
  # loop every interval_s: if link.up: batch = pending(batch_size) → send → mark_sent; process ack.commands; update SyncStatus (replayed_since_restore counts events acked after a restore until backlog hits 0; replay_total_at_restore = backlog at restore; seq_ok from ack); on error: mark_failed, backoff 1→2→4→8 s (cap 30) ; always send heartbeat batch (possibly empty) every heartbeat_s so commands flow; when link.down: only status updates
```
Acceptance: `append()` of 1,000 observations < 1 s; killing the process mid-batch (test uses a subprocess writing in a loop then `terminate()`) leaves DB consistent (events count == outbox count, seq gap-free); outbox ordering strictly by seq; telemetry expires, alerts never; overflow eviction never touches alert/txn; cut→500 events→restore replays all with FakeUplink, `replayed_since_restore == 500`, `seq_ok`; duplicate resend after a lost ack is reported as duplicates by the cloud fake and not re-marked; retention purge counts; KpiToday fields populate from fixture events.
Tests: `test_append_same_txn_events_outbox`, `test_seq_gap_free_after_crash` (subprocess), `test_pending_order_and_marks`, `test_expiry_policy_by_class`, `test_evict_overflow_skips_alert_txn`, `test_kpi_today_from_fixture`, `test_retention_purge`, `test_day_rollover_deltas`, `test_http_uplink_headers_and_ack` (respx/httpx MockTransport), `test_link_cut_blocks_send`, `test_sync_worker_replay_500_ordered`, `test_sync_backoff_and_recovery`, `test_commands_from_ack_dispatched`, `test_mqtt_uplink_topics_and_expiry` (mocked client, P1).

### D9. edge-api + SenseEdge app — `apps/senseedge` [A09]
Purpose: the wiring layer + local REST/WS/MJPEG. Process model (normative): one `CameraWorker` thread per camera (source → detector → tracker → ZoneEngine → QueueAnalyzer → every `shelf_scan_interval_s` ShelfScanner → pushes `list[Observation]` + `FrameResult` to `queue.Queue(maxsize=1000)`, drops oldest telemetry if full; updates `LatestFrame` holder); asyncio main (uvicorn) runs `consumer_task` (drains queue → RuleEngine → `EdgeStore.append` → `WsManager.broadcast` → upserts views), `sync_task` (SyncWorker), `heartbeat_task` (10 s; builds DeviceHeartbeat from worker stats; feeds RuleEngine.on_health/on_sync), `kpi_task` (5 s broadcast), `retention_task` (hourly), `edge_forecast_task` (30 s: TrendForecaster.predict → queue.forecast observation; fetch cloud forecast when link up and set_cloud_forecast), `model_check_task` (5 min). The store is only touched from the asyncio thread. Graceful Ctrl+C: stop event → threads join ≤ 3 s → store.close().
```python
def create_app(cfg: StoreConfig, *, overrides: dict[str, Any] | None = None) -> FastAPI     # overrides let tests inject fakes by registry key
class Wiring: resolves everything via registry (respecting camera.detector, uplink.mode), builds workers; `Wiring.from_config(cfg)`
class WsManager: connect/disconnect/broadcast(WsMessage); per-socket send queue, drops slow clients
class PreviewStreamer: MJPEG generator ≤ 10 fps from LatestFrame + annotate_frame; blur per privacy config
main.py: python -m senseedge --config packages/contracts/.../store_demo.yaml --port 8001 --camera <spec> --detector auto --clock 10 --cloud http://localhost:8000 --uplink http|mqtt|none
```
Acceptance (with contracts fakes only): app boots, `/health` ok, WS delivers hello+kpi, `/demo/link down` flips SyncStatus and `/demo/link up` triggers replay stats, `/alerts/{id}/ack` emits alert.acked, `/calibrate/...` stores references, `/config/zones` hot-reloads and emits config.applied, MJPEG endpoint streams ≥ 2 frames in a test, `/demo/scenario` returns 404 without synthetic source and proxies when present, `/summary/daily` renders hi text via i18n. With real packages (A14 integration) the full pipeline runs.
Tests (httpx ASGI + fakes): `test_boot_health_with_fakes`, `test_ws_hello_and_kpi`, `test_consumer_stamps_and_broadcasts`, `test_ack_flow`, `test_link_toggle_and_sync_status`, `test_calibrate_endpoints`, `test_config_hot_reload`, `test_preview_mjpeg_and_blur_flag`, `test_demo_endpoints_404_without_synthetic`, `test_whatsapp_reply_maps_digit`, `test_shutdown_joins_threads`.

### D10. cloud-api SenseCloud — `apps/sensecloud` [A10]
```python
def create_app(settings: CloudSettings) -> FastAPI    # SENSECLOUD_DB_URL (default sqlite:///var/sensecloud.db), SENSECLOUD_DEV=1, SENSECLOUD_SEED_HISTORY=1, SENSECLOUD_MQTT_HOST (optional bridge), SENSECLOUD_NOTIFIER=simulator|telegram|cloud_api
ingest.py: validate token; insert_ignore events (dedup); per-device seq check (gaps → seq_ok False, listed); update devices row; write ingest_log; return pending commands; fan-out alert/sync WS; trigger aggregator
aggregator.py: incremental per store from agg_cursor: series_5m (queue_count, est_wait_s, footfall_in, footfall_out, occupancy, osa_pct, gap_minutes, lost_sales_inr), kpi_daily rollups (conversion = counter IN crossings / entrance IN; OSA from shelf.state gaps; lost from shelf.state impact or contracts.impact when absent; recovered from alert.resolved; abandoned from max queue.snapshot.abandoned_total; ATV from Tally sales_today when integrations enabled), shelf_state/queue_state/heatmap_cells upserts
alerting.py: mirrors edge alerts from events; cloud-only rules: device_offline (no heartbeat > 60 s → HIGH, auto-resolve on return), sync_backlog_cloud (P2), shrink_suspect from reconcile; one open per (kind, subject)
dispatcher.py: on alert.raised → OutboundMessage (lang per store) via Notifier from registry → notifications table + WS notification; on webhook reply → Command ack_alert for the device (+ immediate cloud-side status acked)
fleet.py: devices/status, manifest storage, assigned_version via contracts.manifest, rollout endpoint, version_drift
reports.py: DailyReport (+csv, +whatsapp text via i18n daily_summary)
forecast glue: at boot (and hourly) resolve history_generator → fit forecaster.queue/footfall per registered store (skip if fakes); /forecast/queue builds `recent` frame from series_5m; stores predictions in forecasts; fills `actual` later for live MAE
seed.py: register demo store from examples/store_demo.yaml; seed 30 days kpi_daily/series_5m from history (so charts and deltas aren't empty)
routers/mock_ondc.py: accepts Beckn-shaped on_update, logs to ondc_log
mqtt_bridge.py (P1): paho subscriber → ingest()
ws.py: WsManager per store
```
Acceptance: batch of 500 events ingests < 300 ms on sqlite; re-sending the same batch returns duplicates=500, accepted=0; seq gap detection; KPI today for the demo store from fixture events matches hand-computed numbers; device_offline raised 60 s after last heartbeat and resolved on next; webhook digit → command returned in the device's next IngestAck; chain rank with 3 stores; manifest assignment with canary; reports in 3 formats; forecast endpoints return fake-shaped data when forecasting package is absent.
Tests: `test_ingest_idempotent_and_seq`, `test_ingest_token`, `test_aggregator_kpis_fixture`, `test_series_buckets`, `test_alert_mirror_and_device_offline`, `test_dispatcher_simulator_outbox_and_webhook_command`, `test_chain_rank_normalised`, `test_fleet_manifest_assignment`, `test_reports_formats`, `test_ws_fanout`, `test_mock_ondc_log`, `test_seed_history`.

### D11. integrations — `packages/integrations` [A11]
```python
class TallyClient(ErpClient): __init__(url, company=None, timeout=3); stock_summary() via <ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST>... StockSummary report; sales_today(); post_stock_journal(); post_purchase_order() → voucher id
tally_xml.py: build_export_request(report, company) / parse_stock_summary(xml) / build_stock_journal(adjustments) / build_purchase_order(lines) — pure functions
tally_mock.py: create_app(initial: dict) -> FastAPI (POST / parses envelope and answers with realistic Tally XML; GET/PUT /mock/state); default state: Amul Taaza 500ml qty 48, Parle-G 70g 120, Fortune Sunflower 1L 18 (so the stage shrink row is Amul: system 48 vs visual 41 → ₹189)
def reconcile(store_cfg, erp: ErpClient, shelf_views: list[ShelfStateView], rules, impact) -> ReconcileReport   # visual_units = facings × units_per_facing; flagged if delta_units ≥ shrink_min_units and delta_inr ≥ shrink_min_inr; produces StockReconciled observations + ShrinkAlertDetails
class WhatsAppSimulator(Notifier): channel="whatsapp_sim"; in-memory + optional sqlite persistence; outbox(store_id) → list[OutboundMessage]; reply(alert_id, digit) → (action)
class WhatsAppCloudNotifier(Notifier): Meta Graph API v20 messages endpoint with interactive buttons (≤ 3) — untested on stage, env WHATSAPP_TOKEN
class TelegramNotifier(Notifier): Bot API sendMessage with inline keyboard; webhook/polling parser mapping callback_data "ack:{alert_id}:{digit}"
class OndcStubPublisher(OndcPublisher): builds Beckn on_update {context{domain:"ONDC:RET10", action:"on_update", bpp_id, message_id, timestamp}, message{order{items[{id, quantity{available{count}}}]}}}; POST gateway_url; signing="none" (Ed25519 documented as future; `signing` field reserved)
def build_router(notifier, erp, ondc) -> APIRouter    # /v1/whatsapp/*, /v1/stores/{id}/integrations/* handlers used by sensecloud via registry "integrations_router"
```
Acceptance: XML builders/parsers round-trip against the mock; reconcile flags Amul only with defaults; simulator outbox + reply mapping; ONDC stub posts valid JSON to a test server; Telegram/Meta notifiers unit-tested with httpx MockTransport; all notifiers satisfy the Notifier protocol.
Tests: `test_tally_xml_export_request`, `test_tally_parse_stock_summary_fixture`, `test_tally_mock_roundtrip`, `test_reconcile_flags_and_inr`, `test_whatsapp_sim_outbox_and_reply`, `test_whatsapp_cloud_payload_shape`, `test_telegram_payload_and_callback_parse`, `test_ondc_stub_payload_and_post`, `test_router_endpoints`.

### D12. cloud-forecast — `packages/forecasting` [A12]
```python
festivals.py: load_festivals(csv=contracts examples/festivals_in.csv) -> list[Festival]; festival_features(date) -> (is_festival, weight, days_to_next, name); CSV must include 2026-27: Pongal/Makar Sankranti 14 Jan, Republic Day 26 Jan, Holi 4 Mar 2026, Eid-ul-Fitr ~20 Mar 2026, Eid-ul-Adha ~27 May 2026, Independence Day 15 Aug, Onam 26 Aug 2026, Raksha Bandhan 28 Aug 2026, Janmashtami 4 Sep 2026, Ganesh Chaturthi 14 Sep 2026, Dussehra 20 Oct 2026, Diwali 8 Nov 2026, Chhath 15 Nov 2026, Christmas 25 Dec, plus 2027 Pongal 14 Jan, Holi 22 Mar 2027 (agent verifies dates; `verified` column)
features.py: make_queue_features(minute_df) → lags 1,2,3,5,10,15 of queue_count, rolling means 5/15, arrivals_pm, service_pm, footfall_in_15m, hour, dow, minute_of_day, is_festival, festival_weight, days_to_festival, is_salary_week; targets y_h = queue_count shifted −h for h ∈ {5,10,15,30}
class QueueForecaster(CloudQueueForecaster): backend = lightgbm if importable else sklearn HistGradientBoostingRegressor (default; max_iter 200); one model per horizon; holdout = last 3 days; FitReport with mae_holdout and naive-persistence mae_baseline; predict(recent 30 min) → {"5":..}; save/load joblib under var/models/
class FootfallForecaster(CloudFootfallForecaster): daily HGB with dow/festival/salary/lags 1,7,14; predict_days with ±1.28×holdout MAE band
def suggest_reorder(cfg: StoreConfig, footfall_fc: FootfallForecast, system_stock: dict[str,int] | None, visual: dict[str,int] | None) -> list[ReorderSuggestion]   # demand = velocity×open_hours×lead_time_days×(forecast/avg footfall); safety = 0.5 day; suggest = max(0, ceil(demand + safety − stock))
eval.py: rolling_mae(forecasts table rows) for the live badge
```
Acceptance: fit on `fake_history(30)` (contracts) in < 5 s; holdout MAE < baseline MAE; MAE ≤ 1.0 customers on the sim history (target like Cali Intelligence 0.8); predictions non-negative; reorder qty sane (Amul ≈ 18×14×1×1.0 + safety − stock).
Tests: `test_festival_features_and_days_to`, `test_salary_week`, `test_queue_features_shapes_no_leak`, `test_queue_forecaster_beats_baseline`, `test_predict_keys_and_nonneg`, `test_footfall_forecaster_band`, `test_reorder_math`, `test_lightgbm_optional`.

### D13. dashboard SenseBoard — `apps/senseboard` [A13]
Stack: Vite 6, React 19, TypeScript, Tailwind v4, Recharts, Zustand, TanStack Query, react-router, vitest + @testing-library/react, json-schema-to-typescript (devDep used by A01's generator). No shadcn CLI; small local `ui/` components (Card, Badge, Button, Sheet). Env: `VITE_EDGE_URL` (http://localhost:8001), `VITE_CLOUD_URL` (http://localhost:8000), `VITE_STORE_ID`. Data: edge WS `/ws/live` (reconnect with backoff) + TanStack polling 5 s fallback; cloud WS for chain/fleet/notifications. i18n: `useT()` over `hi.json`/`en.json` (every key in both; test enforces), default hi, toggle persisted in localStorage. Numbers: `fmtInr` Indian grouping, 300 ms value transitions, `prefers-reduced-motion` respected, never colour-only (icons + text on states), skeleton loaders, "Data as of HH:MM" freshness badge (sim time when `sim_ts` present).
Pages: **/owner** (default, phone-width first): "₹ बचाया आज / ₹ नुकसान" big cards with basis tooltip; KPI row (Footfall, Conversion, ATV, OSA, Avg wait) with deltas + sparklines from `/kpis/series`; PhonePanel (merges edge WS alerts with cloud `/v1/whatsapp/outbox`; digit buttons → edge `POST /demo/whatsapp/reply`; shows "LAN · WhatsApp pending" when link down, "delivered via WhatsApp 16:42" after); "कल का ऑर्डर" list from `/v1/stores/{id}/reorder` with "Tally में PO बनाओ" → `/v1/stores/{id}/orders`; SyncBadge. **/ops**: AlertFeed (severity icons, Ack/Investigate/False-positive inline), QueueLane cards (count, wait, 15-min forecast arrow + MAE + model badge local/cloud), ShelfGrid (state colour + icon, gap timer, coverage bar, facings, "calibrate" button), PreviewTile(s) (MJPEG img), DemoControls drawer (scenario buttons: Baseline, Evening rush, Stockout Amul, Restock, Open counter, Diwali, Camera blackout; Cable kaat do toggle; clock factor slider; chaos toggles). **/insights**: Heatmap canvas over `/floorplan.png` (peak vs off-peak time slider, dwell/visits toggle), PowerHours hour×weekday matrix (cloud series), ForecastChart (actual vs predicted, MAE), ShrinkTable (`/v1/stores/{id}/recon` + "Reconcile now"), bounce rate + STAR (from dwell/occupancy series). **/chain**: RankTable (metric selector, normalised toggle), FleetTable (status, last seen, fps, backlog, model version, drift badge, rollout controls), manifest card. **/zones**: ZoneCanvas over a still `/preview/{cam}.jpg`: draw polygons (zones, shelves) and lines (arrow shows IN), assign kind/SKU, save via `PUT /config/zones` / `/config/shelves` (P2 polish; P1 minimal).
Acceptance: `npm run build` clean; vitest green; works with edge only (cloud down → chain/insights show "cloud offline" states, owner/ops fully functional); WS reconnect; all strings through `useT`; Lighthouse-ish basics (aria labels on tiles/buttons).
Tests (vitest): `AlertCard.test.tsx` (hi message, ₹, actions post ack), `SyncBadge.test.tsx` (online / offline+backlog / replaying N/M / replayed N/N seq ok), `KpiTile.test.tsx` (delta arrow + aria), `PhonePanel.test.tsx` (digit → reply request, pending vs delivered badges), `ShelfGrid.test.tsx` (state colours + gap timer), `QueueLane.test.tsx` (forecast arrow + model badge), `heatmap.test.ts` (colour scale, cell mapping), `geometry.test.ts` (polygon close, line arrow side), `i18n.test.ts` (key parity), `ws.test.ts` (reconnect backoff).

### D14. devops + integrator + docs — root files, `tools/{demo,setup_dev,ports}.py`, `retailsense/`, `tests/`, `deploy/`, `README.md`, `docs/{DEMO_SCRIPT,RUNBOOK}.md` [A14]
```python
retailsense/__main__.py: subcommands → demo (tools/demo.py), edge, cloud, sim, board, test (pytest + vitest), setup (tools/setup_dev.py), types (gen_ts_types), video (make_demo_video), fetch-models, lint (ruff + tsc)
tools/demo.py: --no-board --no-chain --no-tally --camera <spec> --detector auto --clock 10 --scenario baseline --smoke --ports 8000,8001,5173 --open
  # boot order with health polling: cloud → tally mock → edge → headless sims ×2 (chain) → board (npm.cmd run dev on Windows) → open browser /owner ; prints banner with URLs + beat list
  # Windows: CREATE_NEW_PROCESS_GROUP, kill tree via taskkill /T /F on exit; posix: killpg; PYTHONIOENCODING=utf-8; logs to var/logs/*.log
Makefile: setup demo test lint types video up down ci (all → python -m retailsense ...)
deploy/: edge.Dockerfile (python:3.11-slim + opencv-headless), cloud.Dockerfile, board.Dockerfile (node build → nginx), mosquitto.conf (anon, 1883 + ws 9001), docker-compose.yml services cloud/edge/board/tally-mock + profiles broker (mosquitto, edge uplink mqtt), pg (timescale/timescaledb, SENSECLOUD_DB_URL=postgresql+psycopg://…)
.github/workflows/ci.yml: matrix ubuntu/windows py3.11 → setup_dev --no-models → pytest -m "not slow and not gpu" ; node 22 → npm ci, vitest run, build ; ruff ; types drift check
tests/: test_contracts_importable_everywhere; test_e2e_synthetic (in-process edge with real packages, synthetic source clock 20, FakeUplink; run stockout scenario; assert footfall_in > 0, queue snapshots present, exactly one shelf_gap alert for shelf-A with impact > 0 and Hindi message, shelf.state resolve after /demo/restock, sim.truth footfall within ±10 % of counted); test_offline_replay (edge + cloud in-process via httpx ASGI: cut, ≥ 500 events, restore, cloud has all unique, seq strictly increasing, IngestAck.seq_ok, duplicates on resend = 0 new rows); test_demo_script (slow: subprocess demo --no-board; walk all beats via REST; assert each observable); test_demo_boot (smoke --smoke exits 0 in < 90 s)
docs/DEMO_SCRIPT.md (§E table + fallbacks), docs/RUNBOOK.md (ports, env vars, GPU optional install: pip install onnxruntime-gpu, troubleshooting Windows)
```
Acceptance: `python -m retailsense setup && python -m retailsense demo` boots everything on the dev box with no internet after setup; `--smoke` green; both integration tests green with real packages; CI config valid.

---

## E. Integration plan

**Ports:** SenseCloud 8000 · SenseEdge 8001 · SenseBoard 5173 (dev) / 8080 (docker nginx) · Tally mock 9000 · Mosquitto 1883/9001 (docker only) · Postgres 5432 (docker profile).

**Boot order (`python -m retailsense demo`, alias `make demo`):**
1. `tools/ports.py` frees/validates ports; creates `var/`.
2. SenseCloud (`sqlite:///var/sensecloud.db`, `SENSECLOUD_DEV=1`, `SENSECLOUD_SEED_HISTORY=1`): registers demo store, seeds 30 days, trains forecasters (≈2–4 s), starts dispatcher (simulator notifier). Poll `/health`.
3. Tally mock :9000.
4. SenseEdge with `examples/store_demo.yaml` (camera `synthetic:baseline`, clock 10, uplink http): auto-calibrates shelf references on first scan, starts sync (cloud reachable → online). Poll `/health`.
5. Headless FakeEdges `STR-MH-002`/`STR-KA-003` → cloud (chain + fleet have 3 devices).
6. SenseBoard `npm run dev` (or `--board static` serving `dist/` via cloud at `/board`). Browser opens `http://localhost:5173/owner`.
7. Optional: `--camera webcam:0 --detector onnx` adds a real camera tile (after `python -m retailsense fetch-models` once, with internet, at setup time).

**Integration sequencing for agents:** hour 0–1 A01 lands contracts core; everyone else codes against §C immediately (fakes arrive by hour 1). A14 builds demo.py + integration tests against fakes first (they pass with fakes at reduced assertions), then flips to real packages as they land (registry resolves automatically). A09/A10 boot with fakes on hour 1. Merge gate = `python -m retailsense test` green + `demo --smoke`.

**3-minute stage script (each beat = one DemoControls button → REST call; all observables asserted by `tests/test_demo_script.py`):**

| t | Presenter | Button / call | Observable |
|---|---|---|---|
| 0:00 | "Ramesh-ji's store already has a DVR. Zero new hardware. We point it at the shelves, draw the zones, press calibrate." | /zones → `PUT /config/zones`; `POST /calibrate/shelves/reference-all` | Zones on live preview; shelf grid all green "has_reference" |
| 0:30 | "Evening rush." | `POST /demo/scenario {evening_rush}` | Queue lane 5 → 6, wait ~4 min, `queue_long` alert; PhonePanel Hindi message with ₹ risk; presenter taps **1** → `alert.acked` on board |
| 0:55 | "Forecast says 7 in 15 minutes — model MAE 0.8." | (automatic) | QueueLane forecast arrow + MAE badge; `queue_forecast` alert superseded |
| 1:10 | "Amul milk runs out." | `POST /demo/scenario {stockout, shelf_id: shelf-A}` | Scan counter 1/3 → 3/3, shelf-A red with gap timer, alert "अमूल ताज़ा की शेल्फ 3 मिनट से खाली… ₹", ONDC log shows `available=false` |
| 1:35 | "Pull the cable." | Cable kaat do → `POST /demo/link {down}` | SyncBadge Offline, backlog counter climbing; alerts still arrive on the LAN phone panel ("WhatsApp pending"); fleet shows EDGE-001 offline after 60 s |
| 2:00 | "Reconnect. Nothing lost, in order." | `POST /demo/link {up}` | Badge "312/312 replayed · seq ordered"; cloud chart backfills; pending WhatsApp flips to delivered |
| 2:15 | "Shopkeeper taps 1 = bhar diya." | PhonePanel **1** → `/demo/whatsapp/reply` (+ `/demo/restock/shelf-A`) | Shelf green, alert resolved with final gap minutes and "₹ बचाया" increments |
| 2:30 | "Tally says 48 packs, camera sees 41 — that's shrink." | Reconcile now → `POST …/integrations/tally/reconcile` | ShrinkTable row Amul Δ7 = ₹189, `shrink_suspect` alert |
| 2:45 | "Numbers: MAE 0.8 customers, 3-scan filter, ~1 kbit/s uplink, ₹299/month, 12M stores." | /owner | "₹ बचाया आज" card + BMC slide |

Fallbacks: if the board fails, the same beats run from `docs/DEMO_SCRIPT.md` curl lines; if weights are missing, the synthetic detector path is the default anyway; if the cloud dies, the edge keeps working and SyncBadge shows it honestly.

---

## F. Work breakdown (14 agents, disjoint paths)

```json
[
 {"id":"A01","module":"contracts+docs","owned_paths":["packages/contracts/**","tools/gen_ts_types.py","docs/ARCHITECTURE.md","docs/CONTRACTS.md","docs/DECK.md","docs/BMC.md","docs/PRIVACY.md"],"depends_on_contracts_only":true,
  "deliverables":["retailsense_contracts package exactly per spec section C (enums, ids, hlc, clock, geometry, events, alerts, impact, config, api, ws, topics, db, manifest, i18n, interfaces, registry, testing fakes, synthetic palette, privacy)","examples/store_demo.yaml, manifest_demo.json, festivals_in.csv","schemas/*.json + ts/types.gen.ts + ts/index.ts via tools/gen_ts_types.py","docs: ARCHITECTURE.md (mermaid: pipeline, offline sync sequence, deployment), CONTRACTS.md (generated), DECK.md (6 SIH slides content), BMC.md, PRIVACY.md"],
  "acceptance_tests":["pytest packages/contracts green on Windows","every model/function name in section C importable from retailsense_contracts","examples/store_demo.yaml validates and config_hash stable","registry.resolve falls back to fakes for every key","gen_ts_types regenerates identical committed output","fakes pass protocol conformance tests"]},
 {"id":"A02","module":"simulator","owned_paths":["packages/sim/**","tools/make_demo_video.py"],"depends_on_contracts_only":true,
  "deliverables":["StoreModel agent-based shoppers/queue/shelves with scenarios and chaos","VideoGenerator + render_floorplan using SyntheticPalette and demo geometry","SyntheticFrameSource implementing FrameSource + SyntheticControl with clock_factor pacing","generate_history(days) per history DataFrame contract","FakeEdge headless store posting IngestBatch to cloud","CLI: video|headless|history; make_demo_video.py -> var/demo_store.mp4"],
  "acceptance_tests":["pytest packages/sim green","shoppers cross entrance line upward then counter line leftward; abandoners never cross counter line","stockout scenario drains shelf-A to 0 facings within over_s","rendered shopper pixels within SHOPPER_HSV range; empty shelf shows backing colour","history has 43,200 minute rows with festival/salary flags","FakeEdge batches have gap-free seq and validate as IngestBatch"]},
 {"id":"A03","module":"edge-capture+edge-perception","owned_paths":["packages/edgecv/**","tools/fetch_models.py","models/manifest.json"],"depends_on_contracts_only":true,
  "deliverables":["open_source + File/Rtsp/Webcam FrameSources (Windows CAP_DSHOW)","SyntheticDetector (HSV blob, split heuristic)","OnnxPersonDetector (YOLOv8-style decode, letterbox, NMS, CPU/CUDA EP auto)","UltralyticsDetector fallback (lazy import)","ByteTrackLite (Kalman + two-stage Hungarian association + centroid gate)","Homography PointMapper","annotate_frame with person pixelation","CvPipeline thread loop + ModelManager sha/version check","fetch_models.py + models/manifest.json"],
  "acceptance_tests":["pytest packages/edgecv green without weights","tracker holds ids over 30-frame linear motion and crossing objects","letterbox/decode/NMS unit tests","select_detector auto rules","annotate never writes to disk and blurs only person boxes","manifest sha verification"]},
 {"id":"A04","module":"edge-shelf","owned_paths":["packages/edgeshelf/**"],"depends_on_contracts_only":true,
  "deliverables":["ClassicalCoverageEstimator with calibration reference and facings runs","ShelfStateMachine (3-scan persistence, occlusion skip, FP feedback, OSA, gap minutes)","shelf_thumbnail 96x96","occluded_by","TaggedSkuIdentifier + ClipSkuIdentifier stub (Unavailable without weights)","ShelfScanner"],
  "acceptance_tests":["pytest packages/edgeshelf green","coverage within ±0.1 of facings/capacity on palette-rendered shelves, horizontal and vertical","exactly one empty transition after 3 empty scans, none after 2","FP feedback raises persistence to 4","thumbnail <= 96x96 and <= 16 KB","OSA/gap arithmetic"]},
 {"id":"A05","module":"edge-analytics","owned_paths":["packages/edgeanalytics/**"],"depends_on_contracts_only":true,
  "deliverables":["ZoneEngine: zone membership with inertia, LineCrosser with IN/OUT semantics, dwell samples, occupancy cadence, HeatmapAccumulator with floor mapping and delta flush, hot reload"],
  "acceptance_tests":["pytest packages/edgeanalytics green","demo entrance line: upward track = IN, downward = OUT, jitter not double counted","dwell equals frames×dt ±1 frame","heat dwell sum equals tracked time","reload preserves track state"]},
 {"id":"A06","module":"edge-queue","owned_paths":["packages/edgequeue/**"],"depends_on_contracts_only":true,
  "deliverables":["QueueAnalyzer: joins/served/abandoned with min-age guard, arrival and service rates, Little's-Law wait with fallbacks, snapshot cadence, long_since_ts, day reset","TrendForecaster edge short-horizon forecast with self-scored MAE and cloud override"],
  "acceptance_tests":["pytest packages/edgequeue green","scripted scenario yields served 3 / abandoned 1 / transient ignored","method switches little_service -> observed_wait -> default_service correctly","forecast non-negative with keys 5/10/15/30","cloud override expires after 2 min"]},
 {"id":"A07","module":"edge-rules","owned_paths":["packages/edgerules/**"],"depends_on_contracts_only":true,
  "deliverables":["RuleEngine for shelf_gap, queue_long, queue_forecast, camera_down, sync_backlog, footfall_spike with dedupe, severity, hysteresis, ack handling, order.requested","ImpactCalculator wrapper","i18n rendering hi+en","rules_default.yaml + loader"],
  "acceptance_tests":["pytest packages/edgerules green","one open alert per (kind, subject)","hi and en messages contain ₹ amount, basis factor, digit menu","false_positive ack resolves and triggers feedback","queue resolve hysteresis","all emitted Observations validate"]},
 {"id":"A08","module":"edge-store+edge-uplink","owned_paths":["packages/edgestore/**","packages/edgeuplink/**"],"depends_on_contracts_only":true,
  "deliverables":["EdgeStore (SQLite WAL synchronous=FULL, transactional outbox, seq/hlc stamping, views, kpi_today, state)","RetentionJob per RetentionPolicy","KpiAggregator day rollover + deltas","HttpUplink (primary)","MqttUplink (paho v2 API, MQTT5, expiry, LWT) behind flag","LinkController","SyncWorker with replay stats, backoff, heartbeat batches, command dispatch"],
  "acceptance_tests":["pytest packages/edgestore packages/edgeuplink green","events and outbox inserted in one transaction; crash test leaves seq gap-free","telemetry expires, alerts/txn never expire or evict","cut -> 500 events -> restore replays 500 in order with replayed_since_restore==500 and seq_ok","resend after lost ack does not re-mark","retention purge counts"]},
 {"id":"A09","module":"edge-api+senseedge","owned_paths":["apps/senseedge/**"],"depends_on_contracts_only":true,
  "deliverables":["create_app + Wiring via registry with overrides","CameraWorker threads -> queue.Queue -> asyncio consumer -> RuleEngine -> EdgeStore -> WsManager","all edge REST endpoints of section C.16, WS /ws/live, MJPEG preview with blur","demo endpoints proxying SyntheticControl","heartbeat/kpi/retention/edge-forecast/model-check tasks","graceful Windows shutdown","python -m senseedge CLI"],
  "acceptance_tests":["pytest apps/senseedge green using contracts fakes only","/health, WS hello+kpi, ack flow, link toggle + sync status, calibrate, config hot reload, MJPEG streams >= 2 frames, demo endpoints 404 without synthetic, whatsapp reply digit mapping"]},
 {"id":"A10","module":"cloud-api+sensecloud","owned_paths":["apps/sensecloud/**"],"depends_on_contracts_only":true,
  "deliverables":["create_app with CloudSettings; ingest idempotent + seq check + commands in ack","aggregator (series_5m, kpi_daily, views)","alerting (mirror + device_offline + shrink)","dispatcher (notifier via registry, webhook -> command)","fleet registry + manifest/rollout","reports (json/csv/whatsapp)","forecast glue via registry, forecasts table + live MAE","seed demo store + history","mock ONDC router","WS fan-out","optional MQTT bridge","python -m sensecloud CLI"],
  "acceptance_tests":["pytest apps/sensecloud green with contracts fakes","resend of same batch -> accepted 0 duplicates 500","seq gaps reported","KPI fixture matches hand-computed values","device_offline raised after 60 s and resolved","webhook digit returns command in next ack","chain rank + fleet assignment + reports formats"]},
 {"id":"A11","module":"integrations","owned_paths":["packages/integrations/**"],"depends_on_contracts_only":true,
  "deliverables":["TallyClient + tally_xml pure builders/parsers + tally_mock FastAPI app (port 9000, /mock/state)","reconcile() -> ReconcileReport + StockReconciled observations","WhatsAppSimulator, WhatsAppCloudNotifier, TelegramNotifier implementing Notifier","OndcStubPublisher (Beckn on_update, unsigned)","build_router for /v1/whatsapp/* and /v1/stores/{id}/integrations/*"],
  "acceptance_tests":["pytest packages/integrations green","XML round-trip against mock","reconcile flags Amul 48 vs 41 = ₹189 only","simulator outbox and digit reply mapping","ONDC payload posted to test server","notifier payload shapes with MockTransport"]},
 {"id":"A12","module":"cloud-forecast","owned_paths":["packages/forecasting/**"],"depends_on_contracts_only":true,
  "deliverables":["festivals loader/features (2026-27 Indian calendar, verified dates)","queue feature builder (lags, calendar, festival, salary week)","QueueForecaster (sklearn HGB default, LightGBM optional) per horizon with FitReport","FootfallForecaster daily with bands","suggest_reorder","eval rolling MAE"],
  "acceptance_tests":["pytest packages/forecasting green","fit on contracts fake_history(30) < 5 s","holdout MAE < naive baseline and <= 1.0 customers on sim history","predictions non-negative with horizons 5/10/15/30","reorder arithmetic","lightgbm optional path"]},
 {"id":"A13","module":"dashboard-senseboard","owned_paths":["apps/senseboard/**"],"depends_on_contracts_only":true,
  "deliverables":["Vite+React19+TS+Tailwind app with @contracts/types alias","pages /owner /ops /insights /chain /zones","components KpiTile, AlertCard, QueueLane, ShelfGrid, PhonePanel, SyncBadge, FreshnessBadge, LangToggle, Heatmap, PowerHours, ShrinkTable, ForecastChart, FleetTable, RankTable, ZoneCanvas, DemoControls, PreviewTile","edge WS client with reconnect + TanStack polling fallback, cloud client","i18n hi/en with key parity","vitest suite"],
  "acceptance_tests":["npm run build clean and vitest green","AlertCard renders Hindi message with ₹ and posts ack","SyncBadge four states incl. 'N/N replayed · seq ordered'","PhonePanel digit -> /demo/whatsapp/reply and pending/delivered badges","ShelfGrid state colours + gap timer","i18n key parity","app usable with cloud offline"]},
 {"id":"A14","module":"devops+integrator+docs","owned_paths":["README.md","Makefile","pyproject.toml","package.json","docker-compose.yml",".github/**",".gitignore","retailsense/**","tools/demo.py","tools/setup_dev.py","tools/ports.py","tests/**","deploy/**","docs/DEMO_SCRIPT.md","docs/RUNBOOK.md"],"depends_on_contracts_only":true,
  "deliverables":["python -m retailsense CLI (demo|edge|cloud|sim|board|test|setup|types|video|fetch-models|lint) + Makefile wrappers","tools/demo.py one-command Windows-safe supervisor with boot order, health polling, banner, --smoke, --camera, --clock, --no-board, --no-chain","setup_dev.py editable installs + npm ci","docker-compose with profiles broker/pg and Dockerfiles, mosquitto/nginx configs","GitHub Actions CI (ubuntu+windows pytest, vitest+build, ruff, types drift)","integration tests: test_e2e_synthetic, test_offline_replay, test_demo_script (slow), test_demo_boot (smoke)","README quickstart, DEMO_SCRIPT.md (beats + curl fallbacks), RUNBOOK.md"],
  "acceptance_tests":["python -m retailsense setup then demo boots cloud, tally mock, edge, 2 headless stores, board on the dev box without internet","demo --smoke exits 0 < 90 s","test_e2e_synthetic: shelf_gap alert for shelf-A with impact > 0 and Hindi text; footfall within ±10% of sim truth","test_offline_replay: >= 500 events buffered during cut, all unique on cloud, seq strictly increasing, seq_ok true, zero new rows on resend","CI workflow valid and green on fakes-only stage"]}
]
```