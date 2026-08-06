// StepDial.tsx — the press-and-hold magnitude dial (design doc §The step-size
// dial). Collapsed it is ONE control showing the current step; press and hold
// and it blooms upward into an arc; slide to a value; release to commit.
//
// It replaces an eight-button grid whose real cost was not its size but its
// PLACEMENT: six targets could not sit beside the preview, so they lived below
// it, so every adjustment was scroll-down / tap / scroll-up / look. One control
// fits in a 260px rail. That is the entire argument.
//
// Three input paths, because this control is used on a phone in the dark AND on
// a laptop:
//   * press-and-hold + slide  — the designed gesture, one continuous motion
//   * plain click/tap         — opens the arc; a second tap on an option commits
//   * keyboard                — arrows walk the values, Escape closes
//
// The geometry and the commit rule live in lib/stepDial.ts and are tested
// there; this file is the surface.
import { useCallback, useEffect, useRef, useState, type JSX } from "react";
import { DEAD_ZONE_PX, OPTION_PITCH_PX, commitValue, optionAt, stepByKey } from "../../lib/stepDial";

export default function StepDial({
  values,
  value,
  onChange,
  ariaLabel,
  disabled = false,
  disabledReason,
  onBlocked,
}: {
  values: readonly number[];
  value: number;
  onChange: (v: number) => void;
  ariaLabel: string;
  disabled?: boolean;
  /** Why it is unavailable. Rendered as the title so the control is never a
   *  silent grey rectangle (house rule §11.8). */
  disabledReason?: string | null;
  /** Called when a blocked control is pressed, so the caller can toast. */
  onBlocked?: (reason: string) => void;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const pressY = useRef<number | null>(null);
  const moved = useRef(false);
  const btn = useRef<HTMLButtonElement>(null);
  const root = useRef<HTMLDivElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    setHover(null);
    pressY.current = null;
    moved.current = false;
  }, []);

  // Escape closes without committing — the keyboard's version of sliding back
  // to the origin.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  // TAP-AWAY, FOR TAPS THE BACKDROP CANNOT SEE. The backdrop below is
  // `fixed inset-0`, and this control lives inside the Focuser `.panel`, whose
  // `backdrop-filter: blur(14px)` makes a fixed descendant resolve against the
  // PANEL box rather than the viewport (measured in this repo — index.css
  // §overlay: "a `fixed inset-0` probe inside a panel resolves to 700x1515, not
  // the viewport"). So the backdrop covers the Focuser panel and nothing else:
  // a tap on the live preview, the Camera panel or the page gutter left the arc
  // open with the trigger still wearing its engaged `border-accent text-accent`,
  // four floating option buttons sitting over the position/max/temp stats — and
  // the next stray tap on one of them silently re-sized the step, so the NEXT
  // press of +/- moved the focuser by a magnitude nobody chose. Escape already
  // covers a keyboard; a phone in the dark had no way out at all.
  //
  // Window-level and CAPTURE-phase, so no stopPropagation between here and the
  // document can hide the press from us. Anything inside our own subtree (the
  // trigger, the option buttons, the backdrop) is handled by the handlers that
  // own it — this only closes for presses that land somewhere else entirely,
  // which also makes it immune to the press that opened the arc.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: Event) => {
      const t = e.target as Node | null;
      if (t && root.current?.contains(t)) return;
      close();
    };
    window.addEventListener("pointerdown", onDown, true);
    return () => window.removeEventListener("pointerdown", onDown, true);
  }, [open, close]);

  const begin = (clientY: number) => {
    if (disabled) {
      if (disabledReason && onBlocked) onBlocked(disabledReason);
      return;
    }
    pressY.current = clientY;
    moved.current = false;
    setOpen(true);
    setHover(null);
  };

  const track = (clientY: number) => {
    if (pressY.current === null) return;
    const dy = pressY.current - clientY;      // up is positive
    if (Math.abs(dy) > DEAD_ZONE_PX) moved.current = true;
    setHover(optionAt(dy, values.length));
  };

  const end = () => {
    if (pressY.current === null) return;
    // A press with no travel is a TAP: leave the arc open so it can be used as
    // a menu (desktop, or anyone who does not want to hold). A press that
    // travelled commits and closes — one continuous gesture, no second tap.
    if (!moved.current) { pressY.current = null; return; }
    const dy = hover === null ? 0 : DEAD_ZONE_PX + hover * OPTION_PITCH_PX;
    const next = commitValue(values, value, dy);
    if (next !== value) onChange(next);
    close();
  };

  return (
    // touch-action: none, inline, on the root — the browser intersects
    // touch-action from the touched element up to its scroll container, so this
    // covers the button below it. Without it the designed gesture (press and
    // slide UP through the options) is also the browser's page-scroll gesture,
    // and the page wins: the arc opens, the page moves under the thumb, and the
    // release commits whatever the finger happened to be over. Every other
    // vertical-drag control here sets it the same way (SlewPad.tsx:314,
    // TouchGuard.tsx:166, StretchHistogram.tsx:232). Found while building the
    // Focus pod, where the same control sits over a live image.
    <div ref={root} className="relative select-none" style={{ touchAction: "none" }}>
      {/* The arc. Opens UPWARD so the thumb never covers the options it is
          choosing between, and is aria-hidden because the button below is the
          real control — a listbox here would announce twice. */}
      {open && (
        <div
          className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 flex flex-col-reverse
                     items-stretch gap-1 z-30"
          aria-hidden
        >
          {values.map((v, i) => (
            <button
              key={v}
              type="button"
              tabIndex={-1}
              style={{ minHeight: OPTION_PITCH_PX - 4 }}
              className={`px-3 mono text-sm border tap ${
                (hover ?? values.indexOf(value)) === i
                  ? "border-accent text-accent bg-raise"
                  : "border-line2 text-dim bg-bg"
              }`}
              onClick={() => { onChange(v); close(); }}
            >
              {v}
            </button>
          ))}
        </div>
      )}
      <button
        ref={btn}
        type="button"
        role="spinbutton"
        aria-label={ariaLabel}
        aria-valuenow={value}
        aria-valuetext={`${value} steps`}
        aria-disabled={disabled || undefined}
        title={disabled ? (disabledReason ?? undefined) : `${ariaLabel} — ${value}`}
        className={`btn tap min-h-[44px] min-w-[64px] mono !normal-case justify-center ${
          open ? "border-accent text-accent" : ""
        }`}
        onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); begin(e.clientY); }}
        onPointerMove={(e) => track(e.clientY)}
        onPointerUp={end}
        onPointerCancel={close}
        onKeyDown={(e) => {
          if (disabled) return;
          const next = stepByKey(values, value, e.key);
          if (next !== value) { e.preventDefault(); onChange(next); }
        }}
      >
        {value}
      </button>
      {/* Backdrop: a tap anywhere ELSE IN THIS PANEL dismisses the open arc
          without committing, and is swallowed rather than passed through, so a
          dismissing tap cannot also press Halt or a nudge button underneath.
          Rendered AFTER the button so it cannot swallow the press that opened
          it. It reaches only as far as the panel (see the window listener
          above, which covers everything outside it). */}
      {open && (
        <div className="fixed inset-0 z-20" aria-hidden onPointerDown={close} />
      )}
    </div>
  );
}
