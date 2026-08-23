/**
 * Pure 2-D helpers for the zone editor and heatmap, mirroring the normative
 * rules in contracts `geometry.py`:
 *
 *   - A polygon is always stored open (no repeated first point); `closePolygon`
 *     is used only for drawing.
 *   - `sideOfLine` uses image coordinates (y down). +1 == LEFT of start->end,
 *     which is the IN direction for a crossing line. The zone editor draws the
 *     arrow pointing to that side so config authors see what "IN" means.
 */
export type Pt = [number, number];

export function closePolygon(poly: readonly Pt[]): Pt[] {
  if (poly.length === 0) return [];
  const first = poly[0];
  const last = poly[poly.length - 1];
  if (poly.length > 1 && first[0] === last[0] && first[1] === last[1]) return [...poly];
  return [...poly, [first[0], first[1]]];
}

/** Drop a trailing duplicate of the first point (normalises user-drawn polygons). */
export function openPolygon(poly: readonly Pt[]): Pt[] {
  if (poly.length > 1) {
    const first = poly[0];
    const last = poly[poly.length - 1];
    if (first[0] === last[0] && first[1] === last[1]) return poly.slice(0, -1) as Pt[];
  }
  return [...poly];
}

/** Sign of cross((end-start),(pt-start)); +1 = left side of start->end in y-down coords (IN). */
export function sideOfLine(pt: Pt, start: Pt, end: Pt): -1 | 0 | 1 {
  const cross = (end[0] - start[0]) * (pt[1] - start[1]) - (end[1] - start[1]) * (pt[0] - start[0]);
  return cross > 0 ? 1 : cross < 0 ? -1 : 0;
}

/** Unit normal pointing to the IN (+1) side of a line, in image coords. */
export function inNormal(start: Pt, end: Pt): Pt {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const len = Math.hypot(dx, dy) || 1;
  // (-dy, dx) has a positive cross product with (dx, dy), i.e. it points to side +1.
  return [-dy / len, dx / len];
}

/** Arrow for a line: from the midpoint, `len` px towards the IN side. */
export function inArrow(start: Pt, end: Pt, len = 18): { from: Pt; to: Pt } {
  const mid: Pt = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
  const n = inNormal(start, end);
  return { from: mid, to: [mid[0] + n[0] * len, mid[1] + n[1] * len] };
}

export function pointInPolygon(pt: Pt, poly: readonly Pt[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    const crosses = yi > pt[1] !== yj > pt[1] && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi;
    if (crosses) inside = !inside;
  }
  return inside;
}

export function polygonCentroid(poly: readonly Pt[]): Pt {
  if (poly.length === 0) return [0, 0];
  const sx = poly.reduce((a, p) => a + p[0], 0);
  const sy = poly.reduce((a, p) => a + p[1], 0);
  return [sx / poly.length, sy / poly.length];
}

export function dist(a: Pt, b: Pt): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

/** SVG `points` attribute string. */
export function toSvgPoints(poly: readonly Pt[]): string {
  return poly.map((p) => `${p[0]},${p[1]}`).join(" ");
}
