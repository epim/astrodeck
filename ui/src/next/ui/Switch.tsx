import type { JSX } from "react";
import { lockedAttrs, lockedClass, honestPress } from "./honest";

/** The design's 40x24 toggle with an 18 px knob.
 *
 *  Shape, never hue alone (ARCHITECTURE section 6): the knob POSITION carries
 *  the state, and an ON/OFF micro label rides in the DOM at all times, revealed
 *  by `next.css` under `:root.night` - where every token collapses onto one red
 *  hue and a colour-only toggle stops being readable at all. */
export function Switch({ checked, onChange, label, note, lockedReason = null, onExplain, className = "", ...rest }: {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** The accessible name. Rendered as the row title unless `hideLabel`. */
  label: string;
  note?: string;
  hideLabel?: boolean;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  const hideLabel = rest.hideLabel ?? false;
  const control = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={lockedClass(lockedReason, "nx-switch")}
      data-checked={checked ? "true" : "false"}
      onClick={honestPress(lockedReason, onExplain, () => onChange(!checked))}
      data-testid={rest["data-testid"]}
      {...lockedAttrs(lockedReason)}
    >
      <span className="nx-switch-knob" aria-hidden="true" />
      <span className="nx-switch-state" aria-hidden="true">{checked ? "ON" : "OFF"}</span>
    </button>
  );
  if (hideLabel) return control;
  return (
    <div className={`nx-switch-row ${className}`.trim()}>
      {control}
      <span className="nx-switch-text">
        <span className="nx-switch-title">{label}</span>
        {note != null && <span className="nx-switch-note">{note}</span>}
      </span>
    </div>
  );
}
