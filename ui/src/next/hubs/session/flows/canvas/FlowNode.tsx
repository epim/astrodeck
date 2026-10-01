// FlowNode.tsx - one stage on the graph, and one port row on that stage
// (wave R7 parity rows A4 and A5).
//
// THE RE-RENDER DISCIPLINE IS THE POINT OF THIS FILE. A write that concerns one
// stage - selecting it, its progress chip - must re-render ONE stage, not the
// graph. That holds only because the surface is the single subscriber to the
// node ARRAY and passes each node down as a prop, while this card subscribes
// to its own status, its own selectedness and its own loss level with
// selectors that return PRIMITIVES - zustand compares the selector's result
// with Object.is, so selecting stage B cannot wake stage A. Nothing sends a
// per-stage status (there is no `flow.node` topic, and nothing writes
// `flows.statuses`, #464, canvasModel `asNodeStatus`), so every stage reads
// idle; the status selector is as narrow as the others so that a status feed,
// once there is one, wakes one stage. `memo` then stops the surface's own
// re-renders walking every card. Reaching for a `useFlowNode(id)` shape here
// would undo all of it: it returns the node OBJECT, reference-equal only while
// every writer maps just the node it touched.
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
import {
  PORT_ROW_H, nodeLayoutHeight, nodeW, type PortDir,
} from "../../../../../components/flows/geometry";
import type { FlowNodeRec } from "../../../../../components/flows/flowsTypes";
import { progressChip } from "../../../../../components/flows/flowProgress";
import { withLoop, type LaneGraph } from "../../../../../components/flows/panelLane";
import { targetFooter, targetLoops } from "../../../../../components/flows/targetSummary";
import { useStore } from "../../../../../store";
import { nav } from "../../../../router";
import { ActionButton, Mono, Pill, StatusPill } from "../../../../ui";
import {
  NODE_STATUS_TONE, NODE_STATUS_WORD, RIG_VALUE_PREFIX, asNodeStatus, isLoss,
  markTone, markWord, nodeMarkDetail, nodeMarkLevel, portAttr, rigValueFor,
} from "./canvasModel";

// ------------------------------------------------- the card's height, drawn

// THE CARD AS DRAWN IS TALLER THAN ITS FORMULA BOX (#357). The loop arc routes
// under `geometry.nodeLayoutHeight`, a budget that keeps a card's height
// knowable before it renders (spec 1.4: "computed from the card formula and
// never measured from the DOM"). This card's footer grows past that budget:
// its line wraps (10 px mono in 188 - 2 - 20 = 166 px holds 27 characters, and
// the eighth Example's "M31 · rotate · 3x2 · PA 55.0 · 25%" is 34), its marks
// row carries a pill during a run or once a session has counted the block, and
// the selected card shows the 44 px EDIT STAGE. Computed from the stack below,
// an idle card already reaches 10 px past its formula box (its empty marks row
// still takes a gap); the wrapped line adds 15 (25), a pill 26 (36 with one
// line), EDIT STAGE 6 + 44 (60), so either of the last two alone put the
// card's bottom past the arc's 28 px drop and the run passed behind the card.
// The issue's first option: lower the #/next run by what the card can reach,
// computed here from the numbers beside the classes that draw it, so the run
// is still the formula's.
//
// The stack, top to bottom (canvas.css and next.css; Tailwind's preflight
// makes every box border-box and sets `line-height: 1.5`):
const CARD_BORDER_PX = 1; //   .nx-flow-node         border: 1px, top and bottom
const CARD_HEAD_PX = 32; //    .nx-flow-node-head    height: 32px
const PORTS_PAD_PX = 5 + 2; // .nx-flow-node-ports   padding: 5px 0 2px
const FOOT_PAD_PX = 3 + 8; //  .nx-flow-node-foot    padding: 3px 10px 8px
const FOOT_GAP_PX = 6; //      .nx-flow-node-foot    gap: 6px
const FOOT_LINE_PX = 10 * 1.5; // <Mono size={10}> at the preflight's 1.5
const PILL_PX = 26; //         .nx-pill              height: 26px
const BUTTON_PX = 44; //       .nx-btn               height: 44px (EDIT STAGE)

/** The footer the budget allows for: the whole line wrapped once, as the
 *  eighth Example's TARGET wraps. A longer name wraps again and is past it. */
