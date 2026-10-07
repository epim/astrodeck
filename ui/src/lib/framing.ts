// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Framing math — the client mirror of `server/astrodeck/catalog/framing.py`.
// Pure functions, NO React. Used for the zero-latency live FOV/mosaic overlay
// while the user drags. Nothing here is ever slewed to: a mosaic reaches a
// night as one TARGET block through Send to Flow Wizard (#196), and the
// server lays its panels out with framing.py at every compile, so this mirror
// only draws (design spec §1, §5).
//
// ── J2000 INVARIANT (spec §9, critique C2-#7) ──────────────────────────────
// Every coordinate here is J2000 / ICRS. The curated catalog is J2000, the
// survey cutout is requested `coordsys=icrs`, and the mount plate-solves+centers
// on the real sky — all consistent end-to-end. NEVER mix the live JNow mount RA
// into overlay registration: the overlay frame is always J2000. project()/
// deproject() take and return J2000 RA/Dec; callers must not pass precessed
// (JNow) coordinates in.
//
// Projection is gnomonic / TAN (tangent plane), matching the `projection=TAN`
// survey cutout so the deg->px scale over the W×W image is linear and uniform.

import { ARCSEC_PER_RAD } from "./optics";

const DEG = Math.PI / 180;
const RAD = 180 / Math.PI;

/** ρ→0 guard threshold (spec §5). Below this the tangent point IS the center, so
 *  the inverse projection returns (ra0,dec0) verbatim — never divides by ρ and
 *  never ships NaN to the mount on the common "open on target, hit Send" path. */
export const RHO_EPS = 1e-12;

/** THE overlap a new framing starts from, as a fraction: server
 *  `framing.DEFAULT_OVERLAP`, mirrored (spec 2026-09-23 flows mosaic, 2.4:
 *  "one server constant"). There were three: `store.openFraming` seeded 0.25,
 *  the Sky's FRAME mode re-set every session to 0.15 so that its copy
 *  ("Panels overlap 15%") and its pitch agreed, and the Target modal read a
 *  missing overlap as 25%. A Sky framing sent to the wizard then arrived at
 *  15% while the same object framed in the Atlas arrived at 25%, and nothing
 *  said which one the engine would be asked for. Every reader imports this
 *  one: the store's seed, the Sky hub's resets and its panel pitch
 *  (`next/hubs/sky/frame/mosaic.ts`), the Settings mosaic pitch
 *  (`next/lib/fov.ts`) and the modal's missing-key reading
 *  (`framingModel.layoutOf`). `server/tests/test_overlap_constant_one.py`
 *  reads THIS line and holds it to the server's constant, so the line keeps
 *  its shape: `export const DEFAULT_OVERLAP = <number>;`. */
export const DEFAULT_OVERLAP = 0.25;

export interface FovFromOptics {
  fov_x_deg: number;
  fov_y_deg: number;
  pixel_scale_arcsec: number;
}

/** The 4 fields the FOV math needs — config Optics and camera-merged
 *  OpticsComputed both satisfy it structurally. */
export interface OpticsLike {
  focal_length_mm: number;
  pixel_size_um: number;
  sensor_width_px: number;
  sensor_height_px: number;
}

/** Human names of whichever optics fields are still missing (<= 0), for the
 *  warning banner (wave-1 §3.2). Empty array == optics usable. */
export function missingOpticsFields(o: OpticsLike | null | undefined): string[] {
  if (!o) return ["focal length", "pixel size", "sensor size"];
  const missing: string[] = [];
  if (!(o.focal_length_mm > 0)) missing.push("focal length");
  if (!(o.pixel_size_um > 0)) missing.push("pixel size");
  if (!(o.sensor_width_px > 0) || !(o.sensor_height_px > 0)) missing.push("sensor size");
  return missing;
}

export interface ProjectedXiEta {
  xi: number; // standard coordinate ξ, degrees (East/RA-like, +toward +RA)
  eta: number; // standard coordinate η, degrees (North/Dec-like, +toward +Dec)
}

export interface SkyCoord {
  ra_hours: number;
  dec_deg: number;
}

export interface MosaicGridSpec {
  ra_hours: number;
  dec_deg: number;
  rows: number; // 1..10
  cols: number; // 1..10
  overlap: number; // 0..0.5
  rotation_deg: number; // PA applied to the whole mosaic
  fov_x_deg: number; // single-frame FOV at bin 1
  fov_y_deg: number;
}

