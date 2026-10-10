# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The simulator's sensor realism (panorama spec 8.3 T01, 13.1, 13.2).

A case definition with a ``realism`` block gets the faults a phone's fused
sensors really have: a relative and an absolute orientation stream on a 60 Hz
event pump with a reading age, a gyro with scale error, bias and rounding,
frame times that arrive late, all seeded from the case's own seed. A case
without one must keep every byte it has ever had. This file grades both halves,
and pins the one fact about ``motion_events`` that a proposal (issue #897) got
backwards.

Every test names the mutant it must catch. The four the task names:

  (a) ignore the case seed in the realism sources:
      ``Seeds.test_a_different_seed_differs_in_every_source`` and
      ``RealismCaseDirectory.test_a_different_seed_changes_the_case_directory``;
  (b) drop ``gyro_scale_err`` from the relative stream:
      ``RelativeStream.test_the_relative_stream_integrates_to_the_gyro_scale``;
  (c) write ``alpha = rate[2], beta = rate[0], gamma = rate[1]`` (the #897
      proposal): the three ``MotionAxes`` tests;
  (d) omit the duplicate ``deviceorientation`` records in absolute-only mode:
      ``AbsoluteStream.test_absolute_only_writes_every_absolute_record_twice``.

THE COMMITTED FIXTURES. The brief for this task asked for the two fixtures'
``hashes.observations`` to be reproduced from their case definitions. They
cannot be, as written: both fixtures were recorded (commit 3116d8d9) before
``motion_events`` existed (commit 8845c4a6, issue #105), so their
``observations.jsonl`` carries frames and orientation records only, while
today's builder also writes the motion channel for the same definition.
``LegacyCases.test_the_committed_fixtures_reproduce_without_the_motion_channel``
proves what is true instead: the frame and orientation lines are reproduced
byte for byte, so their hash is reproduced once the motion lines are set aside.
The motion channel is pinned separately, by
``LegacyCases.test_a_legacy_case_keeps_today_s_bytes``.
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
from sim.geometry import look_basis, wrap_deg
from sim.trajectory import Trajectory

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


# ---------------------------------------------------------------------------
# Synthetic trajectories and blocks
# ---------------------------------------------------------------------------

def _trajectory(az_at, alt_at, seconds) -> Trajectory:
    """A trajectory whose attitude is ``look_basis(az_at(t), alt_at(t))`` with
    t in seconds, clamped to ``[0, seconds]``. No frames, no holds: the sensor
    generators read only ``pose_at`` and ``duration_ms``."""
    duration_ms = seconds * 1000.0

    def pose_at(t_ms):
        t = min(max(float(t_ms), 0.0), duration_ms) / 1000.0
        return np.zeros(3), look_basis(az_at(t), alt_at(t))

    return Trajectory(frames=[], holds=[], c_ref=np.zeros(3), pose_at=pose_at,
                      duration_ms=duration_ms)


def yaw_trajectory(rate_deg_s, alt_deg, seconds, az0=0.0) -> Trajectory:
    """A pure turn about the vertical at ``rate_deg_s`` (azimuth increasing
    for a positive rate), with the camera held at ``alt_deg`` above the
    horizon and no roll. Rate 0 is a phone held still."""
    return _trajectory(lambda t: az0 + rate_deg_s * t, lambda t: alt_deg, seconds)


def pitch_trajectory(rate_deg_s, seconds, az=0.0, alt0=0.0) -> Trajectory:
    """A pure pitch-up about the device's own x axis at ``rate_deg_s``."""
    return _trajectory(lambda t: az, lambda t: alt0 + rate_deg_s * t, seconds)


def _overlay(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _overlay(out[key], value)
        else:
            out[key] = value
    return out


# Every fault off, every random source live but scaled to zero, the pump and the
# rounding as the spec has them. A test turns on exactly the fault it grades.
CLEAN = {
    "gyro_scale_err": 0.0,
    "relative": {"yaw_zero_deg": 0.0, "drift_deg_min": 0.0, "noise_deg": 0.0},
    "absolute": {"noise": {"sigma_deg": 0.0, "tau_s": 0.3},
                 "sinusoid": {"amp_deg": 0.0, "phase_deg": 0.0},
                 "tilt_noise_deg": 0.0},
    "motion": {"bias_deg_s": 0.0, "noise_deg_s": 0.0},
}


def clean(**patch) -> dict:
    """A resolved realism block: CLEAN, with ``patch`` laid over it."""
    return sensors.resolve_realism(_overlay(CLEAN, patch))


def readings(traj, spec, seed=7) -> list:
    return sensors.realism_readings(traj, spec, seed)


def orientation(records, *, absolute) -> list:
    """The orientation records of one stream, in time order."""
    return [r for r in records if r["kind"] == "orientation" and r["absolute"] is absolute]


def motion(records) -> list:
    return [r for r in records if r["kind"] == "motion"]


def _is_multiple(x, step) -> bool:
    return abs(x / step - round(x / step)) < 1e-6


# ---------------------------------------------------------------------------
# The axis slots of motion_events (RD finding 1; issue #897's premise is inverted)
# ---------------------------------------------------------------------------

class MotionAxes(unittest.TestCase):
    """W3C ``rotationRate.alpha``, ``beta`` and ``gamma`` are the rates about
    the device's x, y and z (the current specification and Chromium's
    ``device_motion_event_pump.cc``), and ``motion_events`` has always written
    them in that order. Its docstring said z, x, y, and issue #897 proposed
    changing the code to match. These pin the code.

    MUTATION (c): write ``alpha = rate[2], beta = rate[0], gamma = rate[1]``.
    A yaw then lands in gamma, a pitch in beta, and all three tests fail.
    """

    def test_a_portrait_yaw_at_pitch_zero_lands_in_beta_negative_for_rising_azimuth(self):
        # Azimuth is clockwise seen from above, so a rising azimuth is a
        # rotation about the device's up axis (y, at pitch 0) in the NEGATIVE
        # right-handed sense.
        rising = sensors.motion_events(yaw_trajectory(10.0, 0.0, 4.0))
        self.assertGreater(len(rising), 200)
        for e in rising:
            rate = e["rate"]
            self.assertAlmostEqual(rate["beta"], -10.0, places=3, msg=e)
            self.assertAlmostEqual(rate["alpha"], 0.0, places=3, msg=e)
            self.assertAlmostEqual(rate["gamma"], 0.0, places=3, msg=e)
        falling = sensors.motion_events(yaw_trajectory(-10.0, 0.0, 4.0, az0=90.0))
        for e in falling:
            self.assertAlmostEqual(e["rate"]["beta"], 10.0, places=3, msg=e)

    def test_a_pure_pitch_up_lands_in_alpha_positive(self):
        events = sensors.motion_events(pitch_trajectory(10.0, 4.0))
        self.assertGreater(len(events), 200)
        for e in events:
            rate = e["rate"]
            self.assertAlmostEqual(rate["alpha"], 10.0, places=3, msg=e)
            self.assertAlmostEqual(rate["beta"], 0.0, places=3, msg=e)
            self.assertAlmostEqual(rate["gamma"], 0.0, places=3, msg=e)

    def test_the_realism_path_writes_the_same_slots(self):
        """The realism motion stream is the same function with faults added, so
        the slots hold there too: scale 2 %, no bias, no noise, rounded to 0.1."""
        spec = clean(gyro_scale_err=0.02)
        yaw = motion(readings(yaw_trajectory(10.0, 0.0, 4.0), spec))
        self.assertGreater(len(yaw), 200)
        for e in yaw:
            self.assertEqual(e["rate"], {"alpha": 0.0, "beta": -10.2, "gamma": 0.0}, e)
        pitch = motion(readings(pitch_trajectory(10.0, 4.0), spec))
        for e in pitch:
            self.assertEqual(e["rate"], {"alpha": 10.2, "beta": 0.0, "gamma": 0.0}, e)


# ---------------------------------------------------------------------------
# Seeds (issue #901)
# ---------------------------------------------------------------------------

class Seeds(unittest.TestCase):
    """Every realism source is seeded from the case's own seed, one child of
    ``SeedSequence(seed).spawn(10)`` each: 0 relative, 1 absolute, 2 motion.
    """

    @classmethod
    def setUpClass(cls):
        # The spec's own default block: every source live.
        cls.spec = sensors.resolve_realism({})
        cls.traj = yaw_trajectory(15.0, 23.0, 6.0)

    @staticmethod
    def _streams(records):
        return {"relative": orientation(records, absolute=False),
                "absolute": orientation(records, absolute=True),
                "motion": motion(records)}

    def test_the_same_seed_reproduces_every_record(self):
        first = readings(self.traj, self.spec, seed=7)
        second = readings(self.traj, self.spec, seed=7)
        self.assertTrue(first)
        self.assertEqual(first, second)

    def test_a_different_seed_differs_in_every_source(self):
        """MUTATION (a): build the SeedSequence from a constant instead of the
        case seed. Every stream is then identical across seeds and this fails,
        naming the first stream that did not move."""
        one = self._streams(readings(self.traj, self.spec, seed=7))
        two = self._streams(readings(self.traj, self.spec, seed=8))
        for name in ("relative", "absolute", "motion"):
            with self.subTest(stream=name):
                self.assertTrue(one[name])
                self.assertNotEqual(one[name], two[name],
                                    f"the {name} stream ignores the case seed")

    def test_one_streams_faults_do_not_move_anothers_noise(self):
        """Each source has its own child of the seed, so turning the motion
        noise up, or the compass noise down, leaves the relative stream alone.
        (One shared generator would shift every later draw.)"""
        base = self._streams(readings(self.traj, self.spec, seed=7))
        quieter = _overlay(self.spec, {"motion": {"noise_deg_s": 0.2},
                                       "absolute": {"noise": {"sigma_deg": 0.5}}})
        moved = self._streams(readings(self.traj, quieter, seed=7))
        self.assertEqual(base["relative"], moved["relative"])
        self.assertNotEqual(base["motion"], moved["motion"])
        self.assertNotEqual(base["absolute"], moved["absolute"])

    def test_the_yaw_zero_is_seeded(self):
        """``yaw_zero_deg: "random"`` is a draw from the case seed, so six seeds
        give six different first readings, and a number is used as written."""
        still = yaw_trajectory(0.0, 23.0, 1.0)      # alpha 0 at az 0
        firsts = {orientation(readings(still, sensors.resolve_realism(
                      _overlay(CLEAN, {"relative": {"yaw_zero_deg": "random"}})), seed=s),
                      absolute=False)[0]["alpha"] for s in range(1, 7)}
        self.assertEqual(len(firsts), 6, firsts)
        fixed = orientation(readings(still, clean(relative={"yaw_zero_deg": 33.0})),
                            absolute=False)[0]["alpha"]
        self.assertEqual(fixed, 33.0)


# ---------------------------------------------------------------------------
# The relative stream
# ---------------------------------------------------------------------------

def _integrated_yaw(records, seconds) -> float:
    """The yaw a stream integrates to over a constant-rate turn of ``seconds``,
    as the least-squares slope of its unwrapped alpha against its event times.
    The slope, not last minus first: the last record trails the end of the turn
    by up to a threshold plus a reading age, which at 20 deg/s is more than the
    0.05 % being graded (0.18 degrees of 367)."""
    t = np.array([r["t_event_ms"] for r in records]) / 1000.0
    alpha = np.array([r["alpha"] for r in records])
    unwrapped = np.concatenate([[0.0], np.cumsum(wrap_deg(np.diff(alpha)))])
    return abs(float(np.polyfit(t, unwrapped, 1)[0])) * seconds


class RelativeStream(unittest.TestCase):

    def test_the_relative_stream_integrates_to_the_gyro_scale(self):
        """A 360-degree turn at 20 deg/s reads 360 * (1 + gyro_scale_err) degrees
        of yaw, within 0.05 %.

        MUTATION (b): drop ``gyro_scale_err`` from the relative stream. It then
        integrates to 360 and the 2 % case fails by a factor of 40."""
        traj = yaw_trajectory(20.0, 23.0, 18.0)
        for scale in (0.02, 0.0, -0.015):
            with self.subTest(gyro_scale_err=scale):
                rel = orientation(readings(traj, clean(gyro_scale_err=scale)), absolute=False)
                self.assertGreater(len(rel), 900)
                expected = 360.0 * (1.0 + scale)
                self.assertLess(abs(_integrated_yaw(rel, 18.0) / expected - 1.0), 5e-4)

    def test_records_have_the_pinned_shape(self):
        rel = orientation(readings(yaw_trajectory(20.0, 23.0, 5.0),
                                   clean(relative={"latency_ms": 5})), absolute=False)
        self.assertTrue(rel)
        times = [r["t_event_ms"] for r in rel]
        self.assertEqual(times, sorted(times))
        for r in rel:
            self.assertEqual(list(r), ["kind", "event", "t_event_ms", "t_receive_ms",
                                       "alpha", "beta", "gamma", "absolute"])
            self.assertEqual(r["event"], "deviceorientation")
            self.assertIs(r["absolute"], False)
            self.assertEqual(r["t_receive_ms"] - r["t_event_ms"], 5)
            self.assertTrue(0.0 <= r["alpha"] < 360.0, r)
            for angle in ("alpha", "beta", "gamma"):
                self.assertTrue(_is_multiple(r[angle], 0.1), (angle, r))
        # Portrait at 23 degrees above the horizon: beta is 90 + pitch.
        self.assertAlmostEqual(rel[0]["beta"], 113.0, places=1)
        self.assertAlmostEqual(rel[0]["gamma"], 0.0, places=1)

    def test_events_land_on_the_pump_grid_and_the_reading_age_is_uniform(self):
        """``t_event_ms`` is the tick, and the reading is the pose at tick minus
        a uniform age in [0, 16.7) ms. At 20 deg/s that is a lag of age * 20
        degrees behind the pose AT the tick: mean 8.3 ms, never negative, never
        a period and a half. (The 0.05-degree rounding is 2.5 ms of that.)"""
        rate = 20.0
        rel = orientation(readings(yaw_trajectory(rate, 23.0, 30.0), clean()), absolute=False)
        period = 1000.0 / 60.0
        lags = []
        for r in rel:
            tick = round(r["t_event_ms"] / period)
            self.assertLessEqual(abs(r["t_event_ms"] - tick * period), 0.51, r)
            truth_alpha = (-(rate * r["t_event_ms"] / 1000.0)) % 360.0
            lags.append(float(wrap_deg(r["alpha"] - truth_alpha)) / rate * 1000.0)
        lags = np.array(lags)
        self.assertGreater(len(lags), 1500)
        self.assertAlmostEqual(float(lags.mean()), period / 2.0, delta=1.0)
        self.assertGreater(float(lags.min()), -3.5)
        self.assertLess(float(lags.max()), period + 3.5)

    def test_the_event_rate_follows_the_turn(self):
        """15 Hz at a 2 deg/s turn and 60 Hz at 20, each within 3: a 0.1-degree
        threshold on a 60 Hz pump, with the reading age. Pooled over four seeds
        so the check does not lean on one draw (at 20 deg/s the mean is 57.6: a
        reading 11.7 ms older than the last one is 0.23 degrees behind it, and
        sometimes that is under the threshold)."""
        for turn, expected in ((2.0, 15.0), (20.0, 60.0)):
            with self.subTest(turn_deg_s=turn):
                seconds = 60.0
                counts = [len(orientation(readings(yaw_trajectory(turn, 23.0, seconds),
                                                   clean(gyro=False), seed=seed), absolute=False))
                          for seed in (1, 2, 3, 4)]
                rate_hz = sum(counts) / (len(counts) * seconds)
                self.assertAlmostEqual(rate_hz, expected, delta=3.0, msg=counts)

    def test_a_still_phone_is_silent_whatever_the_tilt_noise(self):
        """Change-driven: a phone held still produces its first reading and then
        nothing, even with the spec's 0.05-degree tilt noise on the numbers.
        (If that noise reached the change test it would fire at 16 Hz.)"""
        still = yaw_trajectory(0.0, 23.0, 60.0, az0=40.0)
        spec = sensors.resolve_realism(_overlay(CLEAN, {
            "relative": {"noise_deg": 0.05, "drift_deg_min": 0.0}}))
        rel = orientation(readings(still, spec), absolute=False)
        self.assertEqual(len(rel), 1, rel[:5])

    def test_consecutive_records_differ_by_at_least_the_threshold(self):
        rel = orientation(readings(yaw_trajectory(3.0, 23.0, 30.0), clean()), absolute=False)
        steps = np.abs(wrap_deg(np.diff([r["alpha"] for r in rel])))
        self.assertGreater(len(steps), 200)
        self.assertGreaterEqual(float(steps.min()), 0.1 - 1e-6)

    def test_drift_adds_linearly_to_the_heading(self):
        """6 degrees a minute is 0.1 degrees a second: 6 degrees after a minute."""
        still = yaw_trajectory(0.0, 23.0, 60.0)       # truth alpha 0
        rel = orientation(readings(still, clean(relative={"drift_deg_min": 6.0})), absolute=False)
        self.assertEqual(rel[0]["alpha"], 0.0)
        self.assertAlmostEqual(rel[-1]["alpha"], 6.0, delta=0.25)
        self.assertGreater(len(rel), 50)

    def test_spikes_arrive_at_the_configured_rate_and_size(self):
        """Single readings displaced by plus or minus ``size_deg``, at
        ``rate_hz`` a second, within 30 %. A still phone makes them countable:
        everything else is one reading at the base heading. Two a second over
        100 seconds is 200 expected, a standard deviation of 14, so the 30 %
        band is four of them wide (at the spec's 0.2 Hz it would take 400
        seconds to say as much)."""
        seconds, rate_hz, size = 100.0, 2.0, 15.0
        still = yaw_trajectory(0.0, 23.0, seconds)
        for absolute in (False, True):
            with self.subTest(stream="absolute" if absolute else "relative"):
                spikes = {"rate_hz": rate_hz, "size_deg": size}
                spec = clean(**{("absolute" if absolute else "relative"): {"spikes": spikes}})
                stream = orientation(readings(still, spec), absolute=absolute)
                alphas = [float(wrap_deg(r["alpha"] - stream[0]["alpha"])) for r in stream]
                base = float(np.median(alphas))          # the heading a still phone sits at
                offsets = [a - base for a in alphas]
                found = [o for o in offsets if abs(o) > size / 2.0]
                expected = rate_hz * seconds
                self.assertAlmostEqual(len(found), expected, delta=0.3 * expected, msg=len(found))
                for o in found:
                    self.assertAlmostEqual(abs(o), size, delta=0.15)
                self.assertTrue(any(o > 0 for o in found) and any(o < 0 for o in found),
                                "spikes of one sign only")
                # A spike is two records: out, and back.
                self.assertGreaterEqual(len(stream), 1 + len(found))

    def test_no_spikes_unless_configured(self):
        still = yaw_trajectory(0.0, 23.0, 200.0)
        for block in ("relative", "absolute"):
            stream = orientation(readings(still, clean()), absolute=(block == "absolute"))
            self.assertEqual(len(stream), 1, block)


# ---------------------------------------------------------------------------
# The absolute stream
# ---------------------------------------------------------------------------

class AbsoluteStream(unittest.TestCase):

    def test_bias_declination_sinusoid_and_steps_add_to_the_truth(self):
        """A still phone at azimuth 30 turns the truth's alpha into a constant, so
        the stream is that constant plus exactly the configured offsets: bias,
        declination, ``amp * sin(heading + phase)`` (heading in the sense of
        alpha, minus the azimuth) and a 4-degree step at 10 s."""
        still = yaw_trajectory(0.0, 23.0, 20.0, az0=30.0)
        spec = clean(absolute={"bias_deg": 1.5, "declination_deg": 12.5,
                               "sinusoid": {"amp_deg": 2.0, "phase_deg": 40.0},
                               "steps": [{"t_s": 10.0, "deg": 4.0}]})
        ab = orientation(readings(still, spec), absolute=True)
        self.assertEqual(len(ab), 2, ab)
        base = (-30.0 + 1.5 + 12.5 + 2.0 * math.sin(math.radians(-30.0 + 40.0))) % 360.0
        self.assertAlmostEqual(ab[0]["alpha"], base, delta=0.06)
        self.assertAlmostEqual(float(wrap_deg(ab[1]["alpha"] - ab[0]["alpha"])), 4.0, delta=0.11)
        self.assertGreaterEqual(ab[1]["t_event_ms"], 10000)
        self.assertLess(ab[1]["t_event_ms"], 10000 + 34)      # within two ticks of the step
        for r in ab:
            self.assertEqual(r["event"], "deviceorientationabsolute")
            self.assertIs(r["absolute"], True)

    def test_declination_is_subtracted_from_the_compass_azimuth(self):
        """A compass reads magnetic north, so it reports the true azimuth MINUS
        the declination. The case carries the number in both the stream and
        ``scanner.json``, and a scanner that applies ``az + D`` must recover the
        truth: the stream's alpha is raised by D, which lowers its azimuth."""
        d = 12.5
        still = yaw_trajectory(0.0, 23.0, 2.0, az0=30.0)
        ab = orientation(readings(still, clean(absolute={"declination_deg": d})), absolute=True)
        compass_azimuth = (-ab[0]["alpha"]) % 360.0
        self.assertAlmostEqual(compass_azimuth, 30.0 - d, delta=0.06)
        self.assertAlmostEqual((compass_azimuth + d) % 360.0, 30.0, delta=0.06)

    def test_the_compass_noise_is_an_ornstein_uhlenbeck_process(self):
        """Stationary sd ``sigma_deg`` and one-step autocorrelation
        ``exp(-dt / tau_s)``. A threshold of 0 delivers every tick, so the
        series is evenly sampled."""
        sigma, tau = 3.0, 0.3
        spec = clean(absolute={"noise": {"sigma_deg": sigma, "tau_s": tau}, "threshold_deg": 0.0})
        still = yaw_trajectory(0.0, 23.0, 200.0, az0=0.0)
        ab = orientation(readings(still, spec, seed=3), absolute=True)
        self.assertEqual(len(ab), len(range(0, int(round(200.0 * 60)) + 1)))
        series = np.array([float(wrap_deg(r["alpha"])) for r in ab])
        series -= series.mean()
        self.assertAlmostEqual(float(series.std()), sigma, delta=0.4)
        lag1 = float(np.dot(series[:-1], series[1:]) / np.dot(series, series))
        self.assertAlmostEqual(lag1, math.exp(-(1.0 / 60.0) / tau), delta=0.02)

    def test_absolute_only_writes_every_absolute_record_twice(self):
        """Chromium with no relative sensor: every ``deviceorientationabsolute``
        sample also arrives as a ``deviceorientation`` event with ``absolute:
        true``, the same three angles at the same times, and no relative
        stream. The scanner must classify by ``absolute``, not by type (spec
        4.5).

        MUTATION (d): omit the duplicates. The count and the pairing fail."""
        traj = yaw_trajectory(15.0, 23.0, 6.0)
        records = readings(traj, clean(streams="absolute-only"))
        orient = [r for r in records if r["kind"] == "orientation"]
        originals = [r for r in orient if r["event"] == "deviceorientationabsolute"]
        copies = [r for r in orient if r["event"] == "deviceorientation"]
        self.assertGreater(len(originals), 100)
        self.assertEqual(len(copies), len(originals))
        self.assertTrue(all(r["absolute"] is True for r in orient))
        for original, copy_ in zip(originals, copies):
            self.assertEqual({k: v for k, v in copy_.items() if k != "event"},
                             {k: v for k, v in original.items() if k != "event"})
        # The copy follows its original, so the file lists them as a pair.
        for i, r in enumerate(orient):
            self.assertEqual(r["event"], "deviceorientationabsolute" if i % 2 == 0
                             else "deviceorientation")

    def test_both_streams_means_a_relative_stream_with_absolute_false(self):
        records = readings(yaw_trajectory(15.0, 23.0, 6.0), clean(streams="both"))
        rel = orientation(records, absolute=False)
        ab = orientation(records, absolute=True)
        self.assertTrue(rel and ab)
        self.assertEqual({r["event"] for r in rel}, {"deviceorientation"})
        self.assertEqual({r["event"] for r in ab}, {"deviceorientationabsolute"})


# ---------------------------------------------------------------------------
# Motion
# ---------------------------------------------------------------------------

class Motion(unittest.TestCase):

    def test_a_still_phone_reads_exact_zeros_on_most_samples(self):
        """0.02 deg/s of gyro noise rounded to 0.1 deg/s is exactly zero on all
        three axes about 96 % of the time (RD finding 11), as Chromium's is. The
        zeros are plain 0.0, not -0.0."""
        still = yaw_trajectory(0.0, 23.0, 60.0)
        stream = motion(readings(still, clean(motion={"noise_deg_s": 0.02, "round_deg_s": 0.1})))
        self.assertEqual(len(stream), 60 * 60 + 1)
        zeros = [e for e in stream if all(v == 0.0 for v in e["rate"].values())]
        self.assertGreaterEqual(len(zeros) / len(stream), 0.90)
        self.assertAlmostEqual(len(zeros) / len(stream), 0.964, delta=0.03)
        for e in zeros:
            self.assertEqual([str(v) for v in e["rate"].values()], ["0.0"] * 3)
        for e in stream:
            for v in e["rate"].values():
                self.assertTrue(_is_multiple(v, 0.1), e)

    def test_bias_and_scale_are_added_to_every_axis_and_the_turn(self):
        still = yaw_trajectory(0.0, 23.0, 2.0)
        biased = motion(readings(still, clean(motion={"bias_deg_s": 0.3})))
        for e in biased:
            self.assertEqual(e["rate"], {"alpha": 0.3, "beta": 0.3, "gamma": 0.3}, e)
        turning = motion(readings(yaw_trajectory(30.0, 0.0, 2.0),
                                  clean(gyro_scale_err=0.02, motion={"bias_deg_s": 0.3})))
        for e in turning:
            self.assertEqual(e["rate"]["beta"], round(-30.0 * 1.02 + 0.3, 1), e)

    def test_the_motion_grid_is_fixed_and_latency_is_added(self):
        spec = clean(motion={"pump_hz": 30, "latency_ms": 12})
        stream = motion(readings(yaw_trajectory(5.0, 23.0, 2.0), spec))
        self.assertEqual([e["t_event_ms"] for e in stream], [round(k * 1000.0 / 30) for k in range(61)])
        self.assertTrue(all(e["t_receive_ms"] - e["t_event_ms"] == 12 for e in stream))

    def test_gyro_false_omits_the_motion_channel(self):
        traj = yaw_trajectory(5.0, 23.0, 2.0)
        self.assertEqual(motion(readings(traj, clean(gyro=False))), [])
        self.assertTrue(motion(readings(traj, clean(gyro=True))))
        # ... and nothing else disappears with it.
        self.assertEqual(orientation(readings(traj, clean(gyro=False)), absolute=False),
                         orientation(readings(traj, clean(gyro=True)), absolute=False))


# ---------------------------------------------------------------------------
# Frame times
# ---------------------------------------------------------------------------

class CaptureTime(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        route = json.loads((ROOT / "routes" / "shortpan.json").read_text(encoding="utf-8"))
        cls.traj = trajectory.build(route, 5)

    def _records(self, mode, lag_ms=50):
        return sensors.frame_records(self.traj, capture_time={"mode": mode, "lag_ms": lag_ms})

    def test_delivery_stamps_the_frame_when_it_reaches_the_page(self):
        """Chromium's default I420 path: ``captureTime`` is the delivery time,
        the exposure plus the pipeline's lag, and the page cannot present a
        frame before it has one."""
        for lag, present_after in ((50, 60), (80, 90), (0, 60)):
            with self.subTest(lag_ms=lag):
                records = self._records("delivery", lag)
                self.assertEqual(len(records), len(self.traj.frames))
                for record, frame in zip(records, self.traj.frames):
                    self.assertEqual(record["t_capture_ms"], frame.t_capture_ms + lag)
                    self.assertEqual(record["t_present_ms"], frame.t_capture_ms + present_after)
                    self.assertGreaterEqual(record["t_present_ms"], record["t_capture_ms"])

    def test_sensor_stamps_the_exposure(self):
        for record, frame in zip(self._records("sensor"), self.traj.frames):
            self.assertEqual(record["t_capture_ms"], frame.t_capture_ms)
            self.assertEqual(record["t_present_ms"], frame.t_capture_ms + 60)
            self.assertGreaterEqual(record["t_present_ms"], record["t_capture_ms"])

    def test_none_carries_no_capture_time(self):
        for record, frame in zip(self._records("none"), self.traj.frames):
            self.assertIsNone(record["t_capture_ms"])
            self.assertEqual(record["t_present_ms"], frame.t_capture_ms + 60)

    def test_without_a_block_the_records_are_what_they_always_were(self):
        for record, frame in zip(sensors.frame_records(self.traj), self.traj.frames):
            self.assertEqual(record["t_capture_ms"], frame.t_capture_ms)
            self.assertEqual(record["t_present_ms"], frame.t_capture_ms + 60)

    def test_an_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            sensors.frame_records(self.traj, capture_time={"mode": "exposure"})


# ---------------------------------------------------------------------------
# The block itself
# ---------------------------------------------------------------------------

class ResolveRealism(unittest.TestCase):

    def test_an_empty_block_is_the_specs_defaults(self):
        spec = sensors.resolve_realism({})
        self.assertEqual(spec["gyro_scale_err"], 0.02)
        self.assertEqual(spec["relative"]["drift_deg_min"], 2.0)
        self.assertEqual(spec["relative"]["noise_deg"], 0.05)
        self.assertEqual(spec["absolute"]["noise"], {"sigma_deg": 3.0, "tau_s": 5.0})
        self.assertEqual(spec["absolute"]["sinusoid"], {"amp_deg": 2.0, "phase_deg": 40.0})
        self.assertEqual(spec["absolute"]["bias_deg"], 0.0)
        self.assertEqual(spec["capture_time"], {"mode": "delivery", "lag_ms": 50})
        self.assertEqual(spec["streams"], "both")
        self.assertIs(spec["gyro"], True)
        self.assertIsNone(spec["relative"]["spikes"])
        self.assertIsNone(spec["absolute"]["spikes"])

    def test_a_partial_block_keeps_its_own_values_and_fills_the_rest(self):
        given = {"absolute": {"noise": {"tau_s": 0.3}}, "frames": {"exposure_ms": 4}}
        spec = sensors.resolve_realism(given)
        self.assertEqual(spec["absolute"]["noise"], {"sigma_deg": 3.0, "tau_s": 0.3})
        self.assertEqual(spec["frames"], {"exposure_ms": 4})       # passes through untouched
        self.assertEqual(given, {"absolute": {"noise": {"tau_s": 0.3}}, "frames": {"exposure_ms": 4}})
        spec["absolute"]["noise"]["sigma_deg"] = 99.0              # not shared with the defaults
        self.assertEqual(sensors.REALISM_DEFAULTS["absolute"]["noise"]["sigma_deg"], 3.0)

    def test_spikes_fill_the_missing_key_from_the_specs_default(self):
        spec = sensors.resolve_realism({"relative": {"spikes": {"rate_hz": 1.0}}})
        self.assertEqual(spec["relative"]["spikes"], {"rate_hz": 1.0, "size_deg": 15.0})

    def test_nonsense_is_refused(self):
        for bad in ({"streams": "relative-only"}, {"capture_time": {"mode": "never"}},
                    {"relative": {"pump_hz": 0}}, {"motion": {"pump_hz": -60}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sensors.resolve_realism(bad)


# ---------------------------------------------------------------------------
# build_case
# ---------------------------------------------------------------------------

def _definition(case_id, seed, **extra) -> dict:
    definition = {
        "case_id": case_id, "scene": "chartyard", "route": "shortpan",
        "camera": {"width": 24, "height": 32, "fov_short_deg": 41.14}, "fps": 5,
        "seed": seed, "expected": "positive", "profile": "realism-v1",
        "realism": {"capture_time": {"mode": "delivery", "lag_ms": 80}},
    }
    definition.update(extra)
    return definition


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class RealismCaseDirectory(unittest.TestCase):
    """``build_case`` on a definition with a realism block, flat renderer."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(cls.tmp.name)
        extras = dict(grading={"still_pivot": True, "north_graded": False},
                      scanner_options={"focal_prior_scale": 1.1, "sensor_only": False,
                                       "declination_deg": 0})
        cls.definition = _definition("realism-a", 7, **extras)
        cls.a1 = cases.build_case(cls.definition, base / "one", cases.flat_renderer)
        cls.a2 = cases.build_case(cls.definition, base / "two", cases.flat_renderer)
        cls.bare = _definition("realism-b", 8)
        cls.b = cases.build_case(cls.bare, base / "three", cases.flat_renderer)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def _bytes(case_dir):
        return (case_dir / "input" / "observations.jsonl").read_bytes()

    def test_the_same_seed_gives_byte_identical_observations(self):
        self.assertTrue(self._bytes(self.a1))
        self.assertEqual(self._bytes(self.a1), self._bytes(self.a2))
        self.assertEqual(json.loads((self.a1 / "manifest.json").read_text())["hashes"]["observations"],
                         json.loads((self.a2 / "manifest.json").read_text())["hashes"]["observations"])

    def test_a_different_seed_changes_the_case_directory(self):
        """MUTATION (a), through ``build_case``: the case seed never reaches the
        sensors."""
        a = [r for r in _read_jsonl(self.a1 / "input" / "observations.jsonl") if r["kind"] != "frame"]
        b = [r for r in _read_jsonl(self.b / "input" / "observations.jsonl") if r["kind"] != "frame"]
        self.assertTrue(a and b)
        self.assertNotEqual(a, b)

    def test_the_observations_carry_every_stream_sorted_by_delivery_time(self):
        records = _read_jsonl(self.a1 / "input" / "observations.jsonl")
        delivery = [r["t_present_ms"] if r["kind"] == "frame" else r["t_receive_ms"] for r in records]
        self.assertEqual(delivery, sorted(delivery))
        kinds = {(r["kind"], r.get("event")) for r in records}
        self.assertEqual(kinds, {("frame", None), ("motion", None),
                                 ("orientation", "deviceorientation"),
                                 ("orientation", "deviceorientationabsolute")})

    def test_frame_times_follow_the_capture_time_block(self):
        truth = {r["frame_id"]: r["t_capture_ms"]
                 for r in _read_jsonl(self.a1 / "truth" / "trajectory.jsonl")}
        frames = [r for r in _read_jsonl(self.a1 / "input" / "observations.jsonl")
                  if r["kind"] == "frame"]
        self.assertEqual(len(frames), len(truth))
        for r in frames:
            self.assertEqual(r["t_capture_ms"], truth[r["frame_id"]] + 80)
            self.assertEqual(r["t_present_ms"], truth[r["frame_id"]] + 90)
            self.assertEqual((r["width"], r["height"]), (24, 32))

    def test_grading_realism_and_scanner_options_are_copied_into_the_manifest(self):
        manifest = json.loads((self.a1 / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["grading"], self.definition["grading"])
        self.assertEqual(manifest["realism"], self.definition["realism"])     # as written, not resolved
        self.assertEqual(manifest["scanner_options"], self.definition["scanner_options"])

    def test_scanner_options_are_written_as_input_scanner_json(self):
        written = json.loads((self.a1 / "input" / "scanner.json").read_text(encoding="utf-8"))
        self.assertEqual(written, {"focal_prior_scale": 1.1, "sensor_only": False,
                                   "declination_deg": 0})

    def test_absent_blocks_are_not_written(self):
        manifest = json.loads((self.b / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("realism", manifest)
        self.assertNotIn("grading", manifest)
        self.assertNotIn("scanner_options", manifest)
        self.assertFalse((self.b / "input" / "scanner.json").exists())

    def test_a_block_that_cannot_mean_anything_fails_before_anything_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = _definition("realism-bad", 1, realism={"streams": "nonsense"})
            with self.assertRaises(ValueError):
                cases.build_case(bad, pathlib.Path(tmp), cases.flat_renderer)
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])


# ---------------------------------------------------------------------------
# Legacy cases keep today's bytes
# ---------------------------------------------------------------------------

# SHA-256 of `input/observations.jsonl` as `build_case` wrote it for
# `chartyard-shortpan-60` and `-70` (they differ only in the lens, which the
# sensors do not read) BEFORE this task, motion channel included, 242,666 bytes.
# Like the committed fixtures' own hashes, it is float arithmetic written out in
# decimal: a platform whose numpy rounds the last digit differently moves it, and
# moves the fixture check below with it.
LEGACY_SHORTPAN_SHA256 = "fd6aa677d059d7958f1b2abb343bf8c34cc0263a014f5db0360cb1c3986f52e6"
LEGACY_SHORTPAN_BYTES = 242666


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonl_bytes(records) -> bytes:
    return ("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n").encode("utf-8")


class LegacyCases(unittest.TestCase):
    """A case definition with no ``realism`` block takes exactly the code path
    it always has. Built from the committed fixtures' own definitions, flat
    renderer (the observations do not depend on the image)."""

    NAMES = ("chartyard-shortpan-60", "chartyard-shortpan-70")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dirs = {}
        for name in cls.NAMES:
            definition = json.loads((ROOT / "cases" / f"{name}.json").read_text(encoding="utf-8"))
            cls.dirs[name] = cases.build_case(definition, pathlib.Path(cls.tmp.name), cases.flat_renderer)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_committed_fixtures_reproduce_without_the_motion_channel(self):
        """The frame and orientation lines of both fixtures are reproduced byte
        for byte, so ``hashes.observations`` is reproduced once the motion lines
        (which postdate the fixtures; see the module docstring) are set aside.

        MUTATION: change any digit an orientation or frame record carries, for
        example round ``alpha`` to 6 decimals in ``orientation_events``."""
        for name in self.NAMES:
            with self.subTest(case=name):
                committed = (FIXTURES / name / "input" / "observations.jsonl").read_bytes()
                manifest = json.loads((FIXTURES / name / "manifest.json").read_text(encoding="utf-8"))
                rebuilt = _read_jsonl(self.dirs[name] / "input" / "observations.jsonl")
                self.assertEqual({r["kind"] for r in _read_jsonl(FIXTURES / name / "input" / "observations.jsonl")},
                                 {"frame", "orientation"}, "the fixture is expected to predate #105")
                self.assertIn("motion", {r["kind"] for r in rebuilt})
                kept = _jsonl_bytes(r for r in rebuilt if r["kind"] != "motion")
                self.assertEqual(kept, committed)
                self.assertEqual(_sha256(kept), manifest["hashes"]["observations"])

    def test_a_legacy_case_keeps_today_s_bytes(self):
        """The whole file, motion channel included, is what it was before this
        task. MUTATION: apply the realism rounding or scale in ``motion_events``
        when its keyword arguments are left at their defaults."""
        for name in self.NAMES:
            with self.subTest(case=name):
                data = (self.dirs[name] / "input" / "observations.jsonl").read_bytes()
                self.assertEqual(len(data), LEGACY_SHORTPAN_BYTES)
                self.assertEqual(_sha256(data), LEGACY_SHORTPAN_SHA256)

    def test_a_legacy_case_gains_no_manifest_keys_and_no_scanner_json(self):
        for name in self.NAMES:
            with self.subTest(case=name):
                manifest = json.loads((self.dirs[name] / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(sorted(manifest), sorted([
                    "schema", "case_id", "seed", "scene", "route", "camera", "fps",
                    "expected", "profile", "hashes", "versions"]))
                self.assertFalse((self.dirs[name] / "input" / "scanner.json").exists())

    def test_the_new_keyword_arguments_default_to_the_old_behaviour(self):
        """``frame_records`` and ``motion_events`` gained optional arguments; left
        at their defaults they change nothing, and the noise still depends on the
        ``seed`` argument alone."""
        route = json.loads((ROOT / "routes" / "shortpan.json").read_text(encoding="utf-8"))
        traj = trajectory.build(route, 5)
        self.assertEqual(sensors.frame_records(traj), sensors.frame_records(traj, capture_time=None))
        plain = sensors.motion_events(traj, noise_deg_s=0.05)
        self.assertEqual(plain, sensors.motion_events(
            traj, noise_deg_s=0.05, seed=0, scale_err=0.0, bias_deg_s=0.0, round_deg_s=0.0))
        self.assertNotEqual(plain, sensors.motion_events(traj, noise_deg_s=0.05, seed=1))


if __name__ == "__main__":
    unittest.main()
