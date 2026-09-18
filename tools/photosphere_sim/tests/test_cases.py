"""``cases.build_case`` against the contract's case directory layout.

Uses the ``flat`` renderer throughout: it is the one renderer this task ships,
and its output (constant mid-grey frames) is exactly reproducible, which is
what the determinism check needs.
"""
import json, pathlib, tempfile, unittest

from PIL import Image

from sim import cases, trajectory

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_case_def(case_id):
    return json.loads((ROOT / "cases" / f"{case_id}.json").read_text(encoding="utf-8"))


def load_route(name):
    return json.loads((ROOT / "routes" / f"{name}.json").read_text(encoding="utf-8"))


def _read_jsonl(path):
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


class BuildCase(unittest.TestCase):
    """One ``chartyard-still-60`` build, shared read-only by most assertions."""

    @classmethod
    def setUpClass(cls):
        cls.case_def = load_case_def("chartyard-still-60")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out_dir = cases.build_case(cls.case_def, pathlib.Path(cls.tmp.name), cases.flat_renderer)
        cls.traj = trajectory.build(load_route("still"), cls.case_def["fps"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_contract_file_exists(self):
        expected = [
            "manifest.json",
            "input/observations.jsonl",
            "input/actions.jsonl",
            "truth/scene.json",
            "truth/camera.json",
            "truth/reference.json",
            "truth/trajectory.jsonl",
            "truth/landmarks.json",
            "truth/reference-horizon.json",
            "truth/holds.json",
        ]
        for rel in expected:
            self.assertTrue((self.out_dir / rel).is_file(), rel)

    def test_frame_count_matches_the_trajectory(self):
        pngs = list((self.out_dir / "input" / "frames").glob("*.png"))
        self.assertEqual(len(pngs), len(self.traj.frames))

    def test_frame_pngs_are_480x640_rgb(self):
        frames_dir = self.out_dir / "input" / "frames"
        pngs = sorted(frames_dir.glob("*.png"))
        self.assertTrue(pngs)
        for path in pngs:
            with Image.open(path) as image:
                self.assertEqual(image.size, (480, 640))
                self.assertEqual(image.mode, "RGB")

    def test_observations_sorted_by_delivery_time(self):
        records = _read_jsonl(self.out_dir / "input" / "observations.jsonl")
        self.assertTrue(records)
        delivery = [r["t_present_ms"] if r["kind"] == "frame" else r["t_receive_ms"] for r in records]
        self.assertEqual(delivery, sorted(delivery))

    def test_actions_begin_at_0_and_finish_1s_after_the_last_frame(self):
        actions = _read_jsonl(self.out_dir / "input" / "actions.jsonl")
        self.assertEqual(actions[0], {"t_ms": 0, "action": "begin"})
        frames = [r for r in _read_jsonl(self.out_dir / "input" / "observations.jsonl")
                 if r["kind"] == "frame"]
        last_present_ms = max(r["t_present_ms"] for r in frames)
        self.assertEqual(actions[-1], {"t_ms": last_present_ms + 1000, "action": "finish"})

    def test_landmarks_52_entries_46_background_6_surface(self):
        landmarks = json.loads((self.out_dir / "truth" / "landmarks.json").read_text(encoding="utf-8"))
        self.assertEqual(len(landmarks), 52)
        self.assertEqual(sum(1 for lm in landmarks if lm["kind"] == "background"), 46)
        self.assertEqual(sum(1 for lm in landmarks if lm["kind"] == "surface"), 6)

    def test_reference_horizon_has_3600_alt_max(self):
        horizon = json.loads((self.out_dir / "truth" / "reference-horizon.json").read_text(encoding="utf-8"))
        self.assertEqual(horizon["bins"], 3600)
        self.assertEqual(len(horizon["alt_max"]), 3600)


class Determinism(unittest.TestCase):
    def test_frames_hash_is_stable_across_two_builds(self):
        case_def = load_case_def("chartyard-still-60")
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            out1 = cases.build_case(case_def, pathlib.Path(first), cases.flat_renderer)
            out2 = cases.build_case(case_def, pathlib.Path(second), cases.flat_renderer)
            manifest1 = json.loads((out1 / "manifest.json").read_text(encoding="utf-8"))
            manifest2 = json.loads((out2 / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest1["hashes"]["frames"], manifest2["hashes"]["frames"])
        self.assertEqual(manifest1["hashes"]["truth"], manifest2["hashes"]["truth"])


if __name__ == "__main__":
    unittest.main()
