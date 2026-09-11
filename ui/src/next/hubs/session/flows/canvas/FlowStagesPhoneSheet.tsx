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
// WHAT IT MAY NOT INVENT. `run.phase`, `run.etaS`, `run.curStage` and
// `run.frames` are written by nothing server-side: there is no `flow.node`
// topic, no `flow.log` topic and no run-state GET. This renders the store's
// values honestly and they will read IDLE / - / - / 0 during a real run. A
// client-side countdown from an assumed total, or a stage name inferred from the
// graph, would look identical to one the rig computed.

import { useCallback, useEffect, useMemo, type JSX } from "react";

import { flowOrder } from "../../../../../components/flows/autoLayout";
import { NODE_DEFS } from "../../../../../components/flows/nodeDefs";
import { nodeLossDetail, nodeLossLevel } from "../../../../../components/flows/flowsTypes";
import type { FlowEdgeRec, FlowNodeRec } from "../../../../../components/flows/flowsTypes";
import { useFlowRunControls } from "../../../../../components/flows/flowRunControls";
import { accessPhrase, useCapability } from "../../../../../lib/caps";
import { useStore } from "../../../../../store";
import { nav } from "../../../../router";
import { useBreakpoint } from "../../../../breakpoint";
import { NxIcon } from "../../../../icons";
import {
  ActionButton, Card, EmptyCard, Label, ListRow, LockNote, Mono, Pill,
  ReadoutGrid, ReadoutTile, Sheet, StatusPill,
} from "../../../../ui";
import type { SheetProps } from "../../../sheets";
import { PLAN_EDITOR_PHONE_REASON } from "../../sheets/planEditor";
import { leaveFlowEditor } from "../openFlow";
import { FlowPortRow } from "./FlowNode";
import { FlowTapWireBar } from "./FlowTapWireBar";
import {
  ADD_STAGE_LABEL, IDLE_LOG_TEXT, LOG_TONE, NODE_STATUS_TONE, NODE_STATUS_WORD,
  NO_WIRES_TEXT, asNodeStatus, formatEta, framesWord, logTail, logTime, lossLabel,
  lossTone, saveLockReason, saveStateTone, saveStateWord, stageWord,
  tonightLockReason, unsavedRunReason, wireRemoveLabel, wireRowLabel,
} from "./canvasModel";
import "./canvas.css";

/** The sheet's own name in the route and the registry. */
export const FLOW_STAGES_SHEET = "flowStages";

/** Shown while no flow is open yet - the record arrives one tick after the
 *  sheet, and a blank title reads as a broken screen. */
export const FLOW_STAGES_UNTITLED = "FLOW";

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

// -------------------------------------------------------------- a stage row

