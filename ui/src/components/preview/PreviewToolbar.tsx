// PreviewToolbar.tsx — always-visible zoom primaries + overlay toggles + download
// (stream T). Spec §5 "Toolbar", §11/§12.
//
//  - Always-visible (>=44px): −, zoom %, +, Fit, 100%. Never collapsed (§ rejected
//    C3 — Fit/100% never go in the overflow sheet).
//  - Toggles (Stars / Clip / Reticle / Center): filled-background active state +
//    a check glyph (NOT color-only — §11.1). Locked honestly when the data can't
//    support them (no stars / NINA clip) via the house `LockedChip`.
//  - Download ▾: FITS only if saved_local (else locked + lock glyph + reason);
//    stretched PNG; raw/lossless PNG (when has_lossless). §12.5 — never a 404,
//    which means age as well as capability: see DISPLAY_KEEP / LINEAR_KEEP.
//
// FONT-SIZE TRAP — why the `!` on every `!text-[11px]` below is load-bearing.
// `.btn` sets font-size:12px in index.css, and index.css has NO @layer wrapper,
// so `.btn` is UNLAYERED author CSS while Tailwind emits `.text-[11px]` inside
// `@layer utilities`. Unlayered always beats layered, regardless of specificity
// or source order — so a bare `text-[11px]` on a `.btn` is DEAD and the control
// renders 12px. Measured on the shipped bundle: 12px on Fit, 100%, Magnifier
// and Download; 11px only on the zoom readout, the one control here that is not
// a `.btn`. That made this row ~1px per glyph wider than every comment in the
// file assumed, and at 320px it put the "100%" button 0.02px past the cluster's
// content edge, wrapping it to a second row. `!` is the working escape hatch in
// this codebase (Tailwind v4 emits `!important` for it, which is why `!px-2.5`
// has always worked here).
//
// THIS IS SYSTEMIC, and only the width-critical controls in this file are fixed:
// ~59 lines across 20+ files combine `btn` with a bare `text-[11px]` and every
// one of them renders 12px. Correcting them all is a visual change to many
// surfaces and wants its own measured pass — the download-menu items below are
// deliberately left alone for that reason.
//
// EVERY locked surface in this file goes through `LockedChip` (components/ui):
// one dimming token app-wide, tabIndex={0} so a keyboard user lands on it rather
// than tabbing straight past, aria-disabled (never the native `disabled`, which
// strips the element AND its reason out of the a11y tree), an aria-label bearing
// the reason, and a Tooltip so the reason has a TAP path. Before this the reason
// lived only in `title=`, which never fires on touch — on the tablet at the
// scope, which is the primary field device, a locked control was simply dead
// with no stated cause.
//
// The ENABLED controls had the same touch gap for a different reason: they act
// fine, but their `title=` copy ("Magnifier — real sensor pixels at the centre
// of the view … 1:1") was the ONLY place their meaning was written, and a
// fingertip cannot open a title. Two `InfoDot`s — one per group — now give that
// copy a tap-reachable home without adding seven more 44px targets to a
// three-row phone toolbar. See `GroupHelp` for why they carry `mx-4`.
import { useEffect, useRef, useState, type ReactNode } from "react";
import PickerButton, { type PickerOption } from "../ui/PickerButton";
import type { OverlayToggles, PreviewInfo, StretchParams } from "../../types";
import { Icon, type IconName } from "../icons";
import { InfoDot, LockedChip } from "../ui";
import { u } from "../../lib/base";
import { shareQuery } from "../../lib/share";
import { isExactWysiwyg, renderPath } from "../../lib/renderLevels";
import { useLivePreviewId, useStore } from "../../store";

/** Shared chrome for a locked toolbar affordance (LockedChip draws its own lock
 *  glyph, so callers pass only the label). */
const LOCKED_BTN = "btn !px-2.5 !text-[11px]";

