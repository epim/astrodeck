# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The ceiling on one test's time on CI, and the list of tests allowed past it
(#1261, #1256).

Ten tests and one parametrised test held 1222 s of one CI run, and four PPEC
tests waited 150 s each for nothing. ``--durations`` names the slowest tests
after the fact and nothing stopped the next one, so conftest.py's
``_SlowTestCeiling`` does: when ``CI`` is ``true``, a test whose call phase
takes longer than ``SLOW_CEILING_S`` (60 s) fails, naming the test and its
time, unless its node id is in ``SLOW_ALLOWLIST`` below. Elsewhere the ceiling
only records: the run's summary lists the tests over it.

An allowlist entry is a decision, with its reason on the same line. It is made
when a test is slow because of what it proves and cannot be made cheaper
without losing that; it is not a place to park a test that has only grown. An
entry naming a test that no longer exists is stale and fails here, so the list
shrinks as tests are made cheaper or deleted.

The hook's own cases run a throwaway pytest session over a one-file suite whose
conftest loads the real ``_SlowTestCeiling`` by path and gives it a tiny
ceiling, because a test that waits 60 s to prove a 60 s ceiling is the cost
this file exists to refuse.

MUTANT "the hook does not fail the test" (the ``report.outcome = "failed"``
line of ``_SlowTestCeiling.pytest_runtest_makereport`` removed), run from a
byte backup of conftest.py, restored and compared by sha256 -- RED, observed
verbatim:

    AssertionError: a test over the ceiling and not on the allowlist must
    fail the run: exit 0, ...
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_SERVER = Path(__file__).resolve().parents[1]
_CONFTEST = Path(__file__).resolve().parent / "conftest.py"

#: Node ids, relative to server/, of tests allowed to take longer than the
#: ceiling on CI, each with the reason on the same line.
SLOW_ALLOWLIST: dict[str, str] = {
    "tests/test_native_guider_e2e.py::test_native_guider_converges_on_sim": "the native guider's timing anchor: the sim drifts at wall-clock rate and the loop must converge on it in real time (34 s alone on a dev PC; the native job, #1255)",
    "tests/test_native_guider_recovery.py::test_flip_calibration_mid_session_keeps_guiding_bounded": "a real-time calibration in mid session, then guiding to bounded residuals, on the same wall-clock sim (30 s alone on a dev PC; the native job, #1255)",
    "tests/test_cloud_hold_follows_the_mount.py::test_a_cloudy_wait_opens_no_hold_and_the_night_goes_on": "a whole simulated night on the fake clock, all CPU: 24 to 60 s on CI depending on the runner (four runs on 2026-11-10); until the night harness is made cheaper",
    "tests/test_cloud_hold_watch.py::test_a_mount_that_stops_tracking_mid_hold_is_restored_not_judged": "the same simulated night harness: 19 to 47 s on CI depending on the runner (four runs on 2026-11-10); until the night harness is made cheaper",
}


def stale_entries(entries, collected: set[str]) -> list[str]:
    """The entries whose test is not among ``collected`` node ids, or whose
    file is gone."""
    return sorted(e for e in entries
                  if e not in collected
                  or not (_SERVER / e.split("::")[0]).is_file())


def _collected_node_ids(files: set[str]) -> set[str]:
    """Every node id pytest collects from ``files`` (relative to server/)."""
    if not files:
        return set()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-n0",
         "-p", "no:cacheprovider", *sorted(files)],
        cwd=_SERVER, env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    return {line.strip() for line in run.stdout.splitlines() if "::" in line}


def test_the_real_ceiling_is_sixty_seconds_and_enforced_on_ci(request):
    """The plugin conftest registers is the 60 s one, and it enforces exactly
    where CI says so (``CI`` is ``true``).

    MUTANT "enforced nowhere" (``enforce=False`` in ``pytest_configure``) --
    RED, observed verbatim (CI=true):

        AssertionError: the ceiling must enforce when CI is 'true' (CI='true')
    """
    plugin = request.config.pluginmanager.get_plugin("astrodeck-slow-ceiling")
    assert plugin is not None, "conftest registers no slow-test ceiling"
    assert plugin.ceiling_s == 60.0
    ci = os.environ.get("CI")
    assert plugin.enforce == (ci == "true"), (
        f"the ceiling must enforce when CI is 'true' (CI={ci!r})")


def test_every_allowlist_entry_has_a_reason_and_a_test_that_exists():
    """No entry is bare, and every entry names a test that is still collected:
    a test made cheaper or deleted takes its entry with it.

    MUTANT "an entry for a deleted test" (``tests/test_tonight_route.py::
    test_a_test_that_was_deleted`` added to the list) -- RED, observed
    verbatim:

        AssertionError: allowlist entries naming no test: ['tests/
        test_tonight_route.py::test_a_test_that_was_deleted']
    """
    bare = [e for e, reason in SLOW_ALLOWLIST.items() if len(reason) < 12]
    assert bare == [], f"allowlist entries without a reason: {bare}"
    files = {e.split("::")[0] for e in SLOW_ALLOWLIST
             if (_SERVER / e.split("::")[0]).is_file()}
    stale = stale_entries(SLOW_ALLOWLIST, _collected_node_ids(files))
    assert stale == [], f"allowlist entries naming no test: {stale}"


