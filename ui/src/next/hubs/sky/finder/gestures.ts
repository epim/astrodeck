// gestures.ts - dragging the sky, and the five guards that keep a drag from
// outliving the finger that started it.
//
// The pan itself is one line (proto/logic.js:296). The five guards are not, and
// they are copied from `components/atlas/SkyCanvas.tsx:460-628` rather than
// re-derived, because every one of them exists for a bug somebody already had:
//
//   1. `button !== 0` - a right or middle press starts a drag whose release the
//      native context menu eats, so nothing ever ends it.
//   2. window `blur` / `visibilitychange` - alt-tab, an app switch or a screen
//      timeout takes the gesture away without sending any pointer event at all.
//   3. `buttons === 0` on a move - a hover with nothing held cannot belong to a
//      drag, so a latch left by a release we never saw ends on the first hover
//      rather than sliding the sky under an unpressed pointer. Touch is exempt:
//      a finger reports buttons=1 only while down and sends no hover moves.
//   4. `pointercancel` is NOT a release - it is the browser taking the gesture
//      (a scroll started, a system gesture won), so it must not fire a tap.
//   5. The wheel listener is attached NATIVELY with `{ passive: false }`. React
//      registers `onWheel` as passive, so `preventDefault()` inside a synthetic
//      handler is silently ignored and the page scrolls under the finder.
//
// The finder has no zoom, so the wheel PANS in altitude (and in azimuth on a
// trackpad's horizontal axis). Trapping the wheel to do nothing would be worse
// than not trapping it: it would eat a page scroll and give nothing back.

import { useCallback, useEffect, useRef } from "react";
import type { PointerEvent as RPointerEvent, RefObject } from "react";

/** Movement, in px, past which a press stops being a tap. */
export const TAP_SLOP_PX = 6;
/** How long a press may last and still be a tap, ms. */
export const TAP_MS = 500;

export const ALT_MIN = -12;
export const ALT_MAX = 89;

export interface PanDelta {
  /** Pixels moved since the previous event, screen x. */
  dx: number;
  dy: number;
}

export interface SkyGestureOptions {
  boxRef: RefObject<HTMLElement | null>;
  /** Pixels per degree, read at event time so a resize mid-drag cannot desync. */
  ppRef: RefObject<number>;
  /** Apply a pan. Given the pixel delta since the gesture STARTED and the
   *  az/alt the gesture started from. */
  onPan: (next: { az: number; alt: number }) => void;
  /** The view at the moment a drag begins. */
  viewRef: RefObject<{ az: number; alt: number }>;
  /** A press that never became a drag, at box coordinates. */
  onTap?: (x: number, y: number) => void;
  enabled?: boolean;
}

export interface SkyGestureHandlers {
  onPointerDown: (e: RPointerEvent<HTMLElement>) => void;
  onPointerMove: (e: RPointerEvent<HTMLElement>) => void;
  onPointerUp: (e: RPointerEvent<HTMLElement>) => void;
  onPointerCancel: (e: RPointerEvent<HTMLElement>) => void;
}

/** Clamp/wrap a panned view exactly as the prototype does: azimuth wraps, and
 *  altitude stops just short of the zenith so the projection never divides the
 *  sky by a pole it cannot draw. */
export function panView(
  start: { az: number; alt: number },
  dx: number,
  dy: number,
  pp: number,
): { az: number; alt: number } {
  return {
    az: (((start.az - dx / pp) % 360) + 360) % 360,
    alt: Math.max(ALT_MIN, Math.min(ALT_MAX, start.alt + dy / pp)),
  };
}

