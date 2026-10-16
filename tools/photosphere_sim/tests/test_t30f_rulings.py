# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The scorer rulings of T30F: S48 steep bins, S49 effective focal, S50 observability.

Every class names the mutant it must catch. They are fast: the scorer's
horizon function is called with arrays, the focal function with a hand-made
trajectory, and the visibility builder with flat-colour worlds.
"""

from __future__ import annotations

import math
import unittest
from types import SimpleNamespace

import numpy as np

from sim import frames_post, score, visibility
from sim.geometry import Camera, look_basis
from tests.test_visibility import CAM, covered_bins, horizon, sweep, world_frame


# --------------------------------------------------------------------------
# S48: a steep Measured bin is graded against the truth maximum over the bin
# --------------------------------------------------------------------------

TRUTH_N = 3600


def steep_case(steep_bins, top=33.0, ground=5.0):
    """Truth: ``ground`` everywhere, and in each steep bin a ramp ``ground`` .. ``top`` over its five samples."""
    truth = np.full(TRUTH_N, ground)
    profile = np.full(score.PROFILE_BINS, ground)
    for b in steep_bins:
        truth[b * 5:b * 5 + 5] = np.linspace(ground, top, 5)
        profile[b] = top
    return {"bins": TRUTH_N, "alt_max": truth.tolist()}, profile


def staircase(profile, offset=0.0):
    """A polyline that sits on ``profile + offset`` over each bin, ramping in 0.002 degree at the bin edges."""
    points = []
    for i, v in enumerate(profile):
        points.append((i * 0.5 + 0.001, float(v) + offset))
        points.append((i * 0.5 + 0.499, float(v) + offset))
    return points


def score_steep(steep_bins, offset):
    reference, profile = steep_case(steep_bins)
    parsed = {"profile": profile, "traced": profile.copy(),
              "state": np.zeros(score.PROFILE_BINS, dtype=np.int64),
              "points": staircase(profile, offset), "tau": 0.25, "note": None}
    return score._score_horizon_v2(reference, parsed, None)


class SteepBins(unittest.TestCase):
    """PROOF (S48): one steep bin, an honest conservative line passes and the same line 3 degrees high fails.

    The truth over the steep bin climbs 28 degrees in five samples; the line a conservative profile must publish is
    the bin maximum. 30 such bins are 4 percent of the samples, so p99 sees them.

    MUTATION: the rule removed (each sample graded against its own truth). The honest line then misses the ramp
    samples by up to 28 degrees: ``max`` and, with 30 bins, p99 fail.
    """

    def test_one_steep_bin_honest_passes_and_three_degrees_high_fails(self):
        honest = score_steep([100], 0.0)["signed_error_deg"]
        high = score_steep([100], 3.0)["signed_error_deg"]
        self.assertEqual(score_steep([100], 0.0)["steep_bins_graded"], 1)
        self.assertLess(honest["max"], score.GATE_HORIZON_P99)
        self.assertAlmostEqual(high["max"], 3.0, places=6)
        self.assertGreaterEqual(high["max"], score.GATE_HORIZON_P99)

    def test_thirty_steep_bins_reach_the_percentile_gates(self):
        bins = list(range(50, 650, 20))
        honest = score_steep(bins, 0.0)
        high = score_steep(bins, 3.0)
        self.assertEqual(honest["steep_bins_graded"], 30)
        self.assertLess(honest["signed_error_deg"]["p99"], score.GATE_HORIZON_P99)
        self.assertLess(honest["signed_error_deg"]["p95"], score.GATE_HORIZON_P95)
        self.assertGreaterEqual(high["signed_error_deg"]["p99"], score.GATE_HORIZON_P99)

    def test_a_gentle_bin_is_still_graded_sample_by_sample(self):
        # A bin that climbs 1.5 degrees (under the 2 degree range) is not steep: a flat line at its maximum is 1.5
        # high at the low samples, and that error is charged.
        truth = np.full(TRUTH_N, 5.0)
        truth[500:505] = np.linspace(5.0, 6.5, 5)
        profile = np.full(score.PROFILE_BINS, 5.0)
        profile[100] = 6.5
        parsed = {"profile": profile, "traced": profile.copy(),
                  "state": np.zeros(score.PROFILE_BINS, dtype=np.int64),
                  "points": staircase(profile), "tau": 0.25, "note": None}
        got = score._score_horizon_v2({"bins": TRUTH_N, "alt_max": truth.tolist()}, parsed, None)
        self.assertEqual(got["steep_bins_graded"], 0)
        self.assertAlmostEqual(got["signed_error_deg"]["max"], 1.5, places=6)

    def test_an_unresolved_steep_bin_is_not_counted(self):
        reference, profile = steep_case([100])
        state = np.zeros(score.PROFILE_BINS, dtype=np.int64)
        state[100] = score.STATE_UNKNOWN
        parsed = {"profile": np.where(state == 0, profile, 90.0), "traced": profile.copy(), "state": state,
                  "points": staircase(np.where(state == 0, profile, 90.0)), "tau": 0.25, "note": None}
        self.assertEqual(score._score_horizon_v2(reference, parsed, None)["steep_bins_graded"], 0)


# --------------------------------------------------------------------------
# S49: the effective focal
# --------------------------------------------------------------------------

READOUT_MS = 25.0
PITCH = 22.98


def pan_frames(rate_deg_s, seconds=6.0, fps=30.0, pitch=PITCH):
    """Truth records of a constant pan: the keys the scorer's focal path reads."""
    frames = []
    for k in range(int(seconds * fps)):
        t = 1000.0 * k / fps
        b = look_basis(rate_deg_s * t / 1000.0, pitch)
        frames.append({"frame_id": f"f{k:06d}", "t_capture_ms": t,
                       "right": [float(v) for v in b.right], "up": [float(v) for v in b.up],
                       "forward": [float(v) for v in b.forward]})
    return frames


