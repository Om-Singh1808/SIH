# RetailSense — 3-Minute Stage Script and Judge Q&A

One presenter drives; one team member at the laptop presses the DemoControls buttons; every other member owns one module for Q&A (see §3). The board is on the projector at `/ops` with the PhonePanel docked; the phone mirror shows `/owner`. Sim clock runs at ×10 so "minutes" pass in seconds.

---

## 1. Stage script (≈ 3:00)

| t | Presenter says (say it exactly; it is timed) | Operator presses | Audience sees on screen |
|---|---|---|---|
| **0:00** | "Ramesh-ji runs a general store in Delhi. He already has a DVR with two cameras. We install nothing. We point RetailSense at his RTSP stream, draw the shelves and the counter on the live picture, and press calibrate." | `/zones` → Save (`PUT /config/zones`), then **Calibrate all** (`POST /calibrate/shelves/reference-all`) | Live preview with zone polygons, entrance and counter lines with IN arrows; shelf grid turns green with "has_reference" on all three shelves |
| **0:20** | "Every person is a box with a number. No faces, no video stored. Numbers only leave the shop." | – | Preview shows pixelated people with track ids; SyncBadge "Online" |
| **0:30** | "It's 5 pm — evening rush." | **Evening rush** (`POST /demo/scenario {evening_rush}`) | Queue lane climbs 5 → 6; wait ≈ 4 min by Little's Law on the *measured* service rate |
| **0:40** | "Six in line for over a minute. Ramesh-ji gets this on WhatsApp — in Hindi, with the rupee at risk: ₹173. He taps 1: counter खोल दिया." | Wait for alert; tap **1** on PhonePanel | `queue_long` alert card; PhonePanel bubble "मेन काउंटर पर 6 ग्राहक लाइन में (~4 मिनट)। जोखिम ₹173…"; after tap: alert shows "acked · opened_counter" |
| **0:55** | "The model already knew: it forecast 7 people in 15 minutes, and it shows its own error — MAE 0.8 customers." | – | QueueLane forecast arrow ↑7 with "MAE 0.8 · cloud" badge; `queue_forecast` alert shows "superseded" |
| **1:10** | "Now the Amul milk runs out. Watch the scan counter — one empty scan is not a stock-out; three in a row is." | **Stockout Amul** (`POST /demo/scenario {stockout, shelf_id: shelf-A}`) | Shelf-A tile: coverage bar drops, counter 1/3 → 2/3 → 3/3, tile turns red with a gap timer |
| **1:25** | "Alert: 'अमूल ताज़ा की शेल्फ 3 मिनट से खाली'. Rupees, with the formula: ₹27 × 18 an hour × 0.31 — the share of shoppers who walk out. And ONDC already shows the item unavailable." | Hover the ₹ tooltip; open ONDC log tab | Alert card with basis string; `/mock/ondc/log` row `available=false` for `I-AMUL-500` |
| **1:35** | "Tier-2 internet. Cable kaat do." | **Cable kaat do** (`POST /demo/link {down}`) | SyncBadge flips to "Offline · backlog 37… 120… 250"; alerts keep appearing on the LAN PhonePanel with "WhatsApp pending" |
| **1:50** | "Nothing stops. The shop keeps working on Wi-Fi; every event is in SQLite with an outbox row in the same transaction. The cloud notices honestly: device offline." | Show `/chain` fleet tab | Fleet table: EDGE-001 "offline" after 60 s; other two stores online |
| **2:00** | "Reconnect." | **Reconnect** (`POST /demo/link {up}`) | SyncBadge "replaying 180/312 …" then "**312/312 replayed · seq ordered**"; cloud chart backfills; PhonePanel "pending" → "delivered via WhatsApp 17:xx" |
| **2:15** | "Ramesh-ji restocks and taps 1 — भर दिया. The camera confirms. Gap: 8 minutes instead of the two hours it would have taken to notice. ₹281 saved — and the board says so." | Tap **1** on the shelf alert; **Restock** (`/demo/restock/shelf-A`) | Shelf-A green; alert "resolved · restocked_observed · gap 8 min"; `/owner` "₹ बचाया आज" increments |
| **2:30** | "One more. Tally says 48 packs of Amul. The camera sees 41. That gap is ₹189 — shrink, found today, not at the quarterly audit." | **Reconcile now** (`POST …/integrations/tally/reconcile`) | ShrinkTable row: Amul 48 vs 41, Δ7, ₹189, flagged; `shrink_suspect` alert |
| **2:45** | "Numbers: forecast MAE 0.8 customers. Three-scan filter. Under one kilobit per second uplink. ₹299 a month. Twelve million stores. Every number you saw is produced by the code, and every beat you saw is an automated test." | `/owner` | "₹ बचाया आज" card, KPI row, BMC slide |
| **3:00** | "RetailSense. Questions?" | – | – |

