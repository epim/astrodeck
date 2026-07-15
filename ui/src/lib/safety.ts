// safety.ts — pure normalizer for the SafetyMonitor snapshot into the store's
// SafetyState. No React, no DOM: npx-tsx testable (schedule.ts precedent).
//
// The server sends the SAME flat SafetyReading dict — {is_safe, reason, source,
// detail, stale, ts} — in all three places the store consumes safety:
//   • the `hello` cold snapshot   (hub.summary()      → "safety": dict | null)
//   • the 2s `status` poll         (hub.poll_status()  → out["safety"]: dict | null)
//   • the on-change `safety` event (hub._safety_loop() → publish("safety", **dict))
// In every case the payload is that flat dict OR `null` when no monitor is
// connected. The own-cadence poller CLEARS its cache to None on a mid-session
// disconnect, and poll_status forwards that null on the very next 2s tick — so
// routing the `status` channel through this normalizer is what lets
// store.safety.connected self-heal on drop/reconnect WITHOUT a dedicated
// disconnect event.
//
// `connected` is derived fail-closed (mirrors hub's fail-closed poller): a null
// payload → not connected; a `stale` reading (read timed out / device dropped)
// → not connected; otherwise connected. `streak` is a UI-preserved accumulator
// (consecutive unsafe reads) that the raw payloads don't carry, so callers pass
// the prior streak to keep it across the wholesale status/hello replace.

import type { SafetyReading, SafetyState } from "../types";

export function normalizeSafety(
  raw: SafetyReading | null | undefined,
  prevStreak = 0,
): SafetyState {
  if (!raw) return { connected: false, reading: null, streak: prevStreak };
  return { connected: !raw.stale, reading: raw, streak: prevStreak };
}
