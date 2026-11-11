# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 10.6 (#1154): the server package declares its licence, and the artifact audit fails
on a package of ours that does not."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools/licence"))
sys.path.append(str(ROOT / "server/tests"))
import audit_artifact as gate
from test_wp18_image_gate import SITE, credits, good_files, metadata, oci_image  # noqa: F401


def project_table() -> dict:
    return tomllib.loads((ROOT / "server/pyproject.toml").read_text(encoding="utf-8"))["project"]


def installed_metadata_of_the_server() -> bytes:
    """The METADATA a build backend writes for the real pyproject: a PEP 639 string becomes License-Expression."""
    table = project_table()
    return metadata(table["name"], table["version"], expression=table.get("license"))


def test_the_first_party_server_package_without_a_licence_is_a_finding(tmp_path, credits):
    files = good_files()
    files[SITE + "astrodeck-0.3.46.dist-info/METADATA"] = metadata("astrodeck", "0.3.46")

    findings, _ = gate.audit_image(oci_image(tmp_path / "i.tar", [files]), credits)

    assert findings == ["astrodeck: the installed metadata states no licence"]


def test_the_first_party_native_package_without_a_licence_is_a_finding(tmp_path, credits):
    files = good_files()
    files[SITE + "astrodeck_native-0.1.0.dist-info/METADATA"] = metadata("astrodeck-native", "0.1.0")

    findings, _ = gate.audit_image(oci_image(tmp_path / "i.tar", [files]), credits)

    assert findings == ["astrodeck-native: the installed metadata states no licence"]


def test_the_server_pyproject_declares_the_licence_the_repository_states():
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in licence and "Version 2.0, January 2004" in licence

    assert project_table().get("license") == "Apache-2.0"


def test_an_image_built_from_the_real_server_pyproject_passes_and_reports_the_licence(tmp_path, credits):
    files = good_files()
    files[SITE + "astrodeck-0.3.46.dist-info/METADATA"] = installed_metadata_of_the_server()

    findings, report = gate.audit_image(oci_image(tmp_path / "i.tar", [files]), credits)

    assert findings == []
    row = {r["name"]: r for r in report["python_packages"]}["astrodeck"]
    assert row["licence"] == "Apache-2.0"


# --- #1159: records that describe a ruled decision match main ---

def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_release_notes_state_the_wcslib_ruling_not_a_pending_decision():
    import json

    policy = json.loads(read("packaging/distribution-policy.json"))
    assert policy["decisions"]["wcslib"]["mode"] == "not-shipped"

    notes = read("packaging/RELEASE.md")
    assert "WCSLIB stays pending" not in notes
    assert "the WCSLIB row is `not-shipped`" in notes and "#638" in notes


def test_the_gcc_exception_text_is_cut_from_the_numpy_notice_the_record_cites():
    import json

    components = json.loads(read("tools/licence/bundled-components.json"))
    rows = components if isinstance(components, list) else components["components"]
    gcc = next(r for r in rows if r["name"].startswith("GCC runtime library"))
    notice = read("tools/licence_texts/bundled-numpy-2.5.3-notices.txt")
    exception = read("tools/licence_texts/bundled-gcc-runtime-exception-3.1.txt")

    assert "NumPy 2.5.3" in gcc["version"] and "numpy-2.5.3.dist-info" in gcc["texts"][0]["source"]
    assert exception.strip() in notice


def test_the_mutation_evidence_holds_every_case_the_runner_defines():
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location("mutation_check_under_test", ROOT / "packaging/mutation_check.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    recorded = [m["name"] for m in json.loads(read("packaging/mutation-evidence.json"))["mutants"]]

    assert recorded == [c[0] for c in runner.CASES]
    assert "All %d named mutants" % len(recorded) in read("packaging/mutation-evidence.md")


def test_the_frozen_baseline_and_the_dated_evidence_say_wcslib_is_superseded():
    import json

    baseline = json.loads(read("tools/licence/dependency-current-windows-archive.json"))
    assert "#638" in baseline["superseded_note"] and "regenerate" in baseline["superseded_note"]
    for name in ("service-data-audit.md", "completion-report.md", "release-engineering-report.md"):
        assert "Superseded in part by the #638 ruling D-F" in read("tools/licence/" + name)
    assert "Superseded in part by the #638 ruling D-F" in read("docs/superpowers/backlog/2026-08-08-third-party-licences.md")
