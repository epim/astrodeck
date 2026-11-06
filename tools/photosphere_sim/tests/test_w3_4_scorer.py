# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 3.4 (#1117, part b): per-gate rulings (#1059) and the overlay after the ring's own scale (#1060).

Rulings. A case file may rule on a gate: pass it at a stated limit, bound the number of bins it may leave unresolved, or
declare that it still fails and name the issue that owns the failure. Every ruling carries its reason, a ruling on a gate
the case does not have is refused, and a known failure that has started to pass is reported as stale and fails the case,
so a ruling is a claim the scorer keeps (nothing else would notice it had gone out of date).

The overlay. A live pose before the ring closes carries the chain's scale error, which nothing before the closure can
observe (measured: applying the gyro scale to the predictor, even the exact one, does not remove it, because the focal
lock absorbs part of it). The closure measures it: `loop.pre_deg` is the yaw the closing keyframe had to be turned. The
gate grades the frames before the closing keyframe with that residual spread over the turn, and the frames after it as
they are. An error the ring did not measure is not excused.

Mutants this file must catch (each applied from a byte backup of `sim/score.py`):

  * the ring correction not applied (`_ring_scaled_events` answering None): `OverlayAfterTheRing.test_a_ramp_the_ring_measured_passes`
    fails on its first assertion;
  * a known failure that passes treated as a pass (`ruled = not standard` replaced by `ruled = True`):
    `Rulings.test_a_known_failure_that_now_passes_is_stale` fails;
  * the tau bound of a limit ruling dropped: `Rulings.test_a_limit_ruling_bounds_tau` fails.
