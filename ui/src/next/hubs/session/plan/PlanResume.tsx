// PlanResume.tsx - "the server restarted mid-run" as a state with its own two
// actions (`views/SequenceView.tsx:712-727`).
//
// It renders only when the backend says a run is recoverable AND there is no
// live or finished run panel above it - a finished run's own RESUME FROM FRAME
// N lives in `PlanRunHeader`, and two resume buttons on one screen is how a
// user learns to distrust both.
//
// DISCARD IS NEW HERE, AND IT IS A REAL SERVER VERB. The legacy card offered
// only Resume, so an offer the user did not want came back every time this
// editor was opened - and the only way out was to start a different run over
// the top of it. `PATCH /api/sessions/{id} {status:"abandoned"}` is the
// server's own way to retire a dormant session (`app.py:4994-4999`; it refuses
// on an ACTIVE one, which is why the card is absent while a run is live). It is
// gated on `control.mount`, exactly like the resume it cancels, and armed as a
// two-tap because giving up frames already on disk is not a routine tap.

import { useRef, useState, type JSX } from "react";

import { patchSession } from "../../../../api/sessions";
import { accessPhrase, useCanControlMount } from "../../../../lib/caps";
import { fmtClock } from "../../../../lib/eta";
import { useStore } from "../../../../store";
import { explainLock } from "../../../shell/explain";
import { ActionButton, Card, Label, LockNote, Mono } from "../../../ui";
import { sendControl } from "../now";
import type { Recoverable } from "./planModel";

export const RESUME_REASON =
  `Resuming or discarding an interrupted run needs ${accessPhrase("control.mount")}.`;

export function PlanResume({ rec, onDone }: {
  rec: Recoverable;
  /** Called once the offer has been taken or given up, so the card retires
   *  without waiting for the next `running` edge. */
  onDone: () => void;
}): JSX.Element {
  const canRun = useCanControlMount();
  const [busy, setBusy] = useState<"resume" | "discard" | null>(null);
  // Two taps land in one React batch and share one closure, so a state flag
  // reads false both times; only a ref is already true when the second handler
  // runs. Without it the second tap reaches a route whose session the first has
  // already claimed, and answers "no resumable sequence found" over a resume
  // that started perfectly.
  const inFlight = useRef(false);

  const lockedReason = canRun ? null : RESUME_REASON;
  const when = rec.ts ? fmtClock(rec.ts * 1000) : null;

  const resume = () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy("resume");
    void sendControl("Resume", "/api/sequence/recover", { title: "Resuming" }).then((r) => {
      if (r.ok || r.timedOut) { onDone(); return; }
      inFlight.current = false;
      setBusy(null);
    });
  };

  const discard = () => {
    if (inFlight.current) return;
    if (!rec.sessionId) {
      useStore.getState().enqueueToast({
        level: "warning",
        title: "This server did not say which session the offer belongs to",
        detail: "Resume still works; discarding needs a server that reports session_id.",
      });
      return;
    }
    inFlight.current = true;
    setBusy("discard");
    void patchSession(rec.sessionId, { status: "abandoned" }).then(
      () => {
        useStore.getState().enqueueToast({
          level: "info",
          title: `Discarded the interrupted run "${rec.name}"`,
          detail: "The frames stay on disk; only the offer to resume is gone.",
        });
        onDone();
      },
      (e: Error) => {
        inFlight.current = false;
        setBusy(null);
        useStore.getState().enqueueToast({ level: "error", title: "Discard failed", detail: e.message });
      },
    );
  };

  return (
    <Card tone="accent" data-testid="plan-resume-card">
      <div className="nx-plan-stack">
        <Label size={11}>RESUME INTERRUPTED RUN</Label>
        <Mono size={10.5} tone="dim">
          {rec.name} stopped at frame {rec.frames_done} of {rec.frames_total}
          {when ? ` at ${when}` : ""}. Resume picks up where it left off; the frames
          already on disk are intact either way.
        </Mono>
        <div className="nx-plan-row">
          <ActionButton
            kind="primary"
            onPress={resume}
            busy={busy === "resume"}
            lockedReason={lockedReason}
            onExplain={explainLock}
            data-testid="plan-resume-run"
          >
            {busy === "resume" ? "RESUMING" : "RESUME"}
          </ActionButton>
          <ActionButton
            kind="ghost"
            arm={{ label: "TAP AGAIN TO DISCARD", ms: 3000 }}
            onPress={discard}
            busy={busy === "discard"}
            lockedReason={lockedReason}
            onExplain={explainLock}
            ariaLabel="Discard this interrupted run - the frames stay on disk, only the resume offer goes"
            data-testid="plan-discard"
          >
            DISCARD
          </ActionButton>
        </div>
        <LockNote reason={lockedReason} data-testid="plan-resume-lock" />
      </div>
    </Card>
  );
}
