// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossiers in
// docs/native-parity/algorithms/ (nina-star-detection-hfr.md,
// hocusfocus-star-detection-psf.md). No code copied from NINA/Hocus Focus.

//! `astro-star`: star detection, HFR measurement, and PSF fitting.
//!
//! Pure algorithm crate (no async, no I/O, no globals). Implements NINA-parity
//! star detection with Hocus Focus's noise model as the default profile, HFR
//! measurement, and per-star PSF fitting. All coordinates are in pixels
//! (origin top-left, x right, y down) unless a function's doc comment states
//! otherwise.
//!
//! The pipeline is split into an **early stage** (candidate formation — depends
//! only on the "early" params) and a **late stage** (per-candidate gates +
//! measurement), matching the Hocus Focus two-stage design. [`detect_and_measure`]
//! is the entry point.
//!
//! Deferred (see the module TODOs): CFA/OSC debayer paths, donut/defocus
//! recovery, and ROI cropping — mono cameras first.

// Explicit index loops read more clearly than iterator adapters in the dense
// linear-algebra kernels (normal equations, Gauss elimination, Jacobians); the
// `mut p = default(); p.field = …` preset builders are intentional overrides.
#![allow(clippy::needless_range_loop, clippy::field_reassign_with_default)]

pub mod aggregate;
pub mod detect;
pub mod filters;
pub mod floodfill;
pub mod image;
pub mod lm;
pub mod measure;
pub mod noise;
pub mod params;
pub mod psf;
pub mod stats;
pub mod structure;

pub use aggregate::{af_star_score, select_af_stars, FrameStats};
pub use detect::{detect_and_measure, DetectionMetrics, DetectionResult};
pub use image::{BackgroundPlane, GrayFrame, Rect, WorkImage};
pub use measure::{RejectReason, Star};
pub use params::{
    FocusRange, HfrTauPolicy, MeasurementAverage, NoiseLevel, PixelScalePreset, PsfFitType,
    StarDetectionParams, StarSensitivityLevel,
};
pub use psf::PsfModel;
