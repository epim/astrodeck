# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The container image inventory gate: audit_artifact.py --image (#655 part B, #798, #1123).

The gate reads an image archive without running it, merges its layers the way a
container runtime would (whiteouts included), and lists what the image filesystem
holds: the native wheel, every installed Python distribution with its licence, and
the base-image facts the wheel's compatibility depends on. No Docker daemon is
needed: the fixtures are small OCI layouts, Docker archives and flat root
filesystem exports built here, with the layer listing a real Debian 13 image has
(trixie, libc6 2.41) reduced to the files the gate reads.

What a daemon-backed run adds, and this file cannot: that `docker create` plus
`docker export` of the real pushed image yields the tar this gate reads. The
release workflow does that on a tag.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools/licence"))
import audit_artifact as gate

LICENCE_TEXT = "Permission is hereby granted, free of charge, to any person obtaining a copy of this fixture. " * 4
LICENCE_KEY = hashlib.sha256(LICENCE_TEXT.encode("utf-8")).hexdigest()[:16]
SITE = "opt/venv/lib/python3.12/site-packages/"
OS_RELEASE = b'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nVERSION_ID="13"\nVERSION_CODENAME=trixie\nID=debian\n'
DPKG_STATUS = (b"Package: libc6\nStatus: install ok installed\nVersion: 2.41-12+deb13u3\nArchitecture: amd64\n\n"
               b"Package: libusb-1.0-0\nStatus: install ok installed\nVersion: 2:1.0.28-1\nArchitecture: amd64\n")
NATIVE_TAG = "cp311-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64"


def metadata(name, version, *, expression=None, licence=None, classifier=None) -> bytes:
    lines = ["Metadata-Version: 2.4", "Name: " + name, "Version: " + version]
    if expression:
        lines.append("License-Expression: " + expression)
    if licence:
        lines.append("License: " + licence)
    if classifier:
        lines.append("Classifier: License :: OSI Approved :: " + classifier)
    return ("\n".join(lines) + "\n").encode("utf-8")


def wheel_file(tag=NATIVE_TAG) -> bytes:
    return ("Wheel-Version: 1.0\nGenerator: maturin (1.9.0)\nRoot-Is-Purelib: false\nTag: " + tag + "\n").encode("utf-8")


def credit(name, spdx="MIT"):
    return {"name": name, "version": "1", "spdx": spdx, "requires": ["notice"], "texts": [{"hash": LICENCE_KEY}]}


@pytest.fixture
def credits():
    entries = [credit("fastapi"), credit("PyYAML"), credit("pydantic_core")]
    return {"groups": [{"id": "python", "entries": entries}], "licenses": {LICENCE_KEY: LICENCE_TEXT}}


def good_files() -> dict[str, bytes]:
    """The reduced filesystem of a correct release image."""
    return {
        "etc/os-release": OS_RELEASE,
        "var/lib/dpkg/status": DPKG_STATUS,
        "opt/native_probe.py": b"# probe\n",
        SITE + "fastapi-0.115.0.dist-info/METADATA": metadata("fastapi", "0.115.0", expression="MIT"),
        SITE + "PyYAML-6.0.2.dist-info/METADATA": metadata("PyYAML", "6.0.2", licence="MIT License"),
        SITE + "pydantic_core-2.27.0.dist-info/METADATA": metadata("pydantic_core", "2.27.0", classifier="MIT License"),
        SITE + "astrodeck-0.3.46.dist-info/METADATA": metadata("astrodeck", "0.3.46", expression="Apache-2.0"),
        SITE + "pip-25.0.dist-info/METADATA": metadata("pip", "25.0", expression="MIT"),
        SITE + "astrodeck_native-0.1.0.dist-info/METADATA": metadata("astrodeck-native", "0.1.0", expression="Apache-2.0"),
        SITE + "astrodeck_native-0.1.0.dist-info/WHEEL": wheel_file(),
        SITE + "astrodeck_native/__init__.py": b"from .astrodeck_native import *\n",
        SITE + "astrodeck_native/astrodeck_native.abi3.so": b"\x7fELF fixture",
    }


