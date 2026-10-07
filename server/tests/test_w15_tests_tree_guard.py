# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The suite watches its own tests directory as well as the source (#254).

conftest.py's tree guard (issue #118, reported once per run by #250) fails a
run if `server/astrodeck/*.py` changes while it runs. #254 is the same fault
one directory over: a parallel agent editing a test file or conftest.py under
a running suite makes the run grade a mix of two versions of the tests, and a
mutant that is already in a test file when the run starts is graded from the
first test. So the guard is registered twice: once over the package, once
over `server/tests`, each under its own name and with its own headline, so the
block says which tree moved.

These run the REAL `pytest_configure` of conftest.py, loaded by path, in a
throwaway pytest session whose `_SERVER_DIR` is a throwaway directory,
because the suite's own tests directory cannot be edited from inside the
suite without invalidating the run that edits it.

MUTANT, run from a byte backup of server/tests/conftest.py, the file restored
from it, compared by sha256, and the mutant text grepped out afterwards:

  T1 THE NAMED MUTANT, the second registration removed (the
  `astrodeck-tests-guard` `register` call deleted from `pytest_configure`).
  test_an_edit_under_tests_is_one_verdict_naming_the_tests_tree (both
  worker modes), test_both_trees_moving_is_two_verdicts_one_each and
  test_the_suite_watches_its_own_tests_directory.
  T2 the label ignored (the headline built from the fixed words "source
  tree" whatever `label` says). The same first case, in both modes, the
  both-trees case, and test_the_default_label_keeps_the_original_headline.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_CONFTEST = Path(__file__).resolve().parent / "conftest.py"
_SOURCE_HEADLINE = "THIS RUN IS INVALID: the source tree changed while it ran"
_TESTS_HEADLINE = "THIS RUN IS INVALID: the test tree changed while it ran"


def _session(tmp_path: Path, *, edit: tuple[str, ...]) -> Path:
    """A six-test suite whose third test rewrites the files named in `edit`
    (`tests` -> server/tests/helper.py, `source` -> server/astrodeck/mod.py),
    pushing each one's mtime a second on so a coarse clock cannot hide it.
    Its conftest runs the real `pytest_configure` against `tmp_path/server`."""
    server = tmp_path / "server"
    (server / "astrodeck").mkdir(parents=True)
    (server / "tests").mkdir()
    (server / "astrodeck" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (server / "tests" / "helper.py").write_text("y = 1\n", encoding="utf-8")
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "pytest.ini").write_text("[pytest]\naddopts =\n", encoding="utf-8")
    (suite / "conftest.py").write_text(
        "import importlib.util\n"
        "import pathlib\n"
        f"_spec = importlib.util.spec_from_file_location("
        f"'_astrodeck_suite_conftest', {str(_CONFTEST)!r})\n"
        "_mod = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_mod)\n"
        f"_mod._SERVER_DIR = pathlib.Path({str(server)!r})\n"
        "_mod._REAL_CAPTURE_ROOTS = ()\n"
        "\n"
        "def pytest_configure(config):\n"
        "    _mod.pytest_configure(config)\n",
        encoding="utf-8")
    targets = {"tests": server / "tests" / "helper.py",
               "source": server / "astrodeck" / "mod.py"}
    mover = []
    for which in edit:
        mover += [f"_edit({str(targets[which])!r})"]
    body = "\n".join(f"    {line}" for line in mover) or "    pass"
    tests = [
        "import os",
        "import pathlib",
        "",
        "def _edit(path):",
        "    p = pathlib.Path(path)",
        "    st = p.stat()",
        "    p.write_text('z = 2\\n', encoding='utf-8')",
        "    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))",
        "",
        "def _move():",
        body,
        "",
    ]
    for n in range(1, 7):
        tests += [f"def test_{n}():", "    _move()" if n == 3 else "    pass", ""]
    (suite / "test_run.py").write_text("\n".join(tests), encoding="utf-8")
    return suite


def _run(suite: Path, workers: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-n", workers,
         "-q", str(suite)],
        cwd=suite, env=env, capture_output=True, text=True, timeout=180)


def _counts_line(lines: list[str]) -> int:
    hits = [i for i, line in enumerate(lines)
            if re.search(r"\b\d+ passed\b.* in [\d.]+s", line)]
    assert hits, "no counts line in the output"
    return hits[-1]


