// LockControls.tsx - the screen lock and keep-awake, rehomed.
//
// Both lived in `NavMoreSheet`, which the new IA deletes. They belong on
// Monitor because that is the screen a phone is left on: the lock exists so a
// pocket or a sleeve cannot slew the mount while the run is being watched, and
// keep-awake exists so watching it does not mean tapping the glass every 30 s.
// (The other NavMoreSheet controls - haptics, reverse RA/Dec, touch size,
// auto-lock - are Settings > PHONE's, per plan §B.1.7.)
//
// THIS FILE ONLY FLIPS TWO STORE FLAGS. `TouchGuard` - the overlay itself, with
// its slide-to-unlock, its focusable Unlock button, its auto-lock countdown and
// its always-reachable EMERGENCY STOP - is mounted ONCE by `NextApp` and is not
// touched here. `useMonitorWakeLock()` is likewise NextApp's; `monitorAwake` is
// one of the two things it reads.

import type { JSX } from "react";
import {
  useLockAvailable, useMonitorAwake, useSetLocked, useSetMonitorAwake,
} from "../../../../lib/touchStore";
import { useStore } from "../../../../store";
import { haptics } from "../../../../lib/haptics";
import { NxIcon } from "../../../icons";
import { IconButton48 } from "../../../ui";

export const NO_LOCK_REASON = "the screen lock is not available on this display";

export function LockControls(): JSX.Element {
  const lockAvailable = useLockAvailable();
  const monitorAwake = useMonitorAwake();
  const setLocked = useSetLocked();
  const setMonitorAwake = useSetMonitorAwake();
  const explain = (r: string) => useStore.getState().enqueueToast({ level: "warning", title: r });

  return (
    <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
      <IconButton48
        glyph={<NxIcon name="eye" size={18} />}
        label="AWAKE"
        active={monitorAwake}
        onPress={() => setMonitorAwake(!monitorAwake)}
        data-testid="monitor-awake"
      />
      <IconButton48
        glyph={<NxIcon name="lock" size={18} />}
        label="LOCK"
        lockedReason={lockAvailable ? null : NO_LOCK_REASON}
        onExplain={explain}
        onPress={() => { haptics.warn(); setLocked(true); }}
        data-testid="monitor-lock"
      />
    </div>
  );
}
