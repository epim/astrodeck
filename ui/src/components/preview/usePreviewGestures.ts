// usePreviewGestures — pointer/touch/wheel zoom+pan inside the shared transform.
// (live-preview spec §7 "Gestures, remap, transform" — stream S)
//
// Contract:
//  - touch-action is `none` ONLY when zoomed in (scale > fitScale); at fit a
//    single finger scrolls the page (no scroll-trap) — spec §7, §9.
//  - single-pointer drag pans only when zoomed; two-pointer pinch+pan around the
//    centroid; wheel zooms at the cursor; dblclick/double-tap toggles Fit<->100%.
//  - scale clamped to [fitScale, maxScale]; pan integer-rounded; origin 0,0.
//
// The viewport is owned by the store (useViewport/setViewport). This hook is a
// thin controller: it reads the current viewport via a ref (so handlers never go
// stale) and calls `onChange` with partial updates. Fit math lives in the caller
// (PreviewStage) which knows the container + image dims; this hook only needs the
// current fitScale (the scale at which the image exactly fits) to clamp + gate.
import { useCallback, useEffect, useRef } from "react";
import type { Viewport } from "../../types";

export interface GestureGeom {
  /** container (stage) size in CSS px */
  cw: number;
  ch: number;
  /** the natural display image size in CSS px (display_width/height) */
  iw: number;
  ih: number;
  /** scale at which the image fits the container (object-contain) */
  fitScale: number;
}

const MAX_SCALE_MULT = 8; // 100% / fit can be large; cap absolute zoom sanely

