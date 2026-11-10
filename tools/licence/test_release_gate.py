# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Release gates: real archive/RECORD mutations; no application execution."""
from __future__ import annotations
import base64
import contextlib
import copy
import csv
import hashlib
import io
import json
import marshal
from pathlib import Path
import subprocess
import sys
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_release as gate
import release_evidence as evidence
import release_provenance as proof

ROOT = Path(__file__).resolve().parents[2]
#: A stand-in commit for fixtures that need one; the real ledger base is not used here.
FIXTURE_COMMIT = 'b3ce0f1b8f3e2ce68fd7baa740d61c3848165837'

def git_in(root, *args, autocrlf='false'):
    command = ['git', '-c', 'user.name=release-test', '-c', 'user.email=test@example.invalid', '-c', 'core.autocrlf=' + autocrlf, *args]
    return subprocess.run(command, cwd=root, check=True, capture_output=True)

def commit_all(root, message='fixture'):
    git_in(root, 'add', '-A')
    git_in(root, 'commit', '-q', '-m', message)
    return git_in(root, 'rev-parse', 'HEAD').stdout.decode().strip()

def write_files(root, files):
    for name, raw in files.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

def ledger_row(path, data, review='Read the complete change against the reviewed base; nothing else moved.', **more):
    return {'path': path, 'sha256': evidence.digest(data), 'review': review, **more}

def write_ledger_file(root, base, rows):
    path = Path(root) / evidence.LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = {'schema_version': 2, 'base': base, 'digest': evidence.DIGEST_NAME, 'files': rows}
    path.write_text(json.dumps(ledger, indent=2) + '\n', encoding='utf-8')

def load_version_bump():
    import importlib.util
    spec = importlib.util.spec_from_file_location('version_bump_evidence', Path(__file__).with_name('version_bump_evidence.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def record(files):
    name = next(p for p in files if p.endswith('.dist-info/METADATA')).rsplit('/', 1)[0] + '/RECORD'
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator='\n')
    for path, body in sorted(files.items()):
        if path == name:
            continue
        writer.writerow([path, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b'=').decode(), len(body)])
    writer.writerow([name, '', ''])
    files[name] = stream.getvalue().encode()

def zipped(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as output:
        for name, raw in files.items():
            output.writestr(name, raw)
    return stream.getvalue()

class NativeFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        inputs = {'native/Cargo.lock': 'version = 3\n[[package]]\nname="fixture-dependency"\nversion="1.2.3"\nsource="registry+https://github.com/rust-lang/crates.io-index"\n', 'native/src/lib.rs': 'pub fn engine() {}\n',
                  'native/Cargo.toml': '[package]\nname="fixture"\nversion="0.1.0"\n',
                  'native/crates/astrodeck-native/pyproject.toml': '[project]\nversion="0.1.0"\nlicense="MPL-2.0"\n',
                  'server/pyproject.toml': '[project]\nversion="0.3.39"\n'}
        for name, raw in inputs.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw, encoding='utf-8')
        mpl = (ROOT / 'tools/licence_texts/mpl-2.0.txt').read_bytes()
        path = self.root / 'tools/licence_texts/mpl-2.0.txt'
        path.parent.mkdir(parents=True)
        path.write_bytes(mpl)
        source = io.BytesIO()
        self.source_digest = hashlib.sha256()
        with tarfile.open(fileobj=source, mode='w:gz') as archive:
            for name, raw in sorted(inputs.items()):
                if not name.startswith('native/'):
                    continue
                data = raw.encode()
                self.source_digest.update(name.encode() + b'\0' + hashlib.sha256(data).digest())
                item = tarfile.TarInfo(name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        self.stem = 'astrodeck_native-0.1.0.dist-info'
        self.engine = 'astrodeck_native/astrodeck_native.pyd'
        copyright_text = 'Copyright release fixture authors. ' + ('Permission and conditions remain supplied in this complete test notice. ' * 8)
        license_hash = proof.sha(copyright_text.encode())[:16]
        self.credits = {'licenses': {license_hash: copyright_text}, 'groups': [{'id': 'cargo', 'entries': [
            {'name': 'fixture-dependency', 'version': '1.2.3', 'spdx': 'MIT', 'texts': [{'hash': license_hash}], 'requires': ['notice']}]}]}
        notices = {'MPL-2.0.txt': mpl,
                   'THIRD-PARTY-NOTICES.md': b'Root notices for fixture.\n' * 20,
                   'CARGO-NOTICES.txt': ('fixture-dependency 1.2.3\n' + copyright_text).encode(),
                   'NATIVE-SOURCE.txt': ('Exact fixture source commit: ' + FIXTURE_COMMIT + '\n' + 'Source included. ' * 10).encode(),
                   'native-source.tar.gz': source.getvalue()}
        engine=bytearray(b'MZ'+b'\0'*126)
        struct.pack_into('<I',engine,60,64)
        engine[64:68]=b'PE\0\0'
        struct.pack_into('<H',engine,68,0x8664)
        (self.root/'THIRD-PARTY-NOTICES.md').write_bytes(notices['THIRD-PARTY-NOTICES.md'])
        self.files = {self.engine: bytes(engine),
                      self.stem + '/WHEEL': b'Wheel-Version: 1.0\nTag: cp311-abi3-win_amd64\n'}
        # Nested license path mirrors maturin's license-files handling.
        metadata = 'Name: astrodeck-native\nVersion: 0.1.0\nLicense-Expression: MPL-2.0\n'
        for name, body in notices.items():
            if name == 'native-source.tar.gz':
                self.files[self.stem + '/source/' + name] = body
            else:
                self.files[self.stem + '/licenses/licenses/' + name] = body
                metadata += 'License-File: licenses/' + name + '\n'
        self.files[self.stem + '/METADATA'] = metadata.encode()
        sbom = {'bomFormat': 'CycloneDX', 'specVersion': '1.6',
                'metadata': {'component': {'bom-ref': 'root', 'name': 'astrodeck-native', 'version': '0.1.0'}},
                'components': [{'bom-ref': 'fixture', 'name': 'fixture-dependency', 'version': '1.2.3', 'licenses': [{'expression': 'MIT'}]}],
                'dependencies': [{'ref': 'root', 'dependsOn': ['fixture']}, {'ref': 'fixture', 'dependsOn': []}]}
        self.files[self.stem + '/sboms/astrodeck-native.cyclonedx.json'] = json.dumps(sbom).encode()
        self.reseal()
        self.source_patch = patch.object(gate, 'approved_source', return_value=True)
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)
        self.base_patch = patch.object(gate, 'warm_base', return_value=None)
        self.base_patch.start()
        self.addCleanup(self.base_patch.stop)
        self.git_patch = patch.object(gate.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=FIXTURE_COMMIT + '\n'))
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)
    def reseal(self):
        seal = {'schema_version': 1, 'source_commit': FIXTURE_COMMIT, 'native_source_sha256': self.source_digest.hexdigest(),
                'native_version': '0.1.0', 'app_version': '0.3.39',
                'extension_sha256': {self.engine: proof.sha(self.files[self.engine])},
                'source_archive_sha256': {p: proof.sha(b) for p,b in self.files.items() if p.startswith(self.stem + '/source/')},
                'sbom_sha256': {p: proof.sha(b) for p,b in self.files.items() if p.startswith(self.stem + '/sboms/')},
                'notice_sha256': {p: proof.sha(b) for p, b in self.files.items() if p.startswith(self.stem + '/licenses/')}}
        self.files[self.stem + '/astrodeck-build.json'] = json.dumps(seal).encode()
        record(self.files)
    def errors(self, platform='windows-x86_64'):
        return gate.native_review(self.files, platform, self.credits, self.root)[0]
    def codes(self, platform='windows-x86_64'):
        return {x['code'] for x in self.errors(platform)}
    def test_complete_nested_native_fixture_has_no_findings(self):
        self.assertEqual(self.errors(), [])
    def test_frozen_native_installer_markers_remain_allowed(self):
        self.files[self.stem+'/INSTALLER']=b'pip\n'
        self.files[self.stem+'/REQUESTED']=b''
        record(self.files)
        errors,_=gate.native_review(self.files,'windows-x86_64',self.credits,self.root,wheel=False)
        self.assertEqual(errors,[])
    def test_frozen_native_direct_url_is_rejected_even_with_wheel_hash(self):
        path=self.stem+'/direct_url.json'
        self.files[path]=json.dumps({'archive_info':{'hashes':{'sha256':'0'*64}}}).encode()
        record(self.files)
        errors,_=gate.native_review(self.files,'windows-x86_64',self.credits,self.root,wheel=False)
        self.assertTrue(any(e['code']=='UNACCOUNTED' and e['path']==path for e in errors))
    def test_new_binary_cannot_hide_in_valid_resealed_wheel(self):
        self.files['vendor/surprise.dll'] = b'MZunreviewed'
        self.reseal()
        self.assertIn('UNACCOUNTED', self.codes())
    def test_record_hash_is_checked(self):
        self.files[self.engine] += b'altered'
        self.assertIn('WHEEL_RECORD', self.codes())
    def test_extension_seal_is_checked_even_after_record_rewrite(self):
        self.files[self.engine] += b'altered'
        record(self.files)
        self.assertIn('NATIVE_SEAL', self.codes())
    def test_declared_license_file_must_be_utf8(self):
        self.files[self.stem + '/licenses/opaque.txt'] = b'\xff\xfeinvalid text'
        self.files[self.stem + '/METADATA'] += b'License-File: opaque.txt\n'
        self.reseal()
        self.assertIn('LICENSE_FILE', self.codes())
    def test_declared_license_file_cannot_contain_binary_nul(self):
        self.files[self.stem + '/licenses/opaque.txt'] = b'binary\0content'
        self.files[self.stem + '/METADATA'] += b'License-File: opaque.txt\n'
        self.reseal()
        self.assertIn('LICENSE_FILE', self.codes())
    def test_source_archive_must_not_be_a_license_file(self):
        path=self.stem+'/source/native-source.tar.gz'
        self.files[self.stem+'/licenses/native-source.tar.gz']=self.files.pop(path)
        self.files[self.stem+'/METADATA']+=b'License-File: native-source.tar.gz\n'
        self.reseal()
        self.assertIn('LICENSE_FILE', self.codes())
    def test_source_archive_seal_is_checked(self):
        self.files[self.stem+'/source/native-source.tar.gz'] += b'changed'
        record(self.files)
        self.assertIn('NATIVE_SEAL', self.codes())
    def test_current_version_is_checked(self):
        name = self.stem + '/METADATA'
        self.files[name] = self.files[name].replace(b'0.1.0', b'0.2.0')
        self.reseal()
        self.assertIn('NATIVE_VERSION', self.codes())
    def test_pe_machine_cannot_be_arm_with_x64_tag(self):
        data=bytearray(self.files[self.engine]);struct.pack_into('<H',data,68,0xAA64)
        self.files[self.engine]=bytes(data);self.reseal()
        self.assertIn('PLATFORM',self.codes())
    def test_root_notices_cannot_be_replaced_and_resealed(self):
        self.files[self.stem+'/licenses/licenses/THIRD-PARTY-NOTICES.md']=b'arbitrary new text '*40
        self.reseal()
        self.assertIn('NOTICE_MISSING',self.codes())
    def test_native_license_expression_must_match_reviewed_project(self):
        name=self.stem+'/METADATA'
        self.files[name]=self.files[name].replace(b'License-Expression: MPL-2.0',b'License-Expression: MIT')
        self.reseal()
        self.assertIn('NATIVE_LICENSE',self.codes())
    def test_current_platform_is_checked(self):
        self.assertIn('PLATFORM', self.codes('macos-arm64'))
    def test_missing_record_member_is_not_accepted(self):
        self.files['extra.txt'] = b'not recorded'
        self.assertIn('WHEEL_RECORD', self.codes())
    def test_shortened_mpl_is_not_accepted_even_when_resealed(self):
        self.files[self.stem + '/licenses/licenses/MPL-2.0.txt'] = b'Mozilla Public License Version 2.0'
        self.reseal()
        self.assertIn('NOTICE_MISSING', self.codes())
    def test_missing_cargo_full_text_is_not_accepted(self):
        self.files[self.stem + '/licenses/licenses/CARGO-NOTICES.txt'] = b'fixture-dependency 1.2.3\n' + b'metadata only\n' * 30
        self.reseal()
        self.assertIn('NOTICE_MISSING', self.codes())
    def test_native_source_must_include_complete_workspace(self):
        source = io.BytesIO()
        with tarfile.open(fileobj=source, mode='w:gz') as archive:
            for name in ('native/Cargo.lock', 'native/src/lib.rs'):
                body = (self.root / name).read_bytes()
                item = tarfile.TarInfo(name)
                item.size = len(body)
                archive.addfile(item, io.BytesIO(body))
        self.files[self.stem + '/source/native-source.tar.gz'] = source.getvalue()
        self.reseal()
        self.assertIn('SOURCE_MISSING', self.codes())
    def test_source_digest_is_checked(self):
        path = self.stem + '/astrodeck-build.json'
        value = json.loads(self.files[path])
        value['native_source_sha256'] = '0' * 64
        self.files[path] = json.dumps(value).encode()
        record(self.files)
        self.assertIn('NATIVE_SEAL', self.codes())
    def test_metadata_must_declare_notice_paths(self):
        self.files[self.stem + '/METADATA'] = b'Name: astrodeck-native\nVersion: 0.1.0\n'
        self.reseal()
        self.assertIn('NOTICE_MISSING', self.codes())

    def test_sbom_cannot_introduce_unknown_crate(self):
        path = self.stem + '/sboms/astrodeck-native.cyclonedx.json'
        sbom = json.loads(self.files[path])
        sbom['components'][0]['name'] = 'unknown-component'
        self.files[path] = json.dumps(sbom).encode()
        self.reseal()
        self.assertIn('NATIVE_SBOM', self.codes())
    def test_sbom_hash_is_bound(self):
        path = self.stem + '/sboms/astrodeck-native.cyclonedx.json'
        self.files[path] += b' '
        record(self.files)
        self.assertIn('NATIVE_SEAL', self.codes())
    def test_absolute_build_path_is_not_exported(self):
        path = self.stem + '/sboms/astrodeck-native.cyclonedx.json'
        sbom = json.loads(self.files[path])
        sbom['serialNumber'] = 'file:///C:/Users/private-build/temporary'
        self.files[path] = json.dumps(sbom).encode()
        self.reseal()
        self.assertIn('BUILD_PATH', self.codes())

