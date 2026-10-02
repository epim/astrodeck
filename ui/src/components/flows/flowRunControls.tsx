// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowRunControls.ts — the one implementation of "start this flow" and "stop
// this flow", shared by the header's RUN/STOP button (§C.3) and the phone
// MONITOR tab's full-width one (README §5).
//
// WHY IT IS A HOOK AND NOT COPIED INTO BOTH BUTTONS. Two of the three things it
// does are war stories, and a second copy is a second place for them to rot:
//
//   * STOP is NOT a flows route. A flow run IS a sequence run — `run_flow` ends
//     in `engine.start(plan)` (app.py:3791) — so the only thing that stops one
//     is `POST /api/sequence/abort`.
//   * A TIMED-OUT abort is not a failed abort. `/api/sequence/abort` awaits the
//     whole ~210 s wind-down against api.ts's 15 s cap, so on a real teardown
//     the abort that WORKED comes back rejected. TouchGuard carries the same
//     trap. Reporting it as a failure tells the operator the rig is still
//     running while it is stopping exactly as asked.
//   * A 409 `unmapped` is a QUESTION, not an error: the server asking whether
//     the operator accepts running a graph part of which will not be honoured.
//     So are CONTINUE's three (#189 S1): `adopt`, `recount` and
//     `dropped_steps`, each asked with ADOPT or CONTINUE and START OVER by
//     `askContinue` below. `runAnsweringQuestions` is the loop that asks them
//     and re-posts; the #/next Sky flow sheet shares both.
//
// This module was extracted from FlowHeader.tsx when the phone MONITOR tab
// needed the same button; FlowHeader re-exports `runBlockedReason` so the
// existing `flowHeaderText.test.ts` import keeps resolving.
//
// WHAT THE BUTTON SAYS AND WHAT THE READOUTS SHOW live here too (#189 S5):
// `useFlowRunControls().copy` is the one RUN / CONTINUE / STOP line and
// `useFlowRunReadouts()` the one set of STATE / ETA / STAGE / FRAMES, both
// computed by the pure `runCopy.ts`. The #/next surfaces reach them through
// this module, which the r7Parity allow-list already names as the one place
// that decides what RUN does and says, so they take on no new legacy import.
import { useCallback, useMemo } from "react";
import { useShallow } from "zustand/react/shallow";

import { api, ApiError } from "../../api";
import { useStore, type ConfirmRequest } from "../../store";
import { accessPhrase, useCanControlMount, useRoleConnected } from "../../lib/caps";
import { disarmedWarningLine, type DisarmedSession } from "../../lib/disarmed";
import type { FlowRunFlags, FlowUnmapped } from "../../lib/flowsApi";
import {
  nextRunFlags, type FlowContinueCode, type FlowContinueQuestion,
  type FlowRunAcceptance, type FlowsActions,
} from "./flowsSlice";
import { flowRunLive, isRunPhaseLive, knownSessions } from "./flowRunState";
import {
  START_OVER_TITLE, runCopy, runReadouts, startOverBody,
  type RunCopy, type RunReadouts,
} from "./runCopy";

export {
  HOPS_NOT_COSTED, MERIDIAN_WAIT_STAGE,
  type RunCopy, type RunReadouts, type RunVerb,
} from "./runCopy";

/** Why RUN cannot act, or null when it can.
 *
 *  Both sentences are §C.3's, and both guards are real: `POST
 *  /api/flows/{id}/run` is gated on CAP_CONTROL_MOUNT (app.py:3713) and then
 *  calls `hub.require("camera")` (app.py:3791), so a run without either fails
 *  at the server with a 403 or a DeviceError.
 *
 *  `running` narrows it: STOP goes to `/api/sequence/abort`, which needs the
 *  same capability but no camera — refusing to stop a live run because no
 *  camera is attached would be a worse answer than the one the rig would give.
 *
 *  `noun` is what the thing being started is CALLED on the surface asking. It
 *  defaults to "flow", so every existing caller keeps the sentence it has,
 *  byte for byte. It exists because the new UI's Session · Now list offers RUN
 *  on saved PLANS beside saved flows through this same gate (both end in
 *  `engine.start(plan)`), and a plan row that refused with "Running a flow
 *  needs operator or admin access." would name something that is not on the
 *  row - the reader then looks for the flow they did not press. */
