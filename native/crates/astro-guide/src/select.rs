// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§2.1-§2.5, §2.7).
// Derived from PHD2 star.cpp:515-544 (`GetStats`), star.cpp:574-656
// (`psf_conv`), star.cpp:718-1154 (`GuideStar::AutoFind`) (BSD-3-Clause; see
// THIRD-PARTY-NOTICES.md). No code copied from PHD2.

//! `GuideStar::AutoFind` parity — full-frame star search and single-star
//! primary selection (dossier §2).
//!
//! [`auto_find`] runs the §2.1-§2.4 candidate pipeline (9x9 PSF matched
//! filter, local-maximum scan with a significance gate, top-100 cap,
//! sub-5px merge, search-box conflict pruning, edge drop) and then measures
//! every surviving peak with [`starfind::star_find`], returning one
//! [`Candidate`] per peak that was actually found, brightest (by PSF
//! response) first. [`select_primary`] then runs the §2.7 three-pass
//! primary-star selection over that list.
//!
//! Three scope notes, all deliberate simplifications of dossier §2.1 for
//! this task (see brief P1-T3):
//!
//! - **No hot-pixel pre-filter, no downsampling.** Upstream's pipeline
//!   opens with a 3x3 median (`Median3`) and an optional box-average
//!   downsample (`Downsample`, `star.cpp:658-680`, driven by
//!   `/guider/AutoSelDownsample` and the camera's arcsec/px scale) before
//!   the PSF convolution. Neither knob exists on [`SelectParams`] (which
//!   this task's interface freezes to `search_region`/`af_min_snr`/
//!   `extra_edge_allowance`/`max_stars`), so this port convolves the raw
//!   frame directly — equivalent to upstream's `downsample = 1` path. The
//!   coordinate-mapping formula (`imgx = x*downsample + downsample/2`) is
//!   kept in [`find_local_maxima`] with `downsample` fixed at 1 so the
//!   seam is visible if a future task threads a real downsample factor in.
//! - **Multi-star candidate management is P3-T1 scope.** Dossier §2.6
//!   guards its candidate-collection loop with `maxStars > 1` and, when
//!   active, rejects any peak within 25px of an *already-accepted*
//!   candidate (`CloseToReference`, `star.cpp:710-715`,
//!   `star.cpp:1043-1056`) and caps the list at `max_stars`. This port
//!   does not implement that de-dup-against-accepted-set or the cap — see
//!   the seam comment in [`auto_find`].
//! - **`Candidate` carries no `FindResult`.** This task's frozen interface
//!   (P1-T4/P1-T7 consume it verbatim) gives `Candidate` exactly six
//!   fields, none of which is `star_find`'s outcome code. `select_primary`
//!   therefore can't independently test dossier §2.7's "hard `not
//!   saturated`" condition (`STAR_SATURATED`) apart from the `peak_val` vs.
//!   `sat_thresh` comparison — see the adjudication on [`select_primary`].

use crate::starfind::{self, FindParams};
use std::collections::HashSet;

/// Interior margin the 9x9 PSF kernel needs on each side (`CONV_RADIUS`,
/// `star.cpp:788`).
const CONV_MARGIN: i32 = 4;
/// Local-maximum neighbourhood half-width (`srch`, `star.cpp:811`).
const LOCAL_MAX_SEARCH: i32 = 4;
/// Half-width of the 15x15 window used for the local mean in the
/// significance test (`local`, `star.cpp:839`).
const LOCAL_STATS_RADIUS: i32 = 7;
/// Keep only the brightest 100 local maxima (`TOP_N`, `star.cpp:798`).
const TOP_N: usize = 100;
/// Merge peaks with squared distance below this (5px, `star.cpp:869`).
const MERGE_DIST2: i32 = 25;
/// Extra safety margin added to `search_region` for the search-box conflict
/// test (`star.cpp:897`).
const CONFLICT_EXTRA: i32 = 5;
/// Local-maximum significance floor (`threshold`, `star.cpp:807`).
const SIGNIFICANCE_THRESH: f64 = 0.1;
/// Downsample factor. Fixed at 1 — see the module doc's scope note.
const DOWNSAMPLE: i32 = 1;