class ArchiveAndPolicyTests(unittest.TestCase):
    def test_zip_rejects_traversal(self):
        with self.assertRaises(ValueError):
            proof.read_zip(zipped({'../outside': b'payload'}))
    def test_zip_rejects_duplicate_members(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('same', b'a')
            archive.writestr('same', b'b')
        with self.assertRaises(ValueError):
            proof.read_zip(output.getvalue())
    def test_zip_rejects_symlink(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            info = zipfile.ZipInfo('link')
            info.external_attr = 0o120777 << 16
            archive.writestr(info, 'target')
        with self.assertRaises(ValueError):
            proof.read_zip(output.getvalue())
    def test_pending_vendor_decision_preserves_binary_but_blocks_clearance(self):
        policy = [{'component': 'playerone', 'mode': 'pending', 'include_binaries': True,
                   'requires_owner_decision': True, 'evidence': 'issue:632'}]
        errors = gate.owner_findings({'astrodeck/vendor/playerone/PlayerOne.dll': b'MZ'}, 'frozen', policy)
        self.assertEqual([e['code'] for e in errors], ['OWNER_PENDING'])
    def test_fetch_only_decision_rejects_vendor_binary(self):
        policy = [{'component': 'playerone', 'mode': 'fetch-only', 'include_binaries': False,
                   'requires_owner_decision': False, 'evidence': 'owner-fixture'}]
        errors = gate.owner_findings({'astrodeck/vendor/playerone/PlayerOne.dll': b'MZ'}, 'frozen', policy)
        self.assertEqual([e['code'] for e in errors], ['POLICY_EXCLUDED'])
    def test_absent_vendor_binary_does_not_invent_inclusion(self):
        policy = [{'component': 'playerone', 'mode': 'pending', 'requires_owner_decision': True}]
        self.assertEqual(gate.owner_findings({'astrodeck/vendor/playerone/LICENSE': b'text'}, 'source-tar', policy), [])
    def test_native_wheel_does_not_borrow_service_issues(self):
        policy = [{'component': 'astrospheric', 'mode': 'pending', 'requires_owner_decision': True}]
        self.assertEqual(gate.owner_findings({}, 'native-wheel', policy), [])
    def test_not_shipped_wcslib_is_silent_while_openblas_is_retained(self):
        policy = [{'component': 'wcslib', 'mode': 'not-shipped', 'requires_owner_decision': False, 'evidence': 'owner-fixture'}]
        files = {'numpy.libs/libscipy_openblas64_-x.dll': b'MZ'}
        self.assertEqual(gate.owner_findings(files, 'frozen', policy), [])

    def test_not_shipped_wcslib_fails_when_its_extension_reappears(self):
        policy = [{'component': 'wcslib', 'mode': 'not-shipped', 'requires_owner_decision': False, 'evidence': 'owner-fixture'}]
        errors = gate.owner_findings({'astropy/wcs/_wcs.pyd': b'MZ'}, 'frozen', policy)
        self.assertEqual([(e['code'], e['component']) for e in errors], [('POLICY_EXCLUDED', 'wcslib')])
        self.assertEqual(gate.owner_findings({'astropy/wcs/_wcs.pyd': b'MZ'}, 'server-wheel', policy), [])

    def test_current_generated_credits_must_match_ui_input(self):
        errors, good = gate.checked_ui({}, {'credits_sha256': 'old'}, b'new')
        self.assertFalse(good)
        self.assertIn('UI_PROVENANCE', {e['code'] for e in errors})
    def test_code_proof_compiles_but_never_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'module.py'
            source = 'TABLE = ("reviewed", 3)\nraise SystemExit("must not execute")\n'
            path.write_text(source, encoding='utf-8')
            payload = marshal.dumps(compile(source, 'renamed.py', 'exec', dont_inherit=True))
            self.assertTrue(proof.code_matches(payload, path))
            path.write_text(source.replace('reviewed', 'new copied table'), encoding='utf-8')
            self.assertFalse(proof.code_matches(payload, path))
    def test_exception_handlers_are_part_of_python_proof(self):
        a = compile('try:\n  x=1\nexcept ValueError:\n  x=2\n', '', 'exec')
        b = a.replace(co_exceptiontable=b'')
        self.assertNotEqual(proof.code_shape(a), proof.code_shape(b))
    def test_fresh_source_is_not_automatically_a_reviewed_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_ledger_file(root, FIXTURE_COMMIT, [])
            result = subprocess.CompletedProcess([], 1, stdout=b'', stderr=b'absent')
            with patch.object(gate.subprocess, 'run', return_value=result):
                self.assertFalse(gate.approved_source('server/astrodeck/new_table.py', b'DATA="new"', root))
    def test_cli_parse_failure_still_writes_failed_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / 'bad.whl'
            artifact.write_bytes(b'not a wheel')
            report = root / 'report.json'
            result = subprocess.run([sys.executable, str(ROOT / 'tools/licence/audit_release.py'),
                '--kind', 'native-wheel', '--artifact', str(artifact), '--platform', 'windows-x86_64', '--report', str(report)], capture_output=True)
            self.assertEqual(result.returncode, 1)
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertFalse(data['cleared'])
            self.assertFalse(data['accounted'])
            self.assertEqual(data['findings'][0]['code'], 'ARCHIVE')

class ReleaseCreditsTests(unittest.TestCase):
    def test_absent_optional_is_recorded_without_invented_distribution(self):
        import build_credits
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'server').mkdir()
            (root / 'server/pyproject.toml').write_text('[project]\ndependencies=["core"]\n[project.optional-dependencies]\nscience=["h5py"]\n', encoding='utf-8')
            fake = SimpleNamespace(REPO=root, Fatal=RuntimeError, _seeds=lambda x:x)
            with patch.object(build_credits.metadata, 'distributions', return_value=[]):
                fake._walk = unittest.mock.Mock(side_effect=[({'core': 'installed'}, []), ({}, ['h5py'])])
                found, omitted, declared = build_credits.environment_closure(fake)
            self.assertEqual(omitted, ['h5py'])
            self.assertNotIn('h5py', found)
            self.assertEqual(declared, {'core'})
    def test_missing_core_remains_fatal(self):
        import build_credits
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'server').mkdir()
            (root / 'server/pyproject.toml').write_text('[project]\ndependencies=["core"]\n', encoding='utf-8')
            fake = SimpleNamespace(REPO=root, Fatal=RuntimeError, _seeds=lambda x:x, _walk=lambda x:({}, ['core']))
            with self.assertRaisesRegex(RuntimeError, 'Missing required runtime'):
                build_credits.environment_closure(fake)
    def test_generator_functions_restored_after_failure(self):
        import build_credits
        gen = build_credits.generator
        before = (gen.collect_python, gen.collect_cargo)
        with patch.object(gen, 'build', side_effect=gen.Fatal('fixture')):
            with self.assertRaises(gen.Fatal):
                build_credits.build()
        self.assertEqual((gen.collect_python, gen.collect_cargo), before)