def camera_dict():
    return {"width": CAM.width, "height": CAM.height, "fx": CAM.fx}


def sim_scale(rate_deg_s, pitch=PITCH):
    """The simulator's own scale for a constant pan, from its own rate function (not the scorer's)."""
    def pose_at(t_ms):
        return None, look_basis(rate_deg_s * t_ms / 1000.0, pitch)
    h_fov = 2.0 * math.degrees(math.atan2(CAM.width / 2.0, CAM.fx))
    rate = frames_post.horizontal_rate_deg_s(pose_at, 3000.0)
    return frames_post.rolling_shutter_scale(rate, READOUT_MS, h_fov)


def dot_centroid(row, lo, hi):
    xs = np.arange(lo, hi)
    w = row[lo:hi]
    return float((xs * w).sum() / w.sum())


class EffectiveFocal(unittest.TestCase):
    """PROOF (S49): without a rolling shutter the effective focal is fx; with one it matches the simulator's own warp.

    The warp is measured on a synthetic pair of dots, 100 pixels apart about the centre, through
    ``frames_post.resample`` at the scale the simulator computes for 20 degrees per second: the spacing out over the
    spacing in is what the derivation calls fx_effective over fx, to 0.1 percent.

    MUTATION: grade against fx again (``effective_fx`` read as ``truth_fx``): ``error_fraction`` of a result that closed
    at the effective focal is then 1.2 percent, and the first test below fails.
    """

    def test_no_readout_gives_fx_exactly(self):
        frames = pan_frames(20.0)
        ids = [f["frame_id"] for f in frames]
        self.assertEqual(score._effective_fx(camera_dict(), 0.0, frames, ids), CAM.fx)
        self.assertEqual(frames_post.effective_focal(CAM.fx, [20.0, 20.0], [10.0], 0.0, 41.1), CAM.fx)

    def test_a_still_camera_gives_fx_with_a_readout(self):
        frames = pan_frames(0.0)
        ids = [f["frame_id"] for f in frames]
        self.assertAlmostEqual(score._effective_fx(camera_dict(), READOUT_MS, frames, ids), CAM.fx, places=9)

    def test_the_derived_shrink_matches_the_simulators_warp_within_a_tenth_of_a_percent(self):
        s = sim_scale(20.0)
        self.assertGreater(s, 1.009)
        width, height = 360, 8
        image = np.zeros((height, width, 3), dtype=np.float32)
        xs = np.arange(width)
        for u in (-50.0, 50.0):
            image[:] += np.exp(-0.5 * ((xs - (width / 2.0 - 0.5 + u)) / 2.0) ** 2)[None, :, None] * 200.0
        out = frames_post.resample(image, width, height, s)
        row = out[0, :, 0]
        left = dot_centroid(row, 100, 180)
        right = dot_centroid(row, 180, 260)
        measured = (right - left) / 100.0
        h_fov = 2.0 * math.degrees(math.atan2(CAM.width / 2.0, CAM.fx))
        rate = frames_post.horizontal_rate_deg_s(
            lambda t_ms: (None, look_basis(20.0 * t_ms / 1000.0, PITCH)), 3000.0)
        predicted = frames_post.effective_focal(CAM.fx, [rate, rate], [10.0], READOUT_MS, h_fov) / CAM.fx
        self.assertLess(abs(measured - predicted) / predicted, 1e-3, (measured, predicted))

    def test_the_scorer_grades_against_the_effective_focal_and_reports_the_true_error(self):
        frames = pan_frames(20.0)
        ids = [f["frame_id"] for f in frames]
        effective = score._effective_fx(camera_dict(), READOUT_MS, frames, ids)
        s = sim_scale(20.0)
        self.assertAlmostEqual(effective / CAM.fx, 1.0 / s, delta=2e-4)
        diagnostics = {"focal": {"f_norm": effective / CAM.width, "state": "closed"},
                       "keyframes": [{"frame_id": i} for i in ids]}
        block = score._score_focal(diagnostics, camera_dict(), READOUT_MS, frames)
        self.assertLess(block["error_fraction"], 1e-9)
        self.assertGreater(block["error_fraction_true_fx"], 0.010)
        self.assertLess(block["error_fraction_true_fx"], 0.014)
        self.assertEqual(block["truth_fx"], CAM.fx)
        self.assertAlmostEqual(block["effective_fx"], effective)

    def test_a_reverse_pan_stretches_instead_of_shrinking(self):
        frames = pan_frames(-20.0)
        ids = [f["frame_id"] for f in frames]
        self.assertGreater(score._effective_fx(camera_dict(), READOUT_MS, frames, ids), CAM.fx)