/// One auto-found, `star_find`-measured star candidate (dossier §2.6/§2.7
/// fields subset — this task's frozen interface).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Candidate {
    pub x: f64,
    pub y: f64,
    pub snr: f64,
    pub mass: f64,
    pub hfd: f64,
    pub peak_val: u16,
}

/// `AutoFind`/selection parameters (dossier §2). `Default` matches PHD2's
/// shipped defaults: `search_region` 15 (shared with `Star::Find`),
/// `af_min_snr` 6.0 (`/guider/StarMinSNR`), `extra_edge_allowance` 0 (no
/// uncalibrated-mount safety margin), `max_stars` 1 (single-star, P1
/// scope — see module doc).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SelectParams {
    /// half-width of the `Star::Find` search window used to measure each
    /// candidate, and the base edge/search-box-conflict distance, pixels.
    pub search_region: i32,
    /// `select_primary` pass 1/2 SNR floor (dossier §2.7).
    pub af_min_snr: f64,
    /// extra edge-drop margin beyond `search_region`, pixels (dossier
    /// §2.4 — normally the calibration distance for an uncalibrated
    /// mount; 0 when calibrated or unknown).
    pub extra_edge_allowance: i32,
    /// candidate-list cap. Unused by [`auto_find`] in this port — see the
    /// module doc's P3-T1 scope note.
    pub max_stars: usize,
}

impl Default for SelectParams {
    fn default() -> Self {
        SelectParams {
            search_region: 15,
            af_min_snr: 6.0,
            extra_edge_allowance: 0,
            max_stars: 1,
        }
    }
}

/// A PSF-convolution local maximum: pixel position and its significance
/// `h` (dossier §2.3).
#[derive(Debug, Clone, Copy)]
struct Peak {
    x: i32,
    y: i32,
    h: f64,
}

#[inline]
fn pixel(frame: &astro_star::GrayFrame, x: i32, y: i32) -> f64 {
    frame.data[y as usize * frame.width + x as usize] as f64
}

/// Population mean/stdev over an inclusive rect (`left, top, right,
/// bottom`) of a same-size conv buffer (dossier §2.2/§2.3; `GetStats`,
/// `star.cpp:515-544`). Two-pass mean-then-variance, mathematically
/// equivalent to upstream's single-pass Welford accumulation since (unlike
/// the annulus background estimate in `starfind.rs`) this window is fixed
/// and never iteratively re-clipped.
fn stats(conv: &[f64], w: i32, rect: (i32, i32, i32, i32)) -> (f64, f64) {
    let (left, top, right, bottom) = rect;
    let mut sum = 0.0f64;
    let mut n: i64 = 0;
    for y in top..=bottom {
        let row = (y * w) as usize;
        for x in left..=right {
            sum += conv[row + x as usize];
            n += 1;
        }
    }
    let mean = sum / n as f64;
    let mut sq = 0.0f64;
    for y in top..=bottom {
        let row = (y * w) as usize;
        for x in left..=right {
            let d = conv[row + x as usize] - mean;
            sq += d * d;
        }
    }
    (mean, (sq / n as f64).sqrt())
}

