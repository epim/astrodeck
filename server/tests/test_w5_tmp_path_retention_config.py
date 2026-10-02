# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#622: server/pyproject.toml must actually carry the tmp_path retention
settings, not just have carried them once.

24 `pytest-N` base directories under %TEMP%\\pytest-of-bear had grown to
17.54 GB (measured 2026-10-01) with no `tmp_path_retention_count`
or `tmp_path_retention_policy` set, because pytest's own "keep 3" default
retains every test's tmp_path (``policy="all"``) and only prunes an OLD
session directory once its lock is either released or more than
`_pytest.pathlib.LOCK_TIMEOUT` (3 days) stale (traced in WP-68's return).
``policy="failed"`` is what actually bounds a single run's own footprint:
it deletes each PASSED test's tmp_path live, during the run, and deletes
the whole session directory at ``pytest_sessionfinish`` if every test
passed -- verified empirically for WP-68 (two consecutive fully-green runs
in an isolated temp root left ZERO base directories; one with a failure
left only that failed test's own data).

THE POLICY IS BACK TO THE DEFAULT FOR NOW (#659). "failed" deletes each
passed test's tmp_path during that test's teardown, inside the real-config
guard's window, and on Linux `shutil.rmtree` deletes through dir_fd-relative
names: the guard resolved a tmp `config` directory against the CWD as the
real `server/config` and failed five tests on the runner. Only `count = 1`
stays, which bounds how many old run directories survive, not how large
one run grows -- so #622 stays open until #659's guard fix lets "failed"
come back, and whoever restores it updates this test with it.

A setting that silently stops being read (a typo in the key, the section
renamed, the value changed to the default by an unrelated edit) is exactly
the kind of regression nothing else in this suite would catch -- this test
reads it back from pytest's OWN parsed config, not from the TOML text, so
it is provably the value pytest is actually using, not just a string this
file happens to contain."""
from __future__ import annotations


def test_pyproject_sets_the_approved_tmp_path_retention(pytestconfig):
    """``tmp_path_retention_count = 1`` (WP-68 (d) / #622) and the policy
    at pytest's default until #659 is fixed, both read back from pytest's
    own parsed ini config (``pytestconfig.getini``), not re-parsed from the
    TOML text.

    MUTANT "the count reverts to the pytest default" (pyproject.toml's
    ``tmp_path_retention_count`` line deleted, so pytest falls back to 3):
    RED. MUTANT "failed comes back before #659" (the policy line restored
    as ``"failed"``): RED, with the message below naming #659. Both run
    from a byte backup of pyproject.toml, restored and SHA-256-compared
    after (#254's convention)."""
    policy = pytestconfig.getini("tmp_path_retention_policy")
    assert policy == "all", (
        f"tmp_path_retention_policy reads {policy!r}; it stays at pytest's "
        f"default 'all' until #659 is fixed, because 'failed' deletes tmp "
        f"dirs inside the real-config guard's window and the guard misreads "
        f"Linux rmtree's dir_fd-relative names as the real config. Restore "
        f"'failed' together with #659's fix, and update this test then")
    count = int(pytestconfig.getini("tmp_path_retention_count"))
    assert count == 1, (
        f"tmp_path_retention_count must be 1 (#622's stricter floor, below "
        f"pytest's own default of 3); it reads {count!r}")
