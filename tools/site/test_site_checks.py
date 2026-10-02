# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Regression cases for the site gates.

Named mutants are exercised by tools/site/mutate_checks.py from byte backups.
LINK-EXISTS: 'missing local reference' not found in ''.
FRAGMENT-EXISTS: 'missing fragment' not found in ''.
STYLE-DASH: 'em/en dash' not found in ''.
HTML-PARSER: 'HTML5' not found in ''.
PRIVACY-SVG: 'forbidden site value' not found in ''.
SIM-MARKER-PORT: ValueError not raised.
CSS-RESOURCES: 'external resource' not found in ''.
ENTITY-PRIVACY: 'forbidden site value' not found in ''.
DIAGNOSTIC-PRIVACY: 'private-test-label' unexpectedly found in diagnostics.
FRESH-CONFIG: ValueError not raised.
FAILED-LAUNCH-CLEANUP: Lists differ: ['start', 'stop'] != ['start'].
PARENT-PID-REUSE: False != True.
JS-EXIT: 0 == 0.
MONEY-SITE: 'money metaphor is not permitted' not found in ''.
Full exact assertions are recorded in mutation-evidence.md.
"""
import json
import os
import re
import shutil
import subprocess
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import check_site
import capture_sim

BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fixture</title><link rel="stylesheet" href="assets/site.css"></head>
<body><main id="main"><h1>Fixture</h1>CONTENT</main></body></html>"""


class SiteChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.site = Path(self.temp.name)
        (self.site / "assets").mkdir()
        (self.site / "assets/site.css").write_text("body { color: black; }", encoding="utf-8")

    def page(self, content="", name="index.html"):
        (self.site / name).write_text(BASE.replace("CONTENT", content), encoding="utf-8")

    def findings(self):
        return "\n".join(check_site.check(self.site))

    def test_valid_site(self):
        self.page('<a href="index.html#main">Home</a>')
        self.assertEqual([], check_site.check(self.site))

    def test_missing_asset(self):
        self.page('<img src="assets/missing.png" alt="Fixture">')
        self.assertIn("missing local reference", self.findings())

    def test_missing_fragment(self):
        self.page('<a href="index.html#missing">Section</a>')
        self.assertIn("missing fragment", self.findings())

    def test_canonical_is_metadata(self):
        self.page()
        path = self.site / "index.html"
        path.write_text(path.read_text().replace("</head>", '<link rel="canonical" href="https://example.invalid/index.html"></head>'), encoding="utf-8")
        self.assertEqual([], check_site.check(self.site))

    def test_dash_in_alt_text(self):
        self.page('<img src="assets/site.css" alt="First &mdash; second">')
        self.assertIn("em/en dash", self.findings())

    def test_money_metaphor(self):
        self.page("<p>Keep track of what the night has earned.</p>")
        self.assertIn("money metaphor is not permitted", "\n".join(check_site.check(self.site, label_texts=set())))

    def test_money_metaphor_inside_ledgered_label_passes(self):
        self.page("<p>Review the BUDGET row before you run it.</p>")
        self.assertEqual([], check_site.check(self.site, label_texts={"BUDGET"}))

    def test_cross_page_fragment(self):
        self.page('<a href="guide.html#main">Guide</a>')
        self.page(name="guide.html")
        self.assertEqual([], check_site.check(self.site))

    def test_invalid_html(self):
        self.page("<p><strong>Unclosed")
        self.assertIn("HTML5", self.findings())

    def test_duplicate_ids(self):
        self.page('<p id="main">Again</p>')
        self.assertIn("duplicate id", self.findings())

    def test_dash_entity(self):
        self.page("<p>First &mdash; second</p>")
        self.assertIn("em/en dash", self.findings())

    def test_emoji_entity(self):
        self.page("<p>&#x1F680;</p>")
        self.assertIn("emoji", self.findings())

    def test_external_script(self):
        self.page('<script src="https://example.invalid/script.js"></script>')
        self.assertIn("external resource", self.findings())

    def test_project_pages_root_link(self):
        self.page('<a href="/guide.html">Guide</a>')
        self.assertIn("root-relative", self.findings())

    def test_css_missing_asset(self):
        self.page()
        (self.site / "assets/site.css").write_text("body { background: url(missing.png); }", encoding="utf-8")
        self.assertIn("missing local reference", self.findings())

    def test_css_uppercase_url(self):
        self.page('<style>body{background:URL(https://example.invalid/x)}</style>')
        self.assertIn("external resource", self.findings())

    def test_css_import_comment(self):
        self.page('<style>@import/**/"https://example.invalid/x";</style>')
        self.assertIn("external resource", self.findings())

    def test_inline_style_asset(self):
        self.page('<p style="background:url(missing.png)">Text</p>')
        self.assertIn("missing local reference", self.findings())

    def test_external_svg_image(self):
        self.page('<img src="assets/icon.svg" alt="Example">')
        (self.site / "assets/icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.invalid/x"/></svg>', encoding="utf-8")
        self.assertIn("external resource", self.findings())

    def test_inline_svg_xlink(self):
        self.page('<svg xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="missing.png"/></svg>')
        self.assertIn("missing local reference", self.findings())

    def test_encoded_privacy(self):
        self.page("<p>private&#45;test&#45;label</p>")
        with patch.dict(os.environ, {"ASTRODECK_PRIVACY_NEEDLES": "private-test-label"}):
            self.assertIn("forbidden site value", "\n".join(check_site.privacy(self.site, True)))

    def test_diagnostics_do_not_echo_references(self):
        self.page('<a href="private-test-label">Example</a>')
        self.assertNotIn("private-test-label", self.findings())

    def test_svg_privacy(self):
        self.page()
        (self.site / "assets/icon.svg").write_text('<svg><title>private-test-label</title></svg>', encoding="utf-8")
        with patch.dict(os.environ, {"ASTRODECK_PRIVACY_NEEDLES": "private-test-label"}):
            self.assertIn("forbidden site value", "\n".join(check_site.privacy(self.site, True)))

    def test_required_privacy_configuration(self):
        with patch.dict(os.environ, {"ASTRODECK_PRIVACY_NEEDLES": "", "ASTRODECK_PRIVACY_NEEDLES_FILE": str(self.site / "absent")}):
            self.assertEqual(["privacy: no needles configured"], check_site.privacy(self.site, True))


class CaptureBoundary(unittest.TestCase):
    def test_refuses_rig_port(self):
        with self.assertRaises(ValueError):
            capture_sim.checked_config(Path("unused"), 8800)

    def test_refuses_other_directory(self):
        with self.assertRaises(ValueError):
            capture_sim.checked_config(Path(tempfile.gettempdir()), 8876)

    def test_refuses_wrong_probe_port(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = root / ".probe" / "config"
            config.mkdir(parents=True)
            (config / ".astrodeck-probe").write_text(json.dumps({"dir": str(config), "port": 8875, "pid": 123}), encoding="utf-8")
            (config / "server.pid").write_text("123", encoding="utf-8")
            with patch.object(capture_sim, "ROOT", root):
                with self.assertRaises(ValueError):
                    capture_sim.checked_config(config, 8876)

    def test_refuses_reused_config(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = root / ".probe" / "config"
            config.mkdir(parents=True)
            with patch.object(capture_sim, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "new config"):
                    capture_sim.fresh_paths(config, 8876)

    def test_refuses_invalid_probe_pid(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = root / ".probe" / "config"
            config.mkdir(parents=True)
            (config / ".astrodeck-probe").write_text(json.dumps({"dir": str(config), "port": 8876, "pid": -1}), encoding="utf-8")
            with patch.object(capture_sim, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "process identity"):
                    capture_sim.checked_config(config, 8876)

    def test_accepts_own_probe_marker(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = root / ".probe" / "config"
            config.mkdir(parents=True)
            (config / ".astrodeck-probe").write_text(json.dumps({"dir": str(config), "port": 8876, "pid": 123}), encoding="utf-8")
            (config / "server.pid").write_text("123", encoding="utf-8")
            with patch.object(capture_sim, "ROOT", root):
                self.assertEqual(config.resolve(), capture_sim.checked_config(config, 8876))


class CaptureLifecycle(unittest.TestCase):
    def test_failed_launch_stops_owned_process(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            config = root / ".probe" / "new"
            calls = []
            def launch(command, **kwargs):
                calls.append(command[2])
                if command[2] == "start":
                    config.mkdir(parents=True)
                    (config / ".astrodeck-probe").write_text("{}", encoding="utf-8")
                return SimpleNamespace(returncode=3 if command[2] == "start" else 0)
            argv = ["capture_sim", "--config-dir", str(config), "--port", "8877",
                    "--ui-dir", str(root / "ui"), "--venv-python", str(root / "python.exe")]
            with patch.object(capture_sim, "ROOT", root), \
                 patch.object(capture_sim, "os", SimpleNamespace(name="nt", environ=os.environ)), \
                 patch.object(capture_sim.sys, "argv", argv), \
                 patch.object(capture_sim.socket, "socket"), \
                 patch.object(capture_sim.subprocess, "run", side_effect=launch), \
                 patch.object(capture_sim, "owned_process", return_value={"pid": 123}), \
                 patch.object(capture_sim, "capture") as capture:
                with self.assertRaisesRegex(RuntimeError, "launch failed"):
                    capture_sim.main()
                self.assertEqual(["start", "stop"], calls)
                capture.assert_not_called()

    def check_tree(self, processes, leaf, expected):
        with patch.object(capture_sim, "process_info", side_effect=processes.get):
            self.assertEqual(expected, capture_sim.reaches_owner(leaf, processes[123]))

    def test_accepts_venv_child(self):
        self.check_tree({123: {"pid":123,"parent":1,"created":10},
                         456: {"pid":456,"parent":123,"created":11}}, 456, True)

    def test_refuses_unrelated_listener(self):
        self.check_tree({123: {"pid":123,"parent":1,"created":10},
                         456: {"pid":456,"parent":1,"created":11}}, 456, False)

    def test_refuses_parent_pid_reuse(self):
        self.check_tree({123: {"pid":123,"parent":1,"created":10},
                         456: {"pid":456,"parent":123,"created":9}}, 456, False)

    def test_refuses_parent_cycle(self):
        self.check_tree({123: {"pid":123,"parent":1,"created":10},
                         456: {"pid":456,"parent":456,"created":11}}, 456, False)


class MonitorCapture(unittest.TestCase):
    def previous(self):
        return {"captured_utc": "old-date", "source_commit": "old-source", "source": "default site untouched",
                "captures": [{"file": "site/assets/screenshots/" + item[0], "sha256": "original-" + item[0]}
                             for item in capture_sim.CAPTURE_PLAN]}

    def test_monitor_plan_excludes_other_images(self):
        plan = capture_sim.capture_plan(True, "siding-spring")
        self.assertEqual(["monitor-desktop-light.png"], [item[0] for item in plan])

    def test_hanle_target_is_above_horizon(self):
        coords = capture_sim.runpy.run_path(str(capture_sim.ROOT / "server/astrodeck/catalog/coords.py"))
        site, _ = capture_sim.PUBLIC_SCENARIOS["hanle"]
        for timestamp in [1770000000, 1790870000]:
            target = capture_sim.synthetic_target(timestamp, "hanle")
            alt, _ = coords["altaz"](target["ra_hours"], target["dec_deg"],
                                     site["latitude"], site["longitude"], timestamp)
            self.assertGreater(alt, 60)
            self.assertFalse(target["center"])
            self.assertFalse(target["force"])
        self.assertEqual([capture_sim.MONITOR_FILE], [item[0] for item in capture_sim.capture_plan(True, "hanle")])

    def test_public_scenario_requires_monitor_only(self):
        with self.assertRaises(ValueError):
            capture_sim.capture_plan(False, "siding-spring")
        with self.assertRaises(ValueError):
            capture_sim.capture_plan(True, None)

    def test_old_images_keep_old_provenance(self):
        previous = self.previous()
        original = json.loads(json.dumps(previous))
        monitor = {"file": "site/assets/screenshots/monitor-desktop-light.png", "sha256": "new-image"}
        context = {"captured_utc": "new-date", "source_commit": "new-source", "scenario": "synthetic"}
        result = capture_sim.merged_provenance(previous, [monitor], context, True)
        self.assertEqual(original, previous)
        self.assertNotIn("source_commit", result)
        for old, new in zip(previous["captures"], result["captures"]):
            if old["file"] == monitor["file"]:
                self.assertEqual("new-image", new["sha256"])
                self.assertEqual(context, new["provenance"])
            else:
                self.assertEqual(old["sha256"], new["sha256"])
                self.assertEqual("old-source", new["provenance"]["source_commit"])
                self.assertEqual("default site untouched", new["provenance"]["source"])

    def test_monitor_cannot_replace_equipment_record(self):
        other = {"file": "site/assets/screenshots/equipment-phone-light.png"}
        with self.assertRaises(ValueError):
            capture_sim.merged_provenance(self.previous(), [other], {}, True)

    def test_monitor_refuses_incomplete_previous_ledger(self):
        with self.assertRaises(ValueError):
            capture_sim.merged_provenance({}, [{"file": "site/assets/screenshots/monitor-desktop-light.png"}], {}, True)

    def test_mixed_backend_is_not_simulator_proof(self):
        status = {"mode": "sim", "connected": {"camera": {"name": "Sim Camera 533MM", "connected": True, "backend": "sim"},
                  "telescope": {"name": "Sim Mount EQ6-R", "connected": True, "backend": "alpaca"}}}
        with self.assertRaisesRegex(ValueError, "simulator devices"):
            capture_sim.require_simulator(status)

    def test_legacy_sim_descriptions_have_empty_backend(self):
        status = {"mode": "sim", "connected": {"camera": {"name": "Sim Camera 533MM", "connected": True, "backend": "", "host": "", "port": 0},
                  "telescope": {"name": "Sim Mount EQ6-R", "connected": True, "backend": "", "host": "", "port": 0}}}
        self.assertIs(status, capture_sim.require_simulator(status))
        status["connected"]["camera"]["host"] = "example.invalid"
        with self.assertRaises(ValueError):
            capture_sim.require_simulator(status)

    def test_pointing_mismatch_is_not_capture_ready(self):
        # Readiness never reads the mount's alt/az (#140); above-horizon is
        # pinned by the target geometry tests and the server's floor gate.
        target = {"ra_hours": 4, "dec_deg": -45}
        status = {"mount": {"ra_hours": 6, "dec_deg": -45,
                             "slewing": False, "parked": False}}
        self.assertFalse(capture_sim.above_horizon_target(status, target))
        status["mount"].update({"ra_hours": 4, "dec_deg": -30})
        self.assertFalse(capture_sim.above_horizon_target(status, target))

    def test_unsynced_sim_pointing_error_is_capture_ready(self):
        target = {"ra_hours": 4, "dec_deg": -45}
        status = {"mount": {"ra_hours": 4.002, "dec_deg": -44.972,
                             "slewing": False, "parked": False}}
        self.assertTrue(capture_sim.above_horizon_target(status, target))
        status["mount"]["slewing"] = True
        self.assertFalse(capture_sim.above_horizon_target(status, target))

    def test_target_is_above_horizon_at_different_times(self):
        coords = capture_sim.runpy.run_path(str(capture_sim.ROOT / "server/astrodeck/catalog/coords.py"))
        for timestamp in [1770000000, 1790870000]:
            target = capture_sim.synthetic_target(timestamp)
            alt, _ = coords["altaz"](target["ra_hours"], target["dec_deg"],
                                     capture_sim.PUBLIC_SITE["latitude"], capture_sim.PUBLIC_SITE["longitude"], timestamp)
            self.assertGreater(alt, 60)
            self.assertFalse(target["center"])
            self.assertFalse(target["force"])


class WorkflowChecks(unittest.TestCase):
    def test_javascript_syntax_failure_stops_step(self):
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        block = workflow.split("- name: Validate JavaScript syntax", 1)[1].split("- uses:", 1)[0]
        script = "\n".join(line[10:] for line in block.splitlines() if line.startswith("          "))
        bash = Path(r"C:\Program Files\Git\bin\bash.exe") if os.name == "nt" else Path(shutil.which("bash"))
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "site").mkdir()
            (root / "site/invalid.js").write_text("function {", encoding="utf-8")
            result = subprocess.run([str(bash), "-c", script], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("SyntaxError", result.stderr)


if __name__ == "__main__":
    unittest.main()
