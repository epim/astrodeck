// FlowWireDelete.tsx — the ✕ that removes the selected wire. §C.6.
//
// A SIBLING OF THE NODE CARDS, INSIDE THE WORLD TRANSFORM. That is what makes it
// scale and pan with the graph instead of floating over it, and it is why the
// size below is in world units rather than screen pixels. It renders after the
// cards in §C.4's tree, so it paints on top of whichever card the midpoint
// happens to land on.
//
// IT SITS ON THE CURVE, not near it. The position is the straight-line midpoint
// of the two PORT ANCHORS, which §C.6 notes is exactly B(0.5) for both bezier
// forms because both use symmetric control offsets — `edgePath` puts +c/−c
// (canvas) or +18/−18 with equal ±vy (phone FLOW) on the two control points, so
// the curve passes through the midpoint of its endpoints. No sampling, no second
// bezier: the button lands on the wire by construction.
//
// EXCEPT THE PANEL LOOP (#355), which is not a bezier. Its arc drops below the
// lane's cards, runs left and rises into the TARGET's `next`, so the midpoint
// of its anchors is among the lane's cards at port height, with no wire under
// it: on the eighth Example, the gap between AUTOFOCUS and GUIDE. Its control
// sits on the arc's `handle`, a point on the drop, from the same `wireLoopArc`
// the layer draws the arc with, so on the phone FLOW tab it follows the arc
// that tab draws (#360); there it stands 6 px in from the drop, so the
// container's edge does not cut it, and still covers it (#428). And there it
// also stays off every card the tab lays out (#502): moved in off the edge,
// the 26 px box reached 12 px into the cards' column, and when the lane's tail
// sits in the right column the middle of the drop is beside the tail, so the
// box covered the tail's corner. It now takes the clear height on the drop
// nearest its middle, or, when the drop has none, on the rise
// (`geometry.loopControlSpot`).
//
// This owns `data-flows-wire-selected`, which is the ONLY thing capture 15
// waits for — "the selected wire's remove control must be visible". A control
// that rendered with no box, or off behind a card, passes a DOM query and fails
// the picture.
import { useStore } from "../../store";
import { AUTO_PAD } from "./autoLayout";
import { placedCards, wireAnchors, wireLoopArc, wireMidpoint } from "./FlowWireLayer";
import {
  loopControlSpot, type CardBox, type FlowTier, type LoopArc, type Point,
} from "./geometry";
import { COLUMN_LOOP_STUB } from "./targetSummary";
import type { FlowEdgeRec } from "./flowsTypes";

/** Prototype lines 198 (canvas) and 336 (phone FLOW). World units, so the canvas
 *  one is ~10 screen px at the reference captures' 46% zoom and 22px at 1:1;
 *  the auto-graph never zooms, so 26 is 26.
 *
 *  ⚠ Both numbers, and the 26px phone variant in particular, are stated only in
 *  the prototype — README §5 names only the 26px PORT hit. §G-15 is open on
 *  them, so they are reproduced rather than rounded up to the app's 44px
 *  tap floor (`.btn-touch`). The floor's own documented scope, at index.css:949,
 *  is "controls you hit in the dark with cold hands: slew, stop, step,
 *  activate" — deleting a wire in the graph editor is not one of those, and the
 *  22px box still clears WCAG 2.5.8 through the spacing exception because
 *  nothing else is drawn within 24px of it. Flagged, not resolved. */
const SIZE_CANVAS = 22;
const SIZE_AUTO = 26;

/** How far in from the loop arc's drop its remove control stands on the
 *  phone FLOW tab (#428): 13 - (14 - 7) = 6 px, toward the cards.
 *
 *  The tab's container clips at its edges (`overflow-x-hidden`), its columns
 *  stand `AUTO_PAD` (14 px) inside them, and the arc's drop `COLUMN_LOOP_STUB`
 *  (7 px) outside the cards (#360), so the drop runs 7 px inside the edge. A
 *  26 px control CENTRED on it reached 6 px past the edge and was drawn cut
 *  off. Moved in by exactly that much, its outer side meets the edge (the
 *  drop stands right of every card of the loop's body, so in is left) and
 *  the drop still runs through it, 7 px inside that side, so the control
 *  still covers the wire it removes. Zero on the canvas, where the drop
 *  stands 24 px clear of the cards and nothing clips. */
export const LOOP_CONTROL_INSET_AUTO = Math.max(0, SIZE_AUTO / 2 - (AUTO_PAD - COLUMN_LOOP_STUB));

