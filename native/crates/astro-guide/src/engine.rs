// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§3, §5, §6, §7, §9,
// §10.1, §12, §13). This is the composition layer: it wires the already-
// committed, already-tested starfind / select / track / transforms /
// algorithms / calibration primitives into one per-frame decision. Derived
// from PHD2 `guider_multistar.cpp:925-1060`
// (`GuiderMultiStar::UpdateCurrentPosition`: the star-find -> mass-check ->
// distance-check -> offset per-frame pipeline), `mount.cpp:978-1087`
// (`Mount::MoveOffset`: algorithm dispatch, direction/rate -> ms, BLC hook),
// `scope.cpp:640-816` (`Scope::MoveAxis`: dec-mode gating + duration clamps),
// `mount.cpp:1253-1409` (`Mount::AdjustCalibrationForScopePointing`: dec
// compensation + pier flip), `backlash_comp.cpp:371-583` (`BacklashComp::
// ApplyBacklashComp`: the static direction-reversal pulse, §10.1),
// `scope.cpp:1752-1760` (COMPLETE-state calibration stamping), and
// `guider.cpp:1261-1553` (the guide-loop dispatch that sequences these)
// (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied from PHD2.

//! Pure guide engine (dossier §7 composition): the per-frame decision that
//! turns a measured star into an [`Action`] the host executes.
//!
//! [`GuideEngine`] owns the composed guiding state (calibration, lock
//! position, the two per-axis algorithms, the mass / distance / average-error
//! trackers, and any active settle window) and performs **no I/O**: the host
//! calls [`GuideEngine::measure`] to turn a frame into [`MeasuredStar`]s, then
//! [`GuideEngine::ingest`] to get the frame's [`Action`]. The engine never
//! touches a camera, a mount, or a clock — timestamps arrive in
//! [`FrameMeta`].
//!
//! The five hard obligations from prior task reviews are discharged at the
//! sites tagged `OBLIGATION (a)..(e)` in [`GuideEngine::ingest`],
//! [`GuideEngine::begin_guiding`], and the calibration-complete path; see each
//! tag for the upstream citation.

use std::collections::VecDeque;
use std::f64::consts::PI;

use crate::algorithms::{GuideAlgorithm, Hysteresis, ResistSwitch};
use crate::calibration::{
    sanity_advisories, CalConfig, CalOutcome, Calibrator, DecMode, UNKNOWN_DECLINATION,
};
use crate::select::{auto_find, saturation_threshold, select_primary, SelectParams};
use crate::settle::{Settle, SettleState};
use crate::starfind::{star_find, was_found, FindParams};
use crate::track::{AvgDist, DistanceChecker, MassChecker};
use crate::transforms::{camera_to_mount, norm_angle, Cal, Parity, PierSide};
use crate::types::{Action, AxisPulse, Direction, FrameMeta, MeasuredStar};

/// `MassChecker` base time window (dossier §3.1 `DefaultTimeWindowMs`).
const MASS_WINDOW_MS: f64 = 22500.0;
/// Star-mass change threshold (dossier §3.1/§15
/// `/guider/onestar/MassChangeThreshold`).
const MASS_CHANGE_THRESHOLD: f64 = 0.5;
/// Seconds a star may be missing before the engine gives up the lock. Reuses
/// dossier §13's `THRESHOLD_SECONDS` (`guider.cpp:1113`): once no star has
/// been found for this long, `current_error` already reports `LARGE_DISTANCE`
/// and upstream treats guiding as non-functional.
const LOST_STAR_TIMEOUT_S: f64 = 20.0;
/// Guide error reported for a lost-star frame while settling (dossier §13
/// `LARGE_DISTANCE`; `guider.cpp:1114`).
const LARGE_DISTANCE: f64 = 100.0;
/// `DEC_COMP_LIMIT` (dossier §9; `scope.cpp:68`, `M_PI/3`, i.e. 60°): beyond
/// this calibration declination, RA dec-compensation is disabled.
const DEC_COMP_LIMIT: f64 = PI / 3.0;
/// Current declination is clamped to ±89° before dec-compensation (dossier
/// §9 item 6) to keep `cos(dec)` well away from zero.
const DEC_COMP_MAX_DEC: f64 = 89.0 * PI / 180.0;
/// Rolling window of recent accepted frames kept for [`GuideStatsSnapshot`].
const RECENT_CAP: usize = 100;
/// Default dither/settle window used by the P1 [`GuideEngine::dither`] stub
/// (dossier §12). Full dither params thread through in P2.
const DEFAULT_SETTLE_TOL_PX: f64 = 1.5;
const DEFAULT_SETTLE_TIME_S: f64 = 10.0;
const DEFAULT_SETTLE_TIMEOUT_S: f64 = 60.0;
/// `DefaultMaxRaDuration` / `DefaultMaxDecDuration` (dossier §7/§15), ms.
const DEFAULT_MAX_DURATION_MS: u32 = 2500;

