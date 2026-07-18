// SPDX-License-Identifier: Apache-2.0
//
// Provenance: frame-to-frame tracking primitives from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§3.1, §3.2, §13).
// Derived from PHD2 guider_multistar.cpp:52-180 (`MassChecker`),
// guider_multistar.cpp:601-704 (`DistanceChecker`), and guider.cpp:1065-1139
// (`Guider::UpdateCurrentDistance` / `CurrentError`) (BSD-3-Clause; see
// THIRD-PARTY-NOTICES.md). [`MassChecker`] and [`DistanceChecker`] port
// their state machines literally per this task's brief ("port exception for
// the state machines"); [`AvgDist`] is a clean-room reimplementation with a
// scope-narrowed `update()` interface (see its doc comment). No code copied
// from PHD2.

//! Per-frame tracking building blocks: star-mass gating, jump-rejection /
//! lost-star recovery, and the guide-error EMAs (dossier §3, §13).
//!
//! These are the primitives `GuiderMultiStar::UpdateCurrentPosition`
//! (dossier §3 intro) is built from; assembling them into the full
//! per-frame update (star-find -> mass check -> distance check -> offset)
//! is the guide engine's job (a later task), not this module's. Each type
//! here holds only the state its own algorithm needs and performs no I/O.

use std::cell::Cell;

/// Rolling time-window median filter on star mass (dossier §3.1;
/// `MassChecker`, `guider_multistar.cpp:52-180`).
///
/// **`check` takes `&self`, not `&mut self`** (this task's frozen
/// interface), yet upstream's `CheckMass` updates the high/low water marks
/// on every call — the "let the low water mark drift to follow the
/// median" behavior (`guider_multistar.cpp:140-142`) is only meaningful as
/// state that persists and drifts across many real calls. This port
/// reconciles the two with interior mutability: `high_water`/`low_water`
/// are `Cell<f64>`, updated inside `check` exactly as upstream's non-const
/// `CheckMass` updates `m_highMass`/`m_lowMass`, while the method signature
/// stays `&self` as specified. Adjudication citations: dossier §3.1 +
/// `guider_multistar.cpp:132-142` (the mutating water-mark update inside
/// `CheckMass`) vs. this task's brief Interfaces section (`check(&self,
/// ...)`), resolved per the P1-T1/T3 precedent of favoring the literal
/// upstream *behavior* (persistent, drifting water marks) over a
/// surface-level Rust borrow-checker mismatch.
///
/// **Scope note**: upstream also supports an auto-exposure mode that
/// normalizes mass by exposure time (`AdjustedMass`,
/// `guider_multistar.cpp:100-102`) and resets history on an exposure-mode
/// change (`SetExposure`, `guider_multistar.cpp:88-99`). This task's
/// frozen `MassChecker` interface has no exposure knobs, so this port
/// always runs the fixed-exposure path (`AdjustedMass` is the identity).
#[derive(Debug, Clone)]
pub struct MassChecker {
    /// `m_timeWindow` (dossier: window stored x2 as the drop horizon).
    time_window_ms: f64,
    /// `m_data`: `(timestamp_ms, mass)`, oldest first.
    history: Vec<(f64, f64)>,
    /// `m_highMass`, high-water mark. Interior mutability — see the type
    /// doc comment.
    high_water: Cell<f64>,
    /// `m_lowMass`, low-water mark. Interior mutability — see the type
    /// doc comment.
    low_water: Cell<f64>,
}

impl MassChecker {
    /// `window_ms` is the base time window (dossier default `22500.0`);
    /// stored doubled as the drop horizon (`SetTimeWindow`,
    /// `guider_multistar.cpp:83-86`: "an abrupt change in mass will affect
    /// the median after approx `m_timeWindow`/2").
    pub fn new(window_ms: f64) -> Self {
        MassChecker {
            time_window_ms: window_ms * 2.0,
            history: Vec::new(),
            high_water: Cell::new(0.0),
            low_water: Cell::new(9e99),
        }
    }

    /// Append a new mass sample at `t_ms`, first dropping any history entry
    /// older than `t_ms - time_window` (dossier §3.1; `AppendData`,
    /// `guider_multistar.cpp:104-114`). Trimming uses `t_ms` — the entry
    /// about to be appended — as the reference "now", matching upstream's
    /// order (trim, then push).
    pub fn append(&mut self, t_ms: f64, mass: f64) {
        let cutoff = t_ms - self.time_window_ms;
        self.history.retain(|&(t, _)| t >= cutoff);
        self.history.push((t_ms, mass));
    }

