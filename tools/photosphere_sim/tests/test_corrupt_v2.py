# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The version 2 corruptions: each must fail its own gate, and nothing unexplained.

Spec 7.1 and RS m5: every new gate has a corruption that fails it, and any
other gate that corruption also fails is asserted here and justified where it
is asserted. Each test therefore pins the WHOLE set of failing gates, not just
the one that was meant, so a corruption that quietly started breaking a second
thing (or stopped breaking its own) is a failure here rather than a surprise
in a later task's report.

The base is the synthetic pan case of ``test_score_v2`` and the ideal result
``sim.ideal`` writes for it, so every number below is the corruption's own.
"""
import json
import pathlib
import unittest

import numpy as np

from sim import corrupt, score
from sim.__main__ import main as cli_main
from tests import test_score_v2 as sp

#: The eleven corruptions this task adds, spec 7.6.
NEW = ["shift-line-down-1", "dent-one-bin", "relabel-unknown-measured",
       "relabel-measured-low", "mark-bins-unknown", "drop-poses-90pct", "yaw-ramp",
       "inflate-closure", "scale-focal-1.01", "shrink-north-sigma", "delay-first-seen-2s"]


def failing(scores: dict) -> set:
    return {name for name, ok in scores["gates"].items() if name != "pass" and not ok}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = sp.shared()
        cls.case = cls.shared["main"]["case"]
        cls.ideal = cls.shared["main"]["ideal"]
        cls.clear_case = cls.shared["clear"]["case"]
        cls.clear_ideal = cls.shared["clear"]["ideal"]
        cls.base = cls.shared["base"]

    def corrupted(self, name: str, *, case=None, result=None, key=None, **params):
        """Apply a corruption to a result and score it."""
        case = self.case if case is None else case
        result = self.ideal if result is None else result
        out = corrupt.apply(case, result, name, self.base / (key or f"c-{name}"), **params)
        return score.score_case(case, out), out


class NewCorruptions(Base):
    def test_every_new_corruption_is_registered(self):
        for name in NEW:
            self.assertIn(name, corrupt.CORRUPTIONS)

    def test_shift_line_down_1_fails_the_horizon_gate(self):
        """The line is one degree under the truth everywhere it was exact.

        Also fails `never_below_profile`: the line was built to sit on the
        profile, so a degree lower is a degree under it at every edge, by
        construction. `measured_is_honest` (p99 under 2) and the obstacle rule
        (a deficit of exactly 1.0 is not over 1.0) still pass, which is the
        corruption sitting exactly on the gate's own size.

        MUTATION: shifting by 0.5 passes `horizon_p95_lt_1`.
        """
        scores, _ = self.corrupted("shift-line-down-1")
        self.assertEqual(failing(scores), {"horizon_p95_lt_1", "never_below_profile"})
        errors = scores["horizon"]["signed_error_deg"]
        self.assertAlmostEqual(errors["median"], -1.0, places=9)
        self.assertAlmostEqual(errors["p95"], 1.0, places=9)
        half, _ = self.corrupted("shift-line-down-1", key="c-shift-half", deg=0.5)
        self.assertEqual(failing(half), {"never_below_profile"})

    def test_dent_one_bin_fails_never_below_profile(self):
        """One flat bin of ground, two degrees under its profile at both edges.

        The only gate it fails is its own. The dent is a floor of one bin and a
        ramp of one bin each side, 15 of the 2880 graded samples, which the
        1 per cent of the p99 does not reach. Three bins are under the profile:
        the dented one and the two whose shared edge it lowered.

        MUTATION: a dent that leaves the right-hand edge where it was lowers
        two bins' edges only, and the count below becomes 2.
        """
        scores, _ = self.corrupted("dent-one-bin")
        self.assertEqual(failing(scores), {"never_below_profile"})
        self.assertEqual(scores["horizon"]["below_profile_bins"], 3)
        self.assertGreater(scores["horizon"]["signed_error_deg"]["max"], 1.9)
        # Named bin: the same dent anywhere.
        scores, _ = self.corrupted("dent-one-bin", key="c-dent-0", bin_index=0, deg=0.5)
        self.assertEqual(failing(scores), {"never_below_profile"})

    def test_relabel_unknown_measured_fails_the_unobservable_gate(self):
        """The 120 dark bins are called Measured at their published 90.

        Fails `unknown_where_unobservable` (its own: 100 per cent of the
        not-visible bins are now Measured) and, because those bins are now
        graded against a truth of 5, `measured_is_honest`, and
        `horizon_p95_lt_1` since they are 600 of 3480 graded samples, 17 per
        cent, over the 5 per cent the p95 allows. The spec says as much.

        MUTATION: `unknown_where_unobservable` fixed true leaves the other two
        failing and the set below short of its own gate.
        """
        scores, _ = self.corrupted("relabel-unknown-measured")
        self.assertEqual(failing(scores), {"unknown_where_unobservable",
                                           "measured_is_honest", "horizon_p95_lt_1"})
        self.assertEqual(scores["horizon"]["unknown_where_unobservable"]["share"], 1.0)
        self.assertEqual(scores["horizon"]["states"]["unknown"], 0)

    def test_relabel_measured_low_fails_measured_share(self):
        """A contiguous fifth of the Measured bins, 116 of 576, becomes Low.

        Only `measured_share` fails: the run is contiguous, so the line is
        wrong in two shoulder bins only (10 samples, under the 1 per cent of
        the p99), and it is blocked at 90 over a profile of 90. The run starts
        at azimuth 150 so that it stays off the bump at 100 to 130, which
        would also fail `no_missed_obstructions`.

        MUTATION: relabelling 10 per cent passes the gate (share 0.9).
        """
        scores, _ = self.corrupted("relabel-measured-low", az0=150.0)
        self.assertEqual(failing(scores), {"measured_share"})
        self.assertAlmostEqual(scores["horizon"]["measured_share"]["share"], 460 / 576)
        self.assertEqual(scores["horizon"]["states"]["low"], 116)
        gentle, _ = self.corrupted("relabel-measured-low", key="c-low-10",
                                   az0=150.0, fraction=0.1)
        self.assertEqual(failing(gentle), set())

    def test_mark_bins_unknown_fails_the_unresolved_gate(self):
        """Twenty Measured bins of a fully observable case become Unknown.

        The only gate that fails is `no_unresolved_boundary`, which exists on
        this case because it is graded `fully_observable`. 20 of 720 bins is
        under every other threshold.

        MUTATION: a default count of 0 (which marks the one bin it must) leaves
        `unresolved_bins` at 1 and the count below fails.
        """
        scores, _ = self.corrupted("mark-bins-unknown", case=self.clear_case,
                                   result=self.clear_ideal, az0=150.0)
        self.assertEqual(failing(scores), {"no_unresolved_boundary"})
        self.assertEqual(scores["horizon"]["unresolved_bins"], 20)
        self.assertEqual(scores["horizon"]["states"]["unknown"], 20)

    def test_drop_poses_90pct_fails_overlay_complete(self):
        """Every tenth pose stays: 27 of 265 frames. The poses that remain are
        exact, so the moving-overlay gate passes and the only failure is
        completeness (#898).

        MUTATION: keeping every second pose leaves half of them, and the
        fraction below is no longer 27 of 265.
        """
        scores, _ = self.corrupted("drop-poses-90pct")
        self.assertEqual(failing(scores), {"overlay_complete"})
        self.assertAlmostEqual(scores["overlay"]["complete_fraction"], 27 / 265)
        self.assertTrue(scores["gates"]["overlay_moving_p95_lt_1"])

    def test_yaw_ramp_fails_the_moving_overlay_gate(self):
        """A yaw error growing from 0 to 3 degrees across the scan.

        One global yaw takes out the mean, 1.5 degrees, and leaves a ramp of
        plus and minus 1.5 whose 95th percentile is 1.43. Only the overlay
        gate fails; the poses are completely present, and the keyframes in the
        diagnostics are not touched, so the loop and north gates pass.

        MUTATION: a ramp to 1 degree is inside the gate after the mean is out.
        """
        scores, _ = self.corrupted("yaw-ramp")
        self.assertEqual(failing(scores), {"overlay_moving_p95_lt_1"})
        self.assertAlmostEqual(scores["overlay"]["yaw_removed_deg"], 1.5, delta=0.02)
        self.assertAlmostEqual(scores["overlay"]["moving"]["p95_deg"], 1.425, delta=0.05)
        small, _ = self.corrupted("yaw-ramp", key="c-ramp-1", deg=1.0)
        self.assertEqual(failing(small), set())

    def test_inflate_closure_fails_the_loop_gate(self):
        """The late keyframe of the match is turned half a degree.

        Only the loop gate fails: one keyframe of 86 moves the north error by
        0.006 degrees, which is far inside both north gates.

        MUTATION: inflating by 0.1 passes.
        """
        scores, _ = self.corrupted("inflate-closure")
        self.assertEqual(failing(scores), {"loop_residual_lt_0_25"})
        self.assertAlmostEqual(scores["loop"]["residual_deg"], 0.5, places=6)
        self.assertLess(abs(scores["north"]["error_deg"]), 0.01)

    def test_scale_focal_fails_the_focal_gate(self):
        """The reported focal length is a per cent too long, twice the gate.

        MUTATION: scaling by 1.004 passes.
        """
        scores, _ = self.corrupted("scale-focal-1.01")
        self.assertEqual(failing(scores), {"focal_err_lt_0_5pct"})
        self.assertAlmostEqual(scores["focal"]["error_fraction"], 0.01, places=9)

    def test_shrink_north_sigma_fails_the_honesty_gate(self):
        """On a result whose north is off by 0.8 degrees, a sigma of 2 is
        honest (the limit is 5) and a sigma of 0.1 is not (the limit is 0.25).
        The error gate, which looks at the error and not at the claim, still
        passes at 0.8.

        On the exact ideal, whose north error is 0, the corruption cannot fail
        anything: 0 is within 0.25. That is by construction and the scanner
        under test is not the ideal, so the base here is an ideal result with a
        believable error.

        MUTATION: a corruption that set the sigma to 1.0 passes (limit 2.5).
        """
        honest = sp.copy_result(self.ideal, "north-0.8")
        sp.turn_keyframes(honest, 0.8)
        before = score.score_case(self.case, honest)
        self.assertEqual(failing(before), set())
        scores, _ = self.corrupted("shrink-north-sigma", result=honest, key="c-shrink")
        self.assertEqual(failing(scores), {"north_sigma_honest"})
        self.assertAlmostEqual(scores["north"]["error_deg"], 0.8, places=6)
        self.assertEqual(scores["north"]["sigma_deg"], 0.1)
        exact, _ = self.corrupted("shrink-north-sigma", key="c-shrink-exact")
        self.assertEqual(failing(exact), set())

    def test_delay_first_seen_fails_the_live_fill_gate(self):
        """Every seen cell reported two seconds late, so about 2025 ms.

        MUTATION: a delay of 5 deciseconds passes.
        """
        scores, _ = self.corrupted("delay-first-seen-2s")
        self.assertEqual(failing(scores), {"live_fill_p95_le_1000"})
        self.assertAlmostEqual(scores["live_fill"]["p95_ms"],
                               self.shared["main"]["scores"]["live_fill"]["p95_ms"] + 2000.0)
        small, _ = self.corrupted("delay-first-seen-2s", key="c-delay-5", deciseconds=5)
        self.assertEqual(failing(small), set())


class ExistingCorruptionsOnVersion2(Base):
    """The corruptions that already existed act on a version 2 result."""

    def test_north_wrap_fails_the_north_error_gate(self):
        """Ten degrees west, in the poses as well as in the picture.

        It also fails everything that reads where things are: the landmarks, the
        boundary and the test obstacle, and `north_sigma_honest`, because an
        error of 10 degrees is outside five times the sigma's 2.5. And
        `unknown_where_unobservable`: the dark stretch has moved onto
        bins that were Measured and the Measured ground has moved into it.
        """
        scores, _ = self.corrupted("north-wrap")
        self.assertIn("north_err_lt_1", failing(scores))
        self.assertAlmostEqual(scores["north"]["error_deg"], -10.0, places=6)
        self.assertEqual(failing(scores), {
            "north_err_lt_1", "north_sigma_honest", "horizon_p95_lt_1", "measured_is_honest",
            "no_missed_obstructions", "landmarks_p95_lt_0_5", "landmarks_p99_lt_1",
            "no_omissions", "unknown_where_unobservable"})
        # What does not move: the loop closure is a relative rotation.
        self.assertTrue(scores["gates"]["loop_residual_lt_0_25"])
        self.assertLess(scores["loop"]["residual_deg"], 1e-6)

    def test_remove_section_over_the_bump_is_a_missed_obstruction(self):
        """The bump's whole span, called Unknown at 90 and unpainted.

        Fails `no_missed_obstructions` (the bump has no resolved bin) and
        coverage (34 of 360 degrees of the footprint unpainted). The horizon
        gates stay green: the section is blocked at 90 over a profile of 90,
        and a block is not a measurement.
        """
        scores, _ = self.corrupted("remove-section", az0=98.0, width=34.0)
        self.assertEqual(failing(scores), {"no_missed_obstructions", "coverage_ge_0_95"})
        self.assertEqual(scores["horizon"]["missed_obstructions"], ["bump"])
        self.assertEqual(scores["horizon"]["states"]["unknown"], 120 + 68)

    def test_erase_horizon_strip_leaves_nothing_measured(self):
        scores, _ = self.corrupted("erase-horizon-strip")
        self.assertTrue(scores["horizon"]["empty_measured"])
        self.assertTrue({"horizon_p95_lt_1", "measured_is_honest", "measured_share",
                         "coverage_ge_0_95"} <= failing(scores))

    def test_mirror_and_focal_have_a_version_2_form(self):
        for name in ("mirror", "focal"):
            scores, out = self.corrupted(name)
            horizon = sp.read_json(out / "horizon.json")
            self.assertEqual(horizon["version"], 2)
            self.assertEqual(len(horizon["profile"]), 720)
            self.assertFalse(scores["gates"]["pass"], name)

    def test_yaw_turns_the_polyline_by_whole_bins_and_the_poses_exactly(self):
        out = corrupt.apply(self.case, self.ideal, "yaw", self.base / "c-yaw5", deg=5.0)
        before = sp.read_json(self.ideal / "horizon.json")
        after = sp.read_json(out / "horizon.json")
        self.assertEqual(after["profile"], list(np.roll(before["profile"], 10)))
        self.assertEqual(after["profile_state"], list(np.roll(before["profile_state"], 10)))
        moved = sorted((round((p["az"] + 5.0) % 360.0, 9), p["alt"]) for p in before["points"])
        self.assertEqual(sorted((round(p["az"], 9), p["alt"]) for p in after["points"]), moved)
        scores = score.score_case(self.case, out)
        self.assertAlmostEqual(scores["north"]["error_deg"], 5.0, places=6)


class Refusals(Base):
    """A corruption that did not happen would be scored as a clean result."""

    def legacy_like(self, name: str):
        """A result with a version 1 horizon, no diagnostics and no first_seen."""
        result = sp.copy_result(self.ideal, name)
        (result / "diagnostics.json").unlink()
        (result / "first_seen.bin").unlink()
        sp.write_json(result / "horizon.json", {
            "bins": 4, "points": [{"az": 45 + 90 * i, "alt": 5.0} for i in range(4)],
            "uncertain_bins": []})
        return result

    def test_each_version_2_corruption_names_what_it_needs(self):
        result = self.legacy_like("legacy-like")
        wants = {
            "shift-line-down-1": "version 2 horizon", "dent-one-bin": "version 2 horizon",
            "relabel-unknown-measured": "version 2 horizon",
            "relabel-measured-low": "version 2 horizon",
            "mark-bins-unknown": "version 2 horizon",
            "inflate-closure": "diagnostics.json", "scale-focal-1.01": "diagnostics.json",
            "shrink-north-sigma": "diagnostics.json",
            "delay-first-seen-2s": "first_seen.bin",
        }
        for name, needle in wants.items():
            with self.assertRaises(ValueError, msg=name) as caught:
                corrupt.apply(self.case, result, name, self.base / f"refuse-{name}")
            self.assertIn(needle, str(caught.exception), name)

    def test_nothing_to_relabel_is_refused(self):
        """`relabel-unknown-measured` on a result with no Unknown bin."""
        with self.assertRaises(ValueError):
            corrupt.apply(self.clear_case, self.clear_ideal, "relabel-unknown-measured",
                          self.base / "refuse-relabel")

    def test_the_legacy_corruptions_still_act_on_a_legacy_shaped_result(self):
        """`yaw` on a result with no diagnostics turns what it has."""
        result = self.legacy_like("legacy-yaw")
        out = corrupt.apply(self.case, result, "yaw", self.base / "legacy-yaw-out", deg=90.0)
        horizon = sp.read_json(out / "horizon.json")
        self.assertEqual(len(horizon["points"]), 4)

    def test_the_command_line_offers_every_new_corruption(self):
        import contextlib
        import io
        for name in NEW:
            result = self.base / "cli-out"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                status = cli_main(["corrupt", "syn-main", name, "--cases", str(self.base),
                                   "--result", str(self.ideal), "--out", str(result / name)])
            self.assertEqual(status, 0, name)
            self.assertTrue((result / name / "horizon.json").is_file())


class LiftedPolyline(unittest.TestCase):
    def test_a_lifted_polyline_clears_the_profile_and_only_near_the_change(self):
        """Raising bins 100 to 119 to 90 on a sparse polyline: G1 holds at every
        edge, and the line is unchanged more than one bin from the change.

        MUTATION: lifting without the shoulder vertices tilts the whole of the
        sparse piece, and the unchanged-far assertion fails.
        """
        profile = np.full(720, 5.0)
        profile[300:360] = 25.0
        points = [{"az": 150.0 - 0.01, "alt": 5.0}, {"az": 150.0, "alt": 25.0},
                  {"az": 180.0, "alt": 25.0}, {"az": 180.01, "alt": 5.0}]
        changed = np.zeros(720, dtype=bool)
        changed[100:120] = True
        raised = profile.copy()
        raised[changed] = 90.0
        lifted = corrupt._lift_polyline(points, raised, changed)
        xs, ys = [p["az"] for p in lifted], [p["alt"] for p in lifted]
        edges = np.arange(720) * 0.5
        self.assertTrue(np.all(np.interp(edges, xs, ys, period=360.0)
                               >= np.maximum(np.roll(raised, 1), raised) - 1e-9))
        before = np.interp(edges, [p["az"] for p in points], [p["alt"] for p in points],
                           period=360.0)
        after = np.interp(edges, xs, ys, period=360.0)
        far = (edges < 49.0) | (edges > 61.0)
        self.assertTrue(np.all(np.abs(after - before)[far] < 1e-9))


if __name__ == "__main__":
    unittest.main()
