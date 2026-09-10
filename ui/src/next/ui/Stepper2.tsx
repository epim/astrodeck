import type { JSX } from "react";
import { lockedAttrs, lockedClass, honestPress } from "./honest";

/** The `-` value `+` pair from the filter-wheel focus offsets. Both buttons are
 *  44 px; the value between them is the live readout, not an input, because the
 *  design's steppers all move in fixed engine steps. */
export function Stepper2({ value, onChange, step, min, max, format, label, lockedReason = null, onExplain, className = "", ...rest }: {
  value: number;
  onChange: (next: number) => void;
  step: number;
  min?: number;
  max?: number;
  format?: (v: number) => string;
  label: string;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  const clamp = (v: number) => Math.min(max ?? Infinity, Math.max(min ?? -Infinity, v));
  const shown = format ? format(value) : String(value);
  const atMin = min != null && value <= min;
  const atMax = max != null && value >= max;

  // A limit is a REASON, not a dead button: at the end of the range the press
  // still lands and still says why nothing moved.
  const lo = lockedReason ?? (atMin ? `${label} is at its lowest setting` : null);
  const hi = lockedReason ?? (atMax ? `${label} is at its highest setting` : null);

  return (
    <div className={`nx-stepper ${className}`.trim()} role="group" aria-label={label}
      data-testid={rest["data-testid"]}>
      <button
        type="button"
        className={lockedClass(lo, "nx-stepper-btn")}
        aria-label={`${label} down`}
        onClick={honestPress(lo, onExplain, () => onChange(clamp(value - step)))}
        {...lockedAttrs(lo)}
      >-</button>
      <span className="nx-stepper-value">{shown}</span>
      <button
        type="button"
        className={lockedClass(hi, "nx-stepper-btn")}
        aria-label={`${label} up`}
        onClick={honestPress(hi, onExplain, () => onChange(clamp(value + step)))}
        {...lockedAttrs(hi)}
      >+</button>
    </div>
  );
}
