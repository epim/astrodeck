# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Behavioral fixtures for the docs gate and mocked owned-procedure lifecycle."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import check_docs as gate
import probe_session as probe


class DocsChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="astrodeck-docs-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.page = self.root / "docs/guide/demo.md"
        self.page.parent.mkdir(parents=True)
        self.ui = self.root / "ui/src/Button.tsx"
        self.ui.parent.mkdir(parents=True)
        self.ui.write_text('export const label = "START";\n', encoding="utf-8")
        self.page.write_text('# Demo\n\n**START**\n\n[Next](next.md#target)\n', encoding="utf-8")
        (self.page.parent / "next.md").write_text('# Target\n', encoding="utf-8")
        self.labels = [{"page":"docs/guide/demo.md", "label":"START", "source":"ui/src/Button.tsx", "line":1}]
        self.claims = [{"page":"docs/guide/demo.md", "claim":"Start the fixture", "source":"ui/src/Button.tsx", "line":1, "verified_by":"source-trace"}]

    def run_gate(self, text=None, labels=None, claims=None, private=None):
        if text is not None:
            self.page.write_text(text, encoding="utf-8")
        return "\n".join(gate.check(self.root, [self.page], self.labels if labels is None else labels,
                                    self.claims if claims is None else claims, private))

    def test_valid_fixture(self):
        self.assertEqual("", self.run_gate())

    def test_missing_page(self):
        self.page.unlink()
        self.assertIn("missing documentation page", self.run_gate())

    def test_missing_link(self):
        self.assertIn("missing local link target", self.run_gate('**START**\n\n[x](missing.md)'))

    def test_reference_definition_link(self):
        self.assertIn("missing local link target", self.run_gate('**START**\n\n[x][ref]\n\n[ref]: missing.md'))

    def test_html_image_link(self):
        self.assertIn("missing local link target", self.run_gate('**START**\n\n<img src="missing.png">'))

    def test_missing_anchor(self):
        self.assertIn("missing local link anchor", self.run_gate('**START**\n\n[x](next.md#missing)'))

    def test_encoded_escape(self):
        self.assertIn("local link escapes repository", self.run_gate('**START**\n\n[x](%2e%2e/%2e%2e/%2e%2e/outside.md)'))

    def test_external_links_are_not_fetched(self):
        self.assertEqual("", self.run_gate('**START**\n\n[x](https://example.invalid/unreachable)'))

    def test_fenced_links_are_examples(self):
        self.assertEqual("", self.run_gate('**START**\n\n```md\n[x](missing.md)\n```'))

    def test_explicit_and_duplicate_anchors(self):
        self.assertEqual({"same", "same-1", "old"}, gate.anchors('# Same\n# Same\n<a id="old"></a>'))

    def test_fenced_anchor_does_not_exist(self):
        self.assertNotIn("old", gate.anchors('```html\n<a id="old"></a>\n```'))

    def test_plain_text_id_is_not_anchor(self):
        self.assertNotIn("fake", gate.anchors('Example id="fake" in prose'))

    def test_encoded_dash(self):
        self.assertIn("em/en dash is not permitted", self.run_gate('**START** &#8212; next'))

    def test_encoded_emoji(self):
        self.assertIn("emoji is not permitted", self.run_gate('**START** &#128512;'))

    def test_filler(self):
        self.assertIn("house-style filler word", self.run_gate('**START** is robust.'))

    def test_utf8_bom(self):
        self.page.write_bytes(b'\xef\xbb\xbf**START**')
        self.assertIn("UTF-8 BOM is not permitted", self.run_gate())

    def test_invalid_utf8(self):
        self.page.write_bytes(b'\xff')
        self.assertIn("invalid UTF-8", self.run_gate())

    def test_encoded_private_value(self):
        self.assertIn("forbidden observing-site value", self.run_gate('**START** private&#45;canary', private=lambda s:"private-canary" in s))

    def test_percent_encoded_private_link(self):
        self.assertIn("forbidden observing-site value", self.run_gate('**START** [x](private%2dcanary)', private=lambda s:"private-canary" in s))

    def test_diagnostics_do_not_echo_reference(self):
        errors = self.run_gate('**START** [x](private-canary)')
        self.assertIn("missing local link target", errors)
        self.assertNotIn("private-canary", errors)

    def test_label_must_be_quoted(self):
        self.assertIn("registered UI label is absent from bold text", self.run_gate('START'))

    def test_label_requires_ui_source(self):
        self.labels[0]["source"] = "docs/guide/demo.md"
        self.assertIn("UI label lacks a UI source file", self.run_gate())

    def test_label_source_cannot_escape(self):
        self.labels[0]["source"] = "ui/src/../../docs/guide/demo.md"
        self.assertIn("UI label lacks a UI source file", self.run_gate())

    def test_label_line_valid(self):
        self.labels[0]["line"] = 999
        self.assertIn("UI label has an invalid source line", self.run_gate())

    def test_label_source_drift(self):
        self.ui.write_text('export const label = "GO";\n', encoding="utf-8")
        self.assertIn("quoted UI label is absent at its source", self.run_gate())

    def test_label_coverage(self):
        self.assertIn("bold UI label has no provenance record", self.run_gate(labels=[]))

    def test_claim_source_exists(self):
        self.claims[0]["source"] = "missing.py"
        self.assertIn("claim source file is missing", self.run_gate())

    def test_claim_source_cannot_escape(self):
        self.claims[0]["source"] = "../outside.py"
        self.assertIn("claim source file is missing", self.run_gate())

    def test_claim_line_valid(self):
        self.claims[0]["line"] = 999
        self.assertIn("claim source line is invalid", self.run_gate())

    def test_claim_method(self):
        self.claims[0]["verified_by"] = "assumed"
        self.assertIn("claim verification method is missing", self.run_gate())

    def test_claim_coverage(self):
        self.assertIn("no claim/procedure evidence ledger", self.run_gate(claims=[]))

    def test_claim_sources_list(self):
        self.claims[0]["sources"] = [{"path":"ui/src/Button.tsx", "line":1}]
        self.claims[0].pop("source")
        self.assertEqual("", self.run_gate())

    def test_stable_anchor(self):
        self.assertIn("stable guide anchor is missing", "\n".join(gate.check_stable_anchors(self.root, {"docs/guide/demo.md":["old"]})))

    def test_stable_url(self):
        self.assertIn("stable guide URL is missing", "\n".join(gate.check_stable_anchors(self.root, {"docs/guide/gone.md":[]})))

    def test_stable_alias_passes(self):
        self.page.write_text('<a id="old"></a>\n# New\n', encoding="utf-8")
        self.assertEqual([], gate.check_stable_anchors(self.root, {"docs/guide/demo.md":["old"]}))

    def test_ledger_private_value(self):
        path = self.root / "claims.json"
        path.write_text('[{"claim":"private&#45;canary"}]', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "forbidden observing-site value"):
            gate.load_rows(path, "claims", lambda s:"private-canary" in s)

    def test_json_escaped_private_value(self):
        path = self.root / "claims.json"
        path.write_text('[{"claim":"private\\u002dcanary"}]', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "forbidden observing-site value"):
            gate.load_rows(path, "claims", lambda s:"private-canary" in s)

    def test_ledger_rows_are_objects(self):
        path = self.root / "claims.json"
        path.write_text('["invalid row"]', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "ledger must contain a list"):
            gate.load_rows(path, "claims")

    def test_require_privacy_refuses_missing_needles(self):
        with patch.object(gate.sys, "argv", ["check_docs.py", "--require-privacy"]), patch.object(gate.runpy, "run_path", return_value={"load_needles":lambda:[]}), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(2, gate.main())
        self.assertIn("missing external privacy configuration", output.getvalue())


