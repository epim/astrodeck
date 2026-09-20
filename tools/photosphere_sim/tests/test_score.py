"""``score.score_case`` against the ideal result ``ideal.make_ideal_result`` builds.

The scorer is developed against an output that is right by construction, so
every number it reports here is quantisation and nothing else. One class
builds the case and the ideal result once; the variants (a coarse horizon, a
rotated horizon, a missing capture log, a blank panorama) are cheap copies of
that one result, so the expensive truth is computed a single time.
"""
import contextlib
import io
import json
import math
import pathlib
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

from sim import cases, ideal, score
from sim.__main__ import main as cli_main
from sim.geometry import Camera, look_basis, sky_vector

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path):
    return [json.loads(line) for line
            in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path, data):
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8", newline="\n")


def rebin_horizon(path, bins):
    """Replace a horizon file with one of ``bins`` bins, each holding a maximum."""
    data = read_json(path)
    alt = np.array([point["alt"] for point in data["points"]], dtype=float)
    grouped = alt.reshape(bins, alt.size // bins).max(axis=1)
    write_json(path, {
        "bins": bins,
        "points": [{"az": (i + 0.5) * 360.0 / bins, "alt": float(a)}
                   for i, a in enumerate(grouped)],
        "uncertain_bins": [],
    })


def roll_horizon(path, steps):
    """Turn a horizon file east by ``steps`` bins, an artificial north error."""
    data = read_json(path)
    alt = np.roll(np.array([point["alt"] for point in data["points"]], dtype=float), steps)
    for point, value in zip(data["points"], alt):
        point["alt"] = float(value)
    write_json(path, data)


class IdealResult(unittest.TestCase):
    """One built case, one ideal result, and the variants derived from it."""

    @classmethod
    def setUpClass(cls):
        case_def = read_json(ROOT / "cases" / "chartyard-still-60.json")
        cls.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(cls.tmp.name)
        cls.case_dir = cases.build_case(case_def, base / "cases", cases.flat_renderer)
        cls.result_dir = ideal.make_ideal_result(cls.case_dir, cls.case_dir / "result")
        cls.scores = score.score_case(cls.case_dir)
        # The frame count follows from the route's own timing (CONTRACT.md's
        # move-duration rule), not a number pinned in this file: read it back
        # from the built case rather than hardcoding it, so a route timing
        # change changes this test's expectation along with the case.
        cls.frame_count = len(read_jsonl(cls.case_dir / "truth" / "trajectory.jsonl"))

        cls.truth_landmarks = read_json(cls.case_dir / "truth" / "landmarks.json")
        cls.holds = read_json(cls.case_dir / "truth" / "holds.json")

        cls.coarse_dir = base / "coarse"
        shutil.copytree(cls.result_dir, cls.coarse_dir)
        rebin_horizon(cls.coarse_dir / "horizon.json", 30)
        cls.coarse = score.score_case(cls.case_dir, cls.coarse_dir)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # -- the ideal result passes every gate --------------------------------

    def test_no_landmark_is_omitted_or_duplicated(self):
        self.assertEqual(self.scores["landmarks"]["omitted"], [])
        self.assertEqual(self.scores["landmarks"]["duplicated"], [])
        self.assertEqual(self.scores["landmarks"]["spurious"], 0)

    def test_landmark_error_is_raster_quantisation_only(self):
        errors = self.scores["landmarks"]["errors_deg"]
        self.assertLess(errors["p95"], 0.4)
        self.assertLess(errors["p99"], 1.0)

    def test_horizon_matches_the_truth_it_was_built_from(self):
        horizon = self.scores["horizon"]
        self.assertEqual(horizon["truth_bins"], 3600)
        self.assertEqual(horizon["measured_bins"], 3600)
        self.assertAlmostEqual(horizon["measured_resolution_deg"], 0.1)
        self.assertLess(horizon["signed_error_deg"]["p95"], 0.5)
        self.assertEqual(horizon["missed_obstructions"], [])
        self.assertEqual([o["id"] for o in horizon["obstacles"]],
                         ["pole-near", "pole-far", "roof-south", "wall-east", "trunk"])
        self.assertEqual(horizon["unresolved_sr"], 0.0)
        self.assertEqual(horizon["false_open_sr"], 0.0)
        self.assertEqual(horizon["false_blocked_sr"], 0.0)
        self.assertEqual(horizon["north_offset_deg"], 0.0)

    def test_overlay_reproduces_the_truth_basis(self):
        overlay = self.scores["overlay"]
        self.assertEqual(overlay["missing_fraction"], 0.0)
        self.assertLess(overlay["settled"]["p95_deg"], 0.01)
        self.assertLess(overlay["moving"]["p95_deg"], 0.01)
        self.assertEqual(overlay["duplicate_frame_ids"], 0)
        self.assertEqual(overlay["frames_over_gate"], 0)
        # Issue #59: the per-axis maxima are quantisation only on the ideal,
        # same as the combined max_deg they sit beside.
        for block in ("settled", "moving"):
            self.assertLess(overlay[block]["max_forward_deg"], 0.01, block)
            self.assertLess(overlay[block]["max_right_deg"], 0.01, block)
            self.assertLess(overlay[block]["max_up_deg"], 0.01, block)

    def test_every_hold_is_captured_700_ms_in(self):
        capture = self.scores["capture"]
        self.assertEqual(capture["holds"], len(self.holds))
        self.assertEqual(capture["holds_with_capture"], capture["holds"])
        self.assertEqual(capture["latency_ms"]["max"], 700)
        # The ideal photographs every hold, so nothing is satisfied by a cell
        # that was already covered.
        self.assertEqual(capture["holds_satisfied"], capture["holds"])
        self.assertEqual(capture["holds_captured"], capture["holds"])
        self.assertEqual(capture["holds_already_covered"], 0)
        self.assertEqual(capture["accepted_frames"], capture["holds"])

    def test_coverage_is_complete(self):
        coverage = self.scores["coverage"]
        self.assertEqual(coverage["panorama_alpha_fraction"], 1.0)
        self.assertGreater(coverage["observable_fraction_covered"], 0.99)
        self.assertEqual(coverage["cells_covered_fraction"], 1.0)

    def test_every_gate_passes(self):
        gates = self.scores["gates"]
        self.assertTrue(gates["pass"], gates)
        self.assertTrue(all(gates.values()), gates)

    # -- the shape of the report -------------------------------------------

    def test_per_landmark_covers_all_52_and_expected_counts_the_40_observable(self):
        # The chart yard has 52 landmarks and 40 of them are observable from
        # c_ref; both are pinned here as literals, so a scene change or a
        # visibility regression fails this test rather than moving with it.
        landmarks = self.scores["landmarks"]
        self.assertEqual(len(landmarks["per_landmark"]), 52)
        self.assertEqual(landmarks["expected"], 40)
        self.assertEqual(landmarks["found"], 40)
        self.assertEqual(sum(1 for lm in self.truth_landmarks if lm["observable"]), 40)

    def test_per_landmark_statuses_reconcile_with_the_aggregates(self):
        landmarks = self.scores["landmarks"]
        per_landmark = landmarks["per_landmark"]
        counts = {}
        for entry in per_landmark:
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        self.assertEqual(counts, {"found": 40, "sliver": 2, "not_observable": 10})
        self.assertEqual(counts.get("found", 0), landmarks["found"])
        self.assertEqual([e["id"] for e in per_landmark if e["status"] == "omitted"],
                         landmarks["omitted"])
        self.assertEqual([e["id"] for e in per_landmark if e["status"] == "duplicate"],
                         landmarks["duplicated"])
        self.assertEqual(counts.get("sliver", 0), landmarks["slivers"])
        self.assertEqual(sum(counts.values()), 52)
        # observable == the truth file's own flag, entry by entry.
        flags = {lm["id"]: lm["observable"] for lm in self.truth_landmarks}
        self.assertEqual({e["id"]: e["observable"] for e in per_landmark}, flags)
        self.assertEqual(sum(1 for e in per_landmark if e["observable"]),
                         landmarks["expected"])

    def test_a_disc_on_a_curved_face_is_expected_to_be_small(self):
        """T1 is a 0.08 m disc on a 0.25 m trunk, clipped by the normal test.

        Without the clipping factor the filter expects 7.03 cells of it and
        demands 2.81; T1 paints 3, and cleared by under 7 per cent.
        """
        entry = next(e for e in self.scores["landmarks"]["per_landmark"]
                     if e["id"] == "T1")
        self.assertAlmostEqual(entry["expected_px"], 3.10, delta=0.05)
        self.assertLess(score.MIN_DISC_FRACTION * entry["expected_px"], 2.0)
        self.assertEqual(entry["status"], "found")
        # A flat disc on a box face keeps its unclipped area.
        flat = next(e for e in self.scores["landmarks"]["per_landmark"]
                    if e["id"] == "W1")
        self.assertAlmostEqual(flat["expected_px"], 35.56, delta=0.1)

    def test_scores_json_is_written_beside_the_result(self):
        written = read_json(self.result_dir / "scores.json")
        self.assertEqual(written, self.scores)
        self.assertEqual(written["schema"], 1)
        self.assertEqual(written["case_id"], "chartyard-still-60")

    # -- a 30-bin horizon cannot carry the pole ----------------------------

    def test_thirty_bins_fail_only_the_horizon_gate(self):
        horizon = self.coarse["horizon"]
        self.assertEqual(horizon["measured_bins"], 30)
        self.assertEqual(horizon["measured_resolution_deg"], 12.0)
        gates = self.coarse["gates"]
        self.assertFalse(gates["horizon_p95_lt_1"])
        self.assertFalse(gates["pass"])
        for name in ("landmarks_p95_lt_0_5", "landmarks_p99_lt_1",
                     "no_omissions", "no_duplicates", "no_missed_obstructions"):
            self.assertTrue(gates[name], name)
        # A 12 degree bin holding its maximum is never below the truth by
        # more than the width of one obstacle's edge, so no obstacle is lost.
        self.assertEqual(horizon["missed_obstructions"], [])
        for obstacle in horizon["obstacles"]:
            self.assertLessEqual(obstacle["deficit_median"], 1.0, obstacle["id"])
        # Issue #53: resolvability is the obstacle's own visible silhouette
        # against one product bin (12 degrees), never its declared label.
        # The two poles and the trunk are genuinely narrower than one bin
        # (2.2, 0.3 and 3.0 degrees) and stay unresolved at this resolution
        # whatever the scanner does; the roof and the east wall are wide
        # (146.6 and 84.0 degrees) despite both declaring only 10, and are
        # resolvable here even though their own bin is coarse. All five pass
        # `no_missed_obstructions` on this coarse-but-accurate rebin (a 12
        # degree bin holding its maximum loses no obstacle), which is real
        # evidence for the two resolvable ones and merely uninformative for
        # the three that are not.
        resolvable_here = {"pole-near": False, "pole-far": False,
                           "roof-south": True, "wall-east": True, "trunk": False}
        for obstacle in horizon["obstacles"]:
            self.assertEqual(obstacle["resolvable"], resolvable_here[obstacle["id"]],
                             obstacle["id"])
            self.assertEqual(obstacle["resolvable_width_deg"], 12.0, obstacle["id"])

    def test_thirty_bins_keep_the_landmark_numbers(self):
        self.assertEqual(self.coarse["landmarks"], self.scores["landmarks"])

    # -- the scorer measures, it does not merely flag ----------------------

    def test_a_rotated_horizon_is_reported_as_a_north_offset(self):
        rolled = pathlib.Path(self.tmp.name) / "rolled"
        shutil.copytree(self.result_dir, rolled)
        roll_horizon(rolled / "horizon.json", 20)  # 20 bins of 0.1 degree
        scores = score.score_case(self.case_dir, rolled)
        self.assertAlmostEqual(scores["horizon"]["north_offset_deg"], 2.0)
        self.assertFalse(scores["gates"]["pass"])

    def test_a_flattened_horizon_reports_the_truth_area_as_false_open(self):
        """The false-open area is solid angle, checked against the closed form.

        An azimuth wedge of width ``dz`` radians from the horizontal up to
        ``a`` covers ``dz * sin(a)`` steradians. A boundary reported as flat 0
        leaves every truth obstruction false-open, so the scorer's cell sum
        must come out at the sum of those wedges. Weighting equirectangular
        cells equally instead of by ``cos(alt)`` would inflate this by a
        factor of several.
        """
        flat = pathlib.Path(self.tmp.name) / "flat"
        shutil.copytree(self.result_dir, flat)
        data = read_json(flat / "horizon.json")
        for point in data["points"]:
            point["alt"] = 0.0
        write_json(flat / "horizon.json", data)
        scores = score.score_case(self.case_dir, flat)

        reference = read_json(self.case_dir / "truth" / "reference-horizon.json")
        alt = np.clip(np.array(reference["alt_max"], dtype=float), 0.0, 90.0)
        wedge = np.radians(360.0 / alt.size)
        closed_form = float((wedge * np.sin(np.radians(alt))).sum())
        self.assertGreater(closed_form, 0.1)
        self.assertAlmostEqual(scores["horizon"]["false_open_sr"] / closed_form,
                               1.0, places=2)
        self.assertEqual(scores["horizon"]["false_blocked_sr"], 0.0)
        self.assertFalse(scores["gates"]["no_missed_obstructions"])

    # -- an obstacle is scored against its own silhouette ------------------

    def _trunk_bins(self):
        """The bins the trunk is the first thing hit in, and its own altitude."""
        reference = read_json(self.case_dir / "truth" / "reference-horizon.json")
        trunk = next(o for o in reference["obstacles"] if o["id"] == "trunk")
        profile = np.array(trunk["profile"], dtype=float)
        return np.nonzero(profile > -10.0)[0], profile

    def _measured_over(self, name, indices, values):
        target = pathlib.Path(self.tmp.name) / name
        shutil.copytree(self.result_dir, target)
        data = read_json(target / "horizon.json")
        for offset, index in enumerate(indices):
            data["points"][int(index)]["alt"] = float(
                values[offset] if hasattr(values, "__len__") else values)
        write_json(target / "horizon.json", data)
        return score.score_case(self.case_dir, target)

    def test_the_envelope_over_the_trunk_says_nothing_about_the_trunk(self):
        """The trunk stands under its canopy; the envelope there is the canopy.

        A boundary that reports the trunk's own 21.5 degrees has FOUND the
        trunk, even though it is 24 degrees under the envelope. Scoring the
        trunk against the envelope calls that a missed trunk, which is the
        defect: the envelope answer is about the canopy.
        """
        indices, profile = self._trunk_bins()
        self.assertEqual(indices.size, 30)  # 3.0 degrees, 0.1 per bin
        self.assertAlmostEqual(float(profile[indices].max()), 21.5, delta=0.1)

        scores = self._measured_over("trunk-only", indices, profile[indices])
        trunk = next(o for o in scores["horizon"]["obstacles"] if o["id"] == "trunk")
        self.assertAlmostEqual(trunk["truth_alt_peak"], 21.5, delta=0.1)
        self.assertAlmostEqual(trunk["deficit_median"], 0.0, places=9)
        self.assertEqual(trunk["width_missed_deg"], 0.0)
        self.assertFalse(trunk["missed"])
        self.assertEqual(scores["horizon"]["missed_obstructions"], [])
        # The canopy it stopped describing shows up as false-open area.
        self.assertGreater(scores["horizon"]["false_open_sr"], 0.0)

        # On the ideal the same entry is 24 degrees the other way: the
        # measured boundary is the canopy, which more than covers the trunk.
        ideal_trunk = next(o for o in self.scores["horizon"]["obstacles"]
                           if o["id"] == "trunk")
        self.assertLess(ideal_trunk["deficit_median"], -20.0)
        self.assertFalse(ideal_trunk["missed"])

    def test_zeroing_an_obstacles_span_marks_that_obstacle_missed(self):
        indices, _ = self._trunk_bins()
        scores = self._measured_over("trunk-gone", indices, 0.0)
        by_id = {o["id"]: o for o in scores["horizon"]["obstacles"]}
        self.assertAlmostEqual(by_id["trunk"]["deficit_median"], 21.5, delta=0.1)
        self.assertAlmostEqual(by_id["trunk"]["width_missed_deg"], 3.0, places=9)
        self.assertEqual(by_id["trunk"]["min_width_deg"], 1.0)
        self.assertTrue(by_id["trunk"]["missed"])
        self.assertEqual(scores["horizon"]["missed_obstructions"], ["trunk"])
        self.assertFalse(scores["gates"]["no_missed_obstructions"])
        for name in ("pole-near", "pole-far", "roof-south", "wall-east"):
            self.assertFalse(by_id[name]["missed"], name)

    def test_the_ideal_leaves_no_width_missed_at_either_resolution(self):
        """The width term cannot fire on a boundary that is never below truth.

        Both ideal boundaries hold each bin's maximum of the envelope, and the
        envelope is at least each obstacle's own silhouette, so every deficit
        is at or below zero and every missed width is exactly 0.0. This is
        what makes the width term safe to add: it costs the ideal nothing.
        """
        for label, scores in (("3600 bins", self.scores), ("30 bins", self.coarse)):
            for obstacle in scores["horizon"]["obstacles"]:
                self.assertEqual(obstacle["width_missed_deg"], 0.0,
                                 f'{label} {obstacle["id"]}')
                # Issue #64: the run and the total are both zero here, which
                # is the only reading on which they have to agree.
                self.assertEqual(obstacle["width_missed_total_deg"], 0.0,
                                 f'{label} {obstacle["id"]}')
            self.assertEqual(scores["horizon"]["missed_obstructions"], [], label)

    def test_scattered_bins_along_the_roof_do_not_add_up_to_a_stretch(self):
        """Issue #64: ten separated one-degree gaps are not a 10 degree miss.

        Ten separated stretches of 10 bins each are cut out of the roof, 50
        bins apart, so 10.0 degrees of the roof is under-reported in total
        while the widest run is 1.0 degree. The scene declares the roof must
        be found at 10 degrees of width, and the product's bin here is 0.1,
        so `resolvable_width_deg` is 10.0: on the old total-count reading
        this obstacle was MISSED at exactly its threshold, and on the run
        reading it is found, because nothing 10 degrees wide is gone. The
        deficit median stays far below 1.0 either way, so the width term is
        the only term that can decide this row.

        Named mutation: key `too_narrow` on `width_missed_total` instead of
        the run (the pre-#64 behaviour). `width_missed_total_deg` is 10.0
        against a 10.0 threshold, the row goes MISSED, and the last three
        assertions here redden. Run and reverted; see task-2-report.md.
        """
        reference = read_json(self.case_dir / "truth" / "reference-horizon.json")
        roof = next(o for o in reference["obstacles"] if o["id"] == "roof-south")
        profile = np.array(roof["profile"], dtype=float)
        azimuths = (np.arange(profile.size) + 0.5) * (360.0 / profile.size)
        high = np.nonzero((profile > 5.0) & (azimuths > 140.0))[0]
        scattered = np.concatenate([high[start:start + 10]
                                    for start in range(0, 500, 50)])
        self.assertEqual(scattered.size, 100)  # 10.0 degrees in all

        scores = self._measured_over("roof-scattered", scattered, 0.0)
        entry = next(o for o in scores["horizon"]["obstacles"]
                     if o["id"] == "roof-south")
        self.assertEqual(entry["resolvable_width_deg"], 10.0)
        self.assertLess(entry["deficit_median"], 1.0)
        self.assertAlmostEqual(entry["width_missed_total_deg"], 10.0, places=9)
        self.assertAlmostEqual(entry["width_missed_deg"], 1.0, places=9)
        self.assertFalse(entry["missed"])
        self.assertEqual(scores["horizon"]["missed_obstructions"], [])

    def test_a_narrow_notch_in_the_roof_is_missed_by_width_alone(self):
        """Ten degrees of the roof's 146 is below the boundary; its median is not.

        The roof is the widest obstacle in the yard, so a notch cut in it
        moves the median deficit hardly at all: over 1466 bins, 100 of them
        deeply wrong leaves the median at zero. The obstacle is nonetheless
        missed, because the scene declares it must be found at 10 degrees of
        width and 10 degrees of it are gone. Without the width term this is
        the shape of failure the median hides.
        """
        reference = read_json(self.case_dir / "truth" / "reference-horizon.json")
        roof = next(o for o in reference["obstacles"] if o["id"] == "roof-south")
        profile = np.array(roof["profile"], dtype=float)
        # Well above the 1 degree deficit threshold, so every zeroed bin
        # counts toward the missed width and none of it is edge quantisation;
        # and past azimuth 140, where the roof no longer shares its azimuths
        # with the east wall, so the notch belongs to one obstacle only.
        azimuths = (np.arange(profile.size) + 0.5) * (360.0 / profile.size)
        high = np.nonzero((profile > 5.0) & (azimuths > 140.0))[0]
        notch = high[:100]
        self.assertEqual(int(notch[-1] - notch[0]), 99)  # contiguous, 10.0 deg

        scores = self._measured_over("roof-notch", notch, 0.0)
        by_id = {o["id"]: o for o in scores["horizon"]["obstacles"]}
        entry = by_id["roof-south"]
        self.assertEqual(entry["min_width_deg"], 10)
        self.assertAlmostEqual(entry["width_missed_deg"], 10.0, places=9)
        # One stretch, so the run and the total are the same 10.0 here. The
        # case that separates them is
        # `test_scattered_bins_along_the_roof_do_not_add_up_to_a_stretch`.
        self.assertAlmostEqual(entry["width_missed_total_deg"], 10.0, places=9)
        self.assertLess(entry["deficit_median"], 1.0)
        self.assertTrue(entry["missed"])
        self.assertEqual(scores["horizon"]["missed_obstructions"], ["roof-south"])
        self.assertFalse(scores["gates"]["no_missed_obstructions"])
        self.assertFalse(scores["gates"]["pass"])
        # Nothing else moved: the notch is 100 bins of 3600.
        for name in ("pole-near", "pole-far", "wall-east", "trunk"):
            self.assertFalse(by_id[name]["missed"], name)

    # -- a result with no boundary at all ----------------------------------

    def _without_boundary(self, name, horizon=None):
        """The ideal result with `horizon.json` removed, or replaced."""
        target = pathlib.Path(self.tmp.name) / name
        shutil.copytree(self.result_dir, target)
        if horizon is None:
            (target / "horizon.json").unlink()
        else:
            write_json(target / "horizon.json", horizon)
        return score.score_case(self.case_dir, target)

    def test_a_result_with_no_horizon_file_misses_every_visible_obstacle(self):
        """Issue #64: the `product_bins` fallback, pinned.

        No `result/horizon.json` is a scanner that reported no boundary, not
        a scanner excused from reporting one. There is no product resolution
        to defer to, so `resolvable` cannot be read off one: every obstacle
        is resolvable, `resolvable_width_deg` falls back to the scene's
        declared width, nothing is resolved so the deficit and width figures
        are `null`, and every visible obstacle is missed.

        Named mutation: make the fallback `resolvable = False` when
        `bin_width_deg is None` (the reading that treats "no bins" as "the
        product could not have resolved anything"). `missed_obstructions`
        comes back empty, `no_missed_obstructions` passes, and a scanner that
        emitted no boundary at all scores better than one that emitted a
        wrong one. Run and reverted; see task-2-report.md.
        """
        scores = self._without_boundary("no-horizon")
        horizon = scores["horizon"]
        self.assertEqual(horizon["measured_bins"], 0)
        self.assertIsNone(horizon["measured_resolution_deg"])
        self.assertEqual(horizon["signed_error_deg"]["p95"], None)
        for obstacle in horizon["obstacles"]:
            with self.subTest(obstacle=obstacle["id"]):
                self.assertTrue(obstacle["resolvable"])
                self.assertEqual(obstacle["resolvable_width_deg"],
                                 obstacle["min_width_deg"])
                self.assertIsNone(obstacle["deficit_median"])
                self.assertIsNone(obstacle["deficit_p95"])
                self.assertIsNone(obstacle["width_missed_deg"])
                self.assertIsNone(obstacle["width_missed_total_deg"])
                self.assertTrue(obstacle["missed"])
        self.assertEqual(horizon["missed_obstructions"],
                         [o["id"] for o in horizon["obstacles"]])
        self.assertFalse(scores["gates"]["no_missed_obstructions"])
        self.assertFalse(scores["gates"]["horizon_p95_lt_1"])
        self.assertFalse(scores["gates"]["pass"])

    def test_an_empty_boundary_is_the_same_as_no_boundary(self):
        """A file carrying no points says exactly as much as no file.

        Silence is not a flat horizon, and a well-formed file with an empty
        `points` array is silence in an envelope. The two must score alike,
        or a scanner could buy a better score by writing an empty file.

        Named mutation: drop ``or not measured.get("points")`` from
        ``_measured_profile``'s guard, so an empty ``points`` array goes down
        the live path instead of the no-boundary one. The measured bin count
        is then 0 and the resample divides ``360.0 / bins``: this case dies
        with ``ZeroDivisionError``, while the sibling above stays green
        (``not measured`` still catches a missing file). Note the mutation of
        the ``resolvable`` fallback that reddens that sibling does NOT redden
        this one and cannot: this is a differential case, and a fallback
        change moves both of its sides together. Run and reverted; see
        task-2-report.md.
        """
        empty = self._without_boundary(
            "empty-horizon", {"bins": 0, "points": [], "uncertain_bins": []})
        missing = self._without_boundary("no-horizon-again")
        self.assertEqual(empty["horizon"], missing["horizon"])
        self.assertEqual(empty["gates"], missing["gates"])

    def test_zeroing_the_whole_canopy_span_also_loses_the_trunk(self):
        """The canopy is not a declared obstacle; the trunk beneath it is."""
        step = 360.0 / 3600
        canopy = np.nonzero(((np.arange(3600) + 0.5) * step >= 26.0)
                            & ((np.arange(3600) + 0.5) * step < 53.0))[0]
        scores = self._measured_over("canopy-gone", canopy, 0.0)
        self.assertIn("trunk", scores["horizon"]["missed_obstructions"])

    # -- one bad frame, one reused capture ---------------------------------

    def test_a_capture_is_claimed_by_one_hold_only(self):
        """Holds 0 and 1 both admit a record at t = 2000; only one may have it.

        Hold 0 closes at 1200 and the 1500 ms grace carries its window to
        2700, while hold 1 opens at 2000. Without the claim, one record would
        answer for two holds.
        """
        self.assertLessEqual(self.holds[1]["from_ms"],
                             self.holds[0]["to_ms"] + 1500)
        scores = self._with_captures(
            "one-capture", [{"at": 2000, "outcome": "accepted", "cell": 0}])
        self.assertEqual(scores["capture"]["accepted_frames"], 1)
        self.assertEqual(scores["capture"]["holds_with_capture"], 1)
        self.assertEqual(scores["capture"]["latency_ms"]["max"], 2000)
        # And the same across the two satisfying outcomes: one
        # already-captured record cannot answer for two holds either.
        mixed = self._with_captures(
            "one-revisit", [{"at": 2000, "outcome": "already-captured", "cell": 0}])
        self.assertEqual(mixed["capture"]["holds_satisfied"], 1)
        self.assertEqual(mixed["capture"]["holds_already_covered"], 1)

    def _with_captures(self, name, records):
        target = pathlib.Path(self.tmp.name) / name
        shutil.copytree(self.result_dir, target)
        (target / "captures.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records),
            encoding="utf-8", newline="\n")
        return score.score_case(self.case_dir, target)

    def test_a_hold_on_a_cell_already_photographed_is_satisfied(self):
        """Issue #54: the route revisits directions, and declining is correct.

        Holds 0, 1 and 2 open at 0 and approximately 2000 and 4000 (a move's
        duration is rounded UP to the millisecond -- see trajectory.py's
        swing-twist fix -- so consecutive same-band moves can each land a
        sub-millisecond floating-point residual over the nominal 800 ms,
        drifting a hold's exact open time by a couple of ms without changing
        which window anything falls in) and their windows run to
        to_ms + 1500, so hold 0 reaches about 2700 and hold 1 about 4700. One
        `already-captured` at 700 satisfies hold 0 without a photograph; one
        `accepted` at 4000 is inside hold 1's window and hold 2's, and hold 1
        claims it; a `rejected` at 6000 inside hold 2's window satisfies
        nothing.
        """
        hold_starts = [h["from_ms"] for h in self.holds[:3]]
        self.assertEqual(hold_starts[0], 0)
        self.assertAlmostEqual(hold_starts[1], 2000, delta=5)
        self.assertAlmostEqual(hold_starts[2], 4000, delta=5)
        scores = self._with_captures("revisit", [
            {"at": 700, "outcome": "already-captured", "cell": 0},
            {"at": 4000, "outcome": "accepted", "cell": 1},
            {"at": 6000, "outcome": "rejected", "reason": "moving"},
        ])
        capture = scores["capture"]
        self.assertEqual(capture["holds_satisfied"], 2)
        self.assertEqual(capture["holds_captured"], 1)
        self.assertEqual(capture["holds_already_covered"], 1)
        self.assertEqual(capture["holds_with_capture"], 2)
        self.assertEqual(capture["accepted_frames"], 1)
        # Latency is measured to the satisfying record whatever its outcome:
        # 700 for hold 0 and 4000 - hold_starts[1] for hold 1 (~2000, not
        # exactly, per the note above).
        self.assertEqual(capture["latency_ms"]["max"], max(700, 4000 - hold_starts[1]))
        # Two of 48 holds ended in something; the rest did not.
        self.assertFalse(scores["gates"]["every_hold_captured"])

    def test_every_hold_satisfied_by_an_already_captured_cell_passes_the_gate(self):
        scores = self._with_captures("all-revisits", [
            {"at": int(hold["from_ms"]) + 700, "outcome": "already-captured",
             "cell": int(hold["index"])}
            for hold in self.holds
        ])
        capture = scores["capture"]
        self.assertEqual(capture["holds_satisfied"], len(self.holds))
        self.assertEqual(capture["holds_captured"], 0)
        self.assertEqual(capture["holds_already_covered"], len(self.holds))
        self.assertEqual(capture["accepted_frames"], 0)
        self.assertEqual(capture["latency_ms"]["max"], 700)
        self.assertTrue(scores["gates"]["every_hold_captured"])
        self.assertTrue(scores["gates"]["capture_p95_le_1500"])

    def test_one_wrong_overlay_frame_shows_in_max_and_over_gate(self):
        turned = pathlib.Path(self.tmp.name) / "one-turned"
        shutil.copytree(self.result_dir, turned)
        path = turned / "events.jsonl"
        events = [json.loads(line) for line in
                  path.read_text(encoding="utf-8").splitlines() if line]
        frames = {f["frame_id"]: f for f in
                  (json.loads(line) for line in
                   (self.case_dir / "truth" / "trajectory.jsonl")
                   .read_text(encoding="utf-8").splitlines() if line)}
        target = next(e for e in events
                      if frames[e["frame_id"]]["angular_rate_deg_s"] <= 2.0)
        frame = frames[target["frame_id"]]
        target["basis"]["forward"] = list(
            sky_vector(frame["az"], frame["alt"] + 3.0))
        path.write_text("".join(json.dumps(e) + "\n" for e in events),
                        encoding="utf-8", newline="\n")

        scores = score.score_case(self.case_dir, turned)
        overlay = scores["overlay"]
        self.assertEqual(overlay["samples"], self.frame_count)
        self.assertAlmostEqual(overlay["settled"]["max_deg"], 3.0, places=9)
        self.assertEqual(overlay["frames_over_gate"], 1)
        # One frame in a thousand cannot move a percentile, which is why the
        # percentiles alone could not see this.
        self.assertLess(overlay["settled"]["p95_deg"], 0.5)
        # Three degrees is under the maximum gate, so this result still
        # passes: `frames_over_gate` is informational, and the threshold the
        # gate does hold is 10 degrees, not one frame over its class's p95.
        self.assertTrue(scores["gates"]["overlay_max_lt_10"])
        self.assertTrue(scores["gates"]["pass"], scores["gates"])

    def test_a_wrong_right_and_up_show_in_the_overlay_though_forward_is_untouched(self):
        """Issue #59: a half-applied yaw (or a roll) leaves `forward` alone.

        `right` and `up` are rotated 5 degrees about the truth `forward`;
        `forward` itself is written back bit-identical to the truth. A
        forward-only metric would call this event a perfect match.

        Named mutation, verified by hand and reverted (not left in the tree):
        replacing `_score_overlay`'s `max(forward_error, right_error,
        up_error)` with `forward_error` alone reddens this test, because
        `forward` here is exactly the truth and that metric reports settled
        `max_deg` 0.0 instead of ~5.0 -- the failure is
        `0.0 != 5.0 within 7 places` on the `max_deg` assertion below, and
        `max_forward_deg`/`max_right_deg`/`max_up_deg` do not exist under
        that metric at all (AssertionError / KeyError). See task-13-report.md.
        """
        turned = pathlib.Path(self.tmp.name) / "wrong-right-up"
        shutil.copytree(self.result_dir, turned)
        path = turned / "events.jsonl"
        events = [json.loads(line) for line in
                  path.read_text(encoding="utf-8").splitlines() if line]
        frames = {f["frame_id"]: f for f in
                  (json.loads(line) for line in
                   (self.case_dir / "truth" / "trajectory.jsonl")
                   .read_text(encoding="utf-8").splitlines() if line)}
        target = next(e for e in events
                      if frames[e["frame_id"]]["angular_rate_deg_s"] <= 2.0)
        frame = frames[target["frame_id"]]
        forward = np.asarray(frame["forward"], dtype=np.float64)
        forward = forward / np.linalg.norm(forward)
        radians = math.radians(5.0)
        cos, sin = math.cos(radians), math.sin(radians)
        for key in ("right", "up"):
            v = np.asarray(frame[key], dtype=np.float64)
            rotated = v * cos + np.cross(forward, v) * sin
            target["basis"][key] = [float(x) for x in rotated]
        target["basis"]["forward"] = list(frame["forward"])
        path.write_text("".join(json.dumps(e) + "\n" for e in events),
                        encoding="utf-8", newline="\n")

        scores = score.score_case(self.case_dir, turned)
        overlay = scores["overlay"]
        self.assertEqual(overlay["samples"], self.frame_count)
        self.assertAlmostEqual(overlay["settled"]["max_forward_deg"], 0.0, places=7)
        self.assertAlmostEqual(overlay["settled"]["max_right_deg"], 5.0, delta=0.01)
        self.assertAlmostEqual(overlay["settled"]["max_up_deg"], 5.0, delta=0.01)
        self.assertAlmostEqual(overlay["settled"]["max_deg"], 5.0, delta=0.01)
        self.assertEqual(overlay["frames_over_gate"], 1)
        # One frame in a thousand cannot move a percentile (same as the
        # forward-only case above), and 5 degrees is under the 10 degree
        # maximum gate, so this result still passes.
        self.assertLess(overlay["settled"]["p95_deg"], 0.5)
        self.assertTrue(scores["gates"]["overlay_max_lt_10"])
        self.assertTrue(scores["gates"]["pass"], scores["gates"])

    def test_a_frame_delivered_twice_is_counted_once_and_named(self):
        """A repeat delivery is not a second sample and not a second chance.

        The first line for a frame id is the one scored; a later line with the
        same id is a duplicate, counted in `duplicate_frame_ids` and nowhere
        else. Otherwise a scanner could raise its own sample count by
        reprocessing the same frame, and a second line carrying a better basis
        for a frame it has already answered for would quietly overwrite the
        answer it gave.
        """
        twice = pathlib.Path(self.tmp.name) / "twice"
        shutil.copytree(self.result_dir, twice)
        path = twice / "events.jsonl"
        events = [json.loads(line) for line in
                  path.read_text(encoding="utf-8").splitlines() if line]
        repeat = json.loads(json.dumps(events[500]))
        # A different basis on the repeat, three degrees out: if the second
        # line were the one scored, the maximum would show it.
        frame = next(f for f in read_jsonl(self.case_dir / "truth" / "trajectory.jsonl")
                     if f["frame_id"] == repeat["frame_id"])
        repeat["basis"]["forward"] = list(sky_vector(frame["az"], frame["alt"] + 3.0))
        events.insert(501, repeat)
        path.write_text("".join(json.dumps(e) + "\n" for e in events),
                        encoding="utf-8", newline="\n")

        scores = score.score_case(self.case_dir, twice)
        overlay = scores["overlay"]
        self.assertEqual(overlay["duplicate_frame_ids"], 1)
        self.assertEqual(overlay["samples"], self.scores["overlay"]["samples"])
        self.assertEqual(overlay["missing_fraction"], 0.0)
        self.assertEqual(overlay["settled"]["max_deg"],
                         self.scores["overlay"]["settled"]["max_deg"])
        self.assertFalse(scores["gates"]["no_duplicate_frames"])
        self.assertFalse(scores["gates"]["pass"])

    def test_a_single_frame_ten_degrees_out_fails_the_maximum_gate(self):
        """The gate the percentiles cannot reach, pinned at its threshold."""
        turned = pathlib.Path(self.tmp.name) / "ten-out"
        shutil.copytree(self.result_dir, turned)
        path = turned / "events.jsonl"
        events = [json.loads(line) for line in
                  path.read_text(encoding="utf-8").splitlines() if line]
        frames = {f["frame_id"]: f for f
                  in read_jsonl(self.case_dir / "truth" / "trajectory.jsonl")}
        target = next(e for e in events
                      if frames[e["frame_id"]]["angular_rate_deg_s"] <= 2.0)
        frame = frames[target["frame_id"]]
        target["basis"]["forward"] = list(sky_vector(frame["az"], frame["alt"] + 10.5))
        path.write_text("".join(json.dumps(e) + "\n" for e in events),
                        encoding="utf-8", newline="\n")

        scores = score.score_case(self.case_dir, turned)
        self.assertAlmostEqual(scores["overlay"]["settled"]["max_deg"], 10.5, places=6)
        self.assertLess(scores["overlay"]["settled"]["p95_deg"], 0.5)
        self.assertFalse(scores["gates"]["overlay_max_lt_10"])
        self.assertFalse(scores["gates"]["pass"])

    def test_an_event_naming_an_undelivered_frame_counts_as_missing(self):
        stray = pathlib.Path(self.tmp.name) / "stray-frame"
        shutil.copytree(self.result_dir, stray)
        path = stray / "events.jsonl"
        text = path.read_text(encoding="utf-8")
        ghost = {"t_ms": 0, "frame_id": "f999999", "compass_ready": True,
                 "tilt_ready": True, "aim": None,
                 "basis": {"right": [1, 0, 0], "up": [0, 0, 1], "forward": [0, 1, 0]},
                 "frame_count": 0, "cue": "hold"}
        path.write_text(text + json.dumps(ghost) + "\n",
                        encoding="utf-8", newline="\n")
        scores = score.score_case(self.case_dir, stray)
        self.assertEqual(scores["overlay"]["samples"], self.frame_count)
        self.assertAlmostEqual(scores["overlay"]["missing_fraction"],
                               1 / (self.frame_count + 1))

    # -- a panorama that is not the contract's is empty --------------------

    def test_a_panorama_without_alpha_is_empty_not_complete(self):
        flat = pathlib.Path(self.tmp.name) / "no-alpha"
        shutil.copytree(self.result_dir, flat)
        with Image.open(self.result_dir / "panorama.png") as image:
            image.convert("RGB").save(flat / "panorama.png")
        scores = score.score_case(self.case_dir, flat)
        self.assertEqual(scores["panorama"]["mode"], "RGB")
        self.assertIn("no alpha", scores["panorama"]["note"])
        self.assertEqual(scores["coverage"]["panorama_alpha_fraction"], 0.0)
        self.assertEqual(scores["coverage"]["observable_fraction_covered"], 0.0)
        self.assertEqual(scores["landmarks"]["found"], 0)
        self.assertFalse(scores["gates"]["pass"])

    def test_a_panorama_of_the_wrong_size_is_empty_not_complete(self):
        small = pathlib.Path(self.tmp.name) / "wrong-size"
        shutil.copytree(self.result_dir, small)
        Image.fromarray(np.full((150, 540, 4), 255, dtype=np.uint8),
                        mode="RGBA").save(small / "panorama.png")
        scores = score.score_case(self.case_dir, small)
        self.assertEqual((scores["panorama"]["width"], scores["panorama"]["height"]),
                         (540, 150))
        self.assertIn("not 1080 x 300", scores["panorama"]["note"])
        self.assertEqual(scores["coverage"]["panorama_alpha_fraction"], 0.0)
        self.assertEqual(scores["landmarks"]["found"], 0)
        self.assertFalse(scores["gates"]["pass"])

    def test_a_missing_capture_log_is_scored_as_no_captures(self):
        bare = pathlib.Path(self.tmp.name) / "bare"
        shutil.copytree(self.result_dir, bare)
        (bare / "captures.jsonl").unlink()
        scores = score.score_case(self.case_dir, bare)
        self.assertEqual(scores["capture"]["holds"], len(self.holds))
        self.assertEqual(scores["capture"]["holds_with_capture"], 0)
        self.assertEqual(scores["capture"]["holds_satisfied"], 0)
        self.assertEqual(scores["capture"]["holds_captured"], 0)
        self.assertEqual(scores["capture"]["holds_already_covered"], 0)
        self.assertEqual(scores["capture"]["accepted_frames"], 0)
        self.assertFalse(scores["gates"]["every_hold_captured"])
        self.assertFalse(scores["gates"]["pass"])

    # -- a disc on a curved face ------------------------------------------

    def test_a_disc_on_a_sphere_is_clipped_in_two_dimensions(self):
        """A cylinder clips the expected area once; a sphere clips it twice.

        `sim.truth` paints a surface landmark only where the hit face's normal
        is within `acos(0.99)` of the declared one. On a cylinder that is a
        band `R sin(acos(0.99))` wide across one axis and the full disc along
        the axis of the cylinder, so the area is scaled by the width ratio. On
        a sphere the same limit applies in both directions at once, so the
        factor is that ratio squared. The chart yard has no sphere-hosted
        landmark, so the formula is pinned here directly rather than through a
        scene that does not exercise it.
        """
        band = math.sin(math.acos(score._NORMAL_DOT))
        host_radius, disc_radius = 2.5, 0.5
        ratio = host_radius * band / disc_radius
        self.assertLess(ratio, 1.0)  # otherwise there is nothing to clip

        c_ref = np.array([0.0, 0.75, 1.4])
        centre = np.array([4.6625, 6.3840, 6.1400])
        normal = centre - np.array([6.0, 8.0, 7.5])
        normal = normal / np.linalg.norm(normal)
        scene = {
            "objects": [
                {"id": "ball", "kind": "sphere", "centre": [6.0, 8.0, 7.5],
                 "radius": host_radius, "colour": [50, 90, 40]},
                {"id": "post", "kind": "cylinder", "base": [6.0, 8.0, 0.0],
                 "radius": host_radius, "height": 9.0, "colour": [80, 60, 40]},
            ],
            "landmarks": [],
            "surface_landmarks": [
                {"id": "ON-SPHERE", "palette": 0, "object": "ball",
                 "centre": list(centre), "normal": list(normal),
                 "radius_m": disc_radius},
                {"id": "ON-CYLINDER", "palette": 1, "object": "post",
                 "centre": list(centre), "normal": list(normal),
                 "radius_m": disc_radius},
            ],
        }
        areas = score._expected_disc_areas(scene, c_ref)
        sphere_area, _ = areas["ON-SPHERE"]
        cylinder_area, _ = areas["ON-CYLINDER"]

        offset = centre - c_ref
        distance = float(np.linalg.norm(offset))
        facing = abs(float((offset / distance) @ normal))
        unclipped = (math.pi * disc_radius ** 2 * facing / distance ** 2
                     * (180.0 / math.pi) ** 2)
        self.assertAlmostEqual(cylinder_area, unclipped * ratio, places=9)
        self.assertAlmostEqual(sphere_area, unclipped * ratio * ratio, places=9)
        # The discriminating claim: the sphere is clipped by the ratio again.
        self.assertAlmostEqual(sphere_area / cylinder_area, ratio, places=9)

    # -- a case the scorer cannot score ------------------------------------

    def test_a_case_without_obstacle_profiles_cannot_be_scored(self):
        """"I could not run" is exit 2, and it is not a traceback.

        A case built before the per-obstacle silhouette has no `profile` to
        score an obstacle against. That is a case that must be rebuilt, not a
        result that failed, and the two must not look alike on the way out.
        """
        root = pathlib.Path(self.tmp.name) / "no-profiles"
        stripped = root / self.case_dir.name
        shutil.copytree(self.case_dir, stripped,
                        ignore=shutil.ignore_patterns("input", "ideal"))
        path = stripped / "truth" / "reference-horizon.json"
        reference = read_json(path)
        for obstacle in reference["obstacles"]:
            obstacle.pop("profile")
        write_json(path, reference)
        # The copy carries this class's own scores.json; clear it so that the
        # assertion below is about what the failing run wrote, not about it.
        (stripped / "result" / "scores.json").unlink()

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                cli_main(["score", self.case_dir.name, "--cases", str(root)])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("profile", err.getvalue())
        self.assertIn("rebuild it with make-case", err.getvalue())
        self.assertFalse((stripped / "result" / "scores.json").exists())

    def test_a_blank_panorama_is_scored_as_empty(self):
        blank = pathlib.Path(self.tmp.name) / "blank"
        shutil.copytree(self.result_dir, blank)
        empty = np.zeros((300, 1080, 4), dtype=np.uint8)
        Image.fromarray(empty, mode="RGBA").save(blank / "panorama.png")
        scores = score.score_case(self.case_dir, blank)
        self.assertEqual(scores["landmarks"]["found"], 0)
        self.assertEqual(scores["landmarks"]["spurious"], 0)
        self.assertEqual(len(scores["landmarks"]["omitted"]),
                         scores["landmarks"]["expected"])
        self.assertEqual(scores["coverage"]["panorama_alpha_fraction"], 0.0)
        self.assertEqual(scores["coverage"]["observable_fraction_covered"], 0.0)
        self.assertFalse(scores["gates"]["no_omissions"])
        self.assertFalse(scores["gates"]["coverage_ge_0_95"])
        self.assertFalse(scores["gates"]["pass"])


class ResolvableWidth(unittest.TestCase):
    """Issue #53: an obstacle narrower than one product bin grades the
    fixture, not the scanner -- and "narrower" means the obstacle's own
    visible silhouette, never its declared ``min_width_deg`` label.

    Fix round 1 keyed ``resolvable`` on the declared width, which was wrong:
    on the real chart yard, roof-south and wall-east both declare 10 while
    spanning 146 and 84 degrees, so gating on the label would have made two
    wide, genuinely-scoreable obstacles invisible to the gate. ``_score_obstacles``
    is called directly here with a synthetic obstacle, rather than through the
    expensive ``IdealResult`` case build, because the claim under test needs
    nothing from a real scene.
    """

    TRUTH_BINS = 3600
    STEP = 360.0 / TRUTH_BINS  # 0.1 degree, CONTRACT.md's HORIZON_CELL_DEG
    PRODUCT_BINS = 30  # PhotosphereSweep's own default, 12 degrees per bin

    @classmethod
    def _obstacle(cls, width_deg, min_width_deg, notch_width_deg=None, deficit=5.0):
        """A ``width_deg``-wide, fully visible, fully resolved obstacle.

        Under-reported by ``deficit`` degrees everywhere (``notch_width_deg``
        omitted), or over a ``notch_width_deg``-wide stretch at the start of
        it and matched exactly everywhere else -- the shape a correct
        boundary with one notch cut out of it has, the same shape
        ``test_a_narrow_notch_in_the_roof_is_missed_by_width_alone`` exercises
        on the real chart yard's roof.
        """
        width_bins = round(width_deg / cls.STEP)
        start = 100
        profile = np.full(cls.TRUTH_BINS, -10.0)
        profile[start:start + width_bins] = 30.0
        alt = profile.copy()
        under = slice(start, start + width_bins) if notch_width_deg is None \
            else slice(start, start + round(notch_width_deg / cls.STEP))
        alt[under] = 30.0 - deficit
        resolved = np.ones(cls.TRUTH_BINS, dtype=bool)
        reference = {"obstacles": [{"id": "synthetic", "alt_max": 30.0,
                                    "min_width_deg": min_width_deg,
                                    "profile": profile.tolist()}]}
        scored = score._score_obstacles(reference, cls.TRUTH_BINS, cls.STEP,
                                        alt, resolved, cls.PRODUCT_BINS)
        return scored[0]

    def test_a_narrow_obstacle_is_not_resolvable_or_missed(self):
        """2 degrees of actual silhouette, one product bin is 12: below the
        floor, whatever the scene happens to declare its width as.

        The obstacle IS under-reported by 5 degrees everywhere it is visible
        (a real deficit, informational), but the product could never have
        told this 2 degree obstacle apart from its surroundings at 12 degree
        resolution, so grading it "missed" would be grading a fixture the
        product cannot represent. This is the case the median term alone
        would get wrong: 5 degrees is well past MISSED_OBSTRUCTION_DEG, so
        without the resolvable gate suppressing the whole verdict (not just
        the width term) this row would still come out missed.
        """
        entry = self._obstacle(2.0, min_width_deg=2.0)
        self.assertEqual(entry["visible_width_deg"], 2.0)
        self.assertEqual(entry["deficit_median"], 5.0)  # the deficit is real...
        self.assertFalse(entry["resolvable"])
        self.assertEqual(entry["resolvable_width_deg"], 12.0)
        self.assertFalse(entry["missed"])  # ...but uninterpretable at this resolution

    def test_a_wide_obstacle_with_a_bin_wide_notch_is_missed(self):
        """25 degrees of silhouette, a 12 degree stretch under by 5: IS a
        miss with 30 product bins -- the real wall-east/roof-south shape.

        25 degrees is well above the 12 degree floor, so this row is
        resolvable and graded normally. The notch is only 120 of 250 visible
        bins, so the MEDIAN deficit is 0.0 (the majority is matched exactly)
        and cannot see it; the WIDTH term catches it instead, at exactly its
        own threshold (``width_missed_deg`` 12.0 >= ``resolvable_width_deg``
        12.0, the floor, since the declared 10 is smaller than one bin).
        """
        entry = self._obstacle(25.0, min_width_deg=10.0, notch_width_deg=12.0)
        self.assertEqual(entry["visible_width_deg"], 25.0)
        self.assertTrue(entry["resolvable"])
        self.assertEqual(entry["resolvable_width_deg"], 12.0)
        self.assertAlmostEqual(entry["deficit_median"], 0.0, places=9)
        self.assertAlmostEqual(entry["width_missed_deg"], 12.0, places=9)
        # The notch is one stretch, so issue #64's two figures agree; the
        # `ContiguousWidth` class below is where they come apart.
        self.assertAlmostEqual(entry["width_missed_total_deg"], 12.0, places=9)
        self.assertTrue(entry["missed"])

    def test_a_small_declared_width_does_not_make_a_wide_obstacle_unresolvable(self):
        """The fix round 1 bug, pinned directly: declared width must not
        decide resolvability, only the obstacle's own visible extent does.

        Same 25 degree, 12 degree notch obstacle as above, only the scene's
        declared label changes (10 -> 2): resolvability and the miss verdict
        are unchanged, because both are read from ``visible_width_deg``, not
        from ``min_width_deg``. ``resolvable_width_deg`` still floors at one
        product bin (12), so the verdict itself does not move either.
        """
        entry = self._obstacle(25.0, min_width_deg=2.0, notch_width_deg=12.0)
        self.assertTrue(entry["resolvable"])
        self.assertEqual(entry["resolvable_width_deg"], 12.0)
        self.assertTrue(entry["missed"])

    def test_a_wide_declared_width_still_raises_the_threshold_for_a_resolvable_obstacle(self):
        """``resolvable_width_deg`` is still ``max(declared, bin width)``.

        Declared 20 exceeds the 12 degree bin floor, so a resolvable
        obstacle's own 12 degree notch (width_missed_deg 12.0) no longer
        reaches the threshold (20), and the median is 0.0, so this one is NOT
        missed -- the declared width, when it is the wider of the two, still
        protects a resolvable obstacle exactly as it did before #53.
        """
        entry = self._obstacle(25.0, min_width_deg=20.0, notch_width_deg=12.0)
        self.assertTrue(entry["resolvable"])
        self.assertEqual(entry["resolvable_width_deg"], 20.0)
        self.assertAlmostEqual(entry["width_missed_deg"], 12.0, places=9)
        self.assertFalse(entry["missed"])

    def test_a_resolvable_obstacle_with_no_declared_width_is_still_missed(self):
        """Review round 2, Major 1: the width term must not require a
        declared ``min_width_deg`` at all.

        ``resolvable_width_deg`` is already well-defined with nothing
        declared -- it falls back to the bin floor (12) -- so a resolvable
        obstacle (25 degrees visible) with NO declared width and a 12 degree
        stretch under-reported by 5 degrees must still be caught by the
        width term, exactly as one that does declare a width. Mutation: a
        `has_min_width` guard on the width term (requiring a positive
        declared value before it can fire at all) reddens this, since here
        there is none.
        """
        entry = self._obstacle(25.0, min_width_deg=None, notch_width_deg=12.0)
        self.assertTrue(entry["resolvable"])
        self.assertEqual(entry["resolvable_width_deg"], 12.0)
        self.assertAlmostEqual(entry["width_missed_deg"], 12.0, places=9)
        self.assertTrue(entry["missed"])

    # Mutation check (verified by hand, not pinned as a source-level test):
    # replacing `resolvable = visible_width_deg >= bin_width_deg` with
    # `resolvable = False` reddens test_a_narrow_obstacle_is_not_resolvable_or_missed's
    # sibling (nothing would be gradable) and both of the wide-obstacle tests
    # above (a real 12 degree miss would go unreported); replacing it with
    # `resolvable = True` reddens test_a_narrow_obstacle_is_not_resolvable_or_missed
    # itself (a 2 degree fixture would be graded as if the product could see
    # it). Both directions were run against this class and reverted; see
    # task-10-report.md, "Fix round 1".


class ContiguousWidth(unittest.TestCase):
    """Issue #64: what counts as ONE under-reported stretch.

    ``_score_obstacles`` is called directly with a synthetic obstacle, as
    ``ResolvableWidth`` does: the claims here are about the azimuth axis
    itself -- that it wraps, and that a stretch is broken by a bin carrying
    no measurement -- and neither needs a rendered scene.
    """

    TRUTH_BINS = 3600
    STEP = 360.0 / TRUTH_BINS
    PRODUCT_BINS = 3600  # 0.1 degree bins, so the declared width sets the floor

    def _score(self, profile, alt, resolved, min_width_deg=10.0):
        reference = {"obstacles": [{"id": "synthetic", "alt_max": 30.0,
                                    "min_width_deg": min_width_deg,
                                    "profile": profile.tolist()}]}
        return score._score_obstacles(reference, self.TRUTH_BINS, self.STEP,
                                      alt, resolved, self.PRODUCT_BINS)[0]

    def _straddling_north(self, missing_bins):
        """An obstacle over azimuth 0, under-reported across the wrap.

        Visible over the last 300 bins and the first 300 (60 degrees in all),
        with ``missing_bins`` of it lost, half on each side of north.
        """
        profile = np.full(self.TRUTH_BINS, -10.0)
        profile[-300:] = 30.0
        profile[:300] = 30.0
        alt = profile.copy()
        half = missing_bins // 2
        alt[-half:] = 0.0
        alt[:half] = 0.0
        return profile, alt, np.ones(self.TRUTH_BINS, dtype=bool)

    def test_a_stretch_across_north_is_one_stretch(self):
        """120 bins over the wrap are 12 degrees, not two runs of 6.

        Named mutation: drop the rotation in ``_longest_run_deg`` and scan
        the array linearly. The run comes back 6.0, under the declared 10,
        and the obstacle reads found although 12 contiguous degrees of it are
        gone -- a verdict decided by where azimuth 0 happens to fall in the
        array. Run and reverted; see task-2-report.md.
        """
        entry = self._score(*self._straddling_north(120))
        self.assertEqual(entry["visible_width_deg"], 60.0)
        self.assertAlmostEqual(entry["width_missed_total_deg"], 12.0, places=9)
        self.assertAlmostEqual(entry["width_missed_deg"], 12.0, places=9)
        self.assertTrue(entry["missed"])

    def test_the_wrap_is_not_a_licence_to_join_two_far_apart_stretches(self):
        """The same obstacle, the same total, the two halves NOT adjacent.

        60 bins at each far END of the silhouette rather than at the wrap:
        the total is the same 12.0 degrees and the longest run is 6.0, so
        this one is found. Without this case a `_longest_run_deg` that simply
        returned the total would pass the wrap test above.
        """
        profile = np.full(self.TRUTH_BINS, -10.0)
        profile[-300:] = 30.0
        profile[:300] = 30.0
        alt = profile.copy()
        alt[-300:-240] = 0.0
        alt[240:300] = 0.0
        entry = self._score(profile, alt, np.ones(self.TRUTH_BINS, dtype=bool))
        self.assertAlmostEqual(entry["width_missed_total_deg"], 12.0, places=9)
        self.assertAlmostEqual(entry["width_missed_deg"], 6.0, places=9)
        self.assertFalse(entry["missed"])

    def test_an_unresolved_bin_breaks_a_run(self):
        """A bin with no measurement is not evidence of a miss.

        120 contiguous bins are under-reported and the one in the middle is
        unresolved, so the widest MEASURED stretch is 59 bins, not 120. A run
        that stepped over the gap would claim 12 degrees on the strength of a
        bin nothing measured, which is the wrong direction for a term that
        fails a case.
        """
        profile = np.full(self.TRUTH_BINS, -10.0)
        profile[100:700] = 30.0
        alt = profile.copy()
        alt[200:320] = 0.0
        resolved = np.ones(self.TRUTH_BINS, dtype=bool)
        resolved[260] = False
        entry = self._score(profile, alt, resolved)
        self.assertAlmostEqual(entry["width_missed_total_deg"], 11.9, places=9)
        self.assertAlmostEqual(entry["width_missed_deg"], 6.0, places=9)
        self.assertFalse(entry["missed"])
        # And with that one bin resolved the same obstacle IS missed, so the
        # case above is about the gap and not about the 120 bins.
        whole = self._score(profile, alt, np.ones(self.TRUTH_BINS, dtype=bool))
        self.assertAlmostEqual(whole["width_missed_deg"], 12.0, places=9)
        self.assertTrue(whole["missed"])


class ObservableRegion(unittest.TestCase):
    """``score._observable_mask`` on a route that does NOT see the whole sky.

    On all three shipped cases the mask is True for every cell of the raster
    (the routes sweep the dome), so `observable_fraction_covered` and
    `panorama_alpha_fraction` agree by construction there and nothing in those
    numbers can fail on the mask being wrong. A mask stuck at True would score
    exactly the same on them as the real one. So the mask is graded here
    instead, on a trajectory that looks in one direction only, where True
    everywhere is a visibly wrong answer.
    """

    HEIGHT, WIDTH = 300, 1080

    @classmethod
    def setUpClass(cls):
        camera = Camera(480, 640, 60.0)
        cls.camera = {"width": camera.width, "height": camera.height,
                      "fx": camera.fx, "fy": camera.fy,
                      "cx": camera.cx, "cy": camera.cy}
        # Four frames aimed north, a degree apart, at altitude 20: the phone
        # held still on one bearing. The short axis is 480 px wide, so the
        # frustum is 60 degrees across and 2 * atan(320 / fy) = 75.2 degrees
        # tall, and the union of these four covers barely a fifteenth of the
        # sphere.
        cls.frames = []
        for index, az in enumerate((358.5, 359.5, 0.5, 1.5)):
            basis = look_basis(az, 20.0)
            cls.frames.append({
                "frame_id": f"f{index:06d}",
                "right": [float(v) for v in basis.right],
                "up": [float(v) for v in basis.up],
                "forward": [float(v) for v in basis.forward],
            })
        cls.mask = score._observable_mask((cls.HEIGHT, cls.WIDTH, 4),
                                          cls.camera, cls.frames)

    def at(self, az, alt):
        """The mask cell holding direction ``(az, alt)``, in the raster mapping."""
        column = int(round(az / 360.0 * self.WIDTH - 0.5)) % self.WIDTH
        row = int(round((score.PANORAMA_ALT_TOP - alt) / score.PANORAMA_ALT_SPAN
                        * (self.HEIGHT - 1)))
        self.assertTrue(0 <= row < self.HEIGHT, f"altitude {alt} is off the raster")
        return bool(self.mask[row, column])

    def test_the_direction_the_camera_points_is_observable(self):
        self.assertTrue(self.at(0.0, 20.0))

    def test_the_opposite_direction_is_not_observable(self):
        # Behind the lens. A mask that answers True here is answering True
        # everywhere, which is the failure this class exists for.
        self.assertFalse(self.at(180.0, 20.0))

    def test_directions_well_outside_the_frustum_are_not_observable(self):
        # Beyond the 30 degree half-width in azimuth, beyond the 37.6 degree
        # half-height in altitude, and overhead.
        for az, alt in ((90.0, 20.0), (270.0, 20.0), (120.0, 20.0),
                        (0.0, 62.0), (0.0, 85.0), (180.0, 85.0)):
            with self.subTest(az=az, alt=alt):
                self.assertFalse(self.at(az, alt))

    def test_the_frustum_reaches_below_the_bottom_of_the_raster(self):
        # Altitude 20 less the 37.6 degree half-height is -17.6, which is past
        # the raster's lowest row at -10, so the bottom row on this bearing is
        # inside the frustum. Without this the class could pass on a mask that
        # only ever read azimuth.
        self.assertTrue(self.at(0.0, -10.0))

    def test_the_mask_is_a_small_part_of_the_sphere(self):
        # 60 x 75.2 degrees out of the whole sky, four nearly identical poses:
        # anything approaching the whole raster means the test above passed on
        # an accident. Bounds rather than a pinned number, because the count
        # is a raster quantisation of a frustum.
        fraction = float(self.mask.mean())
        self.assertGreater(fraction, 0.02)
        self.assertLess(fraction, 0.20)

    def test_no_frames_means_nothing_was_observable(self):
        empty = score._observable_mask((self.HEIGHT, self.WIDTH, 4), self.camera, [])
        self.assertFalse(empty.any())


if __name__ == "__main__":
    unittest.main()
