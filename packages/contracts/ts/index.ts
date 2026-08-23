/**
 * RetailSense contracts - TypeScript entry point for SenseBoard.
 *
 *   import { Alert, KpiToday, fmtInr, isAlertKind } from "@contracts/types";
 *
 * `types.gen.ts` is generated from the pydantic models by `tools/gen_ts_types.py`
 * (do not edit it); this file adds the hand-written constants, type guards and the
 * small formatting helpers every page needs.
 */

export * from "./types.gen";

import type {
  AckAction,
  Alert,
  AlertKind,
  AlertStatus,
  Event,
  EventClass,
  EventType,
  Lang,
  Payload,
  Severity,
  ShelfState,
  WsKind,
} from "./types.gen";

// ---------------------------------------------------------------------------
// closed vocabularies as runtime arrays (mirrors enums.py / events.py)
// ---------------------------------------------------------------------------

export const EVENT_TYPES = [
  "footfall.crossing",
  "zone.occupancy",
  "dwell.sample",
  "heatmap.tiles",
  "queue.snapshot",
  "queue.forecast",
  "shelf.scan",
  "shelf.state",
  "alert.raised",
  "alert.acked",
  "alert.resolved",
  "device.heartbeat",
  "stock.reconciled",
  "order.requested",
  "config.applied",
  "sim.truth",
] as const satisfies readonly EventType[];

export const EVENT_CLASS: Record<EventType, EventClass> = {
  "footfall.crossing": "aggregate",
  "zone.occupancy": "aggregate",
  "dwell.sample": "aggregate",
  "heatmap.tiles": "telemetry",
  "queue.snapshot": "aggregate",
  "queue.forecast": "aggregate",
  "shelf.scan": "aggregate",
  "shelf.state": "aggregate",
  "alert.raised": "alert",
  "alert.acked": "alert",
  "alert.resolved": "alert",
  "device.heartbeat": "telemetry",
  "stock.reconciled": "txn",
  "order.requested": "txn",
  "config.applied": "config",
  "sim.truth": "telemetry",
};

export const ALERT_KINDS = [
  "shelf_gap",
  "queue_long",
  "queue_forecast",
  "camera_down",
  "sync_backlog",
  "device_offline",
  "shrink_suspect",
  "footfall_spike",
] as const satisfies readonly AlertKind[];

export const ALERT_STATUSES = ["open", "acked", "resolved"] as const satisfies readonly AlertStatus[];
export const SEVERITIES = ["info", "warn", "high", "critical"] as const satisfies readonly Severity[];
export const SHELF_STATES = ["stocked", "partial", "empty", "unknown"] as const satisfies readonly ShelfState[];
export const LANGS = ["hi", "en"] as const satisfies readonly Lang[];
export const WS_KINDS = [
  "hello",
  "event",
  "alert",
  "kpi",
  "health",
  "sync",
  "scenario",
  "notification",
  "device",
  "forecast",
] as const satisfies readonly WsKind[];

/** WhatsApp digit menu per alert kind (mirrors alerts.ACTIONS_BY_KIND). Digit i == actions[i-1]. */
export const ACTIONS_BY_KIND: Record<AlertKind, AckAction[]> = {
  shelf_gap: ["restocked", "order", "false_positive"],
  queue_long: ["opened_counter", "ignore"],
  queue_forecast: ["opened_counter", "ignore"],
  camera_down: ["checked"],
  sync_backlog: [],
  device_offline: [],
  shrink_suspect: ["investigate", "false_positive"],
  footfall_spike: [],
};

// ---------------------------------------------------------------------------
// type guards
// ---------------------------------------------------------------------------

export function isAlertKind(x: unknown): x is AlertKind {
  return typeof x === "string" && (ALERT_KINDS as readonly string[]).includes(x);
}

export function isAlertStatus(x: unknown): x is AlertStatus {
  return typeof x === "string" && (ALERT_STATUSES as readonly string[]).includes(x);
}

export function isEventType(x: unknown): x is EventType {
  return typeof x === "string" && (EVENT_TYPES as readonly string[]).includes(x);
}