export interface GridPanel {
  row: number;
  col: number;
  ra_hours: number; // already %24-wrapped, in [0,24)
  dec_deg: number;
  /** The LAYOUT angle: the one the grid is turned by, and the one the Atlas
   *  draws every panel's rectangle at (PanelLayer). Not the camera angle of
   *  a corrected panel, see `pa_deg`. */
  rotation_deg: number;
  /** Meridian convergence at this panel: local north on the GRID's tangent
   *  plane, degrees from +eta toward +xi (east). Server
   *  `framing.panel_convergence_deg`; positive west of the centre, negative
   *  east of it, 0 on the centre meridian. */
  convergence_deg: number;
  /** The camera's sky position angle for this panel, in [0,360): the layout
   *  angle plus `convergence_deg`, so that the frame lies on the grid (#175).
   *  Image up is north rotated toward WEST by it (CROTA2, confirmed on a
   *  real solve). What a rotating block is commanded per panel. */
  pa_deg: number;
}

// ----------------------------------------------------------------- FOV math
/**
 * THE ONE FOV FORMULA (#168). Linear small-angle: `(sensorMm / focalMm) *
 * (180/pi)` degrees. Every reader of a sensor dimension + focal length in
 * this tree goes through this — `fovFromOptics` below (which derives
 * `sensorMm` from a pixel count * `pixel_size_um`), and `next/lib/fov.ts`'s
 * `fovDeg` (the Settings Optics-sheet preview, which already has the sensor
 * size in mm and calls this directly) — so the Atlas overlay, the Settings
 * preview and the server's `config.fov_deg` (same formula, pixel-count
 * inputs) can no longer compute three different fields for one rig.
 *
 * TAKES NO REDUCER, on purpose: `Optics.reducer` (`server/astrodeck/config.py`
 * :97-111) is recorded, never multiplied — the same rule `f_ratio` documents
 * there. If "USE THE REDUCED FOCAL LENGTH" was pressed, `focalMm` already
 * carries it; if not, the rig genuinely frames at the explicit focal length,
 * and that is what every FOV reader must show. A caller that folds the
 * reducer in before calling this is wrong in exactly the way #168 was: the
 * two UIs disagreeing over a reducer that never changed what the rig frames.
 *
 * Zero when either input is unusable, never NaN/Infinity.
 */
export function fovDegFromSensorMm(sensorMm: number, focalMm: number): number {
  if (!(focalMm > 0) || !(sensorMm > 0)) return 0;
  return (sensorMm / focalMm) * RAD;
}

/**
 * Field of view + pixel scale from optics, ALWAYS at bin 1 (spec §5). Binning
 * affects only the displayed pixel-scale readout; panel tiling uses bin-1 FOV.
 *
 * `focalMm` overrides `optics.focal_length_mm` (the inline Atlas focal field).
 * Returns zeros when optics are unusable (focal/pixel <= 0) so the caller can
 * draw the dashed placeholder + CTA instead of NaN geometry.
 */
export function fovFromOptics(
  optics: OpticsLike | null | undefined,
  focalMm?: number,
): FovFromOptics {
  const zero: FovFromOptics = { fov_x_deg: 0, fov_y_deg: 0, pixel_scale_arcsec: 0 };
  if (!optics) return zero;
  const fl = focalMm != null && focalMm > 0 ? focalMm : optics.focal_length_mm;
  const px = optics.pixel_size_um;
  const w = optics.sensor_width_px;
  const h = optics.sensor_height_px;
  if (!(fl > 0) || !(px > 0) || !(w > 0) || !(h > 0)) return zero;

  // pixel scale (arcsec/px) at bin 1: 206.265 * pixel_size_um / focal_length_mm
  const pixel_scale_arcsec = (ARCSEC_PER_RAD * px) / fl;
  // FOV via fovDegFromSensorMm — sensor size in mm is pixel count * pixel
  // pitch (um -> mm is the /1000) — matching config.py `fov_deg` and
  // lib/optics.ts so Atlas shows the same frame size as Settings/Mount for
  // one rig, and matching `next/lib/fov.ts`'s `fovDeg` (#168: one formula).
  const fov_x_deg = fovDegFromSensorMm((px * w) / 1000, fl);
  const fov_y_deg = fovDegFromSensorMm((px * h) / 1000, fl);
  return { fov_x_deg, fov_y_deg, pixel_scale_arcsec };
}

/** Plausibility note for a pixel scale (spec §5, critique C1-D1) — makes a
 *  focal-length typo (800 vs 80) visible immediately. `null` for the typical
 *  1–4"/px range (no note needed). */
export function plausibilityHint(pixel_scale_arcsec: number): string | null {
  if (!(pixel_scale_arcsec > 0)) return null;
  if (pixel_scale_arcsec < 0.7) return "very high resolution";
  if (pixel_scale_arcsec > 5) return "very wide field";
  return null;
}

// ------------------------------------------------------- gnomonic projection
/**
 * Forward gnomonic (TAN) projection: sky (ra,dec) -> standard coords (ξ,η) in
 * DEGREES, relative to tangent point (ra0,dec0). All angles J2000 (see header).
 */
