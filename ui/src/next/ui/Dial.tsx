import { useRef, type JSX, type PointerEvent as RPointerEvent, type KeyboardEvent as RKeyboardEvent } from "react";
import { lockedAttrs, lockedClass } from "./honest";

export interface DialOption<T> {
  value: T;
  label: string;
}

/** The one control every device sheet shares: a horizontal strip of stops that
 *  scrubs under a fixed centre marker (README section 8, "one dial (drag or
 *  tap, 64 px per stop, centre marker) that edits the selected readout").
 *
 *  ARIA: a slider, not a listbox. `aria-valuetext` is what a screen reader
 *  actually reads out ("-10 C"), because `aria-valuenow` here is an INDEX and
 *  announcing "3 of 7" would tell the user nothing about the setting.
 *
 *  The stops are spans, not buttons, on purpose: a `role="slider"` with seven
 *  focusable descendants is seven tab stops for one setting, and the arrow keys
 *  on the slider are already the keyboard interface. They stay tappable.
 *
 *  The 160 ms ease-out snap lives in `next.css` and is dropped entirely under
 *  `prefers-reduced-motion`. */
export function Dial<T>({ label, hint, options, value, onChange, stopPx = 64, lockedReason = null, onExplain, className = "", ...rest }: {
  label: string;
  hint?: string;
  options: DialOption<T>[];
  value: T;
  onChange: (next: T) => void;
  stopPx?: number;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  const at = Math.max(0, options.findIndex((o) => o.value === value));
  const drag = useRef<{ x: number; from: number; moved: boolean } | null>(null);

  const commit = (idx: number) => {
    const i = Math.min(options.length - 1, Math.max(0, idx));
    const next = options[i];
    if (!next || next.value === value) return;
    onChange(next.value);
  };

  const blocked = (): boolean => {
    if (!lockedReason) return false;
    onExplain?.(lockedReason);
    return true;
  };

  const onPointerDown = (e: RPointerEvent<HTMLDivElement>) => {
    if (blocked()) return;
    drag.current = { x: e.clientX, from: at, moved: false };
    // Pointer capture keeps the scrub alive when the finger leaves the 56 px
    // track - without it a drag dies the moment it strays vertically, which on
    // a phone is most of them. jsdom has no implementation; guarded, not assumed.
    const el = e.currentTarget as unknown as { setPointerCapture?: (id: number) => void };
    try { el.setPointerCapture?.(e.pointerId); } catch { /* not supported here */ }
  };

  const onPointerMove = (e: RPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    if (Math.abs(dx) > 3) d.moved = true;
    commit(d.from - Math.round(dx / stopPx));
  };

  const endDrag = (e: RPointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = null;
    const el = e.currentTarget as unknown as { releasePointerCapture?: (id: number) => void };
    try { el.releasePointerCapture?.(e.pointerId); } catch { /* not supported here */ }
  };

  const onKeyDown = (e: RKeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowUp" ? 1
      : e.key === "ArrowLeft" || e.key === "ArrowDown" ? -1
      : e.key === "Home" ? -options.length
      : e.key === "End" ? options.length : 0;
    if (step === 0) return;
    e.preventDefault();
    if (blocked()) return;
    commit(at + step);
  };

  const selected = options[at];

  return (
    <div
      className={lockedClass(lockedReason, `nx-dial ${className}`.trim())}
      data-testid={rest["data-testid"]}
    >
      <div className="nx-dial-head">
        <span className="nx-dial-name">{label}</span>
        <span className="nx-dial-hint">{hint ?? "drag or tap"}</span>
      </div>
      <div
        className="nx-dial-track"
        role="slider"
        tabIndex={0}
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={Math.max(0, options.length - 1)}
        aria-valuenow={at}
        aria-valuetext={selected ? selected.label : ""}
        aria-orientation="horizontal"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onKeyDown={onKeyDown}
        {...lockedAttrs(lockedReason)}
      >
        <div className="nx-dial-marker" aria-hidden="true" />
        <div
          className="nx-dial-strip"
          style={{ transform: `translateX(${-(at * stopPx + stopPx / 2)}px)` }}
        >
          {options.map((o, i) => (
            <span
              key={`${String(o.value)}-${i}`}
              className="nx-dial-stop"
              data-selected={i === at ? "true" : "false"}
              data-value={String(o.value)}
              style={{ width: `${stopPx}px` }}
              onClick={() => { if (!blocked()) commit(i); }}
            >
              <span className="nx-dial-tick" aria-hidden="true" />
              <span className="nx-dial-stop-label">{o.label}</span>
            </span>
          ))}
        </div>
        <span className="nx-dial-fade" data-side="left" aria-hidden="true" />
        <span className="nx-dial-fade" data-side="right" aria-hidden="true" />
      </div>
    </div>
  );
}
