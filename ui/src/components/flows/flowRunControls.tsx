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
//
// This module was extracted from FlowHeader.tsx when the phone MONITOR tab
// needed the same button; FlowHeader re-exports `runBlockedReason` so the
// existing `flowHeaderText.test.ts` import keeps resolving.
import { useCallback } from "react";

import { api, ApiError } from "../../api";
import { useStore } from "../../store";
import { accessPhrase, useCanControlMount, useRoleConnected } from "../../lib/caps";

/** Why RUN cannot act, or null when it can.
 *
 *  Both sentences are §C.3's, and both guards are real: `POST
 *  /api/flows/{id}/run` is gated on CAP_CONTROL_MOUNT (app.py:3713) and then
 *  calls `hub.require("camera")` (app.py:3791), so a run without either fails
 *  at the server with a 403 or a DeviceError.
 *
 *  `running` narrows it: STOP goes to `/api/sequence/abort`, which needs the
 *  same capability but no camera — refusing to stop a live run because no
 *  camera is attached would be a worse answer than the one the rig would give. */
export function runBlockedReason(
  canControlMount: boolean, cameraConnected: boolean, running: boolean,
): string | null {
  if (!canControlMount) {
    return running
      ? `Stopping a run needs ${accessPhrase("control.mount")}.`
      : `Running a flow needs ${accessPhrase("control.mount")}.`;
  }
  if (!running && !cameraConnected) {
    return "No camera is connected, so there is nothing to run this flow on.";
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
    const unmapped = await run();
    if (!unmapped || unmapped.length === 0) return;
    // ⚠ The presentation is UNDESIGNED (§G-2): no screenshot, no README
    // paragraph. This uses the app's existing confirm primitive unchanged —
    // the least-committal thing that renders the server's own list. The title
    // is app.py:3760's own refusal sentence, not new copy.
    const ok = await pushConfirm({
      title: "Parts of this flow do not survive the compile",
      // A list, not a joined string: `ConfirmHost` renders `body` as-is, and a
      // "\n" inside a text node collapses to a space — five refusals would
      // arrive as one run-on sentence.
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
    if (ok) await run(true);
  }, [pushConfirm, run]);

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