    /// `true` = accept this frame's star, `false` = reject (upstream's
    /// `CheckMass` returns `reject`; this is its logical negation per the
    /// brief's stated contract). Fewer than 5 history samples always
    /// accepts (dossier §3.1; `guider_multistar.cpp:126-127`).
    pub fn check(&self, mass: f64, threshold: f64) -> bool {
        if self.history.len() < 5 {
            return true;
        }

        // median: the `history.len()/2`-th order statistic ascending —
        // upstream's `std::nth_element(..., mid, ...)` with integer
        // `mid = size/2` (`guider_multistar.cpp:135-138`). For an
        // even-sized history this is the *upper* of the two middle
        // values, not their average.
        let mut masses: Vec<f64> = self.history.iter().map(|&(_, m)| m).collect();
        let mid = masses.len() / 2;
        masses.select_nth_unstable_by(mid, |a, b| {
            a.partial_cmp(b).expect("star mass is never NaN")
        });
        let med = masses[mid];

        let mut high = self.high_water.get();
        let mut low = self.low_water.get();
        if med > high {
            high = med;
        }
        if med < low {
            low = med;
        }
        // drift the low water mark back up toward the median (recovers
        // after a period of clouds depressed it), guider_multistar.cpp:140-142.
        low += 0.05 * (med - low);
        self.high_water.set(high);
        self.low_water.set(low);

        let lim0 = low * (1.0 - threshold);
        let lim2 = high * (1.0 + threshold);
        // spike guard: still reject a large excursion even while the water
        // marks are themselves depressed by sky conditions.
        let lim3 = med * (1.0 + 2.0 * threshold);

        let reject = mass < lim0 || mass > lim2 || mass > lim3;
        !reject
    }

    /// Clear history and water marks (dossier §3.1 `Reset`;
    /// `guider_multistar.cpp:176-180`). Called by the engine on star
    /// (re)selection, auto-find, or an exposure-mode change.
    pub fn reset(&mut self) {
        self.history.clear();
        self.high_water.set(0.0);
        self.low_water.set(9e99);
    }
}

/// `DistanceChecker`'s tracking state (dossier §3.2;
/// `guider_multistar.cpp:601-608`'s `enum State`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrackState {
    Guiding,
    Waiting,
    Recovering,
}

/// How long a large offset is tolerated before recovery begins (dossier
/// §3.2 `WAIT_INTERVAL_MS`), in seconds — this crate's clock unit.
const WAIT_INTERVAL_S: f64 = 5.0;

/// Tolerance forced by [`DistanceChecker::activate`] until the checker
/// returns to `Guiding` (dossier §3.2).
const ACTIVATED_TOLERANCE: f64 = 2.0;

/// Base tolerance when nothing has forced a different value (dossier §3.2:
/// `tolerate_jumps_enabled ? threshold : 9e99`). This task's frozen
/// `DistanceChecker::new()` has no tolerate-jumps setter (default
/// **disabled**, per dossier §15 `/guider/onestar/TolerateJumpsEnabled` =
/// false), so the base tolerance is fixed at the disabled sentinel; a
/// config knob for the enabled/4.0 case is out of this task's scope.
const DEFAULT_BASE_TOLERANCE: f64 = 9e99;

/// Jump rejection / lost-star recovery state machine (dossier §3.2;
/// `DistanceChecker`, `guider_multistar.cpp:601-704`). Literal port of the
/// state machine (this task's brief: "port exception for the state
/// machines").
///
/// The caller (a future guide-engine task) is responsible for the "small
/// offset" preconditions dossier §3.2 lists as always-small (not guiding /
/// paused / settling / fewer than 10 accepted frames since reset) — this
/// type has no access to that guider-level bookkeeping (pure-computation
/// crate contract), so [`check_distance`](Self::check_distance) takes the
/// precomputed result as `small_context`.
#[derive(Debug, Clone)]
pub struct DistanceChecker {
    state: TrackState,
    /// Absolute time (seconds) the current `Waiting` period expires.
    expires_s: f64,
    /// `m_forceTolerance`: `0.0` sentinel = not forced, matching upstream's
    /// `if (m_forceTolerance != 0.)` check (`guider_multistar.cpp:651`).
    /// Upstream leaves this field uninitialized until the first `Activate`
    /// call; this port explicitly initializes it to the sentinel in `new`
    /// (the P1-T2 `norm_angle` precedent: honor the stated contract, don't
    /// reproduce an incidental uninitialized-read artifact).
    forced_tolerance: f64,
}

