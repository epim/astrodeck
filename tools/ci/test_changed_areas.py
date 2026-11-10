# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tests for tools/ci/changed_areas.py and the ratchet that ties ci.yml to areas.json (#1259).

Run with: python -B -m unittest discover -s tools/ci -p 'test_*.py'
The ratchet classes need PyYAML (the `changes` job installs it).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

import changed_areas  # noqa: E402

CONFIG = {
    "always_all": [".github/**", "tools/ci/areas.json", "tools/ci/changed_areas.py"],
    "inert": ["notes/**"],
    "areas": {
        "server": ["server/**", "ui/src/lib/**", "tools/**"],
        "ui": ["ui/**"],
        "relay": ["relay/**"],
        "container": ["Dockerfile", "docker-compose*.yml", "server/**"],
    },
}
EVERY = ["container", "relay", "server", "ui"]
ZERO = "0" * 40


def pick(paths, event="pull_request", ref="refs/pull/7/merge", config=CONFIG):
    return changed_areas.select_areas(paths, event, config, ref=ref)


class SelectAreasTests(unittest.TestCase):
    def test_a_path_no_area_owns_selects_every_area(self):
        # Fail open: a path the map has never heard of must run everything.
        self.assertEqual(pick(["brand-new-dir/x.py"]), EVERY)

    def test_one_unknown_path_among_known_ones_still_selects_every_area(self):
        self.assertEqual(pick(["relay/a.py", "mystery.bin"]), EVERY)

    def test_a_workflow_change_selects_every_area(self):
        self.assertEqual(pick([".github/workflows/ci.yml"]), EVERY)

    def test_a_change_to_the_area_map_selects_every_area(self):
        self.assertEqual(pick(["tools/ci/areas.json"]), EVERY)

    def test_a_change_to_the_tool_selects_every_area(self):
        self.assertEqual(pick(["tools/ci/changed_areas.py"]), EVERY)

    def test_a_tag_selects_every_area(self):
        self.assertEqual(pick(["relay/a.py"], event="push", ref="refs/tags/v1.2.3"), EVERY)

    def test_a_manual_dispatch_selects_every_area(self):
        self.assertEqual(pick(["relay/a.py"], event="workflow_dispatch", ref="refs/heads/x"), EVERY)

    def test_an_event_the_tool_does_not_know_selects_every_area(self):
        self.assertEqual(pick(["relay/a.py"], event="schedule", ref="refs/heads/main"), EVERY)

    def test_a_ui_only_change_selects_ui_and_what_server_tests_read_of_ui(self):
        self.assertEqual(pick(["ui/src/lib/api.ts"]), ["server", "ui"])
        self.assertEqual(pick(["ui/src/components/Foo.tsx"]), ["ui"])

    def test_a_path_matching_several_areas_selects_each_once(self):
        self.assertEqual(pick(["server/astrodeck/x.py"]), ["container", "server"])

    def test_the_union_over_several_paths(self):
        self.assertEqual(pick(["relay/a.py", "ui/src/components/Foo.tsx"]), ["relay", "ui"])

    def test_an_empty_diff_selects_every_area(self):
        # An empty list means the diff could not say what changed: do not guess.
        self.assertEqual(pick([]), EVERY)

    def test_an_inert_path_selects_nothing(self):
        self.assertEqual(pick(["notes/todo.txt"]), [])

    def test_an_inert_path_does_not_hide_a_real_one_beside_it(self):
        self.assertEqual(pick(["notes/todo.txt", "relay/a.py"]), ["relay"])

    def test_a_push_to_a_branch_filters_like_a_pull_request(self):
        self.assertEqual(pick(["relay/a.py"], event="push", ref="refs/heads/main"), ["relay"])

    def test_a_glob_star_does_not_cross_a_directory(self):
        self.assertEqual(pick(["docker-compose.usb.yml"]), ["container"])
        self.assertEqual(pick(["docker-compose/deep/x.yml"]), EVERY)

    def test_windows_separators_and_dot_slash_are_normalised(self):
        self.assertEqual(pick([".\\relay\\a.py"]), ["relay"])
        self.assertEqual(pick(["./relay/a.py"]), ["relay"])


