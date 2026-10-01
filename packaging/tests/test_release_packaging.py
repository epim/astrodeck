"""Behavioral release policy/native fixtures. No server, device or install calls."""
from contextlib import ExitStack, redirect_stdout
import copy
import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packaging"))
import distribution_policy as policy
import build_native as native
import build_binary as binary
import entry
import native_probe as probe
import metadata_payloads


def load_release():
    spec = importlib.util.spec_from_file_location("release_fixture", ROOT / "scripts/build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleasePolicy(unittest.TestCase):
    def setUp(self):
        self.policy = policy.load_policy(ROOT)
        scratch = ROOT / ".probe/release"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="policy-tests-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pkg = self.root / "server/astrodeck"
        for relative in ("vendor/playerone/camera.dll", "vendor/playerone/linux-arm64/lib.so.3.2", "vendor/playerone/LICENSE", "vendor/zwo/camera.dll", "catalog/_bundled_pack/dss2color/tile.jpg", "__init__.py"):
            path = self.pkg / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic payload", encoding="utf-8")
        (self.root / "server/pyproject.toml").write_text("[project]\nname='astrodeck'\n", encoding="utf-8")
        (self.root / "packaging").mkdir()
        self.write_policy()

    def write_policy(self):
        (self.root / "packaging/distribution-policy.json").write_text(json.dumps(self.policy), encoding="utf-8")

    def test_pending_preserves_artifact_history(self):
        self.assertFalse(policy.includes_playerone("source-tar", self.policy))
        self.assertTrue(policy.includes_playerone("frozen", self.policy))
        self.assertTrue(policy.includes_playerone("server-wheel", self.policy))
        self.assertTrue(policy.decision_records(self.policy, "frozen")[0]["requires_owner_decision"])

    def test_fetch_only_synchronizes_every_artifact(self):
        self.policy["decisions"]["playerone"]["mode"] = "fetch-only"
        for kind in policy.KINDS:
            self.assertFalse(policy.include_file("vendor/playerone/camera.dll", kind, self.policy), kind)
            self.assertFalse(policy.include_file("vendor/playerone/linux-arm64/lib.so.3.2", kind, self.policy), kind)
            self.assertTrue(policy.include_file("vendor/playerone/LICENSE", kind, self.policy), kind)
        self.assertFalse(any("playerone/*.dll" in p for p in policy.package_data(self.policy)))

    def test_redistribute_synchronizes_every_artifact(self):
        self.policy["decisions"]["playerone"]["mode"] = "redistribute"
        for kind in policy.KINDS:
            self.assertTrue(policy.include_file("vendor/playerone/camera.dll", kind, self.policy), kind)
        self.assertIn("vendor/playerone/*.dll", policy.package_data(self.policy))

    def test_other_vendor_is_unchanged(self):
        self.policy["decisions"]["playerone"]["mode"] = "fetch-only"
        self.assertTrue(policy.include_file("vendor/zwo/camera.dll", "frozen", self.policy))

    def test_dss2_is_refused_in_all_payloads(self):
        for kind in policy.KINDS:
            self.assertFalse(policy.include_file("catalog/_bundled_pack/dss2color/tile.jpg", kind, self.policy))

    def test_invalid_policy_refused(self):
        self.policy["decisions"]["playerone"]["mode"] = "assumed"
        self.write_policy()
        with self.assertRaisesRegex(ValueError, "invalid owner"):
            policy.load_policy(self.root)

    def test_pending_cannot_be_redefined(self):
        self.policy["decisions"]["playerone"]["pending_by_artifact"]["frozen"] = False
        self.write_policy()
        with self.assertRaisesRegex(ValueError, "preserve artifact history"):
            policy.load_policy(self.root)

    def test_payload_cannot_escape(self):
        with self.assertRaisesRegex(ValueError, "contained"):
            policy.include_file("../vendor/playerone/camera.dll", "frozen", self.policy)

    def test_repository_static_table_agrees_with_policy(self):
        policy.check_package_data(ROOT)

    def test_static_table_drift_is_rejected(self):
        (self.root / "server/pyproject.toml").write_text('[tool.setuptools.package-data]\nastrodeck=[]\n',encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"static package-data disagrees"):
            policy.check_package_data(self.root,self.policy)

    def test_sync_only_rewrites_package_data(self):
        before = '[project]\nname="synthetic"\n[tool.setuptools.package-data]\nastrodeck=[]\n[other]\nvalue=3\n'
        path = self.root / "server/pyproject.toml"
        path.write_text(before,encoding="utf-8")
        policy.sync_package_data(self.root)
        after = path.read_text(encoding="utf-8")
        self.assertTrue(after.startswith('[project]\nname="synthetic"\n'))
        self.assertTrue(after.endswith('[other]\nvalue=3\n'))
        policy.check_package_data(self.root,self.policy)

    def test_direct_pip_wheels_follow_static_owner_table(self):
        # Actual wheel build, no install, no dependency fetch, no shared cache.
        # Every mode gets a fresh source/build tree; stale setuptools build trees
        # are not evidence of what the current package-data table selects.
        for mode,included in (("pending",True),("fetch-only",False),("redistribute",True)):
            repo = self.root / ("wheel-" + mode)
            (repo / "packaging").mkdir(parents=True)
            selected = copy.deepcopy(self.policy)
            selected["decisions"]["playerone"]["mode"] = mode
            (repo / "packaging/distribution-policy.json").write_text(json.dumps(selected),encoding="utf-8")
            shutil.copytree(self.pkg,repo / "server/astrodeck")
            (repo / "server/astrodeck/__init__.py").write_text("",encoding="utf-8")
            (repo / "server/pyproject.toml").write_text('[build-system]\nrequires=["setuptools"]\nbuild-backend="setuptools.build_meta"\n[project]\nname="astrodeck-fixture"\nversion="0.0.1"\n[tool.setuptools.packages.find]\ninclude=["astrodeck*"]\n[tool.setuptools.package-data]\nastrodeck=[]\n',encoding="utf-8")
            policy.sync_package_data(repo)
            env=dict(os.environ,PIP_DISABLE_PIP_VERSION_CHECK="1",PYTHONDONTWRITEBYTECODE="1")
            result=subprocess.run([sys.executable,"-B","-m","pip","wheel","--no-deps","--no-build-isolation","--no-cache-dir","--wheel-dir",str(repo / "wheels"),str(repo / "server")],env=env,capture_output=True,text=True)
            self.assertEqual(0,result.returncode,"synthetic direct pip wheel build failed")
            wheel=next((repo / "wheels").glob("*.whl"))
            with zipfile.ZipFile(wheel) as archive:
                names=set(archive.namelist())
            self.assertEqual(included,"astrodeck/vendor/playerone/camera.dll" in names,mode)
            self.assertEqual(included,"astrodeck/vendor/playerone/linux-arm64/lib.so.3.2" in names,mode)
            self.assertIn("astrodeck/vendor/playerone/LICENSE",names)
            self.assertNotIn("astrodeck/catalog/_bundled_pack/dss2color/tile.jpg",names)

    def exercise_server_install(self, forbidden=False):
        self.policy["decisions"]["playerone"]["mode"] = "fetch-only"
        self.write_policy()
        (self.root / "server/pyproject.toml").write_text('[tool.setuptools.package-data]\nastrodeck=[]\n', encoding="utf-8")
        policy.sync_package_data(self.root)
        stale = self.root / "server/build/astrodeck/vendor/playerone/old.dll"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale")
        commands = []
        staged_clean = []
        def run(cmd, **kwargs):
            commands.append(cmd)
            if "wheel" in cmd:
                staged_server = Path(cmd[-1])
                staged_clean.append(not (staged_server / "build").exists())
                out = Path(cmd[cmd.index("--wheel-dir") + 1])
                out.mkdir()
                with zipfile.ZipFile(out / "astrodeck-0.0.1-py3-none-any.whl", "w") as wheel:
                    wheel.writestr("astrodeck/vendor/playerone/LICENSE", "notice")
                    if forbidden:
                        wheel.writestr("astrodeck/vendor/playerone/camera.dll", "forbidden")
        with patch.object(binary,"ROOT",self.root), patch.object(binary,"SERVER",self.root / "server"), patch.object(binary,"run",side_effect=run):
            if forbidden:
                with self.assertRaisesRegex(SystemExit,"excluded by distribution policy"):
                    binary.install_server()
            else:
                binary.install_server()
        return commands,staged_clean

    def test_server_install_never_reuses_stale_build(self):
        commands,clean = self.exercise_server_install()
        self.assertEqual([True],clean)
        self.assertTrue(any("install" in cmd and any(".whl" in value for value in cmd) for cmd in commands))

    def test_server_install_refuses_forbidden_actual_wheel_member(self):
        commands,_ = self.exercise_server_install(forbidden=True)
        self.assertFalse(any("install" in cmd and any(".whl" in value for value in cmd) for cmd in commands))

    def test_real_tar_follows_selected_policy(self):
        release = load_release()
        for mode, included in (("pending", False), ("fetch-only", False), ("redistribute", True)):
            self.policy["decisions"]["playerone"]["mode"] = mode
            self.write_policy()
            with redirect_stdout(io.StringIO()):
                release.build("9.9.9", self.root, self.root / mode)
            staged = self.root / mode / "astrodeck-9.9.9/server/astrodeck"
            self.assertEqual(included, (staged / "vendor/playerone/camera.dll").exists(), mode)
            self.assertTrue((staged / "vendor/playerone/LICENSE").is_file())
            self.assertFalse((staged / "catalog/_bundled_pack/dss2color/tile.jpg").exists())

    def execute_spec(self):
        (self.root / "server/pyproject.toml").write_text('[tool.setuptools.package-data]\nastrodeck=[]\n', encoding="utf-8")
        policy.sync_package_data(self.root)
        webui = self.pkg / "webui"
        webui.mkdir(exist_ok=True)
        (webui / "index.html").write_text("<!doctype html>", encoding="utf-8")
        hooks = types.ModuleType("PyInstaller.utils.hooks")
        def metadata_rows(name):
            directory=self.root / "metadata" / (name + ".dist-info")
            directory.mkdir(parents=True,exist_ok=True)
            (directory / "METADATA").write_text("Name: " + name + "\n",encoding="utf-8")
            (directory / "direct_url.json").write_text('{"url":"file:///synthetic-private/build"}',encoding="utf-8")
            return [(str(directory),name + ".dist-info")]
        hooks.copy_metadata = metadata_rows
        hooks.collect_data_files = lambda name:[]
        calls = {}
        def analysis(*args, **kwargs):
            calls.update(kwargs)
            return types.SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[])
        with patch.dict(sys.modules, {"PyInstaller.utils.hooks":hooks}):
            exec(compile((ROOT / "packaging/astrodeck.spec").read_text(encoding="utf-8"), "fixture.spec", "exec"),
                 {"SPECPATH":str(self.root / "packaging"), "Analysis":analysis, "PYZ":lambda *a:None, "EXE":lambda *a,**kw:None})
        return calls

    def test_spec_collects_native_module_and_metadata(self):
        calls = self.execute_spec()
        self.assertIn("astrodeck_native", calls["hiddenimports"])
        self.assertIn((str(self.root / "metadata/astrodeck-native.dist-info/METADATA"), "astrodeck-native.dist-info"), calls["datas"])
        self.assertFalse(any(Path(source).name == "direct_url.json" for source,dest in calls["datas"]))

    def test_spec_filters_actual_sdk_and_tile_files(self):
        self.policy["decisions"]["playerone"]["mode"] = "fetch-only"
        self.write_policy()
        datas = self.execute_spec()["datas"]
        sources = {Path(a).name for a,b in datas}
        self.assertNotIn("lib.so.3.2", sources)
        self.assertNotIn("tile.jpg", sources)
        self.assertIn("LICENSE", sources)


