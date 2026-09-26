// fov.ts - field-of-view, sampling and mosaic math for the Settings "Optics"
// sheet (README "11. Settings" -> Optics sheet; "Formulas to lift" -> FoV +
// Mosaic):
//
//   fovW = 2*atan(sensorW / 2 / (fl*reducer)), fovH likewise
//   sampling = 206.265 * px_um / fl_mm  ("/px; under-sampled > 2", over-sampled < 0.7"
//   panel pitch = FoV*(1 - overlap)
//
// The Sky hub's framing card does not read this module: its panels come from
// the server (`hubs/sky/frame/mosaic.ts`) with `lib/framing.ts` as the offline
// mirror. The README's formula list also said the panels rotate from pass to
// pass, and that is not what a night does today - see `panelOrder` (#154).
//
// NOTE on reuse: `ui/src/lib/framing.ts` already has `fovFromOptics` and
// `mosaicGrid`, but they solve a different problem (the live Atlas overlay: FOV
// from pixel counts via the server's linear small-angle approximation, and
// panels as real J2000 RA/Dec coordinates for plate-solved centering). This
// module implements the README's own atan()-based, mm-input formula for the
// Optics preview screen verbatim (worked example: 23.5mm sensor at 530mm,
// reducer 1 -> 2.54 deg, matching the README), plus a panel-INDEX order
// (`panelOrder`), not sky coordinates, that only its test calls. The two
// modules are deliberately not merged.

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
 * Row-major panel indices for a `cols` x `rows` mosaic, rotated so pass `k`
 * starts on panel `k` modulo the panel count: pass 0 starts at panel 0, pass 1
 * at panel 1, and so on, wrapping.
 *
 * NOTHING SHOOTS IN THIS ORDER, and only its test calls it. It was written for
 * the design README's rotating mosaic, and the Sky copy used to promise that
 * rotation as if the night kept it (#154). It does not: a Sky mosaic reaches
 * the classic Plan as targets sharing one `mosaic_group`, and the engine's
 * `_run_scheduled` runs each target to completion before it picks the next, so
 * the panels are shot panel-first. The order a night takes is the engine's to
 * decide; anything that wants to show it must read it from the server, never
 * compute it here.
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