class LineEndingProofTests(unittest.TestCase):
    """Registry hashes of text payloads do not depend on the checkout's newline convention (#1073).

    Named mutants: EOL-NOTICE-EXACT (the notice comparison hashes raw bytes), EOL-ASSET-EXACT (the asset comparison
    hashes raw bytes), EOL-NORMALIZES-BINARY (binary payloads also lose CRLF), EOL-IGNORES-CONTENT (normalization
    replaces the whole payload)."""
    NOTICE = "server/astrodeck/vendor/zwo/LICENSE.txt"
    ASSET = "server/astrodeck/catalog/data/table.tsv"
    TEXT = b"line one\nline two\nline three\n"

    def review(self, notice_bytes, asset_bytes):
        artifact = gate.audit_artifact
        credits_bytes = b'{"groups":[],"licenses":{}}'
        rules = {"assets": [{"path": self.ASSET, "sha256": hashlib.sha256(self.TEXT).hexdigest(), "component": "table",
                             "spdx": "MIT", "notices": {self.NOTICE: hashlib.sha256(self.TEXT).hexdigest()}}],
                 "reviewed_sources": []}
        ui = {"credits_sha256": hashlib.sha256(credits_bytes).hexdigest(), "files": []}
        errors, _ = artifact.review({self.NOTICE: notice_bytes, self.ASSET: asset_bytes}, rules, json.loads(credits_bytes), ui, credits_bytes, {})
        return errors

    def test_crlf_and_lf_copies_of_a_notice_and_an_asset_both_pass(self):
        for body in (self.TEXT, self.TEXT.replace(b"\n", b"\r\n")):
            errors = self.review(body, body)
            self.assertFalse([e for e in errors if "companion notice" in e or "asset bytes differ" in e], errors)

    def test_a_changed_byte_in_a_notice_or_an_asset_still_fails(self):
        changed = self.TEXT.replace(b"two", b"twx")
        for body in (changed, changed.replace(b"\n", b"\r\n")):
            errors = self.review(body, self.TEXT)
            self.assertTrue([e for e in errors if "companion notice" in e], errors)
            errors = self.review(self.TEXT, body)
            self.assertTrue([e for e in errors if "asset bytes differ" in e], errors)

    def test_binary_payloads_stay_exact(self):
        artifact = gate.audit_artifact
        data = b"\x00\r\n\x01"
        self.assertEqual(hashlib.sha256(data).hexdigest(), artifact.content_sha256("fonts/a.woff2", data))
        self.assertEqual(hashlib.sha256(data).hexdigest(), artifact.content_sha256("lib/ASICamera2.dll", data))
        self.assertNotEqual(hashlib.sha256(b"x\r\ny").hexdigest(), artifact.content_sha256("a.txt", b"x\r\ny"))
        for name in ("a.txt", "b.md", "c.tsv", "d.csv", "e.json", "f.rst", "LICENSE", "NOTICE.md", "COPYING", "x/LICENSE-APACHE"):
            self.assertTrue(artifact.is_text_payload(name), name)
        for name in ("a.png", "a.dll", "a.woff", "licensed.bin", "a.py"):
            self.assertFalse(artifact.is_text_payload(name), name)

class OwnerArtworkRulingTests(unittest.TestCase):
    """The nebula background is owner-made (#637, owner ruling of 2026-10-20).

    Named mutants: NEBULA-BLOCKED (`blocked: true` put back on the row), NEBULA-SPDX-UNKNOWN (spdx set to UNKNOWN),
    NEBULA-HASH-CHANGED (the reviewed sha256 replaced)."""
    def test_registry_row_is_the_owners_apache_2_0_artwork_with_its_original_hash(self):
        rules=json.loads((Path(__file__).resolve().parents[2]/"tools/licence/artifact-registry.json").read_text(encoding="utf-8"))
        row=next(r for r in rules["assets"] if r["path"]=="ui/dist/bg_nebula.png")
        self.assertNotIn("blocked",row)
        self.assertEqual("Apache-2.0",row["spdx"])
        self.assertEqual("https://github.com/epim/astrodeck/issues/637#issuecomment-6299071149",row["source"])
        self.assertEqual("c44f5e8e23760a61f1685e2597124e875f3637966384c9318196606985c73b5b",row["sha256"])