const FOOTER_LINES = 2;

/** The rows' height is the formula's own (`PORT_ROW_H` each, on both), so it
 *  cancels; this is the card with no rows. */
const NO_ROWS = { id: "", type: "" as FlowNodeRec["type"], x: 0, y: 0, params: {} };

/** How far below its formula box (`nodeLayoutHeight`) this card can reach,
 *  with its footer wrapped to two lines, a pill in its marks row and EDIT
 *  STAGE shown: 164 - 63 = 101 px. The #/next wire layer lowers the loop's
 *  run by this (`geometry.lowerLoopRun`), so the run stands `LOOP_ARC_DROP`
 *  below the card as drawn, where the classic canvas's stands below the
 *  formula box.
 *
 *  Past this budget, the run's own 28 px absorbs a third footer line (15)
 *  and the rig line GUIDE and SLEW add (6 + 15 = 21, level with the chip's
 *  top, 7 px above the run). It does not absorb a marks row that wraps to a
 *  second row (6 + 26: two 90 px pills and their gap are 186 px of a 166 px
 *  row, the progress chip beside a mark on a TARGET), a fourth footer line,
 *  or a selected TARGET that offers LOOP PANELS as well as EDIT STAGE
 *  (6 + 44); those still put a card over the run (#554).
 *
 *  Tailwind builds only a class it finds written out, and the look lives in
 *  the stylesheets, so the numbers cannot be read from them here;
 *  canvas/__tests__/loopArcNext.test.tsx computes the mounted card's height
 *  from its classes and holds the run to it, so a class that grows the card
 *  and not this fails there. */
export const CARD_OVERHANG_PX =
  2 * CARD_BORDER_PX + CARD_HEAD_PX + PORTS_PAD_PX
  + FOOT_PAD_PX + FOOTER_LINES * FOOT_LINE_PX + FOOT_GAP_PX + PILL_PX + FOOT_GAP_PX + BUTTON_PX
  - nodeLayoutHeight(NO_ROWS, NODE_DEFS);

// ------------------------------------------------------------ LOOP PANELS

/** The one-tap button that adds a mosaic's loop wire (spec 1.4 "When the wire
 *  is added": "the one-tap LOOP PANELS button on the card, in the stage list").
 *  The phone stage list shows the same words. */
export const LOOP_PANELS_LABEL = "LOOP PANELS";

/** What the button says it does, for its tooltip and accessible name. The
 *  doctor's M3 names the same wire in the same words. */
export const LOOP_PANELS_WHY =
  "Wire the panel lane's last stage 'pass done' to this TARGET's 'next panel', "
  + "so every pass moves to the next panel";

/** `withLoop` needs an id for a wire it would add; the offer only asks
 *  WHETHER it would add one, so the wire is never kept. */
const NO_ID = (): string => "";

/** True when LOOP PANELS would change the block's loop wiring: exactly when
 *  the press, `flowsApplyFraming(id, {}, true)`, would write anything.
 *
 *  ONE RULE FOR THE OFFER AND THE PRESS. `withLoop(true)` hands back the SAME
 *  wires when it changes nothing, and it changes nothing unless the block is
 *  a multi-panel TARGET, owns a lane with a single tail, and that tail has a
 *  "pass done" to give; then it adds the loop wire when none leaves the tail
 *  yet, and MOVES a pass wire stranded mid-lane (M12) to the tail rather than
 *  adding a second (#410). Asked here with the same call, the button is shown
 *  on exactly the blocks a press changes: never on a mosaic that rotates
 *  (`targetLoops`; a second wire is doctor M4's "one is enough"), but on one
 *  whose tail wire stands beside a stale mid-lane one, which the run refuses;
 *  never on a single target, a block that owns no stage, or a lane that
 *  branches or ends on a stage with no "pass done", where a button that did
 *  nothing would be a dead control with a promise on it. The phone stage list
 *  asks the same function.
 *
 *  A boolean, so a card's selector stays exact under Object.is. */
export function offersLoopPanels(graph: LaneGraph, blockId: string): boolean {
  return withLoop(graph, blockId, true, NO_ID) !== graph.edges;
}

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

