# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A committed fixture must be what the generator builds today, frames included (#1156).

``test_wp19_integrity.CommittedFixturesAreFresh`` rebuilds every file that does
not depend on the pixels, with the flat renderer. That leaves the frames and
``truth/visibility.json`` (a function of the frames) pinned only by the hashes
in ``manifest.json``, which a stale rebuild changes together with the frames.
Nothing compared the committed pixels with what the renderer draws now.

Two halves:

- ``ComparisonCatchesAStaleFixture`` runs everywhere and needs no browser. It
  feeds ``fixture_rebuild.compare_case`` a copy of a committed fixture and the
  same copy with one thing changed, and says what the comparison must do.
- ``CommittedFixturesMatchARealRebuild`` rebuilds every committed fixture with
  Three.js in headless Chromium and compares with what is committed. It skips,
  with a named reason, on a machine with no Chromium, and FAILS when the
  environment variable ``CI`` is set, so the workflow cannot go green by
  skipping.

The tolerance is in ``fixture_rebuild``: byte equal when the generator is
deterministic (it is on the machine that recorded the fixtures), otherwise two
levels per channel.

MUTATION (named): make ``fixture_rebuild.compare_case`` return ``[]``. Every
test in ``ComparisonCatchesAStaleFixture`` that expects a problem then fails.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
import unittest

import numpy as np
from PIL import Image

from sim import cases, render as render_module
from tests import fixture_rebuild, renderer_gate

ROOT = pathlib.Path(__file__).resolve().parents[1]
CASES = ROOT / "cases"
FIXTURES = ROOT / "fixtures"
SMALLEST = "treeline-panshort-41"


def fixture_dirs() -> list:
    return sorted(p for p in FIXTURES.iterdir() if (p / "manifest.json").is_file())


def first_frame(case_dir: pathlib.Path) -> pathlib.Path:
    return sorted((case_dir / "input" / "frames").glob("*.png"))[0]


def write_pixels(path: pathlib.Path, change) -> None:
    pixels = np.array(Image.open(path).convert("RGB"), dtype=np.int16)
    change(pixels)
    Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8)).save(path)


class ComparisonCatchesAStaleFixture(unittest.TestCase):
    """The committed copy is compared with a copy that is the rebuild."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fresh = pathlib.Path(cls.tmp.name) / "fresh"
        shutil.copytree(FIXTURES / SMALLEST, cls.fresh)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.stale = pathlib.Path(self.tmp.name) / "stale"
        shutil.rmtree(self.stale, ignore_errors=True)
        shutil.copytree(FIXTURES / SMALLEST, self.stale)

    def test_an_identical_case_has_no_problems(self):
        self.assertEqual(fixture_rebuild.compare_case(self.stale, self.fresh), [])

    def test_a_changed_frame_is_named(self):
        """The case the issue describes: one committed frame is not what the
        renderer draws."""
        write_pixels(first_frame(self.stale), lambda px: px.__setitem__(
            (slice(0, 10), slice(0, 10)), 255))
        problems = fixture_rebuild.compare_case(self.stale, self.fresh)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(first_frame(self.stale).name, problems[0])

    def test_a_one_level_difference_is_within_the_tolerance(self):
        write_pixels(first_frame(self.stale), lambda px: px.__iadd__(
            np.where(px > 0, -1, 0)))
        self.assertEqual(fixture_rebuild.compare_case(self.stale, self.fresh), [])

    def test_a_difference_above_the_tolerance_is_a_problem(self):
        over = fixture_rebuild.FRAME_TOLERANCE + 1
        write_pixels(first_frame(self.stale), lambda px: px.__setitem__(
            (0, 0), np.where(px[0, 0] > 127, px[0, 0] - over, px[0, 0] + over)))
        self.assertEqual(len(fixture_rebuild.compare_case(self.stale, self.fresh)), 1)

    def test_a_frame_the_rebuild_does_not_draw_is_a_problem(self):
        (first_frame(self.stale).with_name("extra.png")).write_bytes(
            first_frame(self.stale).read_bytes())
        problems = fixture_rebuild.compare_case(self.stale, self.fresh)
        self.assertTrue(any("extra.png" in p for p in problems), problems)

    def test_a_visibility_mask_that_disagrees_is_a_problem(self):
        """Same pixels, different mask: the visibility code moved and the
        committed truth did not."""
        path = self.stale / "truth" / "visibility.json"
        mask = json.loads(path.read_text(encoding="utf-8"))
        mask["visible"][0] = not mask["visible"][0]
        path.write_text(json.dumps(mask, separators=(",", ":")), encoding="utf-8", newline="")
        problems = fixture_rebuild.compare_case(self.stale, self.fresh)
        self.assertTrue(any("visibility.json" in p for p in problems), problems)

    def test_a_manifest_with_other_versions_says_so_in_the_message(self):
        write_pixels(first_frame(self.stale), lambda px: px.__setitem__(
            (slice(0, 10), slice(0, 10)), 255))
        manifest = json.loads((self.fresh / "manifest.json").read_text(encoding="utf-8"))
        manifest["versions"]["chromium"] = "0.0.0.0"
        other = pathlib.Path(self.tmp.name) / "other"
        shutil.rmtree(other, ignore_errors=True)
        shutil.copytree(self.fresh, other)
        (other / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8", newline="")
        problems = fixture_rebuild.compare_case(self.stale, other)
        self.assertTrue(any("chromium" in p and "0.0.0.0" in p for p in problems), problems)


class CommittedFixturesMatchARealRebuild(unittest.TestCase):
    """Three.js in headless Chromium draws what is committed."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.rebuilt = {}
        try:
            for case_dir in fixture_dirs():
                definition = json.loads(
                    (CASES / f"{case_dir.name}.json").read_text(encoding="utf-8"))
                cls.rebuilt[case_dir.name] = cases.build_case(
                    definition, pathlib.Path(cls.tmp.name), render_module.CaseRenderer())
        except Exception as error:  # noqa: BLE001 - classified by renderer_gate, never swallowed
            cls.tmp.cleanup()
            skip = renderer_gate.as_skip(error)
            if skip is None:
                raise
            if os.environ.get("CI"):
                raise AssertionError(f"CI must run the real rebuild, and cannot: {skip}") from error
            raise skip

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_fixture_is_what_the_renderer_draws_today(self):
        self.assertEqual(sorted(self.rebuilt), [p.name for p in fixture_dirs()])
        for name, fresh in self.rebuilt.items():
            with self.subTest(case=name):
                problems = fixture_rebuild.compare_case(FIXTURES / name, fresh)
                self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