export function usePreviewGestures(
  ref: React.RefObject<HTMLElement | null>,
  viewport: Viewport,
  onChange: (v: Partial<Viewport>) => void,
  geom: GestureGeom,
) {
  // Keep live refs so the imperative pointer handlers never read stale closures.
  const vpRef = useRef(viewport);
  vpRef.current = viewport;
  const geomRef = useRef(geom);
  geomRef.current = geom;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // active pointers for pinch
  const pointers = useRef<Map<number, { x: number; y: number }>>(new Map());
  const pinchStart = useRef<{ dist: number; scale: number; cx: number; cy: number } | null>(null);
  const lastTap = useRef(0);

  const clampScale = useCallback((s: number) => {
    const { fitScale } = geomRef.current;
    const max = Math.max(fitScale * MAX_SCALE_MULT, fitScale, 4);
    return Math.min(Math.max(s, fitScale), max);
  }, []);

  // Clamp pan so the image can't be dragged completely off-stage. With
  // transform-origin 0 0 the scaled image spans [tx, tx + iw*scale]. We keep at
  // least the image overlapping the container; when smaller than the container we
  // center it.
  const clampPan = useCallback((scale: number, x: number, y: number) => {
    const { cw, ch, iw, ih } = geomRef.current;
    const sw = iw * scale;
    const sh = ih * scale;
    const cx = sw <= cw ? (cw - sw) / 2 : Math.min(0, Math.max(cw - sw, x));
    const cy = sh <= ch ? (ch - sh) / 2 : Math.min(0, Math.max(ch - sh, y));
    return { x: Math.round(cx), y: Math.round(cy) };
  }, []);

  /** Zoom around a container-space focal point (fx,fy), keeping it stationary. */
  const zoomAt = useCallback(
    (nextScaleRaw: number, fx: number, fy: number) => {
      const vp = vpRef.current;
      const next = clampScale(nextScaleRaw);
      if (next === vp.scale) return;
      // image-space point under the focal point: (fx - tx) / scale
      const imgX = (fx - vp.x) / vp.scale;
      const imgY = (fy - vp.y) / vp.scale;
      const tx = fx - imgX * next;
      const ty = fy - imgY * next;
      const { fitScale } = geomRef.current;
      const pan = clampPan(next, tx, ty);
      onChangeRef.current({ scale: next, ...pan, fit: next <= fitScale + 1e-3 });
    },
    [clampScale, clampPan],
  );

  const setFit = useCallback(() => {
    const { fitScale } = geomRef.current;
    onChangeRef.current({ scale: fitScale, x: 0, y: 0, fit: true });
  }, []);

  const setHundred = useCallback(() => {
    // 100% of the display image == scale 1 (display px == CSS px). Center on
    // the container middle so the user lands on the frame center, not a corner.
    const { cw, ch } = geomRef.current;
    zoomAt(1, cw / 2, ch / 2);
  }, [zoomAt]);

  // ----- pointer handlers (attached imperatively so we can set passive:false) ---
  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const localPoint = (e: PointerEvent) => {
      const r = el.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    const onPointerDown = (e: PointerEvent) => {
      // ignore pointers that begin on an interactive overlay (e.g. a star marker
      // with [data-no-pan]) so a star tap never starts a pan / double-tap zoom.
      if ((e.target as Element | null)?.closest?.("[data-no-pan]")) return;
      el.setPointerCapture?.(e.pointerId);
      pointers.current.set(e.pointerId, localPoint(e));
      if (pointers.current.size === 2) {
        const pts = [...pointers.current.values()];
        const dx = pts[0].x - pts[1].x;
        const dy = pts[0].y - pts[1].y;
        pinchStart.current = {
          dist: Math.hypot(dx, dy) || 1,
          scale: vpRef.current.scale,
          cx: (pts[0].x + pts[1].x) / 2,
          cy: (pts[0].y + pts[1].y) / 2,
        };
      }
      // double-tap accelerator (single finger): Fit <-> 100%
      if (pointers.current.size === 1) {
        const now = performance.now();
        if (now - lastTap.current < 300) {
          lastTap.current = 0;
          const { fitScale } = geomRef.current;
          if (vpRef.current.scale > fitScale + 1e-3) setFit();
          else setHundred();
        } else {
          lastTap.current = now;
        }
      }
    };

    const onPointerMove = (e: PointerEvent) => {
      const prev = pointers.current.get(e.pointerId);
      if (!prev) return;
      const p = localPoint(e);
      pointers.current.set(e.pointerId, p);

      if (pointers.current.size >= 2 && pinchStart.current) {
        const pts = [...pointers.current.values()];
        const dx = pts[0].x - pts[1].x;
        const dy = pts[0].y - pts[1].y;
        const dist = Math.hypot(dx, dy) || 1;
        const cx = (pts[0].x + pts[1].x) / 2;
        const cy = (pts[0].y + pts[1].y) / 2;
        zoomAt((dist / pinchStart.current.dist) * pinchStart.current.scale, cx, cy);
        return;
      }

      // single-pointer drag → pan ONLY when zoomed in (else let the page scroll)
      const { fitScale } = geomRef.current;
      if (vpRef.current.scale > fitScale + 1e-3) {
        const vp = vpRef.current;
        const pan = clampPan(vp.scale, vp.x + (p.x - prev.x), vp.y + (p.y - prev.y));
        onChangeRef.current(pan);
      }
    };

    const endPointer = (e: PointerEvent) => {
      pointers.current.delete(e.pointerId);
      if (pointers.current.size < 2) pinchStart.current = null;
      el.releasePointerCapture?.(e.pointerId);
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const fx = e.clientX - r.left;
      const fy = e.clientY - r.top;
      const factor = Math.exp(-e.deltaY * 0.0015);
      zoomAt(vpRef.current.scale * factor, fx, fy);
    };

    const onDblClick = (e: MouseEvent) => {
      e.preventDefault();
      const { fitScale } = geomRef.current;
      if (vpRef.current.scale > fitScale + 1e-3) setFit();
      else setHundred();
    };

    el.addEventListener("pointerdown", onPointerDown);
    el.addEventListener("pointermove", onPointerMove);
    el.addEventListener("pointerup", endPointer);
    el.addEventListener("pointercancel", endPointer);
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("dblclick", onDblClick);
    return () => {
      el.removeEventListener("pointerdown", onPointerDown);
      el.removeEventListener("pointermove", onPointerMove);
      el.removeEventListener("pointerup", endPointer);
      el.removeEventListener("pointercancel", endPointer);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("dblclick", onDblClick);
    };
  }, [ref, zoomAt, clampPan, setFit, setHundred]);

  // keyboard: arrows pan/nudge, +/- zoom, 0 fit, 1 100%
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const { cw, ch, fitScale } = geomRef.current;
      const vp = vpRef.current;
      const STEP = 40;
      switch (e.key) {
        case "ArrowUp":
          e.preventDefault();
          onChangeRef.current(clampPan(vp.scale, vp.x, vp.y + STEP));
          break;
        case "ArrowDown":
          e.preventDefault();
          onChangeRef.current(clampPan(vp.scale, vp.x, vp.y - STEP));
          break;
        case "ArrowLeft":
          e.preventDefault();
          onChangeRef.current(clampPan(vp.scale, vp.x + STEP, vp.y));
          break;
        case "ArrowRight":
          e.preventDefault();
          onChangeRef.current(clampPan(vp.scale, vp.x - STEP, vp.y));
          break;
        case "+":
        case "=":
          e.preventDefault();
          zoomAt(vp.scale * 1.25, cw / 2, ch / 2);
          break;
        case "-":
        case "_":
          e.preventDefault();
          zoomAt(vp.scale / 1.25, cw / 2, ch / 2);
          break;
        case "0":
          e.preventDefault();
          setFit();
          break;
        case "1":
          e.preventDefault();
          setHundred();
          break;
        default:
          void fitScale;
      }
    },
    [clampPan, zoomAt, setFit, setHundred],
  );

  const zoomedIn = viewport.scale > geom.fitScale + 1e-3;

  return { onKeyDown, setFit, setHundred, zoomAt, zoomedIn };
}
