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
import { Tooltip } from "../ui";
import Logo from "../Logo";
import { u } from "../../lib/base";
import { usePreviewGestures } from "./usePreviewGestures";
import { useImageRemap } from "./useImageRemap";
import { Reticle } from "./Reticle";
import { ScaleBar } from "./ScaleBar";
import { StarOverlay } from "./StarOverlay";
import { ClipMaskLayer, type ClipCropPixels } from "./ClipMaskLayer";
import { TiltOverlay } from "./TiltOverlay";
import { BahtinovOverlay } from "./BahtinovAid";
import { bahtinovAid } from "../../lib/bahtinov";
import { SnrChip } from "./SnrChip";
import { tiltSummary } from "../../lib/tilt";
import { useCropZoom } from "./useCropZoom";
import { CropOverlay } from "./CropOverlay";
import { LoupePanel } from "./LoupePanel";
import { LOUPE_CHROME_PX, loupeBoxSize, shouldCrop, type RoiGeom } from "../../lib/cropRoi";

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
  /** Floor for the stage's height, in px, overriding the default (380 full, and
   *  none at all when `compact` — where the 3:2 ratio alone decides).
   *
   *  Focus passes one because the pod it lays over this stage needs a known
   *  minimum to open into: a compact stage is `w-full` at 3:2, so on a 390px
   *  phone it is 217px tall, and the pod's arc needs 230. See POD_MIN_STAGE_H.
   *  The ratio still wins wherever it gives more, so this only bites on narrow
   *  screens — which is exactly where it is needed. */
  minHeight?: number;
  // expose gesture controls to a parent toolbar
  onControls?: (c: StageControls) => void;
}

/** What the stage hands the toolbar. `loupe*` is the advanced 1:1 pixel-peep
 *  (crop+render design Decision F: ephemeral view state, owned HERE, surfaced
 *  through the existing onControls channel rather than a store slice). */