// WHAT THE RIG STILL HAS. `hub._trim_previews` enforces three memory caps on a
// Pi: the whole ring entry is dropped once a frame is PREVIEW_DISPLAY_KEEP
// behind the newest (from then on the display bytes, /png, /share.jpg and /fits
// all 404), and the heavy lossless/linear arrays are freed far sooner, at
// PREVIEW_LINEAR_KEEP.
//
// Every capability flag on a PreviewInfo — `has_lossless`, `data_is_linear`,
// `saved_local` — is stamped when the frame was CAPTURED and never revised, so
// a pinned frame went on offering Full-res, Lossless and Stretched PNG long
// after the bytes behind them were freed. An `<a download>` that 404s reports
// nothing at all: the tap just does nothing. Mirroring the two constants here
// is the only way the toolbar can tell; keep them in step with hub.py.
const DISPLAY_KEEP = 8; // hub.PREVIEW_DISPLAY_KEEP
const LINEAR_KEEP = 2; // hub.PREVIEW_LINEAR_KEEP

/* The ENABLED controls had the same touch gap the locked ones had. Every
   `title=` below is still there for a mouse, but a fingertip never fires it —
   so on the tablet at the scope "Magnifier" was a word with no explanation
   anywhere, and "100%" and "1:1" are adjacent homographs to a novice. These two
   InfoDots (the house tap-reachable help affordance: 14px icon, 44px trigger,
   opens on TAP as well as hover and focus) give each group's copy a home that
   survives a finger.

   ONE per group, not one per control: the phone toolbar already wraps to five
   rows at 390px, and seven more 44px triggers would have added rows of pure
   help to a bar whose job is the primaries. Two ride inside the existing rows
   and the row count is unchanged: the before/after phone captures break at the
   same five rows, and the bar measures 5 rows / 242px at 390px and 2 rows at
   1440px. The `mx-4` wrapper is load-bearing —
   InfoDot buys its 44px
   with `-m-[15px] p-[15px]`, so its HIT box overhangs its 14px layout box by
   15px on each side and would otherwise steal the right-hand edge of the
   neighbouring toggle. 16px of margin puts the whole trigger back inside its
   own lane. */
function GroupHelp({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="mx-4 inline-flex items-center">
      <InfoDot label={label} content={children} />
    </span>
  );
}

/** One line of a group-help tooltip: the control's own word, then what it does. */
function HelpLine({ name, children }: { name: string; children: ReactNode }) {
  return (
    <span className="block mb-1 last:mb-0">
      <b className="text-ink">{name}</b> — {children}
    </span>
  );
}

function Toggle({
  on,
  disabled,
  icon,
  label,
  title,
  onClick,
}: {
  on: boolean;
  disabled?: boolean;
  icon: IconName;
  label: string;
  /** doubles as the LOCKED REASON when `disabled` — so always state one */
  title?: string;
  onClick: () => void;
}) {
  if (disabled) {
    return (
      <LockedChip reason={title ?? "Not available for this frame"} className={LOCKED_BTN}>
        {label}
      </LockedChip>
    );
  }
  return (
    <button
      type="button"
      aria-pressed={on}
      title={title}
      onClick={onClick}
      className={`btn !px-2.5 min-h-11 inline-flex items-center gap-1 !text-[11px] ${
        on ? "btn-accent" : ""
      }`}
    >
      {on ? <Icon name="check" size={12} /> : <Icon name={icon} size={12} />}
      {label}
    </button>
  );
}


// The annotation set, as data. Availability carries its REASON, because a
// greyed row that will not say why is the defect house rule §11.8 names.
function annotationRows(
  opts: { starsAvailable: boolean; clipAvailable: boolean; tilt: boolean; bahtinov: boolean },
): PickerOption[] {
  const rows: PickerOption[] = [
    {
      id: "stars", label: "Stars", hint: "a ring per detected star, sized by its HFR",
      disabled: !opts.starsAvailable,
      disabledReason: "No per-star data for this frame",
    },
    {
      id: "clip", label: "Clip", hint: "a mask over pixels that hit full well — blown highlights",
      disabled: !opts.clipAvailable,
      disabledReason: "Clip mask needs linear data + known full well",
    },
    { id: "reticle", label: "Reticle", hint: "a full crosshair across the frame" },
    { id: "centerMark", label: "Center", hint: "a small mark at the exact frame centre" },
    {
      id: "tilt", label: "Tilt", hint: "a corner-to-corner heatmap of star shape — sensor tilt",
      disabled: !opts.tilt,
      disabledReason: "No tilt data for this frame",
    },
  ];
  if (opts.bahtinov) {
    rows.push({
      id: "bahtinov", label: "Spikes",
      hint: "the fitted Bahtinov spike lines and their crossing",
    });
  }
  return rows;
}