/// Which per-axis guide algorithm an axis runs (dossier §6). P1 constructs
/// only [`AlgoKind::Hysteresis`] and [`AlgoKind::ResistSwitch`]; the enum is
/// complete so later tasks slot the remaining algorithms in without changing
/// [`GuideEngine::new`]'s signature. Until those tasks land, an
/// unimplemented kind falls back to the axis default (see [`make_algo`]).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AlgoKind {
    Hysteresis,
    ResistSwitch,
    Lowpass,
    Lowpass2,
    ZFilter,
    Ppec,
}

/// Engine configuration (dossier §15 defaults). `cal`/`find` are the
/// calibration and star-find parameter blocks; the rest are the move-pipeline
/// and algorithm knobs.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EngineConfig {
    pub cal: CalConfig,
    pub find: FindParams,
    pub max_ra_duration_ms: u32,
    pub max_dec_duration_ms: u32,
    pub dec_guide_mode: DecMode,
    pub ra_algorithm: AlgoKind,
    pub dec_algorithm: AlgoKind,
    /// Static backlash-compensation seed pulse (ms) added on a dec direction
    /// reversal (dossier §10.1). `0` = BLC disabled, matching PHD2's shipped
    /// default (dossier §10/§15; the adaptive size controller stays OFF per
    /// D4).
    pub blc_pulse_ms: u32,
}

impl Default for EngineConfig {
    /// Dossier §15 shipped defaults: RA = Hysteresis, Dec = ResistSwitch
    /// (dossier §6/§14), 2500 ms axis ceilings, dec mode Auto, BLC pulse 0
    /// (disabled).
    fn default() -> Self {
        EngineConfig {
            cal: CalConfig::default(),
            find: FindParams::default(),
            max_ra_duration_ms: DEFAULT_MAX_DURATION_MS,
            max_dec_duration_ms: DEFAULT_MAX_DURATION_MS,
            dec_guide_mode: DecMode::Auto,
            ra_algorithm: AlgoKind::Hysteresis,
            dec_algorithm: AlgoKind::ResistSwitch,
            blc_pulse_ms: 0,
        }
    }
}

/// Host-supplied scope pointing, patched onto a freshly-calibrated [`Cal`]
/// (OBLIGATION (e)). The [`Calibrator`] is I/O-free and stamps sentinels for
/// everything it cannot read (declination `UNKNOWN_DECLINATION`, pier/parity
/// `Unknown`, rotator 0, binning 1); the host injects the real values here so
/// dec compensation and sanity check #3 have something real to work with,
/// mirroring upstream reading the scope at COMPLETE (`scope.cpp:1752-1760`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ScopePointing {
    pub declination: f64,
    pub pier_side: PierSide,
    pub ra_parity: Parity,
    pub dec_parity: Parity,
    pub rotator_angle: f64,
    pub binning: u16,
}

impl Default for ScopePointing {
    /// All-unknown: the same sentinels [`Calibrator`] itself stamps, so an
    /// engine whose host never calls [`GuideEngine::set_scope_pointing`]
    /// behaves exactly as the raw calibration would.
    fn default() -> Self {
        ScopePointing {
            declination: UNKNOWN_DECLINATION,
            pier_side: PierSide::Unknown,
            ra_parity: Parity::Unknown,
            dec_parity: Parity::Unknown,
            rotator_angle: 0.0,
            binning: 1,
        }
    }
}

/// A snapshot of guide-error statistics matching the host's `GuideStats` bus
/// shape (spec §3.2/§3.5). `recent` is `(timestamp_s, ra_err_px, dec_err_px)`
/// per accepted frame, newest last.
#[derive(Debug, Clone, PartialEq)]
pub struct GuideStatsSnapshot {
    pub guiding: bool,
    pub rms_ra: f64,
    pub rms_dec: f64,
    pub rms_total: f64,
    pub snr: f64,
    pub recent: Vec<(f64, f64, f64)>,
}

/// Engine lifecycle phase.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Phase {
    /// No lock, not calibrating, not guiding (fresh, or after a fatal
    /// calibration failure).
    Idle,
    /// Driving the [`Calibrator`] one [`Action::CalStep`] per frame.
    Calibrating,
    /// Guiding (with, optionally, an active settle window overlaid).
    Guiding,
}

