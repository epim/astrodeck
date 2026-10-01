// FlowWires.tsx - every wire in the graph, the one being dragged, and the
// control that removes the selected one (wave R7 parity rows A6 and A7).
//
// THE SVG IS DELIBERATELY 10x10 AND `pointer-events: none`. It is not a
// viewport: `overflow: visible` lets its children paint the whole graph, and the
// tiny box means the element itself never sits over the surface swallowing
// background pans. Everything clickable opts back IN with
// `pointer-events: stroke`, which gives the clean separation - the fat
// transparent hit path is the only thing a click can land on, and the visible
// path never overrides its parent.
//
// TWO PATHS PER EDGE, and both are load-bearing. A 1.8 px stroke is a ~0.6 px
// target at the 46% zoom the reference captures use; the 14 px transparent band
// is what makes a wire selectable at all.
//
// GEOMETRY IS NOT RESTATED HERE. `portPos` and `edgePath` in the shared
// `geometry.ts` own the anchor formula and the bezier, including the documented
// 1 px attachment offset and the short-span control-point crossing. This file
// decides only which LANE a wire is in and what that lane looks like - colour,
// width, dash - which is not part of the path: a flow wire and an event wire
// between the same two points get byte-identical `d` strings.
//
// THE PANEL LOOP IS THE ONE EXCEPTION (#189 S4 item 6, spec 1.4). An event wire
// into a TARGET's `next` runs backward from the tail of its lane, and
// `edgePath` would sweep it across every card between the two ends. It is drawn
// as `geometry.loopArc` (out, down under the cards, left, up) with its own dash
// and a label chip, all from the pure `targetSummary.ts` the classic layer
// shares, so the two canvases cannot draw the loop differently.
//
// THE RUN STANDS LOWER HERE (#357). This canvas's cards grow past the formula
// box the arc routes under - a wrapped footer, a pill, EDIT STAGE - so the
// run is lowered by what a card can reach (`CARD_OVERHANG_PX`, FlowNode.tsx)
// through `geometry.lowerLoopRun`, and the remove control follows it. Still
// the formula, never the DOM, and the classic canvas's arc is unchanged.
//
// RE-RENDER SHAPE. The layer subscribes to the node array, the edge array and
// the run phase; each edge is its own memo'd child taking only primitives, and
// it reads its own source-stage status and its own selectedness with narrow
// selectors. So selecting a wire re-renders the wire that was selected and the
// one that now is, and nothing else, and the pending wire - which changes on
// every pointermove - is a separate component so dragging one wire does not
// re-path the other twenty. Nothing writes a stage's status today
// (`flows.statuses`, #464: no topic carries it, and the published sequence
// state never names the stage), so every wire reads idle and that selector
// wakes none; it is kept narrow so that a status feed, once there is one,
// re-renders one stage's outgoing wires and not the layer.

import { memo, type JSX, type MouseEvent as RMouseEvent } from "react";

import { NODE_DEFS } from "../../../../../components/flows/nodeDefs";
import {
  edgePath, lowerLoopRun, portPos, type FlowTier, type LoopArc,
} from "../../../../../components/flows/geometry";
import type { FlowEdgeRec, FlowNodeRec, PortKind } from "../../../../../components/flows/flowsTypes";
// The slice's own reader of whether the compile answer is the graph on
// screen's (#356), not a second copy of it. It is a slice, the shared logic
// wave R7 section 2.1 keeps, and the store this file already reads is built
// from it, so the import adds nothing to the split bundle.
import { compiledIsCurrent } from "../../../../../components/flows/flowsSlice";
import {
  LOOP_ARC_DASH, LOOP_CHIP_FONT_PX, loopArcOf, loopChip, loopChipBox,
} from "../../../../../components/flows/targetSummary";
import { useStore } from "../../../../../store";
import {
  HIT_W_CANVAS, isRunning, isWireActive, wireAnchors, wireDash, wireLane,
  wireMidpoint, wireStroke, wireWidth,
} from "./canvasModel";
import { CARD_OVERHANG_PX } from "./FlowNode";

// ---------------------------------------------------------------- loop arc

