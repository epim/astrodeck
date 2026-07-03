// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from docs/native-parity/algorithms/tppa-polar-alignment.md
// (§1.1 angle conventions, §1.3 topocentric frame, §1.4 vector ops, §3.1 RA
// distance). No code copied from TPPA/NINA.

//! Angle conventions, small 2D/3D vector primitives, and the topocentric
//! unit-vector frame used throughout the crate.
//!
//! Angle conventions (dossier §1.1):
//! - Azimuth: North = 0 deg, East = 90 deg (SOFA/ERFA), handled mod 360.
//! - Altitude: degrees above the horizon; zenith = +90 deg.
//! - Internal trig is in radians; `_deg` suffixes mark degree-valued items.
//!
//! Topocentric frame (dossier §1.3), right-handed, fixed to the observer:
//! - `+x` -> horizon point at azimuth 0 deg (north)
//! - `+y` -> horizon point at azimuth 270 deg (west)
//! - `+z` -> zenith

use std::f64::consts::{FRAC_PI_2, PI};

/// Euclidean modulus: always non-negative for a positive divisor (dossier §1.1).
///
/// Units: same as inputs.
#[inline]
pub fn emod(x: f64, y: f64) -> f64 {
    ((x % y) + y) % y
}

/// Degrees -> radians.
#[inline]
pub fn to_rad(deg: f64) -> f64 {
    deg * PI / 180.0
}

/// Radians -> degrees.
#[inline]
pub fn to_deg(rad: f64) -> f64 {
    rad * 180.0 / PI
}

/// Wrap an azimuth-error value (degrees) into the half-open range (-180, 180]
/// exactly as the source does (dossier §6.2): a single additive correction.
#[inline]
pub fn wrap_az_err_deg(mut az_err: f64) -> f64 {
    if az_err > 180.0 {
        az_err -= 360.0;
    }
    if az_err < -180.0 {
        az_err += 360.0;
    }
    az_err
}

/// Wrap-safe great-circle degree distance between two RA values (dossier §3.1).
///
/// Units: inputs degrees, result in `[0, 180]`. Used for "how far did the RA
/// axis travel" checks. `ra_distance_deg(a, b) == ra_distance_deg(b, a)`.
pub fn ra_distance_deg(ra1_deg: f64, ra2_deg: f64) -> f64 {
    180.0 - ((ra1_deg - ra2_deg).abs() - 180.0).abs()
}

/// A right-handed 3D vector in the topocentric frame of §1.3.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Vec3 {
    /// North component (azimuth 0 deg).
    pub x: f64,
    /// West component (azimuth 270 deg).
    pub y: f64,
    /// Up component (zenith).
    pub z: f64,
}

impl Vec3 {
    /// Construct a vector from raw components.
    pub fn new(x: f64, y: f64, z: f64) -> Self {
        Vec3 { x, y, z }
    }

    /// Euclidean length.
    pub fn norm(&self) -> f64 {
        (self.x * self.x + self.y * self.y + self.z * self.z).sqrt()
    }

    /// Zero-safe normalization (dossier §1.4): returns the zero vector for a
    /// zero-length input rather than NaN.
    pub fn normalize(&self) -> Vec3 {
        let n = self.norm();
        if n == 0.0 {
            Vec3::new(0.0, 0.0, 0.0)
        } else {
            Vec3::new(self.x / n, self.y / n, self.z / n)
        }
    }
}

impl std::ops::Add for Vec3 {
    type Output = Vec3;
    fn add(self, o: Vec3) -> Vec3 {
        Vec3::new(self.x + o.x, self.y + o.y, self.z + o.z)
    }
}
impl std::ops::Sub for Vec3 {
    type Output = Vec3;
    fn sub(self, o: Vec3) -> Vec3 {
        Vec3::new(self.x - o.x, self.y - o.y, self.z - o.z)
    }
}
impl std::ops::Mul<f64> for Vec3 {
    type Output = Vec3;
    fn mul(self, s: f64) -> Vec3 {
        Vec3::new(self.x * s, self.y * s, self.z * s)
    }
}
impl std::ops::Neg for Vec3 {
    type Output = Vec3;
    fn neg(self) -> Vec3 {
        Vec3::new(-self.x, -self.y, -self.z)
    }
}

