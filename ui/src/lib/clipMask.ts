// clipMask.ts — per-pixel saturation mask derived from a sensor-1:1 /crop.
// (crop+render UI design §2.3, Decision C)
//
// /crop returns an 8-bit L-mode PNG that has been AUTO-stretched, and auto_stretch
// clamps highlights — so a pixel at/above full well lands on 255. "== 255" is
// therefore a faithful saturated-pixel test on that crop (Decision C).
//
// HONESTY BOUNDARIES (both are real and both are documented in the UI):
//  - This is Tier 2. The authoritative "this frame clips" signal stays Tier 1's
//    frame-level `stats.max >= full_well`, which needs no crop and never lies.
//  - The mask covers ONLY the fetched ROI. Outside it we say nothing new.
//  - On an OSC frame the preview is a mono render, so the mask is per-PREVIEW-
//    pixel, not per-Bayer-channel. It is an indicator, not photometry.

/** Amber that matches the Tier-1 hatch frame in ClipMaskLayer. */
export const CLIP_RGB: [number, number, number] = [217, 164, 65];
/** Saturated == 255 on the auto-stretched 8-bit crop (Decision C). */
export const CLIP_THRESHOLD = 255;

export interface MaskResult {
  /** 1 byte per pixel: 1 == saturated. */
  mask: Uint8Array;
  /** how many pixels tripped the threshold */
  count: number;
  /** count / (w*h), 0..1 */
  fraction: number;
}

/**
 * Threshold an RGBA buffer (as returned by getImageData) into a saturation mask.
 * The crop is L-mode so R==G==B; we read R and ignore alpha.
 */
export function saturationMask(
  rgba: Uint8ClampedArray | Uint8Array,
  w: number,
  h: number,
  threshold: number = CLIP_THRESHOLD,
): MaskResult {
  const n = Math.max(0, Math.floor(w)) * Math.max(0, Math.floor(h));
  const mask = new Uint8Array(n);
  let count = 0;
  for (let i = 0; i < n; i++) {
    if (rgba[i * 4] >= threshold) {
      mask[i] = 1;
      count++;
    }
  }
  return { mask, count, fraction: n > 0 ? count / n : 0 };
}

/**
 * Paint a mask into an RGBA buffer ready for putImageData: opaque amber where
 * saturated, fully transparent everywhere else (so the base pixels show through
 * untouched — we tint, we don't repaint).
 */
export function maskToRgba(
  mask: Uint8Array,
  alpha: number = 255,
  rgb: [number, number, number] = CLIP_RGB,
): Uint8ClampedArray {
  const out = new Uint8ClampedArray(mask.length * 4);
  const a = Math.max(0, Math.min(255, Math.round(alpha)));
  for (let i = 0; i < mask.length; i++) {
    if (!mask[i]) continue;
    const o = i * 4;
    out[o] = rgb[0];
    out[o + 1] = rgb[1];
    out[o + 2] = rgb[2];
    out[o + 3] = a;
  }
  return out;
}
