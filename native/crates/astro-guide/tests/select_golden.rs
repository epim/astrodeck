// SPDX-License-Identifier: Apache-2.0
//
// Provenance: golden-vector tests for astro-guide's AutoFind + primary
// selection port (src/select.rs). Derived from PHD2 star.cpp:515-544
// (`GetStats`), star.cpp:574-656 (`psf_conv`), star.cpp:718-1154
// (`GuideStar::AutoFind`), and image_math.cpp:150-505 (`Median3` pre-filter)
// via the audited algorithm dossier
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

    // The 3px duplicate is absorbed into A's candidate (by local-max
    // suppression: at 3px the two peaks share a Chebyshev-4 window, and the
    // dimmer one's local-max test fails — see src/select.rs's
    // `merge_close_peaks` doc comment for why the explicit merge step only
    // fires on exact response ties) and the edge star is dropped, so
    // exactly the three named stars (A, B, C) survive.
    //
    // Fix round 1 re-derivation: auto_find now applies the unconditional
    // Median3 pre-filter (image_math.cpp:150-505 via star.cpp:752) to the
    // PSF-convolution INPUT only. For these smooth sigma-1.8 Gaussians the
    // 3x3 median erodes each conv-input peak toward its edge-neighbor
    // value (~86% of amplitude) uniformly across all planted stars, so
    // detection, the h brightness ordering (A > B > C tracks amplitude),
    // and the merge/edge outcomes are unchanged. Candidate MEASUREMENT
    // (star_find: positions/mass/snr/hfd/peak_val) runs on the ORIGINAL
    // frame exactly as upstream measures `image`, not `smoothed`
    // (star.cpp:1072), so every position/mass assertion below is untouched
    // by the median. Re-verified: all tolerances hold unchanged.
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

    // None of the planted Gaussians is flat-topped, so no candidate
    // carries the hard-saturation flag (fix round 1 field).
    assert!(cands.iter().all(|c| !c.saturated), "cands={cands:?}");
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
    sat_cand(peak_val, snr, false)
}

fn sat_cand(peak_val: u16, snr: f64, saturated: bool) -> Candidate {
    Candidate {
        x: 0.0,
        y: 0.0,
        snr,
        mass: 1000.0,
        hfd: 3.0,
        peak_val,
        saturated,
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
fn select_primary_hard_saturated_rejected_in_pass1_and_pass2() {
    // Fix round 1 (review): upstream rejects STAR_SATURATED candidates in
    // BOTH pass 1 and pass 2 (star.cpp:1084, star.cpp:1089), independently
    // of the soft peak_val <= sat_thresh cutoff. A flat-topped star with
    // good SNR whose peak_val is *below* sat_thresh must therefore not win
    // pass 1 (previously it could); one above sat_thresh must not win
    // pass 2 (previously it could).

    // Pass 1: brightest candidate is hard-saturated but below sat_thresh
    // with high SNR -> rejected on `saturated`; the clean dimmer candidate
    // wins pass 1.
    let flat_top = sat_cand(40000, 50.0, true);
    let clean = sat_cand(20000, 20.0, false);
    assert_eq!(select_primary(&[flat_top, clean], 50000, 6.0), Some(1));

    // Pass 2: both candidates exceed sat_thresh (pass 1 empty); the hard-
    // saturation rejection still applies, so the clean one wins pass 2.
    let flat_top_hi = sat_cand(60000, 50.0, true);
    let clean_hi = sat_cand(58000, 20.0, false);
    assert_eq!(
        select_primary(&[flat_top_hi, clean_hi], 50000, 6.0),
        Some(1)
    );

    // Alone, the flat-top star fails pass 1 and pass 2 on `saturated` and
    // is selectable only via the unconditional pass 3.
    assert_eq!(select_primary(&[flat_top], 50000, 6.0), Some(0));
}

#[test]
fn auto_find_populates_saturated_and_pass1_defers_flat_top() {
    // End-to-end version of the vector above: auto_find must populate
    // Candidate::saturated from star_find's flat-top heuristic (auto_find
    // measures with max_adu = 0), and select_primary must then defer the
    // brighter flat-topped star. Fixture: a flat-top star built with the
    // starfind_golden.rs `default_flat_top_heuristic_saturated` recipe
    // scaled to peak 50000 (center 50000, 4-neighborhood 49998 stamped on
    // a 50000-amp sigma-2.0 Gaussian: max3 = [50000, 49998, 49998], d = 2,
    // d*65535 = 131070 < 32*50000 = 1600000 -> StarSaturated), plus a
    // clean amp-8000 star. With a camera-known saturation ADU of 65535
    // (sat_thresh = 9*65535/10 = 58981), the flat-top's peak_val 50000 is
    // BELOW sat_thresh — so before fix round 1 it (wrongly) won pass 1 on
    // SNR alone; upstream and this port defer it, and the clean star wins.
    let (w, h) = (121usize, 121usize);
    let mut px = multi_gaussian_frame(
        w,
        h,
        100,
        &[(40.0, 60.0, 50000.0, 2.0), (85.0, 60.0, 8000.0, 1.8)],
    );
    px[60 * w + 40] = 50000;
    for &(ox, oy) in &[(39usize, 60usize), (41, 60), (40, 59), (40, 61)] {
        px[oy * w + ox] = 49998;
    }
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let cands = auto_find(&gf, &SelectParams::default());

    assert_eq!(cands.len(), 2, "cands={cands:?}");
    // Brightest-first: the flat-top star leads, and carries the flag.
    assert!(cands[0].saturated, "cands={cands:?}");
    assert!(!cands[1].saturated, "cands={cands:?}");
    assert!((cands[1].x - 85.0).abs() < 0.5, "x={}", cands[1].x);

    // sat_thresh from a camera-known saturation ADU (dossier §2.5 known-
    // ADU branch): 0 + 9*65535/10 = 58981.
    let fp = FindParams {
        max_adu: 65535,
        ..FindParams::default()
    };
    let sat_thresh = saturation_threshold(&gf, &[], &fp);
    assert_eq!(sat_thresh, 58981);
    assert!(cands[0].peak_val <= sat_thresh, "cands={cands:?}");

    // Pass 1 defers the hard-saturated (yet below-sat_thresh) flat top;
    // the clean star wins.
    assert_eq!(select_primary(&cands, sat_thresh, 6.0), Some(1));
    // Alone, the flat top is still selectable — via pass 3 only.
    assert_eq!(select_primary(&cands[..1], sat_thresh, 6.0), Some(0));
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
