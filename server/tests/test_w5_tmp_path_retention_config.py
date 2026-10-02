# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#622: server/pyproject.toml must actually carry the tmp_path retention
settings, not just have carried them once.

24 `pytest-N` base directories under %TEMP%\\pytest-of-bear had grown to
17.54 GB (measured 2026-10-01 04:42 PDT) with no `tmp_path_retention_count`
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

THE POLICY IS BACK TO "failed" (WP-72 (b)): #659's fix (conftest.py's
``_dir_fd_relative_positions`` and ``_open_is_fd_relative``) tells a
dir_fd-relative path apart from a CWD-relative one, so the guard no longer
resolves a tmp `config` directory against the CWD as the real
`server/config` -- see test_real_config_guard_fd_relative.py for the
reproduction and the control. #622 closes only when branch CI on Linux is
green with this policy restored; the original failure was Linux-only (on
Windows ``shutil.rmtree`` walks full paths) and cannot be reproduced on a
Windows box, so this test and a green Windows run are not that proof.

A setting that silently stops being read (a typo in the key, the section
renamed, the value changed to the default by an unrelated edit) is exactly
the kind of regression nothing else in this suite would catch -- this test
reads it back from pytest's OWN parsed config, not from the TOML text, so
it is provably the value pytest is actually using, not just a string this
file happens to contain."""
from __future__ import annotations


def test_pyproject_sets_the_approved_tmp_path_retention(pytestconfig):
    """``tmp_path_retention_count = 1`` (WP-68 (d) / #622) and
    ``tmp_path_retention_policy = "failed"`` (restored by WP-72 (b) now that
    #659's guard fix is in), both read back from pytest's own parsed ini
    config (``pytestconfig.getini``), not re-parsed from the TOML text.

    MUTANT "the count reverts to the pytest default" (pyproject.toml's
    ``tmp_path_retention_count`` line deleted, so pytest falls back to 3):
    RED. MUTANT "policy reverts to the pytest default" (pyproject.toml's
    ``tmp_path_retention_policy`` line deleted, so pytest falls back to
    "all"): RED, with the message below naming #659/#622. Both run from a
    byte backup of pyproject.toml, restored and SHA-256-compared after
    (#254's convention)."""
    policy = pytestconfig.getini("tmp_path_retention_policy")
    assert policy == "failed", (
        f"tmp_path_retention_policy reads {policy!r}; it must be 'failed' "
        f"(WP-72 (b)), which deletes each passed test's tmp_path during "
        f"that test's teardown and bounds a single run's own disk footprint "
        f"(#622). It only went back to the default while the real-config "
        f"guard misread Linux rmtree's dir_fd-relative names as the real "
        f"config (#659, now fixed -- test_real_config_guard_fd_relative.py)")
    count = int(pytestconfig.getini("tmp_path_retention_count"))
    assert count == 1, (
        f"tmp_path_retention_count must be 1 (#622's stricter floor, below "
        f"pytest's own default of 3); it reads {count!r}")
