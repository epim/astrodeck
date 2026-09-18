"""Ground truth for the chart yard, all from c_ref = [0, 0.75, 1.4].

Every expectation is arithmetic on the scene's own declared numbers, written
out so that a failure names the geometry it disagrees with. Where the plan's
sketch of an expectation disagreed with the scene's numbers the arithmetic
wins and a comment says where the sketch went wrong.
"""
import dataclasses, math, pathlib, time, unittest

import numpy as np

from sim.geometry import angle_between, sky_vector
from sim.palette import PALETTE
from sim.scene import load
from sim.truth import (BACKGROUND_SAMPLE, background_texture, horizon,
                       ideal_panorama, intersect, landmark_directions, lattice_hash)

SCENES = pathlib.Path(__file__).resolve().parents[1] / "scenes"
SCENE = load(SCENES / "chartyard.json")
C_REF = [0.0, 0.75, 1.4]

# Horizontal distances from c_ref, used in several expectations.
D_POLE_NEAR = math.hypot(-1.2 - 0.0, 1.8 - 0.75)   # 1.594522 m
D_POLE_FAR = math.hypot(8.0 - 0.0, 20.0 - 0.75)    # 20.846109 m

_FINE = {}


def fine_horizon():
    """The full-resolution horizon (the declared defaults: 3600 bins, 0.05 deg).

    Computed once for the whole module: it is 3600 x 2001 rays, and the pole
    tests need 0.1 degree bins to see a 0.27 degree obstacle at all.
    """
    if "h" not in _FINE:
        start = time.perf_counter()
        _FINE["h"] = horizon(SCENE, C_REF)
        _FINE["seconds"] = time.perf_counter() - start
    return _FINE["h"]


def alt_max_at(h, az):
    """The bin containing azimuth `az`, and its highest obstructed altitude."""
    step = 360.0 / h["bins"]
    return h["alt_max"][int((az % 360.0) / step)]


def o_width(obs):
    """An obstacle's declared azimuth span in degrees, wrapping through north.

    `az_from`/`az_to` are bin edges, so the span is the sky the obstacle was
    actually found in rather than the distance between two bin centres.
    """
    width = obs["az_to"] - obs["az_from"]
    return width if width > 0.0 else width + 360.0


def open_azimuths(h):
    """(az, alt_max) for the bins where nothing but the ground is in front.

    The chart yard has exactly one such window, west-southwest: the roof's
    western silhouette limit is the corner (-1.5, -4.75) from c_ref, az 253.3,
    and the hill's western limit is the corner (-300, 119.25), az 291.7. The
    window is sampled from 256 to 289 to clear both by more than a bin.
    """
    step = 360.0 / h["bins"]
    return [((b + 0.5) * step, h["alt_max"][b]) for b in range(h["bins"])
            if 256.0 <= (b + 0.5) * step <= 289.0]


def lm_palette(lm_id):
    """The palette index the scene declares for a landmark id."""
    for lm in list(SCENE.landmarks) + list(SCENE.surface_landmarks):
        if lm["id"] == lm_id:
            return lm["palette"]
    raise KeyError(lm_id)


def cast(*angles, scene=None):
    """`intersect` over a handful of (az, alt) directions, in degrees."""
    dirs = np.array([sky_vector(az, alt) for az, alt in angles])
    return intersect(scene or SCENE, C_REF, dirs)


def disc_mask(scene, width, height):
    """True where a pixel centre lies inside a background landmark disc.

    Computed over the whole grid straight from CONTRACT.md's pixel mapping and
    with no windowing, so it also checks the bounding boxes `background_texture`
    paints its discs through.
    """
    az = np.radians((np.arange(width) + 0.5) / width * 360.0)
    alt = np.radians(90.0 - (np.arange(height) + 0.5) / height * 180.0)
    saz, caz = np.sin(az)[None, :], np.cos(az)[None, :]
    salt, calt = np.sin(alt)[:, None], np.cos(alt)[:, None]
    mask = np.zeros((height, width), dtype=bool)
    for lm in scene.landmarks:
        c = sky_vector(lm["az"], lm["alt"])
        dot = calt * (saz * c[0] + caz * c[1]) + salt * c[2]
        mask |= dot > math.cos(math.radians(lm["radius_deg"]))
    return mask


