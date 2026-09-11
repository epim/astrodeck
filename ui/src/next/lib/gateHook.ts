// gateHook.ts - the THIN React wrapper over gate.ts's pure `lockReason`
// (ARCHITECTURE.md #8's `useLock`). Split into its own file, deliberately
// NOT in gate.ts, so `gate.ts` stays store-free/React-free like the rest of
// `next/lib` (the T0.3 brief's explicit deviation from ARCHITECTURE.md's
// single-file sketch, where `useLock` is shown alongside `lockReason`).
//
// This is the one file in `next/lib` allowed to import React and the store.
// It is NOT unit-tested here (a thin hook has nothing to assert beyond "it
// calls lockReason with the store's four fields and wires onExplain to
// enqueueToast" - both already covered by gate.test.ts and the store's own
// tests); a DOM test belongs with whichever primitive/hub first consumes it.
//
// The fifth field is `onRelay`, from `next/lib/relay.ts` - the SAME derivation
// Settings > Connection draws its cards from, so a control's "you are on the
// relay" and that screen's "you are here" can never disagree. It is a
// subscription rather than a read so that a control rendered before `GET
// /api/remote/status` answered re-renders when the rig's own `via` arrives.

import { useSyncExternalStore } from "react";
import { usePrincipal, useStatus, useEquipConnected, useWsPhase, useStore } from "../../store";
import { lockReason, type GateInput } from "./gate";
import { onRelay, subscribeRelay } from "./relay";

export interface UseLockResult {
  lockedReason: string | null;
  onExplain: (reason: string) => void;
}

/** True while this tab is tunnelled through the relay. Exported for the few
 *  surfaces that need the fact itself rather than a lock reason (a note on a
 *  screen, a branch in a model); every CONTROL should take `needsLan: true`
 *  through `useLock` instead, so the sentence is written once. */
export function useOnRelay(): boolean {
  return useSyncExternalStore(subscribeRelay, onRelay, onRelay);
}

export function useLock(inp: GateInput): UseLockResult {
  const principal = usePrincipal();
  const status = useStatus();
  const equipConnected = useEquipConnected();
  const wsPhase = useWsPhase();
  const relay = useOnRelay();

  const lockedReason = lockReason(inp, {
    principal, status, equipConnected, wsPhase, onRelay: relay,
  });

  const onExplain = (reason: string) => {
    useStore.getState().enqueueToast({ level: "warning", title: reason });
  };

  return { lockedReason, onExplain };
}
