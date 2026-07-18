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
use astro_guide::starfind::{star_find, was_found, FindParams, FindResult};

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
}
