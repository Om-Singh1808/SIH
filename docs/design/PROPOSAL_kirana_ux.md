# RetailSense Architecture Proposal — Kirana Business + UX Lens

## 0. Design thesis (what the retailer-judge must feel in 3 minutes)

The end user is Ramesh-ji, a 100-500 sq ft kirana owner, Rs 2-5 lakh/month revenue, 1,500-3,000 SKUs, one Hikvision/CP Plus DVR already on the wall, a Rs 8k Android phone, Tally or Vyapar on a laptop, WhatsApp as his operating system. He will never open a "dashboard". He opens WhatsApp, and he thinks in rupees.

Therefore every module is designed around four UX laws:

1. **Rupee-first.** Every alert, KPI and chart carries `impact_inr`. "Parle-G gap 38 min = Rs 340 lost" beats "OSA 91%".
2. **WhatsApp-first, dashboard-second.** The edge node generates alerts in Hindi/vernacular with 1-tap replies (`1 = bhar diya`, `2 = order karo`, `3 = galat alert`). SenseBoard is for the chain manager, FMCG distributor and the judge.
3. **Zero-hardware onboarding in 10 minutes.** Paste DVR RTSP URL (or point old phone), draw shelf polygons on a phone, tag SKUs from the Tally item list, done.
4. **Works when the cable is cut and the power flickers.** SQLite WAL, outbox, local alerts on LAN. Offline is the default state, not a fault.

Business model: SaaS Rs 299 (1 cam, WhatsApp only) / Rs 599 (2-4 cams + SenseBoard + Tally) / Rs 999 (ONDC + distributor share) per store per month; FMCG share-of-shelf data subscription Rs 25k/brand/district/month; optional hardware kit (RPi5 + Hailo) Rs 12k at 20% margin. TAM: 12-13M kiranas x Rs 299 = Rs 4,500 crore ARR ceiling; SAM: ~1.2M kiranas already owning CCTV + smartphone.

---

## 1. System overview

```
[DVR/NVR RTSP | webcam | video file | synthetic video] 
        |
   SenseEdge (Python 3.11, one process, Windows/RPi/Jetson)
   ├─ ingest      : frame source abstraction, 2-5 fps sampler
   ├─ cv          : YOLO11n ONNX person det + ByteTrack; shelf gap detector; CLIP few-shot SKU
   ├─ analytics   : zones (queue/shelf/entrance), persistence filter, Little's Law, heatmap
   ├─ rules       : alert engine -> impact_inr -> Hindi templates
   ├─ store       : SQLite WAL + outbox (same transaction)
   ├─ sync        : MQTT5 QoS1 store-and-forward, expiry per class, link toggle
   ├─ localapi    : FastAPI :8001 REST+WS (LAN dashboard works with zero internet)
   └─ notify      : WhatsApp Cloud API adapter (+ local "WhatsApp simulator" for demo)
        |  MQTT (events, aggregates, thumbnails only)
   SenseCloud (FastAPI :8000 + Mosquitto-compatible embedded broker (amqtt) + SQLite/Postgres)
   ├─ ingest consumer (idempotent on event_id)
   ├─ forecasting (LightGBM, Indian festival calendar)
   ├─ integrations: Tally XML :9000, ONDC Beckn on_update, Zoho
   ├─ fleet registry + heartbeat absent() alerting
   └─ REST+WS for SenseBoard
        |
   SenseBoard (Vite + React 19 + Tailwind + shadcn, Hindi/English toggle)
   ├─ /owner   (phone-first: "Aaj ka hisaab")
   ├─ /ops     (live alerts, queue lanes, shelf grid)
   ├─ /insights (heatmap, power hours, shrink reconciliation)
   └─ /chain   (store ranking, fleet health, FMCG share-of-shelf)
```

The demo runs with **plain `python -m retailsense demo`** on Windows: it starts SenseCloud, an embedded MQTT broker (`amqtt`, pure Python; falls back to direct HTTP sync if broker unavailable), a SenseEdge fed by the synthetic video generator, the store simulator, the WhatsApp simulator, and SenseBoard dev server (`npm run dev`) — all in one supervisor with a link-cut toggle.

---

## 2. Monorepo tree

