// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from docs/native-parity/algorithms/tppa-polar-alignment.md
// (§7.3 CalculateErrorDetails, §7.4 GetDestinationCoordinates, §7.5 degenerate
// geometry). No code copied from TPPA/NINA.

//! The continuous-phase update math: the axis is **not** re-fit; each new solve
//! re-scales the frozen initial error via a pixel-space geometric construction.

use crate::geom::{
    altaz_to_unit_vector, dot2, intersect, orientation_deg, rotate_rodrigues, slope, to_deg,
    to_rad, unit_vector_to_altaz, AltAz, Line, Point, Vec3, DEGENERATE_EPS,
};
use crate::sky::{
    equatorial_to_horizontal_geometric, horizontal_to_equatorial_geometric, xy_projection, RaDec,
    Site,
};
use crate::TppaError;

/// Rotate the initial frame center on the celestial sphere by an azimuth then an
/// altitude change, refraction-free (dossier §7.4 `GetDestinationCoordinates`).
///
/// `jd_utc` is the epoch at which the transform is evaluated (upstream uses
/// "now"; the pure port uses the current solve's timestamp so the round trip is
/// self-consistent). Returns the destination az/alt in degrees.
pub fn get_destination_coordinates(
    init_coords: RaDec,
    site: Site,
    jd_utc: f64,
    az_angle_deg: f64,
    alt_angle_deg: f64,
) -> AltAz {
    let ref_topo = equatorial_to_horizontal_geometric(init_coords, site, jd_utc);
    let v = altaz_to_unit_vector(to_rad(ref_topo.az_deg), to_rad(ref_topo.alt_deg));
    let az_rot = to_rad(az_angle_deg);
    let alt_rot = to_rad(alt_angle_deg);

    let z_axis = Vec3::new(0.0, 0.0, 1.0);
    let az_dest = rotate_rodrigues(v, z_axis, az_rot);
    // Altitude axis = the east-west axis, itself rotated by the azimuth change.
    let alt_axis = rotate_rodrigues(Vec3::new(0.0, 1.0, 0.0), z_axis, az_rot);
    let final_dest = rotate_rodrigues(az_dest, alt_axis, alt_rot);

    let (az, alt) = unit_vector_to_altaz(final_dest);
    AltAz {
        az_deg: to_deg(az),
        alt_deg: to_deg(alt),
    }
}

/// Convert a destination az/alt back to J2000 RA/Dec, refraction-free
/// (dossier §7.4: the conversion uses `iauAtoc13` with all atmosphere
/// parameters zero).
pub fn destination_to_equatorial(dest: AltAz, site: Site, jd_utc: f64) -> RaDec {
    horizontal_to_equatorial_geometric(dest, site, jd_utc)
}

/// Inputs to the continuous-phase update (dossier §7.3), captured from the
/// frozen model plus the latest solve.
#[derive(Clone, Copy, Debug)]
pub struct UpdateInputs {
    /// Initial reference frame (solve 3) J2000 center.
    pub init_coords: RaDec,
    /// Current solve J2000 center.
    pub cur_coords: RaDec,
    /// Current solve camera position angle, degrees.
    pub cur_position_angle_deg: f64,
    /// Frozen initial azimuth error, degrees.
    pub az_err0_deg: f64,
    /// Frozen initial altitude error, degrees.
    pub alt_err0_deg: f64,
    /// Observer site.
    pub site: Site,
    /// Timestamp for the destination transforms, UTC Julian Date.
    pub jd_utc: f64,
    /// Image scale, arcseconds per pixel.
    pub arcsec_per_pixel: f64,
    /// Image center pixel.
    pub center_px: Point,
}

/// Result of a continuous-phase update: the re-scaled current error (dossier §7.3).
#[derive(Clone, Copy, Debug)]
pub struct UpdatedError {
    /// Current azimuth error, degrees (signed).
    pub az_err_deg: f64,
    /// Current altitude error, degrees (signed).
    pub alt_err_deg: f64,
    /// Current total error, degrees.
    pub total_err_deg: f64,
}

