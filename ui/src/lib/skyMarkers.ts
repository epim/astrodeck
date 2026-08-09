// skyMarkers.ts — turning "what is in this patch of sky" into "what is drawn
// where on this canvas", and turning a tap back into an object.
//
// EVERY SKY→SCREEN NUMBER IN THIS FILE COMES FROM ONE FUNCTION: `skyToView` in
// lib/atlasFov.ts, over the gnomonic in lib/framing.ts. That is the same
// projection the survey tiles, the planned FOV box and the live pointing
// footprint are drawn with, so a marker is glued to the pixels of the object it
// names through every pan and zoom. There is deliberately no second projection
// here, not even a "quick" small-angle one for the label offsets: two
// projections that drift apart is the defect class this codebase keeps finding.
//
// UNITS, because there are two and mixing them is the other easy bug:
//   • MARKERS are viewBox units (SkyCanvas's <svg viewBox="0 0 1000 1000">).
//     They are drawn straight into that svg, and hit-testing happens there too —
//     the pointer is converted ONCE, at the canvas edge.
//   • LABELS are CSS px, because they are real HTML text in the label layer and
//     the file's rule is that no text goes inside the scaled viewBox (text that
//     scales with a zoom is text that becomes 4px on a phone).
// The bridge between them is one number, `scale = boxPx / view`, applied in one
// place (`placeSky`).
//
// WHAT THIS MODULE IS NOT. It does not fetch (lib/skyRegion.ts), it does not
// render (components/atlas/AnnotationMarkers.tsx), and it holds no state. It is
// pure so that "a known object at a known position produces a marker at the
// expected screen coordinate" is a plain assertion rather than a screenshot.

import { skyToView, type AtlasViewGeom } from "./atlasFov";
import type { SkyRow } from "./skyRegion";

export type { SkyRow };

/** The five things a marker can be, by SHAPE. Never by colour alone: this app
 *  is used in the dark under a red filter (the `.survey` CSS rule has one), and
 *  the same rule is already written into the canvas's gesture chip — word +
 *  glyph, never colour. Shapes follow printed charts, which is the vocabulary
 *  anyone holding one already has: ellipse = galaxy, square = nebula, circle =
 *  cluster, dot = star, ringed dot = something in the solar system. */
export type Glyph = "galaxy" | "nebula" | "cluster" | "star" | "body";

export type Anchor = "e" | "w" | "n" | "s";

const NEBULA_TYPES = new Set([
  "Emission Nebula", "Reflection Nebula", "Planetary Nebula",
  "Dark Nebula", "Supernova Remnant",
]);
const CLUSTER_TYPES = new Set(["Open Cluster", "Globular Cluster"]);

export function glyphFor(row: SkyRow): Glyph {
  if (row.kind === "solar_system") return "body";
  if (row.kind === "star") return "star";
  if (row.type === "Galaxy") return "galaxy";
  if (NEBULA_TYPES.has(row.type)) return "nebula";
  if (CLUSTER_TYPES.has(row.type)) return "cluster";
  // "Star" (an NGC/IC-numbered star) and "Object" (OpenNGC's catch-all). A
  // point with no measured extent is a dot whatever list it came from.
  return row.type === "Star" ? "star" : "cluster";
}

/** Floor for a drawn marker, viewBox units. A 1' planetary at a 5° zoom is a
 *  third of a unit across; drawn true to size it is invisible and untappable. */
export const MIN_R = 5;
/** Ceiling, as a fraction of the viewBox. Past this an "outline" stops being an
 *  outline and becomes a wash across the whole canvas — and the object's extent
 *  is then obvious from the survey image underneath it anyway. */
export const MAX_R_FRAC = 0.45;

export interface PlacedMarker {
  row: SkyRow;
  /** viewBox units. */
  x: number;
  y: number;
  /** Drawn radius / semi-axis, viewBox units. */
  r: number;
  glyph: Glyph;
  /** True when `r` is the object's REAL angular size rather than the floor
   *  glyph. Only an extended marker is hittable across its whole face. */
  extended: boolean;
}