class AdditionalBoundaryTests(unittest.TestCase):
    def test_code_name_and_qualname_are_runtime_visible_proof(self):
        original=compile('def actual(): return 1','source.py','exec',dont_inherit=True)
        changed=original.replace(co_name='unreviewed',co_qualname='unreviewed')
        self.assertNotEqual(proof.code_shape(original),proof.code_shape(changed))
    def pyz(self, duplicate=False, gap=False, name='fixture'):
        raw=marshal.dumps(compile('value=1','fixture.py','exec',dont_inherit=True))
        body=zlib.compress(raw)
        prefix=b'X' if gap else b''
        start=17+len(prefix)
        entries=[(name,(0,start,len(body)))]
        payload=prefix+body
        if duplicate:
            entries.append((name,(0,17+len(payload),len(body))))
            payload+=body
        return b'PYZ\0'+b'\0'*4+struct.pack('!i',17+len(payload))+b'\0'*5+payload+marshal.dumps(entries)
    def test_pyz_duplicate_member_is_rejected_before_dict_conversion(self):
        self.assertEqual(proof.validate_pyz(self.pyz()),{'fixture'})
        with self.assertRaisesRegex(ValueError,'duplicate'):
            proof.validate_pyz(self.pyz(duplicate=True))
    def test_pyz_name_errors_carry_the_member_and_the_rule(self):
        # Named mutants: PYZ-ERROR-ANONYMOUS (the message returned to the old text with no name),
        # PYZ-RULE-UNSAID (the three messages made identical).
        cases=[('pkg-name','non-identifier','pkg-name'),
               ('a..b','non-identifier',"a..b"),
               ('1abc','non-identifier','1abc'),
               (b'bytes','not a str',"b'bytes'")]
        for name,rule,shown in cases:
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as caught:
                    proof.validate_pyz(self.pyz(name=name))
                self.assertIn(rule,str(caught.exception))
                self.assertIn(shown,str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            proof.validate_pyz(self.pyz(duplicate=True))
        self.assertIn("duplicate",str(caught.exception));self.assertIn("'fixture'",str(caught.exception))
    def test_pyz_name_in_the_error_is_truncated(self):
        name='x-'+'y'*500
        with self.assertRaises(ValueError) as caught:
            proof.validate_pyz(self.pyz(name=name))
        message=str(caught.exception)
        self.assertIn('x-yyy',message)
        self.assertLess(len(message),400,'the member name is cut to 120 characters')
    def test_pyz_allows_exactly_the_multiarch_sysconfig_data_module(self):
        # PyInstaller's hook-sysconfig collects sysconfig._get_sysconfigdata_name(), which holds hyphens on Linux.
        # Named mutants: SYSCONFIG-ANY-HYPHEN (the pattern widened to any name containing a hyphen),
        # SYSCONFIG-REMOVED (the allowance deleted: the Linux crash returns).
        for good in ('_sysconfigdata__linux_x86_64-linux-gnu','_sysconfigdata__linux_aarch64-linux-gnu','_sysconfigdata__darwin_darwin'):
            with self.subTest(good=good):
                self.assertEqual(proof.validate_pyz(self.pyz(name=good)),{good})
        for bad in ('_sysconfigdata__linux_x86_64-linux-gnu.evil','evil._sysconfigdata__linux_x86_64-linux-gnu',
                    '_sysconfigdata__linux_x86_64-linux-gnu ','_sysconfigdata__linux_x86_64-linux-gnu\n',
                    'other__linux_x86_64-linux-gnu','_sysconfigdata_-','_sysconfigdata__Linux_x86-64','os-path'):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError,'non-identifier'):
                    proof.validate_pyz(self.pyz(name=bad))
    def test_pyz_unexplained_payload_bytes_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'unexplained'):
            proof.validate_pyz(self.pyz(gap=True))
    def test_pyz_trailing_table_bytes_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'trailing'):
            proof.validate_pyz(self.pyz()+b'hidden')
    def test_interpreter_directory_does_not_authenticate_added_source(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            source=base/'Lib/site-packages/unaccounted.py'
            source.parent.mkdir(parents=True);source.write_bytes(b'copied data')
            index=proof.Sources.__new__(proof.Sources)
            index.root=base/'repository';index.base=base;index.paths={};index.runtime_inputs={}
            self.assertFalse(index.identify(source)['record_verified'])
            added=base/'Lib/unaccounted.py';added.write_bytes(b'copied data')
            self.assertFalse(index.identify(added)['record_verified'])
    @unittest.skipUnless(sys.platform == 'win32', 'Windows 8.3 short names only')
    def test_a_short_name_base_still_contains_its_resolved_files(self):
        """The hosted Windows runner's temp dir sits under RUNNER~1; resolve()
        expands that, so identify() must compare resolved roots or a reviewed
        runtime input reads as unreviewed (release run 37669607167).
        MUTANT 'identify compares the unresolved base': RED with
        'AssertionError: False is not true' when the short name differs."""
        import ctypes
        with tempfile.TemporaryDirectory() as directory:
            long_dir=Path(directory)/'a long directory name'
            (long_dir/'Lib').mkdir(parents=True)
            source=long_dir/'Lib/known.py';source.write_bytes(b'reviewed runtime fixture')
            buf=ctypes.create_unicode_buffer(1024)
            if not ctypes.windll.kernel32.GetShortPathNameW(str(long_dir),buf,1024) or Path(buf.value).name==long_dir.name:
                self.skipTest('8.3 short names are disabled on this volume')
            index=proof.Sources.__new__(proof.Sources)
            index.root=Path(directory)/'repository';index.base=Path(buf.value);index.paths={}
            index.runtime_inputs={'Lib/known.py':{'sha256':proof.sha(source.read_bytes())}}
            self.assertTrue(index.identify(Path(buf.value)/'Lib/known.py')['record_verified'])
    def test_reviewed_runtime_input_requires_exact_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);source=base/'Lib/known.py'
            source.parent.mkdir();source.write_bytes(b'reviewed runtime fixture')
            index=proof.Sources.__new__(proof.Sources)
            index.root=base/'repository';index.base=base;index.paths={}
            index.runtime_inputs={'Lib/known.py':{'sha256':proof.sha(source.read_bytes())}}
            self.assertTrue(index.identify(source)['record_verified'])
            source.write_bytes(b'changed runtime fixture')
            self.assertFalse(index.identify(source)['record_verified'])
    def test_elf_machine_and_class_must_match_platform(self):
        data=bytearray(b'\x7fELF\x02\x01\x01'+b'\0'*57)
        struct.pack_into('<H',data,18,183)
        self.assertTrue(gate.native_machine_matches(bytes(data),'linux-arm64'))
        self.assertFalse(gate.native_machine_matches(bytes(data),'linux-x86_64'))
        data[4]=1
        self.assertFalse(gate.native_machine_matches(bytes(data),'linux-arm64'))
    def test_macho_machine_must_be_arm64(self):
        data=b'\xcf\xfa\xed\xfe'+struct.pack('<I',0x0100000c)+b'\0'*24
        self.assertTrue(gate.native_machine_matches(data,'macos-arm64'))
        self.assertFalse(gate.native_machine_matches(data[:4]+struct.pack('<I',0x01000007)+data[8:],'macos-arm64'))
    def test_runtime_exception_does_not_cover_arbitrary_pyinstaller_modules(self):
        package={'notices':[{'text_sha256':'11ec72d544b0ecf0831c8615adcb74ac17fbd3688513f55af8367d3d98b0022c'}]}
        loader={'source':'dist:pyinstaller/PyInstaller/loader/pyimod01_archive.py','record_verified':True}
        self.assertTrue(gate.runtime_tool_resolution('pyinstaller',[loader],package))
        ordinary={'source':'dist:pyinstaller/PyInstaller/building/api.py','record_verified':True}
        self.assertFalse(gate.runtime_tool_resolution('pyinstaller',[loader,ordinary],package))
        self.assertFalse(gate.runtime_tool_resolution('pyinstaller',[dict(loader,record_verified=False)],package))
        self.assertFalse(gate.runtime_tool_resolution('pyinstaller',[loader],{'notices':[]}))
    def test_python_and_cargo_names_do_not_overwrite_each_other(self):
        credits={'groups':[{'id':'python','entries':[{'name':'numpy','version':'2.5.3'}]},{'id':'cargo','entries':[{'name':'numpy','version':'0.26.0'}]},{'id':'flagged','entries':[{'name':'flagged-python','version':'1','tier':'installed-build-environment'}]}]}
        self.assertEqual(gate.credits_entries(credits,'python')['numpy']['version'],'2.5.3')
        self.assertEqual(gate.credits_entries(credits,'cargo')['numpy']['version'],'0.26.0')
        self.assertIn('flagged-python',gate.credits_entries(credits,'python'))
        self.assertNotIn('flagged-python',gate.credits_entries(credits,'cargo'))
    def test_compound_notice_proof_retains_complete_upstream_text(self):
        row=proof.notice_record(b'Copyright source authors.\nComplete terms.')
        self.assertTrue(gate.notice_present(row,set(),set(),['Wrapper header\n'+row['text']+'\nother notice']))
        self.assertFalse(gate.notice_present(row,set(),set(),['Wrapper header\nComplete terms.']))
    def test_python_module_name_ending_lib_is_not_a_native_library(self):
        self.assertFalse(gate.is_native_payload('PYZ.pyz!/numpy.lib',b'code'))
        self.assertFalse(gate.is_native_payload('PYZ.pyz!/ctypes.macholib.dylib',b'code'))
    def test_fetch_only_does_not_clear_retained_service_or_artwork(self):
        decisions=[{'component':'astrospheric','mode':'fetch-only','evidence':'owner-fixture'},
                   {'component':'artwork','mode':'fetch-only','evidence':'owner-fixture'}]
        errors=gate.owner_findings({'ui/dist/bg_nebula.png':b'fixture'},'source-tar',decisions)
        self.assertEqual({e['component'] for e in errors if e['code']=='POLICY_EXCLUDED'},{'astrospheric','artwork'})
    def test_owner_disposition_only_resolves_exact_known_question(self):
        path='ui/dist/bg_nebula.png'
        data=b'reviewed artwork fixture'
        rules={'assets':[{'path':path,'sha256':proof.sha(data)}]}
        messages=[path+': registered asset requires an owner decision',path+': required companion notice is absent or changed',path+': asset bytes differ from the reviewed source']
        decisions=[{'component':'artwork','mode':'redistribute','evidence':'owner-fixture'}]
        self.assertEqual(gate.resolved_owner_messages(messages,{path:data},rules,decisions),messages[1:])
        self.assertEqual(gate.resolved_owner_messages(messages,{path:data+b'new'},rules,decisions),messages)
        self.assertEqual(gate.resolved_owner_messages(messages,{path:data},rules,[{'component':'artwork','mode':'pending','evidence':'owner-fixture'}]),messages)
    def test_player_one_ruling_resolves_only_the_registered_library_question(self):
        path='server/astrodeck/vendor/playerone/linux-x86_64/libPlayerOneCamera.so.3.10.0'
        data=b'\x7fELF reviewed sdk fixture'
        rules={'assets':[{'path':path,'sha256':proof.sha(data)}]}
        ask=path+': reviewed licence still needs owner clearance'
        other=path+': required companion notice is absent or changed'
        ruled=[{'component':'playerone','mode':'redistribute','evidence':'owner-fixture'}]
        self.assertEqual(gate.resolved_owner_messages([ask,other],{path:data},rules,ruled),[other])
        self.assertEqual(gate.resolved_owner_messages([ask],{path:data+b'x'},rules,ruled),[ask])
        self.assertEqual(gate.resolved_owner_messages([ask],{path:data},rules,[{'component':'playerone','mode':'pending','evidence':'owner-fixture'}]),[ask])
        notice='server/astrodeck/vendor/playerone/LICENSE'
        nask=notice+': reviewed licence still needs owner clearance'
        self.assertEqual(gate.resolved_owner_messages([nask],{notice:b'text'},{'assets':[{'path':notice,'sha256':proof.sha(b'text')}]},ruled),[nask])
    def test_owner_disposition_handles_the_same_ui_in_frozen_location(self):
        name='astrodeck/webui/bg_nebula.png'
        data=b'artwork'
        messages=[name+': registered asset requires an owner decision']
        self.assertEqual(gate.resolved_owner_messages(messages,{name:data},{'assets':[{'path':'ui/dist/bg_nebula.png','sha256':proof.sha(data)}]},[{'component':'artwork','mode':'redistribute','evidence':'owner-fixture'}]),[])
    def test_native_magic_cannot_hide_behind_data_suffix(self):
        self.assertTrue(gate.is_native_payload('payload.dat',b'MZnew library'))
        self.assertTrue(gate.is_native_payload('payload.dat',b'\x7fELFnew library'))
        self.assertFalse(gate.is_native_payload('table.dat',b'data table'))

    def test_server_wheel_cannot_hide_binary_in_dist_info(self):
        files = {'astrodeck-0.3.39.dist-info/METADATA': b'Name: astrodeck\n',
                 'astrodeck-0.3.39.dist-info/vendor.dll': b'MZsurprise'}
        errors, _ = gate.server_metadata_review(files)
        self.assertEqual([(e['code'], e['path']) for e in errors], [('UNACCOUNTED', 'astrodeck-0.3.39.dist-info/vendor.dll')])
    def test_server_wheel_metadata_rejects_binary_disguised_as_text(self):
        errors, _ = gate.server_metadata_review({'astrodeck-0.3.39.dist-info/METADATA': b'Name: fake\x00MZ'})
        self.assertIn('UNACCOUNTED', {e['code'] for e in errors})
    def test_server_wheel_cannot_hide_arbitrary_license_payload(self):
        errors, _ = gate.server_metadata_review({'astrodeck-0.3.39.dist-info/licenses/arbitrary.txt': b'copied data'})
        self.assertIn('UNACCOUNTED', {e['code'] for e in errors})
    def test_dss2_fetch_only_policy_rejects_tiles(self):
        errors = gate.owner_findings({'server/astrodeck/catalog/_bundled_pack/dss2color/tile.jpg': b'fake'}, 'source-tar',
                                    [{'component':'dss2','mode':'fetch-only','include_tiles':False}])
        self.assertIn('POLICY_EXCLUDED', {e['code'] for e in errors})
    def test_notice_hash_normalizes_only_encoding_newlines_and_edge_whitespace(self):
        row = proof.notice_record(b'Copyright holders.\r\nTerms follow.\r\n')
        pool = {proof.sha(b'Copyright holders.\nTerms follow.')}
        self.assertTrue(gate.notice_present(row, set(), pool))
        self.assertFalse(gate.notice_present(row, set(), {proof.sha(b'Terms follow.')}))
        self.assertFalse(gate.notice_present(row, set(), {proof.sha(b'Copyright someone else.\nTerms follow.')}))
    def test_binary_notice_has_no_text_digest(self):
        self.assertNotIn('text_sha256', proof.notice_record(b'\xff\xfe\x00'))
    def test_ui_source_set_cannot_drop_an_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'ui').mkdir()
            (root/'ui/package-lock.json').write_bytes(b'{}')
            ui={'schema_version':2, 'npm_lock_sha256':proof.sha(b'{}'),
                'source_inputs':[{'path':'ui/src/credits.generated.json','sha256':proof.sha(b'{}'),'encoding':'normalized-utf8'}],
                'files':[{'path':'ui/dist/main.js','inputs':['repo:ui/src/new-table.ts','repo:ui/src/credits.generated.json']}]}
            with patch.object(gate,'warm_base'):
                errors=gate.review_ui_inputs(ui,b'{}',root)
            self.assertIn('UI_SOURCE',{e['code'] for e in errors})
    def test_ui_new_source_not_approved_by_current_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'ui').mkdir()
            (root/'ui/package-lock.json').write_bytes(b'{}')
            (root/'ui/new-table.ts').write_bytes(b'export const copied = 1;')
            ui={'schema_version':2, 'npm_lock_sha256':proof.sha(b'{}'),
                'source_inputs':[{'path':'ui/new-table.ts','sha256':proof.sha(b'export const copied = 1;'),'encoding':'normalized-utf8'}],
                'files':[{'path':'ui/dist/main.js','inputs':['repo:ui/new-table.ts']}]}
            with patch.object(gate,'warm_base'),patch.object(gate,'approved_source',return_value=False):
                errors=gate.review_ui_inputs(ui,b'{}',root)
            self.assertIn('SOURCE_CHANGED',{e['code'] for e in errors})
    def test_frozen_new_data_stays_unaccounted_even_with_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'tools/licence').mkdir(parents=True)
            (root/'tools/licence/release_provenance.py').write_bytes(b'fixture')
            (root/'tools/licence/dependency-current-windows-archive.json').write_text('{"files":[]}',encoding='utf-8')
            (root/'tools/licence/artifact-registry.json').write_text('{"assets":[]}',encoding='utf-8')
            (root/'ui').mkdir()
            (root/'ui/package-lock.json').write_bytes(b'{}')
            name='some_package/copied-table.dat'
            files={name:b'new copied data'}
            evidence={'artifact_sha256':'artifact','generator_sha256':proof.sha(b'fixture'),
                      'files':[{'path':name,'sha256':proof.sha(files[name]),'record_verified':True,'method':'exact-input-bytes','component':'some-package','source':'dist:some-package/table.dat'}]}
            with patch.object(gate,'checked_ui',return_value=([],False)),patch.object(gate,'review_ui_inputs',return_value=[]),patch.object(gate.audit_artifact,'review',return_value=([],[])):
                errors,_=gate.frozen_review(files,{name:'x'},evidence,'artifact',{'groups':[],'licenses':{}},b'{}',{},root)
            self.assertTrue(any(e['code']=='UNACCOUNTED' and e['path']==name for e in errors))

    def test_frozen_app_and_native_direct_url_block_even_with_verified_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'tools/licence').mkdir(parents=True)
            (root/'tools/licence/release_provenance.py').write_bytes(b'fixture')
            (root/'tools/licence/dependency-current-windows-archive.json').write_text('{"files":[]}',encoding='utf-8')
            (root/'tools/licence/artifact-registry.json').write_text('{"assets":[]}',encoding='utf-8')
            (root/'ui').mkdir(); (root/'ui/package-lock.json').write_bytes(b'{}')
            # No real URL is needed: path-based refusal must precede content use.
            names=['astrodeck-0.3.39.dist-info/direct_url.json','astrodeck_native-0.1.0.dist-info/direct_url.json']
            files={name:b'{"archive_info":{"hashes":{"sha256":"verified"}}}' for name in names}
            evidence={'artifact_sha256':'artifact','generator_sha256':proof.sha(b'fixture'),
                      'files':[{'path':name,'sha256':proof.sha(files[name]),'record_verified':True,'method':'exact-input-bytes','component':'AstroDeck','source':'dist:astrodeck/'+name} for name in names]}
            with patch.object(gate,'checked_ui',return_value=([],False)),patch.object(gate,'review_ui_inputs',return_value=[]),patch.object(gate.audit_artifact,'review',return_value=([],[])),patch.object(gate,'native_review',return_value=([],[])):
                errors,_=gate.frozen_review(files,{name:'x' for name in names},evidence,'artifact',{'groups':[],'licenses':{}},b'{}',{},root)
            self.assertEqual({e['path'] for e in errors if e['code']=='PRIVATE_METADATA'},set(names))


