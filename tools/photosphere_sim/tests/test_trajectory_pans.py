# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The pan route kind: segments with ramps, holds only at the ends, a rate cap,
the axis reference and a seeded tremor (panorama spec 8.3 T13, 13.3).

Every expectation is arithmetic on the route files' own declared numbers
(``routes/pan1-*.json`` and the rest), measured on the poses the trajectory
hands out, never on a number the trajectory reports about itself alone. Where
the trajectory's own ``angular_rate_deg_s`` is checked it is checked against
the secant of the poses beside it.

Every test names the mutant it must catch. The three the task names:

  (a) ignore the rate cap:
      ``RateCap.test_a_route_faster_than_its_cap_does_not_build``,
      ``RateCap.test_the_tremor_counts_towards_the_cap`` and
      ``RateCap.test_the_cap_is_compared_with_the_measured_peak``;
  (b) insert a hold between pan segments:
      ``HoldsOnlyAtTheEnds.test_the_reversal_has_no_hold``,
      ``HoldsOnlyAtTheEnds.test_the_duration_is_the_declared_segments_and_two_holds`` and
      ``HoldsOnlyAtTheEnds.test_exactly_two_holds_the_first_and_the_last``;
  (c) ``c_ref`` without the height:
      ``AxisReference.test_c_ref_is_the_pivot_plus_the_height``,
      ``AxisReference.test_a_synthetic_pivot_and_height_are_both_used`` and
      ``PanCaseDirectory.test_the_case_directory_carries_the_route_and_the_axis``.

