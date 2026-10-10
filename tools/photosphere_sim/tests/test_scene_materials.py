# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Object materials and the four horizon-scanner scenes (SPEC-v2 13.4 and T05).

Three things are checked here, and each one names the mutant that has to turn
it red, because a test nobody has seen fail is not yet a test:

- ``sim.scene.load`` accepts the materials SPEC-v2 13.4 declares and refuses
  every malformed one by name, and an object with no ``material`` loads as it
  always did.
- The renderer actually paints them. A ``noise`` box has pixel spread where a
  ``flat`` box has none, a ``stripes`` box alternates its two colours along u,
  and the texel recipe in ``renderer/materials.js`` equals a second
  implementation written here from the schema text. Python truth ignores
  materials, and a test says so.
- The four scenes (``treeline``, ``backyard``, ``overcast``, ``dusk``) load and
  are built the way SPEC-v2 8.3 says, from ``c_ref = (0, 0, 1.4)``: the
  treeline's tops stay between 8 and 38 degrees at every azimuth, the backyard
  has one sector above 54 degrees, the overcast terrain is low contrast and the
  dusk wall is lit.

The browser tests skip, with the reason, only when headless Chromium cannot
launch (a page that launches and then fails to build the scene is a failure),
and the node tests skip when ``node`` is absent, as ``test_render_texture.py``
does. A skip is reported as a skip.
"""

from __future__ import annotations

import copy
import json
import math
import pathlib
import shutil
import subprocess
import tempfile
import unittest

import numpy as np

from sim.geometry import Camera, look_basis, to_camera
from sim.palette import nearest
from sim.render import ThreeRenderer
from sim.scene import MATERIAL_KINDS, Scene
from sim.scene import load as load_scene
from sim.truth import _lattice_grid, _octave_lattice_shape, horizon, landmark_directions

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCENES = ROOT / "scenes"

#: The camera centre every scene is built around (SPEC-v2 8.3, T05).
C_REF = np.array([0.0, 0.0, 1.4])

#: SPEC-v2 13.4's three examples, verbatim.
FLAT = {"kind": "flat"}
NOISE = {"kind": "noise", "seed": 11, "cells": 24, "octaves": 4, "mod": [0.6, 1.3]}
STRIPES = {"kind": "stripes", "count": 40, "duty": 0.5, "colour2": [60, 52, 40]}

#: A uniform sky, so that a box's pixels are the box's and nothing else's.
GREY_SKY = {
    "kind": "directional", "distance_m": 4000,
    "texture": {"kind": "value-noise", "seed": 7, "octaves": 4, "cells": 16,
                "grey": [170, 170],
                "stripes": {"count": 0, "tilt_deg": 23, "grey": 170, "width_deg": 1.5}},
}

NODE_TIMEOUT_S = 120


def luma(colour) -> float:
    """Rec. 601 luma of an 8-bit colour, as the frames' own luma is read."""
    return 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2]


def scene_dict(objects, background=GREY_SKY) -> dict:
    return {"schema": 1, "name": "t", "seed": 1, "background": background,
            "objects": objects}


def write_scene(directory: pathlib.Path, data: dict, name: str = "t.json") -> pathlib.Path:
    path = directory / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def box(material=None, **extra) -> dict:
    obj = {"id": "b", "kind": "box", "min": [0, 5, 0], "max": [1, 5.2, 2],
           "colour": [150, 130, 100]}
    obj.update(extra)
    if material is not None:
        obj["material"] = material
    return obj


