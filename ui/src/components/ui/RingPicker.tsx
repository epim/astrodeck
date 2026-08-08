/* RingPicker — the overlay a crowded category opens instead of the arc
   (2026-08-08).

   Asked for directly: "when picking exposure it should pop up a new dialog that
   has the exposure icon in the middle and have a ring of exposures around it.
   Think of the trefoil design of the radiation icon, but with more than 3 tabs.
   The same goes for anything under the speed dial that has more than 4 options,
   because it's too dense to show. So this would include gain."

   THE GEOMETRY AND EVERY NUMBER IN IT LIVE IN lib/ringDial.ts, with the
   derivations — including the one that decides this component's whole shape:
   thirteen readable 48px chips do not fit inside the thumb's corner sweep in ANY
   arrangement (the sweep is 21,124px² and a chip is 2,304px², so nine at best),
   which is why the ring is a screen-level overlay clamped toward the thumb
   rather than an inflated corner arc. Read that header before changing anything
   here; this file only draws what it is told.

   WHY A PORTAL AND NOT A BOX INSIDE THE PREVIEW. A ring needs a SQUARE of
   2·(r+24+12) — 298px for 13 exposures — where the quarter arc needed only a
   corner. A preview stage in phone landscape is routinely shorter than that, so
   confining the overlay to the stage would put the fallback on the common path
   for exactly the category that reported the bug. The operator asked for a
   dialog; a dialog is screen-level, so it is measured against the screen.

   THE HUB IS THE WAY BACK. It is the biggest target in the overlay and it sits
   at the one place on the ring that cannot collide with a chip — the centre,
   which every chip clears by construction. That is not decoration: the defect
   this replaces was Back sharing a seat with the first option and eating it.
   Back cannot be given a coordinate near the options ever again.

   DIMMING, unlike the inline arc. The arc deliberately does not dim the frame,
   because it is used while judging that frame. This is a dialog: you are setting
   up the next exposure, not reading the last one, and the chips have to be
   legible over a star field. */
