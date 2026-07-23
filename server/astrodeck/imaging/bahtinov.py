"""Bahtinov-mask focus analysis (NOV-12).

A Bahtinov mask makes three diffraction spikes on a bright star: two outer spikes
that cross in an X, and a central spike that bisects them. At best focus the
central spike passes through the X's crossing point; defocus shifts it sideways.
The signed perpendicular distance from the crossing to the central spike is the
focus error, in pixels, and flips sign as you pass through focus.

Pure numpy (no scipy): the spike lines are found with a hand-rolled discrete
Radon transform (one weighted np.bincount per angle over a small ROI)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .livestack import brightest_centroid
from .stars import detect_stars


def _radon(roi, angles_rad, cx, cy):
    """Discrete Radon over normal-angles. Returns ``(R, rhos)`` with
    ``R.shape == (len(angles_rad), len(rhos))`` and ``R[i, j]`` the summed
    intensity of pixels whose signed distance ``round(x·cosφ + y·sinφ)`` — measured
    from ``(cx, cy)`` — maps to ``rhos[j]``."""
    h, w = roi.shape
    yy, xx = np.mgrid[0:h, 0:w]
    x = (xx.ravel() - cx)
    y = (yy.ravel() - cy)
    wts = roi.ravel().astype(np.float64)
    R = int(np.ceil(np.hypot(max(cx, w - cx), max(cy, h - cy)))) + 1
    rhos = np.arange(-R, R + 1)
    out = np.empty((len(angles_rad), len(rhos)), dtype=np.float64)
    for i, phi in enumerate(angles_rad):
        idx = np.rint(x * np.cos(phi) + y * np.sin(phi)).astype(np.intp) + R
        out[i] = np.bincount(idx, weights=wts, minlength=len(rhos))[:len(rhos)]
    return out, rhos


def _subpixel_peak(profile):
    """``(argmax as float via 3-point parabola, peak value)``. Integer argmax at
    the profile ends where a parabola cannot be fit."""
    j = int(np.argmax(profile))
    v = float(profile[j])
    if 0 < j < len(profile) - 1:
        a, b, c = float(profile[j - 1]), v, float(profile[j + 1])
        denom = (a - 2 * b + c)
        if denom != 0:
            return j + 0.5 * (a - c) / denom, v
    return float(j), v


def _spike_angles(strength, angles_rad, *, n=3, min_sep_rad=np.deg2rad(5.0)):
    """Indices of the ``n`` strongest, ``>= min_sep_rad``-separated local maxima of
    ``strength`` (greedy by descending strength, circular angle separation)."""
    order = np.argsort(strength)[::-1]
    picked: list[int] = []
    for k in order:
        phi = angles_rad[k]
        if all(_circ_sep(phi, angles_rad[p]) >= min_sep_rad for p in picked):
            picked.append(int(k))
            if len(picked) == n:
                break
    return picked


def _circ_sep(a, b):
    """Smallest separation between two normal-angles on the [0, π) circle."""
    d = abs(a - b) % np.pi
    return min(d, np.pi - d)


def _smooth_profile(profile, k=3):
    """Light box de-alias of a Radon ρ-profile before the sub-pixel parabola.

    The discrete Radon (``np.rint`` binning) leaves a fine comb on a ρ-profile
    when a spike lies near a grid-aligned angle — the 45°/135° diagonal is the
    worst case, where ``x·cos45 + y·sin45 = (x + y)/√2`` aliases integer pixels
    into an uneven bin pattern. That comb biases a raw 3-point parabola on the
    exact diagonal by up to ~0.8 px (an in-focus star would then read defocused).
    A 3-tap box average centers the peak without shifting it, so the parabola
    reads the true ρ; non-diagonal angles are already unbiased and are unmoved."""
    if k <= 1 or profile.size < k:
        return profile
    ker = np.ones(k, dtype=np.float64) / k
    return np.convolve(profile, ker, mode="same")


def _intersect(line_a, line_b):
    """Intersection of two lines ``n·p = ρ``, each ``(nx, ny, ρ)``. ``None`` when
    the lines are near-parallel (degenerate 2×2 solve)."""
    nax, nay, ra = line_a
    nbx, nby, rb = line_b
    det = nax * nby - nay * nbx
    if abs(det) < 1e-9:
        return None
    x = (ra * nby - rb * nay) / det
    y = (nax * rb - nbx * ra) / det
    return (float(x), float(y))


def bahtinov_offset(roi, cx, cy, *, n_angles=360, core_mask_px=8.0):
    """Fit 3 spikes in a background-subtracted ROI centered at ``(cx, cy)``.

    Returns ``(signed_offset_px | None, spike_angles_deg[3], valid, reason)``: the
    central spike's signed perpendicular offset from the crossing of the two outer
    spikes (0 == in focus; sign flips across best focus)."""
    a = np.asarray(roi, dtype=np.float64)
    a = a - float(np.median(a))
    a = np.clip(a, 0.0, None)
    # zero the saturated core so the star blob doesn't dominate the Radon
    h, w = a.shape
    yy, xx = np.mgrid[0:h, 0:w]
    core = (xx - cx) ** 2 + (yy - cy) ** 2 <= core_mask_px ** 2
    a = a.copy()
    a[core] = 0.0

    angles = np.linspace(0.0, np.pi, n_angles, endpoint=False)
    R, rhos = _radon(a, angles, cx, cy)
    strength = R.max(axis=1) - np.median(R, axis=1)
    idx = _spike_angles(strength, angles, n=3)
    if len(idx) < 3:
        return None, [], False, "need three spikes — point at a bright star through the mask"

    # Validity floor (design §1.3 step 6): the three picked angles must be real
    # line prominences, not the arbitrary argmax of a flat/starless frame. Each
    # must stand above both a fraction of the strongest spike AND the background
    # strength level — a blank frame (all-zero strength) is rejected right here.
    smax = float(strength.max())
    smed = float(np.median(strength))
    floor = max(0.15 * smax, 2.0 * smed)
    if smax <= 0.0 or min(float(strength[k]) for k in idx) < floor:
        return None, [round(np.rad2deg(float(angles[k])), 2) for k in idx], False, \
            "spikes too weak — point at a bright star through the mask"

    lines = []          # (nx, ny, rho, angle_rad)
    for k in idx:
        jf, _ = _subpixel_peak(_smooth_profile(R[k]))
        rho = float(jf) - (len(rhos) - 1) / 2.0
        phi = float(angles[k])
        lines.append((np.cos(phi), np.sin(phi), rho, phi))
    lines.sort(key=lambda L: L[3])

    # central = circular-median angle (middle after sorting the 3, no wrap for a
    # real mask); outer = the flanking pair
    central = lines[1]
    outer = (lines[0], lines[2])
    # symmetry gate: the flankers must sit ~equidistant in angle from the central
    sep_l = _circ_sep(central[3], outer[0][3])
    sep_r = _circ_sep(central[3], outer[1][3])
    if abs(sep_l - sep_r) > np.deg2rad(8.0) or min(sep_l, sep_r) < np.deg2rad(4.0):
        return None, [round(np.rad2deg(L[3]), 2) for L in lines], False, \
            "spikes not symmetric — reseat the mask and recenter the star"

    v = _intersect(outer[0][:3], outer[1][:3])
    if v is None:
        return None, [round(np.rad2deg(L[3]), 2) for L in lines], False, \
            "outer spikes parallel — recenter the star"
    ncx, ncy, rc, _ = central
    offset = ncx * v[0] + ncy * v[1] - rc      # signed distance of vertex from central line
    return float(offset), [round(np.rad2deg(L[3]), 2) for L in lines], True, "ok"


@dataclass
class BahtinovResult:
    """Per-frame Bahtinov focus verdict + the geometry behind it.

    ``offset_px`` is the signed central-spike offset (None when invalid);
    ``in_focus`` is ``|offset| <= tol_px`` (False when invalid); ``side`` is the
    unambiguous geometric side of the crossing the central spike sits on;
    ``direction`` is the rig-calibrated IN/OUT word (``side`` flipped by ``invert``).
    """
    valid: bool
    offset_px: float | None       # signed; None when invalid
    in_focus: bool                # |offset| <= tol_px  (False when invalid)
    side: str | None              # "left" | "right" | None (geometric)
    direction: str | None         # "in" | "out" | None (side flipped by invert)
    angles_deg: list[float]       # the 3 spike angles (empty when invalid)
    center: tuple[float, float] | None
    tol_px: float
    reason: str                   # plain-language status / why-invalid

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "offset_px": (round(self.offset_px, 2) if self.offset_px is not None else None),
            "in_focus": self.in_focus,
            "side": self.side,
            "direction": self.direction,
            "angles_deg": [round(a, 1) for a in self.angles_deg],
            "tol_px": self.tol_px,
            "reason": self.reason,
        }


def analyze_bahtinov(data, center=None, *, stars=None, half=128,
                     tol_px=1.5, invert=False) -> BahtinovResult:
    """Full analyzer: pick the brightest star if ``center`` is None, crop a square
    ROI, run :func:`bahtinov_offset`, and build the verdict. Never raises on a
    starless/blank frame — returns ``BahtinovResult(valid=False, ...)``."""
    def _invalid(reason, angles=()):
        return BahtinovResult(False, None, False, None, None, list(angles),
                              None, tol_px, reason)
    h, w = data.shape
    if center is None:
        if stars is None:
            stars = detect_stars(data)
        center = brightest_centroid(stars)
        if center is None:
            return _invalid("no bright star found — center a star through the mask")
    cx, cy = float(center[0]), float(center[1])
    x0 = int(round(cx)) - half
    y0 = int(round(cy)) - half
    x0 = max(0, min(x0, w - 1)); y0 = max(0, min(y0, h - 1))
    x1 = min(w, x0 + 2 * half); y1 = min(h, y0 + 2 * half)
    roi = data[y0:y1, x0:x1]
    if roi.shape[0] < 32 or roi.shape[1] < 32:
        return _invalid("star too close to the edge — recenter it")
    off, angles, valid, reason = bahtinov_offset(roi, cx - x0, cy - y0)
    if not valid:
        return _invalid(reason, angles)
    in_focus = abs(off) <= tol_px
    side = None if in_focus else ("left" if off < 0 else "right")
    direction = None
    if side is not None:
        pair = ("out", "in") if not invert else ("in", "out")
        direction = pair[0] if side == "left" else pair[1]
    reason = ("locked — you're focused" if in_focus
              else f"middle spike {abs(off):.1f} px {side} of the crossing")
    return BahtinovResult(valid, off, in_focus, side, direction, angles,
                          (cx, cy), tol_px, reason)
