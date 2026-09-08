// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossiers in
// docs/native-parity/algorithms/ (nina-autofocus.md,
// hocusfocus-autofocus-tilt.md). No code copied from NINA/Hocus Focus.

//! `astro-focus`: autofocus sweep state machine and focus-curve fitting.
//!
//! Pure algorithm crate (no async, no I/O, no globals). Encodes NINA's sweep
//! policy (initial offset, step size, backlash strategy, extend-sweep,
//! outlier re-measure, max points) as a state machine that proposes moves and
//! consumes measurements; device I/O and scheduling live in the host. Focuser
//! positions are in encoder steps; HFR values are in pixels unless a
//! function's doc comment states otherwise.
//!
pub mod backlash;
pub mod config;
pub mod fit;
pub mod gaussian;
pub mod hyperbolic;
mod linalg;
pub mod point;
pub mod quadratic;
pub mod sweep;
pub mod trendline;

pub use backlash::{Backlash, Direction};
pub use config::{AfMethod, BacklashModel, CurveFitting, FocusConfig};
pub use fit::{FitOutcome, FocusFits};
pub use gaussian::GaussianFit;
pub use hyperbolic::HyperbolicFit;
pub use point::{average_measurements, insert_sorted, FocusPoint, MeasureAndError};
pub use quadratic::QuadraticFit;
pub use sweep::{FailReason, FocusSweep, PendingKind, Step};
pub use trendline::{Trendline, TrendlineFit};
