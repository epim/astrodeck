// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: forward-model / round-trip tests for the TPPA reimplementation,
// per docs/native-parity/algorithms/tppa-polar-alignment.md §12 golden vectors
// and the assigned test plan (synthetic misaligned axis, both hemispheres,
// all sign combinations, point-order invariance, degenerate geometry,
// near-pole, continuous update).

use astro_tppa::error_det::{axis_to_altaz, calculate_mount_axis_error, position_from_solve};
use astro_tppa::geom::{
    altaz_to_unit_vector, cross, emod, rotate_rodrigues, to_rad, unit_vector_to_altaz, AltAz, Vec3,
};
use astro_tppa::refraction::{calculate_refracted_altitude, RefractionParams};
use astro_tppa::sky::{
    equatorial_to_horizontal_geometric, horizontal_to_equatorial_geometric,
    topocentric_to_equatorial, RaDec, Site,
};
use astro_tppa::update::get_destination_coordinates;
use astro_tppa::{
    tppa_from_three, tppa_update, Cardinal, KnobDirection, Solve, TppaError, TppaOptions,
};

/// Alt/az gimbal rotation of a vector: azimuth about the zenith, then altitude
/// about the (rotated) east-west axis. This mirrors `get_destination`'s core and
/// models a real polar-mount adjustment (az knob about vertical, alt knob about
/// horizontal). Angles in degrees.
fn gimbal_rotate(v: Vec3, az_deg: f64, alt_deg: f64) -> Vec3 {
    let z = Vec3::new(0.0, 0.0, 1.0);
    let a = rotate_rodrigues(v, z, to_rad(az_deg));
    let alt_axis = rotate_rodrigues(Vec3::new(0.0, 1.0, 0.0), z, to_rad(az_deg));
    rotate_rodrigues(a, alt_axis, to_rad(alt_deg))
}

const JD_2000: f64 = 2451544.5; // 2000-01-01T00:00:00 UTC

fn test_refr() -> RefractionParams {
    RefractionParams {
        pressure_hpa: 1005.0,
        temperature_c: 7.0,
        relative_humidity: 0.8,
        wavelength_um: 0.574,
    }
}

/// Place the true mount axis (observed topocentric az/alt) so that the §6.2
/// decomposition against the TRUE pole yields exactly `(d_alt, d_az)` degrees.
fn place_axis(lat_deg: f64, d_alt_deg: f64, d_az_deg: f64) -> AltAz {
    if lat_deg > 0.0 {
        AltAz {
            az_deg: emod(d_az_deg, 360.0),
            alt_deg: lat_deg + d_alt_deg,
        }
    } else {
        // Southern: alt_err = |lat| - axis_alt; az_err = wrap(axis_az + 180).
        AltAz {
            az_deg: emod(d_az_deg - 180.0, 360.0),
            alt_deg: lat_deg.abs() - d_alt_deg,
        }
    }
}

/// Generate three plate solves whose observed topocentric vectors lie exactly on
/// the small circle of half-angle `cone_deg` about `axis`, rotated by phases
/// `0, step, 2*step`. Returns them in forward order.
fn synth_solves(
    axis: AltAz,
    cone_deg: f64,
    step_deg: f64,
    site: Site,
    jd: f64,
    refr: &RefractionParams,
    pa_deg: [f64; 3],
) -> [Solve; 3] {
    let axis_v = altaz_to_unit_vector(to_rad(axis.az_deg), to_rad(axis.alt_deg));
    // A unit vector perpendicular to the axis (horizontal-ish).
    let zenith = Vec3::new(0.0, 0.0, 1.0);
    let mut perp = cross(axis_v, zenith);
    if perp.norm() < 1e-6 {
        perp = cross(axis_v, Vec3::new(1.0, 0.0, 0.0));
    }
    let perp = perp.normalize();
    // Point on the cone at angular distance cone_deg from the axis.
    let p0 = rotate_rodrigues(axis_v, perp, to_rad(cone_deg));

    let phases = [0.0, step_deg, 2.0 * step_deg];
    let mut solves = [Solve {
        ra_deg: 0.0,
        dec_deg: 0.0,
        position_angle_deg: 0.0,
        time_jd_utc: jd,
    }; 3];
    for i in 0..3 {
        let pv = rotate_rodrigues(p0, axis_v, to_rad(phases[i]));
        let (az, alt) = unit_vector_to_altaz(pv);
        let aa = AltAz {
            az_deg: emod(az.to_degrees(), 360.0),
            alt_deg: alt.to_degrees(),
        };
        assert!(
            aa.alt_deg > 4.0,
            "generated point {i} too low: alt {:.2} (axis alt {:.2}, cone {cone_deg})",
            aa.alt_deg,
            axis.alt_deg
        );
        let rd: RaDec = topocentric_to_equatorial(aa, site, jd, refr);
        solves[i] = Solve {
            ra_deg: rd.ra_deg,
            dec_deg: rd.dec_deg,
            position_angle_deg: pa_deg[i],
            time_jd_utc: jd,
        };
    }
    solves
}

