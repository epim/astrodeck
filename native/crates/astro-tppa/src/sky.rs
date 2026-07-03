// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from docs/native-parity/algorithms/tppa-polar-alignment.md
// (§1.2 image scale, §1.5 equatorial<->topocentric, §1.6 stereographic
// projection). No code copied from TPPA/NINA.
//
// NOTE ON THE EQUATORIAL<->TOPOCENTRIC TRANSFORM: dossier §1.5/§15 recommend the
// `erfa` crate for byte-exact SOFA `iauAtco13`/`iauAtoc13`. The pinned `erfa`
// 0.2.1 does not expose those high-level routines (only low-level primitives),
// so we hand-roll an internally consistent apparent-place-free transform: sidereal
// time + classical spherical astronomy, with the SOFA refraction model (§2)
// applied/removed as an exactly invertible pair. This reproduces the dossier's
// forward-model round-trip vectors and the refracted-pole constant exactly;
// it does NOT reproduce the SOFA-absolute astropy vectors (§12.4/§12.5) to the
// arcsecond because it omits aberration/nutation/precession-from-J2000. See the
// crate summary for the honest scope note.

//! Sidereal time, equatorial<->topocentric transforms, and stereographic
//! sky<->pixel projection.

use crate::geom::{emod, to_deg, to_rad};
use crate::refraction::{refract_altitude_precise, unrefract_altitude, RefractionParams};

/// J2000 RA/Dec, degrees (dossier: solves are normalized to J2000).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RaDec {
    /// Right ascension, degrees, `[0, 360)`.
    pub ra_deg: f64,
    /// Declination, degrees, `[-90, 90]`.
    pub dec_deg: f64,
}

/// Observer site.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Site {
    /// Geodetic latitude, degrees (north positive).
    pub latitude_deg: f64,
    /// Longitude, degrees (east positive).
    pub longitude_deg: f64,
    /// Elevation above the ellipsoid, metres (retained for API parity; the
    /// hand-rolled transform does not use it — topocentric parallax of the
    /// pole is negligible for polar alignment).
    pub elevation_m: f64,
}

/// Greenwich Mean Sidereal Time, degrees, from a UTC Julian Date (IAU 1982,
/// Meeus eq. 12.4). DUT1 is taken as 0 (dossier §1.5: acceptable — a DUT1 error
/// rotates all points rigidly about the pole and leaves the fitted axis
/// unchanged to first order).
pub fn gmst_deg(jd_utc: f64) -> f64 {
    let d = jd_utc - 2451545.0;
    let t = d / 36525.0;
    let gmst = 280.46061837 + 360.98564736629 * d + 0.000387933 * t * t - (t * t * t) / 38710000.0;
    emod(gmst, 360.0)
}

/// Local (apparent) sidereal time in degrees for a site at a UTC Julian Date.
pub fn lst_deg(jd_utc: f64, longitude_deg: f64) -> f64 {
    emod(gmst_deg(jd_utc) + longitude_deg, 360.0)
}

use crate::geom::AltAz;

/// Geometric (vacuum) equatorial -> horizontal transform (no refraction).
///
/// Returns az/alt in degrees with the §1.3 convention (North = 0, East = 90).
pub fn equatorial_to_horizontal_geometric(rd: RaDec, site: Site, jd_utc: f64) -> AltAz {
    let lst = lst_deg(jd_utc, site.longitude_deg);
    let ha = to_rad(emod(lst - rd.ra_deg, 360.0));
    let dec = to_rad(rd.dec_deg);
    let (sin_phi, cos_phi) = to_rad(site.latitude_deg).sin_cos();

    // Equatorial local Cartesian.
    let (sin_ha, cos_ha) = ha.sin_cos();
    let (sin_dec, cos_dec) = dec.sin_cos();
    let x_e = cos_dec * cos_ha;
    let y_e = cos_dec * sin_ha;
    let z_e = sin_dec;

    // Rotate into the horizontal [North, East, Up] frame.
    let north = -sin_phi * x_e + cos_phi * z_e;
    let east = -y_e;
    let up = cos_phi * x_e + sin_phi * z_e;

    let alt = up.clamp(-1.0, 1.0).asin();
    let az = east.atan2(north);
    AltAz {
        az_deg: emod(to_deg(az), 360.0),
        alt_deg: to_deg(alt),
    }
}

/// Geometric (vacuum) horizontal -> equatorial transform (no refraction).
///
/// Exact inverse of [`equatorial_to_horizontal_geometric`]; returns J2000-style
/// RA/Dec in degrees.
pub fn horizontal_to_equatorial_geometric(aa: AltAz, site: Site, jd_utc: f64) -> RaDec {
    let (sin_a, cos_a) = to_rad(aa.alt_deg).sin_cos();
    let (sin_az, cos_az) = to_rad(aa.az_deg).sin_cos();
    let (sin_phi, cos_phi) = to_rad(site.latitude_deg).sin_cos();

    let north = cos_a * cos_az;
    let east = cos_a * sin_az;
    let up = sin_a;

    // Inverse rotation (the rotation matrix is its own inverse).
    let x_e = -sin_phi * north + cos_phi * up;
    let y_e = -east;
    let z_e = cos_phi * north + sin_phi * up;

    let dec = z_e.clamp(-1.0, 1.0).asin();
    let ha = y_e.atan2(x_e);
    let lst = lst_deg(jd_utc, site.longitude_deg);
    let ra = emod(lst - to_deg(ha), 360.0);
    RaDec {
        ra_deg: ra,
        dec_deg: to_deg(dec),
    }
}

