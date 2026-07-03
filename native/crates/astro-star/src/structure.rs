// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (§3). No code
// copied from NINA/Hocus Focus.

//! Structure map: à-trous residual subtraction, post-blur, 65536-bucket linear
//! histogram median, and scalar + adaptive binarization (dossier §3).

use crate::filters::{gaussian_blur, residual_atrous_b3};
use crate::image::WorkImage;

/// Number of buckets in the linear histogram (dossier §3.3).
pub const HIST_BUCKETS: usize = 65536;

/// Adaptive-binarization σ floor (dossier §3.6, `AdaptiveBinarizationSigmaFloor`).
pub const ADAPTIVE_SIGMA_FLOOR: f64 = 1e-6;

/// Build the structure map: subtract the à-trous residual (removing structures
/// larger than ~`2^layers` px), clamp to `[0,1]`, then post-blur with kernel
/// `2·base_layers + 1` (dossier §3.1, §3.2).
///
/// `src` is the noise-reduced structure-map source; `effective_layers` is the
/// (possibly boosted) wavelet layer count; `base_layers` keys the post-blur.
pub fn build_structure_map(
    src: &WorkImage,
    effective_layers: usize,
    base_layers: usize,
) -> WorkImage {
    let residual = residual_atrous_b3(src, effective_layers);
    let mut map = src.clone();
    for i in 0..map.data.len() {
        map.data[i] = (map.data[i] - residual.data[i]).clamp(0.0, 1.0);
    }
    let k = base_layers * 2 + 1;
    if k >= 3 {
        gaussian_blur(&mut map, k);
    }
    map
}

/// 65536-bucket **linear** histogram median with within-bucket interpolation
/// (dossier §3.3). `lower_bound[i] = i / 65536`.
pub fn histogram_median(img: &WorkImage) -> f64 {
    let mut hist = vec![0u64; HIST_BUCKETS];
    for &v in &img.data {
        let b = ((v * HIST_BUCKETS as f64).floor() as i64).clamp(0, (HIST_BUCKETS - 1) as i64);
        hist[b as usize] += 1;
    }
    let target = img.data.len() as f64 / 2.0;
    let mut cum = 0.0f64;
    for i in 0..HIST_BUCKETS {
        cum += hist[i] as f64;
        if cum >= target {
            let lb = i as f64 / HIST_BUCKETS as f64;
            let next = if i < HIST_BUCKETS - 1 {
                (i + 1) as f64 / HIST_BUCKETS as f64
            } else {
                1.0
            };
            let ratio = if hist[i] == 0 {
                0.0
            } else {
                (cum - target) / hist[i] as f64
            };
            return lb + (next - lb) * ratio;
        }
    }
    1.0
}

/// Scalar binarization threshold `median + k·σ` (dossier §3.4).
pub fn scalar_threshold(structure_median: f64, k: f64, structure_sigma: f64) -> f64 {
    structure_median + k * structure_sigma
}

/// Binarize with a single scalar threshold: `dst = src > t ? 1 : 0` (strictly
/// greater; dossier §3.6).
pub fn binarize_scalar(img: &WorkImage, threshold: f64) -> WorkImage {
    let data = img
        .data
        .iter()
        .map(|&v| if v > threshold { 1.0 } else { 0.0 })
        .collect();
    WorkImage {
        data,
        width: img.width,
        height: img.height,
    }
}

/// A coarse block grid of per-block statistics (dossier §3.6
/// `ComputeLocalBackgroundGrid`).
pub struct BlockGrid {
    /// grid columns = `ceil(w / block)`.
    pub cols: usize,
    /// grid rows = `ceil(h / block)`.
    pub rows: usize,
    /// one value per block, row-major.
    pub values: Vec<f64>,
}

/// Compute a per-block **upper-median** grid over `img` (dossier §3.6).
pub fn block_median_grid(img: &WorkImage, block: usize) -> BlockGrid {
    let cols = img.width.div_ceil(block);
    let rows = img.height.div_ceil(block);
    let mut values = vec![0.0f64; cols * rows];
    for gy in 0..rows {
        for gx in 0..cols {
            let x0 = gx * block;
            let y0 = gy * block;
            let x1 = (x0 + block).min(img.width);
            let y1 = (y0 + block).min(img.height);
            let mut buf: Vec<f64> = Vec::with_capacity((x1 - x0) * (y1 - y0));
            for y in y0..y1 {
                for x in x0..x1 {
                    buf.push(img.at(x, y));
                }
            }
            buf.sort_by(|a, b| a.partial_cmp(b).unwrap());
            values[gy * cols + gx] = buf[buf.len() >> 1];
        }
    }
    BlockGrid { cols, rows, values }
}

