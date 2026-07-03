// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from docs/native-parity/algorithms/tppa-polar-alignment.md
// (§2 refraction model, §2.2 calculate_refracted_altitude). The refraction
// coefficients follow the documented SOFA `iauRefco` model (§2.2 / §14 source
// map: SOFA.cs iauRefco). No code copied from TPPA/NINA.

//! Atmospheric refraction: the SOFA refraction-coefficient model and the
//! iterative refracted-altitude search used to place the "refracted pole".
//!
//! All altitudes/zenith distances are in degrees at the public boundary and
//! radians internally; refraction coefficients `A`, `B` are in radians such
//! that `dZ = A*tan(Z) + B*tan^3(Z)` for observed zenith distance `Z`.

use crate::geom::{to_deg, to_rad};

/// Refraction parameters (dossier §2.1).
///
/// `relative_humidity` is a fraction in `[0, 1]` and is **not** clamped by the
/// caller here; the SOFA model clamps internally (dossier §2.1 unit caveat —
/// pass a fraction, divide device percent by 100 upstream).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct RefractionParams {
    /// Atmospheric pressure at the observer, hectopascals (mbar).
    pub pressure_hpa: f64,
    /// Ambient temperature, degrees Celsius.
    pub temperature_c: f64,
    /// Relative humidity as a fraction in `[0, 1]`.
    pub relative_humidity: f64,
    /// Observing wavelength, micrometres.
    pub wavelength_um: f64,
}

/// Standard-atmosphere fallback pressure, hPa (dossier §2.1).
pub const STANDARD_PRESSURE_HPA: f64 = 1013.25;
/// Standard-atmosphere fallback temperature, deg C (dossier §2.1).
pub const STANDARD_TEMPERATURE_C: f64 = 15.0;
/// Standard-atmosphere fallback humidity, fraction (dossier §2.1).
pub const STANDARD_HUMIDITY: f64 = 0.0;
/// Default observing wavelength, micrometres (dossier §2.1).
pub const DEFAULT_WAVELENGTH_UM: f64 = 0.55;

impl Default for RefractionParams {
    /// Standard atmosphere with the default wavelength (dossier §2.1).
    fn default() -> Self {
        RefractionParams {
            pressure_hpa: STANDARD_PRESSURE_HPA,
            temperature_c: STANDARD_TEMPERATURE_C,
            relative_humidity: STANDARD_HUMIDITY,
            wavelength_um: DEFAULT_WAVELENGTH_UM,
        }
    }
}

/// SOFA `iauRefco`: refraction constants `A`, `B` (radians) for the model
/// `dZ = A*tan(Z) + B*tan^3(Z)` (dossier §2.2; SOFA.cs `iauRefco`).
///
/// Inputs: pressure hPa, temperature deg C, humidity fraction, wavelength um.
/// Inputs are clamped to the SOFA safe ranges (temperature `[-150, 200]`,
/// pressure `[0, 10000]`, humidity `[0, 1]`, wavelength `[0.1, 1e6]`), which is
/// where the documented humidity clamp to `[0, 1]` occurs.
pub fn refco(phpa: f64, tc: f64, rh: f64, wl: f64) -> (f64, f64) {
    // Optical/IR vs radio switch at 100 microns.
    let optic = wl <= 100.0;

    // Restrict parameters to safe values.
    let t = tc.clamp(-150.0, 200.0);
    let p = phpa.clamp(0.0, 10000.0);
    let r = rh.clamp(0.0, 1.0);
    let w = wl.clamp(0.1, 1.0e6);

    // Water vapour pressure at the observer.
    let pw = if p > 0.0 {
        let ps = 10f64.powf((0.7859 + 0.03477 * t) / (1.0 + 0.00412 * t))
            * (1.0 + p * (4.5e-6 + 6e-10 * t * t));
        r * ps / (1.0 - (1.0 - r) * ps / p)
    } else {
        0.0
    };

    // Refractive index minus 1 at the observer.
    let tk = t + 273.15;
    let gamma = if optic {
        let wlsq = w * w;
        ((77.53484e-6 + (4.39108e-7 + 3.666e-9 / wlsq) / wlsq) * p - 11.2684e-6 * pw) / tk
    } else {
        (77.6890e-6 * p - (6.3938e-6 - 0.375463 / tk) * pw) / tk
    };

    // Beta (Stone, with empirical adjustments).
    let mut beta = 4.4474e-6 * tk;
    if !optic {
        beta -= 0.0074 * pw * beta;
    }

    // Refraction constants (Green).
    let refa = gamma * (1.0 - beta);
    let refb = -gamma * (beta - gamma / 2.0);
    (refa, refb)
}

/// Refraction shift `dZ` (radians) as a function of the **observed** zenith
/// distance `z_obs_rad` (radians): `A*tan(Z) + B*tan^3(Z)`.
#[inline]
pub fn refraction_dz(z_obs_rad: f64, refa: f64, refb: f64) -> f64 {
    let t = z_obs_rad.tan();
    refa * t + refb * t * t * t
}

