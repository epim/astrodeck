# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``sim.visibility``: which horizon bins a scanner could see at all (spec 13.8).

The world here is the simplest one with a boundary in it: sky above a given
altitude, ground below, each a flat grey, drawn with the same pinhole camera
the frames use. That makes the contrast of a boundary a number the test
chooses (the difference of the two greys over the noise sigma), so what is
under test is the rule and not the picture: which frames count, what is
sampled, what divides it, and where the line between visible and not falls.

The proof of task T20 for this file: a high-contrast boundary is visible, a
boundary under 3 sigma is not, and a boundary above the footprint is not. Each
test names the mutant it must catch.
"""

from __future__ import annotations

import json
import math
import unittest
from types import SimpleNamespace

import numpy as np

from sim import ideal, score, visibility
from sim.geometry import Camera, look_basis

CAM = Camera(180, 320, 41.14)
POS = np.array([0.0, 0.0, 1.4])
PITCH = 22.98

#: ``alt_max`` is 3600 azimuths of 0.1 degree, five to a 0.5 degree bin.
TRUTH_AZIMUTHS = 3600


def _rays():
    u, v = np.meshgrid(np.arange(CAM.width) + 0.5, np.arange(CAM.height) + 0.5)
    ray = np.stack([(u - CAM.cx) / CAM.fx, -(v - CAM.cy) / CAM.fy, np.ones_like(u)], axis=-1)
    return ray / np.linalg.norm(ray, axis=-1, keepdims=True)


RAYS = _rays()


def altitudes(basis):
    """The altitude, degrees, every pixel of a frame looks at."""
    d = RAYS @ np.vstack([basis.right, basis.up, basis.forward])
    return np.degrees(np.arcsin(np.clip(d[..., 2], -1.0, 1.0)))


def world_frame(basis, boundary_deg, sky, ground):
    """The frame of a world whose sky starts at ``boundary_deg``, as uint8 RGB."""
    plane = np.where(altitudes(basis) > boundary_deg, float(sky), float(ground))
    return np.repeat(np.clip(np.rint(plane), 0, 255).astype(np.uint8)[..., None], 3, axis=2)


def horizon(boundary_deg):
    return {"bins": TRUTH_AZIMUTHS, "alt_max": [float(boundary_deg)] * TRUTH_AZIMUTHS}


def sweep(first=0, last=60, step=2, pitch=PITCH):
    """Frames along the horizon, one every ``step`` degrees of azimuth."""
    return [SimpleNamespace(basis=look_basis(float(az), pitch)) for az in range(first, last + 1, step)]


def build(frames, images, truth, noise_sigma=2.0):
    return visibility.build(frames, images, CAM, truth, noise_sigma)


def bin_of(az_deg):
    return int(az_deg / 0.5)


def covered_bins(first=6, last=54):
    """Bins well inside a sweep's coverage: a degree clear of its ends."""
    return range(bin_of(first), bin_of(last))


def contrast_at(data, bins):
    values = [data["contrast_sigma"][b] for b in bins]
    assert all(v is not None for v in values), "a bin that should be covered has no contrast"
    return values


