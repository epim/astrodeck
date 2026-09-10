import type { JSX } from "react";
import type { Tone } from "./types";

/** The phase pill: SLEWING / FOCUSING / GUIDING / CAPTURING / PAUSED / DONE, or
 *  an incident state. The dot pulses at 1.4 s while the phase is live;
 *  `next.css` stops the pulse under `prefers-reduced-motion`, where the pill
 *  still reads because the TEXT is the state, not the animation. */
export function StatusPill({ text, tone = "accent", pulse = false, color, className = "", ...rest }: {
  text: string;
  tone?: Tone;
  pulse?: boolean;
  /** Overrides the tone with an incident's own colour value. */
  color?: string;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <span
      className={`nx-status-pill ${className}`.trim()}
      data-tone={tone}
      style={color ? { color, borderColor: color } : undefined}
      data-testid={rest["data-testid"]}
    >
      <span
        className="nx-status-dot"
        data-pulse={pulse ? "true" : "false"}
        style={color ? { background: color } : undefined}
        aria-hidden="true"
      />
      {text}
    </span>
  );
}