class VersionBumpEvidenceTests(unittest.TestCase):
    """version_bump_evidence carries a reviewed hash across a version line only (#1073)."""
    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("version_bump_evidence", Path(__file__).with_name("version_bump_evidence.py"))
        self.vb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.vb)
        self.base = '"""AstroDeck."""\n__version__ = "0.3.45"\n'
        self.toml = '[project]\nname = "astrodeck"\nversion = "0.3.45"\ndependencies = ["x>=0.3.45"]\n'
    def test_a_version_line_change_is_carried(self):
        self.assertTrue(self.vb.version_only_delta("server/astrodeck/__init__.py", self.base, self.base.replace("0.3.45", "0.3.46"), "0.3.46"))
        self.assertTrue(self.vb.version_only_delta("server/pyproject.toml", self.toml, self.toml.replace('version = "0.3.45"', 'version = "0.3.46"'), "0.3.46"))
    def test_line_endings_do_not_matter(self):
        cur = self.base.replace("0.3.45", "0.3.46").replace("\n", "\r\n")
        self.assertTrue(self.vb.version_only_delta("server/astrodeck/__init__.py", self.base, cur, "0.3.46"))
    def test_any_other_change_is_refused(self):
        cur = self.base.replace("0.3.45", "0.3.46") + "EVIL = 1\n"
        self.assertFalse(self.vb.version_only_delta("server/astrodeck/__init__.py", self.base, cur, "0.3.46"))
        # the dependency pin is not the version line and must not move with it
        cur = self.toml.replace("0.3.45", "0.3.46")
        self.assertFalse(self.vb.version_only_delta("server/pyproject.toml", self.toml, cur, "0.3.46"))
    def test_the_new_version_must_be_the_one_written(self):
        cur = self.base.replace("0.3.45", "0.3.47")
        self.assertFalse(self.vb.version_only_delta("server/astrodeck/__init__.py", self.base, cur, "0.3.46"))
    def test_two_version_lines_are_refused(self):
        base = self.base + '__version__ = "0.3.45"\n'
        self.assertFalse(self.vb.version_only_delta("server/astrodeck/__init__.py", base, base.replace("0.3.45", "0.3.46"), "0.3.46"))

