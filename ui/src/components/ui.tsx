import {
  useEffect, useId, useRef, useState, type ReactNode, type PointerEvent as RPointerEvent,
  type KeyboardEvent as RKeyboardEvent,
} from "react";
import type { LedState, Tone } from "../types";
import { Icon, type IconName } from "./icons";

export function Panel({ title, right, children, className = "" }: {
  title?: string; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`panel p-4 ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between mb-3">
          {title && <h2 className="panel-title">{title}</h2>}
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

/* ============================================================ UI-LED
   State encoded by SHAPE + (optional) letter, not color alone. Accepts the new
   strict `state` prop AND the legacy boolean `on`/`warn` props so existing call
   sites (`<Led on />`, `<Led on warn={p} />`) keep compiling unchanged. When
   `state` is supplied it wins; otherwise the booleans derive a LedState. */
export function Led(props:
  | { state: LedState; label?: string; on?: never; warn?: never }
  | { state?: undefined; on: boolean; warn?: boolean; label?: string }
) {
  const state: LedState =
    props.state ?? (props.on ? (props.warn ? "warn" : "on") : "off");
  return (
    <span
      className={`led led-${state}`}
      role={props.label ? "img" : undefined}
      aria-label={props.label}
      aria-hidden={props.label ? undefined : true}
    />
  );
}

export function Field({ label, hint, children }: {
  label: string; hint?: ReactNode; children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1 min-w-0">
      <span className="label inline-flex items-center gap-1">
        {label}
        {hint != null && <InfoDot content={hint} label={`About ${label}`} />}
      </span>
      {children}
    </label>
  );
}

/* ============================================================ UI-STAT
   Status by SHAPE + text, never color alone (night palette collapses good/warn/bad
   toward coral — spec §8 C3-A4). The leading glyph slot is non-flowing so the value
   never loses truncation budget. `hint` wraps the value in a Tooltip.

   `glyph` channel:
     - `true`  (default): auto tone glyph for warn/bad (alert/x icon).
     - `false`: opt out (hero readouts).
     - a ReactNode: a CUSTOM leading glyph (e.g. ↑/↓/☾ for the Atlas visibility
       chips `↑68°`, `☾ 71°`) — rendered regardless of tone, so a status reads in
       night mode by its glyph + text even where no tone color survives.
   null/loading value renders an em-dash in --text-faint, never throws. */
export function Stat({ label, value, unit, tone, hint, glyph = true }: {
  label: string; value: string | number | null | undefined; unit?: string;
  tone?: Tone; hint?: ReactNode; glyph?: boolean | ReactNode;
}) {
  const empty = value == null || value === "";
  const color = empty ? "text-faint"
    : tone === "good" ? "text-good" : tone === "warn" ? "text-warn"
    : tone === "bad" ? "text-bad" : "text-ink";

  // A custom glyph node (anything that isn't a boolean) renders verbatim; the
  // boolean form keeps the legacy auto-tone-icon behavior (warn/bad only).
  const customGlyph = typeof glyph !== "boolean";
  const autoGlyph = glyph === true && (tone === "warn" || tone === "bad");
  const showGlyph = !empty && (customGlyph || autoGlyph);
  const glyphName: IconName = tone === "bad" ? "x" : "alert";

  const valueEl = (
    <span className={`mono text-sm ${color} truncate`}>
      {empty ? "—" : value}
      {!empty && unit && <span className="text-dim text-xs ml-1">{unit}</span>}
    </span>
  );

  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <span className="label inline-flex items-center gap-1">
        {label}
        {hint != null && <InfoDot content={hint} label={`About ${label}`} />}
      </span>
      <span className="inline-flex items-center gap-1 min-w-0">
        {showGlyph && (
          <span
            className={`shrink-0 ${tone === "bad" ? "text-bad" : tone === "warn" ? "text-warn" : tone === "good" ? "text-good" : "text-dim"}`}
            aria-hidden
          >
            {customGlyph ? (glyph as ReactNode) : <Icon name={glyphName} size={12} />}
          </span>
        )}
        {hint != null && !empty ? <Tooltip content={hint}>{valueEl}</Tooltip> : valueEl}
      </span>
    </div>
  );
}

export function Toggle({ checked, onChange, disabled = false, label, showState = false }: {
  checked: boolean; onChange: (v: boolean) => void; disabled?: boolean;
  label?: string; showState?: boolean;
}) {
  return (
    <span className="inline-flex items-center min-h-11 sm:min-h-0">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative w-9 h-5 border transition-colors shrink-0
          ${checked ? "bg-accent2/40 border-accent" : "bg-raise border-line2"}
          ${disabled ? "opacity-40" : "cursor-pointer"}`}
      >
        <span className={`absolute top-0.5 w-3.5 h-3.5 transition-all
          ${checked ? "left-[18px] bg-accent" : "left-0.5 bg-dim"}`} />
      </button>
      {showState && (
        <span className="label ml-2" aria-hidden>{checked ? "ON" : "OFF"}</span>
      )}
    </span>
  );
}

