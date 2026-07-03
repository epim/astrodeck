// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from docs/native-parity/algorithms/tppa-polar-alignment.md
// (§5 Position, §6 PolarErrorDetermination: axis fit, mount-axis error,
// sign/knob table, quality flags). No code copied from TPPA/NINA.

//! Turning three solved topocentric vectors into the mount's RA-axis direction
//! and the altitude/azimuth polar error, with the knob-direction and
//! quality-flag semantics of dossier §6.

use crate::geom::{
    altaz_to_unit_vector, cross, pa_gap_deg, to_deg, to_rad, unit_vector_to_altaz, wrap_az_err_deg,
    AltAz, Vec3,
};
use crate::refraction::{calculate_refracted_altitude, RefractionParams};
use crate::sky::{equatorial_to_topocentric, RaDec, Site};

/// A single solved measurement reduced to its topocentric geometry (dossier §5).
#[derive(Clone, Copy, Debug)]
pub struct Position {
    /// Observed topocentric az/alt (refraction applied), degrees.
    pub topocentric: AltAz,
    /// Unit vector in the §1.3 frame.
    pub vector: Vec3,
    /// Camera position angle of the solve, degrees.
    pub position_angle_deg: f64,
}

/// Build a [`Position`] from a solved J2000 field center (dossier §5).
///
/// Refraction is **always** applied here; the `correct_for_refraction` setting
/// only affects which pole the axis is later compared against (§6.2).
pub fn position_from_solve(
    coordinates: RaDec,
    position_angle_deg: f64,
    site: Site,
    jd_utc: f64,
    refr: &RefractionParams,
) -> Position {
    let topo = equatorial_to_topocentric(coordinates, site, jd_utc, refr);
    Position {
        topocentric: topo,
        vector: altaz_to_unit_vector(to_rad(topo.az_deg), to_rad(topo.alt_deg)),
        position_angle_deg,
    }
}

/// Minimum `|cross|` below which the three points are treated as non-moving
/// (dossier §6.1: coincident points -> zero normal -> garbage axis upstream).
pub const AXIS_EPS: f64 = 1e-9;

/// Fit the mount RA axis from three unit vectors on the small circle they trace
/// (dossier §6.1). Returns the (un-normalized-sign) plane normal, or `None` if
/// the points are effectively coincident (`|cross| < AXIS_EPS`).
pub fn determine_plane_vector(a: Vec3, b: Vec3, c: Vec3) -> Option<Vec3> {
    let n = cross(b - a, c - b);
    if n.norm() < AXIS_EPS {
        None
    } else {
        Some(n.normalize())
    }
}

/// Resolve the fitted axis into the correct hemisphere (dossier §6.1).
///
/// Northern (`northern == true`, i.e. `latitude_deg > 0`): the axis must point
/// north (`+x`). Southern (including the exact equator, `latitude_deg == 0`):
/// it must point south (`-x`).
pub fn hemisphere_correct(axis: Vec3, northern: bool) -> Vec3 {
    if (northern && axis.x < 0.0) || (!northern && axis.x > 0.0) {
        -axis
    } else {
        axis
    }
}

/// Altitude/azimuth polar error of the mount axis vs the pole (dossier §6.2).
///
/// Returns `(alt_err_deg, az_err_deg)`. With `correct_for_refraction == false`
/// (the default) the axis is compared against the **refracted** pole (via the
/// documented 1-arcsec-scan [`calculate_refracted_altitude`]); on a NaN result
/// it falls back to the true pole (`|latitude|`) exactly as upstream logs and
/// does. The azimuth error is wrapped into `(-180, 180]`.
pub fn calculate_mount_axis_error(
    axis_topo: AltAz,
    latitude_deg: f64,
    refr: &RefractionParams,
    correct_for_refraction: bool,
    northern: bool,
) -> (f64, f64) {
    let mut pole = latitude_deg.abs();
    if !correct_for_refraction {
        let refracted = calculate_refracted_altitude(pole, refr, 1.0, 1000);
        if refracted.is_nan() {
            // upstream: log_error!("refracted pole could not be calculated ... falling back")
        } else {
            pole = refracted;
        }
    }

    let (alt_err, mut az_err) = if northern {
        (axis_topo.alt_deg - pole, axis_topo.az_deg)
    } else {
        (pole - axis_topo.alt_deg, axis_topo.az_deg + 180.0)
    };
    az_err = wrap_az_err_deg(az_err);
    (alt_err, az_err)
}

/// Cardinal annotation on an azimuth knob move (dossier §6.3).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cardinal {
    /// Eastward.
    East,
    /// Westward.
    West,
}

/// Which way to turn a knob (dossier §6.3 sign/knob-direction table). "Left" and
/// "right" are as seen facing the pole; the azimuth moves carry a cardinal
/// (E/W) annotation whose meaning flips between hemispheres.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KnobDirection {
    /// Raise the altitude axis.
    MoveUp,
    /// Lower the altitude axis.
    MoveDown,
    /// Turn the azimuth axis left (toward the given cardinal).
    MoveLeft(Cardinal),
    /// Turn the azimuth axis right (toward the given cardinal).
    MoveRight(Cardinal),
}

