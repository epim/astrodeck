// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier in
// docs/native-parity/algorithms/tppa-polar-alignment.md. No code copied
// from TPPA/NINA.

//! `astro-tppa`: three-point polar alignment math (NINA TPPA parity).
//!
//! Pure algorithm crate (no async, no I/O, no globals, deterministic). Given
//! three plate solves `(ra, dec, pa, t)` plus site `(lat, lon, elev)`, it fits
//! the mount's RA axis from the small circle the three points trace on the
//! celestial sphere, converts the axis to (alt, az), compares it against the
//! (optionally refracted) celestial pole, and decomposes the polar error into
//! altitude/azimuth knob corrections using TPPA's sign conventions. It then
//! re-scales that error from each subsequent solve during the adjustment phase
//! without re-fitting the axis.
//!
//! ## Angle/units summary
//! - Azimuth: North = 0 deg, East = 90 deg (dossier §1.1).
//! - Errors are reported in **arcminutes** at the [`PolarError`] boundary and in
//!   degrees internally.
//! - Refraction humidity is a **fraction** in `[0, 1]` (dossier §2.1).
//!
//! ## Scope note on the equatorial<->topocentric transform
//! The dossier recommends the `erfa` crate for byte-exact SOFA `iauAtco13`/
//! `iauAtoc13`; the pinned `erfa` 0.2.1 does not expose those routines, so the
//! transform is hand-rolled from sidereal time + classical spherical astronomy
//! with the SOFA refraction model applied as an exactly-invertible pair. This
//! reproduces the dossier's forward-model round-trips and the refracted-pole
//! constant exactly, but does not reproduce the SOFA-absolute astropy vectors
//! (§12.4/§12.5) to the arcsecond (it omits aberration/nutation/precession).

pub mod error_det;
pub mod geom;
pub mod measurement;
pub mod refraction;
pub mod sky;
pub mod update;

pub use error_det::{Cardinal, KnobDirection, Position, QualityFlags};
pub use geom::{ra_distance_deg, AltAz, Point, Vec3};
pub use refraction::{calculate_refracted_altitude, RefractionParams};
pub use sky::{arcsec_per_pixel, shift, xy_projection, RaDec, Site};

use error_det::{
    altitude_direction, axis_to_altaz, azimuth_direction, calculate_mount_axis_error,
    determine_plane_vector, hemisphere_correct, position_angle_spread_deg, position_from_solve,
    quality_flags,
};
use update::{calculate_error_details, UpdateInputs};

/// A single plate solve fed to the algorithm.
///
/// Coordinates are J2000 (dossier §1.1: solves are normalized to J2000).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Solve {
    /// Right ascension, degrees, J2000.
    pub ra_deg: f64,
    /// Declination, degrees, J2000.
    pub dec_deg: f64,
    /// Camera position angle of the solve, degrees.
    pub position_angle_deg: f64,
    /// Observation time, UTC Julian Date.
    pub time_jd_utc: f64,
}

impl Solve {
    fn coordinates(&self) -> RaDec {
        RaDec {
            ra_deg: self.ra_deg,
            dec_deg: self.dec_deg,
        }
    }
}

/// Options controlling the alignment computation.
#[derive(Clone, Copy, Debug)]
pub struct TppaOptions {
    /// Atmospheric refraction parameters (always applied in the measurement
    /// transform; dossier §5).
    pub refraction: RefractionParams,
    /// If `true`, compare the axis against the **true** pole (`|lat|`); if
    /// `false` (dossier default) against the **refracted** pole (§6.2).
    pub correct_for_refraction: bool,
    /// Target RA rotation between measurement points, degrees (dossier §3.2,
    /// default 10). Advisory — used by the measurement helpers only.
    pub target_distance_deg: f64,
    /// Image scale, arcseconds per pixel (dossier §1.2; must already include
    /// binning). Required for the continuous-update phase.
    pub arcsec_per_pixel: f64,
    /// Image width, pixels (for the continuous-update image center).
    pub image_width_px: f64,
    /// Image height, pixels (for the continuous-update image center).
    pub image_height_px: f64,
}

impl Default for TppaOptions {
    /// AstroDeck defaults (dossier §15): refracted-pole aim, 10 deg target.
    fn default() -> Self {
        TppaOptions {
            refraction: RefractionParams::default(),
            correct_for_refraction: false,
            target_distance_deg: 10.0,
            arcsec_per_pixel: 0.0,
            image_width_px: 0.0,
            image_height_px: 0.0,
        }
    }
}