class LedgerFixture(unittest.TestCase):
    """A real git repository: a base commit, then a commit that changes one file and adds one."""
    SAME = 'server/astrodeck/same.py'
    CHANGED = 'server/astrodeck/changed.py'
    FRESH = 'server/astrodeck/fresh.py'
    REVIEW = 'Read the whole diff from the base: one constant changed from 1 to 2, nothing else.'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        git_in(self.root, 'init', '-q')
        write_files(self.root, {self.SAME: b'SAME = 1\n', self.CHANGED: b'CHANGED = 1\n'})
        self.base = commit_all(self.root, 'base')
        self.changed, self.fresh = b'CHANGED = 2\n', b'FRESH = 1\n'
        write_files(self.root, {self.CHANGED: self.changed, self.FRESH: self.fresh})
        self.head = commit_all(self.root, 'change')

    def put(self, rows):
        write_ledger_file(self.root, self.base, rows)

    def ledger_bytes(self):
        return (self.root / evidence.LEDGER).read_bytes()

    def rows(self):
        return {row['path']: row for row in json.loads(self.ledger_bytes())['files']}

    def approved(self, path):
        return gate.approved_source(path, (self.root / path).read_bytes(), self.root)


class LedgerTests(LedgerFixture):
    """The ledger is the one place a changed file is approved, and a stale row cannot hide (#1160, #1186)."""
    def test_a_stale_ledger_digest_is_not_approval(self):
        self.put([ledger_row(self.CHANGED, b'CHANGED = 99\n')])
        self.assertFalse(self.approved(self.CHANGED))

    def test_a_matching_row_approves(self):
        self.put([ledger_row(self.CHANGED, self.changed)])
        self.assertTrue(self.approved(self.CHANGED))

    def test_a_stale_row_poisons_a_path_equal_to_the_base(self):
        self.put([ledger_row(self.SAME, b'SAME = 0\n')])
        self.assertFalse(self.approved(self.SAME))
        evidence.sync(self.root)
        self.assertTrue(self.approved(self.SAME))

    def test_without_a_row_the_base_copy_decides(self):
        self.put([])
        self.assertTrue(self.approved(self.SAME))
        self.assertFalse(self.approved(self.CHANGED))
        self.assertFalse(self.approved(self.FRESH))

    def test_a_row_without_review_text_approves_nothing(self):
        self.put([ledger_row(self.CHANGED, self.changed, review=' ')])
        _, problems = evidence.read_ledger(self.root)
        self.assertTrue(problems)
        self.assertFalse(self.approved(self.CHANGED))

    def test_the_base_comes_from_the_ledger_not_a_constant(self):
        self.assertFalse(hasattr(gate, 'BASE'))
        self.put([])
        self.assertEqual(evidence.read_ledger(self.root)[0]['base'], self.base)

    def test_a_missing_ledger_is_a_blocker(self):
        codes = {e['code'] for e in gate.ledger_findings(self.root)}
        self.assertEqual(codes, {'LEDGER_INVALID'})
        self.assertIn('LEDGER_INVALID', gate.UNACCOUNTED)

    def test_a_schema_one_ledger_is_a_blocker(self):
        (self.root / 'tools/licence').mkdir(parents=True)
        (self.root / evidence.LEDGER).write_text(json.dumps({'schema_version': 1, 'base': self.base, 'files': []}), encoding='utf-8')
        self.assertEqual({e['code'] for e in gate.ledger_findings(self.root)}, {'LEDGER_INVALID'})

    def test_two_rows_for_one_path_are_a_blocker(self):
        self.put([ledger_row(self.CHANGED, self.changed), ledger_row(self.CHANGED, b'other\n')])
        self.assertTrue(gate.ledger_findings(self.root))

    def test_a_base_that_is_not_an_ancestor_of_head_is_a_blocker(self):
        self.put([])
        self.assertEqual(gate.ledger_findings(self.root), [])
        git_in(self.root, 'checkout', '-q', '--orphan', 'elsewhere')
        git_in(self.root, 'commit', '-q', '--allow-empty', '-m', 'unrelated')
        orphan = git_in(self.root, 'rev-parse', 'HEAD').stdout.decode().strip()
        git_in(self.root, 'checkout', '-q', '-f', '--detach', self.head)
        write_ledger_file(self.root, orphan, [])
        errors = gate.ledger_findings(self.root)
        self.assertEqual([e['code'] for e in errors], ['LEDGER_INVALID'])
        self.assertIn('ancestor', errors[0]['message'])

    def review_archive(self, files, reviewed_sources=()):
        (self.root / 'tools/licence').mkdir(parents=True, exist_ok=True)
        (self.root / 'tools/licence/artifact-registry.json').write_text(
            json.dumps({'assets': [], 'reviewed_sources': list(reviewed_sources)}), encoding='utf-8')
        (self.root / 'ui').mkdir(exist_ok=True)
        (self.root / 'ui/package-lock.json').write_bytes(b'{}')
        credits_bytes = b'{"groups":[],"licenses":{}}'
        ui = {'schema_version': 2, 'npm_lock_sha256': proof.sha(b'{}'), 'source_inputs': [], 'files': [],
              'credits_sha256': proof.sha(credits_bytes)}
        return gate.source_review(files, json.loads(credits_bytes), credits_bytes, ui, self.root)[0]

    def unaccounted(self, errors, path):
        return [e for e in errors if e['code'] == 'UNACCOUNTED' and e['path'] == path]

    def test_a_new_server_module_needs_only_a_ledger_row(self):
        self.put([])
        found = self.unaccounted(self.review_archive({self.FRESH: self.fresh}), self.FRESH)
        self.assertEqual(len(found), 1)
        self.assertIn('release_evidence.py approve --path ' + self.FRESH, found[0]['message'])
        self.put([ledger_row(self.FRESH, self.fresh)])
        self.assertEqual(self.unaccounted(self.review_archive({self.FRESH: self.fresh}), self.FRESH), [])

    def test_a_registry_digest_is_not_approval(self):
        self.put([])
        rule = {'path': self.CHANGED, 'sha256': evidence.digest(self.changed), 'component': 'AstroDeck application source'}
        self.assertEqual(len(self.unaccounted(self.review_archive({self.CHANGED: self.changed}, [rule]), self.CHANGED)), 1)

    def test_a_stale_row_names_itself_in_the_message(self):
        self.put([ledger_row(self.CHANGED, b'CHANGED = 99\n')])
        found = self.unaccounted(self.review_archive({self.CHANGED: self.changed}), self.CHANGED)
        self.assertEqual(len(found), 1)
        self.assertIn('ledger row is stale', found[0]['message'])

    def test_the_registry_route_matches_audit_artifact(self):
        table = ['server/astrodeck/x.py', 'server/astrodeck/sub/x.py', 'server/astrodeck/vendor/z/x.py', 'server/astrodeck/data.json',
                 'server/pyproject.toml', 'packaging/x.py', 'server/tests/x.py']
        credits_bytes = b'{"groups":[],"licenses":{}}'
        ui = {'credits_sha256': proof.sha(credits_bytes), 'files': []}
        for name in table:
            _, records = gate.audit_artifact.review({name: b'X = 1\n'}, {'assets': [], 'reviewed_sources': []},
                                                     json.loads(credits_bytes), ui, credits_bytes, {})
            routed = records[0]['component'] == 'UNREVIEWED source'
            self.assertEqual(evidence.is_registry_source(name), routed, name)