export interface PlacedLabel {
  id: string;
  text: string;
  /** CSS px, top-left of the text box. */
  left: number;
  top: number;
  anchor: Anchor;
}

export interface Rect { x: number; y: number; w: number; h: number }

export interface Placement {
  markers: PlacedMarker[];
  labels: PlacedLabel[];
  /** Where each placed label ended up, for the next frame to prefer. */
  anchors: Map<string, Anchor>;
}

export interface PlaceOptions {
  /** CSS px edge of the square canvas. */
  boxPx: number;
  /** Measures a label's rendered width in CSS px. See SkyCanvas for the real
   *  one; a test passes a deterministic stub. */
  measure: (text: string) => number;
  /** Anchors chosen last frame, keyed by object id — tried first so a label
   *  does not flip sides while the sky moves a pixel under it. */
  sticky?: Map<string, Anchor>;
  /** Boxes already occupied by the canvas's own furniture (the compass letters,
   *  the pixel-scale readout, the "Your camera" label). CSS px. */
  reserved?: Rect[];
  /** The object the page is FRAMING. FovOverlay already draws that one object's
   *  angular extent, centred on the view; drawing a second ellipse for it here
   *  would put two different claims about one object's size on one canvas. It
   *  still gets a marker and stays tappable — just the floor glyph. */
  framedId?: string | null;
  /** Override for tests. Default: clamp(6, 24, round(boxPx / 60)). */
  labelBudget?: number;
}

/** How many objects get TEXT. A budget, not a score threshold: a threshold
 *  gives an empty sky in Ursa Major and a solid wall in Sagittarius, while a
 *  budget gives the same reading density everywhere, which is the actual
 *  requirement. ~7 on a 390px phone, 12 at the canvas's 720px cap. */
export function labelBudget(boxPx: number): number {
  return Math.min(24, Math.max(6, Math.round(boxPx / 60)));
}

/** Drawn radius in viewBox units for one row, and whether it is the real size.
 *
 *  A star's radius comes from its magnitude, which is the convention every star
 *  chart uses and the only size information a point source has. Everything else
 *  is drawn at its true angular size when that lands between the floor and the
 *  ceiling above. */
export function markerRadius(
  row: SkyRow, pxPerDeg: number, view: number,
): { r: number; extended: boolean } {
  const maxR = MAX_R_FRAC * view;
  const trueR = (row.size_arcmin / 60 / 2) * pxPerDeg;
  if (trueR >= MIN_R && trueR <= maxR) return { r: trueR, extended: true };
  if (row.kind === "star" || row.kind === "solar_system") {
    // No published magnitude falls to the SMALLEST dot this draws, not to a
    // middling one: "nobody measured it" must never be rendered as "it is
    // bright". (In practice every star and body carries one; this is the
    // degraded-payload path, not a common one.)
    const mag = row.mag ?? 99;
    return { r: Math.min(7, Math.max(2.5, 7 - mag)), extended: false };
  }
  // Too big to outline honestly, or too small to see: the floor glyph, which
  // says "something catalogued is here" without claiming a size.
  return { r: MIN_R, extended: false };
}

const LABEL_H = 16;      // 12px text in a 1px-padded plate — measured, not guessed
const LABEL_GAP = 5;     // clearance between the marker edge and the text box
const LABEL_PAD = 3;     // minimum gap between two label boxes

function rectsOverlap(a: Rect, b: Rect, pad: number): boolean {
  return (
    a.x < b.x + b.w + pad && b.x < a.x + a.w + pad &&
    a.y < b.y + b.h + pad && b.y < a.y + a.h + pad
  );
}

function anchorRect(
  cx: number, cy: number, gap: number, w: number, anchor: Anchor,
): Rect {
  const h = LABEL_H;
  switch (anchor) {
    case "e": return { x: cx + gap, y: cy - h / 2, w, h };
    case "w": return { x: cx - gap - w, y: cy - h / 2, w, h };
    case "n": return { x: cx - w / 2, y: cy - gap - h, w, h };
    case "s": return { x: cx - w / 2, y: cy + gap, w, h };
  }
}

const ANCHOR_ORDER: Anchor[] = ["e", "w", "n", "s"];

