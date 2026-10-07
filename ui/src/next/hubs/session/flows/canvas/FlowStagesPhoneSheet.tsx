// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// FlowStagesPhoneSheet.tsx - the phone's whole relationship with a flow's
// graph (wave R7 parity row A24, plan section 4).
//
// WHAT THIS REPLACES. The legacy phone editor is three tabs - FLOW (an
// auto-laid-out graph), CANVAS (a pannable surface at 390 px) and MONITOR - plus
// the armed tap-to-wire bar. Wave 1 shipped the Flows list on the phone and left
// all four on the floor: `OPEN` said "the flows canvas opens on a tablet or
// desktop" and the phone had no way to look INSIDE a flow at all. This sheet is
// where they land, with the pannable canvas deliberately NOT reproduced: a
// 390 px pan/zoom surface is the one part of that editor a thumb cannot use, and
// the stage list says everything the graph said.
//
// So nothing is lost:
//   * FLOW tab      -> the stage list, in the same reading order (`flowOrder`),
//                      each row carrying its status word and its "the compiler
//                      drops this" note.
//   * + ADD STAGE   -> the same button the legacy graph pinned at the end of the
//                      scroll (`FlowPhoneGraph.tsx:92-102`), opening the palette
//                      as a sheet.
//   * tap-to-wire   -> the port buttons on each row, with the armed hint bar as
//                      the sheet's sticky footer, AND a removable row per wire,
//                      which is what the legacy editor's select-the-edge plus
//                      `FlowWireDelete` did with a hit target the phone cannot
//                      reproduce (there is no wire on screen to tap).
//   * the edit door -> a row press selects the stage and opens the `flowNode`
//                      sheet at depth 1.
//   * MONITOR tab   -> the four readouts, the log tail and RUN / STOP.
//   * the way out   -> BACK saves, exactly as the legacy `< LIBRARY` did.
//
// THREE THINGS THE WHOLE-BRANCH REVIEW FOUND MISSING HERE, and they are one
// thing: this sheet could create but not finish. It could wire but not unwire;
// it had no way to add a stage at all (the zero-stage state was one sentence
// pointing at a canvas a phone will never draw); and nothing it did was ever
// saved, because no exit called `flowsCloseEditor`. An editor that cannot undo
// and cannot store is worse than a read-only list.
//
// EVERY ROUTE OUT OF HERE CARRIES `?open=`. `nav.sheet(name, params)` rebuilds
// the whole hash from the params it is HANDED (`router.ts`), so
// `nav.sheet("flowNode", { node })` dropped the flow from the URL: reload or
// share it and the stage editor opens on a flow nobody named. Both navigations
// pass `{ open, node }` / `{ open }`.
//
// WHERE THE READOUTS COME FROM (#189 S5, U-07). While the run the rig is on is
// this flow's (`flowRunLive`: the session the sequence state writes is the one
// the progress route counts), the four readouts and the header's live line are
// the sequence state's (`useFlowRunReadouts`): STATE the engine's own state,
// ETA its `progress.eta_s` with "hops not yet costed" under it while that
// clock prices hops it has not measured, STAGE the target, or for a mosaic the
// panel and pass (`M31 2-3 · pass 2`; `M31 · waiting for the meridian` across
// a meridian wait, with nothing site-derived), FRAMES its `frames_done /
// frames_total`. For any other run, or none, they are the store's own
// `run.phase`, `run.etaS`, `run.curStage` and `run.frames`, which nothing
// server-side writes (there is no `flow.node` topic, no `flow.log` topic and
// no run-state GET), so they read IDLE / - / - / 0: another flow's clock on
// this flow's monitor would describe the wrong ledger. WHAT IT MAY NOT INVENT
// is unchanged: a client-side countdown from an assumed total, or a stage name
// inferred from the graph, would look identical to one the rig computed.
//
// RUN SAYS WHAT IT WILL DO (#189 S5, spec 5.9): `CONTINUE M31 MOSAIC (night 3,
// 412/1890 subs)` over a dormant session, the one copy every RUN surface
// prints, with the flow's name the only part a 390 px footer may cut, and
// START OVER under it behind a confirm.
//
// THE PANEL LOOP, WHICH THE PHONE CANNOT DRAW AS A WIRE (#189 S4 item 6, spec
// 1.4 "How it is drawn"). On a tablet a mosaic's loop is the dashed back-arc
// from its lane's tail to the TARGET's "next panel". Here it is a dashed amber
// rail from the TARGET's row to the TAIL's row, the lane's rows indented
// inside it, labelled EVERY PASS: NEXT PANEL; with the wire absent, a LOOP
// PANELS button takes its place. Both read the graph through panelLane.ts, the
// mirror of the compile's own lane rules, so the rail is drawn exactly when
// the run rotates panels and around exactly the stages it shoots per panel.
//
// THE COUNTS LINE (Revision 2 ruling 2, S4 orchestrator ruling 8). A flow saved
// before new blocks counted accepted subs still counts every sub taken until
// it is next saved, and the phone says so in one persistent line, read from
// the graph on every render (`countsNotice`), so the save that switches the
// counts takes it down without a reopen.
//
// THE REPLAY LINE (#473, S7 orchestrator ruling 1). While the session
// CONTINUE would carry on is armed and the flow was saved after the version
// it froze, a line under the counts line says dusk will replay that version
// (`replayNotice`), and RUN's first tap arms it as `CONFIRM CONTINUE (night
// n, banked/total subs)` (`runArm`, #474), the toolbar's own arm.