class HighContrastIsVisible(unittest.TestCase):
    """PROOF: a high-contrast boundary is visible.

    The boundary stands 100 grey levels clear at a noise sigma of 2, so 50
    sigmas, in every bin the sweep covers; the bins it does not cover have
    no contrast and are not visible.

    MUTATION: the contrast not divided by the noise sigma (it reads 100 and
    the value assertion fails), or ``VISIBLE_SIGMA`` raised to 60.
    """

    @classmethod
    def setUpClass(cls):
        frames = sweep()
        images = [world_frame(f.basis, 15.0, 170, 70) for f in frames]
        cls.data = build(frames, images, horizon(15.0))

    def test_every_covered_bin_reads_fifty_sigma_and_is_visible(self):
        for value in contrast_at(self.data, covered_bins()):
            self.assertAlmostEqual(value, 50.0, delta=0.01)
        for b in covered_bins():
            self.assertTrue(self.data["visible"][b])

    def test_a_bin_no_frame_looked_at_has_no_contrast_and_is_not_visible(self):
        far = bin_of(200.0)
        self.assertIsNone(self.data["contrast_sigma"][far])
        self.assertFalse(self.data["visible"][far])
        self.assertIsNone(self.data["footprint_top_deg"][far])

    def test_the_file_has_the_schema_of_13_8(self):
        data = self.data
        self.assertEqual(sorted(data), ["bins", "contrast_sigma", "footprint_top_deg",
                                        "noise_sigma", "visible"])
        self.assertEqual(data["bins"], 720)
        self.assertEqual(data["noise_sigma"], 2.0)
        for key in ("contrast_sigma", "footprint_top_deg", "visible"):
            self.assertEqual(len(data[key]), 720)
        self.assertTrue(all(isinstance(v, bool) for v in data["visible"]))
        self.assertEqual(json.loads(json.dumps(data, allow_nan=False)), data)

    def test_the_scorer_and_the_ideal_result_read_it(self):
        visible, top = score._parse_visibility(json.loads(json.dumps(self.data)))
        self.assertEqual(int(visible.sum()), sum(self.data["visible"]))
        self.assertEqual(int(np.isfinite(top).sum()),
                         sum(v is not None for v in self.data["footprint_top_deg"]))
        horizon_v2 = ideal._ideal_horizon_v2(horizon(15.0), self.data)
        self.assertEqual(len(horizon_v2["profile_state"]), 720)
        self.assertEqual(horizon_v2["profile_state"][bin_of(30.0)], score.STATE_MEASURED)
        self.assertEqual(horizon_v2["profile_state"][bin_of(200.0)], score.STATE_UNKNOWN)


class LowContrastIsNot(unittest.TestCase):
    """PROOF: a boundary under 3 sigma is not visible, and 3 sigma is.

    The sky is 5 grey levels over the ground at a sigma of 2: 2.5 sigma. At 6
    it is 3.0 and counts (``>=``).

    MUTATION: ``contrast >= VISIBLE_SIGMA`` written ``>`` (the 3.0 case fails),
    or ``VISIBLE_SIGMA`` lowered to 2.
    """

    def run_case(self, difference, noise_sigma=2.0):
        frames = sweep()
        images = [world_frame(f.basis, 15.0, 125 + difference, 125) for f in frames]
        return build(frames, images, horizon(15.0), noise_sigma)

    def test_two_and_a_half_sigma_is_not_visible(self):
        data = self.run_case(5)
        for value in contrast_at(data, covered_bins()):
            self.assertAlmostEqual(value, 2.5, delta=0.01)
        self.assertFalse(any(data["visible"][b] for b in covered_bins()))

    def test_exactly_three_sigma_is_visible(self):
        data = self.run_case(6)
        for value in contrast_at(data, covered_bins()):
            self.assertAlmostEqual(value, 3.0, delta=0.01)
        self.assertTrue(all(data["visible"][b] for b in covered_bins()))

    def test_the_noise_sigma_divides_the_contrast_and_is_recorded(self):
        data = self.run_case(40, noise_sigma=4.0)
        self.assertEqual(data["noise_sigma"], 4.0)
        for value in contrast_at(data, covered_bins()):
            self.assertAlmostEqual(value, 10.0, delta=0.01)

    def test_a_noise_free_case_is_divided_by_the_rounding_step_not_by_zero(self):
        data = self.run_case(6, noise_sigma=0.0)
        self.assertAlmostEqual(data["noise_sigma"], 1.0 / math.sqrt(12.0), places=9)
        for value in contrast_at(data, covered_bins()):
            self.assertAlmostEqual(value, 6.0 * math.sqrt(12.0), delta=0.01)

    def test_no_contrast_at_all_reads_zero(self):
        frames = sweep()
        images = [world_frame(f.basis, 15.0, 128, 128) for f in frames]
        data = build(frames, images, horizon(15.0))
        self.assertTrue(all(v == 0.0 for v in contrast_at(data, covered_bins())))


