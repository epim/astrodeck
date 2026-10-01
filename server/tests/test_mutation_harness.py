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