class LedgerCommandTests(LedgerFixture):
    """sync only removes claims; approve writes a digest only for the files it is told to name."""
    def test_sync_never_writes_a_digest(self):
        stale = ledger_row(self.CHANGED, b'CHANGED = 99\n')
        current = ledger_row(self.FRESH, self.fresh)
        self.put([stale, current, ledger_row(self.SAME, b'SAME = 0\n')])
        dropped = evidence.sync(self.root)
        self.assertEqual(dropped, [self.SAME])
        self.assertEqual(self.rows(), {self.CHANGED: stale, self.FRESH: current})

    def test_sync_drops_a_row_for_a_file_that_is_gone(self):
        self.put([ledger_row('server/astrodeck/gone.py', b'GONE = 1\n')])
        self.assertEqual(evidence.sync(self.root), ['server/astrodeck/gone.py'])
        self.assertEqual(self.rows(), {})

    def test_approve_refuses_without_naming_what_it_approves(self):
        self.put([ledger_row(self.CHANGED, b'CHANGED = 0\n', review=self.REVIEW)])
        write_files(self.root, {'server/astrodeck/untracked.py': b'U = 1\n'})
        before = self.ledger_bytes()
        cases = {
            'no path': ([], self.REVIEW),
            'a pattern': (['server/astrodeck/*.py'], self.REVIEW),
            'an untracked file': (['server/astrodeck/untracked.py'], self.REVIEW),
            'a short review': ([self.FRESH], 'looks fine'),
            'a file equal to the base': ([self.SAME], self.REVIEW),
            'the old review text on new bytes': ([self.CHANGED], self.REVIEW),
        }
        for label, (paths, review) in cases.items():
            with self.subTest(label):
                with self.assertRaises(SystemExit):
                    evidence.approve(self.root, paths, review)
                self.assertEqual(self.ledger_bytes(), before)

    def test_approve_writes_the_current_digest_and_the_head_revision(self):
        self.put([])
        written = evidence.approve(self.root, [self.CHANGED, self.FRESH], self.REVIEW)
        self.assertEqual({path for path, _, _ in written}, {self.CHANGED, self.FRESH})
        rows = self.rows()
        self.assertEqual(rows[self.CHANGED]['sha256'], evidence.digest(self.changed))
        self.assertEqual(rows[self.FRESH]['reviewed_revision'], self.head)
        self.assertEqual(rows[self.FRESH]['review'], self.REVIEW)
        self.assertTrue(self.approved(self.CHANGED) and self.approved(self.FRESH))
        self.assertEqual(evidence.approve(self.root, [self.CHANGED], self.REVIEW), [])

    def test_approve_leaves_unnamed_rows_alone(self):
        stale = ledger_row(self.CHANGED, b'CHANGED = 99\n')
        self.put([stale])
        evidence.approve(self.root, [self.FRESH], self.REVIEW)
        self.assertEqual(self.rows()[self.CHANGED], stale)
        self.assertFalse(self.approved(self.CHANGED))

    def run_cli(self, *argv):
        out = io.StringIO()
        with patch.object(evidence, 'ROOT', self.root), contextlib.redirect_stdout(out):
            code = evidence.main(list(argv))
        return code, out.getvalue()

    def test_status_lists_each_class_and_exits_nonzero(self):
        self.put([ledger_row(self.SAME, b'SAME = 0\n'), ledger_row(self.CHANGED, b'CHANGED = 99\n')])
        code, text = self.run_cli('status', '--json')
        report = json.loads(text)
        self.assertEqual(code, 1)
        self.assertEqual(report['unapproved'], [self.FRESH])
        self.assertEqual(report['stale_droppable'], [self.SAME])
        self.assertEqual(report['stale_changed'], [self.CHANGED])

    def test_status_is_clean_once_everything_is_approved(self):
        self.put([])
        self.assertEqual(self.run_cli('approve', '--path', self.CHANGED, '--path', self.FRESH, '--review', self.REVIEW)[0], 0)
        code, text = self.run_cli('status')
        self.assertEqual((code, text.splitlines()[-1]), (0, 'clean'))

    def test_status_reports_a_gated_file_committed_with_crlf(self):
        write_files(self.root, {'server/astrodeck/crlf.py': b'A = 1\r\nB = 2\r\n'})
        commit_all(self.root, 'crlf')
        self.put([])
        self.assertTrue(evidence.survey(self.root)['hazards'])


class DigestTests(unittest.TestCase):
    def test_digest_agrees_for_lf_crlf_and_bom(self):
        text = b'A = 1\nB = 2\n'
        self.assertEqual(evidence.digest(text), evidence.digest(text.replace(b'\n', b'\r\n')))
        self.assertEqual(evidence.digest(text), evidence.digest(b'\xef\xbb\xbf' + text))
        self.assertNotEqual(evidence.digest(text), evidence.digest(text.replace(b'2', b'3')))

    def test_digest_of_a_crlf_checkout_equals_the_digest_of_the_committed_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git_in(root, 'init', '-q', autocrlf='true')
            write_files(root, {'mod.py': b'A = 1\nB = 2\n'})
            git_in(root, 'add', '-A', autocrlf='true')
            git_in(root, 'commit', '-q', '-m', 'lf blob', autocrlf='true')
            (root / 'mod.py').unlink()
            git_in(root, 'checkout', '--', 'mod.py', autocrlf='true')
            working = (root / 'mod.py').read_bytes()
            committed = evidence.copy_at(root, 'HEAD', 'mod.py')
            self.assertIn(b'\r\n', working)
            self.assertNotIn(b'\r\n', committed)
            self.assertEqual(evidence.digest(working), evidence.digest(committed))