def layer_bytes(files: dict[str, bytes | None], *, gz=True) -> bytes:
    """A layer tar. A None value is a whiteout of that path."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in files.items():
            if data is None:
                parent, _, leaf = name.rpartition("/")
                info = tarfile.TarInfo((parent + "/" if parent else "") + ".wh." + leaf)
                data = b""
            else:
                info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return gzip.compress(buffer.getvalue()) if gz else buffer.getvalue()


def add(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def oci_image(path: Path, layers: list[dict], *, architecture="amd64") -> Path:
    """An OCI image layout: index.json -> manifest -> config + layer blobs."""
    blobs = {}

    def put(data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        blobs["blobs/sha256/" + digest] = data
        return "sha256:" + digest

    layer_digests = [put(layer_bytes(files)) for files in layers]
    config = put(json.dumps({"architecture": architecture, "os": "linux"}).encode("utf-8"))
    manifest = put(json.dumps({
        "schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": config},
        "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": d} for d in layer_digests],
    }).encode("utf-8"))
    index = json.dumps({"schemaVersion": 2, "manifests": [
        {"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": manifest}]}).encode("utf-8")
    with tarfile.open(path, "w") as archive:
        add(archive, "oci-layout", b'{"imageLayoutVersion": "1.0.0"}')
        add(archive, "index.json", index)
        for name, data in blobs.items():
            add(archive, name, data)
    return path


def docker_archive(path: Path, layers: list[dict]) -> Path:
    """A `docker save` archive: manifest.json lists layer tar files in order."""
    with tarfile.open(path, "w") as archive:
        names = []
        for number, files in enumerate(layers):
            names.append("layer%d/layer.tar" % number)
            add(archive, names[-1], layer_bytes(files, gz=False))
        add(archive, "manifest.json", json.dumps([{"Config": "config.json", "RepoTags": None, "Layers": names}]).encode("utf-8"))
    return path


def flat_export(path: Path, files: dict[str, bytes]) -> Path:
    """What `docker export` writes: one merged root filesystem, no layers."""
    path.write_bytes(layer_bytes(files, gz=False))
    return path


def audit(path, credits):
    return gate.audit_image(path, credits)


def text(findings) -> str:
    return "\n".join(findings)


def test_a_correct_oci_image_passes_and_the_report_lists_what_it_holds(tmp_path, credits):
    findings, report = audit(oci_image(tmp_path / "i.tar", [good_files()]), credits)

    assert findings == []
    assert report["native_wheel"] == {
        "name": "astrodeck-native", "version": "0.1.0", "tags": [NATIVE_TAG],
        "extension_modules": [SITE + "astrodeck_native/astrodeck_native.abi3.so"]}
    packages = {row["name"]: row for row in report["python_packages"]}
    assert sorted(packages) == ["astrodeck", "astrodeck-native", "fastapi", "pip", "pydantic-core", "pyyaml"]
    assert packages["fastapi"]["licence"] == "MIT" and packages["fastapi"]["version"] == "0.115.0"
    assert packages["pyyaml"]["licence"] == "MIT License"
    assert packages["pydantic-core"]["licence"] == "MIT License"
    assert report["architecture"] == "amd64"


def test_the_trixie_and_glibc_facts_are_recorded_in_the_report(tmp_path, credits):
    """#798: the pinned base is Debian 13 with glibc 2.41, not bookworm with 2.36."""
    _, report = audit(oci_image(tmp_path / "i.tar", [good_files()]), credits)

    assert report["base"] == {"id": "debian", "version_id": "13", "codename": "trixie", "libc6": "2.41-12+deb13u3"}


def test_docker_archive_and_flat_export_read_the_same_filesystem(tmp_path, credits):
    for path in (docker_archive(tmp_path / "d.tar", [good_files()]), flat_export(tmp_path / "f.tar", good_files())):
        findings, report = audit(path, credits)
        assert findings == [], path.name
        assert report["native_wheel"]["version"] == "0.1.0", path.name
        assert report["base"]["codename"] == "trixie", path.name


def test_layers_are_merged_in_order_so_a_later_layer_overrides_an_earlier_one(tmp_path, credits):
    older = good_files()
    older[SITE + "fastapi-0.115.0.dist-info/METADATA"] = metadata("fastapi", "0.115.0", expression="GPL-3.0-only")
    newer = {SITE + "fastapi-0.115.0.dist-info/METADATA": metadata("fastapi", "0.115.0", expression="MIT")}

    findings, report = audit(oci_image(tmp_path / "i.tar", [older, newer]), credits)

    assert findings == []
    assert {row["name"]: row for row in report["python_packages"]}["fastapi"]["licence"] == "MIT"