import {
  useCallback, useEffect, useMemo, useState, type JSX,
} from "react";

import { flowOrder } from "../../../../../components/flows/autoLayout";
import { NODE_DEFS } from "../../../../../components/flows/nodeDefs";
import type { FlowEdgeRec, FlowNodeRec } from "../../../../../components/flows/flowsTypes";
import {
  START_OVER_LABEL, useFlowRunControls, useFlowRunReadouts,
} from "../../../../../components/flows/flowRunControls";
import { countsNotice } from "../../../../../components/flows/countsNotice";
import { replayNotice } from "../../../../../components/flows/replayNotice";
import { laneTail, loopSource, panelLane } from "../../../../../components/flows/panelLane";
import { LOOP_CHIP_WORDS, targetLoops } from "../../../../../components/flows/targetSummary";
import { accessPhrase, useCapability } from "../../../../../lib/caps";
import { useStore } from "../../../../../store";
import { nav } from "../../../../router";
import { useBreakpoint } from "../../../../breakpoint";
import { NxIcon } from "../../../../icons";
import {
  ActionButton, BannerCard, Card, EmptyCard, Label, ListRow, LockNote, Mono, Pill,
  ReadoutGrid, ReadoutTile, Sheet, StatusPill,
} from "../../../../ui";
import type { SheetProps } from "../../../sheets";
import { PLAN_EDITOR_PHONE_REASON } from "../../sheets/planEditor";
import {
  FLOW_OPEN_FAILED, flowOpenFailure, leaveFlowEditor, libraryErrorNow, libraryHasLoaded,
  openFlowById, waitForLibrary,
} from "../openFlow";
import { FlowPortRow, LOOP_PANELS_LABEL, offersLoopPanels } from "./FlowNode";
import { RunCopyWords, runArm } from "./FlowCanvasToolbar";
import { emptyLogText } from "./FlowLogStrip";
import { FlowTapWireBar } from "./FlowTapWireBar";
import {
  ADD_STAGE_LABEL, LOG_TONE, NODE_STATUS_TONE, NODE_STATUS_WORD,
  NO_WIRES_TEXT, RIG_VALUE_PREFIX, asNodeStatus, formatEta, framesWord, logTail,
  logTime, markTone, markWord, nodeMarkDetail, nodeMarkLevel, rigValueFor,
  saveLockReason, saveStateTone, saveStateWord, stageWord,
  tonightLockReason, wireRemoveLabel, wireRowLabel,
} from "./canvasModel";
import "./canvas.css";

/** The sheet's own name in the route and the registry. */
export const FLOW_STAGES_SHEET = "flowStages";

/** Shown while no flow is open yet - the record arrives one tick after the
 *  sheet, and a blank title reads as a broken screen. */
export const FLOW_STAGES_UNTITLED = "FLOW";

/** The waiting card while the open this sheet asked for (`?open=`) is still
 *  out, or landed on some other flow (#553). The open record is another
 *  flow's, or none, so nothing of it - not its stages, its readouts or its
 *  RUN - may be drawn: the defect this closes is exactly a phone tap on flow
 *  B's row starting flow A because a failed read of B left A's stage list
 *  and RUN live under B's route. Mirrors the pattern already built twice,
 *  `FlowFrameSheet.tsx` (`FRAME_FLOW_LOADING`/`FRAME_OTHER_FLOW`, #384) and
 *  the Sky flow card (`sky/sheets/flow.tsx`, #499).
 *
 *  #592 class (WP-61 new defect, W3 integration): this used to be "This
 *  flow's stages show once it has loaded", which told the reader nothing the
 *  title "OPENING THIS FLOW" had not already - the exact defect #592 fixed in
 *  `sky/sheets/flow.tsx`'s `FLOW_CARD_LOADING`. In that shape, this now names
 *  the cause the title does not: the open is a request this sheet made, and
 *  it has not come back yet. */
export const FLOW_STAGES_LOADING =
  "The open this sheet asked for has not answered yet, so there is nothing "
  + "here to show.";

/** The waiting card's reason for a route with no `?open=` at all: nothing was
 *  asked for, so whatever is open in the store belongs to some other visit
 *  and must not be shown or run here either. */
export const FLOW_STAGES_NO_ID =
  "This link names no flow, so there is nothing here to show or run.";

/** RUN's and SAVE's locked reason once the open has answered and it was not
 *  this flow, or while nothing was asked for. One sentence for both controls:
 *  a sheet that has not loaded this flow has nothing of it to run OR to
 *  save - the graph on screen, if any, is another flow's. */
export const FLOW_STAGES_NOT_OPENED_REASON =
  "That flow did not open, so there is nothing here to run or save.";

/** RUN's and SAVE's locked reason while the open is still out.
 *
 *  #592 class (WP-61 new defect, W3 integration): this used to be "Loading
 *  this flow…", which repeated the waiting card's own loading state and
 *  never said what RUN or SAVE was missing. In the shape of
 *  {@link FLOW_STAGES_NOT_OPENED_REASON}, it now does. */
const FLOW_STAGES_LOADING_REASON =
  "This flow has not loaded yet, so there is nothing here to run or save.";