class ProcedureLifecycle(unittest.TestCase):
    def run_failed(self, returncode=1, listener_error=None, owner=True, stop_error=False):
        with tempfile.TemporaryDirectory(prefix="astrodeck-docs-lifecycle-") as name, ExitStack() as stack:
            root = Path(name)
            config = root / ".probe/docs/fresh"
            config.mkdir(parents=True)
            (config / ".astrodeck-probe").write_text("{}", encoding="utf-8")
            fake_scanner = {"load_needles":lambda:["synthetic-canary"], "patterns_for":lambda x:[]}
            stack.enter_context(patch.object(probe,"ROOT",root))
            stack.enter_context(patch.object(probe.sys,"argv",["probe_session.py","--name","fresh","--port","8892"]))
            stack.enter_context(patch.dict(probe.os.environ,{}))
            stack.enter_context(patch.object(probe.ownership,"fresh_paths",return_value=(config,root/"captures")))
            stack.enter_context(patch.object(probe.runpy,"run_path",return_value=fake_scanner))
            stack.enter_context(patch.object(probe.socket,"socket"))
            def launch_or_stop(cmd, **kwargs):
                if cmd[2] == "stop" and stop_error:
                    raise subprocess.CalledProcessError(1, ["owned-stop"])
                return subprocess.CompletedProcess([], returncode if cmd[2] == "start" else 0)
            run=stack.enter_context(patch.object(probe.subprocess,"run",side_effect=launch_or_stop))
            stack.enter_context(patch.object(probe.ownership,"owned_listener",side_effect=listener_error))
            stack.enter_context(patch.object(probe.ownership,"owned_process",return_value={} if owner else None))
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaisesRegex((RuntimeError,ValueError,subprocess.CalledProcessError), "launch failed|listener denied|non-zero exit status"):
                probe.main()
            self.cleanup_output = output.getvalue()
            return [call.args[0][2] for call in run.call_args_list]

    def test_failed_launch_stops_owned_server(self):
        self.assertEqual(["start","stop"],self.run_failed())

    def test_listener_failure_stops_owned_server(self):
        self.assertEqual(["start","stop"],self.run_failed(returncode=0,listener_error=ValueError("listener denied")))

    def test_unowned_process_is_not_stopped(self):
        self.assertEqual(["start"],self.run_failed(owner=False))
        self.assertIn("No owned process was stopped", self.cleanup_output)
        self.assertNotIn("server stopped", self.cleanup_output)

    def test_stop_failure_is_not_reported_as_success(self):
        self.run_failed(stop_error=True)
        self.assertIn("cleanup incomplete", self.cleanup_output)
        self.assertNotIn("server stopped", self.cleanup_output)

    def test_reused_paths_refuse_before_launch(self):
        with tempfile.TemporaryDirectory(prefix="astrodeck-docs-fresh-") as name, ExitStack() as stack:
            root = Path(name)
            stack.enter_context(patch.object(probe, "ROOT", root))
            stack.enter_context(patch.object(probe.sys, "argv", ["probe_session.py", "--name", "fresh"]))
            stack.enter_context(patch.dict(probe.os.environ, {}))
            stack.enter_context(patch.object(probe.ownership, "fresh_paths", side_effect=ValueError("Capture requires new config")))
            stack.enter_context(patch.object(probe.runpy, "run_path", return_value={"load_needles": lambda: ["synthetic-canary"], "patterns_for": lambda x: []}))
            stack.enter_context(patch.object(probe.socket, "socket"))
            run = stack.enter_context(patch.object(probe.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)))
            stack.enter_context(patch.object(probe.ownership, "owned_listener"))
            stack.enter_context(patch.object(probe.ownership, "owned_process", return_value=None))
            message = ""
            with redirect_stdout(io.StringIO()):
                try:
                    probe.main()
                except (ValueError, RuntimeError) as exc:
                    message = str(exc)
            self.assertIn("new config", message)
            run.assert_not_called()



if __name__ == "__main__":
    unittest.main()