class AboveTheFootprintIsNot(unittest.TestCase):
    """PROOF: a boundary above the footprint is not visible.

    The claimed footprint of a frame pitched at 22.98 degrees reaches 53.96
    along its own meridian, and a boundary needs 2 degrees of footprint above
    it. The sky and the ground are fully contrasted in every frame here, so
    the only thing that can make a bin not visible is the footprint.

    MUTATION: ``FOOTPRINT_ABOVE_DEG`` set to 0 (the 52.5 case becomes visible),
    or the check on the footprint's top dropped altogether (the 53.5 case).
    """

    def run_case(self, boundary):
        frames = sweep()
        images = [world_frame(f.basis, boundary, 170, 70) for f in frames]
        return build(frames, images, horizon(boundary))

    def test_a_boundary_with_two_degrees_of_footprint_above_it_is_visible(self):
        data = self.run_case(51.5)
        for b in covered_bins():
            self.assertTrue(data["visible"][b], b)

    def test_a_boundary_with_less_is_not_even_measured(self):
        for boundary in (52.5, 53.5, 60.0):
            with self.subTest(boundary=boundary):
                data = self.run_case(boundary)
                for b in covered_bins():
                    self.assertIsNone(data["contrast_sigma"][b])
                    self.assertFalse(data["visible"][b])
                # the frames did look there: the footprint region reaches the bin
                self.assertGreater(data["footprint_top_deg"][bin_of(30.0)], 53.0)

    def test_a_boundary_below_the_footprint_is_not_measured_either(self):
        """The footprint starts 8 degrees below the horizon at this pitch."""
        data = self.run_case(-9.0)
        for b in covered_bins():
            self.assertIsNone(data["contrast_sigma"][b])
        data = self.run_case(-7.0)
        for b in covered_bins():
            self.assertTrue(data["visible"][b])

    def test_a_bin_with_no_solid_altitude_has_no_boundary(self):
        frames = sweep()
        images = [world_frame(f.basis, 15.0, 170, 70) for f in frames]
        data = build(frames, images, horizon(-10.0))
        self.assertTrue(all(v is None for v in data["contrast_sigma"]))
        self.assertFalse(any(data["visible"]))


class ContrastIsReadFromTheDeliveredPixels(unittest.TestCase):
    """The luma comes from the post-processed frame, the footprint from the truth pose.

    A frame whose stabiliser looked 3 degrees lower than the truth pose shows
    the boundary 3 degrees above where the truth pose puts it, so both luma
    windows fall on the ground.

    MUTATION: contrast computed from the truth horizon alone (50 in every
    case), or sampled along the wrong azimuth (0, a flat grey column).
    """

    def run_case(self, lowered_by_deg):
        frames = sweep()
        images = [world_frame(look_basis(az, PITCH - lowered_by_deg), 15.0, 170, 70)
                  for az in range(0, 61, 2)]
        return build(frames, images, horizon(15.0))

    def test_a_view_three_degrees_off_has_lost_the_boundary(self):
        data = self.run_case(3.0)
        for value in contrast_at(data, covered_bins()):
            self.assertAlmostEqual(value, 0.0, delta=0.01)
        self.assertFalse(any(data["visible"][b] for b in covered_bins()))

    def test_a_view_one_degree_off_has_a_weaker_boundary(self):
        data = self.run_case(1.0)
        for value in contrast_at(data, covered_bins()):
            self.assertGreater(value, 5.0)
            self.assertLess(value, 40.0)

    def test_a_view_on_the_truth_pose_has_all_of_it(self):
        for value in contrast_at(self.run_case(0.0), covered_bins()):
            self.assertAlmostEqual(value, 50.0, delta=0.01)


