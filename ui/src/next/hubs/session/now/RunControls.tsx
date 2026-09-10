// RunControls.tsx - FILES, SAVE STACK, PAUSE/RESUME, STOP.
//
// FOUR THINGS HERE ARE SCAR TISSUE AND MUST NOT BE SIMPLIFIED AWAY.
//
// 1. THE LOCK IS THE CAPABILITY, NOT THE LINK. `lib/gate.ts` refuses everything
//    while `wsPhase !== "up"` - which is correct for a control that starts work
//    and exactly wrong for the one that STOPS it. Abort exists for the night
//    where the link is bad. So PAUSE and STOP are gated on `control.mount`
//    alone, and they post over a plain fetch (`sendControl`) rather than
//    anything that rides the socket.
//
// 2. A TIMED-OUT ABORT IS NOT A FAILED ABORT. `/api/sequence/abort` awaits the
//    whole wind-down - abort the exposure, stop the guider, finalize the report,
//    drain the thumbnails - which is minutes against a client budget of seconds.
//    The engine's next state frame is the answer; the request's promise never
//    was.
//
// 3. THE ABORT GUARD IS TWO LATCHES. A local 60 s timer covers the gap before
//    the engine's first "aborting" frame (and an old server that never sends
//    one); the server's own `aborting` state covers a teardown that outlives the
//    timer and a client that arrived mid-teardown. Either one means a teardown
//    is in flight.
//
// 4. PAUSING IS CLIENT-COMPUTED. There is no `pausing` wire state: the route
//    returns the instant the flag is set while the exposure keeps running, so
//    the difference between "pause requested" and "paused" is only visible if
//    the client works out how much shutter is left. That number is what tells
//    someone whether they may walk out to the scope.
//
// STOP IS TWO TAPS (`ActionButton`'s own arm), not a native confirm: at 3am, in
// gloves, a modal that steals focus is worse than a button that has to be meant.

import { useEffect, useRef, useState, type JSX } from "react";

import { sessionStackImageUrl } from "../../../../api/sessionStack";
import { accessPhrase, useCanControlMount, useCan } from "../../../../lib/caps";
import { fmtCountdown } from "../../../../lib/eta";
import { useSeq, useStore } from "../../../../store";
import { ActionButton, Bar, Mono } from "../../../ui";
import { NxIcon } from "../../../icons";
import { explainLock } from "../../../shell/explain";
import { nav } from "../../../router";
import { acceptedByFilter, tonightNightKey } from "./filters";
import { sendControl } from "./sendControl";
import { useActiveSession } from "./sessionData";
import { useSessionStackStatus } from "./stackView";
import { useSubFrame } from "./useSubFrame";

/** The Monitor's own view-only note, as one sentence. */
export const RUN_CONTROL_REASON =
  `Pausing, resuming or aborting this run needs ${accessPhrase("control.mount")}.`;

/** README section 6's sentence, kept: the engine really does park nothing on a
 *  manual stop, and the operator has to be told at the moment they walk away. */
export const MANUAL_STOP_NOTE =
  "Run stopped by user - engine parks nothing on manual stop";

/** How long the "it is downloading" row stays up. A plain `<a download>`
 *  navigation exposes NO progress, so the row says what happened and retires
 *  rather than animating a percentage nobody measured (deviation D6). */
const DOWNLOAD_NOTE_MS = 3000;

const LIVE_STATES = new Set(["running", "holding", "paused", "aborting"]);

