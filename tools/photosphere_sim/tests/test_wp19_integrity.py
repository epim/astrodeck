# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The simulator's integrity: what grades the scanner must be able to fail (#1118).

Five claims the simulator made and nothing kept, one class per claim:

- ``ShippedSeeds`` (#901): a case's declared seed reaches every noise source of
  every shipped case that carries a realism block, and the cases that do not
  are exactly the five legacy chart-yard cases, whose seed-0 draw is kept on
  purpose.
- ``CommittedFixturesCarryTheGyro`` and ``CommittedFixturesAreFresh`` (#953,
  #975): the committed fixtures hold a motion channel, and they are what the
  case definitions build today, byte for byte.
- ``TruthSceneBytes`` (#953): the scene copy in ``truth/`` has one set of bytes on
  every checkout, so a rebuild is the same on Windows and Linux.
- ``RendererGate`` (#954): a renderer that cannot start fails the suite; a skip
  needs the named environment reason.
- ``FocalGate`` (#1062): a scan that closed by gyro is graded against the true
  focal, a scan that closed by image against the effective one (S49).

MUTATIONS, the named one first:

- ignore the case seed in ``sensors.realism_readings`` (build the
  ``SeedSequence`` from 0): ``ShippedSeeds.test_a_realism_case_draws_different_noise_for_another_seed``;
- restore ``except Exception: raise unittest.SkipTest`` in
  ``test_render_cardinal.Cardinal.setUpClass``:
  ``RendererGate.test_a_renderer_that_fails_to_load_fails_the_cardinal_suite``;
- grade ``_score_focal`` against the effective focal whatever the loop method:
  ``FocalGate.test_a_gyro_closed_scan_is_graded_against_the_true_focal``;
- drop the motion channel from a fixture's ``observations.jsonl``:
  ``CommittedFixturesCarryTheGyro`` and ``CommittedFixturesAreFresh``.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from sim import cases, render as render_module, score, sensors, trajectory
from tests import renderer_gate
from tests import test_render_cardinal
from tests.test_t30f_rulings import CAM, READOUT_MS, camera_dict, pan_frames

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = ROOT / "cases"
FIXTURES = ROOT / "fixtures"

#: The five cases recorded before the realism block existed. They draw their
#: gyro noise from ``default_rng(0)`` whatever their seed, so that the recorded
#: hashes the baseline document quotes stay valid, until the old scanner and
#: these cases are retired.
LEGACY_CASES = frozenset({
    "chartyard-still-60", "chartyard-arc075-60", "chartyard-arc075-70",
    "chartyard-shortpan-60", "chartyard-shortpan-70"})

#: Files of a case directory that depend on the pixels of the frames. Nothing
#: else a case writes does, so a flat-grey rebuild must reproduce the rest.
PIXEL_DEPENDENT = frozenset({"truth/visibility.json"})

CRLF = bytes([13, 10])
LF = bytes([10])


def case_definitions() -> dict:
    return {path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(CASES.glob("*.json"))}


def read_jsonl(path: pathlib.Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def files_under(root: pathlib.Path) -> dict:
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*")) if p.is_file()}


class ShippedSeeds(unittest.TestCase):
    """#901 on the cases that ship, not on a synthetic block."""

    def test_the_cases_without_a_realism_block_are_exactly_the_legacy_ones(self):
        legacy = {name for name, definition in case_definitions().items()
                  if definition.get("realism") is None}
        self.assertEqual(legacy, set(LEGACY_CASES))

    def test_a_legacy_case_declares_the_gyro_noise_it_draws(self):
        for name in sorted(LEGACY_CASES):
            with self.subTest(case=name):
                self.assertGreater(case_definitions()[name]["gyro_noise_deg_s"], 0.0)

    def test_a_realism_case_draws_different_noise_for_another_seed(self):
        """MUTATION (named): ``np.random.SeedSequence(0)`` in ``realism_readings``.
        Two seeds then give one draw in every stream of every case, and this
        names the first case and stream that did not move."""
        for name, definition in case_definitions().items():
            if definition.get("realism") is None:
                continue
            with self.subTest(case=name):
                route = json.loads((ROOT / "routes" / f"{definition['route']}.json")
                                   .read_text(encoding="utf-8"))
                spec = sensors.resolve_realism(definition["realism"])
                traj = trajectory.build(route, definition["fps"], seed=definition["seed"])
                one = sensors.realism_readings(traj, spec, definition["seed"])
                two = sensors.realism_readings(traj, spec, definition["seed"] + 1)
                self.assertEqual(one, sensors.realism_readings(traj, spec, definition["seed"]))
                streams = [
                    ("relative", spec["streams"] == "both",
                     lambda r: r["kind"] == "orientation" and not r["absolute"]),
                    ("absolute", True,
                     lambda r: r["kind"] == "orientation" and r["absolute"]),
                    ("motion", spec["gyro"], lambda r: r["kind"] == "motion")]
                for label, declared, keep in streams:
                    if not declared:
                        continue
                    first = [r for r in one if keep(r)]
                    self.assertTrue(first, f"{name} declares a {label} stream and has none")
                    self.assertFalse(
                        first == [r for r in two if keep(r)],
                        f"{name}: the {label} stream ignores the case seed")


class CommittedFixturesCarryTheGyro(unittest.TestCase):
    """#953: a fixture without a motion channel runs the replay with no gyro witness."""

    def test_every_fixture_holds_a_motion_channel_the_scanner_can_read(self):
        for case_dir in sorted(p for p in FIXTURES.iterdir() if (p / "manifest.json").is_file()):
            with self.subTest(case=case_dir.name):
                records = read_jsonl(case_dir / "input" / "observations.jsonl")
                motion = [r for r in records if r["kind"] == "motion"]
                self.assertGreater(len(motion), 0, "no motion record: the gyro path is not exercised")
                rates = [r["rate"] for r in motion]
                self.assertTrue(all(isinstance(v, float) for rate in rates for v in rate.values()))
                self.assertTrue(any(any(v != 0.0 for v in rate.values()) for rate in rates),
                                "every motion record is an exact zero")


    def test_the_treeline_fixture_models_a_hold_as_rounded_noise(self):
        """#975: a still phone in Chromium reads exact zeros on most samples
        because the rate is rounded to 0.1 deg/s, and on the rest a non-zero
        triple. The committed realism fixture is the replay case that gives the
        scanner's zero handling that stream: every rate a multiple of 0.1, and
        over each hold mostly exact zero triples but not all of them."""
        case_dir = FIXTURES / "treeline-panshort-41"
        motion = [r for r in read_jsonl(case_dir / "input" / "observations.jsonl")
                  if r["kind"] == "motion"]
        for record in motion:
            for value in record["rate"].values():
                self.assertAlmostEqual(value / 0.1, round(value / 0.1), places=6)
        holds = json.loads((case_dir / "truth" / "holds.json").read_text(encoding="utf-8"))
        self.assertTrue(holds)
        for hold in holds:
            with self.subTest(hold=hold["index"]):
                window = [r for r in motion if hold["from_ms"] <= r["t_event_ms"] <= hold["to_ms"]]
                zeros = [r for r in window if all(v == 0.0 for v in r["rate"].values())]
                self.assertGreater(len(window), 30)
                share = len(zeros) / len(window)
                self.assertGreaterEqual(share, 0.90)
                self.assertLess(share, 1.0)


class CommittedFixturesAreFresh(unittest.TestCase):
    """#953: a committed artefact must match the generator that claims to produce it.

    The frames are Chromium's and cannot be rebuilt here; ``test_manifest_hashes``
    pins them. Everything else a case writes is arithmetic on the case
    definition, so a flat-grey rebuild reproduces it byte for byte, and a case
    definition that moved on (a new noise knob, a new sensor channel) turns a
    fixture stale and red.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.built = {}
        for case_dir in sorted(p for p in FIXTURES.iterdir() if (p / "manifest.json").is_file()):
            definition = json.loads((CASES / f"{case_dir.name}.json").read_text(encoding="utf-8"))
            cls.built[case_dir.name] = cases.build_case(
                definition, pathlib.Path(cls.tmp.name), cases.flat_renderer)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_file_that_does_not_depend_on_the_pixels_is_what_the_definition_builds(self):
        for name, fresh in self.built.items():
            committed = FIXTURES / name
            with self.subTest(case=name):
                committed_files = files_under(committed)
                fresh_files = files_under(fresh)
                self.assertEqual(sorted(committed_files), sorted(fresh_files))
                for relative, path in committed_files.items():
                    if (relative in PIXEL_DEPENDENT or relative == "manifest.json"
                            or relative.startswith("input/frames/")):
                        continue
                    self.assertEqual(path.read_bytes(), fresh_files[relative].read_bytes(),
                                     f"{name}/{relative} is stale against cases/{name}.json")

    def test_the_manifest_says_what_the_definition_says(self):
        for name, fresh in self.built.items():
            with self.subTest(case=name):
                committed = json.loads((FIXTURES / name / "manifest.json").read_text(encoding="utf-8"))
                rebuilt = json.loads((fresh / "manifest.json").read_text(encoding="utf-8"))
                for key in sorted(set(committed) - {"hashes", "versions"}):
                    self.assertEqual(committed[key], rebuilt[key], f"{name}: manifest {key}")
                self.assertEqual(sorted(committed), sorted(rebuilt))
                self.assertEqual(committed["hashes"]["observations"],
                                 rebuilt["hashes"]["observations"])


class TruthSceneBytes(unittest.TestCase):
    """The truth copy of the scene is the same bytes on every checkout.

    ``scenes/*.json`` follow the checkout's line endings (CRLF under
    ``core.autocrlf`` on Windows), and ``truth/scene.json`` is hashed into the
    manifest, so a copy of the file as it lay on disk made a fixture rebuild
    differ between machines (the committed ``treeline-panshort-41`` carried
    CRLF and the two shortpan fixtures LF).

    MUTATION: write ``scene_path.read_bytes()`` unchanged in ``build_case``.
    """

    def test_a_scene_checked_out_with_crlf_is_copied_with_lf(self):
        definition = case_definitions()["chartyard-shortpan-60"]
        source = (ROOT / "scenes" / f"{definition['scene']}.json").read_bytes()
        canonical = source.replace(CRLF, LF)
        with tempfile.TemporaryDirectory() as scenes, tempfile.TemporaryDirectory() as out:
            (pathlib.Path(scenes) / f"{definition['scene']}.json").write_bytes(
                canonical.replace(LF, CRLF))
            with mock.patch.object(cases, "SCENES_DIR", pathlib.Path(scenes)):
                built = cases.build_case(definition, pathlib.Path(out), cases.flat_renderer)
            self.assertEqual((built / "truth" / "scene.json").read_bytes(), canonical)


class _NoBrowser(Exception):
    """What Playwright raises when the browser executable is absent."""


class RendererGate(unittest.TestCase):
    """#954: only a browser that cannot launch is a skip."""

    @staticmethod
    def _failing(error):
        def factory(*args, **kwargs):
            raise error
        return mock.patch.object(render_module, "ThreeRenderer", factory)

    def test_a_script_error_in_the_renderer_is_not_a_skip(self):
        error = RuntimeError("renderer/render.js failed to load: Error: module fails at load")
        with self._failing(error), self.assertRaises(RuntimeError):
            renderer_gate.open_renderer(None, CAM)

    def test_a_browser_that_cannot_launch_is_a_named_skip(self):
        for text in ("BrowserType.launch: Executable doesn't exist at C:\\x",
                     "Executable doesn't exist at /root/.cache/ms-playwright"):
            with self.subTest(text=text):
                with self._failing(_NoBrowser(text)), self.assertRaises(unittest.SkipTest) as caught:
                    renderer_gate.open_renderer(None, CAM)
                self.assertIn("headless Chromium unavailable", str(caught.exception))

    def test_a_missing_playwright_is_a_named_skip(self):
        with self._failing(ImportError("No module named 'playwright'")), \
                self.assertRaises(unittest.SkipTest) as caught:
            renderer_gate.open_renderer(None, CAM)
        self.assertIn("playwright is not installed", str(caught.exception))

    def test_a_renderer_that_fails_to_load_fails_the_cardinal_suite(self):
        """MUTATION: ``except Exception: raise unittest.SkipTest(...)`` back in
        ``Cardinal.setUpClass``. The suite then reports a skip and this fails."""
        error = RuntimeError("renderer/render.js failed to load: Error: mutant")
        with self._failing(error), self.assertRaises(RuntimeError):
            try:
                test_render_cardinal.Cardinal.setUpClass()
            except unittest.SkipTest as skip:
                self.fail(f"the suite skipped instead of failing: {skip}")


class FocalGate(unittest.TestCase):
    """#1062: S49 grades against the effective focal where a ring closed by image."""

    SHORT_PX = float(min(CAM.width, CAM.height))

    def setUp(self):
        self.frames = pan_frames(20.0)
        self.ids = [f["frame_id"] for f in self.frames]
        self.effective = score._effective_fx(camera_dict(), READOUT_MS, self.frames, self.ids)

    def _focal(self, f_px, loop):
        diagnostics = {"focal": {"f_norm": f_px / self.SHORT_PX, "state": "closed"},
                       "keyframes": [{"frame_id": i} for i in self.ids]}
        if loop is not None:
            diagnostics["loop"] = loop
        return score._score_focal(diagnostics, camera_dict(), READOUT_MS, self.frames)

    def test_the_rolling_shutter_moves_the_effective_focal_off_the_true_one(self):
        self.assertGreater(abs(self.effective - CAM.fx) / CAM.fx, 0.010)

    def test_a_gyro_closed_scan_is_graded_against_the_true_focal(self):
        """The fast case: closed by gyro, 0.24 percent from the true fx, which is
        1.2 percent from the effective one.

        MUTATION: grade every scan against ``effective``: ``error_fraction``
        reads 1.2 percent and the gate fails the scan for a scorer reason."""
        block = self._focal(CAM.fx * 1.0024, {"closed": True, "method": "gyro"})
        self.assertAlmostEqual(block["error_fraction"], 0.0024, places=6)
        self.assertEqual(block["graded_against"], "truth_fx")
        self.assertLess(block["error_fraction"], score.GATE_FOCAL_ERR)

    def test_an_image_closed_scan_is_graded_against_the_effective_focal(self):
        block = self._focal(self.effective, {"closed": True, "method": "image"})
        self.assertLess(block["error_fraction"], 1e-9)
        self.assertEqual(block["graded_against"], "effective_fx")
        self.assertGreater(block["error_fraction_true_fx"], 0.010)

    def test_a_scan_with_no_loop_block_is_graded_against_the_true_focal(self):
        block = self._focal(CAM.fx, None)
        self.assertEqual(block["error_fraction"], 0.0)
        self.assertEqual(block["graded_against"], "truth_fx")

    def test_both_errors_stay_reported_whatever_the_loop_method(self):
        block = self._focal(CAM.fx * 1.0024, {"closed": False, "method": "gyro"})
        self.assertEqual(block["truth_fx"], CAM.fx)
        self.assertAlmostEqual(block["effective_fx"], self.effective)
        self.assertAlmostEqual(block["error_fraction_true_fx"], 0.0024, places=6)


if __name__ == "__main__":
    unittest.main()
