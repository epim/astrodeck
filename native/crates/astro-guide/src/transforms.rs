// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§5). Derived from
// PHD2 `mount.cpp:1135-1223`
// (`TransformCameraCoordinatesToMountCoordinates`/
// `TransformMountCoordinatesToCameraCoordinates`), `mount.cpp:1570`
// (`y_angle_error` derivation in `Mount::SetCalibration`), and the
// corroborating `scope.cpp:1163-1171` (`MountCoords` helper, same formula)
// (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied from PHD2.

//! Camera ⇄ mount coordinate transforms (dossier §5).
//!
//! Calibration measures the RA axis's angle in the camera frame (`x_angle`)
//! and the Dec axis's angle (`y_angle`); `y_angle_error` is how far the
//! measured axes deviate from perfect orthogonality (0 or π for a perfect
//! mount, depending on motion direction — see [`Cal::y_angle_error_from`]).
//! [`camera_to_mount`] and [`mount_to_camera`] convert a star offset between
//! the camera's pixel frame and the mount's RA/Dec axis frame using those
//! two angles. When the axes are non-orthogonal the reverse transform is not
//! an exact inverse of the forward one: the round trip returns the input to
//! within a relative error of `|`[`Cal::y_angle_error_folded`]`|` radians
//! (0.05 rad of non-orthogonality costs 5% of the offset's magnitude, and
//! that bound is tight). PHD2 accepts this, and so do we.
//!
//! A rig whose Dec axis moves the star opposite to the right-handed sense of
//! its RA axis (reversed Dec parity) calibrates with `y_angle_error` near
//! ±π, not near 0. That is a normal, orthogonal calibration, not a broken
//! one, and the transforms need the unfolded value: [`camera_to_mount`] uses
//! it directly and [`mount_to_camera`] flips its rotation sense when
//! [`Cal::dec_axis_reversed`] holds. Anything a human reads or judges wants
//! the parity folded out instead — [`Cal::y_angle_error_folded`] turns 178°
//! into -2° — because the raw value makes a good calibration look 178° out
//! of square.
//!
//! This module holds no state and performs no I/O: every function is a pure
//! function of its inputs, matching this crate's synchronous, I/O-free
//! contract.

use std::f64::consts::PI;

/// Wrap an angle (radians) to `(-PI, PI]`.
///
/// PHD2's own `norm_angle` (`image_math.h:110`, `norm(val, -M_PI, M_PI)`) is
/// a floor-division wrap that is numerically unstable exactly at multiples
/// of `PI`: floating-point rounding of `(val + PI) / (2*PI)` can land the
/// `floor()` on either side of an integer boundary, so `norm_angle(PI)`,
/// `norm_angle(3*PI)`, and `norm_angle(-3*PI)` all evaluate to `-PI` under a
/// literal port (verified by direct re-evaluation of the upstream formula),
/// even though the dossier's own restatement of the function's contract
/// (§5: `/* wrap to (-PI, PI] */`) — and this crate's golden vectors, which
/// assert `norm_angle(3*PI) == PI` and `norm_angle(-3*PI) == PI` — both
/// require the closed-at-`PI` convention. This implementation uses
/// remainder-based wrapping instead, which is boundary-stable and matches
/// PHD2's own stated `(-PI, PI]` contract (and agrees with the literal
/// upstream formula everywhere except exact multiples of `PI`, where the
/// literal formula is float-rounding-dependent rather than well-defined).
pub fn norm_angle(a: f64) -> f64 {
    const TWO_PI: f64 = 2.0 * PI;
    let mut a = a % TWO_PI; // now in (-2*PI, 2*PI)
    if a <= -PI {
        a += TWO_PI;
    }
    if a > PI {
        a -= TWO_PI;
    }
    a
}

/// Which side of the pier the mount is on (ASCOM `SideOfPier` collapsed to
/// PHD2's East/West/Unknown, dossier §9).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PierSide {
    East,
    West,
    Unknown,
}

/// Guide-direction parity for an axis: whether a positive mount-frame error
/// requires the "normal" or reversed pulse direction (dossier §8.4/§9).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Parity {
    Even,
    Odd,
    Unknown,
}

