// pendingValue.ts - the optimistic control-state latch for mount tracking and
// tracking rate (plan hub-rig.md section 0.4 shape 2, transcribed from
// `views/MountView.tsx:54-68`).
//
// WHY THIS IS NOT A GENERAL "optimistic write" HELPER. It applies to exactly
// the two mount commands whose route awaits the DEVICE: `/api/mount/tracking`
// and `/api/mount/tracking_rate` answer when the mount answers, but everything
// the sheet RENDERS comes off the 2 s status frame. So the switch sat visibly
// unmoved for up to two seconds after a deliberate press, which is exactly how
// a control teaches you to press it twice.
//
// The three ways a pending value dies, all of them required:
//   - the rig reports the same thing        -> hand over to server truth;
//   - the caller's POST was refused         -> `revert()`, at once;
//   - neither happened inside `timeoutMs`   -> expire.
// The last one is what stops a mount that never adopts the value from stranding
// a number on screen that no hardware agrees with. A latch with no expiry is a
// claim nothing keeps.
//
// Everything the four `_spawn` routes (park / home / goto / solve_sync) need is
// a DIFFERENT problem and must not come here: those return the instant the task
// is created, so their in-flight truth is the rig's own busy lane
// (`lib/useBusy.ts`'s `useBusyOrPending`), never a promise.

import { useEffect, useState } from "react";

export interface PendingValue<T> {
  /** The chosen value while it is pending, the rig's own otherwise. */
  value: T | undefined;
  /** True while a chosen value has not yet been confirmed by the rig. */
  pending: boolean;
  /** Paint `v` immediately; call this BEFORE the POST. */
  show: (v: T) => void;
  /** Drop the chosen value - call when the POST was refused. */
  revert: () => void;
}

export function usePendingValue<T>(
  actual: T | undefined,
  timeoutMs = 6000,
): PendingValue<T> {
  const [pending, setPending] = useState<T | null>(null);

  useEffect(() => {
    if (pending == null) return;
    if (actual === pending) { setPending(null); return; }
    const t = setTimeout(() => setPending(null), timeoutMs);
    return () => clearTimeout(t);
  }, [pending, actual, timeoutMs]);

  return {
    value: pending ?? actual,
    pending: pending != null,
    // A functional update, not `setPending(v)`: T is unconstrained, and a T that
    // is itself a function would otherwise be CALLED by React instead of stored.
    show: (v: T) => setPending(() => v),
    revert: () => setPending(null),
  };
}
