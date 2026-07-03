// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from docs/native-parity/algorithms/tppa-polar-alignment.md
// (§3.1 RA distance metric, §3.2 AutomatedNextPoint, §3.4 ManualNextPoint
// "far enough" test). No code copied from TPPA/NINA.

//! Pure measurement-procedure helpers. These return target coordinates and
//! progress/shortfall judgements so a host session can drive mount moves; they
//! never move anything and hold no state.

use crate::geom::{emod, ra_distance_deg};

/// The RA (degrees) the mount should reach after one measurement step
/// (dossier §3.2/§3.3). `east_direction == true` advances RA in the `+` sense.
///
/// Advisory only: the host still measures actual travel with
/// [`ra_distance_deg`] against the mount-reported RA (dossier §3.1 note).
pub fn automated_next_target_ra_deg(
    current_ra_deg: f64,
    target_distance_deg: f64,
    east_direction: bool,
) -> f64 {
    let sign = if east_direction { 1.0 } else { -1.0 };
    emod(current_ra_deg + sign * target_distance_deg, 360.0)
}

/// Whether the mount has rotated "far enough" for the next point
/// (dossier §3.2/§3.4): traveled within 1 deg of the target is enough.
pub fn next_point_reached(ra_before_deg: f64, ra_now_deg: f64, target_distance_deg: f64) -> bool {
    ra_distance_deg(ra_before_deg, ra_now_deg) - target_distance_deg >= -1.0
}

/// Signed RA-travel shortfall in degrees (dossier §3.2): `traveled - target`.
/// Negative means the mount fell short; the upstream warning fires when the
/// value is `< -1.0` (more than 1 deg short).
pub fn ra_travel_shortfall_deg(
    ra_before_deg: f64,
    ra_after_deg: f64,
    target_distance_deg: f64,
) -> f64 {
    ra_distance_deg(ra_before_deg, ra_after_deg) - target_distance_deg
}

/// Convenience: does the shortfall warrant the "mount did not move far enough"
/// warning (dossier §3.2)?
pub fn shortfall_is_significant(shortfall_deg: f64) -> bool {
    shortfall_deg < -1.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn target_ra_wraps_both_directions() {
        assert!((automated_next_target_ra_deg(355.0, 10.0, true) - 5.0).abs() < 1e-9);
        assert!((automated_next_target_ra_deg(5.0, 10.0, false) - 355.0).abs() < 1e-9);
    }

    #[test]
    fn reached_within_one_degree() {
        assert!(next_point_reached(10.0, 19.5, 10.0)); // 0.5 short is enough
        assert!(!next_point_reached(10.0, 15.0, 10.0)); // 5 short is not
    }

    #[test]
    fn shortfall_sign_and_threshold() {
        let s = ra_travel_shortfall_deg(10.0, 15.0, 10.0); // traveled 5, target 10
        assert!((s + 5.0).abs() < 1e-9);
        assert!(shortfall_is_significant(s));
        let ok = ra_travel_shortfall_deg(10.0, 20.5, 10.0); // traveled 10.5
        assert!(!shortfall_is_significant(ok));
    }
}