/// The pure guide engine (dossier §7 composition). See the module docs.
pub struct GuideEngine {
    cfg: EngineConfig,
    cal: Option<Cal>,
    scope: ScopePointing,
    phase: Phase,

    calibrator: Option<Calibrator>,
    cal_advisories: Vec<String>,

    /// Lock position (camera-frame px). `None` until the first found star of
    /// a guiding session establishes it. The lock is the OFFSET REFERENCE
    /// only — never the search origin (see `search_origin`).
    lock: Option<(f64, f64)>,

    /// Search origin for the next frame's [`measure`](Self::measure): the
    /// star's last found position — upstream's `m_primaryStar` position,
    /// which the per-frame update searches around
    /// (`guider_multistar.cpp:943/945`, `Star newStar(m_primaryStar);
    /// newStar.Find(...)`) and advances on every accepted frame
    /// (`guider_multistar.cpp:1026`, `m_primaryStar = newStar`); a failed
    /// find retains the prior position (`star.cpp:479-483`). `None` means no
    /// star is currently tracked (fresh guiding session before the first
    /// auto-find). Updated in [`ingest`](Self::ingest): every found frame
    /// while calibrating, and the lock-establishing / settle-found /
    /// accepted frames while guiding — never on the mass-reject or
    /// distance-reject paths (upstream only assigns at `:1026`, after both
    /// checks pass).
    search_origin: Option<(f64, f64)>,

    ra_algo: Box<dyn GuideAlgorithm>,
    dec_algo: Box<dyn GuideAlgorithm>,

    mass_checker: MassChecker,
    distance_checker: DistanceChecker,
    avg_dist: AvgDist,

    /// Accepted-frame counter since the last reset boundary — upstream's
    /// `CurrentErrorFrameCount()` / `m_avgDistanceCnt` (dossier §13; feeds the
    /// `< 10` term of OBLIGATION (d)'s `small_context`).
    frames_since_reset: u32,
    /// Timestamp of the last found star (lock frame or accepted frame), for
    /// the lost-star give-up test.
    last_good_find_s: Option<f64>,
    /// Last dec pulse direction, for the static-BLC reversal test (dossier
    /// §10.1; only tracked in DEC_AUTO with a configured pulse).
    last_dec_dir: Option<Direction>,

    settle: Option<Settle>,

    recent: VecDeque<(f64, f64, f64)>,
    last_snr: f64,
}

/// Construct a boxed axis algorithm from an [`AlgoKind`]. P1 implements
/// Hysteresis and ResistSwitch; the other kinds fall back to the axis default
/// (RA = Hysteresis, Dec = ResistSwitch) so the constructor never fails —
/// they are filled in by later tasks (dossier §6.3-§6.5/§6.8) without
/// touching this signature.
fn make_algo(kind: AlgoKind, is_ra: bool) -> Box<dyn GuideAlgorithm> {
    match kind {
        AlgoKind::Hysteresis => Box::new(Hysteresis::default()),
        AlgoKind::ResistSwitch => Box::new(ResistSwitch::default()),
        _ => {
            if is_ra {
                Box::new(Hysteresis::default())
            } else {
                Box::new(ResistSwitch::default())
            }
        }
    }
}

fn flip_parity(p: Parity) -> Parity {
    match p {
        Parity::Even => Parity::Odd,
        Parity::Odd => Parity::Even,
        Parity::Unknown => Parity::Unknown,
    }
}

impl GuideEngine {
    /// Build an engine from a config. The engine starts idle: a caller drives
    /// it via [`begin_calibration`](Self::begin_calibration) (measure a fresh
    /// [`Cal`]) or [`set_calibration`](Self::set_calibration) +
    /// [`begin_guiding`](Self::begin_guiding) (reuse a persisted one).
    pub fn new(cfg: EngineConfig) -> Self {
        GuideEngine {
            ra_algo: make_algo(cfg.ra_algorithm, true),
            dec_algo: make_algo(cfg.dec_algorithm, false),
            cfg,
            cal: None,
            scope: ScopePointing::default(),
            phase: Phase::Idle,
            calibrator: None,
            cal_advisories: Vec::new(),
            lock: None,
            search_origin: None,
            mass_checker: MassChecker::new(MASS_WINDOW_MS),
            distance_checker: DistanceChecker::new(),
            avg_dist: AvgDist::new(),
            frames_since_reset: 0,
            last_good_find_s: None,
            last_dec_dir: None,
            settle: None,
            recent: VecDeque::with_capacity(RECENT_CAP),
            last_snr: 0.0,
        }
    }

