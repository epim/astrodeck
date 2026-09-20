"""The chart yard scene file and the landmark palette.

These tests read `scenes/chartyard.json` as an artifact and check it against
the schema in CONTRACT.md and the numbers the plan declares, so a typo in the
scene file fails here rather than surfacing as a wrong truth value later.
"""
import itertools, json, pathlib, tempfile, unittest

from sim.geometry import angle_between, sky_vector
from sim.palette import PALETTE, nearest
from sim.scene import load

SCENES = pathlib.Path(__file__).resolve().parents[1] / "scenes"
LEVELS = (0, 128, 255)

#: altitude, disc count and disc radius of each background landmark ring.
RINGS = ((5.0, 8, 1.0), (15.0, 8, 1.0), (35.0, 8, 1.0),
         (55.0, 8, 1.0), (75.0, 8, 1.0), (85.0, 6, 1.5))


def ring_az(r, k):
    """The plan's azimuth generators for background landmark ring `r`, disc `k`.

    Rings 0 to 4 carry eight discs on the irregular `(k * k * 7) % 45` spacing.
    The cap ring carries six on a plain 60 degree spacing: azimuth degrees
    shrink by `cos(alt)` on the sphere, so at alt 85 the irregular term's
    smallest gap of 17 degrees is 1.5 degrees of arc and 3 degree discs would
    overlap, while 60 degrees is 5.0 degrees of arc and they do not.
    """
    if r == 5:
        return (k * 60 + 17) % 360
    return (k * 45 + r * 17 + (k * k * 7) % 45) % 360


def ring_palette(r, k):
    """The palette index of ring `r`, disc `k`."""
    return (40 + k) % 24 if r == 5 else (r * 8 + k) % 24


class Palette(unittest.TestCase):
    def test_twenty_four_distinct_non_grey_colours(self):
        self.assertEqual(len(PALETTE), 24)
        self.assertEqual(len(set(PALETTE)), 24)
        for r, g, b in PALETTE:
            self.assertIn(r, LEVELS)
            self.assertIn(g, LEVELS)
            self.assertIn(b, LEVELS)
            self.assertFalse(r == g == b, "a grey is not a landmark colour")
        # 27 combinations of three levels less the three greys.
        self.assertEqual(set(PALETTE), {(r, g, b) for r in LEVELS for g in LEVELS
                                        for b in LEVELS if not r == g == b})

    def test_the_order_is_fixed_red_slowest_blue_fastest(self):
        self.assertEqual(PALETTE[0], (0, 0, 128))
        self.assertEqual(PALETTE[1], (0, 0, 255))
        self.assertEqual(PALETTE[2], (0, 128, 0))
        self.assertEqual(PALETTE[8], (128, 0, 0))
        self.assertEqual(PALETTE[23], (255, 255, 128))

    def test_nearest_is_the_largest_channel_difference(self):
        self.assertEqual(nearest((0, 0, 128)), (0, 0.0))
        # (10, 0, 120) differs from PALETTE[0] by 10, 0 and 8.
        self.assertEqual(nearest((10, 0, 120)), (0, 10.0))
        # A mid grey is 128 from black-ish corners and 127 from nothing nearer:
        # every non-grey palette colour has a channel at least 127 away.
        index, distance = nearest((128, 128, 128))
        self.assertEqual(distance, 127.0)
        self.assertEqual(PALETTE[index][0], 128)


