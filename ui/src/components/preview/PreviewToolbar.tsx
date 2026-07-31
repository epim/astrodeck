// PreviewToolbar.tsx — always-visible zoom primaries + overlay toggles + download
// (stream T). Spec §5 "Toolbar", §11/§12.
//
//  - Always-visible (>=44px): −, zoom %, +, Fit, 100%. Never collapsed (§ rejected
//    C3 — Fit/100% never go in the overflow sheet).
//  - Toggles (Stars / Clip / Reticle / Center): filled-background active state +
//    a check glyph (NOT color-only — §11.1). Locked honestly when the data can't
//    support them (no stars / NINA clip) via the house `LockedChip`.
//  - Download ▾: FITS only if saved_local (else locked + lock glyph + reason);
//    stretched PNG; raw/lossless PNG (when has_lossless). §12.5 — never a 404.
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

/** Shared chrome for a locked toolbar affordance (LockedChip draws its own lock
 *  glyph, so callers pass only the label). */
const LOCKED_BTN = "btn !px-2.5 text-[11px]";

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
      className={`btn !px-2.5 min-h-11 inline-flex items-center gap-1 text-[11px] ${
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

/** Which annotations are ON. `bahtinov` is opt-OUT (undefined reads as on),
 *  which is why it cannot be tested for truthiness like the others. */
function annotationsOn(overlays: OverlayToggles, rows: PickerOption[]): string[] {
  return rows
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
    document.addEventListener("pointerdown", onDoc);
    return () => document.removeEventListener("pointerdown", onDoc);
  }, [dlOpen]);

  const id = preview?.id;
  const savedLocal = !!preview?.saved_local;
  const hasLossless = !!preview?.has_lossless;
  // §12.5 — never offer a download that will 404. The "Stretched PNG" route
  // (/api/preview/{id}/png) only serves a real PNG when a lossless base is held
  // OR the frame's own bytes are already PNG. For NINA (JPEG, no lossless) it
  // 404s, so gate the menuitem and render the disabled+lock variant when false,
  // mirroring the FITS item's honest-disabled treatment.
  const pngAvailable = hasLossless || preview?.mime === "image/png";
  // /render.png bakes the retained LINEAR array at explicit levels, so it exists
  // only on the linear path (NINA/pre-stretched frames 404). Same capability gate
  // as the client LUT canvas and the clip mask — one truth, honestly disabled.
  const renderAvailable = !!preview?.data_is_linear && !preview?.is_stretched;
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
  const dlDisabled = id == null || linkDown;

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
        <span className="mono text-[11px] text-dim w-12 text-center tabular-nums" aria-live="off">
          {scalePct}%
        </span>
        <button className="btn !px-2.5 min-w-11 min-h-11" aria-label="Zoom in" onClick={onZoomIn}>
          +
        </button>
        {/* min-w-11: at 43.3x44 this was the one primary in the cluster under the
            44px floor on a 390px phone — a rounding miss, but the floor is a
            floor. */}
        <button className="btn !px-2.5 min-w-11 min-h-11 text-[11px]" onClick={onFit}>
          Fit
        </button>
        <button className="btn !px-2.5 min-h-11 text-[11px]" onClick={onHundred} title="100% of the preview image">
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
      <PickerButton
        label="Annotations"
        summary={annotationSummary}
        options={annotationOptions}
        selected={annotationSelected}
        onPick={(id) => toggleOverlay(id)}
        multi
      />

      <span className="flex-1" />

      {/* download */}
      <div className="relative" ref={dlRef}>
        {dlDisabled ? (
          <LockedChip
            reason={linkDown ? "Link down — downloads unavailable" : "No frame to download yet"}
            className={LOCKED_BTN}
          >
            Download
          </LockedChip>
        ) : (
          <button
            type="button"
            className="btn !px-2.5 min-h-11 inline-flex items-center gap-1 text-[11px]"
            aria-haspopup="menu"
            aria-expanded={dlOpen}
            onClick={() => setDlOpen((v) => !v)}
          >
            <Icon name="arrow-down" size={12} /> Download ▾
          </button>
        )}
        {dlOpen && id != null && (
          <div role="menu" className="panel absolute right-0 top-full mt-1 z-50 p-1 w-48 flex flex-col gap-0.5">
            {/* The primary "give me the picture" export: full NATIVE resolution,
                baked server-side at the levels currently on screen. Every other
                item here is either the ≤1400px display encode or the raw FITS. */}
            {renderAvailable ? (
              <a
                role="menuitem"
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
                reason="Full-res export needs linear data — this frame came from NINA already stretched."
                className="btn !justify-start !px-2 text-[11px] w-full"
              >
                Full-res PNG
              </LockedChip>
            )}
            <a
              role="menuitem"
              href={u(`/api/preview/${id}/share.jpg${shareQuery(shareMeta?.target, shareMeta?.subs)}`)}
              download={`firstlight_${id}.jpg`}
              className="btn btn-accent !justify-start !px-2 !py-1.5 text-[11px] inline-flex items-center gap-1"
              onClick={() => setDlOpen(false)}
            >
              <Icon name="capture" size={11} /> Save first light
            </a>
            {pngAvailable ? (
              <a
                role="menuitem"
                href={u(`/api/preview/${id}/png`)}
                download={`preview_${id}.png`}
                className="btn !justify-start !px-2 !py-1.5 text-[11px]"
                onClick={() => setDlOpen(false)}
              >
                Stretched PNG
              </a>
            ) : (
              <LockedChip
                reason="This frame is JPEG-only — no lossless source to export a PNG from."
                className="btn !justify-start !px-2 text-[11px] w-full"
              >
                Stretched PNG
              </LockedChip>
            )}
            {hasLossless && (
              <a
                role="menuitem"
                href={u(`/api/preview/${id}/lossless.png`)}
                download={`preview_${id}_lossless.png`}
                className="btn !justify-start !px-2 !py-1.5 text-[11px]"
                onClick={() => setDlOpen(false)}
              >
                Lossless PNG
              </a>
            )}
            {savedLocal ? (
              <a
                role="menuitem"
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
