// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Interrupted.tsx - a run that stopped short of its plan, with frames on disk,
// as a state with its own actions (GAP-ANALYSIS section 11).
//
// THE CAUSE IS THE SERVER'S WORD, OR NONE (#487). The card used to open on "the
// server restarted mid-run" and print "The server restarted" after every
// ending, and an operator who had pressed STOP was told the rig had restarted,
// which reads as a crash they never saw. The recoverable route now says why the
// session went dormant (`end_reason`, from its last report, server
// `_why_dormant`), and the card words a cause from that alone: "aborted" is
// the operator's STOP; `END_REASON_RESTART` is the server's word for the traces
// only a process that stopped under the run leaves (a last report that never
// recorded an ending, on a session the boot sweep turned dormant); any other
// ending, and an answer with no `end_reason` at all (an older server), gets no
// cause sentence. A sentence about a cause the answer did not carry is the
// defect, not a fallback. One gap stays, the server's: a polite stop of the
// server under a run finalizes "aborted" too, so it reads as a STOP (#565).
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

/** The recoverable route's `end_reason` after an operator's STOP: the word
 *  the engine's finalize stamps on the report (`_finalize_report("aborted")`). */
export const END_REASON_ABORTED = "aborted";
/** The recoverable route's `end_reason` for the evidence a restart leaves:
 *  server `RESTART_END_REASON`, which test_h4_recoverable_says_why.py holds
 *  this constant to. */
export const END_REASON_RESTART = "restart";
/** The cause sentence for an operator's STOP. */
export const STOPPED_BY_HAND = "You stopped it";
/** The cause sentence for a restart, said only on `END_REASON_RESTART`. */
export const SERVER_RESTARTED = "The server restarted";

/** The card's cause sentence for the route's `end_reason`, or null when the
 *  answer carries no cause the card can word: every other ending (the sky, the
 *  window, an error, an unsafe stop each end a run for their own reasons, and
 *  a guess among them would be the #487 defect again), null, and a server
 *  older than #487 that sends no `end_reason`. */
export function causeSentence(endReason: unknown): string | null {
  if (endReason === END_REASON_ABORTED) return STOPPED_BY_HAND;
  if (endReason === END_REASON_RESTART) return SERVER_RESTARTED;
  return null;
}

interface Recoverable {
  recoverable: boolean;
  session_id?: string;
  name?: string;
  frames_done?: number;
  frames_total?: number;
  ts?: number;
  /** Why the session went dormant (#487): the last report's own word,
   *  `END_REASON_RESTART`, or null. Absent from a server older than #487. */
  end_reason?: string | null;
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
  const cause = causeSentence(rec.end_reason);
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
        {when ? ` on ${when}` : ""}.{" "}
        {cause ? `${cause}; the frames on disk are intact.` : "The frames on disk are intact."}
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
