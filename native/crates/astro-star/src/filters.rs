// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (§1.4, §1.5,
// §3.1, §11). No code copied from NINA/Hocus Focus.

//! Separable convolution (Gaussian + custom kernels), the à-trous B3-spline
//! wavelet residual, and the mono hot-pixel filter.
//!
//! All convolutions use OpenCV `BORDER_REFLECT` semantics
//! (`fedcba|abcdefgh|hgfedcb`): the edge pixel **is** mirrored (dossier §11).

use crate::image::WorkImage;

/// Reflect an index into `[0, n)` using `BORDER_REFLECT` (edge duplicated).
#[inline]
pub fn reflect(mut i: isize, n: isize) -> usize {
    debug_assert!(n > 0);
    loop {
        if i < 0 {
            i = -i - 1;
        } else if i >= n {
            i = 2 * n - i - 1;
        } else {
            return i as usize;
        }
    }
}

/// OpenCV `getGaussianKernel(n, sigma)`: `k[i] = exp(-(i-(n-1)/2)² / (2σ²))`,
/// normalized to sum 1 (dossier §1.5).
pub fn gaussian_kernel(n: usize, sigma: f64) -> Vec<f64> {
    let center = (n as f64 - 1.0) / 2.0;
    let two_s2 = 2.0 * sigma * sigma;
    let mut k: Vec<f64> = (0..n)
        .map(|i| {
            let d = i as f64 - center;
            (-(d * d) / two_s2).exp()
        })
        .collect();
    let sum: f64 = k.iter().sum();
    for v in &mut k {
        *v /= sum;
    }
    k
}

/// Separable 2-D convolution with (possibly different) x/y kernels, reflect
/// border. Kernels are applied x then y.
pub fn sep_filter_2d(src: &WorkImage, kx: &[f64], ky: &[f64]) -> WorkImage {
    let w = src.width;
    let h = src.height;
    let rx = (kx.len() / 2) as isize;
    let ry = (ky.len() / 2) as isize;
    // horizontal pass
    let mut tmp = vec![0.0f64; w * h];
    for y in 0..h {
        let row = y * w;
        for x in 0..w {
            let mut acc = 0.0;
            for (t, &kv) in kx.iter().enumerate() {
                let sx = reflect(x as isize + t as isize - rx, w as isize);
                acc += kv * src.data[row + sx];
            }
            tmp[row + x] = acc;
        }
    }
    // vertical pass
    let mut dst = vec![0.0f64; w * h];
    for y in 0..h {
        for x in 0..w {
            let mut acc = 0.0;
            for (t, &kv) in ky.iter().enumerate() {
                let sy = reflect(y as isize + t as isize - ry, h as isize);
                acc += kv * tmp[sy * w + x];
            }
            dst[y * w + x] = acc;
        }
    }
    WorkImage {
        data: dst,
        width: w,
        height: h,
    }
}

/// In-place separable Gaussian blur, kernel size `k` (odd, ≥ 3). Default
/// sigma `0.159758·k` (dossier §1.5).
pub fn gaussian_blur(img: &mut WorkImage, k: usize) {
    debug_assert!(k >= 3 && k % 2 == 1);
    let sigma = 0.159758 * k as f64;
    let kernel = gaussian_kernel(k, sigma);
    let out = sep_filter_2d(img, &kernel, &kernel);
    img.data = out.data;
}

/// The à-trous B3-spline scaling kernel for dyadic layer `i` (dossier §3.1).
pub fn atrous_b3_kernel(i: usize) -> Vec<f64> {
    let size = (1usize << (i + 2)) + 1;
    let mut k = vec![0.0f64; size];
    k[0] = 0.0625;
    k[size - 1] = 0.0625;
    let step = 1usize << i;
    k[step] = 0.25;
    k[size - step - 1] = 0.25;
    k[size >> 1] = 0.375;
    k
}

/// Successive-smoothing à-trous residual: convolve `src` through `num_layers`
/// dyadic B3-spline layers; the coarsest smooth is the residual (dossier §3.1).
pub fn residual_atrous_b3(src: &WorkImage, num_layers: usize) -> WorkImage {
    let mut prev = src.clone();
    for i in 0..num_layers {
        let k = atrous_b3_kernel(i);
        prev = sep_filter_2d(&prev, &k, &k);
    }
    prev
}

/// OpenCV `median3(a,b,c)` network (dossier §1.2).
#[inline]
pub fn median3(a: f64, b: f64, c: f64) -> f64 {
    (a.min(b)).max(c.min(a.max(b)))
}

/// 3×3 median blur, `BORDER_REFLECT` (dossier §1.4, §11). Returns a new buffer.
pub fn median_blur_3x3(img: &WorkImage) -> WorkImage {
    let w = img.width;
    let h = img.height;
    let mut out = vec![0.0f64; w * h];
    let mut window = [0.0f64; 9];
    for y in 0..h {
        for x in 0..w {
            let mut n = 0;
            for dy in -1isize..=1 {
                let sy = reflect(y as isize + dy, h as isize);
                for dx in -1isize..=1 {
                    let sx = reflect(x as isize + dx, w as isize);
                    window[n] = img.data[sy * w + sx];
                    n += 1;
                }
            }
            // full sort of 9 is fine and simplest to reason about
            window.sort_by(|a, b| a.partial_cmp(b).unwrap());
            out[y * w + x] = window[4];
        }
    }
    WorkImage {
        data: out,
        width: w,
        height: h,
    }
}

// TODO(deferred): CFA (bayered) hot-pixel path with the 3/5-way same-color
// diagonal median networks (dossier §1.2) — mono cameras first.

/// Result of the mono hot-pixel filter (dossier §1.4).
pub struct HotpixelResult {
    /// number of pixels replaced (0 for the plain, non-thresholded filter).
    pub count: usize,
}

/// Apply the mono hot-pixel filter in place (dossier §1.4).
///
/// - `thresholding == false`: plain 3×3 median blur, count 0.
/// - `thresholding == true`: replace pixels whose `|img − median| > threshold`
///   (strictly greater) with the median; count = replaced pixels.
pub fn hotpixel_filter(img: &mut WorkImage, thresholding: bool, threshold: f64) -> HotpixelResult {
    let blurred = median_blur_3x3(img);
    if !thresholding {
        img.data = blurred.data;
        return HotpixelResult { count: 0 };
    }
    let mut count = 0;
    for i in 0..img.data.len() {
        let diff = (img.data[i] - blurred.data[i]).abs();
        if diff > threshold {
            img.data[i] = blurred.data[i];
            count += 1;
        }
    }
    HotpixelResult { count }
}
