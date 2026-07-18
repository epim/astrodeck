// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§8.1-§8.4). Derived
// from PHD2 `calstep_dialog.cpp:206-241` (calibration distance/step-size
// calculator), `scope.cpp:1202-1784` (`Scope::UpdateCalibrationState`, the
// GO_WEST/GO_EAST/CLEAR_BACKLASH/GO_NORTH/GO_SOUTH/NUDGE_SOUTH/COMPLETE state
// machine), `scope.cpp:44-69` and `scope.h:120-127` (calibration constants),
// `scope.cpp:868-985` (`Scope::SanityCheckCalibration`), and
// `mount.cpp:1544-1593` (`Mount::SetCalibration`, the `y_angle_error` stamp)
// (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). This module ports the state
// machine literally per this task's brief ("LARGE PORT — port exception"),
// with the deliberate, documented deviations noted inline (this crate is
// synchronous and I/O-free: no wall clock, no mount-pointing readback, no
// ST-4/cable-detection path, no fast-recenter budget — dossier §17,
// P0-T3's guide-rates ruling extended). No code copied from PHD2.

//! Calibration state machine (dossier §8): drives a mount through the
//! GO_WEST → GO_EAST → CLEAR_BACKLASH → GO_NORTH → GO_SOUTH → NUDGE_SOUTH
//! sequence, measuring each axis's angle and rate in the camera frame, and
//! produces a [`transforms::Cal`](crate::transforms::Cal) the guide engine
//! can hand to [`camera_to_mount`]/[`mount_to_camera`](crate::transforms::mount_to_camera).
//!
//! [`Calibrator`] holds all state and performs no I/O: the host calls
//! [`Calibrator::step`] once per frame with the star's current camera-frame
//! position and applies whatever [`CalOutcome::Pulse`] it returns before
//! calling `step` again with the resulting position. This mirrors upstream's
//! `Scope::UpdateCalibrationState(currentLocation)`, called once per guide
//! frame by the host's camera loop — including its C++ `switch`
//! fall-through behavior, where completing one leg immediately evaluates
//! the next leg's first frame in the *same* call (scope.cpp's repeated
//! "// fall through" comments at each leg boundary). This port reproduces
//! that with an internal loop in [`Calibrator::step`] that only returns once
//! it has a real [`CalOutcome`] to report.
//!
//! **Angle sign convention** (dossier §8.2, `scope.cpp:1268` vs `:1559`):
//! upstream computes the RA leg's `x_angle` as the angle pointing *from the
//! ending position back to the start* (`m_calibrationStartingLocation.Angle
//! (currentLocation)`), but the Dec leg's `y_angle` as the angle pointing
//! *from the start to the ending position* (`currentLocation.Angle
//! (m_calibrationStartingLocation)`) — upstream's own comment at
//! `scope.cpp:1540-1542` explains why: "this calculation is reversed from
//! the ra calculation, because that one was calibrating WEST, but the angle
//! is really relative to EAST". This is not an inconsistency to resolve; it
//! is ported literally via the `angle(from, to)` helper below, called with
//! swapped argument order for the two legs to match the two upstream call
//! sites exactly.
//!
//! **Sanity-check advisory #4** ("different from last calibration", dossier
//! §8.3 item 4) needs a previous calibration to compare against;
//! [`sanity_advisories`]'s frozen signature (`cal`, `ra_steps`, `dec_steps`
//! only) has no channel for one, so only items 1-3 (min-steps, orthogonality,
//! rate ratio) are implemented — matching this task's brief, which itself
//! only lists those three.
//!
//! **South-retrace advisory** (dossier §8.2 GO_SOUTH: "south_dist < 0.25 *
//! north_dist ... log advisory"): upstream's own alert for this is dead code
//! ("alert text exists but is currently not shown — code path commented
//! out", per the dossier). [`CalOutcome`] has no side channel for a
//! per-step advisory string, so this port does not surface it; only the
//! post-calibration [`sanity_advisories`] checks are exposed, matching the
//! brief's Produces list.

