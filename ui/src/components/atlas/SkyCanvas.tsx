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
//
// J2000 invariant: center is always J2000; never mix live JNow mount RA in.

import {
  useCallback, useEffect, useMemo, useRef, useState, type JSX, type PointerEvent as RPointerEvent,
  type KeyboardEvent as RKeyboardEvent, type WheelEvent as RWheelEvent, type CSSProperties,
} from "react";
import type { CatalogEntry } from "../../types";
import { fovFromOptics, deproject, plausibilityHint, type OpticsLike } from "../../lib/framing";
import { surveyTransform, type SurveyGeom } from "../../lib/surveyView";
import { u } from "../../lib/base";
import { FovOverlay } from "./FovOverlay";

const VIEW = 1000; // SVG viewBox edge (geometry units)
const ZOOM_MIN = 0.1;
const ZOOM_MAX = 10;

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
  activePanel?: number | null;
  catalogTarget?: CatalogEntry; // origin object (size ellipse, legends)
  night: boolean;
  /** survey | schematic — schematic is now ONLY the user's explicit choice. */
  mode: "survey" | "schematic";
  /** Per-image brightness dimmer 0.08..1 (SurveyControls slider). */
  imageBrightness?: number;
  /** Last settled fetch failed; last good frame stays up while retries run. */
  surveyDegraded?: boolean;

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
    mosaic, activePanel = null, catalogTarget, night, mode, imageBrightness = 1,
    surveyDegraded = false,
    onCenterChange, onRotate, onZoom, onSurveyError, onSurveyLoad,
  } = props;

  const boxRef = useRef<HTMLDivElement | null>(null);
  const [boxPx, setBoxPx] = useState(360); // CSS px size of the square canvas
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
  }, [targetUrl, mode, loadSurvey, center.ra_hours, center.dec_deg, fovZoomDeg]);

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

  // ---- pointer drag (translate) + rotation knob ----
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
      // viewBox is N-up: +x is East/RA-increasing on the sky image (the survey is
      // mirrored for RA, but the overlay frame uses the SAME projection so the
      // visual stays consistent). Dragging right moves the sky left under the frame.
      const dXiDeg = -(dxPx / cssPerDeg);
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

  // ---- wheel zoom ----
  const onWheel = (e: RWheelEvent<HTMLDivElement>) => {
    e.preventDefault();
    const factor = e.deltaY > 0 ? 1.12 : 1 / 1.12;
    onZoom(clampZoom(fovZoomDeg * factor));
  };

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

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={boxRef}
        role="application"
        aria-label="Sky framing canvas. Arrow keys nudge center, square-bracket keys rotate, plus and minus zoom."
        tabIndex={0}
        className="astro-surface relative aspect-square w-full max-w-[720px] mx-auto select-none touch-none outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onWheel={onWheel}
        onKeyDown={onKeyDown}
        style={{ cursor: dragRef.current.mode === "rotate" ? "grabbing" : "grab" }}
      >
        {/* 1. survey image — the LAST GOOD frame stays through failures/gestures
              (keep-last-good, spec §1.3); the transform tracks the live view. */}
        {mode === "survey" && shownUrl && (
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

        {/* schematic backdrop: explicit user choice OR no frame fetched yet */}
        {(mode === "schematic" || !shownUrl) && (
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_40%,#10131b,#04060a)]" aria-hidden />
        )}

        {/* visually-hidden status: announces the transient survey loading/offline
            states (the visible chips are aria-hidden decoration). */}
        <span className="sr-only" role="status" aria-live="polite">
          {mode === "schematic"
            ? "Schematic framing"
            : surveyDegraded
              ? `Survey unreachable, retrying, showing ${shownUrl ? "last image" : "schematic"}`
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

        {/* first-ever load skeleton */}
        {mode === "survey" && !everLoaded && !shownUrl && (
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
            activeIndex={activePanel}
            objectSemiMajorDeg={semiMajorDeg}
            objectSemiMinorDeg={semiMajorDeg}
            haveOptics={haveOptics}
            rotateHandle={haveOptics}
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
          {/* "Your camera" + FOV readout, pinned just above the frame footprint */}
          {haveOptics && (
            <span
              className="absolute text-[12px] mono whitespace-nowrap px-1 bg-black/45"
              style={{ left: ccx, top: ccy - frameHalfHcss - 18, transform: "translateX(-50%)" }}
            >
              Your camera · {fmtAngle(fov.fov_x_deg)}×{fmtAngle(fov.fov_y_deg)}
            </span>
          )}
          {/* object-size legend, pinned to the right of the ellipse */}
          {semiMajorDeg && (
            <span
              className="absolute text-[12px] mono whitespace-nowrap px-1 bg-black/45 text-dim"
              style={{ left: ccx + semiMajorDeg * cssPerDeg + 6, top: ccy, transform: "translateY(-50%)" }}
            >
              Object size
            </span>
          )}
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

      </div>

      {/* verdict + offline banner beneath the canvas (real text, >=12px) */}
      {mode === "survey" && surveyDegraded && (
        <div className="text-[12px] text-warn border border-line2 bg-black/30 px-2 py-1">
          ⚠ Survey unreachable — {shownUrl ? "showing the last image" : "schematic framing"}; retrying automatically.
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