/** The panel loop's arc for one wire as THIS canvas draws it, or null for a
 *  wire that is not a loop wire.
 *
 *  `loopArcOf` decides which wires are arcs and routes the arc under the
 *  body by the card formula, as it does for the classic layer; this canvas
 *  then lowers the run by `CARD_OVERHANG_PX`, how far one of its cards can
 *  reach below that formula (#357). The same as a drop of `LOOP_ARC_DROP +
 *  CARD_OVERHANG_PX` (`lowerLoopRun`), so the run and its chip stand
 *  `LOOP_ARC_DROP` clear of the card as drawn rather than behind its footer.
 *
 *  ONE RESOLVER FOR THE LAYER AND THE REMOVE CONTROL, so the control stands
 *  on the drop of the arc drawn here, not on the canvas's shorter one. */
export function nextLoopArc(
  edge: FlowEdgeRec,
  graph: { nodes: readonly FlowNodeRec[]; edges: readonly FlowEdgeRec[] },
  tier: FlowTier,
): LoopArc | null {
  const loop = loopArcOf(edge, graph, tier);
  return loop && lowerLoopRun(loop, CARD_OVERHANG_PX);
}

// --------------------------------------------------------------- one wire

interface FlowWireProps {
  edgeId: string;
  /** The SOURCE stage's id - this component reads that stage's status itself. */
  fromNode: string;
  d: string;
  kind: PortKind;
  running: boolean;
  /** The store action itself, not a closure over it: a fresh `(id) => ...` built
   *  in the layer would be a new prop identity on every layer render and would
   *  defeat the memo below. */
  select: (sel: { kind: "edge"; id: string }) => void;
}

function FlowWireBase({ edgeId, fromNode, d, kind, running, select }: FlowWireProps): JSX.Element {
  // A string and a boolean. zustand compares the selector's RESULT with
  // Object.is, so selecting another wire cannot reach this one, nor could a
  // status written for another stage (nothing writes one today, #464).
  const status = useStore((s) => s.flows.statuses[fromNode] ?? "idle");
  const selected = useStore((s) => s.flows.sel?.kind === "edge" && s.flows.sel.id === edgeId);

  const active = isWireActive(running, status);
  const onSelect = (ev: RMouseEvent): void => {
    // Without this the click reaches the surface background, whose onClick
    // CLEARS the selection - the wire would be selected and deselected by one
    // press, and the remove control would never appear.
    ev.stopPropagation();
    select({ kind: "edge", id: edgeId });
  };

  return (
    <g>
      <path
        data-wire
        data-edge-id={edgeId}
        data-testid="flow-wire"
        d={d}
        fill="none"
        stroke="transparent"
        strokeWidth={HIT_W_CANVAS}
        className="nx-flow-wire-hit"
        onClick={onSelect}
      />
      <path
        d={d}
        fill="none"
        stroke={wireStroke(kind, active, selected)}
        strokeWidth={wireWidth(active, selected)}
        strokeLinecap="round"
        strokeDasharray={wireDash(kind, active)}
        className={active ? "nx-flow-wire nx-flow-wire-march" : "nx-flow-wire"}
      />
    </g>
  );
}

const FlowWire = memo(FlowWireBase);

// ---------------------------------------------------------------- loop arc

interface FlowLoopArcProps extends FlowWireProps {
  /** The chip's words, or null for a wire into `next` the run does not loop on
   *  (targetSummary `loopChip`). A string, so the memo still holds. */
  chip: string | null;
  chipX: number;
  chipY: number;
}

/** The panel loop's back-arc: the same two paths, hit band, stroke and width
 *  rules as any wire, and a dash and a chip of its own.
 *
 *  THE DASH NEVER CHANGES WITH THE RUN. A live wire elsewhere switches to
 *  `7 6`; this one keeps `LOOP_ARC_DASH`, because the run is when the night
 *  palette is up and every lane is the same red - the silhouette and the chip
 *  are what still say "this is the loop". Live still shows, as the 2.5 px
 *  width, the full-strength colour and the march. */
