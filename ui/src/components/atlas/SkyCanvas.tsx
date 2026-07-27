// SkyCanvas — the Atlas sky view (design spec §6). A double-buffered survey
// <img> under an SVG we fully own + an HTML label layer, with drag / rotate /
// zoom / nudge / keyboard interaction. All geometry is gnomonic (TAN) so the
// deg->px scale is uniform (lib/framing.ts). NO magnet file is edited here.
//
// Layering (bottom -> top), per spec §6:
//   1. survey <img class="survey"> — fetch-based loader (abortable, generation-
//      guarded): the last GOOD frame stays mounted through failures and gestures
//      (keep-last-good) while surveyTransform() tracks the live view; the settled
//      fetch decodes behind it and swaps only after decode (no half-frame flash).
//   2. a FIXED night dimmer (0.18 when night, else 0) — the `.survey` CSS rule's
//      red filter tames first paint, so loading no longer blacks out the frame.
//   3. <svg viewBox="0 0 1000 1000"> — geometry only (FovOverlay + compass +
//      scale bar). Strokes use var(--accent) with the .svg-halo black underlay.
//   4. HTML label layer — every text label is real CSS px (>=12px), positioned
//      from the same projection. NO text inside the scaled viewBox (C3-A2).
//   5. RotateHandle — its own top-level <svg>, mounted LAST so it always
//      paints above the label layer (wave-2 G3: it used to live inside layer 3
//      and could render behind the "Your camera" label at some canvas widths).
//
// J2000 invariant: center is always J2000; never mix live JNow mount RA in.

import {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type JSX,
  type PointerEvent as RPointerEvent,
  type KeyboardEvent as RKeyboardEvent, type CSSProperties,
} from "react";
import type { CatalogEntry } from "../../types";
import { fovFromOptics, deproject, plausibilityHint, type OpticsLike } from "../../lib/framing";
import { surveyTransform, type SurveyGeom } from "../../lib/surveyView";
import { u } from "../../lib/base";
import { FovOverlay } from "./FovOverlay";
import { RotateHandle } from "./RotateHandle";
import { initTileGL } from "../../lib/tileGL";
import { TileEngine } from "./TileEngine";

const VIEW = 1000; // SVG viewBox edge (geometry units)
const ZOOM_MIN = 0.1;
const ZOOM_MAX = 10;

// One-time WebGL capability probe (spec §5): try initTileGL on a 1x1 canvas.
// Cached so every SkyCanvas mount shares one probe result.
let _tileGLProbe: boolean | null = null;
function tileGLSupported(): boolean {
  if (_tileGLProbe === null) {
    try {
      const c = document.createElement("canvas");
      c.width = 1;
      c.height = 1;
      const g = initTileGL(c);
      _tileGLProbe = g !== null;
      g?.dispose();
    } catch {
      _tileGLProbe = false;
    }
  }
  return _tileGLProbe;
}

// survey id -> tile slug (UI mirror of the server SLUG_REGISTRY, spec §6).
// schematic / unknown -> null -> the <img> fallback pipeline.
const SURVEY_SLUGS: Record<string, string> = {
  "CDS/P/DSS2/color": "dss2color",
  "CDS/P/DSS2/red": "dss2red",
  "CDS/P/2MASS/color": "twomass",
};

export interface SkyCanvasProps {
  /** Session center (J2000). */
  center: { ra_hours: number; dec_deg: number };
  rotationDeg: number;
  survey: string;
  stretch: "linear" | "asinh";
  fovZoomDeg: number; // survey crop angular width
  optics: OpticsLike | null; // camera-MERGED 4-field optics (AtlasView builds it)
  focalMmOverride?: number; // inline Atlas focal field (overrides optics.focal_length_mm)
  mosaic: { rows: number; cols: number; overlap: number };
  catalogTarget?: CatalogEntry; // origin object (size ellipse, legends)
  night: boolean;
  /** survey | schematic — schematic is now ONLY the user's explicit choice. */
  mode: "survey" | "schematic";
  /** Per-image brightness dimmer 0.08..1 (SurveyControls slider). */
  imageBrightness?: number;
  /** Last settled fetch failed; last good frame stays up while retries run. */
  surveyDegraded?: boolean;
  /** Replacement copy for the degraded banner (offline-pack spec §6). */
  degradedText?: string;
  /** config.survey.online_fetch — passed through to the tile engine (spec §4). */
  onlineFetch?: boolean;

  // callbacks — AtlasView routes these into setFraming.
  onCenterChange: (ra_hours: number, dec_deg: number) => void;
  onRotate: (deg: number) => void;
  onZoom: (fovDeg: number) => void;
  /** Fired when a settled survey fetch fails (AtlasView sets surveyDegraded). */
  onSurveyError?: () => void;
  /** Fired when a survey frame loads OK (AtlasView clears surveyDegraded). */
  onSurveyLoad?: () => void;
}

