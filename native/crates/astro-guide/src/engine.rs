// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§3, §5, §6, §7, §9,
// §10.1, §11.2, §12, §13). This is the composition layer: it wires the
// already-committed, already-tested starfind / select / track / transforms /
// algorithms / calibration primitives into one per-frame decision. Derived
// from PHD2 `guider_multistar.cpp:925-1060`
// (`GuiderMultiStar::UpdateCurrentPosition`: the star-find -> mass-check ->
// distance-check -> offset per-frame pipeline), `mount.cpp:978-1087`
// (`Mount::MoveOffset`: algorithm dispatch, direction/rate -> ms, BLC hook),
// `scope.cpp:640-816` (`Scope::MoveAxis`: dec-mode gating + duration clamps),
// `mount.cpp:1253-1409` (`Mount::AdjustCalibrationForScopePointing`: dec
// compensation + pier flip), `backlash_comp.cpp:371-583` (`BacklashComp::
// ApplyBacklashComp`: the static direction-reversal pulse, §10.1),
// `scope.cpp:1752-1760` (COMPLETE-state calibration stamping),
// `guider.cpp:1261-1553` (the guide-loop dispatch that sequences these),
// `guider.cpp:838-929` (`Guider::MoveLockPosition`: the dither lock-shift +
// average-distance inflate + fast-recenter arming, §11.2),
// `guider.cpp:1485-1511` (the per-frame fast-recenter step, bypassing the
// guide algorithms), and `phdcontrol.cpp:514-567`
// (`PhdController::UpdateControllerState` STATE_SETTLE_WAIT, §12 — this
// module's call site for [`settle::Settle::evaluate`], not the settle state
// machine itself, which lives in `settle.rs`) (BSD-3-Clause; see
// THIRD-PARTY-NOTICES.md). No code copied from PHD2.

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
//! tag for the upstream citation. Three more, from the P1-T7/T10 reviews'
//! P2 punch list, are discharged in [`GuideEngine::dither`] and
//! [`GuideEngine::ingest_guiding`]'s settle overlay (tagged `P2-T1
//! punch-list #1/#2/#3`): the settle evaluator is fed the SMOOTHED
//! `avg_dist` rather than a frame's raw offset (#1), a fresh `AvgDist` is
//! constructed at the post-fast-recenter boundary (#2), and the host-side
//! settle handshake in `guide/native.py` is wired to the engine's real
//! settle lifecycle rather than a single frame's `Action` shape (#3,
//! host-side — see that file). The P2-T1 fix round (opus review) further
//! made the settle a parallel MONITOR over live guiding rather than a
//! phase that suspends it (`guider.cpp:1517-1521`), and modeled upstream's
//! ALGO-vs-RECOVERY moveOptions split (clamps + dec-mode gating are
//! ALGO-only; `scope.cpp:727/:736/:761`) and the dither-settle DEC_AUTO
//! override (`phdcontrol.cpp:181-196/:501-505/:570-575`) — see
//! [`GuideEngine::apply_move`].

use std::collections::VecDeque;
use std::f64::consts::PI;

use crate::algorithms::{GuideAlgorithm, Hysteresis, ResistSwitch};
use crate::calibration::{
    sanity_advisories, CalConfig, CalOutcome, Calibrator, DecMode, UNKNOWN_DECLINATION,
};
use crate::refine::{self, PrimaryDistStats, SecondaryStar};
use crate::select::{
    auto_find, primary_and_secondaries, saturation_threshold, select_primary, SelectParams,
};
use crate::settle::{Settle, SettleState};
use crate::starfind::{star_find, was_found, FindParams};
use crate::track::{AvgDist, DistanceChecker, MassChecker};
use crate::transforms::{camera_to_mount, mount_to_camera, norm_angle, Cal, Parity, PierSide};
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
/// `DEC_COMP_LIMIT` (dossier §9; `scope.cpp:68`, `M_PI/3`, i.e. 60°): beyond
/// this calibration declination, RA dec-compensation is disabled.
const DEC_COMP_LIMIT: f64 = PI / 3.0;
/// Current declination is clamped to ±89° before dec-compensation (dossier
/// §9 item 6) to keep `cos(dec)` well away from zero.
const DEC_COMP_MAX_DEC: f64 = 89.0 * PI / 180.0;
/// Rolling window of recent accepted frames kept for [`GuideStatsSnapshot`].
const RECENT_CAP: usize = 100;
/// Default dither/settle window (dossier §12) [`GuideEngine::dither`] opens.
/// Not yet threaded through [`EngineConfig`] (the brief's frozen
/// `dither(dx_px, dy_px)` signature carries no settle-params override; a
/// per-call tolerance/time/timeout knob is a possible future task).
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
    /// Multi-star candidate-list cap (dossier §2.6/§4/§15; P3-T1).
    /// `1` (this task's default, matching upstream's `/guider/multistar/
    /// enabled = false`) keeps the engine on the P1/P2 single-star path
    /// end to end — [`GuideEngine::measure`]'s acquisition branch, the
    /// per-frame secondary tracking, and [`GuideEngine::ingest_guiding`]'s
    /// refinement call are all no-ops whenever `self.secondaries` stays
    /// empty, which it does unless this is `> 1`. Values above 12
    /// (`MAX_LIST_SIZE`) are accepted but have no additional effect —
    /// [`SelectParams`]/`auto_find`'s candidate scan already caps out at
    /// the top-100 PSF-response peaks (dossier §2.3 `TOP_N`) long before a
    /// list this large could form.
    pub max_stars: usize,
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
            max_stars: 1,
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
/// per accepted frame, newest last. `settling` (P2-T1 Produces line) is the
/// authoritative settle-window state — `true` for every frame from
/// [`GuideEngine::dither`] until the window closes (Done or Failed),
/// INCLUDING fast-recenter frames AND the dwell's ordinary guide-correction
/// frames (P2-T1 fix round: guiding continues through the settle,
/// `guider.cpp:1517-1521`), whose [`Action`] is an ordinary
/// [`Action::PulsePair`] and so cannot be told apart from normal guiding by
/// its shape alone.
#[derive(Debug, Clone, PartialEq)]
pub struct GuideStatsSnapshot {
    pub guiding: bool,
    pub settling: bool,
    pub rms_ra: f64,
    pub rms_dec: f64,
    pub rms_total: f64,
    pub snr: f64,
    pub recent: Vec<(f64, f64, f64)>,
    /// Currently tracked secondary guide stars' last-known camera-frame
    /// `(x, y)` positions (dossier §2.6/§4; P3-T1), for a UI overlay.
    /// Empty in single-star mode (`EngineConfig::max_stars <= 1`) or
    /// before the first multi-star acquisition of a session.
    pub secondaries: Vec<(f64, f64)>,
}

/// Fast-recenter-after-dither state (dossier §11.2; `guider.cpp:912-919`
/// `MoveLockPosition`'s `m_ditherRecenter*` fields + `guider.cpp:1485-1511`'s
/// per-frame step). `remaining`/`step` are mount-frame pixel MAGNITUDES
/// (always `>= 0`); `dir` is the correction's per-axis sign. The dither
/// moved the LOCK by `mount_delta`, so the star's offset from the new lock
/// is `-mount_delta` — `dir` shares that sign (`-sign(mount_delta)`),
/// matching [`GuideEngine::apply_move`]'s `xd/yd` sign convention (the same
/// sign a normal negative-feedback correction of that offset would have).
#[derive(Debug, Clone, Copy, PartialEq)]
struct Recenter {
    remaining: (f64, f64),
    step: (f64, f64),
    dir: (f64, f64),
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
    /// Active fast-recenter (dossier §11.2), overlaid on `settle` while a
    /// dither's post-move recenter hasn't finished. Always `None` when
    /// `settle` is `None` (both are armed together in [`dither`](Self::dither)
    /// and `recenter` never outlives the window it belongs to).
    recenter: Option<Recenter>,

