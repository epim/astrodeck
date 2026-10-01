"""Policy checks cover live-metadata shapes and compiler-only dependencies."""
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools/licence"))
import audit_dependencies as gate


def test_permissive_and_reviewed_project_licences_pass():
    rows = [{"name": "permissive", "spdx": "MIT OR Apache-2.0"},
            {"name": "file-copyleft", "spdx": "MPL-2.0"}]
    assert gate.review(rows) == []


@pytest.mark.parametrize("spdx", ["GPL-3.0-only", "LGPL-2.1-only", "AGPL-3.0-only", "SSPL-1.0", None])
def test_unreviewed_copyleft_or_unknown_graph_dependency_fails(spdx):
    metadata = {"packages": [{"name": "injected", "version": "1", "license": spdx}]}
    assert "injected: licence is outside the allowed policy" in gate.review(gate.cargo_rows(metadata))


def test_npm_dev_flag_does_not_hide_licence_findings():
    lock = {"packages": {"": {}, "node_modules/compiler-helper": {
        "version": "1", "license": "GPL-3.0-only", "dev": True}}}
    assert gate.review(gate.npm_rows(lock)) == ["compiler-helper: licence is outside the allowed policy"]


def test_reviewed_gcc_exception_still_requires_owner_disposition():
    assert gate.review([{"name": "gcc-runtime", "spdx": "GPL-3.0-or-later WITH GCC-exception-3.1"}]) == [
        "gcc-runtime: owner review remains required"]


def test_exact_additional_permissive_licences_keep_their_conditions():
    for expression, requirement in [("BSD-3-Clause-Open-MPI", "notice"), ("BlueOak-1.0.0", "license-or-link")]:
        result = gate.policy.resolve(expression)
        assert not result.flag
        assert requirement in result.requires