The brief's FullAttitudeRateBudget check (``test_trajectory.py``) is
``RateBudget`` here, on every pan route.
"""
import copy
import hashlib
import json
import math
import pathlib
import tempfile
import unittest

import numpy as np

from sim import cases, sensors, trajectory
from sim.geometry import sky_angles, sky_vector, wrap_deg

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTES = ROOT / "routes"
FIXTURES = ROOT / "fixtures"

FPS = 30
SEED = 7

#: name -> the numbers the plan (spec 7.5 and 8.3 T13) gives each route:
#: (altitude, radius, [(turn, speed, direction), ...], rate cap).
ROUTE_PLAN = {
    "pan1-p23": (22.98, 0.0, [(385, 20, "cw")], 45),
    "pan1-p30": (30.0, 0.0, [(385, 20, "cw")], 45),
    "pan1-p17": (17.36, 0.0, [(385, 20, "cw")], 45),
    "arcpan1-030-p23": (22.98, 0.3, [(385, 20, "cw")], 45),
    "arcpan1-075-p23": (22.98, 0.75, [(385, 20, "cw")], 45),
    # 40 deg/s plus a hand's tremor reaches 47-50 deg/s, so this one cap is higher.
    "pan1-fast-p23": (22.98, 0.0, [(385, 40, "cw")], 60),
    "pan1-reverse-p23": (22.98, 0.0, [(300, 20, "cw"), (60, 20, "ccw"), (160, 20, "cw")], 45),
    "panshort-p23": (22.98, 0.0, [(120, 20, "cw")], 45),
}
PAN_ROUTES = tuple(ROUTE_PLAN)


def load_route(name):
    return json.loads((ROUTES / f"{name}.json").read_text(encoding="utf-8"))


def without_tremor(route):
    route = copy.deepcopy(route)
    route["tremor"] = None
    return route


_BUILT = {}


def built(name, seed=SEED, tremor=True):
    """The shipped route at 30 fps, built once per (route, seed, tremor)."""
    key = (name, seed, tremor)
    if key not in _BUILT:
        route = load_route(name)
        _BUILT[key] = trajectory.build(route if tremor else without_tremor(route), FPS, seed)
    return _BUILT[key]


def declared(route):
    """The route's timeline from its declared numbers alone, in seconds:
    ``(segments, total_s)``, each segment ``(t0, t1, speed, ramp, sign, az0)``."""
    t = float(route["start_hold_s"])
    az = float(route["pans"][0]["from_az"])
    segments = []
    for pan in route["pans"]:
        duration = pan["turn_deg"] / pan["speed_deg_s"] + pan["ramp_s"]
        sign = 1.0 if pan["direction"] == "cw" else -1.0
        segments.append((t, t + duration, float(pan["speed_deg_s"]), float(pan["ramp_s"]), sign, az))
        t += duration
        az += sign * pan["turn_deg"]
    return segments, t + float(route["end_hold_s"])


def attitude_angle_deg(basis_a, basis_b):
    """The full-rotation angle between two attitudes (as ``test_trajectory.py``
    measures it): the geodesic angle of ``Mb Ma^T`` from its trace."""
    r = basis_b.matrix_cam_to_world() @ basis_a.matrix_cam_to_world().T
    return math.degrees(math.acos(float(np.clip((np.trace(r) - 1.0) / 2.0, -1.0, 1.0))))


def secant_rate(traj, t_ms, half_ms):
    lo, hi = max(0.0, t_ms - half_ms), min(traj.duration_ms, t_ms + half_ms)
    return attitude_angle_deg(traj.pose_at(lo)[1], traj.pose_at(hi)[1]) / ((hi - lo) / 1000.0)


def unwrapped_az(traj):
    az = np.array([f.az for f in traj.frames])
    return np.concatenate([[az[0]], az[0] + np.cumsum(wrap_deg(np.diff(az)))])


def longest_run(flags):
    best = run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        best = max(best, run)
    return best


def tremor_of(traj, nominal):
    """``(yaw, pitch, roll)`` in degrees at 100 Hz between the fades, read off
    the poses: the optical axis against the same route built without tremor,
    and the roll of the image about the axis."""
    ts = np.arange(2000.0, traj.duration_ms - 2000.0, 10.0)
    out = np.zeros((len(ts), 3))
    for i, t in enumerate(ts):
        basis = traj.pose_at(t)[1]
        az, alt = sky_angles(basis.forward)
        az0, alt0 = sky_angles(nominal.pose_at(t)[1].forward)
        level_right = sky_vector(az + 90.0, 0.0)
        roll = math.degrees(math.atan2(float(basis.right @ np.cross(level_right, basis.forward)),
                                       float(basis.right @ level_right)))
        out[i] = (float(wrap_deg(az - az0)), alt - alt0, roll)
    return ts, out


_SERIES = {}
_NOMINAL = {}


def tremor_series(route, seed):
    """``tremor_of`` for ``route`` at ``seed``, built once. The nominal route
    (no tremor) does not depend on the seed."""
    key = (json.dumps(route, sort_keys=True), seed)
    if key not in _SERIES:
        nominal_route = without_tremor(route)
        nkey = json.dumps(nominal_route, sort_keys=True)
        if nkey not in _NOMINAL:
            _NOMINAL[nkey] = trajectory.build(nominal_route, 5, 0)
        _SERIES[key] = tremor_of(trajectory.build(route, 5, seed), _NOMINAL[nkey])
    return _SERIES[key]


def long_route(**tremor):
    """One 60 second pan with its own tremor block, for the statistics."""
    return {"schema": 1, "name": "long", "kind": "pan", "pivot": [0, 0, 0], "radius_m": 0,
            "height_m": 1.4, "reference": "axis", "start_hold_s": 1.0, "end_hold_s": 1.0,
            "rate_cap_deg_s": 100,
            "pans": [{"alt": 22.98, "from_az": 0, "turn_deg": 600, "speed_deg_s": 10,
                      "ramp_s": 0.5, "direction": "cw"}],
            "tremor": {"yaw_deg": 0.4, "pitch_deg": 0.2, "roll_deg": 0.1,
                       "band_hz": [0.5, 3.0], **tremor}}


def short_route(**overrides):
    route = {"schema": 1, "name": "short", "kind": "pan", "pivot": [0, 0, 0], "radius_m": 0,
             "height_m": 1.4, "reference": "axis", "start_hold_s": 1.0, "end_hold_s": 1.0,
             "rate_cap_deg_s": 45,
             "pans": [{"alt": 20.0, "from_az": 0, "turn_deg": 60, "speed_deg_s": 20,
                       "ramp_s": 0.5, "direction": "cw"}],
             "tremor": None}
    route.update(overrides)
    return route


class RouteFiles(unittest.TestCase):
    def test_the_eight_routes_declare_the_numbers_of_the_plan(self):
        """MUTATION: change any one number in a route file (a speed to 25, a
        turn to 360, a direction), or leave one of the eight out."""
        for name, (alt, radius, pans, cap) in ROUTE_PLAN.items():
            with self.subTest(route=name):
                route = load_route(name)
                self.assertEqual((route["schema"], route["name"], route["kind"]), (1, name, "pan"))
                self.assertEqual(route["pivot"], [0, 0, 0])
                self.assertEqual(route["height_m"], 1.4)
                self.assertEqual(route["reference"], "axis")
                self.assertEqual((route["start_hold_s"], route["end_hold_s"]), (1.0, 1.0))
                self.assertEqual(route["rate_cap_deg_s"], cap)
                self.assertEqual(route["radius_m"], radius)
                self.assertEqual(route["tremor"], {"yaw_deg": 0.3, "pitch_deg": 0.3,
                                                   "roll_deg": 0.3, "band_hz": [0.5, 3.0]})
                self.assertEqual([(p["turn_deg"], p["speed_deg_s"], p["direction"])
                                  for p in route["pans"]], pans)
                self.assertEqual({p["alt"] for p in route["pans"]}, {alt})
                self.assertEqual({p["ramp_s"] for p in route["pans"]}, {0.5})
                self.assertEqual(route["pans"][0]["from_az"], 0)

    def test_a_full_ring_turns_385_degrees_and_the_reverse_route_nets_400(self):
        """The 385 is a full ring plus 25 degrees of overlap; 300 - 60 + 160 is
        a ring plus 40. MUTATION: build any route's ``turn_deg`` as 360."""
        for name in PAN_ROUTES:
            with self.subTest(route=name):
                route = load_route(name)
                net = sum(p["turn_deg"] * (1 if p["direction"] == "cw" else -1)
                          for p in route["pans"])
                self.assertEqual(net, {"panshort-p23": 120, "pan1-reverse-p23": 400}.get(name, 385))
                traj = built(name, tremor=False)
                self.assertAlmostEqual(unwrapped_az(traj)[-1], net, delta=1e-6)