@pytest.mark.parametrize("workers", ["2", "0"], ids=["xdist", "no-xdist"])
def test_an_edit_under_tests_is_one_verdict_naming_the_tests_tree(tmp_path, workers):
    """A test file changed mid-run: one block, after the counts line, headed
    for the TEST tree, naming the file; the source guard says nothing; every
    test still passed; exit 1.

    RED under T1, the second registration removed (observed, verbatim,
    [no-xdist]; [xdist] the same, with "bringing up nodes..." in the
    appended output): the inner run printed its six passes and nothing
    else,

        AssertionError: an edit under tests/ went unreported
          ......                                                 [100%]
          6 passed in 0.01s

        assert 0 == 1
         +  where 0 = <built-in method count of str object at 0x...>('THIS
         RUN IS INVALID: the test tree changed while it ran')

    RED under T2, the label ignored: the block comes out headed "the source
    tree", so the same first assertion fails with the same count of 0.
    """
    proc = _run(_session(tmp_path, edit=("tests",)), workers)
    lines = proc.stdout.splitlines()
    assert proc.stdout.count(_TESTS_HEADLINE) == 1, (
        "an edit under tests/ went unreported\n" + proc.stdout)
    assert _SOURCE_HEADLINE not in proc.stdout, (
        "the source guard reported a tree that did not move")
    assert "changed or removed: helper.py" in proc.stdout, proc.stdout
    counts = _counts_line(lines)
    assert "6 passed" in lines[counts], lines[counts]
    headline = next(i for i, line in enumerate(lines) if _TESTS_HEADLINE in line)
    assert headline > counts, "the verdict must come after the counts line"
    assert not [line for line in lines if line.startswith("ERROR ")], proc.stdout
    assert proc.returncode == 1, proc.stdout


def test_an_edit_under_the_source_is_still_reported_and_only_as_the_source(tmp_path):
    """The original guard is unchanged by the second one: an edit under the
    package is reported once, as the source tree, and the tests guard stays
    quiet."""
    proc = _run(_session(tmp_path, edit=("source",)), "0")
    assert proc.stdout.count(_SOURCE_HEADLINE) == 1, proc.stdout
    assert _TESTS_HEADLINE not in proc.stdout, proc.stdout
    assert "changed or removed: mod.py" in proc.stdout, proc.stdout
    assert proc.returncode == 1, proc.stdout


def test_both_trees_moving_is_two_verdicts_one_each(tmp_path):
    proc = _run(_session(tmp_path, edit=("tests", "source")), "0")
    assert proc.stdout.count(_SOURCE_HEADLINE) == 1, proc.stdout
    assert proc.stdout.count(_TESTS_HEADLINE) == 1, proc.stdout
    assert proc.returncode == 1, proc.stdout


def test_control_a_quiescent_run_reports_nothing(tmp_path):
    proc = _run(_session(tmp_path, edit=()), "0")
    assert "THIS RUN IS INVALID" not in proc.stdout + proc.stderr, proc.stdout
    assert proc.returncode == 0, proc.stdout


def test_the_default_label_keeps_the_original_headline():
    """`_TheTreeMustNotMove(root)` with no label is what it always was, so a
    caller written before the label existed (test_suite_guards_report_once.py
    builds it that way) reads the same words."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_w15_conftest_probe", _CONFTEST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._TheTreeMustNotMove(Path(".")).HEADLINE == _SOURCE_HEADLINE
    assert (mod._TheTreeMustNotMove(Path("."), label="test tree").HEADLINE
            == _TESTS_HEADLINE)


def test_the_suite_watches_its_own_tests_directory(request):
    """The wiring the throwaway sessions above cannot see: THIS run registers
    the second guard, over `server/tests`, and an xdist worker holds no
    snapshot of it, because the controller speaks for the run.

    RED under T1 (observed, verbatim, run on its own with -n 0):

        AssertionError: this run has no guard over its own tests directory
        assert None is not None
    """
    guard = request.config.pluginmanager.get_plugin("astrodeck-tests-guard")
    assert guard is not None, "this run has no guard over its own tests directory"
    root = Path(__file__).resolve().parent
    assert guard.root == root
    assert guard.label == "test tree"
    source = request.config.pluginmanager.get_plugin("astrodeck-tree-guard")
    assert source is not None and source is not guard
    if hasattr(request.config, "workerinput"):
        assert guard._before is None, (
            "an xdist worker took its own snapshot of tests/; the controller "
            "alone speaks for the run")
    else:
        assert root / "conftest.py" in guard._before, (
            "the controller's snapshot of tests/ does not cover conftest.py")