```
retailsense/
├── Makefile                     # make demo | make test | make lint | make up (docker)
├── pyproject.toml               # uv/pip workspace: packages/* + apps/*
├── docker-compose.yml           # edge, cloud, broker, board, postgres (deployment only)
├── retailsense/__main__.py      # `python -m retailsense demo|edge|cloud|sim`
├── packages/
│   ├── contracts/               # AGENT 1 — frozen first; everyone imports from here
│   │   └── retailsense_contracts/
│   │       ├── events.py        # pydantic: Event envelope + all payloads
│   │       ├── alerts.py        # Alert, Severity, AlertAction, ImpactInr
│   │       ├── config.py        # StoreConfig, CameraConfig, Zone, ShelfPolygon, SKU
│   │       ├── api.py           # REST request/response models
│   │       ├── topics.py        # MQTT topic builders + expiry policy table
│   │       ├── db.py            # SQLAlchemy declarative models (edge + cloud share)
│   │       ├── i18n.py          # message keys + hi/en/ta/te templates
│   │       └── schemas/*.json   # exported JSON Schema (for frontend zod + vitest)
│   ├── sim/                     # AGENT 2 — synthetic store simulator + video generator
│   │   └── retailsense_sim/
│   │       ├── store_model.py   # agent-based shoppers, queues, shelf depletion
│   │       ├── video_gen.py     # sprites on floorplan -> frames / mp4
│   │       ├── scenarios.py     # "evening_rush", "diwali", "stockout_parleg", "cable_cut"
│   │       └── cli.py
│   ├── cv/                      # AGENT 3 (detect/track) + AGENT 4 (shelf) + AGENT 5 (sku)
│   │   └── retailsense_cv/
│   │       ├── source.py        # FrameSource: rtsp|file|webcam|synthetic
│   │       ├── detector.py      # PersonDetector (YOLO11n ONNX, CPU/CUDA EP)
│   │       ├── tracker.py       # ByteTrack (pure numpy port)
│   │       ├── shelf.py         # ShelfGapDetector + CoverageEstimator
│   │       ├── sku.py           # CLIP/DINOv2 embeddings + FAISS-lite (numpy) few-shot
│   │       ├── pipeline.py      # CvPipeline: frames -> Observations
│   │       └── models/          # .onnx weights + download script (git-lfs or fetch)
│   ├── analytics/               # AGENT 6
│   │   └── retailsense_analytics/
│   │       ├── zones.py         # point-in-polygon, line crossing, entrance counting
│   │       ├── queue.py         # QueueState, dwell, abandonment, Little's Law
│   │       ├── shelf_state.py   # 3-scan persistence filter, gap duration, OSA
│   │       ├── heatmap.py       # grid accumulator
│   │       └── impact.py        # rupee impact calculator (price x velocity x gap_min)
│   ├── rules/                   # AGENT 7
│   │   └── retailsense_rules/
│   │       ├── engine.py        # RuleEngine: Observation/Aggregate -> Alert
│   │       ├── rules_default.yaml
│   │       └── templates.py     # Hindi/vernacular rendering via contracts.i18n
│   ├── edgestore/               # AGENT 8
│   │   └── retailsense_edgestore/
│   │       ├── db.py            # SQLite WAL, synchronous=FULL
│   │       ├── outbox.py        # transactional outbox + replay cursor
│   │       └── retention.py     # DPDP: purge thumbnails >7d, tracks >24h
│   ├── sync/                    # AGENT 9
│   │   └── retailsense_sync/
│   │       ├── uplink.py        # MQTT5 client, QoS1, expiry by class, backoff
│   │       ├── link.py          # LinkController (cut/restore toggle)
│   │       └── http_fallback.py # POST /v1/ingest/batch when no broker
│   ├── forecasting/             # AGENT 10
│   │   └── retailsense_forecast/
│   │       ├── features.py      # lags, hour, dow, festival flags
│   │       ├── festivals_in.py  # Indian calendar 2026-27 (Diwali, Eid, Pongal, Onam, local)
│   │       ├── queue_forecaster.py   # LightGBM 5/10/15/30 min
│   │       ├── footfall_forecaster.py# daily
│   │       ├── reorder.py       # suggested order qty per SKU (velocity x lead time)
│   │       └── eval.py          # MAE reporting
│   ├── integrations/            # AGENT 11
│   │   └── retailsense_integrations/
│   │       ├── tally.py         # TallyClient: XML over HTTP :9000 (+ mock server)
│   │       ├── ondc.py          # Beckn /on_update with Ed25519 signing
│   │       ├── zoho.py
│   │       ├── whatsapp.py      # Meta Cloud API adapter + WhatsAppSimulator
│   │       └── reconcile.py     # visual vs system stock -> shrink table
│   └── common/                  # AGENT 1 (small) — logging, clock, ids, settings
├── apps/
│   ├── senseedge/               # AGENT 12 — wires packages into the edge process
│   │   └── senseedge/
│   │       ├── main.py          # supervisor: ingest->cv->analytics->rules->store->sync
│   │       ├── api.py           # FastAPI :8001 local REST+WS
│   │       ├── calibrate.py     # polygon drawing endpoints
│   │       └── config/store_demo.yaml
│   ├── sensecloud/              # AGENT 13
│   │   └── sensecloud/
│   │       ├── main.py          # FastAPI :8000
│   │       ├── broker.py        # embedded amqtt broker :1883/:9001(ws)
│   │       ├── consumer.py      # idempotent ingest
│   │       ├── routers/{stores,kpis,alerts,forecast,integrations,fleet,ws}.py
│   │       └── db.py            # SQLite (demo) / Postgres+Timescale (compose)
│   └── senseboard/              # AGENT 14 — Vite React
│       ├── package.json  vite.config.ts  tailwind.config.ts
│       ├── src/
│       │   ├── api/{client.ts, ws.ts, types.ts (generated from schemas/*.json)}
│       │   ├── i18n/{hi.json,en.json,ta.json}
│       │   ├── pages/{Owner,Ops,Insights,Chain,Onboard}.tsx
│       │   ├── components/{KpiTile,AlertCard,QueueLane,ShelfGrid,Heatmap,PowerHours,ShrinkTable,SyncBadge,WhatsAppPhone}.tsx
│       │   ├── store/useLive.ts (Zustand)  hooks/useKpis.ts (TanStack Query)
│       │   └── demo/DemoControls.tsx   # cable-cut, scenario buttons
│       └── tests/*.test.tsx (vitest)
├── tools/
│   ├── demo.py                  # orchestrates everything; `make demo`
│   ├── gen_ts_types.py          # pydantic -> JSON schema -> TS
│   └── train_shelf.py           # fine-tune script (offline, optional)
├── tests/                       # cross-package integration tests (pytest)
│   ├── test_e2e_synthetic.py    # sim video -> cv -> alerts -> cloud -> kpis
│   └── test_offline_replay.py   # cut link, generate 500 events, restore, assert ordered
└── docs/  (research/, ARCHITECTURE.md, DEMO_SCRIPT.md, pitch/)
```

