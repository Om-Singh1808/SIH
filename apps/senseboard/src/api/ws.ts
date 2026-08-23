/**
 * Resilient WebSocket client for the edge `/ws/live` and cloud `/v1/ws` streams.
 *
 * Why a hand-rolled client: both servers speak the tiny `WsMessage` envelope of
 * contracts §C.8, and the demo deliberately kills connectivity ("cable kaat do").
 * We need predictable reconnect behaviour that the SyncBadge can show honestly:
 *
 *   - exponential backoff 500 ms -> 1 s -> 2 s -> ... capped at 10 s, with ±20 %
 *     jitter so many tabs don't reconnect in lock-step;
 *   - the backoff resets after a connection that stayed open for >= 5 s;
 *   - `status` transitions: connecting -> open -> closed (with `attempt` count),
 *     observable through `onStatus`;
 *   - the WebSocket constructor and timer functions are injectable so the
 *     reconnect schedule is unit-tested deterministically (`ws.test.ts`).
 *
 * Polling (TanStack Query, 5 s) is the fallback when the socket is down; the
 * store layer (`store/live.ts`) decides which source wins per data kind.
 */
import type { WsMessage } from "./types";
import { isWsKind } from "./types";

export type WsStatus = "idle" | "connecting" | "open" | "closed";

export interface LiveSocketOptions {
  url: string;
  onMessage: (msg: WsMessage) => void;
  onStatus?: (status: WsStatus, attempt: number) => void;
  /** Injectables for tests */
  WebSocketImpl?: typeof WebSocket;
  setTimeoutImpl?: (fn: () => void, ms: number) => unknown;
  clearTimeoutImpl?: (id: unknown) => void;
  now?: () => number;
  random?: () => number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  stableAfterMs?: number;
}

/** Pure backoff schedule: attempt 0 -> base, doubling, capped; jitter in [-20 %, +20 %]. */
export function backoffDelay(attempt: number, base = 500, max = 10_000, rnd = 0.5): number {
  const raw = Math.min(max, base * 2 ** Math.max(0, attempt));
  const jitter = 1 + (rnd - 0.5) * 0.4;
  return Math.round(raw * jitter);
}

export function parseWsMessage(raw: unknown): WsMessage | null {
  if (typeof raw !== "string") return null;
  try {
    const j = JSON.parse(raw) as Partial<WsMessage>;
    if (!j || !isWsKind(j.kind) || typeof j.data !== "object" || j.data === null) return null;
    return { kind: j.kind, ts: typeof j.ts === "number" ? j.ts : Date.now() / 1000, store_id: j.store_id ?? null, data: j.data };
  } catch {
    return null;
  }
}

export class LiveSocket {
  private ws: WebSocket | null = null;
  private timer: unknown = null;
  private stopped = false;
  private openedAt = 0;
  attempt = 0;
  status: WsStatus = "idle";
  private readonly o: Required<Omit<LiveSocketOptions, "onStatus">> & Pick<LiveSocketOptions, "onStatus">;

  constructor(opts: LiveSocketOptions) {
    this.o = {
      WebSocketImpl: typeof WebSocket !== "undefined" ? WebSocket : (undefined as unknown as typeof WebSocket),
      setTimeoutImpl: (fn, ms) => setTimeout(fn, ms),
      clearTimeoutImpl: (id) => clearTimeout(id as ReturnType<typeof setTimeout>),
      now: () => Date.now(),
      random: Math.random,
      baseDelayMs: 500,
      maxDelayMs: 10_000,
      stableAfterMs: 5000,
      ...opts,
    };
  }

  private setStatus(s: WsStatus) {
    this.status = s;
    this.o.onStatus?.(s, this.attempt);
  }

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer !== null) {
      this.o.clearTimeoutImpl(this.timer);
      this.timer = null;
    }
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.onerror = null;
      ws.onmessage = null;
      ws.onopen = null;
      try {
        ws.close();
      } catch {
        /* already closed */
      }
    }
    this.setStatus("closed");
  }

  private connect(): void {
    if (this.stopped || !this.o.WebSocketImpl) return;
    this.setStatus("connecting");
    let ws: WebSocket;
    try {
      ws = new this.o.WebSocketImpl(this.o.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.openedAt = this.o.now();
      this.setStatus("open");
    };
    ws.onmessage = (ev: MessageEvent) => {
      const msg = parseWsMessage(ev.data);
      if (msg) this.o.onMessage(msg);
    };
    ws.onerror = () => {
      /* the close event follows; nothing to do here */
    };
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      if (this.openedAt && this.o.now() - this.openedAt >= this.o.stableAfterMs) this.attempt = 0;
      this.openedAt = 0;
      this.setStatus("closed");
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    const delay = backoffDelay(this.attempt, this.o.baseDelayMs, this.o.maxDelayMs, this.o.random());
    this.attempt += 1;
    this.timer = this.o.setTimeoutImpl(() => {
      this.timer = null;
      this.connect();
    }, delay);
  }
}
