// telemetry.ts — pure staleness gate for the ConnectionBanner's "TELEMETRY
// CATCHING UP" state (reliability §13). The server's 2s status poller only runs
// while a rig is connected, so with NOTHING connected the /ws socket goes quiet
// by design and the client's age timer would otherwise trip — falsely alarming
// "telemetry catching up" when there is simply no telemetry to report.
//
// The gate: telemetry can only be "stale" when at least one device is connected.
// `connected` is the sticky equipConnected signal (status.mode !== "none"); pass
// it straight through. `busy` suppresses staleness during a known long op
// (slew/solve/AF/capture legitimately block the poll). This mirrors tickStale()'s
// original predicate, now guarded by the connected precondition.

/** Stale ONLY if: a rig is connected, the link is up, no long-op is in flight,
 *  AND no frame has arrived for `staleMs`. Disconnected ⇒ NEVER stale. */
export function computeTelemetryStale(args: {
  connected: boolean;
  busy: string | null;
  ageMs: number;
  staleMs: number;
}): boolean {
  const { connected, busy, ageMs, staleMs } = args;
  if (!connected) return false; // nothing connected ⇒ no telemetry expected
  return busy === null && ageMs > staleMs;
}
