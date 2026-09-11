// opticsModel.ts - the arithmetic behind the Optics sheet. Pure: no React, no
// store, no fetch.
//
// THREE THINGS THIS FILE EXISTS TO KEEP STRAIGHT.
//
// 1. APERTURE AND REDUCER ARE RIG FIELDS NOW (D-SET-1). `ui/src/types.ts`
//    `Optics.aperture_mm`/`reducer` join the same draft `focal_length_mm`
//    already lives in, and the same `PUT /api/optics` press saves all three -
//    the last piece of rig data that used to live only on this phone
//    (`astrodeck-next-optics-aux`, migrated once by `OpticsSheet.tsx`'s mount
//    effect through `next/lib/storageMigration.ts`). `reducer` is still
//    RECORDED, NEVER MULTIPLIED: `focal_length_mm` stays the explicit number
//    the framing maths, the solve hint and the FITS header all use, so nothing
//    multiplies it behind the operator's back. `USE THE REDUCED FOCAL LENGTH`
//    is still how a reducer becomes real, and it is one press the user makes,
//    not a silent write - that did not change when the fields moved to the rig.
//
// 2. THE NUMBER THE RIG USES COMES FROM THE SERVER. `config.optics_computed` is
//    what plate-solve hints, the framing overlay and the FITS cards are drawn
//    from. This module's `fovDeg`-based preview is for the DRAFT, so a drag has
//    a visible consequence before it is saved; on save the readout snaps back to
//    the server's answer, and `fovDisagrees` says so when the two differ (which
//    is the profile-override case, not a rounding artefact).
//
// 3. CALIBRATION MAY ONLY READ A PLATE SOLVE. `PreviewField.source` is
//    `"solve" | "pointing"` and types.ts:477-479 says why: "`pointing` = the
//    MOUNT'S OWN CLAIM, offered to a human and never written: this rig's AM5 has
//    no brake and has been found 50 degrees from where it claimed." So
//    `solveCalibration` returns null for anything but a solve rather than
//    offering a focal length derived from a claim.

import {
  ARCSEC_PER_RAD,
  fovDeg,
  samplingArcsecPerPx,
  samplingVerdict,
  type FovDeg,
} from "../../../lib/fov";
import type { Optics, OpticsComputed, PreviewField } from "../../../../types";

// ------------------------------------------------- the legacy local aux (gone)
//
// `astrodeck-next-optics-aux` used to be this file's home for aperture and
// reducer, before `config.py:98,111` gave them a server field. The key, its
// shape and a parser survive HERE ONLY for two remaining readers, both in
// `OpticsSheet.tsx`: the one-time migration's `read` (through
// `next/lib/storageMigration.ts`'s `migrateKey`), and the read-only fallback a
// role without `config.site_optics` gets while nobody able to write has run
// the migration yet. Nothing writes this key any more - there is no
// `writeOpticsAux` to pair with it.

export const OPTICS_AUX_KEY = "astrodeck-next-optics-aux";

export interface LegacyOpticsAux {
  /** Clear aperture in mm, or null when the phone never had one. */
  apertureMm: number | null;
  /** Focal reducer / extender multiplier; 1 = none. */
  reducer: number;
}

/** Parse the raw stored string, or null for anything unusable. An unreadable
 *  shape is treated the same as "nothing to move" by the migration - see
 *  `storageMigration.ts`'s own rule that a value this build cannot read may
 *  still be one another build wrote, and is left in place rather than lost. */
export function parseLegacyOpticsAux(raw: string): LegacyOpticsAux | null {
  try {
    const p = JSON.parse(raw) as Partial<LegacyOpticsAux>;
    const apertureMm = typeof p.apertureMm === "number" && p.apertureMm > 0 ? p.apertureMm : null;
    const reducer = typeof p.reducer === "number" && p.reducer > 0 ? p.reducer : 1;
    return { apertureMm, reducer };
  } catch {
    return null;
  }
}

/** The one info toast the migration ever raises - only on the CONFLICT path
 *  (`serverHasValue: true`, the rig already had a non-zero aperture), because
 *  its wording describes a replacement: the phone's copy is discarded, not
 *  merged, in favour of the rig's own value. The silent path - the rig had
 *  none yet, so the phone's values become the rig's - raises nothing, on the
 *  same "moving a value nobody knew was local is not news" rule the planning
 *  keys use. */
export const OPTICS_MIGRATION_TOAST =
  "Optics moved to the rig - the phone's copy of aperture and reducer was replaced by the rig's.";