/// The exact pixel-space re-scaling of the initial error (dossier §7.3
/// `CalculateErrorDetails`). No re-fit — each leg's remaining pixel length,
/// divided by its original length, scales the corresponding initial error
/// component; a dot-product test flips the sign on overshoot.
///
/// Returns [`TppaError::DegenerateGeometry`] when a leg's pixel projection
/// collapses or the correction lines are parallel/vertical (dossier §7.5),
/// instead of crashing as upstream does.
pub fn calculate_error_details(inp: &UpdateInputs) -> Result<UpdatedError, TppaError> {
    if inp.arcsec_per_pixel <= 0.0 {
        return Err(TppaError::MissingImageGeometry);
    }
    let rot = orientation_deg(inp.cur_position_angle_deg);
    let s = inp.arcsec_per_pixel;
    let c = inp.center_px;

    // (1) Where does the ORIGINAL frame center now appear? Reflect through C.
    let origin_raw = xy_projection(inp.init_coords, inp.cur_coords, c, s, s, rot);
    let shift2 = (c - origin_raw) * 2.0;
    let origin_px = origin_raw + shift2;

    // Helper: destination -> J2000 -> pixel (+ reflection).
    let project_dest = |az_ang: f64, alt_ang: f64| -> Point {
        let dest =
            get_destination_coordinates(inp.init_coords, inp.site, inp.jd_utc, az_ang, alt_ang);
        let eq = destination_to_equatorial(dest, inp.site, inp.jd_utc);
        xy_projection(eq, inp.cur_coords, c, s, s, rot) + shift2
    };

    // (2) Full destination (perfect axis) and (3) axis-only corner points.
    let dest_px = project_dest(-inp.az_err0_deg, -inp.alt_err0_deg);
    let azonly_px = project_dest(-inp.az_err0_deg, 0.0);
    let altonly_px = project_dest(0.0, -inp.alt_err0_deg);

    // (4) Azimuth leg.
    let (corr_az_dist, orig_az_dist, corr_az_px) = leg(origin_px, azonly_px, dest_px, c)?;
    // (5) Altitude leg.
    let (corr_alt_dist, orig_alt_dist, corr_alt_px) = leg(origin_px, altonly_px, dest_px, c)?;

    // (6) Signs: has the user overshot past the destination on either axis?
    let az_sign = if dot2(dest_px - altonly_px, dest_px - corr_alt_px) > 0.0 {
        1.0
    } else {
        -1.0
    };
    let alt_sign = if dot2(dest_px - azonly_px, dest_px - corr_az_px) > 0.0 {
        1.0
    } else {
        -1.0
    };

    // (7) Current error = initial error rescaled by remaining/original leg length.
    // Guard the 0/0 that arises when an initial component is exactly zero.
    let az_err = if inp.az_err0_deg == 0.0 {
        0.0
    } else {
        if orig_az_dist < DEGENERATE_EPS {
            return Err(TppaError::DegenerateGeometry);
        }
        inp.az_err0_deg * az_sign * corr_az_dist / orig_az_dist
    };
    let alt_err = if inp.alt_err0_deg == 0.0 {
        0.0
    } else {
        if orig_alt_dist < DEGENERATE_EPS {
            return Err(TppaError::DegenerateGeometry);
        }
        inp.alt_err0_deg * alt_sign * corr_alt_dist / orig_alt_dist
    };

    Ok(UpdatedError {
        az_err_deg: az_err,
        alt_err_deg: alt_err,
        total_err_deg: alt_err.hypot(az_err),
    })
}

/// One correction "leg": translate the `origin -> only` line so it passes
/// through `C` (keeping its slope) and intersect it with the `only -> dest`
/// line. Returns `(corrected_dist_to_C, original_leg_length, corrected_point)`.
fn leg(
    origin_px: Point,
    only_px: Point,
    dest_px: Point,
    c: Point,
) -> Result<(f64, f64, Point), TppaError> {
    let m = slope(origin_px, only_px).ok_or(TppaError::DegenerateGeometry)?;
    let line_c = Line::from_slope_through(m, c);
    let line_dst = Line::from_points(only_px, dest_px).ok_or(TppaError::DegenerateGeometry)?;
    let corr = intersect(line_dst, line_c).ok_or(TppaError::DegenerateGeometry)?;
    Ok((corr.dist(c), only_px.dist(origin_px), corr))
}
