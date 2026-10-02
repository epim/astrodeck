# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""gen_credits.py must refuse rather than fabricate when ui/node_modules is
missing (#642).

collect_npm() reads the package LIST from ui/package-lock.json, which survives
a missing node_modules -- so without a preflight the generator does not go
empty, it goes WRONG: it still emits one entry per npm package, with every
licence TEXT silently dropped and a false note claiming "Upstream ships no
licence file in the package" (false: the package was simply never there to
read). A fresh clone, a Python-only CI job, or a wave worktree (whose
ui/node_modules arrives only as a junction that workflow tooling sets up)
would launder a missing dependency tree into a fabricated licence claim.

This drives the real script, as a subprocess, against a throwaway directory
that holds only `tools/` (gen_credits.py needs its sibling modules importable
at module load time) beside an empty `ui/` with no `node_modules` under it --
never a full repository copy, because the preflight must fire before any
other collector (python/cargo/vendor/data) would need its own fixtures, and a
test that needed those fixtures would stop proving that.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def repo_without_node_modules(tmp_path: Path) -> Path:
    """`tools/` (gen_credits.py, credits_registry.py, licence_policy.py)
    copied beside an empty `ui/` with no `node_modules` -- the preflight's own
    precondition, and nothing else."""
    shutil.copytree(REPO / "tools", tmp_path / "tools",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "ui").mkdir()
    return tmp_path


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / "gen_credits.py"), *args],
        capture_output=True, text=True, timeout=60, cwd=str(repo))


def test_check_refuses_without_node_modules(repo_without_node_modules):
    """Mutation "the preflight removed" (the `_require_node_modules()` call
    deleted from `build()`), observed red (2026-10-01, this worktree): build()
    instead reached collect_python(), which needs server/pyproject.toml --
    absent from this minimal fixture on purpose -- and crashed with an
    unhandled FileNotFoundError (returncode 1) instead of this refusal
    (returncode 2):
        assert 1 == 2
         +  where 1 = CompletedProcess(...).returncode
    """
    result = _run(repo_without_node_modules, "--check")
    assert result.returncode == 2, result.stderr
    assert "ui/node_modules is missing" in result.stderr
    assert "npm ci" in result.stderr


def test_regeneration_also_refuses_without_node_modules(repo_without_node_modules):
    """Not just `--check`: a bare run must refuse too, or it would silently
    write the fabricated file to disk -- #642's worse case, since nothing
    downstream of that write says the npm texts it just discarded were ever
    real."""
    out = repo_without_node_modules / "ui" / "src" / "credits.generated.json"
    result = _run(repo_without_node_modules)
    assert result.returncode == 2, result.stderr
    assert "ui/node_modules is missing" in result.stderr
    assert not out.exists(), "the generator wrote output despite refusing"


def test_an_empty_node_modules_directory_is_enough_to_pass_the_preflight(
        repo_without_node_modules):
    """CONTROL: the preflight is a presence check, not a content check, so an
    empty `node_modules/` clears it -- proving the refusal above is this
    check and not some earlier, unrelated failure. (build() then reaches
    collect_python(), which this minimal fixture cannot satisfy either, so
    the run still fails -- just not on this message.)"""
    (repo_without_node_modules / "ui" / "node_modules").mkdir()
    result = _run(repo_without_node_modules, "--check")
    assert "ui/node_modules is missing" not in result.stderr
