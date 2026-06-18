import { useStore } from "../store";
import { Icon } from "./icons";

/**
 * Full-width strip under the header, driven by store wsPhase + telemetryStale.
 * Returns null when the link is up, telemetry is fresh, and a rig is connected.
 *
 * Three states, in priority order:
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

  const down = phase !== "up";
  const connecting = phase === "connecting" || phase === "reconnecting";
  // Quiet no-rig note only when the link is healthy AND we're not already on the
  // Rig page (where connecting is the whole point — a banner there is redundant).
  const noRig = !down && !stale && !equipConnected && view !== "connect";

  if (!down && !stale && !noRig) return null;

  if (noRig) {
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
          Connect equipment on the Rig page to see live telemetry.
        </span>
      </div>
    );
  }

  const label = down
    ? connecting ? "CONNECTING…" : "DISPLAY DISCONNECTED"
    : "TELEMETRY CATCHING UP";
  const detail = down
    ? "Your view lost the AstroDeck server — the rig keeps running. Reconnecting…"
    : "Waiting for fresh telemetry — values may be a few seconds old.";

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center gap-2 px-4 min-h-9 border-b border-line bg-raise/80 backdrop-blur shrink-0"
    >
      <span className={`led led-warn ${connecting ? "blink" : "blink-alert"} shrink-0`} />
      <Icon name="alert" size={14} className="text-warn shrink-0" />
      <span className="label !text-[11px] text-ink shrink-0">{label}</span>
      <span className="text-[11px] text-dim hidden sm:inline truncate">{detail}</span>
    </div>
  );
}