    /// Inject the live scope pointing (declination in radians, pier side,
    /// parities, rotator angle in radians, binning). Patched onto a freshly
    /// measured [`Cal`] at calibration completion (OBLIGATION (e)) and used
    /// live for RA dec-compensation (dossier §9 item 6).
    pub fn set_scope_pointing(&mut self, scope: ScopePointing) {
        self.scope = scope;
    }

    /// Begin measuring a fresh calibration starting from the primary star's
    /// camera-frame position (dossier §8.2). Subsequent [`ingest`](Self::ingest)
    /// calls return [`Action::CalStep`] until the state machine completes, at
    /// which point the engine stamps the [`Cal`] (OBLIGATION (e)) and switches
    /// to guiding.
    pub fn begin_calibration(&mut self, primary: (f64, f64)) {
        // The engine-level dec guide mode is the single source of truth; the
        // Calibrator's own CalConfig copy is overridden to match (both fields
        // exist in the brief's frozen EngineConfig).
        let mut cal_cfg = self.cfg.cal;
        cal_cfg.dec_guide_mode = self.cfg.dec_guide_mode;
        self.calibrator = Some(Calibrator::new(cal_cfg, primary));
        self.cal = None;
        self.cal_advisories.clear();
        self.phase = Phase::Calibrating;
        self.lock = Some(primary);
        // Track the star from its starting position; ingest_calibrating
        // advances this on every found frame (search-origin parity — see the
        // field doc).
        self.search_origin = Some(primary);
        self.reset_guiding_state();
    }

    /// Begin guiding with the current calibration. Requires a valid [`Cal`]
    /// (set via [`set_calibration`](Self::set_calibration) or a prior
    /// calibration run). Resets the guide state to a clean session boundary —
    /// including a **fresh** [`AvgDist`] (OBLIGATION (c)) — and clears the
    /// lock so the next found star establishes it.
    ///
    /// # Panics
    /// If no valid calibration is present.
    pub fn begin_guiding(&mut self) {
        assert!(
            self.cal.map(|c| c.is_valid).unwrap_or(false),
            "begin_guiding requires a valid Cal"
        );
        self.phase = Phase::Guiding;
        self.lock = None;
        // Host-initiated fresh session: drop the tracked star so the next
        // measure() runs a full auto_find acquisition. (The internal
        // calibration-complete transition deliberately KEEPS the origin —
        // upstream never re-auto-finds the star it just calibrated on.)
        self.search_origin = None;
        self.reset_guiding_state();
    }

    /// Reset every per-session tracker to a clean reset boundary. Constructs a
    /// **fresh** [`AvgDist`] rather than mutating the old one — OBLIGATION (c):
    /// the average-distance reinit-collapse (dossier §13's "not guiding /
    /// need-reset" branches both collapse to `avg = dist; cnt = 1`) is only
    /// faithful under reconstruction, so this is the single entry point for
    /// the guiding-start / post-fast-recenter / resume-from-pause boundaries
    /// (post-fast-recenter and resume-from-pause are P2 dither/pause seams;
    /// guiding-start is live here).
    fn reset_guiding_state(&mut self) {
        self.avg_dist = AvgDist::new(); // OBLIGATION (c)
        self.distance_checker = DistanceChecker::new();
        self.mass_checker.reset();
        self.ra_algo.reset();
        self.dec_algo.reset();
        self.frames_since_reset = 0;
        self.last_good_find_s = None;
        self.last_dec_dir = None;
        self.settle = None;
        self.recent.clear();
        self.last_snr = 0.0;
    }

    /// The per-frame decision (dossier §7). `measured[0]` is the primary star
    /// (secondaries follow in P3). Returns the [`Action`] the host performs.
    pub fn ingest(&mut self, meta: &FrameMeta, measured: &[MeasuredStar]) -> Action {
        match self.phase {
            Phase::Idle => Action::Idle,
            Phase::Calibrating => self.ingest_calibrating(measured),
            Phase::Guiding => self.ingest_guiding(meta, measured),
        }
    }