class MaterialValidation(unittest.TestCase):
    """``sim.scene.load`` and the optional ``material`` field.

    Mutant: ``_check_material`` replaced by ``pass``. Every bad case below then
    loads, and the matching ``assertRaises`` fails.
    """

    def load(self, objects):
        with tempfile.TemporaryDirectory() as tmp:
            return load_scene(write_scene(pathlib.Path(tmp), scene_dict(objects)))

    def refused(self, objects, *words):
        with self.assertRaises(ValueError) as caught:
            self.load(objects)
        text = str(caught.exception)
        self.assertIn("'b'", text, "the error names the object")
        for word in words:
            self.assertIn(word, text)

    def test_the_declared_materials_load(self):
        for material in (FLAT, NOISE, STRIPES):
            with self.subTest(kind=material["kind"]):
                scene = self.load([box(material)])
                self.assertEqual(scene.objects[0]["material"], material)

    def test_an_object_without_a_material_is_untouched(self):
        scene = self.load([box()])
        self.assertNotIn("material", scene.objects[0])
        # and so is every chart yard object: the file carries no material at all
        for obj in load_scene(SCENES / "chartyard.json").objects:
            self.assertNotIn("material", obj)

    def test_a_plane_may_only_be_flat(self):
        plane = {"id": "b", "kind": "plane", "z": 0, "colour": [60, 55, 45]}
        self.load([dict(plane, material=FLAT)])
        self.refused([dict(plane, material=NOISE)], "plane")
        self.refused([dict(plane, material=STRIPES)], "plane")

    def test_every_kind_has_a_listed_key_set(self):
        self.assertEqual(set(MATERIAL_KINDS), {"flat", "noise", "stripes"})

    def test_a_material_that_is_not_an_object_or_has_no_known_kind_is_refused(self):
        self.refused([box("noise")], "object")
        self.refused([box([NOISE])], "object")
        self.refused([box({})], "kind")
        self.refused([box({"kind": "marble"})], "marble")
        self.refused([box({"kind": 3})], "kind")

    def test_a_missing_key_is_refused_by_name(self):
        for material in (NOISE, STRIPES):
            for key in material:
                if key == "kind":
                    continue
                with self.subTest(kind=material["kind"], missing=key):
                    short = {k: v for k, v in material.items() if k != key}
                    self.refused([box(short)], key)

    def test_an_unknown_key_is_refused_so_a_typo_is_not_a_default(self):
        self.refused([box(dict(FLAT, tint=[1, 2, 3]))], "tint")
        self.refused([box(dict(NOISE, octave=4))], "octave")
        self.refused([box(dict(STRIPES, colour=[1, 2, 3]))], "colour")

    def test_noise_fields_are_checked(self):
        bad = {
            "seed": [-1, 2 ** 32, 1.5, True, "7", None],
            "cells": [1, 0, -4, 2.5, True, "24"],
            "octaves": [0, -1, 1.5, True, "4"],
            "mod": [[1.3], [0.6, 1.3, 2.0], [1.3, 0.6], [-0.1, 1.0], [0.6, "1.3"],
                    [0.6, float("nan")], [0.6, float("inf")], [True, 1.0], "0.6,1.3", 1.3],
        }
        for key, values in bad.items():
            for value in values:
                with self.subTest(field=key, value=value):
                    self.refused([box(dict(NOISE, **{key: value}))], key)

    def test_noise_limits_are_inclusive_at_the_good_edge(self):
        for fields in ({"seed": 0}, {"seed": 2 ** 32 - 1}, {"cells": 2}, {"octaves": 1},
                       {"mod": [0.0, 0.0]}, {"mod": [1.0, 1.0]}, {"mod": [0, 2]},
                       {"cells": 512, "octaves": 4}):
            with self.subTest(**fields):
                self.load([box(dict(NOISE, **fields))])

    def test_a_lattice_that_is_too_large_is_refused(self):
        self.refused([box(dict(NOISE, cells=1024, octaves=4))], "columns")
        self.refused([box(dict(NOISE, cells=24, octaves=12))], "columns")

    def test_stripes_fields_are_checked(self):
        bad = {
            "count": [0, -1, 129, 2.5, True, "40"],
            "duty": [0, 1, -0.5, 1.5, "0.5", True, float("nan"), None],
            "colour2": [[60, 52], [60, 52, 40, 1], [60, 52, 256], [-1, 0, 0], [60, 52, 40.5],
                        [60, 52, True], "60,52,40", None],
        }
        for key, values in bad.items():
            for value in values:
                with self.subTest(field=key, value=value):
                    self.refused([box(dict(STRIPES, **{key: value}))], key)

    def test_stripes_limits_are_inclusive_at_the_good_edge(self):
        for fields in ({"count": 1}, {"count": 128}, {"duty": 0.001}, {"duty": 0.999},
                       {"colour2": [0, 0, 0]}, {"colour2": [255, 255, 255]}):
            with self.subTest(**fields):
                self.load([box(dict(STRIPES, **fields))])

    def test_the_chart_yard_loads_as_before(self):
        # The loader change must not move a byte of what the chart yard loads as.
        scene = load_scene(SCENES / "chartyard.json")
        self.assertEqual([o["id"] for o in scene.objects],
                         ["ground", "wall-east", "roof-south", "pole-near", "pole-far",
                          "trunk", "canopy", "hill"])
        self.assertEqual(len(scene.landmarks), 46)


# --------------------------------------------------------------------------
# The renderer paints them
# --------------------------------------------------------------------------

#: A 480 x 640 portrait camera, 60 degrees across the short axis (fx = 415.69),
#: aimed level at the north: boxes 6 m away on the north side face it square.
CAM = Camera(480, 640, 60.0)
LEVEL_NORTH = look_basis(0.0, 0.0)


def face_roi(east_lo, east_hi, north, z_lo, z_hi, margin=8):
    """The pixel rectangle inside a north-facing box face, ``(x0, x1, y0, y1)``.

    The face is square to the camera, so it projects to a rectangle; the
    margin keeps the edges, where the sky blends in, out of the measurement.
    """
    corners = [(e, north, z) for e in (east_lo, east_hi) for z in (z_lo, z_hi)]
    cam = [to_camera(LEVEL_NORTH, np.asarray(c) - C_REF) for c in corners]
    uv = CAM.project(np.asarray(cam))
    return (int(math.ceil(uv[:, 0].min() + margin)), int(math.floor(uv[:, 0].max() - margin)),
            int(math.ceil(uv[:, 1].min() + margin)), int(math.floor(uv[:, 1].max() - margin)))


