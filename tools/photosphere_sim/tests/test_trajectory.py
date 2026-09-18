"""The stage-A routes turned into camera trajectories.

Every expectation is arithmetic on the route files' own declared numbers
(``routes/still.json``, ``routes/arc075.json``), the same numbers
CONTRACT.md's Route schema section specifies, never on rendered pixels.
"""
import json, math, pathlib, unittest

import numpy as np

from sim import trajectory
from sim.geometry import angle_between, sky_angles, wrap_deg

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
    def test_look_direction_never_jumps_more_than_1_degree_between_100hz_samples(self):
        # Covers the WHOLE route, including the aims-to-sweep transition: with
        # the 30 deg/s move-duration rule (CONTRACT.md's Route schema), a move
        # takes longer instead of turning faster, so its peak rate -- 1.5x its
        # average, at the smoothstep's own midpoint -- never exceeds 45 deg/s
        # regardless of how far it has to reach. That is 0.45 degrees per
        # 10 ms sample, comfortably inside the 1 degree bound checked here, so
        # there is no discontinuity left anywhere in the route to carve out.
        traj = build("arc075")
        directions = []
        t = 0
        while t <= traj.duration_ms:
            _, basis = traj.pose_at(t)
            directions.append(basis.forward)
            t += 10
        for a, b in zip(directions, directions[1:]):
            self.assertLessEqual(angle_between(a, b), 1.0 + 1e-9)

    def test_azimuth_never_jumps_more_than_1_degree_during_the_aims_scan(self):
        # The same bound, restated in azimuth terms (wrap-compared) over just
        # the 45 aim-to-aim moves, as a second, independent reading of the
        # same property the great-circle test above checks over the whole
        # route.
        traj = build("arc075")
        end_of_aims = traj.holds[45].to_ms
        azimuths = []
        t = 0
        while t <= end_of_aims:
            _, basis = traj.pose_at(t)
            az, _ = sky_angles(basis.forward)
            azimuths.append(az)
            t += 10
        for a, b in zip(azimuths, azimuths[1:]):
            self.assertLessEqual(abs(wrap_deg(b - a)), 1.0 + 1e-9)


class Transition(unittest.TestCase):
    def test_aims_to_sweep_transition_takes_about_3_seconds(self):
        # [0, 89.5] (holds[45], the last aim) to the sweep's start, (180, 0)
        # (holds[46]): a ~90 degree great-circle turn, so the 30 deg/s rule
        # gives it roughly 90 / 30 = 3 s instead of the standard move_s (0.8).
        traj = build("arc075")
        transition_ms = traj.holds[46].from_ms - traj.holds[45].to_ms
        self.assertGreaterEqual(transition_ms, 2900)
        self.assertLessEqual(transition_ms, 3200)


class Sweep(unittest.TestCase):
    def test_alt_rises_monotonically_from_0_to_85_over_4_seconds(self):
        traj = build("arc075")
        # holds[46] is the sweep's own first hold (az 180, alt 0); holds[47]
        # is its last (az 180, alt 85). The tilt is the move between them,
        # always exactly duration_s (4.0 s) long, whatever the transition
        # into holds[46] cost -- read from pose_at directly rather than from
        # traj.frames, since the transition's new, non-round duration means
        # the tilt's own start no longer has to land on a frame at 10 fps.
        traj_holds = traj.holds
        tilt_start_ms = traj_holds[46].to_ms
        tilt_end_ms = traj_holds[47].from_ms
        self.assertEqual(tilt_end_ms - tilt_start_ms, 4000)

        alts = []
        t = tilt_start_ms
        while t <= tilt_end_ms:
            _, basis = traj.pose_at(t)
            az, alt = sky_angles(basis.forward)
            self.assertAlmostEqual(az, 180.0, places=6)
            alts.append(alt)
            t += 100
        for a, b in zip(alts, alts[1:]):
            self.assertLessEqual(a, b + 1e-9)
        self.assertAlmostEqual(alts[0], 0.0, places=6)
        self.assertAlmostEqual(alts[-1], 85.0, places=6)


if __name__ == "__main__":
    unittest.main()