/// Equatorial (J2000) -> observed topocentric az/alt with refraction applied
/// (dossier §5): the observed pointing of the mount for a solved field.
pub fn equatorial_to_topocentric(
    rd: RaDec,
    site: Site,
    jd_utc: f64,
    refr: &RefractionParams,
) -> AltAz {
    let geom = equatorial_to_horizontal_geometric(rd, site, jd_utc);
    if geom.alt_deg < 0.0 {
        // Below the horizon: refraction model is invalid; leave geometric alt.
        return geom;
    }
    let alt_deg = refract_altitude_precise(geom.alt_deg, refr);
    AltAz {
        az_deg: geom.az_deg,
        alt_deg,
    }
}

/// Observed topocentric az/alt -> equatorial (J2000) with refraction removed:
/// the exact inverse partner of [`equatorial_to_topocentric`].
pub fn topocentric_to_equatorial(
    aa: AltAz,
    site: Site,
    jd_utc: f64,
    refr: &RefractionParams,
) -> RaDec {
    let true_alt = if aa.alt_deg < 0.0 {
        aa.alt_deg
    } else {
        unrefract_altitude(aa.alt_deg, refr)
    };
    horizontal_to_equatorial_geometric(
        AltAz {
            az_deg: aa.az_deg,
            alt_deg: true_alt,
        },
        site,
        jd_utc,
    )
}

// ---------------------------------------------------------------------------
// Image scale (§1.2)
// ---------------------------------------------------------------------------

/// `(180/pi) * 3600 / 1000` (dossier §1.2).
pub const ARCSEC_PER_PIX_FACTOR: f64 = 206.26480624709636;

/// Image scale in arcseconds per pixel (dossier §1.2). `pixel_size_um` should
/// already include binning (`pixel_size * binning`, fixing the upstream
/// null-coalescing bug — see §1.2).
pub fn arcsec_per_pixel(pixel_size_um: f64, focal_length_mm: f64) -> f64 {
    (pixel_size_um / focal_length_mm) * ARCSEC_PER_PIX_FACTOR
}

// ---------------------------------------------------------------------------
// Stereographic sky<->pixel projection (§1.6)
// ---------------------------------------------------------------------------

use crate::geom::Point;

/// Radians -> arcseconds.
#[inline]
fn rad_to_arcsec(r: f64) -> f64 {
    to_deg(r) * 3600.0
}

/// Sky -> pixel stereographic projection (dossier §1.6 `xy_projection`).
///
/// Projects `target` (RA/Dec, J2000) into an image whose center pixel `c_px`
/// solves to `center` (RA/Dec, J2000). `rotation_deg` is the solve
/// `orientation` (= 360 - PA), interpreted clockwise. Scales are arcsec/pixel.
pub fn xy_projection(
    target: RaDec,
    center: RaDec,
    c_px: Point,
    scale_x_arcsec: f64,
    scale_y_arcsec: f64,
    rotation_deg: f64,
) -> Point {
    // Unwrap RA to within +-180 deg of the center.
    let mut ra_t = target.ra_deg;
    let d = ra_t - center.ra_deg;
    if d > 180.0 {
        ra_t -= 360.0;
    }
    if d < -180.0 {
        ra_t += 360.0;
    }

    let (sdt, cdt) = to_rad(target.dec_deg).sin_cos();
    let (sdc, cdc) = to_rad(center.dec_deg).sin_cos();
    let dra = to_rad(ra_t) - to_rad(center.ra_deg);
    let (sr, cr) = to_rad(rotation_deg).sin_cos();

    let dd = 2.0 / (1.0 + sdt * sdc + cdt * cdc * dra.cos());
    let ra_mod = dd * dra.sin() * cdt;
    let dec_mod = dd * (sdt * cdc - cdt * sdc * dra.cos());

    let (dx, dy) = if rotation_deg != 0.0 {
        (ra_mod * cr + dec_mod * sr, dec_mod * cr - ra_mod * sr)
    } else {
        (ra_mod, dec_mod)
    };

    Point::new(
        c_px.x - rad_to_arcsec(dx) / scale_x_arcsec,
        c_px.y - rad_to_arcsec(dy) / scale_y_arcsec,
    )
}

