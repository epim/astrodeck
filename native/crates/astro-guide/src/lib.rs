// SPDX-License-Identifier: Apache-2.0
//
// Provenance: crate root scaffold (module wiring, crate-level doc) for the
// PHD2-guiding-derived astro-guide crate. No code copied from PHD2.

//! `astro-guide`: guide-loop state machine and guide algorithms.
//!
//! Pure algorithm crate (no async, no I/O, no globals, no GUI-toolkit
//! dependencies, no external processes, no camera-loop machinery). The host
//! (AstroDeck's Python backend) owns all I/O — camera exposure, mount pulse
//! issuance, and the wall clock; this crate only computes decisions from
//! data the host feeds it. Star positions are in binned pixels, pulse
//! durations are in milliseconds, and axis rates are in pixels per
//! millisecond unless a function's doc comment states otherwise.

pub mod types;