class TheBestFrameWins(unittest.TestCase):
    """The maximum over the frames that qualify, not the first, last or mean.

    MUTATION: ``np.fmax`` written ``np.fmin`` in the per-bin update (the
    washed-out frame wins), or the last frame's value kept.
    """

    def test_one_good_frame_among_washed_out_ones(self):
        good = look_basis(30.0, PITCH)
        for order in ((0, 1, 2), (2, 1, 0), (1, 0, 2)):
            with self.subTest(order=order):
                images = [world_frame(good, 15.0, 140, 120),     # 10 sigma
                          world_frame(good, 15.0, 128, 128),     # none
                          world_frame(good, 15.0, 170, 70)]      # 50 sigma
                frames = [SimpleNamespace(basis=good)] * 3
                data = build(frames, [images[i] for i in order], horizon(15.0))
                self.assertAlmostEqual(data["contrast_sigma"][bin_of(30.0)], 50.0, delta=0.01)

    def test_a_frame_whose_footprint_misses_the_bin_does_not_count(self):
        near = look_basis(30.0, PITCH)
        far = look_basis(120.0, PITCH)
        frames = [SimpleNamespace(basis=near), SimpleNamespace(basis=far)]
        images = [world_frame(near, 15.0, 128, 128), world_frame(far, 15.0, 170, 70)]
        data = build(frames, images, horizon(15.0))
        self.assertAlmostEqual(data["contrast_sigma"][bin_of(30.0)], 0.0, delta=0.01)
        self.assertAlmostEqual(data["contrast_sigma"][bin_of(120.0)], 50.0, delta=0.01)


class TruthBoundary(unittest.TestCase):
    def test_a_is_the_maximum_of_the_five_truth_samples_in_the_bin(self):
        alt_max = [10.0] * TRUTH_AZIMUTHS
        alt_max[7] = 12.5       # azimuth 0.75, bin 1
        alt_max[14] = 11.0      # azimuth 1.45, bin 2
        boundary = visibility.truth_boundary({"alt_max": alt_max})
        self.assertEqual(boundary.size, 720)
        self.assertEqual(float(boundary[0]), 10.0)
        self.assertEqual(float(boundary[1]), 12.5)
        self.assertEqual(float(boundary[2]), 11.0)
        self.assertEqual(float(boundary[3]), 10.0)

    def test_a_horizon_that_the_bins_do_not_divide_is_refused(self):
        with self.assertRaises(ValueError):
            visibility.truth_boundary({"alt_max": [10.0] * 1000})


class FootprintTop(unittest.TestCase):
    def test_one_frame_reaches_its_pitch_plus_the_long_edge(self):
        frames = [SimpleNamespace(basis=look_basis(90.0, PITCH))]
        data = build(frames, [world_frame(frames[0].basis, 15.0, 170, 70)], horizon(15.0))
        k_long = score.FOOTPRINT_LONG_FRACTION * CAM.cy / CAM.fy
        expected = PITCH + math.degrees(math.atan(k_long))
        self.assertAlmostEqual(data["footprint_top_deg"][bin_of(90.0)], expected, delta=0.01)
        self.assertIsNone(data["footprint_top_deg"][bin_of(100.0)])
        self.assertIsNone(data["footprint_top_deg"][bin_of(80.0)])

    def test_the_region_is_the_union_so_the_top_is_the_highest_frame(self):
        frames = [SimpleNamespace(basis=look_basis(90.0, 20.0)),
                  SimpleNamespace(basis=look_basis(90.0, 35.0))]
        images = [world_frame(f.basis, 15.0, 170, 70) for f in frames]
        data = build(frames, images, horizon(15.0))
        k_long = score.FOOTPRINT_LONG_FRACTION * CAM.cy / CAM.fy
        self.assertAlmostEqual(data["footprint_top_deg"][bin_of(90.0)],
                               35.0 + math.degrees(math.atan(k_long)), delta=0.01)

    def test_the_top_agrees_with_the_scorers_own_region(self):
        """``score.footprint_pass`` rasterises the same footprint cell by cell. Asked
        for 720 columns and 2001 rows its cells are the bins' centres and every
        0.05 degrees of altitude, so its top at a column is this file's top at
        the bin to within a row."""
        rng = np.random.default_rng(5)
        bases = [look_basis(float(az), float(alt), float(roll)) for az, alt, roll in zip(
            np.arange(0, 180, 9), rng.uniform(5, 45, 20), rng.uniform(-3, 3, 20))]
        records = [{"right": list(b.right), "up": list(b.up), "forward": list(b.forward)}
                   for b in bases]
        region, _ = score.footprint_pass((2001, 720), {"cy": CAM.cy, "fy": CAM.fy}, records)
        _, rows = score._raster_grid(2001, 720)
        data = build([SimpleNamespace(basis=b) for b in bases],
                     [np.zeros((CAM.height, CAM.width, 3), dtype=np.uint8)] * len(bases),
                     horizon(15.0))
        compared = disagreed = 0
        for b in range(720):
            hit = np.nonzero(region[:, b])[0]
            mine = data["footprint_top_deg"][b]
            if mine is None or hit.size == 0:
                # Both empty is agreement. One empty is a sliver thinner than a
                # raster row falling between its cells, which may happen at a
                # strip's corner but not often.
                disagreed += (mine is None) != (hit.size == 0)
                continue
            compared += 1
            self.assertAlmostEqual(mine, float(rows[hit.min()]), delta=0.06, msg=f"bin {b}")
        self.assertGreater(compared, 300)
        self.assertLess(disagreed, 5)


