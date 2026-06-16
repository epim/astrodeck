// PreviewStage.tsx — the shared, embeddable stage (stream S).
// (spec §2 render strategies, §5 stage states, §7 transform, §8 night, §10 reuse)
//
// Responsibilities:
//  - Double-buffer image swap with NO blank: two stacked <img> buffers; the new
//    one decodes underneath, then we cross-fade (idle/paused or exposure>=3s) or
//    snap (fast loops) — decisions #17. A newer frame cancels an in-flight fade.
//  - One shared `.preview-transform` layer (translate+scale, origin 0 0) holding
//    the image buffer(s) + an overlay <svg> so overlays stay pixel-aligned (kills
//    the misaligned-crosshair bug — §7).
//  - Source split: NINA / pre-stretched => <img> of display bytes; linear =>
//    <canvas> LUT remap via useImageRemap (client stretch, zero round-trip).
//  - Night tint via the shared `.astro-surface` CSS filter on whichever element
//    is the active buffer — ONE mechanism for both paths (§8, perceptual #3).
//  - Empty / loading / pinned / stale states (§5).
//
// The stage is "dumb" about the store: the caller passes the frame + viewport +
// stretch + overlays + actions. LivePreview wires it to the store; Focus passes
// a `compact` variant.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { OverlayToggles, PreviewInfo, StarMark, StretchParams, Viewport } from "../../types";
import { Icon } from "../icons";
import Logo from "../Logo";
import { usePreviewGestures } from "./usePreviewGestures";
import { useImageRemap } from "./useImageRemap";
import { Reticle } from "./Reticle";
import { ScaleBar } from "./ScaleBar";
import { StarOverlay } from "./StarOverlay";
import { ClipMaskLayer } from "./ClipMaskLayer";

interface Props {
  preview: PreviewInfo | null;
  viewport: Viewport;
  setViewport: (v: Partial<Viewport>) => void;
  stretch: StretchParams;
  overlays: OverlayToggles;
  hfrGood: number;
  hfrWarn: number;
  night: boolean;
  linkDown: boolean;
  pinned: boolean;
  newSincePinned: number;
  onReturnToLive: () => void;
  stretchDragging?: boolean;
  compact?: boolean;
  // expose gesture controls to a parent toolbar
  onControls?: (c: { fit: () => void; hundred: () => void; zoomIn: () => void; zoomOut: () => void }) => void;
}

const FADE_MS = 120;
const FADE_MIN_EXPOSURE_S = 3;

