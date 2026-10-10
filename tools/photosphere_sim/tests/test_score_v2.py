# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The version 2 scorer against a small synthetic pan case (spec 7.6, task T04).

The truth is written by hand, below: a flat horizon at 5 degrees
with one 25 degree bump, a 385 degree pan at 22.98 degrees of pitch on the
S25's lens, its claimed footprint and a visibility file with a dark stretch and
a wall too tall for the slit. The result is ``sim.ideal``'s, so every number it
scores is known in advance, and each test moves one thing and watches the gate
that is about it.

The helpers that write the synthetic case and edit a result (``build_case``,
``shared``, ``copy_result`` and the rest, used below as ``sp.``) live in this
module so that the two test files the task names are the only ones it adds:
``test_corrupt_v2`` imports them from here. The truth is written by hand and
independently of the scorer: the footprint top of every bin is found by
projecting through the slit, not by calling ``sim.score``.

Every test names the mutant it must catch. The three the task names:

- (a) on pan routes, read the frustum observable region for ``coverage_ge_0_95``:
  ``SlitPainter.test_a_perfect_slit_painter_covers_its_footprint`` fails.
- (b) no global yaw removal: ``GlobalYaw.test_a_37_degree_yaw_is_removed`` fails.
- (c) let an empty Measured set pass: ``EmptyMeasured.test_nothing_measured_with_36_visible_bins_fails_both_gates`` fails.