/**
 * Project every row, keep the ones that land on the canvas, and decide which of
 * them get text.
 *
 * `rows` MUST already be in score order — the server returns them that way and
 * the label budget is spent in exactly that order. Solar-system bodies are
 * offered a label first regardless of where they sorted: there are at most a
 * handful of them, they are the only things on this map that move, and they are
 * what someone with a new telescope asks about before anything else.
 */
export function placeSky(
  rows: SkyRow[], geom: AtlasViewGeom, opts: PlaceOptions,
): Placement {
  const { boxPx, measure, sticky, reserved = [], framedId = null } = opts;
  const scale = boxPx / geom.view;
  const markers: PlacedMarker[] = [];

  for (const row of rows) {
    // null = the point is past the projection horizon: it has no position on
    // this map at all, which is a different thing from being off its edge, and
    // drawing it would put it MIRRORED onto the near hemisphere.
    const p = skyToView(row.ra_hours, row.dec_deg, geom);
    if (p === null) continue;
    const sized = framedId != null && row.id === framedId
      ? { r: MIN_R, extended: false }
      : markerRadius(row, geom.pxPerDeg, geom.view);
    // Off-canvas, generously: an extended object whose centre has scrolled off
    // may still have most of its face on screen.
    const reach = Math.max(sized.r, MIN_R);
    if (p.x < -reach || p.x > geom.view + reach ||
        p.y < -reach || p.y > geom.view + reach) continue;
    markers.push({
      row, x: p.x, y: p.y, r: sized.r, extended: sized.extended,
      glyph: glyphFor(row),
    });
  }

  const budget = opts.labelBudget ?? labelBudget(boxPx);
  const placed: Rect[] = [...reserved];
  const labels: PlacedLabel[] = [];
  const anchors = new Map<string, Anchor>();
  const order = [
    ...markers.filter((m) => m.row.kind === "solar_system"),
    ...markers.filter((m) => m.row.kind !== "solar_system"),
  ];

  for (const m of order) {
    if (labels.length >= budget) break;
    const cx = m.x * scale;
    const cy = m.y * scale;
    const w = measure(m.row.label);
    const gap = m.r * scale + LABEL_GAP;
    const prev = sticky?.get(m.row.id);
    const tries = prev
      ? [prev, ...ANCHOR_ORDER.filter((a) => a !== prev)]
      : ANCHOR_ORDER;
    for (const anchor of tries) {
      const box = anchorRect(cx, cy, gap, w, anchor);
      if (box.x < 0 || box.y < 0 ||
          box.x + box.w > boxPx || box.y + box.h > boxPx) continue;
      if (placed.some((b) => rectsOverlap(b, box, LABEL_PAD))) continue;
      placed.push(box);
      labels.push({ id: m.row.id, text: m.row.label, left: box.x, top: box.y, anchor });
      anchors.set(m.row.id, anchor);
      break;
    }
    // No anchor fit: marker only, no text. The object is still drawn and still
    // tappable — losing the label is not losing the object.
  }

  return { markers, labels, anchors };
}

/** Thumb-sized, in CSS px. A 44px target is the smallest thing a finger finds
 *  reliably, and this app is used in the dark. */
export const TAP_RADIUS_PX = 22;

/**
 * Which object a tap at (`x`, `y`) — VIEWBOX UNITS — landed on, or null.
 *
 * `reach` is the tap radius in viewBox units; SkyCanvas converts TAP_RADIUS_PX
 * once, at the pointer. An extended marker is hittable anywhere inside its
 * outline as well, because a tap in the middle of a drawn galaxy obviously
 * means that galaxy. Nearest centre wins when several are in reach, so a small
 * object sitting inside a big one's outline is still selectable.
 */
export function hitTest(
  markers: PlacedMarker[], x: number, y: number, reach: number,
): PlacedMarker | null {
  let best: PlacedMarker | null = null;
  let bestD = Infinity;
  for (const m of markers) {
    const d = Math.hypot(m.x - x, m.y - y);
    const r = Math.max(reach, m.extended ? m.r : 0);
    if (d <= r && d < bestD) {
      best = m;
      bestD = d;
    }
  }
  return best;
}
