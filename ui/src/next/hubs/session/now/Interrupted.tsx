// Interrupted.tsx - "the server restarted mid-run" as a state with its own
// actions (GAP-ANALYSIS section 11).
//
// THE DISTINCTION THIS CARD EXISTS FOR is worth 75 minutes of clear sky on
// narrowband: RESUME picks up at frame N, re-running starts over at frame 1 and
// re-shoots what is already on disk. Both are always offered; only the emphasis
// moves - and the difference is said IN WORDS, because button order alone does
// not survive a glance in the dark.
//
// THE SECOND VERB IS NAMED FOR WHAT IT DOES. It read RE-RUN FROM FRAME 1 and
// opened the plan editor, which is a label promising a run that the press does
// not start. It is NOT wired to `POST /api/sequence/start` instead, because the
// recoverable record is the server's and the editor's plan is the STORE'S
// DRAFT: the two are the same plan only if nothing has been loaded since, so a
// one-press re-run here would start whatever plan happens to be open under a
// button labelled with this run's frame count. The editor is where the plan
// being started is on screen, so that is where the press goes, and the sentence
// under the card says to check the name before pressing RE-RUN PLAN there.
//
// RESUMABLE IS THE BACKEND'S FLAG, not `state === "error"`. That client-side
// narrowing silently excluded the far more common `aborted`, so on night two the
// banner offered RE-RUN with no resume anywhere while the working resume sat
// 1400 px down the page.
//
// THE DOUBLE-TAP GUARD IS A REF, NOT STATE. Two taps land in the same React
// batch and share one closure, so `if (resuming) return` reads false both times
// and the second tap reaches a route whose session the first has already
// claimed - which answers "no resumable sequence found" over a resume that
// started perfectly. Only a ref is already true when the second handler runs.

import { useCallback, useEffect, useRef, useState, type JSX } from "react";

import { api } from "../../../../api";
import { accessPhrase, useCanControlMount } from "../../../../lib/caps";
import { fmtClock } from "../../../../lib/eta";
import { useSeq, useStore } from "../../../../store";
import { useBreakpoint } from "../../../breakpoint";
import { ActionButton, Label, Mono } from "../../../ui";
import { explainLock } from "../../../shell/explain";
import { nav } from "../../../router";

export const RERUN_PHONE_REASON = "Starting over opens on a tablet or desktop.";
/** Said on this card's second verb. It NAMES THE DOOR, not the outcome: the
 *  press opens the plan editor, and the run starts from RE-RUN PLAN there. */
export const RERUN_TITLE =
  "Opens the plan editor, where RE-RUN PLAN starts over at frame 1 "
  + "- the frames already on disk are not reused";

interface Recoverable {
  recoverable: boolean;
  session_id?: string;
  name?: string;
  frames_done?: number;
  frames_total?: number;
  ts?: number;
}

const LIVE = new Set(["running", "holding", "paused", "aborting"]);

export function Interrupted(): JSX.Element | null {
  const seq = useSeq();
  const bp = useBreakpoint();
  const canControl = useCanControlMount();
  const [rec, setRec] = useState<Recoverable | null>(null);
  const [resuming, setResuming] = useState(false);
  const inFlight = useRef(false);

  const running = LIVE.has(seq.state);

  // Re-fetched on the `running` edge, not on an interval: the answer only
  // changes when a run starts or stops.
  useEffect(() => {
    if (running) { setRec(null); return; }
    let alive = true;
    void api.get<Recoverable>("/api/sequence/recoverable").then(
      (r) => { if (alive) setRec(r.recoverable ? r : null); },
      () => { /* an older server, or none: the card simply does not appear */ },
    );
    return () => { alive = false; };
  }, [running]);

  useEffect(() => {
    if (!resuming) return;
    if (seq.state === "running") { inFlight.current = false; setResuming(false); return; }
    const t = window.setTimeout(() => { inFlight.current = false; setResuming(false); }, 8000);
    return () => window.clearTimeout(t);
  }, [resuming, seq.state]);

  const resume = useCallback(() => {
    if (inFlight.current) return;
    inFlight.current = true;
    setResuming(true);
    void api.post("/api/sequence/recover").then(
      () => { setRec(null); },
      (e: Error) => {
        inFlight.current = false;
        setResuming(false);
        useStore.getState().enqueueToast({ level: "error", title: "Resume did not land", detail: e.message });
      },
    );
  }, []);

  if (running || !rec) return null;

  const done = rec.frames_done ?? 0;
  const total = rec.frames_total ?? 0;
  const when = rec.ts ? fmtClock(rec.ts * 1000) : null;
  const lockedReason = canControl ? null : `Resuming a run needs ${accessPhrase("control.mount")}.`;

  return (
    <div
      className="nx-card"
      data-tone="accent"
      data-testid="now-interrupted"
      style={{ display: "flex", flexDirection: "column", gap: 10 }}
    >
      <Label size={11}>RESUME INTERRUPTED RUN</Label>
      <Mono size={10.5} tone="dim">
        {rec.name ?? "The last run"} stopped at frame {done} of {total}
        {when ? ` on ${when}` : ""}. The server restarted; the frames on disk are intact.
      </Mono>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <ActionButton
          kind="primary"
          onPress={resume}
          busy={resuming}
          lockedReason={lockedReason}
          onExplain={explainLock}
          data-testid="interrupted-resume"
        >
          {resuming ? "RESUMING" : `RESUME FROM FRAME ${done}/${total}`}
        </ActionButton>
        <ActionButton
          kind="secondary"
          onPress={() => nav.sheet("planEditor")}
          lockedReason={bp === "phone" ? RERUN_PHONE_REASON : lockedReason}
          onExplain={explainLock}
          ariaLabel={RERUN_TITLE}
          data-testid="interrupted-rerun"
        >
          OPEN THE PLAN EDITOR TO RE-RUN
        </ActionButton>
      </div>
      <Mono size={10} tone="dim">
        Resume picks up at frame {done} of {total}. Starting over is two steps on
        purpose: the editor runs whichever plan is LOADED, which need not be
        {" "}{rec.name ?? "this one"}, so check the name there before RE-RUN PLAN
        re-shoots all {total} frames.
      </Mono>
    </div>
  );
}
