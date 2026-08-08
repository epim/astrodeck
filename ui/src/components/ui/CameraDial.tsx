/* CameraDial — one control for every camera setting, everywhere (2026-08-08).

   Asked for directly: "click the icon and have the exposure setting categories
   fan out, and then I can pick the category (exposure, gain, binning, offset,
   etc) which then fans out further to present me options for that thing."

   So: TWO RINGS, one gesture each.
     tap the disc      -> the CATEGORIES bloom on an arc (EXP, GAIN, BIN, …)
     tap a category    -> its VALUES replace them on the same arc
     tap a value       -> it applies and the dial closes
   Back returns to the categories; tapping the disc again closes everything.

   Why radial and not another dropdown: this is used one-handed, in the dark,
   at the scope, with the other hand on a focuser or a bolt. The arc opens
   up-and-left from a disc parked in a corner, which is the only quadrant a
   right thumb pivoting at that corner can sweep without the hand leaving the
   phone — the same reasoning (and the same geometry constants) as the Focus
   pod's action arc, which this generalises.

   OFFSET IS A TEXT BOX, not a ring. Its useful values are a continuum a
   preset list cannot cover, and it is set once per camera and then forgotten;
   a `kind: "entry"` category renders one field and a Set button instead of
   options. Any future setting shaped like that gets it for free.

   The dial owns NO camera state. It renders what it is handed and calls back —
   so the Align strip, the Focus panel and every preview surface can share one
   control while keeping their own very different notions of where a value
   lives (a polar session, a plan's advanced params, a guide config). */
import { useEffect, useRef, useState, type CSSProperties, type JSX } from "react";
import { Icon, type IconName } from "../icons";

/** Disc diameter — 56px, the house one-tap hero size (`.tap-lg`). */
export const DIAL_DISC_PX = 56;
/** Item footprint on the arc. 48 is the spec's chip minimum, above the app's
 *  44px tap floor because these are aimed at over a live image. */
export const DIAL_ITEM_PX = 48;
/** Radius the ring sits at. Five items over a quarter turn are 22.5° apart and
 *  the chord between neighbours is 2·R·sin(11.25°) = 0.39·R, so at R=140 two
 *  48px items clear each other by 6.6px. Below R=124 they would overlap. */
export const DIAL_R = 140;
export const DIAL_MIN_R = 124;

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
  const a = (Math.max(0, Math.min(1, frac)) * Math.PI) / 2;
  const round = (v: number) => Math.round(v * 10) / 10;
  // A disc in the RIGHT corner opens up-and-left; one in the LEFT corner
  // mirrors and opens up-and-right. Either way the arc stays over the image
  // and inside the thumb's sweep from the corner it is parked in — which is
  // what lets two dials share one stage (Focus: actions right, settings left).
  const x = round(-radius * Math.cos(a)) * (side === "left" ? -1 : 1);
  return { x: x === 0 ? 0 : x, y: round(-radius * Math.sin(a)) };
}

/**
 * The radius this stage can actually give the ring, and whether it is usable.
 * The dial measures the box it overlays at runtime; `null` (not yet measured,
 * or server-rendered) assumes the full arc, and the ring is closed on first
 * paint anyway so the correction lands before anything is drawn.
 */
export function dialRadius(box: { w: number; h: number } | null,
                           inset: { right: number; bottom: number }): number {
  if (box == null) return DIAL_R;
  const half = DIAL_ITEM_PX / 2;
  const room = Math.min(box.h - (inset.bottom + DIAL_DISC_PX / 2) - half,
                        box.w - (inset.right + DIAL_DISC_PX / 2) - half);
  return Math.max(0, Math.min(DIAL_R, Math.floor(room)));
}

/** Evenly spaced arc fractions for n items — one item sits at the top. */
export function dialFractions(n: number): number[] {
  if (n <= 1) return [1];
  return Array.from({ length: n }, (_, i) => i / (n - 1));
}

export default function CameraDial({
  categories, label = "Camera settings", summary,
  right = 12, bottom = 12, side = "right", className = "",
}: {
  categories: readonly DialCategory[];
  /** The disc's accessible name — say what it CONTROLS, not that it opens. */
  label?: string;
  /** One line on the disc face: the settings as they stand ("2s g120"). */
  summary?: string;
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
  const rootRef = useRef<HTMLDivElement>(null);
  const discRef = useRef<HTMLButtonElement>(null);
  const entryRef = useRef<HTMLInputElement>(null);

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
    // Focus must return to the disc or it lands on <body> and the next Tab
    // restarts at the top of the page — the classic menu-close trap.
    discRef.current?.focus();
  };

  // Escape closes, from anywhere — a keyboard user who tabbed back to the disc
  // would otherwise be stuck with an open ring. `defaultPrevented` so that
  // dismissing a tooltip does not also throw the ring away.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (openCat) setOpenCat(null); else close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, openCat]);

  const radius = dialRadius(box, { right, bottom });
  const fits = radius >= DIAL_MIN_R;
  useEffect(() => { if (!fits) { setOpen(false); setOpenCat(null); } }, [fits]);

  const cat = categories.find((c) => c.id === openCat) ?? null;
  const isEntry = cat != null && cat.kind === "entry";

  // What the ring currently holds: the categories, or one category's values.
  const items: Array<{ key: string; face: string; aria: string;
                       icon?: IconName; on?: boolean; press: () => void }> =
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

  const fracs = dialFractions(items.length);

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

  return (
    <div
      ref={rootRef}
      className={`absolute inset-0 pointer-events-none overflow-hidden z-20 ${className}`}
      style={{ touchAction: "none" }}
      data-camera-dial
    >
      {fits && (<>
        {open && (
          <div className="absolute inset-0 pointer-events-auto" aria-hidden
            style={{ touchAction: "none" }} onPointerDown={close} />
        )}

        <div
          className="absolute pointer-events-auto"
          style={{ ...(side === "left" ? { left: right } : { right }), bottom,
                   width: DIAL_DISC_PX, height: DIAL_DISC_PX,
                   touchAction: "none" }}
        >
          {/* Radial scrim behind the ring only: the chips must read over a star
              field, but the frame being judged must not be dimmed. */}
          {open && (
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

          {open && (
            <div role="menu" aria-label={cat ? `${cat.label} values` : label}
              className="absolute inset-0" style={{ touchAction: "none" }}>
              {items.map((it, i) => (
                <div key={it.key} data-dial-slot role="none" style={slot(fracs[i])}>
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
                  one-way door, and Escape alone is not reachable by thumb. */}
              {cat != null && (
                <div style={slot(0)} data-dial-slot role="none">
                  <button type="button" role="menuitem" data-dial-item="__back"
                    aria-label="Back to settings"
                    className="btn tap mono !normal-case rounded-lg"
                    style={{ minWidth: DIAL_ITEM_PX, minHeight: DIAL_ITEM_PX,
                             touchAction: "none" }}
                    onClick={() => setOpenCat(null)}>
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
