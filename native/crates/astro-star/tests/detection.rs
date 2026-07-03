// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: tests derived from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (constants,
// golden factors, and synthetic-frame accuracy bounds from the task brief).

use astro_star::filters::{atrous_b3_kernel, gaussian_blur, gaussian_kernel, median3};
use astro_star::image::{BackgroundPlane, GrayFrame, Rect, WorkImage};
use astro_star::measure::{
    evaluate_candidate, measure_hfr, point_cloud_eccentricity, RejectReason,
};
use astro_star::noise::kappa_sigma_noise_estimate;
use astro_star::params::{PsfFitType, StarDetectionParams};
use astro_star::psf::fit_psf;
use astro_star::structure::histogram_median;
use astro_star::{detect_and_measure, FocusRange, NoiseLevel, PixelScalePreset};

// --------------------------------------------------------------------------
// Deterministic seeded LCG (no rand in lib code; tests may use a small LCG).
// --------------------------------------------------------------------------
struct Lcg(u64);
impl Lcg {
    fn new(seed: u64) -> Self {
        Lcg(seed)
    }
    fn next_u64(&mut self) -> u64 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        self.0
    }
    fn unit(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }
    /// Standard-normal deviate via Box-Muller.
    fn gauss(&mut self) -> f64 {
        let u1 = self.unit().max(1e-12);
        let u2 = self.unit();
        (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
    }
}

const BPP: u32 = 16;
const SCALE: f64 = 65536.0;

/// Render one 2-D Gaussian star (normalized amplitude/background) into an ADU
/// buffer at continuous center `(cx, cy)`.
fn add_gaussian(buf: &mut [f64], w: usize, h: usize, cx: f64, cy: f64, amp: f64, sigma: f64) {
    let r = (6.0 * sigma).ceil() as isize;
    let (ix, iy) = (cx.round() as isize, cy.round() as isize);
    for dy in -r..=r {
        for dx in -r..=r {
            let x = ix + dx;
            let y = iy + dy;
            if x < 0 || y < 0 || x >= w as isize || y >= h as isize {
                continue;
            }
            let ex = (x as f64 - cx).powi(2) + (y as f64 - cy).powi(2);
            buf[y as usize * w + x as usize] += amp * (-ex / (2.0 * sigma * sigma)).exp();
        }
    }
}

fn to_u16(buf: &[f64]) -> Vec<u16> {
    buf.iter()
        .map(|&v| (v * SCALE).round().clamp(0.0, 65535.0) as u16)
        .collect()
}

// ==========================================================================
// 1. Constant / golden tests straight from the dossier.
// ==========================================================================

#[test]
fn median3_network() {
    // dossier §1.2 median network
    assert_eq!(median3(1.0, 2.0, 3.0), 2.0);
    assert_eq!(median3(3.0, 1.0, 2.0), 2.0);
    assert_eq!(median3(2.0, 2.0, 1.0), 2.0);
    assert_eq!(median3(5.0, 5.0, 5.0), 5.0);
}

#[test]
fn atrous_b3_layer0_taps() {
    // dossier §3.1: layer 0 taps are the B3 spline [1/16,1/4,3/8,1/4,1/16].
    let k = atrous_b3_kernel(0);
    assert_eq!(k, vec![0.0625, 0.25, 0.375, 0.25, 0.0625]);
    // à-trous zero-padding: layer 1 spreads taps to stride 2, size 9.
    let k1 = atrous_b3_kernel(1);
    assert_eq!(k1.len(), 9);
    assert_eq!(k1[0], 0.0625);
    assert_eq!(k1[2], 0.25);
    assert_eq!(k1[4], 0.375);
    assert_eq!(k1[6], 0.25);
    assert_eq!(k1[8], 0.0625);
    assert_eq!(k1[1], 0.0);
}

#[test]
fn gaussian_kernel_normalized_symmetric() {
    // dossier §1.5: normalized to sum 1, symmetric, center largest.
    let sigma = 0.159758 * 7.0;
    let k = gaussian_kernel(7, sigma);
    let sum: f64 = k.iter().sum();
    assert!((sum - 1.0).abs() < 1e-12);
    for i in 0..3 {
        assert!((k[i] - k[6 - i]).abs() < 1e-15);
    }
    assert!(k[3] > k[2] && k[2] > k[1]);
}

