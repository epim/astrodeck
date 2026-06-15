// lut.ts — display-domain transfer functions shared by useImageRemap (canvas
// pixel remap) and StretchHistogram (transfer-curve overlay). Pure math, no DOM.
//
// The display image arriving from the server is ALREADY auto-stretched (8-bit).
// The client stretch is a *further* remap in display space so the user can push
// black/mid/white or a simple Brightness around the server's auto baseline. We
// model it as a standard MTF (midtones transfer function) with black/white
// clip points, identical in shape to the server stretch so Manual starts where
// the image already looks right.
import type { StretchParams } from "../../types";

/** PixInsight-style MTF: m=mid in (0,1). Maps x in [0,1] -> [0,1]. */
export function mtf(m: number, x: number): number {
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  if (m <= 0) return 1;
  if (m >= 1) return 0;
  if (x === m) return 0.5;
  return ((m - 1) * x) / ((2 * m - 1) * x - m);
}

/** Effective black/mid/white for the current stretch. In Auto mode the simple
 *  Brightness slider nudges `mid` (brighter = lower mid). In Manual mode the
 *  explicit B/M/W win. brightness in [-1,1] maps to a mid in roughly [0.85,0.15]. */
export function effectiveLevels(s: StretchParams): { black: number; mid: number; white: number } {
  if (s.auto) {
    // brightness -1..1 -> mid 0.85..0.15 (more brightness -> lower midtone)
    const mid = clamp01(0.5 - s.brightness * 0.35);
    return { black: 0, mid, white: 1 };
  }
  return {
    black: clamp01(s.black),
    mid: clamp01(s.mid),
    white: clamp01(Math.max(s.white, s.black + 0.001)),
  };
}

/** value in [0,1] after black/white clip + midtone transfer. */
export function transfer(s: StretchParams, x01: number): number {
  const { black, mid, white } = effectiveLevels(s);
  const span = Math.max(white - black, 1e-4);
  const t = clamp01((x01 - black) / span);
  // mid is expressed relative to the [black,white] window; convert to MTF m by
  // remapping the window so mtf's midpoint sits at the requested mid.
  const m = clamp01((mid - black) / span);
  return mtf(m, t);
}

/** 256-entry uint8 LUT for the canvas pixel pass. Identity when neutral. */
export function buildLut(s: StretchParams): Uint8ClampedArray {
  const lut = new Uint8ClampedArray(256);
  for (let i = 0; i < 256; i++) {
    lut[i] = Math.round(transfer(s, i / 255) * 255);
  }
  return lut;
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}
