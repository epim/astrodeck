// ClipMaskLayer.tsx — saturation/clip mask (stream O).
// (spec §5 "Clip mask", §12 honesty rule #4, decisions #3/#7;
//  crop+render UI design §2.3, Decision C)
//
// HONESTY: only renders when data_is_linear && full_well != null && overlays.clip.
// On NINA / unknown-well it renders NOTHING (the caller shows the inline reason).
//
// TWO TIERS, and the cheap one is the authoritative one:
//
//  Tier 1 (always, zero latency, zero traffic) — a STATIC desaturated-amber hatch
//    FRAME shown only when the frame actually clips (stats.max >= full_well).
//    Static, never flashing/magenta (§11.4). This is the honest "this frame
//    contains clipped pixels" indicator and it needs no pixels client-side.
//
//  Tier 2 (only when a sensor-1:1 /crop is already in hand) — the actually
//    saturated pixels INSIDE the fetched ROI are painted amber, so the user can
//    see *where* the clipping is (a blown core vs a hot column). /crop returns an
//    auto-stretched 8-bit PNG in which a full-well pixel lands on 255, so "== 255"
//    is a faithful saturated test (lib/clipMask.ts, Decision C).
//
// The Tier-2 paint is an SVG <image> so it lives in the SAME `.preview-transform`
// layer as everything else and can never drift from the base on pan/zoom (R6).
// Outside the ROI, Tier 1's frame keeps telling the truth — we never imply the
// unfetched region is clean.
import { useMemo } from "react";
import { maskToRgba, saturationMask } from "../../lib/clipMask";

export interface ClipCropPixels {
  /** RGBA of the sensor-1:1 crop (getImageData) */
  data: Uint8ClampedArray;
  /** crop pixel dims (sensor px) */
  pw: number;
  ph: number;
  /** where to paint it, in DISPLAY coordinates (the overlay <svg> viewBox space) */
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Amber fill opacity — visible over a bright core without hiding it. */
const MASK_ALPHA = 0.55;

export function ClipMaskLayer({
  w,
  h,
  active,
  cropPixels,
}: {
  w: number; // display image dims (transform space)
  h: number;
  active: boolean; // already gated by caller: linear+full_well+clip+stats.max>=full_well
  /** Tier 2 — present only when the pixel-peep crop layer already fetched an ROI. */
  cropPixels?: ClipCropPixels | null;
}) {
  // Tier-2 mask, rasterised once per crop into a data URL. Viewport-bounded, so
  // this is a few hundred k px at worst (R4).
  const maskUrl = useMemo(() => {
    if (!active || !cropPixels) return null;
    const { data, pw, ph } = cropPixels;
    if (!pw || !ph || data.length < pw * ph * 4) return null;
    const { mask, count } = saturationMask(data, pw, ph);
    if (count === 0) return null; // nothing saturated in view — paint nothing
    try {
      const canvas = document.createElement("canvas");
      canvas.width = pw;
      canvas.height = ph;
      const ctx = canvas.getContext("2d");
      if (!ctx) return null;
      ctx.putImageData(new ImageData(maskToRgba(mask, 255), pw, ph), 0, 0);
      return canvas.toDataURL("image/png");
    } catch {
      return null; // no canvas (SSR/test) — Tier 1 still stands
    }
  }, [active, cropPixels]);

  if (!active) return null;
  const inset = Math.max(2, Math.min(w, h) * 0.006);
  return (
    <g aria-hidden>
      <defs>
        <pattern id="clip-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" stroke="#d9a441" strokeWidth="1.4" />
        </pattern>
      </defs>
      {/* Tier 2 — per-pixel saturation paint inside the fetched ROI */}
      {maskUrl && cropPixels && (
        <image
          href={maskUrl}
          x={cropPixels.x}
          y={cropPixels.y}
          width={cropPixels.w}
          height={cropPixels.h}
          preserveAspectRatio="none"
          opacity={MASK_ALPHA}
          style={{ imageRendering: "pixelated" }}
        />
      )}
      {/* Tier 1 — hatched frame border (static) */}
      <rect
        x={inset}
        y={inset}
        width={w - inset * 2}
        height={h - inset * 2}
        fill="none"
        stroke="url(#clip-hatch)"
        strokeWidth={Math.max(6, Math.min(w, h) * 0.02)}
        vectorEffect="non-scaling-stroke"
      />
      <rect
        x={inset}
        y={inset}
        width={w - inset * 2}
        height={h - inset * 2}
        fill="none"
        stroke="#d9a441"
        strokeWidth={1}
        strokeDasharray="4 3"
        vectorEffect="non-scaling-stroke"
      />
    </g>
  );
}