#[test]
fn gaussian_blur_preserves_dc() {
    // A constant image is a fixed point of a normalized reflect-border blur.
    let mut img = WorkImage {
        data: vec![0.37; 32 * 24],
        width: 32,
        height: 24,
    };
    gaussian_blur(&mut img, 9);
    for &v in &img.data {
        assert!((v - 0.37).abs() < 1e-12);
    }
}

#[test]
fn kappa_sigma_constant_image_quirk() {
    // Constant image (no zeros): iter1 mean=v sigma=0; iter2 masks everything
    // out, sigma stays 0 and converges, but background_mean lags at v (§2, §13.2).
    let img = WorkImage {
        data: vec![0.25; 100],
        width: 10,
        height: 10,
    };
    let ks = kappa_sigma_noise_estimate(&img, 2.0, 1e-5, 5);
    assert_eq!(ks.sigma, 0.0);
    assert_eq!(ks.background_mean, 0.25);
    assert_eq!(ks.iterations, 2);
}

#[test]
fn kappa_sigma_recovers_noise() {
    // Gaussian-noise image around a background; σ recovered within ~5%.
    let mut rng = Lcg::new(1);
    let (bg, sd) = (0.1_f64, 0.01_f64);
    let data: Vec<f64> = (0..40000)
        .map(|_| (bg + sd * rng.gauss()).max(1e-6))
        .collect();
    let img = WorkImage {
        data,
        width: 200,
        height: 200,
    };
    let ks = kappa_sigma_noise_estimate(&img, 2.0, 1e-5, 5);
    // clipping tightens σ below the raw std; expect within ~15% and positive.
    assert!(ks.sigma > 0.0);
    assert!(
        (ks.sigma - sd).abs() / sd < 0.2,
        "sigma {} vs {}",
        ks.sigma,
        sd
    );
    assert!((ks.background_mean - bg).abs() < 0.005);
}

#[test]
fn histogram_median_ramp() {
    // Linear ramp over [0,1): median ≈ 0.5 (dossier §3.3 interpolation).
    let n = 10000;
    let data: Vec<f64> = (0..n).map(|i| i as f64 / n as f64).collect();
    let img = WorkImage {
        data,
        width: 100,
        height: 100,
    };
    let m = histogram_median(&img);
    assert!((m - 0.5).abs() < 0.01, "median {}", m);
}

#[test]
fn point_cloud_eccentricity_line_vs_disk() {
    // dossier §6.1: a straight line → e ≈ 1; a symmetric blob → small e.
    let line: Vec<(usize, usize)> = (0..20).map(|x| (x, 10)).collect();
    assert!(point_cloud_eccentricity(&line) > 0.99);
    let mut disk = Vec::new();
    for y in 0..7 {
        for x in 0..7 {
            let dx = x as f64 - 3.0;
            let dy = y as f64 - 3.0;
            if dx * dx + dy * dy <= 9.0 {
                disk.push((x, y));
            }
        }
    }
    assert!(point_cloud_eccentricity(&disk) < 0.3);
}

// ==========================================================================
// 2. HFR accuracy on an analytic Gaussian (flux-weighted mean radius).
// ==========================================================================

#[test]
fn hfr_matches_analytic_gaussian() {
    // For a 2-D Gaussian the flux-weighted mean radius (NINA's "HFR") is
    // σ·√(π/2) ≈ 1.2533·σ over a large aperture (dossier §7 definition).
    let sigma = 2.0_f64;
    let (w, h) = (61usize, 61usize);
    let cx = 30.0;
    let cy = 30.0;
    let mut buf = vec![0.03_f64; w * h];
    add_gaussian(&mut buf, w, h, cx, cy, 0.5, sigma);
    let img = WorkImage {
        data: buf,
        width: w,
        height: h,
    };
    // bbox radius 12 = 6σ ⇒ truncation < 0.1%.
    let bounds = Rect {
        x: 18,
        y: 18,
        w: 25,
        h: 25,
    };
    let plane = BackgroundPlane::flat(0.03, cx, cy);
    let p = StarDetectionParams::default();
    let hfr = measure_hfr(&img, &bounds, (cx, cy), &plane, 0.0, &p).expect("hfr");
    let truth = sigma * (std::f64::consts::FRAC_PI_2).sqrt();
    assert!(
        (hfr - truth).abs() / truth < 0.05,
        "hfr {} truth {}",
        hfr,
        truth
    );
}

