// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// FlowNodeCard.tsx — one node on the graph.
//
// THIS IS THE COMPONENT THE RE-RENDER DISCIPLINE EXISTS FOR. The README's rule
// is that a write about one node re-renders ONE node, not the canvas, and §B.3
// spells out how that holds: the canvas is the only subscriber to the node
// ARRAY and passes each node down as a prop, while the card subscribes to its
// own status and its own selected-ness with selectors that return a PRIMITIVE.
// zustand compares the selector's result with Object.is, so selecting node B
// cannot wake node A. The README states the rule for a `flow.node` status
// tick, and nothing sends one: no topic carries a stage's status and nothing
// writes `flows.statuses` (#464, `asStatus` below), so the field alone would
// read every LED idle, a live run of this flow included, which is false. The
// card therefore treats an unwritten status as ABSENT while the open flow's
// run is live and draws no LED (`showStatus = written || !runLive`, the phone
// stage list's rule), and says idle only where it is true. Both added
// subscriptions are booleans, so a sequence tick inside the run wakes nobody.
// The status selector is kept as narrow as the rest so that a status feed,
// once there is one, still wakes one card. `memo` below then stops the
// canvas's own re-renders from walking every card.
//
// Never reach for a `useFlowNode(id)` shape here (§B.3 marks it a trap): it
// returns the node object, which is reference-equal only for as long as every
// writer maps just the node it touched. One careless `nodes.map(n => ({...n}))`
// anywhere would re-render every card on every edit, with nothing failing.
//
// Two independent booleans describe where the card is drawn, and they are NOT
// the same question:
//   phone — the DEVICE tier. 150px wide, no footer. True on both phone tabs.
//   auto  — the phone FLOW tab's auto-graph only. No drag, tap-to-wire ports.
// The phone CANVAS tab is `phone && !auto`; README §5 calls it "the full editor".
import { memo } from "react";
import type { CSSProperties, PointerEvent as RPointerEvent, MouseEvent as RMouseEvent } from "react";
import { useStore } from "../../store";
import { Led } from "../ui";
import type { LedState } from "../../types";
import { NODE_DEFS } from "./nodeDefs";
import { nodeW, type PortDir } from "./geometry";
import { nodeLossDetail, nodeLossLevel } from "./flowsTypes";
import type { FlowNodeRec, FlowNodeStatus } from "./flowsTypes";
import { progressChip } from "./flowProgress";
import { flowRunLive, knownSessions } from "./flowRunState";
import { withLoop, type LaneGraph } from "./panelLane";
import { fittedFooter, monoChars, targetLoops } from "./targetSummary";
import FlowPort from "./FlowPort";

/** The footer line's room, in characters, from the classes that draw it:
 *  the card's `border` (1 px a side), the footer's `px-2.5` (10 px a side)
 *  and its `text-[9.5px]` mono. 188 - 2 - 20 = 166 px, 29 characters.
 *
 *  Tailwind only builds a class it finds written out, so the classes cannot
 *  be spelled from these numbers; cardFooterDom.test.tsx computes the budget
 *  from the MOUNTED card's classes instead and holds this constant to it, so
 *  a card that grows its padding or its type and not this fails there rather
 *  than cutting a TARGET line this file thinks fits (S7 orchestrator ruling
 *  9, #357). */
const CARD_BORDER_PX = 1;
const FOOTER_PAD_PX = 10;
const FOOTER_FONT_PX = 9.5;
export const CLASSIC_FOOTER_CHARS =
  monoChars(nodeW("desktop") - 2 * CARD_BORDER_PX - 2 * FOOTER_PAD_PX, FOOTER_FONT_PX);

/** `withLoop` needs an id for a wire it would add; the offer only asks
 *  WHETHER it would add one, so the wire is never kept. */
const NO_ID = (): string => "";

/** True when LOOP PANELS would change the block's loop wiring: exactly when
 *  the press, `flowsApplyFraming(id, {}, true)`, would write anything (spec
 *  1.4 "When the wire is added").
 *
 *  The #/next card and the phone stage list ask the same `withLoop` call
 *  (canvas/FlowNode.tsx `offersLoopPanels`); #/next may not import this
 *  presentation file, nor this file a #/next one, so the one line is written
 *  twice and the RULE lives once, in panelLane.ts. `withLoop(true)` hands back
 *  the SAME wires when it changes nothing, and it changes nothing unless the
 *  block is a multi-panel TARGET that owns a lane with a single tail and the
 *  tail has a "pass done" to give; then it adds the loop wire when none leaves
 *  the tail yet, and MOVES a pass wire stranded mid-lane (M12) to the tail
 *  rather than adding a second (#410). So the button is never on a mosaic
 *  that rotates (`targetLoops`), and it IS on one whose tail wire stands
 *  beside a stale mid-lane one, which the run refuses. */
