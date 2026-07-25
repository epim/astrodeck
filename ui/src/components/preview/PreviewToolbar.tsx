// PreviewToolbar.tsx — always-visible zoom primaries + overlay toggles + download
// (stream T). Spec §5 "Toolbar", §11/§12.
//
//  - Always-visible (>=44px): −, zoom %, +, Fit, 100%. Never collapsed (§ rejected
//    C3 — Fit/100% never go in the overflow sheet).
//  - Toggles (Stars / Clip / Reticle / Center): filled-background active state +
//    a check glyph (NOT color-only — §11.1). Disabled honestly when the data
//    can't support them (no stars / NINA clip) with an inline reason via title.
//  - Download ▾: FITS only if saved_local (else disabled + lock glyph + reason);
//    stretched PNG; raw/lossless PNG (when has_lossless). §12.5 — never a 404.
import { useEffect, useRef, useState } from "react";
import type { OverlayToggles, PreviewInfo, StretchParams } from "../../types";
import { Icon, type IconName } from "../icons";
import { u } from "../../lib/base";
import { shareQuery } from "../../lib/share";
import { isExactWysiwyg, renderPath } from "../../lib/renderLevels";

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
  title?: string;
  onClick: () => void;
}) {
  // Honest disabled (spec §11.8): a defined dim token (--text-dim is AA, >=4.5:1)
  // + a lock glyph + aria-disabled — NOT the native `disabled` attribute (whose
  // .btn:disabled is opacity:0.35, which the spec rejects). We swallow the click.
  return (
    <button
      type="button"
      aria-pressed={disabled ? undefined : on}
      aria-disabled={disabled}
      title={title}
      onClick={disabled ? undefined : onClick}
      className={`btn !px-2.5 min-h-11 inline-flex items-center gap-1 text-[11px] ${
        on && !disabled ? "btn-accent" : ""
      } ${disabled ? "!text-dim cursor-not-allowed" : ""}`}
    >
      {disabled ? <Icon name="lock" size={12} /> : on ? <Icon name="check" size={12} /> : <Icon name={icon} size={12} />}
      {label}
    </button>
  );
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
      {/* zoom cluster — always visible */}
      <div className="flex items-center gap-1">
        <button className="btn !px-2.5 min-w-11 min-h-11" aria-label="Zoom out" onClick={onZoomOut}>
          −
        </button>
        <span className="mono text-[11px] text-dim w-12 text-center tabular-nums" aria-live="off">
          {scalePct}%
        </span>
        <button className="btn !px-2.5 min-w-11 min-h-11" aria-label="Zoom in" onClick={onZoomIn}>
          +
        </button>
        <button className="btn !px-2.5 min-h-11 text-[11px]" onClick={onFit}>
          Fit
        </button>
        <button className="btn !px-2.5 min-h-11 text-[11px]" onClick={onHundred} title="100% of the preview image">
          100%
        </button>
        {/* ADVANCED (§1.4): the sensor-1:1 loupe. Off by default; a novice never
            needs it. "100%" is 100% of the ≤1400px preview — this is 100% of the
            SENSOR, which is a different and much stricter thing. */}
        <Toggle
          on={loupeOn}
          disabled={!loupeAvailable}
          icon="focus"
          label="1:1"
          title={
            loupeAvailable
              ? "1:1 loupe — real sensor pixels at the centre of the view (true focus/noise check)"
              : "1:1 loupe needs linear data — this frame came from NINA"
          }
          onClick={() => onLoupe?.(!loupeOn)}
        />
      </div>

      <span className="w-px h-6 bg-line mx-1 hidden sm:block" aria-hidden />

      {/* overlay toggles */}
      <Toggle
        on={overlays.stars}
        disabled={!starsAvailable}
        icon="align"
        label="Stars"
        title={starsAvailable ? "Toggle star HFR overlay" : "No per-star data for this frame"}
        onClick={() => setOverlays({ stars: !overlays.stars })}
      />
      <Toggle
        on={overlays.clip}
        disabled={!clipAvailable}
        icon="alert"
        label="Clip"
        title={clipAvailable ? "Toggle saturation mask" : "Clip mask needs linear data + known full well"}
        onClick={() => setOverlays({ clip: !overlays.clip })}
      />
      <Toggle
        on={overlays.reticle}
        icon="focus"
        label="Reticle"
        title="Toggle full reticle"
        onClick={() => setOverlays({ reticle: !overlays.reticle })}
      />
      <Toggle
        on={overlays.centerMark}
        icon="capture"
        label="Center"
        title="Toggle center mark"
        onClick={() => setOverlays({ centerMark: !overlays.centerMark })}
      />
      <Toggle
        on={overlays.tilt}
        disabled={!preview?.tilt}
        icon="grid"
        label="Tilt"
        title={preview?.tilt ? "Toggle tilt / aberration heatmap" : "No tilt data for this frame"}
        onClick={() => setOverlays({ tilt: !overlays.tilt })}
      />

      <span className="flex-1" />

      {/* download */}
      <div className="relative" ref={dlRef}>
        <button
          type="button"
          className={`btn !px-2.5 min-h-11 inline-flex items-center gap-1 text-[11px] ${
            dlDisabled ? "!text-dim cursor-not-allowed" : ""
          }`}
          aria-haspopup="menu"
          aria-expanded={dlDisabled ? undefined : dlOpen}
          aria-disabled={dlDisabled}
          title={
            dlDisabled
              ? linkDown
                ? "Link down — downloads unavailable"
                : "No frame to download yet"
              : undefined
          }
          onClick={dlDisabled ? undefined : () => setDlOpen((v) => !v)}
        >
          <Icon name={dlDisabled ? "lock" : "arrow-down"} size={12} /> Download ▾
        </button>
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
              <span
                role="menuitem"
                aria-disabled
                className="btn !justify-start !px-2 !py-1.5 text-[11px] !text-dim cursor-not-allowed inline-flex items-center gap-1"
                title="Full-res export needs linear data — this frame came from NINA already stretched."
              >
                <Icon name="lock" size={11} /> Full-res PNG
              </span>
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
              <span
                role="menuitem"
                aria-disabled
                className="btn !justify-start !px-2 !py-1.5 text-[11px] !text-dim cursor-not-allowed inline-flex items-center gap-1"
                title="This frame is JPEG-only — no lossless source to export a PNG from."
              >
                <Icon name="lock" size={11} /> Stretched PNG
              </span>
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
              <span
                className="btn !justify-start !px-2 !py-1.5 text-[11px] !text-dim cursor-not-allowed inline-flex items-center gap-1"
                aria-disabled
                title="FITS saved on the NINA host — not downloadable here"
              >
                <Icon name="lock" size={11} /> FITS (on host)
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