export function useSkyGestures(opts: SkyGestureOptions): SkyGestureHandlers {
  const { boxRef, ppRef, onPan, viewRef, onTap, enabled = true } = opts;

  const dragRef = useRef<{
    active: boolean;
    startX: number;
    startY: number;
    startAz: number;
    startAlt: number;
    moved: boolean;
    pointerId: number | null;
  }>({ active: false, startX: 0, startY: 0, startAz: 0, startAlt: 0, moved: false, pointerId: null });
  const tapRef = useRef<{ x: number; y: number; t: number } | null>(null);

  // GUARD 1 of the exits: one function every path routes through, so a drag
  // ended by the window going away and one ended by a pointerup leave the same
  // state behind.
  const endDrag = useCallback((pointerId?: number) => {
    const el = boxRef.current;
    if (pointerId != null && el && "releasePointerCapture" in el) {
      try {
        (el as Element).releasePointerCapture(pointerId);
      } catch {
        /* never captured, or already released */
      }
    }
    dragRef.current.active = false;
    dragRef.current.pointerId = null;
  }, [boxRef]);

  // GUARD 2: the gesture taken away with no pointer event at all.
  useEffect(() => {
    const onWinBlur = () => endDrag();
    const onVis = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") endDrag();
    };
    if (typeof window === "undefined") return;
    window.addEventListener("blur", onWinBlur);
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.removeEventListener("blur", onWinBlur);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [endDrag]);

  // GUARD 5: the non-passive wheel listener.
  const onPanRef = useRef(onPan);
  onPanRef.current = onPan;
  useEffect(() => {
    const el = boxRef.current;
    if (!el || !enabled) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault(); // honoured: registered with passive: false
      const pp = ppRef.current ?? 1;
      const v = viewRef.current;
      if (!v || !(pp > 0)) return;
      onPanRef.current(panView(v, -e.deltaX, -e.deltaY, pp));
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [boxRef, ppRef, viewRef, enabled]);

  const onPointerDown = (e: RPointerEvent<HTMLElement>) => {
    if (!enabled) return;
    const el = boxRef.current;
    if (!el) return;
    // GUARD 1: primary button only.
    if (e.button !== undefined && e.button !== 0) return;
    const rect = el.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    tapRef.current = { x: px, y: py, t: Date.now() };
    const v = viewRef.current ?? { az: 0, alt: 0 };
    dragRef.current = {
      active: true,
      startX: e.clientX,
      startY: e.clientY,
      startAz: v.az,
      startAlt: v.alt,
      moved: false,
      pointerId: e.pointerId,
    };
    try {
      (el as Element).setPointerCapture(e.pointerId);
    } catch {
      /* jsdom and old WebViews have none; the window guards cover us */
    }
  };

  const onPointerMove = (e: RPointerEvent<HTMLElement>) => {
    if (!enabled) return;
    const tap = tapRef.current;
    const el = boxRef.current;
    if (tap && el) {
      const r = el.getBoundingClientRect();
      if (Math.hypot(e.clientX - r.left - tap.x, e.clientY - r.top - tap.y) > TAP_SLOP_PX) {
        tapRef.current = null;
      }
    }
    const d = dragRef.current;
    if (!d.active) return;
    // GUARD 3: a hover with no button held is not a drag.
    if (e.buttons === 0 && e.pointerType !== "touch") {
      endDrag(e.pointerId);
      return;
    }
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
    if (!d.moved) return;
    const pp = ppRef.current ?? 1;
    if (!(pp > 0)) return;
    onPan(panView({ az: d.startAz, alt: d.startAlt }, dx, dy, pp));
  };

  const onPointerUp = (e: RPointerEvent<HTMLElement>) => {
    if (!enabled) return;
    const tap = tapRef.current;
    tapRef.current = null;
    if (tap && onTap && Date.now() - tap.t <= TAP_MS) {
      const el = boxRef.current;
      const rect = el?.getBoundingClientRect();
      if (rect) {
        const px = e.clientX - rect.left;
        const py = e.clientY - rect.top;
        if (Math.hypot(px - tap.x, py - tap.y) <= TAP_SLOP_PX) onTap(px, py);
      }
    }
    endDrag(e.pointerId);
  };

  // GUARD 4: a cancel is not a release, so no tap fires.
  const onPointerCancel = (e: RPointerEvent<HTMLElement>) => {
    tapRef.current = null;
    endDrag(e.pointerId);
  };

  return { onPointerDown, onPointerMove, onPointerUp, onPointerCancel };
}