class RateIsConstantOutsideTheRamps(unittest.TestCase):
    def test_the_rate_is_the_declared_speed_within_2_percent(self):
        """Measured two ways on the route without its tremor (the tremor is a
        separate test): the trajectory's own field and the secant of the poses
        either side. MUTATION: build the speed profile without the ramp's
        plateau (peak the mean speed instead of ``turn / (duration - ramp)``)."""
        for name in PAN_ROUTES:
            with self.subTest(route=name):
                route = load_route(name)
                traj = built(name, tremor=False)
                segments, _ = declared(route)
                checked = 0
                for t0, t1, speed, ramp, _, _ in segments:
                    for frame in traj.frames:
                        if t0 + ramp <= frame.t_capture_ms / 1000.0 <= t1 - ramp:
                            checked += 1
                            self.assertAlmostEqual(frame.angular_rate_deg_s / speed, 1.0, delta=0.02)
                            self.assertAlmostEqual(
                                secant_rate(traj, frame.t_capture_ms, 5.0) / speed, 1.0, delta=0.02)
                self.assertGreater(checked, 60)

    def test_the_ramps_are_smoothsteps_from_rest(self):
        """Half way up a ramp the speed is half the peak (the smoothstep's
        value at 0.5), and the pan starts and ends at rest. MUTATION: a linear
        ramp has the same midpoint, so this also fixes the end slope: the
        speed a tenth of the way up must be 0.028 of the peak, not 0.1."""
        for name in PAN_ROUTES:
            with self.subTest(route=name):
                route = load_route(name)
                traj = built(name, tremor=False)
                for t0, t1, speed, ramp, _, _ in declared(route)[0]:
                    for t, fraction in ((t0 + ramp / 2.0, 0.5), (t1 - ramp / 2.0, 0.5),
                                        (t0 + ramp / 10.0, 0.028), (t1 - ramp / 10.0, 0.028)):
                        measured = secant_rate(traj, t * 1000.0, 1.0) / speed
                        self.assertAlmostEqual(measured, fraction, delta=0.005)

    def test_the_tremor_does_not_change_the_pace(self):
        """With the tremor on, the azimuth still advances at the declared speed
        over each cruise: a least-squares slope of the unwrapped azimuth against
        time, within 2 %. MUTATION: add the tremor's yaw to the azimuth as a
        drift (``az += yaw * t``)."""
        for name in PAN_ROUTES:
            with self.subTest(route=name):
                route = load_route(name)
                traj = built(name)
                az = unwrapped_az(traj)
                t = np.array([f.t_capture_ms / 1000.0 for f in traj.frames])
                for t0, t1, speed, ramp, sign, _ in declared(route)[0]:
                    if t1 - t0 - 2 * ramp < 2.0:
                        continue
                    inside = (t >= t0 + ramp) & (t <= t1 - ramp)
                    slope = np.polyfit(t[inside], az[inside], 1)[0]
                    self.assertAlmostEqual(slope / (sign * speed), 1.0, delta=0.02)


class HoldsOnlyAtTheEnds(unittest.TestCase):
    """MUTATION (b): insert a hold between pan segments, for example a second
    of ``t += 1000`` after every segment but the last in ``_pan_segments``,
    with the view held at the end of the one before."""

    def test_exactly_two_holds_the_first_and_the_last(self):
        for name in PAN_ROUTES:
            with self.subTest(route=name):
                traj = built(name)
                duration = declared(load_route(name))[1] * 1000.0
                self.assertEqual(traj.duration_ms, duration)
                self.assertEqual([(h.index, h.from_ms, h.to_ms) for h in traj.holds],
                                 [(0, 0, 1000), (1, duration - 1000, duration)])

    def test_the_duration_is_the_declared_segments_and_two_holds(self):
        for name in PAN_ROUTES:
            with self.subTest(route=name):
                route = load_route(name)
                total = route["start_hold_s"] + route["end_hold_s"] + sum(
                    p["turn_deg"] / p["speed_deg_s"] + p["ramp_s"] for p in route["pans"])
                self.assertEqual(built(name).duration_ms, round(total * 1000.0))

    def test_the_holds_are_still_and_the_pan_never_is(self):
        """On the route without its tremor, a frame at rest is a frame in a hold,
        the poses across a hold are identical, and no frame inside the panning
        interval is at rest for long."""
        for name in PAN_ROUTES:
            with self.subTest(route=name):
                traj = built(name, tremor=False)
                start, end = traj.holds[0].to_ms, traj.holds[1].from_ms
                for hold in traj.holds:
                    inside = [f for f in traj.frames if hold.from_ms <= f.t_capture_ms <= hold.to_ms]
                    self.assertGreater(len(inside), 25)
                    for frame in inside:
                        self.assertEqual(frame.angular_rate_deg_s, 0.0)
                        np.testing.assert_array_equal(frame.basis.forward, inside[0].basis.forward)
                        np.testing.assert_array_equal(frame.position, inside[0].position)
                interior = [f for f in traj.frames if start < f.t_capture_ms < end]
                self.assertLessEqual(
                    longest_run(f.angular_rate_deg_s < 0.5 for f in interior), 4,
                    "a stretch of more than 4 frames (133 ms) at rest inside the pan")

    def test_the_reversal_has_no_hold(self):
        """The reverse route turns 300, back 60, on 160. At each reversal the
        speed passes through zero for an instant, never for an interval: the
        frames below 0.5 deg/s around it are three (100 ms), where a one second
        hold is thirty."""
        traj = built("pan1-reverse-p23", tremor=False)
        junctions = (1000 + 15500, 1000 + 15500 + 3500)
        for junction in junctions:
            near = [f for f in traj.frames
                    if abs(f.t_capture_ms - junction) < 500 and f.angular_rate_deg_s < 0.5]
            self.assertTrue(near, "the reversal should pass through rest")
            self.assertLessEqual(len(near), 4)
            self.assertAlmostEqual(
                secant_rate(traj, junction, 1.0), 0.0, delta=0.01)
        # And the direction really does reverse: 300, then 240, then 400.
        az = unwrapped_az(traj)
        times = [f.t_capture_ms for f in traj.frames]
        for t, expected in ((16500, 300.0), (20000, 240.0), (28500, 400.0)):
            self.assertAlmostEqual(az[times.index(t)], expected, delta=1e-6)

    def test_with_the_tremor_the_holds_are_still_too(self):
        """The tremor fades in after the start hold and out before the end hold,
        so a still phone is still: exact zero rate and one pose across each hold,
        and no jump in attitude where the pan begins or ends."""
        for name in PAN_ROUTES:
            with self.subTest(route=name):
                traj = built(name)
                for hold in traj.holds:
                    inside = [f for f in traj.frames if hold.from_ms <= f.t_capture_ms <= hold.to_ms]
                    for frame in inside:
                        self.assertEqual(frame.angular_rate_deg_s, 0.0)
                        np.testing.assert_array_equal(frame.basis.right, inside[0].basis.right)
                        np.testing.assert_array_equal(frame.basis.forward, inside[0].basis.forward)
                for t in (traj.holds[0].to_ms, traj.holds[1].from_ms):
                    jump = attitude_angle_deg(traj.pose_at(t - 1.0)[1], traj.pose_at(t + 1.0)[1])
                    self.assertLess(jump, 0.01)


