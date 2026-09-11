// NowEmpty.tsx - the screen when nothing is running, which is most of the day.
//
// "NO SESSION RUNNING" ON ITS OWN IS A DEAD END, so this screen answers the
// three questions someone actually opens it with: what did last night do, what
// happens next by itself, and what can I start.
//
// RUN ARMED IS NOT "NO RUN ACTIVE". `_set_state` clears the session sub-block on
// every terminal transition, so a night that ended owing 58 frames leaves
// `sequence` as literally `{state: "idle"}` - and the Monitor used to render
// "No run active - plan a session" over exactly that, which is an instruction to
// strand the armed session (a fresh start disarms every other one). The armed
// state comes from `/api/sequence/resume-arm`, which the shell already polls.
//
// THE REPORT LIST HAS THREE STATES AND ALL THREE ARE DRAWN: loading, a load
// FAILURE with a Retry, and genuinely empty. A failed fetch that rendered "no
// reports yet" would tell an operator their archive was gone.
//
// A VIEWER SEES THIS SCREEN UNCHANGED, with every verb honest-disabled carrying
// the sentence `flowRunControls` already uses. Nothing is hidden.
//
// ---------------------------------------------------------------------------
// TONIGHT'S LIST (D-FU-3, wave plan section 0 Q3). This card used to be RECENT
// FLOWS: three flows, no plans, no idea whether any of them was worth starting.
// Three things changed and each closes a real gap.
//
//   * PLANS ARE ON THE LIST AND THEIR VERB IS RUN. The plan editor is
//     honest-disabled at phone width, so before this a phone could not start a
//     saved plan at ALL - the gap D-FU-3 exists to close. A plan row is not a
//     LOAD that drops the operator into a disabled editor; it loads the plan and
//     starts it, behind one question (`runPlan` below).
//   * TONIGHT'S VERDICT IS THE SECOND SUB-LINE. "Veil east" says nothing about
//     whether the Veil is up tonight; "3h 40m usable from 21:48" does, and
//     "nothing in this flow is up during dark tonight" saves the whole night.
//   * THE LIST IS THE WHOLE LIBRARY, capped at twelve with a door to Flows,
//     rather than a top three that silently hid the rest.
//
// WHAT THIS FILE DOES NOT DO. It does not re-implement running a flow
// (`useFlowRunControls` owns the 409-unmapped question, the abort route and the
// timed-out-abort trap), it does not re-implement the pre-flight
// (`buildPreflight` is the plan editor's own, called with the SAME store
// slices), and it does not compute tonight (the engine does; `tonightVerdict`
// only folds the answer).

import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";

import { api } from "../../../../api";
import { fmtIntegration } from "../../../../api/sessionStack";
import { getPlan, listPlans, type PlanRow } from "../../../../api/plans";
import { listReports } from "../../../../api/reports";
import { resumeSession } from "../../../../api/sessions";
import { endReasonMeta } from "../../../../lib/reportChart";
import { flowsApi } from "../../../../lib/flowsApi";
import { buildPreflight } from "../../../../lib/preflight";
import {
  accessPhrase, useCanControlMount, useCapability, useRoleConnected,
} from "../../../../lib/caps";
import {
  isRunPhaseLive, runBlockedReason, useFlowRunControls,
} from "../../../../components/flows/flowRunControls";
import { runIsLive } from "../../../../lib/lastSessionFrame";
import {
  useMasters, useResumeArm, useSafety, useSeq, useSite, useStatus, useStore,
} from "../../../../store";
import type {
  CheckItem, CheckStatus, SequencePlan, SessionReportSummary, SiteInfo,
} from "../../../../types";
import { ActionButton, Card, EmptyCard, Label, ListRow, Mono } from "../../../ui";
import { NxIcon } from "../../../icons";
import { explainLock } from "../../../shell/explain";
import { nav } from "../../../router";
import { useCampaignFlowId } from "./useCampaign";
import { useFlowLibrary } from "./sessionData";
import { buildRunnables, type Runnable } from "./runnableList";
import {
  TONIGHT_PER_FLOW, TONIGHT_UNCHECKED, tonightVerdict, type TonightVerdict,
} from "./tonightVerdict";
// The Now screen's own stylesheet: a runnable row's two sub-lines have to say
// how they end, and the primitive's ellipsis cannot reach inside them.
import "./now.css";

