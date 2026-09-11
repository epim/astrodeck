import type { JSX, ReactNode } from "react";
import { lockedAttrs, lockedClass, honestPress } from "./honest";
import type { Tone } from "./types";

/** Sub-nav and filter chip, 40 px tall (the design's 38 is raised to keep the
 *  44 px rule reachable with the row's padding; SubNav sets the row height).
 *  `count` is the badge after the label - GALLERY 8, FLOWS 9. */
export function Chip({ children, active = false, count, onClick, tone = "accent", dashed = false, lockedReason = null, onExplain, className = "", ...rest }: {
  children: ReactNode;
  active?: boolean;
  count?: number;
  onClick?: () => void;
  tone?: Tone;
  dashed?: boolean;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  const press = honestPress(lockedReason, onExplain, () => onClick?.());
  return (
    <button
      type="button"
      className={lockedClass(lockedReason, `nx-chip ${className}`.trim())}
      data-tone={tone}
      data-active={active ? "true" : "false"}
      data-dashed={dashed ? "true" : undefined}
      aria-pressed={onClick ? active : undefined}
      onClick={press}
      data-testid={rest["data-testid"]}
      {...lockedAttrs(lockedReason)}
    >
      <span className="nx-chip-text">{children}</span>
      {count != null && <span className="nx-chip-count">{count}</span>}
    </button>
  );
}
