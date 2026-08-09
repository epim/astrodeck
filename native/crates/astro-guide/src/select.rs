// SPDX-License-Identifier: Apache-2.0
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (§2.1-§2.5, §2.7).
// Derived from PHD2 star.cpp:515-544 (`GetStats`), star.cpp:574-656
// (`psf_conv`), star.cpp:718-1154 (`GuideStar::AutoFind`), and
// image_math.cpp:150-505 (`Median3` + the `median4`/`median6`/`median9`
// selection helpers it dispatches to) (BSD-3-Clause; see
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
//! Two scope notes, both deliberate simplifications of dossier §2.1 for
//! this task (see brief P1-T3 + fix round 1):
//!
//! - **No downsampling.** Upstream's pipeline runs an unconditional 3x3
//!   median (`Median3`, `star.cpp:752` — ported here as [`median3x3`],
//!   applied to the PSF-convolution input in every configuration) and then
//!   an *optional* box-average downsample (`Downsample`,
//!   `star.cpp:658-680`, driven by `/guider/AutoSelDownsample` and the
//!   camera's arcsec/px scale) before the PSF convolution. The downsample
//!   knob doesn't exist on [`SelectParams`] (which this task's interface
//!   freezes to `search_region`/`af_min_snr`/`extra_edge_allowance`/
//!   `max_stars`), so this port convolves the median-filtered frame
//!   directly — equivalent to upstream's `downsample = 1` path. The
//!   coordinate-mapping formula (`imgx = x*downsample + downsample/2`) is
//!   kept in [`find_local_maxima`] with `downsample` fixed at 1 so the
//!   seam is visible if a future task threads a real downsample factor in.
//! - **Multi-star candidate management (dossier §2.6, P3-T1).** When
//!   `max_stars > 1`, [`auto_find`] additionally rejects any measured
//!   candidate whose SNR is below `af_min_snr`, then rejects any survivor
//!   within 25px of an *already-accepted* candidate (`CloseToReference`,
//!   `star.cpp:710-715`, applied at `star.cpp:1043-1051`) — see
//!   [`multi_star_candidates`]. The `max_stars` CAP itself is deliberately
//!   **not** applied here: upstream defers it until after primary
//!   selection erases every candidate "ahead of" (brighter than) the
//!   chosen primary (`star.cpp:1116-1119`) — seaming with the still-brighter
//!   candidates the pass-1/2/3 loop rejected as saturated/degraded would be
//!   wrong to prune before that.  [`primary_and_secondaries`] performs that
//!   tail. For `max_stars <= 1` (this crate's frozen single-star default),
//!   [`auto_find`]'s output is completely unaffected by this task's
//!   changes — the P1-T3 review's regression guard.
//!
//!   **Accepted narrowing vs. upstream** (documented adjudication): this
//!   port's P1-T3 architecture already merges upstream's two independent
//!   per-peak `Star::Find` measurement passes (the §2.6 candidate-list
//!   loop and the §2.7 primary-selection loop) into ONE shared measured
//!   list (`auto_find`'s `out`). When `max_stars > 1`, [`select_primary`]
//!   therefore picks the primary FROM the SAME SNR-filtered/deduped list
//!   [`multi_star_candidates`] built — unlike upstream, whose §2.7 pass 3
//!   ("any found star, even below `af_min_snr`") can still select a
//!   peak the independent §2.6 loop excluded, if it's the last resort.
//!   In this port, once `max_stars > 1`, a peak that failed the SNR gate is
//!   never a `select_primary` candidate at all — pass 3's "even low-SNR"
//!   reach is narrowed to whatever survived the multi-star SNR/dedup
//!   filter. A direct, mechanical consequence: upstream's "primary star
//!   not found in the candidate list -> clear it, insert the primary
//!   alone" fallback (`star.cpp:1120-1126`) is UNREACHABLE here BY
//!   CONSTRUCTION — see [`primary_and_secondaries`]'s doc comment. Single-
//!   star mode (`max_stars <= 1`) is untouched by any of this.

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
/// fields subset — this task's frozen interface, plus the additive
/// `saturated` flag from review fix round 1).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Candidate {
    pub x: f64,
    pub y: f64,
    pub snr: f64,
    pub mass: f64,
    pub hfd: f64,
    pub peak_val: u16,
    /// `star_find` reported `StarSaturated` for this candidate (dossier
    /// §1.3 step 10). [`auto_find`] measures with `max_adu = 0`, so this
    /// is the flat-top heuristic outcome. Carried so [`select_primary`]
    /// can apply upstream's hard-saturation rejection in passes 1 and 2
    /// (`star.cpp:1084`, `star.cpp:1089`) independently of the soft
    /// `peak_val` vs. `sat_thresh` cutoff.
    pub saturated: bool,
}

