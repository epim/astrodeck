import { useEffect, useState } from "react";
import { useStore } from "../store";
import { useAuthRequiredForBanner } from "../lib/caps";
import { bannerState, isConnecting } from "../lib/connection";
import { telemetryStaleNotice } from "../lib/telemetry";
import { Icon } from "./icons";

/**
 * Milliseconds since the last frame arrived, re-read once a second WHILE STALE.
 *
 * A hook rather than a store field on purpose: this is the one number in the app
 * that has to keep changing when nothing is happening. Every other value updates
 * because a message arrived, and during an outage no message arrives — so a
 * banner that rendered the age once would freeze at "20 seconds" and stay there
 * for two hours, which is the same lie in a new costume.
 *
 * The interval only runs while `active`, so a healthy session pays nothing.
 */
function useStaleAgeMs(active: boolean): number {
  const last = useStore((s) => s.wsLastEvent);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return Math.max(0, now - last);
}

/**
 * Full-width strip under the header, driven by store wsPhase + telemetryStale.
 * Returns null when the link is up, telemetry is fresh, and a rig is connected.
 *
 * States, in priority order (the decision lives in lib/connection.bannerState so
 * it is unit-testable):
 *  0. link down BECAUSE sign-in is required (server reachable, WS upgrade rejected
 *     for lack of a session) → a calm "SIGN-IN REQUIRED" note, NOT an alarm. H1:
 *     a browser can't read a rejected WS handshake's status, so this is derived
 *     from a fetch (/api/me → 401). In normal flow App routes to the full Login
 *     screen before this strip mounts; this is the fail-open-after-grace fallback.
 *  1. link down/connecting → "DISPLAY DISCONNECTED" / "CONNECTING…" (warn LED).
 *     Copy must NOT imply the rig stopped — only this browser's view lost the
 *     socket; the backend, engine, mount and cooler keep running. A Wi-Fi blip is
 *     amber, not alarm-red.
 *  2. link up but telemetry stale (a genuine 20s WS stall WHILE a rig is
 *     connected) → "TELEMETRY CATCHING UP" (warn LED). telemetryStale can only be
 *     true when a rig is connected (gated in ws.ts tickStale), so this never fires
 *     just because nothing is plugged in.
 *  3. link up, fresh, but NO rig connected → a QUIET "NO RIG CONNECTED" note (dim
 *     led-off dash, neutral copy — NOT an alarm). Suppressed on the Rig page where
 *     the picker already makes the state obvious and the interstitial covers gated
 *     views, so the two don't shout the same thing twice.
 *
 * The fill is a dim neutral-dark wash, not a saturated red behind red text (which
 * fails AA in night mode). Only the LED/glyph carries any hue.
 */
export default function ConnectionBanner() {
  const phase = useStore((s) => s.wsPhase);
  const stale = useStore((s) => s.telemetryStale);
  const equipConnected = useStore((s) => s.equipConnected);
  const view = useStore((s) => s.view);
  const authRequired = useAuthRequiredForBanner();

  const kind = bannerState({ phase, stale, equipConnected, view, authRequired });
  // Hooks are unconditional — the early returns below must not sit above it.
  const ageMs = useStaleAgeMs(kind === "stale");

  if (kind === "hidden") return null;

  if (kind === "signin") {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 px-4 min-h-9 border-b border-line bg-raise/80 backdrop-blur shrink-0"
      >
        <span className="led led-warn shrink-0" />
        <Icon name="user" size={14} className="text-accent shrink-0" />
        <span className="label !text-[11px] text-ink shrink-0">SIGN-IN REQUIRED</span>
        <span className="text-[11px] text-dim hidden sm:inline truncate">
          This server now requires an account — sign in to reconnect the display.
        </span>
      </div>
    );
  }

  if (kind === "norig") {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 px-4 min-h-9 border-b border-line bg-raise/60 backdrop-blur shrink-0"
      >
        <span className="led led-off shrink-0" />
        <Icon name="rig" size={14} className="text-faint shrink-0" />
        <span className="label !text-[11px] text-dim shrink-0">NO RIG CONNECTED</span>
        <span className="text-[11px] text-faint hidden sm:inline truncate">
          Connect a mount, camera, or other device to see live telemetry.
        </span>
      </div>
    );
  }

  const connecting = isConnecting(phase);
  // THE AGE, IN WORDS, WHEN THE DATA IS STALE. "TELEMETRY CATCHING UP" is
  // equally true of a 20-second gap and of the two-hour one reported on
  // 2026-08-10, and the operator has no way to tell which they are reading —
  // which is how a frozen screen got mistaken for a rig that had stopped
  // imaging at 3am. A number cannot be misread that way. Recomputed on a tick
  // rather than at render, because nothing else re-renders during an outage
  // (that is what an outage IS).
  const notice = kind === "stale" ? telemetryStaleNotice(ageMs) : null;
  const severe = notice?.level === "error";

  const label =
    kind === "down"
      ? connecting
        ? "CONNECTING…"
        : "DISPLAY DISCONNECTED"
      : notice?.title ?? "TELEMETRY CATCHING UP";
  const detail =
    kind === "down"
      ? "Your view lost the AstroDeck server — the rig keeps running. Reconnecting…"
      : notice?.detail ?? "Waiting for fresh telemetry — values may be a few seconds old.";

  return (
    <div
      role="status"
      // A two-hour-old screen is an assertive announcement, not a polite one:
      // a screen reader must interrupt rather than wait to be asked.
      aria-live={severe ? "assertive" : "polite"}
      className={
        "flex items-center gap-2 px-4 min-h-9 border-b shrink-0 backdrop-blur "
        + (severe
          ? "border-bad/60 bg-bad/15"
          : "border-line bg-raise/80")
      }
    >
      <span className={`led ${severe ? "led-bad" : "led-warn"} ${connecting ? "blink" : "blink-alert"} shrink-0`} />
      <Icon name="alert" size={14} className={severe ? "text-bad shrink-0" : "text-warn shrink-0"} />
      <span className="label !text-[11px] text-ink shrink-0">{label}</span>
      {/* NOT `hidden sm:inline` when severe: the sentence that says the data is
          hours old is the entire message, and hiding it on a phone hides it on
          the device most likely to be checked from bed. */}
      <span className={
        "text-[11px] truncate " + (severe ? "text-ink" : "text-dim hidden sm:inline")
      }>{detail}</span>
    </div>
  );
}
