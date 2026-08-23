/**
 * Live state fed by the two WebSocket streams (edge `/ws/live`, cloud `/v1/ws`).
 *
 * This store is the single place where push data lands. REST polling (TanStack
 * Query, 5 s) runs alongside and hydrates the same slices through `mergeRest*`
 * helpers, so components never care which transport delivered a value: they
 * read `useLive((s) => s.kpi)` and get the freshest copy.
 *
 * Freshness rule: a WS payload always wins over a poll result that carries an
 * older `as_of_ts`/`ts`; alerts are keyed by `alert_id` so a later status
 * (acked/resolved) replaces an earlier one regardless of source.
 */
import { create } from "zustand";
import type {
  Alert,
  DeviceStatus,
  Event,
  HealthStatus,
  KpiToday,
  OutboundMessage,
  QueueForecast,
  ScenarioStatus,
  SyncStatus,
  WsMessage,
} from "@contracts/types";
import { isAlert, isEvent } from "@contracts/types";
import type { WsStatus } from "@/api/ws";

export interface LiveState {
  edgeWs: WsStatus;
  edgeWsAttempt: number;
  cloudWs: WsStatus;
  lastEdgeMsgTs: number | null;
  lastCloudMsgTs: number | null;

  kpi: KpiToday | null;
  health: HealthStatus | null;
  sync: SyncStatus | null;
  scenario: ScenarioStatus | null;
  alerts: Record<string, Alert>;
  forecasts: Record<string, QueueForecast>;
  /** last 200 non-telemetry events, newest first (for the ops event ticker) */
  events: Event[];

  notifications: Record<string, OutboundMessage>;
  devices: Record<string, DeviceStatus>;

  setEdgeWs: (s: WsStatus, attempt: number) => void;
  setCloudWs: (s: WsStatus) => void;
  onEdgeMessage: (m: WsMessage) => void;
  onCloudMessage: (m: WsMessage) => void;
  mergeAlerts: (alerts: Alert[]) => void;
  mergeKpi: (kpi: KpiToday) => void;
  mergeHealth: (h: HealthStatus) => void;
  mergeSync: (s: SyncStatus) => void;
  mergeScenario: (s: ScenarioStatus) => void;
  mergeNotifications: (msgs: OutboundMessage[]) => void;
  reset: () => void;
}

const MAX_EVENTS = 200;

function newer<T extends { as_of_ts?: number; ts?: number }>(prev: T | null, next: T): T {
  if (!prev) return next;
  const p = prev.as_of_ts ?? prev.ts ?? 0;
  const n = next.as_of_ts ?? next.ts ?? 0;
  return n >= p ? next : prev;
}

function upsertAlert(map: Record<string, Alert>, a: Alert): Record<string, Alert> {
  const prev = map[a.alert_id];
  // a resolved/acked copy must never be downgraded by a stale "open" poll result
  const rank = { open: 0, acked: 1, resolved: 2 } as const;
  if (prev && rank[prev.status] > rank[a.status]) return map;
  return { ...map, [a.alert_id]: a };
}

export const useLive = create<LiveState>()((set, get) => ({
  edgeWs: "idle",
  edgeWsAttempt: 0,
  cloudWs: "idle",
  lastEdgeMsgTs: null,
  lastCloudMsgTs: null,
  kpi: null,
  health: null,
  sync: null,
  scenario: null,
  alerts: {},
  forecasts: {},
  events: [],
  notifications: {},
  devices: {},

  setEdgeWs: (edgeWs, edgeWsAttempt) => set({ edgeWs, edgeWsAttempt }),
  setCloudWs: (cloudWs) => set({ cloudWs }),

  onEdgeMessage: (m) => {
    const d = m.data as unknown;
    const patch: Partial<LiveState> = { lastEdgeMsgTs: Date.now() / 1000 };
    switch (m.kind) {
      case "kpi":
        patch.kpi = newer(get().kpi, d as KpiToday);
        break;
      case "health":
        patch.health = d as HealthStatus;
        patch.sync = (d as HealthStatus).sync ?? get().sync;
        break;
      case "sync":
        patch.sync = d as SyncStatus;
        break;
      case "scenario":
        patch.scenario = d as ScenarioStatus;
        break;
      case "forecast": {
        const f = d as QueueForecast;
        if (f && typeof f.counter_id === "string") patch.forecasts = { ...get().forecasts, [f.counter_id]: f };
        break;
      }
      case "alert":
        if (isAlert(d)) patch.alerts = upsertAlert(get().alerts, d);
        break;
      case "event":
        if (isEvent(d)) {
          patch.events = [d, ...get().events].slice(0, MAX_EVENTS);
          const p = d.payload;
          if (p.type === "alert.raised" && isAlert(p.alert)) patch.alerts = upsertAlert(get().alerts, p.alert);
          if (p.type === "queue.forecast") patch.forecasts = { ...get().forecasts, [p.counter_id]: p };
        }
        break;
      default:
        break;
    }
    set(patch);
  },

  onCloudMessage: (m) => {
    const d = m.data as unknown;
    const patch: Partial<LiveState> = { lastCloudMsgTs: Date.now() / 1000 };
    switch (m.kind) {
      case "notification": {
        const n = d as OutboundMessage;
        if (n && typeof n.message_id === "string") patch.notifications = { ...get().notifications, [n.message_id]: n };
        break;
      }
      case "device": {
        const dev = d as DeviceStatus;
        if (dev && typeof dev.device_id === "string") patch.devices = { ...get().devices, [dev.device_id]: dev };
        break;
      }
      case "alert":
        // cloud-origin alerts (device_offline, shrink_suspect) also belong in the feed
        if (isAlert(d)) patch.alerts = upsertAlert(get().alerts, d);
        break;
      default:
        break;
    }
    set(patch);
  },

  mergeAlerts: (alerts) => {
    let map = get().alerts;
    for (const a of alerts) map = upsertAlert(map, a);
    set({ alerts: map });
  },
  mergeKpi: (kpi) => set({ kpi: newer(get().kpi, kpi) }),
  mergeHealth: (health) => set({ health, sync: health.sync ?? get().sync }),
  mergeSync: (sync) => set({ sync }),
  mergeScenario: (scenario) => set({ scenario }),
  mergeNotifications: (msgs) => {
    const map = { ...get().notifications };
    for (const n of msgs) map[n.message_id] = n;
    set({ notifications: map });
  },
  reset: () => set({ kpi: null, health: null, sync: null, scenario: null, alerts: {}, forecasts: {}, events: [], notifications: {}, devices: {} }),
}));

/** Derived selectors (kept here so components stay declarative). */
export const selectOpenAlerts = (s: LiveState): Alert[] =>
  Object.values(s.alerts).filter((a) => a.status !== "resolved");
export const selectAllAlerts = (s: LiveState): Alert[] => Object.values(s.alerts);
export const selectEdgeOnline = (s: LiveState): boolean => s.edgeWs === "open" || (s.lastEdgeMsgTs !== null && Date.now() / 1000 - s.lastEdgeMsgTs < 15);
/** sim time when the edge runs a synthetic clock, else wall time of last data */
export const selectDataTs = (s: LiveState): number | null => s.health?.sim_ts ?? s.scenario?.sim_ts ?? s.kpi?.as_of_ts ?? s.lastEdgeMsgTs;