fn site(lat: f64) -> Site {
    Site {
        latitude_deg: lat,
        longitude_deg: 0.0,
        elevation_m: 250.0,
    }
}

fn wrap180(mut a: f64) -> f64 {
    while a > 180.0 {
        a -= 360.0;
    }
    while a <= -180.0 {
        a += 360.0;
    }
    a
}

#[test]
fn forward_model_recovers_error_all_sign_combos_both_hemispheres() {
    let refr = test_refr();
    let combos = [(1.0, 1.0), (1.0, -1.0), (-1.0, 1.0), (-1.0, -1.0)];
    // (lat, cone, step)
    let sites = [
        (40.0, 25.0, 40.0),
        (20.0, 12.0, 40.0),
        (60.0, 25.0, 40.0),
        (-33.0, 22.0, 40.0),
        (-50.0, 25.0, 40.0),
    ];
    for &(lat, cone, step) in &sites {
        let s = site(lat);
        let northern = lat > 0.0;
        for &(d_alt, d_az) in &combos {
            let axis = place_axis(lat, d_alt, d_az);
            let solves = synth_solves(axis, cone, step, s, JD_2000, &refr, [30.0; 3]);

            // correct_for_refraction = true -> exact (d_alt, d_az).
            let opts = TppaOptions {
                refraction: refr,
                correct_for_refraction: true,
                ..Default::default()
            };
            let (_model, err) = tppa_from_three(&solves, s, &opts).unwrap();
            let tol = 0.1; // arcmin  (< 0.1' required)
            assert!(
                (err.alt_arcmin - d_alt * 60.0).abs() < tol,
                "lat {lat} d_alt {d_alt}: got alt {:.4}'",
                err.alt_arcmin
            );
            assert!(
                (wrap180(err.az_arcmin / 60.0) * 60.0 - d_az * 60.0).abs() < tol,
                "lat {lat} d_az {d_az}: got az {:.4}'",
                err.az_arcmin
            );

            // Knob directions per §6.3.
            let expect_alt = if northern {
                if d_alt > 0.0 {
                    KnobDirection::MoveDown
                } else {
                    KnobDirection::MoveUp
                }
            } else if d_alt > 0.0 {
                KnobDirection::MoveUp
            } else {
                KnobDirection::MoveDown
            };
            let expect_az = if northern {
                if d_az > 0.0 {
                    KnobDirection::MoveLeft(Cardinal::West)
                } else {
                    KnobDirection::MoveRight(Cardinal::East)
                }
            } else if d_az > 0.0 {
                KnobDirection::MoveLeft(Cardinal::East)
            } else {
                KnobDirection::MoveRight(Cardinal::West)
            };
            assert_eq!(err.alt_direction, expect_alt, "lat {lat} d_alt {d_alt}");
            assert_eq!(err.az_direction, expect_az, "lat {lat} d_az {d_az}");

            // correct_for_refraction = false -> altitude reduced by the
            // refracted-pole offset; azimuth unchanged (dossier test 3).
            let opts_f = TppaOptions {
                refraction: refr,
                correct_for_refraction: false,
                ..Default::default()
            };
            let (_m2, err_f) = tppa_from_three(&solves, s, &opts_f).unwrap();
            let refr_offset_deg =
                calculate_refracted_altitude(lat.abs(), &refr, 1.0, 1000) - lat.abs();
            // The refracted pole sits `offset` higher in altitude than the true
            // pole in BOTH hemispheres. Northern alt_err = axis - pole, so the
            // higher pole reduces it: d_alt - offset. Southern alt_err = pole -
            // axis, so the higher pole increases it: d_alt + offset.
            let expected_alt_f = if northern {
                d_alt - refr_offset_deg
            } else {
                d_alt + refr_offset_deg
            };
            assert!(
                (err_f.alt_arcmin - expected_alt_f * 60.0).abs() < tol,
                "lat {lat} CFR=false d_alt {d_alt}: got {:.4}' expected {:.4}'",
                err_f.alt_arcmin,
                expected_alt_f * 60.0
            );
            assert!((wrap180(err_f.az_arcmin / 60.0) * 60.0 - d_az * 60.0).abs() < tol);
        }
    }
}

