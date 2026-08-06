// useBusy.ts — is MY operation still running on the rig?
//
// THE PROBLEM THIS EXISTS FOR. Most long operations are started through routes
// that `_spawn` a background task and return `{"started": "<lane>"}` the instant
// the task is CREATED. So `await api.post(...)` resolves in ~40ms while the
// mount is still swinging, ASTAP is still solving, or the focuser is still
// travelling. Twenty controls audited on 2026-08-05 derived their in-flight
// state from that promise and therefore reported THE REQUEST, not the rig:
// "Solving…" flickered for a frame and the button sat there looking ready and
// re-pressable for the next thirty seconds. On lanes that `_spawn` with
// replace=True — park, close-roof, force-recalibrate — the retry that invites
// actually CANCELS the work in flight and restarts it.
//
// The rig has been publishing the answer on every 2s status frame all along.
// `status.busy` is one word, because it was built to suppress a stale-telemetry
// banner: it maps goto→"slewing", solve→"solving", autofocus/focuser/
// filter_offsets→"focusing" and four more onto "capturing", so it cannot tell
// one control's operation from another's. `status.busy_lanes` is that same set
// unreduced (hub.busy_lanes), and this is how a control reads it.
//
// USE IT LIKE THIS:
//
//   const solving = useBusy("solve");
//   <button disabled={solving} aria-busy={solving}>
//     {solving ? "Solving…" : "Solve & Sync"}
//   </button>
//
// and OR it with a short local flag if you want the button to react before the
// next status frame lands (see `useBusyOrPending`).

import { useEffect, useRef, useState } from "react";
import { useStatus } from "../store";

/** Lane names the server uses. Not exhaustive — it is whatever `_spawn` was
 *  called with — but these are the ones the UI drives, spelled once so a typo
 *  is a type error instead of a control that is never busy. */
export type BusyLane =
  | "goto" | "solve" | "autofocus" | "focuser" | "filter_offsets"
  | "capture" | "looping" | "egain" | "polar" | "guide_assistant"
  | "connect" | "system.update" | "park" | "dome";

/** True while the server reports `lane` in flight.
 *
 *  Returns FALSE on a server that does not publish `busy_lanes` (pre
 *  2026-08-05) rather than blocking the control forever: an unknown answer must
 *  not become a permanently disabled button. Callers that need to distinguish
 *  "idle" from "unknown" should use `useBusyLanes` and check for undefined. */
export function useBusy(lane: BusyLane | string): boolean {
  const lanes = useStatus()?.busy_lanes;
  return Array.isArray(lanes) && lanes.includes(lane);
}

/** The raw list, or undefined when the server does not publish it. */
export function useBusyLanes(): string[] | undefined {
  const lanes = useStatus()?.busy_lanes;
  return Array.isArray(lanes) ? lanes : undefined;
}

/** True while ANY of `lanes` is in flight — for a control that owns more than
 *  one route (Equipment's Connect touches `connect` and `goto`). */
export function useBusyAny(...lanes: (BusyLane | string)[]): boolean {
  const live = useBusyLanes();
  return live != null && lanes.some((l) => live.includes(l));
}

/** `useBusy`, plus a local latch that covers the gap before the rig answers.
 *
 *  The status frame is 2s, so a control driven purely from `busy_lanes` looks
 *  dead for up to two seconds after a tap — the exact complaint that made
 *  people press twice. Call `arm()` when you fire the request: the hook reads
 *  busy immediately, and hands over to the server's answer as soon as the lane
 *  appears. The latch also EXPIRES, so a request that never reached a lane (a
 *  403, a dropped connection, an op that finished inside one frame) cannot
 *  leave the control disabled forever — the failure mode a plain
 *  `setBusy(true)` has whenever its `finally` is missed.
 *
 *      const { busy, arm } = useBusyOrPending("solve");
 *      const onClick = () => { arm(); void api.post("/api/mount/solve_sync"); };
 */
export function useBusyOrPending(
  lane: BusyLane | string,
  graceMs = 6000,
): { busy: boolean; arm: () => void } {
  const serverBusy = useBusy(lane);
  const [armedAt, setArmedAt] = useState<number | null>(null);
  const seenRef = useRef(false);

  useEffect(() => {
    if (armedAt == null) return;
    // The rig picked the lane up: the server's answer is authoritative now.
    if (serverBusy) { seenRef.current = true; setArmedAt(null); return; }
    // It never did. Expire rather than latch.
    const t = setTimeout(() => setArmedAt(null), graceMs);
    return () => clearTimeout(t);
  }, [armedAt, serverBusy, graceMs]);

  useEffect(() => { if (!serverBusy) seenRef.current = false; }, [serverBusy]);

  return {
    busy: serverBusy || armedAt != null,
    arm: () => { seenRef.current = false; setArmedAt(Date.now()); },
  };
}
