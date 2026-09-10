// RecoveryCards.tsx - the two run states the old Monitor could not tell apart,
// and the three controls that act on a live run.
//
// GAP-ANALYSIS §11: "Missing - recovering an interrupted run (server restarted
// mid-run) as a distinct state with its own actions."
//
// RUN ARMED and INTERRUPTED RUN ARE NOT THE SAME THING:
//
//   RUN ARMED         a multi-night session is armed and WAITING for its
//                     window. Nothing is wrong; it starts by itself. Source:
//                     `useResumeArm()`, polled by NextApp every 20 s.
//   INTERRUPTED RUN   a run that was going and is NOT any more, and whose
//                     frames the engine can still pick up. Source:
//                     `GET /api/sequence/recoverable`, re-read whenever the
//                     running flag changes (there is no interval: it can only
//                     change when a run ends or starts).
//
// Neither is an entry in `deriveIncidents()` - they are Monitor-local states -
// but both render as `IncidentCard` so the vocabulary (ENGINE / NEXT / actions)
// is the one the Session hub uses for everything else.

import { useEffect, useRef, useState, type JSX } from "react";
import { api } from "../../../../api";
import { useStore, useResumeArm, useArmedBannerDismissed, useSeq } from "../../../../store";
import { accessPhrase, useCanControlMount } from "../../../../lib/caps";
import { ActionButton, IncidentCard, type Incident } from "../../../ui";
import { nav } from "../../../router";
import { sendControl } from "../../session/now/sendControl";
import { MANUAL_STOP_NOTE } from "../../session/now/RunControls";

/** Verbatim, `inventory-session-monitor.md` §4.4, em-dash rewritten as the
 *  house hyphen (ARCHITECTURE.md §0.5). */
export const VIEW_ONLY_NOTE =
  `View only - pausing, resuming or aborting this run needs ${accessPhrase("control.mount")}.`;

/** Control writes must give feedback even when the WS is DOWN - the exact case
 *  Abort exists for. The store's log-to-toast path rides the WS, so a failed or
 *  timed-out POST there is invisible. Plain fetch, 4 s timeout, a client-side
 *  toast on BOTH outcomes. (`MonitorView.tsx:461-482`, transcribed.)
 *
 *  This IS `now/sendControl` - re-exported, not re-implemented. It was a second
 *  copy that dropped the one distinction that matters: a TIMED-OUT abort is not
 *  a failed abort (`/api/sequence/abort` awaits the whole ~210 s wind-down
 *  against a 4 s budget), and this copy toasted it as a failure, telling the
 *  operator the rig was still running while it was stopping exactly as asked.
 *  It also returned `void`, so no caller here could latch on the outcome. */
export { sendControl };

interface Recoverable { name: string; frames_done: number; frames_total: number }

/** Only asked while nothing is running: a run in flight is not recoverable, and
 *  the answer cannot change under a live run. Re-read on the running edge.
 *  (`SequenceView.tsx:471-476` - "no interval - re-fetched on [running]".) */
export function useRecoverable(running: boolean): [Recoverable | null, () => void] {
  const [rec, setRec] = useState<Recoverable | null>(null);
  useEffect(() => {
    if (running) { setRec(null); return; }
    let live = true;
    void api.get<{ recoverable: boolean; name?: string; frames_done?: number; frames_total?: number }>(
      "/api/sequence/recoverable",
    )
      .then((r) => {
        if (!live) return;
        setRec(r.recoverable
          ? { name: r.name ?? "the last run", frames_done: r.frames_done ?? 0, frames_total: r.frames_total ?? 0 }
          : null);
      })
      .catch(() => { /* older server, or offline - the card simply stays away */ });
    return () => { live = false; };
  }, [running]);
  return [rec, () => setRec(null)];
}

export function RunArmedCard(): JSX.Element | null {
  const seq = useSeq();
  const resumeArm = useResumeArm();
  const dismissed = useArmedBannerDismissed();
  const armed = resumeArm?.armed ?? null;
  const hold = resumeArm?.hold ?? null;

  if (!armed) return null;
  if (seq.state !== "idle") return null;
  if (dismissed === armed.id) return null;

  const incident: Incident = {
    kind: "armed",
    pill: hold ? "HOLDING" : "ARMED",
    color: hold ? "#ffb454" : "#00D2FF",
    title: "RUN ARMED",
    sinceMs: hold?.since != null ? hold.since * 1000 : null,
    resolvesItself: true,
    engine: `${armed.name} · ${armed.owed} frames owed of ${armed.total} · ${armed.accepted} accepted`,
    next: hold
      ? `Holding: ${hold.reason}. It starts by itself when that clears.`
      : "It starts by itself when its window opens.",
    actions: [
      { id: "open", label: "OPEN SESSION", primary: true },
      { id: "dismiss", label: "DISMISS" },
    ],
  };

  return (
    <IncidentCard
      incident={incident}
      data-testid="run-armed"
      onAction={(id) => {
        if (id === "open") nav.go("/session/now");
        else useStore.getState().dismissArmedBanner();
      }}
    />
  );
}

