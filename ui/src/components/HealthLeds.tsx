import { useStore } from "../store";
import { ninaTitle } from "../lib/health";
import type { NinaHealth, NinaState } from "../types";

/**
 * Two-hop connection health: browser↔backend (LINK) and backend↔NINA (NINA).
 * Each LED is NOT color-only — it carries a letter (L / N) inside the dot and a
 * per-severity blink rate so it survives the all-red night palette where
 * --good/--warn/--bad are three near-identical reds.
 *
 * Driven by the store: wsPhase (LINK) and ninaHealth (NINA, only when active).
 */

type Sev = "ok" | "warn" | "bad";

function ledClass(sev: Sev): string {
  // .led-on / .led-warn (index.css) + .led-bad (reliability subset).
  return sev === "bad" ? "led-bad" : sev === "warn" ? "led-warn" : "led-on";
}

function blinkClass(sev: Sev): string {
  // steady = ok; slow .blink = warn; fast .blink-alert = bad.
  return sev === "bad" ? "blink-alert" : sev === "warn" ? "blink" : "";
}

/** One LED dot with an inline letter cue + an always-present short label. */
function HealthLed({ letter, label, sev, title, night }: {
  letter: string; label: string; sev: Sev; title: string; night: boolean;
}) {
  const toneText = sev === "bad" ? "text-bad" : sev === "warn" ? "text-warn" : "text-good";
  return (
    <div className="flex items-center gap-1.5" title={title}>
      <span
        role="img"
        aria-label={title}
        className={`led-letter ${ledClass(sev)} ${blinkClass(sev)} text-bg`}
      >
        {letter}
      </span>
      {/* label always visible in night mode (shape/letter alone is too subtle in red),
          otherwise it stays a compact desktop-only label. */}
      <span className={`label ${night ? "inline" : "hidden sm:inline"} ${toneText}`}>
        {label}
      </span>
    </div>
  );
}

const NINA_SEV: Record<NinaState, Sev> = {
  ok: "ok", warming: "warn", stale: "warn", error: "bad", down: "bad", na: "ok",
};

function ninaLabel(h: NinaHealth): string {
  switch (h.state) {
    case "ok": return "NINA";
    case "warming": return "NINA…";
    case "stale": return "NINA?";
    case "error": return "NINA!";
    case "down": return "NINA!";
    default: return "NINA";
  }
}

export default function HealthLeds() {
  const wsPhase = useStore((s) => s.wsPhase);
  const ninaHealth = useStore((s) => s.ninaHealth);
  const night = useStore((s) => s.night);

  const linkUp = wsPhase === "up";
  const linkSev: Sev = linkUp ? "ok" : "bad";
  const linkTitle = linkUp
    ? "Display link up"
    : wsPhase === "connecting" ? "Connecting to AstroDeck server…"
    : "Display disconnected — the rig keeps running. Reconnecting…";

  return (
    <div className="flex items-center gap-3">
      <HealthLed
        letter="L"
        label={linkUp ? "LINK" : "NO LINK"}
        sev={linkSev}
        title={linkTitle}
        night={night}
      />
      {ninaHealth.active && (
        <HealthLed
          letter="N"
          label={ninaLabel(ninaHealth)}
          sev={NINA_SEV[ninaHealth.state]}
          title={ninaTitle(ninaHealth)}
          night={night}
        />
      )}
    </div>
  );
}
