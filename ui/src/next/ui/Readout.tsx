import type { JSX, ReactNode } from "react";
import { lockedAttrs, lockedClass, honestPress } from "./honest";
import type { Tone } from "./types";

/** The device-sheet readout grid: 4 columns by default (README section 8,
 *  "readout tiles 60 px in a 4-col grid"). */
export function ReadoutGrid({ children, cols = 4, className = "", ...rest }: {
  children: ReactNode;
  cols?: 3 | 4;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <div className={`nx-readouts ${className}`.trim()} data-cols={cols} data-testid={rest["data-testid"]}>
      {children}
    </div>
  );
}

/** One 60 px tile: caps label, mono value, dim sub. Selecting a tile is what
 *  points the sheet's single Dial at that setting.
 *
 *  Selection is carried by an accent BORDER **and** a marker bar, never by hue
 *  alone: under `:root.night` every token is the same red and a border-colour
 *  swap would be invisible. The 9.5 / 8.5 px label and sub are raised to the
 *  10 px floor. */
export function ReadoutTile({ label, value, sub, selected = false, onSelect, tone, ariaLabel, lockedReason = null, onExplain, className = "", ...rest }: {
  label: string;
  value: ReactNode;
  sub?: string;
  selected?: boolean;
  onSelect?: () => void;
  tone?: Tone;
  ariaLabel?: string;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  const body = (
    <>
      {selected && <span className="nx-readout-mark" aria-hidden="true" />}
      <span className="nx-readout-label">{label}</span>
      <span className="nx-readout-value">{value}</span>
      {sub != null && <span className="nx-readout-sub">{sub}</span>}
    </>
  );
  const cls = `nx-readout ${className}`.trim();
  if (!onSelect) {
    return (
      <div className={cls} data-tone={tone} data-selected={selected ? "true" : "false"}
        aria-label={ariaLabel} data-testid={rest["data-testid"]}>{body}</div>
    );
  }
  return (
    <button
      type="button"
      className={lockedClass(lockedReason, cls)}
      data-tone={tone}
      data-selected={selected ? "true" : "false"}
      aria-pressed={selected}
      aria-label={ariaLabel ?? `${label} ${typeof value === "string" || typeof value === "number" ? value : ""}`.trim()}
      onClick={honestPress(lockedReason, onExplain, onSelect)}
      data-testid={rest["data-testid"]}
      {...lockedAttrs(lockedReason)}
    >
      {body}
    </button>
  );
}