/// Cross product `a x b` (dossier §1.4).
pub fn cross(a: Vec3, b: Vec3) -> Vec3 {
    Vec3::new(
        a.y * b.z - a.z * b.y,
        a.z * b.x - a.x * b.z,
        a.x * b.y - a.y * b.x,
    )
}

/// Dot product (dossier §1.4).
pub fn dot(a: Vec3, b: Vec3) -> f64 {
    a.x * b.x + a.y * b.y + a.z * b.z
}

/// Rodrigues rotation of `v` about unit axis `k` by `theta` radians (dossier §1.4).
pub fn rotate_rodrigues(v: Vec3, k: Vec3, theta: f64) -> Vec3 {
    v * theta.cos() + cross(k, v) * theta.sin() + k * (dot(k, v) * (1.0 - theta.cos()))
}

/// Topocentric az/alt pair, in degrees (dossier §1.3 conventions).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AltAz {
    /// Azimuth, degrees, North = 0, East = 90.
    pub az_deg: f64,
    /// Altitude, degrees above the horizon.
    pub alt_deg: f64,
}

/// Convert az/alt (radians) to a topocentric unit vector (dossier §1.3).
///
/// `az`/`alt` in radians; returns a unit vector in the §1.3 frame.
pub fn altaz_to_unit_vector(az: f64, alt: f64) -> Vec3 {
    let theta = -az;
    let phi = FRAC_PI_2 - alt; // polar angle from zenith
    Vec3::new(theta.cos() * phi.sin(), theta.sin() * phi.sin(), phi.cos())
}

/// Convert a topocentric unit vector back to (az, alt) in radians (dossier §1.3).
///
/// Faithfully preserves the source quirks: exact zenith -> (0, pi/2), and
/// `az == 0` whenever `v.y == 0` even if `v.x < 0` (only matters on the exact
/// meridian). Requires `|v| == 1` for the altitude to be correct.
pub fn unit_vector_to_altaz(v: Vec3) -> (f64, f64) {
    if v.x == 0.0 && v.y == 0.0 {
        return (0.0, FRAC_PI_2);
    }
    let az = if v.y == 0.0 { 0.0 } else { -v.y.atan2(v.x) };
    let alt = FRAC_PI_2 - v.z.clamp(-1.0, 1.0).acos();
    (az, alt)
}

/// A 2D image-plane point, in pixels.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Point {
    /// X pixel coordinate.
    pub x: f64,
    /// Y pixel coordinate.
    pub y: f64,
}

impl Point {
    /// Construct a pixel point.
    pub fn new(x: f64, y: f64) -> Self {
        Point { x, y }
    }
    /// Euclidean pixel distance to another point.
    pub fn dist(&self, o: Point) -> f64 {
        ((self.x - o.x).powi(2) + (self.y - o.y).powi(2)).sqrt()
    }
}

impl std::ops::Add for Point {
    type Output = Point;
    fn add(self, o: Point) -> Point {
        Point::new(self.x + o.x, self.y + o.y)
    }
}
impl std::ops::Sub for Point {
    type Output = Point;
    fn sub(self, o: Point) -> Point {
        Point::new(self.x - o.x, self.y - o.y)
    }
}
impl std::ops::Mul<f64> for Point {
    type Output = Point;
    fn mul(self, s: f64) -> Point {
        Point::new(self.x * s, self.y * s)
    }
}

/// 2D dot product of two points-as-vectors (dossier §7.3 `dot2`).
pub fn dot2(a: Point, b: Point) -> f64 {
    a.x * b.x + a.y * b.y
}

/// A line in the image plane parameterized as `y = slope * x + intercept`
/// (dossier §7.3). Vertical lines are not representable and are treated as
/// degenerate geometry by the update math.
#[derive(Clone, Copy, Debug)]
pub struct Line {
    /// Slope `dy/dx`.
    pub slope: f64,
    /// Y-intercept.
    pub intercept: f64,
}

/// Threshold below which a pixel-space run is treated as vertical/degenerate.
pub const DEGENERATE_EPS: f64 = 1e-9;

impl Line {
    /// Line through two points; `None` if (near-)vertical (dossier §7.5 guard).
    pub fn from_points(p1: Point, p2: Point) -> Option<Line> {
        let dx = p2.x - p1.x;
        if dx.abs() < DEGENERATE_EPS {
            return None;
        }
        let slope = (p2.y - p1.y) / dx;
        Some(Line {
            slope,
            intercept: p1.y - slope * p1.x,
        })
    }

