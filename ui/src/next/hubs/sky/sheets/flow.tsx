// flow.tsx - the FLOW CARD: what GENERATE FLOW actually built, and the one
// button that starts it (hub-sky plan D.5, D.7, screenshot 06).
//
// THIS SCREEN IS A READ-BACK, and that is its whole value. The quick sheet asked
// four questions; this card shows the night those four answers produced - every
// stage, in the order the run cursor takes them, with each stage's own summary
// composed from its own parameters. If the operator asked for something the
// graph does not contain, this is where it is visible, before the shutter opens.
//
// THREE THINGS IT REFUSES TO FAKE:
//
//  1. THE VALIDATION CHIP HAS THREE STATES. "The compiler has not answered yet"
//     renders as NOT CHECKED, never as GRAPH VALID. RUN NOW sits directly under
//     it, and a green chip over an unchecked graph is the app vouching for
//     something it has not seen.
//  2. THE FOOTER'S CHECK COUNT IS THE COMPILER'S. The prototype's "The doctor
//     passed all 13 checks" is a fixture value; printed on a real graph it would
//     be a number nobody computed.
//  3. A 409 `unmapped` IS A QUESTION. The server is asking whether the operator
//     accepts running a graph part of which the engine will not honour, and the
//     answer is a confirm listing every one of them - not a red toast. Accepting
//     re-runs with `accept_unmapped: true`. It does NOT clear a dome refusal,
//     and nothing here says it does.
//
// STOP IS NOT HERE. A flow run IS a sequence run, so the only thing that stops
// one is `POST /api/sequence/abort`, which lives on Session - Now. On success
// this sheet closes into that hub rather than leaving the operator on a card
// describing a night that has already started.

import { useEffect, useMemo, type JSX } from "react";
import type { SheetProps } from "../../sheets";
import { ActionButton, Card, EmptyCard, Label, Mono, Sheet } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { useFraming, useStore } from "../../../../store";
import { accessPhrase, useCanControlMount, useRoleConnected } from "../../../../lib/caps";
import { isRunPhaseLive, runBlockedReason } from "../../../../components/flows/flowRunControls";
import type { FlowUnmapped } from "../../../../lib/flowsApi";
import {
  FLOWS_NEEDS_WIDTH, UNMAPPED_CANCEL, UNMAPPED_CONFIRM, UNMAPPED_TITLE,
  flowFooterLine,
} from "./quickCopy";
import { doctorChip, issuesLine, laneCards, ruleRows, withMosaicCard } from "./flowLane";