Ownership rule: an agent edits only its package directory plus its tests; `contracts` is frozen after day 0 (changes via PR reviewed by the integrator).

---

## 3. Contracts (packages/contracts)

### 3.1 Event envelope (`events.py`)

```python
class EventClass(str, Enum): TELEMETRY="telemetry"; AGGREGATE="aggregate"; ALERT="alert"; TXN="txn"; CONFIG="config"

class Event(BaseModel):
    event_id: str            # ulid
    store_id: str            # "STR-DL-001"
    device_id: str
    camera_id: str | None
    ts: datetime             # edge wall clock UTC
    hlc: str                 # hybrid logical clock "1724400000123-0007-dev"
    type: str                # "track.update"|"queue.snapshot"|"shelf.scan"|"footfall.count"|"alert.raised"|"alert.acked"|"stock.reconciled"
    cls: EventClass
    seq: int                 # monotonic per device (replay ordering proof)
    payload: TrackUpdate | QueueSnapshot | ShelfScan | FootfallCount | AlertRaised | AlertAcked | StockReconciled

class TrackUpdate(BaseModel):   # never leaves edge; aggregated
    track_id: int; bbox: tuple[int,int,int,int]; zone_ids: list[str]; conf: float
class QueueSnapshot(BaseModel):
    zone_id: str; count: int; avg_dwell_s: float; max_dwell_s: float; arrival_rate_pm: float; est_wait_s: float; abandoned_5m: int
class ShelfScan(BaseModel):
    shelf_id: str; sku_id: str | None; coverage: float; facings: int; state: Literal["stocked","partial","empty"]; thumb_b64: str | None  # 96x96 jpeg only
class FootfallCount(BaseModel):
    zone_id: str; entries: int; exits: int; occupancy: int; window_s: int
class AlertRaised(BaseModel):
    alert: "Alert"
class AlertAcked(BaseModel):
    alert_id: str; action: Literal["restocked","order","false_positive","opened_counter"]; by: Literal["whatsapp","board","auto"]
class StockReconciled(BaseModel):
    sku_id: str; visual_units: int; system_units: int; delta_units: int; delta_inr: float; source: Literal["tally","zoho","manual"]
```

