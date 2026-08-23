# RetailSense — Business Model Canvas, Pricing, Market Sizing, Unit Economics

All market numbers cite `docs/research/DESIGN_BRIEF.md` §7 (Invest India, Deloitte-FICCI 2025, CPM Kirana 2025 n = 4,593, IAMAI-Kantar 2025, LocalCircles 2023, Global Retail Theft Barometer, GMA/FMI). Figures marked **(assumption)** are our planning estimates, not measurements.

---

## 1. Business Model Canvas

| Block | Content |
|---|---|
| **Customer segments** | 1. Kirana / general store owners (12–13 M stores, ₹2–5 lakh/month revenue, 1,500–3,000 SKUs) with an existing CCTV DVR. 2. Mini-supermarkets and regional chains (4–16 cameras, ops manager, Tally/SAP B1). 3. FMCG brands and distributors who want share-of-shelf and on-shelf-availability by outlet. 4. (Indirect) consumers via ONDC availability and shorter queues. |
| **Value propositions** | Rupee-quantified alerts in Hindi on WhatsApp within 3 minutes of a shelf going empty; queue build-up forecast 15 min ahead; shrink exposed in ₹ against Tally; works offline and on existing cameras; no faces, DPDP-ready. For FMCG: outlet-level OSA % and share-of-shelf without field auditors. |
| **Channels** | FMCG distributor sales reps (already visit every kirana weekly); ONDC seller-onboarding partners (₹277 crore MSME onboarding budget); CCTV installers/DVR dealers (kit bundle); WhatsApp referral from the daily summary ("share with another shopkeeper"). |
| **Customer relationships** | Self-serve onboarding on a phone (draw zones, one-tap calibrate); WhatsApp-first support in Hindi; "3 = galat alert" feedback loop; monthly ₹-saved report; distributor rep as the human touchpoint. |
| **Revenue streams** | SaaS per store per month (₹299 / ₹999 / chain); hardware kit margin (optional); FMCG data subscription per brand per territory; integration fees for ERP connectors at chain tier; ONDC transaction-linked add-ons (future). |
| **Key resources** | `retailsense_contracts` + 13 packages (edge CV, rules, store/outbox, cloud, board); synthetic store simulator (test oracle + sales demo); festival calendar + forecasting models; Tally/WhatsApp/ONDC connectors; fleet/OTA manifest. |
| **Key activities** | Edge model tuning per camera tier (ONNX/OpenVINO/TensorRT/Hailo exports), shelf detector fine-tuning on Indian shelves, distributor channel enablement, DPDP compliance operations, fleet monitoring. |
| **Key partners** | FMCG distributors (channel + data buyer); Tally Solutions partner network; ONDC network participants; Raspberry Pi / Jetson distributors; WhatsApp BSPs (Meta Cloud API); balena/Greengrass for fleet OTA; CCTV installers. |
| **Cost structure** | Cloud compute/storage (events only, ~1 kbit/s idle per store), WhatsApp/Telegram message fees, hardware COGS (pass-through), field onboarding, engineering, support; no GPU cloud inference cost because inference is at the edge. |

---

## 2. Pricing tiers

| Tier | Price | Includes | Hardware | Target customer |
|---|---|---|---|---|
| **Kirana** | **₹299 / store / month** (₹3,588/yr) | 1–2 cameras, shelf gap + queue + footfall alerts on WhatsApp (Hindi/English), daily "Aaj ka hisaab", 30-day history, Tally-XML reconcile | ₹0 with existing DVR + phone; optional Pi 5 kit ₹8–15k | 12–13 M kiranas |
| **Mini-supermarket** | **₹999 / store / month** | 4–8 cameras, queue forecast with MAE, heatmap + power hours, reorder suggestions, ONDC availability publish, 90-day history, 2 user logins | Jetson Orin Nano Super ≈ ₹22k or Intel N100 | Mini-supermarkets, large kiranas |
| **Chain** | **per store ₹1,499 / month (assumption)** + onboarding | 8–16 cameras, chain ranking and fleet/OTA console, SAP B1 / GoFrugal connectors, SLA, Postgres/Timescale backend, SSO | NUC i5 / OpenVINO ₹35–50k | Regional chains (10–500 stores) |
| **FMCG data subscription** | **₹20–30 lakh / brand / year (assumption)** per territory | Outlet-level OSA %, share-of-shelf, gap duration by SKU, aggregated and anonymised; opt-in from store owner who receives a ₹ rebate | – | FMCG brands, distributors |

