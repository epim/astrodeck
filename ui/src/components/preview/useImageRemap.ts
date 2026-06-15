// useImageRemap — client-side LUT remap for the LINEAR/raw path (sim, Alpaca).
// (live-preview spec §7 "useImageRemap", §2 render-strategy table — stream S)
//
// - Loads the display base ONCE per url (an already-auto-stretched JPEG/PNG of
//   the display image). On a B/M/W or Brightness change it re-applies a 256-entry
//   MTF LUT to the cached pixels via putImageData, rAF-debounced.
// - While a handle is actively dragged the working canvas runs at <=900px; full
//   res is restored on release (spec §7, C1#24 mitigation).
// - Night tint is NOT baked into the LUT — it comes from the shared
//   `.astro-surface` CSS filter applied to the <canvas> (spec §8, perceptual #3).
//   So this LUT operates on a pure-luminance display image; one tint mechanism.
//
// Returns nothing visible itself; it draws into the passed canvas ref. The caller
// (PreviewStage) owns the <canvas> element + decides when to use this path.
import { useEffect, useRef } from "react";
import type { StretchParams } from "../../types";
import { buildLut } from "./lut";

const DRAG_MAX = 900;

export function useImageRemap(
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
  baseUrl: string | null,
  stretch: StretchParams,
  enabled: boolean,
  dragging: boolean,
  onReady?: (w: number, h: number) => void,
) {
  // Cached decoded base pixels (full res) so a slider drag never re-decodes.
  const baseRef = useRef<{ url: string; img: HTMLImageElement } | null>(null);
  const rafRef = useRef<number | null>(null);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  // (re)load the base image when the url changes
  useEffect(() => {
    if (!enabled || !baseUrl) return;
    if (baseRef.current?.url === baseUrl) return;
    let cancelled = false;
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.decoding = "async";
    img.onload = () => {
      if (cancelled) return;
      baseRef.current = { url: baseUrl, img };
      onReadyRef.current?.(img.naturalWidth, img.naturalHeight);
      draw(false);
    };
    img.src = baseUrl;
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, enabled]);

  // re-apply LUT on stretch change (debounced to rAF)
  useEffect(() => {
    if (!enabled) return;
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => draw(dragging));
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stretch.black, stretch.mid, stretch.white, stretch.brightness, dragging, enabled]);

  function draw(lowRes: boolean) {
    const canvas = canvasRef.current;
    const base = baseRef.current;
    if (!canvas || !base) return;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return;

    const nw = base.img.naturalWidth;
    const nh = base.img.naturalHeight;
    // working resolution: full, or downsampled while dragging a handle
    let w = nw;
    let h = nh;
    if (lowRes && Math.max(nw, nh) > DRAG_MAX) {
      const k = DRAG_MAX / Math.max(nw, nh);
      w = Math.round(nw * k);
      h = Math.round(nh * k);
    }
    canvas.width = w;
    canvas.height = h;
    ctx.drawImage(base.img, 0, 0, w, h);

    const lut = buildLut(stretch);
    // identity LUT (Auto + neutral Brightness)? the drawImage already shows the
    // server's auto-stretched image — skip the per-pixel pass entirely (perf).
    if (isIdentity(lut)) return;

    const id = ctx.getImageData(0, 0, w, h);
    const d = id.data;
    for (let i = 0; i < d.length; i += 4) {
      d[i] = lut[d[i]];
      d[i + 1] = lut[d[i + 1]];
      d[i + 2] = lut[d[i + 2]];
      // alpha untouched
    }
    ctx.putImageData(id, 0, 0);
  }
}

function isIdentity(lut: Uint8ClampedArray): boolean {
  // a few sample points are enough to detect a neutral transfer cheaply
  for (const i of [0, 64, 128, 192, 255]) {
    if (lut[i] !== i) return false;
  }
  return true;
}
