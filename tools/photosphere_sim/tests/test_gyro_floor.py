# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Every chart-yard case records a gyro with a noise floor (issue #76).

An exact gyro reads EXACTLY zero through a hold, and the scanner discards an
exact zero triple as synthetic (`MotionStability.observe`, issue #106: a real
MEMS gyro never reports one twice). So an exact stream does not model a quiet
gyro, it models a dead one: the witness goes stale at every held attitude and
the replay refuses holds a phone would capture. On chartyard-arc075-60 that
was five of #76's seven missed holds (43 -> 46 of 48 once the floor was in).

The two halves pinned here are the two ways this goes wrong again:

  * a case loses its floor - the stream is exact again, the holds go stale;
  * a floor is set too high - the gyro stops being QUIET, and a phone holding
    still reads as turning, which refuses the holds from the other side.

MUTATIONS RUN, and what each printed:

  M1, drop `gyro_noise_deg_s` from chartyard-arc075-60.json. 2 failed:
  test_every_chart_yard_case_sets_a_floor (names the case) and
  test_a_hold_carries_no_exact_zero_triple (71 exact zeros in hold 37).

  M2, set it to 0.5 in every case. 1 failed: test_the_floor_is_quiet,
  p99 magnitude above QUIET_RATE_DEG_S.
"""
import json, math, pathlib, unittest

import numpy as np

from sim import sensors, trajectory

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The scanner's thresholds, from ui/src/next/hubs/sky/sheets/photospherePose.ts.
# Copied rather than parsed: this suite does not read the UI tree, and a change
# there that moves them is a change this test should be re-read against.
QUIET_RATE_DEG_S = 0.5
QUIET_DRIFT_DEG = 0.5


def _cases():
    """The legacy chart-yard cases, those with no ``realism`` block.

    A case with one takes its gyro from ``realism.motion`` (spec 13.2), whose
    defaults are Chromium's: bias 0.01 and noise 0.018 deg/s rounded to 0.1,
    which read an exact zero triple on 96 % of a still hold on purpose (ruling
    S21). Those cases are the panorama scanner's and `test_sensor_realism.py`
    grades their streams; the floor pinned here is the ``gyro_noise_deg_s`` of a
    case built the old way (issue #76).
    """
    return sorted(p for p in (ROOT / "cases").glob("chartyard-*.json")
                  if "realism" not in _case(p))


def _case(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _route(name):
    return json.loads((ROOT / "routes" / f"{name}.json").read_text(encoding="utf-8"))


class GyroFloor(unittest.TestCase):
    def test_every_chart_yard_case_sets_a_floor(self):
        self.assertTrue(_cases(), "no chart-yard cases found, so this grades nothing")
        exact = [p.name for p in _cases()
                 if not float(_case(p).get("gyro_noise_deg_s", 0.0) or 0.0) > 0.0]
        self.assertEqual(exact, [],
                         f"these cases record an exact gyro, which the scanner "
                         f"reads as a dead one at every hold: {exact}")

    def test_a_hold_carries_no_exact_zero_triple(self):
        """Through the real `motion_events`, on the route and hold #76 measured."""
        case = _case(ROOT / "cases" / "chartyard-arc075-60.json")
        traj = trajectory.build(_route(case["route"]), case["fps"])
        events = sensors.motion_events(
            traj, noise_deg_s=float(case.get("gyro_noise_deg_s", 0.0) or 0.0))
        h = traj.holds[37]           # az 174 alt 70, a hold #76 measured
        self.assertEqual((h.az, h.alt), (174.0, 70.0), "the route's holds moved")
        window = (h.from_ms, h.to_ms)
        inside = [e for e in events if window[0] <= e["t_event_ms"] <= window[1]]
        self.assertTrue(inside, "no motion samples inside the hold, so this grades nothing")
        zeros = sum(1 for e in inside if all(v == 0.0 for v in e["rate"].values()))
        self.assertEqual(zeros, 0,
                         f"{zeros} of {len(inside)} samples in hold 37 are an exact "
                         f"zero triple, which the scanner discards as synthetic")

    def test_the_floor_is_quiet(self):
        """A floor must stay under the scanner's quiet rate, or a still phone
        reads as turning. Checked at the 99th percentile of magnitude and on a
        whole second's random-walk drift, over 60 000 samples."""
        for path in _cases():
            sigma = float(_case(path).get("gyro_noise_deg_s", 0.0) or 0.0)
            rng = np.random.default_rng(0)
            rates = rng.normal(0.0, sigma, (60000, 3))
            mags = np.sqrt((rates ** 2).sum(axis=1))
            self.assertLess(float(np.percentile(mags, 99)), QUIET_RATE_DEG_S,
                            f"{path.name}: a gyro at {sigma} deg/s is not quiet")
            # One second at 60 Hz, the scanner's drift window order of magnitude.
            drift = np.abs(rates[:60].sum(axis=0) / 60.0)
            self.assertLess(float(math.hypot(*drift)), QUIET_DRIFT_DEG,
                            f"{path.name}: a gyro at {sigma} deg/s drifts out of quiet")


if __name__ == "__main__":
    unittest.main()