use std::f64::consts::PI;

use crate::transforms::{camera_to_mount, norm_angle, Cal, Parity, PierSide};
use crate::types::{CalLeg, Direction};

/// `UNKNOWN_DECLINATION` (dossier §8.4; PHD2's sentinel for "no pointing
/// source declination available"). Stamped into [`Cal::declination`] on
/// completion, since [`Calibrator`] has no mount-pointing-source input
/// (I/O-free crate contract; dossier §17 / P0-T3's guide-rates ruling
/// extended to RA/Dec coordinate readback).
pub const UNKNOWN_DECLINATION: f64 = 997.0;

/// `CALIBRATION_RATE_UNCALIBRATED` (dossier §8.2 CLEAR_BACKLASH /
/// `Scope::IsCalibrated`): the `y_rate` sentinel meaning "Dec was never
/// calibrated" (`dec_guide_mode == DecMode::Off`).
pub const CALIBRATION_RATE_UNCALIBRATED: f64 = 1.0;

/// `MAX_CALIBRATION_STEPS` (`scope.cpp:56`), the default per-leg pulse
/// budget for [`CalConfig::max_steps`].
const DEFAULT_MAX_STEPS: u32 = 60;

/// `DefaultCalibrationDuration` (`scope.cpp:44`).
const DEFAULT_CALIBRATION_DURATION_MS: u32 = 750;

/// `DEFAULT_DISTANCE` (`calstep_dialog.cpp`/`calstep_dialog.h`): the
/// calibration-distance floor in pixels, used both as
/// [`CalConfig::default`]'s bare distance and as the floor in
/// [`default_calibration_distance`].
const DEFAULT_DISTANCE_PX: f64 = 25.0;

/// `CAL_ALERT_MINSTEPS` (`scope.cpp:57`).
const CAL_ALERT_MINSTEPS: u32 = 4;

/// `CAL_ALERT_ORTHOGONALITY_TOLERANCE` degrees (`scope.cpp:58`).
const CAL_ALERT_ORTHOGONALITY_TOLERANCE_DEG: f64 = 12.5;

/// `CAL_ALERT_AXISRATES_TOLERANCE` (`scope.cpp:60`).
const CAL_ALERT_AXISRATES_TOLERANCE: f64 = 0.20;

/// `Scope::DEC_COMP_LIMIT` (`scope.cpp:68`: `M_PI / 2.0 * 2.0 / 3.0`), i.e.
/// 60 degrees in radians.
const DEC_COMP_LIMIT: f64 = PI / 3.0;

/// `BL_BACKLASH_MIN_COUNT` (`scope.h:124`): consecutive accepted north
/// clearing moves needed to conclude backlash is cleared.
const BL_BACKLASH_MIN_COUNT: u32 = 3;

/// `BL_MAX_CLEARING_TIME` (`scope.h:125`), milliseconds.
const BL_MAX_CLEARING_TIME_MS: u32 = 60_000;

/// `BL_MIN_CLEARING_DISTANCE` (`scope.h:126`) pixels: below this cumulative
/// clearing distance, exhausting the clearing budget is a hard failure
/// rather than a "proceed anyway".
const BL_MIN_CLEARING_DISTANCE: f64 = 3.0;

/// `MAX_NUDGES` (`scope.cpp:64`). Upstream's literal check is
/// `m_calibrationSteps <= MAX_NUDGES` against a *post*-increment counter,
/// which (like `MAX_CALIBRATION_STEPS`'s own post-increment check) actually
/// permits one extra nudge beyond the documented bound. This port uses the
/// clean bound instead (`steps < MAX_NUDGES`, capping at exactly 3 nudges) —
/// same adjudication as [`Calibrator::step`]'s `GoWest`/`GoNorth` step
/// limit, see that doc comment.
const MAX_NUDGES: u32 = 3;

