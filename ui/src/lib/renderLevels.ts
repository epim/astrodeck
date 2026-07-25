// renderLevels.ts — the WYSIWYG bridge for GET /api/preview/{id}/render.png.
// (crop+render UI design §1.5, §2.2, §4.2, Decision A1)
//
// THE PROBLEM. What's on screen is a COMPOSITION:
//     screen = clientMTF( autoStretch(linear) )
// because the server already auto-stretched the 8-bit display base and the client
// LUT (lut.ts) is a *further* display-domain remap on top of it. The server's
// /render.png is a SINGLE linear-domain pass: stretch_with(linear, b, m, w).
// Feeding the client's raw display-domain black/mid/white straight in would NOT
// match the screen.
//
// THE BRIDGE. `preview.auto_levels` is the linear-domain (black, mid, white)
// triple that reproduces auto_stretch exactly through stretch_with
// (processing.py auto_levels/stretch_with). So we can pull the user's
// display-domain levels back through the inverse of the auto MTF and hand the
// server one linear triple:
//
//   B = ab + (aw-ab)·mtf⁻¹(am, black)
//   W = ab + (aw-ab)·mtf⁻¹(am, white)
//   M = (ab + (aw-ab)·mtf⁻¹(am, mid) − B) / (W − B)
//
// using mtf⁻¹(m, ·) == mtf(1−m, ·) (an identity of the PixInsight MTF).
//
// HONESTY (Decision A1). This pins the black point, the white point AND the
// value that lands on mid-grey exactly; only the curve *between* those anchors
// can differ (one MTF vs two composed). In **Auto** mode with neutral Brightness
// the formula collapses to exactly `preview.auto_levels` — byte-faithful WYSIWYG.
// In **Manual** (or Auto + a Brightness nudge) it is a high-quality
// approximation, and the UI says so rather than claiming pixel-identity.
import type { PreviewInfo, StretchParams } from "../types";
import { effectiveLevels, mtf } from "../components/preview/lut";

export interface RenderLevels {
  black: number;
  mid: number;
  white: number;
}

/** The server's default when nothing is known: a plain identity-ish window. */
const FALLBACK_AUTO: RenderLevels = { black: 0, mid: 0.5, white: 1 };

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/** Inverse of the PixInsight MTF: mtf(m, ·)⁻¹ == mtf(1 − m, ·). */
export function invMtf(m: number, y: number): number {
  return mtf(1 - m, y);
}

/**
 * True when the export is byte-faithful to the screen (Auto + neutral
 * Brightness). Drives the honest menu copy — we never call an approximation
 * "exactly what you see".
 */
export function isExactWysiwyg(stretch: StretchParams): boolean {
  return stretch.auto && Math.abs(stretch.brightness) < 1e-6;
}

/**
 * Map the client's display-domain stretch onto the linear-domain triple that
 * /render.png consumes. See the header for the derivation.
 */
export function toRenderLevels(
  stretch: StretchParams,
  autoLevels?: RenderLevels | null,
): RenderLevels {
  const a = autoLevels ?? FALLBACK_AUTO;
  const ab = clamp01(a.black);
  const aw = Math.max(clamp01(a.white), ab + 1e-4);
  const am = Math.min(Math.max(a.mid, 1e-4), 1 - 1e-4);
  const span = aw - ab;

  const d = effectiveLevels(stretch); // display-domain 0..1

  // pull each display anchor back into the linear window
  const lin = (v: number) => ab + span * clamp01(invMtf(am, clamp01(v)));
  let black = lin(d.black);
  let white = lin(d.white);
  if (white <= black) white = Math.min(1, black + 1e-4);
  const midLin = lin(d.mid);
  const mid = Math.min(Math.max((midLin - black) / (white - black), 1e-4), 1 - 1e-4);

  return { black: round6(black), mid: round6(mid), white: round6(white) };
}

function round6(v: number): number {
  return Math.round(v * 1e6) / 1e6;
}

/**
 * Query string for GET /api/preview/{id}/render.png. All three params are always
 * emitted — the server only takes the explicit-levels branch when it has the
 * full triple (to_png in processing.py), so omitting one would silently fall
 * back to auto and quietly ignore the user's stretch.
 */
export function renderQuery(stretch: StretchParams, autoLevels?: RenderLevels | null): string {
  const { black, mid, white } = toRenderLevels(stretch, autoLevels);
  const p = new URLSearchParams();
  p.set("black", String(black));
  p.set("mid", String(mid));
  p.set("white", String(white));
  return `?${p.toString()}`;
}

/** Convenience for the toolbar: the path + query for a preview's full-res bake. */
export function renderPath(previewId: number, stretch: StretchParams, preview?: PreviewInfo | null): string {
  return `/api/preview/${previewId}/render.png${renderQuery(stretch, preview?.auto_levels)}`;
}