class MetadataSelection(unittest.TestCase):
    def test_only_installer_local_url_is_omitted(self):
        with tempfile.TemporaryDirectory(prefix="metadata-test-",dir=ROOT / ".probe/release") as name:
            root=Path(name)
            names=["METADATA","RECORD","direct_url.json","licenses/LICENSE.txt","source/native-source.tar.gz","sboms/native.json","nested/direct_url.json"]
            for relative in names:
                path=root / relative
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(b"synthetic payload")
            actual=metadata_payloads.distributable_metadata([(str(root),"astrodeck-native.dist-info")])
            retained={Path(source).relative_to(root).as_posix() for source,dest in actual}
            self.assertEqual(set(names)-{"direct_url.json"},retained)
            for source,destination in actual:
                relative=Path(source).relative_to(root)
                self.assertEqual((Path("astrodeck-native.dist-info") / relative.parent).as_posix(),destination)
                self.assertEqual(b"synthetic payload",Path(source).read_bytes())


class NativeWheel(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".probe/release"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="native-tests-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        crate = self.root / "native/crates/astrodeck-native"
        crate.mkdir(parents=True)
        (crate / "pyproject.toml").write_text('[project]\nversion="0.1.0"\n', encoding="utf-8")
        (crate / "src").mkdir()
        (crate / "src/lib.rs").write_text("// synthetic source\n", encoding="utf-8")
        (self.root / "server").mkdir()
        (self.root / "server/pyproject.toml").write_text('[project]\nversion="9.9.9"\n', encoding="utf-8")
        from packaging.tags import sys_tags
        platform = next(sys_tags()).platform
        self.path = self.root / ("astrodeck_native-0.1.0-cp311-abi3-" + platform + ".whl")
        self.info = "astrodeck_native-0.1.0.dist-info"
        names = ["MPL-2.0.txt", "THIRD-PARTY-NOTICES.md", "CARGO-NOTICES.txt", "NATIVE-SOURCE.txt"]
        self.entries = {self.info + "/licenses/" + n:b"synthetic notice" for n in names}
        self.entries["sources/native-source.tar.gz"] = b"\x1f\x8bsynthetic compressed archive"
        self.entries.update({self.info + "/METADATA":("Metadata-Version: 2.4\nName: astrodeck-native\nVersion: 0.1.0\n" + "".join("License-File: " + n + "\n" for n in names)).encode(),
                             self.info + "/WHEEL":("Wheel-Version: 1.0\nTag: cp311-abi3-" + platform + "\n").encode(),
                             self.info + "/RECORD":b"", "astrodeck_native/astrodeck_native.pyd":b"synthetic extension"})
        self.write()

    def write(self):
        with zipfile.ZipFile(self.path, "w") as wheel:
            for name, data in self.entries.items(): wheel.writestr(name, data)

    def refresh_record(self):
        rows=[]
        for name,body in sorted(self.entries.items()):
            if name != self.info + "/RECORD":
                digest=base64.urlsafe_b64encode(hashlib.sha256(body).digest()).rstrip(b"=").decode()
                rows.append((name,"sha256="+digest,str(len(body))))
        rows.append((self.info + "/RECORD","",""))
        buffer=io.StringIO(newline="")
        csv.writer(buffer,lineterminator="\n").writerows(rows)
        self.entries[self.info + "/RECORD"]=buffer.getvalue().encode()

    def test_rebuild_recipe_prepares_required_source_data(self):
        recipe=native.native_source_notice(self.root,"a"*40)
        self.assertIn("copy the original accompanying native-source.tar.gz into native/crates/astrodeck-native/sources/native-source.tar.gz",recipe)
        self.assertIn("do not extract this copy",recipe)
        self.assertIn("four accompanying license/notice texts",recipe)
        self.assertIn("python -m maturin build --release --locked",recipe)

    def test_declared_license_files_are_utf8_text(self):
        self.seal()
        entries,info=native.wheel_entries(self.path)
        for name in native.license_texts(entries,info):
            self.assertNotIn(".tar.gz",name)
            entries[name].decode("utf-8",errors="strict")
        self.assertIn(info + "/source/native-source.tar.gz",entries)

    def test_non_utf8_license_file_is_rejected(self):
        self.entries[self.info + "/licenses/MPL-2.0.txt"]=b"\xff\xfe"
        self.write()
        with self.assertRaisesRegex(ValueError,"UTF-8 text"):
            self.seal()

    def test_nul_license_file_is_rejected(self):
        self.entries[self.info + "/licenses/MPL-2.0.txt"]=b"UTF8 but binary\0payload"
        self.write()
        with self.assertRaisesRegex(ValueError,"NUL bytes"):
            self.seal()

    def test_source_archive_cannot_be_a_license(self):
        self.entries[self.info + "/licenses/native-source.tar.gz"]=self.entries.pop("sources/native-source.tar.gz")
        self.entries[self.info + "/METADATA"]+=b"License-File: native-source.tar.gz\n"
        self.write()
        with self.assertRaisesRegex(ValueError,"UTF-8 text|ordinary wheel data"):
            self.seal()

    def test_source_archive_hash_is_verified(self):
        self.seal()
        self.entries,_=native.wheel_entries(self.path)
        self.entries[self.info + "/source/native-source.tar.gz"]=b"changed"
        self.refresh_record()
        self.write()
        with self.assertRaisesRegex(ValueError,"source archive differs"):
            native.validate_wheel(self.path,self.root)

    def seal(self):
        return native.seal_wheel(self.path, self.root, "a" * 40)

    def test_sealed_wheel_validates_record_source_and_notices(self):
        self.seal()
        self.assertEqual("0.1.0", native.validate_wheel(self.path, self.root)["native_version"])

    def test_sbom_paths_normalize_without_breaking_graph(self):
        reference="path+file:///C:/synthetic-private/native/crates/astro-star#0.1.0"
        value={"components":[{"bom-ref":reference,"purl":"pkg:cargo/astro-star@0.1.0?download_url=file%3A%2F%2FC%3A%5Csynthetic-private%5Cnative%5Ccrates%5Castro-star","licenses":[{"license":{"id":"MPL-2.0"}}]}],"dependencies":[{"ref":reference,"dependsOn":[reference]}]}
        result=native.normalize_sbom(value,"b"*64)
        ref=result["components"][0]["bom-ref"]
        self.assertTrue(ref.startswith("urn:astrodeck:source:sha256:"))
        self.assertEqual(ref,result["dependencies"][0]["ref"])
        self.assertEqual([ref],result["dependencies"][0]["dependsOn"])
        self.assertNotIn("synthetic-private",json.dumps(result))
        self.assertEqual(value["components"][0]["licenses"],result["components"][0]["licenses"])

    def test_relative_sbom_source_hint_is_preserved(self):
        value="pkg:cargo/astro-star@0.1.0?download_url=file%3A%2F%2F..%5Castro-star"
        self.assertEqual(value,native.normalize_sbom(value,"b"*64))

    def test_tampered_sbom_refused(self):
        name=self.info + "/sboms/native.json"
        self.entries[name]=b'{"bomFormat":"CycloneDX"}'
        self.write()
        self.seal()
        self.entries,_=native.wheel_entries(self.path)
        self.entries[name]=b'{"bomFormat":"changed"}'
        self.refresh_record()
        self.write()
        with self.assertRaisesRegex(ValueError,"SBOM differs"):
            native.validate_wheel(self.path,self.root)

    def test_non_abi3_wheel_refused(self):
        self.entries[self.info + "/WHEEL"] = b"Tag: cp312-cp312-win_amd64\n"
        self.write()
        with self.assertRaisesRegex(ValueError, "ABI3"):
            self.seal()

    def test_missing_native_notices_refused(self):
        del self.entries[self.info + "/licenses/MPL-2.0.txt"]
        self.write()
        with self.assertRaisesRegex(ValueError, "actual license/source"):
            self.seal()

    def test_changed_source_refused(self):
        self.seal()
        (self.root / "native/crates/astrodeck-native/src/lib.rs").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "current native source"):
            native.validate_wheel(self.path, self.root)

    def test_changed_release_version_refused(self):
        self.seal()
        (self.root / "server/pyproject.toml").write_text('[project]\nversion="10.0.0"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "app version does not match"):
            native.validate_wheel(self.path, self.root)

    def test_source_tar_revision_works_without_git(self):
        (self.root / "manifest.json").write_text(json.dumps({"source_commit":"c"*40}), encoding="utf-8")
        self.assertEqual("c"*40, native.source_revision(self.root))

    def test_source_tar_cannot_invent_revision(self):
        (self.root / "manifest.json").write_text(json.dumps({"source_commit":"main"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"full Git object id"):
            native.source_revision(self.root)

    def test_license_declarations_must_cover_notices(self):
        self.seal()
        self.entries, _ = native.wheel_entries(self.path)
        self.entries[self.info + "/METADATA"] = b"Name: astrodeck-native\nVersion: 0.1.0\n"
        self.refresh_record()
        self.write()
        with self.assertRaisesRegex(ValueError,"missing from METADATA"):
            native.validate_wheel(self.path,self.root)

    def test_tampered_extension_refused(self):
        self.seal()
        self.entries, _ = native.wheel_entries(self.path)
        self.entries["astrodeck_native/astrodeck_native.pyd"] = b"changed"
        self.refresh_record()
        self.write()
        with self.assertRaisesRegex(ValueError, "extension differs"):
            native.validate_wheel(self.path, self.root)

    def test_tampered_notice_refused(self):
        self.seal()
        self.entries, _ = native.wheel_entries(self.path)
        self.entries[self.info + "/licenses/MPL-2.0.txt"] = b"changed"
        self.refresh_record()
        self.write()
        with self.assertRaisesRegex(ValueError, "license/source files differ"):
            native.validate_wheel(self.path, self.root)

    def test_record_digest_is_verified(self):
        self.entries["ordinary.txt"]=b"original"
        self.write()
        self.seal()
        self.entries,_=native.wheel_entries(self.path)
        self.entries["ordinary.txt"]=b"changed"
        self.write()
        with self.assertRaisesRegex(ValueError,"RECORD digest mismatch"):
            native.validate_wheel(self.path,self.root)

    def test_record_must_cover_payload(self):
        self.seal()
        self.entries, _ = native.wheel_entries(self.path)
        self.entries["extra.bin"] = b"unrecorded"
        self.write()
        with self.assertRaisesRegex(ValueError, "RECORD does not cover"):
            native.validate_wheel(self.path, self.root)


class NativeProbe(unittest.TestCase):
    def fixture(self, stars=(), count=0, version="0.1.0"):
        native_module = types.SimpleNamespace(__version__=version, detect_and_measure=lambda frame:(stars,{"star_count":count}))
        numpy_module = types.SimpleNamespace(zeros=lambda *a,**kw:"synthetic blank frame", uint16="uint16")
        dist = types.SimpleNamespace(version="0.1.0", read_text=lambda _:json.dumps({"native_version":"0.1.0","source_commit":"a"*40,"native_source_sha256":"b"*64}))
        return patch.dict(sys.modules,{"astrodeck_native":native_module,"numpy":numpy_module}), patch.object(probe.metadata,"distribution",return_value=dist)

    def test_probe_calls_detector_and_checks_result(self):
        modules, dist = self.fixture()
        with modules, dist:
            self.assertEqual(0, probe.probe_native()["star_count"])

    def test_incorrect_detector_result_refused(self):
        modules, dist = self.fixture(stars=[{}],count=1)
        with modules, dist, self.assertRaisesRegex(RuntimeError,"blank-frame result"):
            probe.probe_native()

    def test_module_metadata_mismatch_refused(self):
        modules, dist = self.fixture(version="0.0.1")
        with modules, dist, self.assertRaisesRegex(RuntimeError,"versions differ"):
            probe.probe_native()

    def test_entry_probe_never_initializes_application_state(self):
        with patch.object(entry.sys,"argv",["astrodeck","--packaging-probe"]), patch.object(entry.multiprocessing,"freeze_support"), patch.object(entry,"default_state_dir") as state, patch.object(probe,"main",return_value=0) as run:
            with self.assertRaises(SystemExit) as outcome:
                entry.main()
            self.assertEqual(0,outcome.exception.code)
            run.assert_called_once()
            state.assert_not_called()

    def test_probe_errors_do_not_echo_exception_text(self):
        with patch.object(probe,"probe_native",side_effect=RuntimeError("private-canary")), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(1,probe.main())
        self.assertNotIn("private-canary",output.getvalue())


class BinaryOrchestration(unittest.TestCase):
    def test_skip_server_install_still_installs_native_before_freeze(self):
        calls = []
        exe = types.SimpleNamespace(stat=lambda:types.SimpleNamespace(st_size=1))
        with ExitStack() as stack:
            stack.enter_context(patch.object(binary.sys,"argv",["build_binary.py","--skip-ui","--skip-install","--no-smoke"]))
            stack.enter_context(patch.object(Path,"is_file",return_value=True))
            stack.enter_context(patch.object(binary,"install_server",side_effect=lambda:calls.append("server")))
            stack.enter_context(patch.object(binary,"install_native",side_effect=lambda wheel:calls.append("native")))
            stack.enter_context(patch.object(binary,"build_binary",side_effect=lambda:calls.append("freeze") or exe))
            stack.enter_context(patch.object(binary,"native_smoke",side_effect=lambda exe:calls.append("native-probe")))
            stack.enter_context(patch.object(binary,"smoke",side_effect=lambda exe:calls.append("server-smoke")))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0,binary.main())
        self.assertEqual(["native","freeze","native-probe"], calls)

    def test_wrong_frozen_source_refused(self):
        with patch.object(binary.subprocess,"run",return_value=subprocess.CompletedProcess([],0,json.dumps({"native_available":True,"native_source_sha256":"stale"}),"")), patch.object(binary.build_native,"native_source_digest",return_value="current"):
            with self.assertRaisesRegex(SystemExit,"does not match this source"):
                binary.native_smoke(Path("synthetic.exe"))

    def test_native_failure_blocks_freeze(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(binary.sys,"argv",["build_binary.py","--skip-ui","--skip-install","--no-smoke"]))
            stack.enter_context(patch.object(Path,"is_file",return_value=True))
            stack.enter_context(patch.object(binary,"install_native",side_effect=ValueError("bad wheel")))
            freeze = stack.enter_context(patch.object(binary,"build_binary"))
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError,"bad wheel"):
                binary.main()
            freeze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