/// `NUDGE_TOLERANCE` (`scope.cpp:65`) pixels.
const NUDGE_TOLERANCE: f64 = 2.0;

/// Dec-axis calibration/guiding mode (dossier §8.2's `DEC_GUIDE_MODE`).
/// Only [`DecMode::Off`] affects the calibration state machine (it skips
/// `CLEAR_BACKLASH`/`GO_NORTH`/`GO_SOUTH`/`NUDGE_SOUTH` entirely,
/// `scope.cpp:1369-1379`); `Auto`/`North`/`South` calibrate identically —
/// they only gate which direction a *guide* correction may pulse, a
/// later engine concern, not a calibration concern.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecMode {
    Off,
    Auto,
    North,
    South,
}

/// Calibration parameters (dossier §8.1/§15).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CalConfig {
    /// Target distance (px) each of the GO_WEST/GO_NORTH legs must move the
    /// star before that axis is considered measured (dossier §8.1;
    /// `GetCalibrationDistance`). Default 25px (the bare `DEFAULT_DISTANCE`
    /// floor); a caller with a known image scale should compute
    /// [`default_calibration_distance`] instead.
    pub calibration_distance: f64,
    /// Pulse duration (ms) for every calibration step (dossier §8.1;
    /// `DefaultCalibrationDuration` = 750).
    pub calibration_duration_ms: u32,
    /// Per-leg pulse budget before GO_WEST/GO_NORTH fail (dossier §8.2;
    /// `MAX_CALIBRATION_STEPS` = 60).
    pub max_steps: u32,
    /// Dec calibration/guide mode (dossier §8.2; default `Auto`).
    pub dec_guide_mode: DecMode,
    /// Force the Dec axis to the nearest of `x_angle ± π/2` instead of the
    /// measured angle (dossier §8.2 GO_NORTH; default `false`).
    pub assume_orthogonal: bool,
}

impl Default for CalConfig {
    fn default() -> Self {
        CalConfig {
            calibration_distance: DEFAULT_DISTANCE_PX,
            calibration_duration_ms: DEFAULT_CALIBRATION_DURATION_MS,
            max_steps: DEFAULT_MAX_STEPS,
            dec_guide_mode: DecMode::Auto,
            assume_orthogonal: false,
        }
    }
}

/// The recommended calibration distance (px) for a known image scale
/// (dossier §8.1; `CalstepDialog::GetCalibrationDistance`):
/// `max(25, ceil(20.0 / image_scale_arcsec_per_px))`. [`CalConfig::default`]
/// cannot compute this (it has no image scale), so it uses the bare 25px
/// floor; a caller that knows the image scale should call this and set
/// [`CalConfig::calibration_distance`] explicitly.
pub fn default_calibration_distance(image_scale_arcsec_per_px: f64) -> f64 {
    const NOMINAL_DISTANCE_ARCSEC: f64 = 20.0;
    (NOMINAL_DISTANCE_ARCSEC / image_scale_arcsec_per_px)
        .ceil()
        .max(DEFAULT_DISTANCE_PX)
}

/// One [`Calibrator::step`] result (dossier §8.2, one state-machine step per
/// frame).
#[derive(Debug, Clone, PartialEq)]
pub enum CalOutcome {
    /// Issue this pulse, re-measure the star, then call `step` again with
    /// the new position.
    Pulse {
        leg: CalLeg,
        dir: Direction,
        ms: u32,
    },
    /// Calibration finished successfully.
    Done(Cal),
    /// Calibration failed; `step` will keep returning this same outcome.
    Failed(String),
}

/// Calibration state machine (dossier §8.2; `Scope::UpdateCalibrationState`,
/// `scope.cpp:1202-1784`). See the module doc comment for the fall-through
/// and angle-sign-convention notes.
#[derive(Debug, Clone)]
pub struct Calibrator {
    cfg: CalConfig,
    state: CalLeg,