Scoring one result costs about two seconds, so the variants are cheap edits of
one ideal result and the expensive build happens once per process.
"""
import contextlib
import io
import json
import math
import pathlib
import sys
import unittest

import numpy as np
from PIL import Image

from sim import ideal, report, score
from sim import scene as scene_module
from sim import truth as truth_module
from sim.__main__ import main as cli_main
from sim.geometry import Camera, angle_between, look_basis

ROOT = pathlib.Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# The synthetic pan case, written straight to disk
#
# The world is a flat horizon at 5 degrees with one bump (a wall 25 degrees
# tall from azimuth 100 to 130). A second, optional, 50 degree wall stands from
# 300 to 312, above what the slit can see, and an optional dark stretch from
# 200 to 260 is not visible. The camera is the S25's 180 x 320 at a 41.14
# degree short axis, panning at 22.98 degrees of pitch for 385 degrees.
# --------------------------------------------------------------------------

PAN_ALT = 22.98
HEIGHT_M = 1.4
RATE_DEG_S = 20.0
SWEEP_DEG = 385.0
HOLD_FRAMES = 4
BUMP = (100.0, 130.0, 25.0)
TALL = (300.0, 312.0, 50.0)
DARK = (200.0, 260.0)
FLAT_ALT = 5.0

SCENE = {
    "schema": 1, "name": "synthetic", "seed": 7,
    "background": {"kind": "directional", "distance_m": 4000, "texture": {
        "kind": "value-noise", "seed": 7, "octaves": 4, "cells": 16, "grey": [70, 150],
        "stripes": {"count": 11, "tilt_deg": 23, "grey": 170, "width_deg": 1.5}}},
    "landmarks": [
        {"id": "in-a", "palette": 0, "az": 10, "alt": 22, "radius_deg": 1.0},
        {"id": "in-b", "palette": 1, "az": 90, "alt": 40, "radius_deg": 1.0},
        # Inside the slit's region but not clear of its top edge by 1.5 degrees.
        {"id": "edge", "palette": 2, "az": 180, "alt": 52, "radius_deg": 1.0},
        {"id": "high", "palette": 3, "az": 250, "alt": 70, "radius_deg": 1.0},
        {"id": "in-c", "palette": 4, "az": 300, "alt": 5, "radius_deg": 1.0},
        {"id": "in-d", "palette": 5, "az": 350, "alt": 30, "radius_deg": 1.0},
    ],
    "objects": [], "surface_landmarks": [], "test_obstacles": [],
}


def _write(path: pathlib.Path, data) -> None:
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8", newline="\n")


def _write_lines(path: pathlib.Path, records) -> None:
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records),
                    encoding="utf-8", newline="\n")


def pan_frames(step_deg: float, camera: Camera, sweep_deg: float = SWEEP_DEG,
               holds: int = HOLD_FRAMES) -> list:
    """Truth frames of a constant-rate pan with a short hold at each end."""
    moving = int(math.ceil(sweep_deg / step_deg))
    dt_ms = step_deg / RATE_DEG_S * 1000.0
    frames = []
    for index in range(holds + moving + holds):
        step = min(max(index - holds, 0), moving)
        az = (step * step_deg) % 360.0
        basis = look_basis(az, PAN_ALT)
        still = index < holds or index >= holds + moving
        frames.append({
            "frame_id": f"f{index:06d}", "t_capture_ms": int(round(index * dt_ms)),
            "position": [0.0, 0.0, HEIGHT_M],
            "right": [float(v) for v in basis.right], "up": [float(v) for v in basis.up],
            "forward": [float(v) for v in basis.forward],
            "az": az, "alt": PAN_ALT, "angular_rate_deg_s": 0.0 if still else RATE_DEG_S,
        })
    return frames


def footprint_tops(frames: list, camera: Camera) -> list:
    """The highest altitude of the claimed footprint at each 0.5 degree bin.

    Independent of ``sim.score``: for each azimuth sample of the bin, walk up
    the altitudes and ask each frame near enough whether the direction is
    inside its slit.
    """
    k_long = 0.9 * camera.cy / camera.fy
    k_wide = math.tan(math.radians(3.0))
    poses = np.array([list(f["right"]) + list(f["up"]) + list(f["forward"]) for f in frames])
    azimuths = np.array([f["az"] for f in frames])
    alts = np.arange(-10.0, 90.0, 0.05)
    sin_alt, cos_alt = np.sin(np.radians(alts)), np.cos(np.radians(alts))
    tops = []
    for bin_index in range(720):
        best = None
        for sample in range(5):
            az = (bin_index + (sample + 0.5) / 5.0) * 0.5
            near = np.nonzero(np.abs((azimuths - az + 180.0) % 360.0 - 180.0) <= 4.0)[0]
            if near.size == 0:
                continue
            direction = np.stack([math.sin(math.radians(az)) * cos_alt,
                                  math.cos(math.radians(az)) * cos_alt, sin_alt], axis=1)
            x = direction @ poses[near, 0:3].T
            y = direction @ poses[near, 3:6].T
            z = direction @ poses[near, 6:9].T
            inside = ((z > 0) & (np.abs(x) <= k_wide * z) & (np.abs(y) <= k_long * z)).any(axis=1)
            if inside.any():
                top = float(alts[np.nonzero(inside)[0].max()])
                best = top if best is None else max(best, top)
        tops.append(best)
    return tops


def horizon_truth(tall: bool) -> dict:
    """The 0.1 degree truth horizon, with the bump (and the tall wall) as obstacles."""
    centres = (np.arange(3600) + 0.5) * 0.1
    alt = np.full(3600, FLAT_ALT)
    obstacles = []
    walls = [("bump", BUMP)] + ([("tall", TALL)] if tall else [])
    for name, (az0, az1, top) in walls:
        inside = (centres >= az0) & (centres < az1)
        alt[inside] = top
        profile = np.where(inside, top, -10.0)
        obstacles.append({"id": name, "az_from": az0, "az_to": az1, "alt_max": top,
                          "min_width_deg": 10.0, "profile": [float(v) for v in profile]})
    return {"bins": 3600, "alt_max": [float(v) for v in alt], "obstacles": obstacles}


def build_case(base: pathlib.Path, name: str, *, step_deg: float = 1.5,
               dark: bool = True, tall: bool = True, grading=None,
               sweep_deg: float = SWEEP_DEG, holds: int = HOLD_FRAMES,
               compass_bias: float = 0.0) -> pathlib.Path:
    """Write the synthetic case directory ``base/name`` and return it."""
    case_dir = pathlib.Path(base) / name
    (case_dir / "truth").mkdir(parents=True, exist_ok=True)
    (case_dir / "input").mkdir(parents=True, exist_ok=True)
    truth_dir = case_dir / "truth"
    camera = Camera(180, 320, 41.14)
    frames = pan_frames(step_deg, camera, sweep_deg, holds)

    _write(truth_dir / "scene.json", SCENE)
    scene = scene_module.load(truth_dir / "scene.json")
    c_ref = [0.0, 0.0, HEIGHT_M]
    _write(truth_dir / "camera.json", {
        "width": camera.width, "height": camera.height, "fov_short_deg": camera.fov_short_deg,
        "fx": camera.fx, "fy": camera.fy, "cx": camera.cx, "cy": camera.cy, "distortion": None})
    _write(truth_dir / "reference.json", {"c_ref": c_ref})
    _write_lines(truth_dir / "trajectory.jsonl", frames)
    _write(truth_dir / "landmarks.json", truth_module.landmark_directions(scene, c_ref))
    _write(truth_dir / "reference-horizon.json", horizon_truth(tall))
    _write(truth_dir / "holds.json", [])
    _write(truth_dir / "route.json", {
        "schema": 1, "name": "pan-synthetic", "kind": "pan", "pivot": [0, 0, 0],
        "radius_m": 0, "height_m": HEIGHT_M, "reference": "axis", "start_hold_s": 0.3,
        "end_hold_s": 0.3, "rate_cap_deg_s": 45,
        "pans": [{"alt": PAN_ALT, "from_az": 0, "turn_deg": SWEEP_DEG,
                  "speed_deg_s": RATE_DEG_S, "ramp_s": 0.0, "direction": "cw"}],
        "tremor": {"yaw_deg": 0.0, "pitch_deg": 0.0, "roll_deg": 0.0, "band_hz": [0.5, 3.0]}})

    tops = footprint_tops(frames, camera)
    visible = [True] * 720
    if dark:
        visible = [not (DARK[0] <= (i + 0.5) * 0.5 < DARK[1]) for i in range(720)]
    _write(truth_dir / "visibility.json", {
        "bins": 720, "noise_sigma": 2.0,
        "contrast_sigma": [10.0 if v else 1.0 for v in visible],
        "visible": visible, "footprint_top_deg": tops})

    last = frames[-1]["t_capture_ms"]
    _write_lines(case_dir / "input" / "actions.jsonl", [
        {"t_ms": 0, "action": "begin"}, {"t_ms": last + 1000, "action": "finish"}])
    base_grading = {"still_pivot": True, "fully_observable": False, "daylight": True,
                    "north_graded": True, "expect_closure": True}
    _write(case_dir / "manifest.json", {
        "schema": 1, "case_id": name, "seed": 7, "scene": "synthetic", "route": "pan-synthetic",
        "camera": {"width": 180, "height": 320, "fov_short_deg": 41.14}, "fps": 13,
        "expected": "positive", "profile": "realism-v1",
        "hashes": {"frames": "0" * 64, "observations": "0" * 64, "truth": "0" * 64},
        "versions": {"app_commit": None},
        "grading": base_grading if grading is None else grading,
        "realism": {"absolute": {"bias_deg": compass_bias}}})
    return case_dir


# --------------------------------------------------------------------------
# One build per process, and small helpers for editing a result
# --------------------------------------------------------------------------

_shared = {}


def shared() -> dict:
    """The main case, its clear variant and their ideal results, built once.

    ``main`` has the bump, the tall wall and the dark stretch. ``clear`` has
    none of them and is graded ``fully_observable``. The temporary directory
    lives until the process exits.
    """
    if _shared:
        return _shared
    import atexit
    import tempfile

    from sim import ideal, score

    tmp = tempfile.TemporaryDirectory()
    atexit.register(tmp.cleanup)
    base = pathlib.Path(tmp.name)
    _shared["base"] = base
    for key, kwargs in (
            ("main", {}),
            ("clear", {"dark": False, "tall": False, "grading": {
                "still_pivot": True, "fully_observable": True, "daylight": True,
                "north_graded": True, "expect_closure": True}})):
        case = build_case(base, f"syn-{key}", **kwargs)
        result = ideal.make_ideal_result(case, base / f"ideal-{key}")
        _shared[key] = {"case": case, "ideal": result,
                        "scores": score.score_case(case, result)}
    return _shared


def read_json(path: pathlib.Path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, data) -> None:
    _write(pathlib.Path(path), data)


def copy_result(source: pathlib.Path, name: str) -> pathlib.Path:
    """A fresh copy of a result directory beside the shared ones, without its scores."""
    import shutil

    target = shared()["base"] / name
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target)
    for stale in ("scores.json", "report.html"):
        (target / stale).unlink(missing_ok=True)
    return target


def copy_case(source: pathlib.Path, name: str) -> pathlib.Path:
    """A copy of a case directory without any result, to edit its truth."""
    import shutil

    target = shared()["base"] / name
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("result"))
    return target


def turn_keyframes(result: pathlib.Path, deg: float) -> None:
    """Turn every keyframe pose in ``diagnostics.json`` east by ``deg`` degrees.

    Written here from the quaternion maths directly, so that a test of the
    north gates does not depend on the corruption that is also under test.
    """
    from sim.score import matrix_to_quat, quat_to_matrix

    radians = math.radians(deg)
    turn = np.array([[math.cos(radians), math.sin(radians), 0.0],
                     [-math.sin(radians), math.cos(radians), 0.0], [0.0, 0.0, 1.0]])
    diagnostics = read_json(result / "diagnostics.json")
    for keyframe in diagnostics["keyframes"]:
        keyframe["q"] = matrix_to_quat(turn @ quat_to_matrix(keyframe["q"]))
    write_json(result / "diagnostics.json", diagnostics)


def edit_diagnostics(result: pathlib.Path, edit) -> None:
    diagnostics = read_json(result / "diagnostics.json")
    edit(diagnostics)
    write_json(result / "diagnostics.json", diagnostics)


def edit_horizon(result: pathlib.Path, edit) -> None:
    horizon = read_json(result / "horizon.json")
    edit(horizon)
    write_json(result / "horizon.json", horizon)


#: The helpers above, under the name both test modules use for them.
sp = sys.modules[__name__]

#: The gates of the main case: a pan route, a version 2 horizon, daylight and
#: still-pivot and north-graded and expecting closure, not fully observable,
#: no compass bias. Everything else is absent on purpose.
MAIN_GATES = {
    "landmarks_p95_lt_0_5", "landmarks_p99_lt_1", "no_omissions", "no_duplicates",
    "horizon_p95_lt_1", "no_missed_obstructions", "overlay_moving_p95_lt_1",
    "no_duplicate_frames", "capture_p95_le_1500", "every_hold_captured",
    "coverage_ge_0_95", "overlay_complete", "never_below_profile",
    "measured_is_honest", "measured_share", "unknown_where_unobservable",
    "loop_residual_lt_0_25", "focal_err_lt_0_5pct", "north_err_lt_1",
    "north_sigma_honest", "live_fill_p95_le_1000", "pass",
}

#: The legacy gate names, in the order they have always been written.
LEGACY_GATES = [
    "landmarks_p95_lt_0_5", "landmarks_p99_lt_1", "no_omissions", "no_duplicates",
    "horizon_p95_lt_1", "no_missed_obstructions", "no_unresolved_boundary",
    "overlay_settled_p95_lt_0_5", "overlay_moving_p95_lt_1", "overlay_max_lt_10",
    "no_duplicate_frames", "capture_p95_le_1500", "every_hold_captured",
    "coverage_ge_0_95", "pass",
]
LEGACY_SCORE_KEYS = {
    "schema", "case_id", "input_hash", "app_commit", "profile", "panorama",
    "landmarks", "horizon", "overlay", "capture", "coverage", "gates",
}


def failing(scores: dict) -> set:
    """The gates that failed, `pass` aside."""
    return {name for name, ok in scores["gates"].items() if name != "pass" and not ok}


class Fixture(unittest.TestCase):
    """The shared main case, with a helper that scores an edited copy."""

    @classmethod
    def setUpClass(cls):
        cls.shared = sp.shared()
        cls.case = cls.shared["main"]["case"]
        cls.ideal = cls.shared["main"]["ideal"]
        cls.ideal_scores = cls.shared["main"]["scores"]

    def variant(self, name: str, edit=None, case=None) -> dict:
        """Score a fresh copy of the ideal result after ``edit(result_dir)``."""
        result = sp.copy_result(self.ideal, name)
        if edit is not None:
            edit(result)
        return score.score_case(case or self.case, result)


class IdealOnAPanRoute(Fixture):
    """The perfect result passes exactly the gates that apply, and says why."""

    def test_the_ideal_passes_every_applicable_gate(self):
        """The perfect result fails nothing.

        MUTATION: drop the Tall rule from `ideal._ideal_horizon_v2` (the wall
        above the slit becomes Measured) and the 50 degree wall is graded
        against a polyline that cannot have measured it; the ideal then fails
        `measured_share` or the states test below, whichever runs first.
        """
        scores = self.ideal_scores
        self.assertEqual({k for k, v in scores["gates"].items() if not v}, set(),
                         scores["gates"])
        self.assertTrue(scores["gates"]["pass"])

    def test_the_gate_table_holds_exactly_the_applicable_gates(self):
        """The retired overlay gates are gone, and `no_unresolved_boundary`
        waits for a `fully_observable` case. MUTATION: keep either and the key
        set differs."""
        self.assertEqual(set(self.ideal_scores["gates"]), MAIN_GATES)
        self.assertEqual(set(self.shared["clear"]["scores"]["gates"]),
                         MAIN_GATES | {"no_unresolved_boundary"})
        self.assertTrue(self.shared["clear"]["scores"]["gates"]["pass"])

    def test_the_states_follow_the_truth_and_the_visibility_file(self):
        """Unknown where nobody could see, Tall above the slit, Measured else.

        The dark stretch is azimuth 200 to 260, 120 bins. The tall wall is 50
        degrees at 300 to 312: 50 + 6 is above the footprint top (about 54),
        24 bins. The bump is 25 + 6, inside it.
        """
        horizon = self.ideal_scores["horizon"]
        self.assertEqual(horizon["version"], 2)
        self.assertEqual(horizon["states"]["unknown"], 120)
        self.assertEqual(horizon["states"]["tall"], 24)
        self.assertEqual(horizon["states"]["measured"], 576)
        self.assertEqual(horizon["visible_bins"], 600)
        written = sp.read_json(self.ideal / "horizon.json")
        profile = np.array(written["profile"])
        state = np.array(written["profile_state"])
        self.assertTrue(np.all(profile[state != 0] == 90.0))
        measured_alt = profile[state == 0]
        self.assertEqual(set(np.unique(measured_alt)), {5.0, 25.0})
        traced = written["profile_traced"]
        self.assertTrue(all(traced[i] is None for i in range(720) if state[i] == 2))
        self.assertTrue(all(traced[i] == 50.0 for i in range(720) if state[i] == 3))
        self.assertEqual(written["uncertain_bins"],
                         [i for i in range(720) if state[i] in (1, 2, 3)])

    def test_the_polyline_is_graded_over_measured_bins_with_nothing_to_spare(self):
        horizon = self.ideal_scores["horizon"]
        self.assertEqual(horizon["error_samples"], 576 * 5)
        self.assertEqual(horizon["signed_error_deg"]["max"], 0.0)
        self.assertEqual(horizon["below_profile_bins"], 0)
        self.assertEqual(horizon["measured_share"]["share"], 1.0)
        self.assertEqual(horizon["unknown_where_unobservable"]["measured"], 0)

    def test_landmarks_are_expected_only_inside_the_footprint_with_a_margin(self):
        """`edge` is at 52 degrees with a 1 degree disc: inside the slit's
        region (its top is 53.98) but not by 1.5 degrees, so it is not
        expected. `high` is far outside. Both are painted by the ideal and so
        are slivers, neither omitted nor found.

        MUTATION: a margin of 0 expects `edge` (alt 52 plus a 1 degree disc is
        inside the footprint top of 53.98) and the assertions below fail.
        """
        rows = {row["id"]: row for row in self.ideal_scores["landmarks"]["per_landmark"]}
        self.assertEqual({k for k, v in rows.items() if v["observable"]},
                         {"in-a", "in-b", "in-c", "in-d"})
        self.assertEqual({k for k, v in rows.items() if v["in_footprint"]},
                         {"in-a", "in-b", "in-c", "in-d"})
        self.assertEqual(rows["edge"]["status"], "sliver")
        self.assertEqual(rows["high"]["status"], "sliver")
        self.assertEqual(self.ideal_scores["landmarks"]["expected"], 4)
        self.assertEqual(self.ideal_scores["landmarks"]["found"], 4)

    def test_the_diagnostics_numbers_are_the_truth(self):
        scores = self.ideal_scores
        self.assertLess(scores["loop"]["residual_deg"], 1e-6)
        self.assertEqual(scores["loop"]["method"], "image")
        self.assertEqual(scores["focal"]["error_fraction"], 0.0)
        self.assertLess(abs(scores["north"]["error_deg"]), 1e-9)
        self.assertEqual(scores["north"]["sigma_deg"], 2.0)
        self.assertEqual(scores["overlay"]["complete_fraction"], 1.0)
        self.assertEqual(scores["overlay"]["frames_in_window"], 265)
        self.assertEqual(scores["live_fill"]["never"], 0)
        self.assertLessEqual(scores["live_fill"]["p95_ms"], 100)
        self.assertEqual(scores["coverage"]["footprint_fraction_covered"], 1.0)

    def test_scores_json_carries_the_version_2_blocks(self):
        written = sp.read_json(self.ideal / "scores.json")
        for key in ("route", "flags", "visibility", "loop", "focal", "north", "live_fill",
                    "cost_ms"):
            self.assertIn(key, written)
        self.assertEqual(written["route"], {"kind": "pan"})
        self.assertEqual(written["flags"]["north_graded"], True)
        self.assertEqual(written["flags"]["fully_observable"], False)
        self.assertEqual(written["cost_ms"]["ref_unit_ms"], 0.0)


class SlitPainter(unittest.TestCase):
    """RS B2: a perfect slit painter must be able to pass the coverage gate."""

    @classmethod
    def setUpClass(cls):
        shared = sp.shared()
        # The exact frame set of the spec's coverage check: every 0.6 degrees
        # around the ring at 22.98 degrees of pitch, 600 frames, no holds.
        cls.case = sp.build_case(shared["base"], "slit", step_deg=0.6, sweep_deg=359.5, holds=0)
        cls.frames = [json.loads(line) for line in
                      (cls.case / "truth" / "trajectory.jsonl").read_text().splitlines()]
        cls.camera = sp.read_json(cls.case / "truth" / "camera.json")

    def painted(self, long_fraction: float):
        """A panorama with alpha only where a slit painter paints, written
        straight from the spec's rule and not from `sim.score`."""
        width, height = 1080, 300
        az = (np.arange(width) + 0.5) / width * 360.0
        alt = 90.0 - np.arange(height) / (height - 1) * 100.0
        grid_az, grid_alt = np.meshgrid(np.radians(az), np.radians(alt))
        cells = np.stack([np.sin(grid_az) * np.cos(grid_alt), np.cos(grid_az) * np.cos(grid_alt),
                          np.sin(grid_alt)], axis=-1).reshape(-1, 3)
        k_wide = math.tan(math.radians(3.0))
        k_long = long_fraction * self.camera["cy"] / self.camera["fy"]
        painted = np.zeros(cells.shape[0], dtype=bool)
        for frame in self.frames:
            x = cells @ np.array(frame["right"])
            y = cells @ np.array(frame["up"])
            z = cells @ np.array(frame["forward"])
            painted |= (z > 0) & (np.abs(x) <= k_wide * z) & (np.abs(y) <= k_long * z)
        image = np.zeros((height, width, 4), dtype=np.uint8)
        image[:, :, :3] = 128
        image[:, :, 3] = np.where(painted.reshape(height, width), 255, 0)
        return image

    def score(self, name: str, image) -> dict:
        result = sp.shared()["base"] / name
        result.mkdir(exist_ok=True)
        Image.fromarray(image, mode="RGBA").save(result / "panorama.png")
        return score.score_case(self.case, result)

    def test_a_perfect_slit_painter_covers_its_footprint(self):
        """At least 0.999 of the claimed footprint, and the old frustum rule
        gives 0.9397 for the same panorama, which is why it cannot be the rule.

        MUTATION (a): `coverage_ge_0_95` reads `observable_fraction_covered`
        (the frustum) on a pan route and the gate below is False.
        """
        scores = self.score("slit-perfect", self.painted(0.9))
        coverage = scores["coverage"]
        self.assertGreaterEqual(coverage["footprint_fraction_covered"], 0.999)
        self.assertEqual(round(coverage["observable_fraction_covered"], 4), 0.9397)
        self.assertLess(coverage["observable_fraction_covered"], score.GATE_COVERAGE)
        self.assertTrue(scores["gates"]["coverage_ge_0_95"])

    def test_a_painter_that_leaves_half_of_the_slit_unpainted_fails(self):
        """The footprint rule is a rule, not a pass: paint only the middle half
        of the slit's height and the gate falls.

        MUTATION: `coverage_ge_0_95` fixed true on a pan route passes this.
        """
        scores = self.score("slit-half", self.painted(0.45))
        self.assertLess(scores["coverage"]["footprint_fraction_covered"], 0.6)
        self.assertFalse(scores["gates"]["coverage_ge_0_95"])


