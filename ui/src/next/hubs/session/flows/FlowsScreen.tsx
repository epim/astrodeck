// FlowsScreen.tsx - SESSION / FLOWS: MY FLOWS, and the door to the canvas
// (plan section D; proto `29-my-flows.html`, screenshot 29).
//
// The phone's whole relationship with Flows is this list. Building a graph is a
// tablet job - the canvas needs pan, zoom, wires and an inspector - but RUNNING
// one is the thing an operator does from bed, and that is what this screen is
// for: which flows exist, which one is going, and start it.
//
// RUN IS NOT RE-IMPLEMENTED. `useFlowRunControls()` owns three facts that cost
// real nights to learn (`inventory-session-monitor.md` section 3.4): STOP is
// `POST /api/sequence/abort` and not a flows route; a timed-out abort is not a
// failed abort; and a 409 `unmapped` is a QUESTION, answered with the confirm
// that lists what the compile drops. This screen calls the hook. It transcribes
// none of it.
//
// WHY RUN OPENS THE FLOW FIRST. `flowsRun()` posts against `flows.record?.id` -
// the flow on the canvas - so a list row has to make its flow the open one
// before it can start it. `flowsOpen(id)` loads the record and calls
// `flowsCompile()`, which is exactly what "RUN re-compiles against tonight's
// ephemeris" means; the server re-compiles again at `/run`, and the UI only
// says so.

import { useCallback, useEffect, useMemo, useState, type JSX } from "react";

import { cardMeta, cardStatus } from "../../../../components/flows/FlowLibraryCard";
import { runBlockedReason, useFlowRunControls } from "../../../../components/flows/flowRunControls";
import { fmtClock } from "../../../../lib/eta";
import { runIsLive } from "../../../../lib/lastSessionFrame";
import {
  accessPhrase, useCanControlCapture, useCanControlMount, useRoleConnected,
} from "../../../../lib/caps";
import { resumeSession } from "../../../../api/sessions";
import { useSeq, useStore } from "../../../../store";
import { nav, useRoute } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { NxIcon } from "../../../icons";
import { explainLock } from "../../../shell/explain";
import { ActionButton, Chip, EmptyCard, ListRow, Mono } from "../../../ui";
import { useSessionCampaign } from "../crossHub";
import { PLAN_EDITOR_PHONE_REASON } from "../sheets/planEditor";
import { FlowRow, type FlowVerb } from "./FlowRow";
import { CANVAS_LIBRARY, FlowsCanvasHost } from "./FlowsCanvasHost";

/** The proto's footer, verbatim. It is the one place the screen says what RUN
 *  actually does and why some flows are a tablet job. */
export const FLOWS_FOOTER =
  "RUN re-compiles a quick session or night plan against tonight's ephemeris "
  + "and starts it. Campaigns, calibration and templates open on the canvas "
  + "(tablet or desktop) and are monitored here.";

/** Plan section D.3. Says what still works here, so the sentence is a
 *  redirection rather than a refusal. */
export const CANVAS_PHONE_REASON =
  "The flows canvas opens on a tablet or desktop. RUN works here.";

/** Creating a flow needs the canvas; on a phone the quick session is created
 *  from a target in the SKY hub, which is already shipped. */
export const CREATE_PHONE_HINT = "Quick sessions are created from a target in SKY";

const DOT_LIVE = "#00D2FF";
const DOT_CAMPAIGN = "#9B51E0";
const DOT_OK = "#3ddc97";
const DOT_WARN = "#ffb454";
const DOT_BAD = "#ff5470";
const DOT_NEVER = "#7683a5";

function dotFor(lastResult: string, live: boolean, campaign: boolean): string {
  if (live) return DOT_LIVE;
  if (campaign) return DOT_CAMPAIGN;
  if (lastResult === "ok") return DOT_OK;
  if (lastResult === "warn") return DOT_WARN;
  if (lastResult === "bad") return DOT_BAD;
  return DOT_NEVER;
}