    /// `m_calibrationInitialLocation`: fixed for the calibration's lifetime.
    initial_location: (f64, f64),
    /// `m_calibrationStartingLocation`: reset at each leg boundary that
    /// needs one (GO_WEST's start, then reused as GO_NORTH's start once
    /// CLEAR_BACKLASH hands off).
    leg_start: (f64, f64),
    /// `m_calibrationSteps`, reused per leg exactly as upstream reuses it
    /// (RA pulse count, then east-recenter countdown, then backlash-pulse
    /// count, then dec pulse count, then south-recenter countdown, then
    /// nudge count).
    steps: u32,

    x_angle: f64,
    x_rate: f64,
    ra_steps: u32,

    /// GO_EAST/GO_SOUTH recenter countdown, in whole
    /// `calibration_duration_ms` pulses. Upstream's general recenter formula
    /// picks a larger `recenter_duration` when fast-recenter is enabled
    /// (dossier §8.2 GO_EAST); [`CalConfig`] has no fast-recenter knobs
    /// (`max_move_px`/`max_ra_duration`/`max_dec_duration`), so this port's
    /// recenter is always the fast-recenter-disabled case: exactly as many
    /// `calibration_duration_ms` pulses as the outbound leg took.
    recenter_pulses_left: u32,

    bl_marker: (f64, f64),
    bl_expected_step: f64,
    bl_max_pulses: u32,
    bl_last_cum: f64,
    bl_accepted: u32,

    y_angle: f64,
    y_rate: f64,
    dec_steps: u32,

    /// `m_southStartingLocation`: the position where GO_SOUTH begins
    /// (GO_NORTH's ending position).
    south_start: (f64, f64),
    /// `m_northDirCosX`/`Y`: direction cosines of the initial→south_start
    /// vector, used by NUDGE_SOUTH's direction check.
    north_dir_cos: (f64, f64),
    /// `m_totalSouthAmt`: mount-frame Y sign reference for NUDGE_SOUTH.
    total_south_amt: f64,

    done: Option<Cal>,
    failed: Option<String>,
}

impl Calibrator {
    /// `start` is the star's camera-frame position at the moment
    /// calibration begins; the first `step(start)` call (with the *same*
    /// position, no motion yet) issues the first GO_WEST pulse — mirroring
    /// upstream's first `UpdateCalibrationState` call, where
    /// `currentLocation == m_calibrationStartingLocation` (dist 0).
    pub fn new(cfg: CalConfig, start: (f64, f64)) -> Self {
        Calibrator {
            cfg,
            state: CalLeg::GoWest,
            initial_location: start,
            leg_start: start,
            steps: 0,
            x_angle: 0.0,
            x_rate: 0.0,
            ra_steps: 0,
            recenter_pulses_left: 0,
            bl_marker: (0.0, 0.0),
            bl_expected_step: 0.0,
            bl_max_pulses: 0,
            bl_last_cum: 0.0,
            bl_accepted: 0,
            y_angle: 0.0,
            y_rate: 0.0,
            dec_steps: 0,
            south_start: (0.0, 0.0),
            north_dir_cos: (0.0, 0.0),
            total_south_amt: 0.0,
            done: None,
            failed: None,
        }
    }

    /// Final RA step count (`m_raSteps`); 0 until GO_WEST completes. Feeds
    /// [`sanity_advisories`].
    pub fn ra_steps(&self) -> u32 {
        self.ra_steps
    }

    /// Final Dec step count (`m_decSteps`); 0 until GO_NORTH completes, and
    /// stays 0 forever when `dec_guide_mode == DecMode::Off` (matching
    /// upstream's "Dec guiding might be disabled" comment,
    /// `scope.cpp:883`). Feeds [`sanity_advisories`].
    pub fn dec_steps(&self) -> u32 {
        self.dec_steps
    }