// ------------------------------------------------------------------ dial stops

/** The dial ladders. `FOCAL_STOPS` spans the plan's 250-2000 mm; the field
 *  itself still validates 1-20000, which is why `dialStops` always splices the
 *  CURRENT value in: a 2350 mm scope must not silently read as 250 because its
 *  value fell off the end of a ladder. */
export const FOCAL_STOPS = [
  250, 300, 350, 400, 450, 500, 530, 600, 700, 800, 900, 1000, 1200, 1500, 1800, 2000,
];
export const APERTURE_STOPS = [50, 60, 70, 80, 90, 100, 106, 120, 130, 150, 180, 200, 250];
export const REDUCER_STOPS = [
  0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1, 1.05, 1.1, 1.25, 1.4, 1.5, 1.75, 2,
];
export const PIXEL_STOPS = [2.4, 2.9, 3.76, 3.8, 4.63, 4.8, 5.4, 5.94];

/** The ladder with `current` spliced in, sorted, de-duplicated at `digits`
 *  precision. A Dial whose value is absent from its options renders index 0 as
 *  selected, which is a readout that disagrees with the value it is editing. */
export function dialStops(base: number[], current: number, digits = 2): number[] {
  const key = (n: number): string => n.toFixed(digits);
  const seen = new Set<string>();
  const out: number[] = [];
  for (const n of [...base, ...(Number.isFinite(current) && current > 0 ? [current] : [])]) {
    const k = key(n);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(n);
  }
  return out.sort((a, b) => a - b);
}

// ------------------------------------------------------------------- readouts

/** `f/5.0`, or the honest absence. Never invents an aperture. */
export function fRatioLabel(flMm: number, apertureMm: number | null): string {
  if (!(apertureMm && apertureMm > 0) || !(flMm > 0)) return "aperture not set";
  return `f/${(flMm / apertureMm).toFixed(1)}`;
}

/** The f-ratio text for the tile and the live line.
 *
 *  The SERVER's own number (`config.optics_computed.f_ratio`) is used only
 *  when the draft is exactly what the server last computed from - so the
 *  figure on screen is one the rig actually derived, never a guess made while
 *  a drag is in flight. Every other case falls back to the local arithmetic
 *  (`fRatioLabel`), SILENTLY: an engine old enough that `f_ratio` never
 *  arrived gets the identical formula and no note about it, because there is
 *  nothing to explain. 0 is `config.py`'s own unset convention for the
 *  aperture, so an aperture of 0 always reads as "aperture not set" - never
 *  `f/Infinity` or `f/0`, from either source. */
export function fRatioFrom(
  computed: OpticsComputed | null | undefined,
  draftFl: number,
  draftAperture: number,
): string {
  if (!(draftAperture > 0)) return "aperture not set";
  if (
    computed
    && computed.focal_length_mm === draftFl
    && computed.aperture_mm === draftAperture
    && typeof computed.f_ratio === "number"
  ) {
    return `f/${computed.f_ratio.toFixed(1)}`;
  }
  return fRatioLabel(draftFl, draftAperture);
}

/** What `USE THE REDUCED FOCAL LENGTH` writes into the draft. */
export function reducedFocalMm(nativeMm: number, reducer: number): number {
  return Math.round(nativeMm * (reducer > 0 ? reducer : 1));
}

/** `OpticsPanel`'s rule, verbatim in effect: 1-20000 mm. */
export function focalInvalid(mm: number): boolean {
  return !(mm > 0 && mm <= 20000);
}

export interface DraftView {
  flMm: number;
  pxUm: number;
  wPx: number;
  hPx: number;
}

/** The four numbers the preview needs, with the camera's own values standing in
 *  wherever the draft holds 0 - which is exactly what `0 falls back to the
 *  camera` means on the server, so the preview must show the same thing the rig
 *  would do rather than a field of zero degrees. */
export function resolveDraft(
  draft: Optics,
  computed: OpticsComputed | null | undefined,
): DraftView {
  return {
    flMm: draft.focal_length_mm || computed?.focal_length_mm || 0,
    pxUm: draft.pixel_size_um || computed?.pixel_size_um || 0,
    wPx: draft.sensor_width_px || computed?.sensor_width_px || 0,
    hPx: draft.sensor_height_px || computed?.sensor_height_px || 0,
  };
}

/** Sensor size in mm from the pixel count and the pixel pitch. */
export function sensorMm(v: DraftView): { wMm: number; hMm: number } {
  return { wMm: (v.wPx * v.pxUm) / 1000, hMm: (v.hPx * v.pxUm) / 1000 };
}

