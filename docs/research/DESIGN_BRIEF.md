# DESIGN BRIEF: Edge-AI Retail Intelligence Platform for SIH

## 1. What SIH judges reward (ranked, with evidence)

1. **A fully integrated end-to-end demo.** Integration failure is the single most-cited cause of losing (Sarika Purohit, Arinjay Pathak: "Integration is the key"). Final round carries ~50% weight and is scored on "functionality, final demonstration, user experience, market readiness" (Team DORA weightages 20/30/50). Judges reward prototype output "very close to the PS requirements" (Dr Topale, WeSchool).
2. **Visible implementation of mentoring-round feedback.** Jurors follow the same teams across R1-R3 and check whether asks were built (RadarVision: R1 "not our best" -> R2 "much better" -> won; Palak: R2 judge took a photo after R1 asks were done).
3. **Professional UI/UX.** Jhanvi's finalist team was explicitly told UI was "too basic"; "the idea is what matters more" was a "bitter realization". Official rubric lists "user experience" as a criterion.
4. **Business framing beyond government.** BMC, revenue model, TAM, cost and dev-time estimates earn "brownie points" (DORA); HUL judges said "focus less on the tech"; GfG finalist judges asked about "estimated costs and development time".
5. **Real end-user research and Indian context.** PM's 2024 address asked teams whether they met real users; winners consulted lawyers, contractors, border residents.
6. **Low-cost, offline/edge, scalable, reuses existing infrastructure.** PM praised "completely offline" and "compact, cost-effective, highly scalable" solutions; SIH1349 (existing railway CCTV) and SIH1605 winners reused installed cameras.
7. **Going beyond the PS minimum.** PM pushed drone team for range/direction/speed; DORA added air-quality; Arinjay advises integrating adjacent PS features as novelty.
8. **Defensible novelty.** Plagiarism is actively probed (Sarika's team accused of copying the frontend, saved by Figma originals). Rubric: "novelty, complexity, clarity in prescribed format, feasibility, sustainability, scale of impact, UX, future work".
9. **Measurable numbers.** Accuracy %, latency, MAE, footfall forecasts, TCO -- "numbers not adjectives".
10. **Pitch discipline.** One presenter, every member defends a module, team visibly working all night, a compact 3-min narrative.

Precedent winners for this track: SIH1358 (e-commerce image QC), SIH1349 (CCTV crowd/work monitoring), SIH1752 (India Post counter-service time analytics), SIH25165 (temple footfall: CatBoost+LightGBM forecaster + YOLOv8 panic detection), SIH25182 (PPE compliance). Competition density matters: CV/PSU problem statements drew 2-51 submissions vs 300-500 for generic ones.

## 2. Official idea-template slide structure (SIH2025 format, 6 slides, PDF only, pointers must not be changed)

| Slide | Official heading | What to put |
|---|---|---|
| 1 | TITLE PAGE | PS ID, PS title, Theme, Category (Software), Team ID, registered team name. Footer on every slide: slide no., "@SIH Idea submission- Template", team name. |
| 2 | IDEA TITLE / Proposed Solution | One-line core concept ("Offline-first edge-AI that turns any existing CCTV into shelf, queue and footfall intelligence for Indian retail"); 3-4 solid features, each linked to the problem it solves; explicit "Innovation and uniqueness" block (see Section 8). No paragraphs. |
| 3 | TECHNICAL APPROACH | Layered stack table (Edge / Uplink / Cloud / Dashboard / Integrations) with exact tool names; a single step-wise flow "Camera -> Edge inference -> SQLite outbox -> MQTT -> Cloud -> Dashboard/Alerts -> Tally/ONDC" with one concrete example (e.g., "Parle-G facing empty at 4:12 pm -> WhatsApp alert -> restocked 4:20"). Label visuals "mockup / prototype in progress". |
| 4 | FEASIBILITY AND VIABILITY | Split Technical / Financial / Market / Operational. Honest risks: camera angle and lighting variance, power cuts, flaky 4G, SKU long-tail, privacy, shopkeeper adoption. One mitigation each (edge buffering, UPS + WAL, few-shot CLIP enrolment, no face recognition, WhatsApp-first UX). |
| 5 | IMPACT AND BENEFITS | Target audiences named (kirana owner, chain ops manager, FMCG distributor, consumer). Quantified: OOS 8.3% -> target <4%, shrink 4-8% -> 2%, queue wait 5-8 min -> <3 min. Social/Economic/Environmental + SDG 8/9/12, Digital India, ONDC alignment. Include honest negatives (cost, adoption). |
| 6 | RESEARCH AND REFERENCES | 8-12 links: SKU-110K paper, Sensors 2024 OOS paper, Cali Intelligence case, Kroger QueVision, IAMAI 2025, LocalCircles power survey, ONDC specs, Tally XML docs. |

Portal fields: idea title, idea description, PDF. Scored criterion "clarity and details in the prescribed format" -- do not alter headings. Answer What/Why/When/Where/Who/How across the deck; keep <= 6 bullets per slide; plan a 3-minute pitch.

## 3. Recommended edge-AI stack

**Models**
- Person detection + tracking: YOLO11n/YOLO26n (Ultralytics) + ByteTrack; `solutions.QueueManager`, `Heatmap`, region counting give queue length, dwell, footfall and heatmaps out of the box. Density-map fallback (CSRNet) only for very congested aisles.
- Shelf gap detection: YOLO11n/RF-DETR-S fine-tuned on Roboflow empty-shelf sets (1,013 + 497 + 1,050 images) plus SKU-110K pretraining for product boxes. Two-class "fully empty / front empty" per the Sensors 2024 paper (mAP 85%, 7-17 ms/img), plus OOS-mirroring augmentation. Coverage rule: bbox-area ratio -> Stocked / Partial (<20%) / Empty (>=20%).
- Product recognition (optional, few-shot): CLIP ViT-B/32 or DINOv2 embeddings + FAISS k-NN (89-92% on MIMEX with 50 samples/class). New SKUs enrolled by photographing 5-10 images -- no retraining.
- Forecasting: LightGBM/CatBoost on lag + calendar + festival features (the SIH25165 winner's pattern), Little's Law for instantaneous wait (W = L/lambda).

**Runtime:** ONNX Runtime / OpenVINO on CPU, TensorRT on Jetson, Hailo on RPi5 AI HAT. Export YOLO to ONNX/TensorRT INT8; frames pulled via go2rtc/RTSP; process 2-5 fps per camera (shelf checks need once per minute, queues need ~2 fps).

**Hardware tiers and expected throughput**
| Tier | Device | Cost | Cameras | Expected FPS (YOLO11n 640) |
|---|---|---|---|---|
| Kirana | Raspberry Pi 5 8GB (+Hailo-8L HAT) | Rs 8-15k | 1-2 | 4-8 fps CPU, 25-30 fps with Hailo |
| Mini-supermarket | Jetson Orin Nano Super | $249 (~Rs 22k) | 4-8 | 60+ fps aggregate INT8 |
| Chain store | Intel NUC/i5 with OpenVINO (iGPU) | Rs 35-50k | 8-16 | 30-60 fps aggregate |
| Zero-hardware | Existing DVR + shopkeeper Android phone | Rs 0 | 1 | 2-5 fps (NCNN/TFLite) |

Justification: reuses installed CCTV (the pattern judges rewarded in SIH1349/1605), BOM below a kirana's Rs 50k-1.5 lakh tech-setup budget, mirrors the stack judges already recognise (YOLO+ONNX, FastAPI), while the few-shot CLIP enrolment and INT8 edge runtime deliver differentiation.

## 4. Shelf/inventory approach and queue-prediction approach

**Shelf:** (1) Calibrate each camera once: shopkeeper draws shelf polygons on a phone and tags them to SKU/category. (2) Every 60 s run gap detector; compute per-polygon coverage and facing count. (3) Persistence filter: a gap must persist across 3 consecutive scans (3 min) before it is a "stockout" -- separates replenishment-in-progress from true OOS (Focal/Trax practice). (4) Derive Shelf Gap Duration, OSA% (target 95-98%), share-of-shelf, and simple planogram rules (brand blocking, minimum facings) without a formal planogram. (5) Optional Deep Hough row detection to drop false gaps above/below shelf edges. (6) Cross-check against POS/Tally stock: system stock > visual stock flags phantom inventory and shrink (2025 grocery audit study: 11% sales lift).

**Queue:** (1) Polygon ROI per counter; count track IDs inside; dwell = frames in zone / fps. (2) Abandonment = tracks that enter the queue zone and exit without crossing the counter line. (3) Short-horizon forecast (5/10/15/30 min, Kroger QueVision horizons): gradient-boosting on queue length lags, entrance footfall, hour-of-day, day-of-week, festival flag; target MAE <1 customer (Cali Intelligence achieved 0.8). (4) Alert rule "One in Front": if predicted queue > N for > T seconds, WhatsApp/Telegram the owner to open a counter. (5) Daily footfall forecaster (LightGBM) for staffing and ordering.

## 5. Offline-first edge-to-cloud architecture and fleet management

**Edge node (source of truth):** Dockerised services under balenaOS (first 10 devices free, delta updates) or AWS Greengrass v2. SQLite in WAL mode with `synchronous=FULL` for events (85% of Indian households see daily outages); inference results and alerts written with a transactional outbox row in the same commit. Local Mosquitto broker; the owner's phone app and a local dashboard work on LAN with zero internet.

**Uplink:** MQTT 5 QoS1 with persistent session and Message Expiry (telemetry expires in hours, sales/alerts never); client-side disk queue; Greengrass Stream Manager File persistence with `OverwriteOldestData` for metrics and `RejectNewData` + high export priority for transactions. Only compressed events and thumbnails leave the store -- no raw video -- keeping bandwidth under ~1 kbit/s idle and protecting privacy.

**Cloud:** FastAPI ingest -> Kafka/Redis Streams -> idempotent consumers deduping on `event_id` -> Postgres + TimescaleDB; LWW/CRDT for catalog and shelf-map metadata, append-only HLC-stamped event ledger for stock with authoritative recompute; WebSocket fan-out to dashboards.

**Fleet:** OTA model and app updates via IoT Jobs/balena releases with canary -> 10% -> 50% -> 100%, abort at 5% failures, ROLLBACK policy, A/B partitions on Jetson (Mender); release pinning per store; federated fine-tuning (local model + "mother model") explicitly praised by the PM.

**Monitoring:** Each edge exposes Prometheus metrics (camera fps, inference ms, drop rate, temperature, queue depth, outbox backlog) remote-written to Mimir; Alertmanager `absent(up{store})` heartbeat alerts because cloud consoles show stale "healthy" status while a device is offline.

**Integrations:** Tally XML over HTTP port 9000 (stock journals, ~3M businesses), Zoho Inventory `/inventoryadjustments` (100 req/min), SAP B1 Service Layer OData for chains, GoFrugal via partner API, ONDC seller catalog with real-time stock updates via Ed25519-signed Beckn callbacks. Vyapar via Tally-XML export.

## 6. Dashboard KPIs and UX patterns

**Headline KPI row (max 5):** Footfall, Conversion (transactions/visitors), ATV, OSA%, Avg queue wait -- each with delta arrow vs yesterday/last week and sparkline; "Data as of 10:42" freshness badge plus sync status (online / buffering 1,240 events).

**Operations view:** live camera tiles (go2rtc WebRTC, still-when-idle), alert feed pinned at top with 4 severities and inline Acknowledge / Investigate / False-positive; queue lane cards (count, wait, forecast 15 min); shelf grid coloured by OSA with gap-duration timers.

**Insights view:** floorplan heatmap (simpleheat/deck.gl over uploaded plan, time-sliced peak vs off-peak, dwell vs traffic toggle), hour x weekday "Power Hours" matrix, STAR (shoppers per staff), bounce/capture rate, shrink reconciliation table (visual vs system stock).

**Chain/district view:** store comparison normalised by traffic, rank and peer benchmarks (RetailNext pattern), fleet health (online, inference fps, model version).

**Mobile/WhatsApp surface for kiranas:** Hindi/regional language, daily PDF/voice summary, one-tap "order from distributor" for flagged SKUs.

**Stack:** Vite + React 19 + Tailwind + shadcn/ui, Recharts for KPI charts, ECharts/ApexCharts for high-tick streams, Zustand + TanStack Query, MQTT.js over WebSocket or FastAPI WebSockets, skeleton loaders, 200-400 ms value transitions, never colour-only encoding, respects prefers-reduced-motion.

## 7. Indian retail context numbers to cite

- 12-13 million kirana stores; 75-78% of consumer-goods sales, >90% of FMCG; ~10% of GDP; retail $1.06T (2024) -> $1.93T (2030) (Invest India, Deloitte-FICCI 2025).
- Typical kirana: 100-500 sq ft, 1,500-3,000 SKUs, Rs 2-5 lakh/month revenue, 5-20% gross margin; tech setup budget Rs 50k-1.5 lakh; only 22% plan tech investment but 45% want inventory tracking (CPM Kirana 2025, n=4,593).
- 958M active internet users, rural 548M growing 4x faster; 95%+ villages with 3G/4G; but 38% of population still offline (IAMAI-Kantar 2025).
- 85% of households face at least one power outage daily; 22% face 3-5 (LocalCircles 2023, 272 districts).
- Shrinkage: India topped the Global Retail Theft Barometer (2.4-3.2% of sales vs ~1.3-1.5% global); small chains lose 4-8%/yr; a Rs 3 crore chain loses Rs 12-24 lakh/yr.
- Stock-outs: global OOS 8.3%; 31% of shoppers buy elsewhere, 9% abandon; OOS costs ~4% of sales; a Rs 1 crore store loses Rs 4-8 lakh/yr to OOS + shrink.
- Queues: 32% abandon after long lines, tolerance ~5-8 min; queue tech cuts perceived wait 30-35%; Kroger cut waits from 4 min to <30 s.
- ONDC: 500M cumulative transactions (Jul 2026), 350k+ sellers, Rs 277 crore MSME onboarding budget.

## 8. Innovation differentiators

1. **Zero-new-hardware onboarding**: ingest RTSP from existing DVR/NVR or an old Android phone as camera; hardware tiers are optional upgrades.
2. **Persistence-filtered gap detection** (3-scan rule) plus Shelf Gap Duration as a first-class KPI, not just "empty/not empty".
3. **Few-shot SKU enrolment via CLIP/DINOv2 + FAISS**: shopkeeper photographs 5 images, product is recognised immediately -- solves the Indian long-tail SKU problem without retraining.
4. **Visual-vs-system stock reconciliation** against Tally/Zoho exposing phantom inventory and shrink, quantified in rupees.
5. **Queue build-up forecast 15 min ahead** with "One in Front" style WhatsApp alerts, evaluated on MAE.
6. **True offline-first**: SQLite WAL + outbox + MQTT expiry; demonstrate by pulling the Ethernet cable live on stage and reconnecting -- events replay in order.
7. **Privacy by design**: no face recognition, no raw video leaves the store, body-shape tracking only, DPDP-ready retention policy.
8. **Federated fine-tuning across stores** (local model + mother model) with canary OTA rollouts and rollback -- the fleet story most student teams lack.
9. **Festival-aware forecasting** with an Indian calendar (Diwali, Eid, Pongal, local melas) as features.
10. **ONDC-native stock publishing**: detected availability auto-updates the seller catalog, linking the PS to a national mission.
11. **Vernacular voice/WhatsApp summaries** for owners who do not use dashboards -- inclusivity point judges probe.
12. **Distributor/FMCG dashboard tier** as a second paying customer (share-of-shelf, OSA by outlet) -- TAM beyond government and beyond the store.

Pair these with a Business Model Canvas: SaaS Rs 299-999/store/month, hardware kit margin, FMCG data subscriptions; cost and dev-time estimates on slide 4.

## 9. Key references

- SIH results and guidelines: https://www.sih.gov.in/sih2025/sih2025-grand-finale-result ; https://www.sih.gov.in/letters/SIH2025-IDEA-Presentation-Format.pptx ; https://www.sih.gov.in/letters/SIH2025-Guidelines-College-SPOC.pdf ; https://sih-nav.vercel.app/
- Winner accounts: https://dev.to/heisdinesh/smart-india-hackathon-2023-winners-471 ; https://medium.com/@acchethan15/how-we-won-smart-india-hackathon-2024-a-story-of-teamwork-innovation-afbe74e8e20c ; https://medium.com/@jhanvim77/smart-india-hackathon-2023-experience-ff02b5992c65 ; https://www.lets-code.co.in/blogs/sih-2025-complete-guide-ppt-template/
- Precedent repo: https://github.com/jinay-k-jain/sih_2025_project_SIH25165
- Shelf CV: https://docs.ultralytics.com/datasets/detect/sku-110k ; https://pmc.ncbi.nlm.nih.gov/articles/PMC10819825/ ; https://blog.roboflow.com/retail-store-object-detection/ ; https://github.com/rokopi-byte/shelf_management ; https://arxiv.org/html/2409.14963v1 ; https://arxiv.org/html/2312.10282
- Queue: https://docs.ultralytics.com/guides/queue-management ; https://www.ultralytics.com/customers/cali-intelligence-cuts-retail-checkout-queues-with-ultralytics-yolo ; https://aiinstitute.hbs.edu/platform-rctom/submission/kroger-sensors-coming-to-aisle-near-you/ ; https://ieeexplore.ieee.org/document/10955915/
- Architecture: https://www.sqlite.org/wal.html ; https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html ; https://www.hivemq.com/blog/mqtt5-essentials-part4-session-and-message-expiry/ ; https://docs.aws.amazon.com/greengrass/v2/developerguide/manage-data-streams.html ; https://docs.balena.io/learn/deploy/delta/ ; https://mender.io/blog/ota-updates-for-nvidia-jetson
- Integrations: https://help.tallysolutions.com/xml-integration/ ; https://www.zoho.com/inventory/api/v1/itemadjustments/ ; https://github.com/ONDC-Official/developer-docs/blob/main/registry/signing-verification.md
- Dashboards: https://retailnext.net/resources/brief/brief-performance-dashboard ; https://docs.frigate.video/configuration/metrics/ ; https://www.smashingmagazine.com/2025/09/ux-strategies-real-time-dashboards/ ; https://github.com/vahapogut/Theft-Detection ; https://github.com/AlexxIT/go2rtc
- India context: https://mediabrief.com/kirana-2025-report-indias-local-retailers-changing-market/ ; https://www.investindia.gov.in/team-india-blogs/modernization-kirana-stores-india ; https://bestmediainfo.com/insights/indias-internet-users-near-one-billion-in-2025-rural-india-leads-growth-iamai-11056899 ; https://www.localcircles.com/a/press/page/power-outage-survey ; https://retailpos.co.in/retail-inventory-shrinkage-india-solution/ ; https://www.nacds.org/pdfs/membership/out_of_stock.pdf ; https://worldef.com/2026/08/07/ondc-crosses-500-million-transactions-india/