"""#250: a source tree that moves under the suite is ONE verdict, not twelve errors.

conftest's tree guard watches `server/astrodeck/*.py` for the length of a run,
because 34 tests grade their subject's source through `inspect.getsource`, and
an edit mid-run makes those read the new file at the old line numbers (#118).
The guard was a session-scoped fixture that raised at teardown. Session
fixtures are torn down once PER PROCESS, and under xdist each worker is one, so
the one fact "the tree moved" came out as an ERROR at the teardown of every
worker's last test: twelve errors on twelve unrelated tests, in files that had
nothing to do with the edit or with each other. With `-x`, pytest also tears
the session down right after a failure, so the two getsource tests the edit
had broken carried the same error on top of their FAILED.

That is #250, reproduced on a byte copy of `server/` (the shared tree has
agents editing it) with the unmodified guard. A quiescent run of the copy:
9003 passed, 30 skipped, exit 0. The same run with 40 comment lines inserted
at the top of `api/app.py` and `hub.py` two minutes in: 9003 passed, 12
errors, the twelve being each worker's last test, every one "AssertionError:
the source tree changed while this suite was running ... changed: api/app.py,
hub.py", and the short summary cutting it to "AssertionEr..." on the two
lines short enough to show any text. The same insert five seconds in, with
`-x` as #250 ran it: 4 failed, 6904 passed, 28 skipped, 12 errors. The four
FAILED are getsource tests reading another function's body (`_spawn_connect`
read back as `_spawn`, `create_app` as `_present_token`), and two of them are
#250's two. Five of the twelve ERRORs are #250's exact node ids, and the one
line that shows its text is #250's, `test_native_backend.py::
test_health_reports_per_host - AssertionE...`. The "unrelated" tests are not
random: `--dist worksteal` hands each worker one contiguous block of the
collection, `-x` lets the workers that did not fail run theirs to the end, and
the eight errors not on a FAILED test are exactly the last items of those
eight workers' blocks, computed from the collection.

It is now a plugin, and only the process that owns the run acts on it: the
xdist controller, or the one process of a `-n0` run. One snapshot at session
start, before any worker has imported the code, and one comparison after
every worker has finished. A moved tree prints one block, headed THIS RUN IS
INVALID, as the last thing in the output - after the counts line, where
`| tail` still shows it - and turns a clean exit into exit 1.

These tests run the real plugin class, loaded from conftest.py by path, in a
throwaway pytest session over a throwaway tree, because the suite's own tree
cannot be edited from inside the suite without invalidating the run that
edits it.

MUTATIONS RUN on conftest.py, each from a byte-for-byte backup, the file
restored from it and compared by sha256 afterwards. Every one went RED; the
failure each printed is recorded, verbatim, in the test that caught it:

  M1, THE NAMED MUTANT, restore the per-worker error: give the plugin back
  the session-scoped autouse fixture that raises the verdict at teardown, and
  return from the controller's report straight after its `yield`.
  test_a_tree_moved_mid_run_is_one_verdict_not_an_error_per_worker.

  M2, report before the counts: `tryfirst` -> `trylast` on the report.
  Same test.

  M3, report whether or not anything moved (`if False and not (changed or
  appeared)`). test_control_a_quiescent_run_reports_nothing.

  M4, leave the exit status alone (the TESTS_FAILED line -> `pass`).
  test_a_tree_moved_mid_run_is_one_verdict_not_an_error_per_worker.

  M5, overwrite any exit status (`if True:`).
  test_a_run_already_exiting_non_zero_keeps_its_own_code.

  M6, drop the worker check, so every worker snapshots too.
  test_the_suite_watches_its_own_source_tree, under `-n 2` only.

  M7, snapshot in `pytest_collection_finish` instead of at session start.
  test_an_edit_during_collection_is_caught.

  M8, stop registering the plugin in conftest's `pytest_configure`.
  test_the_suite_watches_its_own_source_tree.

  M9, drop the stderr fallback for a run without a terminal reporter.
  test_without_a_terminal_the_verdict_goes_to_stderr.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_CONFTEST = Path(__file__).resolve().parent / "conftest.py"
_HEADLINE = "THIS RUN IS INVALID"


def _suite(tmp_path: Path, *, move: str | None, at_import: bool = False,
           then_exit: int | None = None) -> Path:
    """A six-test suite under `tmp_path/suite` whose guard watches
    `tmp_path/watched`, and whose third test (or, `at_import`, whose module
    import) moves that tree the way `move` says: `changed` rewrites
    `pkg/mod.py` and pushes its mtime a second on, so a coarse clock cannot
    hide it; `appeared` adds `pkg/new.py`; `removed` deletes `pkg/mod.py`.
    `then_exit` ends the session from that test with that exit code."""
    watched = tmp_path / "watched" / "pkg"
    watched.mkdir(parents=True)
    (watched / "mod.py").write_text("x = 1\n", encoding="utf-8")
    suite = tmp_path / "suite"
    suite.mkdir()
    # Its own rootdir, so the suite's addopts (`-n 12 --dist worksteal`) and
    # nothing else of ours leaks in: the command line below says all of it.
    (suite / "pytest.ini").write_text("[pytest]\naddopts =\n", encoding="utf-8")
    (suite / "conftest.py").write_text(
        "import importlib.util\n"
        "import pathlib\n"
        f"_spec = importlib.util.spec_from_file_location("
        f"'_astrodeck_suite_conftest', {str(_CONFTEST)!r})\n"
        "_mod = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_mod)\n"
        "\n"
        "def pytest_configure(config):\n"
        "    config.pluginmanager.register(\n"
        f"        _mod._TheTreeMustNotMove(pathlib.Path({str(tmp_path / 'watched')!r})),\n"
        "        'tree-guard-under-test')\n",
        encoding="utf-8")
    mover = {
        None: [],
        "changed": ["st = MOD.stat()",
                    "MOD.write_text('x = 2\\n', encoding='utf-8')",
                    "os.utime(MOD, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))"],
        "appeared": ["(MOD.parent / 'new.py').write_text('y = 1\\n', encoding='utf-8')"],
        "removed": ["MOD.unlink()"],
    }[move]
    if then_exit is not None:
        mover = [*mover, f"pytest.exit('stopping here', returncode={then_exit})"]
    body = "\n".join(f"    {line}" for line in mover) or "    pass"
    tests = [
        "import os",
        "import pathlib",
        "import pytest",
        f"MOD = pathlib.Path({str(watched / 'mod.py')!r})",
        "",
        "def _move():",
        body,
        "",
    ]
    if at_import:
        tests += ["_move()", ""]
    for n in range(1, 7):
        tests += [f"def test_{n}():"]
        tests += ["    _move()" if n == 3 and not at_import else "    pass", ""]
    (suite / "test_run.py").write_text("\n".join(tests), encoding="utf-8")
    return suite


def _run(suite: Path, workers: str,
         *options: str) -> subprocess.CompletedProcess[str]:
    # An inherited PYTEST_XDIST_WORKER or PYTEST_ADDOPTS from the run that is
    # running THIS test must not tell the inner run what it is.
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider",
         "-n", workers, *(options or ("-q",)), str(suite)],
        cwd=suite, env=env, capture_output=True, text=True, timeout=180)


def _counts_line(lines: list[str]) -> int:
    """The index of pytest's closing counts line, `6 passed in 0.70s`."""
    hits = [i for i, line in enumerate(lines)
            if re.search(r"\b\d+ passed\b.* in [\d.]+s", line)]
    assert hits, "no counts line in the output"
    return hits[-1]