/** The DRAFT's field, through the shipped `next/lib/fov` formula. */
export function draftFov(v: DraftView, reducer: number): FovDeg {
  const { wMm, hMm } = sensorMm(v);
  return fovDeg({ sensorWmm: wMm, sensorHmm: hMm, flMm: v.flMm, reducer });
}

/** The sheet's live line: `530 mm · f/5.0 · 3.76 um · 2.54 x 1.70 deg`. Takes
 *  the f-ratio text already resolved by `fRatioFrom` rather than an aperture
 *  number, so this function stays agnostic about which source (the server's
 *  own computation or the local fallback) produced it. */
export function liveLine(
  flMm: number,
  fRatioText: string,
  pxUm: number,
  fov: FovDeg,
): string {
  const parts = [
    flMm > 0 ? `${Math.round(flMm)} mm` : "focal length not set",
    fRatioText,
    pxUm > 0 ? `${Number(pxUm.toFixed(2))} µm` : "pixel size not known",
    fov.wDeg > 0 ? `${fov.wDeg.toFixed(2)}° × ${fov.hDeg.toFixed(2)}°` : "field not computable",
  ];
  return parts.join(" · ");
}

/** The caption under the preview: `2.54° × 1.70° field · 1.46″ per pixel · well
 *  sampled`. Returns the reason rather than a zero when the inputs are missing -
 *  a 0.00 degree field reads as a measurement. */
export function fovCaption(fov: FovDeg, pxUm: number, flMm: number): string {
  if (!(fov.wDeg > 0) || !(pxUm > 0) || !(flMm > 0)) {
    return "field unknown - set the focal length, and connect a camera or pin the sensor below";
  }
  const samp = samplingArcsecPerPx(pxUm, flMm);
  return `${fov.wDeg.toFixed(2)}° × ${fov.hDeg.toFixed(2)}° field · ${samp.toFixed(2)}″ per pixel · ${samplingVerdict(samp)}`;
}

/** The server's own field, when it has one. `have_optics` false means the rig
 *  cannot frame anything yet, and that is a different claim from "zero". */
export function computedFov(c: OpticsComputed | null | undefined): FovDeg | null {
  if (!c?.have_optics) return null;
  if (!(c.fov_w_deg && c.fov_w_deg > 0) || !(c.fov_h_deg && c.fov_h_deg > 0)) return null;
  return { wDeg: c.fov_w_deg, hDeg: c.fov_h_deg };
}

/** True when what the rig frames and what these values give differ by more than
 *  2 % - the provider-override case (a profile's optics block replaces the whole
 *  thing), which the sheet must say out loud rather than hide behind a preview
 *  drawn from the losing layer. 2 % is wider than any rounding in the two
 *  formulas (the server's linear small-angle approximation vs this module's
 *  atan) and narrower than any real optics swap. */
export function fovDisagrees(server: FovDeg | null, local: FovDeg): boolean {
  if (!server || !(local.wDeg > 0)) return false;
  return Math.abs(server.wDeg - local.wDeg) / local.wDeg > 0.02;
}

// ------------------------------------------------- calibrate from a plate solve

export interface SolveCalibration {
  arcsecPerPx: number;
  focalMm: number;
  solvedAt: number;
}

/**
 * The focal length the last PLATE SOLVE implies, or null.
 *
 *   arcsec/px = fov_w_deg * 3600 / data_width
 *   fl_mm     = 206.265 * pixel_size_um / arcsec-per-px
 *
 * `source !== "solve"` returns null on purpose. A `pointing` field is the
 * mount's own claim, and this rig's AM5 has been found 50 degrees from where it
 * claimed - calibrating optics from it would launder a guess into a setting.
 */
export function solveCalibration(
  field: PreviewField | null | undefined,
  pixelSizeUm: number,
): SolveCalibration | null {
  if (!field || field.source !== "solve") return null;
  const fovW = field.fov_w_deg;
  const width = field.data_width;
  if (!(fovW && fovW > 0) || !(width && width > 0) || !(pixelSizeUm > 0)) return null;
  const arcsecPerPx = (fovW * 3600) / width;
  if (!(arcsecPerPx > 0)) return null;
  const focalMm = (ARCSEC_PER_RAD * pixelSizeUm) / arcsecPerPx;
  if (!(focalMm > 0) || !Number.isFinite(focalMm)) return null;
  return { arcsecPerPx, focalMm, solvedAt: field.solved_at ?? 0 };
}
