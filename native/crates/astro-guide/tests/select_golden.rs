// SPDX-License-Identifier: Apache-2.0
//
// Provenance: golden-vector tests for astro-guide's AutoFind + primary
// selection port (src/select.rs). Derived from PHD2 star.cpp:515-544
// (`GetStats`), star.cpp:574-656 (`psf_conv`), star.cpp:718-1154
// (`GuideStar::AutoFind`) via the audited algorithm dossier
// docs/native-parity/algorithms/phd2-guiding.md (§2.1-§2.5, §2.7)
// (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied from PHD2.

// Provenance: hand-derived from dossier §2 pipeline; a 3-Gaussian field
// with one edge star + one 3px duplicate is a direct parity target for
// merge/edge/selection.

use astro_guide::select::{
    auto_find, saturation_threshold, select_primary, Candidate, SelectParams,
};
use astro_guide::starfind::FindParams;

/// Build a WxH u16 frame: flat background `bg` plus one Gaussian bump per
/// `(cx, cy, amp, sigma)` entry in `stars`, summed additively and clamped
/// to `u16::MAX` (reuses the single-star `gaussian_frame` pattern from
/// `starfind_golden.rs`, generalized to multiple stars).
fn multi_gaussian_frame(w: usize, h: usize, bg: u16, stars: &[(f64, f64, f64, f64)]) -> Vec<u16> {
    let mut acc = vec![bg as f64; w * h];
    for &(cx, cy, amp, sg) in stars {
        for y in 0..h {
            for x in 0..w {
                let dx = x as f64 - cx;
                let dy = y as f64 - cy;
                acc[y * w + x] += amp * (-(dx * dx + dy * dy) / (2.0 * sg * sg)).exp();
            }
        }
    }
    acc.into_iter().map(|v| v.min(65535.0) as u16).collect()
}

/// The shared fixture: three well-separated "real" stars (A brightest, B,
/// C dimmest), a 3px-offset near-duplicate of A (dimmer, amp 3000 vs A's
/// 8000), and a star planted within the default `search_region` (15px) of
/// the left edge. All pairwise separations between *unrelated* features
/// exceed `search_region + 5` (the search-box-conflict distance) so only
/// the intended merge/edge interactions fire.
fn planted_field() -> (usize, usize, Vec<u16>) {
    let (w, h) = (201usize, 181usize);
    let stars = [
        (100.0, 90.0, 8000.0, 1.8),  // A: brightest
        (103.0, 90.0, 3000.0, 1.8),  // A's 3px duplicate (dimmer)
        (40.0, 40.0, 5000.0, 1.8),   // B
        (160.0, 140.0, 3200.0, 1.8), // C: dimmest of the three named stars
        (10.0, 90.0, 4000.0, 1.8),   // near left edge (x=10 <= search_region 15)
    ];
    (w, h, multi_gaussian_frame(w, h, 100, &stars))
}

#[test]
fn auto_find_returns_three_brightest_first_merges_duplicate_drops_edge() {
    let (w, h, px) = planted_field();
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let cands = auto_find(&gf, &SelectParams::default());

    // The 3px duplicate is absorbed into A's candidate (either by local-max
    // suppression or the explicit merge step — see src/select.rs's
    // `merge_close_peaks` doc comment) and the edge star is dropped, so
    // exactly the three named stars (A, B, C) survive.
    assert_eq!(cands.len(), 3, "cands={cands:?}");

    // Brightest-first ordering (by measured mass, monotonic with the
    // planted amplitudes here).
    assert!(cands[0].mass > cands[1].mass, "cands={cands:?}");
    assert!(cands[1].mass > cands[2].mass, "cands={cands:?}");

    // A: centroid is pulled toward the (dimmer, absorbed) duplicate at
    // (103, 90) by its share of the combined flux — expect roughly
    // 100 + 3*(3000/11000) ~= 100.8, well inside a 2px tolerance.
    assert!((cands[0].x - 100.0).abs() < 2.0, "x={}", cands[0].x);
    assert!((cands[0].y - 90.0).abs() < 0.5, "y={}", cands[0].y);

    // B and C: isolated, so tight position tolerances apply directly.
    assert!((cands[1].x - 40.0).abs() < 0.5, "x={}", cands[1].x);
    assert!((cands[1].y - 40.0).abs() < 0.5, "y={}", cands[1].y);
    assert!((cands[2].x - 160.0).abs() < 0.5, "x={}", cands[2].x);
    assert!((cands[2].y - 140.0).abs() < 0.5, "y={}", cands[2].y);

    // No survivor near the planted edge star (x=10).
    assert!(cands.iter().all(|c| c.x > 20.0), "cands={cands:?}");
}