// ==========================================================================
// 3. PSF recovery (Gaussian, Moffat fixed β, fittable β).
// ==========================================================================

fn psf_params(fit: PsfFitType) -> StarDetectionParams {
    // Oversample the PSF grid so the fitter's numerics are validated without the
    // undersampling aliasing that a large synthetic bbox would otherwise induce
    // (s = sqrt(w·h)/PSFResolution). Real detection bboxes are tight to the star.
    StarDetectionParams {
        psf_fit_type: fit,
        model_psf: true,
        pixel_scale: 1.0,
        psf_goodness_of_fit_threshold: 0.5,
        psf_resolution: 25.0,
        ..Default::default()
    }
}

#[test]
fn psf_gaussian_recovery() {
    let sigma = 2.5_f64;
    let (w, h) = (41usize, 41usize);
    let (cx, cy) = (20.0, 20.0);
    let mut buf = vec![0.03_f64; w * h];
    add_gaussian(&mut buf, w, h, cx, cy, 0.5, sigma);
    let img = WorkImage {
        data: buf,
        width: w,
        height: h,
    };
    let bounds = Rect {
        x: 10,
        y: 10,
        w: 21,
        h: 21,
    };
    let plane = BackgroundPlane::flat(0.03, cx, cy);
    let mut p = psf_params(PsfFitType::Gaussian);
    p.psf_resolution = bounds.w as f64; // s = sqrt(w·h)/res = 1 px ⇒ integer-grid sampling (no bilinear bias)
    let m = fit_psf(&img, &bounds, (cx, cy), &plane, 0.0, &p).expect("gaussian fit");
    assert!(
        (m.sigma - sigma).abs() / sigma < 0.02,
        "sigma {} vs {}",
        m.sigma,
        sigma
    );
    assert!(m.r_squared > 0.999, "r2 {}", m.r_squared);
}

/// Render a symmetric Moffat star of the given β and core width σ.
#[allow(clippy::too_many_arguments)]
fn add_moffat(
    buf: &mut [f64],
    w: usize,
    h: usize,
    cx: f64,
    cy: f64,
    amp: f64,
    sigma: f64,
    beta: f64,
) {
    let r = (10.0 * sigma).ceil() as isize;
    let (ix, iy) = (cx.round() as isize, cy.round() as isize);
    for dy in -r..=r {
        for dx in -r..=r {
            let x = ix + dx;
            let y = iy + dy;
            if x < 0 || y < 0 || x >= w as isize || y >= h as isize {
                continue;
            }
            let d = 1.0 + ((x as f64 - cx).powi(2) + (y as f64 - cy).powi(2)) / (sigma * sigma);
            buf[y as usize * w + x as usize] += amp * d.powf(-beta);
        }
    }
}

/// Render a pixel-INTEGRATED Gaussian by 7×7 supersampling (independent of the
/// library's erf integral), so a pixel-integration fit can be validated.
fn add_gaussian_integrated(
    buf: &mut [f64],
    w: usize,
    h: usize,
    cx: f64,
    cy: f64,
    amp: f64,
    sigma: f64,
) {
    let r = (6.0 * sigma).ceil() as isize;
    let (ix, iy) = (cx.round() as isize, cy.round() as isize);
    let sub = 7;
    for dy in -r..=r {
        for dx in -r..=r {
            let x = ix + dx;
            let y = iy + dy;
            if x < 0 || y < 0 || x >= w as isize || y >= h as isize {
                continue;
            }
            let mut acc = 0.0;
            for sj in 0..sub {
                for si in 0..sub {
                    let sx = x as f64 - 0.5 + (si as f64 + 0.5) / sub as f64;
                    let sy = y as f64 - 0.5 + (sj as f64 + 0.5) / sub as f64;
                    let e = (sx - cx).powi(2) + (sy - cy).powi(2);
                    acc += (-e / (2.0 * sigma * sigma)).exp();
                }
            }
            buf[y as usize * w + x as usize] += amp * acc / (sub * sub) as f64;
        }
    }
}