/* ============================================================ UI-ICONBUTTON
   A square, icon-only button with a guaranteed >=44px hit area (spec §8 / review
   touch P0). Use for per-row affordances (Mount/Plan "Frame", recenter, nudge).
   The icon stays small; the tap target is the whole 44px box. `label` is required
   (aria) since there is no visible text. `tone` themes the chrome via the same
   .btn variants. */
export function IconButton({
  icon, label, onClick, disabled = false, tone, size = 16, active = false,
  className = "", title,
}: {
  icon: IconName; label: string; onClick?: () => void; disabled?: boolean;
  tone?: "accent" | "danger"; size?: number; active?: boolean;
  className?: string; title?: string;
}) {
  const variant = tone === "accent" ? "btn-accent" : tone === "danger" ? "btn-danger" : "";
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active || undefined}
      title={title ?? label}
      disabled={disabled}
      onClick={onClick}
      className={`btn btn-touch inline-flex items-center justify-center p-0 ${variant}
        ${active ? "border-accent text-accent" : ""} ${className}`}
    >
      <Icon name={icon} size={size} />
    </button>
  );
}

/* ============================================================ UI-STEPPER
   Numeric stepper with >=44px +/- bump buttons (spec §6/§8 — mosaic rows/cols,
   overlap, FOV). Clamps to [min,max] and steps by `step`. The center shows the
   current value (mono); `format` overrides the display (e.g. "25%"). Pure
   controlled — parent owns the value. Buttons disable at the bounds so the
   clamp is also a visible affordance. */
export function Stepper({
  value, onChange, min = 0, max = 99, step = 1, label, unit,
  format, disabled = false,
}: {
  value: number; onChange: (v: number) => void;
  min?: number; max?: number; step?: number;
  label?: string; unit?: string;
  format?: (v: number) => string; disabled?: boolean;
}) {
  const clamp = (v: number) => Math.min(max, Math.max(min, v));
  const dec = () => onChange(clamp(value - step));
  const inc = () => onChange(clamp(value + step));
  const display = format ? format(value) : String(value);
  return (
    <div className="inline-flex flex-col gap-1">
      {label && <span className="label">{label}</span>}
      <div className="inline-flex items-stretch" role="group" aria-label={label}>
        <button
          type="button" className="stepper" aria-label={`Decrease ${label ?? "value"}`}
          onClick={dec} disabled={disabled || value <= min}
        >−</button>
        <span
          className="mono text-sm text-ink inline-flex items-center justify-center px-2 min-w-12
            border-y border-line2 bg-bg"
          aria-live="polite"
        >
          {display}{unit && <span className="text-dim text-xs ml-0.5">{unit}</span>}
        </span>
        <button
          type="button" className="stepper" aria-label={`Increase ${label ?? "value"}`}
          onClick={inc} disabled={disabled || value >= max}
        >+</button>
      </div>
    </div>
  );
}

/* ============================================================ UI-CONFIRM (HoldButton)
   Hold-to-confirm for slow/irreversible destructive actions ONLY (Disconnect All,
   Abort, Park-while-imaging, delete target/step). Emergency motion stops
   (STOP/HALT/polar-STOP) stay single-tap — do NOT route them through HoldButton.

   - setPointerCapture on pointerdown so finger drift / glove jitter does not
     cancel; release before 100% cancels.
   - Fill overlay (--danger-ink) is the non-color progress signal.
   - Persistent "HOLD TO …" affordance via render-prop bind.hintLabel.
   - navigator.vibrate(30) on fire.
   - Honest keyboard two-step: Enter/Space arms (label -> "PRESS … AGAIN",
     aria-live assertive); second press within 3s confirms; Escape disarms. */

export type HoldBind = {
  onPointerDown: (e: RPointerEvent) => void;
  onPointerUp: (e: RPointerEvent) => void;
  onKeyDown: (e: RKeyboardEvent) => void;
  onKeyUp: (e: RKeyboardEvent) => void;
  progress: number;   // 0..1 fill
  armed: boolean;     // pointer-holding OR keyboard-armed
  hintLabel: string;  // persistent "HOLD TO …"
  "aria-label": string;
};

