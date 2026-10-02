# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Serial, assertion-specific source mutation evidence for release packaging.

Run only while no artifact build is reading these sources. Every mutated file
is restored from exact bytes in finally, then the complete new suite runs green.
All selected fixtures use synthetic payloads and mock process/network calls.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CASES = []

def case(name, file, before, after, test):
    CASES.append((name,file,before,after,test))

P='packaging/distribution_policy.py'
N='packaging/build_native.py'
B='packaging/build_binary.py'
S='packaging/smoke_ownership.py'
R='test_release_packaging.'
O='test_smoke_ownership.SmokeOwnership.'
case('pending-source-default',P,'return row["pending_by_artifact"][kind]','return True',R+'ReleasePolicy.test_pending_preserves_artifact_history')
case('fetch-only-decision',P,'return row["mode"] == "redistribute"','return True',R+'ReleasePolicy.test_fetch_only_synchronizes_every_artifact')
case('dss-payload-refusal',P,'if lowered[:3] == ("catalog", "_bundled_pack", "dss2color"):','if False:',R+'ReleasePolicy.test_dss2_is_refused_in_all_payloads')
case('pending-owner-visible',P,'"requires_owner_decision": value["mode"] == "pending"','"requires_owner_decision": False',R+'ReleasePolicy.test_pending_preserves_artifact_history')
case('static-package-table-drift',P,'if actual != {"astrodeck": package_data(policy)}:','if False:',R+'ReleasePolicy.test_static_table_drift_is_rejected')
case('tar-policy-consumer','scripts/build_release.py','if not include_file(base + "/" + n, "source-tar", policy)','if False',R+'ReleasePolicy.test_real_tar_follows_selected_policy')
case('installer-url-metadata-excluded','packaging/metadata_payloads.py','if relative.as_posix() == "direct_url.json":','if False:',R+'MetadataSelection.test_only_installer_local_url_is_omitted')
case('post-analysis-metadata-filter','packaging/astrodeck.spec','a.datas = filter_installer_urls(a.datas, native_metadata + server_metadata)','a.datas = a.datas',R+'ReleasePolicy.test_spec_removes_metadata_reintroduced_by_analysis')
case('post-analysis-url-excluded','packaging/metadata_payloads.py','if row[0].replace("\\\\", "/") not in excluded','if True',R+'MetadataSelection.test_post_analysis_filter_is_exact_and_never_reads_metadata')
case('post-analysis-url-scope','packaging/metadata_payloads.py','if row[0].replace("\\\\", "/") not in excluded','if not row[0].replace("\\\\", "/").endswith("/direct_url.json")',R+'MetadataSelection.test_post_analysis_filter_is_exact_and_never_reads_metadata')
case('spec-native-import','packaging/astrodeck.spec','    "astrodeck_native",\n','',R+'ReleasePolicy.test_spec_collects_native_module_and_metadata')
case('spec-native-metadata','packaging/astrodeck.spec','datas += distributable_metadata(native_metadata)','datas += []',R+'ReleasePolicy.test_spec_collects_native_module_and_metadata')
case('spec-policy-selection','packaging/astrodeck.spec','selected_files(SERVER / "astrodeck", "frozen", policy)','((p,p.relative_to(SERVER / "astrodeck").as_posix()) for p in (SERVER / "astrodeck").rglob("*") if p.is_file())',R+'ReleasePolicy.test_spec_filters_actual_sdk_and_tile_files')
case('server-clean-stage',B,'"build", "dist", "*.egg-info", ".venv", "__pycache__", "*.pyc", "tests"','"dist", "*.egg-info", ".venv", "__pycache__", "*.pyc", "tests"',R+'ReleasePolicy.test_server_install_never_reuses_stale_build')
case('server-same-version-reinstalled',B,'"install", "--force-reinstall", "--no-deps", str(wheels[0])','"install", "--no-deps", str(wheels[0])',R+'ReleasePolicy.test_validated_server_wheel_replaces_same_version_without_dependency_changes')
case('server-dependencies-preserved',B,'"install", "--force-reinstall", "--no-deps", str(wheels[0])','"install", "--force-reinstall", str(wheels[0])',R+'ReleasePolicy.test_validated_server_wheel_replaces_same_version_without_dependency_changes')
case('server-wheel-policy-check',B,'if not include_file(name[len("astrodeck/"):], "server-wheel", decisions):','if False:',R+'ReleasePolicy.test_server_install_refuses_forbidden_actual_wheel_member')
case('native-rebuild-source-data',N,'copy the original accompanying native-source.tar.gz into native/crates/astrodeck-native/sources/native-source.tar.gz','copy nothing',R+'NativeWheel.test_rebuild_recipe_prepares_required_source_data')
case('native-abi3-required',N,'if not re.search(r"^Tag: cp311-abi3-", wheel_meta, re.M):','if False:',R+'NativeWheel.test_non_abi3_wheel_refused')
case('native-notice-files-required',N,'if not required.issubset(names):','if False:',R+'NativeWheel.test_missing_native_notices_refused')
case('pep639-utf8-text',N,'entries[name].decode("utf-8", errors="strict")','entries[name].decode("utf-8", errors="replace")',R+'NativeWheel.test_non_utf8_license_file_is_rejected')
case('pep639-nul-text',N,'if b"\\0" in entries[name]:','if False:',R+'NativeWheel.test_nul_license_file_is_rejected')
case('native-source-archive-hash',N,'if set(sources) != {source_name} or sources != record.get("source_archive_sha256"):','if False:',R+'NativeWheel.test_source_archive_hash_is_verified')
case('native-source-tree-match',N,'if record.get("native_source_sha256") != native_source_digest(root):','if False:',R+'NativeWheel.test_changed_source_refused')
case('native-app-version-match',N,'if record.get("app_version") != app["version"]:','if False:',R+'NativeWheel.test_changed_release_version_refused')
case('native-notice-hash',N,'if not required.issubset({Path(n).name for n in notices}) or notices != record.get("notice_sha256"):','if False:',R+'NativeWheel.test_tampered_notice_refused')
case('native-license-declarations',N,'if not notices.issubset(listed):','if False:',R+'NativeWheel.test_license_declarations_must_cover_notices')
case('native-sbom-hash',N,'if sboms != record.get("sbom_sha256"):','if False:',R+'NativeWheel.test_tampered_sbom_refused')
case('native-record-coverage',N,'if {row[0] for row in rows} != set(entries) or len(rows) != len(entries):','if False:',R+'NativeWheel.test_record_must_cover_payload')
case('native-record-digest',N,'if digest != expected or size != str(len(body)):','if False:',R+'NativeWheel.test_record_digest_is_verified')
case('native-extension-hash',N,'if len(extensions) != 1 or extensions != record.get("extension_sha256"):','if False:',R+'NativeWheel.test_tampered_extension_refused')
case('sbom-private-reference',N,'return source_urn(value)','return value',R+'NativeWheel.test_sbom_paths_normalize_without_breaking_graph')
case('native-install-mandatory',B,'    install_native(args.native_wheel)\n','',R+'BinaryOrchestration.test_skip_server_install_still_installs_native_before_freeze')
case('native-probe-mandatory',B,'    native_smoke(exe)  # never optional: import/call proves #630, no server needed','    pass  # injected mutant',R+'BinaryOrchestration.test_skip_server_install_still_installs_native_before_freeze')
case('native-detector-result','packaging/native_probe.py','if len(stars) != 0 or stats.get("star_count") != 0:','if False:',R+'NativeProbe.test_incorrect_detector_result_refused')
case('native-probe-version','packaging/native_probe.py','if native.__version__ != distribution.version or distribution.version != manifest.get("native_version"):','if False:',R+'NativeProbe.test_module_metadata_mismatch_refused')
case('native-probe-private-errors','packaging/native_probe.py','"error_type": type(exc).__name__','"error_type": str(exc)',R+'NativeProbe.test_probe_errors_do_not_echo_exception_text')
case('native-probe-before-state','packaging/entry.py','        from native_probe import main as native_probe','        default_state_dir()\n        from native_probe import main as native_probe',R+'NativeProbe.test_entry_probe_never_initializes_application_state')
case('smoke-creation-time',S,'self.known.get(process.pid) == process.create_time()','True',O+'test_child_pid_reuse_is_refused')
case('smoke-command-identity',S,'command[1:] == ["run", "--host", "127.0.0.1", "--port", str(self.port)]','True',O+'test_wrong_command_is_not_owned')
case('smoke-parent-ancestry',S,'if any(parent.pid == self.root_pid and parent.create_time() == self.root_created\n                       for parent in process.parents()):','if True:',O+'test_unproven_ancestry_is_not_adopted')
case('smoke-loopback-only',S,'if len(listeners) != 1 or listeners[0][1].laddr.ip != "127.0.0.1":','if False:',O+'test_wildcard_listener_is_refused')
case('smoke-preexisting-launch',S,'if created < launched_at - 1 or not self.command_matches(process, root=True):','if not self.command_matches(process, root=True):',O+'test_preexisting_launch_pid_is_refused')
case('smoke-unknown-setup-cleanup',S,'            if self.known:\n','            if False:\n',O+'test_constructor_failure_cleans_already_proven_identity')
case('smoke-stop-retry',B,'            self._ownership.stop()\n            self._stopped = True','            self._stopped = True\n            self._ownership.stop()',O+'test_failed_stop_can_be_retried')
case('smoke-preserve-unverified-state',B,'preserve = not exc.cleanup_complete','preserve = False',O+'test_unverified_launch_preserves_private_test_state')
case('smoke-early-state-cleanup',B,'        if not preserve:\n','        if False:\n',O+'test_launch_failure_leaves_no_test_state')
case('smoke-no-raw-log-read',B,'        # Retained API, deliberately never reads or emits raw server logs.\n        return ""','        return self.log_path.read_text(encoding="utf-8")',O+'test_no_raw_log_read')


