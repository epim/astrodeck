# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Mutation proof that release publication cannot bypass artifact gates."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
WORKFLOW=ROOT/".github/workflows/release.yml"


def _swap_probe_and_retag():
    """The image job's probe step and its retag step in the other order: the
    names (`:latest`, the version tag) would exist before the native engine in
    the pushed image has been proven. A two-block swap, so it is built from
    the workflow's own text instead of being written out here."""
    text=WORKFLOW.read_text(encoding="utf-8").replace("\r\n","\n")
    probe=text.index("      # The probe runs the pushed image itself")
    retag=text.index("      # The same digest that was probed")
    tail='"$IMAGE"\n'
    end=text.index(tail,retag)+len(tail)
    return text[probe:end], text[retag:end]+text[probe:retag]


_PROBE_FIRST,_RETAG_FIRST=_swap_probe_and_retag()
MUTANTS=[
    ("ALL-TARGETS-BEFORE-PUBLISH", 'needs: [release, binaries]', 'needs: [release]', 'test_publication_waits_for_all_artifacts', "publication must wait for source and every binary target"),
    ("SOURCE-ARTIFACT-GATE", '--kind source-tar', '--kind server-wheel', 'test_source_gate_precedes_payload_upload', 'source archive must run one artifact gate'),
    ("FROZEN-ARTIFACT-GATE", '--kind frozen', '--kind server-wheel', 'test_both_platform_artifacts_are_gated', "both native-wheel and frozen artifact gates must execute"),
    ("NATIVE-WHEEL-GATE", '--kind native-wheel', '--kind server-wheel', 'test_both_platform_artifacts_are_gated', "both native-wheel and frozen artifact gates must execute"),
    ("MATCHING-NATIVE-BUILD", 'python packaging/build_native.py --out dist', 'echo packaging/build_native.py --out dist', 'test_four_native_targets', '1 != 0'),
    ("DSS2-FETCH-EXCLUSION", '      - name: Build source release bundle', '      - name: Forbidden survey fetch\n        run: python -m astrodeck.catalog.survey_pack fetch --order 3 --dest dist/dss2\n      - name: Build source release bundle', 'test_dss2_is_never_fetched_or_passed', 'survey_pack fetch'),
    ("PAYLOAD-UPLOAD-MUST-SUCCEED", '      - name: Retain accepted source release files\n        uses:', '      - name: Retain accepted source release files\n        if: always()\n        uses:', 'test_source_gate_precedes_payload_upload', 'payload upload must require prior gate success'),
    ("REPORT-ISOLATION", 'pattern: accepted-*', 'pattern: "*"', 'test_diagnostics_cannot_be_published_as_payload', 'accepted-*'),
    ("IMAGE-NATIVE-REQUIRED", 'build-args: |\n            REQUIRE_NATIVE=1', 'build-args: |\n            REQUIRE_NATIVE=0', 'test_image_build_requires_the_native_engine', "'REQUIRE_NATIVE=1' not found in"),
    ("IMAGE-DOWNLOAD-REMOVED", '- name: Download the accepted linux payloads\n        uses: actions/download-artifact@v4', '- name: Download the accepted linux payloads\n        run: echo skipped', 'test_image_downloads_the_linux_native_wheels_it_installs', "exactly one artifact download"),
    ("IMAGE-PROBE-BEFORE-TAG", _PROBE_FIRST, _RETAG_FIRST, 'test_nothing_user_visible_is_tagged_before_the_probe_passes', "the retag that creates :latest comes after the probe"),
]

def run(target=None):
    return subprocess.run([sys.executable,"-B","-m","unittest","test_release_pipeline"+(".ReleasePipeline."+target if target else "")],cwd=ROOT/"server/tests",text=True,capture_output=True)

def main():
    if run().returncode: raise RuntimeError("Unmutated pipeline tests must pass first")
    rows=["# Release workflow mutation evidence", "", "Run " + datetime.now(timezone.utc).isoformat(), "", "These tests inspect real workflow commands and dependency/upload order. No workflow is dispatched. Every mutation is killed by its named assertion; exact bytes are restored in finally and SHA-256 checked.", ""]
    for name,before,after,target,expected in MUTANTS:
        backup=WORKFLOW.read_bytes()
        text=backup.decode("utf-8").replace("\r\n","\n")
        if text.count(before)!=1: raise RuntimeError(name+": mutation anchor is not unique")
        try:
            WORKFLOW.write_text(text.replace(before,after,1),encoding="utf-8")
            result=run(target)
            assertion=next((line for line in result.stderr.splitlines() if line.startswith("AssertionError:")),"")
            if result.returncode!=1 or expected not in assertion or "FAILED (failures=1)" not in result.stderr:
                raise RuntimeError(name+": survived or failed outside intended assertion\n"+result.stderr)
            rows += ["- "+name+": `ReleasePipeline."+target+"`", "  `"+assertion+"`", ""]
            print(name+": KILLED")
        finally:
            WORKFLOW.write_bytes(backup)
            if hashlib.sha256(WORKFLOW.read_bytes()).digest()!=hashlib.sha256(backup).digest(): raise RuntimeError("Restoration failed")
    result=run()
    if result.returncode: raise RuntimeError("Restored pipeline suite failed")
    rows += ["## Restored suite", "", "```text", result.stderr.strip(), "```", ""]
    (ROOT/"tools/licence/release-workflow-mutations.md").write_text("\n".join(rows),encoding="utf-8")
    print(result.stderr,end="")

if __name__=="__main__":
    main()
