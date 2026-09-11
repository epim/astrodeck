import { useRef, type JSX } from "react";
import { lockedAttrs, lockedClass } from "./honest";

export interface SegmentedOption<T extends string | number> {
  value: T;
  label: string;
  sub?: string;
  /** Why THIS option cannot be picked while the rest of the group is live (e.g.
   *  "4x4 needs a camera that reports max_bin"). Honest-disabled per option, on
   *  the same terms as the whole group: it stays rendered, stays reachable, and
   *  a press states the reason instead of silently doing nothing. Hiding the
   *  option instead would make a three-way choice look like a two-way one. */
  lockedReason?: string | null;
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
    // The group is live but this ONE option is not. Same contract as the group
    // lock: explain, never change, never swallow the press in silence.
    const own = options.find((o) => o.value === next)?.lockedReason;
    if (own) { onExplain?.(own); return; }
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
        // The group's own reason wins when both are set: it is the outer wall,
        // and telling the user why 4x4 is unavailable is noise when nothing in
        // the group can be pressed at all.
        const own = lockedReason ? null : (o.lockedReason || null);
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            data-value={String(o.value)}
            data-selected={on ? "true" : "false"}
            className={lockedClass(own, "nx-seg-opt")}
            onClick={() => pick(o.value)}
            {...lockedAttrs(own)}
          >
            <span className="nx-seg-label">{o.label}</span>
            {o.sub != null && <span className="nx-seg-sub">{o.sub}</span>}
          </button>
        );
      })}
    </div>
  );
}