    fn ingest_calibrating(&mut self, measured: &[MeasuredStar]) -> Action {
        let star = measured.first().copied();
        // Wait (no move) until the calibration star is measured this frame —
        // upstream simply doesn't advance the state machine on a lost star
        // (star.cpp:479-483: a failed find retains the prior position, so
        // the search origin also stays put).
        let Some(s) = star.filter(|s| s.found) else {
            return Action::Idle;
        };
        // Search-origin parity (guider_multistar.cpp:943/1026): the next
        // frame's find searches around THIS frame's found position — the
        // calibration legs drift the star far beyond search_region of the
        // starting point, so a fixed origin would lose it mid-leg.
        self.search_origin = Some((s.x, s.y));

        let outcome = self
            .calibrator
            .as_mut()
            .expect("calibrating phase implies a live Calibrator")
            .step((s.x, s.y));

        match outcome {
            CalOutcome::Pulse { leg, dir, ms } => Action::CalStep { leg, dir, ms },
            CalOutcome::Done(mut cal) => {
                let calr = self.calibrator.as_ref().expect("live Calibrator");
                let (ra_steps, dec_steps) = (calr.ra_steps(), calr.dec_steps());
                // OBLIGATION (e): stamp REAL declination/pier/parity/rotator/
                // binning onto the sentinel-stamped Cal before it feeds
                // transforms (dec comp) or advisories (sanity check #3), exactly
                // as upstream stamps the scope-reported values at COMPLETE
                // (scope.cpp:1752-1760). Sanity check #3 is a no-op until a
                // real declination lands, so advisories are computed AFTER the
                // patch.
                self.patch_cal_from_scope(&mut cal);
                self.cal_advisories = sanity_advisories(&cal, ra_steps, dec_steps);
                self.cal = Some(cal);
                self.calibrator = None;
                // Transition straight into guiding; the next frame's found
                // star establishes the lock. `search_origin` is deliberately
                // KEPT (unlike begin_guiding's fresh-session clear): the
                // just-calibrated star is still tracked at a known position,
                // so the next measure() star_finds around it rather than
                // re-running a full-frame auto_find, mirroring upstream's
                // continuous m_primaryStar tracking across the
                // calibration->guiding transition.
                self.phase = Phase::Guiding;
                self.lock = None;
                self.reset_guiding_state();
                Action::Idle
            }
            CalOutcome::Failed(_msg) => {
                // Calibration cannot proceed; drop to idle and signal the host.
                // (The Action enum has no dedicated calibration-failure variant;
                // LockLost is the "guiding cannot continue" signal.)
                self.calibrator = None;
                self.phase = Phase::Idle;
                Action::LockLost
            }
        }
    }

