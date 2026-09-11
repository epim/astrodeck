// useSubFrame.ts - where the exposure in flight actually is, on ONE clock.
//
// TRANSCRIBED from `views/SequenceView.tsx:93-166` (`useShutterRemainingS`) and
// the `SequenceRunStrip` anchor, because both encode a bug already paid for:
//
//   ANCHOR ONCE PER FRAME, THEN TICK ON THE CLIENT CLOCK. The engine emits a
//   progress snapshot at each frame BOUNDARY and never again during the
//   exposure, so `server_now_ms` is frozen for the whole sub. Re-deriving the
//   skew every tick subtracts two frozen numbers and cancels to exactly zero -
//   which is how the old sub-frame bar read 0.0 s for five minutes.
//
//   JOINED MID-FRAME. A tab opened halfway through a sub gets the frame's age
//   from the snapshot; a tab that watched it start gets 0 and its own clock.
//   When the snapshot is the only source, the elapsed figure is a FLOOR, not a
//   measurement, and it renders with a `>=` prefix rather than an exact claim.
//
//   RETURN null ONCE THE EXPOSURE'S OWN LENGTH HAS ELAPSED. The shutter is shut
//   by then even if the engine is still saving, and it also means no caller can
//   latch: a rejected frame clears the marker WITHOUT publishing, so a purely
//   marker-driven "pausing" could stick forever.

import { useEffect, useRef, useState } from "react";
import type { SequenceProgress } from "../../../../types";

export interface SubFrame {
  /** 0..1 of the current exposure, or null when no shutter is open. */
  fraction: number | null;
  /** Seconds of SHUTTER still owed, or null. This is the number that makes
   *  "pause requested" observable from a browser. */
  remainingS: number | null;
  /** Seconds this exposure has been open. */
  elapsedS: number | null;
  /** True when the elapsed figure came from the server snapshot rather than
   *  from watching the frame start - so it is a floor. */
  joinedMidFrame: boolean;
  /** `MM:SS` prefixed with `>= ` when `joinedMidFrame`. */
  elapsedLabel: string | null;
}

function mmss(seconds: number): string {
  const t = Math.max(0, Math.round(seconds));
  const m = Math.floor(t / 60);
  const s = t % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** `countdownLive` gates the 500 ms interval only. The VALUE is computed during
 *  render off the client clock, so the first frame that says "paused" already
 *  carries the right number and only then does the timer start. */
export function useSubFrame(
  progress: SequenceProgress | undefined,
  countdownLive: boolean,
): SubFrame {
  const startedAtMs = progress?.frame_started_at_ms ?? null;
  const serverNowMs = progress?.server_now_ms ?? null;
  const exposureS = progress?.current_exposure_s ?? 0;
  const inFlight = startedAtMs != null && exposureS > 0;

  const anchor = useRef<{ key: number; atMs: number; ageS: number; joined: boolean } | null>(null);
  if (startedAtMs != null && anchor.current?.key !== startedAtMs) {
    const reportedAgeS = serverNowMs != null ? (serverNowMs - startedAtMs) / 1000 : 0;
    const ageS = Number.isFinite(reportedAgeS) ? Math.max(0, reportedAgeS) : 0;
    anchor.current = {
      key: startedAtMs,
      atMs: Date.now(),
      ageS,
      // More than a second of the frame was already gone when we first saw it:
      // this tab did not watch the shutter open.
      joined: ageS > 1,
    };
  }

  const elapsedS = (() => {
    if (!inFlight || anchor.current == null) return null;
    return anchor.current.ageS + (Date.now() - anchor.current.atMs) / 1000;
  })();

  const remainingS = elapsedS == null ? null
    : exposureS - elapsedS > 0 ? exposureS - elapsedS : null;

  const ticking = countdownLive && remainingS != null;
  const [, tick] = useState(0);
  useEffect(() => {
    if (!ticking) return;
    const t = setInterval(() => tick((n) => n + 1), 500);
    return () => clearInterval(t);
  }, [ticking]);

  const joined = anchor.current?.joined ?? false;
  return {
    fraction: elapsedS == null || exposureS <= 0
      ? null
      : Math.min(1, Math.max(0, elapsedS / exposureS)),
    remainingS,
    elapsedS,
    joinedMidFrame: joined,
    elapsedLabel: elapsedS == null ? null : `${joined ? ">= " : ""}${mmss(elapsedS)}`,
  };
}
