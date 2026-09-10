// fov.ts — field-of-view, sampling and mosaic math for the Settings "Optics"
// sheet and the Sky hub's framing card (README "11. Settings" -> Optics sheet;
// "Formulas to lift" -> FoV + Mosaic):
//
//   fovW = 2*atan(sensorW / 2 / (fl*reducer)), fovH likewise
//   sampling = 206.265 * px_um / fl_mm  ("/px; under-sampled > 2", over-sampled < 0.7"
//   panel pitch = FoV*(1 - overlap); panels cycle every pass
//
// NOTE on reuse: `ui/src/lib/framing.ts` already has `fovFromOptics` and
// `mosaicGrid`, but they solve a different problem (the live Atlas overlay: FOV
// from pixel counts via the server's linear small-angle approximation, and
// panels as real J2000 RA/Dec coordinates for plate-solved centering). This
// module implements the README's own atan()-based, mm-input formula for the
// Optics preview screen verbatim (worked example: 23.5mm sensor at 530mm,
// reducer 1 -> 2.54 deg, matching the README), plus a simpler panel-INDEX
// cycling order (`panelOrder`) for capture planning, not sky coordinates. The
// two modules are deliberately not merged.

const R2D = 180 / Math.PI;
export const ARCSEC_PER_RAD = 206.265;

export interface OpticsMm {
  sensorWmm: number;
  sensorHmm: number;
  flMm: number;
  /** Focal reducer/extender multiplier; 1 = none. */
  reducer?: number;
}

export interface FovDeg {
  wDeg: number;
  hDeg: number;
}

/** `2*atan(sensor/2/(fl*reducer))` in degrees, both axes. Zero when the optics
 *  are unusable (fl/reducer <= 0) rather than NaN/Infinity. */
export function fovDeg(o: OpticsMm): FovDeg {
  const reducer = o.reducer != null && o.reducer > 0 ? o.reducer : 1;
  const eff = o.flMm * reducer;
  if (!(eff > 0)) return { wDeg: 0, hDeg: 0 };
  const wDeg = 2 * Math.atan(o.sensorWmm / 2 / eff) * R2D;
  const hDeg = 2 * Math.atan(o.sensorHmm / 2 / eff) * R2D;
  return { wDeg, hDeg };
}

/** Pixel scale in arcsec/px: `206.265 * px_um / fl_mm`. */
export function samplingArcsecPerPx(pxUm: number, flMm: number): number {
  return flMm > 0 ? (ARCSEC_PER_RAD * pxUm) / flMm : 0;
}

export type SamplingVerdict = "under-sampled" | "well sampled" | "over-sampled";

/** README: under-sampled above 2"/px, over-sampled below 0.7"/px. */
export function samplingVerdict(v: number): SamplingVerdict {
  if (v > 2) return "under-sampled";
  if (v < 0.7) return "over-sampled";
  return "well sampled";
}

/** Mosaic panel pitch in degrees: `fov * (1 - overlap)`. */
export function mosaicPitch(fovDegValue: number, overlap = 0.15): number {
  return fovDegValue * (1 - overlap);
}

/**
 * Row-major panel indices for a `cols` x `rows` mosaic, rotated so each `pass`
 * starts on a different panel (README: "the flow centres on each panel and
 * cycles panels every pass so a shortened night leaves every panel with
 * data"). `pass` 0 starts at panel 0; pass 1 starts at panel 1; etc, wrapping.
 */
export function panelOrder(cols: number, rows: number, pass: number): { row: number; col: number }[] {
  const c = Math.max(1, Math.round(cols));
  const r = Math.max(1, Math.round(rows));
  const total = c * r;
  const base: { row: number; col: number }[] = [];
  for (let row = 0; row < r; row++) {
    for (let col = 0; col < c; col++) base.push({ row, col });
  }
  const offset = ((Math.round(pass) % total) + total) % total;
  return [...base.slice(offset), ...base.slice(0, offset)];
}