**Contingencies (rehearsed):**
- Board does not load → operator runs the curl lines from `docs/DEMO_SCRIPT.md`; presenter narrates from the JSON.
- Cloud process dies → continue; the SyncBadge shows "cloud unreachable" and the edge keeps alerting — say "this is the offline story happening for real".
- Projector resolution too low → phone mirror only (`/owner` is phone-first by design).

---

## 2. Judge Q&A — 15 likely questions, crisp answers

**1. What accuracy do you actually get?**
Three numbers, each measured in CI against the simulator's ground truth (`sim.truth`): footfall within ±10 % of true entrance crossings (`test_e2e_synthetic`); shelf coverage within ±0.1 of true facings/capacity, exactly one `shelf_gap` transition after 3 empty scans and none after 2; queue forecast holdout MAE ≤ 1.0 customer against a naive baseline (target 0.8, the Cali Intelligence benchmark). On real video the person detector is YOLO11n (39.5 mAP COCO; Intel's retail person detector handles 50 % occlusion at 88.6 % AP) — we will report pilot precision/recall per shelf after 30 days; we do not claim a real-shelf number we have not measured.

**2. Why not do everything in the cloud?**
Three reasons with numbers: 85 % of Indian households see a daily power outage and 38 % of the population is offline (LocalCircles 2023, IAMAI 2025) — a cloud-only system is down exactly when a kirana is busiest; streaming video from 12 M stores is neither affordable nor private; and at the edge inference costs electricity, not GPU-hours, which is what makes ₹299/month possible. The cloud adds chain views, better forecasts and WhatsApp — all optional.

**3. What does it cost a shopkeeper?**
₹0 hardware if a DVR or an old Android phone exists; otherwise a Pi 5 kit at ₹8–15k (Jetson ≈ ₹22k for mini-supermarkets). Subscription ₹299/month. A ₹1 crore/yr store loses ₹4–8 lakh/yr to OOS + shrink; recovering 10 % covers the subscription 11–22 times over.

**4. How is this original? Ultralytics already ships queue management and heatmaps.**
The primitives (detector, tracker, polygon counting) are commodity and we say so. Our contribution is the layer above: rupee impact with a cited basis on every alert; the 3-scan persistence filter with occlusion skip and per-shelf self-tuning from "3 = galat alert"; Little's Law on the measured *service* rate with abandonment; the transactional outbox + seq-ordered idempotent replay proven live; visual-vs-Tally shrink in rupees; an agent-based synthetic store as a test oracle; and the whole thing in Hindi on WhatsApp. Every line of the repo was written during the hackathon against a frozen contracts package; the git history and the 14 disjoint work packages are the evidence.

**5. What happens when the camera angle changes or lighting shifts?**
Shelf coverage is normalised against a per-shelf calibration reference (backing colour + full-shelf coverage) captured on the live frame; if the camera is bumped, the owner taps "calibrate" again — one click, no retraining. Lighting drift is handled by comparing Lab colour distance *and* local texture variance, not absolute brightness. People-counting lines and zones are in image coordinates with an optional homography to the floorplan, so a re-mount means redrawing polygons in the zone editor, not touching code.

**6. Indian stores have 1,500–3,000 SKUs. How do you handle the long tail?**
Today we do not recognise SKUs — we tag a shelf polygon to a SKU or a category and measure *coverage and facings*, which needs no product model and is what "is the milk shelf empty" requires. For SKU identification the `SkuIdentifier` protocol is already in place with a few-shot CLIP/DINOv2 + k-NN backend: the owner photographs 5–10 images, the product is recognised immediately without retraining (89–92 % on MIMEX with 50 samples/class in the literature). That is month 4–5 on the roadmap.

**7. What is the forecast MAE and how do you know?**
The cloud forecaster is gradient boosting (sklearn HistGradientBoosting by default, LightGBM when installed) with one model per horizon (5/10/15/30 min) on queue lags, entrance footfall, hour, weekday, festival flag/weight, days-to-festival and salary week. It holds out the last 3 days and reports `mae_holdout` next to a naive-persistence baseline; the acceptance test requires MAE ≤ 1.0 and beating the baseline. Live, every prediction is stored and scored when the actual count arrives, and the rolling MAE is the badge on the queue lane. The edge has its own trend forecaster so the board shows a forecast even offline.

**8. How does the offline replay guarantee nothing is lost or duplicated?**
Each observation is written to SQLite (WAL, `synchronous=FULL`) together with its outbox row in one `BEGIN IMMEDIATE … COMMIT`, stamped with a per-device gap-free `seq`. The sync worker sends batches in seq order; the cloud inserts with `INSERT OR IGNORE` on the ULID `event_id` and checks the seq chain, returning `seq_ok` and any gaps in the ack. A lost ack means a resend that the cloud reports as duplicates without re-inserting. Alerts and transactions never expire from the outbox; telemetry expires after 1 h and aggregates after 24 h, so a week-long outage fills the disk with nothing you would miss. A crash test kills the process mid-batch and asserts the seq is still gap-free.