/// 9x9 PSF matched-filter convolution (dossier §2.2; `psf_conv`,
/// `star.cpp:574-656`). Returns a same-size buffer; entries outside the
/// interior margin (`CONV_MARGIN` from every edge) are `0.0`, matching
/// upstream's zeroed border (`memset`, `star.cpp:584`).
///
/// The ring sums `A..D2` are the literal per-offset sums from the dossier's
/// PSF grid diagram; `D3` (the 44 remaining border cells) is computed as
/// `total81 - (A+B1+B2+C1+C2+C3+D1+D2)` rather than upstream's explicit
/// border-pointer loop (`star.cpp:626-646`) — the same 44 cells, summed via
/// the full-81-cell total instead, is simpler and numerically identical
/// (both are a sum of the same 44 exact-integer pixel values).
fn psf_conv(frame: &astro_star::GrayFrame) -> Vec<f64> {
    let w = frame.width as i32;
    let h = frame.height as i32;
    const PSF: [f64; 9] = [
        0.906, 0.584, 0.365, 0.117, 0.049, -0.05, -0.064, -0.074, -0.094,
    ];

    let mut dst = vec![0.0f64; frame.data.len()];
    for y in CONV_MARGIN..(h - CONV_MARGIN) {
        for x in CONV_MARGIN..(w - CONV_MARGIN) {
            let px = |dx: i32, dy: i32| pixel(frame, x + dx, y + dy);

            let a = px(0, 0);
            let b1 = px(0, -1) + px(0, 1) + px(1, 0) + px(-1, 0);
            let b2 = px(-1, -1) + px(1, -1) + px(-1, 1) + px(1, 1);
            let c1 = px(0, -2) + px(-2, 0) + px(2, 0) + px(0, 2);
            let c2 = px(-1, -2)
                + px(1, -2)
                + px(-2, -1)
                + px(2, -1)
                + px(-2, 1)
                + px(2, 1)
                + px(-1, 2)
                + px(1, 2);
            let c3 = px(-2, -2) + px(2, -2) + px(-2, 2) + px(2, 2);
            let d1 = px(0, -3) + px(-3, 0) + px(3, 0) + px(0, 3);
            let d2 = px(-1, -3)
                + px(1, -3)
                + px(-3, -1)
                + px(3, -1)
                + px(-3, 1)
                + px(3, 1)
                + px(-1, 3)
                + px(1, 3);

            let mut total = 0.0f64;
            for dy in -4..=4 {
                for dx in -4..=4 {
                    total += px(dx, dy);
                }
            }
            let d3 = total - (a + b1 + b2 + c1 + c2 + c3 + d1 + d2);
            let mean = total / 81.0;

            let response = PSF[0] * (a - mean)
                + PSF[1] * (b1 - 4.0 * mean)
                + PSF[2] * (b2 - 4.0 * mean)
                + PSF[3] * (c1 - 4.0 * mean)
                + PSF[4] * (c2 - 8.0 * mean)
                + PSF[5] * (c3 - 4.0 * mean)
                + PSF[6] * (d1 - 4.0 * mean)
                + PSF[7] * (d2 - 8.0 * mean)
                + PSF[8] * (d3 - 44.0 * mean);

            dst[(y * w + x) as usize] = response;
        }
    }
    dst
}

/// Local-maximum scan + significance gate + top-100 cap (dossier §2.3;
/// `star.cpp:796-865`). Returns peaks **ascending** by `h` (mirroring
/// `std::set<Peak>`'s iteration order, which the merge/conflict/edge steps
/// below rely on for their "erase the earlier — i.e. dimmer or equal —
/// element" logic) with a stable `(x, y)` tiebreak.
fn find_local_maxima(conv: &[f64], w: i32, h: i32) -> Vec<Peak> {
    let valid = (
        CONV_MARGIN,
        CONV_MARGIN,
        w - 1 - CONV_MARGIN,
        h - 1 - CONV_MARGIN,
    );
    let (left, top, right, bottom) = valid;
    let (_global_mean, global_stdev) = stats(conv, w, valid);

    let mut peaks: Vec<Peak> = Vec::new();
    for y in (top + LOCAL_MAX_SEARCH)..=(bottom - LOCAL_MAX_SEARCH) {
        for x in (left + LOCAL_MAX_SEARCH)..=(right - LOCAL_MAX_SEARCH) {
            let val = conv[(y * w + x) as usize];
            if val <= 0.0 {
                continue;
            }

            let mut is_max = true;
            'nb: for dy in -LOCAL_MAX_SEARCH..=LOCAL_MAX_SEARCH {
                for dx in -LOCAL_MAX_SEARCH..=LOCAL_MAX_SEARCH {
                    if dx == 0 && dy == 0 {
                        continue;
                    }
                    if conv[((y + dy) * w + (x + dx)) as usize] > val {
                        is_max = false;
                        break 'nb;
                    }
                }
            }
            if !is_max {
                continue;
            }

            let local_rect = (
                (x - LOCAL_STATS_RADIUS).max(left),
                (y - LOCAL_STATS_RADIUS).max(top),
                (x + LOCAL_STATS_RADIUS).min(right),
                (y + LOCAL_STATS_RADIUS).min(bottom),
            );
            let (local_mean, _local_stdev) = stats(conv, w, local_rect);
            let sig = (val - local_mean) / global_stdev;
            if sig < SIGNIFICANCE_THRESH {
                continue;
            }

            // coordinates on the original image (dossier §2.3; downsample
            // fixed at 1 in this port — see the module doc).
            let imgx = x * DOWNSAMPLE + DOWNSAMPLE / 2;
            let imgy = y * DOWNSAMPLE + DOWNSAMPLE / 2;
            peaks.push(Peak {
                x: imgx,
                y: imgy,
                h: sig,
            });
        }
    }

    peaks.sort_by(|a, b| {
        a.h.partial_cmp(&b.h)
            .expect("PSF response is never NaN")
            .then(a.x.cmp(&b.x))
            .then(a.y.cmp(&b.y))
    });
    if peaks.len() > TOP_N {
        let drop = peaks.len() - TOP_N;
        peaks.drain(0..drop);
    }
    peaks
}

