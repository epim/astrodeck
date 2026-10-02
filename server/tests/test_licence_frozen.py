# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Executable gate must inspect members and retain known owner blockers."""
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools/licence"))
import audit_frozen as gate


def fixture():
    files = {"python312.dll": b"MZ\0fixture"}
    baseline = {"artifact_sha256": "whole-executable", "files": [
        {"path": name, "sha256": gate.digest(data), "archive_type": "b"}
        for name, data in files.items()
    ]}
    return files, baseline


def test_new_vendor_binary_in_executable_is_rejected():
    files, baseline = fixture()
    files["astrodeck/vendor/rogue/foreign.dll"] = b"MZ\0foreign"
    findings, _ = gate.review_frozen(files, baseline, "whole-executable")
    assert "astrodeck/vendor/rogue/foreign.dll: unaccounted executable member" in findings


@pytest.mark.parametrize("suffix", ["PlayerOneCamera.dll", "linux-arm64/libPlayerOneCamera.so.3.10.0"])
def test_known_player_one_binary_is_still_owner_blocked(suffix):
    name = "astrodeck/vendor/playerone/" + suffix
    data = b"MZ\0vendor"
    files = {name: data}
    baseline = {"artifact_sha256": "whole-executable", "files": [
        {"path": name, "sha256": gate.digest(data), "archive_type": "b"}]}
    findings, _ = gate.review_frozen(files, baseline, "whole-executable")
    assert any("Player One redistribution needs owner confirmation (#632)" in x for x in findings)


def test_executable_shell_and_changed_member_both_need_review():
    files, baseline = fixture()
    files["python312.dll"] = b"MZ\0altered"
    findings, _ = gate.review_frozen(files, baseline, "new-shell")
    assert any("bootloader and payload need a new review" in x for x in findings)
    assert any("executable member changed" in x for x in findings)


def test_known_bytes_do_not_erase_unresolved_distribution_findings():
    files, baseline = fixture()
    findings, _ = gate.review_frozen(files, baseline, "whole-executable")
    assert any("No release clearance" in x for x in findings)
    assert any("credits predate" in x for x in findings)