function offersLoopPanels(graph: LaneGraph, blockId: string): boolean {
  return withLoop(graph, blockId, true, NO_ID) !== graph.edges;
}

/** What LOOP PANELS does, for its tooltip and accessible name: the #/next
 *  card's `LOOP_PANELS_WHY`, word for word, and the wire doctor M3 names. */
const LOOP_PANELS_WHY =
  "Wire the panel lane's last stage 'pass done' to this TARGET's 'next panel', "
  + "so every pass moves to the next panel";

/** §C.5: `idle→"off"`, `busy→"busy"`, `ok→"on"`, `warn→"warn"`, `bad→"bad"`.
 *
 *  The shipped `.led-*` are reused rather than re-cut at the design's 9px
 *  (§G-16 is open on that 2px delta). Reuse is what buys the Do-not list's
 *  "everything meaningful survives prefers-reduced-motion": `.led-on` carries a
 *  checkmark and `.led-off` is a 2px dash, so ok / busy / idle stay tellable
 *  apart with every animation and every colour removed. */
const LED_BY_STATUS: Record<FlowNodeStatus, LedState> = {
  idle: "off", busy: "busy", ok: "on", warn: "warn", bad: "bad",
};

/** `flows.statuses` is a loose `Record<string, string>`, and NOTHING WRITES IT
 *  (#464): no server topic carries a stage's status, and the published
 *  sequence state names the running target, its index, its group and a line
 *  of detail, never the stage - `to_plan` takes each compiled step's
 *  `node_id` off before the engine sees the plan. So this reader returns idle
 *  for every stage until the engine publishes the stage (#464 parts B and C,
 *  deferred for that field, S7 orchestrator ruling 10), and the card must not
 *  PRINT that idle through a live run of its own flow: `showStatus = written
 *  || !runLive` draws no LED then (#464 part A), as the phone stage list draws
 *  no pill. Anything the vocabulary does not know reads as idle - an unknown
 *  word must not blank the LED, which would look like "no node here". */
function asStatus(raw: string | undefined): FlowNodeStatus {
  return raw === "busy" || raw === "ok" || raw === "warn" || raw === "bad"
    ? raw : "idle";
}

export interface FlowNodeCardProps {
  node: FlowNodeRec;
  /** Device tier. 150px card and no footer line on phone (§C.5: "device-based,
   *  not tab-based"). */
  phone?: boolean;
  /** The phone FLOW tab's auto-graph. Suppresses the header drag entirely and
   *  switches the ports to tap-to-wire. */
  auto?: boolean;
  /** World position. Defaults to the node's own coordinates; the auto-graph
   *  passes `computeAutoLayout()`'s instead, which is why this is a prop and not
   *  read off `node` unconditionally. */
  x?: number;
  y?: number;
  /** Header drag, canvas only. The canvas owns the world-space pointer maths,
   *  so it hands down a handler rather than the card computing anything.
   *  Take a STABLE callback (useCallback) — an inline arrow defeats `memo`. */
  onStartDrag?: (nodeId: string, e: RPointerEvent) => void;
  onStartWire?: (nodeId: string, portId: string, e: RPointerEvent) => void;
  onTapPort?: (nodeId: string, portId: string, dir: PortDir) => void;
}

