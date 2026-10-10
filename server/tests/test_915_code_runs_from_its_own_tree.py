# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#915: code that runs from a worktree imports that worktree's astrodeck.

The virtualenv's ``astrodeck`` is an editable install of ONE checkout, the
main one. Python puts a script's own directory first on ``sys.path`` and the
editable finder last, so a script run by path from anywhere but a checkout's
``server/`` imports the main tree's code. A benchmark, a probe or a mutation
proof run from a worktree then measured code that was not in the worktree, and
printed a result for it. ``python -m`` and ``python -c`` from ``server/`` put
that directory first and hid the difference. In the case that found it (a
microbenchmark in a scratch directory, WP-153) the worktree held the fix and
the script printed the UNFIXED numbers; only a missing attribute gave it away.

Three places fix it, and each is graded by what a process does, not by what
the source says. A throwaway checkout holds a DECOY ``astrodeck`` beside each
script copy, so "imported its own tree" is a path a child process prints, and
the answer cannot be the real package by accident:

* the scripts in the repository that import astrodeck put their own
  checkout's ``server/`` first (test_a_script_imports_the_astrodeck_in_its_own_
  checkout);
* ``scripts/gate_run.py`` runs its command with ``<tree>/server`` first on
  ``PYTHONPATH``, so a script that command runs by path, and every child it
  starts, follows (the ``test_the_gate_command_*`` cases);
* ``tests/conftest.py`` does the same for the suite, so a child a test starts
  without an environment of its own imports this checkout (test_a_child_
  process_a_test_starts_imports_this_checkout). That case can be red only in a
  linked worktree, which is where the defect lives: in the main checkout the
  editable install IS this tree.

MUTANTS, each run from a byte backup of the production file, restored with a
copy and compared by md5sum afterwards. The failing line each printed is in
the docstring of the case that caught it.

  P1 gallery_benchmark.py: the ``sys.path.insert`` replaced by ``pass``.
  P2 seed_session.py: the same.
  P3 bench_am5_pulse_walk.py: the same.
  P4 gate_run.py: ``child_env`` returns the environment unchanged.
  P5 gate_run.py: the caller's PYTHONPATH dropped instead of kept behind.
  P6 conftest.py: the PYTHONPATH assignment removed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SERVER = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[2]

#: The scripts in the repository that import astrodeck and are run by path.
#: Each is copied to the same relative place in a throwaway checkout.
_SCRIPTS = [
    "tools/gallery_benchmark.py",
    "tools/ui_probe/seed_session.py",
    "server/tools/bench_am5_pulse_walk.py",
]

#: Runs a script's module-level code (``run_name`` is not ``__main__``, so its
#: ``main()`` does not run), then reports which astrodeck an import finds.
_PROBE = """\
import runpy, sys
runpy.run_path(sys.argv[1], run_name="pin_probe")
import astrodeck
print(astrodeck.__file__)
"""


def _bare_env() -> dict[str, str]:
    """This process's environment without anything that steers imports: the
    bare run the issue is about, not the suite's own."""
    return {k: v for k, v in os.environ.items()
            if k != "PYTHONPATH" and not k.startswith("PYTEST_")}


def _checkout(root: Path) -> Path:
    """A throwaway checkout whose ``server/astrodeck`` is a decoy."""
    decoy = root / "server" / "astrodeck"
    decoy.mkdir(parents=True)
    (decoy / "__init__.py").write_text('DECOY = "this checkout"\n',
                                       encoding="utf-8")
    return root


def _same(found: str, expected: Path) -> bool:
    return Path(found.strip()).resolve() == expected.resolve()


@pytest.mark.parametrize("script", _SCRIPTS, ids=[Path(s).stem for s in _SCRIPTS])
def test_a_script_imports_the_astrodeck_in_its_own_checkout(tmp_path, script):
    """The script, run from a directory that is not its checkout and with no
    PYTHONPATH, imports the astrodeck that sits beside it. The venv's
    editable install is the only other astrodeck this child can see, so the
    real package is what an unpinned script finds.

    RED under P1, P2 and P3, the pin removed from each script in turn
    (observed, verbatim, [gallery_benchmark]):

        AssertionError: imported <main checkout>\\server\\astrodeck\\__init__.py,
        not the astrodeck beside the script
    """
    repo = _checkout(tmp_path / "repo")
    copy = repo / script
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_REPO / script, copy)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    done = subprocess.run([sys.executable, "-c", _PROBE, str(copy)],
                          cwd=elsewhere, env=_bare_env(), capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    expected = repo / "server" / "astrodeck" / "__init__.py"
    assert _same(done.stdout, expected), (
        f"imported {done.stdout.strip()}, not the astrodeck beside the script")


def test_a_script_copied_alone_adds_nothing_to_the_path(tmp_path):
    """bench_am5_pulse_walk.py is documented as one file that may be copied to
    the rig alone. With no ``astrodeck`` beside it there is nothing to put
    first, and the directory above it (here a stand-in for a home directory
    full of other people's modules) is left off ``sys.path``.

    RED if the pin were unconditional (observed with the ``is_dir`` test
    removed): the stand-in directory appears on ``sys.path``.
    """
    home = tmp_path / "home"
    (home / "rig").mkdir(parents=True)
    copy = home / "rig" / "bench_am5_pulse_walk.py"
    shutil.copy2(_SERVER / "tools" / "bench_am5_pulse_walk.py", copy)
    probe = ("import runpy, sys\n"
             "from pathlib import Path\n"
             "runpy.run_path(sys.argv[1], run_name='pin_probe')\n"
             "home = Path(sys.argv[2]).resolve()\n"
             "print(any(Path(p).resolve() == home for p in sys.path if p))\n")
    done = subprocess.run([sys.executable, "-c", probe, str(copy), str(home)],
                          cwd=tmp_path, env=_bare_env(), capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "False", \
        "a directory with no astrodeck in it was put on sys.path"


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "astrodeck_gate_run_915", _REPO / "scripts" / "gate_run.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args],
                   capture_output=True, text=True, check=True)


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """A linked worktree (the gate refuses the main checkout) whose
    ``server/astrodeck`` is the decoy, the way a work package's copy has the
    real one."""
    main = tmp_path / "main"
    main.mkdir()
    _git(tmp_path, "init", "-q", str(main))
    _git(main, "config", "user.email", "t@example.invalid")
    _git(main, "config", "user.name", "t")
    _git(main, "config", "core.autocrlf", "false")
    _checkout(main)
    (main / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "one")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "task", str(wt))
    return wt


