"""Release gates: real archive/RECORD mutations; no application execution."""
from __future__ import annotations
import base64
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
import release_provenance as proof

ROOT = Path(__file__).resolve().parents[2]

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
                   'NATIVE-SOURCE.txt': ('Exact fixture source commit: ' + gate.BASE + '\n' + 'Source included. ' * 10).encode(),
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
        self.git_patch = patch.object(gate.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=gate.BASE + '\n'))
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)
    def reseal(self):
        seal = {'schema_version': 1, 'source_commit': gate.BASE, 'native_source_sha256': self.source_digest.hexdigest(),
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


class AdditionalBoundaryTests(unittest.TestCase):
    def test_code_name_and_qualname_are_runtime_visible_proof(self):
        original=compile('def actual(): return 1','source.py','exec',dont_inherit=True)
        changed=original.replace(co_name='unreviewed',co_qualname='unreviewed')
        self.assertNotEqual(proof.code_shape(original),proof.code_shape(changed))
    def pyz(self, duplicate=False, gap=False):
        raw=marshal.dumps(compile('value=1','fixture.py','exec',dont_inherit=True))
        body=zlib.compress(raw)
        prefix=b'X' if gap else b''
        start=17+len(prefix)
        entries=[('fixture',(0,start,len(body)))]
        payload=prefix+body
        if duplicate:
            entries.append(('fixture',(0,17+len(payload),len(body))))
            payload+=body
        return b'PYZ\0'+b'\0'*4+struct.pack('!i',17+len(payload))+b'\0'*5+payload+marshal.dumps(entries)
    def test_pyz_duplicate_member_is_rejected_before_dict_conversion(self):
        self.assertEqual(proof.validate_pyz(self.pyz()),{'fixture'})
        with self.assertRaisesRegex(ValueError,'duplicate'):
            proof.validate_pyz(self.pyz(duplicate=True))
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

if __name__ == '__main__':
    unittest.main()