/// `AutoFind`/selection parameters (dossier §2). `Default` matches PHD2's
/// shipped defaults: `search_region` 15 (shared with `Star::Find`),
/// `af_min_snr` 6.0 (`/guider/StarMinSNR`), `extra_edge_allowance` 0 (no
/// uncalibrated-mount safety margin), `max_stars` 1 (single-star default,
/// matching upstream's `/guider/multistar/enabled = false`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SelectParams {
    /// half-width of the `Star::Find` search window used to measure each
    /// candidate, and the base edge/search-box-conflict distance, pixels.
    pub search_region: i32,
    /// `select_primary` pass 1/2 SNR floor (dossier §2.7), and (P3-T1)
    /// [`auto_find`]'s multi-star candidate SNR floor (dossier §2.6) when
    /// `max_stars > 1`.
    pub af_min_snr: f64,
    /// extra edge-drop margin beyond `search_region`, pixels (dossier
    /// §2.4 — normally the calibration distance for an uncalibrated
    /// mount; 0 when calibrated or unknown).
    pub extra_edge_allowance: i32,
    /// Multi-star candidate-list gate (dossier §2.6; P3-T1). `<= 1`
    /// (default) keeps [`auto_find`]'s single-star behavior completely
    /// unchanged — see the module doc's multi-star section. `> 1` (up to
    /// `MAX_LIST_SIZE` 12 upstream) additionally SNR-gates and 25px-dedups
    /// the returned candidate list; the actual list-size CAP is applied
    /// later by [`primary_and_secondaries`], not by [`auto_find`] itself.
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

/// Median of the two middle values of 4, truncated integer average
/// (`median4`, `image_math.cpp:364-382`: sorts implicitly, returns
/// `(2nd + 3rd) / 2` in integer arithmetic).
fn median4(v: [u16; 4]) -> u16 {
    let mut v = v;
    v.sort_unstable();
    (((v[1] as u32) + (v[2] as u32)) / 2) as u16
}

/// Median of the two middle values of 6, truncated integer average
/// (`median6`, `image_math.cpp:300-335`: returns `(3rd + 4th) / 2`).
fn median6(v: [u16; 6]) -> u16 {
    let mut v = v;
    v.sort_unstable();
    (((v[2] as u32) + (v[3] as u32)) / 2) as u16
}

/// True median (5th smallest) of 9 (`median9`, `image_math.cpp:182-241`).
fn median9(v: [u16; 9]) -> u16 {
    let mut v = v;
    v.sort_unstable();
    v[4]
}

