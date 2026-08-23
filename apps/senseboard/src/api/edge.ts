/**
 * SenseEdge (:8001) REST client — the LAN device that keeps working with zero
 * internet. Every function maps 1:1 to a row of contracts §C.16 and returns the
 * contract model type. URL helpers for images (floorplan, previews, thumbs) are
 * exported so `<img>` tags can point straight at the device.
 */
import { EDGE_URL, request } from "./http";
import type {
  Alert,
  AlertAckRequest,
  ChaosRequest,
  DailySummary,
  HealthStatus,
  HeatmapResponse,
  KpiToday,
  LinkState,
  ModelStatus,
  QueueView,
  ScenarioRequest,
  ScenarioStatus,
  Series,
  ShelfReference,
  ShelfStateView,
  ShelvesUpdate,
  SimTruth,
  StoreConfig,
  SyncStatus,
  WhatsAppReply,
  ZonesUpdate,
} from "./types";

export type SeriesMetric = "queue_count" | "est_wait_s" | "footfall_in" | "occupancy" | "osa_pct";

export const edge = {
  baseUrl: EDGE_URL,

  health: () => request<HealthStatus>(EDGE_URL, "/health", { timeoutMs: 4000 }),
  config: () => request<StoreConfig>(EDGE_URL, "/config"),
  putZones: (body: ZonesUpdate) => request<StoreConfig>(EDGE_URL, "/config/zones", { method: "PUT", body }),
  putShelves: (body: ShelvesUpdate) => request<StoreConfig>(EDGE_URL, "/config/shelves", { method: "PUT", body }),

  kpisToday: () => request<KpiToday>(EDGE_URL, "/kpis/today"),
  series: (metric: SeriesMetric, minutes = 60) => request<Series>(EDGE_URL, "/kpis/series", { query: { metric, minutes } }),

  alerts: (status: "open" | "acked" | "resolved" | "all" = "all", limit = 100) =>
    request<Alert[]>(EDGE_URL, "/alerts", { query: { status, limit } }),
  ackAlert: (alertId: string, body: AlertAckRequest) =>
    request<Alert>(EDGE_URL, `/alerts/${encodeURIComponent(alertId)}/ack`, { method: "POST", body }),

  queues: () => request<QueueView[]>(EDGE_URL, "/queues"),
  shelves: () => request<ShelfStateView[]>(EDGE_URL, "/shelves"),
  shelfThumbUrl: (shelfId: string) => `${EDGE_URL}/shelves/${encodeURIComponent(shelfId)}/thumb.jpg`,

  heatmap: (q: { camera_id?: string; from_ts?: number; to_ts?: number }) =>
    request<HeatmapResponse>(EDGE_URL, "/heatmap", { query: q }),
  floorplanUrl: () => `${EDGE_URL}/floorplan.png`,
  previewMjpgUrl: (cameraId: string) => `${EDGE_URL}/preview/${encodeURIComponent(cameraId)}.mjpg`,
  previewJpgUrl: (cameraId: string, annotate = true) =>
    `${EDGE_URL}/preview/${encodeURIComponent(cameraId)}.jpg${annotate ? "?annotate=1" : ""}`,

  calibrateShelf: (shelfId: string) =>
    request<ShelfReference>(EDGE_URL, `/calibrate/shelves/${encodeURIComponent(shelfId)}/reference`, { method: "POST" }),
  calibrateAll: () => request<ShelfReference[]>(EDGE_URL, "/calibrate/shelves/reference-all", { method: "POST" }),

  sync: () => request<SyncStatus>(EDGE_URL, "/sync"),
  flush: () => request<SyncStatus>(EDGE_URL, "/sync/flush", { method: "POST" }),
  setLink: (state: LinkState) => request<SyncStatus>(EDGE_URL, "/demo/link", { method: "POST", body: { state } }),

  scenarios: () => request<ScenarioStatus>(EDGE_URL, "/demo/scenarios"),
  setScenario: (body: ScenarioRequest) => request<ScenarioStatus>(EDGE_URL, "/demo/scenario", { method: "POST", body }),
  chaos: (body: ChaosRequest) => request<ScenarioStatus>(EDGE_URL, "/demo/chaos", { method: "POST", body }),
  restock: (shelfId: string) => request<unknown>(EDGE_URL, `/demo/restock/${encodeURIComponent(shelfId)}`, { method: "POST" }),
  truth: () => request<SimTruth>(EDGE_URL, "/demo/truth"),
  /**
   * Clock factor. §C.16 has no dedicated endpoint; `SyntheticControl.set_clock_factor`
   * exists on the edge, so we try `POST /demo/clock {factor}` and fall back to
   * re-applying the active scenario with `params.clock_factor`. Reported as a gap.
   */
  setClockFactor: async (factor: number, active = "baseline") => {
    try {
      return await request<ScenarioStatus>(EDGE_URL, "/demo/clock", { method: "POST", body: { factor } });
    } catch {
      return request<ScenarioStatus>(EDGE_URL, "/demo/scenario", { method: "POST", body: { name: active, params: { clock_factor: factor } } });
    }
  },
  whatsappReply: (body: WhatsAppReply) => request<Alert>(EDGE_URL, "/demo/whatsapp/reply", { method: "POST", body }),

  dailySummary: (lang: "hi" | "en") => request<DailySummary>(EDGE_URL, "/summary/daily", { query: { lang } }),
  models: () => request<ModelStatus>(EDGE_URL, "/models"),
  modelsCheck: () => request<ModelStatus>(EDGE_URL, "/models/check", { method: "POST" }),
};