export function RunControls({ size = "lg" }: { size?: "lg" | "md" }): JSX.Element {
  const seq = useSeq();
  const canControl = useCanControlMount();
  const canPreview = useCan("view.preview");
  const { session } = useActiveSession();
  const { status: stack } = useSessionStackStatus();

  const [aborting, setAborting] = useState(false);
  const [downloadNote, setDownloadNote] = useState<string | null>(null);
  const vibratedError = useRef(false);

  const running = LIVE_STATES.has(seq.state);
  const serverAborting = seq.state === "aborting";
  const abortInFlight = aborting || serverAborting;

  // The local latch expires so a dropped socket cannot leave the row dead;
  // re-posting is safe, because aborting a finished task just republishes.
  useEffect(() => {
    if (!aborting) return;
    if (!running) { setAborting(false); return; }
    const t = window.setTimeout(() => setAborting(false), 60_000);
    return () => window.clearTimeout(t);
  }, [aborting, running]);

  // One buzz per entry into the state, not per tick.
  useEffect(() => {
    const bad = seq.state === "error";
    if (bad && !vibratedError.current) {
      vibratedError.current = true;
      try { navigator.vibrate?.(120); } catch { /* unsupported */ }
    }
    if (!bad) vibratedError.current = false;
  }, [seq.state]);

  useEffect(() => {
    if (!downloadNote) return;
    const t = window.setTimeout(() => setDownloadNote(null), DOWNLOAD_NOTE_MS);
    return () => window.clearTimeout(t);
  }, [downloadNote]);

  const paused = seq.state === "paused";
  const sub = useSubFrame(seq.progress, paused);
  // PAUSING: the engine says paused, and a shutter is still open.
  const pausing = paused && sub.remainingS != null;

  const lockedReason = canControl ? null : RUN_CONTROL_REASON;

  const doPause = () => {
    void sendControl("Pause", "/api/sequence/pause", {
      title: "Pausing",
      detail: "Any exposure already in flight finishes first - the run stops at "
        + "the next frame boundary.",
    });
  };
  const doResume = () => {
    void sendControl("Resume", "/api/sequence/resume", { title: "Resuming" });
  };
  const doAbort = () => {
    if (abortInFlight) return;
    setAborting(true);
    void sendControl("Stop", "/api/sequence/abort", {
      title: "Stopping", detail: MANUAL_STOP_NOTE,
    }).then((r) => {
      // A refusal that is NOT a timeout means nothing is winding down.
      if (!r.ok && !r.timedOut) setAborting(false);
      // The Flows log keeps its own record of the night, whichever screen the
      // stop came from.
      useStore.getState().flowsAppendLog(MANUAL_STOP_NOTE, "warn");
    });
  };

  // FILES: the accepted subs of THIS night when there is a ledger; the engine's
  // own frame counter when there is not.
  const acceptedTonight = [...acceptedByFilter(session, tonightNightKey(session)).values()]
    .reduce((a, b) => a + b, 0);
  const subs = session ? acceptedTonight : (seq.progress?.frames_done ?? 0);

  const stackHref = stack && stack.has_image ? sessionStackImageUrl(stack.seq, 2400) : null;
  const stackName = `${(stack?.target || seq.target || "stack").replace(/\s+/g, "-")}-stack.jpg`;

  const btnSize = size;

  return (
    <div data-testid="now-run-controls" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {pausing && (
        <div
          role="alert"
          data-testid="now-pausing"
          style={{
            border: "1px solid var(--warn)", borderRadius: 12, padding: "8px 10px",
            background: "color-mix(in srgb, var(--warn) 8%, transparent)",
          }}
        >
          <Mono size={11}>
            Pausing - this frame still has {fmtCountdown(sub.remainingS!)} of shutter
            left, and the run does not stop until it lands. Keep lights off and hands
            off the scope until this reads PAUSED.{canControl ? " Stop ends it now." : ""}
          </Mono>
        </div>
      )}

      <div style={{ display: "flex", gap: 8 }}>
        <ActionButton
          kind="secondary"
          size="md"
          full
          glyph={<NxIcon name="download" size={16} />}
          onPress={() => nav.sheet("files", { src: "current" })}
          data-testid="now-files"
        >
          FILES · {subs} SUBS
        </ActionButton>
        {/* A plain <a download>, never a fetch into a Blob: the composite is
            served as one file and iOS takes one file at a time, in the
            foreground. */}
        <a
          href={stackHref ?? undefined}
          download={stackName}
          onClick={(e) => {
            if (!stackHref || !canPreview) {
              e.preventDefault();
              explainLock(!canPreview
                ? `Downloading the stack needs ${accessPhrase("view.preview")}.`
                : "There is no stacked picture yet.");
              return;
            }
            setDownloadNote(`SAVE STACK started - it lands in your downloads`);
          }}
          className="nx-btn"
          data-kind="secondary"
          data-size="md"
          data-full="true"
          data-testid="now-save-stack"
          aria-disabled={!stackHref || !canPreview ? true : undefined}
          style={{ flex: 1, textDecoration: "none", opacity: stackHref && canPreview ? 1 : 0.5 }}
        >
          <span className="nx-btn-label">SAVE STACK</span>
        </a>
      </div>

      {downloadNote && (
        <div
          data-testid="now-download-note"
          style={{
            display: "flex", flexDirection: "column", gap: 4,
            border: "1px solid var(--accent)", borderRadius: 10, padding: "6px 10px",
          }}
        >
          <Mono size={10} tone="dim">{downloadNote}</Mono>
          <Bar value={1} height={3} tone="accent" label="Download started" />
        </div>
      )}

      {/* A finished run keeps FILES and SAVE STACK - the frames are still there
          and still worth having - but PAUSE and STOP would be controls for a rig
          that has already stopped. */}
      {running && (
      <div style={{ display: "flex", gap: 8 }}>
        <ActionButton
          kind="secondary"
          size={btnSize}
          full
          onPress={paused ? doResume : doPause}
          lockedReason={lockedReason}
          onExplain={explainLock}
          ariaLabel={pausing
            ? "The pause hasn't taken effect yet - this keeps the run going"
            : undefined}
          data-testid="now-pause"
        >
          {pausing ? "CANCEL PAUSE" : paused ? "RESUME" : "PAUSE"}
        </ActionButton>
        <ActionButton
          kind="danger"
          size={btnSize}
          full
          arm={{ label: "TAP AGAIN TO STOP", ms: 3000 }}
          onPress={doAbort}
          busy={abortInFlight}
          lockedReason={lockedReason}
          onExplain={explainLock}
          ariaLabel={abortInFlight
            ? "Aborting the sequence - ending the exposure and stopping the guider"
            : undefined}
          data-testid="now-stop"
        >
          {abortInFlight ? "STOPPING" : "STOP"}
        </ActionButton>
      </div>
      )}
    </div>
  );
}