export function HoldButton({ onConfirm, label, holdMs = 700, disabled = false, children }: {
  onConfirm: () => void;
  label: string;            // a11y/announce text, e.g. "Abort sequence" (required)
  holdMs?: number;
  disabled?: boolean;
  children: (bind: HoldBind) => ReactNode;
}) {
  const [progress, setProgress] = useState(0);
  const [holding, setHolding] = useState(false);
  const [kbArmed, setKbArmed] = useState(false);
  const raf = useRef<number | null>(null);
  const start = useRef(0);
  const fired = useRef(false);
  const kbTimer = useRef<number | null>(null);

  const hintLabel = `HOLD TO ${label.toUpperCase()}`;

  const fire = () => {
    if (fired.current) return;
    fired.current = true;
    try { navigator.vibrate?.(30); } catch { /* unsupported */ }
    onConfirm();
  };

  const stopRaf = () => {
    if (raf.current != null) { cancelAnimationFrame(raf.current); raf.current = null; }
  };

  const tick = () => {
    const p = Math.min(1, (performance.now() - start.current) / holdMs);
    setProgress(p);
    if (p >= 1) { stopRaf(); setHolding(false); fire(); setProgress(0); return; }
    raf.current = requestAnimationFrame(tick);
  };

  const beginHold = () => {
    if (disabled) return;
    fired.current = false;
    start.current = performance.now();
    setHolding(true);
    setProgress(0);
    stopRaf();
    raf.current = requestAnimationFrame(tick);
  };

  const endHold = () => {
    stopRaf();
    setHolding(false);
    setProgress(0);
  };

  const onPointerDown = (e: RPointerEvent) => {
    if (disabled) return;
    try { (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId); } catch { /* ok */ }
    beginHold();
  };
  const onPointerUp = (e: RPointerEvent) => {
    try { (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId); } catch { /* ok */ }
    if (!fired.current) endHold();
  };

  const disarmKb = () => {
    setKbArmed(false);
    if (kbTimer.current != null) { clearTimeout(kbTimer.current); kbTimer.current = null; }
  };

  const onKeyDown = (e: RKeyboardEvent) => {
    if (disabled) return;
    if (e.key === "Escape") { disarmKb(); return; }
    if (e.key !== "Enter" && e.key !== " " && e.key !== "Spacebar") return;
    e.preventDefault();
    if (e.repeat) return;
    if (!kbArmed) {
      setKbArmed(true);
      kbTimer.current = window.setTimeout(disarmKb, 3000);  // generous 3s window
    } else {
      disarmKb();
      fired.current = false;
      fire();
    }
  };
  const onKeyUp = (e: RKeyboardEvent) => { /* keyboard path is two discrete presses, not a hold */ void e; };

  useEffect(() => () => { stopRaf(); if (kbTimer.current != null) clearTimeout(kbTimer.current); }, []);

  const armed = holding || kbArmed;

  return (
    <>
      {children({
        onPointerDown, onPointerUp, onKeyDown, onKeyUp,
        progress, armed, hintLabel, "aria-label": label,
      })}
      {kbArmed && (
        <span className="sr-only" aria-live="assertive">
          Press Enter again to {label.toLowerCase()}
        </span>
      )}
    </>
  );
}

/* ============================================================ UI-TOOLTIP
   Opens on hover + focus + tap; closes on outside-tap / Escape. Viewport-clamped
   bubble, role=tooltip, >=44px focusable hit area on the trigger. */
export function Tooltip({ content, children, side = "top" }: {
  content: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: Event) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const pos: Record<string, string> = {
    top: "bottom-full left-1/2 -translate-x-1/2 mb-1.5",
    bottom: "top-full left-1/2 -translate-x-1/2 mt-1.5",
    left: "right-full top-1/2 -translate-y-1/2 mr-1.5",
    right: "left-full top-1/2 -translate-y-1/2 ml-1.5",
  };

  return (
    <span ref={ref} className="relative inline-flex">
      <span
        tabIndex={0}
        role="button"
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        className="inline-flex items-center cursor-help"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
      >
        {children}
      </span>
      {open && (
        <span
          id={id}
          role="tooltip"
          className={`panel absolute z-50 ${pos[side]} px-2.5 py-2 text-[11px] leading-snug text-ink
            w-max max-w-[min(240px,90vw)] pointer-events-none`}
        >
          {content}
        </span>
      )}
    </span>
  );
}

/* ============================================================ UI-EMPTY (InfoDot)
   14px info Icon inside a Tooltip; focusable span padded to a >=44px hit area
   (icon stays 14px visually). Drop next to jargon labels. */
export function InfoDot({ label = "More information", content }: { label?: string; content: ReactNode }) {
  return (
    <Tooltip content={content}>
      <span
        aria-label={label}
        className="inline-flex items-center justify-center text-dim hover:text-accent -m-[15px] p-[15px]"
      >
        <Icon name="info" size={14} />
      </span>
    </Tooltip>
  );
}

/* ============================================================ UI-EMPTY (EmptyState)
   Static (no pulse — would imply loading). hero (default) fills a view panel;
   inline is a one-line muted note for tight containers (log drawer, lists). */
export function EmptyState({ icon, title, hint, action, size = "hero" }: {
  icon: IconName; title: string; hint?: string; action?: ReactNode; size?: "hero" | "inline";
}) {
  if (size === "inline") {
    return (
      <div className="empty-state-inline">
        <span className="empty-ghost" aria-hidden><Icon name={icon} size={16} /></span>
        <span>{title}</span>
      </div>
    );
  }
  return (
    <div className="empty-state">
      <span className="empty-ghost" aria-hidden><Icon name={icon} size={56} strokeWidth={1} /></span>
      <div className="panel-title">{title}</div>
      {hint && <p className="text-xs text-dim max-w-[36ch]">{hint}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}