/// 3x3 median filter over the whole frame — upstream's unconditional
/// hot-pixel pre-filter for AutoFind (dossier §2.1; `Median3`,
/// `image_math.cpp:150-173` full-frame path dispatching to
/// `image_math.cpp:396-505`, called from `star.cpp:752`). Interior pixels
/// take the true median of their 3x3 neighborhood; edge pixels the median
/// of the clipped 2x3/3x2 block; corner pixels the median of the 2x2
/// block. Even-count medians use upstream's truncated integer average of
/// the two middle values.
///
/// Upstream's branch-free `swap`-network `median4`/`median6`/`median9`
/// helpers are ported as sort-then-index over the same fixed-size arrays —
/// identical selection semantics (upstream's networks compute exactly the
/// order statistics indexed here), simpler Rust. AutoFind rejects
/// subframes outright (`star.cpp:721-725`) and `GrayFrame` has no
/// subframe/ROI concept, so only the full-frame rect path is ported.
/// Frames narrower/shorter than 2px are returned unfiltered (upstream's
/// pointer walk assumes >= 2 in each dimension; such frames can't contain
/// a 9x9 PSF site anyway).
#[allow(clippy::needless_range_loop)] // index loops mirror upstream's per-row pixel walk
fn median3x3(frame: &astro_star::GrayFrame) -> Vec<u16> {
    let w = frame.width;
    let h = frame.height;
    if w < 2 || h < 2 {
        return frame.data.to_vec();
    }

    let src = frame.data;
    let px = |x: usize, y: usize| src[y * w + x];
    let mut dst = vec![0u16; src.len()];

    // top-left corner
    dst[0] = median4([px(0, 0), px(1, 0), px(0, 1), px(1, 1)]);
    // top row middle pixels
    for x in 1..=(w - 2) {
        dst[x] = median6([
            px(x - 1, 0),
            px(x, 0),
            px(x + 1, 0),
            px(x - 1, 1),
            px(x, 1),
            px(x + 1, 1),
        ]);
    }
    // top-right corner
    dst[w - 1] = median4([px(w - 2, 0), px(w - 1, 0), px(w - 2, 1), px(w - 1, 1)]);

    for y in 1..=(h - 2) {
        let row = y * w;
        // leftmost pixel
        dst[row] = median6([
            px(0, y - 1),
            px(1, y - 1),
            px(0, y),
            px(1, y),
            px(0, y + 1),
            px(1, y + 1),
        ]);
        // interior
        for x in 1..=(w - 2) {
            dst[row + x] = median9([
                px(x - 1, y - 1),
                px(x, y - 1),
                px(x + 1, y - 1),
                px(x - 1, y),
                px(x, y),
                px(x + 1, y),
                px(x - 1, y + 1),
                px(x, y + 1),
                px(x + 1, y + 1),
            ]);
        }
        // rightmost pixel
        dst[row + w - 1] = median6([
            px(w - 2, y - 1),
            px(w - 1, y - 1),
            px(w - 2, y),
            px(w - 1, y),
            px(w - 2, y + 1),
            px(w - 1, y + 1),
        ]);
    }

    // bottom-left corner
    let brow = (h - 1) * w;
    dst[brow] = median4([px(0, h - 2), px(1, h - 2), px(0, h - 1), px(1, h - 1)]);
    // bottom row middle pixels
    for x in 1..=(w - 2) {
        dst[brow + x] = median6([
            px(x - 1, h - 2),
            px(x, h - 2),
            px(x + 1, h - 2),
            px(x - 1, h - 1),
            px(x, h - 1),
            px(x + 1, h - 1),
        ]);
    }
    // bottom-right corner
    dst[brow + w - 1] = median4([
        px(w - 2, h - 2),
        px(w - 1, h - 2),
        px(w - 2, h - 1),
        px(w - 1, h - 1),
    ]);

    dst
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
/// the deduplication instead. The two only disagree in the float-tie case
/// (covered directly by this module's unit tests, since no non-tied pair
/// can reach this loop — see the reachability note there); for the
/// ordinary case — genuinely different peaks compared by `h` — both give
/// the same dimmer-dropped result. Preferring the well-defined `Vec` +
/// explicit-loop behavior over blindly reproducing a `std::set` comparator
/// quirk follows the P1-T2 `norm_angle` precedent (dossier's own contract
/// over an incidental implementation artifact).
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
/// to run unconditionally (every surviving peak, brightest to dimmest).
///
/// For `max_stars > 1` (P3-T1), the resulting list is additionally run
/// through [`multi_star_candidates`] (the §2.6 SNR gate + 25px accepted-set
/// de-dup) before being returned — see the module doc's multi-star section
/// for what that changes and the single-star (`max_stars <= 1`) regression
/// guard.
pub fn auto_find(frame: &astro_star::GrayFrame, p: &SelectParams) -> Vec<Candidate> {
    let w = frame.width as i32;
    let h = frame.height as i32;

    // Unconditional 3x3 median pre-filter (hot-pixel removal) feeding the
    // PSF convolution ONLY — candidate measurement below runs on the
    // original frame, exactly as upstream measures `image`, not
    // `smoothed` (dossier §2.1; `star.cpp:733-752` vs. `star.cpp:1072`).
    let smoothed_buf = median3x3(frame);
    let smoothed = astro_star::GrayFrame::new(&smoothed_buf, frame.width, frame.height);

    let conv = psf_conv(&smoothed);
    let mut peaks = find_local_maxima(&conv, w, h);

    merge_close_peaks(&mut peaks);
    drop_search_box_conflicts(&mut peaks, p.search_region);
    drop_edge_peaks(&mut peaks, w, h, p.search_region + p.extra_edge_allowance);

    // `SelectParams` has no min_hfd/max_hfd/max_adu/pedestal/bits_per_pixel
    // knobs (frozen interface); only search_region is threaded through to
    // `Star::Find` here, matching upstream's searchRegion argument while
    // using `FindParams::default()` for the rest (min_hfd 1.5, max_hfd
    // 7.0 = CENTROID_DISK_RADIUS_PX, max_adu 0, pedestal 0, bpp 16).
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
            saturated: r.result == starfind::FindResult::StarSaturated,
        });
    }

    if p.max_stars > 1 {
        multi_star_candidates(out, p.af_min_snr)
    } else {
        out
    }
}

