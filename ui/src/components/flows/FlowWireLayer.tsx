// FlowWireLayer.tsx — every wire in the graph, plus the one being dragged, in a
// single <svg>. §C.6 / §D.3 / §D.4.
//
// THE SVG IS DELIBERATELY 10x10 AND `pointer-events: none`. It is not a viewport
// — `overflow: visible` lets its children paint the whole graph, and the tiny box
// means the element itself never sits over the canvas swallowing background
// pans. Every clickable thing is a child that opts back IN with
// `pointer-events: stroke` (§D.4), which is what gives the clean separation: the
// fat transparent hit path is the ONLY thing a click can land on, and the visible
// path never overrides its parent.
//
// TWO PATHS PER EDGE, and both are load-bearing. A 1.8px stroke is a ~0.6px
// target at the 46% zoom the reference captures use; the 14px (canvas) / 16px
// (phone FLOW) transparent band is what makes a wire selectable at all. §G-15
// records that both widths come from the prototype only.
//
// GEOMETRY IS NOT RESTATED HERE. `portPos` and `edgePath` in geometry.ts own the
// anchor formula and the two beziers, including the 1px attachment offset README
// §3 ratifies and the short-span control-point crossing §D.3 says not to clamp
// away. This file decides only which LANE a wire is in and what that lane looks
// like — colour, width, dash — which §D.3 is explicit is not part of the path:
// "a flow wire and an event wire between the same two points get byte-identical
// paths".
//
// RE-RENDER SHAPE. The layer subscribes to the node array, the edge array and
// the run phase; each edge is its own memo'd child taking only PRIMITIVES, and
// it reads its own source-node status and its own selectedness with narrow
// selectors that return a string and a boolean. So a `flow.node` tick for one
// stage re-renders that stage's outgoing wires and nothing else, and the pending
// wire — which changes on every pointermove — is a separate component so
// dragging one wire does not re-path the other twenty.
import { memo } from "react";
import type { MouseEvent as RMouseEvent } from "react";
import { useStore } from "../../store";
import { NODE_DEFS } from "./nodeDefs";
import { edgePath, portPos, type EdgeMode, type FlowTier, type Point } from "./geometry";
import type { FlowEdgeRec, FlowNodeRec, FlowRunPhase, PortKind } from "./flowsTypes";

// ------------------------------------------------------------------ constants
/** Transparent hit band, in WORLD units (§D.4). At zoom 0.35 the canvas band is
 *  ~4.9 screen px and at 1.6 it is ~22px — it scales with the graph, like the
 *  wire it is catching for. */
export const HIT_W_CANVAS = 14;
/** The phone FLOW tab's auto-graph never zooms, so 16 is 16. */
export const HIT_W_AUTO = 16;

/** Fraction of the lane colour an idle wire is drawn at (prototype's
 *  `rgba(0,210,255,0.45)` / `rgba(255,180,84,0.45)`, expressed against the
 *  tokens so the night palette reaches it). */
const IDLE_MIX = "45%";

// -------------------------------------------------------------------- lookups
/** The node a wire leaves or enters, with its auto-graph position substituted
 *  when there is one.
 *
 *  The phone FLOW tab lays the graph out itself (`computeAutoLayout`) and does
 *  NOT write those positions back into the graph — the same flow reopened on the
 *  canvas must still be where the operator left it. The prototype resolves this
 *  in `positionOf()`; this is the same substitution, done once at the anchor. */
function placed(
  node: FlowNodeRec,
  positions?: Readonly<Record<string, Point>>,
): FlowNodeRec {
  const p = positions?.[node.id];
  return p ? { ...node, x: p.x, y: p.y } : node;
}

/** Which lane a wire is in — decided by the SOURCE port's kind, never the
 *  target's (§C.6). A kind mismatch cannot be wired in the first place (§D.4
 *  rule 4), so the two agree on every edge the editor can produce; a graph that
 *  arrived some other way still gets drawn in the lane it leaves from.
 *
 *  Falls back to "flow" for an unknown node or port, reproducing the prototype's
 *  `|| "flow"`. Nothing is lost: an edge whose ports cannot be resolved has no
 *  anchors either, so `wireAnchors` drops it before this matters. */
