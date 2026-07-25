// CropOverlay — the sensor-1:1 crop painted over the CSS-upscaled base.
// (crop+render UI design §2.1 "Render", §4.4)
//
// DUMB RENDER SHELL. All geometry arrives pre-computed from lib/cropRoi.ts via
// useCropZoom; this file only positions an <img>.
//
// R6 — it MUST be mounted INSIDE `.preview-transform`, never in screen space, or
// it drifts from the base on pan/zoom (the exact bug the single shared transform
// layer exists to prevent). It is positioned in DISPLAY coordinates, the same
// space as the base canvas and the overlay <svg>.
//
// It carries the same `.astro` night-tint class as the base canvas so tint stays
// ONE mechanism, and `image-rendering: pixelated` so the honest sensor pixels are
// not smoothed back into a blur by the browser.
import { useEffect, useState } from "react";
import type { CropRoi } from "../../lib/cropRoi";

const FADE_MS = 120;

function prefersReducedMotion(): boolean {
  return typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function CropOverlay({
  url,
  roi,
  dispW,
  dispH,
  dataW,
  dataH,
}: {
  url: string | null;
  roi: CropRoi | null;
  dispW: number;
  dispH: number;
  dataW: number;
  dataH: number;
}) {
  const [shown, setShown] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setShown(false);
    setFailed(false);
  }, [url]);

  if (!url || !roi || failed) return null;
  if (!dispW || !dispH || !dataW || !dataH) return null;

  const sx = dispW / dataW;
  const sy = dispH / dataH;
  const reduced = prefersReducedMotion();

  return (
    <img
      src={url}
      alt=""
      aria-hidden
      draggable={false}
      onLoad={() => setShown(true)}
      // R3 parity with Lane B: if the crop can't paint we render NOTHING and the
      // CSS-upscaled base stays — never a broken-image glyph.
      onError={() => setFailed(true)}
      className="astro absolute pointer-events-none"
      style={{
        left: roi.x * sx,
        top: roi.y * sy,
        width: roi.w * sx,
        height: roi.h * sy,
        imageRendering: "pixelated",
        opacity: shown ? 1 : 0,
        transition: reduced ? "none" : `opacity ${FADE_MS}ms ease`,
      }}
    />
  );
}