/// Altitude knob direction for a signed altitude error (dossier §6.3).
pub fn altitude_direction(alt_err_deg: f64, northern: bool) -> KnobDirection {
    // Northern: +err (axis above pole) -> down; -err -> up.
    // Southern: +err (pole above axis) -> up;   -err -> down.
    let axis_above = if northern {
        alt_err_deg > 0.0
    } else {
        alt_err_deg < 0.0
    };
    if axis_above {
        KnobDirection::MoveDown
    } else {
        KnobDirection::MoveUp
    }
}

/// Azimuth knob direction for a signed azimuth error (dossier §6.3).
pub fn azimuth_direction(az_err_deg: f64, northern: bool) -> KnobDirection {
    if northern {
        // +err (east of north) -> left/west; -err -> right/east.
        if az_err_deg >= 0.0 {
            KnobDirection::MoveLeft(Cardinal::West)
        } else {
            KnobDirection::MoveRight(Cardinal::East)
        }
    } else {
        // +err (west of south, az>180) -> left/east; -err -> right/west.
        if az_err_deg >= 0.0 {
            KnobDirection::MoveLeft(Cardinal::East)
        } else {
            KnobDirection::MoveRight(Cardinal::West)
        }
    }
}

/// Quality flags computed alongside the error (dossier §6.4 and §7.5).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct QualityFlags {
    /// Wrap-aware max pairwise position-angle spread, degrees (§6.4).
    pub position_angle_spread_deg: f64,
    /// Spread > 5 deg: camera rotated between frames -> not a pure RA move.
    pub position_angle_spread_large: bool,
    /// Initial total error in `(2, 10]` deg: adjustment phase error-prone.
    pub initial_error_large: bool,
    /// Initial total error > 10 deg: mount way off / wrong site / bad move.
    pub initial_error_huge: bool,
    /// Degenerate update geometry detected (§7.5); set during the continuous
    /// phase, always `false` for the initial determination.
    pub degenerate_geometry: bool,
}

/// Wrap-aware maximum pairwise position-angle spread over the three solves
/// (dossier §6.4): pairs (1,2),(2,3),(3,1).
pub fn position_angle_spread_deg(pa1: f64, pa2: f64, pa3: f64) -> f64 {
    pa_gap_deg(pa1, pa2)
        .max(pa_gap_deg(pa2, pa3))
        .max(pa_gap_deg(pa3, pa1))
}

/// Compute the §6.4 quality flags from the three position angles and the total
/// initial error (degrees).
pub fn quality_flags(pa1: f64, pa2: f64, pa3: f64, total_err_deg: f64) -> QualityFlags {
    let spread = position_angle_spread_deg(pa1, pa2, pa3);
    QualityFlags {
        position_angle_spread_deg: spread,
        position_angle_spread_large: spread > 5.0,
        initial_error_large: total_err_deg > 2.0 && total_err_deg <= 10.0,
        initial_error_huge: total_err_deg > 10.0,
        degenerate_geometry: false,
    }
}

/// Convert a fitted, hemisphere-corrected axis vector to topocentric az/alt in
/// degrees (dossier §6.1).
pub fn axis_to_altaz(axis: Vec3) -> AltAz {
    let (az, alt) = unit_vector_to_altaz(axis);
    AltAz {
        az_deg: to_deg(az),
        alt_deg: to_deg(alt),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geom::emod;

    #[test]
    fn knob_directions_all_sign_combos() {
        // Northern.
        assert_eq!(altitude_direction(1.0, true), KnobDirection::MoveDown);
        assert_eq!(altitude_direction(-1.0, true), KnobDirection::MoveUp);
        assert_eq!(
            azimuth_direction(1.0, true),
            KnobDirection::MoveLeft(Cardinal::West)
        );
        assert_eq!(
            azimuth_direction(-1.0, true),
            KnobDirection::MoveRight(Cardinal::East)
        );
        // Southern.
        assert_eq!(altitude_direction(1.0, false), KnobDirection::MoveUp);
        assert_eq!(altitude_direction(-1.0, false), KnobDirection::MoveDown);
        assert_eq!(
            azimuth_direction(1.0, false),
            KnobDirection::MoveLeft(Cardinal::East)
        );
        assert_eq!(
            azimuth_direction(-1.0, false),
            KnobDirection::MoveRight(Cardinal::West)
        );
    }

    #[test]
    fn pa_spread_is_wrap_aware() {
        // 350 vs 10 -> 20; 10 vs 40 -> 30; 40 vs 350 -> 50. max = 50.
        assert!((position_angle_spread_deg(350.0, 10.0, 40.0) - 50.0).abs() < 1e-9);
    }

    #[test]
    fn coincident_points_detected() {
        let v = altaz_to_unit_vector(1.0, 0.7);
        assert!(determine_plane_vector(v, v, v).is_none());
    }

    #[test]
    fn axis_altaz_round_trip() {
        let aa_in = AltAz {
            az_deg: 1.0,
            alt_deg: 41.0,
        };
        let v = altaz_to_unit_vector(to_rad(aa_in.az_deg), to_rad(aa_in.alt_deg));
        let aa = axis_to_altaz(v);
        assert!((aa.alt_deg - 41.0).abs() < 1e-9);
        assert!((emod(aa.az_deg, 360.0) - 1.0).abs() < 1e-9);
    }
}