impl DistanceChecker {
    pub fn new() -> Self {
        DistanceChecker {
            state: TrackState::Guiding,
            expires_s: 0.0,
            forced_tolerance: 0.0,
        }
    }

    /// Star lost or mass-change reject: if currently `Guiding`, move to
    /// `Waiting` with a fresh 5s expiry and force `tolerance = 2.0` until
    /// the checker returns to `Guiding` (dossier §3.2; `Activate`,
    /// `guider_multistar.cpp:614-622`). A no-op outside `Guiding`
    /// (upstream's `if (m_state == ST_GUIDING)` guard).
    pub fn activate(&mut self, now_s: f64) {
        if self.state == TrackState::Guiding {
            self.state = TrackState::Waiting;
            self.expires_s = now_s + WAIT_INTERVAL_S;
            self.forced_tolerance = ACTIVATED_TOLERANCE;
        }
    }

    /// One frame's jump check. `distance` is this frame's offset from the
    /// lock position (pixels); `err_smoothed` is the caller-supplied
    /// long-run smoothed guide error (dossier §13's `avg_long`/
    /// `avg_long_ra`, i.e. [`AvgDist::current_error_smoothed`]);
    /// `small_context` bundles the always-small preconditions (see the
    /// type doc comment). Returns `true` = accept this frame, `false` =
    /// reject (dossier §3.2; `CheckDistance`,
    /// `guider_multistar.cpp:650-687`).
    pub fn check_distance(
        &mut self,
        now_s: f64,
        distance: f64,
        err_smoothed: f64,
        small_context: bool,
    ) -> bool {
        let tolerance = if self.forced_tolerance != 0.0 {
            self.forced_tolerance
        } else {
            DEFAULT_BASE_TOLERANCE
        };
        // `_CheckDistance` (guider_multistar.cpp:632-648): small_context
        // covers its early `return true` conditions; otherwise small iff
        // distance <= tolerance * err_smoothed.
        let small = small_context || distance <= tolerance * err_smoothed;

        match self.state {
            TrackState::Guiding => {
                if small {
                    true
                } else {
                    self.state = TrackState::Waiting;
                    self.expires_s = now_s + WAIT_INTERVAL_S;
                    false
                }
            }
            TrackState::Waiting => {
                if small {
                    self.state = TrackState::Guiding;
                    self.forced_tolerance = 0.0;
                    true
                } else if now_s < self.expires_s {
                    false
                } else {
                    // timed out: falls through into the Recovering case in
                    // the same call (upstream's switch fallthrough,
                    // guider_multistar.cpp:679-687). `small` is guaranteed
                    // false here (the `if small` branch above already
                    // returned), so this mirrors upstream's dead branch
                    // faithfully without re-deriving it.
                    self.state = TrackState::Recovering;
                    if small {
                        self.state = TrackState::Guiding;
                    }
                    true
                }
            }
            TrackState::Recovering => {
                if small {
                    self.state = TrackState::Guiding;
                }
                true
            }
        }
    }

    pub fn state(&self) -> TrackState {
        self.state
    }
}

impl Default for DistanceChecker {
    fn default() -> Self {
        Self::new()
    }
}

/// Threshold (seconds) beyond which [`AvgDist::current_error`] and
/// [`AvgDist::current_error_smoothed`] report `LARGE_DISTANCE` regardless
/// of the last real sample (dossier §13 `THRESHOLD_SECONDS`;
/// `guider.cpp:1113`).
const THRESHOLD_SECONDS: f64 = 20.0;

/// Sentinel large-error value reported when no star has been found
/// recently (dossier §13 `LARGE_DISTANCE`; `guider.cpp:1114`).
const LARGE_DISTANCE: f64 = 100.0;

