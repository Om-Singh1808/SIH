/**
 * SenseCloud (:8000) REST client. The cloud is optional for the board: owner and
 * ops pages work from the edge alone; chain/insights/fleet degrade to an honest
 * "cloud offline" state (see `isOffline` in http.ts) when these calls fail.
 */
import { CLOUD_URL, STORE_ID, request } from "./http";
import type {
  Alert,
  AlertAckRequest,
  ChainRank,
  DailyReport,
  FitReport,
  FleetView,
  FootfallForecast,
  HeatmapResponse,
  IntegrationsStatus,
  KpiRange,
  ModelManifest,
  OrderRequested,
  OutboundMessage,
  QueueForecast,
  QueueView,
  ReconcileReport,
  ReorderSuggestion,
  RolloutRequest,
  Series,
  ShelfStateView,
  StockReconciled,
  Store,
} from "./types";

export type RankMetric = "osa_pct" | "avg_wait_s" | "lost_sales_inr" | "footfall_in" | "conversion_pct";
export const RANK_METRICS: RankMetric[] = ["osa_pct", "avg_wait_s", "lost_sales_inr", "footfall_in", "conversion_pct"];

const s = (id: string) => `/v1/stores/${encodeURIComponent(id)}`;

export const cloud = {
  baseUrl: CLOUD_URL,
  storeId: STORE_ID,

  health: () => request<{ status: string }>(CLOUD_URL, "/health", { timeoutMs: 4000 }),
  stores: () => request<Store[]>(CLOUD_URL, "/v1/stores"),
  store: (id = STORE_ID) => request<Store>(CLOUD_URL, s(id)),

  kpis: (range: "today" | "7d" | "30d" = "7d", id = STORE_ID) => request<KpiRange>(CLOUD_URL, `${s(id)}/kpis`, { query: { range } }),
  series: (metric: string, range: "today" | "7d" = "7d", id = STORE_ID) =>
    request<Series>(CLOUD_URL, `${s(id)}/series`, { query: { metric, range } }),

  alerts: (status = "open", id = STORE_ID) => request<Alert[]>(CLOUD_URL, `${s(id)}/alerts`, { query: { status } }),
  ackAlert: (alertId: string, body: AlertAckRequest) =>
    request<Alert>(CLOUD_URL, `/v1/alerts/${encodeURIComponent(alertId)}/ack`, { method: "POST", body }),

  queues: (id = STORE_ID) => request<QueueView[]>(CLOUD_URL, `${s(id)}/queues`),
  shelves: (id = STORE_ID) => request<ShelfStateView[]>(CLOUD_URL, `${s(id)}/shelves`),
  heatmap: (q: { from_ts?: number; to_ts?: number }, id = STORE_ID) => request<HeatmapResponse>(CLOUD_URL, `${s(id)}/heatmap`, { query: q }),

  forecastQueue: (counterId: string, id = STORE_ID) =>
    request<QueueForecast>(CLOUD_URL, `${s(id)}/forecast/queue`, { query: { counter_id: counterId } }),
  forecastFootfall: (days = 7, id = STORE_ID) => request<FootfallForecast>(CLOUD_URL, `${s(id)}/forecast/footfall`, { query: { days } }),
  forecastEval: (id = STORE_ID) => request<FitReport[]>(CLOUD_URL, `${s(id)}/forecast/eval`),

  reorder: (id = STORE_ID) => request<ReorderSuggestion[]>(CLOUD_URL, `${s(id)}/reorder`),
  postOrder: (body: OrderRequested, id = STORE_ID) => request<{ po_id: string }>(CLOUD_URL, `${s(id)}/orders`, { method: "POST", body }),
  dailyReport: (q: { date?: string; lang?: "hi" | "en" }, id = STORE_ID) =>
    request<DailyReport>(CLOUD_URL, `${s(id)}/reports/daily`, { query: { ...q, format: "json" } }),

  recon: (id = STORE_ID) => request<StockReconciled[]>(CLOUD_URL, `${s(id)}/recon`),
  reconcileNow: (id = STORE_ID) => request<ReconcileReport>(CLOUD_URL, `${s(id)}/integrations/tally/reconcile`, { method: "POST", timeoutMs: 15000 }),
  integrationsStatus: (id = STORE_ID) => request<IntegrationsStatus>(CLOUD_URL, `${s(id)}/integrations/status`),

  chainRank: (metric: RankMetric, date?: string) => request<ChainRank>(CLOUD_URL, "/v1/chain/rank", { query: { metric, date } }),
  fleet: () => request<FleetView>(CLOUD_URL, "/v1/fleet"),
  manifest: (deviceId?: string) => request<ModelManifest>(CLOUD_URL, "/v1/fleet/manifest", { query: { device_id: deviceId } }),
  rollout: (body: RolloutRequest) => request<unknown>(CLOUD_URL, "/v1/fleet/rollout", { method: "POST", body }),

  whatsappOutbox: (id = STORE_ID, limit = 50) => request<OutboundMessage[]>(CLOUD_URL, "/v1/whatsapp/outbox", { query: { store_id: id, limit } }),
  notifications: (id = STORE_ID) => request<OutboundMessage[]>(CLOUD_URL, "/v1/notifications", { query: { store_id: id } }),
  ondcLog: () => request<unknown[]>(CLOUD_URL, "/mock/ondc/log"),
};
