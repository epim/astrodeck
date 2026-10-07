# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The mutation harness must not be able to leave a mutant in the tree (#96).

The incident: the restore copy failed with OSError 22 while a watcher still
held the handle, the mutated file stayed in the working tree, and the NEXT
mutation run took it as its clean backup and reported a clean digest for a
mutated file. The guard was satisfied by the corruption.

These drive the real module, including a simulated restore failure, because
"it worked the times I ran it" is what the original convention had.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_MUT = Path(__file__).resolve().parents[2] / "scripts" / "mutate.py"
_spec = importlib.util.spec_from_file_location("astrodeck_mutate", _MUT)
mutate = importlib.util.module_from_spec(_spec)
sys.modules["astrodeck_mutate"] = mutate
_spec.loader.exec_module(mutate)

CLEAN = "def f():\n    return 1\n"


@pytest.fixture
def src(tmp_path: Path) -> Path:
    p = tmp_path / "thing.py"
    p.write_text(CLEAN, encoding="utf-8")
    return p


def test_a_failed_restore_stops_the_next_mutation_instead_of_becoming_its_baseline(src):
    """The incident, exactly: restore fails, the mutant stays, and the next
    apply must REFUSE rather than snapshot the corruption as clean.

    MUTATION: delete the digest comparison in `_require_clean`. Observed: the
    second apply succeeds against the mutated file and this fails on the
    "should have refused" assertion.
    """
    mutate.snapshot(src)
    mutate.apply(src, "return 1", "return 2")

    # The restore cannot complete -- the failure mode the harness exists for.
    def always_busy(*_a, **_k):
        raise OSError(22, "Invalid argument")

    original = mutate.shutil.copyfile
    mutate.shutil.copyfile = always_busy
    mutate.RESTORE_WAIT_S, keep = 0.0, mutate.RESTORE_WAIT_S
    mutate.RESTORE_ATTEMPTS, keep_n = 3, mutate.RESTORE_ATTEMPTS
    try:
        with pytest.raises(SystemExit) as failed:
            mutate.restore(src)
        assert "STILL IN THE TREE" in str(failed.value), (
            "a failed restore did not say the mutant is still in the tree")
        assert src.read_text(encoding="utf-8").endswith("return 2\n")

        # ...and now the guard that the incident lacked.
        mutate.shutil.copyfile = original
        with pytest.raises(SystemExit) as refused:
            mutate.apply(src, "return 2", "return 3")
        assert "does not match its snapshot" in str(refused.value), (
            "the harness mutated a file it had already failed to restore, so "
            "the next mutant is measured against a corrupted baseline")
    finally:
        mutate.shutil.copyfile = original
        mutate.RESTORE_WAIT_S, mutate.RESTORE_ATTEMPTS = keep, keep_n


def test_the_restore_retries_and_reports_how_many_attempts_it_needed(src):
    """Twenty attempts at 0.5s cleared the real thing both times. A retry that
    silently succeeded on the second go would hide how close it came.

    MUTATION: remove the `continue` from the OSError arm so the first failure
    propagates. Observed: SystemExit instead of a successful restore.
    """
    mutate.snapshot(src)
    mutate.apply(src, "return 1", "return 2")

    real = mutate.shutil.copyfile
    calls = {"n": 0}

    def busy_twice(a, b):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError(22, "Invalid argument")
        return real(a, b)

    mutate.shutil.copyfile = busy_twice
    keep, mutate.RESTORE_WAIT_S = mutate.RESTORE_WAIT_S, 0.0
    try:
        attempts = mutate.restore(src)
        assert attempts == 3, f"restored on attempt {attempts}, expected the third"
        assert src.read_text(encoding="utf-8") == CLEAN
    finally:
        mutate.shutil.copyfile = real
        mutate.RESTORE_WAIT_S = keep


def test_an_ambiguous_anchor_is_refused(src):
    """A mutation aimed at two places lands somewhere other than intended, and
    the report then names a mutation that was not the one run. I made this
    mistake twice by hand on 2026-09-20.

    MUTATION: drop the `hits > 1` arm. Observed: apply succeeds and this fails.
    """
    src.write_text("a = 1\nb = 1\n", encoding="utf-8")
    mutate.snapshot(src)
    with pytest.raises(SystemExit) as e:
        mutate.apply(src, "= 1", "= 2")
    assert "appears 2 times" in str(e.value)


def test_a_second_snapshot_over_a_live_one_is_refused(src):
    """Re-snapshotting is how the mutant became the baseline: the second
    snapshot records whatever is in the tree as clean.

    MUTATION: delete the `state.exists()` arm in snapshot. Observed: the second
    snapshot succeeds and records the mutated digest.
    """
    mutate.snapshot(src)
    mutate.apply(src, "return 1", "return 2")
    with pytest.raises(SystemExit) as e:
        mutate.snapshot(src)
    assert "already under a mutation snapshot" in str(e.value)
    mutate.restore(src)
    assert src.read_text(encoding="utf-8") == CLEAN


