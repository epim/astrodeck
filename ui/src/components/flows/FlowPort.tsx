// FlowPort.tsx — one port row on a node card: hit box, dot, label.
//
// This is the element the wire system resolves against. §D.4: a drop is decided
// by `document.elementFromPoint(...).closest("[data-port]")` on the POINTERUP
// coordinates, which is what makes dragging a wire work under a finger. So the
// `data-port` value is not decoration — it is the only channel through which the
// canvas learns which port a pointer landed on, and its three pipe-delimited
// fields are unescaped (§D.4), which is why `flowsSlice.nextNodeId()` mints ids
// that cannot contain a `|`.
//
// TWO GEOMETRIES, keyed off `auto` — NOT off the device (§C.5's table). "canvas"
// covers desktop, tablet AND the phone CANVAS tab, which README §5 calls "the
// full editor"; "auto" is only the phone FLOW tab's auto-graph, where wiring is
// tap-then-tap instead of drag and the targets grow accordingly.
//
// The two visual facts a port carries about the graph — wired, armed — are read
// here with their own narrow selectors rather than passed down. Each returns a
// BOOLEAN, so zustand's Object.is comparison is exact: wiring port A cannot
// re-render port B, and a node-status tick re-renders no port at all.
import { memo } from "react";
import type { PointerEvent as RPointerEvent, MouseEvent as RMouseEvent } from "react";
import { useStore } from "../../store";
import { PORT_ROW_H, type PortDir } from "./geometry";
import type { PortDef } from "./nodeDefs";

// §C.5's tier table. `hit` is the invisible square that catches the pointer;
// `dot` is what the eye sees; the row overhangs the card edge by half the hit
// box so the target straddles the border the wire attaches to.
const HIT_CANVAS = 20;
const HIT_AUTO = 26;
const DOT_CANVAS = 9;
const DOT_AUTO = 10;

/** Lane colour. The GRAMMAR, not decoration: a flow port carries the single run
 *  cursor, an event port fires any number of times, and nodes.py runs the two
 *  through different machinery. Wiring is only ever kind-to-kind (§D.4 rule 4). */
const portColorVar = (kind: PortDef["kind"]): string =>
  kind === "flow" ? "--accent" : "--warn";

export interface FlowPortProps {
  /** The owning node's id. The whole node is NOT passed: a port needs only the
   *  id, and taking the object would break `memo` every time the node moves. */
  nodeId: string;
  port: PortDef;
  dir: PortDir;
  /** The phone FLOW tab's auto-graph: tap-to-wire, 26px hits, pointer cursor.
   *  False on every pan/zoom surface, including the phone CANVAS tab. */
  auto?: boolean;
  /** Drag-to-wire, canvas only, OUTPUTS only — there is no reverse drag (§D.4
   *  rule 2), so input spans get no `onPointerDown` at all. The canvas owns the
   *  drop resolution and the refusal toasts; this row only reports the grab. */
  onStartWire?: (nodeId: string, portId: string, e: RPointerEvent) => void;
  /** Tap-to-wire, auto only, BOTH directions — an output arms, an input
   *  completes. The caller owns the verbatim refusal/confirm toasts (§C.15). */
  onTapPort?: (nodeId: string, portId: string, dir: PortDir) => void;
}

function FlowPort({ nodeId, port, dir, auto = false, onStartWire, onTapPort }: FlowPortProps) {
  // Whether THIS port has a wire on it. A boolean, so a graph edit re-renders
  // only the ports whose wiredness actually changed.
  const wired = useStore((s) =>
    s.flows.graph.edges.some((e) =>
      dir === "in"
        ? e.to === nodeId && e.toPort === port.id
        : e.from === nodeId && e.fromPort === port.id));

  // Armed = this output is the source half of a tap-to-wire in flight. Only
  // reachable in the auto-graph; the canvas drags instead of arming.
  const armed = useStore((s) => {
    const tw = s.flows.tapWire;
    return auto && dir === "out" && !!tw
      && tw.from === nodeId && tw.fromPort === port.id;
  });

  const hit = auto ? HIT_AUTO : HIT_CANVAS;
  const dot = auto ? DOT_AUTO : DOT_CANVAS;
  const colorVar = portColorVar(port.kind);

  // ⚠ The 3px inner ring is CYAN even on an amber event port — only the outer
  // glow takes the lane colour (prototype line 1410). §G-15 is open on whether
  // that is intended; reproduced verbatim rather than resolved here.
  const ring = armed
    ? `0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent), 0 0 10px var(${colorVar})`
    : undefined;

  // Half the hit box hangs outside the card, so the target straddles the border
  // the wire anchors to. Written as a branch rather than a computed key so the
  // style object stays a plain CSSProperties for the type checker.
  const overhang = -(hit / 2);

  return (
    <div
      className="flex items-center gap-[7px]"
      // Height is PORT_ROW_H, not a `h-5` literal, because geometry.portPos()
      // computes every wire anchor from that same constant. If the two drift,
      // every wire on the canvas detaches from its dot with nothing failing.
      style={dir === "out"
        ? { height: PORT_ROW_H, flexDirection: "row-reverse" }
        : { height: PORT_ROW_H }}
    >
      <span
        data-port={`${nodeId}|${port.id}|${dir}`}
        data-port-dir={dir}
        onPointerDown={dir === "out" && !auto
          ? (e) => onStartWire?.(nodeId, port.id, e)
          : undefined}
        onClick={auto
          ? (e: RMouseEvent) => { e.stopPropagation(); onTapPort?.(nodeId, port.id, dir); }
          : undefined}
        className="flex-none flex items-center justify-center"
        style={{
          width: hit,
          height: hit,
          cursor: auto ? "pointer" : "crosshair",
          ...(dir === "in" ? { marginLeft: overhang } : { marginRight: overhang }),
        }}
      >
        <span
          className="rounded-full"
          style={{
            width: dot,
            height: dot,
            border: `1.5px solid var(${colorVar})`,
            // Filled when wired OR armed — the fill is the "this port is live"
            // cue, and it is a SHAPE change (hollow → solid), not a colour swap,
            // so it survives the night palette collapsing the lanes together.
            background: wired || armed ? `var(${colorVar})` : "var(--bg)",
            boxShadow: ring,
          }}
        />
      </span>
      <span className="font-mono text-[9.5px] text-dim">{port.label}</span>
    </div>
  );
}

// Every prop is stable across a status tick (`port` comes from NODE_DEFS,
// `nodeId` is a string, the handlers are the card's own props), so memo here is
// what keeps a node-status re-render from walking the whole port list.
export default memo(FlowPort);