/** Every prop is stable across a re-render of the card (`port` comes from
 *  `NODE_DEFS`, `nodeId` is a string, the handlers are the card's own props),
 *  so `memo` here is what keeps a selection, a chip or a loss re-render from
 *  walking the whole port list. */
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
  // Every subscription here returns a primitive, so each is exact under
  // Object.is. `nodeMarkLevel` returns a string or null and never a fresh
  // object, so a compile that changes nothing for this type does not wake this
  // card - and `rigValueFor` reaches into `status.providers` for ONE label
  // rather than taking the providers object, which would be a new reference on
  // every poll and would re-render every card on the canvas four times a
  // minute. `progressChip` is the same: the chip STRING, not the progress
  // answer, which is a new object on every re-read.
  const status = useStore((s) => asNodeStatus(s.flows.statuses[node.id]));
  const selected = useStore((s) => s.flows.sel?.kind === "node" && s.flows.sel.id === node.id);
  const mark = useStore((s) => nodeMarkLevel(s.flows.compiled?.unmapped, node.type));
  const markWhy = useStore((s) => nodeMarkDetail(s.flows.compiled?.unmapped, node.type));
  const rigValue = useStore((s) => rigValueFor(node.type, s.status));
  // THE PROGRESS CHIP (#189 S1 item 9), "212/315 subs". The classic card draws
  // it from the same formatter, which decides everything - TARGET only, a
  // session to count from, a saved graph - so the two canvases cannot disagree
  // about when a count is true.
  const chip = useStore((s) => progressChip(s.flows.progress, node.id, s.flows.dirty));
  // A TARGET's footer names whether its panels rotate (#189 S4 item 6, spec
  // 1.2), which is the loop wire in the GRAPH, not a param. A boolean, so a
  // graph write that leaves this block's loop alone - a drag, another stage's
  // param - does not wake this card, and `targetLoops` never calls `sum`.
  const loops = useStore((s) => node.type === "target" && targetLoops(node, s.flows.graph));
  // LOOP PANELS (#189 S4 item 6, spec 1.4): offered only while a press would
  // add the loop wire or move a stranded one to the tail (#410). A boolean,
  // for the same reason as `loops`.
  const offersLoop = useStore((s) => node.type === "target" && offersLoopPanels(s.flows.graph, node.id));
  // A LOSS earns the `!` and the card outline; a note does not. The note says
  // the run uses the rig's own value for these settings, which is not a defect
  // in the graph and must not be painted as one.
  const lost = isLoss(mark);

  // Actions are stable references on the store, so selecting them costs nothing.
  const select = useStore((s) => s.flowsSelect);
  const setEditNode = useStore((s) => s.flowsSetEditNode);
  const applyFraming = useStore((s) => s.flowsApplyFraming);

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
            without opening anything. A TARGET's line is `targetFooter` -
            "M31 · rotate · 3x2 · PA 30.0 · 25%" - WHOLE, because this footer
            wraps; the classic card's one line draws the same line fitted
            (`fittedFooter`, S7 orchestrator ruling 9). It takes the name from
            the same `def.sum`, once. */}
        <Mono size={10} tone="dim" data-testid="flow-node-summary">
          {node.type === "target" ? targetFooter(node, loops) : def.sum(node.params)}
        </Mono>
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
          {chip && (
            // A pill in the marks row, which wraps, rather than a third footer
            // line: the 188 px card already clips the rig line to fit.
            <Pill tone="info" data-testid="flow-node-progress">{chip}</Pill>
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
        {offersLoop && (
          // THE ONE-TAP LOOP (spec 1.4). Amber because the block it sits on is
          // doctor M3's warning, panels shot one after another, and this is
          // the action that answers it. The press is the modal DONE's own
          // write with an empty patch, so it is one graph write and one
          // compile, and the wire leaves the lane's TAIL by `withLoop`, never
          // an earlier stage (M12). In the footer, under the ports, so no wire
          // anchor moves.
          <ActionButton
            kind="warn"
            data-testid="flow-node-loop"
            ariaLabel={`${LOOP_PANELS_LABEL}: ${LOOP_PANELS_WHY}`}
            onPress={() => { void applyFraming(node.id, {}, true); }}
            full
          >
            {LOOP_PANELS_LABEL}
          </ActionButton>
        )}
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