/// Iterative refracted (observed) altitude for a given true/vacuum altitude
/// (dossier §2.2 `calculate_refracted_altitude`).
///
/// Scans candidate observed zenith distances in `step_arcsec` steps (default
/// 1.0) up to `max_iter` (default 1000) and returns the observed altitude in
/// degrees. Returns `NaN` if the model diverges or the refraction exceeds
/// `max_iter` steps (~16.7' at defaults), matching the upstream fallback which
/// then reverts to the unrefracted pole.
///
/// Panics if `alt_deg < 0` (the source throws).
pub fn calculate_refracted_altitude(
    alt_deg: f64,
    params: &RefractionParams,
    step_arcsec: f64,
    max_iter: u32,
) -> f64 {
    assert!(alt_deg >= 0.0, "altitude must be >= 0");
    let (refa, refb) = refco(
        params.pressure_hpa,
        params.temperature_c,
        params.relative_humidity,
        params.wavelength_um,
    );
    let z = to_rad(90.0 - alt_deg); // true (vacuum) zenith distance
    let inc = to_rad(step_arcsec / 3600.0);
    let mut roller = inc;
    for _ in 0..max_iter {
        let z_refr = z - roller; // candidate observed ZD
        let dz = refraction_dz(z_refr, refa, refb);
        if dz.is_nan() {
            return f64::NAN;
        }
        if ((z_refr + dz) - z).abs() < inc {
            return 90.0 - to_deg(z_refr);
        }
        roller += inc;
    }
    f64::NAN
}

/// Machine-precise refracted (observed) altitude for a given true/vacuum
/// altitude: the exact functional inverse of [`unrefract_altitude`].
///
/// Unlike [`calculate_refracted_altitude`] (which mirrors the dossier's
/// 1-arcsec scan and is used only to place the refracted pole), this fixed-point
/// solve converges to ~1e-11 deg so the equatorial<->topocentric transforms
/// round-trip exactly. `alt_true_deg` in degrees; result in degrees.
pub fn refract_altitude_precise(alt_true_deg: f64, params: &RefractionParams) -> f64 {
    let (refa, refb) = refco(
        params.pressure_hpa,
        params.temperature_c,
        params.relative_humidity,
        params.wavelength_um,
    );
    // Solve alt_obs = alt_true + to_deg(dz(z_obs)), z_obs = 90 - alt_obs.
    let mut alt_obs = alt_true_deg;
    for _ in 0..100 {
        let z_obs = to_rad(90.0 - alt_obs);
        let next = alt_true_deg + to_deg(refraction_dz(z_obs, refa, refb));
        if (next - alt_obs).abs() < 1e-11 {
            return next;
        }
        alt_obs = next;
    }
    alt_obs
}

/// Remove refraction directly: given an **observed** altitude, return the
/// true/vacuum altitude by subtracting `dZ` evaluated at the observed zenith
/// distance (the exact inverse partner of [`calculate_refracted_altitude`],
/// used by the equatorial<->topocentric transforms so the pair round-trips).
///
/// `alt_obs_deg` in degrees; result in degrees.
pub fn unrefract_altitude(alt_obs_deg: f64, params: &RefractionParams) -> f64 {
    let (refa, refb) = refco(
        params.pressure_hpa,
        params.temperature_c,
        params.relative_humidity,
        params.wavelength_um,
    );
    let z_obs = to_rad(90.0 - alt_obs_deg);
    let dz = refraction_dz(z_obs, refa, refb);
    // true zenith distance is larger (object truly lower than it appears)
    let z_true = z_obs + dz;
    90.0 - to_deg(z_true)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refracted_pole_constant_lat40() {
        // Dossier §6.2 / test instruction: lat 40, P=1005, T=7, RH=0.8,
        // lambda=0.574 um -> refracted pole ~ +69.3 arcsec above the true pole.
        let params = RefractionParams {
            pressure_hpa: 1005.0,
            temperature_c: 7.0,
            relative_humidity: 0.8,
            wavelength_um: 0.574,
        };
        let refr = calculate_refracted_altitude(40.0, &params, 1.0, 1000);
        let offset_arcsec = (refr - 40.0) * 3600.0;
        assert!(
            (offset_arcsec - 69.3).abs() < 1.0,
            "refracted-pole offset {offset_arcsec:.2} arcsec, expected ~69.3"
        );
    }

    #[test]
    fn refract_unrefract_round_trip() {
        let params = RefractionParams {
            pressure_hpa: 1005.0,
            temperature_c: 7.0,
            relative_humidity: 0.8,
            wavelength_um: 0.574,
        };
        for &true_alt in &[10.0, 20.0, 41.0, 60.0, 85.0] {
            let obs = calculate_refracted_altitude(true_alt, &params, 1.0, 1000);
            let back = unrefract_altitude(obs, &params);
            assert!(
                (back - true_alt).abs() < 1.0 / 3600.0,
                "round trip alt {true_alt}: obs {obs} back {back}"
            );
        }
    }

    #[test]
    fn zero_pressure_returns_nan() {
        let params = RefractionParams {
            pressure_hpa: 0.0,
            temperature_c: 0.0,
            relative_humidity: 0.0,
            wavelength_um: 0.0,
        };
        // A == B == 0 with zero pressure -> dz is always 0, so the strict
        // `< inc` convergence test never fires and the search yields NaN. The
        // caller (calculate_mount_axis_error) then falls back to the true pole,
        // which is correct because there is no refraction to correct for.
        let refr = calculate_refracted_altitude(40.0, &params, 1.0, 1000);
        assert!(refr.is_nan());
    }
}
