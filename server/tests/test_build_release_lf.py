# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A bundle is the same bytes whichever platform built it (#1091).

scripts/build_release.py writes manifest.json and the ASTAP NOTICE.txt with
Path.write_text. Without newline="\\n" that call translates every line feed to
CRLF on Windows, so a bundle built locally on Windows differs from a CI build of
the same commit and its SHA-256 can never be reproduced. The release workflow's
selftest job runs test_build_release*.py on windows-latest, which is where this
file can fail; on Linux it guards the same contract without being able to see
the translation.
"""
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_release  # noqa: E402


def _fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "server" / "astrodeck" / "catalog").mkdir(parents=True)
    (repo / "server" / "astrodeck" / "__init__.py").write_bytes(b"")
    (repo / "server" / "pyproject.toml").write_bytes(b"[project]\nname = 'astrodeck'\n")
    return repo


def _fake_astap(tmp_path: Path) -> Path:
    directory = tmp_path / "assets" / "astap"
    directory.mkdir(parents=True)
    (directory / "astap").write_bytes(b"ELF")
    (directory / "d05.290").write_bytes(b"db")
    return directory


def _bundle_members(tarball: Path) -> dict[str, bytes]:
    with tarfile.open(tarball, "r:gz") as archive:
        return {member.name: archive.extractfile(member).read()
                for member in archive if member.isfile()}


def test_no_text_member_of_the_bundle_has_a_carriage_return(tmp_path):
    out = tmp_path / "dist"
    tarball = build_release.build("9.9.9", _fake_repo(tmp_path), out, astap_dir=_fake_astap(tmp_path))

    members = _bundle_members(tarball)
    text = {name: data for name, data in members.items()
            if name.endswith(("manifest.json", "NOTICE.txt", ".py", ".toml"))}
    assert "astrodeck-9.9.9/manifest.json" in text
    assert "astrodeck-9.9.9/server/astrodeck/vendor/astap/NOTICE.txt" in text
    crlf = sorted(name for name, data in text.items() if b"\r" in data)
    assert crlf == [], "text members written with the platform newline: %s" % crlf


def test_manifest_json_is_lf_terminated_utf8_that_parses(tmp_path):
    out = tmp_path / "dist"
    build_release.build("9.9.9", _fake_repo(tmp_path), out)

    raw = (out / "astrodeck-9.9.9" / "manifest.json").read_bytes()
    assert raw.endswith(b"}\n")
    assert b"\r" not in raw
    assert json.loads(raw.decode("utf-8"))["version"] == "9.9.9"
