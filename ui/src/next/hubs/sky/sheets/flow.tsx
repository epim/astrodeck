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
//     and nothing here says it does. CONTINUE's three 409s (`adopt`,
//     `recount`, `dropped_steps`, #189 S1) are questions too, asked through
//     the same `askContinue` the Flows RUN button uses, and every re-post
//     carries every answer already given.
//
// STOP IS NOT HERE. A flow run IS a sequence run, so the only thing that stops
// one is `POST /api/sequence/abort`, which lives on Session - Now. On success
// this sheet closes into that hub rather than leaving the operator on a card
// describing a night that has already started.
//
// AND IT DRAWS ONLY THE FLOW ITS LINK NAMES (#499). Everything this card reads
// (the name, the lane, the rules, the chip, the drops, the footer) is the store's
// ONE open record, and `flowsOpen(id)` leaves the record that was open before in
// place when it fails; since #450 it also refuses to replace a flow whose
// unsaved edits its save did not keep (openFlow.ts "THE WAY IN"). So after a
// failed open the card titled for flow B drew flow A's name and stages, with RUN
// locked for good. While the open record is not the flow `?id=` names, the card
// shows a waiting card instead: loading while the open is out, and "that flow
// did not open" with the reason once `openFlowById` has answered false. RUN
// stays locked throughout. The #/next framing sheet waits the same way
// (FlowFrameSheet.tsx `FRAME_FLOW_LOADING`, `FRAME_OTHER_FLOW`, #384).

import { useEffect, useMemo, useState, type JSX } from "react";
import type { SheetProps } from "../../sheets";
import { ActionButton, Card, EmptyCard, Label, Mono, Sheet } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { useStore } from "../../../../store";
import { accessPhrase, useCanControlMount, useRoleConnected } from "../../../../lib/caps";
import {
  askContinue, isRunPhaseLive, runAnsweringQuestions, runBlockedReason,
} from "../../../../components/flows/flowRunControls";
import type { FlowUnmapped } from "../../../../lib/flowsApi";
import {
  FLOWS_NEEDS_WIDTH, UNMAPPED_CANCEL, UNMAPPED_CONFIRM, UNMAPPED_TITLE,
  flowFooterLine,
} from "./quickCopy";
import {
  doctorChip, issuesLine, laneCards, ruleRows,
} from "./flowLane";
import {
  FLOW_OPEN_FAILED, flowOpenFailure, libraryErrorNow, openFlowById,
} from "../../session/flows/openFlow";

/** The waiting card while the open this card asked for is still out. The open
 *  record is some other flow's, or none, so nothing of it is shown. */
export const FLOW_CARD_LOADING =
  "This flow's stages show once it has loaded.";

/** The waiting card's reason for a link with no `?id=`: there is no flow to
 *  open, and the one open in the store is not this link's to show. */
export const FLOW_CARD_NO_ID =
  "This link names no flow, so there is nothing here to show or run.";

/** RUN's locked reason once the open has answered and it was not this flow. */
export const FLOW_CARD_RUN_NOT_OPENED =
  "That flow did not open, so there is nothing here to run.";

/** RUN's locked reason while the open is still out. */
const FLOW_CARD_RUN_LOADING = "Loading this flow…";