    fn ingest_guiding(&mut self, meta: &FrameMeta, measured: &[MeasuredStar]) -> Action {
        let now = meta.timestamp_s;
        let dec_guiding = self.cfg.dec_guide_mode != DecMode::Off;
        let star = measured.first().copied();
        let found = star.map(|s| s.found).unwrap_or(false);

        // 1. Establish the lock on the first found star of the session. The
        //    lock frame issues no correction and is not an accepted guide
        //    frame (no mass append, no avg-dist update).
        let Some(lock) = self.lock else {
            if let Some(s) = star.filter(|s| s.found) {
                self.lock = Some((s.x, s.y));
                self.search_origin = Some((s.x, s.y));
                self.last_good_find_s = Some(now);
            }
            return Action::Idle;
        };
        let cal = self.cal.expect("guiding phase implies a valid Cal");

        // Camera- and mount-frame offset from the lock, when a star was found.
        let offset = star.filter(|s| s.found).map(|s| {
            let camera = (s.x - lock.0, s.y - lock.1);
            (camera, camera_to_mount(camera, &cal))
        });

        // 2. Active settle window (dossier §12). While settling, the P1 stub
        //    waits (returns Settle); full dither guiding-during-settle is P2.
        if let Some(settle) = self.settle.as_mut() {
            // The origin still advances on found frames while settling:
            // upstream's UpdateCurrentPosition keeps running (and assigning
            // m_primaryStar at :1026) while the controller settles — the
            // settle overlay never freezes star tracking. This P1 stub
            // bypasses the accept machinery, so "found" is the analogue.
            if let Some(s) = star.filter(|s| s.found) {
                self.search_origin = Some((s.x, s.y));
            }
            let (locked_now, err) = match offset {
                Some((camera, _)) => (true, camera.0.hypot(camera.1)),
                None => (false, LARGE_DISTANCE),
            };
            return match settle.evaluate(err, locked_now, now) {
                SettleState::Settling => Action::Settle,
                SettleState::Done => {
                    self.settle = None;
                    Action::Idle
                }
                SettleState::Failed(_) => {
                    self.settle = None;
                    Action::LockLost
                }
            };
        }

        // 3. Lost star (dossier §3.3): schedule a dead-reckoning move — every
        //    P1 algorithm's deduce_result is 0.0, so no pulse — and give up the
        //    lock once the star has been missing longer than the staleness
        //    threshold.
        if !found {
            self.distance_checker.activate(now);
            let stale = self
                .last_good_find_s
                .map(|t| now - t > LOST_STAR_TIMEOUT_S)
                .unwrap_or(true);
            if stale {
                return Action::LockLost;
            }
            return Action::Idle;
        }
        let s = star.expect("found implies a star");
        let (camera, mount) = offset.expect("found implies an offset");

        // 4. Star-mass gate (dossier §3.1). OBLIGATION (a): CheckMass is called
        //    EXACTLY ONCE per frame — it drifts the low-water mark through a
        //    Cell on every call, so a second/preview call would corrupt state.
        let mass_ok = self.mass_checker.check(s.mass, MASS_CHANGE_THRESHOLD); // OBLIGATION (a)
        if !mass_ok {
            // STAR_MASSCHANGE: frame dropped, DistanceChecker activated. The new
            // mass IS still appended (upstream guider_multistar.cpp:984).
            self.mass_checker.append(now * 1000.0, s.mass); // OBLIGATION (b): reject path
            self.distance_checker.activate(now);
            return Action::Idle;
        }

        // 5. Jump rejection / lost-star recovery (dossier §3.2). OBLIGATION (d):
        //    feed the staleness-gated current_error_smoothed as err_smoothed,
        //    and compute small_context from the not-guiding / paused / settling
        //    / <10-frames predicate (guider_multistar.cpp _CheckDistance).
        let ra_only = !dec_guiding;
        let distance = if ra_only {
            camera.0.abs()
        } else {
            camera.0.hypot(camera.1)
        };
        let err_smoothed = self.avg_dist.current_error_smoothed(now, dec_guiding); // OBLIGATION (d)
        let not_guiding = self.phase != Phase::Guiding;
        let paused = false; // no host-pause channel in P1 (P2 seam)
        let settling = self.settle.is_some(); // false here — settle returns early above
        let small_context = not_guiding || paused || settling || self.frames_since_reset < 10; // OBLIGATION (d)
        let accepted =
            self.distance_checker
                .check_distance(now, distance, err_smoothed, small_context);
        if !accepted {
            // "Recovering": frame dropped. No mass append here (upstream appends
            // only on mass-reject :984 and on accept :1027, never on this path).
            return Action::Idle;
        }

        // 6. Accepted frame. OBLIGATION (b): append the star mass on the accept
        //    path too (upstream guider_multistar.cpp:1027), after the distance
        //    check passes. The search origin advances HERE and only here on
        //    the guiding path (guider_multistar.cpp:1026, `m_primaryStar =
        //    newStar` — the mass-reject and distance-reject paths above throw
        //    before that assignment, retaining the prior origin).
        self.mass_checker.append(now * 1000.0, s.mass); // OBLIGATION (b): accept path
        self.search_origin = Some((s.x, s.y));
        self.last_good_find_s = Some(now);
        self.frames_since_reset += 1;
        let distance_ra = mount.0.abs();
        self.avg_dist.update(now, distance, distance_ra);
        self.last_snr = s.snr;
        self.push_recent(now, mount.0, mount.1);

        // 7. Move pipeline (dossier §7): algorithms -> direction/rate -> ms,
        //    static BLC, dec-mode gating, duration clamps.
        self.compute_move(mount, s.snr, meta.exposure_s)
    }

