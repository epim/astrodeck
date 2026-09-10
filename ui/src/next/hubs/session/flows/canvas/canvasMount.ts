// canvasMount.ts - the one handle on the mounted canvas element, and the one
// thing that needs it: where a newly added stage goes.
//
// A MODULE OF ITS OWN, deliberately. `FlowCanvasSurface.tsx` sets the handle and
// the PALETTE reads `flowCanvasDropPoint()`; if the function lived in the
// surface, importing it would pull the whole canvas - React, wires, gestures -
// into the palette's chunk for the sake of one rect. Splitting it keeps the
// palette's import cheap and the ownership obvious.
//
// There is one canvas mounted at a time by construction (the Flows hub body
// renders one), and the handle is nulled on unmount so a stale rect can never
// outlive the surface.

import { useStore } from "../../../../../store";
import { nodeW, type FlowTier } from "../../../../../components/flows/geometry";

let mounted: HTMLElement | null = null;

/** Called by `FlowCanvasSurface` on mount and with `null` on unmount. */
export function setMountedFlowCanvas(el: HTMLElement | null): void {
  if (el === null && mounted === null) return;
  mounted = el;
}

/** True while a canvas is on screen. The palette uses it to decide whether it
 *  is placing a stage or falling back. */
export function hasMountedFlowCanvas(): boolean {
  return mounted !== null;
}

/** Horizontally centred, vertically ABOVE centre (`/2.4`, not `/2`) so a dropped
 *  stage lands in the reading half of the canvas rather than under the log
 *  strip. */
const DROP_Y_DIVISOR = 2.4;

/** Where a newly added stage goes, in WORLD coordinates.
 *
 *  `null` when no canvas is mounted - the caller then owns the fallback, and
 *  `PALETTE_FALLBACK_DROP` in `canvasModel.ts` is the prototype's own (120,120).
 *  Returning a plausible point instead would put every stage created from the
 *  phone stage list at a coordinate nobody chose. */
export function flowCanvasDropPoint(tier: FlowTier): { x: number; y: number } | null {
  const el = mounted;
  if (!el) return null;
  const r = el.getBoundingClientRect();
  const { pan, zoom } = useStore.getState().flows;
  const cx = (r.width / 2 - pan.x) / zoom;
  const cy = (r.height / DROP_Y_DIVISOR - pan.y) / zoom;
  return { x: Math.round(cx - nodeW(tier) / 2), y: Math.round(cy) };
}