class GlobTests(unittest.TestCase):
    def test_double_star_alone_matches_every_path(self):
        self.assertTrue(changed_areas.matches("a/b/c.txt", "**"))
        self.assertTrue(changed_areas.matches("top.txt", "**"))

    def test_directory_glob_matches_depth_and_not_a_sibling_prefix(self):
        self.assertTrue(changed_areas.matches("ui/a/b.ts", "ui/**"))
        self.assertFalse(changed_areas.matches("uix/a.ts", "ui/**"))

    def test_exact_name_matches_only_itself(self):
        self.assertTrue(changed_areas.matches("Dockerfile", "Dockerfile"))
        self.assertFalse(changed_areas.matches("Dockerfile.dev", "Dockerfile"))
        self.assertFalse(changed_areas.matches("sub/Dockerfile", "Dockerfile"))

    def test_regex_metacharacters_in_a_pattern_are_literal(self):
        self.assertTrue(changed_areas.matches("a+b.txt", "a+b.txt"))
        self.assertFalse(changed_areas.matches("aab.txt", "a+b.txt"))


class ConfigValidationTests(unittest.TestCase):
    def test_a_config_without_areas_is_refused(self):
        with self.assertRaises(ValueError):
            changed_areas.validate_config({"always_all": [], "inert": []})

    def test_an_area_whose_globs_are_not_a_list_is_refused(self):
        with self.assertRaises(ValueError):
            changed_areas.validate_config({"always_all": [], "inert": [], "areas": {"x": "ui/**"}})


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def commit_files(cwd, files):
    for name, text in files.items():
        target = Path(cwd, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git(cwd, "add", "-A")
    git(cwd, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "-q", "-m", "c")
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


class WrapperTests(unittest.TestCase):
    """The thin git wrapper, against a real throwaway repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.tmp.name
        git(self.repo, "init", "-q")
        self.cfg = Path(self.repo, "areas.json")
        self.cfg.write_text(json.dumps(CONFIG), encoding="utf-8")
        self.first = commit_files(self.repo, {"relay/a.py": "1\n", "ui/src/components/Foo.tsx": "1\n"})

    def run_main(self, base, head, event="pull_request", ref="refs/pull/7/merge"):
        out, err = io.StringIO(), io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.repo)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = changed_areas.main(["--base", base, "--head", head, "--event", event,
                                           "--ref", ref, "--areas", str(self.cfg)])
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0)
        return dict(line.split("=") for line in out.getvalue().split())

    def test_a_ui_only_commit_turns_on_ui_and_nothing_unrelated(self):
        head = commit_files(self.repo, {"ui/src/components/Foo.tsx": "2\n"})
        self.assertEqual(self.run_main(self.first, head),
                         {"container": "false", "relay": "false", "server": "false", "ui": "true"})

    def test_a_commit_of_an_unknown_path_turns_everything_on(self):
        head = commit_files(self.repo, {"stray.bin": "x\n"})
        self.assertEqual(set(self.run_main(self.first, head).values()), {"true"})

    def test_a_zero_base_sha_as_on_a_new_branch_turns_everything_on(self):
        head = commit_files(self.repo, {"ui/src/components/Foo.tsx": "2\n"})
        self.assertEqual(set(self.run_main(ZERO, head, event="push").values()), {"true"})

    def test_a_base_the_repository_does_not_have_turns_everything_on(self):
        head = commit_files(self.repo, {"ui/src/components/Foo.tsx": "2\n"})
        self.assertEqual(set(self.run_main("1" * 40, head).values()), {"true"})

    def test_an_empty_base_turns_everything_on(self):
        head = commit_files(self.repo, {"ui/src/components/Foo.tsx": "2\n"})
        self.assertEqual(set(self.run_main("", head, event="push").values()), {"true"})

    def test_a_rename_reports_the_old_path_too(self):
        git(self.repo, "mv", "relay/a.py", "ui/src/components/a.py")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-q", "-m", "mv")
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()
        got = self.run_main(self.first, head)
        self.assertEqual((got["relay"], got["ui"]), ("true", "true"))

    def test_a_manual_dispatch_turns_everything_on_without_a_diff(self):
        got = self.run_main("", "HEAD", event="workflow_dispatch", ref="refs/heads/main")
        self.assertEqual(set(got.values()), {"true"})


def load_ci():
    import yaml  # PyYAML: installed by the `changes` job

    return yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


# Jobs that keep their own condition and are not gated by an area.
ALWAYS = {"windows-com", "binary-smoke-windows"}
BOOKKEEPING = {"changes", "ci-ok"}


class RealAreasTests(unittest.TestCase):
    def setUp(self):
        self.config = changed_areas.load_config(HERE / "areas.json")

    def tracked(self):
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, check=True,
                             capture_output=True).stdout.decode("utf-8")
        return sorted(p for p in out.split("\0") if p)

    def test_every_tracked_file_is_placed_in_the_map(self):
        # A new top-level path must be placed in areas.json deliberately; until
        # it is, the tool fails open and runs everything.
        unplaced = [p for p in self.tracked() if changed_areas.placement(p, self.config) is None]
        top = sorted({p.split("/")[0] for p in unplaced})
        self.assertEqual(top, [], "top-level paths with no entry in tools/ci/areas.json")

    def test_the_workflow_and_the_map_select_every_area(self):
        every = sorted(self.config["areas"])
        for path in (".github/workflows/ci.yml", "tools/ci/areas.json", "tools/ci/changed_areas.py"):
            self.assertEqual(pick([path], config=self.config), every, path)

    def test_a_server_change_no_ui_test_reads_does_not_run_the_ui_job(self):
        # The ui area is ui/** plus what UI tests declare reading (#1268); see
        # UiAreaTests below for the ratchet that ties the two together.
        for path in ("server/astrodeck/guide/native.py", "server/tests/test_api_fence.py",
                     "tools/ci/changed_areas_notes.txt", "native/Cargo.toml"):
            got = pick([path], config=self.config)
            self.assertNotIn("ui", got, path)
        self.assertIn("server", pick(["server/astrodeck/guide/native.py"], config=self.config))

    def test_a_server_file_a_ui_test_reads_runs_the_ui_job_and_the_server_job(self):
        for path in ("server/astrodeck/hub.py", "server/tests/fixtures/flow_progress_continue.json"):
            got = pick([path], config=self.config)
            self.assertIn("server", got, path)
            self.assertIn("ui", got, path)
            self.assertNotIn("relay", got, path)

    def test_a_relay_change_does_not_run_the_ui_job(self):
        self.assertNotIn("ui", pick(["relay/app.py"], config=self.config))

    def test_a_relay_change_runs_the_relay_job(self):
        self.assertIn("relay", pick(["relay/app.py"], config=self.config))

    def test_the_native_job_runs_for_a_native_change_only(self):
        self.assertIn("native", pick(["native/Cargo.toml"], config=self.config))
        self.assertNotIn("native", pick(["relay/app.py"], config=self.config))

    def test_a_ui_change_runs_the_ui_job(self):
        self.assertIn("ui", pick(["ui/src/App.tsx"], config=self.config))

    def test_a_ui_change_also_runs_the_server_job_because_server_tests_read_ui_sources(self):
        # The read map of #1259 found 37 server test files opening ui/src files.
        got = pick(["ui/src/lib/humanize.ts"], config=self.config)
        self.assertEqual(got, ["container-security", "server", "ui"])

    def test_a_server_change_runs_the_native_job_that_grades_the_wheel_tests(self):
        self.assertIn("native", pick(["server/astrodeck/guide/native.py"], config=self.config))

    def test_a_relay_change_runs_relay_container_and_server_jobs(self):
        got = pick(["relay/relay/server.py"], config=self.config)
        self.assertEqual(got, ["container-security", "relay", "server"])

    def test_a_directory_no_test_reads_runs_no_job(self):
        self.assertEqual(pick(["artifacts/flows-parity/01-library.png"], config=self.config), [])
        self.assertEqual(pick([".claude/skills/stop-slop/SKILL.md"], config=self.config), [])

    def test_a_change_to_the_mosaic_spec_runs_the_server_job_that_reads_it(self):
        spec = "docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-design.md"
        got = pick([spec], config=self.config)
        self.assertIn("server", got)
        self.assertNotIn("relay", got)

    def test_every_document_a_test_reads_selects_the_server_job(self):
        # The run-time read map of WP 7.2 (#1267): files under docs/ and
        # Markdown elsewhere that a server test opened.
        for path in (
            "docs/comhost.md",
            "docs/flows/nina-import.md",
            "docs/guide/guiding.md",
            "docs/hardware/player-one-sdk-licensing.md",
            "docs/ui-rebuild/01-screen-ia-map.md",
            "THIRD-PARTY-NOTICES.md",
            "README.md",
            "design_handoff_astrodeck_flows/milestone2-contract.md",
            "server/astrodeck/vendor/playerone/README.md",
            "tools/ux/uxkit.py",
        ):
            self.assertIn("server", pick([path], config=self.config), path)

    def test_the_documents_the_container_contract_reads_select_that_job(self):
        for path in ("docs/guide/install-docker.md", "docs/SECURITY.md", "relay/README.md"):
            self.assertIn("container-security", pick([path], config=self.config), path)

    def test_a_plan_document_runs_no_job(self):
        self.assertEqual(pick(["docs/superpowers/plans/2026-11-09-open-issue-plan-2.md"],
                              config=self.config), [])

    def test_a_plan_document_beside_a_read_document_still_runs_the_reader(self):
        got = pick(["docs/superpowers/plans/x.md", "docs/comhost.md"], config=self.config)
        self.assertIn("server", got)

    def test_root_markdown_a_test_reads_selects_the_server_job(self):
        self.assertIn("server", pick(["THIRD-PARTY-NOTICES.md"], config=self.config))

    def test_a_new_document_directory_falls_open(self):
        every = sorted(self.config["areas"])
        self.assertEqual(pick(["docs/a-new-dir/x.md"], config=self.config), every)

    def test_a_new_top_level_path_falls_open(self):
        every = sorted(self.config["areas"])
        self.assertEqual(pick(["a-new-top-level-dir/x.py"], config=self.config), every)
        self.assertEqual(pick(["a-new-root-file.py"], config=self.config), every)

    def test_no_path_is_both_inert_and_owned(self):
        for pattern in self.config["inert"]:
            sample = pattern.replace("**", "x")
            self.assertEqual(changed_areas.owners(sample, self.config), [], pattern)


UI_SRC = REPO / "ui" / "src"
DISK_READS = re.compile(r"^[ \t]*//[ \t]*disk-reads:[ \t]*(.+?)[ \t]*\r?$", re.M)


def declared_outside_ui():
    """Each path outside ui/ that a UI test file declares reading, with the files that do.

    The declaration is a `// disk-reads: a, b/` comment line (repo-root-relative
    paths, a trailing slash meaning a directory), read by ui/tools/selectTests.mjs.
    """
    found = {}
    for path in sorted(UI_SRC.rglob("*.test.ts*")):
        if path.suffix not in (".ts", ".tsx"):
            continue
        for match in DISK_READS.finditer(path.read_text(encoding="utf-8")):
            for declared in match.group(1).split(","):
                declared = declared.strip()
                if declared and not declared.startswith("ui/"):
                    found.setdefault(declared, []).append(path.relative_to(REPO).as_posix())
    return found


def glob_for(declared):
    return declared + "**" if declared.endswith("/") else declared


class UiAreaTests(unittest.TestCase):
    """The ui area is ui/** and the paths outside ui/ that UI tests declare reading (#1268).

    A UI test that reads a file outside ui/ must make the ui job run when that
    file changes, and a path in the area that no test declares is a path that
    runs the ui job for nothing. Both directions are checked, from the
    declarations themselves.
    """

    def setUp(self):
        self.config = changed_areas.load_config(HERE / "areas.json")
        self.declared = declared_outside_ui()

    def test_the_declarations_were_found(self):
        self.assertGreater(len(self.declared), 20, "no UI test declares what it reads outside ui/")

    def test_every_path_a_ui_test_declares_outside_ui_selects_the_ui_job(self):
        for declared, tests in sorted(self.declared.items()):
            sample = declared + "probe.txt" if declared.endswith("/") else declared
            where = "%s (declared by %s)" % (declared, tests[0])
            self.assertEqual(changed_areas.placement(sample, self.config), "owned", where)
            self.assertIn("ui", changed_areas.owners(sample, self.config), where)

    def test_every_path_a_ui_test_declares_outside_ui_exists(self):
        # selectTestsRatchet.test.ts checks the paths under ui/; it cannot stat
        # these without reading them, which would run it for every server change.
        for declared, tests in sorted(self.declared.items()):
            where = "%s (declared by %s)" % (declared, tests[0])
            target = REPO / declared
            self.assertTrue(target.is_dir() if declared.endswith("/") else target.is_file(), where)

    def test_the_ui_area_names_only_paths_a_ui_test_declares(self):
        outside = sorted(g for g in self.config["areas"]["ui"] if not g.startswith("ui/"))
        self.assertEqual(outside, sorted(glob_for(d) for d in self.declared))
        self.assertIn("ui/**", self.config["areas"]["ui"])


class WorkflowRatchetTests(unittest.TestCase):
    """A job added to ci.yml without an area, or left out of ci-ok, fails here."""

    def setUp(self):
        self.ci = load_ci()
        self.jobs = self.ci["jobs"]
        self.areas = changed_areas.load_config(HERE / "areas.json")["areas"]

    def gated(self):
        return set(self.jobs) - BOOKKEEPING - ALWAYS

    def test_every_job_has_an_area_or_is_on_the_always_list(self):
        unplaced = sorted(set(self.jobs) - BOOKKEEPING - ALWAYS - set(self.areas))
        self.assertEqual(unplaced, [], "jobs with no entry in tools/ci/areas.json")

    def test_every_area_names_a_job_that_exists(self):
        self.assertEqual(sorted(set(self.areas) - set(self.jobs)), [])

    def test_ci_ok_needs_every_other_job(self):
        needs = self.jobs["ci-ok"]["needs"]
        needs = [needs] if isinstance(needs, str) else needs
        missing = sorted(set(self.jobs) - {"ci-ok"} - set(needs))
        self.assertEqual(missing, [], "jobs ci-ok does not wait for")

    def test_ci_ok_runs_always_and_fails_on_failure_or_cancel(self):
        job = self.jobs["ci-ok"]
        self.assertEqual(str(job["if"]).strip(), "always()")
        text = json.dumps(job)
        self.assertIn("contains(needs.*.result, 'failure')", text)
        self.assertIn("contains(needs.*.result, 'cancelled')", text)
        self.assertNotIn("'skipped'", text)

    def test_each_gated_job_needs_changes_and_reads_its_own_output(self):
        for name in sorted(self.gated()):
            job = self.jobs[name]
            needs = job.get("needs", [])
            needs = [needs] if isinstance(needs, str) else needs
            self.assertIn("changes", needs, name)
            self.assertIn("needs.changes.outputs.%s == 'true'" % name, str(job.get("if")), name)

    def test_only_scratch_notes_are_ignored_by_the_workflow_triggers(self):
        # Tests read Markdown and docs/ (#1267), so ci.yml must start for them
        # and areas.json decide. The `on:` key loads as True under YAML 1.1.
        triggers = self.ci.get("on", self.ci.get(True))
        for event in ("push", "pull_request"):
            self.assertEqual(triggers[event]["paths-ignore"], [".remember/**"], event)

    def test_the_always_jobs_keep_their_own_condition(self):
        self.assertIn("refs/heads/main", self.jobs["windows-com"]["if"])
        self.assertIn("binary_smoke", self.jobs["binary-smoke-windows"]["if"])

    def test_changes_outputs_one_flag_per_area(self):
        # Beside the area flags, `changes` carries the already-green flags (#1260):
        # one per area and the comma list ci-ok logs. test_already_green.py grades those.
        outputs = self.jobs["changes"]["outputs"]
        flags = {name for name in outputs if not name.startswith("already-green-")}
        self.assertEqual(sorted(flags), sorted(self.areas))


if __name__ == "__main__":
    unittest.main()