# --------------------------------------------------------------------------
# S50: blueness and texture join luma in the observability mask
# --------------------------------------------------------------------------

def coloured_frame(basis, boundary, sky_rgb, ground_rgb, texture_on=None, amplitude=0.0):
    """A world frame in colour: sky above ``boundary`` degrees, ground below, with an optional grey checker texture."""
    from tests.test_visibility import altitudes
    above = (altitudes(basis) > boundary)[..., None]
    plane = np.where(above, np.array(sky_rgb, dtype=np.float64), np.array(ground_rgb, dtype=np.float64))
    if amplitude:
        h, w = plane.shape[:2]
        checker = ((np.indices((h, w)).sum(axis=0) % 2) * 2.0 - 1.0)[..., None] * amplitude
        if texture_on == "ground":
            checker = np.where(above, 0.0, checker)
        plane = plane + checker
    return np.clip(np.rint(plane), 0, 255).astype(np.uint8)


def build(frames, images, noise_sigma=2.0):
    return visibility.build(frames, images, CAM, horizon(15.0), noise_sigma)


def share_visible(data):
    return sum(data["visible"][b] for b in covered_bins()) / len(covered_bins())


SKY_RGB = (100, 150, 210)       # luma 141.9, blueness +68.1
TREE_RGB = (90, 180, 60)        # luma 139.4, blueness -79.4: 2.5 levels of luma apart, 147 of blueness


class ObservabilityChannels(unittest.TestCase):
    """PROOF (S50): a blue-sky / green-tree boundary at equal luma is observable by blueness, and was not by luma;
    a rough surface under a smooth sky of the same luma and colour is observable by texture; a boundary with
    nothing different across it stays unobservable.

    MUTATION: drop the blueness channel (the first test fails), drop the texture channel (the second fails). Either
    channel counted for a boundary that has neither (the third fails).
    """

    def test_blue_sky_over_a_green_tree_at_equal_luma_is_observable_by_blueness(self):
        frames = sweep()
        images = [coloured_frame(f.basis, 15.0, SKY_RGB, TREE_RGB) for f in frames]
        data = build(frames, images)
        luma_only = [data["contrast_sigma"][b] for b in covered_bins()]
        self.assertTrue(all(v < visibility.VISIBLE_SIGMA for v in luma_only), max(luma_only))
        self.assertEqual(share_visible(data), 1.0)
        for b in covered_bins():
            self.assertGreater(data["contrast_blue_sigma"][b], 30.0)

    def test_a_rough_ground_under_a_smooth_sky_of_the_same_luma_and_colour_is_observable_by_texture(self):
        frames = sweep()
        grey = (128, 128, 128)
        images = [coloured_frame(f.basis, 15.0, grey, grey, "ground", 30.0) for f in frames]
        data = build(frames, images)
        for b in covered_bins():
            self.assertLess(data["contrast_sigma"][b], visibility.VISIBLE_SIGMA)
            self.assertLess(data["contrast_blue_sigma"][b], visibility.VISIBLE_SIGMA)
            self.assertGreater(data["contrast_texture_sigma"][b], visibility.VISIBLE_SIGMA)
        self.assertEqual(share_visible(data), 1.0)

    def test_equal_luma_colour_and_texture_on_both_sides_stays_unobservable(self):
        frames = sweep()
        grey = (128, 128, 128)
        images = [coloured_frame(f.basis, 15.0, grey, grey, None, 30.0) for f in frames]
        data = build(frames, images)
        for b in covered_bins():
            self.assertLess(data["contrast_sigma"][b], visibility.VISIBLE_SIGMA)
            self.assertLess(data["contrast_blue_sigma"][b], visibility.VISIBLE_SIGMA)
            self.assertLess(data["contrast_texture_sigma"][b], visibility.VISIBLE_SIGMA)
        self.assertEqual(share_visible(data), 0.0)

    def test_a_luma_boundary_is_visible_as_before(self):
        frames = sweep()
        images = [world_frame(f.basis, 15.0, 170, 70) for f in frames]
        data = build(frames, images)
        self.assertEqual(share_visible(data), 1.0)
        for b in covered_bins():
            self.assertAlmostEqual(data["contrast_sigma"][b], 50.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()