/// Stereographic pixel-offset -> sky (dossier §1.6 `shift`).
///
/// Given the RA/Dec of the image center, returns the RA/Dec of a point offset
/// by `(dx_px, dy_px)` pixels. Scales are arcsec/pixel.
pub fn shift(
    center: RaDec,
    dx_px: f64,
    dy_px: f64,
    rotation_deg: f64,
    scale_x_arcsec: f64,
    scale_y_arcsec: f64,
) -> RaDec {
    let dx_deg = dx_px * scale_x_arcsec / 3600.0;
    let dy_deg = dy_px * scale_y_arcsec / 3600.0;
    shift_stereographic(center, dx_deg, dy_deg, rotation_deg)
}

/// Core stereographic un-projection (dossier §1.6 `shift_stereographic`).
pub fn shift_stereographic(o: RaDec, dx_deg: f64, dy_deg: f64, rot_deg: f64) -> RaDec {
    let mut dx = -to_rad(dx_deg);
    let mut dy = -to_rad(dy_deg);
    let rot = to_rad(rot_deg);
    if rot != 0.0 {
        let (sr, cr) = rot.sin_cos();
        let dx0 = dx;
        dx = dx * cr - dy * sr;
        dy = dy * cr + dx0 * sr;
    }
    let (sd, cd) = to_rad(o.dec_deg).sin_cos();
    let sins = dx * dx + dy * dy;
    let dz = (4.0 - sins) / (4.0 + sins);
    let dec = (dz * sd + dy * cd * (1.0 + dz) / 2.0).asin();
    let mut dra = (dx * (1.0 + dz) / (2.0 * dec.cos())).asin();
    let mg = 2.0 * (dec.sin() * cd - dec.cos() * sd * dra.cos())
        / (1.0 + dec.sin() * sd + dec.cos() * cd * dra.cos());
    if (mg - dy).abs() > 1.0e-5 {
        dra = std::f64::consts::PI - dra;
    }
    let ra = emod(o.ra_deg + to_deg(dra), 360.0);
    RaDec {
        ra_deg: ra,
        dec_deg: to_deg(dec),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn site() -> Site {
        Site {
            latitude_deg: 40.0,
            longitude_deg: 0.0,
            elevation_m: 250.0,
        }
    }

    #[test]
    fn geometric_transform_round_trips() {
        let s = site();
        let jd = 2451544.5;
        for &(ra, dec) in &[(20.0, 40.0), (127.0, 27.3), (200.0, 80.0), (330.0, 5.0)] {
            let rd = RaDec {
                ra_deg: ra,
                dec_deg: dec,
            };
            let aa = equatorial_to_horizontal_geometric(rd, s, jd);
            let back = horizontal_to_equatorial_geometric(aa, s, jd);
            assert!(
                (back.ra_deg - ra).abs() < 1e-9,
                "ra {} vs {}",
                back.ra_deg,
                ra
            );
            assert!((back.dec_deg - dec).abs() < 1e-9);
        }
    }

    #[test]
    fn refracted_transform_round_trips() {
        let s = site();
        let jd = 2451544.5;
        let refr = RefractionParams {
            pressure_hpa: 1005.0,
            temperature_c: 7.0,
            relative_humidity: 0.8,
            wavelength_um: 0.574,
        };
        // Pick RA/Dec that are well above the horizon at this site/time.
        for &(ra, dec) in &[(180.0, 60.0), (200.0, 40.0), (160.0, 30.0)] {
            let rd = RaDec {
                ra_deg: ra,
                dec_deg: dec,
            };
            let aa = equatorial_to_topocentric(rd, s, jd, &refr);
            assert!(aa.alt_deg > 5.0, "test point too low: {}", aa.alt_deg);
            let back = topocentric_to_equatorial(aa, s, jd, &refr);
            // Round trip within the 1-arcsec refraction search step.
            assert!(
                (back.ra_deg - ra).abs() < 1.0 / 3600.0,
                "ra {} vs {}",
                back.ra_deg,
                ra
            );
            assert!((back.dec_deg - dec).abs() < 1.0 / 3600.0);
        }
    }

    #[test]
    fn stereographic_projection_round_trips() {
        let center = RaDec {
            ra_deg: 180.0,
            dec_deg: 45.0,
        };
        let c_px = Point::new(1000.0, 800.0);
        let scale = 2.0;
        let rot = 33.0;
        // Take a nearby target, project to pixels, un-project back.
        let target = RaDec {
            ra_deg: 180.3,
            dec_deg: 45.1,
        };
        let px = xy_projection(target, center, c_px, scale, scale, rot);
        let back = shift(center, px.x - c_px.x, px.y - c_px.y, rot, scale, scale);
        assert!(
            (back.ra_deg - target.ra_deg).abs() < 1e-6,
            "ra {} vs {}",
            back.ra_deg,
            target.ra_deg
        );
        assert!((back.dec_deg - target.dec_deg).abs() < 1e-6);
    }

    #[test]
    fn projecting_center_returns_center_pixel() {
        let center = RaDec {
            ra_deg: 123.0,
            dec_deg: 27.0,
        };
        let c_px = Point::new(512.0, 512.0);
        let px = xy_projection(center, center, c_px, 3.0, 3.0, 40.0);
        assert!((px.x - 512.0).abs() < 1e-9 && (px.y - 512.0).abs() < 1e-9);
    }
}