#: A command the gate runs by path from outside the tree: where does an import
#: of astrodeck land, and what PYTHONPATH did the command start with?
_REPORT = """\
import json, os, sys
import astrodeck
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump({"astrodeck": astrodeck.__file__,
               "pythonpath": os.environ.get("PYTHONPATH")}, f)
"""


def _gate_reports(wt: Path, tmp_path: Path) -> dict:
    script = tmp_path / "report.py"
    script.write_text(_REPORT, encoding="utf-8")
    out = tmp_path / "report.json"
    code = gate.main(["gate_run.py", "--tree", str(wt), "--",
                      sys.executable, str(script), str(out)])
    assert code == 0, f"the gate exited {code}"
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_gate_command_imports_the_trees_astrodeck(worktree, tmp_path,
                                                      monkeypatch):
    """A script the gate command runs by path, from outside the tree, imports
    the tree's astrodeck: the gate puts ``<tree>/server`` on PYTHONPATH.

    RED under P4, ``child_env`` returning the environment unchanged
    (observed, verbatim):

        AssertionError: the command imported <main checkout>\\server\\astrodeck\\__init__.py
    """
    monkeypatch.delenv("PYTHONPATH", raising=False)
    got = _gate_reports(worktree, tmp_path)
    assert _same(got["astrodeck"], worktree / "server" / "astrodeck" / "__init__.py"), \
        f"the command imported {got['astrodeck']}"


def test_the_gate_keeps_the_callers_pythonpath_behind_the_trees(
        worktree, tmp_path, monkeypatch):
    """The tree's ``server/`` goes first and the caller's entries follow, with
    no second copy of the server directory when the caller already named it.

    RED under P5, the caller's PYTHONPATH dropped (observed, verbatim):

        AssertionError: ['<tree>/server'] != ['<tree>/server', 'KEEP-A', 'KEEP-B']
    """
    server = str((worktree / "server").resolve())
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["KEEP-A", server, "KEEP-B"]))
    got = _gate_reports(worktree, tmp_path)
    assert got["pythonpath"].split(os.pathsep) == [server, "KEEP-A", "KEEP-B"]


def test_a_tree_without_an_astrodeck_keeps_the_environment_it_came_in(
        tmp_path, monkeypatch):
    """Nothing to put first: the gate must not invent a PYTHONPATH for a tree
    that has no ``server/astrodeck`` (the rest of the gate's cases, and any
    other repository it is pointed at)."""
    tree = tmp_path / "bare"
    (tree / "server").mkdir(parents=True)
    monkeypatch.setenv("PYTHONPATH", "KEEP-A")
    assert gate.child_env(tree)["PYTHONPATH"] == "KEEP-A"
    monkeypatch.delenv("PYTHONPATH")
    assert "PYTHONPATH" not in gate.child_env(tree)


def test_a_child_process_a_test_starts_imports_this_checkout(tmp_path):
    """The suite's own children follow: a bare ``python -c`` that inherits
    this process's environment, started away from ``server/``, finds the
    astrodeck this very test imported. conftest.py puts ``server/`` first on
    PYTHONPATH. In a linked worktree without that, the child imports the main
    tree's package, which is the defect; in the main checkout the two are the
    same tree and this passes either way.

    RED under P6, the assignment removed from conftest.py, run in a linked
    worktree (observed, verbatim):

        AssertionError: the child imported <main checkout>\\server\\astrodeck\\__init__.py,
        this process <worktree>\\server\\astrodeck\\__init__.py
    """
    import astrodeck
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    done = subprocess.run(
        [sys.executable, "-c", "import astrodeck; print(astrodeck.__file__)"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    assert _same(done.stdout, Path(astrodeck.__file__)), (
        f"the child imported {done.stdout.strip()}, this process "
        f"{astrodeck.__file__}")