class AxisReference(unittest.TestCase):
    """MUTATION (c): ``c_ref = pivot`` (or ``pivot + [0, 0, 0]``) for an
    axis route."""

    def test_c_ref_is_the_pivot_plus_the_height(self):
        for name in PAN_ROUTES:
            with self.subTest(route=name):
                np.testing.assert_allclose(built(name).c_ref, [0.0, 0.0, 1.4], atol=1e-12)

    def test_a_synthetic_pivot_and_height_are_both_used(self):
        route = short_route(pivot=[2.5, -1.0, 0.3], height_m=1.6, radius_m=0.5)
        axis = trajectory.build(route, FPS, SEED)
        np.testing.assert_allclose(axis.c_ref, [2.5, -1.0, 1.9], atol=1e-12)
        # Without the key the first frame's position is used, as for the old kinds.
        del route["reference"]
        first = trajectory.build(route, FPS, SEED)
        np.testing.assert_allclose(first.c_ref, first.frames[0].position, atol=0)
        np.testing.assert_allclose(first.c_ref, [2.5, -0.5, 1.9], atol=1e-12)

    def test_no_frame_of_an_arc_sits_on_the_axis_and_none_leaves_the_circle(self):
        """The reference is the axis the arm goes round, a point none of the
        frames occupies. MUTATION: report ``frames[0].position`` as ``c_ref``."""
        for name in ("arcpan1-030-p23", "arcpan1-075-p23"):
            with self.subTest(route=name):
                traj = built(name, tremor=False)
                radius = load_route(name)["radius_m"]
                axis = traj.c_ref
                for frame in traj.frames:
                    self.assertAlmostEqual(float(np.hypot(*(frame.position - axis)[:2])),
                                           radius, delta=1e-9)
                    self.assertAlmostEqual(float(frame.position[2]), axis[2], delta=1e-12)
                self.assertGreater(float(np.linalg.norm(traj.frames[0].position - axis)), 0.29)

    def test_a_reference_other_than_axis_is_refused(self):
        with self.assertRaises(ValueError):
            trajectory.build(short_route(reference="first"), FPS, SEED)


class Position(unittest.TestCase):
    def test_a_still_pivot_never_translates(self):
        for name in ("pan1-p23", "pan1-p30", "pan1-p17", "pan1-fast-p23",
                     "pan1-reverse-p23", "panshort-p23"):
            with self.subTest(route=name):
                traj = built(name)
                for frame in traj.frames:
                    np.testing.assert_allclose(frame.position, traj.c_ref, atol=1e-12)

    def test_an_arc_is_carried_round_the_axis_in_the_direction_of_the_view(self):
        """``pivot + [r sin az, r cos az, height]``: at azimuth 0 the camera is
        a radius north of the axis. MUTATION: swap ``sin`` and ``cos``."""
        for name in ("arcpan1-030-p23", "arcpan1-075-p23"):
            with self.subTest(route=name):
                traj = built(name, tremor=False)
                radius = load_route(name)["radius_m"]
                for frame in traj.frames:
                    az = math.radians(frame.az)
                    np.testing.assert_allclose(
                        frame.position - traj.c_ref,
                        [radius * math.sin(az), radius * math.cos(az), 0.0], atol=1e-9)
                np.testing.assert_allclose(traj.frames[0].position - traj.c_ref,
                                           [0.0, radius, 0.0], atol=1e-12)

    def test_the_arm_follows_the_body_not_the_tremor(self):
        """The position uses the nominal azimuth, so with and without the
        tremor it is the same."""
        with_tremor, without = built("arcpan1-075-p23"), built("arcpan1-075-p23", tremor=False)
        for a, b in zip(with_tremor.frames, without.frames):
            np.testing.assert_allclose(a.position, b.position, atol=1e-12)