/// Multi-star candidate-list management (dossier §2.6; the §2.6 collection
/// loop's body, `star.cpp:1036-1057`, minus the re-measurement this port
/// avoids by reusing `auto_find`'s already-measured `cands` — `Star::Find`
/// is a pure function of `(frame, position, params)`, so re-invoking it at
/// the SAME peak position the caller already measured would return
/// bit-identical results; skipping the redundant call changes nothing
/// observable). Rejects candidates below `af_min_snr` (`/guider/StarMinSNR`,
/// default 6.0), then rejects any survivor within 25px of an
/// already-accepted survivor ([`close_to_reference`]). Preserves the
/// input's brightest-first order. NOT capped at any list-size limit here —
/// see [`auto_find`]'s doc comment and [`primary_and_secondaries`] for why
/// that cap is deferred.
fn multi_star_candidates(cands: Vec<Candidate>, af_min_snr: f64) -> Vec<Candidate> {
    let mut accepted: Vec<Candidate> = Vec::with_capacity(cands.len());
    for c in cands {
        if c.snr < af_min_snr {
            continue;
        }
        let duplicate = accepted.iter().any(|a| close_to_reference(*a, c));
        if duplicate {
            continue;
        }
        accepted.push(c);
    }
    accepted
}

/// `CloseToReference` (dossier §2.6; `star.cpp:710-715`): `true` when
/// `other` is within 25px (Euclidean, strictly less than) of `reference`.
fn close_to_reference(reference: Candidate, other: Candidate) -> bool {
    const MIN_SEPARATION: f64 = 25.0;
    let dx = other.x - reference.x;
    let dy = other.y - reference.y;
    dx.hypot(dy) < MIN_SEPARATION
}

