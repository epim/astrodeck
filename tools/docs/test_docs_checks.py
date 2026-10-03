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
        self.page.write_text('# Demo\n\n**START**\n\nStart the fixture.\n\n[Next](next.md#target)\n', encoding="utf-8")
        (self.page.parent / "next.md").write_text('# Target\n', encoding="utf-8")
        self.labels = [{"page":"docs/guide/demo.md", "label":"START", "source":"ui/src/Button.tsx", "line":1}]
        self.claims = [{"page":"docs/guide/demo.md", "claim":"Start the fixture", "source":"ui/src/Button.tsx", "line":1, "verified_by":"source-trace", "id":"DEMO-001"}]

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
        self.assertEqual("", self.run_gate('**START**\n\nStart the fixture.\n\n[x](https://example.invalid/unreachable)'))

    def test_fenced_links_are_examples(self):
        self.assertEqual("", self.run_gate('**START**\n\nStart the fixture.\n\n```md\n[x](missing.md)\n```'))

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

    def test_money_metaphor(self):
        self.assertIn("money metaphor is not permitted", self.run_gate('**START**\n\nThe operator has earned a reward.'))

    def test_money_metaphor_inside_ledgered_label_passes(self):
        self.ui.write_text('export const label = "START";\nexport const b = "BUDGET";\n', encoding="utf-8")
        self.labels.append({"page": "docs/guide/demo.md", "label": "BUDGET", "source": "ui/src/Button.tsx", "line": 2})
        self.assertEqual("", self.run_gate('**START**\n\nStart the fixture.\n\n**BUDGET**'))

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

    def test_label_survives_insertion_above(self):
        # #660: an unrelated edit above the label shifts it to a later line
        # than the ledger records. The citation must still pass, because
        # the label's own text is untouched.
        padding = "\n".join(f"// padding {i}" for i in range(9))
        self.ui.write_text(padding + '\nexport const label = "START";\n', encoding="utf-8")
        self.assertEqual("", self.run_gate())

    def test_label_wording_change_fails_even_after_insertion(self):
        padding = "\n".join(f"// padding {i}" for i in range(9))
        self.ui.write_text(padding + '\nexport const label = "GO";\n', encoding="utf-8")
        self.assertIn("quoted UI label is absent at its source", self.run_gate())

    def test_label_ambiguous_without_resolving_hint(self):
        # Two occurrences, both before a stale hint that cannot say which
        # one the ledger meant: the line cannot resolve this by itself.
        self.ui.write_text('export const a = "START";\nx\nexport const b = "START";\n' + "pad\n" * 7, encoding="utf-8")
        self.labels[0]["line"] = 10
        self.assertIn("quoted UI label is ambiguous at its source", self.run_gate())

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

    def test_claim_text_must_be_on_page(self):
        # #673: the Orange Pi guide was rewritten and the ledger kept quoting
        # the old wording; nothing checked that the quote still appeared on
        # the page it claims to source. A reworded page must fail the gate
        # and the message must name both the claim and its page, never the
        # stale quote itself (the module never echoes document text).
        errors = self.run_gate('**START**\n\nThe fixture was rewritten.\n')
        self.assertIn("docs/guide/demo.md: claim DEMO-001 text is not on the page", errors)
        self.assertNotIn("Start the fixture", errors)

    def test_claim_text_survives_insertion_above(self):
        # The same content matching as #660's label check: an edit elsewhere
        # on the page must not move a claim's citation off a recorded line,
        # because claims are not resolved by line at all.
        self.assertEqual(
            "",
            self.run_gate('# Demo\n\n**START**\n\n'
                           + "\n\n".join(f"Padding paragraph {i}." for i in range(5))
                           + '\n\nStart the fixture.\n'),
        )

    def test_claim_text_matches_across_an_ordinary_wrap(self):
        # Markdown hand-wraps prose at the column width, so a quoted
        # sentence routinely spans a line break with no indentation of its
        # own; locate_label already joins lines with a single space for
        # this case (#660), and a claim relies on exactly that.
        self.claims[0]["claim"] = "the fixture wraps across a line break"
        wrapped = '# Demo\n\n**START**\n\nHere the fixture wraps across a\nline break in the page.\n'
        self.assertEqual("", self.run_gate(wrapped))

    def test_claim_text_matches_a_hanging_list_indent(self):
        # A numbered item's continuation lines carry a few spaces of
        # hanging indent so they line up under the marker. The ledger
        # quotes the sentence in ordinary single-spaced prose; the indent
        # is page formatting, not part of what the claim says, so it must
        # not count as a mismatch.
        self.claims[0]["claim"] = "the fixture has wrapped under a list marker"
        wrapped = ('# Demo\n\n**START**\n\n'
                   '1. Confirm the fixture has wrapped\n'
                   '   under a list marker.\n')
        self.assertEqual("", self.run_gate(wrapped))

    def test_claim_text_matches_the_pages_own_line_breaks(self):
        # Some ledger rows quote a multi-line claim with the page's own
        # line breaks spelled out as literal newlines (mirroring exactly
        # how the paragraph wraps), rather than as normal single-spaced
        # prose. Both spellings must resolve to the same normalized claim
        # text, because the Markdown has only one actual wrapping and a
        # ledger author should not have to guess which the gate wants.
        self.claims[0]["claim"] = "the fixture wraps across\na line break exactly as the page does"
        wrapped = '# Demo\n\n**START**\n\nHere the fixture wraps across\na line break exactly as the page does.\n'
        self.assertEqual("", self.run_gate(wrapped))

    def test_claim_text_with_aligned_spacing_still_matches(self):
        # A fenced code block or table can use several literal spaces for
        # column alignment. A claim that quotes it verbatim, spaces and
        # all, must still match a page reflowed to different alignment,
        # because the alignment is formatting, not the sentence's content.
        self.claims[0]["claim"] = "left   right"
        wrapped = '# Demo\n\n**START**\n\n```\nleft right\n```\n'
        self.assertEqual("", self.run_gate(wrapped))

    def test_claim_text_missing_id_still_reports(self):
        self.claims[0].pop("id")
        self.assertIn(
            "docs/guide/demo.md: claim ? text is not on the page",
            self.run_gate('**START**\n\nNo match here.\n'),
        )

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
