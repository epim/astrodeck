# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The release workflow's image job inventories the pushed image and cleans up a rejected candidate (#655 part B, #801).

Two steps are added to the image job:

* the image gate, which exports the pushed image's filesystem for each platform and
  runs `tools/licence/audit_artifact.py --image` over it before the image is named;
* the candidate cleanup, which deletes the candidate's package version when the
  gate, the probe or the retag rejected it.

The step texts run under `bash -eo pipefail` as GitHub runs them, against a stubbed
`docker`, `python` and `gh` (the last one answering `--jq` through the real jq), so
what is asserted is behaviour: which calls are made, in which order, what happens when
a platform's inventory fails, and that a package version carrying a release tag is
never deleted. A daemon-backed run is what only a tag can give.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
JQ = shutil.which("jq")
DIGEST = "sha256:" + "ab" * 32
FAKE_IMAGE = "ghcr.io/example/astrodeck@" + DIGEST


def image_job() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))["jobs"]["image"]


def step_index(job: dict, predicate) -> int:
    found = [i for i, step in enumerate(job["steps"]) if predicate(step)]
    assert len(found) == 1, "expected exactly one matching step, found %d" % len(found)
    return found[0]


def gate_index(job):
    return step_index(job, lambda s: "audit_artifact.py" in s.get("run", ""))


def cleanup_index(job):
    return step_index(job, lambda s: "DELETE" in s.get("run", ""))