"""
import copy
import json
import math
import pathlib
import unittest

import numpy as np

from sim import report, score
from tests import test_score_v2 as sp

ROOT = pathlib.Path(__file__).resolve().parents[1]


def horizon(p95=2.29, p99=4.28, tau=1.5, unresolved=26):
    """The fields of a version 2 horizon block that the rulings read."""
    return {"empty_measured": False, "visible_bins": 720, "unresolved_bins": unresolved, "tau": tau,
            "signed_error_deg": {"median": 1.3, "p95": p95, "p99": p99, "max": 17.4}}


def standard_gates(h):
    """The standard verdicts of the three treeline gates for a horizon block."""
    errors = h["signed_error_deg"]
    return {"horizon_p95_lt_1": errors["p95"] is not None and errors["p95"] < 1.0,
            "measured_is_honest": errors["p99"] is not None and errors["p99"] < 2.0,
            "no_unresolved_boundary": h["unresolved_bins"] == 0}


TREELINE = {
    "horizon_p95_lt_1": {"limit_deg": 2.5, "tau_max_deg": 1.5, "why": "the 180 vertex cap forces tau to 1.5"},
    "no_unresolved_boundary": {"max_bins": 36, "why": "isolated closed bins, under 5 percent"},
    "measured_is_honest": {"known_failure": True, "issue": 1058, "why": "false-open Measured bins"},
}


class Rulings(unittest.TestCase):
    """#1059: a ruling row per gate."""

    def apply(self, h, rulings=None):
        return score._apply_rulings(standard_gates(h), h, TREELINE if rulings is None else rulings)

    def test_the_treeline_numbers_pass_under_their_rulings_and_fail_without(self):
        """The measured treeline horizon (p95 2.29, p99 4.28, tau 1.5, 26 unresolved bins) fails all three gates as
        they stand, and passes every one as ruled.

        MUTATION: `_apply_rulings` returning the gates it was given: the ruled verdicts below are False.
        """
        h = horizon()
        self.assertEqual(set(standard_gates(h).values()), {False})
        gates, ruled = self.apply(h)
        self.assertEqual(gates, {"horizon_p95_lt_1": True, "measured_is_honest": True, "no_unresolved_boundary": True})
        self.assertEqual(ruled["horizon_p95_lt_1"]["standard_gate"], False)
        self.assertEqual(ruled["horizon_p95_lt_1"]["observed"], 2.29)
        self.assertEqual(ruled["horizon_p95_lt_1"]["limit"], 2.5)
        self.assertEqual(ruled["no_unresolved_boundary"]["observed"], 26)
        self.assertEqual(ruled["measured_is_honest"]["issue"], 1058)

    def test_a_limit_ruling_holds_its_limit(self):
        """p95 2.6 is over the ruled 2.5. MUTATION: the limit compared with `<=` 10: this passes."""
        gates, _ = self.apply(horizon(p95=2.6))
        self.assertFalse(gates["horizon_p95_lt_1"])

    def test_a_limit_ruling_bounds_tau(self):
        """A coarser line (tau 2) is not excused by a limit that was ruled for tau 1.5, even with a good p95."""
        gates, ruled = self.apply(horizon(p95=2.2, tau=2.0))
        self.assertFalse(gates["horizon_p95_lt_1"])
        self.assertEqual(ruled["horizon_p95_lt_1"]["tau"], 2.0)

    def test_the_unresolved_cap_holds(self):
        gates, _ = self.apply(horizon(unresolved=37))
        self.assertFalse(gates["no_unresolved_boundary"])
        gates, _ = self.apply(horizon(unresolved=36))
        self.assertTrue(gates["no_unresolved_boundary"])

    def test_a_known_failure_that_still_fails_passes_the_case(self):
        gates, ruled = self.apply(horizon(p99=4.28))
        self.assertTrue(gates["measured_is_honest"])
        self.assertNotIn("stale", ruled["measured_is_honest"])

    def test_a_known_failure_that_now_passes_is_stale(self):
        """The failure the ruling named has been fixed: the ruling is out of date and the case fails until it is removed."""
        gates, ruled = self.apply(horizon(p99=1.2))
        self.assertFalse(gates["measured_is_honest"])
        self.assertTrue(ruled["measured_is_honest"]["stale"])
        self.assertTrue(ruled["measured_is_honest"]["standard_gate"])

    def test_an_empty_measured_set_does_not_pass_a_limit(self):
        """No Measured bin over 36 or more visible bins fails the percentile gates, ruled or not."""
        h = horizon(p95=0.0)
        h["empty_measured"] = True
        h["signed_error_deg"] = {"median": None, "p95": None, "p99": None, "max": None}
        gates, _ = self.apply(h, {"horizon_p95_lt_1": TREELINE["horizon_p95_lt_1"]})
        self.assertFalse(gates["horizon_p95_lt_1"])

    def test_a_case_without_rulings_is_untouched(self):
        h = horizon()
        gates, ruled = score._apply_rulings(standard_gates(h), h, None)
        self.assertEqual(gates, standard_gates(h))
        self.assertEqual(ruled, {})

    def test_a_malformed_ruling_is_refused(self):
        h = horizon()
        bad = {
            "a gate the case does not have": {"no_such_gate": {"known_failure": True, "issue": 1, "why": "x"}},
            "no reason": {"horizon_p95_lt_1": {"limit_deg": 2.5, "tau_max_deg": 1.5}},
            "an empty reason": {"horizon_p95_lt_1": {"limit_deg": 2.5, "tau_max_deg": 1.5, "why": " "}},
            "two kinds": {"horizon_p95_lt_1": {"limit_deg": 2.5, "max_bins": 3, "why": "x"}},
            "no kind": {"horizon_p95_lt_1": {"why": "x"}},
            "a limit on a gate with no percentile": {"no_unresolved_boundary": {"limit_deg": 2.5, "why": "x"}},
            "a bin cap on a percentile gate": {"horizon_p95_lt_1": {"max_bins": 3, "why": "x"}},
            "a known failure with no issue": {"horizon_p95_lt_1": {"known_failure": True, "why": "x"}},
            "a known failure of false": {"horizon_p95_lt_1": {"known_failure": False, "issue": 1, "why": "x"}},
        }
        for name, rulings in bad.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    score._apply_rulings(standard_gates(h), h, rulings)

    def test_the_treeline_case_file_rules_each_failing_gate_with_a_reason(self):
        """#1059: the three gates that failed with no owner each have a row, with a reason and no site data."""
        case = json.loads((ROOT / "cases" / "treeline-pan1-41.json").read_text(encoding="utf-8"))
        rulings = case["rulings"]
        self.assertEqual(set(rulings), {"horizon_p95_lt_1", "measured_is_honest", "no_unresolved_boundary"})
        for name, row in rulings.items():
            self.assertTrue(row["why"].strip(), name)
        self.assertEqual(rulings["measured_is_honest"]["issue"], 1058)
        self.assertEqual(rulings["horizon_p95_lt_1"]["limit_deg"], 1.0 + rulings["horizon_p95_lt_1"]["tau_max_deg"])

    def test_the_scorer_applies_a_case_file_ruling_end_to_end(self):
        """Through `score_case`: a manifest ruling shows in `scores.json`, and a stale known failure fails the case."""
        main = sp.shared()["main"]
        case = sp.copy_case(main["case"], "w34-ruled-case")
        manifest = sp.read_json(case / "manifest.json")
        manifest["rulings"] = {"horizon_p95_lt_1": {"known_failure": True, "issue": 1, "why": "test"}}
        sp.write_json(case / "manifest.json", manifest)
        scores = score.score_case(case, main["ideal"])
        self.assertFalse(scores["gates"]["horizon_p95_lt_1"])
        self.assertFalse(scores["gates"]["pass"])
        self.assertTrue(scores["rulings"]["horizon_p95_lt_1"]["stale"])
        manifest["rulings"] = {"horizon_p95_lt_1": {"limit_deg": 0.5, "tau_max_deg": 0.5, "why": "test"}}
        sp.write_json(case / "manifest.json", manifest)
        scores = score.score_case(case, main["ideal"])
        self.assertTrue(scores["gates"]["horizon_p95_lt_1"])
        self.assertEqual(scores["rulings"]["horizon_p95_lt_1"]["limit"], 0.5)
        plain = score.score_case(main["case"], main["ideal"])
        self.assertNotIn("rulings", plain)

    def test_a_legacy_scanners_version_1_horizon_is_graded_by_the_standard_gates(self):
        """Rulings are about the version 2 boundary: a legacy scanner replayed on a ruled case is scored, not refused.

        MUTATION: rulings applied whatever the horizon's version: the legacy result raises KeyError on `visible_bins`.
        """
        main = sp.shared()["main"]
        case = sp.copy_case(main["case"], "w34-ruled-legacy")
        manifest = sp.read_json(case / "manifest.json")
        manifest["rulings"] = {"measured_is_honest": {"known_failure": True, "issue": 1, "why": "test"}}
        sp.write_json(case / "manifest.json", manifest)
        result = sp.copy_result(main["ideal"], "w34-legacy-result")
        sp.write_json(result / "horizon.json", {"bins": 30, "points": [{"az": 6.0 + 12 * i, "alt": 5.0} for i in range(30)]})
        scores = score.score_case(case, result)
        self.assertEqual(scores["horizon"].get("version"), None)
        self.assertNotIn("rulings", scores)