def three_boxes(flat_material=FLAT, noise_material=NOISE, stripes_material=None,
                stripes_count=8) -> list:
    """A flat, a noise and a stripes box side by side, 6 m north of the camera."""
    stripes = stripes_material or {"kind": "stripes", "count": stripes_count, "duty": 0.5,
                                   "colour2": [60, 52, 40]}
    ground = {"id": "ground", "kind": "plane", "z": 0, "colour": [60, 55, 45]}
    flat = {"id": "flat", "kind": "box", "min": [-3.2, 6.0, 0.2], "max": [-1.2, 6.2, 2.6],
            "colour": [150, 130, 100]}
    if flat_material is not None:
        flat["material"] = flat_material
    noise = {"id": "noise", "kind": "box", "min": [-1.0, 6.0, 0.2], "max": [1.0, 6.2, 2.6],
             "colour": [150, 130, 100]}
    if noise_material is not None:
        noise["material"] = noise_material
    strip = {"id": "stripes", "kind": "box", "min": [1.2, 6.0, 0.2], "max": [3.2, 6.2, 2.6],
             "colour": [172, 150, 118], "material": stripes}
    return [ground, flat, noise, strip]


ROI_FLAT = face_roi(-3.2, -1.2, 6.0, 0.2, 2.6)
ROI_NOISE = face_roi(-1.0, 1.0, 6.0, 0.2, 2.6)
ROI_STRIPES = face_roi(1.2, 3.2, 6.0, 0.2, 2.6)


def crop(img, roi):
    x0, x1, y0, y1 = roi
    return img[y0:y1 + 1, x0:x1 + 1].astype(np.float64)


def luma_of(pixels) -> np.ndarray:
    return 0.299 * pixels[..., 0] + 0.587 * pixels[..., 1] + 0.114 * pixels[..., 2]


def open_renderer(scene: Scene) -> ThreeRenderer:
    """A loaded renderer, or a skip when headless Chromium cannot launch.

    Only a browser that cannot start is a skip. A page that starts and then
    fails to build the scene (a script error in ``render.js`` or
    ``materials.js``, a texture the recipe cannot make) is the very thing
    these tests exist to catch, so that error propagates and the test fails.
    ``test_render_cardinal.py`` skips on any exception; a mutant in the
    renderer would pass those quietly, which is why this does not.
    """
    try:
        return ThreeRenderer(scene, CAM)
    except ImportError as error:
        raise unittest.SkipTest(f"playwright is not installed: {error!r}")
    except Exception as error:  # noqa: BLE001 - classified below, never swallowed
        text = str(error)
        if "BrowserType.launch" in text or "Executable doesn't exist" in text:
            raise unittest.SkipTest(f"headless Chromium unavailable: {error!r}")
        raise


def render_frame(objects, background=GREY_SKY) -> np.ndarray:
    """One frame of the level northward view of a scene of these objects."""
    with tempfile.TemporaryDirectory() as tmp:
        scene = load_scene(write_scene(pathlib.Path(tmp), scene_dict(objects, background)))
    with open_renderer(scene) as renderer:
        renderer.take_console_errors()
        img = renderer.render(C_REF, LEVEL_NORTH)
        errors = renderer.take_console_errors()
    if errors:
        raise RuntimeError(f"the page logged errors while drawing: {errors}")
    return img