/** Where the phone FLOW tab puts the loop's remove control (#502): a place
 *  where its 26 px box lies inside the tab's container, covers the arc and
 *  meets no card the tab lays out (`loopControlSpot`, which holds the box
 *  inside the container by moving it in `LOOP_CONTROL_INSET_AUTO` off a leg).
 *
 *  THE CONTAINER'S RIGHT EDGE IS READ OFF THE LAYOUT. The columns fill it,
 *  the right one standing `AUTO_PAD` inside it (autoLayout.ts
 *  `layoutColumns`), and this control is not handed the width the tab
 *  measured. With no card in the right column the edge found is the left
 *  column's plus the gutter, inside the real one, so the box can only stand
 *  further in, never past the edge.
 *
 *  With no clear place on either leg the control stays where #428 put it,
 *  6 px in from the middle of the drop: covering a card is better than
 *  offering no way to remove the wire. */
function phoneLoopSpot(
  loop: LoopArc,
  cards: readonly CardBox[],
): Point {
  const right = Math.max(...cards.map((b) => b.x + b.w)) + AUTO_PAD;
  return loopControlSpot(loop, SIZE_AUTO, 0, right, cards)
    ?? { x: loop.handle.x - LOOP_CONTROL_INSET_AUTO, y: loop.handle.y };
}

export interface FlowWireDeleteProps {
  /** The selected edge. Passed in rather than read from the store because the
   *  caller has already resolved `sel` to decide whether to render this at all
   *  (§C.4: `{selEdge && <FlowWireDelete edge={selEdge} />}`). */
  edge: FlowEdgeRec;
  /** Card width feeds the output anchor (`node.x + nodeW`), so the wrong tier
   *  puts the ✕ 19px off the wire on a phone. See FlowWireLayer's note on the
   *  "desktop" default. */
  tier?: FlowTier;
  /** The phone FLOW tab's auto-graph — bigger button, laid-out positions. */
  auto?: boolean;
  /** Auto-graph positions by node id, from `computeAutoLayout().pos`. Must be
   *  the SAME map the wire layer was given: a ✕ placed from stored coordinates
   *  while the wire is drawn from laid-out ones lands nowhere near its wire. */
  positions?: Readonly<Record<string, Point>>;
}

export default function FlowWireDelete({
  edge,
  tier = "desktop",
  auto = false,
  positions,
}: FlowWireDeleteProps) {
  const nodes = useStore((s) => s.flows.graph.nodes);
  // The lane a loop arc runs under is read from the wires, so the control
  // needs them too; the layer already re-renders on the same array.
  const edges = useStore((s) => s.flows.graph.edges);
  const deleteSel = useStore((s) => s.flowsDeleteSel);

  // Same resolver the wire layer uses, so the button cannot disagree with the
  // wire about where either end is.
  const a = wireAnchors(edge, nodes, tier, positions);
  // Null when either end names a node or port that no longer exists — the wire
  // layer skipped drawing that edge, so there is nothing here to remove FROM.
  // Rendering the ✕ anyway would offer to delete a wire the operator cannot see.
  if (!a) return null;

  const sz = auto ? SIZE_AUTO : SIZE_CANVAS;
  const loop = wireLoopArc(edge, { nodes, edges }, tier, auto, positions);
  // On the loop: on the canvas its handle on the drop, where the drop stands
  // 24 px clear of the cards and nothing clips; on the phone FLOW tab inside
  // the container and off every card (#428, #502, `phoneLoopSpot`).
  const { x: mx, y: my } = !loop
    ? wireMidpoint(a.p1, a.p2)
    : auto
      ? phoneLoopSpot(loop, placedCards(nodes, tier, positions))
      : loop.handle;

  return (
    <button
      type="button"
      data-flows-wire-selected
      // The visible glyph is a bare ✕; without this the control announces
      // itself as "multiplication x".
      aria-label="Delete wire"
      title="Delete wire"
      onClick={(ev) => {
        // The canvas background clears the selection on click. Deletion already
        // clears it, so bubbling would be harmless today — but it would also
        // make this button's behaviour depend on the order two handlers run in.
        ev.stopPropagation();
        deleteSel();
      }}
      className="absolute left-0 top-0 rounded-full flex items-center justify-center
                 border border-bad bg-raise text-bad cursor-pointer leading-none"
      style={{
        width: sz,
        height: sz,
        fontSize: sz === SIZE_AUTO ? 12 : 11,
        // translate3d places the top-left in world space; the -50%/-50% then
        // centres the button on the midpoint. Same two-step the node cards use,
        // so both round the same way under a fractional zoom.
        transform: `translate3d(${mx}px,${my}px,0) translate(-50%,-50%)`,
      }}
    >
      ✕
    </button>
  );
}
