// paletteDrop.ts - where a stage lands when the palette drops one.
//
// WHERE THE TWO HELPERS COME FROM, AND WHY FROM THERE. `flowCanvasDropPoint`
// (`components/flows/FlowCanvas.tsx`) and `PALETTE_FALLBACK_DROP`
// (`components/flows/FlowPalette.tsx`) are the LEGACY SOURCE OF TRUTH for the
// drop rule today, and wave R7's brief for this task says to import them from
// there until the cutover. T-R7-1 re-exports both from
// `session/flows/canvas/`; when T-R7-20 composes the canvas, this module's two
// imports move to that re-export and nothing else here changes. Named in the
// task report as the one cross-directory import this area still makes.
//
// Both are pure with respect to THIS module: `flowCanvasDropPoint` reads the
// mounted canvas's rect and the live pan/zoom and answers `null` when no canvas
// is mounted, which is precisely the case the palette has to answer for.

import { flowCanvasDropPoint } from "../../../../../components/flows/FlowCanvas";
import { PALETTE_FALLBACK_DROP } from "../../../../../components/flows/FlowPalette";
import type { FlowTier } from "../../../../../components/flows/geometry";

export { PALETTE_FALLBACK_DROP };

/** The world point a newly added stage goes to.
 *
 *  With a canvas mounted this is its centre-ish point in world coordinates. With
 *  none - the phone, the palette sheet opened over a list, a test - it is the
 *  prototype's own (120, 120), which is what `PALETTE_FALLBACK_DROP` documents.
 *  Two stages dropped with no canvas land on top of each other, exactly as they
 *  do in the legacy editor under the same conditions. */
export function paletteDropPoint(tier: FlowTier): { x: number; y: number } {
  return flowCanvasDropPoint(tier) ?? { ...PALETTE_FALLBACK_DROP };
}
