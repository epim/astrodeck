"""Mutation proof that release publication cannot bypass artifact gates."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
WORKFLOW=ROOT/".github/workflows/release.yml"
MUTANTS=[
    ("ALL-TARGETS-BEFORE-PUBLISH", 'needs: [release, binaries]', 'needs: [release]', 'test_publication_waits_for_all_artifacts', "publication must wait for source and every binary target"),
    ("SOURCE-ARTIFACT-GATE", '--kind source-tar', '--kind server-wheel', 'test_source_gate_precedes_payload_upload', 'source archive must run one artifact gate'),
    ("FROZEN-ARTIFACT-GATE", '--kind frozen', '--kind server-wheel', 'test_both_platform_artifacts_are_gated', "both native-wheel and frozen artifact gates must execute"),
    ("NATIVE-WHEEL-GATE", '--kind native-wheel', '--kind server-wheel', 'test_both_platform_artifacts_are_gated', "both native-wheel and frozen artifact gates must execute"),
    ("MATCHING-NATIVE-BUILD", 'python packaging/build_native.py --out dist', 'echo packaging/build_native.py --out dist', 'test_four_native_targets', '1 != 0'),
    ("DSS2-FETCH-EXCLUSION", '      - name: Build source release bundle', '      - name: Forbidden survey fetch\n        run: python -m astrodeck.catalog.survey_pack fetch --order 3 --dest dist/dss2\n      - name: Build source release bundle', 'test_dss2_is_never_fetched_or_passed', 'survey_pack fetch'),
    ("PAYLOAD-UPLOAD-MUST-SUCCEED", '      - name: Retain accepted source release files\n        uses:', '      - name: Retain accepted source release files\n        if: always()\n        uses:', 'test_source_gate_precedes_payload_upload', 'payload upload must require prior gate success'),
    ("REPORT-ISOLATION", 'pattern: accepted-*', 'pattern: "*"', 'test_diagnostics_cannot_be_published_as_payload', 'accepted-*'),
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
