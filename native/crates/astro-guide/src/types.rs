// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§1, §3, §5, §7,
// §8.2). Derived from PHD2 `mount.h:46-56` (directions), `mount.cpp:978-1087`
// (move pipeline / axis dispatch), `scope.cpp:1202-1784` (calibration state
// machine legs), `star.h:103-117` (GuideStar fields) (BSD-3-Clause; see
// THIRD-PARTY-NOTICES.md).
// No code copied from PHD2.

//! Shared types the guide engine and its algorithms exchange with the host.
//!
//! Positions are in binned pixels, pulses in milliseconds, unless a field's
//! doc comment states otherwise.

/// Mount axis. `Ra` == PHD2 x (RA), `Dec` == PHD2 y (Dec). Dossier §units.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Axis {
    Ra,
    Dec,
}

/// Guide-pulse direction. UP==NORTH(Dec+), DOWN==SOUTH(Dec-),
/// RIGHT==EAST(RA-), LEFT==WEST(RA+). Dossier §units.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    North,
    South,
    East,
    West,
}

/// One axis's issued pulse.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AxisPulse {
    pub dir: Direction,
    pub ms: u32,
}

/// Which calibration leg a `CalStep` belongs to (dossier §8.2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CalLeg {
    GoWest,
    GoEast,
    ClearBacklash,
    GoNorth,
    GoSouth,
    NudgeSouth,
}

/// The engine's per-frame decision. The host performs exactly one of these.
#[derive(Debug, Clone, PartialEq)]
pub enum Action {
    /// No correction (deadband/vetoed both axes, or paused).
    Idle,
    /// Single-axis pulse (calibration re-center / one-axis guide).
    Pulse { axis: Axis, dir: Direction, ms: u32 },
    /// A guide frame's corrections: up to one pulse per axis (dossier §7).
    PulsePair {
        ra: Option<AxisPulse>,
        dec: Option<AxisPulse>,
    },
    /// A calibration state-machine step (dossier §8.2).
    CalStep { leg: CalLeg, dir: Direction, ms: u32 },
    /// Within a start/dither settle window; the host waits (dossier §12).
    Settle,
    /// Guide star lost and recovery exhausted this frame (dossier §3.3).
    LockLost,
}

/// Per-frame metadata the engine needs (clock injected — dossier §17 deviation).
#[derive(Debug, Clone, Copy)]
pub struct FrameMeta {
    pub timestamp_s: f64,
    pub exposure_s: f64,
}

/// A star measured this frame (positions in binned pixels; dossier §1).
#[derive(Debug, Clone, Copy)]
pub struct MeasuredStar {
    pub x: f64,
    pub y: f64,
    pub snr: f64,
    pub mass: f64,
    pub hfd: f64,
    pub found: bool,
}
