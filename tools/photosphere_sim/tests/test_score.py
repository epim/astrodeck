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

    def test_per_landmark_covers_all_52_and_expected_counts_the_observable(self):
        per_landmark = self.scores["landmarks"]["per_landmark"]
        self.assertEqual(len(per_landmark), 52)
        observable = sum(1 for lm in self.truth_landmarks if lm["observable"])
        self.assertEqual(self.scores["landmarks"]["expected"], observable)
        self.assertEqual(self.scores["landmarks"]["found"], observable)

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
                     "no_omissions", "no_duplicates"):
            self.assertTrue(gates[name], name)

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
