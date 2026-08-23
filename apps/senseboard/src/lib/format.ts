/**
 * Number/time formatting for the board.
 *
 * Money and clock helpers come from the shared contracts (`fmtInr`, `inr`,
 * `fmtClock`, `fmtDuration`) so edge, cloud and board print identical strings.
 * This module adds the board-only helpers: percentages, compact deltas, relative
 * ages for freshness badges, and safe null handling (a dash, never "NaN").
 */
import { fmtInr, inr, fmtClock, fmtDuration } from "@contracts/types";

export { fmtInr, inr, fmtClock, fmtDuration };

export const DASH = "—";

export function fmtPct(x: number | null | undefined, decimals = 0): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return DASH;
  return `${x.toFixed(decimals)}%`;
}

export function fmtNum(x: number | null | undefined, decimals = 0): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return DASH;
  return fmtInr(x, decimals);
}

/** Minutes with one decimal for small values: 3.25 -> "3.3 min", 48 -> "48 min". */
export function fmtMinutes(min: number | null | undefined): string {
  if (min === null || min === undefined || !Number.isFinite(min)) return DASH;
  return min < 10 ? `${min.toFixed(1)} min` : `${Math.round(min)} min`;
}

/** Signed delta, e.g. +12 / −3.4%. Returns null for missing deltas. */
export function fmtDelta(d: number | null | undefined, suffix = ""): string | null {
  if (d === null || d === undefined || !Number.isFinite(d)) return null;
  const sign = d > 0 ? "+" : d < 0 ? "−" : "±";
  const abs = Math.abs(d);
  return `${sign}${abs >= 100 ? fmtInr(abs) : abs.toFixed(abs < 10 ? 1 : 0)}${suffix}`;
}

/** "12 s ago" / "4 min ago" / "2 h ago". `now` injectable for tests. */
export function fmtAge(ts: number | null | undefined, now: number = Date.now() / 1000, lang: "hi" | "en" = "en"): string {
  if (ts === null || ts === undefined || !Number.isFinite(ts)) return DASH;
  const s = Math.max(0, now - ts);
  const ago = lang === "hi" ? "पहले" : "ago";
  if (s < 60) return `${Math.round(s)} s ${ago}`;
  if (s < 3600) return `${Math.round(s / 60)} min ${ago}`;
  return `${(s / 3600).toFixed(1)} h ${ago}`;
}

/** Epoch seconds -> "HH:MM" in Asia/Kolkata; used for "Data as of" and delivered times. */
export function fmtHm(ts: number | null | undefined): string {
  return fmtClock(ts);
}

/** ISO date -> "23 Aug" (store tz). */
export function fmtShortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00+05:30`);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" }).format(d);
}

export function clamp(x: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, x));
}
