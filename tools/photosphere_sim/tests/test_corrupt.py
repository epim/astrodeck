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
from sim.geometry import angle_between, sky_vector, wrap_deg

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

        # The overlay carries the same yaw, and its own numbers must show it.
        # Turning a basis about the vertical by 5 degrees moves a forward
        # vector at altitude `a` by `2 asin(sin(2.5 deg) cos a)`, so the
        # largest possible overlay error is exactly 5, at the horizontal. The
        # still route holds 15 aims at altitude 0, which is well over 5 per
        # cent of the settled frames, so the settled p95 sits at that maximum
        # too: both are pinned at 5.0 here rather than a range, because the
        # closed form gives one number and the route puts the tail on it.
        overlay = scores["overlay"]
        self.assertAlmostEqual(overlay["settled"]["max_deg"], 5.0, delta=0.01)
        self.assertAlmostEqual(overlay["settled"]["p95_deg"], 5.0, delta=0.1)
        self.assertAlmostEqual(overlay["moving"]["max_deg"], 5.0, delta=0.01)
        self.assertFalse(scores["gates"]["overlay_settled_p95_lt_0_5"])
        self.assertFalse(scores["gates"]["overlay_moving_p95_lt_1"])
        # Five degrees is under the 10 degree maximum gate, so that one holds:
        # the yaw is a systematic error, not a flick.
        self.assertTrue(scores["gates"]["overlay_max_lt_10"])

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

    # -- roll ----------------------------------------------------------------

    def test_a_roll_rotates_right_and_up_about_forward_and_keeps_the_basis_orthonormal(self):
        """Issue #59: the corruption itself, read straight off events.jsonl.

        `forward` is bit-identical to the source; `right` and `up` are each
        exactly `deg` degrees from their own source vector; and the resulting
        basis is still orthonormal to 1e-9, because a roll about `forward` is
        an attitude a real device can hold, unlike `mirror`'s reflected one.

        Named mutation, verified by hand and reverted (not left in the
        tree): a roll that rotates `right` and forgets `up` reddens the
        `up`-angle assertion below (it reports 0.0, not 3.0, since `up` never
        moved) and reddens orthonormality too, because a rotated `right`
        paired with an untouched `up` is no longer perpendicular to it. See
        task-13-report.md.
        """
        deg = 3.0
        out_dir = corrupt.apply(self.case_dir, self.ideal_dir, "roll",
                                self.base / "roll-basis", deg=deg)
        source = [json.loads(line) for line in
                  (self.ideal_dir / "events.jsonl").read_text(encoding="utf-8")
                  .splitlines() if line]
        rolled = [json.loads(line) for line in
                  (out_dir / "events.jsonl").read_text(encoding="utf-8")
                  .splitlines() if line]
        self.assertEqual(len(source), len(rolled))
        checked = 0
        for before, after in zip(source, rolled):
            if before.get("basis") is None:
                continue
            checked += 1
            b_before, b_after = before["basis"], after["basis"]
            self.assertEqual(b_after["forward"], b_before["forward"])
            for key in ("right", "up"):
                angle = angle_between(b_after[key], b_before[key])
                self.assertAlmostEqual(angle, deg, delta=1e-6, msg=key)
            right = np.asarray(b_after["right"], dtype=np.float64)
            up = np.asarray(b_after["up"], dtype=np.float64)
            forward = np.asarray(b_after["forward"], dtype=np.float64)
            self.assertAlmostEqual(float(np.linalg.norm(right)), 1.0, delta=1e-9)
            self.assertAlmostEqual(float(np.linalg.norm(up)), 1.0, delta=1e-9)
            self.assertAlmostEqual(float(np.linalg.norm(forward)), 1.0, delta=1e-9)
            self.assertAlmostEqual(float(np.dot(right, up)), 0.0, delta=1e-9)
            self.assertAlmostEqual(float(np.dot(right, forward)), 0.0, delta=1e-9)
            self.assertAlmostEqual(float(np.dot(up, forward)), 0.0, delta=1e-9)
        self.assertGreater(checked, 0)

    def test_a_roll_moves_the_overlay_and_leaves_the_landmarks_and_horizon_alone(self):
        """The raster and the boundary carry no roll of their own (CONTRACT.md):
        only `overlay` may move; `landmarks` and `horizon` are exactly the
        ideal's.

        Named mutation: reverting `_score_overlay`'s error to `forward_error`
        alone reddens the `max_deg`/`frames_over_gate`/`pass` assertions here
        back to the ideal's own numbers, because `roll` never touches
        `forward` -- the same failure `test_score.py`'s
        `test_a_wrong_right_and_up_show_in_the_overlay_though_forward_is_untouched`
        pins directly against the metric.
        """
        deg = 3.0
        scores = self.corrupted("roll", "roll", deg=deg)
        self.assertEqual(scores["landmarks"], self.ideal_scores["landmarks"])
        self.assertEqual(scores["horizon"], self.ideal_scores["horizon"])

        overlay = scores["overlay"]
        self.assertGreaterEqual(overlay["settled"]["max_deg"], deg - 1e-6)
        self.assertGreaterEqual(overlay["moving"]["max_deg"], deg - 1e-6)
        self.assertAlmostEqual(overlay["settled"]["max_forward_deg"], 0.0, places=7)
        self.assertAlmostEqual(overlay["moving"]["max_forward_deg"], 0.0, places=7)
        self.assertAlmostEqual(overlay["settled"]["max_right_deg"], deg, delta=0.01)
        self.assertAlmostEqual(overlay["settled"]["max_up_deg"], deg, delta=0.01)
        self.assertAlmostEqual(overlay["moving"]["max_right_deg"], deg, delta=0.01)
        self.assertAlmostEqual(overlay["moving"]["max_up_deg"], deg, delta=0.01)
        self.assertGreater(overlay["frames_over_gate"], 0)
        self.assertFalse(scores["gates"]["overlay_settled_p95_lt_0_5"])
        self.assertFalse(scores["gates"]["overlay_moving_p95_lt_1"])
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

        # The same warp is on the overlay's forward vectors. Its worst value
        # is at 45 degrees, `atan(1.05 tan 45) - 45 = 1.40`, and the route's
        # aims reach 35 and 70 degrees, so the settled maximum is well past
        # the 0.5 degree settled gate. A landmark-only assertion here would
        # pass with the overlay left untouched.
        overlay = scores["overlay"]
        self.assertGreater(overlay["settled"]["max_deg"], 0.5)
        self.assertLess(overlay["settled"]["max_deg"], 1.41)
        self.assertFalse(scores["gates"]["overlay_settled_p95_lt_0_5"])

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

        # Reflecting the basis about the north-south plane turns a forward
        # vector aimed east into one aimed west, so the overlay error reaches
        # most of a half turn. Anything under 90 here would mean the overlay
        # was not mirrored at all.
        overlay = scores["overlay"]
        self.assertGreater(overlay["settled"]["max_deg"], 90.0)
        self.assertFalse(scores["gates"]["overlay_max_lt_10"])

    # -- sections ----------------------------------------------------------

    def test_a_duplicated_section_is_reported_as_a_duplicate(self):
        """Copying 40 degrees of sky over its neighbour duplicates a landmark.

        Forty degrees of azimuth is 3.5 degrees of angle at altitude 85, well
        inside the 8 degree matching radius, so the copy of a landmark up
        there is a second blob of the same landmark rather than a spurious
        one. Lower down the copies land too far away to be matched and are
        reported as spurious instead, which is the honest answer for them.
        """
        az0, width = 60.0, 40.0
        scores = self.corrupted("duplicate-section", "duplicate-section",
                                az0=az0, width=width)
        # The closed form: an observable landmark in the copied range whose
        # copy, `width` degrees east of it at its own altitude, lands inside
        # the matching radius. `2 asin(sin(width / 2) cos alt) <= 8` is true
        # only above altitude 78.5, so of the four landmarks in [60, 100) it
        # is the one at 85 and no other.
        expected_duplicates = sorted(
            entry["id"] for entry in scores["landmarks"]["per_landmark"]
            if entry["observable"]
            and ((entry["truth"]["az"] - az0) % 360.0) < width
            and angle_between(
                sky_vector(entry["truth"]["az"], entry["truth"]["alt"]),
                sky_vector(entry["truth"]["az"] + width, entry["truth"]["alt"])
            ) <= score.MATCH_RADIUS_DEG)
        self.assertTrue(expected_duplicates)
        self.assertEqual(sorted(scores["landmarks"]["duplicated"]),
                         expected_duplicates)
        self.assertFalse(scores["gates"]["no_duplicates"])
        self.assertFalse(scores["gates"]["pass"])

    def test_a_removed_section_is_omission_and_unresolved_area(self):
        width = 40.0
        scores = self.corrupted("remove-section", "remove-section",
                                az0=100.0, width=width)
        self.assertTrue(scores["landmarks"]["omitted"])
        self.assertLess(scores["coverage"]["observable_fraction_covered"], 0.95)
        # The closed form: an azimuth wedge of `width` degrees, from the
        # horizontal to the zenith, is `width / 360 * 2 pi` steradians.
        # Reporting it in solid angle is the point -- counting the raster's
        # equirectangular cells equally would inflate a wedge at the pole.
        wedge = width / 360.0 * HEMISPHERE_SR
        self.assertAlmostEqual(scores["horizon"]["unresolved_sr"] / wedge,
                               1.0, delta=0.02)
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

        Blanking the sky below 10 degrees leaves 0.704 of the cos-weighted
        raster painted and every landmark above 15 degrees exactly where it
        was, while the whole boundary is unresolved. A report that
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
        # And the gate that reads the maximum rather than a percentile fails,
        # so a flick of the aim dot across a cell is a failing result and not
        # a footnote under a green one.
        self.assertGreater(worst, 10.0)
        self.assertFalse(scores["gates"]["overlay_max_lt_10"])
        self.assertFalse(scores["gates"]["pass"])

    def test_a_duplicated_frame_is_named_and_not_counted_twice(self):
        """`duplicate_frame_ids` is the metric that answers, and it fails a gate.

        The repeat is not a second sample: the sample count is the ideal's,
        the first line's values are the ones scored, and the duplication shows
        up as the one number it is. A scanner cannot raise its own sample
        count by reprocessing a frame.
        """
        scores = self.corrupted("duplicate-frame", "duplicate-frame")
        overlay = scores["overlay"]
        self.assertEqual(overlay["duplicate_frame_ids"], 1)
        self.assertEqual(overlay["samples"], self.ideal_scores["overlay"]["samples"])
        self.assertEqual(overlay["missing_fraction"], 0.0)
        self.assertFalse(scores["gates"]["no_duplicate_frames"])
        self.assertFalse(scores["gates"]["pass"])

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

    def test_an_unknown_parameter_is_refused(self):
        """A corruption that did not happen would be scored as a clean result.

        A misspelled parameter is the quiet version of that: the corruption
        runs with its default, the scores come back as expected, and the run
        reads as a scorer that cannot fail.
        """
        with self.assertRaises(TypeError):
            corrupt.apply(self.case_dir, self.ideal_dir, "yaw",
                          self.base / "refused-param", degrees=5.0)

    def test_corrupting_a_result_directory_in_place_is_refused(self):
        """The uncorrupted result is the baseline; it is not the output.

        Corrupting in place would destroy the only thing every one of these
        tests is measured against, and it would do it silently.
        """
        before = {path.name: path.read_bytes()
                  for path in sorted(self.ideal_dir.iterdir()) if path.is_file()}
        with self.assertRaises(ValueError):
            corrupt.apply(self.case_dir, self.ideal_dir, "brightness",
                          self.ideal_dir, gain=1.2)
        after = {path.name: path.read_bytes()
                 for path in sorted(self.ideal_dir.iterdir()) if path.is_file()}
        self.assertEqual(before, after)
        self.assertIn("panorama.png", before)

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

    def test_the_boundary_plot_never_draws_a_disclaimed_altitude_as_measured(self):
        """An uncertain bin is not a measurement, and must not be drawn as one.

        `remove-section` writes altitude 90 into the bins it marks uncertain.
        Plotted as one unbroken stroke, that reads as a boundary the scanner
        claimed reaches the zenith; plotted as a gap with a dashed grey line
        through it, it reads as what it is. The measured stroke must contain
        no point inside the uncertain run.
        """
        out_dir = self.base / "svg-uncertain"
        out_dir.mkdir(parents=True, exist_ok=True)
        bins = 30
        (out_dir / "horizon.json").write_text(json.dumps({
            "bins": bins,
            "points": [{"az": (i + 0.5) * 360.0 / bins,
                        "alt": 90.0 if 8 <= i <= 11 else 5.0}
                       for i in range(bins)],
            "uncertain_bins": [8, 9, 10, 11],
        }), encoding="utf-8", newline="\n")

        svg = report._horizon_svg(self.case_dir, out_dir)
        measured = re.findall(r'<polyline[^>]*stroke="#ff8a3d"[^>]*points="([^"]*)"', svg)
        dashed = re.findall(
            r'<polyline[^>]*stroke-dasharray="[^"]*"[^>]*points="([^"]*)"', svg)
        self.assertEqual(len(measured), 2)   # one run either side of the gap
        self.assertEqual(len(dashed), 1)

        left, plot_width = 46.0, 1080.0 - 46.0 - 12.0

        def x_of(index):
            return left + (index + 0.5) / bins * plot_width

        def xs(points):
            return [float(pair.split(",")[0]) for pair in points.split()]

        drawn = [x for run in measured for x in xs(run)]
        self.assertEqual(len(drawn), bins - 4)
        for index in (8, 9, 10, 11):
            for x in drawn:
                self.assertNotAlmostEqual(x, x_of(index), places=1)
        self.assertEqual([round(x, 1) for x in xs(dashed[0])],
                         [round(x_of(index), 1) for index in (8, 9, 10, 11)])

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