#[test]
fn point_order_invariance() {
    let refr = test_refr();
    let s = site(45.0);
    let axis = place_axis(45.0, 0.8, -0.6);
    let solves = synth_solves(axis, 25.0, 40.0, s, JD_2000, &refr, [30.0; 3]);
    let opts = TppaOptions {
        refraction: refr,
        correct_for_refraction: true,
        ..Default::default()
    };
    let (_m, fwd) = tppa_from_three(&solves, s, &opts).unwrap();
    let rev = [solves[2], solves[1], solves[0]];
    let (_m2, back) = tppa_from_three(&rev, s, &opts).unwrap();
    // Errors agree within 1 arcsec (dossier §6.1).
    assert!((fwd.alt_arcmin - back.alt_arcmin).abs() < 1.0 / 60.0);
    assert!((wrap180((fwd.az_arcmin - back.az_arcmin) / 60.0) * 60.0).abs() < 1.0 / 60.0);
}

#[test]
fn coincident_points_error() {
    let s = site(45.0);
    let solve = Solve {
        ra_deg: 40.0,
        dec_deg: 60.0,
        position_angle_deg: 10.0,
        time_jd_utc: JD_2000,
    };
    let opts = TppaOptions::default();
    let err = tppa_from_three(&[solve, solve, solve], s, &opts).unwrap_err();
    assert_eq!(err, TppaError::MountDidNotMove);
}

#[test]
fn near_pole_high_latitude() {
    let refr = test_refr();
    let lat = 85.0;
    let s = site(lat);
    let axis = place_axis(lat, 1.0, 1.0);
    // Small cone so all points stay near the pole and well above the horizon.
    let solves = synth_solves(axis, 3.0, 40.0, s, JD_2000, &refr, [30.0; 3]);
    let opts = TppaOptions {
        refraction: refr,
        correct_for_refraction: true,
        ..Default::default()
    };
    let (_m, err) = tppa_from_three(&solves, s, &opts).unwrap();
    assert!(
        (err.alt_arcmin - 60.0).abs() < 0.1,
        "alt {:.4}'",
        err.alt_arcmin
    );
    assert!(
        (wrap180(err.az_arcmin / 60.0) * 60.0 - 60.0).abs() < 0.1,
        "az {:.4}'",
        err.az_arcmin
    );
}

#[test]
fn southern_hemisphere_signs() {
    let refr = test_refr();
    let lat = -35.0;
    let s = site(lat);
    // axis below the pole altitude and west of south.
    let axis = place_axis(lat, 1.5, 0.5);
    let solves = synth_solves(axis, 22.0, 40.0, s, JD_2000, &refr, [30.0; 3]);
    let opts = TppaOptions {
        refraction: refr,
        correct_for_refraction: true,
        ..Default::default()
    };
    let (_m, err) = tppa_from_three(&solves, s, &opts).unwrap();
    assert!((err.alt_arcmin - 1.5 * 60.0).abs() < 0.1);
    assert!((wrap180(err.az_arcmin / 60.0) * 60.0 - 0.5 * 60.0).abs() < 0.1);
    // Southern, d_alt>0 -> MoveUp; d_az>0 -> MoveLeft(East).
    assert_eq!(err.alt_direction, KnobDirection::MoveUp);
    assert_eq!(err.az_direction, KnobDirection::MoveLeft(Cardinal::East));
}

