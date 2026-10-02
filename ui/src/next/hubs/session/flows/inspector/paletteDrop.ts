// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
//
// AND WHAT A DROP OPENS (#189 S4 item 3; spec 2.1). A TARGET is not finished
// when it lands: it has no object, no grid and no angle, and all three are
// set in the Target modal. So a TARGET dropped from the palette opens its
// framing at once, on the node the drop just made (`frameDroppedStage`); any
// other stage opens nothing, and the palette carries on as before.
//
// THE NEW NODE'S ID. `flowsAddNode` mints and returns it, so that this door
// never has to find the node it just made. `FlowPaletteRail` does not pass
// that id on yet - its `onPicked(type)` is older than the return value, and
// the rail is not this file's - so `droppedStageId` takes the id when a caller
// has one, and otherwise reads the one node the add appended. That read is
// exact where it is used, and only there: inside the same synchronous press
// handler, straight after `flowsAddNode`'s synchronous `set`, which appends
// the node last, so no other write can land between the add and the read and
// no second drop can be the last node instead. It answers null when the last
// node is not of the type just picked, rather than open a different stage.

import type { FlowNodeType } from "../../../../../components/flows/flowsTypes";
import { useStore } from "../../../../../store";
import { flowCanvasDropPoint } from "../canvas/canvasMount";
import { PALETTE_FALLBACK_DROP } from "../canvas/canvasModel";
import type { FlowTier } from "../../../../../components/flows/geometry";
import { openFlowFrame } from "../framing/FlowFrameSheet";

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

/** The id of the stage the palette just added: `given` when the caller has
 *  `flowsAddNode`'s own answer, else the node the add appended (see the file
 *  header for why that read is exact at the palette's call sites), else null. */
export function droppedStageId(type: FlowNodeType, given?: string): string | null {
  if (given) return given;
  const nodes = useStore.getState().flows.graph.nodes;
  const last = nodes[nodes.length - 1];
  return last && last.type === type ? last.id : null;
}

/** After a palette drop: a TARGET opens the `flowFrame` sheet on `id`, with
 *  the flow as `?open=`, and this answers true; any other stage, or no id,
 *  opens nothing and answers false, so the caller does what it always did.
 *
 *  `inPlaceOfTop` is for the palette SHEET: the modal replaces it rather than
 *  stacking on it (`openFlowFrame`). The docked rail at desktop has no sheet
 *  to replace, and the sheet is pushed. */
export function frameDroppedStage(
  type: FlowNodeType, id: string | null, open: string | null,
  opts: { inPlaceOfTop?: boolean } = {},
): boolean {
  if (type !== "target" || !id) return false;
  openFlowFrame(id, open, opts);
  return true;
}
