"""The Three.js renderer against hand-specified views of the chart yard.

The renderer is the one part of the simulator that is not analytic, so it is
scored against the analytic part rather than against itself: for six cardinal
views this module predicts where every observable background disc must land
with :meth:`sim.geometry.Camera.project` and then measures where it actually
landed in the rendered frame. A renderer with a mirrored azimuth, a flipped
row order, a sign error in the camera basis or a half-texel offset in the
background texture fails here; a renderer that merely looks plausible does
not pass.

Three things the tests hold the renderer to beyond geometry, because they are
what the later stages read out of the frames:

- Colour is exact. Every material is unlit and colour management is off, so a
  disc's interior must arrive as its palette colour to within a rounding step,
  not as a tone-mapped approximation of it.
- The frame is the right way up and the right way round. Predicted positions
  would also match a frame that was flipped in both axes at once, so the
  ground/sky and the east-of-north checks pin the two axes separately.
- Rendering is deterministic: the same pose twice gives identical bytes, which
  is what makes a case directory's frame hash mean anything.

``landmarks.json`` and :func:`sim.truth.landmark_directions` carry each
landmark's ``colour`` rather than its palette index, so the colour a blob is
hunted by is ``tuple(lm["colour"])``; :data:`sim.palette.PALETTE` is used only
to assert that colour is a palette colour at all.

Skips, with the reason, when Chromium cannot launch. It never passes quietly:
a skip is reported as a skip.
"""

from __future__ import annotations

import pathlib
import unittest
from unittest import mock

import numpy as np

from sim import render as render_module
from sim.geometry import Camera, look_basis, sky_vector, to_camera, wrap_deg
from sim.palette import PALETTE
from sim.render import ThreeRenderer
from sim.scene import load as load_scene
from sim.truth import landmark_directions

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The still route's camera centre, which is every case's ``c_ref``:
#: ``pivot + [0, radius_m, height_m]`` for the chart yard's routes.
C_REF = np.array([0.0, 0.75, 1.4])

#: The positive cases' camera: 480 x 640 portrait, 60 degrees across the short
#: axis, so ``fx = fy = 415.69`` and one pixel is 0.125 degrees at the centre.
CAM = Camera(480, 640, 60.0)

#: Four level views a quarter turn apart, one nearly at the zenith, and one
#: off every axis at once so that no assertion can pass on symmetry alone.
VIEWS = [(0.0, 0.0), (90.0, 0.0), (180.0, 0.0), (270.0, 0.0), (0.0, 89.0), (200.0, 30.0)]

#: How many landmark positions the six views together must score. The chart
#: yard hides 12 of its 46 background discs from ``c_ref`` behind the hill
#: ridge, the east wall and the roof, and a level view sees the low rings
#: through only a 60 degree window, so the six views between them check 28.
#: The floor is on the total rather than on each view because looking east at
#: the horizon leaves exactly one disc unoccluded and in frame: a per-view
#: floor of three, which an earlier draft of this test carried, cannot be met
#: by this scene at all. Every view must still check at least one, so a view
#: that quietly stopped rendering anything cannot pass.
MINIMUM_TOTAL_CHECKED = 24

#: How far inside the frame a predicted centre must be before a blob is
#: demanded: a disc of 1 degree radius is about 8 pixels across its radius, so
#: a centre nearer than this to an edge may be clipped and its centroid pulled.
EDGE_MARGIN_PX = 12.0

#: The largest per-channel difference at which a pixel counts as part of a
#: landmark's blob. The palette's own colours differ by at least 127 on some
#: channel and the scene's greys are never within 40 of any of them, so this
#: window cannot confuse one landmark with another or with the background.
BLOB_TOLERANCE = 40

#: Half-width of the box around a predicted position that a blob is measured
#: in. The chart yard's 46 discs are drawn from 24 palette colours, so every
#: colour appears twice, and a centroid taken over the whole frame would
#: silently average two discs into a point between them wherever a view held
#: both. The widest blob the six views produce reaches 11.1 pixels from its
#: predicted centre, so this leaves better than a factor of two before the
#: window could clip a correctly rendered disc.
BLOB_WINDOW_PX = 24.0


def colour_of(landmark: dict) -> tuple:
    """A landmark's colour, as ``landmarks.json`` carries it."""
    colour = tuple(int(c) for c in landmark["colour"])
    assert colour in PALETTE, f"{landmark['id']} is not a palette colour"
    return colour


