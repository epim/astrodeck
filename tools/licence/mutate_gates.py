"""Run named audit mutants, require selected assertion failure, restore bytes."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[2]
TESTS = ["server/tests/" + name for name in (
    "test_credits.py", "test_licensing.py", "test_licence_artifact.py",
    "test_licence_frozen.py", "test_licence_dependencies.py")]

def pytest(selector):
    return subprocess.run([sys.executable, "-m", "pytest", "-n", "0", "-q",
        "--tb=short", *selector], cwd=ROOT, text=True, encoding="utf-8",
        errors="replace", capture_output=True)

def replace_once(old, new):
    def change(data):
        text = data.decode("utf-8")
        assert text.count(old) == 1, "mutant anchor is not unique"
        return text.replace(old, new, 1).encode("utf-8")
    return change

def mutate_json(change):
    def apply(data):
        doc = json.loads(data)
        change(doc)
        return (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    return apply

def drop_credit(doc, name):
    for group in doc["groups"]:
        group["entries"] = [e for e in group["entries"] if e["name"] != name]

CASES = [
    ("NARROW-COPYLEFT-OWNER", "tools/licence_policy.py",
     replace_once('return Resolution(policies=[pol], flag=flag)', 'return Resolution(policies=[pol], flag=None)'),
     "test_credits.py::test_reviewed_copyleft_components_remain_owner_decisions", "reviewed copyleft component lost its owner gate"),
    ("UNKNOWN-VIRTUAL-RUNTIME", "tools/licence/audit_artifact.py",
     replace_once('elif module == "build-helper:commonjsHelpers.js":', 'elif module.startswith("build-helper:"):'),
     "test_licence_artifact.py::test_unknown_virtual_runtime_cannot_inherit_vite_licence", "unclassified build input"),
    ("TARBALL-ROGUE-SDK", "tools/licence/audit_artifact.py",
     replace_once('errors.append(f"{name}: unaccounted binary/data member")', "pass"),
     "test_licence_artifact.py::test_unregistered_binary_in_real_built_tarball_is_rejected", "unaccounted binary/data member"),
    ("SOURCE-EMBEDDED-DATA", "tools/licence/audit_artifact.py",
     replace_once('errors.append(f"{name}: source or embedded data has not been reviewed")', "pass"),
     "test_licence_artifact.py::test_ascii_table_disguised_as_source_needs_review", "source or embedded data has not been reviewed"),
    ("ASSET-HASH", "tools/licence/audit_artifact.py",
     replace_once('if sha256(data) != rule["sha256"]:', 'if False:'),
     "test_licence_artifact.py::test_changed_known_binary_is_not_covered_by_vendor_name", "asset bytes differ"),
    ("COMPANION-NOTICE", "tools/licence/audit_artifact.py",
     replace_once('errors.append(f"{name}: required companion notice is absent or changed")', "pass"),
     "test_licence_artifact.py::test_companion_notice_must_travel_in_archive", "required companion notice is absent"),
    ("BLOCKED-ARTWORK", "tools/licence/audit_artifact.py",
     replace_once('if rule.get("blocked"):', 'if False:'),
     "test_licence_artifact.py::test_owner_needed_asset_cannot_be_cleared_by_registration", "requires an owner decision"),
    ("ASSET-NOTICE", "tools/licence/audit_artifact.py",
     replace_once('if needs_text and not refs:', 'if False:'),
     "test_licence_artifact.py::test_credit_name_alone_does_not_satisfy_asset_notice", "required licence text is absent"),
    ("CORRUPT-TEXT-POOL", "tools/licence/audit_artifact.py",
     replace_once('errors.append(f"{label}: licence text reference is missing, truncated or corrupt")', "pass"),
     "test_licence_artifact.py::test_asset_notice_body_must_resolve_and_match_hash", "licence text reference is missing, truncated or corrupt"),
    ("STALE-UI-CREDITS", "tools/licence/audit_artifact.py",
     replace_once('errors.append("UI build evidence uses stale generated credits")', "pass"),
     "test_licence_artifact.py::test_stale_generated_credits_fail", "stale generated credits"),
    ("PROJECT-VERSION", "ui/src/credits.generated.json",
     mutate_json(lambda d: d["project"].update(version="0.0.0-stale")),
     "test_credits.py::test_project_credit_version_matches_current_manifest", "credits project version is stale"),
    ("INPUT-FRESHNESS", "tools/credits_registry.py",
     lambda d: d + b"\n# Named mutant: changed registry input without regenerated output.\n",
     "test_credits.py::test_generated_registry_policy_and_text_inputs_are_current", "generated credit inputs are stale"),
    ("DEV-RUNTIME-CREDIT", "ui/src/credits.generated.json",
     mutate_json(lambda d: drop_credit(d, "vite")),
     "test_credits.py::test_compiler_runtime_contributors_are_credited", "emitted compiler runtime has no credit"),
    ("BUNDLED-COMPONENT-NOTICE", "ui/src/credits.generated.json",
     mutate_json(lambda d: next(e for g in d["groups"] for e in g["entries"] if e["name"] == "CPython bundled interpreter").update(texts=[])),
     "test_credits.py::test_reviewed_artifact_component_manifest_is_credited_with_exact_notices", "observed binary component lost its complete notice"),
    ("DEPENDENCY-POLICY", "tools/licence/audit_dependencies.py",
     replace_once('return sorted(set(findings))', "return []"),
     "test_licence_dependencies.py::test_unreviewed_copyleft_or_unknown_graph_dependency_fails", "outside the allowed policy"),
    ("FROZEN-ROGUE-SDK", "tools/licence/audit_frozen.py",
     replace_once('findings.append(f"{name}: unaccounted executable member")', "pass"),
     "test_licence_frozen.py::test_new_vendor_binary_in_executable_is_rejected", "unaccounted executable member"),
    ("FROZEN-PLAYERONE", "tools/licence/audit_frozen.py",
     replace_once('if "/vendor/playerone/" in "/" + lower and re.search', 'if False and re.search'),
     "test_licence_frozen.py::test_known_player_one_binary_is_still_owner_blocked", "E   assert False"),
]

def main():
    baseline = pytest(TESTS)
    if baseline.returncode:
        print(baseline.stdout + baseline.stderr)
        raise SystemExit("Baseline must pass before mutating any source")
    rows = []
    for name, relative, mutate, selector, expected in CASES:
        path = ROOT / relative
        original = path.read_bytes()
        before = hashlib.sha256(original).hexdigest()
        try:
            path.write_bytes(mutate(original))
            result = pytest(["server/tests/" + selector])
            output = result.stdout + result.stderr
            killed = result.returncode == 1 and "FAILED" in output and expected in output
            assertions = [line.strip() for line in output.splitlines() if line.startswith("E ")]
            rows.append({"name": name, "file": relative, "test": selector,
                         "killed": killed, "assertions": assertions})
            print(name + ": " + ("KILLED" if killed else "SURVIVED/INVALID"), flush=True)
            if not killed:
                print(output)
                raise RuntimeError("Mutant did not fail the intended assertion")
        finally:
            path.write_bytes(original)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == before, "Source restoration failed"
    restored = pytest(TESTS)
    if restored.returncode:
        print(restored.stdout + restored.stderr)
        raise SystemExit("Restored baseline failed")
    report = [
        "# Licence gate mutation evidence", "",
        "Run 2026-10-01. Baseline and restored focused suites pass. Every changed file was restored from its exact in-memory bytes in a finally block and SHA-256 checked; no git checkout/reset was used.", "",
        "The TARBALL-ROGUE-SDK fixture inserts a vendor DLL before the real release packager runs and proves that the member reaches the resulting archive. Mutating the gate makes that assertion fail.", ""]
    for row in rows:
        report += ["## " + row["name"], "", "- Source: " + row["file"],
                   "- Selected assertion: server/tests/" + row["test"],
                   "- Result: KILLED; exact original bytes restored.", "", "~~~text",
                   *row["assertions"], "~~~", ""]
    report += ["Final verification:", "", "~~~text", restored.stdout.strip(), "~~~", ""]
    (ROOT / "tools/licence/mutation-evidence.md").write_text("\n".join(report), encoding="utf-8")
    print(f"{len(rows)} named mutants killed; restored suite is green.")

if __name__ == "__main__":
    main()