### 3.2 Alerts (`alerts.py`)

```python
class Severity(str, Enum): INFO="info"; WARN="warn"; HIGH="high"; CRITICAL="critical"
class ImpactInr(BaseModel):
    lost_sales_inr: float; basis: str    # "Rs 5 x 4.2 units/hr x 0.63 hr"
class Alert(BaseModel):
    alert_id: str; store_id: str; kind: Literal["shelf_gap","queue_long","queue_forecast","footfall_spike","shrink_suspect","camera_down","sync_backlog"]
    severity: Severity; title_key: str; params: dict; impact: ImpactInr | None
    raised_at: datetime; resolved_at: datetime | None
    actions: list[Literal["restocked","order","false_positive","opened_counter"]]
    message_hi: str; message_en: str     # pre-rendered on edge so offline WhatsApp/LAN works
```

### 3.3 Config (`config.py`)

```python
class SKU(BaseModel): sku_id: str; name_en: str; name_hi: str; mrp_inr: float; margin_pct: float; velocity_units_per_hr: float; tally_item_name: str | None; ondc_item_id: str | None; enrol_embeddings: int = 0
class ShelfPolygon(BaseModel): shelf_id: str; camera_id: str; polygon: list[tuple[float,float]]; sku_id: str | None; min_facings: int = 2
class Zone(BaseModel): zone_id: str; camera_id: str; kind: Literal["queue","entrance","counter_line","aisle"]; polygon: list[tuple[float,float]]; line: tuple[tuple[float,float],tuple[float,float]] | None
class CameraConfig(BaseModel): camera_id: str; source: str  # "rtsp://...", "file:demo.mp4", "webcam:0", "synthetic:evening_rush"
    fps_sample: float = 2.0; shelf_scan_interval_s: int = 60
class StoreConfig(BaseModel): store_id: str; name: str; lang: Literal["hi","en","ta","te"]="hi"; owner_whatsapp: str; tier: Literal["kirana","mini","chain"]; cameras: list[CameraConfig]; zones: list[Zone]; shelves: list[ShelfPolygon]; skus: list[SKU]; tally_url: str | None; ondc_bpp_id: str | None
```

### 3.4 MQTT topics (`topics.py`)

| Topic | Class | QoS | Expiry |
|---|---|---|---|
| `rs/v1/{store}/{device}/telemetry` | telemetry (fps, ms, temp, backlog) | 1 | 1 h |
| `rs/v1/{store}/{device}/agg/queue` | QueueSnapshot every 10 s | 1 | 6 h |
| `rs/v1/{store}/{device}/agg/shelf` | ShelfScan every 60 s | 1 | 24 h |
| `rs/v1/{store}/{device}/agg/footfall` | FootfallCount every 60 s | 1 | 24 h |
| `rs/v1/{store}/{device}/alert` | Alert raised/acked | 1 | never |
| `rs/v1/{store}/{device}/txn` | StockReconciled, orders | 1 | never |
| `rs/v1/{store}/{device}/cmd` | cloud -> edge (config push, model version) | 1 | 24 h |
| `rs/v1/{store}/{device}/status` | retained LWT `online|offline` | 1 | retained |

### 3.5 DB schema (`db.py`, shared SQLAlchemy models; SQLite on edge, Postgres on cloud)