function FlowNodeCard({
  node, phone = false, auto = false, x, y,
  onStartDrag, onStartWire, onTapPort,
}: FlowNodeCardProps) {
  // The two subscriptions this whole file exists to keep narrow. Both return a
  // primitive, so both are exact under Object.is.
  const status = useStore((s) => asStatus(s.flows.statuses[node.id]));
  // WHAT THE LED MAY SAY (#464 part A). Nothing writes `flows.statuses`, so
  // `status` above is idle for every stage, and through a live run of THIS
  // flow that is false. The phone stage list found it first (FlowStagesPhoneSheet
  // `StageRow`: TARGET, FILTER CYCLE and SESSION REPORT all IDLE while the rig
  // shot panel 1-1) and treats the field as absent while its flow runs; this is
  // the same rule, word for word: a stage with no status of its own shows none
  // while the open flow's run is live, and idle stays whenever nothing of this
  // flow runs, where it is true. `flowRunLive` asks the session the sequence
  // state writes against the sessions the slice knows as this flow's, so
  // another flow's run, or none open, leaves idle standing. Both are booleans
  // (Object.is), so a sequence tick inside the run wakes nobody.
  const written = useStore((s) => s.flows.statuses[node.id] !== undefined);
  const runLive = useStore((s) => flowRunLive(knownSessions(s.flows), s.sequence));
  const showStatus = written || !runLive;
  const selected = useStore((s) =>
    s.flows.sel?.kind === "node" && s.flows.sel.id === node.id);
  // THE THIRD, and it obeys the same rule: `nodeLossLevel` returns a string or
  // null, never a fresh object, so a compile that changes nothing for this type
  // does not wake this card. See flowsTypes.ts for why notes are excluded.
  const loss = useStore((s) => nodeLossLevel(s.flows.compiled?.unmapped, node.type));
  const lossWhy = useStore((s) => nodeLossDetail(s.flows.compiled?.unmapped, node.type));
  // THE PROGRESS CHIP (#189 S1 item 9), under the same rule: `progressChip`
  // returns a string or null, so a re-read that leaves this card's count alone,
  // or moves another card's, does not wake this one. It decides everything -
  // TARGET only, a session, a saved graph - so the #/next stage cannot answer
  // differently.
  const chip = useStore((s) => progressChip(s.flows.progress, node.id, s.flows.dirty));
  // THE FOURTH, for a TARGET's footer (#189 S4 item 6, spec 1.2): whether its
  // panels rotate, which is the loop wire in the GRAPH, not a param. A boolean,
  // so a graph write that leaves this block's loop alone - a drag, another
  // card's param - does not wake this card. `targetLoops` never calls `sum`,
  // which the render counters in flowNodeDom.test.tsx count.
  const loops = useStore((s) => node.type === "target" && targetLoops(node, s.flows.graph));
  // THE FIFTH, LOOP PANELS (spec 1.4): offered only while a press would add
  // the loop wire or move a stranded one to the tail (#410), and a boolean
  // for the same reason as `loops`. It rides in
  // the footer, so the phone card, which has none, asks nothing.
  const offersLoop = useStore((s) =>
    !phone && node.type === "target" && offersLoopPanels(s.flows.graph, node.id));

  // Actions are stable references on the store, so selecting them costs nothing.
  const select = useStore((s) => s.flowsSelect);
  const setEditNode = useStore((s) => s.flowsSetEditNode);
  const applyFraming = useStore((s) => s.flowsApplyFraming);

  const def = NODE_DEFS[node.type];
  const w = nodeW(phone ? "phone" : "desktop");
  const px = x ?? node.x;
  const py = y ?? node.y;
  // A TARGET's footer, fitted to the line (ruling 9). Only where there IS a
  // footer, so the phone card, which has none, calls no `sum`.
  const footer = !phone && def && node.type === "target"
    ? fittedFooter(node, loops, CLASSIC_FOOTER_CHARS) : null;

  // SELECTION OUTRANKS STATUS (§C.5) — a selected busy node reads as selected.
  // Both are token-derived: the prototype's literal rgba(0,210,255,·) values are
  // `--accent` at various mixes, and its idle rgba(120,140,200,0.22) is `--line`
  // at 0.18 (a 4% delta the contract authorises; §G-14 is open on it).
  //
  // A LOSS SITS BELOW BOTH, and above idle. Selection is what the operator is
  // doing right now and busy is what the rig is doing right now; a loss is a
  // standing fact about the graph, true whether or not anyone is looking. It
  // still has to be visible at rest, which is the case this was missing: the
  // list existed in the inspector and behind the RUN confirm, and the canvas —
  // the surface the operator is actually reading — said nothing.
  const lossVar = loss === "danger" ? "--bad" : "--warn";
  const borderColor = selected ? "var(--accent)"
    : status === "busy" ? "color-mix(in srgb, var(--accent) 55%, transparent)"
    : loss ? `color-mix(in srgb, var(${lossVar}) 70%, transparent)`
    : "var(--line)";
  const boxShadow = selected
    ? "0 0 16px color-mix(in srgb, var(--accent) 35%, transparent)"
    : status === "busy"
      ? "0 0 14px color-mix(in srgb, var(--accent) 25%, transparent)"
      : loss
        ? `0 0 12px color-mix(in srgb, var(${lossVar}) 22%, transparent), 0 4px 14px rgba(0,0,0,0.45)`
        : "0 4px 14px rgba(0,0,0,0.45)";

  const cardStyle: CSSProperties = {
    width: w,
    transform: `translate3d(${px}px,${py}px,0)`,
    borderColor,
    boxShadow,
  };

  // A missing def means the client's vocabulary and the server's have drifted —
  // the exact risk nodeDefs.ts's header names (§G-4: two hand-maintained copies
  // of one table, no endpoint serving it). TypeScript says it cannot happen;
  // models.py's permissive validation says it can arrive anyway. Drawing the
  // raw type is the least-committal rendering: the node stays visible and
  // countable, and its lack of ports is the honest report of what we know about
  // it. The design specifies no such state.
  if (!def) {
    return (
      <div
        data-node-id={node.id}
        data-node-type={node.type}
        onClick={(e: RMouseEvent) => {
          e.stopPropagation();
          select({ kind: "node", id: node.id });
        }}
        className="absolute left-0 top-0 rounded-xl bg-panel border"
        style={cardStyle}
      >
        <div className="flex items-center gap-[7px] h-8 px-[9px]">
          <span className="flex-1 min-w-0 truncate font-mono text-[9.5px] text-faint">
            {node.type}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div
      data-node-id={node.id}
      data-node-type={node.type}
      onClick={(e: RMouseEvent) => {
        // Without this the click reaches the canvas background, which deselects
        // — i.e. tapping a node would clear the selection it just made.
        e.stopPropagation();
        select({ kind: "node", id: node.id });
      }}
      className="absolute left-0 top-0 rounded-xl bg-panel border backdrop-blur-[8px]"
      style={cardStyle}
    >
      {/* header — the drag handle everywhere except the auto-graph */}
      <div
        onPointerDown={auto ? undefined : (e) => onStartDrag?.(node.id, e)}
        className={`flex items-center gap-[7px] h-8 px-[9px]
                    border-b border-[color-mix(in_srgb,var(--line)_78%,transparent)]
                    ${auto ? "" : "cursor-grab"}`}
      >
        {/* The category swatch. PER-NODE, not per-category: ABORT + PARK is
            --bad while the other ACTIONs are --warn, because the one node that
            parks the mount and warms the camera must not look like a message. */}
        <span
          className="w-[7px] h-[7px] rounded-[2px] flex-none"
          style={{
            background: `var(${def.colorVar})`,
            boxShadow: `0 0 6px var(${def.colorVar})`,
          }}
          aria-hidden
        />
        <span className="flex-1 min-w-0 truncate font-display font-semibold
                         text-[9.5px] tracking-[0.14em] text-ink">
          {def.label}
        </span>
        {/* The status word is the LED's accessible name, so the state is
            readable without the colour and without the silhouette. */}
        {/* NOT HONOURED BY A RUN, on the node it belongs to.
            A GLYPH AND A NAME, not a colour: the night palette collapses warn
            and bad toward coral (FlowInspector's ISSUE_INK says the same), so
            an amber ring alone is indistinguishable from a red one — and from
            nothing at all under prefers-reduced-motion or a colourblind eye.
            `title` carries to_plan's own sentence, which already names the
            setting and what the run does instead. */}
        {loss && (
          <span
            data-node-loss={loss}
            title={lossWhy}
            aria-label={`settings not honoured by a run: ${lossWhy}`}
            className={`flex-none font-display font-semibold text-[10px] leading-none
                        ${loss === "danger" ? "text-bad" : "text-warn"}`}
          >
            !
          </span>
        )}
        {/* NO LED, NOT AN 'OFF' ONE, while `showStatus` is false: an off LED
            says idle in its shape and its name, which is the claim a live
            run of this flow makes false. */}
        {showStatus && <Led state={LED_BY_STATUS[status]} label={status} />}
        <EditGlyph
          size={auto ? 24 : 20}
          glyph={auto ? 13 : 12}
          onEdit={() => {
            // Selecting as well as opening matches the prototype: the sheet and
            // the desktop inspector both render the SELECTED node, so opening
            // the sheet on a node that is not selected would show another one's
            // parameters.
            select({ kind: "node", id: node.id });
            setEditNode(node.id);
          }}
        />
      </div>

      {/* ports — every input, then every output. Never interleaved (§C.5), which
          is also what geometry.portPos() assumes when it places an output's row
          at `ins.length + idx`. */}
      <div className={phone ? "pt-[5px] pb-1" : "pt-[5px] pb-0.5"}>
        {def.ins.map((p) => (
          <FlowPort key={p.id} nodeId={node.id} port={p} dir="in"
                    auto={auto} onStartWire={onStartWire} onTapPort={onTapPort} />
        ))}
        {def.outs.map((p) => (
          <FlowPort key={p.id} nodeId={node.id} port={p} dir="out"
                    auto={auto} onStartWire={onStartWire} onTapPort={onTapPort} />
        ))}
      </div>

      {/* footer — the params in one line, hidden on phone in BOTH tabs. It is
          computed from the CURRENT params, so an edit shows on the card without
          opening anything. The progress chip rides in it and is hidden on
          phone with it: the 150px card has no footer line, and the
          auto-graph's layout (autoLayout.ts, +8) budgets room for none. */}
      {!phone && (
        <div className="px-2.5 pt-[3px] pb-2 font-mono text-[9.5px] text-faint">
          {/* A TARGET's line is the #/next card's `targetFooter` - "M31 ·
              rotate · 3x2 · PA 30.0 · 25%" - FITTED to this one line's 29
              characters (`fittedFooter`, S7 orchestrator ruling 9, #357):
              the overlap goes first, then the name is shortened with an
              ellipsis, and the loop word and the angle are never cut. The
              whole line is the tooltip whenever anything was left out. It
              takes the name from the same `def.sum`, once. `truncate` stays
              behind it for a line nothing can fit. Every other type keeps
              its `sum`. */}
          <div data-flow-summary className="truncate"
               title={footer && footer.line !== footer.full ? footer.full : undefined}>
            {footer ? footer.line : def.sum(node.params)}
          </div>
          {chip && (
            <div data-flow-progress className="truncate text-dim">{chip}</div>
          )}
          {/* THE ONE-TAP LOOP (spec 1.4). Amber because the block is doctor
              M3's warning, panels shot one after another, and this answers
              it. The press is the modal DONE's own write with an empty patch:
              one graph write, one compile, and the wire leaves the lane's
              TAIL by `withLoop`, never an earlier stage (M12). Under the
              ports, so no wire anchor moves. */}
          {offersLoop && (
            <button
              type="button"
              data-flows-loop
              title={LOOP_PANELS_WHY}
              aria-label={`LOOP PANELS: ${LOOP_PANELS_WHY}`}
              onClick={() => { void applyFraming(node.id, {}, true); }}
              className="mt-1.5 w-full rounded-[6px] border border-dashed border-warn/70
                         bg-transparent py-[3px] cursor-pointer
                         font-display font-semibold text-[9.5px] tracking-[0.14em] text-warn
                         hover:bg-warn/10"
            >
              LOOP PANELS
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** The ✎.
 *
 *  Not an `IconButton`: the shipped `IconName` union has no pencil, folder,
 *  magnifier or chevron, and §G-9 — which is OPEN — is the question of whether
 *  to nominate substitutes from the 42 or author new glyphs. Picking one here
 *  would close it. README §Assets calls the prototype's pencil "a stand-in for
 *  its edit glyph", so the stand-in is what renders until that is ruled on; the
 *  path is the prototype's, verbatim.
 *
 *  `data-flows-edit` is load-bearing: flows_visual_check.py:438 hovers the node
 *  and clicks this control to open the edit sheet, and reports "the edit sheet
 *  has no opener the harness can find" if it is missing.
 *
 *  ⚠ 20px (24px on the auto-graph) is below the 44px touch floor. §G-20 covers
 *  exactly this control among the sub-44px graph affordances and is open — the
 *  design's sizes are reproduced rather than bought back InfoDot-style, because
 *  either choice would settle it. */
function EditGlyph({ size, glyph, onEdit }: {
  size: number; glyph: number; onEdit: () => void;
}) {
  return (
    <button
      type="button"
      data-flows-edit
      aria-label="Edit parameters"
      title="Edit parameters"
      onClick={(e: RMouseEvent) => { e.stopPropagation(); onEdit(); }}
      // THE ✎ NEVER DRAGS. Without this the pointerdown bubbles to the header,
      // which starts a node drag, and the click that follows lands after the
      // card has moved under the finger.
      onPointerDown={(e: RPointerEvent) => e.stopPropagation()}
      className="flex-none flex items-center justify-center rounded-[5px] p-0
                 border-0 bg-transparent text-faint cursor-pointer
                 hover:text-accent hover:bg-accent/10"
      style={{ width: size, height: size }}
    >
      <svg
        width={glyph} height={glyph} viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth={1.9}
        strokeLinecap="round" strokeLinejoin="round" aria-hidden
      >
        <path d="M17 3a2.8 2.8 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
      </svg>
    </button>
  );
}

export default memo(FlowNodeCard);
