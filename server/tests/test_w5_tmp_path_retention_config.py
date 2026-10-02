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

A setting that silently stops being read (a typo in the key, the section
renamed, the value changed to the default by an unrelated edit) is exactly
the kind of regression nothing else in this suite would catch -- this test
reads it back from pytest's OWN parsed config, not from the TOML text, so
it is provably the value pytest is actually using, not just a string this
file happens to contain."""
from __future__ import annotations


def test_pyproject_sets_the_approved_tmp_path_retention(pytestconfig):
    """backlog ruling D-nn N/A -- no ruling needed, fix shape is the plan's
    own text for WP-68 (d) / #622: ``tmp_path_retention_policy = "failed"``
    and ``tmp_path_retention_count = 1``, read back from pytest's own
    parsed ini config (``pytestconfig.getini``), not re-parsed from the
    TOML text.

    MUTANT "the policy reverts to the pytest default" (pyproject.toml's
    ``tmp_path_retention_policy`` line deleted, so pytest falls back to its
    shipped default, "all"): RED, observed verbatim:
        AssertionError: tmp_path_retention_policy must be 'failed' (#622);
        it reads 'all' -- pytest's own default retains every passed test's
        tmp_path forever, which is the exact defect #622 reported
        assert 'all' == 'failed'
    Run from a byte backup of pyproject.toml, restored and SHA-256-compared
    after (#254's convention)."""
    policy = pytestconfig.getini("tmp_path_retention_policy")
    assert policy == "failed", (
        f"tmp_path_retention_policy must be 'failed' (#622); it reads "
        f"{policy!r} -- pytest's own default retains every passed test's "
        f"tmp_path forever, which is the exact defect #622 reported")
    count = int(pytestconfig.getini("tmp_path_retention_count"))
    assert count == 1, (
        f"tmp_path_retention_count must be 1 (#622's stricter floor, below "
        f"pytest's own default of 3); it reads {count!r}")