class RateCap(unittest.TestCase):
    """MUTATION (a): ignore ``rate_cap_deg_s`` in ``_build_pan``."""

    def test_a_route_faster_than_its_cap_does_not_build(self):
        route = short_route(rate_cap_deg_s=45)
        route["pans"][0]["speed_deg_s"] = 50
        with self.assertRaises(ValueError) as caught:
            trajectory.build(route, FPS, SEED)
        self.assertIn("rate_cap_deg_s", str(caught.exception))

    def test_the_tremor_counts_towards_the_cap(self):
        """20 deg/s is under a cap of 21 on its own; the hand's shake is not."""
        route = short_route(rate_cap_deg_s=21)
        route["pans"][0]["speed_deg_s"] = 20
        trajectory.build(route, FPS, SEED)
        route["tremor"] = {"yaw_deg": 0.3, "pitch_deg": 0.3, "roll_deg": 0.3, "band_hz": [0.5, 3.0]}
        with self.assertRaises(ValueError):
            trajectory.build(route, FPS, SEED)

    def test_the_cap_is_compared_with_the_measured_peak(self):
        """A cap a hair above the peak builds, a hair below does not."""
        route = short_route()
        route["pans"][0]["speed_deg_s"] = 40
        route["rate_cap_deg_s"] = 40.01
        trajectory.build(route, FPS, SEED)
        route["rate_cap_deg_s"] = 39.9
        with self.assertRaises(ValueError):
            trajectory.build(route, FPS, SEED)

    def test_the_shipped_caps_leave_room_for_other_seeds(self):
        """A seed that made a shipped route raise would break whichever case
        happens to use it, so the caps are not set at the seed 7 peak: eight
        more seeds build, and each stays 5 deg/s under its cap."""
        for name in ("pan1-p23", "pan1-fast-p23"):
            with self.subTest(route=name):
                route = load_route(name)
                for seed in range(8):
                    traj = trajectory.build(route, 5, seed)
                    peak = max(f.angular_rate_deg_s for f in traj.frames)
                    self.assertLess(peak, route["rate_cap_deg_s"] - 5.0, f"seed {seed}")


class RateBudget(unittest.TestCase):
    """The ``FullAttitudeRateBudget`` check of ``test_trajectory.py``, on every
    pan route: the FULL attitude's rate (angle between consecutive bases as
    rotations, not forward alone) at ~100 Hz over the whole panning interval
    stays at or below the route's cap. MUTATION: the cap check removed does not
    fail this, ``RateCap`` does; what this catches is a tremor or a profile
    that pushes a shipped route over its budget (scale the tremor 4x)."""

    def peak(self, traj):
        start, end = traj.holds[0].to_ms, traj.holds[1].from_ms
        ts = np.arange(float(start), float(end) + 1e-9, 10.0)
        bases = [traj.pose_at(t)[1] for t in ts]
        rates = [attitude_angle_deg(a, b) / 0.01 for a, b in zip(bases, bases[1:])]
        return max(rates)

    def test_every_pan_route_stays_inside_its_cap(self):
        for name in PAN_ROUTES:
            with self.subTest(route=name):
                route = load_route(name)
                peak = self.peak(built(name))
                self.assertLessEqual(peak, route["rate_cap_deg_s"] + 1e-4)
                # Not vacuous: the measure does see the pan, and the tremor on top of it.
                speed = max(p["speed_deg_s"] for p in route["pans"])
                self.assertGreater(peak, speed + 1.0)

    def test_the_old_45_deg_s_budget_holds_for_the_20_deg_s_routes(self):
        for name in PAN_ROUTES:
            if name == "pan1-fast-p23":
                continue
            with self.subTest(route=name):
                self.assertLessEqual(self.peak(built(name)), 45.0 + 1e-4)

    def test_the_fast_route_at_its_worst_seeds(self):
        for seed in (1, 42):
            with self.subTest(seed=seed):
                self.assertLessEqual(self.peak(built("pan1-fast-p23", seed)), 60.0 + 1e-4)

    def test_without_the_tremor_the_peak_is_the_speed(self):
        for name in PAN_ROUTES:
            with self.subTest(route=name):
                speed = max(p["speed_deg_s"] for p in load_route(name)["pans"])
                self.assertAlmostEqual(self.peak(built(name, tremor=False)), speed, delta=1e-3)


