"""Known corruptions of a correct result, and the numbers the scorer answers with.

Spec section 10: before trusting a green report, corrupt an otherwise correct
output in a known way and check that the affected metric moves by the amount
the corruption put in. A red flag is not enough -- a scorer that fails
everything is as useless as one that passes everything -- so every test here
names the metric that must respond and the value it must respond with, and the
geometry-only control (`brightness`) pins the numbers that must NOT move.

One class builds the flat still case and its ideal result once; each test
corrupts that one result into its own directory and scores it. Nothing is
written outside the temporary directory, and in particular nothing is written
into ``cache/``.

Two of the plan's stated expectations are not what the contract's scorer can
report, and the tests say so where they sit:

- A landmark error is a spherical angle, so a yaw of `d` degrees moves a
  landmark at altitude `a` by `d cos(a)`, not by `d`. The chart yard's
  observable landmarks are spread from altitude 5 to 85, so the median
  great-circle error of a 5 degree yaw is 4.0, not 5.0. The azimuth offset
  itself is 5.0, and that is what these tests pin.
- A blob is matched to a landmark only within 8 degrees, so a 10 degree shift
  is reported as omissions plus a north offset, never as a 10 degree median
  error. That is the contract's matching rule, not a hole in it, but it means
  "median near 10" is unreachable by construction.
"""
import json
import math
import pathlib
import re
import tempfile
import unittest

import numpy as np

