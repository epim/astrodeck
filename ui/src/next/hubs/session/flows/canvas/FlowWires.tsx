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
// RE-RENDER SHAPE. The layer subscribes to the node array, the edge array and
// the run phase; each edge is its own memo'd child taking only primitives, and
// it reads its own source-node status and its own selectedness with narrow
// selectors. So a `flow.node` tick for one stage re-paths that stage's outgoing
// wires and nothing else, and the pending wire - which changes on every
// pointermove - is a separate component so dragging one wire does not re-path
// the other twenty.

import { memo, type JSX, type MouseEvent as RMouseEvent } from "react";

import { NODE_DEFS } from "../../../../../components/flows/nodeDefs";
import { edgePath, portPos, type FlowTier } from "../../../../../components/flows/geometry";
import type { FlowEdgeRec, PortKind } from "../../../../../components/flows/flowsTypes";
import { useStore } from "../../../../../store";
import {
  HIT_W_CANVAS, isRunning, isWireActive, wireAnchors, wireDash, wireLane,
  wireMidpoint, wireStroke, wireWidth,
} from "./canvasModel";

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
  // Object.is, so a status frame for another stage cannot reach this wire.
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
  // A string, so the layer re-renders once when a run starts or ends - not on
  // the per-stage status frames, which each wire reads for itself.
  const phase = useStore((s) => s.flows.run.phase);
  // Read once and handed down: one subscription for the whole layer instead of
  // one per wire, and the action's identity is stable so `memo` still holds.
  const select = useStore((s) => s.flowsSelect);

  const running = isRunning(phase);

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
 *  It owns `data-flows-wire-selected`, the marker that says the selected wire
 *  has a visible way to remove it. */
export function FlowWireDelete({ edge, tier }: FlowWireDeleteProps): JSX.Element | null {
  const nodes = useStore((s) => s.flows.graph.nodes);
  const deleteSel = useStore((s) => s.flowsDeleteSel);

  // Same resolver the layer uses, so the button cannot disagree with the wire
  // about where either end is. Null when either end names a stage or port that
  // no longer exists: the layer skipped drawing that edge, so there is nothing
  // here to remove FROM, and offering to delete an invisible wire is worse than
  // offering nothing.
  const a = wireAnchors(edge, nodes, tier);
  if (!a) return null;

  const { x: mx, y: my } = wireMidpoint(a.p1, a.p2);

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
