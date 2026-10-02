# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Artifact regressions use real archive bytes and the real release packager.

Named mutants and exact failing assertions are recorded by
tools/licence/mutate_gates.py; every source byte is restored afterward.
"""
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools/licence"))
import audit_artifact as gate


@pytest.fixture
def sample():
    notice = b"Copyright Fixture. Permission to redistribute with this notice. " * 5
    credits = {"groups": [{"entries": [{"name": "Fixture SDK", "spdx": "MIT",
                "version": "1", "requires": ["notice"],
                "texts": [{"hash": gate.sha256(notice)[:16]}]}]}],
               "licenses": {gate.sha256(notice)[:16]: notice.decode()}}
    raw = json.dumps(credits).encode()
    files = {
        "server/astrodeck/__init__.py": b'"""Fixture."""\n',
        "server/pyproject.toml": b'[project]\nname="fixture"\nversion="1"\n',
        "server/astrodeck/vendor/fixture/test.dll": b"MZ\0test-library",
        "server/astrodeck/vendor/zwo/LICENSE.txt": notice,
        "ui/dist/index.html": b"<!doctype html><title>Fixture</title>",
        "ui/dist/assets/credits.js": raw,
    }
    rules = {"assets": [{
        "path": "server/astrodeck/vendor/fixture/test.dll",
        "component": "Fixture SDK", "credit": "Fixture SDK", "spdx": "MIT",
        "sha256": gate.sha256(files["server/astrodeck/vendor/fixture/test.dll"]),
        "notices": {"server/astrodeck/vendor/zwo/LICENSE.txt": gate.sha256(notice)},
    }]}
    rules["reviewed_sources"] = [
        {"path": name, "sha256": gate.source_sha256(data), "component": "Fixture source"}
        for name, data in files.items() if name.endswith((".py", ".toml"))
    ]
    build = {"credits_sha256": gate.sha256(raw), "files": [
        {"path": name, "sha256": gate.sha256(data),
         "inputs": ["repo:ui/src/credits.generated.json"] if name.endswith("credits.js") else ["repo:ui/index.html"]}
        for name, data in files.items() if name.startswith("ui/")]}
    return files, rules, credits, build, raw, {"packages": {}}


def findings(sample):
    return "\n".join(gate.review(*sample)[0])


def test_reviewed_bytes_and_companion_notice_pass(sample):
    assert findings(sample) == ""


def test_unregistered_binary_in_real_built_tarball_is_rejected(sample, tmp_path):
    # Inject into the package BEFORE the actual packager runs, not into a
    # hand-written inventory. It really reaches the resulting archive.
    (tmp_path / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/build_release.py", tmp_path / "scripts/build_release.py")
    (tmp_path / "packaging").mkdir()
    for name in ("distribution_policy.py", "distribution-policy.json"):
        shutil.copy2(ROOT / "packaging" / name, tmp_path / "packaging" / name)
    for name, data in sample[0].items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    rogue = tmp_path / "server/astrodeck/vendor/rogue/unreviewed.dll"
    rogue.parent.mkdir(parents=True)
    rogue.write_bytes(b"MZ\0unregistered-third-party")
    archive = gate.build_tarball(tmp_path, tmp_path / ".probe/output")
    actual, errors = gate.read_archive(archive)
    assert errors == []
    assert "server/astrodeck/vendor/rogue/unreviewed.dll" in actual
    bad = (actual, *sample[1:])
    assert "unaccounted binary/data member" in findings(bad)


def test_changed_known_binary_is_not_covered_by_vendor_name(sample):
    sample[0]["server/astrodeck/vendor/fixture/test.dll"] = b"MZ\0changed"
    assert "asset bytes differ" in findings(sample)


def test_companion_notice_must_travel_in_archive(sample):
    del sample[0]["server/astrodeck/vendor/zwo/LICENSE.txt"]
    assert "required companion notice is absent" in findings(sample)


def test_owner_needed_asset_cannot_be_cleared_by_registration(sample):
    sample[1]["assets"][0]["blocked"] = True
    assert "requires an owner decision" in findings(sample)


@pytest.mark.parametrize("licence", ["GPL-3.0-only", "LGPL-2.1-only", "AGPL-3.0-only", "SSPL-1.0", "Not-A-Licence"])
def test_disallowed_licence_fails_even_for_known_asset(sample, licence):
    sample[1]["assets"][0]["spdx"] = licence
    assert "outside the allowed policy" in findings(sample)


def test_stale_generated_credits_fail(sample):
    stale = (*sample[:4], sample[4] + b" ", sample[5])
    assert "stale generated credits" in findings(stale)


def test_missing_compiled_credits_fail(sample):
    del sample[0]["ui/dist/assets/credits.js"]
    assert "missing the exact compiled credits" in findings(sample)


def test_unknown_ui_output_fails(sample):
    sample[0]["ui/dist/assets/foreign.js"] = b"foreignRuntime()"
    assert "no matching fresh build evidence" in findings(sample)


def test_renaming_binary_to_python_does_not_bypass_gate(sample):
    sample[0]["server/astrodeck/injected.py"] = b"MZ\0binary"
    assert "binary bytes masquerade as text" in findings(sample)


def test_mirrored_webui_cannot_hide_unknown_data(sample):
    sample[0]["server/astrodeck/webui/foreign.dat"] = b"foreign-data"
    assert "unaccounted binary/data member" in findings(sample)


def test_runtime_helper_requires_credit_even_for_dev_dependency(sample):
    sample[3]["files"][0]["inputs"] = ["npm:vite/preload-helper.js"]
    sample[5]["packages"]["node_modules/vite"] = {"version": "1", "license": "MIT", "dev": True}
    assert "UI component vite: missing lockfile or generated credit" in findings(sample)


def test_archive_paths_links_and_duplicates_fail(tmp_path):
    path = tmp_path / "bad.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name in ["release/../outside", "release/same", "release/same"]:
            member = tarfile.TarInfo(name)
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        link = tarfile.TarInfo("release/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "outside"
        archive.addfile(link)
    _, errors = gate.read_archive(path)
    assert any("unsafe member path" in x for x in errors)
    assert any("duplicate" in x for x in errors)
    assert any("link or non-file" in x for x in errors)


def test_reviewed_lgpl_still_needs_owner_clearance(sample):
    sample[1]["assets"][0]["spdx"] = "LGPL-3.0-or-later"
    assert "reviewed licence still needs owner clearance" in findings(sample)


def test_ascii_table_disguised_as_source_needs_review(sample):
    sample[0]["server/astrodeck/catalog/unaccounted_survey.py"] = b"SURVEY = [(1.0, 2.0)]"
    assert "source or embedded data has not been reviewed" in findings(sample)


def test_changed_reviewed_source_needs_new_provenance_review(sample):
    sample[0]["server/astrodeck/__init__.py"] += b"DATA = [(1.0, 2.0)]\n"
    assert "source or embedded data has not been reviewed" in findings(sample)


def test_credit_name_alone_does_not_satisfy_asset_notice(sample):
    sample[2]["groups"][0]["entries"][0]["texts"] = []
    assert "required licence text is absent" in findings(sample)


@pytest.mark.parametrize("body", ["", "A stub", "Forged copyright notice. " * 20])
def test_asset_notice_body_must_resolve_and_match_hash(sample, body):
    key = next(iter(sample[2]["licenses"]))
    sample[2]["licenses"][key] = body
    assert "licence text reference is missing, truncated or corrupt" in findings(sample)


def test_asset_credit_must_match_reviewed_licence(sample):
    sample[2]["groups"][0]["entries"][0]["spdx"] = "Apache-2.0"
    assert "credit licence differs from reviewed provenance" in findings(sample)


def test_unknown_virtual_runtime_cannot_inherit_vite_licence(sample):
    sample[3]["files"][0]["inputs"] = ["build-helper:foreign-plugin-runtime"]
    assert "unclassified build input" in findings(sample)