@pytest.mark.parametrize("workers", ["2", "0"], ids=["xdist", "no-xdist"])
def test_a_tree_moved_mid_run_is_one_verdict_not_an_error_per_worker(
        tmp_path, workers):
    """The #250 case: the tree moves during a run. One block, at the end,
    naming the file; no ERROR on any test; every test still passed; exit 1.

    RED under M1, the per-worker error restored - one ERROR per worker, on
    whichever test it ran last, which is #250 at the scale of two workers
    (observed, verbatim, [xdist]; [no-xdist] had the one, on test_6):

        AssertionError: the moved tree came out as test errors, the #250
        shape: ['ERROR test_run.py::test_6 - AssertionError: the source tree
        changed while thi...', 'ERROR test_run.py::test_5 - AssertionError:
        the source tree changed while thi...']

    (A re-run of M1 after the last edit to conftest printed the same, and
    "2 failed, 1 error": the mutant's fixture also fired at the teardown of
    the OUTER run's last test, because other agents were editing the real
    tree during it.)

    RED under M2, `tryfirst` turned to `trylast` on the report, which makes
    it the INNER wrapper, so it prints before the terminal reporter's summary
    (observed, verbatim, [xdist]; [no-xdist] "assert 0 > 5"):

        AssertionError: the verdict must come after the counts line, where
        `| tail` shows it
        assert 3 > 8

    RED under M4, the exit status left alone, both cases (observed,
    verbatim):

        AssertionError: 0 != 1: a moved tree must fail the run
        assert 0 == 1
    """
    proc = _run(_suite(tmp_path, move="changed"), workers)
    out = proc.stdout
    lines = out.splitlines()
    errors = [line for line in lines if line.startswith("ERROR ")]
    assert not errors, f"the moved tree came out as test errors, the #250 shape: {errors}"
    assert out.count(_HEADLINE) == 1, out
    assert "Re-run on a quiescent tree" in out, out
    assert "changed or removed: pkg/mod.py" in out, out
    counts = _counts_line(lines)
    assert "6 passed" in lines[counts] and "error" not in lines[counts], lines[counts]
    headline = next(i for i, line in enumerate(lines) if _HEADLINE in line)
    assert headline > counts, (
        "the verdict must come after the counts line, where `| tail` shows it")
    assert proc.returncode == 1, (
        f"{proc.returncode} != 1: a moved tree must fail the run")