from sim import cases, corrupt, ideal, report, score
from sim.geometry import angle_between, wrap_deg

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The solid angle of the sky above the horizontal, in steradians.
HEMISPHERE_SR = 2.0 * math.pi


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class Corruptions(unittest.TestCase):
    """One built case, one ideal result, and a corruption per test."""

    @classmethod
    def setUpClass(cls):
        case_def = read_json(ROOT / "cases" / "chartyard-still-60.json")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = pathlib.Path(cls.tmp.name)
        cls.case_dir = cases.build_case(case_def, cls.base / "cases", cases.flat_renderer)
        cls.ideal_dir = ideal.make_ideal_result(cls.case_dir, cls.base / "ideal")
        cls.ideal_scores = score.score_case(cls.case_dir, cls.ideal_dir)
        cls.results = {}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # -- helpers -----------------------------------------------------------

    @classmethod
    def corrupted(cls, key, name, **params):
        """Score the named corruption once, and remember it under ``key``."""
        if key not in cls.results:
            out_dir = corrupt.apply(cls.case_dir, cls.ideal_dir, name,
                                    cls.base / key, **params)
            cls.results[key] = (out_dir, score.score_case(cls.case_dir, out_dir))
        return cls.results[key][1]

    @staticmethod
    def found(scores):
        return [e for e in scores["landmarks"]["per_landmark"] if e["status"] == "found"]

    @staticmethod
    def azimuth_offsets(scores):
        """Each found landmark's measured azimuth minus its truth azimuth.

        Wrapped into [-180, 180): a scorer that did not wrap would report 350
        where the shift is 10 the other way, which is the north-wrap defect.
        """
        return [wrap_deg(e["measured"]["az"] - e["truth"]["az"])
                for e in Corruptions.found(scores)]

    @staticmethod
    def median(values):
        ordered = sorted(values)
        middle = len(ordered) // 2
        if not ordered:
            raise AssertionError("no values to take a median of")
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    def entry(self, scores, landmark_id):
        return next(e for e in scores["landmarks"]["per_landmark"]
                    if e["id"] == landmark_id)

    # -- yaw ---------------------------------------------------------------

    def test_a_one_degree_yaw_moves_the_landmarks_and_the_north_offset(self):
        scores = self.corrupted("yaw1", "yaw", deg=1.0)
        errors = scores["landmarks"]["errors_deg"]
        self.assertGreaterEqual(errors["median"], 0.8)
        self.assertLessEqual(errors["median"], 1.2)
        self.assertAlmostEqual(scores["horizon"]["north_offset_deg"], 1.0, places=6)
        self.assertFalse(scores["gates"]["pass"])
        # Nothing is lost at one degree: this is a measurement, not a wipeout.
        self.assertEqual(scores["landmarks"]["found"],
                         self.ideal_scores["landmarks"]["found"])

    def test_a_five_degree_yaw_is_five_degrees_of_azimuth(self):
        """The azimuth offset is the corruption; the spherical error is smaller.

        A yaw of 5 degrees moves a landmark at altitude `a` by `5 cos(a)` of
        great-circle angle, so the median over landmarks spread from 5 to 85
        degrees of altitude is 4.0 and not 5.0. The azimuth offset is 5.0 for
        every one of them, and the north offset the boundary reports is 5.0.
        """
        scores = self.corrupted("yaw5", "yaw", deg=5.0)
        self.assertAlmostEqual(self.median(self.azimuth_offsets(scores)), 5.0, delta=0.1)
        self.assertAlmostEqual(scores["horizon"]["north_offset_deg"], 5.0, places=6)
        median = scores["landmarks"]["errors_deg"]["median"]
        self.assertAlmostEqual(median, 4.0, delta=0.15)
        # Five times the one degree yaw, in the metric that scales linearly.
        one = self.median(self.azimuth_offsets(self.corrupted("yaw1", "yaw", deg=1.0)))
        self.assertAlmostEqual(self.median(self.azimuth_offsets(scores)) / one,
                               5.0, delta=0.1)
        self.assertFalse(scores["gates"]["pass"])

    def test_a_north_wrap_is_ten_degrees_the_other_way_never_three_hundred(self):
        scores = self.corrupted("north-wrap", "north-wrap")
        self.assertAlmostEqual(self.median(self.azimuth_offsets(scores)),
                               -10.0, delta=0.1)
        self.assertAlmostEqual(scores["horizon"]["north_offset_deg"], -10.0, places=6)
        # Every reported error is an angle between two directions, so 350 is
        # not a number this scorer can produce; the shift shows up as
        # omissions, because 10 degrees is past the 8 degree matching radius.
        self.assertLessEqual(scores["landmarks"]["errors_deg"]["max"],
                             score.MATCH_RADIUS_DEG)
        self.assertGreater(len(scores["landmarks"]["omitted"]), 0)
        self.assertFalse(scores["gates"]["pass"])

    # -- focal -------------------------------------------------------------

    def test_a_five_percent_focal_error_grows_away_from_the_horizontal(self):
        """`alt' = atan(1.05 tan alt)` is nothing at 0 and 1.4 degrees at 45.

        A landmark near the horizontal barely moves and one at 45 degrees
        moves by more than the gate, which is why a single median would hide
        it. The test splits the landmarks by altitude.
        """
        scores = self.corrupted("focal", "focal", scale=1.05)
        high = [e["error_deg"] for e in self.found(scores)
                if 35.0 <= abs(e["truth"]["alt"]) <= 65.0]
        self.assertGreaterEqual(len(high), 8)
        mean = sum(high) / len(high)
        self.assertGreaterEqual(mean, 0.9)
        self.assertLessEqual(mean, 1.6)
        for entry in self.found(scores):
            if abs(entry["truth"]["alt"]) < 6.0:
                self.assertLess(entry["error_deg"], 0.4, entry["id"])
        self.assertFalse(scores["gates"]["horizon_p95_lt_1"])
        self.assertFalse(scores["gates"]["pass"])

    # -- flip, mirror ------------------------------------------------------

    def test_a_vertical_flip_loses_at_least_half_the_landmarks(self):
        scores = self.corrupted("flip", "flip-vertical")
        expected = scores["landmarks"]["expected"]
        self.assertGreaterEqual(len(scores["landmarks"]["omitted"]), expected / 2)
        self.assertFalse(scores["gates"]["pass"])

    def test_a_mirrored_panorama_is_a_gross_error_or_a_wipeout(self):
        scores = self.corrupted("mirror", "mirror")
        expected = scores["landmarks"]["expected"]
        median = scores["landmarks"]["errors_deg"]["median"]
        self.assertTrue(
            (median is not None and median > 20.0)
            or len(scores["landmarks"]["omitted"]) >= expected / 2,
            scores["landmarks"]["errors_deg"])
        self.assertFalse(scores["gates"]["pass"])

    # -- sections ----------------------------------------------------------

    def test_a_duplicated_section_is_reported_as_a_duplicate(self):
        """Copying 40 degrees of sky over its neighbour duplicates a landmark.

        Forty degrees of azimuth is 3.5 degrees of angle at altitude 85, well
        inside the 8 degree matching radius, so the copy of a landmark up
        there is a second blob of the same landmark rather than a spurious
        one. Lower down the copies land too far away to be matched and are
        reported as spurious instead, which is the honest answer for them.
        """
        scores = self.corrupted("duplicate-section", "duplicate-section",
                                az0=60.0, width=40.0)
        self.assertTrue(scores["landmarks"]["duplicated"], scores["landmarks"])
        self.assertFalse(scores["gates"]["no_duplicates"])
        self.assertFalse(scores["gates"]["pass"])

    def test_a_removed_section_is_omission_and_unresolved_area(self):
        scores = self.corrupted("remove-section", "remove-section",
                                az0=100.0, width=40.0)
        self.assertTrue(scores["landmarks"]["omitted"])
        self.assertLess(scores["coverage"]["observable_fraction_covered"], 0.95)
        self.assertGreater(scores["horizon"]["unresolved_sr"], 0.0)
        self.assertFalse(scores["gates"]["no_unresolved_boundary"])
        self.assertFalse(scores["gates"]["pass"])

    def test_removing_the_trunks_span_misses_the_trunk_and_nothing_else(self):
        """The trunk is 3 degrees wide and stands under a 27 degree canopy.

        Blanking its azimuths leaves the canopy's own azimuths described, so
        an envelope-based horizon score would barely notice. The per-obstacle
        silhouette names the trunk.
        """
        scores = self.corrupted("remove-trunk", "remove-section",
                                az0=38.0, width=3.2)
        self.assertEqual(scores["horizon"]["missed_obstructions"], ["trunk"])
        by_id = {o["id"]: o for o in scores["horizon"]["obstacles"]}
        self.assertTrue(by_id["trunk"]["missed"])
        for name in ("pole-near", "pole-far", "roof-south", "wall-east"):
            self.assertFalse(by_id[name]["missed"], name)
        self.assertFalse(scores["gates"]["no_missed_obstructions"])
        self.assertFalse(scores["gates"]["pass"])

    # -- the wrong reference position --------------------------------------

    def test_the_wrong_reference_position_moves_only_the_near_landmarks(self):
        """A metre east of `c_ref` turns the wall discs and leaves the sky.

        The background is 4 km away and directional, so its landmarks do not
        move at all; the discs two or three metres away on the wall and the
        roof move by 7 to 24 degrees. That split is the signature of a
        reference-position error, and one number over all of them would
        average it away.

        Each near disc is checked against its own geometry: how far the
        direction to it actually turns when the viewpoint moves. W1's turn is
        6.8 degrees and the scorer reports 6.8. W2's is 14.1, past the 8
        degree matching radius, so the scorer reports it as an omission --
        which is the contract's matching rule answering honestly, not a
        number it failed to produce.
        """
        offset = np.array([1.0, 0.0, 0.0])
        scores = self.corrupted("wrong-reference", "wrong-reference",
                                offset_m=list(offset))
        scene = read_json(self.case_dir / "truth" / "scene.json")
        c_ref = np.asarray(read_json(self.case_dir / "truth" / "reference.json")["c_ref"],
                           dtype=float)
        surface = set()
        for landmark in scene["surface_landmarks"]:
            surface.add(landmark["id"])
            centre = np.asarray(landmark["centre"], dtype=float)
            turned = angle_between(centre - c_ref, centre - (c_ref + offset))
            entry = self.entry(scores, landmark["id"])
            if entry["status"] == "found":
                self.assertAlmostEqual(entry["error_deg"], turned, delta=0.5,
                                       msg=landmark["id"])
            else:
                self.assertEqual(entry["status"], "omitted", landmark["id"])
                self.assertTrue(entry["observable"], landmark["id"])

        self.assertGreater(self.entry(scores, "W1")["error_deg"], 5.0)
        self.assertEqual(self.entry(scores, "W2")["status"], "omitted")
        background = [e["error_deg"] for e in self.found(scores)
                      if e["id"] not in surface]
        self.assertLess(self.median(background), 0.4)
        self.assertFalse(scores["gates"]["pass"])

    # -- the raster itself -------------------------------------------------

    def test_blurring_the_output_loses_most_of_the_landmarks(self):
        scores = self.corrupted("blur", "blur", radius_px=5)
        expected = scores["landmarks"]["expected"]
        self.assertGreaterEqual(len(scores["landmarks"]["omitted"]), 0.8 * expected)
        self.assertFalse(scores["gates"]["pass"])

    def test_erasing_the_landmarks_omits_every_one_of_them(self):
        scores = self.corrupted("erase-landmarks", "erase-landmarks")
        landmarks = scores["landmarks"]
        self.assertEqual(len(landmarks["omitted"]), landmarks["expected"])
        self.assertEqual(landmarks["found"], 0)
        self.assertEqual(landmarks["spurious"], 0)
        # The area is all still painted: coverage cannot answer this question.
        self.assertEqual(scores["coverage"]["panorama_alpha_fraction"], 1.0)
        self.assertFalse(scores["gates"]["pass"])

    def test_an_empty_panorama_scores_as_nothing_found(self):
        scores = self.corrupted("empty", "empty")
        self.assertEqual(scores["landmarks"]["found"], 0)
        self.assertEqual(scores["capture"]["holds_with_capture"], 0)
        self.assertEqual(scores["coverage"]["panorama_alpha_fraction"], 0.0)
        self.assertFalse(scores["gates"]["pass"])

    def test_erasing_the_horizon_strip_keeps_the_area_and_loses_the_boundary(self):
        """Total area cannot hide a missing boundary.

        Blanking the sky below 10 degrees leaves 74 per cent of the
        cos-weighted raster painted and every landmark above 15 degrees exactly
        where it was, while the whole boundary is unresolved. A report that
        looked only at painted area and at the landmarks it could still see
        would call this a pass.

        The brief's own strip stops at 15 degrees, which is 37 per cent of the
        hemisphere by solid angle and leaves the alpha fraction at 0.63; both
        widths are scored here, because the claim under test is about what
        high area coverage cannot buy.
        """
        for key, alt_max, floor in (("strip10", 10.0, 0.70), ("strip15", 15.0, 0.60)):
            with self.subTest(alt_max=alt_max):
                scores = self.corrupted(key, "erase-horizon-strip", alt_max=alt_max)
                self.assertGreater(scores["coverage"]["panorama_alpha_fraction"], floor)
                self.assertGreater(scores["horizon"]["unresolved_sr"], HEMISPHERE_SR / 2)
                self.assertFalse(scores["gates"]["no_unresolved_boundary"])
                self.assertFalse(scores["gates"]["pass"])
                for entry in scores["landmarks"]["per_landmark"]:
                    if entry["observable"] and entry["truth"]["alt"] > 15.0:
                        self.assertEqual(entry["status"], "found", entry["id"])
                        self.assertLess(entry["error_deg"], score.GATE_LANDMARK_P95,
                                        entry["id"])

    # -- the geometry-preserving control -----------------------------------

    def test_brightness_alone_changes_no_geometry_number(self):
        scores = self.corrupted("brightness", "brightness", gain=1.2)
        ideal_scores = self.ideal_scores
        pairs = [
            (scores["landmarks"]["errors_deg"]["median"],
             ideal_scores["landmarks"]["errors_deg"]["median"]),
            (scores["landmarks"]["errors_deg"]["p95"],
             ideal_scores["landmarks"]["errors_deg"]["p95"]),
            (scores["horizon"]["signed_error_deg"]["p95"],
             ideal_scores["horizon"]["signed_error_deg"]["p95"]),
            (scores["overlay"]["settled"]["p95_deg"],
             ideal_scores["overlay"]["settled"]["p95_deg"]),
            (scores["overlay"]["settled"]["max_deg"],
             ideal_scores["overlay"]["settled"]["max_deg"]),
            (scores["overlay"]["moving"]["p95_deg"],
             ideal_scores["overlay"]["moving"]["p95_deg"]),
            (scores["overlay"]["moving"]["max_deg"],
             ideal_scores["overlay"]["moving"]["max_deg"]),
        ]
        for measured, reference in pairs:
            self.assertAlmostEqual(measured, reference, delta=0.05)
        self.assertEqual(scores["landmarks"]["found"], ideal_scores["landmarks"]["found"])
        self.assertEqual(scores["gates"], ideal_scores["gates"])
        self.assertTrue(scores["gates"]["pass"])

    # -- one frame ---------------------------------------------------------

    def test_one_substituted_pose_shows_in_the_maximum_not_the_percentile(self):
        scores = self.corrupted("substitute-pose", "substitute-pose")
        overlay = scores["overlay"]
        worst = max(v for v in (overlay["settled"]["max_deg"],
                                overlay["moving"]["max_deg"]) if v is not None)
        self.assertGreater(worst, 1.0)
        self.assertGreaterEqual(overlay["frames_over_gate"], 1)
        # One frame in a thousand cannot move a percentile, which is the whole
        # reason `max_deg` and `frames_over_gate` are reported beside them.
        self.assertAlmostEqual(overlay["settled"]["p95_deg"],
                               self.ideal_scores["overlay"]["settled"]["p95_deg"],
                               delta=0.05)
        self.assertEqual(overlay["samples"], self.ideal_scores["overlay"]["samples"])

    def test_a_duplicated_frame_inflates_the_overlay_sample_count(self):
        """`samples` is the metric that answers, and it answers by one.

        The scorer counts event lines, not distinct frames, so a frame
        delivered twice is one extra scored sample and no missing one. Nothing
        in `scores.json` says a frame_id arrived twice; see the report.
        """
        scores = self.corrupted("duplicate-frame", "duplicate-frame")
        self.assertEqual(scores["overlay"]["samples"],
                         self.ideal_scores["overlay"]["samples"] + 1)
        self.assertEqual(scores["overlay"]["missing_fraction"], 0.0)

    # -- untouched files ---------------------------------------------------

    def test_a_corruption_copies_every_file_it_does_not_touch(self):
        out_dir = corrupt.apply(self.case_dir, self.ideal_dir, "brightness",
                                self.base / "brightness-copy", gain=1.2)
        for name in ("horizon.json", "events.jsonl", "captures.jsonl", "summary.json"):
            self.assertEqual((out_dir / name).read_bytes(),
                             (self.ideal_dir / name).read_bytes(), name)
        # The ideal directory's own scores.json describes the ideal result and
        # would be read as this one's: a corruption must not carry it over.
        self.assertFalse((out_dir / "scores.json").exists())

    def test_an_unknown_corruption_is_refused(self):
        with self.assertRaises(ValueError):
            corrupt.apply(self.case_dir, self.ideal_dir, "no-such-corruption",
                          self.base / "refused")

    # -- the report --------------------------------------------------------

    def test_the_report_is_one_self_contained_file(self):
        path = report.render(self.case_dir, self.ideal_scores,
                             self.base / "ideal-report.html",
                             result_dir=self.ideal_dir,
                             ideal_panorama=self.ideal_dir / "panorama.png")
        html = path.read_text(encoding="utf-8")
        # Nothing to fetch and nothing to run: a report that needs the network
        # is not evidence anyone can keep.
        self.assertNotIn("<script", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertEqual(html.count("<style"), 1)
        self.assertEqual(len(re.findall(r"<img\b", html)), 3)
        self.assertEqual(html.count('src="data:image/png;base64,'), 3)
        self.assertGreaterEqual(html.count("<svg"), 2)
        html.encode("ascii")  # no emoji, no typographic symbols

    def test_the_report_names_the_case_and_states_every_gate_in_words(self):
        path = report.render(self.case_dir, self.ideal_scores,
                             self.base / "ideal-report.html",
                             result_dir=self.ideal_dir,
                             ideal_panorama=self.ideal_dir / "panorama.png")
        html = path.read_text(encoding="utf-8")
        self.assertIn("chartyard-still-60", html)
        self.assertIn(self.ideal_scores["input_hash"][:16], html)
        for gate in self.ideal_scores["gates"]:
            self.assertIn(gate, html)
        self.assertIn("PASS", html)
        # The obstacle table and the landmark table are both in it.
        self.assertIn("trunk", html)
        self.assertIn("R0K0", html)
        self.assertIn("W1", html)

    def test_a_failing_report_says_fail_in_words(self):
        scores = self.corrupted("empty", "empty")
        out_dir = self.results["empty"][0]
        path = report.render(self.case_dir, scores, self.base / "empty-report.html",
                             result_dir=out_dir,
                             ideal_panorama=self.ideal_dir / "panorama.png")
        html = path.read_text(encoding="utf-8")
        self.assertIn("FAIL", html)
        self.assertEqual(len(re.findall(r"<img\b", html)), 3)
        html.encode("ascii")


if __name__ == "__main__":
    unittest.main()