def run(test=None, cache=None):
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPYCACHEPREFIX=str(cache))
    command=[sys.executable,'-B','-m','unittest']
    command += [test] if test else ['discover','-s',str(ROOT/'packaging/tests'),'-p','test_*.py']
    return subprocess.run(command,cwd=ROOT/'packaging/tests',env=env,text=True,capture_output=True,timeout=180)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',required=True)
    parser.parse_args()
    scratch=ROOT/'.probe/release'
    scratch.mkdir(parents=True,exist_ok=True)
    files={name: (ROOT/name).read_bytes() for _,name,*_ in CASES}
    hashes={name:hashlib.sha256(body).hexdigest() for name,body in files.items()}
    evidence=[]
    with tempfile.TemporaryDirectory(prefix='mutation-',dir=scratch) as private:
        cache=Path(private)/'cache'
        for index,(name,body) in enumerate(files.items()):
            (Path(private)/f'backup-{index}').write_bytes(body)
        baseline=run(cache=cache/'baseline')
        if baseline.returncode:
            raise SystemExit('baseline packaging suite failed; no mutation performed')
        try:
            for label,file,before,after,test in CASES:
                path=ROOT/file
                original=files[file]
                ending='\r\n' if b'\r\n' in original else '\n'
                old=before.replace('\n',ending).encode()
                new=after.replace('\n',ending).encode()
                if original.count(old)!=1:
                    raise RuntimeError('mutation target is not unique: '+label)
                try:
                    path.write_bytes(original.replace(old,new,1))
                    result=run(test,cache/label)
                    output=result.stdout+result.stderr
                    expected=test.rsplit('.',1)[1]
                    killed=(result.returncode!=0 and 'FAIL: '+expected in output
                            and 'FAILED (failures=1)' in output
                            and 'AssertionError' in output and 'ERROR:' not in output)
                    evidence.append({'name':label,'file':file,'test':test,'intended_assertion_failed':killed})
                    print(label+': '+('KILLED' if killed else 'FAILED TO PROVE'),flush=True)
                    if not killed:
                        (Path(private)/'failure.txt').write_text(output,encoding='utf-8')
                        print(output)
                        raise RuntimeError('mutant did not fail its exact assertion: '+label)
                finally:
                    path.write_bytes(original)
                    if path.read_bytes()!=original:
                        raise RuntimeError('exact-byte restoration failed: '+file)
        finally:
            for file,body in files.items():
                (ROOT/file).write_bytes(body)
            restored={file:hashlib.sha256((ROOT/file).read_bytes()).hexdigest() for file in files}
            if restored!=hashes:
                raise RuntimeError('final restoration hash mismatch')
            final=run(cache=cache/'restored')
            if final.returncode:
                raise RuntimeError('restored full suite failed')
        report={'schema_version':1,'mutants':evidence,'baseline_green':True,'restored_suite_green':True,'exact_restored_sha256':hashes}
        (ROOT/'packaging/mutation-evidence.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        lines=['# Packaging mutation evidence','','Run with the test interpreter: `python -B packaging/mutation_check.py --run`. No artifact build may run concurrently.','',f'All {len(evidence)} named mutants failed the selected assertion, without test errors. The full new packaging suite passed before and after. Every source was restored byte for byte in `finally`; SHA-256 values are in mutation-evidence.json.','','All process and HTTP behavior is mocked. Wheel builds use synthetic projects without dependency installation. This is regression evidence, not cross-platform runtime certification.','','| Mutant | Intended fixture |','| --- | --- |']
        lines += ['| '+row['name']+' | `'+row['test']+'` |' for row in evidence]
        (ROOT/'packaging/mutation-evidence.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Exact source restoration and restored green suite confirmed.')

if __name__=='__main__':
    main()