export function project(
  ra_hours: number,
  dec_deg: number,
  ra0_hours: number,
  dec0_deg: number,
): ProjectedXiEta {
  const ra = ra_hours * 15 * DEG; // hours -> degrees -> radians
  const dec = dec_deg * DEG;
  const ra0 = ra0_hours * 15 * DEG;
  const dec0 = dec0_deg * DEG;
  const dra = ra - ra0;

  const sinDec = Math.sin(dec);
  const cosDec = Math.cos(dec);
  const sinDec0 = Math.sin(dec0);
  const cosDec0 = Math.cos(dec0);
  const cosDra = Math.cos(dra);

  // h is the cosine of the angular distance from the tangent point.
  const h = sinDec * sinDec0 + cosDec * cosDec0 * cosDra;
  // Guard a point exactly opposite the tangent point (h->0). Return a huge but
  // finite offset rather than Infinity so the overlay clips off-canvas cleanly.
  const safeH = Math.abs(h) < 1e-12 ? 1e-12 : h;

  const xi = (cosDec * Math.sin(dra)) / safeH;
  const eta = (sinDec * cosDec0 - cosDec * sinDec0 * cosDra) / safeH;
  return { xi: xi * RAD, eta: eta * RAD };
}

/**
 * Inverse gnomonic (TAN): standard coords (ξ,η in DEGREES) -> sky (ra,dec).
 * ρ→0 is special-cased to the tangent point to avoid a divide-by-zero NaN (the
 * common "open on target, hit Send" path — spec §5, critique C1-A3 / C2). The
 * returned ra_hours is normalized to [0,24) so Target.ra_hours (Field ge=0,
 * lt=24) never 422s the plan (critique C2-#2).
 */
export function deproject(
  xi_deg: number,
  eta_deg: number,
  ra0_hours: number,
  dec0_deg: number,
): SkyCoord {
  const xi = xi_deg * DEG;
  const eta = eta_deg * DEG;
  const rho = Math.hypot(xi, eta);

  // <-- the common path: open on-target, hit Send. No division, no NaN.
  if (rho < RHO_EPS) {
    return { ra_hours: wrapRaHours(ra0_hours), dec_deg: dec0_deg };
  }

  const dec0 = dec0_deg * DEG;
  const ra0 = ra0_hours * 15 * DEG;
  const c = Math.atan(rho);
  const cosC = Math.cos(c);
  const sinC = Math.sin(c);
  const cosDec0 = Math.cos(dec0);
  const sinDec0 = Math.sin(dec0);

  const dec = Math.asin(cosC * sinDec0 + (eta * sinC * cosDec0) / rho);
  const ra =
    ra0 +
    Math.atan2(xi * sinC, rho * cosDec0 * cosC - eta * sinDec0 * sinC);

  const ra_hours = wrapRaHours((ra * RAD) / 15);
  return { ra_hours, dec_deg: dec * RAD };
}

/** Normalize RA hours into [0,24). Mirrors the server's `ra % 24` (spec §5). */
export function wrapRaHours(ra_hours: number): number {
  let r = ra_hours % 24;
  if (r < 0) r += 24;
  // A value rounding up to exactly 24 (e.g. 23.9999999999) must stay < 24 to
  // satisfy Target.ra_hours Field(lt=24); fold it back to 0.
  if (r >= 24) r -= 24;
  return r;
}

// ------------------------------------------------- meridian convergence (#175)
/** How far north of a panel centre local north is sampled: small enough that
 *  the projection's curvature over the step is negligible, far larger than its
 *  rounding. Server `framing.NORTH_PROBE_DEG`, the same 1e-4. */
export const NORTH_PROBE_DEG = 1e-4;

/**
 * Meridian convergence at a panel: local north at its centre as a direction on
 * the GRID's tangent plane (tangent at `ra0_hours`,`dec0_deg`), degrees from +η
 * toward +ξ (east). Mirrors server `framing.panel_convergence_deg` expression
 * for expression: found by projecting a point `NORTH_PROBE_DEG` north of the
 * centre, or south of it within that distance of the pole, where the answer is
 * reversed. Positive for a panel west of the grid's centre (north leans toward
 * the pole), negative east of it, 0 along the centre meridian.
 *
 * A frame at sky position angle θᵢ lies on the grid when θᵢ = θ + nᵢ (nᵢ this
 * number): the camera's up for PA θᵢ sits at nᵢ − θᵢ on the grid plane and the
 * grid's up at −θ, because image up is north rotated toward WEST by the angle.
 */