    /// The dossier §7 move pipeline for one accepted frame's mount-frame
    /// error. Returns [`Action::PulsePair`] (or [`Action::Idle`] when both
    /// axes are vetoed/clamped to zero).
    fn compute_move(&mut self, mount: (f64, f64), snr: f64, dt: f64) -> Action {
        let cal = self.cal.expect("guiding phase implies a valid Cal");

        // Per-axis transfer functions (dossier §6). result_with threads snr +
        // exposure for a future predictive (PPEC) axis; Hysteresis and
        // ResistSwitch ignore both and delegate to result().
        let xd = self.ra_algo.result_with(mount.0, snr, dt);
        let yd = self.dec_algo.result_with(mount.1, snr, dt);

        // Directions from the post-algorithm correction (dossier §7:
        // xd > 0 => WEST, yd > 0 => SOUTH).
        let xdir = if xd > 0.0 {
            Direction::West
        } else {
            Direction::East
        };
        let ydir = if yd > 0.0 {
            Direction::South
        } else {
            Direction::North
        };

        // RA rate is dec-compensated live; Dec uses the fixed calibration rate
        // (dossier §7/§9.6/§14).
        let x_rate = self.effective_x_rate(&cal);
        let mut x_ms = (xd / x_rate).abs().round() as i64;
        let mut y_ms = (yd / cal.y_rate).abs().round() as i64;

        // Dec-mode gating (dossier §7 MoveAxis, scope.cpp:726-733): Off zeroes
        // dec; North blocks a SOUTH pulse; South blocks a NORTH pulse.
        match self.cfg.dec_guide_mode {
            DecMode::Off => y_ms = 0,
            DecMode::North if ydir == Direction::South => y_ms = 0,
            DecMode::South if ydir == Direction::North => y_ms = 0,
            _ => {}
        }

        // Static backlash compensation (dossier §10.1; D4: static only,
        // adaptive controller OFF). Only in DEC_AUTO, only with a configured
        // seed pulse, only when the dec correction is non-zero (mirroring
        // apply()'s early returns, which also skip updating last_dir on a
        // zero move). Adds one fixed pulse on a dec direction reversal.
        let mut eff_max_dec = self.cfg.max_dec_duration_ms as i64;
        if self.cfg.dec_guide_mode == DecMode::Auto && self.cfg.blc_pulse_ms > 0 && yd != 0.0 {
            if let Some(last) = self.last_dec_dir {
                if ydir != last {
                    y_ms += self.cfg.blc_pulse_ms as i64;
                }
            }
            self.last_dec_dir = Some(ydir);
            // dossier §10.1: a BLC pulse larger than max_dec_duration raises
            // the dec ceiling to admit it (the setter's "raise max_dec" note).
            eff_max_dec = eff_max_dec.max(self.cfg.blc_pulse_ms as i64);
        }

        // MoveAxis duration clamps (dossier §7/§14).
        x_ms = x_ms.clamp(0, self.cfg.max_ra_duration_ms as i64);
        y_ms = y_ms.clamp(0, eff_max_dec);

        let ra = (x_ms > 0).then_some(AxisPulse {
            dir: xdir,
            ms: x_ms as u32,
        });
        let dec = (y_ms > 0).then_some(AxisPulse {
            dir: ydir,
            ms: y_ms as u32,
        });
        if ra.is_none() && dec.is_none() {
            Action::Idle
        } else {
            Action::PulsePair { ra, dec }
        }
    }

    /// RA rate with live declination compensation (dossier §9 item 6):
    /// `cal.x_rate / cos(cal.declination) * cos(current_dec)`. Falls back to
    /// the raw `cal.x_rate` when either declination is the UNKNOWN sentinel or
    /// the calibration is too far from the equator (`|cal.declination| > 60°`).
    fn effective_x_rate(&self, cal: &Cal) -> f64 {
        let cur_dec = self.scope.declination;
        if cal.declination == UNKNOWN_DECLINATION
            || cur_dec == UNKNOWN_DECLINATION
            || cal.declination.abs() > DEC_COMP_LIMIT
        {
            return cal.x_rate;
        }
        let cur = cur_dec.clamp(-DEC_COMP_MAX_DEC, DEC_COMP_MAX_DEC);
        cal.x_rate / cal.declination.cos() * cur.cos()
    }

    /// Run `star_find` around the tracked star's last found position, or
    /// `auto_find` + `select_primary` when no star is tracked (dossier
    /// §1/§2). The search origin is the star's PREVIOUS FRAME position —
    /// upstream parity: `GuiderMultiStar::UpdateCurrentPosition` searches
    /// around `m_primaryStar` (`guider_multistar.cpp:943/945`), which
    /// advances on every accepted frame (`:1026`); the lock position is only
    /// the offset reference and is never used as a search origin. Pure — the
    /// PyO3 `process` wrapper calls this then [`ingest`](Self::ingest), which
    /// performs the origin update. Never mutates engine state (in particular
    /// it never touches the MassChecker, so it cannot violate OBLIGATION
    /// (a)).
    pub fn measure(&self, frame: &astro_star::GrayFrame) -> Vec<MeasuredStar> {
        match self.search_origin {
            Some((lx, ly)) => {
                let r = star_find(frame, lx, ly, &self.cfg.find);
                vec![MeasuredStar {
                    x: r.x,
                    y: r.y,
                    snr: r.snr,
                    mass: r.mass,
                    hfd: r.hfd,
                    found: was_found(r.result),
                }]
            }
            None => {
                let sp = SelectParams {
                    search_region: self.cfg.find.search_region,
                    ..SelectParams::default()
                };
                let cands = auto_find(frame, &sp);
                let peaks: Vec<(i32, i32)> =
                    cands.iter().map(|c| (c.x as i32, c.y as i32)).collect();
                let sat = saturation_threshold(frame, &peaks, &self.cfg.find);
                match select_primary(&cands, sat, sp.af_min_snr) {
                    Some(i) => {
                        let c = cands[i];
                        vec![MeasuredStar {
                            x: c.x,
                            y: c.y,
                            snr: c.snr,
                            mass: c.mass,
                            hfd: c.hfd,
                            found: true,
                        }]
                    }
                    None => Vec::new(),
                }
            }
        }
    }

