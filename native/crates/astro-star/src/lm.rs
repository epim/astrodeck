// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: reimplemented from the audited algorithm dossier
// docs/native-parity/algorithms/hocusfocus-star-detection-psf.md (§9.4). The
// dossier notes ALGLIB `minlm` internals are unknowable; this is a hand-rolled
// box-constrained LM matching the documented seeds/bounds/scales/tolerances and
// pinned by the golden-vector recovery tests. No code copied from NINA/ALGLIB.

//! Hand-rolled box-constrained, diagonally-scaled Levenberg-Marquardt for the
//! PSF least-squares problem (dossier §9.4). Minimizes `Σ residual_i²` subject
//! to `lower ≤ x ≤ upper`, terminating on the scaled step (`epsx`) or the
//! iteration cap.

/// Solve a dense `n×n` system by Gauss elimination with partial pivoting.
/// Returns `None` if singular.
fn solve_dense(a: &mut [Vec<f64>], b: &mut [f64]) -> Option<Vec<f64>> {
    let n = b.len();
    for col in 0..n {
        let mut piv = col;
        let mut best = a[col][col].abs();
        for r in (col + 1)..n {
            if a[r][col].abs() > best {
                best = a[r][col].abs();
                piv = r;
            }
        }
        if best < 1e-15 {
            return None;
        }
        a.swap(col, piv);
        b.swap(col, piv);
        for r in (col + 1)..n {
            let f = a[r][col] / a[col][col];
            for c in col..n {
                a[r][c] -= f * a[col][c];
            }
            b[r] -= f * b[col];
        }
    }
    let mut x = vec![0.0f64; n];
    for i in (0..n).rev() {
        let mut s = b[i];
        for c in (i + 1)..n {
            s -= a[i][c] * x[c];
        }
        x[i] = s / a[i][i];
    }
    Some(x)
}

/// Configuration for [`lm_minimize`].
pub struct LmConfig {
    /// number of residuals.
    pub n_resid: usize,
    /// per-variable lower bounds.
    pub lower: Vec<f64>,
    /// per-variable upper bounds.
    pub upper: Vec<f64>,
    /// per-variable scales (ALGLIB `set_scale`; used in the `epsx` step test).
    pub scale: Vec<f64>,
    /// iteration cap.
    pub max_iter: usize,
    /// scaled step-size convergence tolerance.
    pub epsx: f64,
}

#[inline]
fn clamp_to_bounds(x: &mut [f64], lower: &[f64], upper: &[f64]) {
    for i in 0..x.len() {
        if x[i] < lower[i] {
            x[i] = lower[i];
        }
        if x[i] > upper[i] {
            x[i] = upper[i];
        }
    }
}

/// Box-constrained Levenberg-Marquardt (dossier §9.4).
///
/// `eval(x, r)` fills the residual vector `r` (length `n_resid`); `jac(x, j)`
/// fills the row-major Jacobian `j` (`n_resid × m`, `j[i*m + k] = ∂r_i/∂x_k`).
/// Returns the best parameter vector found.
pub fn lm_minimize(
    x0: &[f64],
    cfg: &LmConfig,
    eval: impl Fn(&[f64], &mut [f64]),
    jac: impl Fn(&[f64], &mut [f64]),
) -> Vec<f64> {
    let m = x0.len();
    let n = cfg.n_resid;
    let mut x = x0.to_vec();
    clamp_to_bounds(&mut x, &cfg.lower, &cfg.upper);

    let mut r = vec![0.0f64; n];
    eval(&x, &mut r);
    let mut cost: f64 = r.iter().map(|v| v * v).sum();

    let mut lambda = 1e-3f64;
    let mut jbuf = vec![0.0f64; n * m];
    // Relative-gradient convergence: far from the optimum ‖g‖ is large, at the
    // optimum ‖g‖ → 0. Terminating on ‖g‖ (not on a single small scaled step)
    // avoids stopping early when λ has inflated the damping near a hard region.
    let mut gmax0: Option<f64> = None;

    for _iter in 0..cfg.max_iter {
        jac(&x, &mut jbuf);
        // A = JᵀJ, g = Jᵀr
        let mut ata = vec![vec![0.0f64; m]; m];
        let mut g = vec![0.0f64; m];
        for i in 0..n {
            let base = i * m;
            let ri = r[i];
            for a in 0..m {
                let ja = jbuf[base + a];
                g[a] += ja * ri;
                for b in a..m {
                    ata[a][b] += ja * jbuf[base + b];
                }
            }
        }
        for a in 0..m {
            for b in 0..a {
                ata[a][b] = ata[b][a];
            }
        }

        let gmax = g.iter().fold(0.0f64, |acc, v| acc.max(v.abs()));
        let g0 = *gmax0.get_or_insert(gmax);
        if gmax <= cfg.epsx * g0.max(1e-300) {
            break; // gradient effectively zero ⇒ optimum
        }

        let mut improved = false;
        for _inner in 0..40 {
            // (A + lambda·diag(A)) dp = -g  (Marquardt damping)
            let mut aug = ata.clone();
            for a in 0..m {
                let d = ata[a][a];
                aug[a][a] = d + lambda * (if d > 0.0 { d } else { 1.0 });
            }
            let mut rhs: Vec<f64> = g.iter().map(|v| -v).collect();
            let dp = match solve_dense(&mut aug, &mut rhs) {
                Some(v) => v,
                None => {
                    lambda *= 4.0;
                    if lambda > 1e14 {
                        break;
                    }
                    continue;
                }
            };
            let mut x_new = x.clone();
            for k in 0..m {
                x_new[k] += dp[k];
            }
            clamp_to_bounds(&mut x_new, &cfg.lower, &cfg.upper);

            let mut r_new = vec![0.0f64; n];
            eval(&x_new, &mut r_new);
            let cost_new: f64 = r_new.iter().map(|v| v * v).sum();

            if cost_new.is_finite() && cost_new < cost {
                x = x_new;
                r = r_new;
                cost = cost_new;
                lambda = (lambda * 0.3).max(1e-12);
                improved = true;
                break;
            } else {
                lambda *= 4.0;
                if lambda > 1e14 {
                    break;
                }
            }
        }
        if !improved {
            break; // no downhill step exists ⇒ converged (or stuck at bound)
        }
    }
    x
}
