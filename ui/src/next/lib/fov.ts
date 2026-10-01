// fov.ts - field-of-view, sampling and mosaic math for the Settings "Optics"
// sheet (README "11. Settings" -> Optics sheet; "Formulas to lift" -> FoV +
// Mosaic):
//
//   fovW = (sensorW / fl) * (180/pi), fovH likewise
//   sampling = 206.265 * px_um / fl_mm  ("/px; under-sampled > 2", over-sampled < 0.7"
//   panel pitch = FoV*(1 - overlap)
//
// The Sky hub's framing card does not read this module: its panels come from
// the server (`hubs/sky/frame/mosaic.ts`) with `lib/framing.ts` as the offline
// mirror. The README's formula list also gave the panels a fixed rotation from
// pass to pass, and no night takes that order - see `panelOrder` (#154).
//
// THE OVERLAP DEFAULT IS NOT THIS FILE'S. `mosaicPitch` defaults to
// `lib/framing.ts`'s `DEFAULT_OVERLAP`, the one overlap every framing starts
// from (spec 2026-09-23 flows mosaic, 2.4). It said 0.15, the README's number,
// while the Atlas, the Target modal and the server said 0.25, so the Optics
// preview priced a mosaic at a pitch no framing of it would be laid out at.
//
// FOV ITSELF IS NOW THE SAME IMPORT (#168). `fovDeg` used to carry its own
// atan()-based formula AND multiply in a focal reducer that `lib/framing.ts`'s
// `fovFromOptics` and the server's `config.fov_deg` both ignore -- so a
// recorded-but-never-applied reducer (`server/astrodeck/config.py`:97-111,
// `Optics.reducer`) made this module's Settings-sheet preview and the Atlas
// overlay disagree about the field for the SAME rig, for every reducer other
// than 1. `fovDeg` now calls `lib/framing.ts`'s `fovDegFromSensorMm` directly
// -- the one FOV formula every reader in the tree uses -- so it can't drift
// from `fovFromOptics` again. `panelOrder`, below, is still this module's
// own: a panel-INDEX order, not sky coordinates, that only its test calls.
// The two modules stay separate files for that (and `mosaicPitch`/sampling,
// which `lib/framing.ts` has no use for), not for a second FOV formula.

import { DEFAULT_OVERLAP, fovDegFromSensorMm } from "../../lib/framing";

export const ARCSEC_PER_RAD = 206.265;

export interface OpticsMm {
  sensorWmm: number;
  sensorHmm: number;
  flMm: number;
  /** Focal reducer/extender multiplier; 1 = none. ACCEPTED, NEVER APPLIED
   *  (#168): `server/astrodeck/config.py`'s `Optics.reducer` (config.py:97-
   *  111) and its `f_ratio` doc are explicit that this field never silently
   *  changes what the rig frames, so `fovDeg` ignores it too -- the same way
   *  `fovFromOptics` (`ui/src/lib/framing.ts`) and the server's `fov_deg` do.
   *  This field stays on the type only so existing callers (`opticsModel.ts`'s
   *  `draftFov`) keep compiling; pass the ALREADY-reduced `flMm` (what "USE
   *  THE REDUCED FOCAL LENGTH" writes) if the reducer should be reflected. */
  reducer?: number;
}

export interface FovDeg {
  wDeg: number;
  hDeg: number;
}

/** `(sensor / fl) * (180/pi)` in degrees, both axes, via `lib/framing.ts`'s
 *  `fovDegFromSensorMm` -- the SAME formula the Atlas overlay's `fovFromOptics`
 *  uses, so this Settings-sheet preview can never compute a different field
 *  from the same optics (#168). `o.reducer` is accepted for call-site
 *  compatibility and is NEVER multiplied in -- see the field's own doc above.
 *  Zero when the optics are unusable (`flMm` <= 0) rather than NaN/Infinity. */
export function fovDeg(o: OpticsMm): FovDeg {
  return {
    wDeg: fovDegFromSensorMm(o.sensorWmm, o.flMm),
    hDeg: fovDegFromSensorMm(o.sensorHmm, o.flMm),
  };
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

/** Mosaic panel pitch in degrees: `fov * (1 - overlap)`, the overlap a
 *  fraction, `DEFAULT_OVERLAP` when none is given. */
export function mosaicPitch(fovDegValue: number, overlap = DEFAULT_OVERLAP): number {
  return fovDegValue * (1 - overlap);
}

/**
 * Row-major panel indices for a `cols` x `rows` mosaic, rotated so pass `k`
 * starts on panel `k` modulo the panel count: pass 0 starts at panel 0, pass 1
 * at panel 1, and so on, wrapping.
 *
 * NOTHING SHOOTS IN THIS ORDER, and only its test calls it. It was written for
 * the design README's rotating mosaic, and the Sky copy used to promise that
 * rotation as if the night kept it (#154). Until S6 a Sky mosaic reached the
 * classic Plan as targets sharing one `mosaic_group`, shot panel-first. Since
 * S6 (#196) it reaches the night through Send to Flow Wizard as one TARGET
 * block, and the engine picks each visit from what every panel has banked
 * (the block's `order`, least complete first by default, spec D3), which no
 * fixed rotation reproduces. The order a night takes is the engine's to
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