def recipe_grey(texture, az, alt):
    """One pixel's noise grey, read scalar-wise straight from CONTRACT.md.

    A second, deliberately plodding implementation of the texture recipe: it
    walks the octaves, reads four lattice corners through `lattice_hash` and
    interpolates by hand, so it shares nothing with the vectorised separable
    form under test. Task 4's JavaScript is a third implementation of the same
    paragraph, and this is the shape it has to take.
    """
    total = 0.0
    for octave in range(texture["octaves"]):
        columns = texture["cells"] * 2 ** octave
        rows = columns // 2 + 1
        u = az / 360.0 * columns
        fu = u - math.floor(u)
        i0 = int(math.floor(u)) % columns
        i1 = (i0 + 1) % columns
        v = (90.0 - alt) / 180.0 * (rows - 1)
        j0 = min(max(int(math.floor(v)), 0), rows - 2)
        fv = min(max(v - j0, 0.0), 1.0)
        corner = [[lattice_hash(texture["seed"], octave, i, j) / 4294967296.0
                   for i in (i0, i1)] for j in (j0, j0 + 1)]
        upper = corner[0][0] * (1.0 - fu) + corner[0][1] * fu
        lower = corner[1][0] * (1.0 - fu) + corner[1][1] * fu
        total += (upper * (1.0 - fv) + lower * fv) * 0.5 ** octave
    grey0, grey1 = texture["grey"]
    return math.floor(grey0 + total / 1.875 * (grey1 - grey0))


def on_a_stripe(stripes, az, alt):
    """True when a direction is within half a stripe width of a great circle."""
    direction = sky_vector(az, alt)
    tilt = math.radians(stripes["tilt_deg"])
    for k in range(stripes["count"]):
        a = math.radians(k * 360.0 / stripes["count"])
        pole = np.array([math.cos(a) * math.cos(tilt),
                         -math.sin(a) * math.cos(tilt),
                         -math.sin(tilt)])
        if abs(float(direction @ pole)) < math.sin(math.radians(stripes["width_deg"] / 2)):
            return True
    return False


def in_a_disc(scene, az, alt):
    """True when a direction lies inside any background landmark disc."""
    direction = sky_vector(az, alt)
    return any(angle_between(direction, sky_vector(lm["az"], lm["alt"]))
               < lm["radius_deg"] for lm in scene.landmarks)


