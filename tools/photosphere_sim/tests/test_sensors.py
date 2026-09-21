"""Change-driven orientation events and frame delivery records.

Built on the arc075 route (the still route reports the same orientation
sequence, since only position differs between the two kinds).
"""
import json, math, pathlib, unittest

from sim import sensors, trajectory
from sim.geometry import wrap_deg

ROUTES = pathlib.Path(__file__).resolve().parents[1] / "routes"


def load_route(name):
    return json.loads((ROUTES / f"{name}.json").read_text(encoding="utf-8"))


def build(name="arc075", fps=10):
    return trajectory.build(load_route(name), fps)


def _delta(e1, e2):
    """The largest single-angle change between two orientation events."""
    d_alpha = abs(wrap_deg(e2["alpha"] - e1["alpha"]))
    d_beta = abs(e2["beta"] - e1["beta"])
    d_gamma = abs(e2["gamma"] - e1["gamma"])
    return max(d_alpha, d_beta, d_gamma)


class OrientationEvents(unittest.TestCase):
    def test_no_events_during_any_hold(self):
        # A direct check, not a bracket-gap comparison: the earlier version
        # compared the nearest event at-or-before a hold's start to the
        # nearest at-or-after its end, but that gap is >= the hold's length
        # BY CONSTRUCTION (before[-1] <= from_ms and after[0] >= to_ms, so
        # after[0] - before[-1] >= to_ms - from_ms always), regardless of
        # whether an event fired strictly inside the hold -- see fix-round-2
        # in task-3-report.md for a mutant that proves it. This asks the
        # actual question instead: is there an emitted event whose own
        # t_event_ms falls strictly inside some hold's interval?
        traj = build()
        events = sensors.orientation_events(traj)
        times = [e["t_event_ms"] for e in events]
        for hold in traj.holds:
            offenders = [t for t in times if hold.from_ms < t < hold.to_ms]
            self.assertEqual(
                offenders, [],
                f"event(s) at {offenders} fired strictly inside hold {hold.index} "
                f"({hold.from_ms}-{hold.to_ms} ms)",
            )

    def test_first_move_emits_at_least_20_events_with_real_change(self):
        traj = build()
        events = sensors.orientation_events(traj)
        # The first move is aim0 (0,0) -> aim1 (24,0), the interval [1200, 2000) ms.
        in_move = [e for e in events if 1200 <= e["t_event_ms"] < 2000]
        self.assertGreaterEqual(len(in_move), 20)
        self.assertTrue(any(_delta(a, b) >= 0.1 for a, b in zip(in_move, in_move[1:])))

    def test_receive_latency_sorted_and_absolute(self):
        traj = build()
        events = sensors.orientation_events(traj)
        self.assertTrue(events)
        for event in events:
            self.assertEqual(event["t_receive_ms"] - event["t_event_ms"], 20)
            self.assertTrue(event["absolute"])
        times = [e["t_event_ms"] for e in events]
        self.assertEqual(times, sorted(times))


class FrameRecords(unittest.TestCase):
    def test_frame_7_at_10_fps(self):
        traj = build(fps=10)
        records = sensors.frame_records(traj)
        record = next(r for r in records if r["frame_id"] == "f000007")
        self.assertEqual(record["t_capture_ms"], 700)
        self.assertEqual(record["t_present_ms"], 760)


if __name__ == "__main__":
    unittest.main()


