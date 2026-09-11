import type { JSX, ReactNode } from "react";
import { lockedAttrs, lockedClass, honestPress } from "./honest";

/** The Sky toolbar button: 48 px square-ish, glyph over a caps label. The
 *  design's 7.5-8.5 px label is raised to the 10 px floor. */
export function IconButton48({ glyph, label, onPress, active = false, lockedReason = null, onExplain, className = "", ...rest }: {
  glyph: ReactNode;
  label: string;
  onPress: () => void;
  active?: boolean;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <button
      type="button"
      className={lockedClass(lockedReason, `nx-iconbtn ${className}`.trim())}
      data-active={active ? "true" : "false"}
      aria-pressed={active}
      onClick={honestPress(lockedReason, onExplain, onPress)}
      data-testid={rest["data-testid"]}
      {...lockedAttrs(lockedReason)}
    >
      <span className="nx-iconbtn-glyph" aria-hidden="true">{glyph}</span>
      <span className="nx-iconbtn-label">{label}</span>
    </button>
  );
}