    /// Line with a given slope through a point.
    pub fn from_slope_through(slope: f64, p: Point) -> Line {
        Line {
            slope,
            intercept: p.y - slope * p.x,
        }
    }
}

/// Slope of the segment `p1 -> p2`; `None` if (near-)vertical.
pub fn slope(p1: Point, p2: Point) -> Option<f64> {
    let dx = p2.x - p1.x;
    if dx.abs() < DEGENERATE_EPS {
        None
    } else {
        Some((p2.y - p1.y) / dx)
    }
}

/// Intersection of two non-parallel lines; `None` if (near-)parallel
/// (dossier §7.5: upstream dereferences the null and crashes — we surface it).
pub fn intersect(a: Line, b: Line) -> Option<Point> {
    let denom = a.slope - b.slope;
    if denom.abs() < DEGENERATE_EPS {
        return None;
    }
    let x = (b.intercept - a.intercept) / denom;
    let y = a.slope * x + a.intercept;
    Some(Point::new(x, y))
}

/// Wrap-aware smallest angular gap between two position angles, in degrees
/// (dossier §6.4): `min(|d|, 360 - |d|)`.
pub fn pa_gap_deg(pa_i: f64, pa_j: f64) -> f64 {
    let d = (pa_i - pa_j).abs();
    d.min(360.0 - d)
}

/// Orientation angle (degrees) passed to the projection functions:
/// `emod(360 - position_angle, 360)`, interpreted clockwise (dossier §1.1).
pub fn orientation_deg(position_angle_deg: f64) -> f64 {
    emod(360.0 - position_angle_deg, 360.0)
}

/// Approximate PI re-export for downstream modules that want a named constant.
pub const PI_F64: f64 = PI;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn emod_is_non_negative() {
        assert!((emod(-10.0, 360.0) - 350.0).abs() < 1e-12);
        assert!((emod(370.0, 360.0) - 10.0).abs() < 1e-12);
    }

    #[test]
    fn ra_distance_wraps() {
        assert!((ra_distance_deg(350.0, 10.0) - 20.0).abs() < 1e-12);
        assert!((ra_distance_deg(10.0, 350.0) - 20.0).abs() < 1e-12);
        assert!((ra_distance_deg(0.0, 180.0) - 180.0).abs() < 1e-12);
    }

    #[test]
    fn altaz_unit_vector_round_trip() {
        for &(az_deg, alt_deg) in &[(0.0, 10.0), (90.0, 40.0), (200.0, 70.0), (359.0, 5.0)] {
            let v = altaz_to_unit_vector(to_rad(az_deg), to_rad(alt_deg));
            assert!((v.norm() - 1.0).abs() < 1e-12);
            let (az, alt) = unit_vector_to_altaz(v);
            assert!((to_deg(alt) - alt_deg).abs() < 1e-9);
            let az_wrapped = emod(to_deg(az), 360.0);
            assert!(
                (az_wrapped - az_deg).abs() < 1e-9 || (az_wrapped - az_deg).abs() > 359.99999,
                "az {az_wrapped} vs {az_deg}"
            );
        }
    }

    #[test]
    fn rodrigues_rotates_about_z() {
        // Rotating +x about +z by 90 deg -> +y.
        let r = rotate_rodrigues(
            Vec3::new(1.0, 0.0, 0.0),
            Vec3::new(0.0, 0.0, 1.0),
            FRAC_PI_2,
        );
        assert!((r.x).abs() < 1e-12);
        assert!((r.y - 1.0).abs() < 1e-12);
        assert!((r.z).abs() < 1e-12);
    }

    #[test]
    fn line_intersection_and_degeneracy() {
        let a = Line {
            slope: 1.0,
            intercept: 0.0,
        };
        let b = Line {
            slope: -1.0,
            intercept: 2.0,
        };
        let p = intersect(a, b).unwrap();
        assert!((p.x - 1.0).abs() < 1e-12 && (p.y - 1.0).abs() < 1e-12);
        // parallel
        let c = Line {
            slope: 1.0,
            intercept: 5.0,
        };
        assert!(intersect(a, c).is_none());
        // vertical from_points
        assert!(Line::from_points(Point::new(3.0, 0.0), Point::new(3.0, 9.0)).is_none());
    }
}