/// A mount calibration (dossier §5, extended with the pier/parity/rotator/
/// binning fields `Mount::SetCalibration` persists — `mount.cpp:1544-1593`).
///
/// Positions/rates are in binned camera pixels; angles are in radians.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Cal {
    /// RA axis rate, pixels per millisecond.
    pub x_rate: f64,
    /// Dec axis rate, pixels per millisecond.
    pub y_rate: f64,
    /// RA axis angle in the camera frame, radians (positive is CW from the
    /// camera's +x axis).
    pub x_angle: f64,
    /// Dec axis angle in the camera frame, radians, as measured.
    pub y_angle: f64,
    /// orthogonality error between the measured axes:
    /// `norm_angle(x_angle - y_angle + PI/2)` — 0 or π for a perfect mount.
    pub y_angle_error: f64,
    /// declination at calibration time, radians (for cos-dec RA-rate scaling).
    pub declination: f64,
    pub pier_side: PierSide,
    pub ra_parity: Parity,
    pub dec_parity: Parity,
    /// field-rotator angle at calibration time, radians.
    pub rotator_angle: f64,
    /// camera binning factor at calibration time.
    pub binning: u16,
    /// whether this calibration is usable.
    pub is_valid: bool,
}

impl Cal {
    /// The orthogonality error between the measured RA/Dec axes
    /// (dossier §5; `Mount::SetCalibration`, `mount.cpp:1570`):
    /// `norm_angle(x_angle - y_angle + PI/2)`.
    ///
    /// Near 0 when the Dec axis runs right-handed from RA and near ±π when
    /// its parity is reversed — both orthogonal. Fold the parity out with
    /// [`fold_y_angle_error`](Cal::fold_y_angle_error) before reporting or
    /// judging the number.
    pub fn y_angle_error_from(x_angle: f64, y_angle: f64) -> f64 {
        norm_angle(x_angle - y_angle + PI / 2.0)
    }

    /// Remove the Dec-parity reversal from a raw orthogonality error: an
    /// error measured against a reversed axis (`|e| > π/2`) is re-expressed
    /// relative to π instead of 0, so +178° folds to -2° and -175° folds to
    /// +5°, while an already right-handed +3° is returned unchanged.
    ///
    /// The folded value is "how far from square is this mount", which is the
    /// number to show a human and the number a sanity check should judge.
    /// The transforms deliberately keep the raw, unfolded value — see the
    /// module docs.
    pub fn fold_y_angle_error(e: f64) -> f64 {
        if e.abs() > PI / 2.0 {
            norm_angle(e - PI)
        } else {
            e
        }
    }

    /// Whether this calibration's Dec axis runs reversed relative to its RA
    /// axis (`|y_angle_error| > π/2`).
    ///
    /// This is the predicate [`mount_to_camera`] uses to flip its rotation
    /// sense, and it calls this method, so the reported hand and the
    /// transform's hand cannot disagree.
    pub fn dec_axis_reversed(&self) -> bool {
        self.y_angle_error.abs() > PI / 2.0
    }

    /// This calibration's orthogonality error with the Dec-parity reversal
    /// folded out ([`fold_y_angle_error`](Cal::fold_y_angle_error)): the
    /// value to report and to judge for validity, never the value to
    /// transform with.
    pub fn y_angle_error_folded(&self) -> f64 {
        Self::fold_y_angle_error(self.y_angle_error)
    }
}

/// Convert a camera-frame `(x, y)` pixel offset to the mount's RA/Dec axis
/// frame (dossier §5; `Mount::TransformCameraCoordinatesToMountCoordinates`,
/// `mount.cpp:1135-1179`).
pub fn camera_to_mount(cam: (f64, f64), cal: &Cal) -> (f64, f64) {
    let (cx, cy) = cam;
    let hyp = cx.hypot(cy);
    let theta = cy.atan2(cx);

    // xAngle measures RA axis rotation vs. camera X axis, positive is CW
    // from the x axis; yAngleError is the orthogonality error.
    let x_angle = theta - cal.x_angle;
    let y_angle = theta - (cal.x_angle + cal.y_angle_error);

    (x_angle.cos() * hyp, y_angle.sin() * hyp)
}

/// Convert a mount-frame RA/Dec `(x, y)` offset to the camera's pixel frame
/// (dossier §5; `Mount::TransformMountCoordinatesToCameraCoordinates`,
/// `mount.cpp:1181-1223`).
pub fn mount_to_camera(mnt: (f64, f64), cal: &Cal) -> (f64, f64) {
    let (mx, my) = mnt;
    let hyp = mx.hypot(my);
    let mut theta = my.atan2(mx);

    // axis-reversal case: a calibration whose Dec parity is reversed flips
    // the sense of rotation. Same predicate as `Cal::dec_axis_reversed`, by
    // construction.
    if cal.dec_axis_reversed() {
        theta = -theta;
    }

    let x_angle = theta + cal.x_angle;
    (x_angle.cos() * hyp, x_angle.sin() * hyp)
}