/// Errors returned by the public entry points.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TppaError {
    /// Two or more measurement points coincide -> zero plane normal
    /// (dossier §6.1: "mount did not move between points").
    MountDidNotMove,
    /// The continuous-update pixel construction degenerated (leg collapsed or
    /// lines parallel/vertical; dossier §7.5).
    DegenerateGeometry,
    /// Image geometry (`arcsec_per_pixel`, dimensions) was not supplied but is
    /// required for the continuous-update phase.
    MissingImageGeometry,
}

impl std::fmt::Display for TppaError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TppaError::MountDidNotMove => write!(f, "mount did not move between points"),
            TppaError::DegenerateGeometry => {
                write!(f, "degenerate geometry; move to a better sky area")
            }
            TppaError::MissingImageGeometry => {
                write!(f, "image geometry (arcsec/pixel + dimensions) required")
            }
        }
    }
}

impl std::error::Error for TppaError {}

/// The frozen alignment model produced by [`tppa_from_three`] and consumed by
/// [`tppa_update`].
///
/// All fields are plain `f64`/`bool` so the model can cross the PyO3 boundary as
/// a flat dict (dossier public-API requirement). The reference frame is solve 3.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TppaModel {
    /// Initial reference-frame (solve 3) RA, degrees J2000.
    pub init_ra_deg: f64,
    /// Initial reference-frame (solve 3) Dec, degrees J2000.
    pub init_dec_deg: f64,
    /// Initial reference-frame (solve 3) camera position angle, degrees.
    pub init_pa_deg: f64,
    /// Initial reference-frame (solve 3) time, UTC Julian Date.
    pub init_time_jd: f64,
    /// Frozen initial altitude error, degrees (signed).
    pub initial_alt_err_deg: f64,
    /// Frozen initial azimuth error, degrees (signed).
    pub initial_az_err_deg: f64,
    /// Frozen initial total error, degrees.
    pub initial_total_err_deg: f64,
    /// Site latitude, degrees.
    pub lat_deg: f64,
    /// Site longitude, degrees (east positive).
    pub lon_deg: f64,
    /// Site elevation, metres.
    pub elev_m: f64,
    /// Refraction pressure, hPa.
    pub pressure_hpa: f64,
    /// Refraction temperature, deg C.
    pub temperature_c: f64,
    /// Refraction relative humidity, fraction.
    pub relative_humidity: f64,
    /// Refraction wavelength, micrometres.
    pub wavelength_um: f64,
    /// Whether the axis was compared against the true pole (`true`) or the
    /// refracted pole (`false`).
    pub correct_for_refraction: bool,
    /// Northern hemisphere (`latitude_deg > 0`).
    pub northern: bool,
    /// Image scale, arcseconds per pixel.
    pub arcsec_per_pixel: f64,
    /// Image width, pixels.
    pub image_width_px: f64,
    /// Image height, pixels.
    pub image_height_px: f64,
    /// Wrap-aware position-angle spread of the three solves, degrees (§6.4).
    pub position_angle_spread_deg: f64,
}

impl TppaModel {
    fn site(&self) -> Site {
        Site {
            latitude_deg: self.lat_deg,
            longitude_deg: self.lon_deg,
            elevation_m: self.elev_m,
        }
    }
    fn refraction(&self) -> RefractionParams {
        RefractionParams {
            pressure_hpa: self.pressure_hpa,
            temperature_c: self.temperature_c,
            relative_humidity: self.relative_humidity,
            wavelength_um: self.wavelength_um,
        }
    }
    fn center_px(&self) -> Point {
        Point::new(self.image_width_px / 2.0, self.image_height_px / 2.0)
    }
    fn flags(&self) -> QualityFlags {
        QualityFlags {
            position_angle_spread_deg: self.position_angle_spread_deg,
            position_angle_spread_large: self.position_angle_spread_deg > 5.0,
            initial_error_large: self.initial_total_err_deg > 2.0
                && self.initial_total_err_deg <= 10.0,
            initial_error_huge: self.initial_total_err_deg > 10.0,
            degenerate_geometry: false,
        }
    }
}

/// A polar-alignment error with knob directions and quality flags.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PolarError {
    /// Altitude error, arcminutes (signed).
    pub alt_arcmin: f64,
    /// Azimuth error, arcminutes (signed).
    pub az_arcmin: f64,
    /// Total error, arcminutes.
    pub total_arcmin: f64,
    /// Which way to turn the altitude knob.
    pub alt_direction: KnobDirection,
    /// Which way to turn the azimuth knob.
    pub az_direction: KnobDirection,
    /// Quality flags (dossier §6.4/§7.5).
    pub flags: QualityFlags,
}