class WorkflowShape(unittest.TestCase):
    def setUp(self):
        self.job = image_job()
        self.steps = self.job["steps"]

    def test_the_gate_runs_after_the_build_and_before_the_probe_and_the_names(self):
        build = step_index(self.job, lambda s: s.get("uses", "").startswith("docker/build-push-action@"))
        probe = step_index(self.job, lambda s: "native_probe.py" in s.get("run", ""))
        retag = step_index(self.job, lambda s: "imagetools create" in s.get("run", ""))
        gate = gate_index(self.job)
        self.assertLess(build, gate)
        self.assertLess(gate, probe)
        self.assertLess(gate, retag, "the inventory must finish before the image gets its release names")

    def test_the_gate_inspects_the_pushed_digest_on_both_platforms_and_cannot_be_skipped(self):
        step = self.steps[gate_index(self.job)]
        self.assertIn("steps.candidate.outputs.digest", step["env"]["IMAGE"])
        for platform in ("linux/amd64", "linux/arm64"):
            self.assertIn(platform, step["run"])
        self.assertNotIn("if", step)
        self.assertNotIn("continue-on-error", step)
        self.assertNotIn("||", step["run"], "a failing inventory must stop the retag")

    def test_python_is_set_up_before_the_gate_runs_a_script(self):
        setup = step_index(self.job, lambda s: s.get("uses", "").startswith("actions/setup-python@"))
        self.assertLess(setup, gate_index(self.job))

    def test_the_gate_report_is_kept_even_when_the_gate_fails(self):
        uploads = [s for s in self.steps if s.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(1, len(uploads))
        self.assertEqual("always()", uploads[0]["if"])
        self.assertIn("image-gate-", uploads[0]["with"]["path"])
        self.assertGreater(self.steps.index(uploads[0]), gate_index(self.job))

    def test_the_cleanup_runs_after_the_retag_only_when_something_failed(self):
        """Mutant IMAGE-CLEANUP-ALWAYS (`failure() &&` removed from the cleanup's if):
          AssertionError: 'failure()' not found in "steps.candidate.outputs.digest != ''" : a released image must never be a cleanup candidate
        """
        step = self.steps[cleanup_index(self.job)]
        retag = step_index(self.job, lambda s: "imagetools create" in s.get("run", ""))
        self.assertGreater(self.steps.index(step), retag)
        self.assertIn("failure()", step["if"], "a released image must never be a cleanup candidate")
        self.assertIn("steps.candidate.outputs.digest != ''", step["if"], "no digest means nothing was pushed")
        self.assertEqual("${{ secrets.GITHUB_TOKEN }}", step["env"]["GH_TOKEN"])
        self.assertIn("steps.candidate.outputs.digest", step["env"]["DIGEST"])
        self.assertEqual("write", self.job["permissions"]["packages"])


@unittest.skipUnless(BASH, "needs bash to execute the release workflow's step text")
class GateStepBehaviour(unittest.TestCase):
    STUBS = (
        'docker() {\n'
        '  printf "docker %s\\n" "$*" >> "$CALLS"\n'
        '  if [ "$1" = create ]; then printf "container-%s\\n" "${3#linux/}"; fi\n'
        '}\n'
        'python() {\n'
        '  printf "python %s\\n" "$*" >> "$CALLS"\n'
        '  case "$*" in *image-arm64*) return "$ARM64_STATUS" ;; esac\n'
        '  return 0\n'
        '}\n'
    )

    def run_gate(self, arm64_status=0):
        job = image_job()
        run = job["steps"][gate_index(job)]["run"]
        with tempfile.TemporaryDirectory(prefix="wp18-gate-step-") as scratch:
            scratch = Path(scratch)
            calls = scratch / "calls.txt"
            (scratch / "step.sh").write_text(self.STUBS + run + "\n", encoding="utf-8", newline="\n")
            env = dict(os.environ, CALLS=calls.as_posix(), IMAGE=FAKE_IMAGE,
                       RUNNER_TEMP=scratch.as_posix(), ARM64_STATUS=str(arm64_status))
            proc = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", (scratch / "step.sh").as_posix()],
                                  env=env, capture_output=True, text=True, timeout=120)
            recorded = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
            return proc, [line.replace(scratch.as_posix(), "<tmp>") for line in recorded]

    def test_each_platform_is_exported_inspected_and_its_container_removed(self):
        """Mutant IMAGE-GATE-PLATFORM-UNUSED (--platform "$platform" deleted from the docker create):
          AssertionError: 'docker rm container-arm64' != 'docker rm container-'
        """
        proc, calls = self.run_gate()

        self.assertEqual(0, proc.returncode, proc.stderr)
        report = "tools/licence/audit_artifact.py --image <tmp>/image-%s.tar --report <tmp>/image-gate-%s.json"
        self.assertEqual([
            "docker create --platform linux/amd64 " + FAKE_IMAGE,
            "docker export --output <tmp>/image-amd64.tar container-amd64",
            "docker rm container-amd64",
            "python " + report % ("amd64", "amd64"),
            "docker create --platform linux/arm64 " + FAKE_IMAGE,
            "docker export --output <tmp>/image-arm64.tar container-arm64",
            "docker rm container-arm64",
            "python " + report % ("arm64", "arm64"),
        ], calls)

    def test_a_failing_inventory_on_either_platform_fails_the_step(self):
        """Mutant IMAGE-GATE-FAILURE-IGNORED (the python call given || true; the structural test sees the `||` first):
          AssertionError: '||' unexpectedly found in 'for platform in linux/amd64 linux/arm64; do ...
        """
        proc, calls = self.run_gate(arm64_status=1)

        self.assertNotEqual(0, proc.returncode, "a rejected inventory must stop the step")
        self.assertEqual("docker rm container-arm64", calls[-2], "the export finished before the failing inventory")