def blob_centroid(img: np.ndarray, colour, near) -> tuple:
    """Where a landmark's colour actually is, near where it was predicted.

    Returns ``(centroid, strays)``: the centroid of the pixels within
    :data:`BLOB_TOLERANCE` of ``colour`` and :data:`BLOB_WINDOW_PX` of
    ``near``, or ``None`` if there are none, and the number of pixels of that
    colour elsewhere in the frame.

    The centroid is in the contract's pixel coordinates, where pixel
    ``(i, j)`` has its centre at ``(i + 0.5, j + 0.5)``, so it is directly
    comparable with :meth:`Camera.project`.

    ``strays`` is one number that covers both ways the window could lie: a
    second disc of the same colour somewhere else in the view, and this disc
    spilling outside the window. Either shows up as a non-zero count, so the
    callers assert it is zero rather than trusting the window.
    """
    diff = np.abs(img.astype(np.int16) - np.asarray(colour, dtype=np.int16))
    mask = diff.max(axis=2) <= BLOB_TOLERANCE
    rows = np.arange(img.shape[0])[:, None] + 0.5
    columns = np.arange(img.shape[1])[None, :] + 0.5
    window = ((np.abs(columns - near[0]) <= BLOB_WINDOW_PX)
              & (np.abs(rows - near[1]) <= BLOB_WINDOW_PX))
    strays = int((mask & ~window).sum())
    mask = mask & window
    if not mask.any():
        return None, strays
    y, x = np.nonzero(mask)
    return np.array([x.mean() + 0.5, y.mean() + 0.5]), strays


def predicted_pixel(basis, az: float, alt: float) -> np.ndarray:
    """Where the pinhole camera says a sky direction lands, or NaNs."""
    d = to_camera(basis, sky_vector(az, alt))
    return CAM.project(d[None, :])[0]


def _inside(uv: np.ndarray) -> bool:
    if np.isnan(uv).any():
        return False
    return (EDGE_MARGIN_PX <= uv[0] <= CAM.width - EDGE_MARGIN_PX
            and EDGE_MARGIN_PX <= uv[1] <= CAM.height - EDGE_MARGIN_PX)


class Cardinal(unittest.TestCase):
    """One renderer and one scene, shared by every view."""

    @classmethod
    def setUpClass(cls):
        cls.scene = load_scene(ROOT / "scenes" / "chartyard.json")
        cls.landmarks = landmark_directions(cls.scene, C_REF)
        try:
            cls.renderer = ThreeRenderer(cls.scene, CAM)
        except Exception as error:  # noqa: BLE001 - the reason is the point
            raise unittest.SkipTest(f"headless Chromium unavailable: {error!r}")
        cls.renderer.__enter__()

    @classmethod
    def tearDownClass(cls):
        renderer = getattr(cls, "renderer", None)
        if renderer is not None:
            renderer.__exit__(None, None, None)

    def background(self):
        return [lm for lm in self.landmarks
                if lm["observable"] and lm["kind"] == "background"]

    def test_landmarks_land_where_the_pinhole_says(self):
        total = 0
        for az, alt in VIEWS:
            basis = look_basis(az, alt)
            img = self.renderer.render(C_REF, basis)
            checked = 0
            for lm in self.background():
                uv = predicted_pixel(basis, lm["az"], lm["alt"])
                if not _inside(uv):
                    continue
                centroid, strays = blob_centroid(img, colour_of(lm), uv)
                self.assertIsNotNone(centroid, f"{lm['id']} missing at view {(az, alt)}")
                self.assertEqual(strays, 0,
                                 f"{lm['id']} at view {(az, alt)}: {strays} pixels of its "
                                 f"colour more than {BLOB_WINDOW_PX} px from {uv}")
                offset = float(np.hypot(*(centroid - uv)))
                self.assertLess(offset, 1.5,
                                f"{lm['id']} at view {(az, alt)}: {centroid} vs {uv}")
                checked += 1
            self.assertGreaterEqual(checked, 1, f"view {(az, alt)} checked no landmark")
            total += checked
        self.assertGreaterEqual(total, MINIMUM_TOTAL_CHECKED,
                                f"the six views checked only {total} landmark positions")

    def test_top_and_bottom_and_left_and_right_are_not_swapped(self):
        basis = look_basis(0.0, 0.0)
        img = self.renderer.render(C_REF, basis)
        # A level view has the ground plane's dark grey along the bottom and
        # the background's 70..170 grey along the top.
        self.assertLess(img[600:].mean(), img[:40].mean())
        # ... and a landmark east of north sits right of centre.
        east_of_north = [lm for lm in self.background()
                         if lm["id"].startswith("R1") and 5.0 < wrap_deg(lm["az"]) < 25.0]
        self.assertTrue(east_of_north, "the chart yard has no ring-1 disc just east of north")
        lm = east_of_north[0]
        uv = predicted_pixel(basis, lm["az"], lm["alt"])
        centroid, strays = blob_centroid(img, colour_of(lm), uv)
        self.assertIsNotNone(centroid, lm["id"])
        self.assertEqual(strays, 0, lm["id"])
        self.assertGreater(centroid[0], 240.0)

    def test_colours_are_exact(self):
        # The level southward view, not the northward one: the hill ridge
        # stands 11 degrees high across the north, so every ring-0 disc there
        # is occluded and the renderer is right not to draw one.
        basis = look_basis(180.0, 0.0)
        img = self.renderer.render(C_REF, basis)
        inside = [lm for lm in self.background()
                  if _inside(predicted_pixel(basis, lm["az"], lm["alt"]))]
        self.assertTrue([lm for lm in inside if lm["id"].startswith("R0")],
                        "no ring-0 disc is inside the level southward view")
        for lm in inside:
            uv = predicted_pixel(basis, lm["az"], lm["alt"])
            centroid, _ = blob_centroid(img, colour_of(lm), uv)
            self.assertIsNotNone(centroid, lm["id"])
            pixel = img[int(centroid[1]), int(centroid[0])].astype(int)
            worst = int(np.abs(pixel - np.asarray(colour_of(lm), dtype=int)).max())
            self.assertLessEqual(worst, 3, f"{lm['id']}: {tuple(pixel)} vs {colour_of(lm)}")

    def test_rendering_is_deterministic(self):
        basis = look_basis(45.0, 10.0)
        first = self.renderer.render(C_REF, basis)
        second = self.renderer.render(C_REF, basis)
        self.assertTrue((first == second).all())

    def test_a_frame_is_the_cameras_size_and_neither_blank_nor_saturated(self):
        img = self.renderer.render(C_REF, look_basis(200.0, 30.0))
        self.assertEqual(img.shape, (CAM.height, CAM.width, 3))
        self.assertEqual(img.dtype, np.uint8)
        # A rendered chart-yard frame is neither a cleared buffer nor a wash.
        self.assertGreater(img.std(), 5.0)
        self.assertGreater(int(np.unique(img).size), 20)

    def test_it_reports_the_versions_the_manifest_records(self):
        version = self.renderer.version
        self.assertIn("three", version)
        self.assertIn("chromium", version)
        self.assertTrue(str(version["three"]).startswith("0."), version)
        self.assertTrue(str(version["chromium"])[0].isdigit(), version)