    /// One state-machine step (dossier §8.2). `current` is the star's
    /// camera-frame position this frame. See the module doc comment for the
    /// fall-through semantics: a single call may advance through several
    /// leg boundaries before it has a pulse (or a final result) to report.
    pub fn step(&mut self, current: (f64, f64)) -> CalOutcome {
        if let Some(cal) = self.done {
            return CalOutcome::Done(cal);
        }
        if let Some(msg) = &self.failed {
            return CalOutcome::Failed(msg.clone());
        }

        loop {
            match self.state {
                CalLeg::GoWest => {
                    let dist = distance(self.leg_start, current);
                    if dist < self.cfg.calibration_distance {
                        self.steps += 1;
                        if self.steps > self.cfg.max_steps {
                            return self.fail("RA Calibration Failed: star did not move enough");
                        }
                        return CalOutcome::Pulse {
                            leg: CalLeg::GoWest,
                            dir: Direction::West,
                            ms: self.cfg.calibration_duration_ms,
                        };
                    }

                    // West calibration complete (scope.cpp:1268-1269). See
                    // the module doc comment for the `angle(current,
                    // leg_start)` argument order.
                    self.x_angle = angle(current, self.leg_start);
                    self.x_rate = dist / self.rate_denom(self.steps);
                    self.ra_steps = self.steps;

                    self.recenter_pulses_left = self.ra_steps;
                    self.state = CalLeg::GoEast;
                    // fall through (scope.cpp:1313-1319)
                }

                CalLeg::GoEast => {
                    if self.recenter_pulses_left > 0 {
                        self.recenter_pulses_left -= 1;
                        return CalOutcome::Pulse {
                            leg: CalLeg::GoEast,
                            dir: Direction::East,
                            ms: self.cfg.calibration_duration_ms,
                        };
                    }

                    // Recenter complete. The ST-4/no-pulse-guide cable-check
                    // advisory (scope.cpp:1344-1362) is skipped: this crate
                    // targets pulse-guide-only output (dossier D2), so
                    // `CanPulseGuide()` is always true and that branch never
                    // fires upstream either.
                    self.steps = 0;
                    self.leg_start = current;

                    if self.cfg.dec_guide_mode == DecMode::Off {
                        // scope.cpp:1369-1378. Upstream `break`s here and
                        // stamps the completed calibration on the *next*
                        // frame's call; this port completes immediately
                        // (see the module doc comment: `CalOutcome` has no
                        // "not done, no pulse" variant to represent that
                        // extra no-op frame, and collapsing it changes no
                        // measured value).
                        self.y_angle = norm_angle(self.x_angle + PI / 2.0);
                        self.y_rate = CALIBRATION_RATE_UNCALIBRATED;
                        return self.complete();
                    }

                    self.bl_marker = current;
                    self.bl_expected_step =
                        self.x_rate * self.cfg.calibration_duration_ms as f64 * 0.6;
                    // No RA/Dec guide-rate scaling of `bl_expected_step`
                    // (scope.cpp:1386-1391): this crate has no
                    // pointing-source guide-rate query (dossier §17 /
                    // P0-T3's "calibration must work without them" ruling).
                    self.bl_max_pulses =
                        (BL_MAX_CLEARING_TIME_MS / self.cfg.calibration_duration_ms.max(1)).max(8);
                    self.bl_last_cum = 0.0;
                    self.bl_accepted = 0;
                    self.state = CalLeg::ClearBacklash;
                    // fall through (scope.cpp:1398-1399)
                }

                CalLeg::ClearBacklash => {
                    if self.steps == 0 {
                        // "Get things moving with the first clearing pulse"
                        // (scope.cpp:1413-1419): unconditional, no
                        // evaluation yet.
                        self.steps = 1;
                        return CalOutcome::Pulse {
                            leg: CalLeg::ClearBacklash,
                            dir: Direction::North,
                            ms: self.cfg.calibration_duration_ms,
                        };
                    }

                    let bl_delta = distance(self.bl_marker, current);
                    let bl_cum = distance(self.leg_start, current);

                    if bl_delta >= self.bl_expected_step {
                        if self.bl_accepted == 0 || bl_cum > self.bl_last_cum {
                            self.bl_accepted += 1;
                        } else {
                            self.bl_accepted = 0; // direction reversal
                        }
                    } else if bl_cum < self.bl_last_cum {
                        self.bl_accepted = 0; // small direction reversal
                    }

                    if self.bl_accepted < BL_BACKLASH_MIN_COUNT {
                        if self.steps < self.bl_max_pulses && bl_cum < self.cfg.calibration_distance
                        {
                            self.steps += 1;
                            self.bl_marker = current;
                            self.bl_last_cum = bl_cum;
                            return CalOutcome::Pulse {
                                leg: CalLeg::ClearBacklash,
                                dir: Direction::North,
                                ms: self.cfg.calibration_duration_ms,
                            };
                        }
                        if bl_cum >= BL_MIN_CLEARING_DISTANCE {
                            // Exhausted the clearing budget but moved > 3px:
                            // proceed anyway (scope.cpp:1465-1476), fresh
                            // GO_NORTH start.
                            self.steps = 0;
                            self.leg_start = current;
                        } else {
                            return self.fail("Backlash Clearing Failed: star did not move enough");
                        }
                    } else {
                        // 3 consecutive accepted moves: the last one becomes
                        // north calibration step 1 (scope.cpp:1488-1503).
                        self.leg_start = self.bl_marker;
                        self.steps = 1;
                    }

                    self.state = CalLeg::GoNorth;
                    // fall through (scope.cpp:1512-1515)
                }

                CalLeg::GoNorth => {
                    let dist = distance(self.leg_start, current);
                    if dist < self.cfg.calibration_distance {
                        self.steps += 1;
                        if self.steps > self.cfg.max_steps {
                            return self.fail("DEC Calibration Failed: star did not move enough");
                        }
                        return CalOutcome::Pulse {
                            leg: CalLeg::GoNorth,
                            dir: Direction::North,
                            ms: self.cfg.calibration_duration_ms,
                        };
                    }

                    if self.cfg.assume_orthogonal {
                        // scope.cpp:1543-1556.
                        let a1 = norm_angle(self.x_angle + PI / 2.0);
                        let a2 = norm_angle(self.x_angle - PI / 2.0);
                        let measured = angle(self.leg_start, current);
                        self.y_angle =
                            if norm_angle(a1 - measured).abs() < norm_angle(a2 - measured).abs() {
                                a1
                            } else {
                                a2
                            };
                        let dec_dist = dist * (measured - self.y_angle).cos();
                        self.y_rate = dec_dist / self.rate_denom(self.steps);
                    } else {
                        self.y_angle = angle(self.leg_start, current);
                        self.y_rate = dist / self.rate_denom(self.steps);
                    }
                    self.dec_steps = self.steps;

                    self.recenter_pulses_left = self.dec_steps;
                    self.south_start = current;
                    self.state = CalLeg::GoSouth;
                    // fall through (scope.cpp:1605-1610)
                }

                CalLeg::GoSouth => {
                    if self.recenter_pulses_left > 0 {
                        self.recenter_pulses_left -= 1;
                        return CalOutcome::Pulse {
                            leg: CalLeg::GoSouth,
                            dir: Direction::South,
                            ms: self.cfg.calibration_duration_ms,
                        };
                    }

                    // Recenter complete. The south-retrace advisory is dead
                    // code upstream and not surfaced here (see the module
                    // doc comment).
                    let init_to_south = distance(self.initial_location, self.south_start);
                    self.north_dir_cos = if init_to_south > 0.0 {
                        (
                            (self.initial_location.0 - self.south_start.0) / init_to_south,
                            (self.initial_location.1 - self.south_start.1) / init_to_south,
                        )
                    } else {
                        (0.0, 0.0)
                    };
                    let south_vec = (
                        self.south_start.0 - current.0,
                        self.south_start.1 - current.1,
                    );
                    self.total_south_amt = self.to_mount(south_vec).1;

                    self.steps = 0;
                    self.state = CalLeg::NudgeSouth;
                    // fall through (scope.cpp:1689-1692)
                }

                CalLeg::NudgeSouth => {
                    let nudge_amt = distance(current, self.initial_location);
                    if nudge_amt > 0.0 {
                        let nudge_dir = (
                            (current.0 - self.initial_location.0) / nudge_amt,
                            (current.1 - self.initial_location.1) / nudge_amt,
                        );
                        let cos_theta = (nudge_dir.0 * self.north_dir_cos.0
                            + nudge_dir.1 * self.north_dir_cos.1)
                            .clamp(-1.0, 1.0);
                        let theta_deg = cos_theta.acos().to_degrees();

                        // Roughly opposite the north-move vector (within
                        // 40 deg of 180 deg), scope.cpp:1710.
                        if (theta_deg - 180.0).abs() < 40.0 {
                            let bl_distance_moved = distance(self.bl_marker, self.initial_location);
                            if self.steps < MAX_NUDGES
                                && nudge_amt > NUDGE_TOLERANCE
                                && nudge_amt < self.cfg.calibration_distance + bl_distance_moved
                            {
                                let offset = (
                                    current.0 - self.initial_location.0,
                                    current.1 - self.initial_location.1,
                                );
                                let dec_amt_signed = self.to_mount(offset).1;
                                if dec_amt_signed * self.total_south_amt > 0.0 {
                                    let dec_amt = dec_amt_signed.abs();
                                    let mut pulse_ms = (dec_amt / self.y_rate).floor() as u32;
                                    if pulse_ms > self.cfg.calibration_duration_ms {
                                        pulse_ms = self.cfg.calibration_duration_ms;
                                    }
                                    self.steps += 1;
                                    return CalOutcome::Pulse {
                                        leg: CalLeg::NudgeSouth,
                                        dir: Direction::South,
                                        ms: pulse_ms,
                                    };
                                }
                            }
                        }
                    }

                    return self.complete();
                }
            }
        }
    }

