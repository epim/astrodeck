// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: standard dense linear solver supporting the weighted
// least-squares normal equations (dossier §6.2) and the Levenberg-Marquardt
// step (dossier §6.4) reimplemented from docs/native-parity/algorithms/
// nina-autofocus.md. No code copied from NINA/Accord.

//! Small dense linear-algebra helpers (hand-rolled, no external crates).
//!
//! Only what the fits need: solving a small `n x n` linear system via
//! Gauss-Jordan elimination with partial pivoting. Values are `f64`.

/// Solve `a * x = b` for `x` by Gauss-Jordan elimination with partial
/// pivoting. `a` is row-major `n x n`, `b` has length `n`. Returns `None`
/// if the matrix is singular (a zero pivot column). Inputs are consumed.
#[allow(clippy::needless_range_loop)] // explicit indices track pivot rows/cols
pub fn solve(mut a: Vec<Vec<f64>>, mut b: Vec<f64>) -> Option<Vec<f64>> {
    let n = b.len();
    debug_assert_eq!(a.len(), n);
    for col in 0..n {
        // Partial pivot: largest magnitude in this column at or below the diagonal.
        let mut pivot = col;
        let mut best = a[col][col].abs();
        for r in (col + 1)..n {
            let v = a[r][col].abs();
            if v > best {
                best = v;
                pivot = r;
            }
        }
        if best == 0.0 {
            return None;
        }
        a.swap(col, pivot);
        b.swap(col, pivot);
        let diag = a[col][col];
        for r in 0..n {
            if r == col {
                continue;
            }
            let factor = a[r][col] / diag;
            if factor == 0.0 {
                continue;
            }
            for c in col..n {
                a[r][c] -= factor * a[col][c];
            }
            b[r] -= factor * b[col];
        }
    }
    Some((0..n).map(|i| b[i] / a[i][i]).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn solves_2x2() {
        // [[2,1],[1,3]] x = [3,5] -> x = [0.8, 1.4]
        let x = solve(vec![vec![2.0, 1.0], vec![1.0, 3.0]], vec![3.0, 5.0]).unwrap();
        assert!((x[0] - 0.8).abs() < 1e-12);
        assert!((x[1] - 1.4).abs() < 1e-12);
    }

    #[test]
    fn singular_returns_none() {
        assert!(solve(vec![vec![1.0, 2.0], vec![2.0, 4.0]], vec![1.0, 2.0]).is_none());
    }
}