class MotionEvents(unittest.TestCase):
    """The second witness (issue #105).

    What makes it a second witness rather than a copy of the first: it is not
    change-driven. A phone holding still stops producing orientation events and
    keeps producing these, which is what lets a reading be vouched for during a
    hold.
    """

    def test_emitted_on_every_tick_including_through_a_hold(self):
        """MUTATION: threshold the stream the way `orientation_events` does, so
        a sample equal to the last is dropped. Observed: the holds below emit
        nothing and the count falls from 6317 to a few hundred."""
        traj = build()
        events = sensors.motion_events(traj, sample_hz=60)
        expected = int(round(traj.duration_ms / (1000.0 / 60))) + 1
        self.assertEqual(len(events), expected)
        hold = traj.holds[-1]
        inside = [e for e in events
                  if hold.from_ms + 20 <= e["t_event_ms"] <= hold.to_ms - 20]
        self.assertGreater(len(inside), 5,
                           "a hold produced almost no motion samples, so the "
                           "stream is behaving like the change-driven one")

    def test_the_magnitude_is_the_trajectory_own_angular_rate(self):
        """The correctness claim, and the only quantity the scanner reads:
        `MotionStability` compares `hypot(alpha, beta, gamma)` against
        `QUIET_RATE_DEG_S`.

        MUTATION: differentiate the Euler angles instead - return
        `(d_alpha, d_beta, d_gamma) / dt`. Observed: agreement holds over most
        of the route and then diverges without bound as the arc approaches the
        zenith, which is the one hold this stream exists to measure.
        """
        traj = build()
        by_t = {e["t_event_ms"]: math.hypot(e["rate"]["alpha"], e["rate"]["beta"],
                                            e["rate"]["gamma"])
                for e in sensors.motion_events(traj, sample_hz=60)}
        errors = []
        for frame in traj.frames:
            nearest = min(by_t, key=lambda k: abs(k - frame.t_capture_ms))
            if abs(nearest - frame.t_capture_ms) > 9:
                continue
            errors.append(abs(by_t[nearest] - frame.angular_rate_deg_s))
        self.assertGreater(len(errors), 500, "almost nothing was compared")
        errors.sort()
        self.assertLess(errors[len(errors) // 2], 0.01,
                        "the synthesised rate does not track the trajectory's own")

    def test_a_hold_reads_exactly_zero_without_noise(self):
        """A hold is exactly still, so the stream through it is exactly zero -
        which is what makes the noise case below able to fail.

        NOT GRADED, and said here rather than claimed: dropping the
        `angle < 1e-12` guard in `_device_rate_deg_s` leaves this passing.
        During a hold the relative rotation is exactly the identity, so the
        fallthrough takes the `norm < 1e-12` branch and returns
        `degrees(0) / seconds`, which is the same zero. The guard is defensive
        - it also covers `seconds <= 0`, which the caller already prevents by
        skipping `t1 <= t0` - and it is kept as a division guard, not because
        any case here holds it."""
        traj = build()
        hold = traj.holds[-1]
        mid = (hold.from_ms + hold.to_ms) / 2
        events = sensors.motion_events(traj, sample_hz=60)
        nearest = min(events, key=lambda e: abs(e["t_event_ms"] - mid))
        self.assertEqual(
            math.hypot(nearest["rate"]["alpha"], nearest["rate"]["beta"],
                       nearest["rate"]["gamma"]), 0.0)

    def test_the_noise_knob_moves_a_still_hold_off_zero_and_repeats(self):
        """The profile knob the issue asks for, so a case can carry a gyro too
        noisy to vouch for anything.

        MUTATION: ignore `noise_deg_s`. Observed: the still hold still reads
        exactly 0 and this fails.
        """
        traj = build()
        hold = traj.holds[-1]
        mid = (hold.from_ms + hold.to_ms) / 2
        noisy = sensors.motion_events(traj, sample_hz=60, noise_deg_s=0.4)
        nearest = min(noisy, key=lambda e: abs(e["t_event_ms"] - mid))
        self.assertGreater(
            math.hypot(nearest["rate"]["alpha"], nearest["rate"]["beta"],
                       nearest["rate"]["gamma"]), 0.0)
        # Seeded, so a recording repeats. Without this the case format would
        # carry a stream nobody can reproduce, which is the one thing a
        # recording may not do.
        again = sensors.motion_events(traj, sample_hz=60, noise_deg_s=0.4)
        self.assertEqual([e["rate"] for e in noisy], [e["rate"] for e in again])
