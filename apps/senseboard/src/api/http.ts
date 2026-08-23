/**
 * Tiny typed fetch wrapper shared by the edge and cloud clients.
 *
 * Design:
 *  - Base URLs come from `VITE_EDGE_URL` / `VITE_CLOUD_URL` (primary) and fall
 *    back to the dev-server proxies `/edge` and `/cloud` when the env is empty,
 *    so `npm run dev` works with zero configuration.
 *  - Every failure becomes an `ApiError` with `offline=true` when the host is
 *    unreachable (network error / timeout). Pages use that flag to render the
 *    honest "cloud offline" state instead of a generic error.
 *  - Timeouts are short (6 s) because the board must degrade quickly when the
 *    cloud disappears during the "cable kaat do" demo beat.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly offline: boolean;
  readonly url: string;
  constructor(message: string, opts: { status?: number; offline?: boolean; url: string }) {
    super(message);
    this.name = "ApiError";
    this.status = opts.status ?? 0;
    this.offline = opts.offline ?? false;
    this.url = opts.url;
  }
}

export function isOffline(err: unknown): boolean {
  return err instanceof ApiError && err.offline;
}

function trimSlash(s: string): string {
  return s.replace(/\/+$/, "");
}

export const EDGE_URL = trimSlash(import.meta.env.VITE_EDGE_URL || "/edge");
export const CLOUD_URL = trimSlash(import.meta.env.VITE_CLOUD_URL || "/cloud");
export const STORE_ID = import.meta.env.VITE_STORE_ID || "STR-DL-001";

/** http(s)://host -> ws(s)://host; relative proxy paths resolve against window.location. */
export function toWsUrl(base: string, path: string): string {
  if (typeof window === "undefined") return base + path;
  const abs = new URL(base + path, window.location.origin);
  abs.protocol = abs.protocol === "https:" ? "wss:" : "ws:";
  return abs.toString();
}

export interface RequestOpts {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  timeoutMs?: number;
  headers?: Record<string, string>;
}

export function buildUrl(base: string, path: string, query?: RequestOpts["query"]): string {
  let url = base + path;
  if (query) {
    const qs = Object.entries(query)
      .filter(([, v]) => v !== undefined && v !== null && v !== "")
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
      .join("&");
    if (qs) url += (url.includes("?") ? "&" : "?") + qs;
  }
  return url;
}

export async function request<T>(base: string, path: string, opts: RequestOpts = {}): Promise<T> {
  const url = buildUrl(base, path, opts.query);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 6000);
  try {
    const res = await fetch(url, {
      method: opts.method ?? "GET",
      headers: { Accept: "application/json", ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}), ...opts.headers },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: ctrl.signal,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = (await res.json()) as { detail?: unknown; message?: unknown };
        detail = String(j.detail ?? j.message ?? detail);
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(`${res.status} ${detail}`, { status: res.status, url });
    }
    const ct = res.headers.get("content-type") ?? "";
    if (ct.includes("application/json")) return (await res.json()) as T;
    return (await res.text()) as unknown as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    // AbortError, TypeError (network) -> host unreachable
    throw new ApiError(e instanceof Error ? e.message : "network error", { offline: true, url });
  } finally {
    clearTimeout(timer);
  }
}