class GlobalYaw(Fixture):
    """RS B3: the scan frame's zero is arbitrary, so one yaw is removed."""

    def test_a_37_degree_yaw_is_removed(self):
        """A result turned 37 degrees as a whole passes the moving-overlay
        gate. Before the removal its error is about 34 degrees, so the gate
        passing is the removal, not an easy case.

        What else it fails is the north and the boundary, correctly: it is
        turned 37 degrees from the truth.

        MUTATION (b): no global yaw removal (`remove_yaw=False` in
        `score_case`) and the first assertion below fails.
        """
        from sim import corrupt
        turned = corrupt.apply(self.case, self.ideal, "yaw", sp.shared()["base"] / "yaw37",
                               deg=37.0)
        scores = score.score_case(self.case, turned)
        self.assertTrue(scores["gates"]["overlay_moving_p95_lt_1"])
        self.assertAlmostEqual(scores["overlay"]["yaw_removed_deg"], 37.0, places=6)
        self.assertLess(scores["overlay"]["moving"]["p95_deg"], 1e-6)
        truth = {f["frame_id"]: f for f in
                 map(json.loads, (self.case / "truth" / "trajectory.jsonl").read_text().splitlines())}
        events = [json.loads(line) for line in
                  (turned / "events.jsonl").read_text().splitlines()]
        raw = [angle_between(e["basis"]["forward"], truth[e["frame_id"]]["forward"])
               for e in events]
        self.assertGreater(float(np.median(raw)), 30.0)
        self.assertTrue({"north_err_lt_1", "north_sigma_honest", "horizon_p95_lt_1",
                         "no_omissions"} <= failing(scores))

    def test_the_mean_is_taken_on_the_circle(self):
        """359 and 1 degrees of heading error are 0 apart, not 358.

        MUTATION: a plain mean of the wrapped differences reads 0 for +179 and
        -179, which are 2 degrees apart on the circle; the circular mean reads
        180.
        """
        def event(frame_id, heading):
            radians = math.radians(heading)
            forward = [math.sin(radians), math.cos(radians), 0.0]
            return {"frame_id": frame_id, "basis": {"forward": forward}}

        truth = {"a": {"forward": [0.0, 1.0, 0.0]}, "b": {"forward": [0.0, 1.0, 0.0]}}
        self.assertAlmostEqual(
            abs(score.global_yaw_deg([event("a", 179.0), event("b", -179.0)], truth)), 180.0,
            places=6)
        self.assertAlmostEqual(
            score.global_yaw_deg([event("a", 359.0), event("b", 1.0)], truth), 0.0, places=6)

    def test_a_350_degree_yaw_is_minus_ten(self):
        from sim import corrupt
        turned = corrupt.apply(self.case, self.ideal, "north-wrap",
                               sp.shared()["base"] / "wrap")
        scores = score.score_case(self.case, turned)
        self.assertAlmostEqual(scores["overlay"]["yaw_removed_deg"], -10.0, places=6)
        self.assertTrue(scores["gates"]["overlay_moving_p95_lt_1"])
        self.assertAlmostEqual(scores["north"]["error_deg"], -10.0, places=6)