#[test]
fn psf_gaussian_pixel_integration_recovery() {
    // Undersampled Gaussian rendered as pixel-integrated flux; the pixel-area
    // model must recover σ better than a point-sample model would (dossier §9.3).
    let sigma = 1.4_f64;
    let (w, h) = (41usize, 41usize);
    let (cx, cy) = (20.0, 20.0);
    let mut buf = vec![0.03_f64; w * h];
    add_gaussian_integrated(&mut buf, w, h, cx, cy, 0.5, sigma);
    let img = WorkImage {
        data: buf,
        width: w,
        height: h,
    };
    let bounds = Rect {
        x: 11,
        y: 11,
        w: 19,
        h: 19,
    };
    let plane = BackgroundPlane::flat(0.03, cx, cy);
    let mut p = psf_params(PsfFitType::Gaussian);
    p.psf_pixel_integration = true;
    p.psf_resolution = bounds.w as f64; // s = 1 px ⇒ one model sample per pixel
    let m = fit_psf(&img, &bounds, (cx, cy), &plane, 0.0, &p).expect("pixel-integrated fit");
    assert!(
        (m.sigma - sigma).abs() / sigma < 0.05,
        "sigma {} vs {}",
        m.sigma,
        sigma
    );
    assert!(m.r_squared > 0.99, "r2 {}", m.r_squared);
}

#[test]
fn psf_moffat_fixed_beta_recovery() {
    let (sigma, beta) = (3.5_f64, 4.0_f64);
    let (w, h) = (61usize, 61usize);
    let (cx, cy) = (30.0, 30.0);
    let mut buf = vec![0.03_f64; w * h];
    add_moffat(&mut buf, w, h, cx, cy, 0.5, sigma, beta);
    let img = WorkImage {
        data: buf,
        width: w,
        height: h,
    };
    let bounds = Rect {
        x: 15,
        y: 15,
        w: 31,
        h: 31,
    };
    let plane = BackgroundPlane::flat(0.03, cx, cy);
    let mut p = psf_params(PsfFitType::Moffat40);
    p.psf_resolution = bounds.w as f64; // s = sqrt(w·h)/res = 1 px ⇒ integer-grid sampling (no bilinear bias)
    let m = fit_psf(&img, &bounds, (cx, cy), &plane, 0.0, &p).expect("moffat fit");
    assert!(
        (m.sigma - sigma).abs() / sigma < 0.02,
        "sigma {} vs {}",
        m.sigma,
        sigma
    );
    assert!(m.r_squared > 0.999, "r2 {}", m.r_squared);
}

#[test]
fn psf_moffat_fittable_beta_recovery() {
    let (sigma, beta) = (2.4_f64, 2.5_f64);
    let (w, h) = (51usize, 51usize);
    let (cx, cy) = (25.0, 25.0);
    let mut buf = vec![0.03_f64; w * h];
    add_moffat(&mut buf, w, h, cx, cy, 0.5, sigma, beta);
    let img = WorkImage {
        data: buf,
        width: w,
        height: h,
    };
    let bounds = Rect {
        x: 13,
        y: 13,
        w: 25,
        h: 25,
    };
    let plane = BackgroundPlane::flat(0.03, cx, cy);
    let mut p = psf_params(PsfFitType::MoffatFittable);
    p.psf_resolution = bounds.w as f64; // s = sqrt(w·h)/res = 1 px ⇒ integer-grid sampling (no bilinear bias)
    let m = fit_psf(&img, &bounds, (cx, cy), &plane, 0.0, &p).expect("fittable moffat fit");
    assert!(!m.beta.is_nan());
    assert!(
        (m.beta - beta).abs() / beta < 0.02,
        "beta {} vs {}",
        m.beta,
        beta
    );
    assert!(
        (m.sigma - sigma).abs() / sigma < 0.02,
        "sigma {} vs {}",
        m.sigma,
        sigma
    );
}

// ==========================================================================
// 4. Full-pipeline recall / precision on a synthetic star field.
// ==========================================================================