/** How many flows get a tonight verdict, ever.
 *
 *  `GET /api/flows/{id}/tonight` is ONE ASTROPY EPHEMERIS PASS PER RESOLVED
 *  TARGET (`app.py:4671-4673`), off the event loop but on the rig's own CPU -
 *  which on a deployed appliance is an Orange Pi in a box on the pier. Twelve
 *  rows times a four-member pool would be forty-eight passes fired the moment
 *  an operator opened the app. Five, sequentially, newest first: the rows an
 *  operator is actually choosing between get an answer, and the tail says
 *  honestly that it was not asked rather than showing a blank. */
export const TONIGHT_RESOLVE_CAP = 5;

/** The full list is capped so the card cannot become the Flows screen. */
export const RUNNABLE_ROW_CAP = 12;

/** How long the optimistic `flows.run.phase` is believed on its own.
 *
 *  `flowsRun` sets it the moment `POST /api/flows/{id}/run` returns and NOTHING
 *  ever sets it back - not the socket, not a terminal transition. It is
 *  therefore evidence of a live run only for as long as the engine could still
 *  be about to confirm one; after that the engine's own state is the only fact
 *  on the subject. Generous, because it covers a slow socket over a field link;
 *  bounded, because the alternative is a screen whose RUN button stops working
 *  for the rest of the night. */
export const RUN_PHASE_GRACE_MS = 20_000;

/** The desktop SessionColumn's density. Unchanged from what this card always
 *  showed there: three flows, no plans, no verdicts (see `compact` below). */
export const COMPACT_ROW_CAP = 3;

/** Said ONCE above the list, not repeated per row: a viewer is missing one
 *  capability, not twelve. It names what still works, because the list and RUN
 *  genuinely do - only the ephemeris is withheld. */
export const TONIGHT_LOCK_NOTE =
  `Tonight's verdict needs ${accessPhrase("view.site_derived")}. The list and RUN still work.`;

/** The door out of a capped list. */
export const MORE_IN_FLOWS = "MORE IN FLOWS ›";

/** `buildPreflight` needs a site object even when none is set; `is_default:
 *  true` is what makes its horizon row read "SET LOCATION" instead of a verdict
 *  about a place nobody chose. Same literal as `PreflightStrip.tsx:20-25`. */
const DEFAULT_SITE: SiteInfo = {
  latitude: 0, longitude: 0, is_default: true, horizon_min_deg: 15,
};

type ReportsState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; rows: SessionReportSummary[] };

function useReportList(): { state: ReportsState; retry: () => void } {
  const [state, setState] = useState<ReportsState>({ kind: "loading" });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    let alive = true;
    setState({ kind: "loading" });
    void listReports().then(
      (rows) => { if (alive) setState({ kind: "ready", rows: Array.isArray(rows) ? rows : [] }); },
      (e: Error) => { if (alive) setState({ kind: "error", message: e.message }); },
    );
    return () => { alive = false; };
  }, [nonce]);
  return { state, retry: useCallback(() => setNonce((n) => n + 1), []) };
}

/** The saved-plan library. `view.status` (`app.py:4217-4221`), so every role
 *  gets the rows and only RUN is refused. A failed read leaves `rows` empty and
 *  `error` set - the flows half of the list still renders, and the line says
 *  which half is missing rather than pretending the library is empty. */
function usePlanLibrary(enabled: boolean): { rows: PlanRow[]; error: string | null } {
  const [rows, setRows] = useState<PlanRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    void listPlans().then(
      (r) => { if (alive) { setRows(Array.isArray(r) ? r : []); setError(null); } },
      (e: Error) => { if (alive) setError(e.message); },
    );
    return () => { alive = false; };
  }, [enabled]);
  return { rows, error };
}

// --------------------------------------------------------- tonight, sequential
//
// ONE IN FLIGHT, NEWEST FIRST, AND CACHED FOR THE PAGE. The cache is
// module-level rather than per-mount because the Now screen and the desktop
// SessionColumn both mount this component, StrictMode double-invokes the effect
// in development, and the answer to "what does tonight look like" does not
// change between two renders one frame apart. Nothing invalidates it within a
// page: a night is a night.
const tonightCache = new Map<string, TonightVerdict>();