Positioning: a kirana's tech budget is ₹50k–1.5 lakh (CPM Kirana 2025); ₹299/month is below the cost of one lost Amul crate per month. A ₹1 crore/yr store loses ₹4–8 lakh/yr to OOS + shrink (DESIGN_BRIEF §7); recovering 10 % of that (₹40–80k) covers the subscription 11–22×.

---

## 3. TAM / SAM / SOM (software revenue, arithmetic shown)

| Level | Definition | Arithmetic | Result |
|---|---|---|---|
| **TAM** | Every kirana in India on the Kirana tier | 12.5 M stores × ₹299 × 12 months = 12,500,000 × 3,588 | **≈ ₹4,485 crore / year** (excludes mini-supermarkets, chains and FMCG data — upside not counted) |
| **SAM** | Kiranas that plan tech investment (22 %, CPM Kirana 2025) — a proxy for "has or will have CCTV + smartphone" | 12.5 M × 0.22 = 2.75 M stores; 2.75 M × ₹3,588 | **≈ ₹987 crore / year** |
| **SOM (year 3)** | Reachable through 3 distributor partnerships in 5 Tier-2 cities **(assumption)** | 10,000 kirana × ₹3,588 = ₹3.59 crore; 1,000 mini × ₹11,988 = ₹1.20 crore; 5 FMCG data subscriptions × ₹25 lakh = ₹1.25 crore | **≈ ₹6.0 crore ARR** |

Cross-check on demand: 45 % of kiranas want inventory tracking (CPM Kirana 2025) — 5.6 M stores — so SAM is limited by *willingness to pay and CCTV presence*, not by interest.

---

## 4. Unit economics

### Hardware BOM per tier (pass-through; kit sold at BOM + 15 % **(assumption)**)

| Tier | BOM | Notes |
|---|---|---|
| Zero-hardware | ₹0 | Existing DVR RTSP + shopkeeper's old Android phone / laptop; 2–5 fps |
| Kirana kit | ₹8–15k | Raspberry Pi 5 8 GB (+ optional Hailo-8L AI HAT), case, PSU, 64 GB card; 1–2 cameras; UPS ₹3k recommended (85 % of households see daily outages) |
| Mini-supermarket kit | ≈ ₹22k ($249) | Jetson Orin Nano Super; 4–8 cameras |
| Chain kit | ₹35–50k | Intel NUC i5 / OpenVINO iGPU; 8–16 cameras |

### Monthly SaaS gross margin per store **(assumptions)**

| Line | Kirana ₹299 | Mini ₹999 | Chain ₹1,499 |
|---|---|---|---|
| Cloud compute + storage (events only, ≈ 10–50 MB/month/store) | ₹25 | ₹60 | ₹120 |
| WhatsApp/Telegram messages (≈ 10 alerts/day + daily summary ≈ 330 msgs × ₹0.12) | ₹40 | ₹60 | ₹80 |
| Support / fleet ops (amortised) | ₹25 | ₹60 | ₹150 |
| Distributor channel commission (15 % of SaaS) | ₹45 | ₹150 | – (direct sales) |
| **COGS** | **₹135** | **₹330** | **₹350** |
| **Gross margin** | **₹164 (55 %)** | **₹669 (67 %)** | **₹1,149 (77 %)** |

Edge inference costs the customer electricity only (Pi 5 ≈ 5–8 W); there is no per-inference cloud bill, which is what makes ₹299 viable.

### CAC and payback **(assumptions)**

- Distributor-rep onboarding: ₹500 one-time per store (15 minutes of zone drawing + calibration during a routine visit) → payback ≈ 3 months on the Kirana tier.
- Churn control: the daily "₹ बचाया" WhatsApp summary makes the value visible every day; target monthly churn < 3 %.

---

## 5. Go-to-market