/** Which annotations are ON **and drawable on this frame**.
 *
 *  The stored preference and the overlay actually being drawn are two different
 *  facts, and this helper used to report the first while claiming to report the
 *  second. Turn Stars on (it persists to localStorage, store.ts persistPreview)
 *  and then let a frame arrive with no star list — a NINA frame, cloud, a
 *  detection that found nothing — and PreviewStage draws no rings at all
 *  (it gates on `starsAvailable`), while this returned "stars" anyway. The
 *  button then read "2 on" with exactly one overlay on screen, and the picker
 *  row rendered ENGAGED AND GREYED at once: aria-selected="true" plus the `•`
 *  bullet on top of opacity-40 + aria-disabled, a selection assertion about an
 *  overlay the app was refusing to render. It could not even be cleared while
 *  the data stayed away — PickerButton blocks a disabled row before `onPick`,
 *  so the tap only toasted the reason and the bullet stayed — and because the
 *  preference persists, a NINA user saw it stuck across reloads.
 *
 *  So: a row the frame cannot support reads OFF, in the summary and in the
 *  list, exactly as `Toggle` above already does for the Magnifier (locked chip,
 *  no engaged look). Nothing is written to the store, so the preference is
 *  intact and the row lights up again on the first frame that carries the data.
 *
 *  `bahtinov` is opt-OUT (undefined reads as on), which is why it cannot be
 *  tested for truthiness like the others; it is never `disabled` — the row only
 *  exists when `preview.bahtinov.geom` does — so it is unaffected by the filter.
 */
function annotationsOn(overlays: OverlayToggles, rows: PickerOption[]): string[] {
  return rows
    .filter((r) => !r.disabled)
    .filter((r) => (r.id === "bahtinov"
      ? (overlays as unknown as Record<string, unknown>).bahtinov !== false
      : !!(overlays as unknown as Record<string, unknown>)[r.id]))
    .map((r) => r.id);
}

