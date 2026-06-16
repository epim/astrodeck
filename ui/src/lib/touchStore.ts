// touchStore.ts — typed accessors for the touch store slice (touch spec §2.2).
//
// Why this shim exists: the touch slice (`locked`, `lockAvailable`, `monitorAwake`,
// `touch`, `setLocked`, `setMonitorAwake`, `setTouch`) is appended to `store.ts`
// by lane 3A/3B at a named anchor. THIS lane (3B touch-ergonomics components) must
// NOT edit `store.ts`, yet our components consume those fields. So we read them
// through a single typed view here, against the DOCUMENTED shape (master §A.2 /
// touch spec §2.2). When the store landing adds the fields to `AppState`, these
// selectors keep working unchanged; until then the cast keeps the lane compiling
// in isolation. One place to retarget if the slice ever moves.
//
// Every accessor is a narrow zustand selector (one slice each) so rendering a lock
// chip / nav badge never widens App's subscription (R27).

import { useStore } from "../store";
import type { TouchSettings } from "../types";

// The exact documented touch-slice contract (touch spec §2.2). Kept local so we
// depend only on the shape, not on store.ts having merged it yet.
export interface TouchSlice {
  locked: boolean;
  lockAvailable: boolean;
  monitorAwake: boolean;
  touch: TouchSettings;
  setLocked: (v: boolean) => void;
  setMonitorAwake: (v: boolean) => void;
  setTouch: (patch: Partial<TouchSettings>) => void;
}

// Defaults mirror the store init block (touch spec §2.2) so a not-yet-merged store
// degrades safely instead of throwing on undefined.
const DEFAULT_TOUCH: TouchSettings = {
  hapticsEnabled: true,
  touchSizing: "auto",
  reverseRa: false,
  reverseDec: false,
  autoLockMs: null,
};

const NOOP = () => {};

// Read the slice off the live store state via an unknown cast. `Partial` because
// in isolation (pre-merge) the keys are absent; we coalesce to safe defaults.
function slice(s: unknown): Partial<TouchSlice> {
  return s as Partial<TouchSlice>;
}

// ----------------------------------------------------------------- state hooks
export const useLocked = (): boolean => useStore((s) => slice(s).locked ?? false);
export const useLockAvailable = (): boolean => useStore((s) => slice(s).lockAvailable ?? false);
export const useMonitorAwake = (): boolean => useStore((s) => slice(s).monitorAwake ?? false);
export const useTouchSettings = (): TouchSettings =>
  useStore((s) => slice(s).touch ?? DEFAULT_TOUCH);

// ----------------------------------------------------------------- action hooks
export const useSetLocked = (): TouchSlice["setLocked"] =>
  useStore((s) => slice(s).setLocked ?? NOOP);
export const useSetMonitorAwake = (): TouchSlice["setMonitorAwake"] =>
  useStore((s) => slice(s).setMonitorAwake ?? NOOP);
export const useSetTouch = (): TouchSlice["setTouch"] =>
  useStore((s) => slice(s).setTouch ?? NOOP);

// Imperative getter for non-React call sites (e.g. SlewPad's global lock effect
// reading the latest `locked` without subscribing).
export function getTouchState(): TouchSlice {
  const s = slice(useStore.getState());
  return {
    locked: s.locked ?? false,
    lockAvailable: s.lockAvailable ?? false,
    monitorAwake: s.monitorAwake ?? false,
    touch: s.touch ?? DEFAULT_TOUCH,
    setLocked: s.setLocked ?? NOOP,
    setMonitorAwake: s.setMonitorAwake ?? NOOP,
    setTouch: s.setTouch ?? NOOP,
  };
}