1. **Distributor-led (months 1–6):** sign 3 FMCG distributors in one Tier-2 city; reps install RetailSense on routes they already cover; distributor receives the OSA/share-of-shelf feed and a commission; the owner gets a ₹ rebate for opting into anonymised data sharing.
2. **ONDC onboarding partners (months 4–12):** bundle "live availability on ONDC" with seller onboarding; RetailSense publishes `available=false/true` automatically from the shelf state.
3. **CCTV installers (months 6–12):** pre-flash the Pi/N100 image; installer earns kit margin.
4. **Chains (months 9–12):** pilot with one regional chain via the fleet/OTA console and the chain ranking view.
5. **Content loop:** vernacular WhatsApp/voice summaries and "share with a shopkeeper" referral.

---

## 6. 12-month roadmap

| Month | Milestone | Evidence of done |
|---|---|---|
| 0 | SIH MVP: synthetic store + real CV pipeline, WAL/outbox replay, Hindi WhatsApp simulator, Tally mock, ONDC stub, 5-page board | `python -m retailsense demo` + integration tests green |
| 1–2 | Real-camera hardening: YOLO11n ONNX/OpenVINO on Pi 5 and N100, RTSP reconnect, shelf detector fine-tune on 50 Indian shelf photos, Meta WhatsApp Cloud API live | 3 stores running for 30 days; OSA and footfall accuracy report |
| 3 | Pilot of 10 stores in one Tier-2 city with 1 distributor; DPDP signage + retention audit | Pilot report: gap-detection precision/recall, MAE, ₹ recovered |
| 4–5 | Few-shot SKU enrolment (CLIP/DINOv2 + k-NN), Tally live (not mock), reorder → PO | SKU recognition ≥ 89 % on enrolled products (MIMEX benchmark basis) |
| 6 | Fleet console GA: OTA canary/rollback, Prometheus metrics, Postgres/Timescale backend | 100 devices, < 5 % rollout failure abort tested |
| 7–8 | Mini-supermarket tier: Jetson build, multi-camera homography, power-hours and staffing suggestions | 10 mini-supermarkets live |
| 9 | FMCG data product v1: outlet OSA and share-of-shelf API, opt-in + rebate flow | First paid data subscription |
| 10–11 | ONDC signed callbacks (Ed25519), Tamil/Telugu i18n, TTS summaries | ONDC conformance; 4 languages |
| 12 | Chain pilot (≥ 25 stores), SAP B1 / GoFrugal connectors | Chain ranking used in weekly ops review |

---

## 7. Pilot cost and development-time estimate

**Pilot: 10 stores, 12 weeks, one Tier-2 city (assumptions)**

| Item | Cost |
|---|---|
| Hardware: 5 zero-hardware stores (₹0) + 5 Pi 5 kits × ₹12k | ₹60,000 |
| UPS 10 × ₹3,000 | ₹30,000 |
| Cloud (single VM + Postgres) ₹3,000 × 3 months | ₹9,000 |
| WhatsApp Cloud API messages (10 stores × 330 msgs × 3 months × ₹0.12) | ₹1,200 |
| Field onboarding and travel (2 people, 12 weeks) | ₹1,20,000 |
| Contingency (camera mounts, SIMs, spares) | ₹30,000 |
| Signage printing, DPDP notice, legal review | ₹25,000 |
| Engineering stipends for pilot fixes (2 × 12 weeks) | ₹1,00,000 |
| **Total** | **≈ ₹3.75 lakh** |

**Development time**

| Phase | Effort | Team |
|---|---|---|
| SIH MVP (this build) | 36-hour hackathon, 14 parallel work packages against frozen contracts | 6 members |
| Pilot-ready (real cameras, live WhatsApp, Tally live, field onboarding flow) | 8–10 weeks | 2 engineers + 1 field lead |
| Production v1 (fleet GA, Postgres, few-shot SKU, Jetson tier) | 6 months cumulative | 3–4 engineers |

Break-even on the pilot economics: at ₹164 gross margin/store/month, 10 pilot stores return ₹1,640/month — the pilot is a learning investment; the distributor channel at 10,000 stores returns ≈ ₹16.4 lakh/month gross margin before FMCG data revenue.