class Tremor(unittest.TestCase):
    def test_the_same_seed_is_identical_and_a_different_seed_differs(self):
        """MUTATION: ignore the seed (always seed 0) -- the second half fails;
        draw from ``default_rng()`` unseeded -- the first half fails."""
        route = load_route("panshort-p23")
        a = trajectory.build(route, FPS, 7)
        b = trajectory.build(route, FPS, 7)
        c = trajectory.build(route, FPS, 8)
        for fa, fb in zip(a.frames, b.frames):
            np.testing.assert_array_equal(fa.basis.forward, fb.basis.forward)
            np.testing.assert_array_equal(fa.basis.right, fb.basis.right)
            self.assertEqual(fa.angular_rate_deg_s, fb.angular_rate_deg_s)
        worst = max(attitude_angle_deg(fa.basis, fc.basis) for fa, fc in zip(a.frames, c.frames))
        self.assertGreater(worst, 0.2)

    def test_a_missing_seed_is_seed_zero(self):
        route = load_route("panshort-p23")
        bare, zero = trajectory.build(route, FPS), trajectory.build(route, FPS, 0)
        for fa, fb in zip(bare.frames, zero.frames):
            np.testing.assert_array_equal(fa.basis.forward, fb.basis.forward)

    def test_without_a_tremor_the_seed_changes_nothing(self):
        route = without_tremor(load_route("panshort-p23"))
        a, b = trajectory.build(route, FPS, 1), trajectory.build(route, FPS, 2)
        for fa, fb in zip(a.frames, b.frames):
            np.testing.assert_array_equal(fa.basis.forward, fb.basis.forward)
            np.testing.assert_array_equal(fa.basis.right, fb.basis.right)

    def test_the_tremor_is_drawn_from_seed_sequence_child_3(self):
        """Phases then weights, axis by axis in yaw, pitch, roll order, five
        sinusoids log-spaced over the band, from child 3 of
        ``SeedSequence(seed).spawn(10)``, scaled to the rms. Recomputed here from
        that recipe and compared with what the poses show.

        MUTATION: draw from child 4 (the frame noise's) or child 2 (the gyro's)."""
        rms = {"yaw_deg": 0.4, "pitch_deg": 0.2, "roll_deg": 0.1}
        ts, observed = tremor_series(long_route(), 11)

        rng = np.random.default_rng(np.random.SeedSequence(11).spawn(10)[3])
        freqs = np.geomspace(0.5, 3.0, 5)
        for column, axis in enumerate(("yaw_deg", "pitch_deg", "roll_deg")):
            phases = rng.uniform(0.0, 2.0 * math.pi, 5)
            weights = rng.uniform(0.5, 1.5, 5)
            scale = rms[axis] / math.sqrt(float(np.sum(weights ** 2)) / 2.0)
            expected = scale * np.sum(
                weights[:, None] * np.sin(2.0 * math.pi * freqs[:, None] * ts[None, :] / 1000.0
                                          + phases[:, None]), axis=0)
            np.testing.assert_allclose(observed[:, column], expected, atol=2e-4,
                                       err_msg=f"{axis} is not the child 3 series")

    def test_each_axis_has_the_rms_it_was_given(self):
        """The long-run rms of a sum of sinusoids of different frequencies is
        ``sqrt(sum w^2 / 2)``, and the scale is derived from it. Measured over
        a minute, within 3 % (measured: 0.2 % at most).
        MUTATION: scale by the sum of the weights instead of their rss."""
        _, observed = tremor_series(long_route(), 3)
        for column, rms in enumerate((0.4, 0.2, 0.1)):
            self.assertAlmostEqual(float(np.sqrt(np.mean(observed[:, column] ** 2))) / rms,
                                   1.0, delta=0.03)

    def test_the_power_sits_on_five_log_spaced_lines_in_the_band(self):
        """A Hann-windowed spectrum of the yaw tremor has its power on the lines
        at 0.5, 0.78, 1.22, 1.92 and 3.0 Hz (log-spaced over [0.5, 3]) and
        essentially none anywhere else. MUTATION: space them linearly."""
        _, observed = tremor_series(long_route(), 3)
        series = observed[:, 0]
        window = np.hanning(len(series))
        power = np.abs(np.fft.rfft((series - series.mean()) * window)) ** 2
        freq = np.fft.rfftfreq(len(series), d=0.01)
        total = float(power.sum())
        lines = np.geomspace(0.5, 3.0, 5)
        on_lines = np.zeros(len(freq), dtype=bool)
        for line in lines:
            near = np.abs(freq - line) < 0.08
            on_lines |= near
            self.assertGreater(float(power[near].sum()) / total, 0.01, f"no line at {line:.2f} Hz")
        self.assertGreater(float(power[on_lines].sum()) / total, 0.97)

    def test_the_axes_are_independent_and_each_can_be_off(self):
        """A roll-only tremor leaves the optical axis exactly on the nominal
        path, and changing the yaw rms leaves the pitch series where it was (all
        three axes are drawn whatever their rms). MUTATION: draw only the axes
        whose rms is non-zero."""
        _, observed = tremor_series(long_route(yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.3), 3)
        np.testing.assert_allclose(observed[:, :2], 0.0, atol=1e-9)
        self.assertGreater(float(np.abs(observed[:, 2]).max()), 0.3)

        _, a = tremor_series(long_route(yaw_deg=1.0), 3)
        _, b = tremor_series(long_route(yaw_deg=0.0), 3)
        np.testing.assert_allclose(a[:, 1:], b[:, 1:], atol=1e-9)
        self.assertGreater(float(np.abs(a[:, 0]).max()), 0.5)
        np.testing.assert_allclose(b[:, 0], 0.0, atol=1e-9)

    def test_the_rate_the_frames_report_includes_the_tremor(self):
        """``angular_rate_deg_s`` is the truth the gyro stream reads: the
        magnitude of the motion record at the same instant agrees with it (the
        gyro's 16.7 ms central difference against the 2 ms one). MUTATION: a
        rate computed from the nominal pan alone."""
        traj = built("pan1-p23")
        records = {r["t_event_ms"]: r["rate"] for r in sensors.motion_events(traj)}
        checked = 0
        for frame in traj.frames:
            rate = records.get(frame.t_capture_ms)
            if rate is None or not 2000 < frame.t_capture_ms < 20000:
                continue
            checked += 1
            magnitude = math.hypot(rate["alpha"], rate["beta"], rate["gamma"])
            self.assertAlmostEqual(magnitude, frame.angular_rate_deg_s, delta=0.15)
        self.assertGreater(checked, 400)
        spread = np.std([f.angular_rate_deg_s for f in traj.frames if 3000 < f.t_capture_ms < 19000])
        self.assertGreater(spread, 1.0, "the tremor should be moving the rate around the 20")


