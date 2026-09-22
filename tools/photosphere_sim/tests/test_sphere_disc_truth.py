"""The sphere's clip factor, checked against the ray-caster that paints it (#53).

`score._expected_disc_areas` scales a surface disc's expected area by the
band a curved host lets `sim.truth` paint: the ratio once on a cylinder, and
the ratio SQUARED on a sphere, because a sphere curves both ways. Until now
that was pinned only by a unit test on the formula (test_score's
`test_a_disc_on_a_sphere_is_clipped_in_two_dimensions`), which checks the
arithmetic and not the claim - the chart yard has no sphere-hosted landmark,
so no scene ever set the model against what the truth actually paints.

This does, without touching the chart yard: it lends the chart yard's
background to a scene of one sphere (the canopy's own centre and radius) with
one disc on the face turned to the camera, casts a fine grid of rays through
`truth.intersect`, and integrates the solid angle the disc was painted over.

Measured when written, expected / painted:

    disc 0.20 m (ratio 1.76, unclipped)   1.004
    disc 0.50 m (ratio 0.71)              1.007   linear factor would be 1.43
    disc 0.80 m (ratio 0.44)              1.007   linear factor would be 2.28

So the squared factor is right to under one per cent, and the linear one
would expect a cap 43 to 128 per cent larger than can be painted - exactly
the over-expectation the formula's docstring says the 40 per cent filter
would punish by discarding the landmark. One thing the numbers correct: that
docstring calls the clipped model "a lower bound on the clipped area". It is
not quite: it runs 0.4 to 1.2 per cent OVER what is painted, because the disc
is measured along the curved surface and projects a little smaller than a
flat one. Harmless against a 40 per cent filter, and recorded here so the
word is not relied on.

MUTATION RUN: make the sphere branch use the linear factor (drop `** 2`).
Observed red: 'the model expects 1.43 times what the truth paints'.
"""
import dataclasses, math, pathlib, unittest

import numpy as np

from sim import scene as scene_module, score, truth

ROOT = pathlib.Path(__file__).resolve().parents[1]
C_REF = np.array([0.0, 0.75, 1.4])
HOST_CENTRE = np.array([6.0, 8.0, 7.5])     # the chart yard's canopy
HOST_RADIUS = 2.5


def _painted_and_expected(disc_radius):
    base = scene_module.load(ROOT / "scenes" / "chartyard.json")
    toward = C_REF - HOST_CENTRE
    toward /= np.linalg.norm(toward)
    centre = HOST_CENTRE + HOST_RADIUS * toward
    objects = [{"id": "ball", "kind": "sphere", "centre": list(HOST_CENTRE),
                "radius": HOST_RADIUS, "colour": [50, 90, 40]}]
    discs = [{"id": "ON", "palette": 0, "object": "ball", "centre": list(centre),
              "normal": list(toward), "radius_m": disc_radius}]
    scene = dataclasses.replace(base, objects=objects, surface_landmarks=discs,
                                landmarks=[], test_obstacles=[])
    view = centre - C_REF
    az0 = math.degrees(math.atan2(view[0], view[1])) % 360.0
    alt0 = math.degrees(math.asin(view[2] / np.linalg.norm(view)))
    half = math.degrees(disc_radius / np.linalg.norm(view)) * 1.5
    step = half / 300.0
    az, alt = np.meshgrid(np.arange(az0 - half, az0 + half, step),
                          np.arange(alt0 - half, alt0 + half, step))
    a, l = np.radians(az), np.radians(alt)
    dirs = np.stack([np.cos(l) * np.sin(a), np.cos(l) * np.cos(a), np.sin(l)],
                    axis=-1).reshape(-1, 3)
    hit = truth.intersect(scene, C_REF, dirs)
    on = (hit.landmark_id == "ON").reshape(alt.shape)
    painted = float((on * np.cos(l)).sum() * step * step)       # square degrees
    expected = score._expected_disc_areas(
        {"objects": objects, "landmarks": [], "surface_landmarks": discs},
        C_REF)["ON"][0]
    ratio = HOST_RADIUS * math.sin(math.acos(score._NORMAL_DOT)) / disc_radius
    return painted, expected, ratio


class SphereClipAgainstTheTruth(unittest.TestCase):
    def test_a_clipped_disc_is_expected_at_the_size_it_is_painted(self):
        for disc in (0.5, 0.8):
            painted, expected, ratio = _painted_and_expected(disc)
            self.assertLess(ratio, 1.0, "premise: this disc is clipped by the band")
            self.assertGreater(painted, 0.0, "the truth painted nothing, so this grades nothing")
            self.assertLess(abs(expected / painted - 1.0), 0.03,
                            f"disc {disc}: the model expects {expected / painted:.2f} "
                            f"times what the truth paints")

    def test_the_case_can_tell_the_two_factors_apart(self):
        """The guard on the guard: if the linear factor were within 3 per cent
        as well, the case above would pass for either formula."""
        painted, expected, ratio = _painted_and_expected(0.5)
        linear = expected / ratio            # expected is unclipped * ratio ** 2
        self.assertGreater(linear / painted, 1.3,
                           f"the linear factor is also close ({linear / painted:.2f}), "
                           f"so this disc cannot discriminate")

    def test_an_unclipped_disc_is_the_flat_formula(self):
        """The control: where the band does not bite, the model is just the
        foreshortened flat disc, and must match the paint on its own."""
        painted, expected, ratio = _painted_and_expected(0.2)
        self.assertGreater(ratio, 1.0)
        self.assertLess(abs(expected / painted - 1.0), 0.03)


if __name__ == "__main__":
    unittest.main()