export function FlowCardSheet({ params }: SheetProps): JSX.Element {
  const id = params.id ?? "";
  const record = useStore((s) => s.flows.record);
  const graph = useStore((s) => s.flows.graph);
  const compiled = useStore((s) => s.flows.compiled);
  const compiling = useStore((s) => s.flows.compiling);
  const phase = useStore((s) => s.flows.run.phase);
  const flowsOpen = useStore((s) => s.flowsOpen);
  const flowsRun = useStore((s) => s.flowsRun);
  const pushConfirm = useStore((s) => s.pushConfirm);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const framing = useFraming();
  const breakpoint = useBreakpoint();

  const canControlMount = useCanControlMount();
  const cameraConnected = useRoleConnected("camera").connected;
  const running = isRunPhaseLive(phase);

  // A deep link lands here with only an id in the hash - support asks for
  // exactly that ("open #/sky/quick/flow?id=… and read me the stages"). The
  // quick sheet has usually opened it already; this is the case where nothing
  // has.
  useEffect(() => {
    if (!id) return;
    if (record?.id === id) return;
    void flowsOpen(id);
  }, [id, record?.id, flowsOpen]);

  const mosaic = framing?.mosaic;
  const cards = useMemo(() => {
    const lane = laneCards(graph);
    return mosaic ? withMosaicCard(lane, mosaic.cols, mosaic.rows) : lane;
  }, [graph, mosaic]);
  const rules = useMemo(() => ruleRows(graph), [graph]);

  const chip = doctorChip(compiled, compiling);
  const unmapped = compiled?.unmapped ?? [];

  const runReason = runBlockedReason(canControlMount, cameraConnected, running)
    ?? (record?.id !== id ? "Loading this flow…" : null);

  /** `flowsRun` answers null for BOTH a clean start and a refusal it has already
   *  written to the flow log, so null on its own is not "it started". The run
   *  phase is what the engine actually said, and it is read back before this
   *  sheet navigates away from the card the operator would need to try again. */
  const started = (): boolean => isRunPhaseLive(useStore.getState().flows.run.phase);

  const start = async (): Promise<void> => {
    if (runReason) return;
    const list = await flowsRun(false);
    if (!list) {
      if (started()) { nav.hub("session", "now"); return; }
      enqueueToast({
        level: "error",
        title: "The flow did not start",
        detail: "The engine refused it. The reason is in the flow log on Session - Flows.",
      });
      return;
    }
    const ok = await pushConfirm({
      title: UNMAPPED_TITLE,
      body: (
        <ul style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12 }}>
          {list.map((u: FlowUnmapped) => (
            <li key={u.key}>{u.detail}</li>
          ))}
        </ul>
      ),
      confirmLabel: UNMAPPED_CONFIRM,
      cancelLabel: UNMAPPED_CANCEL,
      tone: "warn",
      confirmPrimary: true,
    });
    if (!ok) return;
    await flowsRun(true);
    if (started()) { nav.hub("session", "now"); return; }
    enqueueToast({
      level: "error",
      title: "The flow still did not start",
      detail: "Accepting the losses was not what was blocking it - the reason is in the flow log.",
    });
  };

  const flowsReason = breakpoint === "phone" ? FLOWS_NEEDS_WIDTH : null;

  const title = record?.name
    ? record.name.toUpperCase()
    : "QUICK SESSION";
  const sub = `${cards.length} stage${cards.length === 1 ? "" : "s"} · `
    + `${rules.length} rule${rules.length === 1 ? "" : "s"} · saved to My flows`;

  return (
    <Sheet
      data-testid="sky-flow"
      title={title}
      sub={sub}
      icon={<NxIcon name="flows" size={18} />}
      backLabel="EDIT"
      onBack={() => nav.back()}
      right={
        <span
          data-testid="flow-doctor"
          data-state={chip.state}
          className="nx-mono"
          style={{
            padding: "4px 8px",
            borderRadius: 6,
            fontSize: 10,
            letterSpacing: ".08em",
            whiteSpace: "nowrap",
            border: chip.state === "valid"
              ? "1px solid rgba(61,220,151,.5)"
              : chip.state === "open"
                ? "1px solid rgba(255,180,84,.5)"
                : "1px solid rgba(120,140,200,.28)",
            color: chip.state === "valid"
              ? "var(--good, #3ddc97)"
              : chip.state === "open"
                ? "var(--warn, #ffb454)"
                : "var(--text-3, #7683a5)",
          }}
        >
          {chip.label}
        </span>
      }
      footer={
        <div style={{ display: "flex", gap: 8 }}>
          <ActionButton
            kind="secondary"
            size="xl"
            data-testid="flow-open-in-flows"
            lockedReason={flowsReason}
            onExplain={(r) => enqueueToast({ level: "warning", title: r })}
            onPress={() => nav.go(`/session/flows?open=${encodeURIComponent(id)}`)}
          >
            OPEN IN FLOWS
          </ActionButton>
          <ActionButton
            kind="primary"
            size="xl"
            full
            data-testid="flow-run"
            glyph={<NxIcon name="play" size={16} />}
            lockedReason={runReason}
            onExplain={(r) => enqueueToast({ level: "warning", title: r })}
            onPress={() => void start()}
          >
            {running ? "RUN IN PROGRESS" : "RUN NOW"}
          </ActionButton>
        </div>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {cards.length === 0 ? (
          <EmptyCard
            title="NO STAGES IN THIS FLOW"
            hint="The flow record has not arrived, or its graph has no flow-lane wire. Open it in Flows to see what is on the canvas."
          />
        ) : (
          <div data-testid="flow-lane" style={{ display: "flex", flexDirection: "column" }}>
            {cards.map((c, i) => (
              <div key={c.id} style={{ display: "flex", flexDirection: "column" }}>
                <Card tone="default" padding={0}>
                  <div
                    data-lane-card={c.label}
                    style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px" }}
                  >
                    <span
                      aria-hidden="true"
                      style={{
                        width: 7, height: 7, borderRadius: 2, flexShrink: 0,
                        background: `var(${c.colorVar})`,
                        boxShadow: `0 0 8px var(${c.colorVar})`,
                      }}
                    />
                    <span style={{ flex: 1, display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                      <Label size={10}>{c.label}</Label>
                      <Mono size={10} tone="dim">
                        <span style={{ display: "block", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                          {c.sum}
                        </span>
                      </Mono>
                      {c.footnote && (
                        <Mono size={10} tone="warn">{c.footnote}</Mono>
                      )}
                    </span>
                    <span aria-hidden="true" style={{ width: 10, height: 2, background: "var(--text-3, #7683a5)", flexShrink: 0 }} />
                  </div>
                </Card>
                {i < cards.length - 1 && (
                  <span aria-hidden="true" style={{ width: 2, height: 12, marginLeft: 26, background: "rgba(0,210,255,.6)" }} />
                )}
              </div>
            ))}
          </div>
        )}

        {rules.length > 0 && (
          <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <Label size={10}>RULES · EVENT WIRES</Label>
            {rules.map((r) => (
              <div
                key={r.id}
                data-rule={r.srcLabel}
                style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 26px minmax(0,1fr)", alignItems: "center", gap: 6 }}
              >
                <Card tone="default" padding={10}>
                  <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                    <span className="nx-display" style={{ fontSize: 10, letterSpacing: ".14em", color: "var(--accent, #00D2FF)" }}>
                      {r.srcLabel}
                    </span>
                    <Mono size={10} tone="dim">{r.when}</Mono>
                  </span>
                </Card>
                <span aria-hidden="true" style={{ borderTop: "1.5px dashed rgba(255,180,84,.6)" }} />
                <Card tone="default" padding={10}>
                  <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                    <span className="nx-display" style={{ fontSize: 10, letterSpacing: ".14em", color: `var(${r.colorVar})` }}>
                      {r.actLabel}
                    </span>
                    <Mono size={10} tone="dim">
                      <span style={{ display: "block", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {r.sum}
                      </span>
                    </Mono>
                  </span>
                </Card>
              </div>
            ))}
          </section>
        )}

        {unmapped.length > 0 && (
          <section data-testid="flow-unmapped" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <Label size={10}>WHAT THE COMPILE DROPS</Label>
            {unmapped.map((u) => (
              <p
                key={u.key}
                data-level={u.level}
                style={{
                  fontSize: 11.5,
                  lineHeight: 1.45,
                  // `note` is NOT a quieter warn: it says the thing IS honoured,
                  // by another part of the engine than the wire names. Amber on
                  // it would report a working feature as a defect.
                  color: u.level === "note"
                    ? "var(--text-3, #7683a5)"
                    : u.level === "danger"
                      ? "var(--bad, #ff5470)"
                      : "var(--warn, #ffb454)",
                }}
              >
                {u.detail}
              </p>
            ))}
          </section>
        )}

        <p style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--text-3, #7683a5)" }}>
          {flowFooterLine(issuesLine(compiled, compiling))}
        </p>
        {!canControlMount && (
          <p style={{ fontSize: 11.5, color: "var(--text-3, #7683a5)" }}>
            {`Running a flow needs ${accessPhrase("control.mount")}; everything above is the flow as it was saved.`}
          </p>
        )}
      </div>
    </Sheet>
  );
}
