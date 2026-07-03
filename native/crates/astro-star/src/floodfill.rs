// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (§4). No code
// copied from NINA/Hocus Focus.

//! Candidate collection: the exact flood-fill scan over the binarized map
//! (dossier §4). Grows down/right only; the whole bounding box is zeroed so
//! nothing inside a candidate is revisited.

use crate::image::{Rect, WorkImage};

/// Foreground threshold in the binarized map (dossier §4, `ZERO_THRESHOLD`).
pub const ZERO: f64 = 0.001;

/// A connected candidate region: its bounding box and the collected structure
/// points (which may include gap-bridged pixels on bbox-expanding rows).
#[derive(Clone, Debug)]
pub struct Candidate {
    /// bounding box (correct even when the point list overshoots).
    pub bounds: Rect,
    /// collected `(x, y)` structure points.
    pub points: Vec<(usize, usize)>,
}

/// Scan the binarized `map` (consumed/mutated) into candidate regions
/// (dossier §4). The last row and last column never start a candidate.
pub fn collect_candidates(map: &mut WorkImage) -> Vec<Candidate> {
    let width = map.width;
    let height = map.height;
    if width < 2 || height < 2 {
        return Vec::new();
    }
    let mut candidates = Vec::new();

    for y_top in 0..height - 1 {
        for x_left in 0..width - 1 {
            if map.at(x_left, y_top) < ZERO {
                continue;
            }
            let mut points: Vec<(usize, usize)> = Vec::new();
            let mut bx = x_left;
            let by = y_top;
            let mut bw = 1usize;
            let bh: usize;
            let x = x_left;
            let mut y = y_top;

            loop {
                let mut row_added = 0usize;
                if map.at(x, y) >= ZERO {
                    points.push((x, y));
                    row_added += 1;
                }
                // extend LEFT (only if the anchor pixel was foreground)
                let mut row_start = x;
                if row_added > 0 {
                    while row_start > 0 && map.at(row_start - 1, y) >= ZERO {
                        row_start -= 1;
                        points.push((row_start, y));
                        row_added += 1;
                    }
                }
                // extend RIGHT, with gap-bridging while searching for the row's
                // first foreground pixel inside the current bounds.
                let mut row_end = x;
                let bounds_right = bx + bw; // exclusive
                while row_end < width - 1 {
                    if map.at(row_end + 1, y) < ZERO {
                        if row_added > 0 || row_end >= bounds_right {
                            break;
                        }
                        row_end += 1; // skip gap while searching
                    } else {
                        row_end += 1;
                        points.push((row_end, y));
                        row_added += 1;
                    }
                }
                // grow bbox
                if row_start < bx {
                    bw += bx - row_start;
                    bx = row_start;
                }
                if row_end > bx + bw - 1 {
                    bw += row_end - (bx + bw - 1);
                }
                if row_added == 0 {
                    bh = y - by;
                    break;
                }
                if y == height - 1 {
                    bh = y - by + 1;
                    break;
                }
                y += 1;
            }

            let bounds = Rect {
                x: bx,
                y: by,
                w: bw,
                h: bh.max(1),
            };
            // zero the whole bounding box so nothing inside is revisited
            for yy in bounds.y..bounds.bottom() {
                for xx in bounds.x..bounds.right() {
                    *map.at_mut(xx, yy) = 0.0;
                }
            }
            candidates.push(Candidate { bounds, points });
        }
    }
    candidates
}
