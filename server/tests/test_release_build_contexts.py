# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""COPY checks inspect fixture files; no daemon, network or production config."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_contexts", ROOT / "deploy/reverse-proxy/check_build_contexts.py")
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)

class BuildContexts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "requirements.txt").write_text("fixture", encoding="utf-8")
        (self.root / "relay").mkdir()
        (self.root / "relay/__init__.py").write_text("", encoding="utf-8")
        self.dockerfile = self.root / "Dockerfile"

    def findings(self, content):
        self.dockerfile.write_text(content, encoding="utf-8")
        return gate.check_build(gate.Build("fixture", self.root, self.dockerfile))[0]

    def test_valid_shell_json_and_prior_stage(self):
        self.assertEqual([], self.findings('FROM fixture AS build\nCOPY requirements.txt .\nFROM fixture\nCOPY --from=build /built /dest\nCOPY ["relay", "/app/relay"]\n'))

    def test_tab_delimited_copy_is_checked(self):
        self.assertIn("COPY source is missing: absent.txt", self.findings("FROM fixture\nCOPY\tabsent.txt .\n"))

    def test_missing_copy_fails(self):
        self.assertIn("COPY source is missing: absent.txt", self.findings("FROM fixture\nCOPY absent.txt .\n"))

    def test_dockerignore_excluded_fails(self):
        (self.root / ".dockerignore").write_text("relay/\n", encoding="utf-8")
        self.assertIn("COPY source is excluded by dockerignore: relay", self.findings("FROM fixture\nCOPY relay /app/relay\n"))

    def test_unknown_stage_fails(self):
        self.assertIn("COPY references an unknown or current stage", self.findings("FROM fixture AS build\nCOPY --from=other /built /dest\n"))

    def test_context_escape_fails(self):
        self.assertIn("COPY source must stay inside local context", self.findings("FROM fixture\nCOPY ../requirements.txt .\n"))

    def test_unhandled_syntax_fails_closed(self):
        self.assertIn("unsupported COPY flag", self.findings("FROM fixture\nCOPY --unknown=1 relay /dest\n"))

    def test_specific_ignore_overrides_context_ignore(self):
        (self.root / ".dockerignore").write_text("relay\n", encoding="utf-8")
        (self.root / "Dockerfile.dockerignore").write_text("requirements.txt\n", encoding="utf-8")
        self.assertEqual([], self.findings("FROM fixture\nCOPY relay /app/relay\n"))

    def test_ignore_exception_and_zero_directory_glob(self):
        self.assertFalse(gate.ignored("README.md", ["*.md", "!README.md"]))
        self.assertTrue(gate.ignored("__pycache__/x.pyc", ["**/__pycache__"]))

    def test_ignore_wildcard_does_not_cross_directory(self):
        self.assertTrue(gate.ignored("relay/deep/private.py", ["*", "!relay/*.py"]))
        self.assertFalse(gate.ignored("relay/public.py", ["*", "!relay/*.py"]))
        self.assertTrue(gate.ignored("relay/deep/cache/private.py", ["relay/**/cache"]))
        self.assertTrue(gate.ignored("relay/cache/private.py", ["relay/**/cache"]))

    def test_copy_recursive_glob_needs_review(self):
        self.assertIn("unsupported recursive path pattern", self.findings("FROM fixture\nCOPY relay/**/*.py /payload\n"))

    def test_copy_character_class_needs_review(self):
        self.assertIn("unsupported path pattern", self.findings("FROM fixture\nCOPY relay/[a-z]*.py /payload\n"))

    def test_copy_case_matches_container_semantics(self):
        self.assertIn("COPY source is missing: REQUIREMENTS.txt", self.findings("FROM fixture\nCOPY REQUIREMENTS.txt /payload\n"))

    def test_real_compose_and_fly_contexts(self):
        reports = gate.check(ROOT)
        self.assertEqual(3, len(reports))
        self.assertEqual([], [row for row in reports if row["findings"]])
        relay = [row for row in reports if "relay" in row["build"]]
        self.assertTrue(all(row["context"] == "relay" for row in relay))

if __name__ == "__main__":
    unittest.main()
