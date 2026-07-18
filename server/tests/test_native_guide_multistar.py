"""Smoke tests for multi-star guide-star search + tracking (dossier
docs/native-parity/algorithms/phd2-guiding.md §2.6/§4; P3-T1).

Exercises the `astrodeck_native` PyO3 surface directly (mirroring
`test_native_guide_surface.py`'s pattern), NOT the async `NativeGuider`
wrapper: `NativeGuider._build_engine_config`'s config allowlist does not
(yet) forward `max_stars` to the Rust engine, so a `NativeGuider`-based test
would silently stay single-star. Skipped entirely when the compiled wheel is
not installed (build with `maturin develop --release -m
native/crates/astrodeck-native/Cargo.toml`, run from `server/`).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

native = pytest.importorskip(
    "astrodeck_native",
    reason="native engine wheel not installed (build with maturin develop)",
)


def _multi_gaussian_frame(w, h, stars, bg=100.0, amp=8000.0, sg=1.8):
    """A `w x h` uint16 frame: flat background `bg` plus one Gaussian bump
    per `(cx, cy)` in `stars` (all sharing the same amplitude/sigma unless
    a star tuple supplies its own), summed additively and clamped to
    uint16 range. Mirrors `select_golden.rs`'s `multi_gaussian_frame`."""
    acc = np.full((h, w), float(bg))
    yy, xx = np.mgrid[0:h, 0:w]
    for star in stars:
        cx, cy = star[0], star[1]
        a = star[2] if len(star) > 2 else amp
        s = star[3] if len(star) > 3 else sg
        acc += a * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * s * s)))
    return np.clip(acc, 0, 65535).astype(np.uint16)


# A primary plus two well-separated secondaries: pairwise distances all
# exceed both the 25px dedup radius (dossier §2.6) and the default
# search-box-conflict distance (search_region 15 + 5 = 20px), so all three
# survive `auto_find`'s merge/conflict/edge filtering as distinct peaks.
_PRIMARY = (120.0, 120.0)
_SECONDARY_A = (190.0, 70.0)  # offset from primary: (70, -50), hypot ~86
_SECONDARY_B = (60.0, 190.0)  # offset from primary: (-60, 70), hypot ~92


def _jitter(frame_idx, star_idx, axis):
    """Small deterministic sub-pixel perturbation (~0.03px), standing in for
    real measurement noise. Without it, a synthetic field drawn from EXACT
    pixel coordinates can converge to a bit-identical repeat of a star's own
    reference position — which dossier §4's hot-pixel heuristic (an EXACT
    zero-on-both-axes displacement, `refine.rs`'s `refine_offset`) correctly
    treats as a hot pixel and erases on real, always-slightly-noisy data.
    That heuristic firing here would be an artifact of a too-clean synthetic
    field, not a bug — this jitter keeps the simulation realistic enough to
    avoid it. Deterministic (no RNG), so the test stays reproducible."""
    return 0.03 * math.sin(1.7 * frame_idx + 2.3 * star_idx + axis * 0.9)


def _field(dx=0.0, dy=0.0, w=260, h=260, frame_idx=0):
    raw = [
        (_PRIMARY[0] + dx, _PRIMARY[1] + dy, 8000.0),
        (_SECONDARY_A[0] + dx, _SECONDARY_A[1] + dy, 5000.0),
        (_SECONDARY_B[0] + dx, _SECONDARY_B[1] + dy, 3200.0),
    ]
    stars = [
        (cx + _jitter(frame_idx, k, 0), cy + _jitter(frame_idx, k, 1), amp)
        for k, (cx, cy, amp) in enumerate(raw)
    ]
    return _multi_gaussian_frame(w, h, stars)


def test_guide_star_find_multi_star_returns_at_least_two_candidates():
    frame = _field()
    stars, meta = native.guide_star_find(frame, {"max_stars": 12})
    assert len(stars) >= 2, f"stars={stars}"
    assert "sat_thresh" in meta
    # Brightest-first: the primary (amp 8000) leads.
    assert abs(stars[0]["x"] - _PRIMARY[0]) < 0.6
    assert abs(stars[0]["y"] - _PRIMARY[1]) < 0.6


def _ident_cal():
    # Orthogonal, unit-parity calibration (matches test_native_guide_surface.py's
    # `_ident_cal`): camera_to_mount reduces to the identity, so a pulse's
    # sign/magnitude maps directly and predictably onto a camera-frame
    # correction for this test's closed-loop simulation below.
    return {
        "x_rate": 0.02,
        "y_rate": 0.02,
        "x_angle": 0.0,
        "y_angle": math.pi / 2,
        "y_angle_error": 0.0,
        "declination": 0.0,
        "pier_side": "west",
        "ra_parity": "even",
        "dec_parity": "even",
        "rotator_angle": 0.0,
        "binning": 1,
        "is_valid": True,
    }


def test_multi_star_guiding_converges_on_synthetic_drift():
    """A `GuideEngine` with multi-star enabled (`max_stars > 1`), guiding a
    synthetic field of a primary + 2 secondaries that all drift together
    (as a real mount-tracking error would move the whole sky), still drives
    the primary's offset from lock back toward zero over successive frames
    — the RefineOffset-augmented offset feeds the same per-axis algorithms
    the single-star e2e test (`test_native_guider_e2e.py`) already proves
    converge, so this is the multi-star analogue: "extend the e2e"."""
    e = native.GuideEngine({"max_stars": 12, "search_region": 15})
    e.load_calibration(_ident_cal())
    e.begin_guiding()

    # Frame 0: establish the lock via a full multi-star acquisition pass.
    a0 = e.process(_field(0.0, 0.0), 0.0, 2.0)
    assert a0["action"] == "idle"
    assert len(e.stats()["secondaries"]) == 2, (
        "multi-star acquisition must have found both secondaries: "
        f"{e.stats()['secondaries']}"
    )

    # Inject a one-shot disturbance: the whole field (primary + both
    # secondaries) shifts together by (dx, dy), as a real unguided mount
    # drift would move every star in the frame identically.
    offset = [8.0, -6.0]
    t = 2.0
    initial_dist = math.hypot(*offset)

    for i in range(1, 61):
        frame = _field(offset[0], offset[1], frame_idx=i)
        action = e.process(frame, t, 2.0)
        t += 2.0

        if action["action"] != "pulse_pair":
            continue
        # Closed-loop simulation (test-only physics, not upstream): a
        # WEST/SOUTH pulse of `ms` corrects a positive x/y error by
        # `ms * rate` px; EAST/NORTH corrects a negative one. Move the
        # synthetic field's offset toward zero by that amount, capped so
        # it never overshoots past zero — mirroring what a real mount
        # would do in response to the issued pulse.
        ra = action["ra"]
        if ra is not None:
            step = min(ra["ms"] * 0.02, abs(offset[0]))
            offset[0] += -step if ra["dir"] == "west" else step
        dec = action["dec"]
        if dec is not None:
            step = min(dec["ms"] * 0.02, abs(offset[1]))
            offset[1] += -step if dec["dir"] == "south" else step

    final_dist = math.hypot(*offset)
    assert final_dist < initial_dist * 0.3, (
        f"multi-star guiding did not converge: initial={initial_dist:.2f}px "
        f"final={final_dist:.2f}px offset={offset}"
    )
    # The secondaries must still be tracked at the end (not erased/dropped
    # by a spurious hot-pixel or panic-guard path over the whole run).
    assert len(e.stats()["secondaries"]) == 2
