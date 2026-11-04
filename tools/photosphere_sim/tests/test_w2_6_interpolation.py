# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 2.6 (#900, #1117): the scorer reads a published horizon the way the
consumers of a saved horizon read it.

The finder, the dome, the ranked list (``horizonAltAt`` in the UI) and the
engine floor (``interp_wrap`` on the server) all read a horizon as a polyline
and interpolate linearly between its points, wrapping at 360. The version 1
scorer instead read measured bin ``i`` as a step covering the whole bin, a
representation no consumer uses, so a wall whose edge fell between two bin
centres was graded as a sliver and the planner's real reading of it, about half
open over several degrees, was never graded at all.

``PROBES`` is the table ``w2_6HorizonBins.test.ts`` checks ``horizonAltAt`` on:
the same polyline and the same expected altitudes in both languages, which is
how "one reading" is kept honest across a boundary this package may not import
across (``test_independence`` forbids the simulator reading production code).
"""
from __future__ import annotations

import unittest

import numpy as np

from sim import score

#: (azimuth, altitude the consumers read) on POLYLINE.
POLYLINE = [{"az": 0.0, "alt": 10.0}, {"az": 90.0, "alt": 40.0}, {"az": 270.0, "alt": 20.0}]
PROBES = [(0.0, 10.0), (45.0, 25.0), (90.0, 40.0), (180.0, 30.0), (270.0, 20.0), (315.0, 15.0), (359.0, 10.0 + 10.0 / 90.0)]

TRUTH_BINS = 3600
STEP = 360.0 / TRUTH_BINS
#: A wall at 25 degrees from azimuth 71 to 100 over a 5 degree horizon: the
#: wall starts 1 degree short of the end of bin 5 (60 to 72 at 30 bins).
WALL_FROM, WALL_TO, WALL_ALT, GROUND_ALT = 71.0, 100.0, 25.0, 5.0


def reference():
    centres = (np.arange(TRUTH_BINS) + 0.5) * STEP
    wall = (centres >= WALL_FROM) & (centres < WALL_TO)
    alt_max = np.where(wall, WALL_ALT, GROUND_ALT)
    profile = np.where(wall, WALL_ALT, -10.0)
    return {"bins": TRUTH_BINS, "alt_max": alt_max.tolist(),
            "obstacles": [{"id": "wall", "alt_max": WALL_ALT, "profile": profile.tolist(), "min_width_deg": 3.0}]}


def bins_as_the_tracer_publishes(values):
    """One point per bin at its centre, as the shipped 30-bin tracer wrote them."""
    n = len(values)
    return {"bins": n, "uncertain_bins": [],
            "points": [{"az": (i + 0.5) * 360.0 / n, "alt": float(v)} for i, v in enumerate(values)]}


def thirty_bins_with_the_wall_missed_in_bin_5():
    """Bin 5 (60 to 72) saw none of the wall, which starts at 71; bins 6 to 8 saw it."""
    values = [GROUND_ALT] * 30
    for i in (6, 7, 8):
        values[i] = WALL_ALT
    return bins_as_the_tracer_publishes(values)


def edge_points(centre_horizon):
    """The same bins published at both of their edges: a step, as the saved line is."""
    n = centre_horizon["bins"]
    points = []
    for i, p in enumerate(centre_horizon["points"]):
        points.append({"az": i * 360.0 / n, "alt": p["alt"]})
        points.append({"az": (i + 1) * 360.0 / n - 0.01, "alt": p["alt"]})
    return {"bins": n, "uncertain_bins": centre_horizon["uncertain_bins"], "points": points}


class OnePolylineReading(unittest.TestCase):
    """PROOF: the scorer's polyline reading is the consumers' reading.

    MUTATION: a step reading (``np.floor`` of the azimuth onto the nearest
    control point) instead of linear interpolation. The 45 degree probe then
    reads 10 or 40, not 25.
    """

    def test_the_scorer_reads_the_probe_table_as_the_consumers_do(self):
        points = [(p["az"], p["alt"]) for p in POLYLINE]
        read = score._polyline_alt(points, [az for az, _ in PROBES])
        for (az, want), got in zip(PROBES, read):
            self.assertAlmostEqual(float(got), want, places=9, msg=f"at azimuth {az}")


class TheEdgeBinIsGradedAsPlannerReadsIt(unittest.TestCase):
    """PROOF (#900): the wall's real width of under-reported azimuth is graded.

    Read as steps, bin 5 under-reports only the 71 to 72 sliver, one degree. The
    planner interpolates from bin 5's centre (66, 5 degrees) to bin 6's (78, 25
    degrees) and reads the wall as under 24 degrees until 77.4, so 6.4 degrees of
    it are false open by more than a degree.

    MUTATION: ``_measured_profile`` back to ``values[owner]`` (each truth bin takes
    its measured bin's value). ``width_missed_deg`` then reads 1.0.
    """

    def graded(self):
        return score._score_horizon(reference(), thirty_bins_with_the_wall_missed_in_bin_5())

    def test_the_under_reported_run_is_the_planners_not_the_sliver(self):
        wall = self.graded()["obstacles"][0]
        self.assertGreaterEqual(wall["width_missed_deg"], 6.0, wall)
        self.assertLessEqual(wall["width_missed_deg"], 7.0, wall)

    def test_false_open_area_counts_the_planners_reading(self):
        """The same wall and the same bin values, published once as centre points
        (read by sliding between centres) and once as edge points (a step): the
        slide leaves about twice the false-open area, and it is the slide the
        planner reads."""
        slide = self.graded()["false_open_sr"]
        step = score._score_horizon(reference(), edge_points(thirty_bins_with_the_wall_missed_in_bin_5()))["false_open_sr"]
        self.assertGreater(step, 0.0)
        self.assertGreater(slide, 1.8 * step)

    def test_a_polyline_of_edge_points_is_graded_by_the_bin_count_it_declares(self):
        """The saved line is two points per bin or more, so its length is not the
        bin count: ``bins`` is what says how coarse it is, and the obstacle rule
        takes its resolvable width from that."""
        graded = score._score_horizon(reference(), edge_points(thirty_bins_with_the_wall_missed_in_bin_5()))
        self.assertEqual(graded["measured_bins"], 30)
        self.assertAlmostEqual(graded["measured_resolution_deg"], 12.0)
        # A step line has no slide to read: the wall is under-reported over 71 to 72 only.
        self.assertAlmostEqual(graded["obstacles"][0]["width_missed_deg"], 1.0, places=1)

    def test_uncertain_bins_still_mask_the_declared_bins_not_the_points(self):
        measured = thirty_bins_with_the_wall_missed_in_bin_5()
        measured["uncertain_bins"] = [5]
        wall = score._score_horizon(reference(), measured)["obstacles"][0]
        # Bin 5 (truth bins 600 to 719) is unresolved, so the 71 to 72 part of the
        # run is not graded and what is left is 72 to 77.4.
        self.assertGreater(wall["width_missed_deg"], 5.0, wall)
        self.assertLess(wall["width_missed_deg"], 6.0, wall)


if __name__ == "__main__":
    unittest.main()