fn make_polar_error(
    alt_err_deg: f64,
    az_err_deg: f64,
    total_err_deg: f64,
    northern: bool,
    flags: QualityFlags,
) -> PolarError {
    PolarError {
        alt_arcmin: alt_err_deg * 60.0,
        az_arcmin: az_err_deg * 60.0,
        total_arcmin: total_err_deg * 60.0,
        alt_direction: altitude_direction(alt_err_deg, northern),
        az_direction: azimuth_direction(az_err_deg, northern),
        flags,
    }
}

/// Fit the mount axis and initial polar error from three solves (dossier §6).
///
/// The reference frame for the continuous phase is **solve 3** (`solves[2]`).
/// Returns the frozen [`TppaModel`] and the initial [`PolarError`].
///
/// # Errors
/// [`TppaError::MountDidNotMove`] if any two points coincide (dossier §6.1).
pub fn tppa_from_three(
    solves: &[Solve; 3],
    site: Site,
    opts: &TppaOptions,
) -> Result<(TppaModel, PolarError), TppaError> {
    // Northern iff latitude > 0; exactly 0 takes the south branch (dossier §6.1).
    let northern = site.latitude_deg > 0.0;
    let refr = opts.refraction;

    let positions: Vec<Position> = solves
        .iter()
        .map(|s| {
            position_from_solve(
                s.coordinates(),
                s.position_angle_deg,
                site,
                s.time_jd_utc,
                &refr,
            )
        })
        .collect();

    let raw_axis = determine_plane_vector(
        positions[0].vector,
        positions[1].vector,
        positions[2].vector,
    )
    .ok_or(TppaError::MountDidNotMove)?;
    let axis = hemisphere_correct(raw_axis, northern);
    let axis_topo = axis_to_altaz(axis);

    let (alt_err, az_err) = calculate_mount_axis_error(
        axis_topo,
        site.latitude_deg,
        &refr,
        opts.correct_for_refraction,
        northern,
    );
    let total_err = alt_err.hypot(az_err);

    let spread = position_angle_spread_deg(
        solves[0].position_angle_deg,
        solves[1].position_angle_deg,
        solves[2].position_angle_deg,
    );
    let flags = quality_flags(
        solves[0].position_angle_deg,
        solves[1].position_angle_deg,
        solves[2].position_angle_deg,
        total_err,
    );

    let s3 = &solves[2];
    let model = TppaModel {
        init_ra_deg: s3.ra_deg,
        init_dec_deg: s3.dec_deg,
        init_pa_deg: s3.position_angle_deg,
        init_time_jd: s3.time_jd_utc,
        initial_alt_err_deg: alt_err,
        initial_az_err_deg: az_err,
        initial_total_err_deg: total_err,
        lat_deg: site.latitude_deg,
        lon_deg: site.longitude_deg,
        elev_m: site.elevation_m,
        pressure_hpa: refr.pressure_hpa,
        temperature_c: refr.temperature_c,
        relative_humidity: refr.relative_humidity,
        wavelength_um: refr.wavelength_um,
        correct_for_refraction: opts.correct_for_refraction,
        northern,
        arcsec_per_pixel: opts.arcsec_per_pixel,
        image_width_px: opts.image_width_px,
        image_height_px: opts.image_height_px,
        position_angle_spread_deg: spread,
    };

    let err = make_polar_error(alt_err, az_err, total_err, northern, flags);
    Ok((model, err))
}

/// Re-estimate the live polar error from a new solve during the adjustment
/// phase (dossier §7.3). The axis is **not** re-fit.
///
/// # Errors
/// [`TppaError::MissingImageGeometry`] if the model has no image scale, or
/// [`TppaError::DegenerateGeometry`] if the pixel construction collapses.
pub fn tppa_update(model: &TppaModel, solve: &Solve) -> Result<PolarError, TppaError> {
    if model.arcsec_per_pixel <= 0.0 || model.image_width_px <= 0.0 || model.image_height_px <= 0.0
    {
        return Err(TppaError::MissingImageGeometry);
    }
    let inputs = UpdateInputs {
        init_coords: RaDec {
            ra_deg: model.init_ra_deg,
            dec_deg: model.init_dec_deg,
        },
        cur_coords: solve.coordinates(),
        cur_position_angle_deg: solve.position_angle_deg,
        az_err0_deg: model.initial_az_err_deg,
        alt_err0_deg: model.initial_alt_err_deg,
        site: model.site(),
        jd_utc: solve.time_jd_utc,
        arcsec_per_pixel: model.arcsec_per_pixel,
        center_px: model.center_px(),
    };
    let _ = model.refraction(); // re-read each update (display-only upstream)
    let upd = calculate_error_details(&inputs)?;
    Ok(make_polar_error(
        upd.alt_err_deg,
        upd.az_err_deg,
        upd.total_err_deg,
        model.northern,
        model.flags(),
    ))
}