function FlowLoopArcBase({
  edgeId, fromNode, d, kind, running, select, chip, chipX, chipY,
}: FlowLoopArcProps): JSX.Element {
  const status = useStore((s) => s.flows.statuses[fromNode] ?? "idle");
  const selected = useStore((s) => s.flows.sel?.kind === "edge" && s.flows.sel.id === edgeId);

  const active = isWireActive(running, status);
  const stroke = wireStroke(kind, active, selected);
  const onSelect = (ev: RMouseEvent): void => {
    // As FlowWire: the surface background would otherwise clear the selection.
    ev.stopPropagation();
    select({ kind: "edge", id: edgeId });
  };
  const box = chip ? loopChipBox(chip) : null;

  return (
    <g data-loop-arc data-edge-id={edgeId} data-testid="flow-loop-arc">
      <path
        data-wire
        data-edge-id={edgeId}
        data-testid="flow-wire"
        d={d}
        fill="none"
        stroke="transparent"
        strokeWidth={HIT_W_CANVAS}
        className="nx-flow-wire-hit"
        onClick={onSelect}
      />
      <path
        data-loop-arc-path
        d={d}
        fill="none"
        stroke={stroke}
        strokeWidth={wireWidth(active, selected)}
        strokeLinecap="round"
        strokeDasharray={LOOP_ARC_DASH}
        className={active ? "nx-flow-wire nx-flow-wire-march" : "nx-flow-wire"}
      />
      {chip && box && (
        // On the run, centred, inside the SVG so it pans and zooms with the
        // wire it names. Sized from the text (monospace), never measured.
        <g data-loop-chip data-testid="flow-loop-chip" transform={`translate(${chipX},${chipY})`}>
          <rect
            x={-box.w / 2} y={-box.h / 2} width={box.w} height={box.h} rx={box.h / 2}
            fill="var(--bg)" stroke={stroke} strokeWidth={1}
          />
          <text
            textAnchor="middle"
            dominantBaseline="central"
            fill="var(--warn)"
            style={{ fontFamily: "var(--font-mono)", fontSize: LOOP_CHIP_FONT_PX }}
          >
            {chip}
          </text>
        </g>
      )}
    </g>
  );
}

const FlowLoopArc = memo(FlowLoopArcBase);

// ------------------------------------------------------------ pending wire

/** The wire following the pointer during a drag.
 *
 *  Its own component because `flowsMoveWire` writes on every pointermove: folded
 *  into the layer it would re-path every edge in the graph sixty times a second.
 *
 *  `wire.to` is already in WORLD coordinates - a pending path built in screen
 *  space would bend differently from the edge it becomes on drop. */
function FlowPendingWire({ tier }: { tier: FlowTier }): JSX.Element | null {
  const wire = useStore((s) => s.flows.wire);
  const nodes = useStore((s) => s.flows.graph.nodes);
  if (!wire) return null;

  const from = nodes.find((n) => n.id === wire.from);
  if (!from) return null;
  const p1 = portPos(from, wire.fromPort, "out", NODE_DEFS, tier);
  if (!p1) return null;

  return (
    <path
      data-testid="flow-wire-pending"
      d={edgePath(p1, wire.to, "canvas")}
      fill="none"
      stroke="var(--accent)"
      strokeWidth={2}
      strokeDasharray="5 5"
      opacity={0.8}
    />
  );
}

// ------------------------------------------------------------------- layer

export interface FlowWireLayerProps {
  tier: FlowTier;
}