/// Compute a per-block σ grid `max(1.4826·upper_median(|v − block_median|),
/// floor)` over `img` (dossier §3.6).
pub fn block_sigma_grid(img: &WorkImage, block: usize, floor: f64) -> BlockGrid {
    let cols = img.width.div_ceil(block);
    let rows = img.height.div_ceil(block);
    let mut values = vec![0.0f64; cols * rows];
    for gy in 0..rows {
        for gx in 0..cols {
            let x0 = gx * block;
            let y0 = gy * block;
            let x1 = (x0 + block).min(img.width);
            let y1 = (y0 + block).min(img.height);
            let mut buf: Vec<f64> = Vec::with_capacity((x1 - x0) * (y1 - y0));
            for y in y0..y1 {
                for x in x0..x1 {
                    buf.push(img.at(x, y));
                }
            }
            buf.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let med = buf[buf.len() >> 1];
            let mut dev: Vec<f64> = buf.iter().map(|&v| (v - med).abs()).collect();
            dev.sort_by(|a, b| a.partial_cmp(b).unwrap());
            let mad = dev[dev.len() >> 1];
            values[gy * cols + gx] = (1.4826 * mad).max(floor);
        }
    }
    BlockGrid { cols, rows, values }
}

/// Bilinear upsample a `cols×rows` grid to `w×h` using OpenCV `Resize(Linear)`
/// coordinate mapping `src = (dst + 0.5)·scale − 0.5` (dossier §3.6, §11).
pub fn resize_bilinear(grid: &BlockGrid, w: usize, h: usize) -> Vec<f64> {
    let mut out = vec![0.0f64; w * h];
    let sx = grid.cols as f64 / w as f64;
    let sy = grid.rows as f64 / h as f64;
    for y in 0..h {
        let mut fy = (y as f64 + 0.5) * sy - 0.5;
        if fy < 0.0 {
            fy = 0.0;
        }
        let gy0 = (fy.floor() as usize).min(grid.rows - 1);
        let gy1 = (gy0 + 1).min(grid.rows - 1);
        let wy = fy - gy0 as f64;
        for x in 0..w {
            let mut fx = (x as f64 + 0.5) * sx - 0.5;
            if fx < 0.0 {
                fx = 0.0;
            }
            let gx0 = (fx.floor() as usize).min(grid.cols - 1);
            let gx1 = (gx0 + 1).min(grid.cols - 1);
            let wx = fx - gx0 as f64;
            let v00 = grid.values[gy0 * grid.cols + gx0];
            let v10 = grid.values[gy0 * grid.cols + gx1];
            let v01 = grid.values[gy1 * grid.cols + gx0];
            let v11 = grid.values[gy1 * grid.cols + gx1];
            let top = v00 * (1.0 - wx) + v10 * wx;
            let bot = v01 * (1.0 - wx) + v11 * wx;
            out[y * w + x] = top * (1.0 - wy) + bot * wy;
        }
    }
    out
}

/// Adaptive binarization (dossier §3.6): build `T = median_grid + k·σ_grid` at
/// grid resolution, bilinearly upsample, then `dst = (src − T) > 0 ? 1 : 0`.
///
/// `median_src` is the structure map (after wavelet+blur); `sigma_src` is the
/// noise-reduced structure-map source (σ-consistency). Grids must share
/// geometry or this panics (dossier §13.22).
pub fn binarize_adaptive(
    structure_map: &WorkImage,
    median_src: &WorkImage,
    sigma_src: &WorkImage,
    block: usize,
    k: f64,
) -> WorkImage {
    let med = block_median_grid(median_src, block);
    let sig = block_sigma_grid(sigma_src, block, ADAPTIVE_SIGMA_FLOOR);
    assert_eq!(
        (med.cols, med.rows),
        (sig.cols, sig.rows),
        "adaptive grid mismatch"
    );
    let t_grid = BlockGrid {
        cols: med.cols,
        rows: med.rows,
        values: med
            .values
            .iter()
            .zip(&sig.values)
            .map(|(&m, &s)| m + k * s)
            .collect(),
    };
    let surface = resize_bilinear(&t_grid, structure_map.width, structure_map.height);
    let data = structure_map
        .data
        .iter()
        .zip(&surface)
        .map(|(&v, &t)| if v - t > 0.0 { 1.0 } else { 0.0 })
        .collect();
    WorkImage {
        data,
        width: structure_map.width,
        height: structure_map.height,
    }
}