class EmptyMeasured(Fixture):
    """RS M1: a result that blocks everything has measured nothing."""

    @staticmethod
    def block_everything(result: pathlib.Path) -> None:
        def edit(horizon):
            horizon["profile"] = [90.0] * 720
            horizon["profile_traced"] = [None] * 720
            horizon["profile_state"] = [2] * 720
            horizon["points"] = [{"az": 0.0, "alt": 90.0}, {"az": 180.0, "alt": 90.0}]
            horizon["uncertain_bins"] = list(range(720))
        sp.edit_horizon(result, edit)

    def test_nothing_measured_with_36_visible_bins_fails_both_gates(self):
        """The main case has 600 visible bins; all of them blocked fails the
        two percentile gates, and the profile alone (no state is a lie) is
        otherwise clean.

        MUTATION (c): let an empty Measured set pass and both assertions fail.
        """
        scores = self.variant("empty-600", self.block_everything)
        self.assertTrue(scores["horizon"]["empty_measured"])
        self.assertFalse(scores["gates"]["horizon_p95_lt_1"])
        self.assertFalse(scores["gates"]["measured_is_honest"])
        # The same result fails `measured_share` (nothing Measured among the
        # visible bins) and `no_missed_obstructions` (the bump has no resolved
        # bin), by construction. The polyline is at 90 over a profile of 90.
        self.assertEqual(failing(scores), {"horizon_p95_lt_1", "measured_is_honest",
                                           "measured_share", "no_missed_obstructions"})

    def test_the_limit_is_36_visible_bins(self):
        """35 visible bins and an all-blocked result is honest; 36 is not.

        MUTATION: the constant 36 changed to 40 passes the second case.
        """
        for visible_bins, passes in ((35, True), (36, False)):
            case = sp.copy_case(self.case, f"few-{visible_bins}")
            visibility = sp.read_json(case / "truth" / "visibility.json")
            visibility["visible"] = [i < visible_bins for i in range(720)]
            sp.write_json(case / "truth" / "visibility.json", visibility)
            scores = self.variant(f"few-{visible_bins}-result", self.block_everything, case)
            self.assertEqual(scores["horizon"]["visible_bins"], visible_bins)
            self.assertEqual(scores["gates"]["horizon_p95_lt_1"], passes, visible_bins)
            self.assertEqual(scores["gates"]["measured_is_honest"], passes, visible_bins)


