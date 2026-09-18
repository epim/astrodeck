"""``score.score_case`` against the ideal result ``ideal.make_ideal_result`` builds.

The scorer is developed against an output that is right by construction, so
every number it reports here is quantisation and nothing else. One class
builds the case and the ideal result once; the variants (a coarse horizon, a
rotated horizon, a missing capture log, a blank panorama) are cheap copies of
that one result, so the expensive truth is computed a single time.
"""
import json
import pathlib
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

from sim import cases, ideal, score
from sim.geometry import sky_vector

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


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

    def test_every_hold_is_captured_700_ms_in(self):
        capture = self.scores["capture"]
        self.assertEqual(capture["holds"], len(self.holds))
        self.assertEqual(capture["holds_with_capture"], capture["holds"])
        self.assertEqual(capture["latency_ms"]["max"], 700)

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
        shared = pathlib.Path(self.tmp.name) / "one-capture"
        shutil.copytree(self.result_dir, shared)
        (shared / "captures.jsonl").write_text(
            json.dumps({"at": 2000, "outcome": "accepted", "cell": 0}) + "\n",
            encoding="utf-8", newline="\n")
        scores = score.score_case(self.case_dir, shared)
        self.assertEqual(scores["capture"]["accepted_frames"], 1)
        self.assertEqual(scores["capture"]["holds_with_capture"], 1)
        self.assertEqual(scores["capture"]["latency_ms"]["max"], 2000)

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
        self.assertEqual(overlay["samples"], 1015)
        self.assertAlmostEqual(overlay["settled"]["max_deg"], 3.0, places=9)
        self.assertEqual(overlay["frames_over_gate"], 1)
        # One frame in a thousand cannot move a percentile, which is why the
        # percentiles alone could not see this.
        self.assertLess(overlay["settled"]["p95_deg"], 0.5)

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
        self.assertEqual(scores["overlay"]["samples"], 1015)
        self.assertAlmostEqual(scores["overlay"]["missing_fraction"], 1 / 1016)

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
        self.assertEqual(scores["capture"]["accepted_frames"], 0)
        self.assertFalse(scores["gates"]["every_hold_captured"])
        self.assertFalse(scores["gates"]["pass"])

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


if __name__ == "__main__":
    unittest.main()