class Validation(unittest.TestCase):
    def build_with(self, edit):
        route = short_route()
        edit(route)
        return trajectory.build(route, FPS, SEED)

    def test_a_route_that_cannot_mean_anything_is_refused(self):
        def second_pan(**fields):
            def edit(route):
                route["pans"].append({"alt": 20.0, "turn_deg": 30, "speed_deg_s": 20,
                                      "ramp_s": 0.5, "direction": "cw", **fields})
            return edit

        cases_ = {
            "no pans": lambda r: r.update(pans=[]),
            "no from_az on the first pan": lambda r: r["pans"][0].pop("from_az"),
            "a bad direction": lambda r: r["pans"][0].update(direction="left"),
            "a zero speed": lambda r: r["pans"][0].update(speed_deg_s=0),
            "a negative ramp": lambda r: r["pans"][0].update(ramp_s=-0.1),
            "shorter than its own ramps": lambda r: r["pans"][0].update(turn_deg=9.0),
            "a from_az that is not where the last ended": second_pan(from_az=75.0),
            "an altitude jump between pans": second_pan(alt=25.0),
            "a negative radius": lambda r: r.update(radius_m=-1),
            "a tremor with no band": lambda r: r.update(tremor={"yaw_deg": 0.3}),
            "a negative tremor": lambda r: r.update(tremor={"yaw_deg": -0.3, "band_hz": [0.5, 3.0]}),
        }
        for label, edit in cases_.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    self.build_with(edit)

    def test_a_from_az_that_is_where_the_last_ended_is_accepted_through_the_wrap(self):
        route = short_route()
        route["pans"].append({"alt": 20.0, "from_az": 60.0, "turn_deg": 30, "speed_deg_s": 20,
                              "ramp_s": 0.5, "direction": "cw"})
        route["pans"][0]["turn_deg"] = 420.0
        traj = trajectory.build(route, FPS, SEED)
        self.assertAlmostEqual(unwrapped_az(traj)[-1], 450.0, delta=1e-6)

    def test_a_pan_with_no_ramp_starts_at_speed(self):
        route = short_route()
        route["pans"][0]["ramp_s"] = 0.0
        traj = trajectory.build(route, FPS, SEED)
        self.assertAlmostEqual(secant_rate(traj, 2000.0, 5.0), 20.0, delta=1e-6)
        self.assertEqual(traj.duration_ms, 1000 + 3000 + 1000)


class LegacyRoutes(unittest.TestCase):
    def test_a_seed_changes_nothing_for_the_old_kinds(self):
        """``build(route, fps, seed=None)`` keeps today's behaviour for the
        existing routes. MUTATION: draw anything from the seed in the other
        kinds."""
        route = json.loads((ROUTES / "shortpan.json").read_text(encoding="utf-8"))
        bare, seeded = trajectory.build(route, 5), trajectory.build(route, 5, seed=123)
        self.assertEqual(len(bare.frames), len(seeded.frames))
        for a, b in zip(bare.frames, seeded.frames):
            np.testing.assert_array_equal(a.position, b.position)
            np.testing.assert_array_equal(a.basis.forward, b.basis.forward)
            self.assertEqual(a.angular_rate_deg_s, b.angular_rate_deg_s)
        self.assertEqual(bare.holds, seeded.holds)

    def test_an_unknown_kind_is_still_refused(self):
        route = json.loads((ROUTES / "shortpan.json").read_text(encoding="utf-8"))
        route["kind"] = "pans"
        with self.assertRaises(ValueError):
            trajectory.build(route, 5)