def test_a_whiteout_in_a_later_layer_removes_the_native_wheel(tmp_path, credits):
    """The image a runtime would run has no engine even though an earlier layer installed one."""
    removal = {SITE + "astrodeck_native-0.1.0.dist-info/METADATA": None,
               SITE + "astrodeck_native-0.1.0.dist-info/WHEEL": None}

    findings, report = audit(oci_image(tmp_path / "i.tar", [good_files(), removal]), credits)

    assert "native wheel is absent" in text(findings)
    assert report["native_wheel"] is None


def test_an_opaque_directory_marker_hides_everything_below_it_from_earlier_layers(tmp_path, credits):
    hide = {SITE + "astrodeck_native/.wh..wh..opq": b""}
    older = good_files()
    
    older.pop(SITE + "astrodeck_native/astrodeck_native.abi3.so")
    older[SITE + "astrodeck_native/astrodeck_native.abi3.so"] = b"\x7fELF"

    findings, _ = audit(oci_image(tmp_path / "i.tar", [older, hide]), credits)

    assert "no compiled extension module" in text(findings)


def test_an_image_without_the_native_wheel_fails_and_says_so(tmp_path, credits):
    files = {n: d for n, d in good_files().items() if "astrodeck_native" not in n}

    findings, report = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert any("native wheel is absent" in f for f in findings), findings
    assert report["native_wheel"] is None


def test_a_wheel_without_its_compiled_module_fails(tmp_path, credits):
    files = {n: d for n, d in good_files().items() if not n.endswith(".so")}

    findings, _ = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert "no compiled extension module" in text(findings)


def test_the_probe_must_be_baked_into_the_image(tmp_path, credits):
    files = good_files()
    del files["opt/native_probe.py"]

    findings, _ = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert "the native probe is not in the image" in text(findings)


def test_an_installed_package_with_no_generated_credit_fails(tmp_path, credits):
    files = good_files()
    files[SITE + "mystery-1.0.dist-info/METADATA"] = metadata("mystery", "1.0", expression="MIT")

    findings, _ = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert "mystery: component has no generated credit" in text(findings)


def test_an_installed_package_that_states_no_licence_fails(tmp_path, credits):
    files = good_files()
    files[SITE + "fastapi-0.115.0.dist-info/METADATA"] = metadata("fastapi", "0.115.0")

    findings, _ = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert "fastapi: the installed metadata states no licence" in text(findings)


def test_the_first_party_server_package_is_listed_but_needs_no_licence_metadata(tmp_path, credits):
    """server/pyproject.toml declares no licence, so the installed astrodeck metadata has none; the
    source-tar gate is what accounts for first-party code, and the image gate must not fail every image on it."""
    files = good_files()
    files[SITE + "astrodeck-0.3.46.dist-info/METADATA"] = metadata("astrodeck", "0.3.46")

    findings, report = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert findings == []
    assert "astrodeck" in {row["name"] for row in report["python_packages"]}


def test_installer_tooling_needs_a_licence_but_no_credit(tmp_path, credits):
    files = good_files()
    files[SITE + "pip-25.0.dist-info/METADATA"] = metadata("pip", "25.0")

    findings, _ = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert text(findings) == "pip: the installed metadata states no licence"


def test_a_credit_whose_licence_needs_owner_clearance_fails(tmp_path, credits):
    credits["groups"][0]["entries"][0] = credit("fastapi", "GPL-3.0-only")

    findings, _ = audit(oci_image(tmp_path / "i.tar", [good_files()]), credits)

    assert "fastapi: " in text(findings) and "licence" in text(findings)


def test_a_moved_base_image_fails_until_the_wheel_compatibility_is_reviewed_again(tmp_path, credits):
    """#798: a bookworm or glibc 2.36 base would change which manylinux wheels pip accepts."""
    files = good_files()
    files["etc/os-release"] = b'VERSION_ID="12"\nVERSION_CODENAME=bookworm\nID=debian\n'
    files["var/lib/dpkg/status"] = DPKG_STATUS.replace(b"2.41-12+deb13u3", b"2.36-9+deb12u10")

    findings, report = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert "base image is bookworm with libc6 2.36-9+deb12u10, not the reviewed trixie with glibc 2.41" in text(findings)
    assert report["base"]["codename"] == "bookworm"