@unittest.skipUnless(BASH and JQ, "needs bash and jq to execute the cleanup step's text")
class CleanupStepBehaviour(unittest.TestCase):
    # gh answers a --jq argument through the real jq, as the real gh does.
    GH = (
        'gh() {\n'
        '  printf "gh %s\\n" "$*" >> "$CALLS"\n'
        '  jq_filter=""; method=GET; path=""\n'
        '  while [ "$#" -gt 0 ]; do\n'
        '    case "$1" in\n'
        '      --jq) jq_filter=$2; shift ;;\n'
        '      --method) method=$2; shift ;;\n'
        '      --paginate) ;;\n'
        '      api) ;;\n'
        '      *) path=$1 ;;\n'
        '    esac\n'
        '    shift\n'
        '  done\n'
        '  case "$method:$path" in\n'
        '    GET:/users/*/packages/*) jq -r "$jq_filter" "$VERSIONS_FILE" ;;\n'
        '    GET:/orgs/*/packages/*) jq -r "$jq_filter" "$VERSIONS_FILE" ;;\n'
        '    GET:/users/*) printf "%s\\n" "$OWNER_TYPE" ;;\n'
        '    DELETE:*) ;;\n'
        '    *) return 97 ;;\n'
        '  esac\n'
        '}\n'
    )

    def run_cleanup(self, versions, owner_type="User"):
        job = image_job()
        run = job["steps"][cleanup_index(job)]["run"]
        with tempfile.TemporaryDirectory(prefix="wp18-cleanup-step-") as scratch:
            scratch = Path(scratch)
            calls = scratch / "calls.txt"
            versions_file = scratch / "versions.json"
            versions_file.write_text(json.dumps(versions), encoding="utf-8")
            (scratch / "step.sh").write_text(self.GH + run + "\n", encoding="utf-8", newline="\n")
            env = dict(os.environ, CALLS=calls.as_posix(), VERSIONS_FILE=versions_file.as_posix(),
                       OWNER_TYPE=owner_type, DIGEST=DIGEST, GH_TOKEN="unused",
                       GITHUB_REPOSITORY="example/astrodeck", GITHUB_REPOSITORY_OWNER="example")
            proc = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", (scratch / "step.sh").as_posix()],
                                  env=env, capture_output=True, text=True, timeout=120)
            recorded = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
            return proc, recorded

    @staticmethod
    def version(identifier, name, tags):
        return {"id": identifier, "name": name, "metadata": {"container": {"tags": tags}}}

    def deletions(self, calls):
        return [line for line in calls if "DELETE" in line]

    def test_a_candidate_only_version_is_deleted(self):
        """Mutant IMAGE-CLEANUP-NO-DELETE (the DELETE call replaced by echo DELETE):
          AssertionError: Lists differ: ['gh api --method DELETE /users/example/pa[35 chars]/42'] != []
        """
        versions = [self.version(41, "sha256:" + "cd" * 32, ["v0.3.45", "latest"]),
                    self.version(42, DIGEST, ["candidate-77-1"])]

        proc, calls = self.run_cleanup(versions)

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(["gh api --method DELETE /users/example/packages/container/astrodeck/versions/42"],
                         self.deletions(calls))

    def test_an_untagged_version_of_the_digest_is_deleted_too(self):
        proc, calls = self.run_cleanup([self.version(43, DIGEST, [])])

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(1, len(self.deletions(calls)))

    def test_a_version_that_also_carries_a_release_tag_is_never_deleted(self):
        """Retagging gives the probed digest its release names, so the candidate tag and the release
        tags share one package version; deleting it would delete the release.
        Mutant IMAGE-CLEANUP-IGNORES-RELEASE-TAGS (the `all(...; startswith("candidate-"))` filter removed):
          AssertionError: Lists differ: [] != ['gh api --method DELETE /users/example/pa[35 chars]/42']
        """
        versions = [self.version(42, DIGEST, ["candidate-77-1", "v0.3.46", "latest"])]

        proc, calls = self.run_cleanup(versions)

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual([], self.deletions(calls))
        self.assertIn("nothing to delete", proc.stdout)

    def test_a_digest_with_no_package_version_is_nothing_to_delete(self):
        proc, calls = self.run_cleanup([self.version(41, "sha256:" + "cd" * 32, ["candidate-5-1"])])

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual([], self.deletions(calls))

    def test_an_organisation_owner_uses_the_orgs_endpoint(self):
        proc, calls = self.run_cleanup([self.version(42, DIGEST, ["candidate-77-1"])], owner_type="Organization")

        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(["gh api --method DELETE /orgs/example/packages/container/astrodeck/versions/42"],
                         self.deletions(calls))


if __name__ == "__main__":
    unittest.main()
