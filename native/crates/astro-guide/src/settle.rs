// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§12). Derived from
// PHD2 `phdcontrol.cpp:514-567` (`PhdController::UpdateControllerState`
// STATE_SETTLE_WAIT branch) and `phdcontrol.h:38-44` (`SettleParams`)
// (BSD-3-Clause; see THIRD-PARTY-NOTICES.md). No code copied from PHD2.

//! Dither / guide-start settle monitor (dossier §12).
//!
//! [`Settle`] watches the guide error frame-by-frame after a disturbance
//! (a dither, or the start of guiding) and reports when the guider has
//! *settled*: the error has stayed within a pixel tolerance continuously for
//! `settle_time_sec`, before a `timeout_sec` deadline. It holds only the
//! settle bookkeeping and performs no I/O — the host feeds it one
//! `(err, locked, now_s)` triple per frame and acts on the returned
//! [`SettleState`].
//!
//! **Clock injected** (dossier §17 deviation): upstream reads
//! `wxStopWatch`/`wxGetUTCTimeMillis` directly; this port takes the frame
//! timestamp (`now_s`, seconds) as an argument so it is deterministic under
//! test. The overall-timeout clock is anchored lazily to the first
//! [`evaluate`](Settle::evaluate) call's `now_s` (the disturbance that
//! opened the window may have been applied between frames, with no timestamp
//! of its own).
//!
//! **Scope note**: upstream's `SettleParams` also carries a frame-count
//! ceiling (`frames`, event-server default 99999, legacy dither 10) that can
//! force success independent of the tolerance/time gates
//! (`phdcontrol.cpp:516-520`). This crate's [`Settle`] exposes only the
//! `pixels`/`time`/`timeout` triple the brief's Produces list freezes (and
//! that AstroDeck's existing guider settle API maps onto 1:1 — dossier §17);
//! the frame-count shortcut is out of scope.

/// The result of one settle evaluation (dossier §12).
#[derive(Debug, Clone, PartialEq)]
pub enum SettleState {
    /// Still settling; the host should keep looping and not treat the guider
    /// as settled yet.
    Settling,
    /// Settled: the error stayed within tolerance for `settle_time_sec`.
    Done,
    /// The overall `timeout_sec` deadline passed before settling.
    Failed(String),
}

/// Frame-by-frame settle monitor (dossier §12; `PhdController` settle wait).
#[derive(Debug, Clone, PartialEq)]
pub struct Settle {
    /// "pixels": the guide error must be at or below this to count as
    /// in-range.
    tolerance_px: f64,
    /// "time": required continuous in-range dwell (seconds) before success.
    settle_time_sec: f64,
    /// "timeout": overall deadline (seconds) from the first evaluated frame.
    timeout_sec: f64,
    /// Timestamp the overall-timeout clock is anchored to; set lazily on the
    /// first [`evaluate`](Self::evaluate) call.
    start_s: Option<f64>,
    /// Timestamp of the frame that began the current in-range run; `None`
    /// until the first in-range frame (and reset whenever a frame falls out
    /// of range).
    in_range_since: Option<f64>,
    /// Whether the previous frame was in range — upstream's `prior_in_range`
    /// (`phdcontrol.cpp:552`); any out-of-range frame restarts the dwell
    /// clock.
    prior_in_range: bool,
}

impl Settle {
    /// A settle window with the PHD2 request triple (dossier §12): error
    /// tolerance (px), required in-range dwell (s), and overall timeout (s).
    pub fn new(tolerance_px: f64, settle_time_sec: f64, timeout_sec: f64) -> Self {
        Settle {
            tolerance_px,
            settle_time_sec,
            timeout_sec,
            start_s: None,
            in_range_since: None,
            prior_in_range: false,
        }
    }

