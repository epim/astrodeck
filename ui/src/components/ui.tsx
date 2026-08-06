import {
  useEffect, useId, useLayoutEffect, useReducer, useRef, useState,
  type ReactNode, type PointerEvent as RPointerEvent,
  type KeyboardEvent as RKeyboardEvent,
} from "react";
import { createPortal } from "react-dom";
import type { LedState, Tone } from "../types";
import { Icon, type IconName } from "./icons";
import {
  tooltipNext, TOOLTIP_IDLE, TOOLTIP_OPEN_DELAY_MS, TOOLTIP_CLOSE_GRACE_MS,
} from "../lib/tooltipMachine";
import { placeTooltip, type Placement } from "../lib/tooltipPlace";

// Re-export the segmented tri-state control (mount tracking-rate spec, Task 5) so
// it joins the rest of the UI primitives at `../components/ui`.
export { SegmentedControl, type SegmentedControlProps } from "./ui/SegmentedControl";

// THE overlay primitive. Every dialog / sheet / drawer in the app goes through
// this — do not hand-roll another `fixed inset-0` panel. Review S1 found five
// separate findings that were all one bug (an overlay rendered inside a view
// tree lands wherever an ancestor's filter / backdrop-filter / residual
// animation transform says it does, not where the eye is), and the fix is
// structural: Overlay portals to a body-level host that IS the viewport.
// See components/Overlay.tsx for the measurements and the full contract.
export { Overlay, useMediaQuery, useIsLg, type OverlayProps, type OverlayVariant } from "./Overlay";

