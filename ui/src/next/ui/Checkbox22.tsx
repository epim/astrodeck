import type { JSX, ReactNode } from "react";
import { lockedAttrs, lockedClass, honestPress } from "./honest";

/** The 22 px checkbox from the quick-session filter rows. The box carries a
 *  tick when checked (shape, not fill colour), and the whole row is the hit
 *  target so the 44 px floor is met without a 44 px box. */
export function Checkbox22({ checked, onChange, label, lockedReason = null, onExplain, className = "", ...rest }: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: ReactNode;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      className={lockedClass(lockedReason, `nx-check ${className}`.trim())}
      data-checked={checked ? "true" : "false"}
      onClick={honestPress(lockedReason, onExplain, () => onChange(!checked))}
      data-testid={rest["data-testid"]}
      {...lockedAttrs(lockedReason)}
    >
      <span className="nx-check-box" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
          strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 12.5l5 5L19 7" />
        </svg>
      </span>
      <span className="nx-check-label">{label}</span>
    </button>
  );
}