function StageRow({ node, nodes, edges, openId }: {
  node: FlowNodeRec;
  /** The whole graph, from the sheet's two subscriptions. Passed rather than
   *  read here so a wire added anywhere re-renders one list, not sixteen - and
   *  so a wire row names the stage at its far end without a second lookup. */
  nodes: readonly FlowNodeRec[];
  edges: readonly FlowEdgeRec[];
  /** The flow id the route names, carried into every sheet this row opens. */
  openId: string;
}): JSX.Element {
  const status = useStore((s) => asNodeStatus(s.flows.statuses[node.id]));
  const loss = useStore((s) => nodeLossLevel(s.flows.compiled?.unmapped, node.type));
  const lossWhy = useStore((s) => nodeLossDetail(s.flows.compiled?.unmapped, node.type));
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
        right={<StatusPill text={word} tone={NODE_STATUS_TONE[status]} pulse={status === "busy"} />}
        chevron
        onPress={open}
      />
      {loss && (
        <div className="nx-flow-stage-loss">
          <Pill tone={lossTone(loss)}>{lossLabel(loss)}</Pill>
          <Mono size={10.5} tone="dim">{lossWhy}</Mono>
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
  const phase = useStore((s) => s.flows.run.phase);
  const etaS = useStore((s) => s.flows.run.etaS);
  const curStage = useStore((s) => s.flows.run.curStage);
  const frames = useStore((s) => s.flows.run.frames);
  const frameGoal = useStore((s) => s.flows.run.frameGoal);
  const logs = useStore((s) => s.flows.logs);
  const dirty = useStore((s) => s.flows.dirty);
  const readonly = useStore((s) => s.flows.record?.readonly ?? false);
  const flowsOpen = useStore((s) => s.flowsOpen);
  const save = useStore((s) => s.flowsSave);

  const canViewSiteDerived = useCapability("view.site_derived");
  const phone = useBreakpoint() === "phone";
  const { running, reason: hookRunReason, explain, act } = useFlowRunControls();

  const openId = record?.id ?? null;
  useEffect(() => {
    // Idempotent: re-opening the flow already loaded would discard an unsaved
    // edit and re-run the compile for nothing.
    if (!want || openId === want) return;
    void flowsOpen(want);
  }, [want, openId, flowsOpen]);

  const rows = useMemo(() => stageOrder(nodes, edges), [nodes, edges]);
  const lines = useMemo(() => logTail(logs), [logs]);

  const tonightReason = canViewSiteDerived
    ? null
    : tonightLockReason(accessPhrase("view.site_derived"));
  const planReason = phone ? PLAN_EDITOR_PHONE_REASON : null;

  // Same three decisions as the canvas toolbar, from the same pure helpers, so
  // the phone and the tablet cannot end up disagreeing about whether a flow is
  // safe to start. The rig's own refusal outranks the unsaved one.
  const runReason = hookRunReason ?? (running ? null : unsavedRunReason(dirty, readonly));
  const saveReason = saveLockReason(dirty, readonly);
  const stateWord = saveStateWord(dirty, readonly);

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
      title={record?.name ?? FLOW_STAGES_UNTITLED}
      sub={`${nodes.length} stages · ${edges.length} wires`}
      icon={<NxIcon name="flows" size={18} />}
      live={<Mono size={10.5} tone="dim">{`${phase.toUpperCase()} · ETA ${formatEta(etaS)}`}</Mono>}
      right={(
        <span data-testid="flow-stages-save-state">
          <Pill tone={saveStateTone(dirty, readonly)} ariaLabel={`This flow: ${stateWord}`}>
            {stateWord}
          </Pill>
        </span>
      )}
      onBack={back}
      footer={(
        <div className="nx-flow-stages-foot">
          <FlowTapWireBar />
          {/* SAVE above RUN, because RUN is refused until it has been pressed.
              A full-width pair would put the two most consequential buttons on
              the phone under one thumb sweep, so SAVE is the smaller of the
              two and RUN keeps the 56 px primary. */}
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
            // STOP is a plain single tap: emergency motion stops are never
            // armed, held or confirmed.
            arm={running ? undefined : { label: "CONFIRM RUN" }}
            onPress={act}
          >
            {running ? "STOP" : "RUN"}
          </ActionButton>
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
      <ReadoutGrid cols={4} data-testid="flow-stages-monitor">
        <ReadoutTile label="STATE" value={phase.toUpperCase()} />
        <ReadoutTile label="ETA" value={formatEta(etaS)} />
        <ReadoutTile label="STAGE" value={stageWord(curStage)} />
        <ReadoutTile label="FRAMES" value={framesWord(frames, frameGoal)} />
      </ReadoutGrid>

      <Label size={10}>STAGES</Label>
      {rows.length === 0 ? (
        <EmptyCard
          data-testid="flow-stages-empty"
          title="THIS FLOW HAS NO STAGES"
          hint="Nothing will happen when this flow runs. Add the first stage below."
        />
      ) : (
        <div className="nx-flow-stages">
          {rows.map((n) => (
            <StageRow key={n.id} node={n} nodes={nodes} edges={edges} openId={want} />
          ))}
        </div>
      )}

      {/* PINNED AT THE END OF THE LIST, and rendered at zero stages too - which
          is the state that most needs it. The legacy phone graph put the same
          control in the same place (`FlowPhoneGraph.tsx:92-102`) and wave 1
          dropped it, leaving a screen that could open a flow, wire it and run it
          but never add anything to it. */}
      <ActionButton
        kind="purple"
        size="lg"
        full
        data-testid="flow-stages-add"
        onPress={addStage}
      >
        {ADD_STAGE_LABEL}
      </ActionButton>

      <Label size={10}>LOG</Label>
      <div role="log" className="nx-flow-log-body" data-testid="flow-stages-log">
        {lines.length === 0 ? (
          <Mono size={10.5} tone="dim">{IDLE_LOG_TEXT}</Mono>
        ) : (
          lines.map((l) => (
            <div key={l.id} className="nx-flow-log-line">
              <Mono size={10} tone="dim">{logTime(l.ts)}</Mono>
              <Mono size={10.5} tone={LOG_TONE[l.tone]}>{l.msg}</Mono>
            </div>
          ))
        )}
      </div>

      <ListRow
        data-testid="flow-stages-tonight"
        icon={<NxIcon name="clock" size={16} />}
        title="TONIGHT"
        sub="timeline, brief, compiled plan and campaign ledger"
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