export function wireLane(
  edge: FlowEdgeRec,
  nodes: readonly FlowNodeRec[],
): PortKind {
  const from = nodes.find((n) => n.id === edge.from);
  const def = from ? NODE_DEFS[from.type] : undefined;
  const port = def?.outs.find((p) => p.id === edge.fromPort);
  return port ? port.kind : "flow";
}

/** The two port anchors a wire runs between, or null when either end cannot be
 *  placed.
 *
 *  Null happens for real: a saved graph can name a node that has been deleted or
 *  a port the vocabulary has since dropped. geometry.portPos() refuses to guess
 *  in that case, and so does this — the caller SKIPS the wire rather than
 *  drawing it somewhere plausible, because a wire anchored inside a card header
 *  reads as a deliberate connection. */
export function wireAnchors(
  edge: FlowEdgeRec,
  nodes: readonly FlowNodeRec[],
  tier: FlowTier,
  positions?: Readonly<Record<string, Point>>,
): { p1: Point; p2: Point } | null {
  const a = nodes.find((n) => n.id === edge.from);
  const b = nodes.find((n) => n.id === edge.to);
  if (!a || !b) return null;
  const p1 = portPos(placed(a, positions), edge.fromPort, "out", NODE_DEFS, tier);
  const p2 = portPos(placed(b, positions), edge.toPort, "in", NODE_DEFS, tier);
  return p1 && p2 ? { p1, p2 } : null;
}

/** Where the wire's remove control goes: the straight-line midpoint of the two
 *  PORT ANCHORS — not of the path's bounding box, and not a sampled point.
 *
 *  §C.6: that midpoint is exactly B(0.5) for both bezier forms, because both put
 *  symmetric offsets on the control points (+c/−c on canvas, +18/−18 with equal
 *  ±vy on phone FLOW). Expanding the cubic at t=0.5 gives (p1 + 3c1 + 3c2 + p2)/8,
 *  and the ±offsets cancel in pairs, leaving (p1 + p2)/2. So "the button always
 *  sits on the curve" is an identity, not an approximation — which is why this
 *  lives beside `wireAnchors` rather than in the control: the two must never
 *  drift apart, and __tests__/flowWire.test.ts re-derives it from the emitted
 *  path string. */
export function wireMidpoint(p1: Point, p2: Point): Point {
  return { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };
}

// ---------------------------------------------------------------- lane styling
/** The prototype's boolean `s.running`, recovered from the four-valued phase.
 *
 *  `holding` counts. Capture 07 IS the hold — clouds in, calibration queue
 *  running — and its wires are marching, so a "running means phase === running"
 *  reading would photograph a dead graph in the one state the harness waits
 *  180s to reach. `stopping` counts for the same reason: the run has not
 *  stopped yet. */
export function isRunning(phase: FlowRunPhase): boolean {
  return phase !== "idle";
}

/** Is this wire carrying the run right now?
 *
 *  `ok` counts, not just `busy` — a finished stage's OUTGOING wires keep
 *  marching for the rest of the run, which is what capture 07 shows (the whole
 *  dusk → dome → duskflats → target → slew chain lit at once) and §C.6 says is
 *  deliberate. */
export function isWireActive(running: boolean, status: string): boolean {
  return running && (status === "busy" || status === "ok");
}

/** Stroke colour. Selection outranks activity, which outranks the idle lane. */
export function wireStroke(
  kind: PortKind,
  active: boolean,
  selected: boolean,
): string {
  if (selected) return "var(--text)";
  const lane = kind === "flow" ? "--accent" : "--warn";
  return active
    ? `var(${lane})`
    : `color-mix(in srgb, var(${lane}) ${IDLE_MIX}, transparent)`;
}