export function runBlockedReason(
  canControlMount: boolean, cameraConnected: boolean, running: boolean, noun = "flow",
): string | null {
  if (!canControlMount) {
    return running
      ? `Stopping a run needs ${accessPhrase("control.mount")}.`
      : `Running a ${noun} needs ${accessPhrase("control.mount")}.`;
  }
  if (!running && !cameraConnected) {
    return `No camera is connected, so there is nothing to run this ${noun} on.`;
  }
  return null;
}

// `isRunPhaseLive` moved to `flowRunState.ts` at W5 integration (#647), so
// `flowsSlice.ts` can read it too (to clear a finished run's optimistic
// `phase`) without an import cycle back through this file. Re-exported here
// so every existing import of it from "./flowRunControls" keeps resolving.
export { isRunPhaseLive } from "./flowRunState";

// ------------------------------------------------- CONTINUE's questions
//
// The run route continues the flow's own dormant session, and asks before
// that changes what the session's ledger counts (#189 S1, spec 5.9). The body
// of each question is the server's sentence, verbatim and once: it holds the
// numbers being decided on. The title only frames the decision.

/** What the question is deciding. Not a restatement of the sentence under it. */
export const CONTINUE_TITLE: Record<FlowContinueCode, string> = {
  adopt: "Adopt the subs this flow banked before?",
  dropped_steps: "Continue without those steps?",
  recount: "Continue and recount the session?",
};

/** The verb that says yes. ADOPT is not CONTINUE: it rewrites the session's
 *  step ids (after a .bak copy), which CONTINUE never does. */
export const CONTINUE_VERB: Record<FlowContinueCode, string> = {
  adopt: "ADOPT",
  dropped_steps: "CONTINUE",
  recount: "CONTINUE",
};

/** START OVER's one spelling: the button in a CONTINUE question's body, the
 *  button beside CONTINUE on every RUN surface (#189 S5) and the yes of the
 *  confirm that button asks. */
export const START_OVER_LABEL = "START OVER";

/** What START OVER does to the session being asked about, in the server's own
 *  words (continuation.py `adopt_detail`). Shown only under the questions
 *  whose sentence does not already say it - the adopt sentence does, and the
 *  same clause twice is noise. */
export const START_OVER_NOTE = "START OVER begins a new session and leaves this one on disk.";

/** Ask one CONTINUE question. Resolves to the answer to re-post with - the
 *  question's own code for ADOPT/CONTINUE, `"fresh"` for START OVER - or null
 *  for CANCEL, Escape or a tap outside, which start nothing.
 *
 *  THREE ANSWERS THROUGH A TWO-BUTTON PRIMITIVE. `pushConfirm` resolves a
 *  boolean, and both hosts (classic `ConfirmHost`, the new `ConfirmCard`)
 *  draw exactly a cancel and a confirm. START OVER is therefore a button in
 *  the BODY, which both hosts render as-is: it records the choice and closes
 *  the question through `resolveConfirm(false)`. FALSE, not true: if the
 *  recorded choice were ever lost, the answer reads as CANCEL and nothing
 *  starts, rather than as a CONTINUE the operator did not press.
 *
 *  Spans, not paragraphs or a list: the classic host puts the body inside a
 *  `<p>`. `startOverClass` lets each UI dress the button in its own chrome.
 *  Like the graph question, this presentation is UNDESIGNED (§G-2). The
 *  CONTINUE button itself, with the night and counts on it, is S5's
 *  (`runCopy`), and so is the START OVER beside it (`startOver` below), which
 *  asks its own confirm before a single request goes out. */