export interface StageControls {
  fit: () => void;
  hundred: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  loupeOn: boolean;
  setLoupeOn: (v: boolean) => void;
  /** the 1:1 loupe needs the linear /crop path — false ⇒ honest-disabled */
  loupeAvailable: boolean;
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
    minHeight,
    onControls,
  } = props;
  const stageMinH = minHeight ?? (compact ? undefined : 380);

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

  // ADVANCED (opt-in, off by default): the sensor-1:1 loupe. Local state per
  // Decision F — ephemeral view state, not worth a store slice.
  const [loupeOn, setLoupeOn] = useState(false);

  const isNina = !!preview?.is_stretched;
  // The linear path is the capability gate for the client LUT canvas AND for both
  // /crop and /render.png (they 404 without entry.linear). One flag, one truth.
  const linearEnabled = !!preview && !isNina && preview.data_is_linear;

  // ----- the bytes, and their OWN dimensions -----
  // ALWAYS keep the old front visible until the new image has DECODED in the back
  // buffer, then promote (no blank, ever — decisions #17). Declared up here
  // because `displayUrl` keys the decoded-size map the layout below reads.
  const displayUrl = preview ? u(`/api/preview/${preview.id}`) : null;
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

  // Dimensions of the bytes the browser ACTUALLY decoded, keyed by frame URL.
  //
  // The stage used to take the event's display_width/display_height on faith and
  // stretch whatever bytes arrived into that box, so it could not tell a frame
  // it was drawing correctly from one it was rescaling. An <img> knows its own
  // naturalWidth/naturalHeight; the linear canvas gets the same pair from
  // useImageRemap's onReady. Measure, then draw — and when the measurement
  // disagrees with the event, say so (chip at the bottom) rather than silently
  // rescaling. Keyed by URL so a new frame can never inherit the previous
  // frame's measurement.
  //
  // This is NOT a fix for the sheared/tiled preview of 2026-07-31 and must not
  // be read as one: a frame whose rows were laid out at the wrong length still
  // has the pixel count the event predicts, so every number here agrees and the
  // stage sees a perfectly ordinary frame. That fault is upstream of the browser
  // — see the hand-off at the top of server/tests/test_processing_stride.py.
  const [decodedByUrl, setDecodedByUrl] =
    useState<ReadonlyMap<string, { w: number; h: number }>>(() => new Map());
  const noteDecoded = (url: string | null, w: number, h: number) => {
    if (!url || !w || !h) return;
    setDecodedByUrl((prev) => {
      const cur = prev.get(url);
      if (cur && cur.w === w && cur.h === h) return prev;
      const next = new Map(prev);
      next.set(url, { w, h });
      // Only the few URLs in flight (front, back, live) can matter; a kept-around
      // measurement is exactly the stale dimension this map exists to prevent.
      while (next.size > 4) {
        const oldest = next.keys().next();
        if (oldest.done) break;
        next.delete(oldest.value);
      }
      return next;
    });
  };

  // P3-2 (client half): `_image_dims` returns 0 on a PIL decode failure, and 0 is
  // not nullish — so `??` would keep a 0-wide invisible stage. Use a FALSY fallback
  // so display_width=0 falls back to data_width (then 0). Same for height.
  const metaW = preview?.display_width || preview?.data_width || 0;
  const metaH = preview?.display_height || preview?.data_height || 0;
  // The measurement of THE FRAME THE EVENT DESCRIBES — never of whatever buffer
  // happens to be painted. Everything the stage places is in one frame's display
  // space (displayScale for the star marks, the overlay <svg> viewBox, the scale
  // bar, the crop ROI, the loupe centre) and all of it is paired with THIS
  // event's data_width/data_height, so mixing in the size of a buffer from a
  // different exposure scales every annotation by the ratio between the two.
  // That window is real and not short: from the event arriving until the back
  // buffer decodes and promotes is a network fetch plus the fade. So: use the
  // event's numbers as an estimate until this frame's own bytes land, then the
  // bytes; each <img> buffer separately carries its own size (below) so the
  // outgoing frame is never squashed into the incoming frame's box.
  const curDims = displayUrl ? decodedByUrl.get(displayUrl) ?? null : null;
  const dispW = curDims?.w || metaW;
  const dispH = curDims?.h || metaH;
  // The bytes served for this frame are not the size the event said they were.
  // Nothing here can repair that; the stage draws and measures everything from
  // the bytes and names the disagreement (chip at the bottom of the render).
  //
  // What this CAN see: a browser-cached answer for a REUSED frame number
  // (/api/preview/{id} is served max-age=3600 and hub.preview_seq restarts at 1
  // with the server, so an old #1 can answer for a new #1), and a NINA-rendered
  // frame the server could not decode — the hub then publishes the RAW frame's
  // dimensions instead, which the browser contradicts.
  //
  // What it can NOT see, and its silence must not be read as clearing: rows laid
  // out at the wrong length upstream (#110). That keeps the pixel count, so the
  // encoded size still agrees with the event and this stays quiet while the
  // picture is sheared. The check is a transport check, nothing more.
  const dimsMismatch =
    !!preview && !!curDims && !!metaW && !!metaH
      ? Math.abs(curDims.w - metaW) > 1 || Math.abs(curDims.h - metaH) > 1
      : false;

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
      loupeOn,
      setLoupeOn,
      loupeAvailable: linearEnabled,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setFit, setHundred, zoomAt, viewport.scale, stageSize.w, stageSize.h, loupeOn, linearEnabled]);

  // ----- double buffer (NINA / display <img> path) -----
  // On a slow loop / pause / long exposure we cross-fade; on a fast loop we snap
  // (0ms fade). A newer frame arriving mid-fade cancels the in-flight one by
  // replacing backUrl. (The buffer state itself is declared further up, next to
  // the decoded-dimension map that measures it.)
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
  // The 6th argument is the canvas path's measurement: useImageRemap reports the
  // naturalWidth/naturalHeight of the base it just decoded. Keying it on the
  // CURRENT displayUrl is sound because a superseded load is cancelled in the
  // hook's cleanup before its onload can fire, so onReady only ever describes
  // the newest url — the one this closure is holding.
  useImageRemap(canvasRef, linearEnabled ? displayUrl : null, stretch, linearEnabled,
    stretchDragging, (w, h) => noteDecoded(displayUrl, w, h));

  // displayScale: display px per data px (for star coords)
  const displayScale = preview && preview.data_width ? dispW / preview.data_width : 1;

  const clipActive =
    !!preview &&
    preview.data_is_linear &&
    preview.full_well != null &&
    overlays.clip &&
    preview.stats.max >= preview.full_well;

  // ----- pixel-peep zoom (sensor-1:1 /crop of the visible ROI) -----
  // The display base is capped at ≤1400px, so past display-native the CSS upscale
  // is a lie about focus/noise/stars. useCropZoom fetches the REAL sensor pixels
  // of the visible box — debounced to gesture-settle, ROI-quantized, aborted on
  // supersede, LRU-cached, and silently degrading to the CSS upscale on 404.
  const dataW = preview?.data_width ?? 0;
  const dataH = preview?.data_height ?? 0;
  const cropGeom = useMemo<RoiGeom>(
    () => ({
      scale: viewport.scale,
      x: viewport.x,
      y: viewport.y,
      stageW: stageSize.w,
      stageH: stageSize.h,
      dispW,
      dispH,
      dataW,
      dataH,
    }),
    [viewport.scale, viewport.x, viewport.y, stageSize.w, stageSize.h, dispW, dispH, dataW, dataH],
  );
  // "peeping" == the user has zoomed PAST display-native, so the crop covers the
  // visible box. Below that the only crop that can exist is the loupe's small
  // centre sample, which must NOT be painted over the base or treated as a
  // viewport-wide clip mask (it would overclaim coverage).
  const peeping = shouldCrop(cropGeom);
  const crop = useCropZoom({
    previewId: preview?.id ?? null,
    // /crop is linear-only — it 404s for NINA/pre-stretched frames
    enabled: linearEnabled,
    geom: cropGeom,
    wantPixels: clipActive && peeping,
    loupe: loupeOn,
  });

  // Tier-2 clip mask input: the crop's RGBA plus where to paint it in DISPLAY
  // coordinates (the overlay <svg>'s viewBox space).
  const clipCropPixels = useMemo<ClipCropPixels | null>(() => {
    if (!peeping) return null;
    if (!crop.pixels || !crop.roi || !dataW || !dataH || !dispW || !dispH) return null;
    const sx = dispW / dataW;
    const sy = dispH / dataH;
    return {
      data: crop.pixels.data,
      pw: crop.pixels.w,
      ph: crop.pixels.h,
      x: crop.roi.x * sx,
      y: crop.roi.y * sy,
      w: crop.roi.w * sx,
      h: crop.roi.h * sy,
    };
  }, [peeping, crop.pixels, crop.roi, dataW, dataH, dispW, dispH]);

  // Where the loupe samples: the VIEWPORT CENTRE in sensor px (Decision D — it
  // reuses the already-fetched crop, so it costs no extra traffic).
  const loupeCenter = useMemo(() => {
    if (!dispW || !dispH || !dataW || !dataH || !(viewport.scale > 0)) return { x: 0, y: 0 };
    const dx = (stageSize.w / 2 - viewport.x) / viewport.scale;
    const dy = (stageSize.h / 2 - viewport.y) / viewport.scale;
    return {
      x: Math.round((dx * dataW) / dispW),
      y: Math.round((dy * dataH) / dispH),
    };
  }, [dispW, dispH, dataW, dataH, viewport.scale, viewport.x, viewport.y, stageSize.w, stageSize.h]);

  const starsAvailable = !!preview?.star_list && preview.star_list.length > 0;
  const tiltAvailable = !!preview?.tilt;
  // A selected star belongs to ONE exposure: its HFR describes that frame and
  // its ring is drawn from that frame's star_list. Carried across a frame swap
  // it became a chip quoting a star that is no longer on screen, with no ring
  // under it and no way to get rid of it. So: drop the selection when the frame
  // does, and never show the readout outside the overlay that produced it —
  // turning Stars off now also clears it, which is the dismiss path the chip
  // never had. (Re-tapping the same ring still toggles it off — StarOverlay.)
  useEffect(() => { setSelectedStar(null); }, [preview?.id]);
  const starChip = overlays.stars && starsAvailable ? selectedStar : null;
  // bottom-left chip stack: selected-star readout, then the tilt verdict, then the
  // per-sub SNR chip — each one lifts the next by a row so they never overlap.
  const chipRows = (starChip ? 1 : 0) + (overlays.tilt && tiltAvailable ? 1 : 0);
  const snrChipPos = chipRows >= 2 ? "bottom-20" : chipRows === 1 ? "bottom-11" : "bottom-2";

  // The loupe is sized from the MEASURED stage, not a viewport media query — the
  // stage is a panel in a scrolling column, so the two are different numbers.
  // 0 == even the minimum useful 1:1 window would crowd this stage; we then say
  // so where the loupe would have been instead of dropping the toggle on the
  // floor. (lib/cropRoi.loupeBoxSize documents the scale-vs-suppress reasoning.)
  const loupeBox = loupeBoxSize(stageSize.w);
  const loupeShown = loupeOn && linearEnabled && !compact && loupeBox > 0;
  // Horizontal room the bottom-left chips must leave for the loupe panel (its
  // own 8px inset + chrome + an 8px gap). Without this the untruncated SNR chip
  // ran straight under the loupe on a phone.
  const chipReserve = loupeShown ? loupeBox + LOUPE_CHROME_PX + 24 : 16;

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
  // The two buffers can hold frames of DIFFERENT shapes (a binning or ROI change
  // mid-loop), and for the length of a fetch-plus-fade they both do. Each is
  // drawn at ITS OWN measured size rather than at the layer's, so neither frame
  // is stretched into the other's box; until a buffer's bytes land, the event
  // dims of the frame it is loading are the best estimate we have. (`dispW/H`
  // stays the CURRENT frame's space — that is what the overlays are drawn in.)
  const frontDims = (activeImgUrl ? decodedByUrl.get(activeImgUrl) : null)
    ?? { w: dispW, h: dispH };
  const backDims = (backUrl ? decodedByUrl.get(backUrl) : null) ?? { w: metaW, h: metaH };
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
        style={{ aspectRatio: compact ? "3 / 2" : undefined, minHeight: stageMinH }}
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
        minHeight: stageMinH,
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
              src={activeImgUrl ?? undefined}
              alt={`frame ${preview.id}`}
              // Measure on every load, including a cache hit, so the front buffer
              // is sized by the bytes in it rather than by what the event claimed.
              onLoad={(e) =>
                noteDecoded(activeImgUrl, e.currentTarget.naturalWidth, e.currentTarget.naturalHeight)
              }
              onError={() => setBrokenUrl(activeImgUrl ?? null)}
              className="astro absolute top-0 left-0"
              style={{ width: frontDims.w, height: frontDims.h, filter: ninaFilter }}
            />
            {/* back buffer decodes ON TOP; fade-in (slow loop) or instant promote
                (fast loop). onLoad reveals + promotes it → no blank ever. */}
            {backUrl && (
              <img
                key={backUrl}
                src={backUrl}
                alt=""
                onLoad={(e) => {
                  // record the incoming frame's real size BEFORE promoting it, so
                  // the moment it becomes the front buffer the layer around it is
                  // already the right shape — no one-frame stretch on a size change.
                  noteDecoded(backUrl, e.currentTarget.naturalWidth, e.currentTarget.naturalHeight);
                  onBackLoaded();
                }}
                onError={() => {
                  // a broken incoming frame must not blank the (good) front buffer
                  // or get promoted; just drop the back buffer and keep the front.
                  setBackUrl(null);
                  setBackVisible(false);
                }}
                className="astro absolute top-0 left-0"
                style={{
                  width: backDims.w,
                  height: backDims.h,
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

        {/* pixel-peep: real sensor pixels over the CSS-upscaled base. Lives in
            THIS transform layer (R6) so it can never drift; renders nothing at
            all when we have no crop, leaving the base untouched. */}
        <CropOverlay
          url={peeping ? crop.url : null}
          roi={crop.roi}
          dispW={dispW}
          dispH={dispH}
          dataW={dataW}
          dataH={dataH}
        />

        {/* overlays in the same transform space (perfect alignment) */}
        <svg
          className="absolute top-0 left-0 pointer-events-none"
          width={dispW}
          height={dispH}
          viewBox={`0 0 ${dispW} ${dispH}`}
          style={{ overflow: "visible" }}
        >
          <ClipMaskLayer w={dispW} h={dispH} active={clipActive} cropPixels={clipCropPixels} />
          {overlays.tilt && tiltAvailable && (
            <TiltOverlay tilt={preview.tilt!} dispW={dispW} dispH={dispH} />
          )}
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
          {/* Bahtinov spikes (polish grab-bag (a)) — LAST inside the shared
              transform so the focus lines sit over the star overlay, in exactly
              the same data->display space (displayScale), and only while the aid
              is armed AND the server sent a valid fit. Passive: pointer-events
              off (it must never eat a star tap). `!== false` so a persisted
              pre-grab-bag overlay blob still defaults ON. */}
          {overlays.bahtinov !== false && preview.bahtinov?.geom && (
            <BahtinovOverlay
              geom={preview.bahtinov.geom}
              dispW={dispW}
              dispH={dispH}
              displayScale={displayScale}
              inFocus={!!preview.bahtinov.in_focus}
            />
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

      {/* Clip-mask SCOPE disclosure (advanced nuance, §1.4). The amber frame
          always means "this frame clips"; the per-pixel paint only exists inside
          the ROI we actually fetched. Say which one the user is looking at rather
          than letting an un-painted region read as "clean".

          The scope explanation is a `Tooltip`, not a raw `title=`: title never
          fires on touch, and a tablet at the scope is the primary field device,
          so on the device that matters most the nuance simply did not exist.

          `data-no-pan` on the WRAPPER, here and on every other tappable chip
          below, is what makes that tooltip reachable. The gesture layer listens
          for pointerdown natively on the stage element, so a tap on a chip
          bubbled into it and counted towards the double-tap accelerator: asking
          a chip what it meant zoomed the preview to 100% instead of answering.
          usePreviewGestures already skips a press inside [data-no-pan] —
          StarOverlay marks its rings that way — and every chip the user is
          invited to touch needs the same mark, not just the stars. */}
      {clipActive && (
        <div data-no-pan className={`absolute top-2 left-2 ${preview.bayer_pattern ? "mt-14" : ""}`}>
          <Tooltip
            content={
              clipCropPixels
                ? "The exact overexposed pixels are painted inside the zoomed region. The amber frame means somewhere in the full frame is overexposed."
                : "Somewhere in this frame is overexposed. Zoom in to see exactly which pixels."
            }
          >
            <span className="preview-chip !text-warn">
              Some stars are overexposed
              {clipCropPixels ? " — shown in view" : ""}
            </span>
          </Tooltip>
        </div>
      )}

      {/* Bahtinov verdict — the WORD, on the preview itself. The overlay's
          focused/unfocused state was carried by a --accent → --good swap, and
          on :root.night those tokens are #ff3a3a vs #ff3333 under a red filter:
          indistinguishable. The word-bearing verdict used to live only in
          FocusView's panel, so a user focusing from CaptureView had colour
          alone. Top-centre keeps it clear of the scale bar and the left chips. */}
      {overlays.bahtinov !== false && preview.bahtinov?.geom && (() => {
        const v = bahtinovAid(preview.bahtinov);
        const tint = v.tone === "good" ? "!text-good"
          : v.tone === "bad" ? "!text-bad" : v.tone === "warn" ? "!text-warn" : "";
        return (
          <div
            className={`absolute top-2 left-1/2 -translate-x-1/2 preview-chip flex items-center gap-1 ${tint}`}
            role="status"
            aria-live="polite"
          >
            <Icon name={v.tone === "good" ? "check" : "focus"} size={11} />
            {v.headline}
          </div>
        );
      })()}

      {/* selected star readout */}
      {starChip && (
        <div className="absolute bottom-2 left-2 preview-chip mono" aria-live="polite">
          HFR {starChip.hfr.toFixed(2)} px
          {preview.pixel_scale_arcsec != null && ` · ${(starChip.hfr * preview.pixel_scale_arcsec).toFixed(2)}″`}
        </div>
      )}

      {/* tilt/aberration classification chip (PRO-13) — bottom-left, mirroring
          the selected-star readout; stacked above it when both are on (niche,
          rarely simultaneous — Open Decision E).

          NOT aria-live (same rule as SnrChip): the label is recomputed from every
          incoming sub, so a live region here re-announces "Field: tilted" all
          night and talks over everything that actually needs saying. The advice
          moved out of `title=` into a Tooltip so it has a tap path. */}
      {overlays.tilt && tiltAvailable && (() => {
        const s = tiltSummary(preview.tilt!);
        const tint = s.tone === "good" ? "!text-good" : s.tone === "warn" ? "!text-warn" : "!text-bad";
        const pos = starChip ? "bottom-11" : "bottom-2";
        return (
          <div data-no-pan className={`absolute ${pos} left-2`}>
            <Tooltip content={s.advice}>
              <span className={`preview-chip flex items-center gap-1 ${tint}`}>
                Field: {s.label}
              </span>
            </Tooltip>
          </div>
        );
      })()}

      {/* per-sub SNR chip (polish grab-bag (b)). Self-subscribes to the photometry
          profile and renders NOTHING when it is unset or when this frame carried
          no trusted star flux — zero novice clutter, never a fabricated number.
          Width-capped so it wraps instead of sliding under the loupe. */}
      <div
        data-no-pan
        className={`absolute ${snrChipPos} left-2`}
        style={{ maxWidth: `calc(100% - ${chipReserve}px)` }}
      >
        <SnrChip preview={preview} />
      </div>

      {/* decimation disclosure — lifted clear of the loupe when it's open */}
      {overlays.stars && starsAvailable && decimated && decimated.shown < decimated.total && (
        <div className="absolute right-2 preview-chip" style={{ bottom: loupeShown ? loupeBox + 52 : 8 }}>
          Showing {decimated.shown}/{decimated.total} stars
        </div>
      )}

      {/* ADVANCED: sensor-1:1 loupe (opt-in; the toolbar's "1:1" toggle). Reuses
          the debounced crop the zoom layer already fetched — Decision D. Sized to
          the measured stage; see loupeBoxSize. */}
      {loupeShown && (
        <div className="absolute bottom-2 right-2">
          <LoupePanel
            url={crop.url}
            roi={crop.roi}
            centerX={loupeCenter.x}
            centerY={loupeCenter.y}
            previewId={preview.id}
            loading={crop.loading}
            size={loupeBox}
          />
        </div>
      )}

      {/* …and when the stage is too narrow to host even the minimum useful 1:1
          window, say that where the loupe would have been. A toggle the user
          just pressed must never silently do nothing (§11.8) — Icon + WORD, with
          the full reason on a tap/hover/focus path. */}
      {loupeOn && linearEnabled && !compact && loupeBox === 0 && (
        <div data-no-pan className="absolute bottom-2 right-2">
          <Tooltip content="A 1:1 view needs about 320 px of preview width to show enough sensor pixels to judge focus. Rotate the device, or open the preview in a wider panel.">
            <span className="preview-chip flex items-center gap-1">
              <Icon name="lock" size={11} /> Magnifier hidden
            </span>
          </Tooltip>
        </div>
      )}

      {/* The bytes are not the size the frame said they were. The stage draws and
          measures EVERYTHING from the bytes (see dispW/dispH), so the picture and
          the marks over it stay aligned to each other — what breaks is the claim
          that these bytes are this frame. Say that, and say only that: this chip
          was written once claiming the overlays were off by the size ratio, which
          they never were, and an instrument that lies about a lie is worse than
          no instrument. `dimsMismatch` implies curDims, but TS wants it named. */}
      {dimsMismatch && curDims && (
        <div data-no-pan className="absolute bottom-2 left-1/2 -translate-x-1/2">
          <Tooltip
            content={`Frame #${preview.id} was published as ${metaW}×${metaH} display pixels; the bytes served for it decode as ${curDims.w}×${curDims.h}. The picture, the star marks, the reticle and the scale bar are all drawn from the measured size, so they agree with each other. What cannot be checked here is whether these bytes are frame #${preview.id} at all — the usual cause is the browser answering from its cache with an older frame that reused this number (the counter restarts when the server restarts), which would put an earlier exposure under this frame's stars and HFR. Reload the page to clear that. The saved FITS is written from the raw frame and is unaffected.`}
          >
            <span className="preview-chip !text-warn flex items-center gap-1" role="status">
              <Icon name="alert" size={11} />
              Preview bytes are {curDims.w}×{curDims.h}, not {metaW}×{metaH}
            </span>
          </Tooltip>
        </div>
      )}

      {/* pinned banner */}
      {pinned && (
        <div className="absolute inset-0 flex items-start justify-center pointer-events-none">
          <div className="absolute inset-0 bg-black/35" />
          {/* data-no-pan for the same reason as the chips: two quick taps on
              "Return to Live" must not also be read as a double-tap zoom. */}
          <div data-no-pan className="relative mt-3 panel px-3 py-2 flex items-center gap-3 pointer-events-auto" aria-live="polite">
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