export function wireWidth(active: boolean, selected: boolean): number {
  return active || selected ? 2.5 : 1.8;
}

/** The dash pattern — and the reason status here is never colour alone.
 *
 *  Three states, three silhouettes: a live wire is `7 6` (and marches), an event
 *  wire is `4 5`, a resting flow wire is solid. Night mode collapses the lane
 *  HUES toward one another and `prefers-reduced-motion` stops the march, so the
 *  pattern is what still says which lane a wire is in and whether it is carrying
 *  the run. Selection does NOT get its own dash — it changes colour and width —
 *  so the pattern keeps reading the lane while a wire is picked. */
export function wireDash(kind: PortKind, active: boolean): string | undefined {
  if (active) return "7 6";
  return kind === "event" ? "4 5" : undefined;
}

// --------------------------------------------------------------------- one wire
interface FlowWireProps {
  edgeId: string;
  /** The SOURCE node id — this component reads that node's status itself. */
  fromNode: string;
  d: string;
  kind: PortKind;
  running: boolean;
  hitW: number;
  /** The store action itself, not a closure over it — a fresh `(id) => …` built
   *  in the layer would be a new prop identity on every layer render and would
   *  defeat the memo below. */
  select: (sel: { kind: "edge"; id: string }) => void;
}

function FlowWire({ edgeId, fromNode, d, kind, running, hitW, select }: FlowWireProps) {
  // A string and a boolean. zustand compares the selector's RESULT with
  // Object.is, so a status frame for another stage cannot reach this wire.
  const status = useStore((s) => s.flows.statuses[fromNode] ?? "idle");
  const selected = useStore(
    (s) => s.flows.sel?.kind === "edge" && s.flows.sel.id === edgeId);

  const active = isWireActive(running, status);
  const onSelect = (ev: RMouseEvent) => {
    // Without this the click reaches the canvas background, whose onClick
    // CLEARS the selection — the wire would be selected and deselected by one
    // press, and the remove control would never appear.
    ev.stopPropagation();
    select({ kind: "edge", id: edgeId });
  };

  return (
    <g>
      <path
        data-wire
        data-edge-id={edgeId}
        d={d}
        fill="none"
        stroke="transparent"
        strokeWidth={hitW}
        style={{ pointerEvents: "stroke", cursor: "pointer" }}
        onClick={onSelect}
      />
      <path
        d={d}
        fill="none"
        stroke={wireStroke(kind, active, selected)}
        strokeWidth={wireWidth(active, selected)}
        strokeLinecap="round"
        strokeDasharray={wireDash(kind, active)}
        // The march is `.flow-wire-march`, which §A.2 item 7 adds to index.css
        // (`@keyframes flow-dash` + a `prefers-reduced-motion` kill switch) —
        // this file cannot author it, since a keyframe cannot be expressed
        // inline. Until that lands the class is inert and the wire is static.
        // Nothing is LOST when that happens: the live state is already carried
        // by the `7 6` pattern, the 2.5px width and the full-strength lane
        // colour above, which is the same set of cues a reduced-motion reader
        // gets by design.
        className={active ? "flow-wire-march" : undefined}
      />
    </g>
  );
}

const FlowWireMemo = memo(FlowWire);

// ----------------------------------------------------------------- pending wire
/** The wire following the pointer during a drag.
 *
 *  Its own component because `flowsMoveWire` writes on every pointermove: folded
 *  into the layer it would re-path every edge in the graph 60 times a second.
 *
 *  ⚠ CYAN UNCONDITIONALLY, even when dragged from an amber event output. That is
 *  the prototype's behaviour and §G-17 is open on whether it is intended — not
 *  resolved here. */