/** The stage list's reading order.
 *
 *  `flowOrder` is the shared topological order over FLOW-lane edges only - the
 *  same order the legacy phone graph reads top to bottom. It deliberately omits
 *  pure event stages (CLOUD WATCH, NOTIFY, SAFETY MONITOR) and any stage inside
 *  a flow-edge cycle, so those are appended in graph order afterwards. Dropping
 *  them would be exactly the kind of silent loss this wave exists to stop: a
 *  SAFETY MONITOR that is not in the list is a safety rule the operator cannot
 *  see on the only screen their phone offers. */
export function stageOrder(
  nodes: readonly FlowNodeRec[],
  edges: readonly FlowEdgeRec[],
): FlowNodeRec[] {
  const ordered = flowOrder({ nodes: [...nodes], edges: [...edges] }, NODE_DEFS);
  const seen = new Set(ordered.map((n) => n.id));
  return [...ordered, ...nodes.filter((n) => !seen.has(n.id))];
}

/** The rail's label (spec 1.4): the canvas chip's words, in the list's
 *  capitals, so the two views of one wire say the same thing. */
export const LOOP_RAIL_LABEL = LOOP_CHIP_WORDS.toUpperCase();

/** One entry of the drawn list: a plain row; a looped mosaic's TARGET with its
 *  lane on the rail, ending at `tail`; or the LOOP PANELS button under a
 *  mosaic's row, with the stage its wire would leave. */
export type StageBlock =
  | { kind: "row"; node: FlowNodeRec }
  | { kind: "rail"; target: FlowNodeRec; lane: FlowNodeRec[]; tail: FlowNodeRec }
  | { kind: "offer"; target: FlowNodeRec; tail: FlowNodeRec };

/** `stageOrder`, with each looped mosaic's lane gathered onto its rail.
 *
 *  A RAIL IS DRAWN for a TARGET whose panels rotate (`targetLoops`: more than
 *  one panel and a loop wire from its lane's tail), and it holds that block's
 *  panel lane (`panelLane`) and nothing else, ending at the lane's TAIL
 *  (`laneTail`), the stage the loop wire leaves. The lane is moved up under
 *  its TARGET rather than drawn where `stageOrder` put it: that order is
 *  breadth-first, so a stage fed by the TARGET's own parent (a DOME opened by
 *  the same DUSK) can sit between the TARGET and its lane, and a rail drawn
 *  down the list would claim that stage is shot per panel. Every other row
 *  keeps its place. A block with a loop wire has a single tail, so its lane is
 *  one chain and `panelLane` lists it nearest first, the tail last.
 *
 *  THE BUTTON IS OFFERED where the rail would start, under the TARGET's row,
 *  exactly when a press would add the wire (`offersLoopPanels`); its `tail` is
 *  the stage that wire would leave (`loopSource`), which the button names.
 *
 *  With no multi-panel TARGET this is `stageOrder`, row for row. */
export function stageBlocks(
  nodes: readonly FlowNodeRec[],
  edges: readonly FlowEdgeRec[],
): StageBlock[] {
  const g = { nodes, edges };
  const order = stageOrder(nodes, edges);
  const rails = new Map<string, { lane: FlowNodeRec[]; tail: FlowNodeRec }>();
  const railed = new Set<string>();
  for (const n of order) {
    if (!targetLoops(n, g)) continue;
    const tail = laneTail(g, n.id);
    if (!tail) continue;
    const lane = panelLane(g, n.id);
    rails.set(n.id, { lane, tail });
    for (const m of lane) railed.add(m.id);
  }
  const out: StageBlock[] = [];
  for (const n of order) {
    if (railed.has(n.id)) continue;
    const rail = rails.get(n.id);
    if (rail) {
      out.push({ kind: "rail", target: n, ...rail });
      continue;
    }
    out.push({ kind: "row", node: n });
    const tail = n.type === "target" && offersLoopPanels(g, n.id) ? loopSource(g, n.id) : null;
    if (tail) out.push({ kind: "offer", target: n, tail });
  }
  return out;
}

/** The rail: a dashed amber line down the left of the lane, from under the
 *  TARGET's row to the bottom of the TAIL's row, with the lane's rows indented
 *  inside it. Inline, like every other one-off in this sheet's rows, because
 *  canvas.css is the canvas's stylesheet; `--warn` is the amber token, and a
 *  DASH, not the hue, is what the night palette leaves telling it apart. */
const RAIL_BODY_STYLE = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  marginLeft: 14,
  paddingLeft: 12,
  borderLeft: "2px dashed var(--warn)",
} as const;

/** The TARGET's row and the rail under it, spaced as the list spaces its
 *  rows (`.nx-flow-stages`, 8 px). */
const RAIL_STYLE = { display: "flex", flexDirection: "column", gap: 8 } as const;

/** The LOOP PANELS button sits where the rail would start: under the
 *  TARGET's row, at the rail's inset. */
const OFFER_STYLE = { marginLeft: 14 } as const;

/** THE FOOTER'S COLUMN FILLS THE FOOTER (#189 S5, found by the real-page
 *  probe, routes_s5_s6.json). `.nx-sheet-foot` is a flex ROW, and this column
 *  is its one child; with no `flex` it took its content's width, so every
 *  `full` button under it was only as wide as the widest label: RUN and SAVE
 *  92 px of the 358 the footer holds, and with CONTINUE's copy the whole
 *  column as wide as that one line, off the right edge of the screen for a
 *  long flow name, cutting the parenthetical the copy promises never to cut
 *  (runCopy.ts). `min-width: 0` lets it be narrower than that line, so the
 *  name, the one part that may give up width, is what gives it up. */
