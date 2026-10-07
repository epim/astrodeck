# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A gate run refuses the shared tree and records that the tree stayed quiet (#254).

Parallel agents have mutated, deleted and rewritten files in one working
tree while another agent's suite ran, three times, and each time the run
graded code that was not the code under review. Backlog ruling D-01
(owner-approved 2026-09-30) closes when three things hold: isolation is
enforced where a script can enforce it, the discipline text says "in a copy
of the tree", and every gate run records that the tree was quiet. This file
is the first and the third; scripts/gate_run.py is the wrapper under test.

These drive the real module against a real throwaway repository and a real
`git worktree add`, because a double would agree with whatever the code did.
The case the design turns on is the one a porcelain-only comparison cannot
see: in a work-package worktree the owned files are ALREADY modified, so a
mutant applied to one and then restored leaves `git status` byte-identical,
and only a size-and-mtime stamp notices that the file was touched.

MUTANTS, each run from a byte backup of scripts/gate_run.py with the file
restored from it, compared by sha256, and the mutant text grepped out
afterwards; every one went RED and the failure each printed is recorded in
the case that caught it:

  G1 THE NAMED MUTANT, drop the stamp and compare porcelain text only
     (`stamp_moved` returns []). test_a_restored_mutant_under_an_already_
     modified_file_is_a_moved_tree, and two cases that read the same verdict.
  G2 `moved` always empty (the list built from the stamp AND porcelain is
     replaced by `[]`). The same case, and test_a_file_that_appears_or_
     vanishes_is_named.
  G3 the main-checkout refusal removed. test_the_main_checkout_is_refused_
     without_shared_tree.
  G4 the leftover-mutation sentinel check removed. test_a_leftover_mutation_
     file_refuses_the_start.
  G5 a moved tree exits with the command's code even when that is 0 (the
     final `return` becomes `return rc`). Six cases, the first of them the
     G1 case, on the exit-code assertion.
  G6 the porcelain half dropped (`porcelain_moved` returns []).
     test_a_git_add_under_the_run_moves_the_porcelain.
  G7 HEAD left out of `quiet`. test_a_commit_under_the_run_moves_head, which
     commits NOTHING but an empty commit, so that HEAD is the only thing
     that moved: with a commit that also changed files the porcelain
     catches it and the mutant survives (the first version of this case did
     exactly that, and G7 passed it).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load("astrodeck_gate_run", "gate_run.py")
mutate = _load("astrodeck_gate_run_mutate", "mutate.py")

OWNED = "OWNED = 2\n"
LONG_AGO_NS = 1_700_000_000 * 10**9          # far from any clock tick of this run


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, check=True)
    return out.stdout


def _py(script: str, *args: str) -> list[str]:
    return [sys.executable, "-c", script, *args]