export function FlowCardSheet({ params }: SheetProps): JSX.Element {
  const id = params.id ?? "";
  const record = useStore((s) => s.flows.record);
  const graph = useStore((s) => s.flows.graph);
  const compiled = useStore((s) => s.flows.compiled);
  const compiling = useStore((s) => s.flows.compiling);
  const phase = useStore((s) => s.flows.run.phase);
  const flowsRun = useStore((s) => s.flowsRun);
  const pushConfirm = useStore((s) => s.pushConfirm);
  const resolveConfirm = useStore((s) => s.resolveConfirm);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const breakpoint = useBreakpoint();

  const canControlMount = useCanControlMount();
  const cameraConnected = useRoleConnected("camera").connected;
  const running = isRunPhaseLive(phase);

  const recordId = record?.id ?? null;
  // THE ONE TEST every read of the open record below is gated on, through
  // `waiting`. A link with no id names no flow, so it is never "mine" whatever
  // is open.
  const mine = id !== "" && recordId === id;

  /** The last open this card asked for that did not land: which flow, the
   *  record that was open when it was asked (the attempt's identity: a new
   *  attempt starts exactly when `id` or `recordId` changes, so a failure
   *  recorded under an earlier one no longer matches), and the reason. */
  const [failure, setFailure] = useState<
    { id: string; from: string | null; reason: string } | null
  >(null);

  // A deep link lands here with only an id in the hash - support asks for
  // exactly that ("open #/sky/quick/flow?id=… and read me the stages"). The
  // quick sheet opens the flow before it navigates here (#499); this is the
  // case where nothing has, or where the open record has since changed.
  //
  // Through `openFlowById`, because `flowsOpen` answers nothing: its failure
  // is a record left as it was, and only a check of what landed can tell the
  // waiting card to stop saying "loading". An answer arriving after the card
  // has moved on (another id, or the record changed under it) is dropped.
  useEffect(() => {
    if (!id || recordId === id) return;
    let current = true;
    const before = libraryErrorNow();
    void openFlowById(id).then((landed) => {
      if (current && !landed) setFailure({ id, from: recordId, reason: flowOpenFailure(before) });
    });
    return () => { current = false; };
  }, [id, recordId]);

  /** What the card shows INSTEAD of the open record, and RUN's locked reason,
   *  while that record is not the flow this card names; null once it is. The
   *  waiting card has three things to say: the link names no flow, the open
   *  answered and it was not this flow (with the reason the store wrote, or
   *  the mismatch sentence), or the open is still out. */
  const failed = failure !== null && failure.id === id && failure.from === recordId
    ? failure.reason : null;
  const waiting: { title: string; hint: string; run: string } | null = mine ? null
    : id === ""
      ? { title: "NO FLOW", hint: FLOW_CARD_NO_ID, run: FLOW_CARD_NO_ID }
      : failed !== null
        ? { title: FLOW_OPEN_FAILED.toUpperCase(), hint: failed, run: FLOW_CARD_RUN_NOT_OPENED }
        : { title: "OPENING THIS FLOW", hint: FLOW_CARD_LOADING, run: FLOW_CARD_RUN_LOADING };

  // The lane is the saved graph's and nothing else. The synthetic MOSAIC row
  // stood for panels the quick sheet queued as Plan targets beside the flow,
  // named by a `mosaic` hash param; that side channel went in S6 (#196), and
  // the S5/S6 integration dropped the row's call and the param (#461). A
  // mosaic is the flow's own TARGET block, whose card the lane draws. A link
  // from before S6 may still carry `mosaic`; it is not read.
  const cards = useMemo(() => laneCards(graph), [graph]);
  const rules = useMemo(() => ruleRows(graph), [graph]);

  // While the card waits, the store's compile is another flow's (or none), so
  // the chip is computed over nothing: NOT CHECKED, which is true of a flow
  // this card has not loaded, and never that other flow's GRAPH VALID.
  const chip = waiting === null ? doctorChip(compiled, compiling) : doctorChip(null, false);
  const unmapped = compiled?.unmapped ?? [];

  const runReason = runBlockedReason(canControlMount, cameraConnected, running)
    ?? waiting?.run ?? null;

  /** `flowsRun` answers null for BOTH a clean start and a refusal it has already
   *  written to the flow log, so null on its own is not "it started". The run
   *  phase is what the engine actually said, and it is read back before this
   *  sheet navigates away from the card the operator would need to try again. */
  const started = (): boolean => isRunPhaseLive(useStore.getState().flows.run.phase);

  const start = async (): Promise<void> => {
    if (runReason) return;
    // The loop the Flows RUN button uses: one question per request, and every
    // re-post carries every answer already given. A decline is silent -
    // nothing failed, the operator said no.
    const { cancelled, answered } = await runAnsweringQuestions(
      flowsRun,
      (list) => pushConfirm({
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
      }),
      (q) => askContinue(q, pushConfirm, resolveConfirm, "nx-confirm-btn"),
    );
    if (cancelled) return;
    if (started()) { nav.hub("session", "now"); return; }
    enqueueToast(answered === null
      ? {
          level: "error",
          title: "The flow did not start",
          detail: "The engine refused it. The reason is in the flow log on Session - Flows.",
        }
      : {
          level: "error",
          title: "The flow still did not start",
          detail: answered === "unmapped"
            ? "Accepting the losses was not what was blocking it - the reason is in the flow log."
            : "Answering the question was not what was blocking it - the reason is in the flow log.",
        });
  };

  const flowsReason = breakpoint === "phone" ? FLOWS_NEEDS_WIDTH : null;

  // The name and the counts are the open record's too: while the card waits
  // it is titled for no flow and counts nothing.
  const title = waiting !== null
    ? "FLOW"
    : record?.name
      ? record.name.toUpperCase()
      : "QUICK SESSION";
  const sub = waiting !== null
    ? undefined
    : `${cards.length} stage${cards.length === 1 ? "" : "s"} · `
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
      {waiting !== null ? (
        // NOTHING OF THE OPEN RECORD: not its lane, rules, drops or footer,
        // which are all another flow's (or none) while this is up (#499).
        <EmptyCard data-testid="flow-card-waiting" title={waiting.title} hint={waiting.hint} />
      ) : (
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
      )}
    </Sheet>
  );
}