/** Drop the cache. Tests only. */
export function resetTonightVerdictsForTests(): void {
  tonightCache.clear();
}

function useTonightVerdicts(
  flowIds: string[], enabled: boolean,
): Record<string, TonightVerdict> {
  const [map, setMap] = useState<Record<string, TonightVerdict>>({});
  // The ids as ONE string, so the effect re-runs when the LIST changes and not
  // when React hands back a new array carrying the same list.
  const key = flowIds.join(",");
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!enabled) return;
    const ids = key === "" ? [] : key.split(",");
    let cancelled = false;
    void (async () => {
      for (const id of ids.slice(0, TONIGHT_RESOLVE_CAP)) {
        if (cancelled || !mounted.current) return;
        let verdict = tonightCache.get(id);
        if (!verdict) {
          try {
            verdict = tonightVerdict(await flowsApi.tonight(id), Date.now());
          } catch (e) {
            // A TRANSPORT failure is not a refusal and must not be printed as
            // one: `tonightVerdict` prints the server's own `reason` for a
            // refusal, and this branch says the request itself did not land.
            verdict = {
              tone: "warn",
              line: `tonight could not be read: ${e instanceof Error ? e.message : String(e)}`,
            };
          }
          tonightCache.set(id, verdict);
        }
        if (cancelled || !mounted.current) return;
        const settled = verdict;
        setMap((m) => (m[id] === settled ? m : { ...m, [id]: settled }));
      }
    })();
    return () => { cancelled = true; };
  }, [key, enabled]);

  return map;
}

/** The blocked / warning rows of a pre-flight, as lines for the confirm card.
 *  `CheckItem.detail.value` is the sentence the plan editor's own checklist
 *  prints; this is that text in a smaller frame, never new copy. */
function checkLines(items: CheckItem[], want: CheckStatus[]): string[] {
  return items
    .filter((i) => want.includes(i.status))
    .map((i) => (i.detail?.value ? `${i.label}: ${i.detail.value}` : `${i.label}: ${i.word}`));
}

/** The confirm card's list body. A joined string would collapse into one
 *  run-on sentence: `ConfirmCard` renders `body` as-is and a "\n" inside a text
 *  node is a space (`flowRunControls.tsx` learnt this the same way). */
function ConfirmList({ lines }: { lines: string[] }): JSX.Element {
  return (
    <ul style={{
      display: "flex", flexDirection: "column", gap: 4,
      fontSize: 12, margin: 0, paddingLeft: 16,
    }}>
      {lines.map((l) => <li key={l}>{l}</li>)}
    </ul>
  );
}