export function isSeverity(x: unknown): x is Severity {
  return typeof x === "string" && (SEVERITIES as readonly string[]).includes(x);
}

export function isShelfState(x: unknown): x is ShelfState {
  return typeof x === "string" && (SHELF_STATES as readonly string[]).includes(x);
}

export function isLang(x: unknown): x is Lang {
  return x === "hi" || x === "en";
}

export function isWsKind(x: unknown): x is WsKind {
  return typeof x === "string" && (WS_KINDS as readonly string[]).includes(x);
}

/** Narrow an Event's payload by its `type` discriminator. */
export function isPayload<T extends EventType>(p: Payload, type: T): p is Extract<Payload, { type: T }> {
  return p.type === type;
}

export function isAlert(x: unknown): x is Alert {
  return (
    typeof x === "object" &&
    x !== null &&
    typeof (x as Alert).alert_id === "string" &&
    isAlertKind((x as Alert).kind) &&
    isAlertStatus((x as Alert).status)
  );
}

export function isEvent(x: unknown): x is Event {
  return (
    typeof x === "object" &&
    x !== null &&
    typeof (x as Event).event_id === "string" &&
    isEventType((x as Event).type) &&
    typeof (x as Event).seq === "number"
  );
}

// ---------------------------------------------------------------------------
// formatting helpers
// ---------------------------------------------------------------------------

/**
 * Indian digit grouping without a currency sign: 1234567.8 -> "12,34,568".
 * Rounds to the nearest rupee by default; pass `decimals` to keep paise.
 * Mirrors i18n.fmt_inr in Python so edge/cloud/board agree on every number.
 */
export function fmtInr(x: number | null | undefined, decimals = 0): string {
  if (x === null || x === undefined || Number.isNaN(x) || !Number.isFinite(x)) return "?";
  const neg = x < 0;
  const fixed = Math.abs(x).toFixed(decimals);
  const [intPart, frac] = fixed.split(".");
  let grouped = intPart;
  if (intPart.length > 3) {
    const tail = intPart.slice(-3);
    let head = intPart.slice(0, -3);
    const groups: string[] = [];
    while (head.length > 2) {
      groups.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) groups.unshift(head);
    grouped = groups.join(",") + "," + tail;
  }
  return (neg ? "-" : "") + grouped + (frac ? "." + frac : "");
}

/** "₹12,34,568" */
export function inr(x: number | null | undefined, decimals = 0): string {
  const s = fmtInr(x, decimals);
  return s === "?" ? "₹?" : s.startsWith("-") ? "-₹" + s.slice(1) : "₹" + s;
}

/** Seconds -> "4 min" / "45 s" / "1 h 05 min" (for wait times and gap timers). */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "?";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${String(m % 60).padStart(2, "0")} min`;
}

/** Epoch seconds -> "HH:MM" in the store timezone (default Asia/Kolkata). */
export function fmtClock(ts: number | null | undefined, tz = "Asia/Kolkata"): string {
  if (ts === null || ts === undefined || !Number.isFinite(ts)) return "--:--";
  return new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz }).format(
    new Date(ts * 1000),
  );
}

/** Pick the pre-rendered title/message for a language. */
export function alertTitle(a: Alert, lang: Lang): string {
  return lang === "hi" ? a.title_hi : a.title_en;
}

export function alertMessage(a: Alert, lang: Lang): string {
  return lang === "hi" ? a.message_hi : a.message_en;
}

/** WhatsApp digit (1-based) -> action, or null when out of range. */
export function actionForDigit(a: Alert, digit: number): AckAction | null {
  return digit >= 1 && digit <= a.actions.length ? a.actions[digit - 1] : null;
}

/** Severity ordering for sorting alert lists (critical first). */
export const SEVERITY_RANK: Record<Severity, number> = { critical: 0, high: 1, warn: 2, info: 3 };

export function sortAlerts(alerts: readonly Alert[]): Alert[] {
  return [...alerts].sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || b.raised_ts - a.raised_ts);
}
