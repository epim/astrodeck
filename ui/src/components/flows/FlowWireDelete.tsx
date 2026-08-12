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
// This owns `data-flows-wire-selected`, which is the ONLY thing capture 15
// waits for — "the selected wire's remove control must be visible". A control
// that rendered with no box, or off behind a card, passes a DOM query and fails
// the picture.
import { useStore } from "../../store";
import { wireAnchors, wireMidpoint } from "./FlowWireLayer";
import type { FlowTier, Point } from "./geometry";
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
  const deleteSel = useStore((s) => s.flowsDeleteSel);

  // Same resolver the wire layer uses, so the button cannot disagree with the
  // wire about where either end is.
  const a = wireAnchors(edge, nodes, tier, positions);
  // Null when either end names a node or port that no longer exists — the wire
  // layer skipped drawing that edge, so there is nothing here to remove FROM.
  // Rendering the ✕ anyway would offer to delete a wire the operator cannot see.
  if (!a) return null;

  const sz = auto ? SIZE_AUTO : SIZE_CANVAS;
  const { x: mx, y: my } = wireMidpoint(a.p1, a.p2);

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
