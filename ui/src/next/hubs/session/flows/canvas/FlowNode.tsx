// FlowNode.tsx - one stage on the graph, and one port row on that stage
// (wave R7 parity rows A4 and A5).
//
// THE RE-RENDER DISCIPLINE IS THE POINT OF THIS FILE. A `flow.node` status tick
// must re-render ONE stage, not the graph. That holds only because the surface
// is the single subscriber to the node ARRAY and passes each node down as a
// prop, while this card subscribes to its own status, its own selectedness and
// its own loss level with selectors that return PRIMITIVES - zustand compares
// the selector's result with Object.is, so a status write for stage B cannot
// wake stage A. `memo` then stops the surface's own re-renders walking every
// card. Reaching for a `useFlowNode(id)` shape here would undo all of it: it
// returns the node OBJECT, reference-equal only while every writer maps just the
// node it touched.
//
// THE CARD IS NOT `<Card>`, AND THAT IS DELIBERATE. It wears the design's card
// vocabulary - 14 px radius, `--bg-panel`, `--line`, the accent/purple tone
// border - but `geometry.portPos()` places every wire anchor from a fixed
// stack (1 px border + 32 px header + 5 px port-list padding + 20 px rows), and
// `Card`'s own `12px 14px` padding cannot be reconciled with that. A card that
// looked right and moved every wire 12 px off its dot would be the worse
// outcome, so the look is reproduced in `canvas.css` and the arithmetic stays
// the shared one.
//
// TOUCH TARGETS. The header pencil is 28 px - a mouse affordance on a surface
// that only exists at 768 px and up. The 44 px door to the same sheet is the
// EDIT STAGE button the card reveals when it is selected, and selecting is a
// tap anywhere on the card. On a phone there is no canvas at all: the stage list
// in `FlowStagesPhoneSheet` is the editing path and every row there is a full
// 44 px target.

import { memo, type JSX, type MouseEvent as RMouseEvent, type PointerEvent as RPointerEvent } from "react";

import { NODE_DEFS, type PortDef } from "../../../../../components/flows/nodeDefs";
import { PORT_ROW_H, nodeW, type PortDir } from "../../../../../components/flows/geometry";
import type { FlowNodeRec } from "../../../../../components/flows/flowsTypes";
import { useStore } from "../../../../../store";
import { nav } from "../../../../router";
import { ActionButton, Mono, Pill, StatusPill } from "../../../../ui";
import {
  NODE_STATUS_TONE, NODE_STATUS_WORD, RIG_VALUE_PREFIX, asNodeStatus, isLoss,
  markTone, markWord, nodeMarkDetail, nodeMarkLevel, portAttr, rigValueFor,
} from "./canvasModel";

// ------------------------------------------------------------------- a port

export interface FlowPortRowProps {
  /** The owning stage's id. The whole node is NOT passed: a port needs only the
   *  id, and taking the object would break `memo` every time the stage moves. */
  nodeId: string;
  port: PortDef;
  dir: PortDir;
  /** Drag-to-wire, OUTPUTS only - there is no reverse drag, so an input span
   *  gets no `onPointerDown` at all. The surface owns the drop resolution and
   *  the refusal toast; this row only reports the grab. */
  onStartWire?: (nodeId: string, portId: string, e: RPointerEvent) => void;
  /** Tap-to-wire, BOTH directions - an output arms, an input completes. Used by
   *  the phone stage list, where there is no drag. */
  onTapPort?: (nodeId: string, portId: string, dir: PortDir) => void;
  /** Phone-sized tap targets and a visible port name. */
  big?: boolean;
}