    /// Rate denominator (`steps * calibration_duration_ms`), guarding the
    /// (upstream-impossible, but not API-impossible: a caller could pass an
    /// out-of-band first `current`) `steps == 0` case against
    /// division-by-zero.
    fn rate_denom(&self, steps: u32) -> f64 {
        steps.max(1) as f64 * self.cfg.calibration_duration_ms as f64
    }

    /// The local `MountCoords(cameraVector, xAngle, yAngle)` helper
    /// (`scope.cpp:1163-1171`) is formula-identical to
    /// [`camera_to_mount`] fed a [`Cal`] whose only relevant fields are
    /// `x_angle`/`y_angle_error` (also `Mount::TransformCameraCoordinatesToMountCoordinates`,
    /// `mount.cpp:1135-1179` — the corroborating citation `transforms.rs`
    /// already carries). This reuses that already-committed, already-tested
    /// transform instead of re-deriving the formula.
    fn to_mount(&self, cam: (f64, f64)) -> (f64, f64) {
        let cal = Cal {
            x_rate: 0.0,
            y_rate: 0.0,
            x_angle: self.x_angle,
            y_angle: self.y_angle,
            y_angle_error: Cal::y_angle_error_from(self.x_angle, self.y_angle),
            declination: 0.0,
            pier_side: PierSide::Unknown,
            ra_parity: Parity::Unknown,
            dec_parity: Parity::Unknown,
            rotator_angle: 0.0,
            binning: 1,
            is_valid: false,
        };
        camera_to_mount(cam, &cal)
    }