import { useEffect, useMemo, useRef, useState,
         type CSSProperties, type JSX, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { Icon, type IconName } from "../icons";
import {
  DIAL_ITEM_PX, RING_HUB_PX, ringPlacement, ringSlots, ringStep,
} from "../../lib/ringDial";

export interface RingItem {
  key: string;
  /** What the chip reads. */
  face: string;
  /** What it SETS, said in full — "EXP 30s", never "30s". */
  aria: string;
  icon?: IconName;
  /** The value in force now. Pinned at the near pole. */
  on?: boolean;
  press: () => void;
}

export interface RingHub {
  label: string;
  /** The setting as it stands, on the hub face — so the current value is
   *  readable without hunting the ring for the highlighted chip. */
  value?: string;
  icon?: IconName;
  /** Accessible name. Starts with the visible text, then says what it does. */
  aria: string;
  press: () => void;
}

export default function RingPicker({
  items, hub, anchor, label, onDismiss,
}: {
  items: readonly RingItem[];
  hub: RingHub;
  /** Viewport point the ring is pulled toward — the disc the operator touched.
   *  See ringDial.ringPlacement: this is what keeps the reach honest. */
  anchor: { x: number; y: number };
  /** The menu's accessible name. */
  label: string;
  /** Tap outside / dismiss the whole dial. */
  onDismiss: () => void;
}): JSX.Element | null {
  const [view, setView] = useState<{ w: number; h: number }>(() =>
    typeof window === "undefined"
      ? { w: 0, h: 0 }
      : { w: window.innerWidth, h: window.innerHeight });
  const [bloom, setBloom] = useState(false);

  const selected = Math.max(0, items.findIndex((i) => i.on));
  const [focus, setFocus] = useState(selected);
  const btns = useRef<Array<HTMLButtonElement | null>>([]);
  const hubRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const on = () => setView({ w: window.innerWidth, h: window.innerHeight });
    on();
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);

  // Same two-frame bloom as the arc: mount at the hub, push out next frame, so
  // the browser has a from-state to transition out of.
  useEffect(() => {
    const id = requestAnimationFrame(() => setBloom(true));
    return () => cancelAnimationFrame(id);
  }, []);

  // Roving tabindex. Focus opens ON THE CURRENT VALUE, not on the first chip:
  // the ring's whole readability claim is that you can find what is set without
  // reading every label, and that has to hold for a keyboard too.
  useEffect(() => { btns.current[focus]?.focus(); }, [focus, items.length]);

  const place = useMemo(
    () => ringPlacement(view, anchor, items.length),
    [view.w, view.h, anchor.x, anchor.y, items.length]);
  const slots = useMemo(
    () => ringSlots(items.length, place.r, place.near, selected),
    [items.length, place.r, place.near, selected]);

  if (typeof document === "undefined") return null;

  const onKey = (e: ReactKeyboardEvent) => {
    // Tab stays inside. The roving tabindex leaves the ring exactly two tab
    // stops — the chip with focus, and the hub — so cycling between them is a
    // complete trap and needs no sentinel nodes. Without it Tab walks out onto
    // a page the operator cannot see behind a full-screen scrim, and the way
    // back is a guess.
    if (e.key === "Tab") {
      e.preventDefault();
      if (document.activeElement === hubRef.current) btns.current[focus]?.focus();
      else hubRef.current?.focus();
      return;
    }
    const next = ringStep(focus, items.length, e.key);
    if (next == null) return;
    e.preventDefault();
    setFocus(next);
  };

  const chip = (i: number, it: RingItem, style: CSSProperties) => (
    <button
      key={it.key}
      ref={(el) => { btns.current[i] = el; }}
      type="button"
      role="menuitem"
      data-dial-item={it.key}
      data-ring-item={it.key}
      aria-label={it.aria}
      tabIndex={i === focus ? 0 : -1}
      className={`btn tap mono !normal-case inline-flex flex-col items-center justify-center
                  gap-0.5 leading-none rounded-lg ${
                    it.on ? "border-accent text-accent bg-accent/10" : ""}`}
      style={{ minWidth: DIAL_ITEM_PX, minHeight: DIAL_ITEM_PX,
               paddingLeft: 6, paddingRight: 6, touchAction: "none", ...style }}
      onFocus={() => setFocus(i)}
      onClick={it.press}
    >
      {it.icon && <Icon name={it.icon} size={14} aria-hidden />}
      <span className="text-[11px] tracking-wide">{it.face}</span>
    </button>
  );

  const hubFace = (
    <button
      ref={hubRef}
      type="button"
      role="menuitem"
      data-dial-item="__back"
      data-ring-hub
      aria-label={hub.aria}
      className="btn !normal-case inline-flex flex-col items-center justify-center
                 gap-0.5 leading-none rounded-full border-line2 bg-panel"
      style={{ width: RING_HUB_PX, height: RING_HUB_PX, touchAction: "none" }}
      onClick={hub.press}
    >
      <span aria-hidden className="text-[13px] leading-none text-dim">‹</span>
      {hub.icon && <Icon name={hub.icon} size={16} aria-hidden />}
      <span className="label !text-[9px] leading-none">{hub.label}</span>
      {hub.value && (
        <span className="mono text-[11px] leading-none text-accent">{hub.value}</span>
      )}
    </button>
  );

  return createPortal(
    <div
      data-ring-picker
      className="fixed inset-0 z-[70]"
      style={{ touchAction: "none" }}
    >
      {/* Dismiss surface. aria-hidden: the menu below carries the semantics and
          Escape is the keyboard route out, so this must not appear in the tree
          as a nameless control. */}
      <div
        aria-hidden
        className="absolute inset-0"
        style={{
          background: place.fits
            ? `radial-gradient(circle at ${place.cx}px ${place.cy}px,`
              + " rgba(0,0,0,0.80) 0%, rgba(0,0,0,0.74) 55%, rgba(0,0,0,0.62) 100%)"
            : "rgba(0,0,0,0.74)",
          opacity: bloom ? 1 : 0,
          transition: "opacity 150ms ease",
          touchAction: "none",
        }}
        onPointerDown={onDismiss}
      />

      <div
        role="menu"
        aria-label={label}
        data-ring-fit={place.fits ? "ring" : "list"}
        onKeyDown={onKey}
        className="absolute inset-0"
        style={{ touchAction: "none" }}
      >
        {place.fits ? (
          <>
            <div className="absolute" style={{
              left: place.cx, top: place.cy,
              transform: "translate(-50%, -50%)",
            }}>{hubFace}</div>

            {items.map((it, i) => chip(i, it, {
              position: "absolute",
              left: place.cx,
              top: place.cy,
              transform: bloom
                ? `translate(calc(-50% + ${slots[i].x}px), calc(-50% + ${slots[i].y}px))`
                : "translate(-50%, -50%) scale(0.7)",
              opacity: bloom ? 1 : 0,
              transition:
                "transform 190ms cubic-bezier(0.2,0.9,0.3,1), opacity 150ms ease",
              transitionDelay: `${Math.min(4, Math.abs(i - selected)) * 25}ms`,
            }))}
          </>
        ) : (
          /* THE FLOOR, not a routine. A screen too small for a legible ring gets
             the same chips as a wrapped list rather than a tighter ring — a ring
             whose chips overlap is the defect this component exists to fix, and
             shipping a smaller one would just hide it again. Arrows still walk
             list order, which is what they mean on a line. */
          <div className="absolute inset-0 flex items-center justify-center p-4"
               style={{ pointerEvents: "none" }}>
            <div className="border border-line2 bg-panel rounded-xl p-3 max-w-full
                            max-h-full overflow-auto"
                 style={{ pointerEvents: "auto" }}>
              <div className="flex justify-center mb-2">{hubFace}</div>
              <div className="flex flex-wrap gap-2 justify-center"
                   style={{ maxWidth: 4 * (DIAL_ITEM_PX + 8) }}>
                {items.map((it, i) => chip(i, it, {}))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