function FlowPortRowBase({ nodeId, port, dir, onStartWire, onTapPort, big = false }: FlowPortRowProps): JSX.Element {
  // Whether THIS port carries a wire. A boolean, so a graph edit re-renders only
  // the ports whose wiredness actually changed.
  const wired = useStore((s) => s.flows.graph.edges.some((e) => (dir === "in"
    ? e.to === nodeId && e.toPort === port.id
    : e.from === nodeId && e.fromPort === port.id)));

  // Armed = this output is the source half of a tap-to-wire in flight.
  const armed = useStore((s) => {
    const tw = s.flows.tapWire;
    return dir === "out" && !!tw && tw.from === nodeId && tw.fromPort === port.id;
  });

  const attr = portAttr(nodeId, port.id, dir);
  const tap = onTapPort
    ? (e: RMouseEvent) => { e.stopPropagation(); onTapPort(nodeId, port.id, dir); }
    : undefined;

  // The dot and its hit box. `data-port` is not decoration: it is the only
  // channel through which the surface learns which port a pointerup landed on
  // (`document.elementFromPoint(...).closest("[data-port]")`), which is what
  // makes dragging a wire work under a finger at any zoom.
  const hit = (
    <span
      data-port={attr}
      data-port-dir={dir}
      className="nx-flow-port-hit"
      data-big={big ? "true" : undefined}
      onPointerDown={dir === "out" && onStartWire
        ? (e) => onStartWire(nodeId, port.id, e)
        : undefined}
    >
      <span
        className="nx-flow-port-dot"
        data-kind={port.kind}
        // Filled when wired OR armed. A SHAPE change (hollow to solid), not a
        // colour swap, so it survives the night palette collapsing the lanes.
        data-live={wired || armed ? "true" : "false"}
        data-armed={armed ? "true" : undefined}
      />
    </span>
  );

  const label = <span className="nx-flow-port-label">{port.label}</span>;

  // Tap-to-wire makes the whole row one button, so the target is the row and
  // not the 9 px dot. The canvas drags instead and needs no button at all - a
  // tab stop per port on a twenty-stage graph would bury every other control.
  if (tap) {
    return (
      <button
        type="button"
        className="nx-flow-port nx-flow-port-btn"
        data-dir={dir}
        aria-label={`${dir === "out" ? "Wire from" : "Wire to"} ${port.label}`}
        aria-pressed={armed}
        onClick={tap}
      >
        {hit}
        {label}
      </button>
    );
  }

  return (
    <div
      className="nx-flow-port"
      data-dir={dir}
      // Height is PORT_ROW_H, never a literal: `geometry.portPos()` computes
      // every wire anchor from that same constant, and if the two drift, every
      // wire on the canvas detaches from its dot with nothing failing.
      style={{ height: PORT_ROW_H }}
    >
      {hit}
      {label}
    </div>
  );
}

/** Every prop is stable across a status tick (`port` comes from `NODE_DEFS`,
 *  `nodeId` is a string, the handlers are the card's own props), so `memo` here
 *  is what keeps a status re-render from walking the whole port list. */
export const FlowPortRow = memo(FlowPortRowBase);

// -------------------------------------------------------------------- a node

export interface FlowNodeCardProps {
  node: FlowNodeRec;
  /** Card width comes from the tier and every OUTPUT anchor is `node.x + nodeW`,
   *  so a wrong tier detaches every outgoing wire from its dot by 38 px. */
  phone?: boolean;
  /** Header drag. The surface owns the world-space pointer maths, so it hands a
   *  handler down rather than the card computing anything. Must be a STABLE
   *  callback - an inline arrow defeats `memo`. */
  onStartDrag?: (nodeId: string, e: RPointerEvent) => void;
  onStartWire?: (nodeId: string, portId: string, e: RPointerEvent) => void;
  onTapPort?: (nodeId: string, portId: string, dir: PortDir) => void;
}

