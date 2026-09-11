// useEta.ts - the finish clock, anchored ONCE per server frame.
//
// The client renders the countdown AND the absolute clock from one instant
// (`lib/eta.ts::deriveFinish`), so the two can never disagree - the engine's
// `server_now_ms` is used to detect a fresh frame and is never itself rendered.
// Re-anchoring on every render would restart the countdown every tick; not
// re-anchoring at all would let it drift for the whole night.
//
// A PAUSED RUN HAS NO HONEST FINISH. `progress.eta_s` keeps its last value
// through a pause, and printing it would be a promise about a rig that is not
// moving - so the caller gets `paused: true` and prints "no ETA while paused"
// (`components/monitor.tsx`'s LiveTimer, the same rule).

import { useEffect, useRef, useState } from "react";
import { deriveFinish } from "../../../../lib/eta";
import { useSeq } from "../../../../store";

export interface EtaRead {
  /** null when the engine has published no ETA yet. */
  remainingS: number | null;
  finishAtMs: number | null;
  /** false until the engine has measured enough frames; the clock takes a `~`. */
  confident: boolean;
  paused: boolean;
}

/** `tickMs` is the caller's re-render cadence. 1 s on the phone screen, where
 *  the countdown is being watched; the desktop column shares the same hook. */
export function useEta(tickMs = 1000): EtaRead {
  const seq = useSeq();
  const progress = seq.progress;
  const anchor = useRef<{ etaS?: number; receivedAtMs: number }>({ receivedAtMs: Date.now() });
  const sentinel = useRef<number | null>(null);

  const stamp = progress?.server_now_ms ?? progress?.frames_done ?? null;
  if (stamp !== sentinel.current) {
    sentinel.current = stamp;
    anchor.current = { etaS: progress?.eta_s, receivedAtMs: Date.now() };
  }

  const [now, setNow] = useState(() => Date.now());
  const live = seq.state === "running" || seq.state === "holding";
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => setNow(Date.now()), tickMs);
    return () => clearInterval(t);
  }, [live, tickMs]);

  const paused = seq.state === "paused";
  const etaS = anchor.current.etaS;
  if (paused || etaS == null) {
    return { remainingS: null, finishAtMs: null, confident: progress?.eta_confident !== false, paused };
  }
  const { remainingS, finishAtMs } = deriveFinish(etaS, anchor.current.receivedAtMs, now);
  return { remainingS, finishAtMs, confident: progress?.eta_confident !== false, paused: false };
}
