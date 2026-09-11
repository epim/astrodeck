// paletteDrop.ts - where a stage lands when the palette drops one.
//
// WHERE THE TWO HELPERS COME FROM, AND WHY FROM THERE. They come from the
// REBUILT canvas (`../canvas/canvasMount`, `../canvas/canvasModel`), and they
// have to: `flowCanvasDropPoint` answers from whichever canvas element is
// currently registered, and the legacy `components/flows/FlowCanvas.tsx` copy
// this module used until T-R7-21 is registered only by the legacy `FlowCanvas`
// component, which the new UI never mounts. So the legacy copy returned `null`
// on every call here and every stage dropped from the palette landed on the
// (120,120) fallback instead of the canvas the operator was looking at. Deep
// imports, not the `../canvas` barrel: both modules are component-free, and the
// barrel would pull the surface, the wires and `canvas.css` into the palette's
// chunk for the sake of one rect (see `canvasMount.ts`'s own note).
//
// Both are pure with respect to THIS module: `flowCanvasDropPoint` reads the
// mounted canvas's rect and the live pan/zoom and answers `null` when no canvas
// is mounted, which is precisely the case the palette has to answer for.

import { flowCanvasDropPoint } from "../canvas/canvasMount";
import { PALETTE_FALLBACK_DROP } from "../canvas/canvasModel";
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