function FlowNodeCardBase({ node, phone = false, onStartDrag, onStartWire, onTapPort }: FlowNodeCardProps): JSX.Element {
  // Five subscriptions, all returning a primitive, so all five are exact under
  // Object.is. `nodeMarkLevel` returns a string or null and never a fresh
  // object, so a compile that changes nothing for this type does not wake this
  // card - and `rigValueFor` reaches into `status.providers` for ONE label
  // rather than taking the providers object, which would be a new reference on
  // every poll and would re-render every card on the canvas four times a
  // minute.
  const status = useStore((s) => asNodeStatus(s.flows.statuses[node.id]));
  const selected = useStore((s) => s.flows.sel?.kind === "node" && s.flows.sel.id === node.id);
  const mark = useStore((s) => nodeMarkLevel(s.flows.compiled?.unmapped, node.type));
  const markWhy = useStore((s) => nodeMarkDetail(s.flows.compiled?.unmapped, node.type));
  const rigValue = useStore((s) => rigValueFor(node.type, s.status));
  // A LOSS earns the `!` and the card outline; a note does not. The note says
  // the run uses the rig's own value for these settings, which is not a defect
  // in the graph and must not be painted as one.
  const lost = isLoss(mark);

  // Actions are stable references on the store, so selecting them costs nothing.
  const select = useStore((s) => s.flowsSelect);
  const setEditNode = useStore((s) => s.flowsSetEditNode);

  const def = NODE_DEFS[node.type];
  const w = nodeW(phone ? "phone" : "desktop");
  const word = NODE_STATUS_WORD[status];

  const onCardClick = (e: RMouseEvent): void => {
    // Without this the click reaches the surface background, which deselects -
    // tapping a stage would clear the selection it just made.
    e.stopPropagation();
    select({ kind: "node", id: node.id });
  };
  const openEditor = (): void => {
    // Selecting as well as opening: the inspector column and the node sheet both
    // render the SELECTED stage, so opening the sheet on a stage that is not
    // selected would show another one's parameters.
    select({ kind: "node", id: node.id });
    setEditNode(node.id);
    // BOTH, and neither is redundant. `editNode` is the store seam the node
    // sheet reads and clears; the route is what puts it on the glass. Setting
    // only the flag would leave this pencil looking live on a tablet with no
    // sheet in front of it, which is the dead-control-with-a-promise shape.
    nav.sheet("flowNode", { node: node.id });
  };

  // A missing def means the client's vocabulary and the server's have drifted.
  // TypeScript says it cannot happen; the server's permissive param validation
  // says it can arrive anyway. Drawing the raw type is the least-committal
  // rendering: the stage stays visible and countable, and its lack of ports is
  // the honest report of what we know about it.
  if (!def) {
    return (
      <div
        data-testid="flow-node"
        data-node-id={node.id}
        data-node-type={node.type}
        className="nx-flow-node"
        data-unknown="true"
        style={{ width: w, transform: `translate3d(${node.x}px,${node.y}px,0)` }}
        onClick={onCardClick}
      >
        <div className="nx-flow-node-head">
          <span className="nx-flow-node-kind">{node.type}</span>
        </div>
        <div className="nx-flow-node-foot">
          <Mono size={10} tone="warn">this build has no vocabulary for this stage</Mono>
        </div>
      </div>
    );
  }

  return (
    <div
      data-testid="flow-node"
      data-node-id={node.id}
      data-node-type={node.type}
      className="nx-flow-node"
      // SELECTION OUTRANKS STATUS, and a loss sits below both and above idle:
      // selection is what the operator is doing right now, busy is what the rig
      // is doing right now, and a loss is a standing fact about the graph, true
      // whether or not anyone is looking.
      //
      // `data-loss` carries the LEVEL, `note` included, so the attribute still
      // reports what the compile said - but `canvas.css` paints a border for
      // the two LOSS levels only. A note is not a defect in the graph.
      data-selected={selected ? "true" : "false"}
      data-status={status}
      data-loss={mark ?? undefined}
      style={{ width: w, transform: `translate3d(${node.x}px,${node.y}px,0)` }}
      onClick={onCardClick}
    >
      <div
        className="nx-flow-node-head"
        onPointerDown={onStartDrag ? (e) => onStartDrag(node.id, e) : undefined}
        data-draggable={onStartDrag ? "true" : undefined}
      >
        {/* The category swatch is PER-NODE, not per-category: ABORT + PARK is
            `--bad` while the other actions are `--warn`, because the one stage
            that parks the mount and warms the camera must not look like a
            message. */}
        <span
          className="nx-flow-node-swatch"
          style={{ background: `var(${def.colorVar})`, boxShadow: `0 0 6px var(${def.colorVar})` }}
          aria-hidden="true"
        />
        <span className="nx-flow-node-kind">{def.label}</span>
        {/* THE `!` IS FOR A LOSS ONLY. It used to fire on every `nodes.<type>`
            entry, which was every standard stage the compiler does not read -
            ten of them on a clean flow, five amber marks on the canvas and an
            amber outline on two cards, all of it saying "you have a problem"
            about a rig doing exactly what it was asked. */}
        {mark && lost && (
          <span
            className="nx-flow-node-loss"
            data-level={mark}
            data-node-loss={mark}
            title={markWhy}
            aria-label={`settings not honoured by a run: ${markWhy}`}
          >
            !
          </span>
        )}
        {/* The status WORD is the dot's accessible name AND its tooltip, so the
            state is readable without the colour and without the silhouette. */}
        <span
          className="nx-flow-node-led"
          data-status={status}
          data-pulse={status === "busy" ? "true" : undefined}
          role="img"
          title={word}
          aria-label={`stage ${word}`}
        />
        <button
          type="button"
          data-flows-edit
          data-testid="flow-node-edit"
          className="nx-flow-node-edit"
          aria-label={`Edit ${def.label} parameters`}
          title="Edit parameters"
          onClick={(e: RMouseEvent) => { e.stopPropagation(); openEditor(); }}
          // The pencil never drags: without this the pointerdown bubbles to the
          // header, which starts a stage drag, and the click that follows lands
          // after the card has moved out from under the pointer.
          onPointerDown={(e: RPointerEvent) => e.stopPropagation()}
        >
          {/* Drawn here rather than taken from `next/icons.tsx`: that set has no
              pencil, and the nearest member is a gear, which would say
              "settings for the whole rig" on a control that edits one stage. */}
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
            strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M17 3a2.8 2.8 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
          </svg>
        </button>
      </div>

      {/* Ports: every input, then every output, never interleaved - which is
          also what `geometry.portPos()` assumes when it places an output's row
          at `ins.length + idx`. */}
      <div className="nx-flow-node-ports">
        {def.ins.map((p) => (
          <FlowPortRow key={`in-${p.id}`} nodeId={node.id} port={p} dir="in"
            onStartWire={onStartWire} onTapPort={onTapPort} />
        ))}
        {def.outs.map((p) => (
          <FlowPortRow key={`out-${p.id}`} nodeId={node.id} port={p} dir="out"
            onStartWire={onStartWire} onTapPort={onTapPort} />
        ))}
      </div>

      <div className="nx-flow-node-foot">
        {/* Computed from the CURRENT params, so an edit shows on the card
            without opening anything. */}
        <Mono size={10} tone="dim">{def.sum(node.params)}</Mono>
        {/* And what the rig will really use, for the stages whose stored params
            name a provider it overrides: GUIDE ships "PHD2" and SLEW ships
            "ASTAP" in the vocabulary's own defaults, so a rig that guides
            natively and solves with its own engine has been reading someone
            else's names off its own canvas. Labelled, so which value is which
            needs no guessing; absent when the rig has not said. */}
        {rigValue && (
          <Mono size={10} tone="dim" className="nx-flow-node-rig" data-testid="flow-node-rig">
            {RIG_VALUE_PREFIX}{rigValue}
          </Mono>
        )}
        <div className="nx-flow-node-marks">
          {status !== "idle" && (
            <StatusPill text={word} tone={NODE_STATUS_TONE[status]} pulse={status === "busy"} />
          )}
          {mark && (
            // A word, not a ring. The night palette collapses warn and bad
            // toward coral, so a coloured mark alone is indistinguishable from
            // a red one - and from nothing at all under a colourblind eye. At
            // note level the word is FROM THE RIG and the tone is dim: it is
            // secondary information, not a finding.
            <Pill tone={markTone(mark)} ariaLabel={markWhy} data-testid="flow-node-mark">
              {markWord(mark)}
            </Pill>
          )}
        </div>
        {selected && (
          // The 44 px door to the same sheet the 28 px pencil opens. Revealed on
          // selection so every stage does not carry a button, and selection is a
          // tap anywhere on the card.
          <ActionButton
            kind="secondary"
            data-testid="flow-node-edit-cta"
            onPress={openEditor}
            full
          >
            EDIT STAGE
          </ActionButton>
        )}
      </div>
    </div>
  );
}

export const FlowNodeCard = memo(FlowNodeCardBase);

export default FlowNodeCard;