class VersionCarryTests(unittest.TestCase):
    """version_bump_evidence writes the one ledger, from HEAD's approved copy, and checks the other carriers."""
    INIT = 'server/astrodeck/__init__.py'
    TOML = 'server/pyproject.toml'
    REGISTRY = b'{"reviewed_sources": [{"path": "server/pyproject.toml", "sha256": "0000"}]}\n'

    def setUp(self):
        self.vb = load_version_bump()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        git_in(self.root, 'init', '-q')
        self.carriers = {
            'README.md': b'![version](https://img.shields.io/badge/version-0.3.45-4DD9E8)\n',
            'tools/docs/setup-claims.json': b'{"claim": "![version](https://img.shields.io/badge/version-0.3.45-4DD9E8)"}\n',
            'ui/package.json': b'{\n  "name": "ui",\n  "version": "0.3.45"\n}\n',
            'ui/src/credits.generated.json': b'{\n  "version": "0.3.45"\n}\n'}
        write_files(self.root, {self.INIT: b'"""AstroDeck."""\n__version__ = "0.3.45"\n',
                                self.TOML: b'[project]\nname = "astrodeck"\nversion = "0.3.45"\n',
                                'tools/licence/artifact-registry.json': self.REGISTRY, **self.carriers})
        self.base = commit_all(self.root, 'base')
        write_ledger_file(self.root, self.base, [])

    def bump(self, names, extra=b''):
        for name in names:
            path = self.root / name
            path.write_bytes(path.read_bytes().replace(b'0.3.45', b'0.3.46') + (extra if name == self.INIT else b''))

    def approved(self, path):
        return gate.approved_source(path, (self.root / path).read_bytes(), self.root)

    def test_carry_writes_the_ledger_rows_and_not_the_registry(self):
        self.bump([self.INIT, self.TOML])
        self.assertFalse(self.approved(self.INIT))
        self.vb.carry_ledger(self.root, '0.3.46')
        rows = {r['path']: r for r in json.loads((self.root / evidence.LEDGER).read_bytes())['files']}
        head = evidence.head_revision(self.root)
        for path in (self.INIT, self.TOML):
            self.assertEqual(rows[path]['sha256'], evidence.digest((self.root / path).read_bytes()))
            self.assertEqual(rows[path]['reviewed_revision'], head)
            self.assertTrue(self.approved(path))
        self.assertEqual((self.root / 'tools/licence/artifact-registry.json').read_bytes(), self.REGISTRY)

    def test_a_carried_source_is_accepted_where_the_frozen_leg_asks(self):
        # frozen_review asks approved_source about the bumped __init__.py; before the ledger row it said no (B4).
        self.bump([self.INIT, self.TOML])
        self.assertFalse(self.approved(self.INIT))
        self.vb.carry_ledger(self.root, '0.3.46')
        self.assertTrue(self.approved(self.INIT))

    def test_carrying_twice_changes_nothing(self):
        self.bump([self.INIT, self.TOML])
        self.vb.carry_ledger(self.root, '0.3.46')
        first = (self.root / evidence.LEDGER).read_bytes()
        self.assertTrue(all('already current' in line for line in self.vb.carry_ledger(self.root, '0.3.46')))
        self.assertEqual((self.root / evidence.LEDGER).read_bytes(), first)

    def test_carry_refuses_when_heads_copy_was_not_approved(self):
        (self.root / self.INIT).write_bytes(b'"""AstroDeck."""\n__version__ = "0.3.45"\nEXTRA = 1\n')
        commit_all(self.root, 'an unreviewed edit')
        self.bump([self.INIT, self.TOML])
        before = (self.root / evidence.LEDGER).read_bytes()
        with self.assertRaises(SystemExit) as refused:
            self.vb.carry_ledger(self.root, '0.3.46')
        self.assertIn('was not approved', str(refused.exception))
        self.assertEqual((self.root / evidence.LEDGER).read_bytes(), before)

    def test_carry_refuses_a_change_that_is_more_than_the_version_line(self):
        self.bump([self.INIT, self.TOML], extra=b'EVIL = 1\n')
        before = (self.root / evidence.LEDGER).read_bytes()
        with self.assertRaises(SystemExit) as refused:
            self.vb.carry_ledger(self.root, '0.3.46')
        self.assertIn('more than its version line', str(refused.exception))
        self.assertEqual((self.root / evidence.LEDGER).read_bytes(), before)

    def test_other_version_carriers_are_checked_not_written(self):
        before = {name: (self.root / name).read_bytes() for name in self.carriers}
        lagging = self.vb.lagging_carriers(self.root, '0.3.46')
        self.assertEqual(len(lagging), 4)
        self.assertIn('run python tools/gen_credits.py', ' '.join(lagging))
        self.assertEqual({name: (self.root / name).read_bytes() for name in self.carriers}, before)
        self.bump(list(self.carriers))
        self.assertEqual(self.vb.lagging_carriers(self.root, '0.3.46'), [])

    def run_main(self):
        with patch.object(self.vb, 'ROOT', self.root), contextlib.redirect_stdout(io.StringIO()):
            return self.vb.main(['0.3.46'])

    def test_main_fails_while_a_carrier_lags_and_passes_when_all_carry(self):
        self.bump([self.INIT, self.TOML])
        self.assertEqual(self.run_main(), 1)
        self.assertIn(b'0.3.45', (self.root / 'README.md').read_bytes())
        self.bump(list(self.carriers))
        self.assertEqual(self.run_main(), 0)


class LedgerMigrationTests(LedgerFixture):
    """Moving a v1 ledger to v2 approves nothing it did not approve before (#1186, R)."""
    INIT = 'server/astrodeck/__init__.py'
    NOROW = 'server/astrodeck/norow.py'
    CURRENT = 'server/astrodeck/current.py'

    def setUp(self):
        super().setUp()
        self.vb = load_version_bump()
        write_files(self.root, {self.INIT: b'__version__ = "0.3.45"\n', self.NOROW: b'N = 1\n', self.CURRENT: b'C = 1\n'})
        self.base = commit_all(self.root, 'second base')
        write_files(self.root, {self.INIT: b'__version__ = "0.3.46"\n', self.NOROW: b'N = 2\n', self.CURRENT: b'C = 2\n',
                                self.CHANGED: b'CHANGED = 3\n', self.FRESH: b'FRESH = 2\n'})
        commit_all(self.root, 'work since the base')

    def digest_of(self, path):
        return evidence.digest((self.root / path).read_bytes())

    def old_rule(self, path, rows, registry):
        data = (self.root / path).read_bytes()
        row = next((r for r in rows if r['path'] == path), None)
        if row and row['sha256'] == evidence.digest(data):
            return True
        if evidence.matches_base(self.root, {'base': self.base}, path, data):
            return True
        return registry.get(path) == evidence.digest(data)

    def migrate(self):
        evidence.sync(self.root)
        ledger, _ = evidence.read_ledger(self.root)
        for path, line in self.vb.VERSION_LINES.items():
            if not (self.root / path).is_file():
                continue
            data = (self.root / path).read_bytes()
            if evidence.row_verdict(ledger, path, data):
                continue
            original = evidence.copy_at(self.root, self.base, path).decode()
            new = line.search(self.vb.normalized(data.decode())).group(1)
            self.assertTrue(self.vb.version_only_delta(path, original, data.decode(), new))
            ledger['files'].append(ledger_row(path, data, review='Carried from the reviewed base: the version line changed and nothing else.'))
        evidence.write_ledger(self.root, ledger)

    def test_the_migration_leaves_the_approved_set_identical(self):
        rows = [ledger_row(self.CURRENT, b'C = 2\n'),                      # current row
                ledger_row(self.SAME, b'SAME = 0\n'),                      # stale, file equals base: droppable
                ledger_row(self.CHANGED, b'CHANGED = 2\n')]                # stale, file changed: stays stale
        self.put(rows)
        registry = {self.INIT: self.digest_of(self.INIT)}                  # approved only by the registry digest
        paths = [self.CURRENT, self.SAME, self.CHANGED, self.INIT, self.NOROW, self.FRESH]
        before = {p: self.old_rule(p, rows, registry) for p in paths}
        self.migrate()
        after = {p: self.approved(p) for p in paths}
        self.assertEqual(after, before)
        self.assertEqual([p for p, ok in after.items() if not ok], [self.CHANGED, self.NOROW, self.FRESH])
        self.assertEqual(set(self.rows()), {self.CURRENT, self.CHANGED, self.INIT})

if __name__ == '__main__':
    unittest.main()