/// The dossier §2.7 tail (`star.cpp:1096-1119`): given the multi-star
/// candidate list [`auto_find`] returns when `max_stars > 1` and the index
/// [`select_primary`] chose within it, split `(primary, secondaries)`.
/// Candidates AHEAD of the primary in `cands` (brighter, but rejected
/// during selection — e.g. saturated or near-saturated) are dropped
/// entirely (`star.cpp:1116-1117`); the primary and every dimmer survivor
/// are kept, capped at `max_stars` total (`star.cpp:1118-1119`). Each
/// secondary's `offset_from_primary` is `(x, y) - primary(x, y)`
/// (dossier: `reference_point - primary_ref`; at this port's
/// [`multi_star_candidates`] construction time `reference_point == (x,
/// y)`, the just-measured position, so the two are equal — the caller
/// computes this from the returned `Candidate`s directly).
///
/// # Panics
/// If `primary_idx >= cands.len()` (caller contract: `primary_idx` must be
/// an index [`select_primary`] returned for this exact `cands` slice).
///
/// Upstream's "primary not found in the candidate list -> clear it, insert
/// the primary alone" fallback (`star.cpp:1120-1126`) is UNREACHABLE here
/// BY CONSTRUCTION: [`select_primary`] picks `primary_idx` FROM `cands`
/// directly (this port's single shared measurement list, established
/// P1-T3), so the chosen primary is always an element of `cands` — unlike
/// upstream, which re-measures the primary independently over the raw peak
/// set and searches for it by exact-position match in a SEPARATELY built
/// `foundStars` list (where, e.g., a saturated bright duplicate can shadow
/// a dimmer non-saturated primary out of the SNR+dedup gate). See the
/// module doc's multi-star section for the accepted narrowing this
/// implies.
pub fn primary_and_secondaries(
    cands: &[Candidate],
    primary_idx: usize,
    max_stars: usize,
) -> (Candidate, Vec<Candidate>) {
    let kept = &cands[primary_idx..];
    let kept = if max_stars > 0 && kept.len() > max_stars {
        &kept[..max_stars]
    } else {
        kept
    };
    (kept[0], kept[1..].to_vec())
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
/// Pass semantics match upstream exactly (fix round 1 restored the hard-
/// saturation checks via [`Candidate::saturated`]):
///
/// 1. **Pass 1** (`star.cpp:1076-1085`): `peak_val <= sat_thresh` (soft,
///    90%-of-range near-saturation cutoff) AND not `STAR_SATURATED` (the
///    independent hard flag from `Star::Find` — flat-top heuristic here,
///    since [`auto_find`] measures with `max_adu = 0`) AND
///    `snr >= af_min_snr`.
/// 2. **Pass 2** (`star.cpp:1087-1095`): drops the `sat_thresh` cutoff but
///    keeps the hard `STAR_SATURATED` rejection and the SNR gate.
/// 3. **Pass 3** (`star.cpp:1097+`): any candidate at all. Unconditional
///    because every `Candidate` already passed `star_find`'s `was_found`
///    gate inside [`auto_find`] — "found" needs no re-checking here (and
///    `StarSaturated` counts as found, dossier §1.2, so saturated
///    candidates are legitimately selectable in this last-resort pass).
///
/// Citations: dossier §2.7; `star.cpp:1059-1150` (`AutoFind`'s three-pass
/// loop).
pub fn select_primary(cands: &[Candidate], sat_thresh: u16, af_min_snr: f64) -> Option<usize> {
    // pass 1: near-saturation cutoff AND hard-saturation AND snr gates.
    if let Some(i) = cands
        .iter()
        .position(|c| c.peak_val <= sat_thresh && !c.saturated && c.snr >= af_min_snr)
    {
        return Some(i);
    }
    // pass 2: hard-saturation and snr gates only (near-saturated
    // peak_val values now eligible).
    if let Some(i) = cands
        .iter()
        .position(|c| !c.saturated && c.snr >= af_min_snr)
    {
        return Some(i);
    }
    // pass 3: any candidate at all (brightest, i.e. first in the list).
    if !cands.is_empty() {
        return Some(0);
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    // Provenance: direct unit coverage of `merge_close_peaks` (dossier
    // §2.4; star.cpp:867-891). REACHABILITY (upheld by the P1-T3 review):
    // the local-max scan preceding this step rejects a candidate whenever
    // ANY pixel in its Chebyshev-4 (9x9) window has a strictly greater
    // response (star.cpp:812-834), and integer Euclidean distance < 5
    // always implies Chebyshev distance <= 4 — so any pair close enough to
    // merge has already had its strictly-dimmer member suppressed, and the
    // ONLY peak pairs that can reach this loop within merge range are
    // bit-identical `h` ties. These tests therefore hand-construct tied
    // peaks (bypassing the scan) to pin the erase-the-earlier (ascending
    // order: dimmer-or-equal) behavior and the restart-to-fixpoint
    // cascade.

    #[test]
    fn merge_close_peaks_tied_pair_erases_earlier_keeps_later() {
        // Two bit-tied peaks 3px apart (d^2 = 9 < 25). Ascending sort
        // order (h tie broken by x) puts (10,10) first; the merge loop
        // erases the earlier element, keeping (13,10).
        let mut peaks = vec![
            Peak {
                x: 10,
                y: 10,
                h: 1.0,
            },
            Peak {
                x: 13,
                y: 10,
                h: 1.0,
            },
        ];
        merge_close_peaks(&mut peaks);
        assert_eq!(peaks.len(), 1);
        assert_eq!((peaks[0].x, peaks[0].y), (13, 10));
    }

    #[test]
    fn merge_close_peaks_tied_chain_restarts_to_fixpoint() {
        // Three bit-tied peaks in a chain: A(10,10)-B(13,10)-C(16,10).
        // d^2(A,B) = d^2(B,C) = 9 < 25 but d^2(A,C) = 36 >= 25. First
        // sweep erases A and restarts (upstream's `goto repeat`,
        // star.cpp:887); the fresh sweep then sees B-C in range and erases
        // B; C alone is the fixpoint. Without the restart, a
        // single-forward-pass implementation could skip the B-C pair after
        // the removal shifted indices.
        let mut peaks = vec![
            Peak {
                x: 10,
                y: 10,
                h: 1.0,
            },
            Peak {
                x: 13,
                y: 10,
                h: 1.0,
            },
            Peak {
                x: 16,
                y: 10,
                h: 1.0,
            },
        ];
        merge_close_peaks(&mut peaks);
        assert_eq!(peaks.len(), 1);
        assert_eq!((peaks[0].x, peaks[0].y), (16, 10));
    }

    #[test]
    fn merge_close_peaks_distant_pair_untouched() {
        // Exactly at the boundary: d^2 = 25 is NOT < 25 -> no merge
        // (upstream `d2 < minlimitsq`, star.cpp:880).
        let mut peaks = vec![
            Peak {
                x: 10,
                y: 10,
                h: 1.0,
            },
            Peak {
                x: 15,
                y: 10,
                h: 1.0,
            },
        ];
        merge_close_peaks(&mut peaks);
        assert_eq!(peaks.len(), 2);
    }

    // Provenance: direct unit coverage of `multi_star_candidates` /
    // `primary_and_secondaries` (dossier §2.6/§2.7 tail; P3-T1). These
    // operate on already-measured `Candidate`s, so a hand-built list
    // exercises the list-management logic without needing a synthetic
    // frame (the frame-level `auto_find` pipeline is already covered by
    // this file's other tests and by select_golden.rs).

    fn cand(x: f64, y: f64, snr: f64) -> Candidate {
        Candidate {
            x,
            y,
            snr,
            mass: 5000.0,
            hfd: 3.0,
            peak_val: 20000,
            saturated: false,
        }
    }

    #[test]
    fn multi_star_candidates_rejects_below_af_min_snr() {
        let cands = vec![cand(10.0, 10.0, 20.0), cand(100.0, 10.0, 4.0)];
        let out = multi_star_candidates(cands, 6.0);
        assert_eq!(out.len(), 1);
        assert_eq!((out[0].x, out[0].y), (10.0, 10.0));
    }

    #[test]
    fn multi_star_candidates_dedups_within_25px_keeps_brighter_first_occurrence() {
        // Input is brightest-first (as auto_find returns); the second
        // candidate is 20px from the first (< 25) -> rejected as a
        // duplicate of the already-accepted (brighter) first one.
        let cands = vec![
            cand(100.0, 100.0, 30.0),
            cand(115.0, 100.0, 25.0), // 15px away -> duplicate, dropped
            cand(300.0, 300.0, 20.0), // far away -> kept
        ];
        let out = multi_star_candidates(cands, 6.0);
        assert_eq!(out.len(), 2);
        assert_eq!((out[0].x, out[0].y), (100.0, 100.0));
        assert_eq!((out[1].x, out[1].y), (300.0, 300.0));
    }

    #[test]
    fn multi_star_candidates_boundary_25px_not_a_duplicate() {
        // Exactly 25px apart: `< 25.0` is false at the boundary -> both
        // survive (dossier §2.6; star.cpp:713's strict `<`).
        let cands = vec![cand(0.0, 0.0, 30.0), cand(25.0, 0.0, 20.0)];
        let out = multi_star_candidates(cands, 6.0);
        assert_eq!(out.len(), 2);
    }

    #[test]
    fn primary_and_secondaries_drops_candidates_ahead_of_primary() {
        // 4 candidates; primary is chosen at index 1 (e.g. index 0 was
        // rejected during selection as saturated). Index 0 must be
        // dropped entirely; 1..end survive with offsets relative to the
        // primary.
        let cands = vec![
            cand(0.0, 0.0, 50.0),     // ahead of primary -> dropped
            cand(100.0, 100.0, 20.0), // primary
            cand(110.0, 90.0, 10.0),  // secondary
            cand(80.0, 130.0, 8.0),   // secondary
        ];
        let (primary, secondaries) = primary_and_secondaries(&cands, 1, 12);
        assert_eq!((primary.x, primary.y), (100.0, 100.0));
        assert_eq!(secondaries.len(), 2);
        assert_eq!((secondaries[0].x, secondaries[0].y), (110.0, 90.0));
        assert_eq!((secondaries[1].x, secondaries[1].y), (80.0, 130.0));
    }

    #[test]
    fn primary_and_secondaries_caps_total_list_at_max_stars() {
        let cands = vec![
            cand(0.0, 0.0, 50.0), // primary
            cand(1.0, 0.0, 40.0), // kept (within cap)
            cand(2.0, 0.0, 30.0), // kept (within cap)
            cand(3.0, 0.0, 20.0), // dropped (cap = 3 total)
        ];
        let (primary, secondaries) = primary_and_secondaries(&cands, 0, 3);
        assert_eq!((primary.x, primary.y), (0.0, 0.0));
        assert_eq!(
            secondaries.len(),
            2,
            "capped to max_stars=3 total incl. primary"
        );
        assert_eq!((secondaries[0].x, secondaries[0].y), (1.0, 0.0));
        assert_eq!((secondaries[1].x, secondaries[1].y), (2.0, 0.0));
    }

    #[test]
    fn primary_and_secondaries_single_candidate_no_secondaries() {
        let cands = vec![cand(5.0, 5.0, 20.0)];
        let (primary, secondaries) = primary_and_secondaries(&cands, 0, 12);
        assert_eq!((primary.x, primary.y), (5.0, 5.0));
        assert!(secondaries.is_empty());
    }
}