    fn fail(&mut self, msg: &str) -> CalOutcome {
        let msg = msg.to_string();
        self.failed = Some(msg.clone());
        CalOutcome::Failed(msg)
    }

    /// Stamp and store the completed calibration (dossier §8.2 COMPLETE;
    /// scope.cpp:1752-1760, `Mount::SetCalibration`'s `y_angle_error` stamp
    /// at `mount.cpp:1570`). `declination`/`pier_side`/`rotator_angle`/
    /// `binning` have no input channel on this I/O-free crate's `new`/`step`
    /// (no pointing source, no camera-binning readback) — they are stamped
    /// with neutral/unknown defaults; a host with that information patches
    /// the returned [`Cal`] before use.
    fn complete(&mut self) -> CalOutcome {
        let cal = Cal {
            x_rate: self.x_rate,
            y_rate: self.y_rate,
            x_angle: self.x_angle,
            y_angle: self.y_angle,
            y_angle_error: Cal::y_angle_error_from(self.x_angle, self.y_angle),
            declination: UNKNOWN_DECLINATION,
            pier_side: PierSide::Unknown,
            ra_parity: Parity::Unknown,
            dec_parity: Parity::Unknown,
            rotator_angle: 0.0,
            binning: 1,
            is_valid: true,
        };
        self.done = Some(cal);
        CalOutcome::Done(cal)
    }
}