export function panelConvergenceDeg(
  panel: SkyCoord,
  ra0_hours: number,
  dec0_deg: number,
): number {
  const ra = panel.ra_hours;
  const dec = panel.dec_deg;
  const step = dec + NORTH_PROBE_DEG <= 90 ? NORTH_PROBE_DEG : -NORTH_PROBE_DEG;
  const a = project(ra, dec, ra0_hours, dec0_deg);
  const b = project(ra, dec + step, ra0_hours, dec0_deg);
  const sign = step > 0 ? 1 : -1;
  return Math.atan2(sign * (b.xi - a.xi), sign * (b.eta - a.eta)) * RAD;
}

// ----------------------------------------------------------- mosaic tiling
/**
 * Mosaic panel grid, mirroring `catalog/framing.py` exactly (spec §5):
 *   step = fov · (1 − overlap)
 *   gx   = (c − (cols−1)/2)·step_x ;  gy = ((rows−1)/2 − r)·step_y
 *   ξ = gx·cosθ − gy·sinθ ;  η = gx·sinθ + gy·cosθ      (θ = rotation_deg)
 *   (ra,dec) = deproject(ξ, η, ra0, dec0)               (ra already %24)
 *   convergence_deg = panelConvergenceDeg(panel, ra0, dec0)
 *   pa_deg = (θ + convergence_deg) wrapped into [0,360)  (#175)
 * Panels are returned in **boustrophedon (snake)** order so a multi-row mosaic
 * minimizes slew travel between consecutive panels. `rotation_deg` stays the
 * LAYOUT angle (what the Atlas draws); the camera angle of each panel is
 * `pa_deg`.
 */
export function mosaicGrid(spec: MosaicGridSpec): GridPanel[] {
  const rows = Math.max(1, Math.round(spec.rows));
  const cols = Math.max(1, Math.round(spec.cols));
  const overlap = Math.min(0.5, Math.max(0, spec.overlap));
  const theta = spec.rotation_deg * DEG;
  const cosT = Math.cos(theta);
  const sinT = Math.sin(theta);
  const stepX = spec.fov_x_deg * (1 - overlap);
  const stepY = spec.fov_y_deg * (1 - overlap);

  const panels: GridPanel[] = [];
  for (let r = 0; r < rows; r++) {
    // snake: even rows left->right, odd rows right->left.
    const colOrder: number[] = [];
    for (let c = 0; c < cols; c++) colOrder.push(c);
    if (r % 2 === 1) colOrder.reverse();

    for (const c of colOrder) {
      const gx = (c - (cols - 1) / 2) * stepX;
      const gy = ((rows - 1) / 2 - r) * stepY;
      const xi = gx * cosT - gy * sinT;
      const eta = gx * sinT + gy * cosT;
      const sky = deproject(xi, eta, spec.ra_hours, spec.dec_deg);
      const convergence = panelConvergenceDeg(sky, spec.ra_hours, spec.dec_deg);
      // Wrapped into [0,360) as the server wraps it, and past the one hole
      // a tiny negative sum has: adding 360 to it rounds to exactly 360, which
      // is not a position angle in [0,360).
      let pa = (spec.rotation_deg + convergence) % 360;
      if (pa < 0) pa += 360;
      if (pa >= 360) pa = 0;
      panels.push({
        row: r,
        col: c,
        ra_hours: sky.ra_hours, // already %24-wrapped by deproject
        dec_deg: sky.dec_deg,
        rotation_deg: spec.rotation_deg,
        convergence_deg: convergence,
        pa_deg: pa,
      });
    }
  }
  return panels;
}

// --------------------------------------------------------- rotate-handle geom
/**
 * Half-height of the WHOLE mosaic grid footprint, in the same px units as
 * `pxPerDeg` (viewBox px for the Atlas SVG). The Atlas rotate-handle stalk
 * (SkyCanvas §6, wave-2 G3) hangs off the TOP of the whole grid — not one
 * panel — so it never overlaps a frame; this is the shared calc between the
 * frame geometry and the handle layer, extracted so both agree byte-for-byte
 * (previously duplicated inline in FovOverlay only).
 */
export function gridHalfHeightPx(
  fovYDeg: number,
  pxPerDeg: number,
  rows: number,
  overlap: number,
): number {
  const frameH = fovYDeg * pxPerDeg;
  const halfH = frameH / 2;
  const stepY = frameH * (1 - overlap);
  return halfH + ((rows - 1) * stepY) / 2;
}

/** Tangent-plane total mosaic extent in degrees (spec §5) — NOT raw degrees of
 *  RA, so high-dec mosaics aren't mislabeled (critique C2-#4). */
export function mosaicTotalFov(
  cols: number,
  rows: number,
  overlap: number,
  fov_x_deg: number,
  fov_y_deg: number,
): { total_fov_x_deg: number; total_fov_y_deg: number } {
  const ov = Math.min(0.5, Math.max(0, overlap));
  return {
    total_fov_x_deg: (cols - (cols - 1) * ov) * fov_x_deg,
    total_fov_y_deg: (rows - (rows - 1) * ov) * fov_y_deg,
  };
}
