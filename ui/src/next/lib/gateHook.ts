// gateHook.ts — the THIN React wrapper over gate.ts's pure `lockReason`
// (ARCHITECTURE.md #8's `useLock`). Split into its own file, deliberately
// NOT in gate.ts, so `gate.ts` stays store-free/React-free like the rest of
// `next/lib` (the T0.3 brief's explicit deviation from ARCHITECTURE.md's
// single-file sketch, where `useLock` is shown alongside `lockReason`).
//
// This is the one file in `next/lib` allowed to import React and the store.
// It is NOT unit-tested here (a thin hook has nothing to assert beyond "it
// calls lockReason with the store's four fields and wires onExplain to
// enqueueToast" — both already covered by gate.test.ts and the store's own
// tests); a DOM test belongs with whichever primitive/hub first consumes it.

import { usePrincipal, useStatus, useEquipConnected, useWsPhase, useStore } from "../../store";
import { lockReason, type GateInput } from "./gate";

export interface UseLockResult {
  lockedReason: string | null;
  onExplain: (reason: string) => void;
}

export function useLock(inp: GateInput): UseLockResult {
  const principal = usePrincipal();
  const status = useStatus();
  const equipConnected = useEquipConnected();
  const wsPhase = useWsPhase();

  const lockedReason = lockReason(inp, { principal, status, equipConnected, wsPhase });

  const onExplain = (reason: string) => {
    useStore.getState().enqueueToast({ level: "warning", title: reason });
  };

  return { lockedReason, onExplain };
}