const FOOT_STYLE = { flex: "1 1 auto", minWidth: 0 } as const;

/** NOTHING UNDER THE LIST SHRINKS (#189 S5, the same probe).
 *  `.nx-sheet-body` is a flex column that scrolls, and a flex item there
 *  shrinks, when the column overflows, down to its automatic minimum: under a
 *  long stage list + ADD STAGE, a 52 px button whose minimum is its label's
 *  line, was drawn 20 px tall, and the LOG, a scroller whose minimum is 0
 *  (overflow is not visible), 17 px, its lines cut in half. The list scrolls
 *  instead, which is what the column is for. The LOG takes `flex-shrink: 0`;
 *  ADD STAGE sits in a plain block, which is then the flex item, and whose
 *  minimum is its content, the whole button. The same trap the Dial met on
 *  the mount sheet (next.css `.nx-dial`). */
const NO_SHRINK = { flexShrink: 0 } as const;

// -------------------------------------------------------------- a stage row

function StageRow({ node, nodes, edges, openId, runLive }: {
  node: FlowNodeRec;
  /** The whole graph, from the sheet's two subscriptions. Passed rather than
   *  read here so a wire added anywhere re-renders one list, not sixteen - and
   *  so a wire row names the stage at its far end without a second lookup. */
  nodes: readonly FlowNodeRec[];
  edges: readonly FlowEdgeRec[];
  /** The flow id the route names, carried into every sheet this row opens. */
  openId: string;
  /** This flow's run is live on the rig (`useFlowRunReadouts().fed`), read
   *  once by the sheet and handed down, as the graph is. */
  runLive: boolean;
}): JSX.Element {
  const status = useStore((s) => asNodeStatus(s.flows.statuses[node.id]));
  // WHAT THE STAGE'S PILL MAY SAY (#189 S5, found by the real-page probe,
  // routes_s5_s6.json). Nothing writes `flows.statuses`: no topic carries a
  // stage's status (see the header on `flow.node`), so every stage reads the
  // default, IDLE, and through a live run of THIS flow that is false. The
  // probe saw TARGET, FILTER CYCLE and SESSION REPORT all IDLE while the rig
  // shot panel 1-1. So while this flow runs, a stage with no status of its
  // own shows none, and the readouts above say what the run is doing; IDLE
  // stays whenever nothing of this flow runs, where it is true.
  const written = useStore((s) => s.flows.statuses[node.id] !== undefined);
  const showStatus = written || !runLive;
  // The same three primitives the canvas card subscribes to, and the same
  // words: a stage that reads FROM THE RIG on a tablet must not read something
  // else in the list a phone opens.
  const mark = useStore((s) => nodeMarkLevel(s.flows.compiled?.unmapped, node.type));
  const markWhy = useStore((s) => nodeMarkDetail(s.flows.compiled?.unmapped, node.type));
  const rigValue = useStore((s) => rigValueFor(node.type, s.status));
  const selected = useStore((s) => s.flows.sel?.kind === "node" && s.flows.sel.id === node.id);
  const select = useStore((s) => s.flowsSelect);
  const setEditNode = useStore((s) => s.flowsSetEditNode);
  const tapPort = useStore((s) => s.flowsTapPort);
  const deleteSel = useStore((s) => s.flowsDeleteSel);

  const def = NODE_DEFS[node.type];
  const word = NODE_STATUS_WORD[status];

  const open = (): void => {
    // Selecting as well as opening: the node sheet renders the SELECTED stage,
    // so opening it on a stage that is not selected would show another one's
    // parameters.
    select({ kind: "node", id: node.id });
    setEditNode(node.id);
    // `open` travels with `node`: `nav.sheet` builds the whole hash from what it
    // is handed, so passing only the node id would leave the URL claiming the
    // FLOWS LIST is showing - copy it, share it or reload it and the flow is
    // gone.
    nav.sheet("flowNode", openId ? { open: openId, node: node.id } : { node: node.id });
  };

  // THE WIRES THIS STAGE FEEDS. Outgoing only, so each wire is listed once and
  // reads as a sentence about the stage it is under: "window -> TARGET · arm".
  // The legacy phone editor selected the wire on the drawn graph and offered a
  // 26 px cross on it; there is no drawn graph here, so the row IS the wire.
  const outgoing = edges.filter((e) => e.from === node.id);

  /** `flowsDeleteSel` deletes what is SELECTED, so selecting the edge is part of
   *  the removal rather than a side effect of it. Both are plain `set` calls and
   *  zustand applies them synchronously, so the delete sees the selection this
   *  line just made. The selection is cleared by `flowsDeleteSel` itself. */
  const removeWire = (edgeId: string): void => {
    select({ kind: "edge", id: edgeId });
    deleteSel();
  };

  if (!def) {
    return (
      <Card data-testid="flow-stage-row" padding={12}>
        <Label size={10}>{node.type}</Label>
        <Mono size={10.5} tone="warn">this build has no vocabulary for this stage</Mono>
      </Card>
    );
  }

  return (
    <Card
      data-testid="flow-stage-row"
      tone={selected ? "accent" : "default"}
      padding={0}
      className="nx-flow-stage"
    >
      <ListRow
        icon={<NxIcon name="flows" size={16} />}
        title={def.label}
        sub={def.sum(node.params)}
        right={showStatus
          ? <StatusPill text={word} tone={NODE_STATUS_TONE[status]} pulse={status === "busy"} />
          : undefined}
        chevron
        onPress={open}
      />
      {/* The rig's own value for a stage whose stored params name a provider it
          overrides, in the same words the canvas card uses. */}
      {rigValue && (
        <div className="nx-flow-stage-rig">
          <Mono size={10.5} tone="dim" data-testid="flow-stage-rig">
            {RIG_VALUE_PREFIX}{rigValue}
          </Mono>
        </div>
      )}
      {mark && (
        <div className="nx-flow-stage-loss">
          <Pill tone={markTone(mark)} data-testid="flow-stage-mark">{markWord(mark)}</Pill>
          <Mono size={10.5} tone="dim">{markWhy}</Mono>
        </div>
      )}
      <div className="nx-flow-stage-ports">
        {def.ins.map((p) => (
          <FlowPortRow key={`in-${p.id}`} nodeId={node.id} port={p} dir="in" big
            onTapPort={(n, id, dir) => tapPort(n, id, dir)} />
        ))}
        {def.outs.map((p) => (
          <FlowPortRow key={`out-${p.id}`} nodeId={node.id} port={p} dir="out" big
            onTapPort={(n, id, dir) => tapPort(n, id, dir)} />
        ))}
      </div>

      {/* Only for a stage that HAS outputs. A sink - ABORT + PARK, NOTIFY - has
          none, and telling its reader to "tap an output port" would send them
          looking for a control that is not on the card. */}
      {def.outs.length > 0 && (
        <div className="nx-flow-stage-wires" data-testid={`flow-stage-wires-${node.id}`}>
          <Label size={10}>WIRES OUT</Label>
          {outgoing.length === 0 ? (
            <Mono size={10.5} tone="dim">{NO_WIRES_TEXT}</Mono>
          ) : (
            outgoing.map((e) => (
              <WireRow key={e.id} edge={e} nodes={nodes} onRemove={removeWire} />
            ))
          )}
        </div>
      )}
    </Card>
  );
}

