import { useStore } from "../store";
import { Icon } from "./icons";

/**
 * Full-width strip under the header, driven by store wsPhase + telemetryStale.
 * Returns null when the link is up and telemetry is fresh.
 *
 * Copy must NOT imply the rig stopped — only this browser's view lost the socket;
 * the backend, engine, mount and cooler keep running. A Wi-Fi blip is amber, not
 * alarm-red; the fill is a dim neutral-dark wash, not a saturated red behind red
 * text (which fails AA in night mode). Only the LED/glyph carries the hue.
 */
export default function ConnectionBanner() {
  const phase = useStore((s) => s.wsPhase);
  const stale = useStore((s) => s.telemetryStale);

  if (phase === "up" && !stale) return null;

  const down = phase !== "up";
  const connecting = phase === "connecting" || phase === "reconnecting";
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