class ChartYard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = SCENES / "chartyard.json"
        cls.raw = json.loads(cls.path.read_text(encoding="utf-8"))
        cls.scene = load(cls.path)

    def test_the_file_declares_schema_one_and_its_seed(self):
        self.assertEqual(self.raw["schema"], 1)
        self.assertEqual(self.raw["name"], "chartyard")
        self.assertEqual(self.scene.seed, 7)

    def test_the_background_is_the_declared_directional_texture(self):
        bg = self.scene.background
        self.assertEqual(bg["kind"], "directional")
        self.assertEqual(bg["distance_m"], 4000)
        self.assertEqual(bg["texture"], {
            "kind": "value-noise", "seed": 7, "octaves": 4, "cells": 16,
            "grey": [70, 150],
            "stripes": {"count": 11, "tilt_deg": 23, "grey": 170, "width_deg": 1.5},
        })

    def test_forty_six_background_landmarks_on_six_rings(self):
        lms = self.scene.landmarks
        self.assertEqual(len(lms), 46)          # five rings of eight, a cap of six
        self.assertEqual(len({lm["id"] for lm in lms}), 46)
        pairs = {(lm["palette"], lm["id"][1]) for lm in lms}
        self.assertEqual(len(pairs), 46, "(palette, ring) pairs are distinct")

    def test_every_ring_matches_the_declared_generator(self):
        by_id = {lm["id"]: lm for lm in self.scene.landmarks}
        seen = 0
        for r, (alt, count, radius) in enumerate(RINGS):
            for k in range(count):
                lm = by_id[f"R{r}K{k}"]
                with self.subTest(lm["id"]):
                    self.assertEqual(lm["az"], float(ring_az(r, k)))
                    self.assertEqual(lm["alt"], alt)
                    self.assertEqual(lm["palette"], ring_palette(r, k))
                    self.assertEqual(lm["radius_deg"], radius)
                seen += 1
        self.assertEqual(seen, len(self.scene.landmarks), "no ring has extra discs")

    def test_three_azimuths_by_hand(self):
        by_id = {lm["id"]: lm for lm in self.scene.landmarks}
        self.assertEqual(by_id["R0K0"]["az"], 0.0)          # 0 + 0 + 0
        self.assertEqual(by_id["R3K2"]["az"], 169.0)        # 90 + 51 + 28
        self.assertEqual(by_id["R5K5"]["az"], 317.0)        # 5 * 60 + 17
        self.assertNotIn("R5K6", by_id, "the cap ring has six discs, not eight")

    def test_no_two_background_discs_touch(self):
        # Two overlapping discs are one smear with two names: the colour in the
        # overlap decodes as whichever wins the tie, so neither landmark can be
        # used to measure direction error there. Issue #45 was the cap ring
        # failing exactly this, so it is now an invariant of every scene.
        worst = None
        for a, b in itertools.combinations(self.scene.landmarks, 2):
            gap = (angle_between(sky_vector(a["az"], a["alt"]),
                                 sky_vector(b["az"], b["alt"]))
                   - (a["radius_deg"] + b["radius_deg"]))
            if worst is None or gap < worst[0]:
                worst = (gap, a["id"], b["id"])
            self.assertGreater(gap, 0.0, f'{a["id"]} and {b["id"]} overlap')
        # The tightest pair is two adjacent cap discs: 60 degrees of azimuth at
        # alt 85 is 2 * asin(sin 30 * cos 85) = 4.995 degrees of arc, which
        # clears their 1.5 + 1.5 radii by 1.995.
        self.assertAlmostEqual(worst[0], 1.995, places=3)
        self.assertEqual(worst[1][:2], "R5")
        self.assertEqual(worst[2][:2], "R5")

    def test_eight_objects_five_test_obstacles_six_surface_landmarks(self):
        self.assertEqual([o["id"] for o in self.scene.objects],
                         ["ground", "wall-east", "roof-south", "pole-near",
                          "pole-far", "trunk", "canopy", "hill"])
        self.assertEqual([o["id"] for o in self.scene.test_obstacles],
                         ["pole-near", "pole-far", "roof-south", "wall-east", "trunk"])
        self.assertEqual([s["id"] for s in self.scene.surface_landmarks],
                         ["W1", "W2", "RF1", "RF2", "T1", "H1"])

    def test_the_declared_obstacle_widths(self):
        widths = {o["id"]: o["min_width_deg"] for o in self.scene.test_obstacles}
        self.assertEqual(widths, {"pole-near": 2.0, "pole-far": 0.25,
                                  "roof-south": 10.0, "wall-east": 10.0, "trunk": 1.0})

    def test_object_colours_are_dull_and_never_mistakable_for_a_landmark(self):
        for obj in self.scene.objects:
            r, g, b = obj["colour"]
            with self.subTest(obj["id"]):
                self.assertLess(max(r, g, b) - min(r, g, b), 60, "channel spread")
                self.assertGreaterEqual(nearest((r, g, b))[1], 40.0, "palette distance")

    def test_every_surface_landmark_lies_on_an_object_that_exists(self):
        ids = {o["id"] for o in self.scene.objects}
        for s in self.scene.surface_landmarks:
            self.assertIn(s["object"], ids)
            self.assertIn(s["palette"], range(24))
        for o in self.scene.test_obstacles:
            self.assertIn(o["object"], ids)

    def test_the_trunk_surface_landmark_sits_on_the_trunk_face(self):
        # T1 is 0.25 m (the trunk radius) from the axis at (6, 8) along the
        # horizontal unit vector toward c_ref's (0, 0.75), rounded to 3 places.
        t1 = next(s for s in self.scene.surface_landmarks if s["id"] == "T1")
        e, n, u = t1["centre"]
        self.assertAlmostEqual(((e - 6.0) ** 2 + (n - 8.0) ** 2) ** 0.5, 0.25, places=3)
        self.assertEqual(u, 2.0)
        self.assertEqual(t1["normal"], [-0.6375, -0.7704, 0.0])

    def test_a_bad_schema_version_is_refused(self):
        bad = dict(self.raw)
        bad["schema"] = 2
        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d) / "bad.json"
            tmp.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError):
                load(tmp)


if __name__ == "__main__":
    unittest.main()