    /// One settling frame (dossier §12 STATE_SETTLE_WAIT). `err` is the
    /// current guide error (px; the host passes `avgDistance`, or a large
    /// sentinel when the star is lost), `locked` is whether the guider still
    /// holds a lock this frame, and `now_s` is the frame timestamp (seconds).
    ///
    /// A frame is *in range* only when `locked && err <= tolerance_px`. The
    /// dwell timer starts on the first in-range frame and any out-of-range
    /// frame restarts it (via `prior_in_range`). Success needs the dwell to
    /// reach `settle_time_sec` (or `settle_time_sec <= 0`, which succeeds on
    /// the first in-range frame — `phdcontrol.cpp:544-548`); the overall
    /// `timeout_sec` deadline yields [`SettleState::Failed`].
    pub fn evaluate(&mut self, err: f64, locked: bool, now_s: f64) -> SettleState {
        let start = *self.start_s.get_or_insert(now_s);
        let in_range = locked && err <= self.tolerance_px;

        let mut result = SettleState::Settling;
        if in_range {
            if !self.prior_in_range {
                // First in-range frame of a run (dossier §12: `if
                // (!prior_in_range) { if (time <= 0) succeed; timer.restart();
                // }`).
                if self.settle_time_sec <= 0.0 {
                    result = SettleState::Done;
                } else {
                    self.in_range_since = Some(now_s);
                }
            } else if let Some(t0) = self.in_range_since {
                if now_s - t0 >= self.settle_time_sec {
                    result = SettleState::Done;
                }
            }
        }

        // Overall deadline (dossier §12: `if (total.secs() >= timeout)
        // fail(...)`). Only a genuinely-still-settling frame can time out.
        if matches!(result, SettleState::Settling) && now_s - start >= self.timeout_sec {
            result = SettleState::Failed("timed-out waiting for guider to settle".to_string());
        }

        self.prior_in_range = in_range;
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn settles_after_continuous_in_range_dwell() {
        // tol 1.5px, dwell 4s, timeout 30s. In range from t=0; succeeds once
        // 4s of continuous in-range frames have elapsed.
        let mut s = Settle::new(1.5, 4.0, 30.0);
        assert_eq!(s.evaluate(0.5, true, 0.0), SettleState::Settling); // first in-range frame: start dwell
        assert_eq!(s.evaluate(0.5, true, 2.0), SettleState::Settling);
        assert_eq!(s.evaluate(0.5, true, 3.9), SettleState::Settling);
        assert_eq!(s.evaluate(0.5, true, 4.0), SettleState::Done); // 4s dwell reached
    }

    #[test]
    fn out_of_range_frame_restarts_dwell() {
        let mut s = Settle::new(1.5, 4.0, 30.0);
        assert_eq!(s.evaluate(0.5, true, 0.0), SettleState::Settling);
        assert_eq!(s.evaluate(0.5, true, 3.0), SettleState::Settling);
        // out of range at t=3.5 restarts the clock
        assert_eq!(s.evaluate(5.0, true, 3.5), SettleState::Settling);
        // back in range: dwell restarts from t=4.0
        assert_eq!(s.evaluate(0.5, true, 4.0), SettleState::Settling);
        assert_eq!(s.evaluate(0.5, true, 7.9), SettleState::Settling);
        assert_eq!(s.evaluate(0.5, true, 8.0), SettleState::Done);
    }

    #[test]
    fn lost_lock_is_out_of_range() {
        let mut s = Settle::new(1.5, 2.0, 30.0);
        // err within tolerance but not locked -> out of range.
        assert_eq!(s.evaluate(0.5, false, 0.0), SettleState::Settling);
        assert_eq!(s.evaluate(0.5, true, 1.0), SettleState::Settling); // starts dwell now
        assert_eq!(s.evaluate(0.5, true, 3.0), SettleState::Done);
    }

    #[test]
    fn times_out_when_never_in_range() {
        let mut s = Settle::new(1.5, 2.0, 10.0);
        assert_eq!(s.evaluate(9.0, true, 0.0), SettleState::Settling); // anchors timeout clock at t=0
        assert_eq!(s.evaluate(9.0, true, 5.0), SettleState::Settling);
        assert_eq!(
            s.evaluate(9.0, true, 10.0),
            SettleState::Failed("timed-out waiting for guider to settle".to_string())
        );
    }

    #[test]
    fn zero_dwell_succeeds_on_first_in_range_frame() {
        let mut s = Settle::new(1.5, 0.0, 30.0);
        assert_eq!(s.evaluate(0.5, true, 0.0), SettleState::Done);
    }
}
