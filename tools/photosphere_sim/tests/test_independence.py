import pathlib, re, unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
class Independence(unittest.TestCase):
    def test_simulator_never_imports_production_code(self):
        scanned = (list(ROOT.glob("sim/**/*.py")) + list(ROOT.glob("renderer/**/*.js"))
                   + list(ROOT.glob("renderer/**/*.mjs")) + list(ROOT.glob("renderer/**/*.html")))
        # The globs are a list of suffixes, so a new kind of renderer file
        # silently escapes the scan. Check the scan covers every script under
        # renderer/ before trusting what it did not find.
        renderer_scripts = {p for p in (ROOT / "renderer").rglob("*")
                            if p.suffix in {".js", ".mjs", ".html"}}
        self.assertTrue(renderer_scripts, "no renderer scripts found at all")
        self.assertEqual(renderer_scripts - set(scanned), set(),
                         "a renderer script is not being scanned")
        bad = []
        for p in scanned:
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