export async function askContinue(
  q: FlowContinueQuestion,
  pushConfirm: (req: Omit<ConfirmRequest, "resolve">) => Promise<boolean>,
  resolveConfirm: (ok: boolean) => void,
  startOverClass = "btn",
): Promise<FlowRunAcceptance | null> {
  let startOver = false;
  const ok = await pushConfirm({
    title: CONTINUE_TITLE[q.code],
    body: (
      <span style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <span data-testid="continue-detail">{q.detail}</span>
        {q.code !== "adopt" && <span>{START_OVER_NOTE}</span>}
        {/* `flex: none`: `.nx-confirm-btn` is `flex: 1` for the card's own
            button row, which inside this column would override its height. */}
        <button
          type="button"
          className={startOverClass}
          data-testid="confirm-start-over"
          style={{ flex: "none", alignSelf: "stretch" }}
          onClick={() => { startOver = true; resolveConfirm(false); }}
        >
          {START_OVER_LABEL}
        </button>
      </span>
    ),
    confirmLabel: CONTINUE_VERB[q.code],
    cancelLabel: "CANCEL",
    tone: "warn",
    mode: "confirm",
    confirmPrimary: true,
  });
  if (ok) return q.code;
  return startOver ? "fresh" : null;
}

/** How one press of RUN ended. `answered` is the last yes the operator gave
 *  (null when the server asked nothing); `cancelled` is true when they
 *  declined a question, which starts nothing and is not a failure.
 *  `disarmed` is non-empty only when the run that DID start (#643) turned
 *  another session's auto-resume off (#595, D-04) - absent otherwise, the
 *  codebase's own convention, so `outcome.disarmed?.length` reads false for
 *  the overwhelmingly common case with no cast required. */
export interface RunOutcome {
  cancelled: boolean;
  answered: FlowRunAcceptance | null;
  disarmed?: DisarmedSession[];
}

/** Post the run, and answer each question the server asks until it starts,
 *  refuses (the refusal is already on the flow log) or the operator declines.
 *
 *  THE ONE LOOP both RUN paths use - the shared hook below and the #/next Sky
 *  flow sheet - so neither can build a re-post of its own. Every re-post is
 *  `nextRunFlags(<the flags the question came back with>, yes)`, which is what
 *  keeps ADOPT on the request that then answers a dropped-steps CONTINUE.
 *  Each caller brings its own way of asking the graph question, because the
 *  two UIs word and dress that list differently; the CONTINUE questions are
 *  `askContinue` in both.
 *
 *  `first` is what the FIRST request already carries: `{ fresh: true }` for
 *  the START OVER beside CONTINUE (#189 S5), which the operator confirmed
 *  before this loop ran. Every re-post keeps it, because each is built from
 *  the flags the question came back with. Omitted, the first request is
 *  `run()` exactly as it always was. */
export async function runAnsweringQuestions(
  run: FlowsActions["flowsRun"],
  askUnmapped: (list: FlowUnmapped[]) => Promise<boolean>,
  askContinueQuestion: (q: FlowContinueQuestion) => Promise<FlowRunAcceptance | null>,
  first?: FlowRunFlags,
): Promise<RunOutcome> {
  let answered: FlowRunAcceptance | null = null;
  let answer = await (first ? run(first) : run());
  while (answer) {
    // Not a question (#643): the run already started, and the server named
    // sessions it disarmed along the way. Handed straight back as the
    // outcome's own field, never into `askContinueQuestion` - that call
    // expects an actual CONTINUE question's shape, which this is not.
    if (answer.kind === "started") {
      return { cancelled: false, answered, disarmed: answer.disarmed };
    }
    const yes: FlowRunAcceptance | null = answer.kind === "unmapped"
      ? ((await askUnmapped(answer.unmapped)) ? "unmapped" : null)
      : await askContinueQuestion(answer.question);
    if (yes === null) return { cancelled: true, answered };
    answered = yes;
    answer = await run(nextRunFlags(answer.flags, yes));
  }
  return { cancelled: false, answered };
}

