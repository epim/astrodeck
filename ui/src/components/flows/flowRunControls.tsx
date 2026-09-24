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
import { useCallback } from "react";

import { api, ApiError } from "../../api";
import { useStore, type ConfirmRequest } from "../../store";
import { accessPhrase, useCanControlMount, useRoleConnected } from "../../lib/caps";
import type { FlowUnmapped } from "../../lib/flowsApi";
import {
  nextRunFlags, type FlowContinueCode, type FlowContinueQuestion,
  type FlowRunAcceptance, type FlowsActions,
} from "./flowsSlice";

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

/** True while the engine still owns the rig.
 *
 *  `stopping` counts: the engine publishes "aborting" the moment teardown
 *  starts and only says stopped once the rig has, so a button that flipped back
 *  to ▶ RUN here would offer to start over a moving mount. */
export function isRunPhaseLive(phase: string): boolean {
  return phase === "running" || phase === "holding" || phase === "stopping";
}

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
 *  Like the graph question, this presentation is UNDESIGNED (§G-2); the
 *  designed CONTINUE button, with the night and counts on it, is slice S5. */
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
 *  declined a question, which starts nothing and is not a failure. */
export interface RunOutcome {
  cancelled: boolean;
  answered: FlowRunAcceptance | null;
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
 *  `askContinue` in both. */
export async function runAnsweringQuestions(
  run: FlowsActions["flowsRun"],
  askUnmapped: (list: FlowUnmapped[]) => Promise<boolean>,
  askContinueQuestion: (q: FlowContinueQuestion) => Promise<FlowRunAcceptance | null>,
): Promise<RunOutcome> {
  let answered: FlowRunAcceptance | null = null;
  let answer = await run();
  while (answer) {
    const yes: FlowRunAcceptance | null = answer.kind === "unmapped"
      ? ((await askUnmapped(answer.unmapped)) ? "unmapped" : null)
      : await askContinueQuestion(answer.question);
    if (yes === null) return { cancelled: true, answered };
    answered = yes;
    answer = await run(nextRunFlags(answer.flags, yes));
  }
  return { cancelled: false, answered };
}

export interface FlowRunControls {
  /** Live-run flag; drives the glyph AND the word, so the state is never
   *  carried by colour alone. */
  running: boolean;
  /** Honest-disabled reason, or null. Never becomes a bare `disabled`. */
  reason: string | null;
  /** Toast the reason — the `onExplain` half of `HonestButton`. */
  explain: (reason: string) => void;
  /** Start, or stop if a run is live. */
  act: () => void;
}

export function useFlowRunControls(): FlowRunControls {
  const phase = useStore((s) => s.flows.run.phase);
  const running = isRunPhaseLive(phase);

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
    await runAnsweringQuestions(
      run,
      async (unmapped) => {
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
      },
      (q) => askContinue(q, pushConfirm, resolveConfirm),
    );
  }, [pushConfirm, resolveConfirm, run]);

  const act = useCallback(() => {
    void (running ? stop() : start());
  }, [running, start, stop]);

  return {
    running,
    reason: runBlockedReason(canControlMount, camera.connected, running),
    explain,
    act,
  };
}
