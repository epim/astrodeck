"""The stage-A routes turned into camera trajectories.

Every expectation is arithmetic on the route files' own declared numbers
(``routes/still.json``, ``routes/arc075.json``), the same numbers
CONTRACT.md's Route schema section specifies, never on rendered pixels.
"""
import json, math, pathlib, unittest

import numpy as np

from sim import trajectory
from sim.geometry import angle_between, sky_angles

ROUTES = pathlib.Path(__file__).resolve().parents[1] / "routes"


def load_route(name):
    return json.loads((ROUTES / f"{name}.json").read_text(encoding="utf-8"))


def build(name, fps=10):
    return trajectory.build(load_route(name), fps)


def _attitude_angle_deg(basis_a, basis_b):
    """The full-rotation (not forward-only) angle between two attitudes.

    ``matrix_cam_to_world()`` is a proper rotation (det +1), so the relative
    rotation ``Mb @ Ma.T`` is too, and its trace gives the geodesic angle in
    SO(3) directly. Well-conditioned for the small, sub-degree deltas this is
    used on (only singular near exactly 180 degrees, never reached between
    consecutive 100 Hz samples of a move built to average 30 deg/s or less).
    """
    ma = basis_a.matrix_cam_to_world()
    mb = basis_b.matrix_cam_to_world()
    r = mb @ ma.T
    return math.degrees(math.acos(np.clip((np.trace(r) - 1.0) / 2.0, -1.0, 1.0)))


def _segment_spans(traj):
    """``(t0_ms, t1_ms, kind)`` for every move and the one tilt, reconstructed
    from the PUBLIC ``holds`` list alone: a move or tilt is exactly the gap
    between two consecutive holds, since none of them is ever a hard cut."""
    holds = traj.holds
    spans = [(holds[i - 1].to_ms, holds[i].from_ms, "move") for i in range(1, 46)]
    spans.append((holds[45].to_ms, holds[46].from_ms, "move"))  # into the sweep
    spans.append((holds[46].to_ms, holds[47].from_ms, "tilt"))
    return spans


def _smoothstep(u):
    return 3.0 * u * u - 2.0 * u * u * u


def _spherical_slerp(v0, v1, s):
    """The point at fraction ``s`` along the great circle from ``v0`` to
    ``v1`` (both unit vectors)."""
    omega = math.radians(angle_between(v0, v1))
    if omega < 1e-9:
        return v0
    result = (math.sin((1.0 - s) * omega) * v0 + math.sin(s * omega) * v1) / math.sin(omega)
    return result / np.linalg.norm(result)


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
        #
        # There used to be a second version of this same check restated in
        # azimuth terms (wrap-compared), over just the aim-to-aim moves. It is
        # gone: azimuth is a COORDINATE, not a physical quantity, and near the
        # zenith it can change arbitrarily fast for an arbitrarily small
        # physical motion of the camera (the classic polar-coordinate
        # singularity). The move from band 2's last aim into [0, 89.5] passes
        # within half a degree of the pole, where a correct, physically
        # bounded rotation legitimately produces a 1.4 degree azimuth jump
        # between two 10 ms samples -- exceeding what the old test asserted,
        # not because the camera moved too fast, but because "how fast is az
        # changing" stops meaning "how fast is the camera turning" near a
        # pole. Measuring the actual look direction (as this test does) is
        # exactly the fix for that; re-adding an azimuth-based version would
        # be re-introducing the bug review P2 is about.
        traj = build("arc075")
        directions = []
        t = 0
        while t <= traj.duration_ms:
            _, basis = traj.pose_at(t)
            directions.append(basis.forward)
            t += 10
        for a, b in zip(directions, directions[1:]):
            self.assertLessEqual(angle_between(a, b), 1.0 + 1e-9)


