// SPDX-License-Identifier: Apache-2.0
//
// Provenance: golden-vector tests for astro-guide's Star::Find port
// (src/starfind.rs). Derived from PHD2 star.cpp:83-124 (`hfr`),
// star.cpp:126-483 (`Star::Find`, FindCentroid path) via the audited
// algorithm dossier docs/native-parity/algorithms/phd2-guiding.md
// (§1.3-§1.4) (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied
// from PHD2.

// Provenance: fixtures hand-derived from dossier §1.3 (FindCentroid) / §1.4 (hfr).
// A synthetic Gaussian star on a flat background is a direct parity target:
// the 3x3-smoothed peak, 2-sigma-clipped annulus background, and mass-weighted
// centroid recover the injected sub-pixel center to <0.05 px (dossier §17:
// "unit test against synthetic Gaussians + hot pixels").
use astro_guide::starfind::{
    star_find, was_found, FindParams, FindResult, CENTROID_DISK_RADIUS_PX,
};

/// Build a WxH u16 frame: flat background `bg`, one Gaussian star at (cx,cy)
/// with peak amplitude `amp` and sigma `sg` (background-additive).
fn gaussian_frame(w: usize, h: usize, bg: u16, cx: f64, cy: f64, amp: f64, sg: f64) -> Vec<u16> {
    let mut v = vec![bg; w * h];
    for y in 0..h {
        for x in 0..w {
            let dx = x as f64 - cx;
            let dy = y as f64 - cy;
            let g = amp * (-(dx * dx + dy * dy) / (2.0 * sg * sg)).exp();
            v[y * w + x] = (bg as f64 + g).min(65535.0) as u16;
        }
    }
    v
}

#[test]
fn centroid_recovers_subpixel_gaussian() {
    // 41x41, bg=100, star at (20.30, 19.70), amp=4000, sigma=1.6.
    let (w, h) = (41usize, 41usize);
    let px = gaussian_frame(w, h, 100, 20.30, 19.70, 4000.0, 1.6);
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let r = star_find(&gf, 20.0, 20.0, &FindParams::default());
    assert!(was_found(r.result), "result was {:?}", r.result);
    assert!((r.x - 20.30).abs() < 0.05, "x={}", r.x);
    assert!((r.y - 19.70).abs() < 0.05, "y={}", r.y);
    assert!(r.snr > 10.0, "snr={}", r.snr);
    // HFD of a sigma=1.6 Gaussian ~ 2*sigma*1.1774 ~ 3.77 px (half-flux diameter).
    assert!(r.hfd > 3.0 && r.hfd < 4.5, "hfd={}", r.hfd);
}

#[test]
fn hot_pixel_rejected_low_hfd() {
    // Single bright pixel on flat bg: hfr()==0.25 -> hfd==0.5 < min_hfd 1.5.
    let (w, h) = (31usize, 31usize);
    let mut px = vec![100u16; w * h];
    px[15 * w + 15] = 60000;
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let r = star_find(&gf, 15.0, 15.0, &FindParams::default());
    assert!(
        matches!(r.result, FindResult::StarLowHfd),
        "result={:?}",
        r.result
    );
}

#[test]
fn empty_field_low_mass_or_snr() {
    let (w, h) = (31usize, 31usize);
    let px = vec![100u16; w * h];
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let r = star_find(&gf, 15.0, 15.0, &FindParams::default());
    assert!(!was_found(r.result));
    assert!(matches!(
        r.result,
        FindResult::StarLowMass | FindResult::StarLowSnr
    ));
}

#[test]
#[allow(clippy::field_reassign_with_default)] // brief's literal fixture form
fn saturated_star_still_found() {
    // Flat-topped star: many pixels at 65535 -> StarSaturated (still "found").
    let (w, h) = (41usize, 41usize);
    let mut px = gaussian_frame(w, h, 100, 20.0, 20.0, 200000.0, 2.2); // clips flat
                                                                       // ensure a broad flat top
    for y in 18..23 {
        for x in 18..23 {
            px[y * w + x] = 65535;
        }
    }
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let mut p = FindParams::default();
    p.max_adu = 65535;
    let r = star_find(&gf, 20.0, 20.0, &p);
    assert!(matches!(r.result, FindResult::StarSaturated));
    assert!(was_found(r.result));
    // Reviewer check: the HFD must genuinely land inside (min_hfd, max_hfd)
    // so control reaches the saturation branch (star.cpp:412-446) rather
    // than exiting at an HFD gate. Bounds read from `p` itself (not
    // hardcoded) so this stays true if the defaults ever move.
    assert!(r.hfd > p.min_hfd && r.hfd < p.max_hfd, "hfd={}", r.hfd);
}

