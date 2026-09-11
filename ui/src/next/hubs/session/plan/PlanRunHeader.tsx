// PlanRunHeader.tsx - what the run is doing, and the three controls that change
// it: PAUSE / RESUME, STOP, and the terminal-state actions.
//
// The rebuild of `views/SequenceView.tsx:728-970` (the `Sequence - <plan>`
// panel) plus `components/sequence/SequenceRunStrip.tsx` (the glance line). It
// carries five pieces of scar tissue verbatim, none of which may be
// simplified away:
//
// 1. A TIMED-OUT STOP IS NOT A FAILED STOP. `/api/sequence/abort` awaits the
//    whole wind-down - abort the exposure, stop the guider, finalize the
//    report, drain the thumbnails - which routinely outlives the client's
//    budget. The engine's next state frame is the answer; the request's promise
//    never was. `sendControl` already reports `timedOut` separately, and the
//    local latch below stays armed through it.
//
// 2. THE STOP LATCH IS TWO LATCHES. A 60 s local timer covers the gap before
//    the engine's first `aborting` frame (and an old server that never sends
//    one); the server's own `aborting` state covers a teardown that outlives
//    the timer and a client that arrived mid-teardown.
//
// 3. THE RESUME GUARD IS A REF. Two taps land in one React batch and share one
//    closure, so `if (resuming) return` reads false both times and the second
//    tap reaches a route whose session the first has already claimed - which
//    answers "no resumable sequence found" over a resume that started
//    perfectly. Only a ref is already true when the second handler runs.
//
// 4. PAUSING IS CLIENT-COMPUTED. `pause()` clears a flag the run loop only
//    rechecks at the next frame boundary, so the shutter that was open when you
//    tapped stays open - up to ten minutes on narrowband - while the badge says
//    PAUSED. That gap is what someone reads before walking out to the scope, so
//    it gets a word (PAUSING), a countdown and an instruction.
//
// 5. RESUME AND RE-RUN DIFFER BY AN HOUR OF CLEAR SKY, and the difference is
//    said IN WORDS, not by button order. Resume picks up at frame N; re-run
//    starts at frame 1 and re-shoots what is already on disk.
//
// STOP is two taps (`ActionButton`'s own arm), not a hold and not a modal: at
// 3 a.m. in gloves a dialog that steals focus is worse than a button that has
// to be meant.

import { useEffect, useRef, useState, type JSX } from "react";

import { accessPhrase, useCanControlMount } from "../../../../lib/caps";
import { fmtCountdown } from "../../../../lib/eta";
import { formatScheduleStatus } from "../../../../lib/scheduleStatus";
import { diagnoseFailure } from "../../../../lib/troubleshoot";
import { useLastReportId, useSeq } from "../../../../store";
import type { SequencePlan } from "../../../../types";
import { LOG_ROUTE } from "../../../legacyBridge";
import { nav } from "../../../router";
import { explainLock } from "../../../shell/explain";
import { ActionButton, Bar, Card, Label, LockNote, Mono, Pill, StatusPill } from "../../../ui";
import { sendControl, useSubFrame } from "../now";
import {
  isSequenceFinished, isSequenceLive, runningPlanName, stateView, type Recoverable,
} from "./planModel";

/** The one sentence every locked run control in this sheet gives. Exported so
 *  the test asserts the SAME string the control renders, not a copy of it. */
export const RUN_CONTROL_REASON =
  `Pausing, resuming or stopping this run needs ${accessPhrase("control.mount")}.`;

/** Said on the re-run control, because starting over is the expensive one. */
export const RERUN_TITLE =
  "Starts over at frame 1 - the frames already on disk are not reused";

