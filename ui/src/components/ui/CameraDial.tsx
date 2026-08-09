/* CameraDial — one control for every camera setting, everywhere (2026-08-08).

   Asked for directly: "click the icon and have the exposure setting categories
   fan out, and then I can pick the category (exposure, gain, binning, offset,
   etc) which then fans out further to present me options for that thing."

   So: TWO RINGS, one gesture each.
     tap the disc      -> the CATEGORIES bloom on an arc (EXP, GAIN, BIN, …)
     tap a category    -> its VALUES replace them
     tap a value       -> it applies and the dial closes
   Back returns to the categories; tapping the disc again closes everything.

   Why radial and not another dropdown: this is used one-handed, in the dark,
   at the scope, with the other hand on a focuser or a bolt. The arc opens
   up-and-left from a disc parked in a corner, which is the only quadrant a
   right thumb pivoting at that corner can sweep without the hand leaving the
   phone — the same reasoning (and the same geometry constants) as the Focus
   pod's action arc, which this generalises.

   ── A CATEGORY THAT DOES NOT FIT THE ARC OPENS A RING INSTEAD (2026-08-08) ──

   The arc seats five 48px chips and was being handed thirteen exposures and ten
   gains: 7.5° apart, an 18.3px chord between 48px chips, about three deep.
   "The exposures are too densely populated. I can't actually read any of them."

   So the second ring is now TWO layouts and the choice is derived, not judged:
   `needsRing()` counts seats. Four options or fewer (four options plus Back is
   exactly the arc's five seats) keep today's arc, unchanged, in the corner.
   More than four open RingPicker — the requested "new dialog… with the exposure
   icon in the middle and a ring of exposures around it".

   The ring is a screen-level overlay and NOT an inflated corner arc, because a
   full circle of thirteen readable chips cannot be reached from the corner in
   any arrangement — the thumb's quarter-disc sweep holds nine chips, total. The
   full derivation, and the three things done to pay the reach back (smallest
   legible radius, hub clamped toward the thumb, current value pinned at the near
   pole), are in lib/ringDial.ts. That file is the one to read before touching
   any number in this one.

   ── BACK GETS A SEAT, NOT A COORDINATE ──

   Back used to be drawn at `slot(0)` while the options were laid out with
   `dialFractions(n)`, which puts option 0 at frac 0 as well. Back rendered last,
   so it sat on top of the first option and swallowed it — the operator's filter
   ring read "R G B S H O" with no L, and the same thing was quietly eating the
   first exposure, the first gain and the first bin. `arcSeatFracs()` now hands
   out seats to the options AND to Back, so the collision cannot be reintroduced
   by rendering order, and lib/__tests__/ringDial.test.ts measures the gap
   between every pair of seats rather than reading their labels.

   OFFSET IS A TEXT BOX, not a ring. Its useful values are a continuum a
   preset list cannot cover, and it is set once per camera and then forgotten;
   a `kind: "entry"` category renders one field and a Set button instead of
   options. Any future setting shaped like that gets it for free.

   The dial owns NO camera state. It renders what it is handed and calls back —
   so the Align strip, the Focus panel and every preview surface can share one
   control while keeping their own very different notions of where a value
   lives (a polar session, a plan's advanced params, a guide config). */
import { useEffect, useLayoutEffect, useRef, useState,
         type CSSProperties, type JSX } from "react";
import { Icon, type IconName } from "../icons";
import { LockedChip } from "../ui";
import RingPicker, { type RingItem } from "./RingPicker";
import {
  DIAL_DISC_PX, DIAL_ITEM_PX, DIAL_MIN_R, DIAL_R,
  arcSeatFracs, arcXY, dialFractions, dialRadius, needsRing,
} from "../../lib/ringDial";

// The geometry moved to lib/ringDial.ts (no React, so the seat maths can be
// tested without a DOM) but these were part of this module's surface, so they
// stay part of it.
export { DIAL_DISC_PX, DIAL_ITEM_PX, DIAL_MIN_R, DIAL_R, dialFractions, dialRadius };

export type DialOption = { id: string; label: string };

