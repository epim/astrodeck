// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (§1.1, §5.4
// bilinear sampling, §5.2 background plane). No code copied from NINA/Hocus Focus.

//! Image model: a borrowed `&[u16]` grayscale frame, its normalized `f64`
//! working buffer, bilinear sampling, and the local-background plane type.
//!
//! Pixel coordinates: `x` → column, `y` → row; index = `y * width + x`
//! (dossier §Conventions).

/// An axis-aligned pixel rectangle. `x,y` is the top-left inclusive pixel;
/// `w,h` are extents in pixels.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    /// left column (inclusive).
    pub x: usize,
    /// top row (inclusive).
    pub y: usize,
    /// width in pixels.
    pub w: usize,
    /// height in pixels.
    pub h: usize,
}

impl Rect {
    /// Exclusive right edge (`x + w`).
    #[inline]
    pub fn right(&self) -> usize {
        self.x + self.w
    }
    /// Exclusive bottom edge (`y + h`).
    #[inline]
    pub fn bottom(&self) -> usize {
        self.y + self.h
    }
    /// Bounding-box center x, `x + w/2` (dossier §5.1 anchor).
    #[inline]
    pub fn cx(&self) -> f64 {
        self.x as f64 + self.w as f64 / 2.0
    }
    /// Bounding-box center y, `y + h/2` (dossier §5.1 anchor).
    #[inline]
    pub fn cy(&self) -> f64 {
        self.y as f64 + self.h as f64 / 2.0
    }
}

/// A borrowed single-channel `u16` frame (units: raw ADU).
#[derive(Clone, Copy)]
pub struct GrayFrame<'a> {
    /// row-major pixels, length `width * height`.
    pub data: &'a [u16],
    /// frame width in pixels.
    pub width: usize,
    /// frame height in pixels.
    pub height: usize,
}

impl<'a> GrayFrame<'a> {
    /// Borrow a frame, asserting the buffer length matches `width * height`.
    pub fn new(data: &'a [u16], width: usize, height: usize) -> Self {
        assert_eq!(data.len(), width * height, "frame buffer length mismatch");
        Self {
            data,
            width,
            height,
        }
    }

    /// Normalize to a working buffer via `value = adu / (1 << bpp)` (dossier
    /// §1.1 — note the divisor is `2^bpp`, **not** `2^bpp − 1`).
    pub fn to_working(&self, bpp: u32) -> WorkImage {
        let scale = (1u64 << bpp) as f64;
        let data = self.data.iter().map(|&v| v as f64 / scale).collect();
        WorkImage {
            data,
            width: self.width,
            height: self.height,
        }
    }
}

/// A mutable normalized working image (`f64`, values in `[0, 1)`).
#[derive(Clone, Debug)]
pub struct WorkImage {
    /// row-major pixels, length `width * height`.
    pub data: Vec<f64>,
    /// width in pixels.
    pub width: usize,
    /// height in pixels.
    pub height: usize,
}

impl WorkImage {
    /// Allocate a zeroed image of the given size.
    pub fn zeros(width: usize, height: usize) -> Self {
        Self {
            data: vec![0.0; width * height],
            width,
            height,
        }
    }

    /// Value at integer pixel `(x, y)` (no bounds check beyond slice indexing).
    #[inline]
    pub fn at(&self, x: usize, y: usize) -> f64 {
        self.data[y * self.width + x]
    }

    /// Mutable value at integer pixel `(x, y)`.
    #[inline]
    pub fn at_mut(&mut self, x: usize, y: usize) -> &mut f64 {
        &mut self.data[y * self.width + x]
    }

    /// Bilinear sample at continuous `(x, y)` (dossier §5.4): floor to
    /// `(x0, y0)`, clamp `x1 = min(w-1, x0+1)`, `y1 = min(h-1, y0+1)`, standard
    /// 4-tap bilinear. Negative coordinates are clamped to 0.
    pub fn bilinear(&self, x: f64, y: f64) -> f64 {
        let xf = if x < 0.0 { 0.0 } else { x };
        let yf = if y < 0.0 { 0.0 } else { y };
        let x0 = xf.floor() as usize;
        let y0 = yf.floor() as usize;
        let x0 = x0.min(self.width - 1);
        let y0 = y0.min(self.height - 1);
        let x1 = (x0 + 1).min(self.width - 1);
        let y1 = (y0 + 1).min(self.height - 1);
        let fx = xf - x0 as f64;
        let fy = yf - y0 as f64;
        let v00 = self.at(x0, y0);
        let v10 = self.at(x1, y0);
        let v01 = self.at(x0, y1);
        let v11 = self.at(x1, y1);
        let top = v00 * (1.0 - fx) + v10 * fx;
        let bot = v01 * (1.0 - fx) + v11 * fx;
        top * (1.0 - fy) + bot * fy
    }
}

/// A local-background model anchored at `(cx, cy)` (dossier §5.2). A tilted
/// plane `B(x,y) = b0 + b1·(x−cx) + b2·(y−cy)`; a flat background sets `b1=b2=0`.
#[derive(Clone, Copy, Debug)]
pub struct BackgroundPlane {
    /// value at the anchor.
    pub b0: f64,
    /// x gradient (per pixel).
    pub b1: f64,
    /// y gradient (per pixel).
    pub b2: f64,
    /// anchor x.
    pub cx: f64,
    /// anchor y.
    pub cy: f64,
}

impl BackgroundPlane {
    /// A flat plane of constant `value` anchored at `(cx, cy)`.
    pub fn flat(value: f64, cx: f64, cy: f64) -> Self {
        Self {
            b0: value,
            b1: 0.0,
            b2: 0.0,
            cx,
            cy,
        }
    }

    /// Evaluate the plane at continuous `(x, y)`.
    #[inline]
    pub fn value_at(&self, x: f64, y: f64) -> f64 {
        self.b0 + self.b1 * (x - self.cx) + self.b2 * (y - self.cy)
    }
}