class FixturesRebuildByteIdentical(unittest.TestCase):
    """The existing routes are unchanged: the committed fixtures' ``truth/``
    rebuilds byte for byte through ``build_case`` (which now passes the seed and
    may write ``truth/route.json``). MUTATION: write ``truth/route.json`` for
    every route, or let the seed reach the old kinds."""

    NAMES = ("chartyard-shortpan-60", "chartyard-shortpan-70")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dirs = {}
        for name in cls.NAMES:
            definition = json.loads((ROOT / "cases" / f"{name}.json").read_text(encoding="utf-8"))
            cls.dirs[name] = cases.build_case(definition, pathlib.Path(cls.tmp.name),
                                              cases.flat_renderer)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_trajectory_jsonl_is_byte_identical(self):
        for name in self.NAMES:
            with self.subTest(case=name):
                committed = (FIXTURES / name / "truth" / "trajectory.jsonl").read_bytes()
                rebuilt = (self.dirs[name] / "truth" / "trajectory.jsonl").read_bytes()
                self.assertGreater(len(committed), 20000)
                self.assertEqual(rebuilt, committed)

    def test_the_rest_of_truth_is_too_and_gains_no_route_file(self):
        for name in self.NAMES:
            with self.subTest(case=name):
                committed = sorted(p.name for p in (FIXTURES / name / "truth").iterdir())
                rebuilt = sorted(p.name for p in (self.dirs[name] / "truth").iterdir())
                self.assertEqual(rebuilt, committed)
                self.assertNotIn("route.json", rebuilt)
                for file in ("holds.json", "reference.json", "camera.json"):
                    self.assertEqual((self.dirs[name] / "truth" / file).read_bytes(),
                                     (FIXTURES / name / "truth" / file).read_bytes())


class PanCaseDirectory(unittest.TestCase):
    """``cases.build_case`` on a pan route, with the flat renderer and the
    sensor realism on (so the streams are generated from the pan's poses)."""

    @classmethod
    def definition(cls, seed):
        return {"case_id": f"pan-test-{seed}", "scene": "chartyard", "route": "arcpan1-030-p23",
                "camera": {"width": 36, "height": 64, "fov_short_deg": 41.14}, "fps": 5,
                "seed": seed, "expected": "positive", "profile": "realism-v1", "realism": {}}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        cls.a = cases.build_case(cls.definition(7), root / "a", cases.flat_renderer)
        cls.b = cases.build_case(cls.definition(8), root / "b", cases.flat_renderer)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_case_directory_carries_the_route_and_the_axis(self):
        route = load_route("arcpan1-030-p23")
        written = json.loads((self.a / "truth" / "route.json").read_text(encoding="utf-8"))
        self.assertEqual(written, route)
        self.assertEqual(written["kind"], "pan")
        reference = json.loads((self.a / "truth" / "reference.json").read_text(encoding="utf-8"))
        np.testing.assert_allclose(reference["c_ref"], [0.0, 0.0, 1.4], atol=1e-12)
        holds = json.loads((self.a / "truth" / "holds.json").read_text(encoding="utf-8"))
        self.assertEqual([(h["from_ms"], h["to_ms"]) for h in holds], [(0, 1000), (20750, 21750)])

    def test_the_case_seed_reaches_the_tremor(self):
        """MUTATION: ``build_case`` calls ``build(route, fps)`` without the seed
        (every case then has seed 0's tremor)."""
        def truth_poses(directory):
            lines = (directory / "truth" / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
            return [json.loads(line) for line in lines]

        # The case directories differ only in seed. Their truth poses agree in
        # the holds and on the nominal path, and part company by the tremor's
        # size (0.3 degrees rms) in the pan. With the seed ignored they would
        # be the same bytes.
        poses_a, poses_b = truth_poses(self.a), truth_poses(self.b)
        self.assertEqual(len(poses_a), len(poses_b))
        self.assertEqual(poses_a[0], poses_b[0])
        apart = [abs(float(wrap_deg(x["az"] - y["az"]))) for x, y in zip(poses_a, poses_b)]
        self.assertGreater(max(apart), 0.1)
        self.assertNotEqual((self.a / "truth" / "trajectory.jsonl").read_bytes(),
                            (self.b / "truth" / "trajectory.jsonl").read_bytes())

    def test_the_streams_were_generated_from_the_pan(self):
        records = [json.loads(line) for line in
                   (self.a / "input" / "observations.jsonl").read_text(encoding="utf-8").splitlines()]
        kinds = {r["kind"] for r in records}
        self.assertEqual(kinds, {"frame", "orientation", "motion"})
        frames = [r for r in records if r["kind"] == "frame"]
        self.assertEqual(len(frames), 21750 // 200 + 1)
        # The relative stream turns through the pan: its last reading is far from its first.
        relative = [r for r in records if r["kind"] == "orientation" and not r["absolute"]]
        self.assertGreater(len(relative), 100)

    def test_the_truth_hash_covers_the_route_file(self):
        """CONTRACT.md's recipe: the SHA-256 of the concatenated per-file digests
        over every file under ``truth/``, sorted by relative path."""
        def truth_hash(skip=None):
            files = sorted((p for p in (self.a / "truth").rglob("*")
                            if p.is_file() and p.name != skip),
                           key=lambda p: p.relative_to(self.a / "truth").as_posix())
            joined = "".join(hashlib.sha256(p.read_bytes()).hexdigest() for p in files)
            return hashlib.sha256(joined.encode("ascii")).hexdigest()

        manifest = json.loads((self.a / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["hashes"]["truth"], truth_hash())
        self.assertNotEqual(manifest["hashes"]["truth"], truth_hash(skip="route.json"))
        self.assertEqual(manifest["route"], "arcpan1-030-p23")


if __name__ == "__main__":
    unittest.main()