// --------------------------------------------------------------- a wire row

/** One outgoing wire, with the control that removes it.
 *
 *  Renders NOTHING when either end cannot be resolved - `wireRowLabel` answers
 *  null for a node or port the vocabulary has since dropped, which is the same
 *  rule `wireAnchors` applies before drawing a wire on the canvas. A row reading
 *  "undefined -> undefined" would offer to cut a wire nobody can see, and the
 *  graph would still hold the edge afterwards. */
function WireRow({ edge, nodes, onRemove }: {
  edge: FlowEdgeRec;
  nodes: readonly FlowNodeRec[];
  onRemove: (edgeId: string) => void;
}): JSX.Element | null {
  const row = wireRowLabel(edge, nodes);
  if (!row) return null;
  return (
    <div className="nx-flow-wire-row" data-testid={`flow-wire-row-${edge.id}`}>
      <Mono size={10.5} tone="dim">{`${row.out} -> ${row.into}`}</Mono>
      <ActionButton
        kind="ghost"
        data-testid={`flow-wire-remove-${edge.id}`}
        glyph={<NxIcon name="x" size={13} />}
        ariaLabel={wireRemoveLabel(row)}
        onPress={() => onRemove(edge.id)}
      >
        REMOVE
      </ActionButton>
    </div>
  );
}

// ------------------------------------------------------------------- sheet