@pytest.mark.parametrize("workers", ["2", "0"], ids=["xdist", "no-xdist"])
def test_control_a_quiescent_run_reports_nothing(tmp_path, workers):
    """The control: nothing moves, so nothing is said and the exit is 0.

    RED under M3, reporting whether or not anything moved, both cases
    (observed, verbatim, up to the output pytest quotes after it):

        AssertionError: a quiescent run was called invalid
        assert 'THIS RUN IS INVALID' not in 'bringing up...ared: none\\n'
        ...
            changed or removed: none
            appeared: none
    """
    proc = _run(_suite(tmp_path, move=None), workers)
    assert _HEADLINE not in proc.stdout + proc.stderr, (
        "a quiescent run was called invalid")
    lines = proc.stdout.splitlines()
    assert "6 passed" in lines[_counts_line(lines)], proc.stdout
    assert proc.returncode == 0, proc.stdout


@pytest.mark.parametrize(("move", "named"), [
    ("appeared", "appeared: pkg/new.py"),
    ("removed", "changed or removed: pkg/mod.py"),
])
def test_every_kind_of_move_is_named(tmp_path, move, named):
    """A file that appears or disappears mid-run moves the tree as surely as
    one that is edited: a new module can be imported by code that was loaded
    before it existed, and a deleted one leaves `getsource` reading nothing.
    Named by path, never by content."""
    proc = _run(_suite(tmp_path, move=move), "0")
    assert proc.stdout.count(_HEADLINE) == 1, proc.stdout
    assert named in proc.stdout, proc.stdout
    assert proc.returncode == 1, proc.stdout


