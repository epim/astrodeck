// SPDX-License-Identifier: Apache-2.0
//
// Provenance: golden-vector tests for astro-guide's camera<->mount transform
// port (src/transforms.rs). Derived from PHD2 mount.cpp:1135-1223
// (TransformCameraCoordinatesToMountCoordinates /
// TransformMountCoordinatesToCameraCoordinates), mount.cpp:1570
// (`y_angle_error` derivation) via the audited algorithm dossier
// docs/native-parity/algorithms/phd2-guiding.md (§5) (BSD-3-Clause; see
// THIRD-PARTY-NOTICES.md). No code copied from PHD2.

// Provenance: exact vectors hand-derived from dossier §5 (camera<->mount).
use astro_guide::transforms::{camera_to_mount, mount_to_camera, norm_angle, Cal};
use std::f64::consts::PI;

fn cal(x_angle: f64, y_angle_error: f64) -> Cal {
    Cal {
        x_rate: 1.0,
        y_rate: 1.0,
        x_angle,
        y_angle: 0.0,
        y_angle_error,
        declination: 0.0,
        pier_side: astro_guide::transforms::PierSide::West,
        ra_parity: astro_guide::transforms::Parity::Unknown,
        dec_parity: astro_guide::transforms::Parity::Unknown,
        rotator_angle: 0.0,
        binning: 1,
        is_valid: true,
    }
}

#[test]
fn identity_when_xangle0_yerr0() {
    // x_angle=0, y_angle_error=0 => cam==mount. cam=(3,4)->mount=(3,4).
    let c = cal(0.0, 0.0);
    let (mx, my) = camera_to_mount((3.0, 4.0), &c);
    assert!((mx - 3.0).abs() < 1e-9, "mx={}", mx);
    assert!((my - 4.0).abs() < 1e-9, "my={}", my);
}

#[test]
fn rotated_ninety_degrees() {
    // x_angle=PI/2, y_angle_error=0. cam=(3,4)->mount=(4,-3).
    let c = cal(PI / 2.0, 0.0);
    let (mx, my) = camera_to_mount((3.0, 4.0), &c);
    assert!((mx - 4.0).abs() < 1e-9, "mx={}", mx);
    assert!((my + 3.0).abs() < 1e-9, "my={}", my);
}

#[test]
fn mount_to_camera_inverts_identity() {
    let c = cal(0.0, 0.0);
    let (cx, cy) = mount_to_camera((3.0, 4.0), &c);
    assert!((cx - 3.0).abs() < 1e-9 && (cy - 4.0).abs() < 1e-9);
}

#[test]
fn norm_angle_wraps() {
    assert!((norm_angle(3.0 * PI) - PI).abs() < 1e-9);
    assert!((norm_angle(-3.0 * PI) - PI).abs() < 1e-9);
    assert!(norm_angle(0.0).abs() < 1e-9);
}

// ---------------------------------------------------------------------------
// GN-06: a reversed Dec axis (left-handed calibration). Three fresh
// calibrations on the AM5N reported `y_angle_error` 174.9-178.2 deg with
// `is_valid` true: the rig's Dec axis moves the star opposite to the
// right-handed sense of its RA axis, so the error lands near ±π. The
// transforms must keep using that unfolded value; only the REPORT folds.
// ---------------------------------------------------------------------------

/// A left-handed calibration with 0.05 rad (2.9 deg) of real
/// non-orthogonality: `y_angle_error` ~= +178 deg, folded -2.9 deg.
fn left_handed_cal() -> Cal {
    let x_angle = 0.3;
    let y_angle = 0.3 - PI / 2.0 + 0.05;
    let mut c = cal(x_angle, Cal::y_angle_error_from(x_angle, y_angle));
    c.y_angle = y_angle;
    c
}

#[test]
fn y_angle_error_from_orthogonal_right_handed_is_zero() {
    let e = Cal::y_angle_error_from(0.3, 0.3 + PI / 2.0);
    assert!(e.abs() < 1e-12, "e={e}");
    assert!(Cal::fold_y_angle_error(e).abs() < 1e-12);
}

#[test]
fn y_angle_error_from_orthogonal_left_handed_is_pi() {
    let e = Cal::y_angle_error_from(0.3, 0.3 - PI / 2.0);
    assert!((e.abs() - PI).abs() < 1e-12, "e={e}");
    // Perfectly square, once the parity is folded out.
    assert!(Cal::fold_y_angle_error(e).abs() < 1e-12, "e={e}");
}

#[test]
fn y_angle_error_folds_five_degrees_on_either_hand() {
    let five = 5.0_f64.to_radians();

    // Right-handed, 5 deg out of square: the raw error already reads -5 deg.
    let right = Cal::y_angle_error_from(0.3, 0.3 + PI / 2.0 + five);
    assert!((right + five).abs() < 1e-12, "right={right}");
    assert!((Cal::fold_y_angle_error(right) + five).abs() < 1e-12);

    // Left-handed, 5 deg out of square: raw reads 175 deg, folded -5 deg.
    let left = Cal::y_angle_error_from(0.3, 0.3 - PI / 2.0 + five);
    assert!(
        (left.to_degrees() - 175.0).abs() < 1e-9,
        "left={} deg",
        left.to_degrees()
    );
    assert!((Cal::fold_y_angle_error(left) + five).abs() < 1e-12);
}

#[test]
fn dec_axis_reversed_agrees_with_the_transform_branch() {
    assert!(left_handed_cal().dec_axis_reversed());
    assert!(!cal(0.3, 0.05).dec_axis_reversed());
    // The predicate is the transform's own: a reversed cal negates theta, so
    // it and its right-handed mirror send +x to opposite y.
    let (_, rev_y) = mount_to_camera((1.0, 1.0), &left_handed_cal());
    let (_, fwd_y) = mount_to_camera((1.0, 1.0), &cal(0.3, 0.05));
    assert!(
        rev_y * fwd_y < 0.0,
        "reversed={rev_y} right-handed={fwd_y} should have opposite sense"
    );
}

#[test]
fn left_handed_cal_maps_plus_y_camera_to_negative_mount_y() {
    // The reversal is real geometry, not a display artefact: a star that
    // drifts +y in the camera is a NEGATIVE Dec error on this rig.
    let (_, my) = camera_to_mount((0.0, 1.0), &left_handed_cal());
    assert!(my < -0.9, "my={my}");
    // The right-handed mirror sends the same offset the other way.
    let (_, my_right) = camera_to_mount((0.0, 1.0), &cal(0.3, 0.05));
    assert!(my_right > 0.9, "my_right={my_right}");
}

#[test]
fn left_handed_cal_round_trips_within_documented_tolerance() {
    // Module contract: the round trip returns the input to within a relative
    // error of |y_angle_error_folded()| radians. Here that is 0.05.
    let c = left_handed_cal();
    let tol = c.y_angle_error_folded().abs();
    assert!((tol - 0.05).abs() < 1e-12, "tol={tol}");
    for (cx, cy) in [
        (3.0, 4.0),
        (1.0, 0.0),
        (0.0, 1.0),
        (-2.0, 5.0),
        (7.0, -3.0),
        (-1.0, -1.0),
    ] {
        let (bx, by) = mount_to_camera(camera_to_mount((cx, cy), &c), &c);
        let hyp = cx.hypot(cy);
        let err = (bx - cx).hypot(by - cy) / hyp;
        assert!(
            err <= tol,
            "cam=({cx},{cy}) round-tripped to ({bx},{by}), relative error {err} > {tol}"
        );
    }
}
