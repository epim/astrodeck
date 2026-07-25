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
"""
from __future__ import annotations

import numpy as np


def measure_egain(flats: list, biases: list) -> float:
    """e-/ADU from >=2 matched flats and >=2 bias frames (mean-variance).

    Raises ``ValueError`` when there are too few frames, or when the
    fixed-pattern-corrected signal variance is non-positive (saturated /
    identical flats) — never a silently bogus gain."""
    f = [np.asarray(a, dtype=np.float64) for a in flats]
    b = [np.asarray(a, dtype=np.float64) for a in biases]
    if len(f) < 2:
        raise ValueError("measure_egain needs at least 2 flat frames")
    if len(b) < 2:
        raise ValueError("measure_egain needs at least 2 bias frames")

    signal_mean = (float(np.mean(f[0])) + float(np.mean(f[1]))
                   - float(np.mean(b[0])) - float(np.mean(b[1])))
    signal_var = (float(np.var(f[0] - f[1])) - float(np.var(b[0] - b[1])))

    if not np.isfinite(signal_var) or signal_var <= 0.0:
        raise ValueError(
            "degenerate variance — check the flat illumination isn't saturated "
            "or perfectly flat (the two flats must differ only by shot noise)")
    if not np.isfinite(signal_mean) or signal_mean <= 0.0:
        raise ValueError(
            "no signal above bias — the flats must be brighter than the bias "
            "frames (aim for roughly half full well)")
    return signal_mean / signal_var