class RayCasts(unittest.TestCase):
    def test_the_ground_is_the_first_hit_below_the_horizontal(self):
        hit = cast((0.0, -5.0))
        self.assertEqual(hit.object_id[0], "ground")
        # c_ref is 1.4 m up, so a 5 degree dip reaches z = 0 at 1.4 / sin 5.
        self.assertAlmostEqual(hit.t[0], 1.4 / math.sin(math.radians(5.0)), places=9)
        self.assertEqual(tuple(hit.colour[0]), (60, 55, 45))
        self.assertEqual(hit.landmark_id[0], "")

    def test_the_background_is_grey_infinitely_far_and_unnamed(self):
        # az 17 alt 20 clears the hill (10.7 degrees there) and is 5 degrees
        # above ring 1, so it is bare background rather than a disc.
        hit = cast((17.0, 20.0))
        self.assertEqual(hit.object_id[0], "background")
        self.assertEqual(hit.t[0], math.inf)
        self.assertEqual(hit.landmark_id[0], "")
        r, g, b = (int(c) for c in hit.colour[0])
        self.assertEqual(r, g, "noise and stripes are greys")
        self.assertEqual(g, b, "noise and stripes are greys")
        self.assertTrue(70 <= r <= 170, r)

    def test_the_roof_has_two_faces_and_the_underside_ends_where_it_should(self):
        # Due south the roof is a slab 0.45 m away, z 2.9 to 3.1. Below
        # atan(1.5 / 0.45) = 73.30 degrees a rising ray passes under the near
        # edge and strikes the underside; above it, the near vertical face.
        hit = cast((180.0, 73.0), (180.0, 74.0))
        self.assertEqual(list(hit.object_id), ["roof-south", "roof-south"])
        p0 = np.array(C_REF) + hit.t[0] * sky_vector(180.0, 73.0)
        p1 = np.array(C_REF) + hit.t[1] * sky_vector(180.0, 74.0)
        self.assertAlmostEqual(p0[2], 2.9, places=9)   # the underside
        self.assertAlmostEqual(p1[1], 0.3, places=9)   # the near vertical face
        self.assertEqual(tuple(hit.colour[0]), (90, 70, 60))

    def test_a_surface_landmark_is_named_only_inside_its_disc(self):
        # W1 is a 0.05 m disc at (2.5, 0.5, 1.8) on the wall's west face; a
        # point 0.5 m further north along the same face is bare wall.
        on = np.array([2.5, 0.5, 1.8]) - np.array(C_REF)
        off = np.array([2.5, 1.0, 1.8]) - np.array(C_REF)
        hit = intersect(SCENE, C_REF, np.array([on, off]))
        self.assertEqual(list(hit.object_id), ["wall-east", "wall-east"])
        self.assertEqual(hit.landmark_id[0], "W1")
        self.assertEqual(tuple(hit.colour[0]), PALETTE[3])
        self.assertEqual(hit.landmark_id[1], "")
        self.assertEqual(tuple(hit.colour[1]), (120, 100, 80))

    def test_a_background_landmark_paints_its_palette_colour(self):
        # R1K0 sits at az 17 alt 15 with a 1 degree radius; 0.5 degrees off
        # centre is still inside the disc, 1.5 degrees off is not.
        hit = cast((17.0, 15.0), (17.5, 15.0), (18.6, 15.0))
        self.assertEqual(list(hit.object_id), ["background"] * 3)
        self.assertEqual(hit.landmark_id[0], "R1K0")
        self.assertEqual(tuple(hit.colour[0]), PALETTE[8])
        self.assertEqual(hit.landmark_id[1], "R1K0")
        self.assertEqual(hit.landmark_id[2], "")

    def test_a_cylinder_is_hit_on_its_top_cap_from_above(self):
        # From c_ref every ray toward these poles rises and can only meet the
        # side, so the cap branch and its +z normal need a case of their own:
        # a trunk on its own, looked straight down on from 2 m above its top.
        marked = dataclasses.replace(
            SCENE, landmarks=[], test_obstacles=[],
            objects=[o for o in SCENE.objects if o["id"] == "trunk"],
            surface_landmarks=[{"id": "CAP", "palette": 0, "object": "trunk",
                                "centre": [6.0, 8.0, 7.0], "normal": [0, 0, 1],
                                "radius_m": 0.2}])
        hit = intersect(marked, [6.0, 8.0, 9.0], np.array([[0.0, 0.0, -1.0]]))
        self.assertEqual(hit.object_id[0], "trunk")
        self.assertAlmostEqual(hit.t[0], 2.0, places=12)
        self.assertEqual(hit.landmark_id[0], "CAP")

    def test_an_object_beyond_the_background_distance_is_background(self):
        # The background is directional and sits at distance_m, so anything
        # further away is behind it: nothing closer means background.
        far = dataclasses.replace(
            SCENE, landmarks=[], surface_landmarks=[], test_obstacles=[],
            objects=[{"id": "beyond", "kind": "sphere", "centre": [0.0, 5000.0, 1.4],
                      "radius": 100.0, "colour": [10, 20, 30]}])
        hit = intersect(far, C_REF, np.array([sky_vector(0.0, 0.0)]))
        self.assertEqual(hit.object_id[0], "background")
        self.assertEqual(hit.t[0], math.inf)
        near = dataclasses.replace(
            far, objects=[dict(far.objects[0], centre=[0.0, 3000.0, 1.4])])
        self.assertEqual(intersect(near, C_REF, np.array([sky_vector(0.0, 0.0)]))
                         .object_id[0], "beyond")

    def test_the_background_colour_is_the_declared_texture_sampled_at_4096(self):
        # The seam Task 4's renderer has to land on: truth's background colour
        # is this exact image, looked up nearest-neighbour, so the sampler's
        # row and column arithmetic has to be the generator's inverse.
        # The noise is smooth on the scale of a pixel, so most directions give
        # the same grey at 2048 as at 4096 and no comparison of colours can
        # see the resolution: the declared size is pinned on its own.
        self.assertEqual(BACKGROUND_SAMPLE, (4096, 2048))
        width, height = BACKGROUND_SAMPLE
        texture = background_texture(SCENE, width, height)
        for az, alt in [(17.0, 20.0), (270.0, 30.0), (359.99, 13.0), (300.0, 80.0)]:
            hit = cast((az, alt))
            with self.subTest((az, alt)):
                self.assertEqual(hit.object_id[0], "background")
                column = int(az / 360.0 * width)
                row = int((90.0 - alt) / 180.0 * height)
                np.testing.assert_array_equal(hit.colour[0], texture[row, column])

    def test_a_tie_goes_to_the_earlier_object_in_file_order(self):
        box = {"kind": "box", "min": [1.0, -1.0, -1.0], "max": [2.0, 1.0, 1.0]}
        tied = dataclasses.replace(
            SCENE, landmarks=[], surface_landmarks=[], test_obstacles=[],
            objects=[dict(box, id="first", colour=[10, 20, 30]),
                     dict(box, id="second", colour=[200, 190, 180])])
        hit = intersect(tied, [0.0, 0.0, 0.0], np.array([[1.0, 0.0, 0.0]]))
        self.assertEqual(hit.object_id[0], "first")
        self.assertAlmostEqual(hit.t[0], 1.0, places=12)