class Thresholds(Fixture):
    """Each rule's number, from both sides."""

    @staticmethod
    def bump(result: pathlib.Path, az0: float, az1: float, extra: float) -> None:
        """Raise the polyline by ``extra`` degrees over [az0, az1): still above
        the profile, so only the error moves."""
        def edit(horizon):
            horizon["points"] += [
                {"az": az0 - 0.01, "alt": 5.0}, {"az": az0, "alt": 5.0 + extra},
                {"az": az1, "alt": 5.0 + extra}, {"az": az1 + 0.01, "alt": 5.0}]
        sp.edit_horizon(result, edit)

    def test_p95_and_p99_are_different_gates(self):
        """3 degrees of error over 100 of 2880 samples (3.5 per cent) is
        inside the 5 per cent the p95 allows and outside the 1 per cent the
        p99 allows; over 200 samples (6.9 per cent) both fail.

        MUTATION: `measured_is_honest` reading the p95, or `horizon_p95_lt_1`
        reading the p99, makes one of the two cases wrong.
        """
        scores = self.variant("err-100", lambda r: self.bump(r, 20.0, 30.0, 3.0))
        self.assertEqual(failing(scores), {"measured_is_honest"})
        self.assertAlmostEqual(scores["horizon"]["signed_error_deg"]["p99"], 3.0, places=6)
        scores = self.variant("err-200", lambda r: self.bump(r, 20.0, 40.0, 3.0))
        self.assertEqual(failing(scores), {"horizon_p95_lt_1", "measured_is_honest"})

    def test_a_polyline_that_wanders_over_a_blocked_bin_is_not_graded_there(self):
        """The polyline is graded over Measured bins only. Pulling it to the
        floor across the dark stretch (profile 90) breaks G1, and nothing else:
        the error in a bin the scanner disclaimed is not an error.

        MUTATION: grading every bin instead of the Measured mask fails the
        `horizon_p95_lt_1` assertion (120 bins is 17 per cent of the ring).
        """
        def edit(horizon):
            horizon["points"] += [{"az": 201.0, "alt": 0.0}, {"az": 259.0, "alt": 0.0}]
        scores = self.variant("dark-floor", lambda r: sp.edit_horizon(r, edit))
        self.assertEqual(failing(scores), {"never_below_profile"})

    def test_the_measured_share_gate_is_85_per_cent(self):
        """86 of the 576 expected bins relabelled Low is 14.93 per cent: share
        0.8507, passes. 87 is 0.8490, fails.

        MUTATION: a threshold of 0.80 passes the second case.
        """
        def relabel(count):
            def edit(horizon):
                horizon["profile_state"][:count] = [1] * count
            return lambda result: sp.edit_horizon(result, edit)

        # The first 87 bins are azimuth 0 to 43.5, all flat Measured ground.
        scores = self.variant("share-86", relabel(86))
        self.assertTrue(scores["gates"]["measured_share"])
        self.assertAlmostEqual(scores["horizon"]["measured_share"]["share"], 490 / 576)
        scores = self.variant("share-87", relabel(87))
        self.assertFalse(scores["gates"]["measured_share"])
        self.assertEqual(failing(scores), {"measured_share"})

    def test_unknown_where_unobservable_allows_5_per_cent(self):
        """6 of the 120 not-visible bins called Measured is exactly 5 per cent:
        passes. 7 fails. Both also fail `measured_is_honest`, because those
        bins are published at 90 over a truth of 5 and are graded.

        MUTATION: a limit of 0.10 passes 7.
        """
        def measure(count):
            def edit(horizon):
                horizon["profile_state"][400:400 + count] = [0] * count
            return lambda result: sp.edit_horizon(result, edit)

        scores = self.variant("dark-6", measure(6))
        self.assertTrue(scores["gates"]["unknown_where_unobservable"])
        scores = self.variant("dark-7", measure(7))
        self.assertFalse(scores["gates"]["unknown_where_unobservable"])
        self.assertIn("measured_is_honest", failing(scores))

    def test_overlay_complete_is_95_per_cent_of_the_frames_delivered(self):
        """265 frames are delivered between Begin and Finish: 252 with a pose
        is 95.09 per cent, 251 is 94.72.

        MUTATION: a threshold of 0.90 passes 251.
        """
        def drop(count):
            def edit(result):
                path = result / "events.jsonl"
                events = [json.loads(line) for line in path.read_text().splitlines()]
                for event in events[:count]:
                    event["basis"] = None
                path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
            return edit

        self.assertTrue(self.variant("complete-13", drop(13))["gates"]["overlay_complete"])
        scores = self.variant("complete-14", drop(14))
        self.assertFalse(scores["gates"]["overlay_complete"])
        self.assertEqual(scores["overlay"]["frames_with_basis"], 251)

    def test_the_denominator_is_the_frames_delivered_not_the_lines_written(self):
        """A scanner that stops writing events halfway has not shrunk the
        denominator.

        MUTATION: counting the lines present instead of the frames delivered
        reads 100 per cent.
        """
        def truncate(result):
            path = result / "events.jsonl"
            lines = path.read_text().splitlines()
            path.write_text("".join(line + "\n" for line in lines[:len(lines) // 2]),
                            encoding="utf-8")
        scores = self.variant("half-events", truncate)
        self.assertAlmostEqual(scores["overlay"]["complete_fraction"], 132 / 265)
        self.assertEqual(failing(scores), {"overlay_complete"})

    def test_the_window_is_the_replays_actions_not_the_scanners_events(self):
        """With Finish at the middle of the scan, only the frames delivered
        before it are asked about, and the footprint is theirs.

        MUTATION: ignoring `input/actions.jsonl` leaves 265 frames in the window.
        """
        case = sp.copy_case(self.case, "half-window")
        frames = [json.loads(line) for line in
                  (case / "truth" / "trajectory.jsonl").read_text().splitlines()]
        middle = frames[len(frames) // 2]["t_capture_ms"]
        (case / "input" / "actions.jsonl").write_text(
            json.dumps({"t_ms": 0, "action": "begin"}) + "\n"
            + json.dumps({"t_ms": middle, "action": "finish"}) + "\n", encoding="utf-8")
        scores = self.variant("half-window-result", case=case)
        self.assertLess(scores["overlay"]["frames_in_window"], 140)
        self.assertGreater(scores["overlay"]["frames_in_window"], 125)
        self.assertEqual(scores["overlay"]["complete_fraction"], 1.0)
        self.assertLess(scores["live_fill"]["cells"], self.ideal_scores["live_fill"]["cells"])

    def test_the_loop_gate_is_a_quarter_of_a_degree(self):
        """Measured in degrees, and exactly: the residual is the rotation put
        in. 0.24 passes and 0.26 fails.

        MUTATION: `loop_residual_lt_0_25` reading 0.5 passes the second case.
        """
        from sim import corrupt
        for deg, passes in ((0.24, True), (0.26, False)):
            out = corrupt.apply(self.case, self.ideal, "inflate-closure",
                                sp.shared()["base"] / f"loop-{deg}", deg=deg)
            scores = score.score_case(self.case, out)
            self.assertAlmostEqual(scores["loop"]["residual_deg"], deg, places=6)
            self.assertEqual(scores["gates"]["loop_residual_lt_0_25"], passes, deg)

    def test_a_loop_that_is_not_an_image_closure_fails(self):
        """RS m4: not closed, closed by the gyro, and no match at all each fail
        however small the residual.

        MUTATION: dropping the method test passes the `gyro` case.
        """
        def closed(value):
            return lambda result: sp.edit_diagnostics(
                result, lambda d: d["loop"].update({"closed": value}))

        def method(value):
            return lambda result: sp.edit_diagnostics(
                result, lambda d: d["loop"].update({"method": value}))

        def no_match(result):
            sp.edit_diagnostics(result, lambda d: d["loop"].update({"match": None}))

        for name, edit in (("open", closed(False)), ("gyro", method("gyro")),
                           ("no-match", no_match)):
            scores = self.variant(f"loop-{name}", edit)
            self.assertEqual(failing(scores), {"loop_residual_lt_0_25"}, name)

    def test_the_focal_gate_is_half_a_per_cent(self):
        """MUTATION: a limit of 1 per cent passes the 0.6 per cent case."""
        for scale, passes in ((1.004, True), (1.006, False)):
            def edit(result, scale=scale):
                sp.edit_diagnostics(result, lambda d: d["focal"].update(
                    {"f_norm": d["focal"]["f_norm"] * scale}))
            scores = self.variant(f"focal-{scale}", edit)
            self.assertAlmostEqual(scores["focal"]["error_fraction"], scale - 1.0, places=9)
            self.assertEqual(scores["gates"]["focal_err_lt_0_5pct"], passes, scale)

    def test_north_error_and_north_honesty_are_two_gates(self):
        """0.9 degrees is inside both. 1.1 fails the error gate and is still
        honest at a sigma of 2 (limit 5). 5.2 fails both.

        MUTATION: `north_sigma_honest` using 1.0 x sigma fails the 4.9 case.
        """
        for deg, error_ok, honest in ((0.9, True, True), (1.1, False, True),
                                      (4.9, False, True), (5.2, False, False)):
            scores = self.variant(f"north-{deg}", lambda r, d=deg: sp.turn_keyframes(r, d))
            self.assertAlmostEqual(scores["north"]["error_deg"], deg, places=6)
            self.assertEqual(scores["gates"]["north_err_lt_1"], error_ok, deg)
            self.assertEqual(scores["gates"]["north_sigma_honest"], honest, deg)

    def test_live_fill_counts_late_and_never(self):
        """Seen 0.9 s late passes and 1.0 s late fails (the ideal is within
        0.1 s). A cell the scan entered and the scanner never reported counts
        10 s: 3 per cent of the cells never seen is inside the p95 and 7 per
        cent is outside.

        MUTATION: skipping never-seen cells (counting them 0) passes the 7 per
        cent case.
        """
        def late(deciseconds):
            def edit(result):
                path = result / "first_seen.bin"
                seen = np.frombuffer(path.read_bytes(), dtype="<u2").copy()
                seen[seen > 0] += deciseconds
                path.write_bytes(seen.tobytes())
            return edit

        def never(columns):
            def edit(result):
                path = result / "first_seen.bin"
                seen = np.frombuffer(path.read_bytes(), dtype="<u2").copy().reshape(300, 1080)
                seen[:, :columns] = 0
                path.write_bytes(seen.tobytes())
            return edit

        self.assertTrue(self.variant("late-9", late(9))["gates"]["live_fill_p95_le_1000"])
        scores = self.variant("late-10", late(10))
        self.assertFalse(scores["gates"]["live_fill_p95_le_1000"])
        self.assertEqual(failing(scores), {"live_fill_p95_le_1000"})
        self.assertTrue(self.variant("never-32", never(32))["gates"]["live_fill_p95_le_1000"])
        scores = self.variant("never-76", never(76))
        self.assertFalse(scores["gates"]["live_fill_p95_le_1000"])
        self.assertEqual(scores["live_fill"]["p95_ms"], score.LIVE_FILL_NEVER_MS)

    def test_missing_files_fail_the_gates_that_need_them(self):
        """A result without diagnostics or `first_seen.bin` is not excused.

        MUTATION: `honest = True` for a result with no diagnostics (a missing
        file is no claim) drops `north_sigma_honest` from the failing set.
        """
        def remove(result):
            (result / "diagnostics.json").unlink()
            (result / "first_seen.bin").unlink()
        scores = self.variant("no-diagnostics", remove)
        self.assertEqual(failing(scores), {
            "loop_residual_lt_0_25", "focal_err_lt_0_5pct", "north_err_lt_1",
            "north_sigma_honest", "live_fill_p95_le_1000"})


class OtherCaseKinds(Fixture):
    """Gates follow the case's flags and route, not the result's shape."""

    def test_a_case_without_the_flags_has_fewer_gates(self):
        """An arc-like case: daylight only. No measured share (needs still
        pivot), no closure, no focal lock, no north error. The honesty of the
        north has no flag: it applies to every case without a compass bias.
        The `focal` block is still reported.
        """
        case = sp.build_case(sp.shared()["base"], "syn-arc",
                             grading={"still_pivot": False, "fully_observable": False,
                                      "daylight": True, "north_graded": False,
                                      "expect_closure": False})
        scores = score.score_case(case, self.ideal)
        self.assertEqual(set(scores["gates"]), MAIN_GATES - {
            "measured_share", "loop_residual_lt_0_25", "focal_err_lt_0_5pct",
            "north_err_lt_1"})
        self.assertIn("focal", scores)
        self.assertTrue(scores["gates"]["pass"])

    def test_a_compass_bias_removes_the_honesty_gate(self):
        """A biased compass has an error nothing averages away: the sigma
        cannot be held to it."""
        case = sp.build_case(sp.shared()["base"], "syn-bias", compass_bias=3.0)
        scores = score.score_case(case, self.ideal)
        self.assertEqual(set(scores["gates"]), MAIN_GATES - {"north_sigma_honest"})

    def test_without_a_pan_route_the_legacy_rules_stand(self):
        """Delete `truth/route.json`: the same result is a non-pan case. The
        hold gates and the two settled/max overlay gates are real again, the
        frustum is the coverage region, and the pan-only gates are gone.

        MUTATION: `pan = horizon_v2` in `score_case` (a version 2 horizon makes
        a pan case) keeps the pan-only gates and fails the key set.
        """
        case = sp.copy_case(self.case, "syn-nopan")
        (case / "truth" / "route.json").unlink()
        scores = score.score_case(case, self.ideal)
        self.assertEqual(set(scores["gates"]), (MAIN_GATES - {
            "overlay_complete", "loop_residual_lt_0_25", "focal_err_lt_0_5pct",
            "north_err_lt_1", "north_sigma_honest", "live_fill_p95_le_1000"}) | {
            "overlay_settled_p95_lt_0_5", "overlay_max_lt_10"})
        self.assertNotIn("footprint_fraction_covered", scores["coverage"])
        self.assertNotIn("yaw_removed_deg", scores["overlay"])
        rows = {row["id"]: row for row in scores["landmarks"]["per_landmark"]}
        self.assertTrue(rows["edge"]["observable"])
        self.assertNotIn("in_footprint", rows["edge"])

    def test_a_legacy_case_scores_with_none_of_the_version_2_keys(self):
        """The committed still-pivot fixture: no route file, no visibility, a
        version 1 horizon, no grading. Its `scores.json` has the keys and the
        fifteen gates it has always had. (The byte comparison against the
        scorer before this task is the report's, run on the same fixture.)

        MUTATION: `extended = True` in `score_case` puts the version 2 blocks
        and gates on the legacy path and the key checks below fail.
        """
        fixture = ROOT / "fixtures" / "chartyard-shortpan-60"
        result = ideal.make_ideal_result(fixture, sp.shared()["base"] / "legacy-ideal")
        scores = score.score_case(fixture, result)
        self.assertEqual(set(scores), LEGACY_SCORE_KEYS)
        self.assertEqual(list(scores["gates"]), LEGACY_GATES)
        self.assertTrue(scores["gates"]["pass"])
        self.assertNotIn("version", scores["horizon"])
        self.assertEqual(set(scores["coverage"]), {
            "panorama_alpha_fraction", "observable_fraction_covered", "cells_covered_fraction"})
        self.assertNotIn("yaw_removed_deg", scores["overlay"])
        self.assertNotIn("in_footprint", scores["landmarks"]["per_landmark"][0])
        self.assertFalse((result / "diagnostics.json").exists())
        self.assertEqual(sp.read_json(result / "horizon.json")["bins"], 3600)


class Reports(Fixture):
    """`sim score` and `report.html` on a version 2 result."""

    def test_the_command_line_scores_a_pan_case_and_the_report_shows_it(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = cli_main(["score", "syn-main", "--cases", str(self.shared["base"]),
                               "--result", str(self.ideal)])
        self.assertEqual(status, 0)
        html = (self.ideal / "report.html").read_text(encoding="utf-8")
        for needle in ("measured_share", "never_below_profile", "live_fill_p95_le_1000",
                       "overlay_complete", "profile (version 2)", "loop closure",
                       "one global yaw removed", "class=\"state-tall\"",
                       "coverage of the claimed footprint"):
            self.assertIn(needle, html)
        self.assertNotIn("overlay_settled_p95_lt_0_5", html)

    def test_a_legacy_report_is_unchanged_by_the_version_2_styles(self):
        """The version 2 style block is added to a version 2 page only."""
        self.assertIn("svg .state-low", report.STYLE_V2)
        self.assertNotIn("state-low", report.STYLE)


class Helpers(unittest.TestCase):
    """The shared maths, pinned independently."""

    def test_a_quaternion_round_trips_a_rotation(self):
        """MUTATION: a sign flipped in the first branch of `matrix_to_quat`."""
        rng = np.random.default_rng(3)
        for _ in range(50):
            axis = rng.normal(size=3)
            axis /= np.linalg.norm(axis)
            angle = rng.uniform(0.0, math.pi)
            skew = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]],
                             [-axis[1], axis[0], 0]])
            rotation = np.eye(3) + math.sin(angle) * skew + (1 - math.cos(angle)) * skew @ skew
            back = score.quat_to_matrix(score.matrix_to_quat(rotation))
            self.assertLess(np.abs(back - rotation).max(), 1e-12)
            self.assertAlmostEqual(score.rotation_angle_deg(rotation), math.degrees(angle),
                                   places=9)

    def test_a_rotation_angle_is_exact_near_zero(self):
        """`acos` of the cosine has no precision near zero; the angle comes
        from the sine as well.

        MUTATION: `acos` of the cosine alone is wrong by about 5e-9 degrees at
        1e-4 degrees and fails the 12 places.
        """
        tiny = math.radians(1e-4)
        rotation = np.array([[math.cos(tiny), -math.sin(tiny), 0.0],
                             [math.sin(tiny), math.cos(tiny), 0.0], [0.0, 0.0, 1.0]])
        self.assertAlmostEqual(score.rotation_angle_deg(rotation), 1e-4, places=12)

    def test_the_truth_matrix_is_right_up_and_minus_forward(self):
        basis = look_basis(33.0, 12.0, 5.0)
        frame = {"right": list(basis.right), "up": list(basis.up),
                 "forward": list(basis.forward)}
        matrix = score.truth_matrix(frame)
        self.assertAlmostEqual(float(np.linalg.det(matrix)), 1.0, places=12)
        quat = score.matrix_to_quat(matrix)
        forward = -score.quat_to_matrix(quat)[:, 2]
        self.assertLess(np.abs(forward - basis.forward).max(), 1e-12)

    def test_the_step_polyline_is_never_below_the_profile_and_tight_at_bin_centres(self):
        """G1 at both ends of every bin, and exactly the profile at every bin
        centre: it is the tightest line that clears both bins at a shared edge.

        MUTATION: a vertex at the lower of the two bins at a step edge puts the
        line below the higher bin at its edge.
        """
        rng = np.random.default_rng(11)
        for _ in range(20):
            profile = np.repeat(rng.choice([0.0, 5.0, 12.5, 40.0, 90.0], size=72), 10)
            profile[rng.integers(0, 720, size=10)] = rng.choice([3.0, 77.0], size=10)
            points = ideal.step_polyline(profile)
            xs = [p["az"] for p in points]
            ys = [p["alt"] for p in points]
            edges = np.arange(720) * 0.5
            left = np.interp(edges, xs, ys, period=360.0)
            right = np.interp(edges + 0.5, xs, ys, period=360.0)
            centre = np.interp(edges + 0.25, xs, ys, period=360.0)
            self.assertTrue(np.all(left >= profile - 1e-9))
            self.assertTrue(np.all(right >= profile - 1e-9))
            self.assertLess(np.abs(centre - profile).max(), 1e-9)


if __name__ == "__main__":
    unittest.main()