# ---------------------------------------------------------------------------
# #254: a mutation is never run in the shared checkout.
#
# Parallel agents have mutated, deleted and rewritten each other's files in
# the one working tree three times, and every time a run graded code that was
# not the code under review. Backlog ruling D-01 (owner-approved 2026-09-30)
# wants the isolation enforced where a script can enforce it, and this is the
# one place the mutation harness can: it refuses to take a snapshot, which is
# where every mutation starts, in the repository's main checkout.
# ---------------------------------------------------------------------------
import subprocess  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, check=True)
    return out.stdout


@pytest.fixture(autouse=True)
def _no_inherited_override(monkeypatch):
    """The override is an operator's switch; an inherited one would make the
    refusal cases below pass or fail by the shell they were started from."""
    monkeypatch.delenv("ASTRODECK_MUTATE_SHARED_TREE", raising=False)


@pytest.fixture
def main_repo(tmp_path: Path) -> Path:
    """A real git repository with one commit: its checkout is the shared tree."""
    r = tmp_path / "main"
    r.mkdir()
    _git(r.parent, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    _git(r, "config", "core.autocrlf", "false")
    (r / "thing.py").write_text(CLEAN, encoding="utf-8")
    _git(r, "add", "--", "thing.py")
    _git(r, "commit", "-q", "-m", "one")
    return r


@pytest.fixture
def linked_worktree(main_repo: Path, tmp_path: Path) -> Path:
    wt = tmp_path / "linked"
    _git(main_repo, "worktree", "add", "-q", "-b", "task", str(wt))
    return wt


def test_a_snapshot_in_the_main_checkout_is_refused(main_repo):
    """The shared tree is where three parallel agents collided; a mutation
    started there is refused before it records anything, and the refusal names
    the issue and both ways out.

    MUTATION MA: `_refuse_shared_tree` returns on its first line (the
    environment check and everything after it gone). Observed, verbatim: this
    case, the subdirectory case below and the gate-parity case in
    test_w15_gate_run.py fail, the first with

        Failed: DID NOT RAISE <class 'SystemExit'>
    """
    target = main_repo / "thing.py"
    with pytest.raises(SystemExit) as refused:
        mutate.snapshot(target)
    text = str(refused.value)
    assert "#254" in text, text
    assert "wp_worktree.py" in text and "byte copy" in text, text
    assert not mutate._state_path(target).exists(), (
        "a refused snapshot still left its state file in the shared tree")
    assert not mutate._backup_path(target).exists(), (
        "a refused snapshot still left its backup in the shared tree")
    assert target.read_text(encoding="utf-8") == CLEAN


def test_a_snapshot_in_a_linked_worktree_is_allowed(linked_worktree):
    """The way out the refusal names: a linked worktree has its own working
    files, so a mutant there cannot reach the shared tree.

    MUTATION MC: refuse when the two git directories DIFFER instead of when
    they are equal. Observed, verbatim: this case fails with the refusal,
    and the main-checkout case above fails on `DID NOT RAISE`, since the
    main checkout is now the one that goes through:

        SystemExit: refusing to mutate thing.py: it is in the repository's
        main checkout, which parallel agents share (#254). A mutant here is
        graded by every other run in the tree.
    """
    target = linked_worktree / "thing.py"
    mutate.snapshot(target)
    mutate.apply(target, "return 1", "return 2")
    mutate.restore(target)
    assert target.read_text(encoding="utf-8") == CLEAN


def test_a_snapshot_outside_any_repository_is_allowed(src):
    """The other way out, a byte copy outside any repository. `git` failing
    is not a reason to refuse: there is no shared tree to protect."""
    mutate.snapshot(src)
    mutate.restore(src)


def test_the_override_lets_a_snapshot_into_the_main_checkout(main_repo, monkeypatch):
    """An operator who really means it can say so; the variable is the whole
    switch, and `restore` never needs it, so a snapshot taken under it can
    always be undone afterwards.

    MUTATION MD: ignore the variable. Observed, verbatim: the snapshot is
    refused here, and no other case fails:

        SystemExit: refusing to mutate thing.py: it is in the repository's
        main checkout, which parallel agents share (#254). ...
    """
    target = main_repo / "thing.py"
    monkeypatch.setenv("ASTRODECK_MUTATE_SHARED_TREE", "1")
    mutate.snapshot(target)
    mutate.apply(target, "return 1", "return 2")
    monkeypatch.delenv("ASTRODECK_MUTATE_SHARED_TREE")
    mutate.restore(target)
    assert target.read_text(encoding="utf-8") == CLEAN


def test_a_subdirectory_of_the_main_checkout_is_still_the_main_checkout(main_repo):
    """`git rev-parse` prints the two directories relative from a repository's
    root and absolute from below it; the comparison must not depend on which.

    MUTATION MB: compare the two printed strings instead of the resolved
    paths (`git_dir, common_dir = lines`). Observed, verbatim: the snapshot of
    a file one level down goes through, while the one at the root is still
    refused (".git" equals ".git"), so only this case and the gate-parity
    case fail:

        Failed: DID NOT RAISE <class 'SystemExit'>
    """
    sub = main_repo / "pkg"
    sub.mkdir()
    target = sub / "inner.py"
    target.write_text(CLEAN, encoding="utf-8")
    with pytest.raises(SystemExit) as refused:
        mutate.snapshot(target)
    assert "#254" in str(refused.value)