class RulingsInTheReport(unittest.TestCase):
    """A ruled PASS is visibly ruled in `report.html`.

    MUTATION: `render` not writing the rulings table: both tests below fail.
    """

    def ruled_scores(self):
        main = sp.shared()["main"]
        case = sp.copy_case(main["case"], "w34-report-case")
        manifest = sp.read_json(case / "manifest.json")
        manifest["rulings"] = {"horizon_p95_lt_1": {"limit_deg": 0.5, "tau_max_deg": 0.5, "why": "a reason the page must show"},
                               "measured_is_honest": {"known_failure": True, "issue": 1058, "why": "a defect with an owner"}}
        sp.write_json(case / "manifest.json", manifest)
        return case, score.score_case(case, main["ideal"])

    def test_the_page_lists_each_ruling_with_what_the_standard_rule_said(self):
        case, scores = self.ruled_scores()
        out = sp.shared()["base"] / "w34-ruled-report.html"
        report.render(case, scores, out, result_dir=sp.shared()["main"]["ideal"])
        html = out.read_text(encoding="utf-8")
        self.assertIn("<h2>Rulings</h2>", html)
        self.assertIn("a reason the page must show", html)
        self.assertIn("a defect with an owner", html)
        self.assertIn("#1058", html)
        self.assertIn("STALE", html)

    def test_a_case_without_rulings_has_no_rulings_section(self):
        main = sp.shared()["main"]
        out = sp.shared()["base"] / "w34-plain-report.html"
        report.render(main["case"], main["scores"], out, result_dir=main["ideal"])
        self.assertNotIn("Rulings", out.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# #1060: the overlay after the ring's own scale
# --------------------------------------------------------------------------

RESIDUAL_DEG = 3.0


def turned_headings(events):
    """The unwrapped heading turned since the first event, from each event's own forward vector."""
    out, previous, total = [], None, 0.0
    for event in events:
        heading = score._heading_deg(event["basis"]["forward"])
        if previous is not None:
            total += (heading - previous + 180.0) % 360.0 - 180.0
        previous = heading
        out.append(total)
    return out


def ramped_result(name, residual=RESIDUAL_DEG, method="image", closed=True, pre=None):
    """The ideal result of the main case with a live yaw error that grows from 0 to `residual` over the turn.

    The error is the shape a chain with a scale error draws before its ring closes; it is applied to every event up to and
    including the closing keyframe's, and the diagnostics carry `pre` (default: the same residual) as the closure's own
    measurement. Frames after the closing keyframe keep the truth pose.
    """
    main = sp.shared()["main"]
    result = sp.copy_result(main["ideal"], name)
    events = [json.loads(line) for line in (result / "events.jsonl").read_text().splitlines()]
    diagnostics = sp.read_json(result / "diagnostics.json")
    late = diagnostics["loop"]["match"]["late_kf"]
    closing = next(k["frame_id"] for k in diagnostics["keyframes"] if k["id"] == late)
    turned = turned_headings(events)
    at = next(i for i, e in enumerate(events) if e["frame_id"] == closing)
    for i, event in enumerate(events[:at + 1]):
        error = residual * turned[i] / turned[at]
        event["basis"] = {k: score.rotate_about_up(v, error) for k, v in event["basis"].items()}
    (result / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    diagnostics["loop"]["pre_deg"] = [0.0, 0.0, residual if pre is None else pre]
    diagnostics["loop"]["method"] = method
    diagnostics["loop"]["closed"] = closed
    sp.write_json(result / "diagnostics.json", diagnostics)
    return result


class OverlayAfterTheRing(unittest.TestCase):
    """#1060: overlay_moving is graded after the scale the ring measured."""

    def test_a_ramp_the_ring_measured_passes(self):
        """A 3 degree yaw ramp over the turn is over the gate raw (p95 above 1) and inside it once the closure's own 3
        degree residual is spread back over the turn: the reported raw figure stays, the gate reads the corrected one.

        MUTATION: `_ring_scaled_events` answering None (no correction): the gate below is False.
        """
        main = sp.shared()["main"]
        scores = score.score_case(main["case"], ramped_result("w34-ramp"))
        overlay = scores["overlay"]
        self.assertGreater(overlay["moving"]["p95_deg"], 1.0)
        after = overlay["after_ring_scale"]
        self.assertLess(after["moving"]["p95_deg"], 0.05)
        self.assertAlmostEqual(after["residual_deg"], RESIDUAL_DEG, places=9)
        self.assertGreater(abs(after["turned_deg"]), 300.0)
        self.assertTrue(scores["gates"]["overlay_moving_p95_lt_1"])

    def test_an_error_the_ring_did_not_measure_is_not_excused(self):
        """The same ramp with a closure that measured 0 degrees: the gate fails."""
        main = sp.shared()["main"]
        scores = score.score_case(main["case"], ramped_result("w34-unmeasured", pre=0.0))
        self.assertFalse(scores["gates"]["overlay_moving_p95_lt_1"])
        self.assertGreater(scores["overlay"]["after_ring_scale"]["moving"]["p95_deg"], 1.0)

    def test_a_residual_of_the_wrong_size_does_not_excuse_it(self):
        """A closure that measured half the ramp leaves a ramp of half its size: 1.5 degrees of yaw."""
        main = sp.shared()["main"]
        scores = score.score_case(main["case"], ramped_result("w34-half", pre=RESIDUAL_DEG / 2))
        self.assertGreater(scores["overlay"]["after_ring_scale"]["moving"]["p95_deg"], 0.5)

    def test_no_image_closure_means_no_correction(self):
        """A ring closed by gyro measured no scale (tracker.ts): the raw overlay is graded and the ramp fails."""
        main = sp.shared()["main"]
        scores = score.score_case(main["case"], ramped_result("w34-gyro", method="gyro"))
        self.assertNotIn("after_ring_scale", scores["overlay"])
        self.assertFalse(scores["gates"]["overlay_moving_p95_lt_1"])

    def test_an_open_ring_means_no_correction(self):
        main = sp.shared()["main"]
        scores = score.score_case(main["case"], ramped_result("w34-open", closed=False))
        self.assertNotIn("after_ring_scale", scores["overlay"])

    def test_a_ring_that_did_not_need_correcting_is_graded_as_before(self):
        """The ideal result: residual 0, the corrected figure equals the raw one, and the gate is unchanged."""
        main = sp.shared()["main"]
        overlay = main["scores"]["overlay"]
        self.assertEqual(overlay["after_ring_scale"]["residual_deg"], 0.0)
        self.assertEqual(overlay["after_ring_scale"]["moving"], overlay["moving"])
        self.assertTrue(main["scores"]["gates"]["overlay_moving_p95_lt_1"])

    def test_the_correction_is_signed_by_the_turn(self):
        """A scan that turns counter-clockwise is corrected the same way: the residual is the heading error at the
        closing frame whichever way the scan went."""
        events = []
        for i in range(0, 361, 4):
            heading = -float(i)
            error = 2.0 * (i / 360.0)
            radians = math.radians(heading + error)
            events.append({"frame_id": f"f{i}", "basis": {"right": [math.cos(radians), -math.sin(radians), 0.0],
                                                         "up": [0.0, 0.0, 1.0],
                                                         "forward": [math.sin(radians), math.cos(radians), 0.0]}})
        diagnostics = {"loop": {"closed": True, "method": "image", "pre_deg": [0.0, 0.0, 2.0], "match": {"late_kf": 0}},
                       "keyframes": [{"id": 0, "frame_id": "f360"}]}
        scaled, info = score._ring_scaled_events(events, diagnostics)
        errors = []
        for event, source in zip(scaled, events):
            at = int(event["frame_id"][1:])
            radians = math.radians(-float(at))
            truth = [math.sin(radians), math.cos(radians), 0.0]
            errors.append(score.angle_between(event["basis"]["forward"], truth))
        self.assertLess(max(errors), 1e-6)
        # The turn is read from the reported headings, error included: 360 degrees less the 2 the error took back.
        self.assertAlmostEqual(info["turned_deg"], -358.0, places=6)


if __name__ == "__main__":
    unittest.main()