def test_a_missing_base_record_fails(tmp_path, credits):
    files = good_files()
    del files["var/lib/dpkg/status"]

    findings, report = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert "the image does not record its libc6 version" in text(findings)
    assert report["base"]["libc6"] is None


def test_a_wheel_tagged_for_a_newer_glibc_than_the_base_fails(tmp_path, credits):
    files = good_files()
    files[SITE + "astrodeck_native-0.1.0.dist-info/WHEEL"] = wheel_file("cp311-abi3-manylinux_2_42_x86_64")

    findings, _ = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert "native wheel tag manylinux_2_42_x86_64 needs glibc 2.42, the base has 2.41" in text(findings)


def test_a_wheel_for_another_operating_system_fails(tmp_path, credits):
    files = good_files()
    files[SITE + "astrodeck_native-0.1.0.dist-info/WHEEL"] = wheel_file("cp311-abi3-win_amd64")

    findings, _ = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert "native wheel has no linux platform tag" in text(findings)


def test_two_native_wheels_are_ambiguous_and_fail(tmp_path, credits):
    files = good_files()
    files[SITE + "astrodeck_native-0.2.0.dist-info/METADATA"] = metadata("astrodeck-native", "0.2.0", expression="Apache-2.0")
    files[SITE + "astrodeck_native-0.2.0.dist-info/WHEEL"] = wheel_file()

    findings, _ = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert "more than one native wheel is installed" in text(findings)


def test_an_unsafe_layer_path_fails_instead_of_being_read(tmp_path, credits):
    files = good_files()
    files["../escape/METADATA"] = b"x"

    findings, _ = audit(oci_image(tmp_path / "i.tar", [files]), credits)

    assert "archive contains an unsafe member path" in text(findings)


def test_an_archive_that_is_not_an_image_fails_loudly(tmp_path, credits):
    path = tmp_path / "i.tar"
    with tarfile.open(path, "w") as archive:
        add(archive, "index.json", json.dumps({"manifests": []}).encode("utf-8"))

    with pytest.raises(ValueError, match="holds no image manifest"):
        audit(path, credits)


def test_an_oci_index_of_several_platforms_is_refused(tmp_path, credits):
    path = tmp_path / "i.tar"
    with tarfile.open(path, "w") as archive:
        add(archive, "index.json", json.dumps({"manifests": [
            {"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": "sha256:" + "a" * 64},
            {"mediaType": "application/vnd.oci.image.manifest.v1+json", "digest": "sha256:" + "b" * 64}]}).encode("utf-8"))

    with pytest.raises(ValueError, match="several images"):
        audit(path, credits)


def run_cli(tmp_path, image, extra=()):
    report = tmp_path / "report.json"
    code = gate.main(["--image", str(image), "--report", str(report), *extra])
    return code, json.loads(report.read_text(encoding="utf-8"))


def test_the_command_line_writes_a_report_and_exits_zero_for_a_correct_image(tmp_path, credits, capsys):
    credits_path = tmp_path / "credits.json"
    credits_path.write_text(json.dumps(credits), encoding="utf-8")
    image = oci_image(tmp_path / "i.tar", [good_files()])

    code, report = run_cli(tmp_path, image, ["--credits", str(credits_path)])

    assert code == 0
    assert report["cleared"] is True and report["findings"] == []
    assert report["artifact_sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert "Image gate: PASS" in capsys.readouterr().out


def test_the_command_line_exits_nonzero_and_prints_each_finding_for_a_bad_image(tmp_path, credits, capsys):
    credits_path = tmp_path / "credits.json"
    credits_path.write_text(json.dumps(credits), encoding="utf-8")
    files = {n: d for n, d in good_files().items() if "astrodeck_native" not in n}

    code, report = run_cli(tmp_path, oci_image(tmp_path / "i.tar", [files]), ["--credits", str(credits_path)])

    assert code == 1
    assert report["cleared"] is False
    out = capsys.readouterr().out
    assert "native wheel is absent" in out and "Image gate: FAIL" in out


def test_image_mode_is_exclusive_with_the_archive_and_build_modes(tmp_path):
    with pytest.raises(SystemExit):
        gate.main(["--image", str(tmp_path / "i.tar"), "--build"])
    with pytest.raises(SystemExit):
        gate.main(["--image", str(tmp_path / "i.tar"), "--archive", str(tmp_path / "a.tar.gz")])