function FlowPendingWire({ tier, mode }: { tier: FlowTier; mode: EdgeMode }) {
  const wire = useStore((s) => s.flows.wire);
  const nodes = useStore((s) => s.flows.graph.nodes);
  if (!wire) return null;

  const from = nodes.find((n) => n.id === wire.from);
  if (!from) return null;
  const p1 = portPos(from, wire.fromPort, "out", NODE_DEFS, tier);
  if (!p1) return null;

  // `wire.to` is already in WORLD coordinates (flowsTypes.PendingWire): a
  // pending path built in screen space would bend differently from the edge it
  // becomes on drop.
  //
  // The prototype builds this with the CANVAS bezier unconditionally, and that
  // is reproduced rather than switched on `mode`: a pending wire only exists on
  // a drag, drags only happen on a pan/zoom surface, and the phone FLOW tab
  // wires by tap. `mode` is threaded through so the two cannot silently diverge
  // if that ever stops being true.
  const d = edgePath(p1, wire.to, mode === "phone-flow" ? "canvas" : mode);

  return (
    <path d={d} fill="none" stroke="var(--accent)" strokeWidth={2}
          strokeDasharray="5 5" opacity={0.8} />
  );
}

// ----------------------------------------------------------------------- layer
export interface FlowWireLayerProps {
  /** Card width comes from the tier (188 wide / 150 phone), and every OUTPUT
   *  anchor is `node.x + nodeW`, so a wrong tier detaches every outgoing wire
   *  from its dot by 38px. Defaults to "desktop" only so `<FlowWireLayer />`
   *  — the literal call in §C.4 — compiles; the phone CANVAS tab must pass
   *  `tier="phone"` explicitly. */
  tier?: FlowTier;
  /** The phone FLOW tab's auto-graph (the prototype's `autoMode()`): vertical
   *  bezier, 16px hit band, positions from `computeAutoLayout`. False on every
   *  pan/zoom surface INCLUDING the phone CANVAS tab, which §C.5 calls the full
   *  editor. */
  auto?: boolean;
  /** Auto-graph positions by node id, from `computeAutoLayout().pos`. Ignored
   *  for any node it does not name, so a partial map degrades to stored
   *  coordinates rather than to (0,0). */
  positions?: Readonly<Record<string, Point>>;
}

export default function FlowWireLayer({
  tier = "desktop",
  auto = false,
  positions,
}: FlowWireLayerProps) {
  const nodes = useStore((s) => s.flows.graph.nodes);
  const edges = useStore((s) => s.flows.graph.edges);
  // A string, so the layer re-renders once when a run starts or ends — not on
  // the per-node status frames, which each wire reads for itself.
  const phase = useStore((s) => s.flows.run.phase);
  // Read once and handed down: one subscription for the whole layer instead of
  // one per wire, and the action's identity is stable so `memo` still holds.
  const select = useStore((s) => s.flowsSelect);

  const mode: EdgeMode = auto ? "phone-flow" : "canvas";
  const hitW = auto ? HIT_W_AUTO : HIT_W_CANVAS;
  const running = isRunning(phase);

  return (
    <svg
      width="10"
      height="10"
      className="absolute left-0 top-0 pointer-events-none"
      style={{ overflow: "visible" }}
    >
      {/* STORED ORDER, and it is a harness contract, not a preference. §D.4:
          the parity harness clicks `[data-wire]`.first and Playwright aims at
          the bounding-box CENTRE, which for a curved path need not be on the
          stroke. In stored order the first M16 edge is dusk→dome — a perfectly
          horizontal span in the 42px gap between two cards, whose bbox centre
          (239, 97) is dead on the wire. Sorting these would move that click
          onto a curve and capture 15 would select nothing, or deselect. */}
      {edges.map((e) => {
        const a = wireAnchors(e, nodes, tier, positions);
        // An edge naming a node or port that no longer exists. Skipped, not
        // drawn at a fallback anchor: see wireAnchors().
        if (!a) return null;
        return (
          <FlowWireMemo
            key={e.id}
            edgeId={e.id}
            fromNode={e.from}
            d={edgePath(a.p1, a.p2, mode)}
            kind={wireLane(e, nodes)}
            running={running}
            hitW={hitW}
            select={select}
          />
        );
      })}
      <FlowPendingWire tier={tier} mode={mode} />
    </svg>
  );
}
