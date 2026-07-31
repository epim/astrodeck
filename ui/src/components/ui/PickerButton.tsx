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
import { useCallback, useEffect, useRef, useState, type JSX, type ReactNode } from "react";

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
  /** Extra content inside the open panel, below the options. */
  children?: ReactNode;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);

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
        <span className="mono text-xs !normal-case">{summary}</span>
      </button>
      {open && (
        <div
          role="listbox"
          aria-multiselectable={multi || undefined}
          aria-label={label}
          className={`absolute z-40 mt-1 min-w-[190px] max-h-[60vh] overflow-y-auto
                      border border-line2 bg-raise p-1 flex flex-col gap-0.5
                      ${align === "right" ? "right-0" : "left-0"}`}
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
                className={`text-left px-2 py-2 min-h-[40px] text-xs flex items-center gap-2
                            ${on ? "text-accent" : "text-dim"} ${o.disabled ? "opacity-40" : ""}`}
                onClick={() => {
                  if (o.disabled) {
                    if (o.disabledReason && onBlocked) onBlocked(o.disabledReason);
                    return;
                  }
                  onPick(o.id);
                  if (!multi) close();
                }}
              >
                {/* A glyph, not colour alone — night mode kills hue as a channel. */}
                <span className="mono w-3 shrink-0" aria-hidden>{on ? "•" : " "}</span>
                <span className="flex-1 truncate">{o.label}</span>
              </button>
            );
          })}
          {children}
        </div>
      )}
    </div>
  );
}