def test_a_stale_entry_is_found():
    """``stale_entries`` names an entry whose test is not collected, and one
    whose file is gone, and no other."""
    here = "tests/test_slow_test_ceiling.py"
    collected = {f"{here}::test_a_stale_entry_is_found", "tests/gone.py::t"}
    stale = stale_entries(
        [f"{here}::test_a_stale_entry_is_found", f"{here}::test_renamed",
         "tests/gone.py::t"], collected)
    assert stale == ["tests/gone.py::t", f"{here}::test_renamed"]


# ------------------------------------------------------------ the hook itself

_SLEEP_PAST_S = 0.3
_SLOW_TEST = "test_run.py::test_slow"


def _suite(tmp_path: Path, *, ceiling_s: float, enforce: bool,
           allowed: dict[str, str]) -> Path:
    """A suite of one slow test (it sleeps ``_SLEEP_PAST_S`` past the ceiling)
    and one quick one, whose conftest registers the real ``_SlowTestCeiling``
    with ``ceiling_s``, ``enforce`` and the ``allowed`` list."""
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "pytest.ini").write_text("[pytest]\naddopts =\n", encoding="utf-8")
    (suite / "conftest.py").write_text(
        "import importlib.util\n"
        "import pytest\n"
        "_spec = importlib.util.spec_from_file_location("
        f"'_astrodeck_suite_conftest', {str(_CONFTEST)!r})\n"
        "_mod = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_mod)\n"
        "\n"
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_configure(config):\n"
        "    config.pluginmanager.register(_mod._SlowTestCeiling(\n"
        f"        {ceiling_s!r}, enforce={enforce!r},\n"
        f"        allowlist=lambda: {allowed!r}), 'ceiling')\n",
        encoding="utf-8")
    (suite / "test_run.py").write_text(
        "import time\n"
        "\n"
        "def test_slow():\n"
        f"    time.sleep({ceiling_s + _SLEEP_PAST_S!r})\n"
        "\n"
        "def test_quick():\n"
        "    pass\n",
        encoding="utf-8")
    return suite


def _run(suite: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-n0",
         "-q", str(suite)],
        cwd=suite, env=env, capture_output=True, text=True, timeout=180)


@pytest.mark.parametrize("ceiling_s", [0.2, 0.5])
def test_a_test_over_the_ceiling_and_off_the_allowlist_fails_on_ci(
        tmp_path, ceiling_s):
    """A test that sleeps past the ceiling fails the run when the ceiling is
    enforced, and the failure names the test and the seconds it took; the
    quick test beside it passes. At either ceiling, so the bound is the
    plugin's argument and not a number that happens to be near the sleep."""
    suite = _suite(tmp_path, ceiling_s=ceiling_s, enforce=True, allowed={})
    run = _run(suite)
    out = run.stdout
    assert run.returncode == 1 and "1 failed, 1 passed" in out, (
        f"a test over the ceiling and not on the allowlist must fail the "
        f"run: exit {run.returncode}, {out[-600:]}")
    took = re.search(rf"{re.escape(_SLOW_TEST)} took ([\d.]+) s in its call "
                     rf"phase, over the {ceiling_s:g} s ceiling", out)
    assert took, f"the failure must name the test and its time: {out[-800:]}"
    assert float(took.group(1)) >= ceiling_s + _SLEEP_PAST_S - 0.05


def test_the_same_test_on_the_allowlist_passes(tmp_path):
    """The same slow test, with its node id on the allowlist, passes, and the
    summary still lists it with its reason."""
    suite = _suite(tmp_path, ceiling_s=0.3, enforce=True,
                   allowed={_SLOW_TEST: "waits on purpose, for the test"})
    run = _run(suite)
    assert run.returncode == 0 and "2 passed" in run.stdout, run.stdout[-600:]
    assert re.search(rf"{re.escape(_SLOW_TEST)}  \[allowed: waits on purpose, "
                     rf"for the test\]", run.stdout), run.stdout[-600:]


def test_without_ci_the_ceiling_only_records(tmp_path):
    """Not enforced (no ``CI``), the slow test passes, and the summary lists
    it as over the ceiling and off the allowlist, so it is seen where it was
    written."""
    suite = _suite(tmp_path, ceiling_s=0.3, enforce=False, allowed={})
    run = _run(suite)
    assert run.returncode == 0 and "2 passed" in run.stdout, run.stdout[-600:]
    assert "recorded only" in run.stdout
    assert re.search(rf"{re.escape(_SLOW_TEST)}  \[NOT on the allowlist\]",
                     run.stdout), run.stdout[-600:]