def test_an_edit_during_collection_is_caught(tmp_path):
    """The snapshot is taken at session start, before collection imports the
    code. The fixture it replaces took it at the first test's setup, AFTER
    collection had imported every module, so an edit landing in between was
    invisible to it while every getsource in the run read shifted text.

    RED under M7, the snapshot taken in `pytest_collection_finish` instead of
    `pytest_sessionstart` (observed, verbatim):

        AssertionError: an edit during collection went unreported
        assert 0 == 1
         +  where 0 = <built-in method count of str object at 0x...>('THIS
         RUN IS INVALID')
    """
    proc = _run(_suite(tmp_path, move="changed", at_import=True), "0")
    assert proc.stdout.count(_HEADLINE) == 1, (
        "an edit during collection went unreported")
    assert proc.returncode == 1, proc.stdout


def test_a_run_already_exiting_non_zero_keeps_its_own_code(tmp_path):
    """The verdict makes a clean exit dirty; it does not overwrite a code that
    already says something (here 3, from `pytest.exit`), because whoever reads
    that code reads it for a reason of their own.

    RED under M5, any exit status overwritten (`if True:`) (observed,
    verbatim):

        AssertionError: 1 != 3: the run's own exit code was overwritten
        assert 1 == 3
    """
    proc = _run(_suite(tmp_path, move="changed", then_exit=3), "0")
    assert proc.stdout.count(_HEADLINE) == 1, proc.stdout
    assert proc.returncode == 3, (
        f"{proc.returncode} != 3: the run's own exit code was overwritten")


def test_without_a_terminal_the_verdict_goes_to_stderr(tmp_path):
    """`-p no:terminal` silences pytest, not this: a run that is invalid and
    says nothing but "exit 1" sends its reader hunting for a failure that is
    not there, which is the hunt #250 was. (No `-q` here: it is the terminal
    plugin's own option, and without the plugin it is a usage error.)

    RED under M9, the stderr fallback dropped (observed, verbatim):

        AssertionError: an invalid run said nothing
        assert 0 == 1
    """
    proc = _run(_suite(tmp_path, move="changed"), "0", "-p", "no:terminal")
    assert proc.stderr.count(_HEADLINE) == 1, "an invalid run said nothing"
    assert "changed or removed: pkg/mod.py" in proc.stderr, proc.stderr
    assert proc.returncode == 1, proc.stderr


def test_the_suite_watches_its_own_source_tree(request):
    """The wiring the throwaway suites above cannot see: THIS run registers
    the guard, over `server/astrodeck`, and on an xdist worker it holds no
    snapshot, because the controller speaks for the whole run.

    Run under xdist, this lands on a worker and grades the worker arm; run
    with -n0, it grades the other one. A worker's own report would be
    invisible, which is why the worker arm is graded here, by its state,
    and not through any output.

    RED under M6, the worker check dropped, the file run with `-n 2`: this
    case failed and the other nine passed, which is the point above - with
    the check gone, each worker's report went nowhere and nothing in the
    output changed (observed, verbatim):

        AssertionError: an xdist worker took its own snapshot; the controller
        alone speaks for the run
        assert {WindowsPath('C:/.../server/astrodeck/__init__.py'):
        1790033688534024500, ...} is None

    RED under M8, the plugin not registered in `pytest_configure` (observed,
    verbatim):

        AssertionError: this run has no tree guard
        assert None is not None
    """
    guard = request.config.pluginmanager.get_plugin("astrodeck-tree-guard")
    assert guard is not None, "this run has no tree guard"
    root = Path(__file__).resolve().parents[1] / "astrodeck"
    assert guard.root == root
    if hasattr(request.config, "workerinput"):
        assert guard._before is None, (
            "an xdist worker took its own snapshot; the controller alone "
            "speaks for the run")
    else:
        assert root / "hub.py" in guard._before, (
            "the controller's snapshot does not cover the package")
