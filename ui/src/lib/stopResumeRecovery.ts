// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// lib/stopResumeRecovery.ts - the stop that sits beside every screen's
// "Auto-resume is re-centring the mount" line (#246).
//
// WHY THE LINE NEEDS ITS OWN STOP. After a restart ResumeArm's recovery ladder
// blind-solves and re-centres the mount for minutes, with the engine idle, so
// neither Pause nor Abort is on screen. The control that stops it is the
// session's auto-resume switch (H2: a disarm stops the ladder recovering that
// session), and that switch lives in the session lists, not on the screens that
// say the rig is moving. A line that says "the mount is moving on its own" with
// no way to stop it from where it is read is half an answer.
//
// THE SAME REQUEST THE SWITCH SENDS. `PATCH /api/sessions/{id}` with
// `auto_resume: false` for the session the ladder names (`recovery.session_id`),
// which the server routes to `ResumeArm.stop_recovery` before its save. Nothing
// new on the wire: the route is `control.mount`, so a principal without it sees
// the stop locked with the reason, the way every other run control is.
//
// Then the resume-arm route is read once more, so the armed card and the line
// come off as soon as the server says so rather than on the next 20 s poll. A
// failed re-read is left to that poll: the stop itself has already landed.

import { useCallback, useRef, useState } from "react";
import { getResumeArm, patchSession } from "../api/sessions";
import { useStore } from "../store";

export interface StopResumeRecovery {
  /** Disarm `sessionId`, which stops the ladder recovering it. */
  stop: (sessionId: string) => void;
  /** A stop is in flight; the button says so and does not send a second. */
  pending: boolean;
  /** Why the last stop did not land, in the server's words; null otherwise. */
  error: string | null;
}

export function useStopResumeRecovery(): StopResumeRecovery {
  const setResumeArm = useStore((s) => s.setResumeArm);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A ref, not the state flag: two taps land in one React batch and share one
  // closure, so a `pending` check reads false both times (the same reason
  // RecoveryCards' InterruptedRunCard keeps one).
  const inFlight = useRef(false);

  const stop = useCallback((sessionId: string) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setError(null);
    void (async () => {
      try {
        await patchSession(sessionId, { auto_resume: false });
        try {
          setResumeArm(await getResumeArm());
        } catch {
          /* the 20 s poll brings it; the disarm has landed */
        }
      } catch (e) {
        setError(`Auto-resume was not stopped: ${e instanceof Error ? e.message : String(e)}`);
      } finally {
        inFlight.current = false;
        setPending(false);
      }
    })();
  }, [setResumeArm]);

  return { stop, pending, error };
}