export function FlowStagesPhoneSheet({ params }: SheetProps): JSX.Element {
  const want = params.open ?? "";

  const record = useStore((s) => s.flows.record);
  const nodes = useStore((s) => s.flows.graph.nodes);
  const edges = useStore((s) => s.flows.graph.edges);
  // The four readouts, from the sequence state while the rig's run is this
  // flow's and from `flows.run` otherwise: one shallow-compared record of
  // primitives, so a publish that moves none of them re-renders nothing.
  const readouts = useFlowRunReadouts();
  const logs = useStore((s) => s.flows.logs);
  const dirty = useStore((s) => s.flows.dirty);
  // True while a PUT is out (the autosave's, or SAVE's own): the pill says
  // SAVING and SAVE does not send the same graph beside it, as on the toolbar.
  const saving = useStore((s) => s.flows.saving);
  const readonly = useStore((s) => s.flows.record?.readonly ?? false);
  const save = useStore((s) => s.flowsSave);
  const applyFraming = useStore((s) => s.flowsApplyFraming);
  // THE COUNTS LINE, read from the graph on every store write: a string or
  // null, so this subscription is exact under Object.is. Not captured when the
  // sheet opens: the save that switches the counts writes the switch into this
  // graph (`acceptCounts`) and does not reopen the flow, so a line read once
  // would go on promising a switch the save already made.
  const countsLine = useStore((s) => countsNotice(s.flows.graph, s.flows.countsNote));
  // THE REPLAY LINE (#473, S7 orchestrator ruling 1), from the one function
  // the toolbar and the classic editor read, as a string or null.
  const replayLine = useStore((s) => replayNotice(s.flows.progress, s.flows.record));

  const canViewSiteDerived = useCapability("view.site_derived");
  const phone = useBreakpoint() === "phone";
  const {
    running, reason: hookRunReason, explain, act, copy, startOver, stopsOnPress,
  } = useFlowRunControls();

  const openId = record?.id ?? null;

  /** The last open this sheet asked for that did not land: which flow, the
   *  record that was open when it was asked (the attempt's identity - a new
   *  attempt starts exactly when `want` or `openId` changes, so a failure
   *  recorded under an earlier one no longer matches), and the reason. */
  const [failure, setFailure] = useState<
    { id: string; from: string | null; reason: string } | null
  >(null);

  // THROUGH `openFlowById`, NOT A BARE `flowsOpen` (#553). `flowsOpen`
  // swallows its own failure and leaves the record that was open before in
  // place, so a phone tap on flow B's row while flow A is what the store
  // still holds used to draw A's stage list, with RUN live, under a route
  // that named B - a press on B's row starting A on the rig. `openFlowById`
  // says whether `want` actually landed, and `mine` below is the one gate
  // every read of `record`, `nodes` and `edges` goes through: while it is
  // false the sheet shows a waiting card instead (`waiting`), never a stage
  // of whatever the store happens to hold. A late answer to an earlier
  // attempt (another id, or the record changed under it) is dropped.
  // ONE RETRY FOR THE COLD-LOAD RACE (W7 follow-on, #658 class). A fresh tab
  // landing straight on the phone sheet's own `?open=<id>` route mounts this
  // effect the SAME render `FlowsScreen`'s own `flowsLoadLibrary()` starts the
  // flows-list GET - the same race `FlowsCanvasHost.tsx` was fixed against,
  // this sheet just had the pre-fix shape still: firing straight to
  // `setFailure` below on a server whose single-flow read loses that race,
  // turning a timing loss into a permanent "That flow did not open". A
  // failure that happened before the list had loaded is held back - the
  // waiting card below already covers it, since nothing has visibly changed -
  // and retried once the list lands (or this sheet gives up waiting for it,
  // `waitForLibrary`, shared with the host through `openFlow.ts` rather than
  // copied). Only a SECOND failure is reported.
  useEffect(() => {
    // Idempotent: re-opening the flow already loaded would discard an unsaved
    // edit and re-run the compile for nothing.
    if (!want || openId === want) return;
    let current = true;
    // Set only while a wait is actually in flight, so the cleanup below has
    // nothing to release the rest of the time.
    let cancelWait: (() => void) | null = null;
    const before = libraryErrorNow();
    void (async () => {
      const landed = await openFlowById(want);
      if (!current || landed) return;
      if (!libraryHasLoaded()) {
        const wait = waitForLibrary();
        cancelWait = wait.cancel;
        await wait.promise;
        cancelWait = null;
        if (!current) return;
        const retried = await openFlowById(want);
        if (!current || retried) return;
      }
      setFailure({ id: want, from: openId, reason: flowOpenFailure(before) });
    })();
    return () => { current = false; cancelWait?.(); };
  }, [want, openId]);

  // THE ONE TEST every read of the open record below is gated on. A route
  // with no `?open=` at all names no flow, so it is never "mine" whatever the
  // store happens to hold - showing it would be the same defect under a
  // different cause (nothing asked for, rather than an open that failed).
  const mine = want !== "" && openId === want;
  const failed = failure !== null && failure.id === want && failure.from === openId
    ? failure.reason : null;
  /** What the sheet shows INSTEAD of the open record, and RUN/SAVE's locked
   *  reason, while that record is not the flow this sheet names; null once it
   *  is. Three things to say: the route names no flow, the open answered and
   *  it was not this flow (with the reason the store wrote, or the mismatch
   *  sentence), or the open is still out. */
  const waiting: { title: string; hint: string } | null = mine ? null
    : want === ""
      ? { title: "NO FLOW", hint: FLOW_STAGES_NO_ID }
      : failed !== null
        ? { title: FLOW_OPEN_FAILED.toUpperCase(), hint: failed }
        : { title: "OPENING THIS FLOW", hint: FLOW_STAGES_LOADING };

  // The lane, the log and every readout below are the open record's and
  // nothing else: while `waiting` is up they would be another flow's (or
  // none), so they are computed from empty inputs rather than read at all.
  const blocks = useMemo(() => (mine ? stageBlocks(nodes, edges) : []), [mine, nodes, edges]);
  const lines = useMemo(() => (mine ? logTail(logs) : []), [mine, logs]);

  const tonightReason = canViewSiteDerived
    ? null
    : tonightLockReason(accessPhrase("view.site_derived"));
  const planReason = phone ? PLAN_EDITOR_PHONE_REASON : null;

  // Same three decisions as the canvas toolbar, from the same pure helpers, so
  // the phone and the tablet cannot end up disagreeing about whether a flow is
  // safe to start. The rig's own refusal outranks the unsaved one, and both
  // outrank a wait: a flow that has not loaded has nothing on screen to run
  // or save, whatever `dirty` says about the flow left over from before.
  const waitingReason = waiting === null ? null
    : failed !== null ? FLOW_STAGES_NOT_OPENED_REASON : FLOW_STAGES_LOADING_REASON;
  // The hook's reason carries the one unsaved-edit refusal that survives the
  // autosave (an edited Example, never stored); an ordinary edited flow is saved
  // by `flowsRun` before it posts (#688), so it is not locked here, as on the
  // toolbar.
  const runReason = waitingReason ?? hookRunReason;
  const saveReason = waitingReason ?? saveLockReason(dirty, readonly, saving);
  const stateWord = saveStateWord(dirty, readonly, saving);

  /** BACK saves, exactly as the legacy `< LIBRARY` button did.
   *
   *  POP FIRST, THEN CLOSE. `leaveFlowEditor` clears `flows.record`, and this
   *  sheet's own effect re-opens whatever `?open=` still names the moment
   *  `openId` goes null - so closing while the sheet is still mounted would
   *  fetch the flow straight back and the save would look like it did nothing.
   *  The close runs on the store, so unmounting this component does not cancel
   *  the PUT. */
  const back = useCallback((): void => {
    nav.back();
    void leaveFlowEditor();
  }, []);

  /** `?open=` travels, or the palette's own BACK would land on a stage list
   *  whose flow the URL no longer names. */
  const addStage = useCallback((): void => {
    nav.sheet("flowPalette", want ? { open: want } : {});
  }, [want]);

  return (
    <Sheet
      data-testid="session-flow-stages"
      title={mine ? record?.name ?? FLOW_STAGES_UNTITLED : FLOW_STAGES_UNTITLED}
      sub={mine ? `${nodes.length} stages · ${edges.length} wires` : undefined}
      icon={<NxIcon name="flows" size={18} />}
      // While waiting the store's readouts, if any, are another flow's run -
      // not this route's - so the live line is blank rather than borrowed.
      live={mine
        ? <Mono size={10.5} tone="dim">{`${readouts.state} · ETA ${formatEta(readouts.etaS)}`}</Mono>
        : undefined}
      right={(
        <span data-testid="flow-stages-save-state">
          <Pill tone={saveStateTone(dirty, readonly, saving)} ariaLabel={`This flow: ${stateWord}`}>
            {stateWord}
          </Pill>
        </span>
      )}
      onBack={back}
      footer={(
        <div className="nx-flow-stages-foot" style={FOOT_STYLE}>
          <FlowTapWireBar />
          {/* SAVE above RUN. It was because RUN was refused until SAVE had been
              pressed; since #688 RUN saves first and the flow saves itself, so
              SAVE is "save now", and its place is the same for the reason that
              remains: a full-width pair would put the two most consequential
              buttons on the phone under one thumb sweep, so SAVE is the
              smaller of the two and RUN keeps the 56 px primary. */}
          <ActionButton
            kind="secondary"
            size="lg"
            full
            data-testid="flow-stages-save"
            glyph={<NxIcon name="check" size={15} />}
            lockedReason={saveReason}
            onExplain={explain}
            onPress={() => { void save(); }}
          >
            SAVE
          </ActionButton>
          <ActionButton
            kind={running ? "danger" : "primary"}
            size="xl"
            full
            data-testid="flow-stages-run"
            glyph={<NxIcon name={running ? "stop" : "play"} size={16} />}
            lockedReason={runReason}
            onExplain={explain}
            // The toolbar's own arm (#474): CONFIRM and the copy's verb with
            // its night and counts, and none for a press that really stops
            // (`stopsOnPress`, #647) - a plain single tap, since emergency
            // motion stops are never armed, held or confirmed. Gated on
            // `stopsOnPress`, not `copy.verb`: see `runArm`'s own doc.
            arm={runArm(copy, stopsOnPress)}
            onPress={act}
          >
            <RunCopyWords copy={copy} />
          </ActionButton>
          {/* START OVER, under CONTINUE and only under it (spec 5.9): the
              secondary weight and size, so it is not RUN's twin under the
              same thumb, and the confirm `startOver` asks is its guard. */}
          {copy.verb === "CONTINUE" && (
            <ActionButton
              kind="secondary"
              full
              data-testid="flow-stages-start-over"
              lockedReason={runReason}
              onExplain={explain}
              onPress={startOver}
            >
              {START_OVER_LABEL}
            </ActionButton>
          )}
          {/* `LockNote` prints "Read-only - <reason>", which is the right frame
              for a capability or a missing camera and the WRONG one for an
              unsaved edit: this flow is not read-only, it is ahead of the rig.
              So the rig's refusals keep the note and ours gets its own line. */}
          <LockNote reason={hookRunReason} />
          {!hookRunReason && runReason && (
            <span data-testid="flow-stages-run-reason">
              <Mono size={10.5} tone="warn">{runReason}</Mono>
            </span>
          )}
        </div>
      )}
    >
      {waiting !== null ? (
        // NOTHING OF THE OPEN RECORD: not its readouts, stages, log or ADD
        // STAGE, every one of which is another flow's (or none) while this is
        // up (#553).
        <EmptyCard data-testid="flow-stages-waiting" title={waiting.title} hint={waiting.hint} />
      ) : (
        <>
          <ReadoutGrid cols={4} data-testid="flow-stages-monitor">
            <ReadoutTile label="STATE" value={readouts.state} data-testid="flow-stages-state" />
            <ReadoutTile
              label="ETA"
              value={formatEta(readouts.etaS)}
              sub={readouts.etaNote ?? undefined}
              data-testid="flow-stages-eta"
            />
            <ReadoutTile label="STAGE" value={stageWord(readouts.stage)} data-testid="flow-stages-stage" />
            <ReadoutTile
              label="FRAMES"
              value={framesWord(readouts.frames, readouts.frameGoal)}
              data-testid="flow-stages-frames"
            />
          </ReadoutGrid>

          {/* Ruling 2's line, for as long as the graph counts every sub taken.
              No dismiss: it is a standing fact about the flow, and saving is what
              ends it. */}
          {countsLine && (
            <BannerCard tone="info" text={countsLine} data-testid="flow-stages-counts" />
          )}
          {/* The replay line: dusk will replay the version the armed session
              froze unless CONTINUE applies the saved edits. A warning, so it
              takes the warn tone; no dismiss, since it stays true until
              CONTINUE applies the edits or the session is disarmed. */}
          {replayLine && (
            <BannerCard tone="warn" text={replayLine} data-testid="flow-stages-replay" />
          )}

          <Label size={10}>STAGES</Label>
          {blocks.length === 0 ? (
            <EmptyCard
              data-testid="flow-stages-empty"
              title="THIS FLOW HAS NO STAGES"
              hint="Nothing will happen when this flow runs. Add the first stage below."
            />
          ) : (
            <div className="nx-flow-stages">
              {blocks.map((b) => {
                if (b.kind === "row") {
                  return <StageRow key={b.node.id} node={b.node} nodes={nodes} edges={edges} openId={want} runLive={readouts.fed} />;
                }
                if (b.kind === "rail") {
                  return (
                    <div
                      key={`rail-${b.target.id}`}
                      style={RAIL_STYLE}
                      data-testid="flow-stage-rail"
                      data-rail-from={b.target.id}
                      data-rail-to={b.tail.id}
                    >
                      <StageRow node={b.target} nodes={nodes} edges={edges} openId={want} runLive={readouts.fed} />
                      <div
                        role="group"
                        aria-label={`${LOOP_RAIL_LABEL}: the stages shot on each panel`}
                        data-testid="flow-stage-rail-body"
                        style={RAIL_BODY_STYLE}
                      >
                        <span data-testid="flow-stage-rail-label">
                          <Mono size={10} tone="warn">{LOOP_RAIL_LABEL}</Mono>
                        </span>
                        {b.lane.map((n) => (
                          <StageRow key={n.id} node={n} nodes={nodes} edges={edges} openId={want} runLive={readouts.fed} />
                        ))}
                      </div>
                    </div>
                  );
                }
                // THE WIRE IS ABSENT, so the button stands where the rail would.
                // The press is the modal DONE's own write with an empty patch: one
                // graph write, one compile, and `withLoop` takes the wire from the
                // lane's TAIL, never an earlier stage (M12).
                const from = NODE_DEFS[b.tail.type]?.label ?? b.tail.type;
                return (
                  <div key={`loop-${b.target.id}`} style={OFFER_STYLE}>
                    <ActionButton
                      kind="warn"
                      full
                      data-testid={`flow-stage-loop-${b.target.id}`}
                      ariaLabel={`${LOOP_PANELS_LABEL}: wire ${from} 'pass done' to this TARGET's 'next panel', so every pass moves to the next panel`}
                      onPress={() => { void applyFraming(b.target.id, {}, true); }}
                    >
                      {LOOP_PANELS_LABEL}
                    </ActionButton>
                  </div>
                );
              })}
            </div>
          )}

          {/* PINNED AT THE END OF THE LIST, and rendered at zero stages too - which
              is the state that most needs it. The legacy phone graph put the same
              control in the same place (`FlowPhoneGraph.tsx:92-102`) and wave 1
              dropped it, leaving a screen that could open a flow, wire it and run it
              but never add anything to it. The block around it is what keeps it
              52 px tall under a long list (see NO_SHRINK). */}
          <div>
            <ActionButton
              kind="purple"
              size="lg"
              full
              data-testid="flow-stages-add"
              onPress={addStage}
            >
              {ADD_STAGE_LABEL}
            </ActionButton>
          </div>

          <Label size={10}>LOG</Label>
          {/* An empty log says why it is empty, never "Idle" (#529): the STATE
              tile says what the run is doing, and under a live run of this flow
              (`readouts.fed`, the tiles' own rule) "Idle" contradicted it. */}
          <div role="log" className="nx-flow-log-body" data-testid="flow-stages-log" style={NO_SHRINK}>
            {lines.length === 0 ? (
              <Mono size={10.5} tone="dim">{emptyLogText(readouts.fed)}</Mono>
            ) : (
              lines.map((l) => (
                <div key={l.id} className="nx-flow-log-line">
                  <Mono size={10} tone="dim">{logTime(l.ts)}</Mono>
                  <Mono size={10.5} tone={LOG_TONE[l.tone]}>{l.msg}</Mono>
                </div>
              ))
            )}
          </div>
        </>
      )}

      <ListRow
        data-testid="flow-stages-tonight"
        icon={<NxIcon name="clock" size={16} />}
        title="TONIGHT"
        // The sheet's four tabs, in 40 characters: `.nx-row-sub` is one line
        // with an ellipsis, and "timeline, brief, compiled plan and campaign
        // ledger" ran 32 px past it at 390 px, ending "campaign..." (#189 S5,
        // the real-page probe).
        sub="timeline, brief, compiled plan, campaign"
        chevron
        lockedReason={tonightReason}
        onExplain={explain}
        onPress={() => nav.sheet("flowTonight", want ? { open: want } : {})}
      />
      <ListRow
        data-testid="flow-stages-plan"
        icon={<NxIcon name="session" size={16} />}
        title="PLAN EDITOR"
        sub="guiding, count mode and per-target flip"
        chevron
        lockedReason={planReason}
        onExplain={explain}
        onPress={() => nav.sheet("planEditor", want ? { open: want } : {})}
      />
      <LockNote reason={planReason} />
    </Sheet>
  );
}

export default FlowStagesPhoneSheet;
