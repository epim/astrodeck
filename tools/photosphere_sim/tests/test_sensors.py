"""Change-driven orientation events and frame delivery records.

Built on the arc075 route (the still route reports the same orientation
sequence, since only position differs between the two kinds).
"""
import json, pathlib, unittest

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
        traj = build()
        events = sensors.orientation_events(traj)
        times = sorted(e["t_event_ms"] for e in events)
        for hold in traj.holds:
            before = [t for t in times if t <= hold.from_ms]
            self.assertTrue(before, "the mandatory first sample precedes every hold")
            after = [t for t in times if t >= hold.to_ms]
            if after:
                gap = after[0] - before[-1]
                self.assertGreaterEqual(gap, hold.to_ms - hold.from_ms)

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