export function NowEmpty({ compact = false }: { compact?: boolean }): JSX.Element {
  const seq = useSeq();
  const resumeArm = useResumeArm();
  const safety = useSafety();
  const canControl = useCanControlMount();
  const camera = useRoleConnected("camera");
  const { cards } = useFlowLibrary();
  const { id: campaignFlowId } = useCampaignFlowId();
  const flowControls = useFlowRunControls();
  const flowsOpen = useStore((s) => s.flowsOpen);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const pushConfirm = useStore((s) => s.pushConfirm);
  const setPlan = useStore((s) => s.setPlan);
  const setLoadedPlanId = useStore((s) => s.setLoadedPlanId);
  const editorDirty = useStore((s) => s.editorDirty);
  const status = useStatus();
  const site = useSite();
  const masters = useMasters();
  const { state, retry } = useReportList();
  /** When THIS screen last dispatched a run. See `RUN_PHASE_GRACE_MS`. */
  const startedAt = useRef(0);
  /** The CURRENT render's run/stop toggle, so a handler created before a state
   *  correction does not fire the decision that correction just invalidated. */
  const actRef = useRef(flowControls.act);
  actRef.current = flowControls.act;

  const armed = resumeArm?.armed ?? null;
  const hold = resumeArm?.hold ?? null;
  const last = state.kind === "ready" ? state.rows[0] ?? null : null;

  // THE REFUSAL NAMES WHAT THE ROW IS (T-R7-21a item 11). The gate is one gate
  // - `POST /api/sequence/start` and `POST /api/flows/{id}/run` both need
  // CAP_CONTROL_MOUNT and then a camera - but `runBlockedReason`'s `noun`
  // defaults to "flow", so a saved PLAN row refused with "Running a flow needs
  // operator or admin access" and sent the reader looking for a flow that is
  // not on the screen. Same two conditions, the row's own word.
  const runReason = runBlockedReason(canControl, camera.connected, false);
  const runPlanReason = runBlockedReason(canControl, camera.connected, false, "plan");
  const reasonFor = (kind: Runnable["kind"]): string | null =>
    kind === "plan" ? runPlanReason : runReason;

  // The plan half of the card is off in the desktop column's density, so the
  // library is not fetched there either: that column is on screen all night on
  // every other hub and must not spend a request per mount on rows it does not
  // draw.
  const plans = usePlanLibrary(!compact);

  const runnables = useMemo(() => buildRunnables({
    cards: Array.isArray(cards) ? cards : [],
    plans: compact ? [] : plans.rows,
    seqState: seq.state,
    campaignFlowId,
    armed: armed ? { origin: armed.origin, origin_id: armed.origin_id } : null,
  }), [cards, plans.rows, compact, seq.state, campaignFlowId, armed]);

  const rowCap = compact ? COMPACT_ROW_CAP : RUNNABLE_ROW_CAP;
  const shown = useMemo(() => runnables.slice(0, rowCap), [runnables, rowCap]);
  const hiddenCount = runnables.length - shown.length;

  // The route is CAP_VIEW_SITE_DERIVED, not view.status: every number in the
  // payload is f(latitude, longitude) and a key-name filter cannot withhold it
  // (`app.py:4655-4662`). Without the capability nothing is fired at all - a
  // row of 403s all night is not a graceful degradation.
  const canSeeTonight = useCapability("view.site_derived");
  const wantVerdicts = !compact && canSeeTonight;
  const flowIds = useMemo(
    () => (wantVerdicts ? shown.filter((r) => r.kind === "flow").map((r) => r.id) : []),
    [shown, wantVerdicts],
  );
  const verdicts = useTonightVerdicts(flowIds, wantVerdicts);

  const title = armed ? "RUN ARMED" : "NO SESSION RUNNING";
  const hint = armed
    ? "It starts by itself when its window opens."
    : "Pick a target in Sky, or run a saved flow.";

  /** Shared by both verbs: A LIST ROW NEVER STOPS A RUN.
   *  `useFlowRunControls().act()` is a toggle, so a press while something is
   *  going would abort it - from the row that looked idle
   *  (`FlowsScreen.tsx:123-127`). Returns true when the press has been handled
   *  and the caller must stop.
   *
   *  TWO SOURCES, BECAUSE NEITHER IS ENOUGH ON ITS OWN.
   *
   *  `flows.run.phase` is written optimistically by `flowsRun` and by NOTHING
   *  server-side, so it LEADS the engine by however long the socket takes - and
   *  it is never written back. A guard on the phase alone was therefore stuck on
   *  for the life of the screen: one flow run at 21:00 and every RUN press for
   *  the rest of the night navigated to a Now screen with no run on it, with no
   *  message, on the only phone-reachable way to start a saved plan.
   *
   *  `seq.state` alone is not enough either: it is still `idle` in the seconds
   *  between the POST returning and the engine's first event, which is exactly
   *  the double-press window, and `act()` in that window STOPS the run that was
   *  just started.
   *
   *  So: the engine being live is the fact, and the optimistic phase is trusted
   *  only for as long as the engine could still be about to confirm it. */
  const divertedWhileRunning = useCallback((): boolean => {
    const justStarted = flowControls.running
      && Date.now() - startedAt.current < RUN_PHASE_GRACE_MS;
    if (!runIsLive(seq) && !justStarted) return false;
    nav.go("/session/now");
    return true;
  }, [flowControls, seq]);

  /** Correct the optimistic phase once the engine has been shown to own no run.
   *
   *  `flowsSlice.ts:402` is the ONLY assignment to `flows.run.phase` in the app
   *  and nothing ever writes it back (that slice's own §G-1: there is no flow
   *  run topic and no run-state GET). So after one flow run the flag reads
   *  "running" for the life of the page - and `useFlowRunControls().act()` is a
   *  TOGGLE, so the next press of a button labelled RUN would send
   *  `POST /api/sequence/abort`. Reaching this line means
   *  `divertedWhileRunning()` already answered false: the engine is idle and no
   *  press of ours is outstanding, so the flag is a leftover and is cleared
   *  against the state that is actually authoritative. */
  const clearStaleRunPhase = (): void => {
    const st = useStore.getState();
    if (!isRunPhaseLive(st.flows.run.phase)) return;
    useStore.setState({ flows: { ...st.flows, run: { ...st.flows.run, phase: "idle" } } });
  };

  const runFlow = (id: string) => () => {
    if (divertedWhileRunning()) return;
    if (runReason) { explainLock(runReason); return; }
    clearStaleRunPhase();
    // THE ACT IS TAKEN FROM THE LATEST RENDER, not from this closure.
    // `flowControls.act` captures `running` at render time, so the object this
    // handler was created with would still toggle to STOP even after the line
    // above corrected the flag. React flushes the state write at the end of this
    // event handler, long before `flowsOpen`'s request comes back, so by then
    // the ref holds an `act` that starts.
    
    // The SHARED run control, not a second copy of it: `useFlowRunControls`
    // owns the 409-unmapped question, the abort route and the timeout rule.
    // Selecting the flow first is the only thing this screen adds, and it is
    // also the local re-compile against tonight's ephemeris.
    //
    // NO ARM TWO-TAP, matching `FlowsScreen`: RUN starts a run, it does not end
    // one, and the two-tap arm in this app guards the controls that STOP an
    // unattended night (`now-stop`). Arming a start would train the arm away.
    startedAt.current = Date.now();
    void flowsOpen(id).then(() => actRef.current());
  };

  /** RESUME picks the ARMED session back up; it does not start a fresh run.
   *
   *  THIS ROW USED TO LIE. The verb said RESUME and the handler called the same
   *  `runFlow` as RUN - and a fresh start DISARMS every other session (this
   *  file's own header says so), so the press that promised to finish the 58
   *  owed frames was the press that stranded them. `POST /api/sessions/{id}/
   *  resume` is the route that actually owes-and-continues, and it is reached
   *  through the same `resumeSession` wrapper `FlowsScreen.tsx:140-158` uses,
   *  with the same two answers - because "nothing to resume" is an ANSWER
   *  (`app.py` returns `resumed: false`), not a failure. */
  const resumeArmed = (sessionId: string, kind: Runnable["kind"]) => () => {
    if (divertedWhileRunning()) return;
    const reason = reasonFor(kind);
    if (reason) { explainLock(reason); return; }
    void resumeSession(sessionId).then(
      (r) => enqueueToast(r.resumed
        ? { level: "success", title: "Session resumed", detail: `${r.remaining} frames still owed` }
        : { level: "warning", title: "Nothing to resume", detail: "The session owes no frames." }),
      (e: Error) => enqueueToast({
        level: "error", title: "Resume did not land", detail: e.message,
      }),
    );
  };

  /** RUN for a saved plan (wave plan section 0 Q3).
   *
   *  ROUTE EVIDENCE. `POST /api/sequence/start` takes the WHOLE PLAN in its
   *  body plus a `force` flag - `StartSequenceBody` subclasses `SequencePlan`
   *  and the handler rebuilds a plain plan from it (`app.py:6551-6563`), which
   *  is why `views/SequenceView.tsx:341-343` posts `{ ...plan, force }`. Its
   *  capability is CAP_CONTROL_MOUNT and it then calls `hub.require("camera")`
   *  (`app.py:6551`, `:6625-6627`) - byte for byte the gate `runBlockedReason`
   *  already describes for a flow run, which is why one reason serves both
   *  verbs rather than a second sentence for the same two conditions.
   *
   *  THE PLAN IS LOADED BEFORE IT IS STARTED, and only after the question is
   *  answered YES. `setPlan` replaces whatever the plan editor holds; doing it
   *  before the confirm would destroy an unsaved draft on a press the operator
   *  then answered KEEP to. The card says so when there is a draft to lose.
   *
   *  ONE CARD, and it is the plan editor's own pre-flight behind it:
   *  `buildPreflight` is called with the same store slices `usePreflight` feeds
   *  it, minus the per-target altitude poll (that is a 30 s
   *  `/api/sequence/preflight` interval this screen must not start; its absence
   *  downgrades the horizon row to "altitude unverified", a WARN the card
   *  prints, never a silent pass). A BLOCKED verdict does not become a confirm
   *  with a START button on it - it becomes the same card in `mode: "ok"`,
   *  naming what to fix. */
  const runPlan = (row: Runnable) => () => {
    if (divertedWhileRunning()) return;
    if (runPlanReason) { explainLock(runPlanReason); return; }
    void (async () => {
      let plan: SequencePlan;
      try {
        plan = await getPlan(row.id);
      } catch (e) {
        enqueueToast({
          level: "error",
          title: `Could not read the plan '${row.name}'`,
          detail: e instanceof Error ? e.message : String(e),
        });
        return;
      }

      const items = buildPreflight(status, plan, site ?? DEFAULT_SITE, {}, {}, masters);
      const blocked = checkLines(items, ["blocked"]);
      const warnings = checkLines(items, ["warn", "disabled"]);
      const lights = plan.targets.filter((t) => !t.calibration);
      const first = lights[0] ?? plan.targets[0] ?? null;
      const frames = plan.targets.reduce(
        (a, t) => a + t.steps.reduce((b, s) => b + s.count, 0), 0);

      if (blocked.length > 0) {
        await pushConfirm({
          title: `'${plan.name}' cannot start yet`,
          body: <ConfirmList lines={blocked} />,
          confirmLabel: "OK",
          mode: "ok",
          tone: "warn",
        });
        return;
      }

      const ok = await pushConfirm({
        title: `Start '${plan.name}' now?`,
        body: (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12 }}>
            <span>
              {first
                ? `First target ${first.name}, ${frames} frames across `
                  + `${plan.targets.length} ${plan.targets.length === 1 ? "target" : "targets"}.`
                : `${frames} frames, and no light target in this plan.`}
            </span>
            {editorDirty && (
              <span>The plan editor&apos;s unsaved edits are replaced by this one.</span>
            )}
            {warnings.length > 0 && <ConfirmList lines={warnings} />}
          </div>
        ),
        confirmLabel: "START",
        cancelLabel: "KEEP",
        mode: "confirm",
        tone: "warn",
        confirmPrimary: true,
      });
      if (!ok) return;

      // Loaded, then started - never the other way round.
      setPlan(plan, false);
      setLoadedPlanId(row.id);
      startedAt.current = Date.now();
      try {
        await api.post("/api/sequence/start", { ...plan, force: false });
      } catch (e) {
        enqueueToast({
          level: "error",
          title: `'${plan.name}' did not start`,
          detail: e instanceof Error ? e.message : String(e),
        });
      }
    })();
  };

  const verdictFor = (r: Runnable): TonightVerdict | null => {
    if (compact || !canSeeTonight) return null;
    // The tonight route is FLOW-scoped (`app.py:4652`) and `resolve_tonight` is
    // exposed nowhere else, so a plan row says which surface has the answer
    // instead of guessing at one.
    if (r.kind === "plan") return { tone: "dim", line: TONIGHT_PER_FLOW };
    return verdicts[r.id] ?? { tone: "dim", line: TONIGHT_UNCHECKED };
  };

  return (
    <div data-testid="now-empty" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <EmptyCard
        title={title}
        hint={
          <span style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span>{hint}</span>
            {armed && (
              <Mono size={10} tone="dim">
                {armed.name} - {armed.owed} frames owed ({armed.accepted}/{armed.total})
              </Mono>
            )}
            {armed && hold && (
              <Mono size={10} tone="warn">
                Holding: {hold.reason}. It starts by itself when that clears.
              </Mono>
            )}
            {state.kind === "loading" && <Mono size={10} tone="dim">reading the report archive...</Mono>}
            {state.kind === "error" && (
              <Mono size={10} tone="warn">
                Couldn&apos;t read the list of session reports: {state.message}
              </Mono>
            )}
            {last && (
              <Mono size={10} tone="dim">
                Last night: {last.plan_name} - {fmtIntegration(last.integration_s)} banked,{" "}
                {endReasonMeta(last.end_reason).word.toLowerCase()}.
              </Mono>
            )}
            {state.kind === "ready" && !last && (
              <Mono size={10} tone="dim">No reports yet - the first finished run writes one.</Mono>
            )}
          </span>
        }
        action={
          <>
            <ActionButton
              kind="primary"
              size={compact ? "md" : "xl"}
              onPress={() => nav.hub("sky")}
              data-testid="now-find-target"
            >
              FIND A TARGET
            </ActionButton>
            {state.kind === "error" && (
              <ActionButton kind="secondary" onPress={retry} data-testid="now-reports-retry">
                RETRY
              </ActionButton>
            )}
            {last && (
              <ActionButton
                kind="secondary"
                onPress={() => nav.sheet("files", { src: last.id })}
                data-testid="now-last-files"
              >
                {(last.plan_name || "LAST RUN").toUpperCase()} FILES
              </ActionButton>
            )}
          </>
        }
      />

      {shown.length > 0 && (
        <Card>
          <Label>{compact ? "RECENT FLOWS" : "TONIGHT'S LIST"}</Label>
          {!compact && !canSeeTonight && (
            <div style={{ marginTop: 4 }}>
              <Mono size={10} tone="dim">{TONIGHT_LOCK_NOTE}</Mono>
            </div>
          )}
          {!compact && plans.error && (
            <div style={{ marginTop: 4 }}>
              <Mono size={10} tone="warn">
                Saved plans are missing from this list: {plans.error}
              </Mono>
            </div>
          )}
          <div
            data-testid="now-runnables"
            style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 6 }}
          >
            {shown.map((r) => {
              const verdict = verdictFor(r);
              const live = r.verb === "LIVE";
              return (
                <div key={`${r.kind}:${r.id}`} data-runnable={r.kind} data-runnable-id={r.id}>
                  <ListRow
                    className="nx-runnable"
                    icon={<NxIcon name={r.kind === "flow" ? "flows" : "session"} size={16} />}
                    title={r.name}
                    // Two sub-lines, and each one ends honestly (now.css): the
                    // description ellipsises on one line, the tonight verdict
                    // wraps in full. Before this both were cut mid-sentence
                    // with no ellipsis, and the verdict is the sentence that
                    // says what to go and fix.
                    sub={
                      <span className="nx-runnable-sub">
                        <span className="nx-runnable-meta">{r.meta}</span>
                        {verdict && (
                          <Mono size={10} tone={verdict.tone} className="nx-runnable-verdict">
                            {verdict.line}
                          </Mono>
                        )}
                      </span>
                    }
                    right={
                      <ActionButton
                        kind={live ? "ghost" : "secondary"}
                        onPress={
                          live
                            ? () => nav.hub("session", "now")
                            : r.verb === "RESUME" && armed
                              ? resumeArmed(armed.id, r.kind)
                              : r.kind === "flow" ? runFlow(r.id) : runPlan(r)
                        }
                        lockedReason={live ? null : reasonFor(r.kind)}
                        onExplain={explainLock}
                        data-testid={`run-${r.kind}-${r.id}`}
                      >
                        {r.verb}
                      </ActionButton>
                    }
                  />
                </div>
              );
            })}
          </div>
          {hiddenCount > 0 && (
            <div style={{ marginTop: 8 }}>
              <ActionButton
                kind="ghost"
                onPress={() => nav.hub("session", "flows")}
                data-testid="now-runnables-more"
              >
                {`${MORE_IN_FLOWS} (${hiddenCount} more)`}
              </ActionButton>
            </div>
          )}
        </Card>
      )}

      {armed && safety?.connected === false && (
        <span data-testid="now-armed-no-safety">
          <Mono size={10.5} tone="warn">
            auto-resume armed without a safety monitor - rig may start in bad weather
          </Mono>
        </span>
      )}
    </div>
  );
}