class Horizon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = fine_horizon()

    def test_the_full_resolution_horizon_fits_the_time_budget(self):
        self.assertEqual(self.h["bins"], 3600)
        self.assertEqual(len(self.h["alt_max"]), 3600)
        self.assertLess(_FINE["seconds"], 60.0)

    def test_open_sky_north_is_background_and_the_hill_is_where_the_arithmetic_says(self):
        # Bin 0's centre is az 0.05. The hill's near face is at y = 120,
        # 119.25 m away, and its top is z = 25, so alt = atan(23.6 / 119.25).
        # The altitude grid is 0.05 degrees, so the reported value is the last
        # grid step below that edge.
        expected = math.degrees(math.atan2(25.0 - 1.4, 120.0 - 0.75))
        self.assertAlmostEqual(self.h["alt_max"][0], expected, delta=0.06)

    def test_the_roof_edge_is_at_the_hand_computed_altitude(self):
        # Due south the roof's near edge is 0.45 m away. The plan's sketch used
        # the underside, z = 2.9 (atan(1.5 / 0.45) = 73.30 degrees), but the
        # slab is 0.2 m thick and its near vertical face keeps obstructing up
        # to the TOP near edge, z = 3.1: atan(1.7 / 0.45) = 75.17 degrees.
        expected = math.degrees(math.atan2(3.1 - 1.4, 0.75 - 0.3))
        self.assertAlmostEqual(alt_max_at(self.h, 180.0), expected, delta=0.06)
        self.assertGreater(expected, math.degrees(math.atan2(1.5, 0.45)))

    def test_the_near_pole_has_its_declared_angular_width_and_height(self):
        obs = next(o for o in self.h["obstacles"] if o["id"] == "pole-near")
        self.assertAlmostEqual(o_width(obs),
                               math.degrees(2 * math.atan2(0.03, D_POLE_NEAR)),
                               delta=0.15)
        # The top rim's nearest point is a radius closer than the axis, so the
        # silhouette reaches atan(3.1 / (d - r)), not atan(3.1 / d). The plan's
        # sketch used the axis distance and lands 0.45 degrees too low.
        self.assertAlmostEqual(obs["alt_max"],
                               math.degrees(math.atan2(4.5 - 1.4, D_POLE_NEAR - 0.03)),
                               delta=0.1)

    def test_the_far_pole_is_at_least_the_minimum_width(self):
        obs = next(o for o in self.h["obstacles"] if o["id"] == "pole-far")
        self.assertGreaterEqual(o_width(obs), 0.25)
        # Its true width is 2 * atan(0.05 / 20.846) = 0.275 degrees: three
        # 0.1 degree bins, which is why the scene declares 0.25 as the floor.
        self.assertAlmostEqual(o_width(obs),
                               math.degrees(2 * math.atan2(0.05, D_POLE_FAR)),
                               delta=0.05)
        self.assertAlmostEqual(obs["alt_max"],
                               math.degrees(math.atan2(6.0 - 1.4, D_POLE_FAR - 0.05)),
                               delta=0.1)

    def test_every_declared_test_obstacle_is_found_at_its_minimum_width(self):
        found = {o["id"]: o for o in self.h["obstacles"]}
        self.assertEqual(sorted(found), sorted(o["id"] for o in SCENE.test_obstacles))
        for declared in SCENE.test_obstacles:
            obs = found[declared["id"]]
            with self.subTest(declared["id"]):
                self.assertEqual(obs["min_width_deg"], declared["min_width_deg"])
                self.assertGreaterEqual(o_width(obs), declared["min_width_deg"])
                self.assertGreater(obs["alt_max"], 0.0)

    def test_ground_never_blocks_above_the_horizontal(self):
        window = open_azimuths(self.h)
        self.assertEqual(len(window), 330)
        self.assertTrue(all(alt < 0.0 for _, alt in window))
        # The ground is an object, so it is the highest hit there, and it is
        # hit at every altitude below 0 and none at or above it: the answer is
        # the grid step just below the horizontal.
        self.assertTrue(all(abs(alt + 0.05) < 1e-9 for _, alt in window))