- `events(event_id PK, store_id, device_id, ts, hlc, seq, type, cls, payload JSON)` index `(store_id, ts)`, `(device_id, seq)`
- `outbox(id PK autoinc, event_id FK, cls, enqueued_at, expires_at NULL, sent_at NULL, attempts)` — **inserted in the same transaction as `events`**
- `alerts(alert_id PK, store_id, kind, severity, raised_at, resolved_at, impact_inr, action, acked_by, message_hi, message_en, params JSON)`
- `shelf_state(shelf_id PK, sku_id, state, coverage, facings, consecutive_empty_scans, gap_started_at, last_scan_at)`
- `queue_state(zone_id PK, count, est_wait_s, arrival_rate_pm, updated_at)`
- `heatmap_cells(camera_id, cell_x, cell_y, hour_bucket, dwell_s, visits)` PK composite
- `kpi_daily(store_id, date, footfall, conversion, atv_inr, osa_pct, avg_wait_s, gap_minutes, lost_sales_inr, shrink_inr)`
- `stock_recon(id, store_id, sku_id, ts, visual_units, system_units, delta_inr, source)`
- `forecasts(store_id, zone_id, made_at, horizon_min, predicted, actual NULL, mae NULL)`
- cloud-only: `stores`, `devices(device_id, store_id, last_seen, model_version, fps, backlog)`, `sku_catalog`, `festivals(date, name, region, weight)`

### 3.6 REST + WS

**SenseEdge :8001 (LAN, no internet needed)**
- `GET /health` -> `{fps, infer_ms, backlog, link:"up|down", model_version}`
- `GET /kpis/today` -> `KpiToday`
- `GET /alerts?status=open` ; `POST /alerts/{id}/ack {action}`
- `GET /queue` -> list[QueueSnapshot] ; `GET /shelves` -> list[ShelfState]
- `GET /heatmap?camera_id&from&to`
- `GET /frame/{camera_id}.jpg` (annotated, no persistence) ; `WS /ws/live` (JSON events stream)
- `POST /calibrate/zones`, `POST /calibrate/shelves`, `POST /sku/enrol` (multipart 5-10 images) 
- `POST /demo/link {state:"up"|"down"}` ; `POST /demo/scenario {name}` ; `POST /demo/whatsapp/reply {alert_id, digit}`
- `GET /summary/daily?lang=hi` -> text + `audio_url` (pyttsx3 / edge-tts offline voice note)

**SenseCloud :8000**
- `POST /v1/ingest/batch` (HTTP fallback) ; `GET /v1/stores` ; `GET /v1/stores/{id}/kpis?range=today|7d|30d`
- `GET /v1/stores/{id}/alerts` ; `POST /v1/alerts/{id}/ack`
- `GET /v1/stores/{id}/forecast/queue?zone_id` -> `{horizons:{5:..,10:..,15:..,30:..}, mae_7d}`
- `GET /v1/stores/{id}/forecast/footfall?days=7` ; `GET /v1/stores/{id}/reorder` -> list[ReorderSuggestion]
- `GET /v1/stores/{id}/recon` ; `POST /v1/stores/{id}/integrations/tally/sync` ; `POST /v1/stores/{id}/integrations/ondc/publish`
- `GET /v1/chain/rank?metric=osa|wait|lost_inr` ; `GET /v1/fleet` ; `GET /v1/fmcg/share-of-shelf?brand=`
- `WS /v1/ws?store_id=` fan-out of alerts/aggregates
- `GET /v1/whatsapp/outbox` (simulator inbox for the on-stage phone panel); `POST /v1/whatsapp/webhook` (real Meta webhook)

---

## 4. CV pipeline (packages/cv)

```python
class FrameSource(Protocol):
    def __iter__(self) -> Iterator[tuple[float, np.ndarray]]  # (ts, BGR frame)
def open_source(spec: str, fps_sample: float) -> FrameSource   # rtsp:// file: webcam:N synthetic:<scenario>

class PersonDetector:                     # YOLO11n.onnx via onnxruntime, CUDAExecutionProvider if available else CPU
    def __call__(self, frame) -> np.ndarray  # [N,5] x1,y1,x2,y2,conf
class ByteTracker:
    def update(self, dets) -> list[Track]    # Track(id, bbox, age, hits)
class ShelfGapDetector:                   # fine-tuned YOLO11n (empty / front-empty) ; fallback: classical coverage (edge density + colour variance vs calibrated "full" reference)
    def scan(self, frame, shelf: ShelfPolygon) -> ShelfScan
class SkuEmbedder:                        # CLIP ViT-B/32 ONNX; FAISS replaced by numpy cosine for Windows simplicity
    def enrol(self, sku_id, images: list[np.ndarray]) -> int
    def identify(self, crop) -> tuple[str|None, float]
class CvPipeline:
    def __init__(self, cfg: CameraConfig, zones, shelves, on_obs: Callable[[Observation], None])
    def run_forever(self) -> None
```