    recent: VecDeque<(f64, f64, f64)>,
    last_snr: f64,

    /// Tracked secondary guide stars (dossier §2.6/§4; P3-T1). Empty in
    /// single-star mode. Rebuilt wholesale on every full re-acquisition
    /// (see [`rebuild_secondaries`](Self::rebuild_secondaries)), otherwise
    /// mutated in place by [`refine::refine_offset`] each accepted guiding
    /// frame.
    secondaries: Vec<SecondaryStar>,
    /// Running statistics on the primary star's per-frame distance from
    /// the lock position (dossier §4 `m_primaryDistStats`), feeding the
    /// stabilization gate's sigma.
    primary_dist_stats: PrimaryDistStats,
    /// Stabilization hysteresis flag (dossier §4 `m_stabilizing`): entered
    /// when the primary excursion exceeds
    /// [`refine::STABILITY_SIGMA_ENTER`] sigma, only exited at or below
    /// [`refine::STABILITY_SIGMA_EXIT`] sigma. While `true`,
    /// [`ingest_guiding`](Self::ingest_guiding) never calls
    /// [`refine::refine_offset`] in normal (non-recovery) mode.
    stabilizing: bool,
    /// Set by [`dither`](Self::dither) (dossier §4: "a dither sets
    /// lock_position_moved = true"); consumed on the frame the
    /// stabilization period exits, triggering a one-time secondary
    /// reference-point recovery re-find instead of the ordinary weighted
    /// average.
    lock_position_moved: bool,
    /// Set permanently once a panic inside the multi-star refinement path
    /// has been caught (dossier §4: "any exception... permanently drops
    /// back to single-star mode for the session"). Gates every subsequent
    /// [`refine_multistar`](Self::refine_multistar) call for the rest of
    /// this engine's life (cleared only by [`reset_guiding_state`]
    /// (Self::reset_guiding_state), i.e. a fresh session).
    multi_star_broken: bool,
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
            recenter: None,
            recent: VecDeque::with_capacity(RECENT_CAP),
            last_snr: 0.0,
            secondaries: Vec::new(),
            primary_dist_stats: PrimaryDistStats::new(),
            stabilizing: false,
            lock_position_moved: false,
            multi_star_broken: false,
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
        // measure() runs a full auto_find acquisition. (Since the P3-T1 fix
        // round the internal calibration-complete transition does the same —
        // see ingest_calibrating's CalOutcome::Done arm for the review-ruled
        // rationale.)
        self.search_origin = None;
        self.reset_guiding_state();
    }

    /// Reset every per-session tracker to a clean reset boundary. Constructs a
    /// **fresh** [`AvgDist`] rather than mutating the old one — OBLIGATION (c):
    /// the average-distance reinit-collapse (dossier §13's "not guiding /
    /// need-reset" branches both collapse to `avg = dist; cnt = 1`) is only
    /// faithful under reconstruction. This is the entry point for the
    /// guiding-start boundary (live here) and resume-from-pause (still a P2+
    /// seam — no host-pause channel exists yet).
    ///
    /// **Not** used for the post-fast-recenter boundary (dossier §11.2,
    /// P2-T1 punch-list #2), even though it is also an `AvgDist`
    /// reinitialization point: this method resets far more than `AvgDist` —
    /// `distance_checker`, `mass_checker`, both axis algorithms,
    /// `last_dec_dir`, and (critically) `self.settle` itself — none of which
    /// the dossier's post-recenter action touches, and clearing `settle`
    /// mid-window would abort the in-flight dwell timer entirely. See
    /// [`step_recenter`](Self::step_recenter)'s narrower fresh-`AvgDist`
    /// reconstruction (the same "construct rather than mutate" technique,
    /// applied to only the one field the boundary actually resets).
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
        self.recenter = None;
        self.recent.clear();
        self.last_snr = 0.0;
        // Multi-star state (dossier §4): a fresh session boundary is also
        // where `multi_star_broken`'s "for the session" scope ends.
        self.secondaries.clear();
        self.primary_dist_stats = PrimaryDistStats::new();
        self.stabilizing = false;
        self.lock_position_moved = false;
        self.multi_star_broken = false;
    }

    /// The per-frame decision (dossier §7). `measured[0]` is the primary
    /// star; `measured[1..]`, when multi-star tracking is active
    /// (`EngineConfig::max_stars > 1` and a secondary list has been
    /// established), are this frame's re-measurements of `self.secondaries`
    /// in the same order (dossier §2.6/§4; P3-T1) — see [`measure`](Self::measure).
    /// Returns the [`Action`] the host performs.
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
                // star establishes the lock. `search_origin` is CLEARED
                // (P3-T1 fix round, review ruling #3): the next `measure()`
                // runs exactly one full-frame auto_find, which is the ONLY
                // path that acquires multi-star secondaries — keeping the
                // origin here (the pre-fix behavior) left `max_stars > 1`
                // dead on the default calibrate->guide flow. DELIBERATE
                // DIFFERENCE vs upstream: PHD2 acquires the full guide-star
                // list at SELECT time and persists it through calibration
                // (`GuiderMultiStar::AutoSelect`,
                // guider_multistar.cpp:445-531 — `newStar.AutoFind(...,
                // m_guideStars, MAX_LIST_SIZE)` runs before any calibration
                // does, and calibration never touches `m_guideStars`); this
                // engine's acquisition lives inside `measure()`'s auto_find
                // branch instead, so re-running it here is the equivalent
                // seam. Functionally identical list contents: the star
                // field is unchanged across calibration, so the one
                // full-frame re-find returns the same stars at the same
                // places — and the continuous-tracking property this
                // replaces was cosmetic (the just-calibrated primary is
                // re-found by auto_find at its current position; asserted
                // by the auto-transition witness test in
                // engine_scenarios.rs).
                self.phase = Phase::Guiding;
                self.lock = None;
                self.search_origin = None;
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
        // Whether THIS frame's `measured` came from a fresh `auto_find`
        // acquisition pass rather than per-star tracking (dossier §2.6/§4;
        // P3-T1): `measure()` decides this from `self.search_origin` — see
        // its doc comment — so it must be captured here, BEFORE this
        // function's own mutations, to still reflect the state `measure()`
        // saw. True both on a session's very first lock-establishing frame
        // and on a post-long-lost bounded re-acquisition (dossier's
        // `AutoSelect` always rebuilds the whole guide-star list).
        let was_acquiring = self.search_origin.is_none();

        // 1. Establish the lock on the first found star of the session. The
        //    lock frame issues no correction and is not an accepted guide
        //    frame (no mass append, no avg-dist update).
        let Some(lock) = self.lock else {
            if let Some(s) = star.filter(|s| s.found) {
                self.lock = Some((s.x, s.y));
                self.search_origin = Some((s.x, s.y));
                self.last_good_find_s = Some(now);
                self.rebuild_secondaries(measured); // dossier §2.6/§4; P3-T1
            }
            return Action::Idle;
        };
        let cal = self.cal.expect("guiding phase implies a valid Cal");

        // Camera- and mount-frame offset from the lock, when a star was found.
        let offset = star.filter(|s| s.found).map(|s| {
            let camera = (s.x - lock.0, s.y - lock.1);
            (camera, camera_to_mount(camera, &cal))
        });

        // Settle-window flag for this frame (dossier §12). P2-T1 fix round
        // (CRITICAL): settling is a parallel MONITOR overlaid on guiding,
        // not a phase that suspends it — upstream keeps the Guider in
        // STATE_GUIDING issuing ordinary MOVEOPTS_GUIDE_STEP corrections on
        // every settling frame (guider.cpp:1517-1521) while PhdController
        // watches `current_guide_error`. Settling frames therefore flow
        // through the NORMAL mass/distance/accept/move machinery below; the
        // settle overlay after the accept bookkeeping (step 6) decides
        // whether this frame's Action is a fast-recenter step, the ordinary
        // guide correction, the Settle wait signal, or the window closing.
        let settling = self.settle.is_some();

        // 2. Lost star (dossier §3.3): schedule a dead-reckoning move — every
        //    P1 algorithm's deduce_result is 0.0, so no pulse — and give up the
        //    lock once the star has been missing longer than the staleness
        //    threshold. While a settle window is open the settle monitor OWNS
        //    the failure path instead (reason classification: a settling
        //    LockLost must mean settle_timeout and nothing else): the frame
        //    is dropped exactly as upstream drops it (no avg update), and the
        //    monitor still evaluates with locked=false — upstream's
        //    IsLocked() is `m_primaryStar.WasFound()`
        //    (guider_multistar.h:174-177), false after the failed find
        //    SetErrors the star (star.cpp:48-70).
        if !found {
            self.distance_checker.activate(now);
            if settling {
                return self.settle_monitor_dropped_frame(now, dec_guiding);
            }
            let stale = self
                .last_good_find_s
                .map(|t| now - t > LOST_STAR_TIMEOUT_S)
                .unwrap_or(true);
            if stale {
                // P2-T2 hardening (bounded auto-reselect; NOT upstream-derived
                // — PHD2 has no analogue here, this is new AstroDeck policy):
                // a star missing this long is no longer well-modeled by a
                // narrow search around its last local position — it may have
                // drifted, or whatever occluded it (cloud, satellite trail)
                // cleared while the mount kept tracking elsewhere. Dropping
                // the search origin makes the NEXT measure() fall back to the
                // same full-frame auto_find/select_primary pass a session's
                // very first lock uses (measure()'s `None` branch) — a
                // strictly BROADER search than the narrow search_region this
                // frame's failed local star_find just used. `lock` (the
                // offset reference) is left untouched: this only changes
                // WHERE the next frame searches, not what a subsequently
                // found star's offset is measured against. The engine keeps
                // signalling LockLost{star_lost} on every frame the star
                // stays missing, exactly as before `last_good_find_s` is
                // never touched here — so the HOST's own bounded give-up
                // budget (NativeGuider._REACQUIRE_BUDGET, guide/native.py)
                // is unchanged and remains the "bounded" half of "bounded
                // auto-reselect".
                self.search_origin = None;
                return Action::LockLost;
            }
            return Action::Idle;
        }
        let s = star.expect("found implies a star");
        // `mut`: dossier §4's RefineOffset may replace both (step 5 below),
        // once the frame is accepted — see the `refine_multistar` call.
        let (mut camera, mut mount) = offset.expect("found implies an offset");

        // 3. Star-mass gate (dossier §3.1). OBLIGATION (a): CheckMass is called
        //    EXACTLY ONCE per frame — it drifts the low-water mark through a
        //    Cell on every call, so a second/preview call would corrupt state.
        //    Runs on settling frames too (upstream UpdateCurrentPosition runs
        //    the full gate stack while the controller settles).
        let mass_ok = self.mass_checker.check(s.mass, MASS_CHANGE_THRESHOLD); // OBLIGATION (a)
        if !mass_ok {
            // STAR_MASSCHANGE: frame dropped, DistanceChecker activated. The new
            // mass IS still appended (upstream guider_multistar.cpp:984). While
            // settling, the settle monitor evaluates this dropped frame too,
            // with locked=false: the mass reject SetErrors the star
            // (guider_multistar.cpp:972), so IsLocked()/WasFound() is false.
            self.mass_checker.append(now * 1000.0, s.mass); // OBLIGATION (b): reject path
            self.distance_checker.activate(now);
            if settling {
                return self.settle_monitor_dropped_frame(now, dec_guiding);
            }
            return Action::Idle;
        }

        // 4. Jump rejection / lost-star recovery (dossier §3.2). OBLIGATION (d):
        //    feed the staleness-gated current_error_smoothed as err_smoothed,
        //    and compute small_context from the not-guiding / paused / settling
        //    / <10-frames predicate (guider_multistar.cpp _CheckDistance).
        let ra_only = !dec_guiding;
        // `mut`: RefineOffset (below) reports the refined distance to
        // `UpdateCurrentDistance` as a full 2D hypot regardless of
        // `ra_only` (dossier §4/§3.2; `guider_multistar.cpp:1026-1027`:
        // "distance = hypot(ofs->cameraOfs.X, ofs->cameraOfs.Y)"
        // unconditionally on a successful refine) — but the JUMP-CHECK
        // value computed here, used immediately below, is always the
        // PRE-refine distance (refinement only ever runs on an already-
        // accepted frame, dossier §3 step 7).
        let mut distance = if ra_only {
            camera.0.abs()
        } else {
            camera.0.hypot(camera.1)
        };
        let err_smoothed = self.avg_dist.current_error_smoothed(now, dec_guiding); // OBLIGATION (d)
        let not_guiding = self.phase != Phase::Guiding;
        let paused = false; // no host-pause channel yet (P2+ seam)
                            // `settling` (computed above) is LIVE here since the P2-T1 fix round:
                            // settling frames run this machinery too, and dossier §12's "the
                            // DistanceChecker also treats settling frames as automatically
                            // acceptable" is exactly this small_context term.
        let small_context = not_guiding || paused || settling || self.frames_since_reset < 10; // OBLIGATION (d)
        let accepted =
            self.distance_checker
                .check_distance(now, distance, err_smoothed, small_context);
        if !accepted {
            // "Recovering": frame dropped. No mass append here (upstream appends
            // only on mass-reject :984 and on accept :1027, never on this path).
            return Action::Idle;
        }

        // 5. Accepted frame. OBLIGATION (b): append the star mass on the accept
        //    path too (upstream guider_multistar.cpp:1027), after the distance
        //    check passes. The search origin advances HERE and only here on
        //    the guiding path (guider_multistar.cpp:1026, `m_primaryStar =
        //    newStar` — the mass-reject and distance-reject paths above throw
        //    before that assignment, retaining the prior origin). All of this
        //    bookkeeping runs on accepted settling frames too (upstream's
        //    UpdateCurrentPosition, incl. the avg-dist update at :1043, never
        //    pauses for the settle).
        self.mass_checker.append(now * 1000.0, s.mass); // OBLIGATION (b): accept path
        if was_acquiring {
            // `measured` on this frame came from a fresh `auto_find` pass
            // (a session's first lock, or a post-long-lost bounded
            // re-acquisition) — the secondary list it may carry replaces
            // whatever was tracked before (dossier §2.6/§4; P3-T1).
            self.rebuild_secondaries(measured);
        }
        self.search_origin = Some((s.x, s.y));
        self.last_good_find_s = Some(now);
        self.frames_since_reset += 1;

        // Multi-star offset refinement (dossier §4). Preconditions mirror
        // `guider_multistar.cpp:734` (`IsGuiding() && m_guideStars.size() >
        // 1 && GetGuidingEnabled() && !IsSettling()`): guiding is implied
        // by `Phase::Guiding` (this function's caller), "guiding enabled"
        // has no analogue in this crate's frozen host contract (assumed
        // true), `!settling` is `settling` computed above, and
        // `m_guideStars.size() > 1` is `!self.secondaries.is_empty()`.
        // `multi_star_broken` is the panic-guard latch (module doc,
        // dossier §4's "any exception... permanently drops to single-star
        // mode for the session"). Runs BEFORE `avg_dist.update` so a
        // refined offset feeds the smoothed statistics AND the move
        // pipeline (guider_multistar.cpp:1023-1034).
        if !settling && !self.multi_star_broken && !self.secondaries.is_empty() {
            let secondary_measured = &measured[1..];
            if secondary_measured.len() == self.secondaries.len() {
                let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    self.refine_multistar(camera, s.snr, secondary_measured)
                }));
                match outcome {
                    Ok(Some(new_offset)) => {
                        camera = new_offset;
                        mount = camera_to_mount(camera, &cal);
                        distance = camera.0.hypot(camera.1);
                    }
                    Ok(None) => {}
                    Err(_) => {
                        // Panic guard (dossier §4): drop to single-star for
                        // the rest of this session. `self.secondaries` may
                        // be left in a partially-mutated state by the
                        // aborted call; clearing it makes that moot.
                        self.multi_star_broken = true;
                        self.secondaries.clear();
                    }
                }
            }
        }

        let distance_ra = mount.0.abs();
        self.avg_dist.update(now, distance, distance_ra);
        self.last_snr = s.snr;
        self.push_recent(now, mount.0, mount.1);

        // 6. Settle overlay (dossier §12; P2-T1 fix round, CRITICAL).
        //    Evaluated AFTER this frame's avg-dist update so the monitor
        //    reads the freshly smoothed error — P2-T1 punch-list #1 (SETTLE
        //    INPUT FIX, binding per the P1-T7 review): STATE_SETTLE_WAIT
        //    feeds `current_guide_error()` (phdcontrol.cpp:516; the fast
        //    EMA, `Guider::CurrentError`, guider.cpp:1116-1132), never a
        //    frame's raw instantaneous offset — a single lucky in-tolerance
        //    frame must not declare settled.
        if settling {
            let err = self.avg_dist.current_error(now, dec_guiding); // P2-T1 punch-list #1
            let state = self
                .settle
                .as_mut()
                .expect("settling flag implies self.settle is Some")
                .evaluate(err, true, now);
            match state {
                SettleState::Failed(_) => {
                    self.settle = None;
                    self.recenter = None; // abandon any in-flight recenter too
                    return Action::LockLost;
                }
                SettleState::Done => {
                    // Window closed. Fall through to the ordinary move below
                    // — the settle-complete frame still guides (guiding
                    // never stopped, guider.cpp:1517-1521), and apply_move's
                    // dec-mode override reverts with the cleared window.
                    self.settle = None;
                    self.recenter = None; // normally already None; be safe
                }
                SettleState::Settling => {
                    // Fast recenter (dossier §11.2) REPLACES the ordinary
                    // guide step while active (guider.cpp:1485-1511: the
                    // recenter branch, ELSE the ordinary guide step)...
                    if let Some(a) = self.step_recenter() {
                        return a;
                    }
                    // ...otherwise GUIDE THROUGH THE DWELL (P2-T1 fix
                    // round, CRITICAL; guider.cpp:1517-1521): the ordinary
                    // ALGO correction. A vetoed/empty correction surfaces
                    // as the Settle wait signal — the host-visible "still
                    // settling" — instead of a bare Idle.
                    let a = self.compute_move(mount, s.snr, meta.exposure_s);
                    return if matches!(a, Action::Idle) {
                        Action::Settle
                    } else {
                        a
                    };
                }
            }
        }

        // 7. Move pipeline (dossier §7): algorithms -> direction/rate -> ms,
        //    static BLC, dec-mode gating, duration clamps.
        self.compute_move(mount, s.snr, meta.exposure_s)
    }

    /// The settle monitor's evaluation for a DROPPED settling frame (star
    /// not found, or a mass-change reject): no avg-dist update (upstream
    /// drops the frame before `UpdateCurrentDistance`), `locked == false`
    /// (upstream's IsLocked() is `m_primaryStar.WasFound()`,
    /// guider_multistar.h:174-177 — false after the drop path SetErrors the
    /// star, star.cpp:48-70), and the error input is the stale-gated
    /// smoothed current error (LARGE_DISTANCE once the star has been
    /// missing > 20 s — dossier §13, guider.cpp:1108-1114). While the
    /// window is open this monitor OWNS the LockLost failure path (reason
    /// classification: a settling LockLost is always settle_timeout). A
    /// dropped frame can never be Done (in-range requires locked), and no
    /// correction is possible (dead-reckoning `deduce_result` is 0), so any
    /// non-failure outcome is the Settle wait signal. An in-flight recenter
    /// does NOT step — upstream schedules no move on dropped frames.
    fn settle_monitor_dropped_frame(&mut self, now: f64, dec_guiding: bool) -> Action {
        let err = self.avg_dist.current_error(now, dec_guiding);
        let state = self
            .settle
            .as_mut()
            .expect("caller verified self.settle is Some")
            .evaluate(err, false, now);
        match state {
            SettleState::Failed(_) => {
                self.settle = None;
                self.recenter = None;
                Action::LockLost
            }
            _ => Action::Settle,
        }
    }

    /// Rebuild the secondary-star list from a fresh acquisition's
    /// `measured` output (dossier §2.6/§2.7; P3-T1): `measured[0]` is the
    /// primary, `measured[1..]` are the freshly found secondaries. Each
    /// becomes a [`SecondaryStar`] with `reference_point == (x, y)`
    /// (dossier: "set in AutoFind") and `offset_from_primary` computed
    /// against `measured[0]` — equivalent to upstream's `referencePoint -
    /// primaryRef` (`star.cpp:1108`), since `referencePoint` is freshly
    /// stamped to the just-measured position at this exact moment
    /// ([`select::primary_and_secondaries`]'s doc comment). A `measured`
    /// with fewer than 2 entries (single-star mode, or a failed
    /// acquisition) clears the list. Also resets the stabilization state
    /// machine (dossier §4) fresh, matching upstream's `SetMultiStarMode`/
    /// a fresh `AutoSelect` clearing `m_primaryDistStats` together with the
    /// secondary list (this crate has no literal analogue to port from —
    /// DERIVED, but the only choice consistent with "every full
    /// `auto_find`+`select_primary` pass replaces the whole guide-star
    /// list": stale primary-distance history from a DIFFERENT star's
    /// tracking history is not meaningful to the new one).
    fn rebuild_secondaries(&mut self, measured: &[MeasuredStar]) {
        self.primary_dist_stats = PrimaryDistStats::new();
        self.stabilizing = false;
        self.lock_position_moved = false;
        if measured.len() < 2 {
            self.secondaries.clear();
            return;
        }
        let (px, py) = (measured[0].x, measured[0].y);
        self.secondaries = measured[1..]
            .iter()
            .map(|m| SecondaryStar::new(m.x, m.y, m.snr, (m.x - px, m.y - py)))
            .collect();
    }

    /// Multi-star offset refinement for one accepted, non-settling guiding
    /// frame (dossier §4). Owns the persisted stabilization state machine
    /// (`primary_dist_stats`/`stabilizing`/`lock_position_moved`) that
    /// [`refine::refine_offset`] itself cannot — it is a pure, stateless
    /// function per this task's frozen signature (see `refine.rs`'s module
    /// doc for the full split-of-responsibility argument). Returns
    /// `Some(refined_camera_offset)` when refinement should replace the
    /// caller's `camera` offset for this frame; `None` otherwise
    /// (stabilizing, no shrink, or a lock-recovery frame that only
    /// refreshed secondary reference points).
    ///
    /// **Why the redundant gate inside [`refine::refine_offset`] never
    /// disagrees with this method's own hysteresis**: this method only
    /// calls [`refine::refine_offset`] in normal (non-recovery) mode from
    /// the `!self.stabilizing` branch below — i.e. only on a frame where
    /// THIS method has just confirmed, using the real persisted hysteresis
    /// (enter at [`refine::STABILITY_SIGMA_ENTER`] sigma, exit at
    /// [`refine::STABILITY_SIGMA_EXIT`] sigma, with memory across frames),
    /// that refinement should be attempted. [`refine::refine_offset`]'s own
    /// stateless `primary_dist > 5*sigma` re-check can therefore only ever
    /// see a primary distance already known to be within the enter
    /// threshold — it is authoritative only when [`refine::refine_offset`]
    /// is called in isolation (as this crate's golden-vector tests do,
    /// without this surrounding state machine).
    fn refine_multistar(
        &mut self,
        camera: (f64, f64),
        primary_snr: f64,
        secondary_measured: &[MeasuredStar],
    ) -> Option<(f64, f64)> {
        let primary_dist = camera.0.hypot(camera.1);
        self.primary_dist_stats.add(primary_dist);

        if self.primary_dist_stats.count() > 5 {
            let sigma = self.primary_dist_stats.sigma();
            if !self.stabilizing && primary_dist > refine::STABILITY_SIGMA_ENTER * sigma {
                self.stabilizing = true;
            } else if self.stabilizing && primary_dist <= refine::STABILITY_SIGMA_EXIT * sigma {
                self.stabilizing = false;
                if self.lock_position_moved {
                    // Lock-recovery frame (dossier §4): refresh secondary
                    // reference points only, no averaging this frame.
                    // `secondary_measured` was already re-searched using
                    // each star's [`SecondaryStar::search_position`] (see
                    // `measure()`) — the one accepted narrowing from
                    // upstream's dedicated "expected location" re-find is
                    // documented on `refine.rs`'s module doc.
                    self.lock_position_moved = false;
                    refine::refine_offset(
                        camera,
                        &mut self.secondaries,
                        secondary_measured,
                        primary_snr,
                        sigma,
                        true,
                    );
                    self.secondaries.retain(|sec| !sec.erase);
                    return None;
                }
            }

            if !self.stabilizing {
                let refined = refine::refine_offset(
                    camera,
                    &mut self.secondaries,
                    secondary_measured,
                    primary_snr,
                    sigma,
                    false,
                );
                self.secondaries.retain(|sec| !sec.erase);
                return refined;
            }
        } else {
            self.stabilizing = true;
        }
        None
    }

    /// The dossier §7 move pipeline for one accepted frame's mount-frame
    /// error: runs the per-axis algorithms, then [`apply_move`](Self::apply_move)
    /// as an ALGO move for the rest of the pipeline
    /// (direction/rate/BLC/gating/clamps).
    fn compute_move(&mut self, mount: (f64, f64), snr: f64, dt: f64) -> Action {
        // Per-axis transfer functions (dossier §6). result_with threads snr +
        // exposure for a future predictive (PPEC) axis; Hysteresis and
        // ResistSwitch ignore both and delegate to result().
        let xd = self.ra_algo.result_with(mount.0, snr, dt);
        let yd = self.dec_algo.result_with(mount.1, snr, dt);
        self.apply_move(xd, yd, false)
    }

    /// One fast-recenter step (dossier §11.2; `guider.cpp:1485-1511`), if a
    /// recenter is currently in flight. Bypasses the guide algorithms
    /// entirely — goes straight to [`apply_move`](Self::apply_move) as a
    /// RECOVERY move, matching upstream's `MOVEOPTS_RECOVERY_MOVE`
    /// (`mount.h:139`: the `MOVEOPT_USE_BLC` bit alone): BLC still applies,
    /// but dec-mode gating and the max-duration clamps do NOT (both sit
    /// inside scope.cpp's ALGO-only guard — see `apply_move`). The move is
    /// open-loop: it does not re-read the star's current position, just
    /// walks down the pre-planned `remaining` distance from
    /// [`dither`](Self::dither) one `step` at a time.
    ///
    /// Returns `None` when there is nothing to recenter (already finished,
    /// or `dither` never armed one — a zero-distance dither, dossier
    /// §11.2's div-by-zero guard).
    fn step_recenter(&mut self) -> Option<Action> {
        let mut r = self.recenter.take()?;
        let step = (r.step.0.min(r.remaining.0), r.step.1.min(r.remaining.1));
        let signed = (r.dir.0 * step.0, r.dir.1 * step.1);
        r.remaining = (r.remaining.0 - step.0, r.remaining.1 - step.1);
        if r.remaining.0 < 0.5 && r.remaining.1 < 0.5 {
            // Fast recenter is done (dossier §11.2). P2-T1 punch-list #2:
            // reconstruct a FRESH AvgDist right here — the post-fast-recenter
            // boundary (guider.cpp:1504 `m_avgDistanceNeedReset = true`,
            // reinitialized on the NEXT UpdateCurrentDistance call,
            // guider.cpp:1100-1106) — using the same "construct fresh rather
            // than mutate" technique `reset_guiding_state` uses for
            // OBLIGATION (c) (see `AvgDist`'s own doc comment: the two are
            // formula-identical, only a narrower entry point).
            // `frames_since_reset` mirrors `AvgDist`'s own `count`
            // (`m_avgDistanceCnt` / `CurrentErrorFrameCount()`, per its own
            // field doc) 1:1, so it is reset alongside — NOT via
            // `reset_guiding_state()`, which would also clear the still-open
            // `self.settle` mid-dwell and wipe `mass_checker`/
            // `distance_checker`/`last_dec_dir` state the recenter never
            // touched (see `reset_guiding_state`'s doc comment).
            self.avg_dist = AvgDist::new();
            self.frames_since_reset = 0;
        } else {
            self.recenter = Some(r);
        }
        Some(self.apply_move(signed.0, signed.1, true))
    }

    /// The dossier §7 move pipeline's post-algorithm half: direction from
    /// sign, duration from magnitude/rate, dec-mode gating, static BLC,
    /// duration clamps. Shared by the normal per-axis-algorithm path
    /// ([`compute_move`](Self::compute_move), `recovery == false`, upstream
    /// `MOVEOPTS_GUIDE_STEP`) and the fast-recenter path
    /// ([`step_recenter`](Self::step_recenter), `recovery == true`, upstream
    /// `MOVEOPTS_RECOVERY_MOVE`). `xd`/`yd` are mount-frame pixel
    /// corrections (post-algorithm for the normal path; the raw signed
    /// recenter step for the fast-recenter path).
    ///
    /// The `recovery` flag models upstream's moveOptions bits (P2-T1 fix
    /// round): dec-mode gating (scope.cpp:727-734) and the max-duration
    /// clamps (scope.cpp:736-741 dec, :761-768 RA) are BOTH inside the
    /// `moveOptions & (MOVEOPT_ALGO_RESULT | MOVEOPT_ALGO_DEDUCE)` guard,
    /// and `MOVEOPTS_RECOVERY_MOVE` carries neither bit (`mount.h:139` — it
    /// is `MOVEOPT_USE_BLC` alone), so recovery moves bypass both while BLC
    /// still applies. Returns [`Action::PulsePair`] (or [`Action::Idle`]
    /// when both axes are vetoed/clamped to zero).
    fn apply_move(&mut self, xd: f64, yd: f64, recovery: bool) -> Action {
        let cal = self.cal.expect("guiding phase implies a valid Cal");

        // Effective dec guide mode (dossier §12 note; P2-T1 fix round,
        // Minor): while a dither settle window is open, upstream temporarily
        // sets a uni-directional DEC_NORTH/DEC_SOUTH mode to DEC_AUTO for
        // the whole settle and restores it after
        // (phdcontrol.cpp:181-187 arms the override, :501-505 sets
        // DEC_AUTO at STATE_SETTLE_BEGIN, :570-575 restores), so the
        // dither's dec displacement can be guided back out in either
        // direction. Off is NOT overridden — an Off-mode dither is forced
        // RA-only at the source instead (see [`dither`](Self::dither)),
        // matching phdcontrol.cpp:188-196.
        let dec_mode = if self.settle.is_some()
            && matches!(self.cfg.dec_guide_mode, DecMode::North | DecMode::South)
        {
            DecMode::Auto
        } else {
            self.cfg.dec_guide_mode
        };

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
        // dec; North blocks a SOUTH pulse; South blocks a NORTH pulse. ALGO
        // moves only (P2-T1 fix round, Minor): upstream's scope.cpp:727 guard
        // exempts recovery moves, so a fast-recenter dec pulse fires
        // regardless of mode.
        if !recovery {
            match dec_mode {
                DecMode::Off => y_ms = 0,
                DecMode::North if ydir == Direction::South => y_ms = 0,
                DecMode::South if ydir == Direction::North => y_ms = 0,
                _ => {}
            }
        }

        // Static backlash compensation (dossier §10.1; D4: static only,
        // adaptive controller OFF). Only in (effective) DEC_AUTO, only with a
        // configured seed pulse, only when the dec correction is non-zero
        // (mirroring apply()'s early returns, which also skip updating
        // last_dir on a zero move). Adds one fixed pulse on a dec direction
        // reversal. Applies to recovery moves too — MOVEOPT_USE_BLC is the
        // one bit MOVEOPTS_RECOVERY_MOVE carries (mount.h:139).
        let mut eff_max_dec = self.cfg.max_dec_duration_ms as i64;
        if dec_mode == DecMode::Auto && self.cfg.blc_pulse_ms > 0 && yd != 0.0 {
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

        // MoveAxis duration clamps (dossier §7/§14): ALGO moves only (P2-T1
        // fix round, Important) — the same scope.cpp guard (:736-741 dec,
        // :761-768 RA) exempts recovery moves, whose deliberately large
        // recenter steps go out unclamped.
        if !recovery {
            x_ms = x_ms.clamp(0, self.cfg.max_ra_duration_ms as i64);
            y_ms = y_ms.clamp(0, eff_max_dec);
        }

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
    ///
    /// **Multi-star (dossier §2.6/§4; P3-T1)**: the returned `Vec` is
    /// `[primary, secondary_0, secondary_1, ...]` whenever
    /// `self.secondaries` is non-empty (index-aligned to it) — see
    /// [`ingest_guiding`](Self::ingest_guiding) and
    /// [`refine_multistar`](Self::refine_multistar) for how the secondaries
    /// are consumed. Each secondary is re-measured at
    /// [`SecondaryStar::search_position`] (own last position, or
    /// `primary + offset_from_primary` if it was lost last frame) using
    /// the JUST-FOUND primary's fresh position — this is the same "search
    /// where last seen" strategy dossier §4's per-frame loop uses
    /// (`guider_multistar.cpp:802-810`). It is deliberately used
    /// UNCONDITIONALLY here, even during a stabilization period or on the
    /// one frame that should use upstream's dedicated lock-recovery
    /// "expected location" re-find instead
    /// (`guider_multistar.cpp:762-791`): this method is `&self` (pure, no
    /// mutation — an established P1/P2 contract this task does not
    /// change), so it has no access to `self.stabilizing`/
    /// `self.lock_position_moved`, which only `ingest`'s `&mut self` call
    /// updates. The accepted consequence (DERIVED, not upstream-literal):
    /// on the one frame stabilization exits after a dither, a secondary
    /// whose pre-dither position is now well outside its search window may
    /// come back `not found` here and get marked `was_lost` by
    /// [`refine_multistar`]'s lock-recovery branch instead of being
    /// instantly relocated — it then self-heals via the ordinary
    /// `was_lost` branch of [`SecondaryStar::search_position`] on the VERY
    /// NEXT frame, i.e. at most one extra frame of delay, never a stuck or
    /// incorrect state.
    pub fn measure(&self, frame: &astro_star::GrayFrame) -> Vec<MeasuredStar> {
        match self.search_origin {
            Some((lx, ly)) => {
                let r = star_find(frame, lx, ly, &self.cfg.find);
                let primary = MeasuredStar {
                    x: r.x,
                    y: r.y,
                    snr: r.snr,
                    mass: r.mass,
                    hfd: r.hfd,
                    found: was_found(r.result),
                };
                let mut out = Vec::with_capacity(1 + self.secondaries.len());
                let primary_pos = (primary.x, primary.y);
                out.push(primary);
                for sec in &self.secondaries {
                    let (sx, sy) = sec.search_position(primary_pos);
                    let rs = star_find(frame, sx, sy, &self.cfg.find);
                    out.push(MeasuredStar {
                        x: rs.x,
                        y: rs.y,
                        snr: rs.snr,
                        mass: rs.mass,
                        hfd: rs.hfd,
                        found: was_found(rs.result),
                    });
                }
                out
            }
            None => {
                let sp = SelectParams {
                    search_region: self.cfg.find.search_region,
                    max_stars: self.cfg.max_stars,
                    ..SelectParams::default()
                };
                let cands = auto_find(frame, &sp);
                let peaks: Vec<(i32, i32)> =
                    cands.iter().map(|c| (c.x as i32, c.y as i32)).collect();
                let sat = saturation_threshold(frame, &peaks, &self.cfg.find);
                match select_primary(&cands, sat, sp.af_min_snr) {
                    Some(i) if sp.max_stars > 1 => {
                        let (primary, secondaries) =
                            primary_and_secondaries(&cands, i, sp.max_stars);
                        let mut out = Vec::with_capacity(1 + secondaries.len());
                        out.push(MeasuredStar {
                            x: primary.x,
                            y: primary.y,
                            snr: primary.snr,
                            mass: primary.mass,
                            hfd: primary.hfd,
                            found: true,
                        });
                        out.extend(secondaries.iter().map(|c| MeasuredStar {
                            x: c.x,
                            y: c.y,
                            snr: c.snr,
                            mass: c.mass,
                            hfd: c.hfd,
                            found: true,
                        }));
                        out
                    }
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

    /// Dither by a mount-frame offset (px; dossier §11.2 `MoveLockPosition`,
    /// `guider.cpp:838-929`). Shifts the lock position (`mount_to_camera`),
    /// resets the axis algorithms (`GuidingDithered` -> `reset()`),
    /// immediately inflates the guide-error statistics by the dither
    /// distance, arms a fast-recenter walk back toward the new lock (dossier
    /// §11.2 `FastRecenter`, default-on with no disable knob in this crate's
    /// frozen [`EngineConfig`]), and opens a settle window (dossier §12).
    /// While the window is open, [`ingest`](Self::ingest) returns
    /// fast-recenter [`Action::PulsePair`]s (dossier §11.2's
    /// `GuidingDitherSettleDone` "notify algorithms of a direct move" step
    /// has no analogue in this crate's frozen [`GuideAlgorithm`] trait — see
    /// `step_recenter`'s doc comment), then GUIDES THROUGH THE DWELL
    /// (P2-T1 fix round; `guider.cpp:1517-1521` — settle is a parallel
    /// monitor, not a phase that suspends guiding): ordinary corrections
    /// keep flowing, with [`Action::Settle`] standing in for frames whose
    /// correction is vetoed/empty, until the window closes (the ordinary
    /// correction on the Done frame, [`Action::LockLost`] on timeout).
    ///
    /// A no-op when there is no calibration or no established lock yet (the
    /// host only dithers while guiding, so this never fires live).
    pub fn dither(&mut self, dx_px: f64, dy_px: f64) {
        let (Some(cal), Some(lock)) = (self.cal, self.lock) else {
            return;
        };

        // DEC_OFF forces an RA-only dither (dossier §11.1 "forced true if
        // dec guide mode makes dec dithering impossible" + the §12 note;
        // phdcontrol.cpp:188-196: DEC_NONE forces raOnly): with dec guiding
        // off the dec displacement could never be guided back out, so it
        // must not be applied. North/South modes keep their dy — they get
        // the temporary DEC_AUTO override instead (see `apply_move`).
        let dy_px = if self.cfg.dec_guide_mode == DecMode::Off {
            0.0
        } else {
            dy_px
        };

        // ADJUDICATION (dossier §11.2 "4-sign validity search",
        // `guider.cpp:864-895`): upstream tries the 4 sign combinations of
        // the requested mount delta and keeps whichever produces a lock
        // position at least `search_region+1` inside the CAMERA FRAME,
        // falling back to the combination farthest from the nearest edge.
        // That check needs the frame's pixel bounds, which this crate's
        // frozen `dither(dx_px, dy_px)` signature never receives (D1: no
        // camera-loop machinery in Rust — `GuideEngine` never stores frame
        // width/height; [`measure`](Self::measure) only borrows a
        // `GrayFrame` for the duration of one call). Established ruling
        // "clean-bounds require unobservability proof": with no frame size
        // observable at this call, the 4-sign search has nothing to
        // validate against, so it degenerates to the literal (unflipped)
        // request — the only choice definable without a size the engine
        // cannot see. A host that must avoid pushing the star off-frame
        // sizes its dither amount conservatively before calling this (a
        // host-side, not engine-side, concern).
        let mount_delta = (dx_px, dy_px);
        let camera_delta = mount_to_camera(mount_delta, &cal);
        self.lock = Some((lock.0 + camera_delta.0, lock.1 + camera_delta.1));

        // GuidingDithered -> reset() (dossier §11.1/§11.2).
        self.ra_algo.reset();
        self.dec_algo.reset();

        // Multi-star (dossier §4): "A dither sets lock_position_moved =
        // true and stabilizing = true" (`SetLockPosition` override,
        // `guider_multistar.cpp:766`/`1183`-ish path via the lock-position
        // setter). No-op (harmlessly) when `self.secondaries` is empty.
        self.lock_position_moved = true;
        self.stabilizing = true;

        // Immediately inflate the error statistics by the dither distance
        // (dossier §11.2; `guider.cpp:903-908`, "update average distance
        // right away so GetCurrentDistance reflects the increased distance
        // from the dither") so settle logic — and anyone polling `stats()`
        // right after `dither()` returns, before the next frame — sees the
        // displacement at once. Camera- and mount-frame deltas have the same
        // magnitude (`mount_to_camera` is a rotation, possibly with an
        // axis-reversal flip — neither changes length), so `dist` below
        // equals both `camera_delta`'s and `mount_delta`'s hypot.
        let dist = dx_px.hypot(dy_px);
        let dist_ra = dx_px.abs();
        self.avg_dist.inflate(dist, dist_ra);

        // Fast recenter (dossier §11.2). Zero-distance dithers (a
        // settle-only trigger, `guider.cpp:910-912`) skip it to avoid the
        // div-by-zero in `f`.
        self.recenter = if dist != 0.0 {
            let remaining = (mount_delta.0.abs(), mount_delta.1.abs());
            let dir = (
                if mount_delta.0 < 0.0 { 1.0 } else { -1.0 },
                if mount_delta.1 < 0.0 { 1.0 } else { -1.0 },
            );
            // "make each step a bit less than the full search region
            // distance to avoid losing the star" (guider.cpp:917).
            let max_move_px = self.cfg.find.search_region as f64;
            let f = 0.7 * max_move_px / dist;
            let step = (f * remaining.0, f * remaining.1);
            Some(Recenter {
                remaining,
                step,
                dir,
            })
        } else {
            None
        };

        self.settle = Some(Settle::new(
            DEFAULT_SETTLE_TOL_PX,
            DEFAULT_SETTLE_TIME_S,
            DEFAULT_SETTLE_TIMEOUT_S,
        ));
    }

    /// Whether a settle window (dither or a future start-of-guiding settle)
    /// is currently open (dossier §12). P2-T1 punch-list #3: hosts that need
    /// the authoritative settle lifecycle use this (or the equivalent
    /// [`GuideStatsSnapshot::settling`]) rather than inferring it from a
    /// single frame's [`Action`] — fast-recenter frames (dossier §11.2) and
    /// the dwell's guide-correction frames (P2-T1 fix round,
    /// `guider.cpp:1517-1521`) both return a normal [`Action::PulsePair`]
    /// while the window stays open.
    pub fn is_settling(&self) -> bool {
        self.settle.is_some()
    }

    /// Current guide-error statistics in the host's `GuideStats` shape
    /// (spec §3.2/§3.5). RMS is over the retained recent-frame window.
    pub fn stats(&self) -> GuideStatsSnapshot {
        let guiding = self.phase == Phase::Guiding && !self.is_settling();
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
            settling: self.settle.is_some(),
            rms_ra: rms(sra),
            rms_dec: rms(sdec),
            rms_total: rms(stot),
            snr: self.last_snr,
            recent: self.recent.iter().copied().collect(),
            secondaries: self.secondaries.iter().map(|s| (s.x, s.y)).collect(),
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

#[cfg(test)]
mod tests {
    use super::*;

    // Provenance: direct unit coverage of `refine_multistar`'s PERSISTED
    // stabilization state machine (dossier §4; `m_stabilizing` /
    // `m_primaryDistStats` / `m_lockPositionMoved`,
    // guider_multistar.cpp:744-799) — the review's top thin item on P3-T1:
    // `refine_offset`'s own golden vectors only exercise the STATELESS
    // sigma-sentinel re-check, never the engine's real hysteresis with
    // memory across frames. These tests drive `refine_multistar` directly
    // (in-module, private access) with hand-seeded `PrimaryDistStats`
    // state so every threshold crossing is an exact hand-traced Welford
    // literal, per the crate's golden-vector style.

    /// One well-behaved secondary at reference (200, 50), SNR 20 (equal to
    /// the tests' primary SNR, so its weight is exactly 1.0).
    fn one_secondary() -> Vec<SecondaryStar> {
        vec![SecondaryStar::new(200.0, 50.0, 20.0, (100.0, -50.0))]
    }

    /// Its per-frame re-measurement: displacement (+0.05, +0.05) from the
    /// reference point — nonzero on both axes (zero-count gate silent),
    /// hypot ~0.0707 (far inside every excursion gate these tests reach).
    fn small_move() -> Vec<MeasuredStar> {
        vec![MeasuredStar {
            x: 200.05,
            y: 50.05,
            snr: 20.0,
            mass: 1000.0,
            hfd: 3.0,
            found: true,
        }]
    }

    /// Engine with `primary_dist_stats` seeded to a KNOWN state: 100
    /// samples alternating 0.9/1.1 -> mean exactly 1.0, M2 exactly 1.0
    /// (Welford is order-exact for the final sum of squared deviations),
    /// sample sigma sqrt(1/99) ~ 0.100504. `stabilizing` starts false.
    fn seeded_engine() -> GuideEngine {
        let mut e = GuideEngine::new(EngineConfig::default());
        e.secondaries = one_secondary();
        for _ in 0..50 {
            e.primary_dist_stats.add(0.9);
            e.primary_dist_stats.add(1.1);
        }
        e
    }

    /// Collection phase (dossier §4 `else { m_stabilizing = true }`,
    /// guider_multistar.cpp:790): with 5 or fewer samples every call
    /// returns None and forces `stabilizing`, and each call still feeds
    /// the running stats.
    #[test]
    fn stabilization_collects_first_five_samples() {
        let mut e = GuideEngine::new(EngineConfig::default());
        e.secondaries = one_secondary();
        assert!(!e.stabilizing, "fresh engine starts non-stabilizing");
        for i in 1..=5u64 {
            let out = e.refine_multistar((1.0, 0.0), 20.0, &small_move());
            assert_eq!(out, None, "call {i}: still collecting");
            assert!(e.stabilizing, "call {i}: collection forces stabilizing");
            assert_eq!(e.primary_dist_stats.count(), i);
        }
    }

    /// 5-sigma ENTRY (guider_multistar.cpp:749-753): a non-stabilizing
    /// engine whose primary distance exceeds 5x the (post-add) sample
    /// sigma enters stabilization and skips refinement that same frame.
    /// Hand trace: seeded stats (count 100, mean 1.0, M2 1.0) + this
    /// call's 2.0 -> count 101, new mean 1 + 1/101, M2' = 1 +
    /// (2-1)(2 - 1.009901) = 1.990099, sigma = sqrt(1.990099/100) =
    /// 0.141071; 5*sigma = 0.705356 < 2.0 -> enter.
    #[test]
    fn stabilization_enters_at_five_sigma_excursion() {
        let mut e = seeded_engine();
        let out = e.refine_multistar((2.0, 0.0), 20.0, &small_move());
        assert_eq!(out, None);
        assert!(e.stabilizing, "5-sigma excursion must enter stabilization");
    }

    /// HYSTERESIS MEMORY between 2 and 5 sigma (the review's exact thin
    /// spot): the SAME seeded stats and the SAME 0.5px distance produce
    /// OPPOSITE outcomes depending only on the persisted `stabilizing`
    /// flag. Hand trace of the shared sigma: seeded + 0.5 -> count 101,
    /// new mean 1 - 0.5/101 = 0.995050, M2' = 1 + (0.5-1)(0.5-0.995050)
    /// = 1.247525, sigma = sqrt(1.247525/100) = 0.111693; 2*sigma =
    /// 0.223387 < 0.5 < 5*sigma = 0.558467 — inside the hysteresis band:
    /// too large to EXIT, too small to ENTER.
    #[test]
    fn stabilization_memory_persists_between_two_and_five_sigma() {
        // Already stabilizing: 0.5 does not exit -> None, flag persists.
        let mut stab = seeded_engine();
        stab.stabilizing = true;
        let out = stab.refine_multistar((0.5, 0.0), 20.0, &small_move());
        assert_eq!(out, None, "inside the band, a stabilizing engine stays");
        assert!(stab.stabilizing, "the flag is MEMORY, not a per-frame test");

        // Not stabilizing: the identical distance does not enter, so the
        // same frame refines. Weighted average (weight 1.0):
        // ((0.5 + 0.05)/2, 0.05/2) = (0.275, 0.025), hypot 0.276 < 0.5.
        let mut calm = seeded_engine();
        let out = calm.refine_multistar((0.5, 0.0), 20.0, &small_move());
        let (rx, ry) = out.expect("non-stabilizing engine refines in the band");
        assert!((rx - 0.275).abs() < 1e-9, "rx={rx}");
        assert!((ry - 0.025).abs() < 1e-9, "ry={ry}");
        assert!(!calm.stabilizing);
    }

    /// 2-sigma EXIT with same-frame fall-through
    /// (guider_multistar.cpp:755-760 exit, then the same invocation runs
    /// the secondary loop at :800): the exit frame itself refines — no
    /// dead frame between exit and first refinement. Hand trace: seeded +
    /// 0.2 -> count 101, new mean 1 - 0.8/101 = 0.992079, M2' = 1 +
    /// (0.2-1)(0.2-0.992079) = 1.633663, sigma = sqrt(1.633663/100) =
    /// 0.127815; 2*sigma = 0.255629 >= 0.2 -> exit; refined = ((0.2 +
    /// 0.05)/2, 0.05/2) = (0.125, 0.025), hypot 0.127 < 0.2 -> Some.
    #[test]
    fn stabilization_exits_at_two_sigma_and_refines_same_frame() {
        let mut e = seeded_engine();
        e.stabilizing = true;
        let out = e.refine_multistar((0.2, 0.0), 20.0, &small_move());
        let (rx, ry) = out.expect("the exit frame must fall through to refinement");
        assert!((rx - 0.125).abs() < 1e-9, "rx={rx}");
        assert!((ry - 0.025).abs() < 1e-9, "ry={ry}");
        assert!(!e.stabilizing, "exited");
    }

    /// 2-sigma exit with `lock_position_moved` armed (a dither happened;
    /// guider_multistar.cpp:761-791): the exit frame consumes the flag,
    /// refreshes every secondary's reference point from this frame's
    /// measurement, and does NOT refine (upstream `return false`).
    #[test]
    fn stabilization_exit_with_lock_moved_recovers_secondaries_without_refining() {
        let mut e = seeded_engine();
        e.stabilizing = true;
        e.lock_position_moved = true;
        let recovered = vec![MeasuredStar {
            x: 210.0,
            y: 40.0,
            snr: 20.0,
            mass: 1000.0,
            hfd: 3.0,
            found: true,
        }];
        let out = e.refine_multistar((0.2, 0.0), 20.0, &recovered);
        assert_eq!(out, None, "the lock-recovery frame never refines");
        assert!(!e.stabilizing, "still exits stabilization");
        assert!(!e.lock_position_moved, "the flag is consumed");
        assert_eq!(
            e.secondaries[0].reference_point,
            (210.0, 40.0),
            "reference point refreshed from the recovery measurement"
        );
        assert!(!e.secondaries[0].was_lost);
    }
}