/// Merge peaks with squared distance `< 25` (5px), dropping the earlier
/// (ascending-order, i.e. dimmer-or-equal) one of each pair; repeats until
/// no pair is left within range (dossier §2.4; `star.cpp:867-891`'s
/// `goto repeat`). `peaks` must be sorted ascending by `h` on entry and
/// stays sorted (removal preserves relative order).
///
/// This uses a plain sorted `Vec` rather than literally porting upstream's
/// `std::set<Peak>` (whose ordering comparator looks only at `h`). A
/// `std::set` silently *refuses* to insert a second element that compares
/// equal to one already present — so two distinct peaks with bit-identical
/// `h` would already have collapsed to one entry before this merge step
/// ever ran, and only one is retained regardless of `(x, y)`. A `Vec`
/// doesn't have that insertion-time collapse; it relies on this loop to do
/// the deduplication instead. The two only disagree in the (float-tie)
/// case this file's golden tests deliberately avoid; for the ordinary case
/// — genuinely different peaks compared by `h` — both give the same
/// dimmer-dropped result. Preferring the well-defined `Vec` + explicit-loop
/// behavior over blindly reproducing a `std::set` comparator quirk follows
/// the P1-T2 `norm_angle` precedent (dossier's own contract over an
/// incidental implementation artifact).
fn merge_close_peaks(peaks: &mut Vec<Peak>) {
    'restart: loop {
        for a in 0..peaks.len() {
            for b in (a + 1)..peaks.len() {
                let dx = peaks[a].x - peaks[b].x;
                let dy = peaks[a].y - peaks[b].y;
                if dx * dx + dy * dy < MERGE_DIST2 {
                    peaks.remove(a);
                    continue 'restart;
                }
            }
        }
        break;
    }
}

/// Drop pairs of peaks that would both fit inside one `search_region + 5`
/// box, unless one is at least 5x brighter than the other (dossier §2.4;
/// `star.cpp:893-927`). `peaks` sorted ascending by `h`.
fn drop_search_box_conflicts(peaks: &mut Vec<Peak>, search_region: i32) {
    let fullw = search_region + CONFLICT_EXTRA;
    let mut erase: HashSet<usize> = HashSet::new();
    for a in 0..peaks.len() {
        for b in (a + 1)..peaks.len() {
            let dx = (peaks[a].x - peaks[b].x).abs();
            let dy = (peaks[a].y - peaks[b].y).abs();
            if dx <= fullw && dy <= fullw {
                // ascending order: peaks[b].h >= peaks[a].h, so this is the
                // brighter/dimmer ratio regardless of which literal index
                // upstream's a/b happen to be.
                if peaks[b].h / peaks[a].h >= 5.0 {
                    continue; // safety margin: a dim star can't erase a bright one
                }
                erase.insert(a);
                erase.insert(b);
            }
        }
    }
    let mut i = 0usize;
    peaks.retain(|_| {
        let keep = !erase.contains(&i);
        i += 1;
        keep
    });
}