/** How long `flows.run.phase`'s optimistic guess is trusted ON ITS OWN, past
 *  the moment `flowsRun` stamped `startedAt` (#647). Past this window the
 *  display defers to the server's own answer (`ours`) alone, whether or not
 *  `flowsSlice.ts`'s own `onSequence` clear has already landed - belt and
 *  braces, since that clear fires only once the server has actually reported
 *  the run over, and a dropped or delayed publish must not leave a STOP
 *  label live forever either. Not imported from `NowEmpty.tsx`'s identical
 *  `RUN_PHASE_GRACE_MS`: that constant answers a different question (divert
 *  navigation to Now), and importing a next/ screen's module into this
 *  shared classic hook would couple the two for no reason beyond sharing a
 *  number. */
export const RUN_PHASE_BRIDGE_MS = 20_000;

export interface FlowRunControls {
  /** Live-run flag; drives the glyph AND the word, so the state is never
   *  carried by colour alone.
   *
   *  TWO SOURCES (#189 S5). `flows.run.phase`, which `flowsRun` writes the
   *  moment its request returns (before the engine's first publish), and
   *  `flowRunLive`: the rig's run IS this flow's, the session the sequence
   *  state writes being one the slice knows as this flow's
   *  (`knownSessions`). The second is what makes the button read STOP over a
   *  run this flow did not start from this page, such as auto-resume on
   *  night two or another browser, and keeps it STOP through the re-read a
   *  save starts, which blanks the progress answer it used to read (#449).
   *
   *  THE FIRST SOURCE IS BOUNDED (#647). Before this fix `phase` stayed
   *  "running" for the rest of the page's life once a run on it had ever
   *  started - so after a run completed, navigating back to the canvas
   *  showed a STOP that `act()` (below) would not actually honour, because
   *  `act()` always decided on `ours` alone (#162). `running` now trusts the
   *  optimistic `phase` only for `RUN_PHASE_BRIDGE_MS` after it was set, and
   *  `flowsSlice.ts`'s `onSequence` clears it outright the moment the server
   *  reports the run over - so once a server answer exists, `ours` is what
   *  decides, same as `act()` already did. */
  running: boolean;
  /** Honest-disabled reason, or null. Never becomes a bare `disabled`. */
  reason: string | null;
  /** Toast the reason — the `onExplain` half of `HonestButton`. */
  explain: (reason: string) => void;
  /** Start, or stop if a run is live. */
  act: () => void;
  /** What the button says: RUN, CONTINUE with the flow's name and the
   *  session's numbers, or STOP (`runCopy`). Every RUN surface prints this. */
  copy: RunCopy;
  /** START OVER, offered beside CONTINUE only (`copy.verb === "CONTINUE"`).
   *  It asks a confirm first and posts `fresh` only on its yes. */
  startOver: () => void;
  /** What `act()` WILL ACTUALLY DO on the next press: stop a run that really
   *  is this flow's (`ours`), never `running`'s bridged/optimistic guess.
   *  `FlowCanvasToolbar.runArm` (#647) gates CONFIRM RUN on this, not on
   *  `copy.verb`, so a press whose real action is a start is confirmed
   *  whatever the label happens to read - the two could disagree for up to
   *  `RUN_PHASE_BRIDGE_MS`, or longer still if a publish were ever dropped. */
  stopsOnPress: boolean;
}

/** Ask the graph question: the compile's losses, as the run route's 409
 *  `unmapped` lists them. True runs anyway. */
