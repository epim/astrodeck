"""Read-noise measurement from bias frames — the photon-free acceptance test
for a camera's read modes (e.g. Player One LRN vs Normal, or the HCG transition
at gain 125). Read noise in electrons = sigma(ADU) * eGain.

With >=2 bias frames we use the difference method: std(frame_i - frame_j)/sqrt(2)
removes the fixed-pattern (per-pixel offset) component and, taken spatially over
the whole frame, is an unbiased estimator of the temporal read noise. A single
frame falls back to spatial std (includes fixed pattern — less accurate)."""
from __future__ import annotations

import numpy as np


def read_noise_e(frames: list[np.ndarray], egain_e_per_adu: float) -> float:
    """Read noise in electrons from one or more bias frames."""
    arrs = [np.asarray(f, dtype=np.float64) for f in frames]
    if not arrs:
        raise ValueError("read_noise_e needs at least one frame")
    if len(arrs) >= 2:
        diffs = [arrs[i + 1] - arrs[i] for i in range(len(arrs) - 1)]
        sigma_adu = float(np.mean([np.std(d) / np.sqrt(2.0) for d in diffs]))
    else:
        sigma_adu = float(np.std(arrs[0]))
    return sigma_adu * float(egain_e_per_adu)


def compare_modes(normal: dict, low: dict) -> dict:
    """Compare two read-mode captures. Each dict is {"frames": [...],
    "egain": float}. Returns per-mode read noise (e-) and whether ``low`` is the
    lower-noise mode (the LRN acceptance criterion)."""
    normal_e = read_noise_e(normal["frames"], normal["egain"])
    low_e = read_noise_e(low["frames"], low["egain"])
    return {"normal_e": normal_e, "low_e": low_e, "improved": low_e < normal_e}
