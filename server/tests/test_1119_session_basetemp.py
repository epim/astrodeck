# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Concurrent pytest sessions on one box never share a temp directory (#1119;
#909, #916).

pytest numbers its base directories ``pytest-N`` under one shared
``pytest-of-<user>``, and each session sweeps the older ones at exit. Two
sessions on one machine (an agent's suite beside another's, or beside the
operator's) therefore reach into each other's directories: #909 saw
``FileNotFoundError ... pytest-30548\\popen-gw10`` as teardown errors under
xdist on Windows, and #916 saw every test of one worker error at setup, a
failing temp directory, once in forty runs, with other sessions running.
Neither reproduced alone, which is what the cause predicts.

conftest.py gives each controller session a base temp of its own,
``astrodeck-pytest/<pid>-<id>`` under the temp root, with the xdist workers'
``popen-gwN`` directories inside it. It is removed when the session passes,
kept when it fails (the failed test's files are the evidence), and any left
over from a day ago is swept. An explicit ``--basetemp`` and a worker's own
are left alone. These cases run real child sessions, since the thing under
test is what a session does to the disk.

MUTANT "no session base temp" (``_own_basetemp`` returns None at once): every
case but the ``--basetemp`` one is RED, ``assert 'pytest-of-' not in ...``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_CONFTEST = Path(__file__).resolve().parent / "conftest.py"

_CONFTEST_OF_THE_CHILD = """\
import importlib.util
import pathlib
_spec = importlib.util.spec_from_file_location(
    '_astrodeck_suite_conftest', {conftest!r})
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_mod._SERVER_DIR = pathlib.Path({server!r})
_mod._REAL_CAPTURE_ROOTS = ()

def pytest_configure(config):
    _mod.pytest_configure(config)
"""

_TESTS_OF_THE_CHILD = """\
import os
import pathlib

def _note(tmp_path):
    here = pathlib.Path(os.environ['CHILD_NOTES']) / os.environ.get(
        'PYTEST_XDIST_WORKER', 'main')
    here.mkdir(parents=True, exist_ok=True)
    (here / (os.environ['PYTEST_CURRENT_TEST'].split(' ')[0].split('::')[-1])
     ).write_text(str(tmp_path), encoding='utf-8')

def test_one(tmp_path):
    (tmp_path / 'f').write_text('x')
    _note(tmp_path)

def test_two(tmp_path):
    (tmp_path / 'f').write_text('x')
    _note(tmp_path)
"""

_A_FAILING_TEST = """\

def test_three_fails(tmp_path):
    (tmp_path / 'evidence').write_text('x')
    _note(tmp_path)
    assert False
"""


def _suite(tmp_path: Path, name: str, *, failing: bool = False) -> Path:
    """A suite whose conftest runs the real `pytest_configure` of conftest.py
    against a throwaway ``server/`` tree, as test_w15_tests_tree_guard.py does."""
    server = tmp_path / name / "server"
    (server / "astrodeck").mkdir(parents=True)
    (server / "tests").mkdir()
    suite = tmp_path / name / "suite"
    suite.mkdir()
    (suite / "pytest.ini").write_text("[pytest]\naddopts =\n", encoding="utf-8")
    (suite / "conftest.py").write_text(
        _CONFTEST_OF_THE_CHILD.format(conftest=str(_CONFTEST),
                                      server=str(server)), encoding="utf-8")
    body = _TESTS_OF_THE_CHILD + (_A_FAILING_TEST if failing else "")
    (suite / "test_run.py").write_text(body, encoding="utf-8")
    return suite


def _child(suite: Path, root: Path, notes: Path, *args: str) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    root.mkdir(parents=True, exist_ok=True)       # pytest wants its root to exist
    env["PYTEST_DEBUG_TEMPROOT"] = str(root)
    env["CHILD_NOTES"] = str(notes)
    return subprocess.Popen(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q",
         *args, str(suite)],
        cwd=suite, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)


def _finish(child: subprocess.Popen) -> tuple[int, str]:
    out, _ = child.communicate(timeout=180)
    return child.returncode, out


def _where(notes: Path) -> dict[str, str]:
    """``{worker/test: the tmp_path it was given}``."""
    return {f"{p.parent.name}/{p.name}": p.read_text(encoding="utf-8")
            for p in sorted(notes.rglob("*")) if p.is_file()}


def _sessions(root: Path) -> list[Path]:
    base = root / "astrodeck-pytest"
    return sorted(base.iterdir()) if base.is_dir() else []


def test_a_session_has_a_base_temp_of_its_own(tmp_path):
    root, notes = tmp_path / "root", tmp_path / "notes"
    code, out = _finish(_child(_suite(tmp_path, "a"), root, notes, "-n", "0"))
    assert code == 0, out
    given = _where(notes)
    assert given, out
    for path in given.values():
        assert "pytest-of-" not in path, path
        assert str(root / "astrodeck-pytest") in path, path


def test_a_passing_session_leaves_nothing_behind(tmp_path):
    root, notes = tmp_path / "root", tmp_path / "notes"
    code, out = _finish(_child(_suite(tmp_path, "a"), root, notes, "-n", "0"))
    assert code == 0, out
    given = _where(notes)
    assert given, out
    for path in given.values():       # the session did run under the root
        assert str(root / "astrodeck-pytest") in path, path
        assert not Path(path).exists(), f"{path} is still there"
    assert _sessions(root) == [], [str(p) for p in _sessions(root)]


def test_a_failing_session_keeps_its_directory_for_the_evidence(tmp_path):
    root, notes = tmp_path / "root", tmp_path / "notes"
    code, out = _finish(_child(_suite(tmp_path, "a", failing=True), root, notes,
                               "-n", "0"))
    assert code == 1, out
    kept = _sessions(root)
    assert len(kept) == 1, [str(p) for p in kept]
    assert list(kept[0].rglob("evidence")), "the failed test's files are gone"


def test_the_workers_of_a_session_sit_inside_its_directory(tmp_path):
    root, notes = tmp_path / "root", tmp_path / "notes"
    code, out = _finish(_child(_suite(tmp_path, "a", failing=True), root, notes,
                               "-n", "2"))
    assert code == 1, out
    (session,) = _sessions(root)
    given = _where(notes)
    assert given, out
    for key, path in given.items():
        worker = key.split("/")[0]
        assert Path(path).is_relative_to(session / f"popen-{worker}"), (key, path)


def test_two_sessions_at_once_never_share_a_directory(tmp_path):
    """The #909 and #916 shape: two xdist sessions on one temp root, running
    together. Each gets its own directory, neither passes through the other's,
    and both clean up their own."""
    root = tmp_path / "root"
    notes_a, notes_b = tmp_path / "notes-a", tmp_path / "notes-b"
    a = _child(_suite(tmp_path, "a", failing=True), root, notes_a, "-n", "2")
    b = _child(_suite(tmp_path, "b", failing=True), root, notes_b, "-n", "2")
    (code_a, out_a), (code_b, out_b) = _finish(a), _finish(b)
    assert (code_a, code_b) == (1, 1), (out_a, out_b)
    kept = _sessions(root)
    assert len(kept) == 2, [str(p) for p in kept]

    def owners(notes: Path) -> set[Path]:
        return {next(p for p in kept if Path(path).is_relative_to(p))
                for path in _where(notes).values()}

    owner_a, owner_b = owners(notes_a), owners(notes_b)
    assert len(owner_a) == 1 and len(owner_b) == 1, (owner_a, owner_b)
    assert owner_a.isdisjoint(owner_b), (owner_a, owner_b)


def test_an_explicit_basetemp_is_respected(tmp_path):
    root, notes = tmp_path / "root", tmp_path / "notes"
    mine = tmp_path / "mine"
    code, out = _finish(_child(_suite(tmp_path, "a"), root, notes, "-n", "0",
                               f"--basetemp={mine}"))
    assert code == 0, out
    for path in _where(notes).values():
        assert Path(path).is_relative_to(mine), path
    assert not (root / "astrodeck-pytest").exists()


def test_a_directory_a_day_old_is_swept_and_a_fresh_one_is_not(tmp_path):
    root, notes = tmp_path / "root", tmp_path / "notes"
    base = root / "astrodeck-pytest"
    old, fresh = base / "1-old", base / "2-fresh"
    for d in (old, fresh):
        (d / "popen-gw0").mkdir(parents=True)
        (d / "popen-gw0" / "left").write_text("x")
    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(old, (two_days_ago, two_days_ago))
    code, out = _finish(_child(_suite(tmp_path, "a"), root, notes, "-n", "0"))
    assert code == 0, out
    assert not old.exists(), "a two-day-old session directory was kept"
    assert fresh.exists(), "a session directory of today was swept"


def test_this_suite_runs_under_its_own_base_temp(tmp_path_factory, request):
    """The run this test is in: its base temp is under ``astrodeck-pytest``,
    not the shared numbered directory, unless the run was given a
    ``--basetemp`` of its own, which wins."""
    given = [a for a in request.config.invocation_params.args
             if str(a).startswith("--basetemp")]
    if given:
        pytest.skip("this run was given a --basetemp of its own")
    base = str(tmp_path_factory.getbasetemp())
    assert "pytest-of-" not in base, base
    assert "astrodeck-pytest" in base, base