@pytest.fixture
def main_repo(tmp_path: Path) -> Path:
    """The repository's own checkout: the tree agents share."""
    r = tmp_path / "main"
    r.mkdir()
    _git(r.parent, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    _git(r, "config", "core.autocrlf", "false")
    (r / "sub").mkdir()
    (r / "owned.py").write_text("OWNED = 1\n", encoding="utf-8")
    (r / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
    (r / "sub" / "inner.py").write_text("INNER = 1\n", encoding="utf-8")
    (r / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    _git(r, "add", "--", "owned.py", "other.py", "sub/inner.py", ".gitignore")
    _git(r, "commit", "-q", "-m", "one")
    return r


@pytest.fixture
def tree(main_repo: Path, tmp_path: Path) -> Path:
    """A linked worktree the way a work package has one: its owned file is
    already modified before any gate starts, with a modification time far in
    the past so a rewrite cannot land inside the clock tick of the setup."""
    wt = tmp_path / "wt"
    _git(main_repo, "worktree", "add", "-q", "-b", "task", str(wt))
    (wt / "owned.py").write_text(OWNED, encoding="utf-8")
    os.utime(wt / "owned.py", ns=(LONG_AGO_NS, LONG_AGO_NS))
    return wt


def _gate(tree: Path, tmp_path: Path, command: list[str], *flags: str):
    """Run the wrapper in-process; the record goes OUTSIDE the tree."""
    record = tmp_path / "record.json"
    record.unlink(missing_ok=True)
    code = gate.main(["gate_run.py", "--tree", str(tree), "--record", str(record),
                      *flags, "--", *command])
    data = json.loads(record.read_text(encoding="utf-8")) if record.exists() else None
    return code, data


def _last_line(text: str) -> str:
    return [line for line in text.splitlines() if line.strip()][-1]


REWRITE_SAME_BYTES = ("import pathlib, sys;"
                      "p = pathlib.Path(sys.argv[1]);"
                      "p.write_bytes(p.read_bytes())")


def test_a_restored_mutant_under_an_already_modified_file_is_a_moved_tree(
        tree, tmp_path, capsys):
    """The case the stamp exists for. The command applies a mutant to a file
    that is already modified and restores it: the bytes and so `git status`
    are the same afterwards, the modification time is not. The run is
    invalid, the exit is 1 although the command exited 0, and the path is
    named in both the record and the last line.

    RED under G1, the stamp dropped (`stamp_moved` returning `[]`, so only
    porcelain text is compared), which stays identical here (observed,
    verbatim):

        AssertionError: a file rewritten under an already-modified tree went
        unnoticed: porcelain alone cannot see it
        assert True is False

    RED under G2, `moved` always empty: here the same assertion as G1, since
    the porcelain is identical and so `quiet` stays true; the list itself is
    graded by test_a_file_that_appears_or_vanishes_is_named (observed,
    verbatim, there):

        AssertionError: []
        assert [] == ['newcomer.txt', 'other.py']

    RED under G5, the exit left at the command's own code (observed,
    verbatim):

        AssertionError: 0 != 1: a run on a moving tree must not exit 0
        assert 0 == 1
    """
    code, rec = _gate(tree, tmp_path, _py(REWRITE_SAME_BYTES, str(tree / "owned.py")))
    out = capsys.readouterr().out
    assert rec["porcelain_before_sha256"] == rec["porcelain_after_sha256"], (
        "the premise of this case is that `git status` cannot see the rewrite")
    assert rec["quiet"] is False, (
        "a file rewritten under an already-modified tree went unnoticed: "
        "porcelain alone cannot see it")
    assert rec["moved"] == ["owned.py"], f"the moved file was not named: {rec['moved']}"
    assert code == 1, f"{code} != 1: a run on a moving tree must not exit 0"
    last = _last_line(out)
    assert last.startswith("gate run: THIS RUN IS INVALID: the tree moved:"), last
    assert "owned.py" in last, last


def test_a_quiet_run_records_quiet_and_passes_the_command_s_exit_code_through(
        tree, tmp_path, capsys):
    """A run that touches nothing is recorded as quiet, with the count of
    files it watched, and the command's own exit code is the gate's. A gate
    that swallowed a failing suite's exit status would be the green check on
    the wrong tree with extra steps.

    RED when the exit is forced to 0 on a quiet tree, or the quiet flag is
    left false: this case fails on the assertion that names which.
    """
    ok, rec = _gate(tree, tmp_path, _py("pass"))
    assert ok == 0
    assert rec["quiet"] is True and rec["moved"] == []
    assert rec["exit_code"] == 0
    assert rec["files_watched"] >= 4
    assert _last_line(capsys.readouterr().out) == (
        f"gate run: tree QUIET ({rec['files_watched']} files watched)")

    failed, rec = _gate(tree, tmp_path, _py("import sys; sys.exit(3)"))
    assert failed == 3, f"a failing command reported {failed}"
    assert rec["quiet"] is True and rec["exit_code"] == 3


def test_a_moved_tree_keeps_a_failing_command_s_own_code(tree, tmp_path):
    """The verdict turns a clean exit dirty; it does not replace a code that
    already says something (the same convention as the #250 plugin in
    conftest.py)."""
    code, rec = _gate(tree, tmp_path, _py(
        REWRITE_SAME_BYTES + "; sys.exit(3)", str(tree / "owned.py")))
    assert rec["quiet"] is False
    assert code == 3, f"{code} != 3: the command's own failure was overwritten"
    assert rec["exit_code"] == 3


def test_the_record_is_names_only_and_carries_the_schema(tree, tmp_path):
    """The record names paths and hashes, never contents: it is the kind of
    file that ends up pasted into an issue."""
    (tree / "owned.py").write_text("SECRET_MARKER = 'do-not-record-me'\n",
                                   encoding="utf-8")
    os.utime(tree / "owned.py", ns=(LONG_AGO_NS, LONG_AGO_NS))
    code, rec = _gate(tree, tmp_path, _py(REWRITE_SAME_BYTES, str(tree / "owned.py")))
    text = (tmp_path / "record.json").read_text(encoding="utf-8")
    assert "do-not-record-me" not in text
    assert rec["schema_version"] == 1
    assert set(rec) >= {"schema_version", "command", "exit_code", "head", "started",
                        "finished", "quiet", "moved", "porcelain_before_sha256",
                        "porcelain_after_sha256", "isolated"}
    assert rec["head"] == _git(tree, "rev-parse", "HEAD").strip()
    assert rec["isolated"] is True
    assert rec["started"] <= rec["finished"]
    assert len(rec["porcelain_before_sha256"]) == 64


def test_a_file_that_appears_or_vanishes_is_named(tree, tmp_path):
    """Appeared and vanished move the tree as surely as an edit: a new module
    can be imported by code loaded before it existed, a deleted one leaves a
    source scrape reading nothing."""
    code, rec = _gate(tree, tmp_path, _py(
        "import pathlib, sys;"
        "pathlib.Path(sys.argv[1]).write_text('x');"
        "pathlib.Path(sys.argv[2]).unlink()",
        str(tree / "newcomer.txt"), str(tree / "other.py")))
    assert code == 1
    assert rec["quiet"] is False
    assert rec["moved"] == ["newcomer.txt", "other.py"], rec["moved"]


def test_a_git_add_under_the_run_moves_the_porcelain(tree, tmp_path):
    """Staging a file changes `git status` and no file's size or time: the
    other half of the comparison, and the reason both halves exist.

    RED under G6, the porcelain half dropped (observed, verbatim):

        AssertionError: []
        assert [] == ['owned.py']
    """
    code, rec = _gate(tree, tmp_path, ["git", "-C", str(tree), "add", "--", "owned.py"])
    assert rec["porcelain_before_sha256"] != rec["porcelain_after_sha256"]
    assert rec["quiet"] is False
    assert rec["moved"] == ["owned.py"], rec["moved"]
    assert code == 1


def test_a_commit_under_the_run_moves_head(tree, tmp_path, capsys):
    """A commit mid-run changes what the run is grading even where no byte on
    disk and no line of `git status` differs afterwards from before it. The
    commit is empty so that HEAD is the ONLY thing that moves.

    RED under G7, HEAD left out of `quiet` (observed, verbatim):

        assert True is False
    """
    code, rec = _gate(tree, tmp_path, ["git", "-C", str(tree), "commit", "-q",
                                       "--allow-empty", "-m", "mid-run"])
    assert rec["porcelain_before_sha256"] == rec["porcelain_after_sha256"]
    assert rec["moved"] == []
    assert rec["quiet"] is False
    assert rec["head_after"] != rec["head"]
    assert code == 1
    assert "HEAD" in _last_line(capsys.readouterr().out)


def test_an_ignored_prefix_is_not_watched(tree, tmp_path):
    """A gate that cries wolf gets bypassed, so a path the run legitimately
    writes can be excluded by prefix; without the flag the same write is a
    moved tree, which is the control.
    """
    writer = _py("import pathlib, sys; d = pathlib.Path(sys.argv[1]);"
                 "d.mkdir(exist_ok=True); (d / 'out.txt').write_text('x')",
                 str(tree / "scratch"))
    code, rec = _gate(tree, tmp_path, writer)
    assert code == 1 and rec["moved"] == ["scratch/out.txt"], rec["moved"]
    (tree / "scratch" / "out.txt").unlink()
    (tree / "scratch").rmdir()
    code, rec = _gate(tree, tmp_path, writer, "--ignore", "scratch/")
    assert code == 0 and rec["quiet"] is True, rec["moved"]
    assert rec["moved"] == []


def test_the_main_checkout_is_refused_without_shared_tree(
        main_repo, tree, tmp_path, capsys):
    """Isolation enforced: the repository's own checkout is the tree agents
    share, so a gate wrapped around it is refused (exit 2, nothing run, no
    record), unless the operator says `--shared-tree`, which is recorded as
    isolated:false so the run cannot pass for an isolated one afterwards. A
    linked worktree is recorded isolated:true.

    RED under G3, the refusal removed (observed, verbatim):

        AssertionError: the main checkout was not refused: 0
        assert 0 == 2
    """
    ran = tmp_path / "ran.txt"
    marker = _py("import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('ran')",
                 str(ran))
    code, rec = _gate(main_repo, tmp_path, marker)
    err = capsys.readouterr().err
    assert code == 2, f"the main checkout was not refused: {code}"
    assert not ran.exists(), "the command ran in the shared tree"
    assert rec is None, "a refused run left a record"
    assert "--shared-tree" in err and "#254" in err, err

    code, rec = _gate(main_repo, tmp_path, marker, "--shared-tree")
    assert code == 0 and ran.exists()
    assert rec["isolated"] is False

    code, rec = _gate(tree, tmp_path, _py("pass"))
    assert rec["isolated"] is True


def test_a_directory_that_is_not_a_git_tree_is_refused(tmp_path, capsys):
    """Byte copies are not supported here: without git there is nothing to
    compare, and a gate that records nothing is not this gate."""
    plain = tmp_path / "plain"
    plain.mkdir()
    code, rec = _gate(plain, tmp_path, _py("pass"))
    assert code == 2 and rec is None
    assert "git" in capsys.readouterr().err


@pytest.mark.parametrize("leftover", [
    "owned.py.mutation-state.json",
    "owned.py.mutation-backup",
    "fresh/deeper/thing.py.mutation-state.json",
])
def test_a_leftover_mutation_file_refuses_the_start(tree, tmp_path, capsys, leftover):
    """A run that STARTS while a mutant is in place grades the mutant from its
    first test, and nothing that watches for movement can see it, because
    nothing moves afterwards. scripts/mutate.py leaves both of these beside
    its target, and .gitignore does not hide them.

    RED under G4, the sentinel check removed (observed, verbatim, for each
    parameter):

        AssertionError: a run started over a leftover mutation
        assert 0 == 2
    """
    path = tree / leftover
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    ran = tmp_path / "ran.txt"
    code, rec = _gate(tree, tmp_path, _py(
        "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('ran')", str(ran)))
    assert code == 2, "a run started over a leftover mutation"
    assert not ran.exists(), "the command ran with a mutant in the tree"
    assert rec is None
    err = capsys.readouterr().err
    assert leftover in err and "mutate.py restore" in err, err


def test_the_sentinel_names_are_the_ones_mutate_py_writes():
    """The gate looks for files another script writes; the two must agree or
    the check is a name nothing ever has."""
    target = Path("x.py")
    names = {mutate._state_path(target).name, mutate._backup_path(target).name}
    assert names == {"x.py" + s for s in gate.SENTINEL_SUFFIXES}


def test_the_gate_and_the_mutation_harness_agree_on_what_the_main_checkout_is(
        main_repo, tree, tmp_path, monkeypatch):
    """Two scripts carry the same test, because mutate.py is copied alone and
    cannot import the other; this keeps them from drifting. Checked from the
    root, from a directory below it, from a linked worktree and from a plain
    directory, where git's two printed paths take different forms."""
    monkeypatch.delenv("ASTRODECK_MUTATE_SHARED_TREE", raising=False)
    plain = tmp_path / "plain"
    plain.mkdir()
    below = main_repo / "sub"
    for where, kind in [(main_repo, "main"), (below, "main"),
                        (tree, "linked"), (plain, None)]:
        assert gate.checkout_kind(where) == kind, where
        probe = where / "probe.py"
        probe.write_text("x = 1\n", encoding="utf-8")
        try:
            mutate._refuse_shared_tree(probe)
            refused = False
        except SystemExit:
            refused = True
        finally:
            probe.unlink()
        assert refused == (kind == "main"), (where, kind, refused)


def test_the_command_runs_in_the_requested_subdirectory(tree, tmp_path):
    seen = tmp_path / "cwd.txt"
    code, rec = _gate(tree, tmp_path, _py(
        "import os, pathlib, sys; pathlib.Path(sys.argv[1]).write_text(os.getcwd())",
        str(seen)), "--cwd", "sub")
    assert code == 0
    assert Path(seen.read_text(encoding="utf-8")).resolve() == (tree / "sub").resolve()


def test_a_command_that_cannot_start_is_a_recorded_failure(tree, tmp_path):
    """Not a traceback and not a lost record: the run happened, it failed."""
    code, rec = _gate(tree, tmp_path, ["definitely-not-a-command-254"])
    assert code == 127
    assert rec["exit_code"] == 127 and rec["quiet"] is True


def test_the_command_output_streams_and_the_verdict_is_the_last_line(tree, tmp_path, capfd):
    """The verdict is the last thing printed, so `| tail` shows it, and the
    command's own output is not held back or swallowed."""
    _gate(tree, tmp_path, _py("print('output of the gated command')"))
    out = capfd.readouterr().out
    assert "output of the gated command" in out
    assert _last_line(out).startswith("gate run: tree QUIET (")


def test_the_command_line_entry_point_end_to_end(tree, tmp_path):
    """The `__main__` path, argument parsing and `--`, as a user types it."""
    record = tmp_path / "cli-record.json"
    quiet = subprocess.run(
        [sys.executable, str(_SCRIPTS / "gate_run.py"), "--tree", str(tree),
         "--record", str(record), "--", sys.executable, "-c", "pass"],
        capture_output=True, text=True)
    assert quiet.returncode == 0, quiet.stderr
    assert _last_line(quiet.stdout).startswith("gate run: tree QUIET (")
    assert json.loads(record.read_text(encoding="utf-8"))["quiet"] is True

    moved = subprocess.run(
        [sys.executable, str(_SCRIPTS / "gate_run.py"), "--tree", str(tree),
         "--record", str(record), "--", sys.executable, "-c",
         REWRITE_SAME_BYTES, str(tree / "owned.py")],
        capture_output=True, text=True)
    assert moved.returncode == 1, moved.stdout + moved.stderr
    assert _last_line(moved.stdout).startswith(
        "gate run: THIS RUN IS INVALID: the tree moved: owned.py")