export function Panel({ title, right, children, className = "" }: {
  title?: string; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`panel p-4 ${className}`}>
      {(title || right) && (
        <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 mb-3">
          {title && <h2 className="panel-title min-w-0">{title}</h2>}
          {right && <div className="min-w-0 shrink">{right}</div>}
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

// `label` is REQUIRED, not optional (root-cause fix for W-01/R2-CAP-02): the
// control is a <button role="switch">, and wrapping it in a visual <label>
// element (the common call-site idiom for the adjacent text) does NOT give a
// button an accessible name in the accname algorithm the way it does for a
// bare <input> — only aria-label/aria-labelledby do. Making the prop
// mandatory turns "forgot the a11y name" into a compile error instead of a
// silent gap, so every switch in the app carries one.
export function Toggle({ checked, onChange, disabled = false, label, showState = false }: {
  checked: boolean; onChange: (v: boolean) => void; disabled?: boolean;
  label: string; showState?: boolean;
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
   - Persistent "HOLD TO …" affordance via render-prop bind.hintLabel; while the
     keyboard two-step is armed the SAME slot switches to "PRESS AGAIN TO …",
     because at that point "HOLD TO ABORT" is instructing the user to do the one
     thing the keyboard path ignores (`e.repeat`).
   - navigator.vibrate(30) on fire.
   - Honest keyboard two-step: Enter/Space arms (hintLabel -> "PRESS AGAIN TO …",
     aria-live assertive); second press within 3s confirms; Escape disarms.
   - A hold must be WITNESSED, not inferred from two clock readings: unwatched
     time buys no progress (HOLD_MAX_FRAME_CREDIT_MS) and the tab going away
     cancels the hold outright. Nothing here fires without a finger on glass. */

export type HoldBind = {
  onPointerDown: (e: RPointerEvent) => void;
  onPointerUp: (e: RPointerEvent) => void;
  onKeyDown: (e: RKeyboardEvent) => void;
  onKeyUp: (e: RKeyboardEvent) => void;
  progress: number;   // 0..1 fill
  armed: boolean;     // pointer-holding OR keyboard-armed
  hintLabel: string;  // "HOLD TO …", or "PRESS AGAIN TO …" while kb-armed
  "aria-label": string;
};

/** Most credit one animation frame can add to a hold. The progress used to be
 *  `now - start`, which measures the CLOCK, not a finger: rAF does not run at
 *  all while the tab is hidden, and a GC pause or a heavy re-render can starve
 *  it for the better part of a second — after which the first frame back sees
 *  `now - start >= holdMs` and fires a confirm nobody held through. Capping the
 *  per-frame credit means unwatched time buys nothing, while a stall on a slow
 *  tablet only makes the hold take a little longer (it must never CANCEL a
 *  legitimate hold either — that would be its own broken promise). ~4 frames at
 *  60Hz, so ordinary jank costs nothing. */
const HOLD_MAX_FRAME_CREDIT_MS = 70;

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
  /** ms of hold we have actually WATCHED elapse (see HOLD_MAX_FRAME_CREDIT_MS). */
  const held = useRef(0);
  const lastTick = useRef(0);
  const fired = useRef(false);
  const kbTimer = useRef<number | null>(null);

  // The persistent affordance has to describe the gesture the control is
  // actually waiting for. Armed by keyboard it is waiting for a SECOND PRESS —
  // telling that user to "HOLD TO ABORT" sends them into the one input the
  // handler drops (`e.repeat`), after which the caption silently reverts.
  const hintLabel = kbArmed
    ? `PRESS AGAIN TO ${label.toUpperCase()}`
    : `HOLD TO ${label.toUpperCase()}`;

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
    const now = performance.now();
    held.current += Math.min(now - lastTick.current, HOLD_MAX_FRAME_CREDIT_MS);
    lastTick.current = now;
    const p = Math.min(1, held.current / holdMs);
    setProgress(p);
    if (p >= 1) { stopRaf(); setHolding(false); fire(); setProgress(0); return; }
    raf.current = requestAnimationFrame(tick);
  };

  const beginHold = () => {
    if (disabled) return;
    fired.current = false;
    held.current = 0;
    lastTick.current = performance.now();
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

  // The hold is bound to a finger that is on the glass NOW. If the tab is
  // backgrounded, the app switched away from, or the OS steals focus mid-press,
  // no pointerup or pointercancel is guaranteed to arrive — and the hold would
  // otherwise sit there and complete itself on the next frame the browser
  // schedules, firing Abort / "Connect this rig" / a permanent purge with the
  // screen not even visible. Same guard SlewPad.tsx:173-188 puts on a held slew,
  // for the same reason. A keyboard arming is dropped too: a two-step confirm
  // whose first step happened in another context is not a confirmation.
  useEffect(() => {
    const cancel = () => {
      if (raf.current != null) { cancelAnimationFrame(raf.current); raf.current = null; }
      setHolding(false);
      setProgress(0);
      setKbArmed(false);
      if (kbTimer.current != null) { clearTimeout(kbTimer.current); kbTimer.current = null; }
    };
    const onVis = () => { if (document.visibilityState === "hidden") cancel(); };
    window.addEventListener("blur", cancel);
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.removeEventListener("blur", cancel);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);

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
   Mouse hover runs through hover-intent grace timers (lib/tooltipMachine.ts);
   touch/pen taps toggle exactly once; keyboard focus opens; Escape/outside-tap
   closes. role=tooltip, pointer-events-none bubble, >=44px focusable hit area
   on the trigger.

   The bubble is PORTALED to document.body as a position:fixed node (not an
   in-flow child): rendering it inside the trigger — the old `.panel absolute`
   approach — put it IN FLOW (unlayered `.panel { position: relative }` beats
   the layered Tailwind `absolute` utility) and trapped `fixed` descendants
   against the panel's backdrop-filter, so it could neither overlay nor clamp.
   Placement is pure (lib/tooltipPlace.ts): we measure the bubble hidden via
   useLayoutEffect, then clamp it to the viewport, recomputing on resize/scroll
   while open so it stays glued to the trigger. The outside-pointerdown check
   still uses the trigger ref — the bubble is pointer-events-none and can never
   be an event target, so it never registers as "inside". */
export function Tooltip({ content, children, side = "top", label, triggerClassName = "", ariaDisabled }: {
  content: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right";
  /** Accessible name for the TRIGGER (UX review #25/#26). The trigger is the
   *  `role="button"` span below — an `aria-label` on the children lands on a
   *  descendant, which is not what an a11y audit (or a screen reader's control
   *  list) reads. Optional so existing call sites whose children carry their own
   *  text keep compiling and keep their name-from-content. */
  label?: string;
  /** Extra classes for the TRIGGER, so a caller can grow the hit area to the
   *  44px floor on the element that is actually interactive. */
  triggerClassName?: string;
  /** `aria-disabled` on the TRIGGER (never the native attribute — house rule
   *  §11.8). Used by LockedChip, whose whole job is a read-only control that
   *  still announces itself and its reason. */
  ariaDisabled?: boolean;
}) {
  const [st, dispatch] = useReducer(tooltipNext, TOOLTIP_IDLE);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);
  const bubbleRef = useRef<HTMLSpanElement>(null);
  const [placement, setPlacement] = useState<Placement | null>(null);
  // Suppress the focus-opens path when focus was pointer-induced (a touch tap
  // focuses the span THEN fires pointerup — without this, tap = open+toggle).
  const pointerDownAtRef = useRef(0);

  // Grace timers: the machine sets pending flags; these effects fire the expiry
  // events. State changes re-run the effect, cancelling stale timers.
  useEffect(() => {
    if (!st.pendingOpen) return;
    const t = window.setTimeout(() => dispatch("open-timer"), TOOLTIP_OPEN_DELAY_MS);
    return () => window.clearTimeout(t);
  }, [st.pendingOpen]);
  useEffect(() => {
    if (!st.pendingClose) return;
    const t = window.setTimeout(() => dispatch("close-timer"), TOOLTIP_CLOSE_GRACE_MS);
    return () => window.clearTimeout(t);
  }, [st.pendingClose]);

  useEffect(() => {
    if (!st.open) return;
    const onDoc = (e: Event) => { if (ref.current && !ref.current.contains(e.target as Node)) dispatch("outside"); };
    // preventDefault MARKS THE PRESS AS SPENT. Escape has no default action of
    // its own, so this costs nothing here and it is the only signal an outer
    // dismissable has that the key was already used: a locked chip inside the
    // Focus pod's arc opens this bubble to say WHY it is blocked, and one
    // Escape was closing the answer and the arc together (FocusPod's own
    // window-level handler now skips a consumed event).
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      dispatch("escape");
    };
    document.addEventListener("pointerdown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [st.open]);

  // Measure the (already-mounted, hidden) bubble and clamp it to the viewport
  // before paint; keep it glued to the trigger on resize/scroll while open.
  // useLayoutEffect runs after the bubble commits, so bubbleRef is populated.
  useLayoutEffect(() => {
    if (!st.open) { setPlacement(null); return; }
    const measure = () => {
      const trig = ref.current;
      const bub = bubbleRef.current;
      if (!trig || !bub) return;
      const r = trig.getBoundingClientRect();
      setPlacement(placeTooltip({
        trigger: { left: r.left, top: r.top, width: r.width, height: r.height },
        bubble: { width: bub.offsetWidth, height: bub.offsetHeight },
        viewport: { width: window.innerWidth, height: window.innerHeight },
        side,
      }));
    };
    measure();
    const scrollOpts = { capture: true, passive: true } as const;
    window.addEventListener("resize", measure, { passive: true });
    window.addEventListener("scroll", measure, scrollOpts);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, scrollOpts);
    };
  }, [st.open, side]);

  return (
    <span ref={ref} className="relative inline-flex min-w-0">
      <span
        tabIndex={0}
        role="button"
        aria-label={label}
        aria-disabled={ariaDisabled || undefined}
        aria-describedby={st.open ? id : undefined}
        aria-expanded={st.open}
        className={`inline-flex items-center cursor-help min-w-0 ${triggerClassName}`}
        onPointerEnter={(e: RPointerEvent<HTMLSpanElement>) => { if (e.pointerType === "mouse") dispatch("enter-mouse"); }}
        onPointerLeave={(e: RPointerEvent<HTMLSpanElement>) => { if (e.pointerType === "mouse") dispatch("leave-mouse"); }}
        onPointerDown={() => { pointerDownAtRef.current = Date.now(); }}
        onPointerUp={(e: RPointerEvent<HTMLSpanElement>) => { if (e.pointerType !== "mouse") dispatch("tap"); }}
        onFocus={() => { if (Date.now() - pointerDownAtRef.current > 400) dispatch("focus"); }}
        onBlur={() => dispatch("blur")}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </span>
      {st.open && createPortal(
        <span
          ref={bubbleRef}
          id={id}
          role="tooltip"
          className="tooltip-bubble fixed z-50 px-2.5 py-2 text-[11px] leading-snug text-ink
            w-max max-w-[min(240px,90vw)] pointer-events-none"
          style={placement
            ? { left: placement.left, top: placement.top }
            : { left: 0, top: 0, visibility: "hidden" }}
        >
          {content}
        </span>,
        document.body,
      )}
    </span>
  );
}

/* ============================================================ UI-EMPTY (InfoDot)
   14px info Icon inside a Tooltip. Drop next to jargon labels.

   UX review #25/#26: the twelve (i)s on Plan measured 9-14px with no accessible
   name, and the tablet audit still counted them as `unnamed` after the label and
   the 15px padding were added HERE — because neither was on the element the
   audit (and the a11y tree, and the finger) actually sees. The interactive
   element is Tooltip's own `role="button" tabIndex=0` trigger span; the label sat
   on a DESCENDANT of it, and the negative margin collapsed the trigger's box back
   to the icon's 14px. Both now ride on the trigger via Tooltip's `label` /
   `triggerClassName`, so every call site is fixed at once and none of them
   change: `label` keeps its default, so no call site is required to pass one.

   The 44px hit area is bought with `-m-[15px] p-[15px]` (14 + 2x15 = 44) — the
   negative margin means the trigger's LAYOUT footprint is still 14px, so no row
   reflows; `shrink-0` stops a crowded flex row squeezing it back under 44. */
export function InfoDot({ label = "More information", content }: { label?: string; content: ReactNode }) {
  return (
    <Tooltip
      content={content}
      label={label}
      triggerClassName="justify-center shrink-0 text-dim hover:text-accent -m-[15px] p-[15px]"
    >
      <Icon name="info" size={14} />
    </Tooltip>
  );
}

/* ============================================================ UI-LOCKED
   ONE read-only presentation for the whole app. Before this, five files dimmed
   read-only surfaces at four different opacities (0.35/0.4/0.5) and put the
   reason only in `title=` — which never fires on touch and is not reachable by
   keyboard, so on a tablet (the primary field device) a locked control was
   simply dead with no stated cause.

   `LOCKED_CLASS` is the single dimming token. `lockedProps(reason)` spreads
   onto a read-only CONTAINER; `LockedNote` renders the reason as VISIBLE text
   beside a lock glyph; `LockedChip` is the focusable inline variant that
   replaces locked `<span>`s (tabIndex={0}, so keyboard users land on it and
   hear the reason instead of tabbing straight past). */
export const LOCKED_CLASS = "opacity-50 pointer-events-none select-none";

/** Spread onto a container that is read-only for `reason`. Returns empty
 *  props (no dimming, no aria) when `reason` is falsy, so call sites read
 *  `<div {...lockedProps(canEdit ? null : accessPhrase(cap))}>` with no
 *  ternary around the className. `aria-disabled` — never the native
 *  `disabled` attribute, which strips the element from the a11y tree along
 *  with the reason we are trying to convey (house rule §11.8). */
export function lockedProps(reason?: string | null): {
  className?: string; "aria-disabled"?: true; "data-locked"?: "true";
} {
  if (!reason) return {};
  return { className: LOCKED_CLASS, "aria-disabled": true, "data-locked": "true" };
}

/** Visible lock glyph + reason. Pair with `lockedProps` on the container —
 *  the container carries the dimming, this carries the explanation. Rendered
 *  OUTSIDE the dimmed element so the reason stays at full contrast. */
export function LockedNote({ reason, className = "" }: {
  reason: string; className?: string;
}) {
  return (
    <p className={`flex items-center gap-1.5 text-[11px] text-dim ${className}`}>
      <Icon name="lock" size={12} aria-hidden />
      <span>{reason}</span>
    </p>
  );
}

/** Inline read-only stand-in for a control the user cannot operate. Focusable
 *  on purpose: `aria-disabled` keeps it in the tab order (unlike `disabled`),
 *  and the reason rides in `aria-label` AND in a tooltip, so it is reachable
 *  by keyboard, by screen reader, and by tap.
 *
 *  The name and the 44px floor go on Tooltip's TRIGGER, not on an inner span:
 *  the trigger is already `role="button" tabIndex=0`, so a second focusable
 *  inside it was a duplicate tab stop whose outer half was unnamed (UX #26). */
export function LockedChip({ reason, children, className = "" }: {
  reason: string; children: ReactNode; className?: string;
}) {
  return (
    <Tooltip
      content={reason}
      label={`Unavailable — ${reason}`}
      ariaDisabled
      triggerClassName={`gap-1.5 tap min-h-[44px] ${LOCKED_CLASS}
        !pointer-events-auto cursor-default ${className}`}
    >
      <Icon name="lock" size={12} aria-hidden />
      {children}
    </Tooltip>
  );
}

/** A BUTTON that is honest about being inert (house rule §11.8) — the third
 *  member of the locked family, next to `lockedProps` (containers) and
 *  `LockedChip` (inline read-only stand-ins). Reach for this one when the
 *  surface must stay a real, pressable button: a primary action that is
 *  temporarily blocked (Start guiding, Save, Apply, Calibrate).
 *
 *  The native `disabled` attribute is never used for a control the user could
 *  plausibly want to press: it removes the element from the accessibility tree,
 *  taking the reason with it, and leaves a grey rectangle that does nothing on
 *  tap and cannot even be focused to ask why. Instead this dims (the one shared
 *  LOCKED_CLASS token), carries `aria-disabled`, stays focusable AND tappable
 *  (`!pointer-events-auto`, the same escape LockedChip uses), and its press
 *  STATES the reason instead of acting. Callers pair it with a visible reason
 *  line so the reason also reaches a sighted user who never presses it.
 *
 *  `onExplain` is REQUIRED on purpose. An "honest" button whose blocked press
 *  did nothing would be exactly the defect this primitive exists to remove, so
 *  the type system makes the caller nominate a channel (a toast, an inline
 *  note, a focus move) rather than letting one be forgotten.
 *
 *  It lived privately inside GuideView until it was being copied by hand into
 *  other views — which is how an idiom drifts into four slightly different
 *  opacities again. Same props, same rendered element; hoisting it is a move,
 *  not a redesign. */
export function HonestButton({ reason, onClick, onExplain, className = "btn", children }: {
  /** null / "" => the control is live. A sentence => it is blocked, for THIS. */
  reason: string | null;
  onClick: () => void;
  /** How the reason reaches the user when a blocked button is pressed. */
  onExplain: (reason: string) => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`${className} ${reason ? `${LOCKED_CLASS} !pointer-events-auto` : ""}`}
      aria-disabled={reason ? true : undefined}
      onClick={() => (reason ? onExplain(reason) : onClick())}
    >
      {children}
    </button>
  );
}

/* ============================================================ UI-DISCLOSURE
   The house progressive-disclosure row, extracted. Fourteen call sites hand-rolled
   this same shape (aria-expanded button, >=44px tap target, ▸/▾ caret); two later
   ones reached for native <details>/<summary> instead, which renders a different
   caret, ignores the 44px floor, and cannot be driven open from outside. This is
   the hand-rolled shape as one component so new disclosures have somewhere to go.

   `summary` is what the collapsed row shows; `label` is the accessible verb
   phrase ("Show raw curves"). Controlled `open`/`onToggle` is optional — omit
   for the common self-managing case. */
export function Disclosure({
  summary, label, glyph, children, className = "", open: openProp, onToggle,
  defaultOpen = false,
}: {
  summary: ReactNode; label: string; glyph?: ReactNode; children: ReactNode;
  className?: string; open?: boolean; onToggle?: (next: boolean) => void;
  defaultOpen?: boolean;
}) {
  const [openState, setOpenState] = useState(defaultOpen);
  const open = openProp ?? openState;
  const toggle = () => {
    const next = !open;
    if (openProp === undefined) setOpenState(next);
    onToggle?.(next);
  };
  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={open}
        aria-label={`${open ? "Hide" : "Show"} ${label}`}
        onClick={toggle}
        className="w-full tap min-h-[44px] flex items-center gap-2 px-1 text-left
          text-[11px] text-dim hover:text-accent transition-colors cursor-pointer"
      >
        {glyph && <span aria-hidden className="text-accent">{glyph}</span>}
        <span className="min-w-0 truncate">{summary}</span>
        <span className="flex-1" />
        <span aria-hidden className="text-sm leading-none">{open ? "▾" : "▸"}</span>
      </button>
      {open && <div className="mt-1 px-1 pb-1">{children}</div>}
    </div>
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
