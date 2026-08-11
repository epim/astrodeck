// telemetry.ts — the pure staleness gate behind the ConnectionBanner.
//
// The server's 2s status poller only runs while a rig is connected, so with
// NOTHING connected the /ws socket goes quiet by design and a naive age timer
// would falsely alarm. And a known long op (slew, solve, autofocus, an exposure)
// legitimately blocks that poll for a while. Both are real reasons to expect a
// gap, and both used to be absolute vetoes.
//
// THAT IS THE BUG THIS FILE NOW FIXES (#224). Reported 2026-08-10 03:40 — "any
// idea why the last capture was 2 hours ago". It hadn't been: frame 0125 had
// landed 30 seconds earlier, 125 frames that night, guiding at RMS 1.11, all six
// device links up. The operator's VIEW was two hours old and said nothing.
//
// The reason it said nothing is that `busy` is READ FROM THE CLIENT'S OWN LAST
// STATUS MESSAGE. When the stream dies mid-capture — which, during a sequence,
// is nearly always — `busy` freezes at "capture" and never clears, because the
// message that would have cleared it is the message that stopped arriving. So
// `busy !== null` suppressed the staleness check FOREVER, using a value the very
// outage had frozen. The watchdog was disarmed by the thing it was watching for.
//
// This is the shape recorded in memory as the dead-link class: `connected` was a
// memory rather than a measurement, so the flag that should have driven recovery
// was the flag suppressing it. `ageMs` is the only input here that is a genuine
// measurement — it is computed from the wall clock against the arrival time of
// the last frame — so it is the only one allowed the final word.
//
// The rule is therefore: a long op BUYS TIME, it does not buy silence.

/** Beyond this age nothing legitimises the quiet, whatever `busy` says.
 *
 *  Sized off the longest gap a healthy rig can actually produce. The server
 *  polls status every 2 s while connected, so most gaps are seconds; the
 *  exceptions are a long exposure plus its tail — this rig's Ha subs are 180 s,
 *  and dither, settle, filter move and download add perhaps a further 60 s. Five
 *  minutes is comfortably past that worst case and 24x short of the two hours
 *  that went unreported. */
export const HARD_STALE_MS = 300_000;

/**
 * True when what is on screen should no longer be presented as live.
 *
 * `connected` is the sticky equipConnected signal; `busy` is the backend's
 * current long op, or null. Both come from the last message received, which is
 * exactly why neither may veto past `hardStaleMs`.
 *
 * Disconnected still means never stale: with no rig there is no telemetry to be
 * behind on, and the app says "nothing connected" through a different surface.
 * That gate reads a frozen value too, but it fails in the honest direction — a
 * client stuck on "nothing connected" under-claims rather than over-claims.
 */
export function computeTelemetryStale(args: {
  connected: boolean;
  busy: string | null;
  ageMs: number;
  staleMs: number;
  hardStaleMs?: number;
}): boolean {
  const { connected, busy, ageMs, staleMs } = args;
  const hardStaleMs = args.hardStaleMs ?? HARD_STALE_MS;
  if (!connected) return false; // nothing connected ⇒ no telemetry expected
  if (ageMs > hardStaleMs) return true; // no long op runs this long
  return busy === null && ageMs > staleMs;
}

/**
 * How the banner should describe the gap: `null` while things are fine, else a
 * severity plus a sentence that STATES THE AGE.
 *
 * Age in words is the whole point. "TELEMETRY CATCHING UP" is true of a 20-second
 * gap and of a two-hour one, and the operator cannot tell which they are looking
 * at — which is how a frozen screen got read as a rig that had stopped imaging.
 * A number cannot be misread that way.
 */
export function telemetryStaleNotice(ageMs: number, hardStaleMs = HARD_STALE_MS): {
  level: "warn" | "error";
  title: string;
  detail: string;
} | null {
  if (ageMs <= 0) return null;
  if (ageMs <= hardStaleMs) {
    return {
      level: "warn",
      title: "TELEMETRY CATCHING UP",
      detail: `No update for ${formatAge(ageMs)}. The rig may be mid-exposure.`,
    };
  }
  return {
    level: "error",
    title: `SHOWING DATA FROM ${formatAge(ageMs).toUpperCase()} AGO`,
    detail:
      `Nothing has arrived from the rig for ${formatAge(ageMs)}, so every ` +
      `number on screen is that old. This does NOT mean the rig has stopped — ` +
      `reconnecting now.`,
  };
}

/** "42 seconds" / "6 minutes" / "2 hours 5 minutes". Coarse on purpose: the
 *  decision this informs is "is this current or not", not a stopwatch. */
export function formatAge(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 90) return `${s} second${s === 1 ? "" : "s"}`;
  const m = Math.round(s / 60);
  if (m < 90) return `${m} minute${m === 1 ? "" : "s"}`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem
    ? `${h} hour${h === 1 ? "" : "s"} ${rem} minute${rem === 1 ? "" : "s"}`
    : `${h} hour${h === 1 ? "" : "s"}`;
}
