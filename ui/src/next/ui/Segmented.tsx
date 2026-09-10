import { useRef, type JSX } from "react";
import { lockedAttrs, lockedClass } from "./honest";

export interface SegmentedOption<T extends string | number> {
  value: T;
  label: string;
  sub?: string;
}

/** BINNING 1x1 / 2x2 / 3x3, SOFT / AUTO / HARD, FITS / JPEG. A real radiogroup:
 *  one tab stop, arrows move between the options - `aria-pressed` buttons would
 *  announce three independent toggles for a single choice. */
export function Segmented<T extends string | number>({ options, value, onChange, label, lockedReason = null, onExplain, className = "", ...rest }: {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (next: T) => void;
  label: string;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  const group = useRef<HTMLDivElement | null>(null);

  const pick = (next: T) => {
    if (lockedReason) { onExplain?.(lockedReason); return; }
    onChange(next);
  };

  const onKeyDown = (e: { key: string; preventDefault: () => void }) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1
      : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (step === 0 || options.length === 0) return;
    e.preventDefault();
    const at = options.findIndex((o) => o.value === value);
    const next = options[(((at < 0 ? 0 : at) + step) + options.length) % options.length];
    pick(next.value);
    // Roving tabindex: the newly selected option becomes the one tab stop, so
    // focus has to follow it or the next arrow key lands on nothing.
    const el = group.current?.querySelector<HTMLButtonElement>(
      `[data-value="${String(next.value)}"]`,
    );
    el?.focus();
  };

  return (
    <div
      ref={group}
      role="radiogroup"
      aria-label={label}
      className={lockedClass(lockedReason, `nx-seg ${className}`.trim())}
      onKeyDown={onKeyDown}
      data-testid={rest["data-testid"]}
      {...lockedAttrs(lockedReason)}
    >
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            data-value={String(o.value)}
            data-selected={on ? "true" : "false"}
            className="nx-seg-opt"
            onClick={() => pick(o.value)}
          >
            <span className="nx-seg-label">{o.label}</span>
            {o.sub != null && <span className="nx-seg-sub">{o.sub}</span>}
          </button>
        );
      })}
    </div>
  );
}