class FullAttitudeRateBudget(unittest.TestCase):
    def test_every_move_and_the_tilt_respect_the_30_and_45_deg_s_budget(self):
        # Integrates the FULL attitude rate (angle between consecutive
        # bases as rotations, not just forward vectors) at ~100 Hz over
        # every shipped move and the sweep's tilt, for both routes (their
        # orientation timing is identical; only position differs). Every
        # segment's PEAK must stay at or below 45 deg/s; every MOVE's
        # AVERAGE (net rotation over its duration) must stay at or below
        # 30 deg/s -- the declared budget from CONTRACT.md's Route schema,
        # now measured the way review P2 asked: on the full rotation.
        for name in ("still", "arc075"):
            traj = build(name)
            for index, (t0, t1, kind) in enumerate(_segment_spans(traj)):
                duration_s = (t1 - t0) / 1000.0
                self.assertGreater(duration_s, 0.0)
                n = max(2, round(duration_s * 100) + 1)
                ts = np.linspace(t0, t1, n)
                bases = [traj.pose_at(t)[1] for t in ts]
                deltas = [_attitude_angle_deg(bases[i], bases[i + 1])
                         for i in range(len(bases) - 1)]
                dts = np.diff(ts) / 1000.0
                rates = np.asarray(deltas) / dts
                # A ~100 Hz secant is a finite-difference estimate of the
                # continuous rate, which peaks exactly at 45 deg/s (an exact
                # closed form, not an approximation -- see trajectory.py); the
                # measured secant can overshoot that by a hair (worst case
                # observed: 45.008, 0.02%) purely from sampling a smooth
                # parabola-shaped rate curve at a finite step near its own
                # peak, so the tolerance below is a few times that margin,
                # not a loosened physical budget.
                self.assertLessEqual(
                    float(rates.max()), 45.05,
                    f"{name} {kind} segment {index}: peak {rates.max():.4f} deg/s exceeds 45",
                )
                if kind == "move":
                    average = sum(deltas) / duration_s
                    self.assertLessEqual(
                        average, 30.05,
                        f"{name} move segment {index}: average {average:.4f} deg/s exceeds 30",
                    )


class ForwardOnGreatCircle(unittest.TestCase):
    def test_forward_matches_the_endpoint_slerp_at_every_sample(self):
        # For every move, the swing keeps forward on the exact great circle
        # between the move's endpoint forward vectors: rotating a vector by
        # a fraction of the angle between it and a target, about an axis
        # perpendicular to both, is mathematically identical to spherically
        # interpolating the two vectors directly. Checked at the smoothstep
        # fraction (not the linear time fraction), since that is what the
        # implementation actually uses.
        traj = build("arc075")
        for index, (t0, t1, kind) in enumerate(_segment_spans(traj)):
            if kind != "move":
                continue
            fwd0 = traj.pose_at(t0)[1].forward
            fwd1 = traj.pose_at(t1)[1].forward
            n = max(2, round((t1 - t0) / 10) + 1)
            for k in range(n):
                u = k / (n - 1)
                t = t0 + (t1 - t0) * u
                fwd_actual = traj.pose_at(t)[1].forward
                fwd_expected = _spherical_slerp(fwd0, fwd1, _smoothstep(u))
                deviation = angle_between(fwd_actual, fwd_expected)
                self.assertLess(
                    deviation, 0.01,
                    f"move segment {index} sample {k}: forward is {deviation:.6f} deg "
                    f"off the endpoint great circle",
                )


class Transition(unittest.TestCase):
    def test_aims_to_sweep_transition_is_governed_by_the_full_attitude_change(self):
        # [0, 89.5] (holds[45], the last aim) to the sweep's start, (180, 0)
        # (holds[46]): the SWING (forward-only great-circle angle) is only
        # about 90.5 degrees, but look_basis's azimuth-only `right` -- which
        # ignores altitude entirely -- hands this move a near-total reversal
        # on top of that: the TWIST is about -180 degrees. The combined
        # theta = hypot(90.5, 180) is about 201.5 degrees, giving a duration
        # of about 201.5 / 30 = 6.7 s, not the ~3 s a forward-only reading
        # would suggest (and shipped, before this fix). That the fix makes
        # this move slower is the point: it is roll near the zenith the fix
        # is for, and a forward-only duration would have under-timed it.
        traj = build("arc075")
        transition_ms = traj.holds[46].from_ms - traj.holds[45].to_ms
        self.assertGreaterEqual(transition_ms, 6600)
        self.assertLessEqual(transition_ms, 6800)


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
