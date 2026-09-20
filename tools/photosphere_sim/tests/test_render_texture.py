"""The JavaScript background texture against the Python one, texel for texel.

The renderer repaints CONTRACT.md's background from the same declarative
recipe :mod:`sim.truth` evaluates, in another language, and the contract says
the two must agree. Until this test existed only the lattice hash was checked:
the noise interpolation, the stripe great circles and the landmark discs could
all have drifted without anything failing, and the discs do not touch the hash
at all. So this compares the whole image.

It runs the recipe through ``node``, which is the only way to run the
JavaScript one honestly. ``renderer/dump_texture.mjs`` imports
``renderer/texture.js`` and nothing else -- no Three.js, no DOM, no
``node_modules`` -- so the only environmental reason this test cannot run is
that ``node`` is absent, which it says out loud. Anything else (a non-zero
exit, a short file, one differing byte) is a failure, because it means the two
implementations of the recipe have diverged.

Why 512 x 256 rather than the 4096 x 2048 the renderer actually paints:

- Every octave is exercised with real interpolation. The chart yard's finest
  octave has ``cells * 2**3 = 128`` lattice columns, so at 512 pixels each
  lattice cell spans four pixels and every octave is sampled at fractional
  weights rather than on its lattice points.
- The azimuth wrap is exercised: the last column samples ``u = 127.875`` in
  the finest octave, so ``i1`` wraps to column 0 in all four.
- The altitude edge is exercised: the last row lands in the final lattice row
  of every octave, which is where the clamp lives.
- The discs are exercised: 1465 texels get a palette colour at this size, and
  all 24 palette colours appear.
- It is a different size from the production texture, so a 4096 or a 2048
  hard-coded anywhere in either implementation shows up here as a mismatch.

The production size is covered too, end to end, by
``tests/test_render_cardinal.py``: those landmark centroids can only land
within 1.5 px of the pinhole prediction if the 4096 x 2048 texture is right.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

from sim.scene import load as load_scene
from sim.truth import background_texture

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: See the module docstring for why this size is enough.
WIDTH = 512
HEIGHT = 256

#: How long each Node script gets. Painting 512 x 256 takes a few
#: milliseconds; this is a hang guard, not a budget.
NODE_TIMEOUT_S = 120


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest(
            "node is not on PATH, so the JavaScript half of the texture recipe "
            "cannot be run; install Node 24 to check it")
    return node


def _run(node: str, script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [node, str(ROOT / "renderer" / script), *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=NODE_TIMEOUT_S)


class JavaScriptTexture(unittest.TestCase):

    def test_the_two_implementations_paint_the_same_background(self):
        node = _node()
        scene_path = ROOT / "scenes" / "chartyard.json"
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "texture.raw"
            done = _run(node, "dump_texture.mjs", str(scene_path),
                        str(WIDTH), str(HEIGHT), str(out))
            self.assertEqual(done.returncode, 0,
                             f"dump_texture.mjs failed:\n{done.stdout}\n{done.stderr}")
            raw = out.read_bytes()
        self.assertEqual(len(raw), WIDTH * HEIGHT * 3,
                         "dump_texture.mjs wrote the wrong number of bytes")
        javascript = np.frombuffer(raw, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)

        python = background_texture(load_scene(scene_path), WIDTH, HEIGHT)
        difference = np.abs(python.astype(np.int16) - javascript.astype(np.int16))
        differing = int((difference.max(axis=2) > 0).sum())
        if differing:
            rows, columns = np.nonzero(difference.max(axis=2) > 0)
            first = [(int(x), int(y), tuple(python[y, x]), tuple(javascript[y, x]))
                     for x, y in zip(columns[:5], rows[:5])]
            self.fail(f"{differing} of {WIDTH * HEIGHT} texels differ, worst channel "
                      f"{int(difference.max())}; first (x, y, python, javascript): {first}")

    def test_the_sample_size_still_exercises_the_discs_and_the_stripes(self):
        # The comparison above is only worth as much as what it covers, and
        # what it covers is a property of the size, which a later edit could
        # quietly change. These are the numbers the module docstring claims.
        scene = load_scene(ROOT / "scenes" / "chartyard.json")
        image = background_texture(scene, WIDTH, HEIGHT).reshape(-1, 3)
        from sim.palette import PALETTE

        painted = np.zeros(image.shape[0], dtype=bool)
        seen = 0
        for colour in PALETTE:
            hit = (image == np.asarray(colour, dtype=np.uint8)).all(axis=1)
            seen += int(hit.any())
            painted |= hit
        self.assertEqual(seen, len(PALETTE), "not every palette colour reaches this size")
        self.assertGreater(int(painted.sum()), 1000, "too few disc texels at this size")
        stripe = int((image == 170).all(axis=1).sum())
        self.assertGreater(stripe, 10000, "too few stripe texels at this size")
        self.assertGreater(len(np.unique(image[:, 0])), 50, "the noise is not being sampled")


class PinnedIntegers(unittest.TestCase):

    def test_the_node_hash_test_passes(self):
        # renderer/hash.test.mjs is the authority on the lattice integers
        # CONTRACT.md pins. Running it from here means the Python suite fails
        # when they drift, without a second copy of the numbers to maintain.
        node = _node()
        done = _run(node, "hash.test.mjs")
        self.assertEqual(done.returncode, 0,
                         f"node renderer/hash.test.mjs failed:\n{done.stdout}\n{done.stderr}")
        self.assertIn("all checks passed", done.stdout)


if __name__ == "__main__":
    unittest.main()
