// SegmentedControl — an accessible tri-/multi-state segmented "pill" (mount
// tracking-rate spec, Task 5). A single XOR selector styled as a modern
// instrument control: a recessed track with the ACTIVE segment lifted as a
// sliding accent pill that glides between options.
//
// A11y: it is a real `role="radiogroup"` of `role="radio"` buttons with
// `aria-checked`; roving tabindex (only the selected — or first — radio is in the
// tab order); full keyboard model (Arrow/Home/End move+select, Enter/Space select
// the focused one). Every segment is a >=44px tap target.
//
// Night-mode safe: colour comes ONLY from design-system tokens (the accent fill +
// its ink, `--raise`/`--line` for the track), so the all-red night palette themes
// it automatically — the ACTIVE state is also carried by the filled pill (shape),
// never hue alone. No hooks (pure function of props): focus movement is done
// imperatively off the event so the component stays trivially testable.

import type {
  KeyboardEvent as RKeyboardEvent,
  PointerEvent as RPointerEvent,
} from "react";

export interface SegmentedControlProps<T extends string> {
  options: { value: T; label: string }[];
  value: T | undefined;
  onChange: (v: T) => void;
  disabled?: boolean;
  ariaLabel: string;
}

/** Pure keyboard-navigation map: the target index for a navigation key, or `null`
 *  for any non-navigation key (Enter/Space/typing). Arrows wrap; Home/End clamp. */
export function segmentedNextIndex(key: string, current: number, count: number): number | null {
  switch (key) {
    case "ArrowRight":
    case "ArrowDown":
      return (current + 1) % count;
    case "ArrowLeft":
    case "ArrowUp":
      return (current - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

// Move DOM focus to the radio at `idx`. Guarded end-to-end so a synthetic event
// (no real DOM, as in the unit tests) is a harmless no-op.
function focusRadioAt(
  e: RKeyboardEvent<HTMLButtonElement> | { currentTarget?: { closest?: (s: string) => Element | null } },
  idx: number,
): void {
  const group = e.currentTarget?.closest?.("[role=radiogroup]");
  const radios = group?.querySelectorAll?.("[role=radio]");
  (radios?.[idx] as HTMLElement | undefined)?.focus?.();
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  disabled = false,
  ariaLabel,
}: SegmentedControlProps<T>) {
  const activeIndex = options.findIndex((o) => o.value === value);
  // Roving tabindex: the selected radio owns the tab stop; with nothing selected
  // the first radio is the entry point (standard radiogroup behaviour).
  const tabIndex = activeIndex >= 0 ? activeIndex : 0;
  const n = options.length;

  const select = (i: number) => { if (!disabled) onChange(options[i].value); };

  const onKeyDown = (e: RKeyboardEvent<HTMLButtonElement>, i: number) => {
    if (disabled) return;
    const next = segmentedNextIndex(e.key, i, n);
    if (next !== null) {
      e.preventDefault();
      select(next);
      focusRadioAt(e, next); // move focus to follow selection
      return;
    }
    if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      select(i);
    }
  };

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      aria-disabled={disabled || undefined}
      className={`relative inline-grid grid-flow-col auto-cols-fr items-stretch
        border border-line2 bg-raise p-0.5 select-none
        ${disabled ? "opacity-40" : ""}`}
    >
      {/* Sliding active pill — the accent fill glides to the selected cell. It is
          purely decorative (aria-hidden); the aria-checked state lives on the
          buttons. Hidden entirely when nothing is selected. */}
      {activeIndex >= 0 && (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-y-0.5 left-0.5 z-0 bg-accent
            transition-transform duration-200 ease-out motion-reduce:transition-none"
          style={{
            width: `calc((100% - 0.25rem) / ${n})`,
            transform: `translateX(${Math.max(0, activeIndex) * 100}%)`,
          }}
        />
      )}
      {options.map((opt, i) => {
        const selected = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={i === tabIndex ? 0 : -1}
            disabled={disabled}
            onClick={() => select(i)}
            onKeyDown={(e: RKeyboardEvent<HTMLButtonElement>) => onKeyDown(e, i)}
            onPointerDown={(e: RPointerEvent<HTMLButtonElement>) => {
              // keep focus visually on the pressed segment without scrolling
              if (!disabled) (e.currentTarget as HTMLElement).focus?.();
            }}
            className={`relative z-10 min-h-[44px] px-3 inline-flex items-center justify-center
              text-xs font-semibold uppercase tracking-wider whitespace-nowrap
              transition-colors duration-150
              focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent
              ${selected ? "text-accent-ink" : "text-dim hover:text-ink"}
              ${disabled ? "cursor-not-allowed" : "cursor-pointer"}`}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

export default SegmentedControl;