export function InterruptedRunCard(): JSX.Element | null {
  const seq = useSeq();
  const canRun = useCanControlMount();
  const running = seq.state === "running" || seq.state === "holding" || seq.state === "aborting";
  const [rec, clear] = useRecoverable(running);
  // A ref, not the state flag: two taps land in one React batch and share one
  // closure, so `if (busy) return` reads false both times (SequenceView.tsx:440).
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);

  if (!rec) return null;

  const at = `${rec.frames_done}/${rec.frames_total}`;
  const incident: Incident = {
    kind: "interrupted",
    pill: "RECOVERING",
    color: "#ffb454",
    title: "RESUME INTERRUPTED RUN",
    sinceMs: null,
    resolvesItself: false,
    engine: `"${rec.name}" stopped at frame ${at} and its frames are still on disk`,
    next: "Nothing restarts by itself - the engine picks up at the next frame when you say so.",
    actions: [
      { id: "recover", label: busy ? "Resuming..." : `RESUME FROM FRAME ${at}`, primary: true, cap: "control.mount" },
    ],
  };

  return (
    <IncidentCard
      incident={incident}
      data-testid="run-interrupted"
      lockedFor={() => (canRun ? null : VIEW_ONLY_NOTE)}
      onExplain={(r) => useStore.getState().enqueueToast({ level: "warning", title: r })}
      onAction={() => {
        if (inFlight.current) return;
        inFlight.current = true;
        setBusy(true);
        void sendControl("Resume", "/api/sequence/recover", {
          title: "Resuming the interrupted run",
          detail: `It picks up at frame ${rec.frames_done + 1} of ${rec.frames_total}.`,
        });
        clear();
      }}
    />
  );
}

/** PAUSE / RESUME and ABORT. Both write over the plain-fetch path above, so a
 *  dead WebSocket cannot swallow the acknowledgement of the one control the
 *  operator reaches for when things are going wrong.
 *
 *  THE ABORT GUARD IS TWO LATCHES, exactly as `now/RunControls.tsx`. A local
 *  60 s timer covers the gap before the engine's first "aborting" frame (and an
 *  old server that never sends one); the server's own `aborting` state covers a
 *  teardown that outlives the timer and a client that arrived mid-teardown.
 *  Without them this button sat there saying STOP through the whole ~210 s
 *  wind-down - `ActionButton`'s `busy` deliberately does not block a press, so
 *  it could be re-tapped indefinitely with no acknowledgement at all. */
export function RunControls(): JSX.Element | null {
  const seq = useSeq();
  const canRun = useCanControlMount();
  const state = seq.state;
  const live = state === "running" || state === "holding" || state === "paused" || state === "aborting";

  const [aborting, setAborting] = useState(false);
  const serverAborting = state === "aborting";
  const abortInFlight = aborting || serverAborting;

  // The local latch expires so a dropped socket cannot leave the button dead;
  // re-posting is safe, because aborting a finished task just republishes.
  useEffect(() => {
    if (!aborting) return;
    if (!live) { setAborting(false); return; }
    const t = window.setTimeout(() => setAborting(false), 60_000);
    return () => window.clearTimeout(t);
  }, [aborting, live]);

  if (!live) return null;

  const paused = state === "paused";
  const locked = canRun ? null : VIEW_ONLY_NOTE;
  const explain = (r: string) => useStore.getState().enqueueToast({ level: "warning", title: r });

  const doAbort = () => {
    if (abortInFlight) return;
    setAborting(true);
    void sendControl("Stop", "/api/sequence/abort", {
      title: "Stopping", detail: MANUAL_STOP_NOTE,
    }).then((r) => {
      // A refusal that is NOT a timeout means nothing is winding down, so the
      // latch has to come back off or STOP is dead for a run still going.
      if (!r.ok && !r.timedOut) setAborting(false);
    });
  };

  return (
    <div data-testid="run-controls" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 6 }}>
        <ActionButton
          kind="secondary"
          full
          lockedReason={locked}
          onExplain={explain}
          data-testid="run-pause"
          onPress={() => {
            if (paused) void sendControl("Resume", "/api/sequence/resume");
            // "Pause sent" was true about the REQUEST and false about the rig:
            // the route returns the instant the flag is set, while the exposure
            // it interrupts keeps running.
            else void sendControl("Pause", "/api/sequence/pause", {
              title: "Pausing",
              detail: "Any exposure already in flight finishes first - the run stops at the next frame boundary.",
            });
          }}
        >
          {paused ? "RESUME" : "PAUSE"}
        </ActionButton>
        <ActionButton
          kind="danger"
          full
          arm={{ label: "PRESS AGAIN TO STOP" }}
          lockedReason={locked}
          onExplain={explain}
          busy={abortInFlight}
          ariaLabel={abortInFlight
            ? "Stopping the sequence - ending the exposure and stopping the guider"
            : undefined}
          data-testid="run-abort"
          onPress={doAbort}
        >
          {abortInFlight ? "STOPPING" : "STOP"}
        </ActionButton>
      </div>
      {locked && <div className="nx-empty-hint" data-testid="run-locked-note">{locked}</div>}
    </div>
  );
}