function clampZoom(v: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, v));
}

// Build the survey cutout URL. ra in HOURS — the backend multiplies by 15.
function surveyUrl(
  center: { ra_hours: number; dec_deg: number },
  fovDeg: number,
  survey: string,
  stretch: string,
  width: number,
): string {
  const q = new URLSearchParams({
    ra: center.ra_hours.toFixed(6),
    dec: center.dec_deg.toFixed(6),
    fov: fovDeg.toFixed(6),
    width: String(width),
    survey,
    stretch,
  });
  return u(`/api/survey/cutout.jpg?${q.toString()}`);
}

// Format an angular size for the HTML labels (arcmin under 1°, else degrees).
function fmtAngle(deg: number): string {
  if (deg < 1) return `${(deg * 60).toFixed(1)}′`;
  return `${deg.toFixed(2)}°`;
}

export function SkyCanvas(props: SkyCanvasProps): JSX.Element {
  const {
    center, rotationDeg, survey, stretch, fovZoomDeg, optics, focalMmOverride,
    mosaic, catalogTarget, night, mode, imageBrightness = 1,
    surveyDegraded = false, degradedText, onlineFetch = false,
    onCenterChange, onRotate, onZoom, onSurveyError, onSurveyLoad,
  } = props;

  const boxRef = useRef<HTMLDivElement | null>(null);
  const [boxPx, setBoxPx] = useState(360); // CSS px size of the square canvas

  // ---- touch scroll-trap escape (phone feedback, 2026-07) -----------------
  // The box used to carry `touch-none` (touch-action: none) unconditionally, so
  // EVERY finger gesture that landed on it was consumed as a sky pan. On a phone
  // the canvas is ~45% of the viewport and sits mid-page, so once a thumb landed
  // on it the page could not be scrolled at all: measured on the real build, two
  // 260px upward swipes on the canvas left `main.scrollTop` at 0 with 1648px of
  // page still below. The user was stranded on the Atlas.
  //
  // Resolution (mirrors the precedent in preview/usePreviewGestures.ts, where
  // touch-action is `none` only when zoomed in): on a TOUCH device the canvas
  // defaults to `touch-action: pan-y`, so a one-finger swipe always scrolls the
  // page — the browser owns it, no handler can eat it, and the escape needs no
  // knowledge of any trick. Dragging the sky with a finger becomes an explicit,
  // labelled mode: the chip pinned inside the canvas states what a swipe will do
  // right now and toggles it. The chip lives INSIDE the canvas, so it is on
  // screen whenever the surface that could trap you is on screen.
  //
  // Rejected: direction-sniffing a one-finger drag (hand-rolled scrolling has no
  // fling/rubber-band and still costs vertical panning); two-finger pan (needs
  // touch-action:none, which is the trap itself, and hides the escape behind a
  // multipoint gesture). Mouse/pen drag is untouched — touch-action does not
  // apply to them, so desktop panning behaves exactly as before.
  const touchDevice = useMemo(
    () => typeof navigator !== "undefined" && navigator.maxTouchPoints > 0,
    [],
  );
  const [dragSky, setDragSky] = useState(false);
  // Non-touch pointers are unaffected by touch-action; keep `none` there so the
  // wheel/drag path is byte-for-byte the old behaviour.
  const touchAction = touchDevice && !dragSky ? "pan-y" : "none";
  const [slowLoad, setSlowLoad] = useState(false);   // settled fetch in flight > 300 ms
  const [everLoaded, setEverLoaded] = useState(false);

  // Last good frame: object URL + the geometry it was fetched at (from the
  // X-Survey-* headers). The frame stays mounted through failures/gestures;
  // surveyTransform() maps it onto the live view until the next swap.
  const [shownUrl, setShownUrl] = useState<string | null>(null);
  const [shownGeom, setShownGeom] = useState<SurveyGeom | null>(null);
  const shownUrlRef = useRef<string | null>(null);   // for unmount revocation
  const debounceRef = useRef<number | null>(null);
  const genRef = useRef(0);                          // stale-response guard
  const abortRef = useRef<AbortController | null>(null);
  const retryRef = useRef<{ timer: number | null; attempt: number }>({ timer: null, attempt: 0 });

  // ---- optics-derived FOV (bin-1) ----
  const fov = useMemo(
    () => fovFromOptics(optics, focalMmOverride),
    [optics, focalMmOverride],
  );
  const haveOptics = fov.fov_x_deg > 0 && fov.fov_y_deg > 0;
  const hint = plausibilityHint(fov.pixel_scale_arcsec);

  // px-per-degree in the survey plane (uniform — TAN cutout of width fovZoomDeg).
  const pxPerDeg = VIEW / fovZoomDeg;
  const cssPerDeg = boxPx / fovZoomDeg; // for pointer-delta -> degrees
  const cx = VIEW / 2;
  const cy = VIEW / 2;

  // ---- WebGL tile engine gate (spec §5) ----
  const surveySlug = SURVEY_SLUGS[survey] ?? null;
  const useTileEngine = mode === "survey" && surveySlug !== null && tileGLSupported();
  const [tileDrew, setTileDrew] = useState(false);
  // Tile engine first-draw flag resets when the survey (slug) changes.
  useEffect(() => { setTileDrew(false); }, [survey]);

  const onTileFirst = useCallback(() => {
    setTileDrew(true);
    onSurveyLoad?.();
  }, [onSurveyLoad]);
  const onTileAllFailing = useCallback(() => {
    onSurveyError?.();
  }, [onSurveyError]);

  // ---- responsive square sizing ----
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        const w = e.contentRect.width;
        if (w > 0) setBoxPx(w);
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ---- survey URL recompute, debounced 300ms (spec §6) ----
  const targetUrl = useMemo(
    () => surveyUrl(center, fovZoomDeg, survey, stretch, 768),
    [center, fovZoomDeg, survey, stretch],
  );

  // Fetch-based loader (Wave-1 spec §1.1): abortable, generation-guarded, swaps
  // only after decode. Reads the server's snapped-geometry headers so the
  // residual (<= fov/40 after server §5.2) is compensated by the transform below.
  const loadSurvey = useCallback((url: string, fallback: SurveyGeom) => {
    const gen = ++genRef.current;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    const slowTimer = window.setTimeout(() => {
      if (gen === genRef.current) setSlowLoad(true);
    }, 300);
    void (async () => {
      try {
        const res = await fetch(url, { signal: ac.signal });
        if (!res.ok) throw new Error(`survey ${res.status}`);
        const geom: SurveyGeom = {
          raDeg: Number(res.headers.get("x-survey-ra-deg") ?? fallback.raDeg),
          decDeg: Number(res.headers.get("x-survey-dec-deg") ?? fallback.decDeg),
          fovDeg: Number(res.headers.get("x-survey-fov-deg") ?? fallback.fovDeg),
        };
        const blob = await res.blob();
        const blobUrl = URL.createObjectURL(blob);
        // Decode before swap — double-buffer semantics, no flash of a half-
        // decoded frame. decode() rejection is benign (frame still usable).
        const img = new Image();
        img.src = blobUrl;
        try { await img.decode(); } catch { /* ok */ }
        if (gen !== genRef.current) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        retryRef.current.attempt = 0;
        setShownUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return blobUrl;
        });
        shownUrlRef.current = blobUrl;
        setShownGeom(geom);
        setSlowLoad(false);
        setEverLoaded(true);
        onSurveyLoad?.();
      } catch {
        if (gen !== genRef.current) return; // aborted by a newer load — not a failure
        setSlowLoad(false);
        // Self-healing retry with backoff (spec §1.4): 5s -> 10s -> ... cap 60s.
        // Any new settled view cancels this and fetches immediately instead.
        const attempt = retryRef.current.attempt + 1;
        retryRef.current.attempt = attempt;
        const delay = Math.min(60_000, 5_000 * 2 ** (attempt - 1));
        retryRef.current.timer = window.setTimeout(() => {
          if (gen === genRef.current) loadSurvey(url, fallback);
        }, delay);
        onSurveyError?.();
      } finally {
        window.clearTimeout(slowTimer);
      }
    })();
  }, [onSurveyError, onSurveyLoad]);

  // Settled-fetch scheduler: 300 ms debounce (unchanged cadence). A new view is
  // always a fresh chance — pending backoff retries are cancelled first.
  useEffect(() => {
    if (mode === "schematic") {
      setSlowLoad(false);
      return;
    }
    // Tile engine active (spec §5): the debounce/loader remain ONLY in the
    // fallback img path. Gate off cutout fetching and cancel any pending
    // backoff retry; the fallback resumes if the engine ever unmounts.
    if (useTileEngine) {
      setSlowLoad(false);
      if (retryRef.current.timer != null) {
        window.clearTimeout(retryRef.current.timer);
        retryRef.current.timer = null;
      }
      retryRef.current.attempt = 0;
      return;
    }
    if (retryRef.current.timer != null) {
      window.clearTimeout(retryRef.current.timer);
      retryRef.current.timer = null;
    }
    retryRef.current.attempt = 0;
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    const fallback: SurveyGeom = {
      raDeg: center.ra_hours * 15,
      decDeg: center.dec_deg,
      fovDeg: fovZoomDeg,
    };
    debounceRef.current = window.setTimeout(() => loadSurvey(targetUrl, fallback), 300);
    return () => {
      if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    };
  }, [targetUrl, mode, useTileEngine, loadSurvey, center.ra_hours, center.dec_deg, fovZoomDeg]);

  // A survey-source change must not keep showing the previous survey's frame.
  useEffect(() => {
    genRef.current++;
    abortRef.current?.abort();
    if (retryRef.current.timer != null) window.clearTimeout(retryRef.current.timer);
    retryRef.current.attempt = 0;
    setShownUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    shownUrlRef.current = null;
    setShownGeom(null);
    setEverLoaded(false);
  }, [survey]);

  // Unmount: kill in-flight work + timers, release the object URL.
  useEffect(() => () => {
    genRef.current++;
    abortRef.current?.abort();
    if (retryRef.current.timer != null) window.clearTimeout(retryRef.current.timer);
    if (shownUrlRef.current) URL.revokeObjectURL(shownUrlRef.current);
  }, []);

  // CSS transform mapping the last-fetched frame onto the live view (spec §1.2):
  // pans track the pointer with zero fetches; the settled fetch swaps in a
  // re-centered frame and the transform collapses back toward identity.
  const imgTransform = useMemo(() => {
    if (!shownGeom) return undefined;
    const t = surveyTransform(
      shownGeom,
      { raDeg: center.ra_hours * 15, decDeg: center.dec_deg, fovDeg: fovZoomDeg },
      boxPx,
    );
    if (Math.abs(t.dx) < 0.01 && Math.abs(t.dy) < 0.01 && Math.abs(t.scale - 1) < 1e-4) {
      return undefined;
    }
    return `translate(${t.dx.toFixed(2)}px, ${t.dy.toFixed(2)}px) scale(${t.scale.toFixed(4)})`;
  }, [shownGeom, center.ra_hours, center.dec_deg, fovZoomDeg, boxPx]);

  // ---- pointer drag (translate) + rotation handle ----
  const dragRef = useRef<{
    mode: "pan" | "rotate" | null;
    startX: number;
    startY: number;
    startCenter: { ra_hours: number; dec_deg: number };
    startAngle: number;
    startRotation: number;
  }>({ mode: null, startX: 0, startY: 0, startCenter: center, startAngle: 0, startRotation: rotationDeg });

  // Convert a CSS-px delta into a new center via tangent-plane offset.
  const panTo = useCallback(
    (dxPx: number, dyPx: number, startCenter: { ra_hours: number; dec_deg: number }) => {
      // Grab-the-sky, both axes (spec §5): drag right pulls the sky right
      // (map-style), revealing what lay to the left. Vertical was already correct.
      const dXiDeg = dxPx / cssPerDeg;
      const dEtaDeg = dyPx / cssPerDeg; // screen-down is -Dec (north up)
      const sky = deproject(dXiDeg, dEtaDeg, startCenter.ra_hours, startCenter.dec_deg);
      onCenterChange(sky.ra_hours, sky.dec_deg);
    },
    [cssPerDeg, onCenterChange],
  );

  const onPointerDown = (e: RPointerEvent<HTMLDivElement>) => {
    const el = boxRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    // Real element hit-test on the drawn stalk handle (wave-2 §1) — correct at
    // any rotation/zoom, no duplicated geometry math.
    const onHandle = !!(e.target as Element | null)?.closest?.('[data-role="rotate-handle"]');
    // Scroll mode: a finger on the sky is a page scroll, not a pan. Start no
    // drag at all so the sky cannot creep before the browser takes the gesture.
    // The rotate handle is exempt — it is a small deliberate target, never the
    // surface a scrolling thumb lands on, and gating it would strand the only
    // touch affordance for rotation.
    if (e.pointerType === "touch" && !dragSky && !onHandle) return;
    try {
      el.setPointerCapture(e.pointerId);
    } catch {
      /* ok */
    }
    dragRef.current = {
      mode: onHandle ? "rotate" : "pan",
      startX: px,
      startY: py,
      startCenter: center,
      startAngle: Math.atan2(py - rect.height / 2, px - rect.width / 2),
      startRotation: rotationDeg,
    };
  };

  const onPointerMove = (e: RPointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d.mode) return;
    const el = boxRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    if (d.mode === "pan") {
      panTo(px - d.startX, py - d.startY, d.startCenter);
    } else {
      const ang = Math.atan2(py - rect.height / 2, px - rect.width / 2);
      const deltaDeg = ((ang - d.startAngle) * 180) / Math.PI;
      let next = (d.startRotation + deltaDeg) % 360;
      if (next < 0) next += 360;
      onRotate(next);
    }
  };

  const onPointerUp = (e: RPointerEvent<HTMLDivElement>) => {
    try {
      boxRef.current?.releasePointerCapture(e.pointerId);
    } catch {
      /* ok */
    }
    dragRef.current.mode = null;
  };

  // ---- wheel zoom (native, non-passive) ----
  // React >=17 registers synthetic onWheel as a PASSIVE listener, so
  // e.preventDefault() in a React handler is silently ignored and the page
  // scrolls under the Atlas (spec §5 "Wheel-zoom page-scroll trap"). Attach a
  // real { passive: false } listener to the box instead — it serves ALL modes
  // (tile engine, img fallback, schematic). Refs keep the handler current
  // without re-attaching on every zoom change.
  const fovZoomRef = useRef(fovZoomDeg);
  fovZoomRef.current = fovZoomDeg;
  const onZoomRef = useRef(onZoom);
  onZoomRef.current = onZoom;
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault(); // honored: registered with passive: false
      const factor = e.deltaY > 0 ? 1.12 : 1 / 1.12;
      onZoomRef.current(clampZoom(fovZoomRef.current * factor));
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, []);

  // ---- keyboard nudge ----
  const onKeyDown = (e: RKeyboardEvent<HTMLDivElement>) => {
    const stepDeg = haveOptics ? fov.fov_x_deg * 0.25 : 0.05;
    let dxi = 0;
    let deta = 0;
    switch (e.key) {
      case "ArrowLeft": dxi = -stepDeg; break;
      case "ArrowRight": dxi = stepDeg; break;
      case "ArrowUp": deta = stepDeg; break;
      case "ArrowDown": deta = -stepDeg; break;
      case "[": onRotate((rotationDeg - 5 + 360) % 360); e.preventDefault(); return;
      case "]": onRotate((rotationDeg + 5) % 360); e.preventDefault(); return;
      case "+": case "=": onZoom(clampZoom(fovZoomDeg / 1.12)); e.preventDefault(); return;
      case "-": onZoom(clampZoom(fovZoomDeg * 1.12)); e.preventDefault(); return;
      default: return;
    }
    e.preventDefault();
    const sky = deproject(dxi, deta, center.ra_hours, center.dec_deg);
    onCenterChange(sky.ra_hours, sky.dec_deg);
  };

  // ---- object-size ellipse + verdict ----
  const sizeDeg = (catalogTarget?.size_arcmin ?? 0) / 60;
  const semiMajorDeg = sizeDeg > 0 ? sizeDeg / 2 : null;
  const verdict = useMemo(() => {
    if (!haveOptics) return null;
    if (sizeDeg <= 0) return null; // stars/doubles — suppress (C3-A11)
    const frac = sizeDeg / Math.max(fov.fov_x_deg, fov.fov_y_deg);
    if (frac <= 1) return `Object fills ${Math.round(frac * 100)}% of frame`;
    return `Object is ${frac.toFixed(1)}× your frame — needs a mosaic`;
  }, [haveOptics, sizeDeg, fov.fov_x_deg, fov.fov_y_deg]);

  // ---- HTML labels (real CSS px; positioned from the projection) ----
  // "Your camera" label sits at the top of the (unrotated) frame footprint.
  const frameHalfHcss = (fov.fov_y_deg * cssPerDeg) / 2;
  const ccx = boxPx / 2;
  const ccy = boxPx / 2;

  // "Your camera · WxH" geometry. Two hard constraints, and they used to fight:
  //
  //   RIGHT edge = ccx - 16. The 16px clearance from dead-centre is the wave-2
  //     G3 guard: the rotate handle's stalk and ring live in the central column
  //     and touch the frame's top edge — the exact spot this label is pinned to.
  //     That clearance is preserved EXACTLY, at every width, below.
  //   LEFT edge >= 0. It wasn't: measured on the real build at 390px the label
  //     started at x=-9 and read "our camera"; at 412px it started 14px outside
  //     the canvas box. Its natural width (188px) is simply wider than the
  //     163px of canvas that exists to the left of the guard.
  //
  // Both cannot hold on one line, so the label gets a second line instead of
  // being slid off the screen. `maxWidth` is the clamp — the BROWSER measures
  // the glyphs, which is the one measurement that is never a guess (a hardcoded
  // character width is exactly what the previous pass rightly refused to ship).
  const camGuardRight = ccx - 16;
  const camMaxW = Math.max(40, camGuardRight);
  // Wrapping means the label's height is no longer a constant, and it has to
  // grow UP (away from the frame edge and the handle), not down over the frame.
  // That needs the RENDERED height — hence the ref + ResizeObserver below, not
  // an assumed line-height. On one line it reproduces the old position to ~1px:
  // old top was ccy - frameHalfHcss - 18, and a measured one-line box is 16.5px
  // tall, so the new top lands at ccy - frameHalfHcss - 19.5.
  const camLabelRef = useRef<HTMLSpanElement | null>(null);
  const [camLabelH, setCamLabelH] = useState(0);
  useLayoutEffect(() => {
    const el = camLabelRef.current;
    if (!el) {
      setCamLabelH(0);
      return;
    }
    const read = () => setCamLabelH(el.getBoundingClientRect().height);
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [haveOptics]);
  // Bottom edge parks 3px above the frame's top edge; clamped so a tall frame
  // (zoomed in) can't push the label off the TOP of the canvas either.
  const camTop = Math.max(2, ccy - frameHalfHcss - 3 - camLabelH);

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={boxRef}
        role="application"
        aria-label="Sky framing canvas. Arrow keys nudge center, square-bracket keys rotate, plus and minus zoom. On touch, swiping scrolls the page until you turn on finger drag."
        tabIndex={0}
        className="astro-surface relative aspect-square w-full min-w-[min(320px,calc(100vw-2rem))] mx-auto select-none outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onKeyDown={onKeyDown}
        style={{
          cursor: dragRef.current.mode === "rotate" ? "grabbing" : "grab",
          touchAction,
          // The 720px cap, plus a viewport-height cap the square never had.
          // The canvas is square and width-driven, so in LANDSCAPE it grew to
          // the column width and became TALLER than the screen: measured on a
          // rotated phone, a 720x720 canvas against a 342px scrollport — one
          // whole screenful with no chip, no labels, no controls on it (see
          // shots/before-phone-landscape-full-screen3.png). 85svh keeps the
          // whole map, and its escape chip, inside one screen. Inline rather
          // than a Tailwind arbitrary value so the cap cannot silently vanish
          // if the class fails to generate — losing it would blow the 720px
          // cap too, on every viewport.
          maxWidth: "min(720px, 85svh)",
        }}
      >
        {/* 1a. WebGL tile engine (spec §5): mounts for survey mode when a slug
              maps and WebGL is available; else the <img> pipeline below. */}
        {useTileEngine && surveySlug && (
          <TileEngine
            centerRaDeg={center.ra_hours * 15}
            centerDecDeg={center.dec_deg}
            fovDeg={fovZoomDeg}
            slug={surveySlug}
            onlineFetch={onlineFetch}
            brightness={imageBrightness}
            onFirstTile={onTileFirst}
            onAllFailing={onTileAllFailing}
          />
        )}

        {/* 1b. survey image — kept EXACTLY as-is; the tile engine gates it off.
              The LAST GOOD frame stays through failures/gestures (keep-last-good,
              spec §1.3); the transform tracks the live view. */}
        {mode === "survey" && !useTileEngine && shownUrl && (
          <img
            src={shownUrl}
            alt=""
            aria-hidden
            draggable={false}
            className="survey absolute inset-0 w-full h-full object-cover"
            // Brightness rides the CSS var (see .survey rule); the transform is
            // the pan/zoom tracker — never animate it (it must follow 1:1).
            style={{
              ["--survey-bright" as string]: imageBrightness,
              transform: imgTransform,
              transformOrigin: "center",
            } as CSSProperties}
          />
        )}

        {/* schematic backdrop: explicit user choice OR no fallback frame fetched
              yet — never over the tile engine (it sits later in DOM order and
              is opaque, so it would occlude the tile canvas). */}
        {(mode === "schematic" || (!useTileEngine && !shownUrl)) && (
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_40%,#10131b,#04060a)]" aria-hidden />
        )}

        {/* visually-hidden status: announces the transient survey loading/offline
            states (the visible chips are aria-hidden decoration). */}
        <span className="sr-only" role="status" aria-live="polite">
          {mode === "schematic"
            ? "Schematic framing"
            : surveyDegraded
              ? (degradedText ?? `Survey unreachable, retrying, showing ${shownUrl ? "last image" : "schematic"}`)
              : slowLoad
                ? "Loading survey"
                : ""}
        </span>

        {/* 2. fixed night dimmer — loading no longer blacks out the frame */}
        <div
          className="absolute inset-0 bg-black pointer-events-none transition-opacity duration-200"
          style={{ opacity: night ? 0.18 : 0 }}
          aria-hidden
        />

        {/* first-ever tile-engine skeleton — until the first texture draws.
            UX-07: gated on !surveyDegraded so a no-source / persistent-404 survey no
            longer shows "LOADING…" forever; the honest empty-state below takes over. */}
        {useTileEngine && !tileDrew && !surveyDegraded && (
          <div className="absolute inset-0 grid place-items-center text-dim text-xs" aria-hidden>
            <span className="animate-pulse">LOADING {survey.split("/").pop()}…</span>
          </div>
        )}
        {/* UX-07: honest empty-state when the survey has no reachable source. */}
        {useTileEngine && !tileDrew && surveyDegraded && (
          <div className="absolute inset-0 grid place-items-center px-6 text-center text-dim text-xs">
            <span>
              {degradedText ??
                "No sky survey available — download the offline pack or enable online fetch in Settings."}
            </span>
          </div>
        )}

        {/* first-ever load skeleton (img fallback path only) */}
        {mode === "survey" && !useTileEngine && !everLoaded && !shownUrl && (
          <div className="absolute inset-0 grid place-items-center text-dim text-xs" aria-hidden>
            <span className="animate-pulse">LOADING {survey.split("/").pop()}…</span>
          </div>
        )}

        {/* loading progress chip (subsequent loads) */}
        {mode === "survey" && everLoaded && slowLoad && (
          <div className="absolute top-2 right-2 px-2 py-0.5 text-[11px] mono text-dim bg-black/60 border border-line2 pointer-events-none">
            LOADING…
          </div>
        )}

        {/* 3. SVG geometry layer */}
        <svg
          viewBox={`0 0 ${VIEW} ${VIEW}`}
          className="absolute inset-0 w-full h-full pointer-events-none"
          aria-hidden
        >
          <FovOverlay
            view={VIEW}
            cx={cx}
            cy={cy}
            pxPerDeg={pxPerDeg}
            fovXDeg={fov.fov_x_deg || fovZoomDeg * 0.4}
            fovYDeg={fov.fov_y_deg || fovZoomDeg * 0.28}
            rotationDeg={rotationDeg}
            rows={mosaic.rows}
            cols={mosaic.cols}
            overlap={mosaic.overlap}
            objectSemiMajorDeg={semiMajorDeg}
            objectSemiMinorDeg={semiMajorDeg}
            haveOptics={haveOptics}
          />
          {/* compass N/E ticks (geometry; the N/E letters live on the HTML layer) */}
          <g className="svg-halo" stroke="var(--accent)" strokeWidth={1.5} opacity={0.8}>
            <line x1={cx} y1={24} x2={cx} y2={56} />
            <line x1={VIEW - 56} y1={cy} x2={VIEW - 24} y2={cy} />
          </g>
        </svg>

        {/* 4. HTML label layer — real CSS px, >=12px */}
        <div className="absolute inset-0 pointer-events-none text-ink" aria-hidden>
          {/* compass letters */}
          <span className="absolute left-1/2 -translate-x-1/2 top-1 text-[12px] mono">N</span>
          <span className="absolute right-1 top-1/2 -translate-y-1/2 text-[12px] mono">E</span>
          {/* "Your camera" + FOV readout, pinned just above the frame footprint.
              Anchored to END short of dead-center (not centered on it) so its
              text never sits in the rotate handle's central column — the
              handle's stalk/ring is centered on the same x as this label would
              be if centered, and always touches the frame's top edge, the same
              spot this label is pinned to (wave-2 G3: the genuine geometric
              collision behind the invisible-handle bug). A fixed 16px clearance
              is width-independent and comfortably exceeds the handle's widest
              visible reach (10 viewBox-unit ring radius = 1% of canvas width,
              <=7.2px even at the 720px canvas cap).
              The outer span is the positioned box (maxWidth clamps the left
              edge to the canvas; see camGuardRight/camTop above); the inner one
              is INLINE so its plate hugs each line instead of painting one wide
              slab, and the size half is nowrap so a wrap can only ever fall
              between "Your camera" and the numbers. */}
          {haveOptics && (
            <span
              ref={camLabelRef}
              className="absolute text-[12px] mono text-right leading-snug"
              style={{
                left: camGuardRight,
                top: camTop,
                maxWidth: camMaxW,
                transform: "translateX(-100%)",
                overflowWrap: "anywhere",
              }}
            >
              <span
                className="px-1 bg-black/45"
                style={{ boxDecorationBreak: "clone", WebkitBoxDecorationBreak: "clone" } as CSSProperties}
              >
                Your camera <span className="whitespace-nowrap">· {fmtAngle(fov.fov_x_deg)}×{fmtAngle(fov.fov_y_deg)}</span>
              </span>
            </span>
          )}
          {/* object-size legend, pinned to the right of the ellipse — CLAMPED to
              the canvas. When the object is larger than the view (Andromeda at
              its default framing is 6.4x the frame) the ellipse's semi-major
              axis runs far past the canvas edge, and this label went with it:
              measured at 390px it sat at x=774 inside a 390px column and dragged
              `main.scrollWidth` out to 861px, so the whole Atlas could be
              scrolled sideways into empty space. Off the right edge it is also
              simply invisible. Clamped, it parks on the edge it points past and
              flips its anchor so the text stays inside. */}
          {semiMajorDeg && (() => {
            const want = ccx + semiMajorDeg * cssPerDeg + 6;
            const clamped = want > boxPx - 6;
            return (
              <span
                className="absolute text-[12px] mono whitespace-nowrap px-1 bg-black/45 text-dim"
                style={{
                  // Parked on the right edge it would land exactly on the "E"
                  // compass letter (also right-1, vertically centred), so the
                  // clamped position steps down clear of it.
                  left: clamped ? boxPx - 6 : want,
                  top: clamped ? ccy + 22 : ccy,
                  transform: clamped ? "translate(-100%, -50%)" : "translateY(-50%)",
                }}
              >
                Object size
              </span>
            );
          })()}
          {/* pixel-scale plausibility hint (top-left, real px) */}
          {haveOptics && (
            <span className="absolute left-1 bottom-1 text-[12px] mono bg-black/45 px-1">
              {fov.pixel_scale_arcsec.toFixed(2)}″/px
              {hint && <span className="text-warn ml-1">⚠ {hint}</span>}
            </span>
          )}
          {/* scale-bar value */}
          <span className="absolute right-1 bottom-1 text-[12px] mono bg-black/45 px-1 text-dim">
            {fmtAngle(fovZoomDeg)} wide
          </span>
        </div>

        {/* 5. rotate handle — mounted AFTER the HTML label layer (wave-2 G3
              fix) so it ALWAYS paints on top and stays visible/grabbable at
              every canvas width; see RotateHandle.tsx for the root-cause note. */}
        {haveOptics && (
          <RotateHandle
            view={VIEW}
            cx={cx}
            cy={cy}
            pxPerDeg={pxPerDeg}
            fovYDeg={fov.fov_y_deg}
            rotationDeg={rotationDeg}
            rows={mosaic.rows}
            overlap={mosaic.overlap}
          />
        )}

        {/* 6. touch gesture-mode chip — mounted LAST so nothing can paint over
              the one control that guarantees an exit. It states the CURRENT
              effect of a swipe (word + glyph, never colour alone) rather than
              naming a mode, because the question a thumb is about to ask is
              "what happens if I swipe here?". Touch pointers only: on a mouse
              it would be dead chrome, since touch-action never applies to one.
              stopPropagation keeps the tap from being read as a pan start.

              STICKY, not merely absolute (the residual the scroll-trap fix
              left behind). Pinned to the canvas's top-left it rode the canvas:
              armed, scroll the sky up and the chip leaves with it while the
              surface that eats your swipes stays under your thumb. Measured
              before this change, phone 390 portrait: canvas still on screen
              (bottom y=141) with the chip at y=-212 — the only control that
              can give the page back was gone. In landscape it is worse: the
              square canvas is a whole screenful on its own (measured 720px
              tall against a 342px scrollport), so an armed user could face a
              screen that is nothing but trap.
              `position: sticky` inside a canvas-sized box is exactly the
              invariant we want — the chip is on screen whenever ANY pixel of
              the canvas is, and gone once none is (no canvas, nothing to
              escape). The wrapper is inert; only the button takes taps. */}
        {touchDevice && (
          <div className="absolute inset-0 z-20 pointer-events-none">
            <div className="sticky top-1 p-1">
              <button
                type="button"
                aria-pressed={dragSky}
                aria-label={dragSky
                  ? "Finger drag moves the sky. Activate to swipe-scroll the page instead."
                  : "Swiping scrolls the page. Activate to drag the sky with one finger."}
                className={`pointer-events-auto tap min-h-[44px] px-2 inline-flex items-center gap-1.5
                  rounded-[10px] border bg-black/75 text-[12px] mono uppercase tracking-wider
                  ${dragSky ? "border-accent text-accent" : "border-line2 text-dim"}`}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => setDragSky((v) => !v)}
              >
                <span aria-hidden>{dragSky ? "✥" : "⇕"}</span>
                <span>{dragSky ? "Swipe moves sky" : "Swipe scrolls page"}</span>
              </button>
            </div>
          </div>
        )}

      </div>

      {/* verdict + offline banner beneath the canvas (real text, >=12px) */}
      {mode === "survey" && surveyDegraded && (
        <div className="text-[12px] text-warn border border-line2 bg-black/30 px-2 py-1">
          {degradedText ?? <>⚠ Survey unreachable — {shownUrl ? "showing the last image" : "schematic framing"}; retrying automatically.</>}
        </div>
      )}
      {mode === "schematic" && (
        <div className="text-[12px] text-dim border border-line2 bg-black/30 px-2 py-1">
          Schematic framing: sizes approximate, can't preview nebula shape.
        </div>
      )}
      {verdict && (
        <div className="text-[12px] text-ink">{verdict}</div>
      )}
    </div>
  );
}
