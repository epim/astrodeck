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