/// Drop peaks within `search_region + extra_edge_allowance` of any frame
/// edge (dossier §2.4; `star.cpp:929-946`).
fn drop_edge_peaks(peaks: &mut Vec<Peak>, width: i32, height: i32, edge_dist: i32) {
    peaks.retain(|p| {
        !(p.x <= edge_dist
            || p.x >= width - edge_dist
            || p.y <= edge_dist
            || p.y >= height - edge_dist)
    });
}

/// Full-frame star search (dossier §2.1-§2.4), then a `star_find`
/// measurement pass over every surviving peak (dossier §2.6's per-peak
/// `Star::Find` call, generalized — see below). Returns one [`Candidate`]
/// per peak `star_find` actually found ([`starfind::was_found`]),
/// brightest-first by the PSF response `h` computed in
/// [`find_local_maxima`] (i.e. the same order upstream's `stars.rbegin()`
/// walks, not a re-sort by the measured `mass`/`snr`).
///
/// **Seam vs. upstream's per-call fast path**: `GuideStar::AutoFind` only
/// ever materializes a full multi-entry `foundStars` list when `maxStars >
/// 1` (`star.cpp:1036-1057`, guarded `if (maxStars > 1)`, and rejecting
/// duplicates within 25px of an already-accepted candidate via
/// `CloseToReference`, `star.cpp:710-715`). For `maxStars == 1` (this
/// task's `SelectParams` default) upstream never builds that list at all —
/// its single-star path calls `Star::Find` once per peak *inside* the
/// pass-1/2/3 selection loop (`star.cpp:1065-1144`) and returns as soon as
/// one star clears a pass, so at most one star is ever measured or
/// returned. This task's brief splits candidate generation
/// (`auto_find`) from selection (`select_primary`) into two functions, so
/// `select_primary` needs a materialized list to choose from regardless of
/// `max_stars` — this port therefore generalizes the §2.6 measurement loop
/// to run unconditionally (every surviving peak, brightest to dimmest) but
/// **without** the 25px accepted-set de-dup or the `max_stars` cap (both
/// P3-T1 scope, per the brief). For `max_stars == 1` today, only
/// [`select_primary`]'s pick is actually used by a caller; the rest of the
/// list is there for a future multi-star caller to consume once P3-T1
/// lands.
pub fn auto_find(frame: &astro_star::GrayFrame, p: &SelectParams) -> Vec<Candidate> {
    let w = frame.width as i32;
    let h = frame.height as i32;

    let conv = psf_conv(frame);
    let mut peaks = find_local_maxima(&conv, w, h);

    merge_close_peaks(&mut peaks);
    drop_search_box_conflicts(&mut peaks, p.search_region);
    drop_edge_peaks(&mut peaks, w, h, p.search_region + p.extra_edge_allowance);

    // `SelectParams` has no min_hfd/max_hfd/max_adu/pedestal/bits_per_pixel
    // knobs (frozen interface); only search_region is threaded through to
    // `Star::Find` here, matching upstream's searchRegion argument while
    // using `FindParams::default()` for the rest (min_hfd 1.5, max_hfd
    // 20.0, max_adu 0, pedestal 0, bpp 16).
    let find_params = FindParams {
        search_region: p.search_region,
        ..FindParams::default()
    };

    let mut out = Vec::with_capacity(peaks.len());
    for peak in peaks.iter().rev() {
        let r = starfind::star_find(frame, peak.x as f64, peak.y as f64, &find_params);
        if !starfind::was_found(r.result) {
            continue;
        }
        out.push(Candidate {
            x: r.x,
            y: r.y,
            snr: r.snr,
            mass: r.mass,
            hfd: r.hfd,
            peak_val: r.peak_val,
        });
    }
    out
}