export function FlowWireLayer({ tier }: FlowWireLayerProps): JSX.Element {
  const nodes = useStore((s) => s.flows.graph.nodes);
  const edges = useStore((s) => s.flows.graph.edges);
  // A string, so the layer re-renders once when a run starts or ends. A
  // stage's status is each wire's own read (`FlowWireBase`), never the
  // layer's; nothing writes one today (#464).
  const phase = useStore((s) => s.flows.run.phase);
  // Read once and handed down: one subscription for the whole layer instead of
  // one per wire, and the action's identity is stable so `memo` still holds.
  const select = useStore((s) => s.flowsSelect);
  // The loop chip's count comes from the last compile, and is withheld while
  // that answer is not the graph on screen's (`loopChip`). The plan object
  // changes only when a compile lands, so this costs one re-render per
  // compile.
  const plan = useStore((s) => s.flows.compiled?.plan ?? null);
  // ASKED OF THE ANSWER, NOT OF THE SAVE (#356, S7), as the classic layer
  // asks: `compiledIsCurrent` compares the graph the compile sent with the
  // graph on screen. `dirty` cleared a round trip before a save's compile
  // landed, drawing the old count over a new skip, and withheld the count
  // DONE's compile of the unsaved draft had just made.
  const current = useStore((s) => compiledIsCurrent(s.flows));

  const running = isRunning(phase);
  const graph = { nodes, edges };

  return (
    <svg width="10" height="10" className="nx-flow-wires" aria-hidden="true">
      {/* STORED ORDER. Sorting these would move the parity harness's click off
          the stroke of the first wire and onto a curve, where it would select
          nothing. */}
      {edges.map((e) => {
        const a = wireAnchors(e, nodes, tier);
        // An edge naming a stage or port that no longer exists. Skipped, not
        // drawn at a fallback anchor - a wire anchored inside a card header
        // reads as a deliberate connection.
        if (!a) return null;
        // The panel loop: a backward event wire into a TARGET's `next`, routed
        // under the lane from the card formula and clear of this canvas's
        // cards as drawn (`nextLoopArc`).
        const loop = nextLoopArc(e, graph, tier);
        if (loop) {
          return (
            <FlowLoopArc
              key={e.id}
              edgeId={e.id}
              fromNode={e.from}
              d={loop.d}
              kind={wireLane(e, nodes)}
              running={running}
              select={select}
              chip={loopChip(graph, e, plan, !current)}
              chipX={loop.label.x}
              chipY={loop.label.y}
            />
          );
        }
        return (
          <FlowWire
            key={e.id}
            edgeId={e.id}
            fromNode={e.from}
            d={edgePath(a.p1, a.p2, "canvas")}
            kind={wireLane(e, nodes)}
            running={running}
            select={select}
          />
        );
      })}
      <FlowPendingWire tier={tier} />
    </svg>
  );
}

// ----------------------------------------------------------- remove control

export interface FlowWireDeleteProps {
  /** The selected edge. Passed in rather than read from the store because the
   *  caller has already resolved `sel` to decide whether to render this at all. */
  edge: FlowEdgeRec;
  tier: FlowTier;
}

/** The control that removes the selected wire.
 *
 *  A SIBLING OF THE STAGE CARDS, INSIDE THE WORLD TRANSFORM, so it scales and
 *  pans with the graph instead of floating over it. It sits ON the curve: the
 *  position is the straight-line midpoint of the two port anchors, which is
 *  exactly B(0.5) for the canvas bezier because the control offsets are
 *  symmetric. No sampling, no second bezier - the button lands on the wire by
 *  construction.
 *
 *  THE LOOP ARC IS NOT A BEZIER, so the anchors' midpoint is not on it: for a
 *  lane drawn left to right it lands among the lane's cards at port height, a
 *  remove control floating with no wire under it (on the eighth Example, in
 *  the gap between AUTOFOCUS and GUIDE). The arc's `handle` is a point on its
 *  drop, outside every card and away from its chip.
 *
 *  It owns `data-flows-wire-selected`, the marker that says the selected wire
 *  has a visible way to remove it. */
export function FlowWireDelete({ edge, tier }: FlowWireDeleteProps): JSX.Element | null {
  const nodes = useStore((s) => s.flows.graph.nodes);
  const edges = useStore((s) => s.flows.graph.edges);
  const deleteSel = useStore((s) => s.flowsDeleteSel);

  // Same resolver the layer uses, so the button cannot disagree with the wire
  // about where either end is. Null when either end names a stage or port that
  // no longer exists: the layer skipped drawing that edge, so there is nothing
  // here to remove FROM, and offering to delete an invisible wire is worse than
  // offering nothing.
  const a = wireAnchors(edge, nodes, tier);
  if (!a) return null;

  const loop = nextLoopArc(edge, { nodes, edges }, tier);
  const { x: mx, y: my } = loop ? loop.handle : wireMidpoint(a.p1, a.p2);

  return (
    <button
      type="button"
      data-flows-wire-selected
      data-testid="flow-wire-delete"
      className="nx-flow-wire-cut"
      aria-label="Delete wire"
      title="Delete wire"
      onClick={(ev) => {
        // Deletion already clears the selection, so bubbling would be harmless
        // today - but it would make this button's behaviour depend on the order
        // two handlers run in.
        ev.stopPropagation();
        deleteSel();
      }}
      style={{ transform: `translate3d(${mx}px,${my}px,0) translate(-50%,-50%)` }}
    >
      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
        strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
        <path d="M6 6l12 12M18 6L6 18" />
      </svg>
    </button>
  );
}

export default FlowWireLayer;
