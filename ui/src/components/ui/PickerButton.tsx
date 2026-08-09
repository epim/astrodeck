// PickerButton.tsx — one control that opens a list, instead of a row of
// controls that is always open.
//
// Three QA complaints from the first night were the same complaint: the
// annotation toggles, the capture presets and the filter-wheel slots each
// rendered EVERY option as a permanent 44px button. That is fine on a desktop
// and ruinous in a 300px rail — six presets and six overlay switches push the
// thing you actually came for off the bottom of the screen.
//
// So: collapse each set to one button that states the current selection, and
// open the options on demand. The button is the readout AND the affordance, so
// nothing is lost from a glance; only the permanent real estate goes.
//
// Single-select closes on choice (you picked, you are done). Multi-select stays
// open, because turning on stars AND clip is one intent, not two visits.
import { useCallback, useEffect, useLayoutEffect, useRef, useState,
         type CSSProperties, type JSX, type ReactNode } from "react";

export interface PickerOption {
  id: string;
  label: string;
  /** The long-form explanation. Becomes the row's title, so the help that used
   *  to live in a permanent legend still has a home. */
  hint?: string;
  disabled?: boolean;
  /** Why it is unavailable. Shown instead of `hint` when disabled — a greyed row
   *  with no reason is the thing house rule §11.8 forbids. */
  disabledReason?: string;
}

export default function PickerButton({
  label,
  summary,
  options,
  selected,
  onPick,
  multi = false,
  disabled = false,
  disabledReason,
  onBlocked,
  className = "",
  align = "left",
  columns = 1,
  children,
}: {
  /** What the set IS ("Annotations", "Presets", "Filter"). */
  label: string;
  /** What is currently chosen — the readout half of the button. */
  summary: string;
  options: PickerOption[];
  /** Selected ids. One entry for single-select. */
  selected: readonly string[];
  onPick: (id: string) => void;
  multi?: boolean;
  disabled?: boolean;
  disabledReason?: string | null;
  onBlocked?: (reason: string) => void;
  className?: string;
  align?: "left" | "right";
  /** Lay the options out in N columns. Numeric sets (13 exposures, 9 gains)
   *  read better as a grid than as a column you scroll — one glance, no
   *  scrolling, and the tap targets stay 44px. Default 1 = the list. */
  columns?: number;
  /** Extra content inside the open panel, below the options. */
  children?: ReactNode;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [nudge, setNudge] = useState(0);
  const close = useCallback(() => setOpen(false), []);

  /* KEEP THE PANEL ON SCREEN. Measured on a real phone (S25 Ultra,
     2026-08-07 20:53): the Align bar's right-aligned filter picker sits near
     the left edge, so `right-0` on a 190px panel hung it off the LEFT of the
     viewport — the operator saw an empty black rectangle, because every
     option's text was outside the screen. Neither `left-0` nor `right-0` is
     right on its own; what is right is "whatever keeps it inside the
     window". Measure after paint, then translate the minimum amount.

     useLayoutEffect so the correction lands in the SAME frame the panel
     appears — a visible jump would be its own defect. */
  useLayoutEffect(() => {
    if (!open) { setNudge(0); return; }
    const el = panel.current;
    if (!el) return;
    setNudge(0);
    const r = el.getBoundingClientRect();
    const margin = 8;
    const overRight = r.right - (window.innerWidth - margin);
    const overLeft = margin - r.left;
    if (overRight > 0) setNudge(-overRight);
    else if (overLeft > 0) setNudge(overLeft);
  }, [open]);

  // Escape and outside-press both dismiss. A picker you cannot get out of
  // without choosing is worse than the row of buttons it replaced.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    const onDown = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) close();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open, close]);

  return (
    <div className="relative" ref={wrap}>
      <button
        type="button"
        /* Stable hooks for the cross-surface agreement test
           (src/__tests__/cameraSettingsAgreement.test.tsx): it must read what a
           screen DISPLAYS, off the DOM, because a test that reads React state
           cannot catch a screen that renders something else. Matching on the
           face's text would rot on the first copy change. */
        data-picker={label}
        className={`btn tap min-h-[44px] !px-3 ${open ? "border-accent text-accent" : ""} ${
          disabled ? "opacity-40" : ""
        } ${className}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-disabled={disabled || undefined}
        aria-label={`${label} — ${summary}`}
        title={disabled ? (disabledReason ?? undefined) : `${label}: ${summary}`}
        onClick={() => {
          if (disabled) {
            if (disabledReason && onBlocked) onBlocked(disabledReason);
            return;
          }
          setOpen((v) => !v);
        }}
      >
        <span className="label !text-[9px] mr-1.5">{label}</span>
        <span className="mono text-xs !normal-case" data-picker-summary>{summary}</span>
      </button>
      {open && (
        <div
          ref={panel}
          role="listbox"
          aria-multiselectable={multi || undefined}
          aria-label={label}
          className={`absolute z-40 mt-1.5 max-h-[60vh] overflow-y-auto rounded-lg
                      border border-line2 bg-raise shadow-2xl p-1.5
                      ${columns > 1 ? "grid gap-1" : "min-w-[190px] flex flex-col gap-0.5"}
                      ${align === "right" ? "right-0" : "left-0"}`}
          style={{
            transform: nudge ? `translateX(${nudge}px)` : undefined,
            ...(columns > 1
              ? { gridTemplateColumns: `repeat(${columns}, minmax(64px, 1fr))` }
              : {}) as CSSProperties,
          }}
        >
          {options.map((o) => {
            const on = selected.includes(o.id);
            return (
              <button
                key={o.id}
                type="button"
                role="option"
                aria-selected={on}
                aria-disabled={o.disabled || undefined}
                title={o.disabled ? (o.disabledReason ?? o.hint) : o.hint}
                className={`min-h-[44px] rounded border text-xs mono tabular-nums
                            ${columns > 1
                              ? "flex items-center justify-center px-2"
                              : "text-left px-2 py-2 flex items-center gap-2"}
                            ${on
                              ? "border-accent text-accent bg-accent/10"
                              : "border-line text-dim hover:text-ink hover:border-line2"}
                            ${o.disabled ? "opacity-40" : ""}`}
                onClick={() => {
                  if (o.disabled) {
                    if (o.disabledReason && onBlocked) onBlocked(o.disabledReason);
                    return;
                  }
                  onPick(o.id);
                  if (!multi) close();
                }}
              >
                {/* In LIST mode the dot is the selection channel (night mode
                    kills hue); in GRID mode the filled border+tint carries it,
                    and a dot beside a centred number reads as noise. */}
                {columns === 1 && (
                  <span className="mono w-3 shrink-0" aria-hidden>{on ? "•" : " "}</span>
                )}
                <span className={columns > 1 ? "" : "flex-1 truncate"}>{o.label}</span>
              </button>
            );
          })}
          {children}
        </div>
      )}
    </div>
  );
}