/// Saturation-level near-threshold inference (dossier §2.5;
/// `star.cpp:952-1030`). **Not** part of this task's frozen P1-T4/P1-T7
/// contract (`Candidate`/`SelectParams`/`auto_find`/`select_primary`,
/// listed in the brief's Interfaces section) — `SelectParams` carries no
/// camera-ADU knobs, so [`select_primary`]'s `sat_thresh` argument is
/// caller-supplied. This helper is provided so a caller can compute that
/// value faithfully (this crate's golden test uses it) rather than
/// inventing its own formula.
///
/// `peaks_brightest_first` are candidate `(x, y)` positions, brightest
/// first (e.g. `auto_find`'s output re-mapped to `(c.x, c.y)`, or the
/// pre-measurement peak list) — used only to probe for a genuinely
/// flat-topped (`StarSaturated`) star when `find_params.max_adu == 0`.
pub fn saturation_threshold(
    frame: &astro_star::GrayFrame,
    peaks_brightest_first: &[(i32, i32)],
    find_params: &FindParams,
) -> u16 {
    let pedestal = find_params.pedestal as u32;

    let sat_level: u32 = if find_params.max_adu > 0 {
        find_params.max_adu + pedestal
    } else {
        let max_val = frame.data.iter().copied().max().unwrap_or(0);
        let mut found_saturated = false;
        for &(x, y) in peaks_brightest_first {
            let r = starfind::star_find(frame, x as f64, y as f64, find_params);
            if r.result == starfind::FindResult::StarSaturated {
                let diff = max_val.saturating_sub(r.peak_val) as u32;
                if diff * 255 <= max_val as u32 {
                    found_saturated = true;
                    break;
                }
            }
        }
        if found_saturated {
            max_val as u32
        } else {
            ((1u32 << find_params.bits_per_pixel) - 1) + pedestal
        }
    };

    let range = sat_level.saturating_sub(pedestal);
    let thresh = pedestal + 9 * range / 10;
    thresh.min(65535) as u16
}

/// Three-pass primary-star selection (dossier §2.7; `star.cpp:1059-1150`).
/// `cands` should be brightest-first (as returned by [`auto_find`]); each
/// pass scans in the given order and the first candidate to satisfy that
/// pass's test wins, returning its index. `None` if `cands` is empty.
///
/// **Adjudication — "not saturated" without a `FindResult`.** Upstream's
/// pass 1 rejects on *two* independent saturation signals: the near-
/// saturation cutoff (`tmp.PeakVal > sat_thresh`) and the hard
/// `STAR_SATURATED` outcome from `Star::Find` itself
/// (`star.cpp:1076-1085`); pass 2 drops the `sat_thresh` check but keeps
/// the hard-saturation one (`star.cpp:1087-1095`). This task's frozen
/// `Candidate` (six fields: `x, y, snr, mass, hfd, peak_val` — no
/// `FindResult`) can't carry the hard-saturation flag independently of
/// `peak_val`, so this port folds both upstream checks into the one signal
/// available: pass 1 requires `peak_val <= sat_thresh`, and pass 2 drops
/// that requirement entirely (relying on `snr` alone), rather than
/// swapping in a second, unavailable condition. This preserves the
/// dossier's pass-to-pass *shape* (progressively fewer constraints,
/// terminating in "any found star") while adapting to the interface this
/// task is required to ship. Pass 3 is unconditional because every
/// `Candidate` already passed `star_find`'s `was_found` gate inside
/// [`auto_find`] — "found" needs no re-checking here.
///
/// Citations: dossier §2.7; `star.cpp:1059-1150` (`AutoFind`'s three-pass
/// loop).
pub fn select_primary(cands: &[Candidate], sat_thresh: u16, af_min_snr: f64) -> Option<usize> {
    // pass 1: near-/non-saturated (peak_val <= sat_thresh) AND snr gate.
    if let Some(i) = cands
        .iter()
        .position(|c| c.peak_val <= sat_thresh && c.snr >= af_min_snr)
    {
        return Some(i);
    }
    // pass 2: snr gate only (near-saturated candidates now eligible).
    if let Some(i) = cands.iter().position(|c| c.snr >= af_min_snr) {
        return Some(i);
    }
    // pass 3: any candidate at all (brightest, i.e. first in the list).
    if !cands.is_empty() {
        return Some(0);
    }
    None
}