class RenderedMaterials(unittest.TestCase):
    """The pixels a material produces, measured inside a face.

    Mutant (the named one): ``objectMaterial`` in ``renderer/render.js`` made to
    return ``basicMaterial(obj.colour)`` whatever the material says. The noise
    box's spread is then 0 and the stripes box is one colour, so
    ``test_a_noise_box_has_texture_and_a_flat_box_has_none`` and
    ``test_stripes_alternate_along_u`` fail.
    """

    @classmethod
    def setUpClass(cls):
        # One render serves the three measurements: the scene is the same and
        # the renderer is deterministic (test_render_cardinal pins that).
        cls.img = render_frame(three_boxes())

    def test_a_noise_box_has_texture_and_a_flat_box_has_none(self):
        flat = crop(self.img, ROI_FLAT)
        noise = crop(self.img, ROI_NOISE)
        self.assertGreater(flat.shape[0] * flat.shape[1], 10000, "the flat ROI is too small")
        self.assertGreater(noise.shape[0] * noise.shape[1], 10000, "the noise ROI is small")
        # The flat box is its own colour, exactly, in every pixel.
        self.assertEqual(flat.std(axis=(0, 1)).tolist(), [0.0, 0.0, 0.0], "luma sd 0")
        self.assertEqual(float(np.ptp(luma_of(flat))), 0.0)
        self.assertEqual(np.unique(flat.reshape(-1, 3), axis=0).tolist(), [[150, 130, 100]])
        # The noise box varies by more than 8 luma levels (SPEC-v2 8.3, T05).
        self.assertGreater(float(luma_of(noise).std()), 8.0)

    def test_noise_stays_inside_the_declared_factor_range(self):
        noise = crop(self.img, ROI_NOISE)
        lo = np.round(np.array([150, 130, 100]) * NOISE["mod"][0]) - 1
        hi = np.round(np.array([150, 130, 100]) * NOISE["mod"][1]) + 1
        self.assertTrue((noise >= lo).all(),
                        f"darker than the factor floor: {noise.min(axis=(0, 1))}")
        self.assertTrue((noise <= hi).all(),
                        f"brighter than the factor ceiling: {noise.max(axis=(0, 1))}")
        # It uses a real share of the range, so a constant factor cannot pass.
        factor = luma_of(noise) / luma([150, 130, 100])
        self.assertGreater(float(factor.max() - factor.min()), 0.2)

    def test_stripes_alternate_along_u(self):
        stripes = crop(self.img, ROI_STRIPES)
        on, off = np.array([172, 150, 118]), np.array([60, 52, 40])
        middle = stripes[stripes.shape[0] // 2]
        # Vertical stripes: every row of the face is the same row.
        self.assertLess(float(np.abs(stripes - middle[None]).max()), 3.0)
        # The row holds only the two colours and the blend between them.
        near_on = np.abs(middle - on).max(axis=1) <= 6
        near_off = np.abs(middle - off).max(axis=1) <= 6
        self.assertGreater(float(near_on.mean()), 0.3)
        self.assertGreater(float(near_off.mean()), 0.3)
        self.assertAlmostEqual(float(near_on.mean()), float(near_off.mean()), delta=0.15)
        # Eight stripes across the face: eight runs of the first colour, or
        # seven when the margin trims the end of one.
        runs = int(np.count_nonzero(np.diff(near_on.astype(int)) == 1) + int(near_on[0]))
        self.assertIn(runs, (7, 8), f"{runs} runs of the first colour across the face")


class FlatIsNoMaterial(unittest.TestCase):
    """``{"kind": "flat"}`` draws the same pixels as no material at all.

    Mutant: ``hasTexture`` made to accept ``flat``. The page then builds a
    texture from a recipe that has none and either errors or draws the boxes
    in the wrong colour, so the two frames differ (or the render raises).
    """

    def test_flat_and_absent_are_byte_identical(self):
        with_flat = render_frame(three_boxes(flat_material=FLAT, noise_material=FLAT))
        without = render_frame(three_boxes(flat_material=None, noise_material=None))
        self.assertTrue((with_flat == without).all())


class PlaneGuard(unittest.TestCase):
    """``render.js`` refuses a textured plane that got past ``sim.scene``.

    Mutant: the ``plane`` guard in ``objectMaterial`` deleted. The page would
    then draw a ground texture that swims with the camera, and this test fails
    because the page no longer errors.
    """

    def test_the_page_refuses_a_noise_plane(self):
        ground = {"id": "ground", "kind": "plane", "z": 0, "colour": [60, 55, 45],
                  "material": NOISE}
        scene = Scene(name="t", seed=1, background=GREY_SKY, landmarks=[],
                      objects=[ground], surface_landmarks=[], test_obstacles=[])
        try:
            renderer = open_renderer(scene)
        except unittest.SkipTest:
            raise
        except Exception as error:  # noqa: BLE001 - the page's own message is the evidence
            self.assertIn("plane", str(error))
            return
        renderer.close()
        self.fail("a noise material on a plane was drawn")


# --------------------------------------------------------------------------
# The texel recipe, from the schema text
# --------------------------------------------------------------------------


def reference_noise(material, colour, size=256) -> np.ndarray:
    """SPEC-v2 13.4's ``noise``, written from the schema text, ``(size, size, 3)``.

    The texel factor is ``mod[0] + (mod[1] - mod[0]) * valueNoise(u, v) / 1.875``
    with the contract's lattice recipe on ``u = (x + 0.5) / size`` and
    ``v = (y + 0.5) / size``; the object colour is multiplied by it, rounded
    and clamped. ``/ 1.875`` is ``/ (2 - 0.5**(octaves - 1))`` here, the same
    number for the four octaves the scenes use.
    """
    cells, octaves, seed = material["cells"], material["octaves"], material["seed"]
    lo, hi = material["mod"]
    total = np.zeros((size, size))
    for octave in range(octaves):
        rows, columns = _octave_lattice_shape(cells, octave)
        lattice = _lattice_grid(seed, octave, rows, columns)
        u = (np.arange(size) + 0.5) / size * columns
        iu = np.floor(u)
        fu = u - iu
        i0 = iu.astype(np.int64) % columns
        i1 = (i0 + 1) % columns
        v = (np.arange(size) + 0.5) / size * (rows - 1)
        j0 = np.clip(np.floor(v).astype(np.int64), 0, rows - 2)
        fv = np.clip(v - j0, 0.0, 1.0)
        upper = (lattice[np.ix_(j0, i0)] * (1 - fu)[None, :]
                 + lattice[np.ix_(j0, i1)] * fu[None, :])
        lower = (lattice[np.ix_(j0 + 1, i0)] * (1 - fu)[None, :]
                 + lattice[np.ix_(j0 + 1, i1)] * fu[None, :])
        total += (upper * (1 - fv)[:, None] + lower * fv[:, None]) * 0.5 ** octave
    field = total / (2 - 0.5 ** (octaves - 1))
    factor = lo + (hi - lo) * field
    rgb = np.asarray(colour, dtype=np.float64)[None, None, :] * factor[:, :, None]
    return np.clip(np.floor(rgb + 0.5), 0, 255).astype(np.uint8)


def reference_stripes(material, colour, size=256) -> np.ndarray:
    """SPEC-v2 13.4's ``stripes``: the object's colour where ``fract(u * count) < duty``."""
    t = (np.arange(size) + 0.5) / size * material["count"]
    on = (t - np.floor(t)) < material["duty"]
    row = np.where(on[:, None], np.asarray(colour)[None, :],
                   np.asarray(material["colour2"])[None, :])
    return np.broadcast_to(row.astype(np.uint8)[None, :, :], (size, size, 3)).copy()


DUMP = """\
import fs from 'node:fs';
import { materialTextureRGBA } from %(module)s;
const [, , spec, out] = process.argv;
const { material, colour } = JSON.parse(fs.readFileSync(spec, 'utf8'));
const data = materialTextureRGBA(material, colour);
fs.writeFileSync(out, Buffer.from(data.buffer, data.byteOffset, data.byteLength));
"""


class MaterialTexels(unittest.TestCase):
    """``renderer/materials.js`` against the schema text, texel for texel.

    Mutants: the noise factor's ``lo +`` dropped, the stripes' ``duty``
    comparison reversed, or ``Math.round`` turned into ``Math.floor``. Each
    changes thousands of texels, and the comparison below counts every one.
    (Dropping ``clampByte`` is not a mutant: the texture is a
    ``Uint8ClampedArray``, which clamps on assignment anyway.)
    """

    def run_node(self, material, colour):
        node = shutil.which("node")
        if node is None:
            raise unittest.SkipTest(
                "node is not on PATH, so the JavaScript half of the material recipe "
                "cannot be run; install Node 24 to check it")
        module = json.dumps((ROOT / "renderer" / "materials.js").as_uri())
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            script = tmp / "dump_material.mjs"
            script.write_text(DUMP % {"module": module}, encoding="utf-8")
            spec = tmp / "spec.json"
            spec.write_text(json.dumps({"material": material, "colour": colour}),
                            encoding="utf-8")
            out = tmp / "texture.raw"
            done = subprocess.run([node, str(script), str(spec), str(out)], cwd=str(ROOT),
                                  capture_output=True, text=True, timeout=NODE_TIMEOUT_S)
            self.assertEqual(done.returncode, 0,
                             f"materials.js failed:\n{done.stdout}\n{done.stderr}")
            raw = out.read_bytes()
        self.assertEqual(len(raw), 256 * 256 * 4, "a 256 x 256 RGBA texture")
        rgba = np.frombuffer(raw, dtype=np.uint8).reshape(256, 256, 4)
        self.assertTrue((rgba[..., 3] == 255).all(), "every texel is opaque")
        return rgba[..., :3]

    def assertTexelsEqual(self, javascript, python):
        differing = int((javascript != python).any(axis=2).sum())
        if differing:
            ys, xs = np.nonzero((javascript != python).any(axis=2))
            first = [(int(x), int(y), tuple(python[y, x]), tuple(javascript[y, x]))
                     for x, y in zip(xs[:5], ys[:5])]
            self.fail(f"{differing} of 65536 texels differ; "
                      f"first (x, y, python, javascript): {first}")

    def test_noise_matches_the_recipe(self):
        for material, colour in (
                (NOISE, [150, 130, 100]),
                (dict(NOISE, seed=4000000000, cells=24, octaves=3, mod=[0.0, 2.0]),
                 [40, 70, 34]),
                (dict(NOISE, seed=1, cells=2, octaves=1, mod=[0.9, 1.1]), [92, 104, 88]),
                (dict(NOISE, seed=5, cells=3, octaves=2, mod=[1.0, 1.8]), [200, 180, 160])):
            with self.subTest(material=material, colour=colour):
                javascript = self.run_node(material, colour)
                self.assertTexelsEqual(javascript, reference_noise(material, colour))

    def test_noise_clamps_rather_than_wraps(self):
        material, colour = dict(NOISE, mod=[1.0, 1.8]), [200, 180, 160]
        javascript = self.run_node(material, colour)
        self.assertEqual(int(javascript.max()), 255, "the brightest texels clamp at 255")
        self.assertGreaterEqual(int(javascript.min()), 160, "and none wrapped round to dark")

    def test_a_constant_factor_gives_the_object_colour(self):
        javascript = self.run_node(dict(NOISE, mod=[1.0, 1.0]), [150, 130, 100])
        self.assertEqual(np.unique(javascript.reshape(-1, 3), axis=0).tolist(),
                         [[150, 130, 100]])

    def test_stripes_match_the_recipe(self):
        for material, colour in (
                (STRIPES, [172, 150, 118]),
                (dict(STRIPES, count=1, duty=0.25), [10, 20, 30]),
                (dict(STRIPES, count=128, duty=0.5, colour2=[255, 0, 255]), [0, 255, 0]),
                (dict(STRIPES, count=7, duty=0.9), [200, 190, 170])):
            with self.subTest(material=material, colour=colour):
                javascript = self.run_node(material, colour)
                self.assertTexelsEqual(javascript, reference_stripes(material, colour))

    def test_stripes_hold_the_duty_fraction(self):
        javascript = self.run_node(dict(STRIPES, count=16, duty=0.25), [172, 150, 118])
        share = float((javascript[0] == np.array([172, 150, 118])).all(axis=1).mean())
        self.assertAlmostEqual(share, 0.25, delta=0.01)


# --------------------------------------------------------------------------
# Python truth ignores materials
# --------------------------------------------------------------------------


class TruthIgnoresMaterials(unittest.TestCase):
    """Geometry is the same with and without a material, so truth is too.

    Mutant: ``sim.truth`` reading ``material`` (it must not be touched, so this
    is a guard rather than a mutation target): any change to a horizon or a
    landmark's observability fails the equality.
    """

    def test_horizon_and_landmarks_do_not_see_materials(self):
        scene = load_scene(SCENES / "backyard.json")
        stripped = copy.deepcopy(scene)
        for obj in stripped.objects:
            obj.pop("material", None)
        materials = [o for o in scene.objects if "material" in o]
        self.assertGreater(len(materials), 10, "the backyard carries materials")
        self.assertEqual(horizon(scene, C_REF, bins=180, alt_step=0.5),
                         horizon(stripped, C_REF, bins=180, alt_step=0.5))
        self.assertEqual(landmark_directions(scene, C_REF),
                         landmark_directions(stripped, C_REF))


# --------------------------------------------------------------------------
# The four scenes
# --------------------------------------------------------------------------

NAMES = ("treeline", "backyard", "overcast", "dusk")


def corner_colours(obj):
    """Every colour a textured object can show: its own, and the modulated ends."""
    colours = [obj["colour"]]
    material = obj.get("material") or {}
    if material.get("kind") == "noise":
        for factor in material["mod"]:
            colours.append([min(255, round(c * factor)) for c in obj["colour"]])
    if material.get("kind") == "stripes":
        colours.append(material["colour2"])
    return colours


def circular_runs(mask) -> int:
    """How many separate runs of True a circular boolean array holds."""
    mask = np.asarray(mask, dtype=bool)
    if mask.all():
        return 1
    return int(np.count_nonzero(mask & ~np.roll(mask, 1)))


class Scenes(unittest.TestCase):
    """Each scene loads and is built the way SPEC-v2 8.3 (T05) says."""

    @classmethod
    def setUpClass(cls):
        cls.scenes = {name: load_scene(SCENES / f"{name}.json") for name in NAMES}
        # 0.5 degree bins and 0.1 degree altitude steps: 720 x 1001 rays a
        # scene, a few seconds, and fine enough for bounds that are degrees
        # wide. The full-resolution run is in the report.
        cls.truth = {name: horizon(cls.scenes[name], C_REF, bins=720, alt_step=0.1)
                     for name in ("treeline", "backyard", "dusk")}
        cls.horizons = {name: np.asarray(h["alt_max"]) for name, h in cls.truth.items()}

    def obj(self, name, object_id):
        return next(o for o in self.scenes[name].objects if o["id"] == object_id)

    def test_each_scene_loads_under_its_own_name(self):
        for name, scene in self.scenes.items():
            with self.subTest(name):
                self.assertEqual(scene.name, name)
                self.assertEqual(scene.background["kind"], "directional")
                self.assertGreaterEqual(len(scene.objects), 5)
                self.assertEqual(scene.objects[0]["kind"], "plane", "the ground comes first")

    def test_object_colours_are_dull_and_never_mistakable_for_a_landmark(self):
        # The chart yard's invariant (test_scene.py), held for every colour a
        # material can show: modulated, striped or flat.
        for name, scene in self.scenes.items():
            for obj in scene.objects:
                for colour in corner_colours(obj):
                    with self.subTest(scene=name, object=obj["id"], colour=colour):
                        self.assertLess(max(colour) - min(colour), 60, "channel spread")
                        self.assertGreaterEqual(nearest(tuple(colour))[1], 40.0,
                                                "palette distance")

    def test_no_two_background_discs_touch_and_all_are_palette_discs(self):
        from sim.geometry import angle_between, sky_vector
        for name, scene in self.scenes.items():
            lms = scene.landmarks
            with self.subTest(name):
                self.assertGreaterEqual(len(lms), 6)
                self.assertEqual(len({lm["id"] for lm in lms}), len(lms))
                self.assertEqual(len({lm["palette"] for lm in lms}), len(lms),
                                 "palette indices differ")
                for i, a in enumerate(lms):
                    for b in lms[i + 1:]:
                        arc = angle_between(sky_vector(a["az"], a["alt"]),
                                            sky_vector(b["az"], b["alt"]))
                        gap = arc - a["radius_deg"] - b["radius_deg"]
                        self.assertGreater(gap, 5.0, f'{a["id"]} and {b["id"]} are too close')

    # -- treeline ------------------------------------------------------------

    def test_treeline_tops_stay_between_8_and_38_degrees_at_every_azimuth(self):
        alt = self.horizons["treeline"]
        self.assertEqual(alt.size, 720)
        self.assertGreaterEqual(float(alt.min()), 8.0, f"lowest top {alt.min():.2f}")
        self.assertLessEqual(float(alt.max()), 38.0, f"highest top {alt.max():.2f}")
        # ... and it is a skyline, not a flat line: tops differ by 20 degrees.
        self.assertGreater(float(alt.max() - alt.min()), 20.0)

    def test_treeline_is_fully_observable_from_a_ring_at_pitch_22_98(self):
        # Ring 1 at pitch 22.98 reaches 53.96 degrees (4.4); the Tall threshold
        # is 53.29, and a Measured bin wants its boundary 6 degrees under the
        # top of the footprint. Every top is below both.
        alt = self.horizons["treeline"]
        self.assertLess(float(alt.max()) + 6.0, 53.96)
        # The discs sit above the trees, so every one is observable.
        lms = landmark_directions(self.scenes["treeline"], C_REF)
        self.assertTrue(all(lm["observable"] for lm in lms))
        self.assertGreater(min(lm["alt"] for lm in lms), float(alt.max()) + 6.0)

    def test_treeline_trees_have_noise_materials_and_the_sky_is_brighter(self):
        scene = self.scenes["treeline"]
        canopies = [o for o in scene.objects if o["id"].startswith("canopy-")]
        trunks = [o for o in scene.objects if o["id"].startswith("trunk-")]
        self.assertGreaterEqual(len(canopies), 12)
        self.assertEqual(len(trunks), len(canopies))
        for obj in canopies + trunks:
            self.assertEqual(obj.get("material", {}).get("kind"), "noise", obj["id"])
        self.assertEqual(len({o["material"]["seed"] for o in canopies}), len(canopies),
                         "every tree has its own foliage, so none repeats")
        sky_floor = scene.background["texture"]["grey"][0]
        for obj in scene.objects[1:]:
            for colour in corner_colours(obj):
                self.assertLess(luma(colour), sky_floor - 12,
                                f"{obj['id']} stands against the sky")

    # -- backyard ------------------------------------------------------------

    def test_backyard_has_one_sector_above_54_degrees_reaching_65(self):
        alt = self.horizons["backyard"]
        above = alt > 54.0
        self.assertTrue(above.any(), "a sector above 54 degrees")
        self.assertEqual(circular_runs(above), 1, "one sector, not several")
        self.assertGreater(float(above.sum()) * 0.5, 20.0, "a sector, not a spike")
        self.assertGreaterEqual(float(alt.max()), 64.5)
        self.assertLessEqual(float(alt.max()), 66.0)
        # Tall (above 53.29 at the S25's ring 1) is that sector and no other.
        self.assertEqual(circular_runs(alt > 53.29), 1)
        # The tall tree hides some of the discs behind it, and not all of them.
        discs = landmark_directions(self.scenes["backyard"], C_REF)
        observable = [lm["observable"] for lm in discs]
        self.assertTrue(any(observable))
        self.assertFalse(all(observable))

    def test_backyard_near_field_is_where_the_brief_puts_it(self):
        def horizontal(point):
            return math.hypot(point[0] - C_REF[0], point[1] - C_REF[1])

        wall, eave = self.obj("backyard", "house-wall"), self.obj("backyard", "eave")
        self.assertEqual(wall["min"][0], 3.0, "the wall's face is 3 m east of the camera")
        self.assertEqual(eave["min"][2], wall["max"][2], "the eave sits on the wall")
        self.assertLess(eave["min"][0], wall["min"][0], "and overhangs toward the camera")

        fence = self.obj("backyard", "fence")
        self.assertEqual(fence["max"][1], -5.0, "the fence's face is 5 m south")
        self.assertEqual(fence["max"][2], 1.8, "and 1.8 m high")
        self.assertEqual(fence["material"]["kind"], "stripes")
        self.assertEqual((fence["material"]["count"], fence["material"]["duty"]), (40, 0.5))

        pole = self.obj("backyard", "pole")
        self.assertAlmostEqual(2 * pole["radius"], 0.2, places=9, msg="0.2 m wide")
        self.assertAlmostEqual(horizontal(pole["base"]), 8.0, delta=0.02)

        canopies = [o for o in self.scenes["backyard"].objects
                    if o["id"].startswith("canopy-")]
        self.assertGreaterEqual(len(canopies), 10)
        for tree in canopies:
            self.assertTrue(15.0 <= horizontal(tree["centre"]) <= 40.0, tree["id"])

    def test_backyard_declares_the_obstacles_a_scanner_must_find(self):
        scene = self.scenes["backyard"]
        self.assertEqual([o["id"] for o in scene.test_obstacles],
                         ["house-wall", "eave", "pole", "tall-canopy"])
        found = {o["id"]: o for o in self.truth["backyard"]["obstacles"]}
        for name, obstacle in found.items():
            self.assertIsNotNone(obstacle["az_from"], f"{name} is never the first thing hit")
        self.assertAlmostEqual(found["pole"]["alt_max"], 35.3, delta=0.3)
        self.assertGreater(found["tall-canopy"]["alt_max"], 64.0)

    # -- overcast ------------------------------------------------------------

    def test_overcast_sky_is_flat_grey_without_stripes(self):
        texture = self.scenes["overcast"].background["texture"]
        self.assertEqual(texture["grey"], [115, 125])
        self.assertEqual(texture["stripes"]["count"], 0, "no stripes")

    def test_overcast_terrain_is_low_contrast_in_a_mix_of_degrees(self):
        scene = self.scenes["overcast"]
        sky = float(np.mean(scene.background["texture"]["grey"]))
        contrast = {}
        for obj in scene.objects:
            contrast[obj["id"]] = max(abs(luma(c) - sky) for c in corner_colours(obj))
        for name, value in contrast.items():
            self.assertLessEqual(value, 35.0, f"{name} contrasts {value:.1f} against the sky")
        # Three noise sigmas at the frames' sigma of 2 is 6 levels: some terrain
        # is under it (an unseen boundary), some is above it (a seen one).
        base = {o["id"]: abs(luma(o["colour"]) - sky) for o in scene.objects}
        self.assertLess(base["ridge-far"], 6.0)
        self.assertGreater(base["hill-east"], 6.0)
        self.assertGreater(base["canopy-00"], 12.0)

    # -- dusk ----------------------------------------------------------------

    def test_dusk_sky_and_terrain_are_dark_and_the_wall_is_lit_at_6_m(self):
        scene = self.scenes["dusk"]
        texture = scene.background["texture"]
        self.assertEqual(texture["grey"], [8, 20])
        stripes = texture["stripes"]
        self.assertTrue(stripes["count"] == 0 or stripes["grey"] <= 20,
                        "no stripe brighter than the sky")
        wall = self.obj("dusk", "lit-wall")
        for channel, expected in zip(wall["colour"], (200, 190, 170)):
            self.assertLessEqual(abs(channel - expected), 10)
        self.assertEqual(wall["min"][0], 6.0, "the wall's face is 6 m east of the camera")
        for obj in scene.objects:
            if obj["id"] == "lit-wall":
                continue
            for colour in corner_colours(obj):
                self.assertLessEqual(luma(colour), 40.0, f"{obj['id']} is dark terrain")
        self.assertGreater(luma(wall["colour"]), 150.0)

    def test_dusk_wall_is_the_first_thing_hit_across_80_degrees_at_18_degrees(self):
        # 3.4 m high and 6 m out from a camera at 1.4 m: atan(2.0 / 6) = 18.4.
        wall = next(o for o in self.truth["dusk"]["obstacles"] if o["id"] == "lit-wall")
        self.assertEqual((wall["az_from"], wall["az_to"]), (50.0, 130.0))
        self.assertAlmostEqual(wall["alt_max"], 18.4, delta=0.2)

    def test_dusk_terrain_ranges_from_seen_to_unseen_against_the_sky(self):
        scene = self.scenes["dusk"]
        sky = float(np.mean(scene.background["texture"]["grey"]))
        canopies = [o for o in scene.objects if o["id"].startswith("canopy-")]
        seen = [o for o in canopies if abs(luma(o["colour"]) - sky) >= 6.0]
        unseen = [o for o in canopies if abs(luma(o["colour"]) - sky) < 6.0]
        self.assertGreaterEqual(len(seen), 3)
        self.assertGreaterEqual(len(unseen), 3)


if __name__ == "__main__":
    unittest.main()
