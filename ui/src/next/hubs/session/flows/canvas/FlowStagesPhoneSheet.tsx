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
//   * tap-to-wire   -> the port buttons on each row, with the armed hint bar as
//                      the sheet's sticky footer.
//   * the edit door -> a row press selects the stage and opens the `flowNode`
//                      sheet at depth 1.
//   * MONITOR tab   -> the four readouts, the log tail and RUN / STOP.
//
// WHAT IT MAY NOT INVENT. `run.phase`, `run.etaS`, `run.curStage` and
// `run.frames` are written by nothing server-side: there is no `flow.node`
// topic, no `flow.log` topic and no run-state GET. This renders the store's
// values honestly and they will read IDLE / - / - / 0 during a real run. A
// client-side countdown from an assumed total, or a stage name inferred from the
// graph, would look identical to one the rig computed.

import { useEffect, useMemo, type JSX } from "react";

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
import { FlowPortRow } from "./FlowNode";
import { FlowTapWireBar } from "./FlowTapWireBar";
import {
  IDLE_LOG_TEXT, LOG_TONE, NODE_STATUS_TONE, NODE_STATUS_WORD, asNodeStatus,
  formatEta, framesWord, logTail, logTime, lossLabel, lossTone, stageWord,
  tonightLockReason,
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

function StageRow({ node }: { node: FlowNodeRec }): JSX.Element {
  const status = useStore((s) => asNodeStatus(s.flows.statuses[node.id]));
  const loss = useStore((s) => nodeLossLevel(s.flows.compiled?.unmapped, node.type));
  const lossWhy = useStore((s) => nodeLossDetail(s.flows.compiled?.unmapped, node.type));
  const selected = useStore((s) => s.flows.sel?.kind === "node" && s.flows.sel.id === node.id);
  const select = useStore((s) => s.flowsSelect);
  const setEditNode = useStore((s) => s.flowsSetEditNode);
  const tapPort = useStore((s) => s.flowsTapPort);

  const def = NODE_DEFS[node.type];
  const word = NODE_STATUS_WORD[status];

  const open = (): void => {
    // Selecting as well as opening: the node sheet renders the SELECTED stage,
    // so opening it on a stage that is not selected would show another one's
    // parameters.
    select({ kind: "node", id: node.id });
    setEditNode(node.id);
    nav.sheet("flowNode", { node: node.id });
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
    </Card>
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
  const flowsOpen = useStore((s) => s.flowsOpen);

  const canViewSiteDerived = useCapability("view.site_derived");
  const phone = useBreakpoint() === "phone";
  const { running, reason: runReason, explain, act } = useFlowRunControls();

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

  return (
    <Sheet
      data-testid="session-flow-stages"
      title={record?.name ?? FLOW_STAGES_UNTITLED}
      sub={`${nodes.length} stages · ${edges.length} wires`}
      icon={<NxIcon name="flows" size={18} />}
      live={<Mono size={10.5} tone="dim">{`${phase.toUpperCase()} · ETA ${formatEta(etaS)}`}</Mono>}
      onBack={() => nav.back()}
      footer={(
        <div className="nx-flow-stages-foot">
          <FlowTapWireBar />
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
          <LockNote reason={runReason} />
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
          hint="Stages are added on the canvas, which opens on a tablet or desktop."
        />
      ) : (
        <div className="nx-flow-stages">
          {rows.map((n) => <StageRow key={n.id} node={n} />)}
        </div>
      )}

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
        onPress={() => nav.sheet("flowTonight")}
      />
      <ListRow
        data-testid="flow-stages-plan"
        icon={<NxIcon name="session" size={16} />}
        title="PLAN EDITOR"
        sub="guiding, count mode and per-target flip"
        chevron
        lockedReason={planReason}
        onExplain={explain}
        onPress={() => nav.sheet("planEditor")}
      />
      <LockNote reason={planReason} />
    </Sheet>
  );
}

export default FlowStagesPhoneSheet;