    /// Dither by a mount-frame offset (px). **P1 stub**: resets the axis
    /// algorithms (dossier §11.2 `GuidingDithered` -> `reset()`) and opens a
    /// settle window (dossier §12); the full lock reposition + fast recenter
    /// is P2. While the window is open, [`ingest`](Self::ingest) returns
    /// [`Action::Settle`].
    pub fn dither(&mut self, dx_px: f64, dy_px: f64) {
        let _ = (dx_px, dy_px); // lock reposition + fast recenter: P2
        self.ra_algo.reset();
        self.dec_algo.reset();
        self.settle = Some(Settle::new(
            DEFAULT_SETTLE_TOL_PX,
            DEFAULT_SETTLE_TIME_S,
            DEFAULT_SETTLE_TIMEOUT_S,
        ));
    }

    /// Current guide-error statistics in the host's `GuideStats` shape
    /// (spec §3.2/§3.5). RMS is over the retained recent-frame window.
    pub fn stats(&self) -> GuideStatsSnapshot {
        let guiding = self.phase == Phase::Guiding && self.settle.is_none();
        let n = self.recent.len();
        let (mut sra, mut sdec, mut stot) = (0.0f64, 0.0f64, 0.0f64);
        for &(_, ra, dec) in &self.recent {
            sra += ra * ra;
            sdec += dec * dec;
            stot += ra * ra + dec * dec;
        }
        let rms = |sumsq: f64| {
            if n > 0 {
                (sumsq / n as f64).sqrt()
            } else {
                0.0
            }
        };
        GuideStatsSnapshot {
            guiding,
            rms_ra: rms(sra),
            rms_dec: rms(sdec),
            rms_total: rms(stot),
            snr: self.last_snr,
            recent: self.recent.iter().copied().collect(),
        }
    }

    /// Post-calibration sanity advisories from the last completed calibration
    /// (dossier §8.3), computed after OBLIGATION (e)'s declination patch so
    /// check #3 is meaningful. Empty until a calibration completes.
    pub fn calibration_advisories(&self) -> &[String] {
        &self.cal_advisories
    }

    /// Adjust the stored calibration for a meridian flip (dossier §9 item 4;
    /// `Mount::FlipCalibration`, `mount.cpp:891-957`). `x_angle += π`;
    /// `y_angle += π` only when `requires_dec_flip`; dec parity flips unless
    /// the dec flip was required; RA parity never changes; pier side toggles.
    /// Returns `false` (no-op) when there is no valid calibration.
    pub fn flip_calibration(&mut self, requires_dec_flip: bool) -> bool {
        match self.cal.as_mut() {
            Some(cal) if cal.is_valid => {
                cal.x_angle = norm_angle(cal.x_angle + PI);
                if requires_dec_flip {
                    cal.y_angle = norm_angle(cal.y_angle + PI);
                }
                cal.y_angle_error = Cal::y_angle_error_from(cal.x_angle, cal.y_angle);
                if !requires_dec_flip {
                    cal.dec_parity = flip_parity(cal.dec_parity);
                }
                cal.pier_side = match cal.pier_side {
                    PierSide::East => PierSide::West,
                    PierSide::West => PierSide::East,
                    PierSide::Unknown => PierSide::Unknown,
                };
                true
            }
            _ => false,
        }
    }

    /// The current calibration, if any (serializable state for persistence).
    pub fn calibration(&self) -> Option<Cal> {
        self.cal
    }

    /// Install a calibration (e.g. one persisted from a prior session). Stored
    /// verbatim — no sentinel patching (unlike the calibration-complete path,
    /// this Cal is assumed already real). Follow with
    /// [`begin_guiding`](Self::begin_guiding).
    pub fn set_calibration(&mut self, cal: Cal) {
        self.cal = Some(cal);
    }

    fn patch_cal_from_scope(&self, cal: &mut Cal) {
        cal.declination = self.scope.declination;
        cal.pier_side = self.scope.pier_side;
        cal.ra_parity = self.scope.ra_parity;
        cal.dec_parity = self.scope.dec_parity;
        cal.rotator_angle = self.scope.rotator_angle;
        cal.binning = self.scope.binning;
    }

    fn push_recent(&mut self, t: f64, ra: f64, dec: f64) {
        if self.recent.len() == RECENT_CAP {
            self.recent.pop_front();
        }
        self.recent.push_back((t, ra, dec));
    }
}