#[test]
fn select_primary_returns_brightest_non_saturated() {
    let (w, h, px) = planted_field();
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let params = SelectParams::default();
    let cands = auto_find(&gf, &params);

    let find_params = FindParams {
        search_region: params.search_region,
        ..FindParams::default()
    };
    let peak_positions: Vec<(i32, i32)> = cands.iter().map(|c| (c.x as i32, c.y as i32)).collect();
    let sat_thresh = saturation_threshold(&gf, &peak_positions, &find_params);

    let picked = select_primary(&cands, sat_thresh, params.af_min_snr);
    assert_eq!(picked, Some(0), "cands={cands:?} sat_thresh={sat_thresh}");
}

fn cand(peak_val: u16, snr: f64) -> Candidate {
    Candidate {
        x: 0.0,
        y: 0.0,
        snr,
        mass: 1000.0,
        hfd: 3.0,
        peak_val,
    }
}

#[test]
fn select_primary_pass1_skips_near_saturated_brightest() {
    // idx0: brightest but near-saturated (peak_val > sat_thresh) -> pass 1
    // rejects it; idx1: dimmer, within sat_thresh, good SNR -> pass 1 wins.
    let cands = [cand(60000, 50.0), cand(20000, 20.0)];
    assert_eq!(select_primary(&cands, 50000, 6.0), Some(1));
}

#[test]
fn select_primary_pass2_accepts_high_peak_val_when_pass1_is_empty() {
    // Both candidates exceed sat_thresh (pass 1 has nothing to pick), but
    // idx1 clears the SNR gate -> pass 2 picks it.
    let cands = [cand(60000, 2.0), cand(58000, 20.0)];
    assert_eq!(select_primary(&cands, 50000, 6.0), Some(1));
}

#[test]
fn select_primary_pass3_falls_back_to_brightest_when_all_fail_snr() {
    // Every candidate is below af_min_snr regardless of peak_val -> passes
    // 1 and 2 both come up empty; pass 3 takes the brightest (first).
    let cands = [cand(1000, 2.0), cand(900, 1.0)];
    assert_eq!(select_primary(&cands, 50000, 6.0), Some(0));
}

#[test]
fn select_primary_empty_list_returns_none() {
    assert_eq!(select_primary(&[], 50000, 6.0), None);
}

#[test]
fn saturation_threshold_known_adu_uses_adu_plus_pedestal() {
    let (w, h) = (11usize, 11usize);
    let px = vec![100u16; w * h];
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let fp = FindParams {
        max_adu: 60000,
        pedestal: 100,
        ..FindParams::default()
    };
    // sat_level = 60000 + 100 = 60100; range = 60000;
    // thresh = 100 + 9*60000/10 = 54100.
    assert_eq!(saturation_threshold(&gf, &[], &fp), 54100);
}

#[test]
fn saturation_threshold_infers_from_flat_top_when_adu_unknown() {
    // Provenance: same flat-top construction as starfind_golden.rs's
    // `default_flat_top_heuristic_saturated` (max3 = [60000, 59998, 59998]
    // -> StarSaturated on the shipped-default flat-top heuristic path).
    let (w, h) = (41usize, 41usize);
    let mut px = multi_gaussian_frame(w, h, 100, &[(20.0, 20.0, 50000.0, 2.0)]);
    px[20 * w + 20] = 60000;
    for &(ox, oy) in &[(19usize, 20usize), (21, 20), (20, 19), (20, 21)] {
        px[oy * w + ox] = 59998;
    }
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let fp = FindParams::default(); // max_adu=0 -> infer from the image
                                    // max_val = 60000 = peak_val -> diff=0 -> found_saturated=true ->
                                    // sat_level=60000; range=60000; thresh = 9*60000/10 = 54000.
    assert_eq!(saturation_threshold(&gf, &[(20, 20)], &fp), 54000);
}
