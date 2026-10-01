"""Named release-gate mutants with exact-byte restoration and fresh imports."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
GATE = "tools/licence/audit_release.py"
PROOF = "tools/licence/release_provenance.py"
PREFIX = "tools.licence.test_release_gate."
MUTANTS = [
    ("ignore-runtime-code-names", PROOF, 'code.co_code.hex(), code.co_name, code.co_qualname,', 'code.co_code.hex(),', "AdditionalBoundaryTests.test_code_name_and_qualname_are_runtime_visible_proof"),
    ("pyz-duplicate-member-bypass", PROOF, ' or name in seen:', ':', "AdditionalBoundaryTests.test_pyz_duplicate_member_is_rejected_before_dict_conversion"),
    ("pyz-hidden-gap-bypass", PROOF, 'if start != cursor or end <= start:', 'if end <= start:', "AdditionalBoundaryTests.test_pyz_unexplained_payload_bytes_are_rejected"),
    ("native-machine-header-bypass", GATE, 'if not native_machine_matches(binary, platform_id):', 'if False:', "NativeFixture.test_pe_machine_cannot_be_arm_with_x64_tag"),
    ("replace-root-notices-and-reseal", GATE, 'if provenance.normalized_source(notice["THIRD-PARTY-NOTICES.md"]).strip() != provenance.normalized_source((root / "THIRD-PARTY-NOTICES.md").read_bytes()).strip():', 'if False:', "NativeFixture.test_root_notices_cannot_be_replaced_and_resealed"),
    ("replace-native-license-expression", GATE, 'if expected_license != "MPL-2.0" or package_meta.get("License-Expression") != expected_license:', 'if False:', "NativeFixture.test_native_license_expression_must_match_reviewed_project"),
    ("runtime-directory-authenticates-any-bytes", PROOF, 'if rule and sha(path.read_bytes()) == rule.get("sha256"):', 'if True:', "AdditionalBoundaryTests.test_interpreter_directory_does_not_authenticate_added_source"),
    ("binary-license-file", GATE, "    errors += license_file_errors(files, stem, package_meta)", "    errors += []", "NativeFixture.test_declared_license_file_must_be_utf8"),
    ("nul-license-file", GATE, 'if "\\0" in text:', 'if False:', "NativeFixture.test_declared_license_file_cannot_contain_binary_nul"),
    ("record-hash-bypass", GATE, 'if encoded != expected or length != str(len(files[name])):', 'if False:', "NativeFixture.test_record_hash_is_checked"),
    ("engine-seal-bypass", GATE, 'if len(extensions) != 1 or seal.get("extension_sha256") != actual_extensions:', 'if False:', "NativeFixture.test_extension_seal_is_checked_even_after_record_rewrite"),
    ("source-digest-bypass", GATE, 'if seal.get("native_source_sha256") != source_digest.hexdigest():', 'if False:', "NativeFixture.test_source_digest_is_checked"),
    ("source-archive-seal-bypass", GATE, 'if seal.get("source_archive_sha256") != actual_sources or len(actual_sources) != 1:', 'if False:', "NativeFixture.test_source_archive_seal_is_checked"),
    ("metadata-dll-smuggling", GATE, 'accepted = tail in known', 'accepted = True', "AdditionalBoundaryTests.test_server_wheel_cannot_hide_binary_in_dist_info"),
    ("new-data-auto-approved", GATE, 'if kinds.get(name) in {"x", "b"} and not is_native_payload(name, data)', 'if False and not is_native_payload(name, data)', "AdditionalBoundaryTests.test_frozen_new_data_stays_unaccounted_even_with_record"),
    ("fetch-only-grants-retained-use", GATE, 'if applies and component not in {"playerone", "dss2"} and row.get("mode") == "fetch-only":', 'if False:', "AdditionalBoundaryTests.test_fetch_only_does_not_clear_retained_service_or_artwork"),
    ("pending-owner-cleared", GATE, 'if applies and row.get("requires_owner_decision", row.get("mode") == "pending"):', 'if False:', "ArchiveAndPolicyTests.test_pending_vendor_decision_preserves_binary_but_blocks_clearance"),
    ("dss2-policy-bypass", GATE, 'if component == "dss2" and not row.get("include_tiles", True)', 'if False and not row.get("include_tiles", True)', "AdditionalBoundaryTests.test_dss2_fetch_only_policy_rejects_tiles"),
    ("review-current-source-automatically", GATE, '    if original is None:\n        return False', '    if original is None:\n        return True', "ArchiveAndPolicyTests.test_fresh_source_is_not_automatically_a_reviewed_delta"),
    ("runtime-exception-applies-to-every-module", GATE, 'all(r.get("record_verified") and r.get("source", "").startswith(rule[1]) for r in retained)', 'all(r.get("record_verified") for r in retained)', "AdditionalBoundaryTests.test_runtime_exception_does_not_cover_arbitrary_pyinstaller_modules"),
    ("python-cargo-credit-collision", GATE, 'if group is None or container.get("id") == group or moved:', 'if True:', "AdditionalBoundaryTests.test_python_and_cargo_names_do_not_overwrite_each_other"),
    ("inject-auditor-future-flags", PROOF, 'optimize=n, dont_inherit=True', 'optimize=n, dont_inherit=False', "ArchiveAndPolicyTests.test_code_proof_compiles_but_never_executes"),
    ("ignore-python-exception-table", PROOF, 'getattr(code, "co_exceptiontable", b"").hex()', '""', "ArchiveAndPolicyTests.test_exception_handlers_are_part_of_python_proof"),
]

def run_test(name, cache):
    return subprocess.run([sys.executable, "-B", "-X", "pycache_prefix=" + str(cache), "-m", "unittest", name, "-q"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")

def main():
    original = {name: (ROOT / name).read_bytes() for name in {m[1] for m in MUTANTS}}
    results = []
    scratch = ROOT / ".probe/release"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gate-mutation-cache-", dir=scratch) as directory:
        cache=Path(directory)
        baseline=run_test("tools.licence.test_release_gate",cache)
        if baseline.returncode:
            raise RuntimeError("unmodified gate tests are not green")
        try:
            for name, filename, before, after, test in MUTANTS:
                path=ROOT/filename
                text=original[filename].decode("utf-8")
                if text.count(before)!=1:
                    raise RuntimeError("ambiguous mutation anchor: "+name)
                try:
                    path.write_bytes(text.replace(before,after,1).encode("utf-8"))
                    result=run_test(PREFIX+test,cache)
                    failed_test="FAIL: "+test.split(".")[-1]
                    killed=result.returncode!=0 and failed_test in result.stderr and "FAILED (failures=" in result.stderr
                    results.append({"name":name,"test":PREFIX+test,"killed_by_assertion":killed,"returncode":result.returncode})
                    if not killed:
                        raise RuntimeError("mutant survived or did not fail by its target assertion: "+name)
                finally:
                    path.write_bytes(original[filename])
        finally:
            for name, raw in original.items():
                (ROOT/name).write_bytes(raw)
        restored=all((ROOT/name).read_bytes()==raw for name,raw in original.items())
        green=run_test("tools.licence.test_release_gate",cache)
        if not restored or green.returncode:
            raise RuntimeError("exact restoration or restored green suite failed")
    report={"schema_version":1,"mutants":results,"baseline_green":True,"restored_green":True,"exact_bytes_restored":True,
            "source_sha256":{n:hashlib.sha256(b).hexdigest() for n,b in original.items()}}
    (ROOT/'tools/licence/release-gate-mutations.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(f"Release gate mutants: {len(results)} killed by named assertions; exact bytes restored; restored suite green")
    return 0

if __name__=='__main__':
    raise SystemExit(main())