class MeridianFootprint(unittest.TestCase):
    """The closed-form arc against the footprint tested point by point."""

    def test_it_matches_a_brute_force_scan(self):
        k_wide = math.tan(math.radians(score.FOOTPRINT_HALF_WIDTH_DEG))
        k_long = score.FOOTPRINT_LONG_FRACTION * CAM.cy / CAM.fy
        rng = np.random.default_rng(1)
        grid = np.arange(-90.0, 90.0001, 0.01)
        sin_alt, cos_alt = np.sin(np.radians(grid)), np.cos(np.radians(grid))
        empty = non_empty = 0
        for _ in range(60):
            basis = look_basis(rng.uniform(0, 360), rng.uniform(-30, 80), rng.uniform(-5, 5))
            view_az = math.degrees(math.atan2(basis.forward[0], basis.forward[1]))
            azimuths = view_az + rng.uniform(-60, 60, 15)
            low, high = visibility.meridian_footprint(basis, CAM, azimuths)
            matrix = np.column_stack([basis.right, basis.up, basis.forward])
            for az, lo, hi in zip(azimuths, low, high):
                d = np.stack([np.sin(math.radians(az)) * cos_alt,
                              np.cos(math.radians(az)) * cos_alt, sin_alt], axis=1)
                cam = d @ matrix
                inside = ((cam[:, 2] > 0) & (np.abs(cam[:, 0]) <= k_wide * cam[:, 2])
                          & (np.abs(cam[:, 1]) <= k_long * cam[:, 2]))
                if not inside.any():
                    empty += 1
                    self.assertTrue(math.isnan(lo) and math.isnan(hi))
                    continue
                non_empty += 1
                lo_ref, hi_ref = grid[inside].min(), grid[inside].max()
                self.assertAlmostEqual(lo, lo_ref, delta=0.011)
                self.assertAlmostEqual(hi, hi_ref, delta=0.011)
                self.assertEqual(int(inside.sum()), int(round((hi_ref - lo_ref) / 0.01)) + 1,
                                 "the footprint along a meridian is one arc")
        self.assertGreater(empty, 50)
        self.assertGreater(non_empty, 50)

    def test_the_meridian_square_on_to_a_level_view_is_empty_not_nan_poisoned(self):
        low, high = visibility.meridian_footprint(look_basis(0.0, 0.0), CAM, [90.0, 270.0, 0.0])
        self.assertTrue(math.isnan(low[0]) and math.isnan(high[0]))
        self.assertTrue(math.isnan(low[1]) and math.isnan(high[1]))
        self.assertFalse(math.isnan(low[2]))


if __name__ == "__main__":
    unittest.main()