**9. Scalability — can this run a 500-store chain?**
The edge scales per store (one process, N camera threads). The cloud ingest is a single idempotent endpoint; the demo runs on SQLite, the compose `pg` profile switches to Postgres + TimescaleDB with no code change, and the ingest contract is unchanged if we put Kafka/Redis Streams in front of it. Fleet management is designed in: a model manifest with sha256, canary → 10 % → 50 % → 100 % rollout, abort at 5 % failures, per-device pinning, and version-drift badges. On stage you saw three devices in the fleet view.

**10. What about privacy and the DPDP Act?**
No face recognition exists in the system — the model manifest contains only a person detector. Tracking is appearance-free (ByteTrack without ReID), so there is no biometric embedding to leak. Track IDs never leave the edge; raw frames are never written to disk; previews pixelate people; shelf thumbnails are 96×96 crops of the shelf polygon and are skipped when someone stands in front. Retention is enforced by an hourly purge job (telemetry 24 h, aggregates 30 d, thumbnails 7 d). `docs/PRIVACY.md` maps each DPDP section and includes the Hindi/English entrance signage.

**11. Why Little's Law on the service rate instead of just measuring waits?**
We do both. The wait estimate uses `L / service_rate` because the service rate — measured from tracks crossing the counter line — is what actually drains a queue; arrival rate only tells you the future, which is the forecaster's job. When fewer than 0.2 served/min have been observed we fall back to the mean of the last five observed waits, then to `count × 45 s`. A 5-second minimum in-zone age discards ID-switch ghosts, and anyone who leaves the queue zone without crossing the counter line is counted as an abandonment — that is the ₹ risk number on the alert.

**12. How did you pick 0.31 and how sensitive is the rupee number to it?**
0.31 is the share of shoppers who buy the item elsewhere on a stock-out in Gruen, Corsten & Bharadwaj 2002 (GMA/FMI, 71k shoppers, 29 countries; 9 % more abandon the trip). It is a config field with the citation stored next to it; the basis string on every alert shows the exact multiplication so the owner can disagree with it. Lost margin uses the SKU margin %, and ATV comes from Tally's sales of the day when connected.

**13. What did real shopkeepers tell you?**
The design is built on the CPM Kirana 2025 survey (n = 4,593): 45 % want inventory tracking but only 22 % plan tech spend and most do not use dashboards. That is why the primary surface is a Hindi WhatsApp message with a one-digit reply, the dashboard is phone-first ("Aaj ka hisaab"), onboarding is drawing on a picture, and hardware is optional. In the pilot we will measure the two things owners asked for: false alerts per day (target < 1 per shelf, with the "3 = galat" loop) and rupees recovered per month.

**14. What breaks first in production, honestly?**
Cluttered, multi-SKU shelves where the classical estimator sees "covered" even when the wrong product fills the gap — that is why a learned gap detector (Sensors 2024 two-class model, Roboflow empty-shelf sets) is behind the same `CoverageEstimator` interface for P1. Second, RTSP streams from ₹2,000 DVRs drop frames; the source layer reconnects every 3 s and the camera-down rule alerts the owner. Third, WhatsApp Cloud API template approvals take days; Telegram is the same `Notifier` interface as a fallback.

**15. How long to a product, and what do you need?**
Pilot-ready in 8–10 weeks with 2 engineers and 1 field lead: real-camera hardening on Pi 5/N100, live WhatsApp Cloud API, Tally live, DPDP signage. A 10-store, 12-week pilot costs about ₹3.75 lakh including hardware for five stores that have no CCTV. Production v1 (fleet GA, Postgres, few-shot SKU, Jetson tier) is six months. Go-to-market rides FMCG distributor reps who already visit every kirana weekly and who are the second paying customer for outlet-level OSA data.

---

## 3. Who answers what

| Topic | Owner |
|---|---|
| CV pipeline, detector/tracker, hardware tiers, FPS | Edge CV member |
| Shelf estimator, persistence filter, OSA, SKU long-tail | Edge shelf member |
| Queue analytics, Little's Law, forecaster, MAE | Queue/forecasting member |
| SQLite/outbox/replay, MQTT vs HTTP, fleet/OTA | Store/uplink member |
| Dashboard, WhatsApp UX, Hindi, privacy signage | Board member |
| Business model, pricing, TAM, pilot cost, go-to-market | Presenter |

Rule for every answer: a number first, then the mechanism, then the test that proves it.