#[test]
fn continuous_update_tracks_residual() {
    let refr = test_refr();
    let lat = 42.0;
    let s = site(lat);
    // Adjustment-phase-representative sub-degree misalignment (the §6.4 flags
    // warn above 2 deg; the pixel-space rescaling is a linearization whose error
    // grows with the misalignment magnitude).
    let d_alt = 0.5;
    let d_az = -0.4;
    let axis = place_axis(lat, d_alt, d_az);
    let solves = synth_solves(axis, 25.0, 40.0, s, JD_2000, &refr, [30.0; 3]);
    let opts = TppaOptions {
        refraction: refr,
        correct_for_refraction: true,
        arcsec_per_pixel: 2.0,
        image_width_px: 4000.0,
        image_height_px: 3000.0,
        ..Default::default()
    };
    let (model, err0) = tppa_from_three(&solves, s, &opts).unwrap();

    // f = 0: updating with the reference frame (solve 3) reproduces the initial
    // error.
    let solve3 = solves[2];
    let e_same = tppa_update(&model, &solve3).unwrap();
    assert!((e_same.alt_arcmin - err0.alt_arcmin).abs() < 0.05);
    assert!((e_same.az_arcmin - err0.az_arcmin).abs() < 0.05);

    // Rigorous ground truth for a partial knob correction. A polar-mount knob
    // moves the mount in an alt/az gimbal (az about the vertical, alt about the
    // horizontal); the same gimbal moves BOTH the pointing and the mount axis.
    // The correction that zeroes the axis is (+azErr0, +altErr0) in the source's
    // gimbal-sign convention (verified below); applying fraction f leaves the
    // TRUE residual = the §6.2 decomposition of the partially-corrected axis.
    // The continuous update (a pixel-space linearization, §7.3) must track it.
    let az_err0 = model.initial_az_err_deg;
    let alt_err0 = model.initial_alt_err_deg;
    let axis0_v = altaz_to_unit_vector(to_rad(axis.az_deg), to_rad(axis.alt_deg));
    let init_coords = RaDec {
        ra_deg: model.init_ra_deg,
        dec_deg: model.init_dec_deg,
    };

    for &f in &[0.25_f64, 0.5, 0.75] {
        // Pointing after the partial gimbal move (refraction-free, matching the
        // update's internal get_destination model).
        let cur_topo = get_destination_coordinates(
            init_coords,
            s,
            model.init_time_jd,
            f * az_err0,
            f * alt_err0,
        );
        let cur_eq = horizontal_to_equatorial_geometric(cur_topo, s, model.init_time_jd);
        let cur_solve = Solve {
            ra_deg: cur_eq.ra_deg,
            dec_deg: cur_eq.dec_deg,
            position_angle_deg: model.init_pa_deg,
            time_jd_utc: model.init_time_jd,
        };

        // True residual: decompose the axis after the same partial gimbal move.
        let axis_f = gimbal_rotate(axis0_v, f * az_err0, f * alt_err0);
        let (res_alt, res_az) =
            calculate_mount_axis_error(axis_to_altaz(axis_f), lat, &refr, true, lat > 0.0);

        let e = tppa_update(&model, &cur_solve).unwrap();
        assert!(
            (e.alt_arcmin - res_alt * 60.0).abs() < 0.2,
            "f {f}: alt {:.4}' true residual {:.4}'",
            e.alt_arcmin,
            res_alt * 60.0
        );
        assert!(
            (wrap180((e.az_arcmin / 60.0) - res_az) * 60.0).abs() < 0.2,
            "f {f}: az {:.4}' true residual {:.4}'",
            e.az_arcmin,
            res_az * 60.0
        );
    }

    // Convergence: after a full correction the reported total error is small
    // (near-perfect alignment; the residual is dominated by the real alt/az
    // gimbal cross-term).
    let cur_topo =
        get_destination_coordinates(init_coords, s, model.init_time_jd, az_err0, alt_err0);
    let cur_eq = horizontal_to_equatorial_geometric(cur_topo, s, model.init_time_jd);
    let cur_solve = Solve {
        ra_deg: cur_eq.ra_deg,
        dec_deg: cur_eq.dec_deg,
        position_angle_deg: model.init_pa_deg,
        time_jd_utc: model.init_time_jd,
    };
    let e_full = tppa_update(&model, &cur_solve).unwrap();
    assert!(
        e_full.total_arcmin < 1.0,
        "full-correction total error {:.4}'",
        e_full.total_arcmin
    );
}