export type DialCategory =
  | { id: string; label: string; icon?: IconName; kind?: "options";
      options: readonly DialOption[]; selected?: string;
      onPick: (id: string) => void }
  | { id: string; label: string; icon?: IconName; kind: "entry";
      value: string; placeholder?: string; hint?: string;
      onSubmit: (text: string) => void };

/**
 * Where one item sits on the quarter arc that opens up-and-left from the disc.
 * `frac` 0 is due LEFT, 1 is due UP. Offsets in px from the disc centre, y
 * negative = upward, ready to drop into a translate().
 */
export function dialPolar(frac: number, radius: number,
                          side: "right" | "left" = "right"): { x: number; y: number } {
  const { x, y } = arcXY(frac, radius);
  // A disc in the RIGHT corner opens up-and-left; one in the LEFT corner
  // mirrors and opens up-and-right. Either way the arc stays over the image
  // and inside the thumb's sweep from the corner it is parked in — which is
  // what lets two dials share one stage (Focus: actions right, settings left).
  const sx = x * (side === "left" ? -1 : 1);
  return { x: sx === 0 ? 0 : sx, y };
}

export default function CameraDial({
  categories, label = "Camera settings", summary, elsewhere,
  right = 12, bottom = 12, side = "right", className = "",
}: {
  categories: readonly DialCategory[];
  /** The disc's accessible name — say what it CONTROLS, not that it opens. */
  label?: string;
  /** One line on the disc face: the settings as they stand ("2s g120"). */
  summary?: string;
  /**
   * Where else THIS screen exposes these settings, for the locked face below
   * ("on the row under the reticle"). The dial cannot know — it is used on
   * Capture, Focus and Align, which each keep their values somewhere different
   * — so the screen tells it, and the sentence names a place the operator can
   * actually go instead of just refusing.
   */
  elsewhere?: string;
  right?: number;
  bottom?: number;
  /** Which corner the disc parks in; the arc mirrors to match. */
  side?: "right" | "left";
  className?: string;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const [openCat, setOpenCat] = useState<string | null>(null);
  const [bloom, setBloom] = useState(false);
  const [box, setBox] = useState<{ w: number; h: number } | null>(null);
  const [anchor, setAnchor] = useState<{ x: number; y: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const discRef = useRef<HTMLButtonElement>(null);
  const entryRef = useRef<HTMLInputElement>(null);
  // Which category chip to put focus back on when the second ring closes.
  const returnTo = useRef<string | null>(null);

  // Two-frame bloom: items mount at the disc centre and are pushed out on the
  // NEXT frame, so the browser has a from-state to transition out of.
  useEffect(() => {
    if (!open) { setBloom(false); return; }
    const id = requestAnimationFrame(() => setBloom(true));
    return () => cancelAnimationFrame(id);
  }, [open, openCat]);

  // Measure the surface this overlays — the arc must stay inside it.
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    // A ZERO-SIZE READING IS NOT A MEASUREMENT. `null` means "not measured
    // yet" and assumes the full arc; storing {0,0} instead would mean
    // "measured, and it does not fit", which suppresses the dial entirely.
    // An element read before layout reports 0, so the eager read below has to
    // be guarded or the control disappears on first paint and never returns
    // if the observer's first callback already landed.
    const put = (w: number, h: number) => {
      if (w > 0 && h > 0) setBox({ w, h });
    };
    const ro = new ResizeObserver((e) => {
      const r = e[0].contentRect;
      put(r.width, r.height);
    });
    ro.observe(el);
    put(el.clientWidth, el.clientHeight);
    return () => ro.disconnect();
  }, []);

  const close = () => {
    setOpen(false);
    setOpenCat(null);
    returnTo.current = null;
    // Focus must return to the disc or it lands on <body> and the next Tab
    // restarts at the top of the page — the classic menu-close trap.
    discRef.current?.focus();
  };

  const cat = categories.find((c) => c.id === openCat) ?? null;
  const isEntry = cat != null && cat.kind === "entry";

  const goBack = () => {
    // Remember where we came from: closing the second ring must put focus back
    // on the category chip it opened from, not on <body>.
    if (cat != null) returnTo.current = cat.id;
    setOpenCat(null);
  };

  // Escape closes, from anywhere — a keyboard user who tabbed back to the disc
  // would otherwise be stuck with an open ring. `defaultPrevented` so that
  // dismissing a tooltip does not also throw the ring away.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (openCat) goBack(); else close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, openCat]);

  const radius = dialRadius(box, { right, bottom });
  const fits = radius >= DIAL_MIN_R;
  useEffect(() => { if (!fits) { setOpen(false); setOpenCat(null); } }, [fits]);

  // What the ring currently holds: the categories, or one category's values.
  const items: RingItem[] =
    cat == null
      ? categories.map((c) => ({
          key: c.id,
          face: c.label,
          aria: `${c.label} settings`,
          icon: c.icon,
          press: () => setOpenCat(c.id),
        }))
      : cat.kind === "entry"
        ? []
        : cat.options.map((o) => ({
            key: o.id,
            face: o.label,
            aria: `${cat.label} ${o.label}`,
            on: cat.selected === o.id,
            press: () => { cat.onPick(o.id); close(); },
          }));

  // Which layout. Back occupies a seat exactly like an option does, so it is
  // counted — that is what makes "more than four options" the threshold.
  const hasBack = cat != null;
  const ringOpen = open && fits && needsRing(items.length, hasBack) && !isEntry;

  // Where the ring is pulled toward: the disc the operator's thumb is on.
  useLayoutEffect(() => {
    if (!ringOpen) { setAnchor(null); return; }
    const r = discRef.current?.getBoundingClientRect();
    if (r && r.width > 0 && r.height > 0) {
      setAnchor({ x: r.left + r.width / 2, y: r.top + r.height / 2 });
      return;
    }
    // A zero-size reading is not a measurement, here as above: fall back to
    // where the disc parks, measured off the screen instead of off the element.
    const w = typeof window === "undefined" ? 0 : window.innerWidth;
    const h = typeof window === "undefined" ? 0 : window.innerHeight;
    const half = DIAL_DISC_PX / 2;
    setAnchor(side === "left"
      ? { x: right + half, y: h - bottom - half }
      : { x: w - right - half, y: h - bottom - half });
  }, [ringOpen, openCat, side, right, bottom]);

  // Focus follows the ring. Opening a category moves focus into what replaced
  // the chip that was under it (RingPicker does its own); closing it puts focus
  // back on that chip. Neither used to happen, so a keyboard user lost their
  // place at every step.
  useEffect(() => {
    if (!open || ringOpen) return;
    if (openCat == null) {
      const back = returnTo.current;
      if (back == null) return;
      returnTo.current = null;
      const el = Array.from(
        rootRef.current?.querySelectorAll<HTMLElement>("[data-dial-item]") ?? [])
        .find((b) => b.getAttribute("data-dial-item") === back);
      el?.focus();
      return;
    }
    // Going FORWARD only follows a caret that already exists. If nothing has
    // focus, nobody is driving by keyboard and yanking it would take the
    // operator somewhere they did not ask to go; closing, above, is
    // unconditional, because focus left on a node we are about to unmount
    // falls to <body> and the next Tab restarts at the top of the page.
    const active = rootRef.current?.ownerDocument?.activeElement;
    if (active == null || active === rootRef.current?.ownerDocument?.body) return;
    if (isEntry) { entryRef.current?.focus(); return; }
    const first = rootRef.current?.querySelector<HTMLElement>(
      "[data-dial-slot] [data-dial-item]");
    first?.focus();
  }, [open, openCat, ringOpen, isEntry]);

  // Seats, Back included. `arcSeatFracs` is why Back can no longer land on top
  // of the first option.
  const seats = arcSeatFracs(items.length, hasBack);

  const slot = (frac: number): CSSProperties => {
    const { x, y } = dialPolar(frac, radius, side);
    return {
      position: "absolute",
      left: DIAL_DISC_PX / 2,
      top: DIAL_DISC_PX / 2,
      transform: bloom
        ? `translate(calc(-50% + ${x}px), calc(-50% + ${y}px))`
        : "translate(-50%, -50%) scale(0.7)",
      opacity: bloom ? 1 : 0,
      transition: "transform 190ms cubic-bezier(0.2,0.9,0.3,1), opacity 150ms ease",
      transitionDelay: `${Math.round(frac * 4) * 30}ms`,
      touchAction: "none",
    };
  };

  const submitEntry = () => {
    if (cat == null || cat.kind !== "entry") return;
    const v = entryRef.current?.value ?? "";
    if (!v.trim()) return;
    cat.onSubmit(v.trim());
    close();
  };

  const selectedFace = cat != null && cat.kind !== "entry"
    ? cat.options.find((o) => o.id === cat.selected)?.label
    : undefined;

  return (
    <div
      ref={rootRef}
      className={`absolute inset-0 pointer-events-none overflow-hidden z-20 ${className}`}
      style={{ touchAction: "none" }}
      data-camera-dial
    >
      {/* ── THE STAGE IS TOO SHORT: SAY SO, DO NOT VANISH (#202) ────────────
          This branch used to be nothing at all. `{fits && …}` with no `else`
          meant that on a stage under DIAL_MIN_R the entire control — disc,
          summary, every setting behind it — was simply absent, with no dim, no
          glyph, no sentence, and nothing in the accessibility tree. That is the
          shape §11.8 exists to forbid, and it is worse than a native `disabled`
          button: at least a grey rectangle can be pointed at.

          It also had a concrete cost. `PolarQuickBar`'s exp/gain/bin/filt row
          survived #179 partly on this: "the dial deletes itself below a 124px
          stage, i.e. on the phone polar alignment is done from", so the row had
          to stay or offset became unreachable. A control that disappears cannot
          be reasoned about by the screen around it.

          So: the disc's own footprint, dimmed, carrying the lock glyph, the
          summary it would have shown anyway, and a reason naming where the
          settings ARE. Still a READOUT when it cannot be a control — which is
          the half of the job a short stage does not actually prevent.
          `LockedChip` brings `aria-disabled`, focusability and
          `!pointer-events-auto`, so a touch user can tap it and hear why. */}
      {!fits && (
        <div
          className="absolute pointer-events-auto"
          style={{ ...(side === "left" ? { left: right } : { right }), bottom }}
          data-dial-locked
        >
          <LockedChip
            reason={`${label} needs a taller image area to open its arc.`
              + (elsewhere ? ` These settings are ${elsewhere}.` : "")}
            className="rounded-full border border-line2 bg-raise/90 px-2.5"
          >
            <span className="mono text-[11px] leading-none">{summary ?? label}</span>
          </LockedChip>
        </div>
      )}

      {fits && (<>
        {open && !ringOpen && (
          <div className="absolute inset-0 pointer-events-auto" aria-hidden
            style={{ touchAction: "none" }} onPointerDown={close} />
        )}

        {/* A category the arc cannot seat gets the ring instead. It is a portal
            at screen level, so it is drawn outside this stage-sized box. */}
        {ringOpen && anchor != null && (
          <RingPicker
            items={items}
            anchor={anchor}
            label={cat ? `${cat.label} values` : label}
            onDismiss={close}
            hub={cat != null
              ? {
                  label: cat.label,
                  value: selectedFace,
                  icon: cat.icon,
                  aria: `${cat.label}${selectedFace ? ` ${selectedFace}` : ""}`
                        + " — back to settings",
                  press: goBack,
                }
              : {
                  label: "CLOSE",
                  value: summary,
                  icon: "x",
                  aria: `Close ${label}`,
                  press: close,
                }}
          />
        )}

        <div
          className="absolute pointer-events-auto"
          style={{ ...(side === "left" ? { left: right } : { right }), bottom,
                   width: DIAL_DISC_PX, height: DIAL_DISC_PX,
                   touchAction: "none" }}
        >
          {/* Radial scrim behind the ring only: the chips must read over a star
              field, but the frame being judged must not be dimmed. */}
          {open && !ringOpen && (
            <div aria-hidden className="absolute pointer-events-none"
              style={{
                left: DIAL_DISC_PX / 2, top: DIAL_DISC_PX / 2,
                width: 2 * (radius + 44), height: 2 * (radius + 44),
                transform: "translate(-50%, -50%)", borderRadius: "50%",
                background: "radial-gradient(circle, rgba(0,0,0,0.62) 0%,"
                  + " rgba(0,0,0,0.52) 52%, rgba(0,0,0,0.22) 76%, rgba(0,0,0,0) 92%)",
                opacity: bloom ? 1 : 0, transition: "opacity 150ms ease",
              }} />
          )}

          {open && !ringOpen && (
            <div role="menu" aria-label={cat ? `${cat.label} values` : label}
              className="absolute inset-0" style={{ touchAction: "none" }}>
              {/* `data-dial-seat` puts the seat in the DOM, so a test can ask
                  whether two things share one without reading a transform
                  string through a browser that may not have laid out yet. */}
              {items.map((it, i) => (
                <div key={it.key} data-dial-slot data-dial-seat={seats.options[i]}
                     role="none" style={slot(seats.options[i])}>
                  <button
                    type="button"
                    role="menuitem"
                    data-dial-item={it.key}
                    aria-label={it.aria}
                    className={`btn tap mono !normal-case inline-flex flex-col items-center
                                justify-center gap-0.5 leading-none rounded-lg ${
                                  it.on ? "border-accent text-accent bg-accent/10" : ""}`}
                    style={{ minWidth: DIAL_ITEM_PX, minHeight: DIAL_ITEM_PX,
                             paddingLeft: 6, paddingRight: 6, touchAction: "none" }}
                    onClick={it.press}
                  >
                    {it.icon && <Icon name={it.icon} size={14} aria-hidden />}
                    <span className="text-[11px] tracking-wide">{it.face}</span>
                  </button>
                </div>
              ))}

              {/* A category whose values are a continuum, not a list. */}
              {isEntry && cat.kind === "entry" && (
                <div data-dial-entry style={slot(0.5)}>
                  <div className="border border-line2 bg-raise rounded-lg p-2 w-[168px]">
                    <span className="label !text-[9px] block mb-1">{cat.label}</span>
                    <div className="flex items-center gap-1.5">
                      <input
                        ref={entryRef}
                        className="field flex-1 min-w-0 mono text-xs"
                        inputMode="numeric"
                        defaultValue={cat.value}
                        placeholder={cat.placeholder}
                        aria-label={`${cat.label} value`}
                        onKeyDown={(e) => { if (e.key === "Enter") submitEntry(); }}
                      />
                      <button type="button" className="btn min-h-[36px] text-[11px] !px-2.5"
                        onClick={submitEntry}>Set</button>
                    </div>
                    {cat.hint && (
                      <p className="text-[10px] text-faint mt-1 leading-snug">{cat.hint}</p>
                    )}
                  </div>
                </div>
              )}

              {/* Back to the categories — the second ring must not be a
                  one-way door, and Escape alone is not reachable by thumb. It
                  holds a SEAT of its own (frac 0, the seat a corner-pivoting
                  thumb reaches most easily); it used to share one with the
                  first option and hide it. */}
              {seats.back != null && (
                <div style={slot(seats.back)} data-dial-slot data-dial-seat={seats.back}
                     role="none">
                  <button type="button" role="menuitem" data-dial-item="__back"
                    aria-label="Back to settings"
                    className="btn tap mono !normal-case rounded-lg"
                    style={{ minWidth: DIAL_ITEM_PX, minHeight: DIAL_ITEM_PX,
                             touchAction: "none" }}
                    onClick={goBack}>
                    ‹
                  </button>
                </div>
              )}
            </div>
          )}

          <button
            ref={discRef}
            type="button"
            data-dial-disc
            aria-haspopup="menu"
            aria-expanded={open}
            aria-label={open ? `Close ${label}` : `${label}${summary ? ` — ${summary}` : ""}`}
            title={summary ? `${label}: ${summary}` : label}
            className="absolute inset-0 rounded-full flex flex-col items-center justify-center
                       border border-line2 bg-panel"
            style={{ touchAction: "none" }}
            onClick={() => (open ? close() : setOpen(true))}
          >
            <Icon name="settings" size={16} aria-hidden />
            {summary && (
              <span className="mono text-[9px] leading-none text-dim mt-0.5">{summary}</span>
            )}
          </button>
        </div>
      </>)}
    </div>
  );
}