export function PreviewToolbar({
  preview,
  overlays,
  setOverlays,
  scalePct,
  onZoomIn,
  onZoomOut,
  onFit,
  onHundred,
  starsAvailable,
  clipAvailable,
  linkDown,
  shareMeta,
  stretch,
  loupeOn = false,
  loupeAvailable = false,
  onLoupe,
}: {
  preview: PreviewInfo | null;
  overlays: OverlayToggles;
  setOverlays: (o: Partial<OverlayToggles>) => void;
  scalePct: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  onHundred: () => void;
  starsAvailable: boolean;
  clipAvailable: boolean; // data_is_linear && full_well != null
  linkDown: boolean;
  shareMeta?: { target?: string; subs?: number };
  /** current client stretch — needed to bake /render.png at the levels on screen */
  stretch: StretchParams;
  /** advanced 1:1 loupe (state lives in PreviewStage — Decision F) */
  loupeOn?: boolean;
  loupeAvailable?: boolean;
  onLoupe?: (v: boolean) => void;
}) {
  const [dlOpen, setDlOpen] = useState(false);
  const dlRef = useRef<HTMLDivElement>(null);
  const dlBtnRef = useRef<HTMLButtonElement>(null);
  /** Was focus inside the download region when it last moved? Read only on the
   *  path below where the trigger unmounts under the user. */
  const dlFocusRef = useRef(false);
  // Self-subscribed rather than passed down (SnrChip's idiom): the toolbar's
  // caller has no reason to know about the server's frame ring, and a blocked
  // annotation row has to be able to say so somewhere the user is looking.
  const showToast = useStore((s) => s.showToast);
  const liveId = useLivePreviewId();

  // Annotations, as one picker. `bahtinov` is opt-OUT (undefined reads as on),
  // so the toggle cannot be a plain boolean flip like the others.
  const annotationOptions = annotationRows({
    starsAvailable,
    clipAvailable,
    tilt: !!preview?.tilt,
    bahtinov: !!preview?.bahtinov?.geom,
  });
  const annotationSelected = annotationsOn(overlays, annotationOptions);
  const annotationSummary =
    annotationSelected.length === 0
      ? "none"
      : annotationSelected.length === 1
        ? (annotationOptions.find((o) => o.id === annotationSelected[0])?.label ?? "1 on")
        : `${annotationSelected.length} on`;
  const toggleOverlay = (id: string) => {
    if (id === "bahtinov") {
      setOverlays({ bahtinov: overlays.bahtinov === false } as Partial<OverlayToggles>);
      return;
    }
    const cur = (overlays as unknown as Record<string, boolean>)[id];
    setOverlays({ [id]: !cur } as Partial<OverlayToggles>);
  };

  useEffect(() => {
    if (!dlOpen) return;
    const onDoc = (e: Event) => {
      if (dlRef.current && !dlRef.current.contains(e.target as Node)) setDlOpen(false);
    };
    // Escape closes it and hands focus back to the trigger. This was the one
    // popover in the app that Escape would not close — PickerButton and Tooltip
    // both pair the outside-press listener with a key listener, and a keyboard
    // user who opened this had no way out except tabbing through every item.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setDlOpen(false);
      dlBtnRef.current?.focus();
    };
    document.addEventListener("pointerdown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [dlOpen]);

  const id = preview?.id;
  const savedLocal = !!preview?.saved_local;
  const hasLossless = !!preview?.has_lossless;
  // How far behind the newest frame this one is — the second half of "will this
  // download work", next to the capture-time capability flags. `liveId` is the
  // newest frame THIS browser has seen, so a client that missed frames can only
  // ever UNDER-estimate the gap: it may still offer a download that 404s, but it
  // can never hide one that would have worked.
  const behind = id != null && liveId != null ? Math.max(0, liveId - id) : 0;
  const entryDropped = behind >= DISPLAY_KEEP;
  const heavyFreed = behind >= LINEAR_KEEP;
  /** The reason a full-quality export is gone, in the user's terms. */
  const heavyFreedReason = (what: string) =>
    `${what} needs the full-quality copy of frame #${id}, and the rig keeps that ` +
    `for the latest ${LINEAR_KEEP} frames only — this one is ${behind} frames back. ` +
    `Return to Live to export the current frame.`;
  // §12.5 — never offer a download that will 404. The "Stretched PNG" route
  // (/api/preview/{id}/png) only serves a real PNG when a lossless base is still
  // held OR the frame's own bytes are already PNG. For NINA (JPEG, no lossless)
  // it 404s from the start, and for a linear frame it starts 404-ing once the
  // lossless base is freed — so gate on both, and render the disabled+lock
  // variant with whichever reason applies.
  const pngCapable = hasLossless || preview?.mime === "image/png";
  const pngAvailable = (hasLossless && !heavyFreed) || preview?.mime === "image/png";
  // /render.png bakes the retained LINEAR array at explicit levels, so it exists
  // only on the linear path (NINA/pre-stretched frames 404) and only while that
  // array is still held. Same capability gate as the client LUT canvas and the
  // clip mask — one truth, honestly disabled.
  const renderCapable = !!preview?.data_is_linear && !preview?.is_stretched;
  const renderAvailable = renderCapable && !heavyFreed;
  // WYSIWYG honesty (Decision A1): in Auto with neutral Brightness the server
  // reproduces the on-screen stretch EXACTLY (it replays preview.auto_levels).
  // In Manual — or Auto with a Brightness nudge — the on-screen image is a
  // composition (auto-stretch, then a display-domain curve) and the server's
  // single linear pass can only match it very closely. We say so; we do not
  // claim pixel-identity we cannot deliver.
  const renderExact = isExactWysiwyg(stretch);
  const renderTitle = renderExact
    ? "The image exactly as you see it, at full sensor resolution."
    : "Full sensor resolution at your current levels. Your Manual stretch is baked as a very close match — not pixel-identical to the screen.";
  // UX-49: honest-disabled for the Download control (the file's own §11.8 rule —
  // dim token + lock + title, not native `disabled` which greys with no reason).
  // Once the ring entry is gone EVERY item behind this button 404s — including
  // "Save first light" and FITS, which have no capability flag of their own —
  // so the honest place to say so is the button, not five separate rows.
  const dlDisabled = id == null || linkDown || entryDropped;
  const dlReason = linkDown
    ? "Link down — downloads unavailable"
    : entryDropped
      ? `Frame #${id} is no longer on the rig: only the last ${DISPLAY_KEEP} frames stay in memory, ` +
        `and this one is ${behind} back. Return to Live, or open the saved sub from the Gallery.`
      : "No frame to download yet";

  // A DISCLOSURE CANNOT OUTLIVE ITS OWN TRIGGER. `dlDisabled` flips the moment
  // the link drops or the pinned frame falls DISPLAY_KEEP behind, and the
  // trigger is then replaced by a LockedChip whose reason says the downloads
  // are unavailable — but the panel below it kept rendering, because its render
  // was gated on `dlOpen && id != null` and never on `dlDisabled`. On the
  // link-down path (the instant, far more reachable one — `behind` is normally
  // 0 there) EVERY row stayed a live `<a download>`, so a locked control sat
  // directly on top of the offers it had just withdrawn, and tapping one did
  // nothing whatsoever: see the note at the top of this file — an `<a download>`
  // that 404s reports nothing at all.
  //
  // The render below is gated on `!dlDisabled` so that contradiction cannot be
  // painted for even one frame; the STATE is cleared here so the panel does not
  // spring back open by itself when the link returns.
  useEffect(() => {
    if (!dlDisabled) return;
    setDlOpen(false);
    // The trigger just unmounted, possibly under the user's focus, which leaves
    // focus on <body> — the keyboard user is dropped at the top of the document
    // with no idea why the control vanished. Hand focus to the LockedChip that
    // replaced it: it is focusable for exactly this purpose (tabIndex=0, the
    // reason in its aria-label, and a tooltip so the reason has a tap path).
    // Guarded twice so this can never STEAL focus: only when focus was ours,
    // and only when it is now nowhere.
    if (!dlFocusRef.current) return;
    const active = document.activeElement as HTMLElement | null;
    // `isConnected` as well as the body check: browsers differ on whether
    // removing the focused node resets `activeElement` to <body> or leaves it
    // pointing at the detached element. Both readings mean the same thing here.
    if (active && active !== document.body && active.isConnected) return;
    dlRef.current?.querySelector<HTMLElement>('[role="button"]')?.focus();
  }, [dlDisabled]);

  return (
    <div className="preview-toolbar">
      {/* zoom cluster — always visible.
          `flex-wrap` is load-bearing, not cosmetic. `.preview-toolbar` itself
          wraps, but a non-wrapping child is ONE unbreakable flex item whose
          min-content is the SUM of its buttons (372px here). That became the
          Live Preview panel's min-content, and since the view's grid track is
          `1fr` — i.e. `minmax(auto, 1fr)`, whose floor is min-content — the
          single mobile column was forced to 406px inside a 348px space. `main`
          is `overflow-x-hidden`, so the excess was CLIPPED rather than
          scrollable: on a 380px phone the Magnifier toggle, Center and
          Download simply could not be reached, and every sibling panel
          (Exposure, Cooler, Filter Wheel) inherited the blown-out track. */}
      <div className="flex flex-wrap items-center gap-1">
        <button className="btn !px-2.5 min-w-11 min-h-11" aria-label="Zoom out" onClick={onZoomOut}>
          −
        </button>
        {/* w-10, not w-12. Combined with the `!` fix above this buys the row
            ~11px of slack at 320px; the `!` alone left 2.98px, which one font
            metric change would eat again. Safe against the widest reading the
            control can produce: usePreviewGestures caps zoom at MAX_SCALE_MULT
            = 8, so the string is at most 4-5 characters, and "1600%" in IBM
            Plex Mono at 11px measures ~33px inside the 40px box. This span is
            NOT a .btn, so its 11px was always real. */}
        <span className="mono text-[11px] text-dim w-10 text-center tabular-nums" aria-live="off">
          {scalePct}%
        </span>
        <button className="btn !px-2.5 min-w-11 min-h-11" aria-label="Zoom in" onClick={onZoomIn}>
          +
        </button>
        {/* min-w-11: at 43.3x44 this was the one primary in the cluster under the
            44px floor on a 390px phone — a rounding miss, but the floor is a
            floor. */}
        <button className="btn !px-2.5 min-w-11 min-h-11 !text-[11px]" onClick={onFit}>
          Fit
        </button>
        <button className="btn !px-2.5 min-h-11 !text-[11px]" onClick={onHundred} title="100% of the preview image">
          100%
        </button>
        {/* ADVANCED (§1.4): the sensor-1:1 loupe. Off by default; a novice never
            needs it. "100%" is 100% of the ≤1400px preview — this is 100% of the
            SENSOR, which is a different and much stricter thing. */}
        {/* "1:1" and "100%" are adjacent homographs to a novice — this one is
            100% of the SENSOR, so give it a name instead of a ratio. */}
        <Toggle
          on={loupeOn}
          disabled={!loupeAvailable}
          icon="focus"
          label="Magnifier"
          title={
            loupeAvailable
              ? "Magnifier — real sensor pixels at the centre of the view (the true focus/noise check; 1:1)"
              : "The magnifier needs linear data — this frame came from NINA"
          }
          onClick={() => onLoupe?.(!loupeOn)}
        />
        <GroupHelp label="About the zoom and magnifier controls">
          <HelpLine name="Fit">scales the whole frame into the panel.</HelpLine>
          <HelpLine name="100%">100% of the PREVIEW image, which is downscaled to 1400px on its long edge.</HelpLine>
          <HelpLine name="Magnifier">real sensor pixels at the centre of the view — the true focus/noise check (1:1). Needs linear data.</HelpLine>
        </GroupHelp>
      </div>

      <span className="w-px h-6 bg-line mx-1 hidden sm:block" aria-hidden />

      {/* Annotations — ONE picker, not six permanent switches.
          Six 44px toggles plus a help legend is most of a 300px rail, and the
          set is consulted far less often than it was displayed. The button
          states how many are on, so the glance still works; the options open
          on demand and the per-overlay explanations ride each row's title
          instead of a legend that was always on screen. */}
      {/* onBlocked is why the per-row `disabledReason` above is worth writing.
          Without it a dimmed row gave a tap NOTHING — no bullet, no toast, not
          even a press depression — and the reason reached a mouse hover only.
          Both CaptureView call sites already wire it this way. */}
      <PickerButton
        label="Annotations"
        summary={annotationSummary}
        options={annotationOptions}
        selected={annotationSelected}
        onPick={(id) => toggleOverlay(id)}
        onBlocked={(r) => showToast("warning", r)}
        multi
      />

      <span className="flex-1" />

      {/* download */}
      {/* onFocus/onBlur, not a focus poll: React delegates these from the
          BUBBLING focusin/focusout, so the flag tracks focus anywhere in the
          region — trigger, panel links, locked rows — and stays true when the
          focused node is REMOVED (removal fires no blur), which is precisely
          the case the effect above needs to recognise. */}
      <div
        className="relative"
        ref={dlRef}
        onFocus={() => { dlFocusRef.current = true; }}
        onBlur={() => { dlFocusRef.current = false; }}
      >
        {dlDisabled ? (
          <LockedChip reason={dlReason} className={LOCKED_BTN}>
            Download
          </LockedChip>
        ) : (
          <button
            ref={dlBtnRef}
            type="button"
            className="btn !px-2.5 min-h-11 inline-flex items-center gap-1 !text-[11px]"
            aria-expanded={dlOpen}
            onClick={() => setDlOpen((v) => !v)}
          >
            <Icon name="arrow-down" size={12} /> Download ▾
          </button>
        )}
        {/* A DISCLOSURE, not a menu. It used to declare role="menu" with
            role="menuitem" children, which promises the ARIA menu keyboard
            model — roving focus, arrow keys, Home/End, type-ahead — none of
            which is implemented here; and two of the rows are LockedChips,
            which are not menuitems at all. What this actually is: a group of
            download links and a few honest stand-ins for the ones that are
            unavailable. Links are focusable and Tab-navigable natively, so
            saying that plainly is both true and usable. */}
        {dlOpen && !dlDisabled && id != null && (
          <div
            role="group"
            aria-label={`Download frame ${id}`}
            className="panel absolute right-0 top-full mt-1 z-50 p-1 w-48 flex flex-col gap-0.5"
          >
            {/* The primary "give me the picture" export: full NATIVE resolution,
                baked server-side at the levels currently on screen. Every other
                item here is either the ≤1400px display encode or the raw FITS. */}
            {renderAvailable ? (
              <a
                href={u(renderPath(id, stretch, preview))}
                download={`astrodeck_${id}.png`}
                title={renderTitle}
                className="btn !justify-start !px-2 !py-1.5 text-[11px] inline-flex items-center gap-1"
                onClick={() => setDlOpen(false)}
              >
                <Icon name="download" size={11} /> Full-res PNG
              </a>
            ) : (
              <LockedChip
                reason={renderCapable
                  ? heavyFreedReason("A full-res export")
                  : "Full-res export needs linear data — this frame came from NINA already stretched."}
                className="btn !justify-start !px-2 text-[11px] w-full"
              >
                Full-res PNG
              </LockedChip>
            )}
            <a
              href={u(`/api/preview/${id}/share.jpg${shareQuery(shareMeta?.target, shareMeta?.subs)}`)}
              download={`firstlight_${id}.jpg`}
              className="btn btn-accent !justify-start !px-2 !py-1.5 text-[11px] inline-flex items-center gap-1"
              onClick={() => setDlOpen(false)}
            >
              <Icon name="capture" size={11} /> Save first light
            </a>
            {pngAvailable ? (
              <a
                href={u(`/api/preview/${id}/png`)}
                download={`preview_${id}.png`}
                className="btn !justify-start !px-2 !py-1.5 text-[11px]"
                onClick={() => setDlOpen(false)}
              >
                Stretched PNG
              </a>
            ) : (
              <LockedChip
                reason={pngCapable
                  ? heavyFreedReason("A PNG export")
                  : "This frame is JPEG-only — no lossless source to export a PNG from."}
                className="btn !justify-start !px-2 text-[11px] w-full"
              >
                Stretched PNG
              </LockedChip>
            )}
            {hasLossless && (heavyFreed ? (
              <LockedChip
                reason={heavyFreedReason("The lossless copy")}
                className="btn !justify-start !px-2 text-[11px] w-full"
              >
                Lossless PNG
              </LockedChip>
            ) : (
              <a
                href={u(`/api/preview/${id}/lossless.png`)}
                download={`preview_${id}_lossless.png`}
                className="btn !justify-start !px-2 !py-1.5 text-[11px]"
                onClick={() => setDlOpen(false)}
              >
                Lossless PNG
              </a>
            ))}
            {savedLocal ? (
              <a
                href={u(`/api/preview/${id}/fits`)}
                download={`preview_${id}.fits`}
                className="btn !justify-start !px-2 !py-1.5 text-[11px]"
                onClick={() => setDlOpen(false)}
              >
                FITS
              </a>
            ) : (
              <LockedChip
                reason="FITS saved on the NINA host — not downloadable here"
                className="btn !justify-start !px-2 text-[11px] w-full"
              >
                FITS (on host)
              </LockedChip>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