Design points for the kirana lens:
- **Synthetic-first contract**: `synthetic:` source draws shoppers as coloured rounded rectangles with a head circle on a floorplan PNG; the detector has a `--detector=synthetic` mode (colour blob detection) so CV runs real tracking/zone logic without GPU. With GPU, real YOLO11n runs on the same frames (sprites are also detectable by YOLO when rendered from a small sprite sheet of person silhouettes — we ship both).
- Shelf scans every 60 s, queue at 2 fps; saves CPU on a RPi/old phone.
- Classical fallback coverage estimator ensures the shelf story works even if the fine-tuned weights don't download on stage.
- Privacy: frames are never written; thumbnails are 96x96 of the shelf polygon only (no people); tracks purged every 24 h.
- Metrics exported: `infer_ms`, `fps`, `det_conf_mean`, `track_count` to `/health` and telemetry topic.

---

## 5. Analytics + rules + rupee impact

- `ShelfStateMachine.apply(scan)`: `stocked -> partial(<20% empty) -> empty(>=20%)`; `empty` must persist 3 consecutive scans before `shelf_gap` alert; `gap_started_at` set on first empty; `gap_minutes` KPI accumulates.
- `impact.lost_sales(sku, gap_minutes) = sku.mrp_inr * sku.velocity_units_per_hr * gap_minutes/60 * 0.31` (31% of shoppers buy elsewhere — cited basis string kept in alert for judges).
- Queue: `est_wait_s = count / max(arrival_rate_pm/60, eps)` (Little's Law); `abandoned` = tracks exiting queue polygon without crossing `counter_line`; `queue_long` alert if `count >= 4` for `>= 60 s`; `queue_forecast` alert if cloud forecast(15 min) >= 5 (edge caches last forecast for offline use).
- Rules YAML lets a shopkeeper (or judge) change thresholds on stage:
```yaml
- kind: shelf_gap     when: shelf.consecutive_empty_scans >= 3   severity: high   actions: [restocked, order, false_positive]
- kind: queue_long    when: queue.count >= 4 and queue.duration_s >= 60  severity: warn  actions: [opened_counter]
- kind: shrink_suspect when: recon.delta_units >= 3 and recon.delta_inr >= 200  severity: high
```
- Hindi templates (`i18n.py`): `shelf_gap.hi = "⚠️ {sku_hi} ki shelf {gap_min} min se khaali hai. Anumaanit nuksaan ₹{lost}. Reply 1=bhar diya, 2=distributor ko order, 3=galat alert"`. Also `ta`, `te` keys; English for the board.

---

## 6. Offline-first store-and-forward (packages/edgestore, packages/sync)

- `EdgeDB.write(event)`: one SQLite transaction inserts into `events` and `outbox`; `PRAGMA journal_mode=WAL; synchronous=FULL` — survives power cut mid-write (85% of households see daily outages).
- `Outbox.pending(limit=500)` ordered by `id`; `Uplink.publish()` uses paho MQTT5 QoS1, `clean_start=False`, `session_expiry=7d`, `message_expiry_interval` from the topic table; on `on_publish` ack -> `sent_at` set. Alerts/txn never expire; telemetry evicted when outbox > 50k rows (`OverwriteOldestData` for telemetry, `RejectNewData` never applies to alerts).
- `LinkController.cut()` sets a flag that makes the MQTT client disconnect and refuse reconnect; `restore()` reconnects and replays. Exposed via `POST /demo/link` and a physical-feeling red toggle in SenseBoard ("🔌 Cable kaat do").
- Cloud consumer: `INSERT ... ON CONFLICT(event_id) DO NOTHING`; ordering by `(device_id, seq)`; KPI recompute is idempotent per (store, date).
- Local WhatsApp: while offline, the edge still renders messages and pushes them to the LAN phone panel via `/ws/live`; real WhatsApp sends are queued in outbox class `alert` and flushed on restore (owner gets them late but never loses them).
- Proof test `tests/test_offline_replay.py`: cut, generate 500 events, restore, assert cloud has 500 unique and `seq` strictly increasing.

---

## 7. Forecasting (packages/forecasting)

- Features: queue lags (1,2,3,5,10 min), footfall lags, rolling means, hour, dow, `is_festival`, `festival_weight`, `days_to_festival`, `is_salary_week` (1st-7th; kirana credit cycle), `is_rainy_flag` (manual).
- `QueueForecaster.fit(df)`, `.predict(now) -> {5,10,15,30}`; LightGBM (pip) with sklearn GradientBoosting fallback; trained on the simulator's 30 synthetic days at demo boot (~2 s), MAE reported live on the board ("MAE 0.7 customers, 7-day").
- `FootfallForecaster` daily, 7 days ahead, drives `reorder.py`: `suggest_qty = forecast_units(lead_time_days) + safety_stock - system_stock`, rendered as the "Kal ka order" WhatsApp list with one tap to create a Tally purchase order XML (mocked on stage).
- `festivals_in.py` ships 2026-27 dates for Diwali, Dussehra, Eid, Holi, Pongal, Onam, Ganesh Chaturthi, Chhath, Christmas, plus a per-store `local_melas` list.

---

## 8. Integrations (packages/integrations)

- `TallyClient(url="http://localhost:9000")`: `export_stock_summary() -> dict[item_name, qty]` via `<ENVELOPE><HEADER><TALLYREQUEST>Export</TALLYREQUEST>...` XML; `post_stock_journal(adjustments)`; a `tally_mock_server.py` serves realistic XML for the demo.
- `reconcile.run(store)`: visual units (facings x depth estimate) vs Tally qty -> `StockReconciled`; delta > threshold -> `shrink_suspect` alert with `delta_inr`.
- `OndcClient.on_update(item_id, available)`: Beckn payload signed with Ed25519 (`pynacl`), posted to a local mock gateway; board shows "ONDC: Parle-G marked unavailable 16:12, back 16:21".
- `WhatsAppAdapter.send(to, text, buttons)`; `WhatsAppSimulator` stores to cloud `/v1/whatsapp/outbox` so the board renders a phone mock with incoming alerts and lets the presenter tap "1"; reply flows back to `POST /alerts/{id}/ack`.

---

## 9. SenseBoard information architecture (retailer-judge-first)

**Global chrome**: language toggle (हिंदी/EN/தமிழ்), freshness badge "Data as of 16:42", sync badge ("Online" / "Offline — 1,240 events buffered" with pulsing amber), store switcher, red "Cable kaat do" demo toggle.

1. **/owner — "Aaj ka hisaab" (phone-width, also the default)**
   - Top card: `₹ bachaya aaj` (rupees recovered = restock alerts acted within 10 min x impact) vs `₹ nuksaan` (open gaps). Big numbers, green/red with icons (never colour-only).
   - KPI row (5): Footfall, Conversion, ATV, OSA%, Avg wait — delta vs kal, sparkline.
   - WhatsApp phone mock on the right (desktop) / bottom sheet (mobile) showing live alerts and tap replies.
   - "Kal ka order" list with qty + Rs + "Tally me PO banao" button.
   - Voice summary play button (Hindi TTS).
2. **/ops** — alert feed pinned (4 severities, Acknowledge / Investigate / False-positive inline), queue lane cards (count, wait, 15-min forecast arrow), shelf grid coloured by state with gap timers, live annotated frame tiles (still-when-idle).
3. **/insights** — floorplan heatmap (peak vs off-peak slider, dwell vs traffic), Power Hours matrix (hour x weekday), STAR, bounce rate, **shrink reconciliation table** (visual vs Tally, Rs delta, sorted by Rs).
4. **/chain** — store rank normalised by traffic, peer benchmarks, fleet health (online, fps, model version, backlog), FMCG share-of-shelf by outlet (second customer).
5. **/onboard** — 3-step wizard: paste RTSP / pick webcam / upload video -> draw shelf polygons + tag SKU from Tally item list (autocomplete) -> enrol 5 photos -> WhatsApp number -> "Shuru karo". Timer shows "10:00 min onboarding".

Stack: Vite, React 19, Tailwind, shadcn/ui, Recharts, Zustand, TanStack Query, native WebSocket to edge (`/ws/live`) with cloud fallback; i18next; skeleton loaders; 300 ms number transitions; `prefers-reduced-motion` respected; vitest for components (AlertCard renders Hindi, SyncBadge state machine, KpiTile delta arrows).

---

## 10. Demo orchestration (`python -m retailsense demo` / `make demo`)

`tools/demo.py` starts in order: embedded broker -> SenseCloud (SQLite) -> Tally mock -> ONDC mock -> SenseEdge with `synthetic:evening_rush` camera + `file:` second camera (shipped mp4 generated at first run) -> simulator clock at 20x -> `npm run dev` -> opens browser at `http://localhost:5173/owner`. Flags: `--no-gpu`, `--real-cam rtsp://...`, `--lang hi`.

### 3-minute demo script

| t | Presenter says | On screen |
|---|---|---|
| 0:00 | "Ramesh-ji's kirana already has a DVR. Zero new hardware." | /onboard: paste RTSP, draw Parle-G shelf polygon, tag from Tally list, 5 photos enrolled. Timer 0:45. |
| 0:30 | "Now evening rush." | Click scenario `evening_rush`. Synthetic video with tracked shoppers; queue lane hits 5; wait 4.2 min. |
| 0:50 | "Forecast says 7 in 15 minutes. WhatsApp, not dashboard." | Phone mock: Hindi alert "Counter par line 15 min me 7 hogi — dusra counter kholo". Presenter taps 1. Alert acked on board. |
| 1:10 | "Parle-G goes empty." | Scenario `stockout_parleg`; 3 scans -> shelf turns red with timer; Hindi alert with "₹340 nuksaan"; ONDC badge flips to unavailable. |
| 1:35 | "Pull the cable." | Click "Cable kaat do". Badge: Offline, buffering 0 -> 312 events. Alerts still appear on LAN phone. |
| 2:00 | "Reconnect — nothing lost, in order." | Restore; counter drains to 0; cloud chart backfills; test badge "312/312 replayed, seq ordered". |
| 2:20 | "Tally says 48 packets; camera sees 41. That's ₹ shrink." | /insights shrink table; Rs 350 delta row highlighted. |
| 2:40 | "Numbers: MAE 0.7 customers, 18 ms/frame on GPU / 90 ms CPU, Rs 299/month, 12M stores." | /owner big card "₹ bachaya aaj: 2,140" and BMC slide. |

---

## 11. Innovation list (defensible, ranked for retailer judge)

1. Rupee-quantified alerts (`impact_inr` with cited basis) — every event has a price.
2. WhatsApp-native 1-tap ops loop (alert -> reply digit -> ack -> Tally PO) in Hindi/Tamil/Telugu; voice daily summary.
3. Zero-hardware onboarding from existing DVR RTSP or old Android phone in 10 minutes.
4. 3-scan persistence filter + Shelf Gap Duration as first-class KPI.
5. Few-shot SKU enrolment (5 photos, CLIP embeddings, no retraining) for the Indian long tail.
6. Visual-vs-Tally reconciliation exposing phantom inventory and shrink in rupees.
7. 15-min queue forecast with live MAE, festival + salary-week features.
8. True offline-first with live cable-cut replay proof and ordered `seq`.
9. ONDC-native availability publishing (Beckn, Ed25519).
10. Privacy by design: no faces, no video persisted, 96 px shelf-only thumbnails, DPDP retention.
11. Second paying customer: FMCG share-of-shelf tier.
12. Synthetic store simulator + video generator as a test oracle (also a sales demo tool).

---

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Camera angle/lighting on real DVRs | Per-shelf calibrated "full" reference + classical fallback; confidence shown on board |
| Weights fail to download on stage | Weights vendored; `--detector=synthetic` mode proves pipeline logic |
| GPU/driver issues on judge laptop | onnxruntime CPU path tested at 2 fps; all scenarios run CPU-only |
| Shopkeeper ignores dashboard | WhatsApp + voice are primary; board optional |
| False alerts erode trust | "3 = galat alert" feedback lowers per-shelf sensitivity automatically |
| Power cut mid-write | WAL + synchronous=FULL; test kills process mid-batch |
| WhatsApp Cloud API approval latency | Simulator on stage; Telegram adapter as fallback with same interface |
| Tally not running on judge machine | `tally_mock_server.py` with realistic XML |
| 14 agents colliding | Frozen contracts package, per-package ownership, integration tests only in `/tests` |
| Velocity numbers (impact) disputed | Basis string in every alert; derived from Tally sales history when connected |