#[test]
fn hot_pixel_single_survivor_hfr_quarter_branch() {
    // Provenance: hand-trace of star.cpp:229-297 (annulus loop) + :83-124
    // (hfr single-pixel branch). Fixture: 31x31 checkerboard background
    // (98 where (x+y) is even, else 102 — a deterministic few-ADU
    // dispersion so sigma_bg > 0 and thresh > bg, unlike the flat-bg
    // hot-pixel vector above whose zero-sigma thresh keeps every aperture
    // pixel), hot pixel 60000 at (15,15). The 3x3-smoothed peak lands on
    // the hot pixel; the annulus (49 < r^2 <= 144) around it holds 292
    // pixels: 156 at 98, 136 at 102 (the hot pixel is at r=0, outside it).
    //   iter0: mean = (156*98 + 136*102)/292 = 29160/292 = 99.86301,
    //          sigma2 = 3.99492, sigma = 1.99873.
    //   iter1: clip bounds 99.863 +/- 2*1.99873 = (95.866, 103.860) keep
    //          both bg values -> identical mean, |dmean| = 0 < 0.5 ->
    //          converged break.
    // thresh = (99.86301 + 3*1.99873 + 0.5) as u16 = trunc(106.359) = 106;
    // both bg values (98, 102) < 106, so ONLY the hot pixel survives the
    // r <= 7 aperture: hfrvec.len() == 1 -> hfr() == 0.25 (star.cpp:85-86)
    // -> HFD = 0.5 < min_hfd 1.5 -> StarLowHfd (the computed HFD is kept
    // in the result on this gate, per star.cpp:396-404).
    // mass = 60000 - 29160/292 = 59900.137 (exact in f64: integer sum /
    // count); snr = mass/sqrt(mass/0.5 + 3.99492*1*(1 + 1/292)) = 173.058.
    let (w, h) = (31usize, 31usize);
    let mut px: Vec<u16> = (0..w * h)
        .map(|i| if (i % w + i / w) % 2 == 0 { 98 } else { 102 })
        .collect();
    px[15 * w + 15] = 60000;
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let r = star_find(&gf, 15.0, 15.0, &FindParams::default());
    assert!(
        matches!(r.result, FindResult::StarLowHfd),
        "result={:?}",
        r.result
    );
    assert!(!was_found(r.result));
    // The genuine hfr()==0.25 single-pixel branch: hfd exactly 0.5.
    assert!((r.hfd - 0.5).abs() < 1e-12, "hfd={}", r.hfd);
    // Centroid collapsed onto the hot pixel (cx = 0*d): position (15, 15).
    assert!((r.x - 15.0).abs() < 1e-12, "x={}", r.x);
    assert!((r.y - 15.0).abs() < 1e-12, "y={}", r.y);
    let expected_mass = 60000.0 - 29160.0 / 292.0;
    assert!((r.mass - expected_mass).abs() < 1e-9, "mass={}", r.mass);
    assert!((r.snr - 173.058).abs() < 1e-3, "snr={}", r.snr);
    assert_eq!(r.peak_val, 60000);
}