// gate the cross-fade behind prefers-reduced-motion (spec §11.4 -> static snap).
function prefersReducedMotion(): boolean {
  return typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function PreviewStage(props: Props) {
  const {
    preview,
    viewport,
    setViewport,
    stretch,
    overlays,
    hfrGood,
    hfrWarn,
    linkDown,
    pinned,
    newSincePinned,
    onReturnToLive,
    stretchDragging = false,
    compact = false,
    onControls,
  } = props;

  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [stageSize, setStageSize] = useState({ w: 0, h: 0 });
  const [selectedStar, setSelectedStar] = useState<StarMark | null>(null);
  const [decimated, setDecimated] = useState<{ shown: number; total: number } | null>(null);
  // P3-9: the stage is keyboard-interactive (tabIndex + arrow-pan/+/-/0/1) but
  // role="img" hid that from AT. We use role="group" and announce the zoom % via a
  // visually-hidden polite live region, but ONLY after a keyboard interaction
  // (gated by kbInteracted) so frame swaps/pan-by-pointer don't spam the SR.
  const [zoomMsg, setZoomMsg] = useState("");
  const kbInteracted = useRef(false);

  const isNina = !!preview?.is_stretched;
  // P3-2 (client half): `_image_dims` returns 0 on a PIL decode failure, and 0 is
  // not nullish — so `??` would keep a 0-wide invisible stage. Use a FALSY fallback
  // so display_width=0 falls back to data_width (then 0). Same for height.
  const dispW = preview?.display_width || preview?.data_width || 0;
  const dispH = preview?.display_height || preview?.data_height || 0;

  // ----- measure stage -----
  useLayoutEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0].contentRect;
      setStageSize({ w: cr.width, h: cr.height });
    });
    ro.observe(el);
    setStageSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // fit scale: image fits the stage (object-contain)
  const fitScale = useMemo(() => {
    if (!dispW || !dispH || !stageSize.w || !stageSize.h) return 1;
    return Math.min(stageSize.w / dispW, stageSize.h / dispH);
  }, [dispW, dispH, stageSize.w, stageSize.h]);

  const geom = useMemo(
    () => ({ cw: stageSize.w, ch: stageSize.h, iw: dispW, ih: dispH, fitScale }),
    [stageSize.w, stageSize.h, dispW, dispH, fitScale],
  );

  const { onKeyDown, setFit, setHundred, zoomAt, zoomedIn } = usePreviewGestures(
    stageRef,
    viewport,
    setViewport,
    geom,
  );

  // P3-9: wrap the gesture key handler so any keyboard zoom/pan arms an SR
  // announcement; the effect below reads the resulting viewport and updates the
  // polite live region with the new zoom % (matching the visible toolbar readout).
  const onStageKeyDown = (e: React.KeyboardEvent) => {
    kbInteracted.current = true;
    onKeyDown(e);
  };
  useEffect(() => {
    if (!kbInteracted.current) return;
    kbInteracted.current = false; // consume: one keypress arms one announcement
    setZoomMsg(`Zoom ${Math.round((viewport.scale || 1) * 100)}%`);
  }, [viewport.scale, viewport.x, viewport.y]);

  // when the frame changes size or first appears, snap to Fit so it's centered.
  // P3-3: ALSO re-apply Fit on a container resize (stageSize/fitScale change) while
  // viewport.fit===true, so a phone rotate / panel reflow re-centers instead of
  // leaving the image oversized/off-center. We only preserve the stored viewport
  // across a resize when fit===false (the user has actively zoomed/panned).
  const lastFitKey = useRef("");
  useEffect(() => {
    if (!dispW || !dispH || fitScale <= 0) return;
    const key = `${dispW}x${dispH}`;
    const newFrame = key !== lastFitKey.current;
    if (newFrame) {
      lastFitKey.current = key;
      setViewport({ scale: fitScale, x: 0, y: 0, fit: true });
      return;
    }
    // same frame dims but the stage (or derived fitScale) changed: snap back to
    // fit only if we're in fit mode and the current viewport drifted from it.
    if (viewport.fit && (viewport.scale !== fitScale || viewport.x !== 0 || viewport.y !== 0)) {
      setViewport({ scale: fitScale, x: 0, y: 0, fit: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dispW, dispH, fitScale, stageSize.w, stageSize.h, viewport.fit]);

  // expose controls to the parent toolbar
  useEffect(() => {
    onControls?.({
      fit: setFit,
      hundred: setHundred,
      zoomIn: () => zoomAt(viewport.scale * 1.25, stageSize.w / 2, stageSize.h / 2),
      zoomOut: () => zoomAt(viewport.scale / 1.25, stageSize.w / 2, stageSize.h / 2),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setFit, setHundred, zoomAt, viewport.scale, stageSize.w, stageSize.h]);

  // ----- double buffer (NINA / display <img> path) -----
  // ALWAYS keep the old front visible until the new image has DECODED in the back
  // buffer, then promote (no blank, ever — decisions #17). On a slow loop / pause
  // / long exposure we cross-fade; on a fast loop we snap (0ms fade). A newer
  // frame arriving mid-fade cancels the in-flight one by replacing backUrl.
  const displayUrl = preview ? `/api/preview/${preview.id}` : null;
  const [frontUrl, setFrontUrl] = useState<string | null>(displayUrl);
  const [backUrl, setBackUrl] = useState<string | null>(null);
  const [backVisible, setBackVisible] = useState(false);
  const [fadeOn, setFadeOn] = useState(true);
  const fadeTimer = useRef<number | null>(null);

  // Lane B — track the frame URL whose <img> failed to decode (404 / corrupt /
  // pruned on the server). When the ACTIVE frame's url is the broken one we render
  // the logo empty state instead of the browser's broken-image glyph. Reset on
  // every new frame so a later good frame recovers automatically.
  const [brokenUrl, setBrokenUrl] = useState<string | null>(null);
  useEffect(() => {
    setBrokenUrl(null);
  }, [displayUrl]);

  useEffect(() => {
    if (!displayUrl) return;
    if (displayUrl === frontUrl || displayUrl === backUrl) return;
    const exposure = preview?.exposure_s ?? 0;
    setFadeOn((exposure >= FADE_MIN_EXPOSURE_S || pinned) && !prefersReducedMotion());
    // load under the front; promote on decode (onLoad). Replacing backUrl mid-
    // flight cancels any pending fade for the older frame.
    setBackVisible(false);
    setBackUrl(displayUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displayUrl]);

  const onBackLoaded = () => {
    if (!backUrl) return;
    // reveal the decoded back image (opacity transition handles the cross-fade;
    // 0ms duration => instant snap). Then promote it to the front and drop back.
    setBackVisible(true);
    if (fadeTimer.current != null) clearTimeout(fadeTimer.current);
    fadeTimer.current = window.setTimeout(
      () => {
        setFrontUrl(backUrl);
        setBackUrl(null);
        setBackVisible(false);
        fadeTimer.current = null;
      },
      fadeOn ? FADE_MS + 20 : 0,
    );
  };

  useEffect(() => () => { if (fadeTimer.current != null) clearTimeout(fadeTimer.current); }, []);

  // ----- linear path canvas remap -----
  const linearEnabled = !!preview && !isNina && preview.data_is_linear;
  useImageRemap(canvasRef, linearEnabled ? displayUrl : null, stretch, linearEnabled, stretchDragging);

  // displayScale: display px per data px (for star coords)
  const displayScale = preview && preview.data_width ? dispW / preview.data_width : 1;

  const clipActive =
    !!preview &&
    preview.data_is_linear &&
    preview.full_well != null &&
    overlays.clip &&
    preview.stats.max >= preview.full_well;

  const starsAvailable = !!preview?.star_list && preview.star_list.length > 0;

  // NINA / pre-stretched path: Brightness/Contrast are display-only and MUST
  // visibly act on the rendered <img> (honesty rule #6 — no fake control). We
  // chain them onto the shared night tint (.astro -> var(--img-filter)) inline so
  // both effects compose. brightness/contrast in [-1,1] -> CSS [0.5,1.5].
  const ninaFilter =
    isNina && (stretch.brightness !== 0 || stretch.contrast !== 0)
      ? `var(--img-filter) brightness(${(1 + stretch.brightness * 0.5).toFixed(3)}) contrast(${(1 + stretch.contrast * 0.5).toFixed(3)})`
      : undefined;

  const transform = `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.scale})`;

  // The <img> path can fail to decode (404 / pruned / corrupt). When the frame the
  // stage is currently trying to paint is the broken one, treat it like "no frame"
  // so we show the logo placeholder instead of a broken-image glyph.
  const imgPath = !preview || isNina || !preview.data_is_linear;
  const activeImgUrl = frontUrl ?? displayUrl;
  const imgBroken = imgPath && brokenUrl != null && brokenUrl === activeImgUrl;

  // ----- empty / placeholder state -----
  // Shown when there is no frame at all, or the only frame we have cannot be
  // displayed. Always renders the AstroDeck logo centered on the dark stage — never
  // a broken-image icon (Lane B).
  if (!preview || imgBroken) {
    const caption = !preview ? "No capture yet" : "Capture unavailable";
    return (
      <div
        ref={stageRef}
        className="preview-stage astro-surface relative w-full overflow-hidden flex items-center justify-center"
        style={{ aspectRatio: compact ? "3 / 2" : undefined, minHeight: compact ? undefined : 380 }}
      >
        <div className="flex flex-col items-center text-center">
          <Logo size={compact ? 56 : 80} className="text-dim opacity-60" />
          <div className="mt-3 text-dim text-xs tracking-[0.3em] uppercase">{caption}</div>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={stageRef}
      tabIndex={0}
      role="group"
      aria-label={`Live preview, frame ${preview.id}. Arrow keys pan; plus and minus zoom; 0 fits; 1 is 100%.`}
      onKeyDown={onStageKeyDown}
      className="preview-stage astro-surface relative w-full overflow-hidden outline-none"
      style={{
        aspectRatio: compact ? "3 / 2" : undefined,
        minHeight: compact ? undefined : 380,
        touchAction: zoomedIn ? "none" : "pan-y",
      }}
    >
      {/* P3-9: polite SR announcement of zoom % on keyboard zoom/pan */}
      <span className="sr-only" aria-live="polite">
        {zoomMsg}
      </span>
      {/* shared transform layer — image buffers + overlay svg pan/zoom together */}
      <div
        className="preview-transform absolute top-0 left-0"
        style={{ transform, transformOrigin: "0 0", width: dispW, height: dispH }}
      >
        {isNina || !preview.data_is_linear ? (
          <>
            {/* front buffer (current frame) stays painted until back decodes */}
            <img
              src={frontUrl ?? displayUrl ?? undefined}
              alt={`frame ${preview.id}`}
              onError={() => setBrokenUrl(frontUrl ?? displayUrl ?? null)}
              className="astro absolute top-0 left-0"
              style={{ width: dispW, height: dispH, filter: ninaFilter }}
            />
            {/* back buffer decodes ON TOP; fade-in (slow loop) or instant promote
                (fast loop). onLoad reveals + promotes it → no blank ever. */}
            {backUrl && (
              <img
                key={backUrl}
                src={backUrl}
                alt=""
                onLoad={onBackLoaded}
                onError={() => {
                  // a broken incoming frame must not blank the (good) front buffer
                  // or get promoted; just drop the back buffer and keep the front.
                  setBackUrl(null);
                  setBackVisible(false);
                }}
                className="astro absolute top-0 left-0"
                style={{
                  width: dispW,
                  height: dispH,
                  opacity: backVisible ? 1 : 0,
                  transition: fadeOn ? `opacity ${FADE_MS}ms ease` : "none",
                  filter: ninaFilter,
                }}
              />
            )}
          </>
        ) : (
          // linear path: client-LUT canvas
          <canvas
            ref={canvasRef}
            className="astro absolute top-0 left-0"
            style={{ width: dispW, height: dispH }}
          />
        )}

        {/* overlays in the same transform space (perfect alignment) */}
        <svg
          className="absolute top-0 left-0 pointer-events-none"
          width={dispW}
          height={dispH}
          viewBox={`0 0 ${dispW} ${dispH}`}
          style={{ overflow: "visible" }}
        >
          <ClipMaskLayer w={dispW} h={dispH} active={clipActive} />
          {overlays.stars && starsAvailable && (
            <g style={{ pointerEvents: "auto" }}>
              <StarOverlay
                stars={preview.star_list!}
                displayScale={displayScale}
                scale={viewport.scale}
                hfrGood={hfrGood}
                hfrWarn={hfrWarn}
                selectedKey={selectedStar ? `${selectedStar.x},${selectedStar.y}` : null}
                onSelect={setSelectedStar}
                onDecimated={(shown, total) =>
                  setDecimated((d) => (d?.shown === shown && d?.total === total ? d : { shown, total }))
                }
              />
            </g>
          )}
          <Reticle w={dispW} h={dispH} centerMark={overlays.centerMark} reticle={overlays.reticle} />
        </svg>
      </div>

      {/* ---- screen-space chrome (not transformed) ---- */}

      {/* scale bar (top-right) — hidden honestly when plate scale unknown */}
      {!compact && (
        <div className="absolute top-2 right-2">
          <ScaleBar
            pixelScaleArcsec={preview.pixel_scale_arcsec}
            dataWidth={preview.data_width}
            displayWidth={dispW}
            scale={viewport.scale}
          />
        </div>
      )}

      {/* NINA source badge */}
      {isNina && (
        <div className="absolute top-2 left-2 preview-chip flex items-center gap-1" aria-live="polite">
          <Icon name="info" size={11} /> Source: NINA (display-only)
        </div>
      )}
      {preview.bayer_pattern && (
        <div className="absolute top-2 left-2 mt-7 preview-chip">Mono preview of OSC frame</div>
      )}

      {/* selected star readout */}
      {selectedStar && (
        <div className="absolute bottom-2 left-2 preview-chip mono" aria-live="polite">
          HFR {selectedStar.hfr.toFixed(2)} px
          {preview.pixel_scale_arcsec != null && ` · ${(selectedStar.hfr * preview.pixel_scale_arcsec).toFixed(2)}″`}
        </div>
      )}

      {/* decimation disclosure */}
      {overlays.stars && starsAvailable && decimated && decimated.shown < decimated.total && (
        <div className="absolute bottom-2 right-2 preview-chip">
          Showing {decimated.shown}/{decimated.total} stars
        </div>
      )}

      {/* pinned banner */}
      {pinned && (
        <div className="absolute inset-0 flex items-start justify-center pointer-events-none">
          <div className="absolute inset-0 bg-black/35" />
          <div className="relative mt-3 panel px-3 py-2 flex items-center gap-3 pointer-events-auto" aria-live="polite">
            <span className="text-xs text-ink">
              Viewing frame #{preview.id}
              {newSincePinned > 0 && <span className="text-accent"> · {newSincePinned} new</span>}
            </span>
            <button className="btn btn-accent !px-3 min-h-11 text-[11px]" onClick={onReturnToLive}>
              Return to Live
            </button>
          </div>
        </div>
      )}

      {/* stale link ribbon */}
      {linkDown && (
        <div className="absolute inset-0 bg-black/40 flex items-end justify-center pointer-events-none">
          <div className="mb-3 preview-chip !text-warn flex items-center gap-1" aria-live="polite">
            <Icon name="alert" size={12} /> Stale — link down
          </div>
        </div>
      )}
    </div>
  );
}
