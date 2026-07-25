"""Conversion-gain (e-/ADU) measurement by the mean-variance / photon-transfer
method — the auto-learn path for cameras whose driver reports no EGAIN.

Native adapters read e-/ADU straight off the SDK (Player One ``get_egain``, ZWO
``ElecPerADU``); Alpaca and NINA do not, so ``Camera.egain`` stays ``0.0`` and
the FITS EGAIN card is omitted. This module recovers it from frames the user can
already take: a matched pair of flats plus a pair of biases.

Shot noise on a signal of ``S`` electrons has variance ``S``; in ADU that is
``var = S/g / g`` for gain ``g`` e-/ADU, so ``g = mean_ADU / var_ADU``. Frames
are differenced pairwise (the exact idiom ``imaging/readnoise.py`` already
establishes) so the fixed-pattern component — per-pixel gain and offset
structure, which is NOT noise — cancels:

    signal_mean = mean(flat1) + mean(flat2) - mean(bias1) - mean(bias2)
    signal_var  = var(flat1 - flat2) - var(bias1 - bias2)
    egain       = signal_mean / signal_var          # the /2 cancels in the ratio

MEASUREMENT VALIDITY is the user's job: the flats must be matched (same
illumination, exposure, gain) and UNSATURATED. A saturated or perfectly flat
pair collapses the variance, which the degenerate guard below rejects rather
than returning a wild number.

MORE FRAMES ACTUALLY HELP: the caller's ``count`` is honoured. Frames are split
into DISJOINT pairs (0,1), (2,3), … and the MEDIAN of the per-pair ratios is
returned, so a noisy mean-variance estimate really does average down instead of
the extra exposures being silently discarded. With exactly 2+2 this is the
single pair above — identical to the original result.
"""
from __future__ import annotations

import numpy as np


def measure_egain(flats: list, biases: list) -> float:
    """e-/ADU from >=2 matched flats and >=2 bias frames (mean-variance).

    Uses ``min(len(flats), len(biases)) // 2`` disjoint pairs and returns the
    MEDIAN per-pair ratio — a user who asks for 10 frames gets a 5-pair median,
    not a 1-pair answer with 16 wasted exposures.

    Raises ``ValueError`` when there are too few frames, or when NO pair yields
    a positive fixed-pattern-corrected signal variance and mean (saturated /
    identical flats) — never a silently bogus gain."""
    f = [np.asarray(a, dtype=np.float64) for a in flats]
    b = [np.asarray(a, dtype=np.float64) for a in biases]
    if len(f) < 2:
        raise ValueError("measure_egain needs at least 2 flat frames")
    if len(b) < 2:
        raise ValueError("measure_egain needs at least 2 bias frames")

    n_pairs = min(len(f), len(b)) // 2
    ratios: list[float] = []
    bad_var = bad_mean = 0
    for k in range(n_pairs):
        i, j = 2 * k, 2 * k + 1
        signal_mean = (float(np.mean(f[i])) + float(np.mean(f[j]))
                       - float(np.mean(b[i])) - float(np.mean(b[j])))
        signal_var = (float(np.var(f[i] - f[j])) - float(np.var(b[i] - b[j])))
        # Reject a bad PAIR, not the whole run: one saturated pair in ten must
        # not throw away nine good ones.
        if not np.isfinite(signal_var) or signal_var <= 0.0:
            bad_var += 1
            continue
        if not np.isfinite(signal_mean) or signal_mean <= 0.0:
            bad_mean += 1
            continue
        ratios.append(signal_mean / signal_var)

    if not ratios:
        if bad_mean and not bad_var:
            raise ValueError(
                "no signal above bias — the flats must be brighter than the "
                "bias frames (aim for roughly half full well)")
        raise ValueError(
            "degenerate variance — check the flat illumination isn't saturated "
            "or perfectly flat (the two flats must differ only by shot noise)")
    return float(np.median(ratios))