#[test]
fn annulus_outliers_clipped_then_converges() {
    // Provenance: hand-trace of star.cpp:248-297 (the 9-iteration 2-sigma
    // clip: outlier rejection at iter > 0 AND the |dmean| < 0.5 convergence
    // break). Fixture: 41x41, flat bg 100, Gaussian star amp 4000 sigma 1.6
    // centered exactly at (20,20) (its tail adds < 0.28 ADU at r >= 7, so
    // every non-outlier annulus pixel truncates to exactly 100), plus 4
    // outlier pixels = 3000 injected INTO the annulus at r=9:
    // (29,20),(11,20),(20,29),(20,11). Annulus (49 < r^2 <= 144) around
    // the peak (20,20) holds 292 pixels: 288 at 100, 4 at 3000.
    //   iter0: mean = (288*100 + 4*3000)/292 = 40800/292 = 139.726,
    //          sigma = 337.67 -> clip bounds (-535.6, 815.1).
    //   iter1: the 4 outliers (3000) fall outside the bounds -> rejected;
    //          nbg = 288, mean = 100 exactly, sigma = 0;
    //          |dmean| = 39.726 >= 0.5 -> NO convergence break yet.
    //   iter2: bounds 100 +/- 0 keep exactly the 288 pixels at 100 ->
    //          mean = 100, |dmean| = 0 < 0.5 -> converged break.
    // Final: mean_bg = 100, sigma2_bg = 0, nbg = 288.
    // thresh = (100 + 0 + 0.5) as u16 = 100, so every pixel of the r <= 7
    // disk (149 px) survives (star.cpp:344-345 skips only val < thresh;
    // bg pixels contribute d = 0) and mass = sum(val - 100) over the disk
    // = 64252 (reference value; asserted below against an independent sum
    // over the fixture buffer to stay exact under libm exp() ULP
    // variation). With sigma2_bg = 0 the Simonetti SNR reduces exactly to
    // snr = mass/sqrt(mass/0.5) = sqrt(mass/2) = 179.237. The outliers sit
    // outside the disk, so they affect neither mass nor max3
    // (3000 < the star's 3390 edge-neighbors): max3 = [4100, 3390, 3390],
    // d = 710, d*65535 >= 32*4100 -> not saturated -> StarOk.
    let (w, h) = (41usize, 41usize);
    let mut px = gaussian_frame(w, h, 100, 20.0, 20.0, 4000.0, 1.6);
    for &(ox, oy) in &[(29usize, 20usize), (11, 20), (20, 29), (20, 11)] {
        px[oy * w + ox] = 3000;
    }
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let r = star_find(&gf, 20.0, 20.0, &FindParams::default());
    assert!(
        matches!(r.result, FindResult::StarOk),
        "result={:?}",
        r.result
    );
    // Independent expected mass: sum(val - 100) over the r <= 7 disk of
    // the fixture buffer (valid because the traced thresh is exactly 100).
    let mut expected_mass = 0.0f64;
    for y in 0..h {
        for x in 0..w {
            let (dx, dy) = (x as i32 - 20, y as i32 - 20);
            if dx * dx + dy * dy <= 49 {
                expected_mass += (px[y * w + x] - 100) as f64;
            }
        }
    }
    assert!((expected_mass - 64252.0).abs() < 4.0, "{}", expected_mass);
    assert!((r.mass - expected_mass).abs() < 1e-9, "mass={}", r.mass);
    let expected_snr = (expected_mass / 2.0).sqrt();
    assert!((r.snr - expected_snr).abs() < 1e-6, "snr={}", r.snr);
    // Symmetric star, integer-valued pixels: centroid is exactly (20, 20).
    assert!((r.x - 20.0).abs() < 1e-9, "x={}", r.x);
    assert!((r.y - 20.0).abs() < 1e-9, "y={}", r.y);
    assert!(r.hfd > 3.0 && r.hfd < 4.5, "hfd={}", r.hfd);
}

#[test]
fn default_flat_top_heuristic_saturated() {
    // Provenance: dossier §1.3 step 10 / star.cpp:430-446 — the flat-top
    // saturation heuristic on the SHIPPED default path (max_adu == 0,
    // bits_per_pixel == 16), which the explicit-max_adu vector above never
    // reaches. Fixture: Gaussian amp 50000 sigma 2.0 at (20,20) (center px
    // 50100 — no clipping), then a NEAR-equal flat top stamped on:
    // (20,20) = 60000 and its 4-neighborhood = 59998 (diagonals stay at
    // the Gaussian's 39040, so the top-3 raw values are 60000, 59998,
    // 59998). Trace: max3 = [60000, 59998, 59998] -> d = max3[0] - max3[2]
    // = 2; mx = 60000 - pedestal(0) = 60000. 16-bit branch (bpp 16 >= 12):
    // d*65535 = 131070 < 32*mx = 1920000 -> StarSaturated (still "found").
    let (w, h) = (41usize, 41usize);
    let mut px = gaussian_frame(w, h, 100, 20.0, 20.0, 50000.0, 2.0);
    px[20 * w + 20] = 60000;
    for &(ox, oy) in &[(19usize, 20usize), (21, 20), (20, 19), (20, 21)] {
        px[oy * w + ox] = 59998;
    }
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let p = FindParams::default();
    let r = star_find(&gf, 20.0, 20.0, &p);
    assert!(
        matches!(r.result, FindResult::StarSaturated),
        "result={:?}",
        r.result
    );
    assert!(was_found(r.result));
    assert_eq!(r.peak_val, 60000);
    // Control reached step 10: HFD passed both gates on the way. Bounds
    // read from `p` itself (not hardcoded) so this stays true if the
    // defaults ever move.
    assert!(r.hfd > p.min_hfd && r.hfd < p.max_hfd, "hfd={}", r.hfd);
}

