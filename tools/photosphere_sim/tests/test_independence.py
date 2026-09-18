import pathlib, re, unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
class Independence(unittest.TestCase):
    def test_simulator_never_imports_production_code(self):
        bad = []
        for p in list(ROOT.glob("sim/**/*.py")) + list(ROOT.glob("renderer/**/*.js")) + list(ROOT.glob("renderer/**/*.html")):
            text = p.read_text(encoding="utf-8")
            if re.search(r"ui[\\/]src|photosphereGeometry|photosphere\.ts|from ['\"]\.\./\.\./ui", text):
                bad.append(str(p))
        self.assertEqual(bad, [], "simulator files reference production code")
    def test_replay_never_reads_truth(self):
        replay = ROOT.parents[1] / "ui" / "src" / "next" / "hubs" / "sky" / "sheets" / "__sim__"
        if not replay.exists():
            self.skipTest("replay driver not written yet")
        for p in replay.glob("*.ts"):
            self.assertNotIn("truth", p.read_text(encoding="utf-8"), f"{p} mentions truth/")