class Landmarks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lms = landmark_directions(SCENE, C_REF)
        cls.by_id = {lm["id"]: lm for lm in cls.lms}

    def test_every_landmark_is_reported_once_with_its_palette_colour(self):
        # 46 background discs (five rings of eight, a cap of six) and 6 on
        # object faces.
        self.assertEqual(len(self.lms), 52)
        self.assertEqual(len(self.by_id), 52)
        for lm in self.lms:
            with self.subTest(lm["id"]):
                self.assertEqual(tuple(lm["colour"]), PALETTE[lm_palette(lm["id"])])
                self.assertIn(lm["kind"], ("background", "surface"))
                self.assertTrue(0.0 <= lm["az"] < 360.0)

    def test_background_landmarks_report_their_declared_direction(self):
        for declared in SCENE.landmarks:
            lm = self.by_id[declared["id"]]
            with self.subTest(declared["id"]):
                self.assertEqual(lm["az"], declared["az"])
                self.assertEqual(lm["alt"], declared["alt"])
                self.assertEqual(lm["kind"], "background")

    def test_surface_landmark_directions_are_the_vector_from_the_reference(self):
        w1 = self.by_id["W1"]     # (2.5, 0.5, 1.8) - c_ref = (2.5, -0.25, 0.4)
        self.assertAlmostEqual(w1["az"], math.degrees(math.atan2(2.5, -0.25)), places=9)
        self.assertAlmostEqual(w1["alt"],
                               math.degrees(math.atan2(0.4, math.hypot(2.5, 0.25))),
                               places=9)
        h1 = self.by_id["H1"]     # (10, 120, 12) - c_ref = (10, 119.25, 10.6)
        self.assertAlmostEqual(h1["az"], math.degrees(math.atan2(10.0, 119.25)), places=9)
        self.assertAlmostEqual(h1["alt"],
                               math.degrees(math.atan2(10.6, math.hypot(10.0, 119.25))),
                               places=9)
        self.assertEqual(h1["kind"], "surface")

    def test_background_landmark_hidden_by_the_roof_is_not_observable(self):
        # R3K2 is at az 169 alt 55. Rising at 55 degrees the ray reaches
        # z = 2.9 after 1.5 / tan 55 = 1.050 m of ground track, which is
        # 0.281 m SOUTH of the roof's near edge at y = 0.3: it comes up through
        # the underside. An open ring-1 disc at az 17 alt 15 is observable.
        self.assertFalse(self.by_id["R3K2"]["observable"])
        self.assertTrue(self.by_id["R1K0"]["observable"])
        # R2K0, az 34 alt 35, is 5 degrees from the canopy's centre direction
        # (az 39.6, alt 32.9), well inside its 12.9 degree radius.
        self.assertFalse(self.by_id["R2K0"]["observable"])
        # R1K1, az 69 alt 15, strikes the wall's west face 2.77 m out at
        # z = 2.12, below its 2.6 m top.
        self.assertFalse(self.by_id["R1K1"]["observable"])

    def test_the_lowest_ring_clears_the_ground_but_not_the_hill_or_the_wall(self):
        # Ring 0 sits at alt 5, above the horizontal, so the ground no longer
        # hides it; three of the eight now stand in open sky and five are
        # blocked by scenery, which is the mix the scorer needs.
        observable = {lm_id for lm_id in (f"R0K{k}" for k in range(8))
                      if self.by_id[lm_id]["observable"]}
        self.assertEqual(observable, {"R0K3", "R0K4", "R0K5"})
        # R0K0, az 0: the hill's top edge is atan(23.6 / 119.25) = 11.19
        # degrees there, so 5 is well below it.
        self.assertGreater(math.degrees(math.atan2(23.6, 119.25)), 5.0)
        # R0K1, az 52: the wall's west face is 2.5 / sin 52 = 3.17 m out, where
        # a 5 degree ray is at z = 1.4 + 3.17 tan 5 = 1.68, under its 2.6 top.
        self.assertLess(1.4 + 2.5 / math.sin(math.radians(52.0))
                        * math.tan(math.radians(5.0)), 2.6)
        # R0K3, az 153: the hill spans az 291.7 to 68.3, and a 5 degree ray
        # only reaches the roof's underside plane 1.5 / tan 5 = 17.1 m out,
        # far beyond the roof's 4.75 m southern extent.
        self.assertGreater(1.5 / math.tan(math.radians(5.0)), 4.75)
        # R0K6, az 297, is the one marginal member: the hill's edge is 5.13
        # degrees there, so it is hidden by 0.13 degrees of altitude. A change
        # to the hill or to c_ref's height will flip it.
        self.assertFalse(self.by_id["R0K6"]["observable"])

    def test_the_cap_ring_is_six_discs_that_are_all_observable(self):
        # The point of the cap: six separated markers near the zenith, where a
        # wrong correspondence looks right. Nothing in the yard reaches alt 85.
        cap = [lm for lm in self.lms if lm["id"].startswith("R5")]
        self.assertEqual([lm["id"] for lm in cap], [f"R5K{k}" for k in range(6)])
        for lm in cap:
            self.assertEqual(lm["alt"], 85.0)
            self.assertTrue(lm["observable"], lm["id"])

    def test_every_surface_landmark_is_observable_from_the_reference(self):
        for lm_id in ("W1", "W2", "RF1", "RF2", "T1", "H1"):
            self.assertTrue(self.by_id[lm_id]["observable"], lm_id)