function askUnmappedWith(
  pushConfirm: (req: Omit<ConfirmRequest, "resolve">) => Promise<boolean>,
): (unmapped: FlowUnmapped[]) => Promise<boolean> {
  return async (unmapped) => {
    // An empty list asks nothing and runs nothing, as it always has.
    if (unmapped.length === 0) return false;
    // WARNING: the presentation is UNDESIGNED (§G-2): no screenshot, no
    // README paragraph. This uses the app's existing confirm primitive
    // unchanged - the least-committal thing that renders the server's own
    // list. The title is app.py:3760's own refusal sentence, not new copy.
    return pushConfirm({
      title: "Parts of this flow do not survive the compile",
      // A list, not a joined string: `ConfirmHost` renders `body` as-is,
      // and a "\n" inside a text node collapses to a space — five
      // refusals would arrive as one run-on sentence.
      body: (
        <ul className="flex flex-col gap-1.5 text-[12px] text-dim">
          {unmapped.map((u) => <li key={u.key}>{u.detail}</li>)}
        </ul>
      ),
      confirmLabel: "RUN ANYWAY",
      cancelLabel: "CANCEL",
      tone: "warn",
      mode: "confirm",
      confirmPrimary: true,
    });
  };
}

export function useFlowRunControls(): FlowRunControls {
  const phase = useStore((s) => s.flows.run.phase);
  const phaseStartedAt = useStore((s) => s.flows.run.startedAt);
  // A boolean, so exact under Object.is: a frame landing on the run wakes
  // nothing here unless it changes whose run it is. Over the known sessions,
  // never the progress answer (#449): see `running` above.
  const ours = useStore((s) => flowRunLive(knownSessions(s.flows), s.sequence));
  // #647: the optimistic `phase` only bridges the gap between posting RUN
  // and the engine's first answer - bounded by `RUN_PHASE_BRIDGE_MS` from
  // the moment `flowsRun` stamped it, so a `phase` that outlives both the
  // bridge AND `flowsSlice.ts`'s own end-of-run clear (a dropped or delayed
  // publish) cannot keep reading live forever either. `ours` always wins
  // when true; this is the ONLY case where `running` can be true while
  // `ours` is false.
  const bridging = isRunPhaseLive(phase) && phaseStartedAt !== null
    && Date.now() - phaseStartedAt < RUN_PHASE_BRIDGE_MS;
  const running = ours || bridging;

  // The copy's two inputs. `progress` changes identity only when a new
  // answer lands (open, save, a started run, a frame on a live one, at most
  // every 30 s), so the memo below recomputes that rarely.
  const name = useStore((s) => s.flows.record?.name ?? "");
  const progress = useStore((s) => s.flows.progress);
  const copy = useMemo(() => runCopy(name, progress, running), [name, progress, running]);

  const canControlMount = useCanControlMount();
  const camera = useRoleConnected("camera");

  const run = useStore((s) => s.flowsRun);
  const appendLog = useStore((s) => s.flowsAppendLog);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const pushConfirm = useStore((s) => s.pushConfirm);
  const resolveConfirm = useStore((s) => s.resolveConfirm);

  const explain = useCallback(
    (reason: string) => enqueueToast({ level: "warning", title: reason }),
    [enqueueToast],
  );

  // #643 (W5 integration): the same warning `NowEmpty.tsx`'s WP-65 fix shows
  // for its two routes, now for this one (`POST /api/flows/{id}/run`, both
  // the plain start and the `fresh` one START OVER sends) - the route that
  // fix could not reach because `flowsRun` discarded the response's
  // `disarmed` list before any `NowEmpty.tsx`-owned code ever saw it.
  const warnIfDisarmed = useCallback((outcome: RunOutcome) => {
    if (outcome.disarmed && outcome.disarmed.length > 0) {
      enqueueToast({
        level: "warning",
        title: "Starting this flow disarmed another session",
        detail: disarmedWarningLine(outcome.disarmed),
      });
    }
  }, [enqueueToast]);

  const stop = useCallback(async () => {
    try {
      await api.post("/api/sequence/abort");
      // README §6's exact sentence. The engine really does park nothing on a
      // manual stop, and the operator has to be told that at the moment they
      // walk away, not later.
      appendLog("Run stopped by user — engine parks nothing on manual stop", "warn");
    } catch (e) {
      if (e instanceof ApiError && e.timedOut) {
        appendLog("Stop sent — the rig is winding the run down", "warn");
        return;
      }
      const detail = e instanceof Error ? e.message : String(e);
      appendLog(`could not stop: ${detail}`, "bad");
      enqueueToast({ level: "error", title: "STOP did not land", detail });
    }
  }, [appendLog, enqueueToast]);

  const start = useCallback(async () => {
    const outcome = await runAnsweringQuestions(
      run,
      askUnmappedWith(pushConfirm),
      (q) => askContinue(q, pushConfirm, resolveConfirm),
    );
    warnIfDisarmed(outcome);
  }, [pushConfirm, resolveConfirm, run, warnIfDisarmed]);

  // START OVER, BEHIND A CONFIRM (spec 5.9: "START OVER behind a confirm").
  // The press walks away from a ledger CONTINUE would carry on, for good:
  // CONTINUE reads only the newest session this flow started, so once the
  // fresh one exists the old one is never reopened. So nothing is posted
  // until the operator has read that and said yes, and CANCEL, Escape or a
  // tap outside post nothing at all. The yes then runs the ordinary loop with
  // `fresh` on the first request: the server can still ask the graph
  // question, and every re-post keeps `fresh`.
  const startOver = useCallback(async () => {
    const ok = await pushConfirm({
      title: START_OVER_TITLE,
      body: startOverBody(copy),
      confirmLabel: START_OVER_LABEL,
      cancelLabel: "CANCEL",
      tone: "warn",
      mode: "confirm",
    });
    if (!ok) return;
    const outcome = await runAnsweringQuestions(
      run,
      askUnmappedWith(pushConfirm),
      (q) => askContinue(q, pushConfirm, resolveConfirm),
      { fresh: true },
    );
    warnIfDisarmed(outcome);
  }, [copy, pushConfirm, resolveConfirm, run, warnIfDisarmed]);

  // THE ACTION IS DECIDED ON `ours`, NEVER ON `running` (#162). `running` is a
  // DISPLAY flag: it is OR'd with the client's own phase latch (bridged and
  // cleared now, #647 - see `running`'s own doc comment above - but still
  // only a guess while it applies) so the button reads STOP the instant a
  // press lands, before the engine's first publish. Deciding the ACTION on
  // `running` would call `stop()` for however long the bridge or a dropped
  // publish left the latch live, posting `/api/sequence/abort` to whatever
  // the engine is doing next - including a run started elsewhere. `ours`
  // (`flowRunLive` over `knownSessions`) is the half of `running` that is
  // grounded in the rig's own state: the session the sequence state publishes
  // NOW is one of this flow's. The pattern of the Send-to-Wizard fix (#454,
  // SendToWizardSheet.tsx "THE START IS JUDGED ON WHAT THIS PRESS WROTE"): act
  // on what the rig's own state says, not on a client flag that can still be
  // ahead of or behind it. A press made against a stale or bridging latch
  // therefore tries to START - harmless when nothing else is running, and a
  // refusal logged rather than a stranger's night cut short when something
  // is; `stopsOnPress` below (`ours`, same source as this decision) is what
  // lets the confirm agree with it even while `running`'s display does not.
  const act = useCallback(() => {
    void (ours ? stop() : start());
  }, [ours, start, stop]);
  const pressStartOver = useCallback(() => { void startOver(); }, [startOver]);

  return {
    running,
    reason: runBlockedReason(canControlMount, camera.connected, running),
    explain,
    act,
    copy,
    startOver: pressStartOver,
    stopsOnPress: ours,
  };
}

/** The run readouts every monitor and the two ETA slots draw (#189 S5).
 *
 *  Fed from the sequence state while `flowRunLive` says the rig's run is this
 *  flow's, over the sessions the slice knows as the flow's (`knownSessions`,
 *  #449), and the idle values in `flows.run` otherwise (`runReadouts`).
 *  Returned through `useShallow`: every member is a primitive, so a publish
 *  that changes none of them (a sky reading, a guide RMS, a hold's reason)
 *  re-renders none of the surfaces that read it. */
export function useFlowRunReadouts(): RunReadouts {
  return useStore(useShallow((s) => runReadouts(knownSessions(s.flows), s.sequence, s.flows.run)));
}
