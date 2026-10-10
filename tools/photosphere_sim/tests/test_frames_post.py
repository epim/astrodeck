# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Frame realism (``sim.frames_post``, ``sim.render.render_exposures``) against
what a phone camera does to a frame (the panorama scanner's spec 13.5).

Each proof of task T20 is a class here, and each names the mutant it must
catch. The effects are tested one at a time: a block that switches the others
off (no stabiliser, no noise, a pinned auto-exposure) isolates the one under
test, so a failure names its cause. The renderer is a small analytic stand-in
for the Three.js one, because what is under test is the post-processing and
not the picture: an anti-aliased step edge for the blur kernel, a horizontal
ramp for the rolling-shutter scale, a hard-edged texture for the gradient
energy (the chart yard's own content is hard-edged discs and stripes, and a
smooth texture would not show the blur the same way). Two classes at the end
use the real renderer and skip, with the reason, when Chromium cannot launch.

The cases that are built from a case definition (the legacy bytes, the new
files) use the flat renderer or a recording stand-in.

Mutants named by the task: (a) one sub-frame instead of four, (b) the
rolling-shutter scale forced to 1, (c) the EIS clamp removed.
"""

from __future__ import annotations

import json
import math
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

from sim import cases, frames_post, trajectory
from sim import render as render_module
from sim.frames_post import (AutoExposure, FramePost, eis_offsets, horizontal_rate_deg_s,
                             offset_basis, resample, resolve_frames, subframe_times)
from sim.geometry import Camera, angle_between, look_basis
from sim.trajectory import FrameTruth

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The S25-like camera of the realism cases: 180 x 320 portrait, 41.14 degrees
#: across the short axis, so a degree is 4.19 pixels at the centre.
CAM = Camera(180, 320, 41.14)
POS = np.array([0.0, 0.0, 1.4])
PX_PER_DEG = CAM.fx * math.pi / 180.0
H_FOV_DEG = 2.0 * math.degrees(math.atan2(CAM.width / 2.0, CAM.fx))

#: Spec 13.5, typed out again so that a change to the defaults is a change to
#: this file as well.
SPEC_13_5 = {"exposure_ms": 4, "subframes": 4,
             "ae": {"min_gain": 0.6, "max_gain": 1.6, "tau_s": 0.5, "target_luma": 118},
             "noise_sigma": 2.0, "rolling_shutter_ms": 25,
             "eis": {"tau_s": 0.2, "margin_deg": 1.8, "jitter_deg": 0.05},
             "aliasing": None}

#: A block with every effect off. Tests overlay the one they exercise.
OFF = {"exposure_ms": 0, "subframes": 1, "ae": None, "eis": None, "noise_sigma": 0,
       "rolling_shutter_ms": 0, "aliasing": None}


def block(**kwargs):
    return {**OFF, **kwargs}


# ---------------------------------------------------------------------------
# Trajectories and stand-in renderers
# ---------------------------------------------------------------------------


def make_traj(rate_deg_s=0.0, alt=0.0, fps=30, duration_ms=3000, az0=0.0):
    """A camera panning clockwise at a constant rate, extended past both ends.

    ``pose_at`` does not clamp, so a frame at t = 0 has a whole exposure
    window around it and the tests can look at frame 0 of a fresh ``FramePost``.
    """
    def pose_at(t_ms):
        return POS, look_basis(az0 + rate_deg_s * t_ms / 1000.0, alt)

    frames = []
    for k in range(int(duration_ms * fps / 1000) + 1):
        t = int(round(k * 1000 / fps))
        basis = pose_at(t)[1]
        frames.append(FrameTruth(frame_id=f"f{k:06d}", t_capture_ms=t, position=POS, basis=basis,
                                 az=az0 + rate_deg_s * t / 1000.0, alt=alt,
                                 angular_rate_deg_s=abs(rate_deg_s)))
    return SimpleNamespace(frames=frames, pose_at=pose_at, duration_ms=float(duration_ms))


def _ray_cache():
    cache = {}

    def rays(camera):
        key = (camera.width, camera.height)
        if key not in cache:
            u, v = np.meshgrid(np.arange(camera.width) + 0.5, np.arange(camera.height) + 0.5)
            ray = np.stack([(u - camera.cx) / camera.fx, -(v - camera.cy) / camera.fy,
                            np.ones_like(u)], axis=-1)
            cache[key] = ray / np.linalg.norm(ray, axis=-1, keepdims=True)
        return cache[key]

    return rays


def _world(rays, basis):
    """Per-pixel azimuth and altitude (degrees) of the camera's rays."""
    d = rays @ np.vstack([basis.right, basis.up, basis.forward])
    return (np.degrees(np.arctan2(d[..., 0], d[..., 1])),
            np.degrees(np.arcsin(np.clip(d[..., 2], -1.0, 1.0))))


def _grey(plane):
    return np.repeat(np.clip(np.rint(plane), 0, 255).astype(np.uint8)[..., None], 3, axis=2)


def flat_renderer(value):
    def render(scene, camera, frames):
        for _ in frames:
            yield np.full((camera.height, camera.width, 3), value, dtype=np.uint8)
    return render


def edge_renderer(az_edge_deg, softness_px=1.5):
    """A vertical edge at a fixed world azimuth, smooth across ``softness_px``.

    The camera looks at the horizon, so the edge is a vertical image line at
    ``cx + fx tan(edge - view azimuth)``; a smooth profile keeps the pixel
    phase of the edge out of the kernel measurement.
    """
    def render(scene, camera, frames):
        cols = np.arange(camera.width) + 0.5
        for frame in frames:
            f = frame.basis.forward
            view_az = math.degrees(math.atan2(f[0], f[1]))
            u_edge = camera.cx + camera.fx * math.tan(math.radians(az_edge_deg - view_az))
            row = 127.5 * (1.0 + np.tanh((cols - u_edge) / softness_px))
            yield _grey(np.broadcast_to(row[None, :], (camera.height, camera.width)))
    return render


def ramp_renderer(scene, camera, frames):
    """A horizontal ramp, 0 at the left edge, the same in every frame."""
    for _ in frames:
        row = np.arange(camera.width) * (255.0 / camera.width)
        yield _grey(np.broadcast_to(row[None, :], (camera.height, camera.width)))


def texture_renderer(kind, seed=3, wavelength_deg=(1.5, 9.0)):
    """A texture that is a function of the view direction only.

    ``hard`` thresholds a sum of plane waves into two levels, which gives the
    hard edges the chart yard's discs and stripes have; ``smooth`` is the sum
    itself; ``fine`` is the sum with short wavelengths, for the aliasing test.
    """
    rng = np.random.default_rng(seed)
    gain = {"smooth": 14.0, "fine": 5.0}.get(kind)
    waves = []
    for _ in range(14):
        wavelength = rng.uniform(*wavelength_deg)
        theta = rng.uniform(0.0, math.pi)
        k = 2.0 * math.pi / wavelength
        waves.append((k * math.cos(theta), k * math.sin(theta),
                      rng.uniform(0.0, 2.0 * math.pi), rng.uniform(0.5, 1.5)))
    rays = _ray_cache()

    def render(scene, camera, frames):
        grid = rays(camera)
        for frame in frames:
            az, alt = _world(grid, frame.basis)
            g = sum(w * np.sin(kx * az * math.cos(math.radians(23.0)) + ky * alt + phase)
                    for kx, ky, phase, w in waves)
            yield _grey(128.0 + (90.0 * np.sign(g) if kind == "hard" else gain * g))
    return render


def develop_each(post, renderer, count=None):
    """Yield every frame (or the first ``count``) of ``post``, developed in order."""
    exposures = post.exposures()
    if count is not None:
        exposures = exposures[:count]
    for k, images in enumerate(render_module.render_exposures(
            renderer, None, CAM, exposures, scale=post.factor)):
        yield post.develop(k, images)


def develop_all(post, renderer, count=None):
    """The frames :func:`develop_each` yields, as a list."""
    return list(develop_each(post, renderer, count))


def develop_first(blk, traj, renderer, seed=1):
    post = FramePost(blk, CAM, traj, seed)
    images = list(renderer(None, post.render_camera, post.requests(0)))
    return post, post.develop(0, images)


# ---------------------------------------------------------------------------
# The block
# ---------------------------------------------------------------------------


class ResolveFrames(unittest.TestCase):
    def test_the_defaults_are_spec_13_5_verbatim(self):
        """MUTATION: change any default in ``FRAMES_DEFAULTS``."""
        self.assertEqual(resolve_frames({}), SPEC_13_5)
        self.assertEqual(frames_post.FRAMES_DEFAULTS, SPEC_13_5)

    def test_a_given_key_wins_and_nested_blocks_merge(self):
        spec = resolve_frames({"exposure_ms": 33, "ae": {"tau_s": 1.0},
                               "aliasing": {"factor": 4}})
        self.assertEqual(spec["exposure_ms"], 33)
        self.assertEqual(spec["ae"], {"min_gain": 0.6, "max_gain": 1.6, "tau_s": 1.0,
                                      "target_luma": 118})
        self.assertEqual(spec["aliasing"], {"factor": 4})
        self.assertEqual(spec["eis"], SPEC_13_5["eis"])

    def test_the_argument_is_left_alone(self):
        given = {"ae": {"tau_s": 1.0}}
        resolve_frames(given)
        self.assertEqual(given, {"ae": {"tau_s": 1.0}})

    def test_ae_and_eis_can_be_switched_off(self):
        spec = resolve_frames({"ae": None, "eis": None})
        self.assertIsNone(spec["ae"])
        self.assertIsNone(spec["eis"])

    def test_a_block_that_cannot_mean_anything_raises(self):
        bad = [
            "not a dict", {"exposure": 4}, {"exposure_ms": -1}, {"subframes": 0},
            {"subframes": 2.5}, {"subframes": True}, {"noise_sigma": -1},
            {"rolling_shutter_ms": -1}, {"ae": {"min_gain": 2.0, "max_gain": 1.0}},
            {"ae": {"tau_s": 0}}, {"ae": {"gain": 1}}, {"eis": {"tau_s": 0}},
            {"eis": {"margin_deg": -1}}, {"aliasing": {"factor": 1}},
            {"aliasing": {"factor": 2.5}}, {"aliasing": {"scale": 4}}, {"aliasing": 4},
        ]
        for case in bad:
            with self.subTest(block=case):
                with self.assertRaises(ValueError):
                    resolve_frames(case)


class SubframeTimesAreCentredOnTheCaptureTime(unittest.TestCase):
    def test_equal_slices_sampled_at_their_midpoints(self):
        """MUTATION: place the taps at the start of each slice."""
        times = subframe_times(1000.0, 16.0, 4)
        self.assertTrue(np.allclose(times, [994.0, 998.0, 1002.0, 1006.0]))
        self.assertAlmostEqual(float(times.mean()), 1000.0)

    def test_one_subframe_is_the_capture_time(self):
        self.assertTrue(np.allclose(subframe_times(500.0, 33.0, 1), [500.0]))


class OffsetBasis(unittest.TestCase):
    def test_yaw_turns_toward_right_and_pitch_toward_up(self):
        base = look_basis(30.0, 10.0)
        turned = offset_basis(base, 1.0, 0.0)
        self.assertGreater(float(turned.forward @ base.right), 0.0)
        self.assertAlmostEqual(angle_between(turned.forward, base.forward), 1.0, places=6)
        raised = offset_basis(base, 0.0, 1.0)
        self.assertGreater(float(raised.forward @ base.up), 0.0)
        self.assertAlmostEqual(angle_between(raised.forward, base.forward), 1.0, places=6)

    def test_the_result_is_a_camera_attitude(self):
        turned = offset_basis(look_basis(200.0, 40.0, 5.0), 1.3, -0.7)
        m = np.column_stack([turned.right, turned.up, turned.forward])
        self.assertTrue(np.allclose(m.T @ m, np.eye(3), atol=1e-12))
        self.assertTrue(np.allclose(np.cross(turned.right, turned.up), -turned.forward,
                                    atol=1e-12))

    def test_no_offset_is_the_same_basis(self):
        base = look_basis(30.0, 10.0, 2.0)
        same = offset_basis(base, 0.0, 0.0)
        for name in ("right", "up", "forward"):
            self.assertTrue(np.array_equal(getattr(base, name), getattr(same, name)))


# ---------------------------------------------------------------------------
# Proof: the blur kernel (mutant a)
# ---------------------------------------------------------------------------


def _edge_kernel_variance(profile):
    """Variance of the derivative of an edge's profile, as a distribution over x."""
    d = np.diff(profile.astype(np.float64)) / 255.0
    x = np.arange(d.size) + 1.0
    mean = float((x * d).sum() / d.sum())
    return float((((x - mean) ** 2) * d).sum() / d.sum())


def box_equivalent_length_px(rate_deg_s, exposure_ms, subframes):
    """The length of the box whose variance equals the blur's, in pixels.

    The edge is the same smooth step in both frames, so the difference of the
    two derivative variances is the variance the blur added; a box of length L
    has variance L^2 / 12.
    """
    traj = make_traj(rate_deg_s)
    renderer = edge_renderer(az_edge_deg=0.0)
    _, sharp = develop_first(block(), traj, renderer)
    _, blurred = develop_first(block(exposure_ms=exposure_ms, subframes=subframes), traj, renderer)
    row = CAM.height // 2
    extra = _edge_kernel_variance(blurred[row, :, 0]) - _edge_kernel_variance(sharp[row, :, 0])
    return math.sqrt(12.0 * max(extra, 0.0))


class ExposureBlurKernel(unittest.TestCase):
    """PROOF: the blur kernel length equals rate x exposure x px/deg within 10 %.

    Four midpoint taps over an exposure E have a box-equivalent length of
    sqrt(12 x 15 / 12) / 4 = 0.968 E, so the expected ratio is 0.968, and 0.9
    to 1.1 is the band.

    MUTATION (a): ``FramePost`` takes one sub-frame instead of four. The kernel
    is then a point and the ratio is 0.
    """

    def test_the_kernel_length_is_rate_times_exposure_times_pixels_per_degree(self):
        for rate, exposure in ((40.0, 33.0), (20.0, 66.0), (40.0, 66.0)):
            with self.subTest(rate=rate, exposure_ms=exposure):
                expected = rate * exposure / 1000.0 * PX_PER_DEG
                measured = box_equivalent_length_px(rate, exposure, 4)
                self.assertGreater(measured, 0.9 * expected)
                self.assertLess(measured, 1.1 * expected)
                self.assertAlmostEqual(measured / expected, 0.968, delta=0.03)

    def test_the_kernel_is_linear_in_the_exposure(self):
        short = box_equivalent_length_px(40.0, 33.0, 4)
        long = box_equivalent_length_px(40.0, 66.0, 4)
        self.assertAlmostEqual(long / short, 2.0, delta=0.1)

    def test_a_still_camera_is_not_blurred(self):
        self.assertLess(box_equivalent_length_px(0.0, 33.0, 4), 0.05)

    def test_the_subframes_are_drawn_at_the_exposure_slices(self):
        """The renders come from the truth pose at each slice midpoint."""
        traj = make_traj(40.0)
        post = FramePost(block(exposure_ms=40, subframes=4), CAM, traj, 1)
        k = 30
        requests = post.requests(k)
        self.assertEqual(len(requests), 4)
        azimuths = [math.degrees(math.atan2(r.basis.forward[0], r.basis.forward[1]))
                    for r in requests]
        t_ms = traj.frames[k].t_capture_ms
        expected = [40.0 * (t_ms + dt) / 1000.0 for dt in (-15.0, -5.0, 5.0, 15.0)]
        self.assertTrue(np.allclose(azimuths, expected, atol=1e-6))
        self.assertEqual([r.frame_id for r in requests],
                         [f"f{k:06d}-s{j}" for j in range(4)])


# ---------------------------------------------------------------------------
# Proof: rolling shutter (mutant b)
# ---------------------------------------------------------------------------


def _ramp_slope_ratio(rate_deg_s, readout_ms, alt=0.0):
    traj = make_traj(rate_deg_s, alt=alt)
    post, out = develop_first(block(rolling_shutter_ms=readout_ms), traj, ramp_renderer)
    source = next(iter(ramp_renderer(None, CAM, [None])))
    x = np.arange(CAM.width)[10:170]
    return (np.polyfit(x, out[100, 10:170, 0], 1)[0]
            / np.polyfit(x, source[100, 10:170, 0].astype(np.float64), 1)[0]), post


class RollingShutterStretch(unittest.TestCase):
    """PROOF: the rolling-shutter stretch equals omega t_r / hFoV within 5 %.

    Measured as the slope of a horizontal ramp after the frame is developed,
    against the ramp's own slope: a horizontal scale s multiplies it by s.

    MUTATION (b): ``rolling_shutter_scale`` returns 1.0. The measured stretch is
    then 0, a 100 % error.
    """

    def test_the_stretch_is_omega_times_readout_over_the_field_of_view(self):
        for rate, readout in ((20.0, 25.0), (40.0, 25.0), (20.0, 100.0)):
            with self.subTest(rate=rate, readout_ms=readout):
                expected = rate * (readout / 1000.0) / H_FOV_DEG
                ratio, post = _ramp_slope_ratio(rate, readout)
                measured = ratio - 1.0
                self.assertLess(abs(measured - expected) / expected, 0.05)
                self.assertAlmostEqual(float(post.rolling_scale[0]) - 1.0, expected, places=6)

    def test_the_spec_example_is_one_point_two_percent(self):
        """4.13: a 20 degree per second pan with a 25 ms readout is about 1.2 %."""
        ratio, _ = _ramp_slope_ratio(20.0, 25.0)
        self.assertAlmostEqual(ratio - 1.0, 0.0122, delta=0.0006)

    def test_a_pan_the_other_way_scales_the_other_way(self):
        ratio, _ = _ramp_slope_ratio(-20.0, 25.0)
        self.assertLess(ratio, 1.0)
        self.assertAlmostEqual(1.0 - ratio, 0.0122, delta=0.0006)

    def test_only_the_horizontal_part_of_the_rate_counts(self):
        """Tilted up 60 degrees, a 20 degree per second azimuth rate is 10 in the image."""
        ratio, _ = _ramp_slope_ratio(20.0, 100.0, alt=60.0)
        expected = 20.0 * math.cos(math.radians(60.0)) * 0.1 / H_FOV_DEG
        self.assertLess(abs((ratio - 1.0) - expected) / expected, 0.05)

    def test_a_still_camera_is_not_resampled_at_all(self):
        traj = make_traj(0.0)
        post, out = develop_first(block(rolling_shutter_ms=25), traj, ramp_renderer)
        source = next(iter(ramp_renderer(None, CAM, [None])))
        self.assertEqual(float(post.rolling_scale[0]), 1.0)
        self.assertTrue(np.array_equal(out, source.astype(np.float32)))

    def test_the_rate_is_the_one_the_camera_turns_at(self):
        traj = make_traj(20.0, alt=0.0)
        self.assertAlmostEqual(horizontal_rate_deg_s(traj.pose_at, 1000.0), 20.0, places=2)
        traj = make_traj(-20.0, alt=23.0)
        self.assertAlmostEqual(horizontal_rate_deg_s(traj.pose_at, 1000.0),
                               -20.0 * math.cos(math.radians(23.0)), places=2)


# ---------------------------------------------------------------------------
# Proof: EIS (mutant c)
# ---------------------------------------------------------------------------

MARGIN_DEG = 1.8
TOLERANCE = 1e-9


def pan_offsets(rate=20.0, alt=23.0, jitter=0.05, seed=1, margin=MARGIN_DEG, tau=0.2,
                duration_ms=6000):
    traj = make_traj(rate, alt=alt, duration_ms=duration_ms)
    times = [f.t_capture_ms for f in traj.frames]
    return eis_offsets(traj.pose_at, times, tau_s=tau, margin_deg=margin, jitter_deg=jitter,
                       rng=np.random.default_rng(seed))


class EisStaysWithinTheMargin(unittest.TestCase):
    """PROOF: the EIS offset stays within +-1.8 degrees.

    A pan at 20 degrees per second would leave a critically damped filter 3.7
    degrees behind (``tau`` times the rate times cos(altitude)), so the clamp is
    what holds it, and the test also asks that it bind.

    MUTATION (c): ``_clamp_disc`` returns its argument. The offset then settles
    at 3.7 degrees.
    """

    def test_the_offset_never_leaves_the_margin_on_a_pan(self):
        for rate in (20.0, 40.0, -20.0):
            with self.subTest(rate=rate):
                offsets = pan_offsets(rate=rate)
                self.assertLessEqual(float(np.hypot(offsets[:, 0], offsets[:, 1]).max()),
                                     MARGIN_DEG + TOLERANCE)
                self.assertLessEqual(float(np.abs(offsets).max()), MARGIN_DEG + TOLERANCE)

    def test_the_clamp_binds_on_a_fast_pan(self):
        offsets = pan_offsets(rate=20.0, jitter=0.0)
        self.assertGreater(float(np.hypot(offsets[:, 0], offsets[:, 1]).max()), 1.79)

    def test_the_virtual_camera_lags_a_clockwise_pan(self):
        """The view sits behind the truth: a negative yaw for a clockwise turn."""
        offsets = pan_offsets(rate=20.0, jitter=0.0)
        self.assertLess(float(offsets[100:, 0].max()), -1.7)
        offsets = pan_offsets(rate=-20.0, jitter=0.0)
        self.assertGreater(float(offsets[100:, 0].min()), 1.7)

    def test_a_slow_pan_is_not_clamped_and_lags_by_tau_times_the_rate(self):
        """5 degrees per second at pitch 0 is a 1.0 degree lag, inside the margin."""
        offsets = pan_offsets(rate=5.0, alt=0.0, jitter=0.0)
        self.assertAlmostEqual(float(offsets[120:, 0].mean()), -5.0 * 0.2, delta=0.02)

    def test_the_jitter_cannot_push_the_total_past_the_margin(self):
        offsets = pan_offsets(rate=20.0, jitter=0.3)
        self.assertLessEqual(float(np.hypot(offsets[:, 0], offsets[:, 1]).max()),
                             MARGIN_DEG + TOLERANCE)

    def test_a_zero_margin_is_no_stabiliser(self):
        offsets = pan_offsets(rate=20.0, margin=0.0)
        self.assertTrue(np.array_equal(offsets, np.zeros_like(offsets)))


class EisLagsByItsTimeConstant(unittest.TestCase):
    """PROOF: after a step the EIS view lags truth by its time constant within 20 %.

    The lag is the mean delay of the step response: the area between the step
    and the response, over the step. For two lags of ``tau / 2`` each it is
    exactly ``tau``.

    MUTATION: the filter's pole at ``1 / tau_s`` instead of ``2 / tau_s``,
    which doubles the delay.
    """

    @staticmethod
    def step_response(tau, step_deg=1.0, t_step=500.0, end_ms=3000):
        def pose_at(t_ms):
            return POS, look_basis(step_deg if t_ms >= t_step else 0.0, 0.0)

        times = list(range(0, end_ms, 10))
        offsets = eis_offsets(pose_at, times, tau_s=tau, margin_deg=1.8, jitter_deg=0.0,
                              rng=np.random.default_rng(0))
        return np.array(times), offsets[:, 0]

    def test_the_mean_delay_is_tau(self):
        for tau in (0.1, 0.2, 0.4):
            with self.subTest(tau_s=tau):
                times, offset = self.step_response(tau, end_ms=int(500 + 12000 * tau))
                after = times >= 500
                delay = -float(offset[after].sum()) * 0.010 / 1.0
                self.assertLess(abs(delay - tau) / tau, 0.2)

    def test_the_response_is_the_critically_damped_one(self):
        """A step of 1 degree, 0.2 s on: 1 - 3 exp(-2) = 0.594 of it has arrived."""
        times, offset = self.step_response(0.2)
        arrived = 1.0 + float(offset[list(times).index(700)])
        self.assertAlmostEqual(arrived, 1.0 - 3.0 * math.exp(-2.0), delta=0.02)
        self.assertLess(float(np.abs(offset[times < 500]).max()), 1e-12)

    def test_the_view_starts_locked_on_the_truth(self):
        offsets = pan_offsets(rate=20.0, jitter=0.0)
        self.assertEqual(float(np.abs(offsets[0]).max()), 0.0)


class EisIsRenderedAsAViewOffset(unittest.TestCase):
    def test_the_subframes_are_drawn_from_the_moved_view_and_the_truth_is_untouched(self):
        traj = make_traj(20.0, alt=23.0, duration_ms=6000)
        truth_before = [(f.basis.right.copy(), f.basis.up.copy(), f.basis.forward.copy())
                        for f in traj.frames]
        post = FramePost(block(eis={"tau_s": 0.2, "margin_deg": 1.8, "jitter_deg": 0.0},
                               exposure_ms=0), CAM, traj, 1)
        k = 120
        (request,) = post.requests(k)
        truth = traj.frames[k].basis
        offset = post.eis[k]
        self.assertAlmostEqual(angle_between(request.basis.forward, truth.forward),
                               math.hypot(*offset), places=6)
        self.assertLess(float(request.basis.forward @ truth.right), 0.0)
        # The offset is in the image frame: the same yaw and pitch, read back.
        self.assertAlmostEqual(math.degrees(math.asin(float(request.basis.forward @ truth.right))),
                               float(offset[0]), places=3)
        for frame, (right, up, forward) in zip(traj.frames, truth_before):
            self.assertTrue(np.array_equal(frame.basis.right, right))
            self.assertTrue(np.array_equal(frame.basis.up, up))
            self.assertTrue(np.array_equal(frame.basis.forward, forward))

    def test_eis_off_draws_the_truth_pose_exactly(self):
        traj = make_traj(20.0, alt=23.0)
        post = FramePost(block(exposure_ms=0), CAM, traj, 1)
        (request,) = post.requests(40)
        self.assertTrue(np.array_equal(request.basis.forward, traj.frames[40].basis.forward))
        self.assertTrue(np.array_equal(request.basis.right, traj.frames[40].basis.right))


class EisJitter(unittest.TestCase):
    def test_a_still_camera_shows_the_seeded_jitter(self):
        offsets = pan_offsets(rate=0.0, jitter=0.05, duration_ms=20000)
        self.assertAlmostEqual(float(offsets[:, 0].std()), 0.05, delta=0.006)
        self.assertAlmostEqual(float(offsets[:, 1].std()), 0.05, delta=0.006)
        self.assertLess(abs(float(offsets.mean())), 0.01)

    def test_the_same_seed_gives_the_same_offsets_and_another_does_not(self):
        self.assertTrue(np.array_equal(pan_offsets(seed=4), pan_offsets(seed=4)))
        self.assertFalse(np.array_equal(pan_offsets(seed=4), pan_offsets(seed=5)))

    def test_changing_the_jitter_scales_the_same_draws(self):
        """The stream does not shift when the amplitude changes (still camera)."""
        a = pan_offsets(rate=0.0, jitter=0.05, seed=2, duration_ms=2000)
        b = pan_offsets(rate=0.0, jitter=0.10, seed=2, duration_ms=2000)
        self.assertTrue(np.allclose(b, 2.0 * a))


# ---------------------------------------------------------------------------
# Proof: auto-exposure
# ---------------------------------------------------------------------------


class AutoExposureGain(unittest.TestCase):
    """PROOF: the auto-exposure gain stays within 0.6 to 1.6.

    MUTATION: ``AutoExposure._limit`` returns its argument; a dark scene then
    asks for a gain of 118 / 5 and the lag walks toward it.
    """

    def test_a_dark_scene_saturates_at_the_maximum_and_a_bright_one_at_the_minimum(self):
        for value, limit in ((5, 1.6), (250, 0.6)):
            with self.subTest(luma=value):
                traj = make_traj(0.0, duration_ms=8000)
                post = FramePost(block(ae={"min_gain": 0.6, "max_gain": 1.6, "tau_s": 0.5,
                                           "target_luma": 118}), CAM, traj, 1)
                for _ in develop_each(post, flat_renderer(value)):
                    pass
                self.assertGreaterEqual(min(post.gains), 0.6)
                self.assertLessEqual(max(post.gains), 1.6)
                self.assertAlmostEqual(post.gains[-1], limit, places=3)

    def test_a_scene_at_the_target_holds_its_gain_at_one(self):
        traj = make_traj(0.0, duration_ms=8000)
        post = FramePost(block(ae={"min_gain": 0.6, "max_gain": 1.6, "tau_s": 0.5,
                                   "target_luma": 118}), CAM, traj, 1)
        for _ in develop_each(post, flat_renderer(118)):
            pass
        self.assertAlmostEqual(post.gains[-1], 1.0, places=3)

    def test_the_gain_moves_toward_the_wanted_one_with_the_time_constant(self):
        """One time constant covers 1 - 1/e of the distance, however it is sliced."""
        for dt in (0.5, 0.1, 0.02):
            with self.subTest(dt=dt):
                ae = AutoExposure(0.6, 1.6, 0.5, 118.0, initial_gain=1.0)
                luma = 118.0 / 1.5
                for _ in range(int(round(0.5 / dt))):
                    gain = ae.step(luma, dt)
                self.assertAlmostEqual(gain, 1.0 + 0.5 * (1.0 - math.exp(-1.0)), places=9)

    def test_the_gain_is_applied_to_the_frame(self):
        traj = make_traj(0.0, duration_ms=3000)
        post = FramePost(block(ae={"min_gain": 0.6, "max_gain": 1.6, "tau_s": 0.5,
                                   "target_luma": 118}), CAM, traj, 1)
        for k, frame in enumerate(develop_each(post, flat_renderer(60))):
            self.assertAlmostEqual(float(frame.mean()), 60.0 * post.gains[k], places=3)

    def test_the_first_gain_is_seeded_near_one(self):
        ae = {"min_gain": 0.6, "max_gain": 1.6, "tau_s": 0.5, "target_luma": 118}
        starts = {}
        for seed in range(6):
            start = FramePost(block(ae=ae), CAM, make_traj(0.0), seed)._ae.gain
            self.assertTrue(0.9 <= start <= 1.1)
            starts[seed] = start
        self.assertGreater(len(set(starts.values())), 1)
        self.assertEqual(FramePost(block(ae=ae), CAM, make_traj(0.0), 3)._ae.gain, starts[3])

    def test_ae_off_is_a_gain_of_one(self):
        post = FramePost(block(), CAM, make_traj(0.0, duration_ms=500), 1)
        for _ in develop_each(post, flat_renderer(60)):
            pass
        self.assertEqual(set(post.gains), {1.0})


# ---------------------------------------------------------------------------
# Proof: noise
# ---------------------------------------------------------------------------


class NoiseSigma(unittest.TestCase):
    """PROOF: the noise standard deviation is 2 +- 0.2.

    MUTATION: the noise added before the gain, which scales its sd by the gain
    (the dark-scene case below reads 3.2); or one independent draw per channel,
    which makes the luma sd 1.34 (the luma assertion below).
    """

    def frames(self, value=128, sigma=2.0, count=6, seed=1, ae=None):
        traj = make_traj(0.0, duration_ms=int(count * 1000 / 30))
        post = FramePost(block(noise_sigma=sigma, ae=ae), CAM, traj, seed)
        return post, develop_all(post, flat_renderer(value), count)

    def test_the_noise_sd_is_two(self):
        _, frames = self.frames()
        noise = np.concatenate([(f - 128.0).ravel() for f in frames])
        self.assertAlmostEqual(float(noise.std()), 2.0, delta=0.2)
        self.assertAlmostEqual(float(noise.mean()), 0.0, delta=0.05)

    def test_the_luma_noise_is_the_same_two(self):
        """Luma is what the scanner measures, so the noise is on all three channels at once."""
        _, frames = self.frames()
        luma = np.concatenate([(frames_post.luma_of(f) - 128.0).ravel() for f in frames])
        self.assertAlmostEqual(float(luma.std()), 2.0, delta=0.2)

    def test_the_noise_is_after_the_gain(self):
        """A dark scene is lifted by the gain and its noise is still 2."""
        ae = {"min_gain": 0.6, "max_gain": 1.6, "tau_s": 0.05, "target_luma": 118}
        post, frames = self.frames(value=50, count=40, ae=ae)
        late = frames[-5:]
        self.assertGreater(post.gains[-1], 1.55)
        noise = np.concatenate([(f - f.mean()).ravel() for f in late])
        self.assertAlmostEqual(float(noise.std()), 2.0, delta=0.2)

    def test_frames_are_independent_and_reproducible(self):
        _, a = self.frames(seed=1)
        _, again = self.frames(seed=1)
        _, other = self.frames(seed=2)
        self.assertTrue(all(np.array_equal(x, y) for x, y in zip(a, again)))
        self.assertFalse(np.array_equal(a[0], other[0]))
        first, second = (a[0] - 128.0)[..., 0].ravel(), (a[1] - 128.0)[..., 0].ravel()
        self.assertLess(abs(float(np.corrcoef(first, second)[0, 1])), 0.02)

    def test_a_frames_noise_does_not_depend_on_which_frames_came_first(self):
        traj = make_traj(0.0, duration_ms=500)
        full = FramePost(block(noise_sigma=2.0), CAM, traj, 1)
        both = develop_all(full, flat_renderer(128), 3)
        skipping = FramePost(block(noise_sigma=2.0), CAM, traj, 1)
        skipping.seek(2)
        images = list(flat_renderer(128)(None, CAM, skipping.requests(2)))
        self.assertTrue(np.array_equal(skipping.develop(2, images), both[2]))

    def test_no_noise_is_the_render_unchanged(self):
        traj = make_traj(0.0, duration_ms=500)
        post = FramePost(block(), CAM, traj, 1)
        frames = develop_all(post, flat_renderer(77), 2)
        self.assertTrue(all(np.array_equal(f, np.full_like(f, 77.0)) for f in frames))


# ---------------------------------------------------------------------------
# Proof: gradient energy along the pan
# ---------------------------------------------------------------------------


def gradient_energy(image):
    """Mean squared horizontal difference, the direction of the pan."""
    return float((np.diff(image[..., 0].astype(np.float64), axis=1) ** 2).mean())


def blur_to_sharp_gradient_ratio(renderer, rate, exposure_ms, alt=23.0):
    traj = make_traj(rate, alt=alt)
    _, sharp = develop_first(block(), traj, renderer)
    _, blurred = develop_first(block(exposure_ms=exposure_ms, subframes=4), traj, renderer)
    return gradient_energy(blurred) / gradient_energy(sharp)


class GradientEnergyAlongThePan(unittest.TestCase):
    """PROOF: on a pan at 40 deg/s with 16 ms exposure the gradient energy along
    the pan falls below 0.7 of the sharp frame's (RI M12).

    MUTATION (a): one sub-frame. The ratio is then exactly 1.
    """

    def test_a_fast_pan_loses_its_gradient_energy(self):
        ratio = blur_to_sharp_gradient_ratio(texture_renderer("hard"), 40.0, 16.0)
        self.assertLess(ratio, 0.7)
        self.assertGreater(ratio, 0.2)

    def test_the_loss_grows_with_the_exposure_and_the_rate(self):
        hard = texture_renderer("hard")
        base = blur_to_sharp_gradient_ratio(hard, 40.0, 16.0)
        self.assertLess(blur_to_sharp_gradient_ratio(hard, 40.0, 33.0), base)
        self.assertGreater(blur_to_sharp_gradient_ratio(hard, 20.0, 16.0), base)
        self.assertGreater(blur_to_sharp_gradient_ratio(hard, 40.0, 4.0), base)

    def test_a_still_camera_loses_nothing(self):
        self.assertAlmostEqual(blur_to_sharp_gradient_ratio(texture_renderer("hard"), 0.0, 16.0),
                               1.0, places=9)


# ---------------------------------------------------------------------------
# Proof: aliasing
# ---------------------------------------------------------------------------


def high_frequency_energy(image, cutoff=0.25):
    """Energy of the red channel above ``cutoff`` cycles per pixel."""
    plane = image[..., 0].astype(np.float64)
    power = np.abs(np.fft.rfft2(plane - plane.mean())) ** 2
    radius = np.hypot(np.fft.rfftfreq(plane.shape[1])[None, :],
                      np.fft.fftfreq(plane.shape[0])[:, None])
    return float(power[radius > cutoff].sum())


class AliasingRender(unittest.TestCase):
    """PROOF: the aliasing render has more high-frequency energy than the box-filtered one.

    MUTATION: ``resample`` averages each 4 x 4 block (a box prefilter) in place
    of the bilinear sample; the two renders are then the same.
    """

    @classmethod
    def setUpClass(cls):
        cls.renderer = texture_renderer("fine", wavelength_deg=(0.4, 1.3))
        cls.traj = make_traj(0.0, alt=23.0)
        cls.post = FramePost(block(aliasing={"factor": 4}), CAM, cls.traj, 1)
        cls.high = list(cls.renderer(None, cls.post.render_camera, cls.post.requests(0)))
        cls.aliased = cls.post.develop(0, cls.high)
        cls.box = cls.high[0].astype(np.float64).reshape(
            CAM.height, 4, CAM.width, 4, 3).mean(axis=(1, 3))

    def test_the_render_is_four_times_the_size_and_the_frame_is_not(self):
        self.assertEqual(self.post.factor, 4)
        self.assertEqual((self.post.render_camera.width, self.post.render_camera.height),
                         (4 * CAM.width, 4 * CAM.height))
        self.assertEqual(self.post.render_camera.fov_short_deg, CAM.fov_short_deg)
        self.assertEqual(self.high[0].shape, (4 * CAM.height, 4 * CAM.width, 3))
        self.assertEqual(self.aliased.shape, (CAM.height, CAM.width, 3))

    def test_the_aliased_frame_has_more_high_frequency_energy_than_the_box_filtered_one(self):
        aliased = high_frequency_energy(self.aliased)
        box = high_frequency_energy(self.box)
        self.assertGreater(aliased, 1.3 * box)
        # and it is a sample of the same picture, not another one
        self.assertAlmostEqual(float(self.aliased.mean()), float(self.box.mean()), delta=1.0)

    def test_it_is_the_mean_of_the_four_central_pixels_of_each_block(self):
        """Bilinear at the block centre, with no prefilter."""
        centre = self.high[0].astype(np.float64).reshape(CAM.height, 4, CAM.width, 4, 3)
        expected = centre[:, 1:3, :, 1:3].mean(axis=(1, 3))
        self.assertTrue(np.allclose(self.aliased, expected, atol=1e-3))

    def test_no_aliasing_renders_at_the_camera_size(self):
        post = FramePost(block(), CAM, make_traj(0.0), 1)
        self.assertEqual(post.factor, 1)
        self.assertIs(post.render_camera, CAM)

    def test_resample_leaves_a_plain_image_alone(self):
        image = np.random.default_rng(0).random((CAM.height, CAM.width, 3)).astype(np.float32)
        self.assertIs(resample(image, CAM.width, CAM.height, 1.0, 1), image)


# ---------------------------------------------------------------------------
# The renderer hook
# ---------------------------------------------------------------------------


class RenderHook(unittest.TestCase):
    def test_scaled_camera_keeps_the_field_of_view(self):
        big = render_module.scaled_camera(CAM, 4)
        self.assertEqual((big.width, big.height), (720, 1280))
        self.assertEqual(big.fov_short_deg, CAM.fov_short_deg)
        self.assertAlmostEqual(big.fx, 4.0 * CAM.fx, places=9)
        self.assertIs(render_module.scaled_camera(CAM, 1), CAM)
        for bad in (0, 2.5, -1):
            with self.assertRaises(ValueError):
                render_module.scaled_camera(CAM, bad)

    def test_exposures_come_back_grouped_in_order(self):
        seen = {}

        def renderer(scene, camera, frames):
            seen["camera"] = camera
            for n, frame in enumerate(frames):
                yield np.full((camera.height, camera.width, 3), n, dtype=np.uint8)

        post = FramePost(block(exposure_ms=8, subframes=3), CAM, make_traj(10.0, duration_ms=200), 1)
        groups = list(render_module.render_exposures(renderer, None, CAM, post.exposures()))
        self.assertEqual(len(groups), len(post.exposures()))
        self.assertEqual([[int(im[0, 0, 0]) for im in g] for g in groups[:3]],
                         [[0, 1, 2], [3, 4, 5], [6, 7, 8]])
        self.assertIs(seen["camera"], CAM)

    def test_the_renderer_gets_the_scaled_camera(self):
        seen = {}

        def renderer(scene, camera, frames):
            seen["camera"] = camera
            for _ in frames:
                yield np.zeros((camera.height, camera.width, 3), dtype=np.uint8)

        post = FramePost(block(aliasing={"factor": 4}), CAM, make_traj(0.0, duration_ms=100), 1)
        list(render_module.render_exposures(renderer, None, CAM, post.exposures(),
                                            scale=post.factor))
        self.assertEqual((seen["camera"].width, seen["camera"].height), (720, 1280))

    def test_a_renderer_that_miscounts_or_misjudges_the_size_raises(self):
        post = FramePost(block(exposure_ms=8, subframes=2), CAM, make_traj(0.0, duration_ms=100), 1)

        def short(scene, camera, frames):
            for n, _ in enumerate(frames):
                if n < 3:
                    yield np.zeros((camera.height, camera.width, 3), dtype=np.uint8)

        def long(scene, camera, frames):
            for _ in frames:
                yield np.zeros((camera.height, camera.width, 3), dtype=np.uint8)
            yield np.zeros((camera.height, camera.width, 3), dtype=np.uint8)

        def small(scene, camera, frames):
            for _ in frames:
                yield np.zeros((10, 10, 3), dtype=np.uint8)

        for renderer in (short, long, small):
            with self.subTest(renderer=renderer.__name__):
                with self.assertRaises(RuntimeError):
                    list(render_module.render_exposures(renderer, None, CAM, post.exposures()))

    def test_the_renderer_is_read_to_the_end(self):
        """So a generator holding a browser open gets to close it."""
        closed = []

        def renderer(scene, camera, frames):
            try:
                for _ in frames:
                    yield np.zeros((camera.height, camera.width, 3), dtype=np.uint8)
            finally:
                closed.append(True)

        post = FramePost(block(), CAM, make_traj(0.0, duration_ms=100), 1)
        list(render_module.render_exposures(renderer, None, CAM, post.exposures()))
        self.assertEqual(closed, [True])


class FramePostContract(unittest.TestCase):
    def test_frames_must_be_developed_in_order(self):
        post = FramePost(block(), CAM, make_traj(0.0, duration_ms=200), 1)
        images = list(flat_renderer(100)(None, CAM, post.requests(1)))
        with self.assertRaises(ValueError):
            post.develop(1, images)

    def test_the_wrong_number_or_size_of_renders_raises(self):
        post = FramePost(block(exposure_ms=8, subframes=2), CAM, make_traj(0.0, duration_ms=200), 1)
        with self.assertRaises(ValueError):
            post.develop(0, [np.zeros((CAM.height, CAM.width, 3), dtype=np.uint8)])
        with self.assertRaises(ValueError):
            post.develop(0, [np.zeros((10, 10, 3), dtype=np.uint8)] * 2)

    def test_process_rounds_and_clips_to_uint8(self):
        post = FramePost(block(ae={"min_gain": 1.6, "max_gain": 1.6, "tau_s": 0.5,
                                   "target_luma": 118}), CAM, make_traj(0.0, duration_ms=100), 1)
        images = list(flat_renderer(200)(None, CAM, post.requests(0)))
        out = post.process(0, images)
        self.assertEqual(out.dtype, np.uint8)
        self.assertEqual(int(out.max()), 255)

    def test_the_exposures_are_a_lazy_sequence(self):
        post = FramePost(block(exposure_ms=8, subframes=3), CAM, make_traj(0.0, duration_ms=200), 1)
        exposures = post.exposures()
        self.assertEqual(len(exposures), len(post.times_ms))
        self.assertEqual(len(exposures[-1]), 3)
        self.assertEqual(len(exposures[1:3]), 2)
        with self.assertRaises(IndexError):
            exposures[len(exposures)]


# ---------------------------------------------------------------------------
# Cases: the new files, and the old bytes
# ---------------------------------------------------------------------------

PAN_CASE = {
    "case_id": "t20-pan", "scene": "treeline", "route": "panshort-p23",
    "camera": {"width": 180, "height": 320, "fov_short_deg": 41.14}, "fps": 10, "seed": 7,
    "expected": "positive", "profile": "realism-v1",
    "grading": {"daylight": True},
    "realism": {"frames": {"exposure_ms": 4, "subframes": 4}},
}


class RecordingFlatRenderer:
    """The flat renderer, counting what it was asked for."""

    def __init__(self):
        self.cameras = []
        self.poses = 0

    def __call__(self, scene, camera, frames):
        self.cameras.append((camera.width, camera.height))
        for _ in frames:
            self.poses += 1
            yield np.full((camera.height, camera.width, 3), 128, dtype=np.uint8)


_TRUTH_CACHE = {}


def _horizon_cache():
    """``truth.horizon`` and ``landmark_directions``, computed once per scene.

    The truth at its full resolution is half a minute of ray casting for a
    scene, and nothing these tests assert depends on its last decimals, so the
    cases below share one computation at an altitude step of 0.25 degrees. The
    legacy test does not use this: it pins the real bytes.
    """
    from sim import truth
    real_horizon, real_landmarks = truth.horizon, truth.landmark_directions

    def horizon(scene, c_ref, *args, **kwargs):
        key = ("h", scene.name, tuple(float(v) for v in c_ref))
        if key not in _TRUTH_CACHE:
            _TRUTH_CACHE[key] = real_horizon(scene, c_ref, alt_step=0.25)
        return _TRUTH_CACHE[key]

    def landmarks(scene, c_ref, *args, **kwargs):
        key = ("l", scene.name, tuple(float(v) for v in c_ref))
        if key not in _TRUTH_CACHE:
            _TRUTH_CACHE[key] = real_landmarks(scene, c_ref, *args, **kwargs)
        return _TRUTH_CACHE[key]

    return mock.patch.multiple(truth, horizon=horizon, landmark_directions=landmarks)


class FramesCase(unittest.TestCase):
    """``build_case`` with a ``frames`` block, flat renderer."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patch = _horizon_cache()
        cls.patch.start()
        cls.renderer = RecordingFlatRenderer()
        cls.out = cases.build_case(PAN_CASE, pathlib.Path(cls.tmp.name) / "a", cls.renderer)
        cls.again = cases.build_case(PAN_CASE, pathlib.Path(cls.tmp.name) / "b",
                                     RecordingFlatRenderer())
        alias = json.loads(json.dumps(PAN_CASE))
        alias["case_id"] = "t20-alias"
        alias["realism"] = {"frames": {"subframes": 2, "aliasing": {"factor": 4}}}
        cls.alias_renderer = RecordingFlatRenderer()
        cls.alias = cases.build_case(alias, pathlib.Path(cls.tmp.name) / "c", cls.alias_renderer)
        cls.n_frames = len(list((cls.out / "input" / "frames").glob("*.png")))

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.tmp.cleanup()

    def test_visibility_json_is_written_with_the_schema_of_13_8(self):
        data = json.loads((self.out / "truth" / "visibility.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(data), ["bins", "contrast_sigma", "footprint_top_deg",
                                        "noise_sigma", "visible"])
        self.assertEqual(data["bins"], 720)
        self.assertEqual(data["noise_sigma"], 2.0)
        for key in ("contrast_sigma", "footprint_top_deg", "visible"):
            self.assertEqual(len(data[key]), 720)
        self.assertTrue(all(isinstance(v, bool) for v in data["visible"]))
        self.assertTrue(any(v is not None for v in data["footprint_top_deg"]))

    def test_the_renderer_is_asked_for_every_subframe_in_one_run(self):
        self.assertEqual(self.renderer.poses, 4 * self.n_frames)
        self.assertEqual(self.renderer.cameras, [(180, 320)])

    def test_the_aliasing_case_renders_at_four_times_and_delivers_the_camera_size(self):
        from PIL import Image
        self.assertEqual(self.alias_renderer.cameras, [(720, 1280)])
        self.assertEqual(self.alias_renderer.poses, 2 * self.n_frames)
        with Image.open(self.alias / "input" / "frames" / "f000000.png") as image:
            self.assertEqual(image.size, (180, 320))
        manifest = json.loads((self.alias / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["realism"]["frames"]["aliasing"], {"factor": 4})

    def test_the_block_is_copied_into_the_manifest_as_written(self):
        manifest = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["realism"]["frames"], {"exposure_ms": 4, "subframes": 4})

    def test_the_truth_trajectory_is_the_device_pose(self):
        """EIS moves what is drawn, never what the truth records."""
        route = json.loads((ROOT / "routes" / "panshort-p23.json").read_text(encoding="utf-8"))
        traj = trajectory.build(route, 10, seed=7)
        lines = (self.out / "truth" / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines]
        self.assertEqual(len(records), len(traj.frames))
        for record, frame in zip(records, traj.frames):
            self.assertEqual(record["forward"], [float(v) for v in frame.basis.forward])

    def test_a_build_is_reproducible(self):
        a = json.loads((self.out / "manifest.json").read_text(encoding="utf-8"))["hashes"]
        b = json.loads((self.again / "manifest.json").read_text(encoding="utf-8"))["hashes"]
        self.assertEqual(a, b)

    def test_a_bad_block_fails_before_anything_is_written(self):
        bad = json.loads(json.dumps(PAN_CASE))
        bad["case_id"] = "t20-bad"
        bad["realism"] = {"frames": {"subframes": 0}}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                cases.build_case(bad, pathlib.Path(tmp), cases.flat_renderer)
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])


#: SHA-256 digests of the hashes ``build_case`` recorded with the flat renderer
#: BEFORE task T20, for a case with no ``realism`` block and for one with a
#: ``realism`` block that has no ``frames``. Like the committed fixtures'
#: own hashes they are float arithmetic written out in decimal: a platform whose
#: numpy rounds the last digit differently moves them.
LEGACY_STILL = {
    "frames": "1e95dd7a6e284b89ea6592fcb065b480ab4d773f741de2b4b3ad7fd0bc8c265b",
    "observations": "3a04ccd6f024c3d129d3fc2a55c601e24411e63953b0af2053eecca28fb7ae1b",
    "truth": "f5bb34b8e0f120fe92c2ae28737b215b712c298afe2f88d32a6fdde5fcc2fe6c"}
LEGACY_PAN_NO_FRAMES = {
    "frames": "ec4dc443c63c964b3b87bb0a315befefbd89a587a1e530b11a2ade36f45c1b07",
    "observations": "cb0907f1eb9da9a9194241279165a6cc2435b7721d0ac09f01e0fc3be860f464",
    "truth": "c75ff916bbea136f90d5fe92d3027a1647def294834a006812de282550c17b4b"}


class LegacyCasesAreUnchanged(unittest.TestCase):
    """PROOF: cases without a ``frames`` block are byte-identical to today.

    The recorded hashes are those of the code before this task, for a still
    legacy case and for a pan case whose realism block has no frames block.

    MUTATION: take the new loop for every case, or write ``visibility.json``
    for every case; the frames hash or the truth hash moves.
    """

    def build(self, case_def):
        with tempfile.TemporaryDirectory() as tmp:
            out = cases.build_case(case_def, pathlib.Path(tmp), cases.flat_renderer)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            return manifest["hashes"], sorted(
                p.relative_to(out).as_posix() for p in (out / "truth").rglob("*") if p.is_file())

    def test_a_case_with_no_realism_block(self):
        case_def = json.loads((ROOT / "cases" / "chartyard-still-60.json").read_text(encoding="utf-8"))
        hashes, truth_files = self.build(case_def)
        self.assertEqual(hashes, LEGACY_STILL)
        self.assertNotIn("truth/visibility.json", truth_files)

    def test_a_realism_block_without_a_frames_block(self):
        case_def = {k: v for k, v in PAN_CASE.items() if k not in ("realism", "grading")}
        case_def.update(case_id="t20-pan-no-frames", scene="chartyard", fps=30,
                        realism={"gyro_scale_err": 0.02})
        hashes, truth_files = self.build(case_def)
        self.assertEqual(hashes, LEGACY_PAN_NO_FRAMES)
        self.assertNotIn("truth/visibility.json", truth_files)
        self.assertIn("truth/route.json", truth_files)


# ---------------------------------------------------------------------------
# The real renderer
# ---------------------------------------------------------------------------


def _real_renderer_or_skip(scene_name, camera):
    from sim.scene import load
    try:
        renderer = render_module.ThreeRenderer(load(ROOT / "scenes" / f"{scene_name}.json"), camera)
    except Exception as error:  # noqa: BLE001 - the reason is the point
        raise unittest.SkipTest(f"headless Chromium unavailable: {error!r}")
    return renderer


class RealRendererGradientEnergy(unittest.TestCase):
    """The spec's gradient-energy proof on the simulator's own scenes, 40 deg/s,
    16 ms: frame 180 of ``pan1-fast-p23`` mid-pan, a sharp render against the
    four-sub-frame frame. Skips when Chromium cannot launch."""

    def test_the_blur_costs_the_chart_yard_and_the_treeline_over_thirty_percent(self):
        route = json.loads((ROOT / "routes" / "pan1-fast-p23.json").read_text(encoding="utf-8"))
        traj = trajectory.build(route, 30, seed=7)
        k = 180
        self.assertGreater(traj.frames[k].angular_rate_deg_s, 35.0)
        for scene_name in ("chartyard", "treeline"):
            with self.subTest(scene=scene_name):
                renderer = _real_renderer_or_skip(scene_name, CAM)
                with renderer:
                    post = FramePost(block(exposure_ms=16, subframes=4), CAM, traj, 7)
                    post.seek(k)
                    images = [renderer.render(r.position, r.basis) for r in post.requests(k)]
                    blurred = post.develop(k, images)
                    sharp = renderer.render(traj.frames[k].position, traj.frames[k].basis)
                self.assertLess(gradient_energy(blurred) / gradient_energy(sharp), 0.7)


class RealRendererBuildsACase(unittest.TestCase):
    """``build_case`` through ``CaseRenderer``: the browser starts once, the
    sub-frames are drawn, and the visibility file finds the treeline."""

    def test_a_short_pan_case_builds_and_most_of_its_horizon_is_visible(self):
        _real_renderer_or_skip("treeline", CAM).close()
        calls = []
        real_render = render_module.ThreeRenderer.render

        def counting(self, position, basis):
            calls.append(1)
            return real_render(self, position, basis)

        case_def = json.loads(json.dumps(PAN_CASE))
        case_def.update(case_id="t20-real", fps=5)
        with tempfile.TemporaryDirectory() as tmp:
            with _horizon_cache(), mock.patch.object(render_module.ThreeRenderer, "render",
                                                     counting):
                out = cases.build_case(case_def, pathlib.Path(tmp), render_module.CaseRenderer())
            n_frames = len(list((out / "input" / "frames").glob("*.png")))
            vis = json.loads((out / "truth" / "visibility.json").read_text(encoding="utf-8"))
        self.assertEqual(len(calls), 4 * n_frames)
        seen = [v for v in vis["contrast_sigma"] if v is not None]
        self.assertGreater(len(seen), 200)
        self.assertGreater(sum(vis["visible"]), 0.5 * len(seen))


if __name__ == "__main__":
    unittest.main()
