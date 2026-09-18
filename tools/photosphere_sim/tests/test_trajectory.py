"""The stage-A routes turned into camera trajectories.

Every expectation is arithmetic on the route files' own declared numbers
(``routes/still.json``, ``routes/arc075.json``), the same numbers
CONTRACT.md's Route schema section specifies, never on rendered pixels.
"""
import json, math, pathlib, unittest

import numpy as np

from sim import trajectory
from sim.geometry import sky_angles, wrap_deg

ROUTES = pathlib.Path(__file__).resolve().parents[1] / "routes"


def load_route(name):
    return json.loads((ROUTES / f"{name}.json").read_text(encoding="utf-8"))


def build(name, fps=10):
    return trajectory.build(load_route(name), fps)


class StillRoute(unittest.TestCase):
    def test_never_translates(self):
        traj = build("still")
        for frame in traj.frames:
            np.testing.assert_allclose(frame.position, traj.c_ref, atol=1e-9)

    def test_c_ref_is_the_held_position(self):
        traj = build("still")
        np.testing.assert_allclose(traj.c_ref, [0.0, 0.75, 1.4], atol=1e-9)


class ArcRoute(unittest.TestCase):
    def test_c_ref(self):
        traj = build("arc075")
        np.testing.assert_allclose(traj.c_ref, [0.0, 0.75, 1.4], atol=1e-9)

    def test_24_degree_turn_moves_the_camera_by_the_chord(self):
        traj = build("arc075")
        h0, h1 = traj.holds[0], traj.holds[1]
        t0 = (h0.from_ms + h0.to_ms) / 2.0
        t1 = (h1.from_ms + h1.to_ms) / 2.0
        p0, _ = traj.pose_at(t0)
        p1, _ = traj.pose_at(t1)
        expected = 2.0 * 0.75 * math.sin(math.radians(12.0))  # 0.3118675 m
        self.assertAlmostEqual(float(np.linalg.norm(p1 - p0)), expected, delta=1e-3)

    def test_lift_at_the_near_zenith_aim(self):
        traj = build("arc075")
        # The 46th aim, [0, 89.5], is held last before the sweeps: holds[45].
        hold = traj.holds[45]
        self.assertEqual(hold.az, 0.0)
        self.assertAlmostEqual(hold.alt, 89.5, places=9)
        t_mid = (hold.from_ms + hold.to_ms) / 2.0
        p, _ = traj.pose_at(t_mid)
        expected_z = 1.4 + 0.25 * 89.5 / 90.0
        self.assertAlmostEqual(float(p[2]), expected_z, delta=1e-3)


class Holds(unittest.TestCase):
    def test_count_is_46_aims_plus_2_sweep_holds(self):
        traj = build("arc075")
        self.assertEqual(len(traj.holds), 46 + 2)

    def test_every_hold_lasts_1200_ms(self):
        traj = build("arc075")
        for hold in traj.holds:
            self.assertEqual(hold.to_ms - hold.from_ms, 1200)

    def test_rate_is_zero_strictly_inside_every_hold(self):
        traj = build("arc075")
        for frame in traj.frames:
            inside_a_hold = any(h.from_ms < frame.t_capture_ms < h.to_ms for h in traj.holds)
            if inside_a_hold:
                self.assertEqual(frame.angular_rate_deg_s, 0.0)

    def test_rate_exceeds_5_deg_s_at_the_first_moves_midpoint(self):
        traj = build("arc075")
        # First move is aim0 (0,0) -> aim1 (24,0), the interval [1200, 2000) ms;
        # its midpoint, 1600 ms, is frame k=16 at 10 fps.
        frame = next(f for f in traj.frames if f.t_capture_ms == 1600)
        self.assertGreater(frame.angular_rate_deg_s, 5.0)


class SmoothstepContinuity(unittest.TestCase):
    def test_azimuth_never_jumps_more_than_1_degree_between_100hz_samples(self):
        # Covers the 46-aim scan (holds[45].to_ms is where it ends, just before
        # the sweep's own, deliberately discontinuous, entry point -- see
        # trajectory.py's module docstring for why that cut is not a move).
        traj = build("arc075")
        end_of_aims = traj.holds[45].to_ms
        azimuths = []
        # Deliberately excludes t == end_of_aims itself: that instant belongs
        # to the sweep's first hold (see trajectory.py's module docstring for
        # why the cut there is not a move), so this loop stops one 10 ms
        # sample short of it.
        t = 0
        while t < end_of_aims:
            _, basis = traj.pose_at(t)
            az, _ = sky_angles(basis.forward)
            azimuths.append(az)
            t += 10
        for a, b in zip(azimuths, azimuths[1:]):
            self.assertLessEqual(abs(wrap_deg(b - a)), 1.0 + 1e-9)


class Sweep(unittest.TestCase):
    def test_alt_rises_monotonically_from_0_to_85_over_4_seconds(self):
        traj = build("arc075")
        # Restricted to the sweep's own time span: az 180 is also one of
        # band1's aims (alt 35), so filtering on azimuth alone would catch
        # that unrelated hold too.
        end_of_aims = traj.holds[45].to_ms
        az180 = sorted((f for f in traj.frames
                        if f.az == 180.0 and f.t_capture_ms >= end_of_aims),
                       key=lambda f: f.t_capture_ms)
        self.assertGreater(len(az180), 0)
        for a, b in zip(az180, az180[1:]):
            self.assertLessEqual(a.alt, b.alt + 1e-9)
        start = next(f for f in az180 if f.t_capture_ms == 92400)
        end = next(f for f in az180 if f.t_capture_ms == 96400)
        self.assertAlmostEqual(start.alt, 0.0, places=6)
        self.assertAlmostEqual(end.alt, 85.0, places=6)


if __name__ == "__main__":
    unittest.main()