#[test]
fn oversized_star_rejected_high_hfd() {
    // The `max_hfd` gate at the SHIPPED default actually firing. Task #191:
    // `FindParams::default().max_hfd` used to be 20.0, a value HFD can
    // never reach given this aperture (see the doc comment on
    // `FindParams::max_hfd`), so `StarHiHfd` was dead code from any real
    // input. This is a genuine, physically-plausible single-peaked star
    // (one broad Gaussian, not a pathological multi-blob fixture) wide
    // enough to still clear the mass/SNR gates comfortably (mass in the
    // millions, SNR > 600) while its HFD lands past the aperture-derived
    // default -- proving the gate rejects a real oversized/diffuse
    // detection instead of never firing at all.
    let (w, h) = (81usize, 81usize);
    let px = gaussian_frame(w, h, 500, 40.0, 40.0, 20000.0, 3.2);
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let p = FindParams::default();
    let r = star_find(&gf, 40.0, 40.0, &p);
    assert!(
        matches!(r.result, FindResult::StarHiHfd),
        "result={:?} hfd={} mass={} snr={}",
        r.result,
        r.hfd,
        r.mass,
        r.snr
    );
    assert!(!was_found(r.result));
    assert!(r.hfd > p.max_hfd, "hfd={} max_hfd={}", r.hfd, p.max_hfd);
    // Sanity: this genuinely reached the HFD gate on its own merits, not
    // because mass or SNR were already marginal.
    assert!(r.mass > 1_000_000.0, "mass={}", r.mass);
    assert!(r.snr > 100.0, "snr={}", r.snr);
}

/// Detector for this bug's shape, not just this instance of it (task #191).
/// A "guide-quality threshold" here means any `FindParams` field that gates
/// acceptance on a value this module's own measurement produces (`min_hfd`,
/// `max_hfd` today). Such a threshold is dead the moment it sits outside the
/// range its own measurement can reach -- exactly what shipped for
/// `max_hfd` (20.0, while HFD's provable ceiling given the
/// `CENTROID_DISK_RADIUS_PX`-radius aperture is `2 * CENTROID_DISK_RADIUS_PX`
/// = 14, itself only approached in the limit by a physically-degenerate
/// fixture no real star produces). Whoever adds the next such threshold
/// should add its case here alongside `max_hfd`'s.
#[test]
fn guide_quality_thresholds_stay_inside_their_measurement_range() {
    let p = FindParams::default();
    let hfd_ceiling = 2.0 * CENTROID_DISK_RADIUS_PX as f64;

    // 1. Provable-bound check: a threshold at or above the ceiling its own
    //    measurement can produce can never fire, by construction.
    assert!(
        p.max_hfd < hfd_ceiling,
        "max_hfd={} is at/above HFD's provable ceiling ({} = \
         2 * CENTROID_DISK_RADIUS_PX); a gate set there can never fire",
        p.max_hfd,
        hfd_ceiling
    );
    assert!(
        p.min_hfd >= 0.0 && p.min_hfd < hfd_ceiling,
        "min_hfd={} is outside HFD's reachable range [0, {})",
        p.min_hfd,
        hfd_ceiling
    );
    assert!(
        p.min_hfd < p.max_hfd,
        "min_hfd={} >= max_hfd={}; the acceptance window is empty",
        p.min_hfd,
        p.max_hfd
    );

    // 2. Reachability, not just an upper bound: the ceiling in (1) is only
    //    approached in the limit by a fixture no real star produces, so
    //    "under the ceiling" alone would not have caught the original bug
    //    at a smaller but still-unreachable value. Confirm the shipped
    //    default rejects a genuine oversized-but-measurable star via
    //    StarHiHfd specifically, not StarLowMass/StarLowSnr arriving first
    //    (which would mean max_hfd is still functionally unreachable).
    let (w, h) = (81usize, 81usize);
    let px = gaussian_frame(w, h, 500, 40.0, 40.0, 20000.0, 3.2);
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let r = star_find(&gf, 40.0, 40.0, &p);
    assert!(
        matches!(r.result, FindResult::StarHiHfd),
        "expected StarHiHfd on an oversized-but-measurable star (mass={}, \
         snr={}) at the shipped default max_hfd={}; got {:?} (hfd={}) -- \
         the gate cannot fire at this default",
        r.mass,
        r.snr,
        p.max_hfd,
        r.result,
        r.hfd
    );
}