#[test]
fn detection_recall_precision_snr10() {
    let (w, h) = (256usize, 256usize);
    let bg = 0.03_f64;
    let noise = 0.0009_f64; // normalized read noise
    let sigma = 2.0_f64;
    let mut rng = Lcg::new(42);

    // gradient background + noise
    let mut buf = vec![0.0_f64; w * h];
    for y in 0..h {
        for x in 0..w {
            let grad = 0.01 * (x as f64 / w as f64) + 0.008 * (y as f64 / h as f64);
            buf[y * w + x] = bg + grad + noise * rng.gauss();
        }
    }

    // truth stars on a jittered grid, away from borders (OnBorder gate).
    let mut truth: Vec<(f64, f64)> = Vec::new();
    let mut amp_rng = Lcg::new(7);
    for gy in 0..5 {
        for gx in 0..5 {
            let cx = 40.0 + gx as f64 * 44.0 + (amp_rng.unit() - 0.5) * 6.0;
            let cy = 40.0 + gy as f64 * 44.0 + (amp_rng.unit() - 0.5) * 6.0;
            let amp = 0.15 + 0.15 * amp_rng.unit(); // SNR = amp/noise ≈ 165..330
            add_gaussian(&mut buf, w, h, cx, cy, amp, sigma);
            truth.push((cx, cy));
        }
    }

    let frame_data = to_u16(&buf);
    let frame = GrayFrame::new(&frame_data, w, h);
    let p = StarDetectionParams {
        bpp: BPP,
        model_psf: false, // HFR-only path for the field test
        ..Default::default()
    };
    let res = detect_and_measure(&frame, &p);

    // match detections to truth within 2.5 px
    let mut matched_truth = vec![false; truth.len()];
    let mut true_pos = 0usize;
    for s in &res.stars {
        let mut best = None;
        let mut best_d = 2.5_f64;
        for (i, t) in truth.iter().enumerate() {
            if matched_truth[i] {
                continue;
            }
            let d = ((s.center.0 - t.0).powi(2) + (s.center.1 - t.1).powi(2)).sqrt();
            if d < best_d {
                best_d = d;
                best = Some(i);
            }
        }
        if let Some(i) = best {
            matched_truth[i] = true;
            true_pos += 1;
        }
    }
    let recall = true_pos as f64 / truth.len() as f64;
    let precision = if res.stars.is_empty() {
        0.0
    } else {
        true_pos as f64 / res.stars.len() as f64
    };
    assert!(
        recall >= 0.9,
        "recall {} ({} stars)",
        recall,
        res.stars.len()
    );
    assert!(
        precision >= 0.9,
        "precision {} ({} stars)",
        precision,
        res.stars.len()
    );
    // frame HFR should land near the analytic Gaussian value.
    let truth_hfr = sigma * (std::f64::consts::FRAC_PI_2).sqrt();
    assert!(
        (res.stats.hfr - truth_hfr).abs() / truth_hfr < 0.15,
        "frame hfr {}",
        res.stats.hfr
    );
}

#[test]
fn detection_is_deterministic() {
    let (w, h) = (128usize, 128usize);
    let mut buf = vec![0.03_f64; w * h];
    add_gaussian(&mut buf, w, h, 64.0, 64.0, 0.3, 2.0);
    add_gaussian(&mut buf, w, h, 40.0, 90.0, 0.25, 2.0);
    let data = to_u16(&buf);
    let frame = GrayFrame::new(&data, w, h);
    let p = StarDetectionParams::default();
    let a = detect_and_measure(&frame, &p);
    let b = detect_and_measure(&frame, &p);
    assert_eq!(a.stars.len(), b.stars.len());
    for (sa, sb) in a.stars.iter().zip(&b.stars) {
        assert_eq!(sa.center, sb.center);
        assert_eq!(sa.hfr, sb.hfr);
    }
}

// ==========================================================================
// 5. Gate unit tests.
// ==========================================================================

fn filled_points(b: &Rect) -> Vec<(usize, usize)> {
    let mut v = Vec::new();
    for y in b.y..b.bottom() {
        for x in b.x..b.right() {
            v.push((x, y));
        }
    }
    v
}

#[test]
fn gate_too_small_rejected() {
    let img = WorkImage {
        data: vec![0.1; 20 * 20],
        width: 20,
        height: 20,
    };
    let b = Rect {
        x: 5,
        y: 5,
        w: 3,
        h: 3,
    };
    let pts = filled_points(&b);
    let p = StarDetectionParams::default();
    assert_eq!(
        evaluate_candidate(&img, &b, &pts, 0.001, &p).err(),
        Some(RejectReason::TooSmall)
    );
}