export function PlanRunHeader({ plan, recoverable, onStart, startLockedReason }: {
  plan: SequencePlan;
  /** The backend's resume offer, or null. Owned by the root so the resume card
   *  and this panel cannot disagree about whether one exists. */
  recoverable: Recoverable | null;
  /** Re-run: the root's single writer of `POST /api/sequence/start`. */
  onStart: () => void;
  startLockedReason: string | null;
}): JSX.Element | null {
  const seq = useSeq();
  const canRun = useCanControlMount();
  const lastReportId = useLastReportId();

  const [aborting, setAborting] = useState(false);
  const [resuming, setResuming] = useState(false);
  const resumeInFlight = useRef(false);

  const live = isSequenceLive(seq.state);
  const finished = isSequenceFinished(seq.state);
  const serverAborting = seq.state === "aborting";
  const abortInFlight = aborting || serverAborting;

  // The local latch expires so a dropped socket cannot leave the row dead.
  // Re-posting is safe: stopping a finished task just republishes "aborted".
  useEffect(() => {
    if (!aborting) return;
    if (!live) { setAborting(false); return; }
    const t = window.setTimeout(() => setAborting(false), 60_000);
    return () => window.clearTimeout(t);
  }, [aborting, live]);

  useEffect(() => {
    if (!resuming) return;
    if (seq.state === "running") { resumeInFlight.current = false; setResuming(false); return; }
    // The POST resolves BEFORE the engine's first "running" publish, and it is
    // that gap the second tap falls into. Expire so a resume that never took
    // cannot leave the button dead.
    const t = window.setTimeout(() => { resumeInFlight.current = false; setResuming(false); }, 8000);
    return () => window.clearTimeout(t);
  }, [resuming, seq.state]);

  const sub = useSubFrame(seq.progress, seq.state === "paused");
  const pausing = seq.state === "paused" && sub.remainingS != null;

  if (!live && !finished) return null;

  const view = stateView(seq.state, pausing);
  const planName = runningPlanName(seq.plan_name, plan.name);
  const diag = diagnoseFailure(seq.detail, {
    state: seq.state,
    framesDone: seq.progress?.frames_done,
    framesTotal: seq.progress?.frames_total,
  });
  const failed = seq.state === "error" || seq.state === "aborted";
  const stoppedByUser = !!diag.userInitiated;
  const lockedReason = canRun ? null : RUN_CONTROL_REASON;
  const schedule = formatScheduleStatus(seq.schedule, seq.live, Date.now() / 1000);
  const p = seq.progress;
  // Resume-from-N is offered whenever the BACKEND says the run is recoverable,
  // for BOTH terminal states. Narrowing it to `error` excluded the far more
  // common `aborted`, which is how night two offered RE-RUN and no resume.
  const resumable = recoverable != null && failed;

  const resume = (path: "/api/sequence/recover" | "/api/sequence/resume") => {
    if (resumeInFlight.current) return;
    resumeInFlight.current = true;
    setResuming(true);
    void sendControl("Resume", path, { title: "Resuming" }).then((r) => {
      if (!r.ok && !r.timedOut) { resumeInFlight.current = false; setResuming(false); }
    });
  };

  const stop = () => {
    if (abortInFlight) return;
    setAborting(true);
    void sendControl("Stop", "/api/sequence/abort", {
      title: "Stopping",
      detail: "Ending the exposure and stopping the guider - the engine parks nothing "
        + "on a manual stop.",
    }).then((r) => { if (!r.ok && !r.timedOut) setAborting(false); });
  };

  return (
    <Card tone={failed && !stoppedByUser ? "accent" : "default"} data-testid="plan-run-header">
      <div className="nx-plan-stack">
        <div className="nx-plan-row">
          <Label size={11}>SEQUENCE</Label>
          <span style={{ marginLeft: "auto" }}>
            <StatusPill text={view.word} tone={view.tone} pulse={view.pulse} />
          </span>
        </div>
        <Mono size={11}>{planName}</Mono>

        {/* The glance line: which target, which frame of how many, how far
            through, and what is left. Every number rides the sequence bus
            event; none of it is a client timer. */}
        {p && (
          <>
            <Bar value={Math.max(0, Math.min(1, p.percent / 100))} height={6}
              tone={failed ? "bad" : "accent"}
              label={`${Math.round(p.percent)} percent of the plan complete`} />
            <Mono size={10.5} tone="dim" data-testid="plan-run-progress">
              {seq.target ? `${seq.target} · ` : ""}
              frame {Math.min(p.frames_done + 1, p.frames_total)}/{p.frames_total}
              {p.rejected > 0 ? ` · ${p.rejected} rejected` : ""}
              {` · ${Math.floor(p.elapsed_s / 60)}m elapsed`}
              {p.eta_s != null
                ? ` · ${p.eta_confident === false ? "about " : ""}${fmtCountdown(p.eta_s)} left`
                : ""}
            </Mono>
          </>
        )}
        {!failed && seq.detail && <Mono size={10} tone="dim">{seq.detail}</Mono>}

        {/* The runtime autorun-schedule line: why imaging is not happening even
            though the run is live. Absent entirely when the engine attached no
            schedule block. */}
        {schedule && (
          <p className="nx-plan-note" data-tone={schedule.tone === "warn" ? "warn" : undefined}
            data-testid="plan-run-schedule">
            {schedule.text}
          </p>
        )}

        {/* The half of the pause the engine cannot report. */}
        {pausing && (
          <div className="nx-plan-notice" data-tone="warn" role="alert" data-testid="plan-pausing">
            <Mono size={11}>
              Pausing - this frame still has {fmtCountdown(sub.remainingS!)} of shutter left,
              and the run does not stop until it lands. Keep lights off and hands off the
              scope until this reads PAUSED.{canRun ? " Stop ends it now." : ""}
            </Mono>
          </div>
        )}

        {/* A run the operator stopped on purpose is not a failure: it reads as
            itself, on a neutral card, with no advisory and no raw engine echo
            underneath - that echo was the engine's own wording for the action
            the user had just taken. */}
        {failed && (
          <div className="nx-plan-notice" data-tone={stoppedByUser ? "dim" : "bad"}
            data-testid="plan-run-failure">
            <Mono size={11}>
              {stoppedByUser ? diag.title
                : seq.state === "error" ? "Sequence failed" : "Sequence aborted"}
            </Mono>
            <p className="nx-plan-note" data-tone="ink">
              {diag.cause}{diag.fix ? ` ${diag.fix}` : ""}
            </p>
            {diag.topic && (
              <ActionButton kind="ghost" size="md"
                onPress={() => nav.go(`/settings/help?topic=${encodeURIComponent(diag.topic!)}`)}
                data-testid="plan-run-help">HOW TO FIX</ActionButton>
            )}
            {seq.detail && !stoppedByUser && (
              <Mono size={10} tone="dim">{seq.detail}</Mono>
            )}
          </div>
        )}

        {/* ------------------------------------------------------- controls */}
        {live && (
          <div className="nx-plan-row">
            {!canRun && <Pill tone="warn" data-testid="plan-view-only">VIEW ONLY</Pill>}
            {seq.state === "paused" ? (
              <ActionButton
                kind="secondary"
                onPress={() => resume("/api/sequence/resume")}
                busy={resuming}
                lockedReason={lockedReason}
                onExplain={explainLock}
                ariaLabel={pausing
                  ? "The pause has not taken effect yet - this keeps the run going"
                  : undefined}
                data-testid="plan-resume"
              >
                {resuming ? "RESUMING" : pausing ? "CANCEL PAUSE" : "RESUME"}
              </ActionButton>
            ) : (
              <ActionButton
                kind="secondary"
                onPress={() => {
                  void sendControl("Pause", "/api/sequence/pause", {
                    title: "Pausing",
                    detail: "Any exposure already in flight finishes first - the run stops "
                      + "at the next frame boundary.",
                  });
                }}
                lockedReason={lockedReason}
                onExplain={explainLock}
                data-testid="plan-pause"
              >
                PAUSE
              </ActionButton>
            )}
            <ActionButton
              kind="danger"
              arm={{ label: "TAP AGAIN TO STOP", ms: 3000 }}
              onPress={stop}
              busy={abortInFlight}
              lockedReason={lockedReason}
              onExplain={explainLock}
              ariaLabel={abortInFlight
                ? "Stopping the sequence - ending the exposure and stopping the guider"
                : undefined}
              data-testid="plan-stop"
            >
              {abortInFlight ? "STOPPING" : "STOP"}
            </ActionButton>
          </div>
        )}

        {/* -------------------------------------------- terminal-state actions */}
        {finished && (
          <div className="nx-plan-row">
            {!canRun && <Pill tone="warn" data-testid="plan-view-only">VIEW ONLY</Pill>}
            {resumable && recoverable && (
              <ActionButton
                kind="primary"
                onPress={() => resume("/api/sequence/recover")}
                busy={resuming}
                lockedReason={lockedReason}
                onExplain={explainLock}
                data-testid="plan-resume-from"
              >
                {resuming ? "RESUMING"
                  : `RESUME FROM FRAME ${recoverable.frames_done}/${recoverable.frames_total}`}
              </ActionButton>
            )}
            <ActionButton
              kind={resumable ? "secondary" : "primary"}
              arm={{ label: "TAP AGAIN TO RE-RUN", ms: 3000 }}
              onPress={onStart}
              lockedReason={startLockedReason}
              onExplain={explainLock}
              ariaLabel={RERUN_TITLE}
              data-testid="plan-rerun"
            >
              RE-RUN PLAN
            </ActionButton>
            <ActionButton kind="ghost" onPress={() => nav.go(LOG_ROUTE)}
              data-testid="plan-view-log">VIEW LOG</ActionButton>
            {lastReportId && (
              <ActionButton kind="ghost"
                onPress={() => nav.sheet("report", { id: lastReportId })}
                data-testid="plan-view-report">SESSION REPORT</ActionButton>
            )}
          </div>
        )}

        {/* Said in words, not by button order alone - the distinction that costs
            75 minutes has to survive a glance in the dark. */}
        {finished && resumable && recoverable && (
          <p className="nx-plan-note">
            Resume picks up at frame {recoverable.frames_done} of {recoverable.frames_total}.
            {" "}Re-run starts over from frame 1 and re-shoots what you already have.
          </p>
        )}

        <LockNote reason={live || finished ? lockedReason : null} data-testid="plan-run-lock" />
      </div>
    </Card>
  );
}
