// Framing math — the client mirror of `server/astrodeck/catalog/framing.py`.
// Pure functions, NO React. Used for the zero-latency live FOV/mosaic overlay
// while the user drags; "Send to Plan" always re-runs the server so the slew
// targets are byte-identical to the preview (design spec §1, §5).
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
  rotation_deg: number;
}

// ----------------------------------------------------------------- FOV math
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
  // FOV via the LINEAR small-angle form (pixel_scale·N/3600), matching config.py
  // `fov_deg` and lib/optics.ts so Atlas shows the same frame size as Settings/
  // Mount for one rig (the exact atan() form drifts a few % at wide fields).
  const fov_x_deg = (pixel_scale_arcsec * w) / 3600;
  const fov_y_deg = (pixel_scale_arcsec * h) / 3600;
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

// ----------------------------------------------------------- mosaic tiling
/**
 * Mosaic panel grid, mirroring `catalog/framing.py` exactly (spec §5):
 *   step = fov · (1 − overlap)
 *   gx   = (c − (cols−1)/2)·step_x ;  gy = ((rows−1)/2 − r)·step_y
 *   ξ = gx·cosθ − gy·sinθ ;  η = gx·sinθ + gy·cosθ      (θ = rotation_deg)
 *   (ra,dec) = deproject(ξ, η, ra0, dec0)               (ra already %24)
 * Panels are returned in **boustrophedon (snake)** order so a multi-row mosaic
 * minimizes slew travel between consecutive panels.
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
      panels.push({
        row: r,
        col: c,
        ra_hours: sky.ra_hours, // already %24-wrapped by deproject
        dec_deg: sky.dec_deg,
        rotation_deg: spec.rotation_deg,
      });
    }
  }
  return panels;
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