/// Guide-error EMAs (dossier §13; `Guider::UpdateCurrentDistance` /
/// `CurrentError` / `CurrentErrorSmoothed`, `guider.cpp:1062-1139`).
///
/// Tracks a fast EMA (`avg_dist`/`avg_dist_ra`, alpha 0.3 — settling /
/// "current error") and a slow EMA (`avg_long`/`avg_long_ra`, mean-seeded
/// for the first 10 samples then alpha 0.045 — the DistanceChecker
/// tolerance baseline), each split into a full 2D-distance variant and an
/// RA-only variant.
///
/// **Scope note**: upstream's `UpdateCurrentDistance` branches on an
/// external `IsGuiding()` flag (raw-sample-only, no smoothing, while not
/// yet guiding) and a separate `m_avgDistanceNeedReset` flag, both of
/// which reduce to the *identical* reinitialization formula
/// (`m_avgDistance = m_avgDistanceLong = distance; ...; m_avgDistanceCnt =
/// 1;`, `guider.cpp:1088-1090` / `1096-1101`). This task's frozen
/// `update(now_s, dist, dist_ra)` interface has no guiding/reset flags, so
/// this port collapses both to "the first `update` call after
/// construction reinitializes"; every call after that runs upstream's
/// `IsGuiding()` EMA branch unconditionally. A caller that needs upstream's
/// full pre-guiding/need-reset semantics constructs a fresh `AvgDist`
/// (`new`) at the point a real reset should occur — the two are formula-
/// identical, so this is not a behavior loss, only a narrower entry point.
#[derive(Debug, Clone, Copy)]
pub struct AvgDist {
    avg_dist: f64,
    avg_dist_ra: f64,
    avg_long: f64,
    avg_long_ra: f64,
    count: u32,
    /// `m_starFoundTimestamp`. `None` == upstream's `0` sentinel ("never
    /// found a star"), which alone forces `LARGE_DISTANCE`
    /// (`guider.cpp:1108-1111`).
    last_update_s: Option<f64>,
}

impl AvgDist {
    pub fn new() -> Self {
        AvgDist {
            avg_dist: 0.0,
            avg_dist_ra: 0.0,
            avg_long: 0.0,
            avg_long_ra: 0.0,
            count: 0,
            last_update_s: None,
        }
    }

    /// Feed one accepted frame's distance-from-lock (`dist`, full 2D
    /// pixels) and RA-only distance (`dist_ra`, pixels) at `now_s`
    /// (dossier §13; `UpdateCurrentDistance`, `guider.cpp:1062-1101`).
    pub fn update(&mut self, now_s: f64, dist: f64, dist_ra: f64) {
        self.last_update_s = Some(now_s);

        if self.count == 0 {
            // First sample: hard reinitialize (collapses upstream's
            // "not yet guiding" and "need reset" branches — see the type
            // doc's scope note).
            self.avg_dist = dist;
            self.avg_dist_ra = dist_ra;
            self.avg_long = dist;
            self.avg_long_ra = dist_ra;
            self.count = 1;
            return;
        }

        const ALPHA: f64 = 0.3;
        self.avg_dist += ALPHA * (dist - self.avg_dist);
        self.avg_dist_ra += ALPHA * (dist_ra - self.avg_dist_ra);

        self.count += 1;
        if self.count < 10 {
            // seed the slow EMA with the running mean of the first 10
            // points.
            self.avg_long += (dist - self.avg_long) / self.count as f64;
            self.avg_long_ra += (dist_ra - self.avg_long_ra) / self.count as f64;
        } else {
            const ALPHA_LONG: f64 = 0.045;
            self.avg_long += ALPHA_LONG * (dist - self.avg_long);
            self.avg_long_ra += ALPHA_LONG * (dist_ra - self.avg_long_ra);
        }
    }

    /// Fast EMA of the guide error ("CurrentGuideError"): `avg_dist` when
    /// `dec_guiding`, else the RA-only `avg_dist_ra` — or `LARGE_DISTANCE`
    /// (100.0) when no star has been found for more than 20s, or never
    /// (dossier §13; `Guider::CurrentError`, `guider.cpp:1116-1132`).
    pub fn current_error(&self, now_s: f64, dec_guiding: bool) -> f64 {
        let raw = if dec_guiding {
            self.avg_dist
        } else {
            self.avg_dist_ra
        };
        self.gate_staleness(now_s, raw)
    }

    /// Slow EMA of the guide error (the `DistanceChecker` tolerance
    /// baseline): `avg_long` when `dec_guiding`, else `avg_long_ra` — same
    /// staleness gate as [`current_error`](Self::current_error) (dossier
    /// §13; `Guider::CurrentErrorSmoothed`, `guider.cpp:1134-1137`).
    pub fn current_error_smoothed(&self, now_s: f64, dec_guiding: bool) -> f64 {
        let raw = if dec_guiding {
            self.avg_long
        } else {
            self.avg_long_ra
        };
        self.gate_staleness(now_s, raw)
    }

    fn gate_staleness(&self, now_s: f64, raw: f64) -> f64 {
        match self.last_update_s {
            None => LARGE_DISTANCE,
            Some(t) if now_s - t > THRESHOLD_SECONDS => LARGE_DISTANCE,
            Some(_) => raw,
        }
    }
}

impl Default for AvgDist {
    fn default() -> Self {
        Self::new()
    }
}