export function FlowsScreen(): JSX.Element {
  const route = useRoute();
  const bp = useBreakpoint();
  const phone = bp === "phone";

  const cards = useStore((s) => s.flows.cards);
  const libraryLoaded = useStore((s) => s.flows.libraryLoaded);
  const libraryError = useStore((s) => s.flows.libraryError);
  const loadLibrary = useStore((s) => s.flowsLoadLibrary);
  const flowsOpen = useStore((s) => s.flowsOpen);
  const flowsSetUi = useStore((s) => s.flowsSetUi);
  const enqueueToast = useStore((s) => s.enqueueToast);

  const seq = useSeq();
  const camp = useSessionCampaign();
  const runControls = useFlowRunControls();
  const canCreate = useCanControlCapture();
  const canControlMount = useCanControlMount();
  const camera = useRoleConnected("camera");
  const openRecordId = useStore((s) => s.flows.record?.id ?? null);

  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => { void loadLibrary(); }, [loadLibrary]);

  const openReason = phone ? CANVAS_PHONE_REASON : null;
  const createReason = canCreate ? null : `Creating a flow needs ${accessPhrase("control.capture")}.`;

  // The hook's own `reason` narrows on whether a run is LIVE, because its button
  // is a RUN/STOP toggle. A list row is not: it shows LIVE and navigates instead
  // of offering to stop, so the sentence it wants is always the starting one.
  // The sentence itself still comes from `runBlockedReason` - only the third
  // argument is ours.
  const runReason = runBlockedReason(canControlMount, camera.connected, false);

  // `flows.run.phase` is written optimistically by `flowsRun` and by nothing
  // server-side (`inventory-session-monitor.md` section 3.0), so it leads the
  // engine's own `sequence.state` by however long the socket takes. Both count:
  // a row that still said RUN in that gap would offer to start a second run, and
  // `act()` would stop the first one instead.
  const startedFlowId = runControls.running ? openRecordId : null;
  const seqLive = runIsLive(seq);

  const openCanvas = useCallback((id: string) => {
    nav.go(`/session/flows?open=${encodeURIComponent(id)}`);
  }, []);

  const start = useCallback(async (id: string) => {
    // A list row NEVER stops a run. `useFlowRunControls().act()` is a toggle, so
    // pressing RUN on a second row while one is going would abort the first -
    // and the row that did it would be the one that looked idle.
    if (runControls.running) { nav.go("/session/now"); return; }
    if (runReason) { runControls.explain(runReason); return; }
    setBusyId(id);
    try {
      // The record has to be the open one before `flowsRun` can post against
      // it, and opening it is also the local re-compile the footer promises.
      await flowsOpen(id);
    } finally {
      setBusyId(null);
    }
    runControls.act();
  }, [flowsOpen, runControls, runReason]);

  const resume = useCallback(async (sessionId: string) => {
    if (runReason) { runControls.explain(runReason); return; }
    setBusyId(sessionId);
    try {
      const r = await resumeSession(sessionId);
      enqueueToast(r.resumed
        ? { level: "success", title: "Campaign resumed", detail: `${r.remaining} frames still owed` }
        : { level: "warning", title: "Nothing to resume", detail: "The session owes no frames." });
    } catch (e) {
      enqueueToast({
        level: "error",
        title: "Resume did not land",
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setBusyId(null);
    }
  }, [enqueueToast, runControls, runReason]);

  const rows = useMemo(() => cards.map((card) => {
    const isCampaign = camp != null && camp.flowId === card.id;
    const isLive = startedFlowId === card.id
      || (seqLive && (isCampaign ? camp.live : seq.plan_name === card.name));
    const verb: FlowVerb = isLive
      ? "live"
      : (isCampaign && camp.parked && camp.sessionId) ? "resume" : "run";

    let meta: string;
    if (isCampaign) {
      const head = `campaign · night ${camp.night} of ~${camp.totalNights}`;
      if (isLive) meta = `${head} · running now`;
      else if (camp.parked) {
        meta = `${head} · parked · resumes at dusk${
          camp.duskMs != null ? ` ${fmtClock(camp.duskMs)}` : ""}`;
      } else meta = `${head} · ${camp.bankedH.toFixed(1)} of ${camp.goalH} h banked`;
    } else {
      // `cardStatus` has designed copy for exactly two of the server's four
      // values; `warn` and `bad` print the server's OWN word rather than
      // invented copy (`FlowLibraryCard.tsx:53-60`).
      const status = cardStatus(card.last_result);
      meta = card.last_result === "ok" || card.last_result === "warn" || card.last_result === "bad"
        ? `${cardMeta(card)} · ${status.text.toLowerCase()}`
        : cardMeta(card);
    }

    return {
      card,
      meta,
      verb,
      dotColor: dotFor(card.last_result, isLive, isCampaign),
      sessionId: isCampaign ? camp.sessionId : null,
    };
  }), [cards, camp, seqLive, startedFlowId, seq.plan_name]);

  // The canvas takes the whole hub body when the route asks for it. Reachable
  // only at 768 px and up: every door to it is honest-disabled below that, and
  // a hand-typed hash lands on the same reason rather than on a canvas nobody
  // can pan.
  const open = route.params.open ?? "";
  if (open && !phone) return <FlowsCanvasHost open={open} />;

  const count = libraryLoaded ? cards.length : null;

  return (
    <div data-testid="session-flows" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
        <span className="nx-display" style={{ fontSize: 15, letterSpacing: ".1em" }}>MY FLOWS</span>
        <span data-testid="flows-summary">
          <Mono size={10} tone="dim">
            {count == null ? "reading the library" : `${count} saved`} · quick sessions land here
          </Mono>
        </span>
      </div>

      {libraryError && (
        <span data-testid="flows-error"><Mono size={10.5} tone="bad">{libraryError}</Mono></span>
      )}

      {libraryLoaded && cards.length === 0 ? (
        <EmptyCard
          title="NO SAVED FLOWS"
          hint="A quick session saves itself here the moment you start one from a target in SKY."
        />
      ) : (
        <div
          data-testid="flows-list"
          style={{
            border: "1px solid rgba(120,140,200,.18)", borderRadius: 16,
            background: "rgba(12,14,22,.92)", overflow: "hidden",
          }}
        >
          {rows.map((r) => (
            <FlowRow
              key={r.card.id}
              card={r.card}
              meta={r.meta}
              dotColor={r.dotColor}
              verb={r.verb}
              busy={busyId === r.card.id || busyId === r.sessionId}
              runReason={runReason}
              openReason={openReason}
              onRun={() => { void start(r.card.id); }}
              onResume={() => { if (r.sessionId) void resume(r.sessionId); }}
              onLive={() => nav.go("/session/now")}
              onOpen={() => openCanvas(r.card.id)}
              onExplain={explainLock}
            />
          ))}
        </div>
      )}

      {/* The list-level OPEN. Same control, same reason - the design puts it at
          the bottom because on a tablet it is the way INTO the workspace, not a
          per-row alternative. */}
      <ActionButton
        kind="secondary"
        size="lg"
        full
        data-testid="flows-open-canvas"
        lockedReason={openReason}
        onExplain={explainLock}
        onPress={() => openCanvas(CANVAS_LIBRARY)}
      >
        OPEN THE FLOWS CANVAS
      </ActionButton>

      {phone ? (
        <ListRow
          data-testid="flows-create-phone"
          icon={<NxIcon name="sky" size={16} />}
          title={CREATE_PHONE_HINT}
          sub="A flow saved there appears in this list."
          chevron
          onPress={() => nav.hub("sky")}
        />
      ) : (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Chip
            data-testid="flows-new"
            lockedReason={createReason}
            onExplain={explainLock}
            onClick={() => { flowsSetUi({ screen: "library", wizardOpen: true }); openCanvas(CANVAS_LIBRARY); }}
          >
            + NEW FLOW
          </Chip>
          <Chip
            data-testid="flows-quick"
            lockedReason={createReason}
            onExplain={explainLock}
            onClick={() => { flowsSetUi({ screen: "library", quickOpen: true }); openCanvas(CANVAS_LIBRARY); }}
          >
            QUICK FLOW
          </Chip>
        </div>
      )}

      {/* The doorway to everything a flow cannot express - plan identity,
          import/export, per-target scheduling, the quota guards and the
          when/then rules (plan section D.4, GAP-ANALYSIS section 7). */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Chip
          data-testid="flows-plan-editor"
          lockedReason={phone ? PLAN_EDITOR_PHONE_REASON : null}
          onExplain={explainLock}
          onClick={() => nav.sheet("planEditor")}
        >
          PLAN EDITOR
        </Chip>
      </div>

      {runReason && (
        <span data-testid="flows-run-reason">
          <Mono size={10.5} tone="warn">{runReason}</Mono>
        </span>
      )}
      {openReason && (
        <span data-testid="flows-canvas-reason">
          <Mono size={10.5} tone="dim">{openReason}</Mono>
        </span>
      )}

      <p style={{ fontSize: 11.5, color: "var(--text-faint)", lineHeight: 1.5, margin: 0 }}>
        {FLOWS_FOOTER}
      </p>
    </div>
  );
}

export default FlowsScreen;