class _Frame:
    """The two fields :class:`sim.render.CaseRenderer` reads off a frame."""

    def __init__(self, frame_id: str):
        self.frame_id = frame_id
        self.position = C_REF
        self.basis = look_basis(0.0, 0.0)


class _FakeRenderer:
    """A ThreeRenderer that hands back whatever the test wants it to."""

    version = {"three": "0.0.0", "chromium": "0.0.0.0"}

    def __init__(self, images, errors):
        self._images = list(images)
        self._errors = list(errors)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def take_console_errors(self):
        return self._errors.pop(0) if self._errors else []

    def render(self, position, basis):
        return self._images.pop(0)


class Refusals(unittest.TestCase):
    """What ``CaseRenderer`` refuses to write into a case directory.

    A thousand frames go past unwatched, so the two ways WebGL fails quietly
    -- a readback of zeros, and an error three logs and carries on from --
    have to stop the build rather than be hashed into a manifest. These run
    without a browser, on purpose: a guard that only fires when the graphics
    stack is already broken is a guard nothing ever exercises.
    """

    def _build(self, images, errors=()):
        frames = [_Frame(f"f{i:06d}") for i in range(len(images))]
        fake = _FakeRenderer(images, errors)
        with mock.patch.object(render_module, "ThreeRenderer", lambda *a, **k: fake):
            return list(render_module.CaseRenderer()(None, CAM, frames))

    def _picture(self, value=None):
        if value is not None:
            return np.full((CAM.height, CAM.width, 3), value, dtype=np.uint8)
        rng = np.random.default_rng(1)
        return rng.integers(0, 256, (CAM.height, CAM.width, 3), dtype=np.uint8)

    def test_a_good_frame_passes_through_unchanged(self):
        image = self._picture()
        out = self._build([image])
        self.assertEqual(len(out), 1)
        self.assertTrue((out[0] == image).all())

    def test_a_frame_of_one_value_is_refused_by_name(self):
        for value in (0, 255, 128):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError) as caught:
                    self._build([self._picture(), self._picture(value)])
                self.assertIn("f000001", str(caught.exception))

    def test_an_error_the_page_logged_is_refused_by_name(self):
        with self.assertRaises(RuntimeError) as caught:
            self._build([self._picture(), self._picture()],
                        errors=[[], [], ["error: WebGL context lost"]])
        self.assertIn("f000001", str(caught.exception))
        self.assertIn("context lost", str(caught.exception))

    def test_an_error_before_the_first_frame_is_not_blamed_on_it(self):
        # The page logs while it is loading; those are drained, not charged to
        # frame zero, or every case would refuse to build.
        out = self._build([self._picture()], errors=[["error: from loading"]])
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