#[test]
fn missing_image_geometry_update_errors() {
    let refr = test_refr();
    let s = site(45.0);
    let axis = place_axis(45.0, 1.0, 1.0);
    let solves = synth_solves(axis, 25.0, 40.0, s, JD_2000, &refr, [30.0; 3]);
    let opts = TppaOptions {
        refraction: refr,
        correct_for_refraction: true,
        ..Default::default() // no image geometry
    };
    let (model, _e) = tppa_from_three(&solves, s, &opts).unwrap();
    assert_eq!(
        tppa_update(&model, &solves[2]).unwrap_err(),
        TppaError::MissingImageGeometry
    );
}

// Sanity property from dossier §12.6: transforming the refraction-affected
// solves back to alt/az WITHOUT refraction yields altitudes strictly lower than
// the original observed altitudes.
#[test]
fn refraction_lowers_altitude_direction() {
    let refr = test_refr();
    let s = site(40.0);
    let axis = place_axis(40.0, 1.0, 1.0);
    let solves = synth_solves(axis, 25.0, 40.0, s, JD_2000, &refr, [30.0; 3]);
    for sv in &solves {
        let rd = RaDec {
            ra_deg: sv.ra_deg,
            dec_deg: sv.dec_deg,
        };
        let with_refr = position_from_solve(rd, sv.position_angle_deg, s, sv.time_jd_utc, &refr);
        let geom = equatorial_to_horizontal_geometric(rd, s, sv.time_jd_utc);
        assert!(
            geom.alt_deg < with_refr.topocentric.alt_deg,
            "geom {:.4} should be below observed {:.4}",
            geom.alt_deg,
            with_refr.topocentric.alt_deg
        );
    }
}

/// Foundations check: a pure-RA circle (constant dec about the true pole) yields
/// ~zero error (dossier §12 test 2).
#[test]
fn pure_ra_circle_zero_error() {
    // Build three points at constant dec around the pole directly in equatorial
    // space, no refraction.
    let s = site(49.0);
    let no_refr = RefractionParams {
        pressure_hpa: 0.0,
        temperature_c: 0.0001,
        relative_humidity: 0.0,
        wavelength_um: 0.0,
    };
    let solves = [
        Solve {
            ra_deg: 20.0,
            dec_deg: 80.0,
            position_angle_deg: 0.0,
            time_jd_utc: JD_2000,
        },
        Solve {
            ra_deg: 60.0,
            dec_deg: 80.0,
            position_angle_deg: 0.0,
            time_jd_utc: JD_2000,
        },
        Solve {
            ra_deg: 90.0,
            dec_deg: 80.0,
            position_angle_deg: 0.0,
            time_jd_utc: JD_2000,
        },
    ];
    let opts = TppaOptions {
        refraction: no_refr,
        correct_for_refraction: true,
        ..Default::default()
    };
    let (_m, err) = tppa_from_three(&solves, s, &opts).unwrap();
    // A perfect circle about the celestial pole -> axis at the pole -> ~0 error.
    assert!(err.total_arcmin < 0.1, "total {:.4}'", err.total_arcmin);
}