fn distance(a: (f64, f64), b: (f64, f64)) -> f64 {
    (a.0 - b.0).hypot(a.1 - b.1)
}

/// Angle (radians) of the vector pointing from `from` to `to`
/// (`PHD_Point::Angle`, `point.h:96-110`: `to.Angle(from)` in upstream's
/// method-call notation equals `angle(from, to)` here — see the module doc
/// comment for why GO_WEST and GO_NORTH call this with swapped argument
/// order).
fn angle(from: (f64, f64), to: (f64, f64)) -> f64 {
    (to.1 - from.1).atan2(to.0 - from.0)
}

/// Post-calibration sanity-check advisories (dossier §8.3;
/// `Scope::SanityCheckCalibration`, `scope.cpp:868-985`). First failure
/// wins (upstream shows only one alert at a time); this port mirrors that
/// by returning at most one string. Only checks 1-3 (min-steps,
/// orthogonality, rate ratio) are implemented — see the module doc comment
/// for why check 4 ("different from last calibration") is out of scope for
/// this signature. Dec-compensation-enabled (item 3's precondition) is
/// assumed `true`, upstream's own default (`/scope/UseDecComp`, dossier
/// §9) — this crate has no such setting to query.
pub fn sanity_advisories(cal: &Cal, ra_steps: u32, dec_steps: u32) -> Vec<String> {
    // 1. Too few steps (scope.cpp:883-887).
    if ra_steps < CAL_ALERT_MINSTEPS || (dec_steps > 0 && dec_steps < CAL_ALERT_MINSTEPS) {
        return vec![
            "Advisory: Calibration completed but few guide steps were used, so accuracy is questionable"
                .to_string(),
        ];
    }

    // 2. Non-orthogonal RA/Dec axes (scope.cpp:890-897).
    let non_ortho_deg = (norm_angle(cal.x_angle - cal.y_angle).abs() - PI / 2.0)
        .abs()
        .to_degrees();
    if non_ortho_deg > CAL_ALERT_ORTHOGONALITY_TOLERANCE_DEG {
        return vec![
            "Advisory: Calibration completed but RA/Dec axis angles are questionable and guiding may be impaired"
                .to_string(),
        ];
    }

    // 3. RA/Dec rate ratio vs cos(dec) (scope.cpp:900-918).
    if cal.declination != UNKNOWN_DECLINATION
        && cal.y_rate != CALIBRATION_RATE_UNCALIBRATED
        && cal.declination.abs() <= DEC_COMP_LIMIT
    {
        let expected_ratio = cal.declination.cos();
        let actual_ratio = cal.x_rate / cal.y_rate;
        if (expected_ratio - actual_ratio).abs() > CAL_ALERT_AXISRATES_TOLERANCE {
            return vec![
                "Advisory: Calibration completed but RA and Dec rates vary by an unexpected amount (often caused by large Dec backlash)"
                    .to_string(),
            ];
        }
    }

    Vec::new()
}
