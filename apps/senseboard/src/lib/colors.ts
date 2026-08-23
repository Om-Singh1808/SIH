/**
 * Colour scales and state palettes.
 *
 * The heat scale is an ordered warm ramp (transparent -> amber -> saffron ->
 * deep red) that stays legible over both the light floorplan and the dark
 * theme. State colours are exported as Tailwind class bundles AND as an icon +
 * i18n label key, so no component can fall back to colour alone.
 */
import type { Severity, ShelfState } from "@contracts/types";

export interface Rgba {
  r: number;
  g: number;
  b: number;
  a: number;
}

const HEAT_STOPS: Array<[number, Rgba]> = [
  [0.0, { r: 250, g: 204, b: 21, a: 0.0 }],
  [0.15, { r: 250, g: 204, b: 21, a: 0.35 }],
  [0.45, { r: 249, g: 115, b: 22, a: 0.6 }],
  [0.75, { r: 220, g: 38, b: 38, a: 0.78 }],
  [1.0, { r: 127, g: 29, b: 29, a: 0.9 }],
];

/** Map t in [0,1] to a heat colour by linear interpolation between stops. */
export function heatColor(t: number): Rgba {
  const x = Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0;
  for (let i = 1; i < HEAT_STOPS.length; i++) {
    const [t1, c1] = HEAT_STOPS[i];
    if (x <= t1) {
      const [t0, c0] = HEAT_STOPS[i - 1];
      const f = t1 === t0 ? 0 : (x - t0) / (t1 - t0);
      return {
        r: Math.round(c0.r + (c1.r - c0.r) * f),
        g: Math.round(c0.g + (c1.g - c0.g) * f),
        b: Math.round(c0.b + (c1.b - c0.b) * f),
        a: +(c0.a + (c1.a - c0.a) * f).toFixed(3),
      };
    }
  }
  return HEAT_STOPS[HEAT_STOPS.length - 1][1];
}

export function rgbaCss(c: Rgba): string {
  return `rgba(${c.r},${c.g},${c.b},${c.a})`;
}

export interface StateStyle {
  /** Tailwind classes for the background/border of a tile */
  tile: string;
  /** Text colour classes */
  text: string;
  /** Solid dot / bar colour */
  bar: string;
  /** Unicode glyph shown next to the label (never colour-only) */
  icon: string;
  /** i18n key for the label */
  labelKey: string;
}

export const SHELF_STATE_STYLE: Record<ShelfState, StateStyle> = {
  stocked: {
    tile: "bg-leaf-50 border-leaf-500/40 dark:bg-leaf-700/15",
    text: "text-leaf-700 dark:text-leaf-400",
    bar: "bg-leaf-500",
    icon: "✓",
    labelKey: "shelf.state.stocked",
  },
  partial: {
    tile: "bg-amber-50 border-amber-400/50 dark:bg-amber-500/10",
    text: "text-amber-700 dark:text-amber-400",
    bar: "bg-amber-400",
    icon: "◐",
    labelKey: "shelf.state.partial",
  },
  empty: {
    tile: "bg-red-50 border-red-500/50 dark:bg-red-500/10",
    text: "text-red-700 dark:text-red-400",
    bar: "bg-red-500",
    icon: "✕",
    labelKey: "shelf.state.empty",
  },
  unknown: {
    tile: "bg-surface-3 border-line",
    text: "text-ink-2",
    bar: "bg-ink-3",
    icon: "?",
    labelKey: "shelf.state.unknown",
  },
};

export const SEVERITY_STYLE: Record<Severity, { badge: string; icon: string; labelKey: string }> = {
  critical: { badge: "bg-red-600 text-white", icon: "‼", labelKey: "severity.critical" },
  high: { badge: "bg-red-100 text-red-800 dark:bg-red-500/20 dark:text-red-300", icon: "!", labelKey: "severity.high" },
  warn: { badge: "bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300", icon: "△", labelKey: "severity.warn" },
  info: { badge: "bg-sky-100 text-sky-800 dark:bg-sky-500/20 dark:text-sky-300", icon: "i", labelKey: "severity.info" },
};

/** Chart series colours: one accent for "actual", a cool contrast for "predicted". */
export const CHART = {
  actual: "#f97316",
  predicted: "#0ea5e9",
  band: "rgba(14,165,233,0.15)",
  grid: "rgba(148,163,184,0.25)",
  good: "#16a34a",
  bad: "#dc2626",
};