class Panorama(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pano = ideal_panorama(SCENE, C_REF)
        cls.lms = landmark_directions(SCENE, C_REF)

    def test_the_shape_and_alpha_are_the_contract_s(self):
        self.assertEqual(self.pano.shape, (300, 1080, 4))
        self.assertEqual(self.pano.dtype, np.uint8)
        self.assertTrue((self.pano[:, :, 3] == 255).all())

    def test_the_row_and_column_mapping_matches_the_contract(self):
        # Row 0 is alt 90 (open sky), row 299 is alt -10 (the ground), and
        # column x is azimuth (x + 0.5) / 1080 * 360.
        top = intersect(SCENE, C_REF, np.array([sky_vector(0.5 / 1080 * 360.0, 90.0)]))
        self.assertEqual(top.object_id[0], "background")
        np.testing.assert_array_equal(self.pano[0, 0, :3], top.colour[0])
        self.assertEqual(tuple(self.pano[299, 0, :3]), (60, 55, 45))

    def test_ideal_panorama_shows_each_observable_landmark_at_its_direction(self):
        for lm in self.lms:
            if not lm["observable"]:
                continue
            x = int(lm["az"] / 360 * 1080)
            y = int(round((90 - lm["alt"]) / 100 * 299))
            with self.subTest(lm["id"]):
                # The sampled pixel centre is within a fifth of a degree of the
                # landmark direction, well inside every disc in this scene.
                sampled = sky_vector((x + 0.5) / 1080 * 360.0, 90.0 - y / 299 * 100.0)
                self.assertLess(angle_between(sampled, sky_vector(lm["az"], lm["alt"])),
                                0.25)
                self.assertEqual(tuple(self.pano[y, x, :3]),
                                 PALETTE[lm_palette(lm["id"])])
                self.assertEqual(tuple(self.pano[y, x, :3]), tuple(lm["colour"]))


class BackgroundTexture(unittest.TestCase):
    def test_background_texture_is_deterministic_and_in_range(self):
        a = background_texture(SCENE, 512, 256)
        b = background_texture(SCENE, 512, 256)
        self.assertEqual(a.shape, (256, 512, 3))
        self.assertTrue((a == b).all())
        # Noise maps into grey 70..150 and stripes paint 170, so every pixel
        # outside a landmark disc is a grey in [70, 170]. The discs are painted
        # last in palette colours, which reach 0 and 255 by design.
        discs = disc_mask(SCENE, 512, 256)
        sky = a[~discs]
        self.assertGreaterEqual(sky.min(), 70)
        self.assertLessEqual(sky.max(), 170)
        self.assertTrue((sky[:, 0] == sky[:, 1]).all() and (sky[:, 1] == sky[:, 2]).all())
        self.assertTrue(discs.any())
        for pixel in {tuple(int(c) for c in p) for p in a[discs]}:
            self.assertIn(pixel, PALETTE)

    def test_the_stripes_pass_through_their_declared_points(self):
        # Each of the 11 great circles passes through (az = k * 360 / 11,
        # alt = 0), so the pixel nearest that point is painted the stripe grey
        # whatever the tilt: its distance to the circle is under half a pixel,
        # and half the stripe width is 0.75 degrees.
        a = background_texture(SCENE, 512, 256)
        for k in range(11):
            az = k * 360.0 / 11.0
            x = int(az / 360.0 * 512)
            self.assertEqual(tuple(int(c) for c in a[128, x]), (170, 170, 170), az)

    def test_the_noise_matches_a_scalar_reading_of_the_recipe(self):
        # Including the two columns either side of the azimuth seam, which is
        # where a lattice that does not wrap gives itself away, and a row three
        # degrees from the zenith, where the clamped rows do.
        image = background_texture(SCENE, 512, 256)
        texture = SCENE.background["texture"]
        checked = 0
        for y, x in [(4, 0), (4, 511), (60, 100), (158, 255), (200, 7), (250, 400)]:
            az = (x + 0.5) / 512 * 360.0
            alt = 90.0 - (y + 0.5) / 256 * 180.0
            if on_a_stripe(texture["stripes"], az, alt) or in_a_disc(SCENE, az, alt):
                continue
            with self.subTest((y, x)):
                self.assertEqual(int(image[y, x, 0]), recipe_grey(texture, az, alt))
            checked += 1
        self.assertGreaterEqual(checked, 4, "too few pixels left to compare")

    def test_overlapping_discs_resolve_the_same_way_in_both_products(self):
        # No two discs in the chart yard overlap any more (issue #45), but the
        # contract still has to say what an overlap means, because a painter's
        # natural "later covers earlier" is the opposite of the tie rule: the
        # earlier disc in file order wins in intersect, so background_texture
        # paints its discs from last to first. Two discs of radius 1.5 at az 90
        # and 91, alt 40, are 0.766 degrees apart (1 degree of azimuth is
        # cos 40 of arc), and their midpoint is 0.383 from each, inside both.
        pair = dataclasses.replace(
            SCENE, surface_landmarks=[], test_obstacles=[],
            landmarks=[{"id": "A", "palette": 1, "az": 90.0, "alt": 40.0,
                        "radius_deg": 1.5},
                       {"id": "B", "palette": 2, "az": 91.0, "alt": 40.0,
                        "radius_deg": 1.5}])
        middle = sky_vector(90.0, 40.0) + sky_vector(91.0, 40.0)
        middle /= np.linalg.norm(middle)
        self.assertLess(angle_between(middle, sky_vector(91.0, 40.0)), 1.5)
        hit = intersect(pair, C_REF, middle[None, :])
        self.assertEqual(hit.object_id[0], "background")
        self.assertEqual(hit.landmark_id[0], "A")
        self.assertEqual(tuple(hit.colour[0]), PALETTE[1])
        az = math.degrees(math.atan2(middle[0], middle[1])) % 360.0
        alt = math.degrees(math.asin(middle[2]))
        # The tie rule does not depend on the resolution, so the cheap size is
        # enough: this pixel centre is under 0.6 degrees from both centres.
        width, height = 512, 256
        texture = background_texture(pair, width, height)
        column = int(az / 360.0 * width)
        row = int((90.0 - alt) / 180.0 * height)
        self.assertEqual(tuple(int(c) for c in texture[row, column]), PALETTE[1])

    def test_the_lattice_hash_is_pinned_for_the_javascript_side(self):
        # Task 4 regenerates this texture in JavaScript from the same recipe.
        # These are uint32 values of
        #   h = seed ^ (o * 0x9E3779B1) ^ (i * 0x85EBCA77) ^ (j * 0xC2B2AE3D)
        #   h ^= h >> 16; h *= 0x7FEB352D; h ^= h >> 15; h *= 0x846CA68B; h ^= h >> 16
        # with every product truncated to 32 bits, computed independently from
        # CONTRACT.md's text.
        self.assertEqual(lattice_hash(7, 0, 0, 0), 2492178918)
        self.assertEqual(lattice_hash(7, 0, 1, 0), 3390191309)
        self.assertEqual(lattice_hash(7, 0, 0, 1), 420868019)
        self.assertEqual(lattice_hash(7, 1, 5, 3), 1763849442)
        self.assertEqual(lattice_hash(7, 2, 63, 32), 4028108650)
        self.assertEqual(lattice_hash(7, 3, 127, 64), 3154280637)
        self.assertEqual(lattice_hash(0, 0, 0, 0), 0)   # the finaliser's fixed point
        self.assertAlmostEqual(lattice_hash(7, 0, 0, 0) / 4294967296.0,
                               0.5802556215785444, places=15)


if __name__ == "__main__":
    unittest.main()