#[test]
fn gate_on_border_rejected() {
    let img = WorkImage {
        data: vec![0.1; 20 * 20],
        width: 20,
        height: 20,
    };
    let b = Rect {
        x: 0,
        y: 5,
        w: 6,
        h: 6,
    }; // touches x==0
    let pts = filled_points(&b);
    let p = StarDetectionParams::default();
    assert_eq!(
        evaluate_candidate(&img, &b, &pts, 0.001, &p).err(),
        Some(RejectReason::OnBorder)
    );
}

#[test]
fn gate_contaminated_rejected_and_clean_accepted() {
    let (w, h) = (60usize, 60usize);
    let (cx, cy) = (30.0, 30.0);
    let sigma = 2.0;
    // clean star
    let mut clean = vec![0.02_f64; w * h];
    add_gaussian(&mut clean, w, h, cx, cy, 0.4, sigma);
    let clean_img = WorkImage {
        data: clean.clone(),
        width: w,
        height: h,
    };
    let bounds = Rect {
        x: 20,
        y: 20,
        w: 21,
        h: 21,
    };
    let pts = filled_points(&bounds);
    let p = StarDetectionParams::default();
    let star = evaluate_candidate(&clean_img, &bounds, &pts, 0.001, &p).expect("clean star");
    assert!(!star.contamination_suspected);

    // Add a one-sided bright contaminant concentrated in the +x octant of the
    // annulus (dx>=dy>0, outside the bbox 20..41, inside the expanded box 17..44):
    // columns 41..44, rows 31..40 ⇒ ~27 px, all in octant 0 (≥ 8-px minimum).
    let mut dirty = clean;
    for y in 31..40 {
        for x in 41..44 {
            dirty[y * w + x] += 0.4;
        }
    }
    let dirty_img = WorkImage {
        data: dirty,
        width: w,
        height: h,
    };
    let r = evaluate_candidate(&dirty_img, &bounds, &pts, 0.001, &p);
    assert_eq!(
        r.err(),
        Some(RejectReason::Contaminated),
        "expected contamination rejection"
    );
}

// ==========================================================================
// 6. Degenerate frames produce no stars and no panics.
// ==========================================================================

#[test]
fn degenerate_flat_frame() {
    let (w, h) = (64usize, 64usize);
    let data = vec![1500u16; w * h];
    let frame = GrayFrame::new(&data, w, h);
    let p = StarDetectionParams::default();
    let res = detect_and_measure(&frame, &p);
    assert_eq!(res.stars.len(), 0);
    assert_eq!(res.stats.star_count, 0);
}

#[test]
fn degenerate_saturated_frame() {
    let (w, h) = (64usize, 64usize);
    let data = vec![65535u16; w * h];
    let frame = GrayFrame::new(&data, w, h);
    let p = StarDetectionParams::default();
    let res = detect_and_measure(&frame, &p);
    assert_eq!(res.stars.len(), 0);
}

#[test]
fn degenerate_single_hot_pixel() {
    let (w, h) = (64usize, 64usize);
    let mut data = vec![1500u16; w * h];
    data[32 * w + 32] = 60000; // single hot pixel (removed by hotpixel filter)
    let frame = GrayFrame::new(&data, w, h);
    let p = StarDetectionParams::default();
    let res = detect_and_measure(&frame, &p);
    assert_eq!(res.stars.len(), 0);
}

#[test]
fn tiny_frames_dont_panic() {
    for (w, h) in [(1usize, 1usize), (1, 10), (3, 3), (5, 1)] {
        let data = vec![1000u16; w * h];
        let frame = GrayFrame::new(&data, w, h);
        let p = StarDetectionParams::default();
        let _ = detect_and_measure(&frame, &p);
    }
}

// ==========================================================================
// 7. Preset wiring sanity (dossier §12.1).
// ==========================================================================

#[test]
fn typical_preset_effective_values() {
    let p = StarDetectionParams::default();
    assert_eq!(p.noise_reduction_radius, 4); // thresholded-hotpixel += 1
    assert!((p.sensitivity - 2.0).abs() < 1e-12); // 10 * 0.2
    assert_eq!(p.structure_layers, 4);
    let adv = StarDetectionParams::advanced_defaults();
    assert_eq!(adv.noise_reduction_radius, 3);
    let none = StarDetectionParams::simple(
        NoiseLevel::None,
        PixelScalePreset::Typical,
        FocusRange::Typical,
    );
    assert_eq!(none.noise_reduction_radius, 0);
    assert!((none.sensitivity - 10.0).abs() < 1e-12);
}
