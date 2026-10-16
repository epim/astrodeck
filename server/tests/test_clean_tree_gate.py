# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The clean-tree gate must grade HEAD, and must not eat the link's target (#78).

Two claims, and both have already failed once in this repository in some form:

  * the command it runs must see the committed tree and nothing else. A whole
    pass of `tsc -b` reported clean on a tree that carried another session's
    uncommitted file, which is the defect the gate exists for. A gate that
    quietly ran in the working tree would report exactly the same green.
  * taking the worktree down must not follow the `node_modules` link. The
    gate links rather than copies, so the link's target is the live
    dependency tree, and a recursive delete over a Windows junction is not
    reliably the same operation as removing the junction. Issue #88 is what
    an unguarded delete costs here.

These drive the real module against a real throwaway git repository. A double
would agree with whatever the code did.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_GATE = Path(__file__).resolve().parents[2] / "scripts" / "clean_tree_gate.py"
_spec = importlib.util.spec_from_file_location("astrodeck_clean_tree_gate", _GATE)
gate = importlib.util.module_from_spec(_spec)
sys.modules["astrodeck_clean_tree_gate"] = gate
_spec.loader.exec_module(gate)


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, check=True)
    return out.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repository with one commit, a linked directory the gate expects,
    and the working-tree edit the gate must NOT show the command."""
    r = tmp_path / "repo"
    (r / "ui" / "src").mkdir(parents=True)
    (r / "ui" / "node_modules" / "pkg").mkdir(parents=True)
    (r / "ui" / "node_modules" / "pkg" / "index.js").write_text("module.exports=1\n",
                                                                encoding="utf-8")
    (r / "ui" / "src" / "committed.txt").write_text("from HEAD\n", encoding="utf-8")
    (r / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _git(r.parent, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    _git(r, "add", "--", ".gitignore", "ui/src/committed.txt")
    _git(r, "commit", "-q", "-m", "one")
    # The working tree now differs from HEAD in the two ways that matter: a
    # file that exists only here, and a committed file with different bytes.
    (r / "ui" / "src" / "uncommitted.txt").write_text("only in the worktree\n",
                                                      encoding="utf-8")
    (r / "ui" / "src" / "committed.txt").write_text("edited, not committed\n",
                                                    encoding="utf-8")
    return r


def test_the_command_sees_head_and_not_the_working_tree(repo, monkeypatch):
    """The whole point. The command lists the source directory and reads the
    committed file; it must find neither the uncommitted file nor the edit.

    MUTATION: `cwd=str(root / cwd)` instead of the worktree. Observed: this
    case alone fails, 1 failed / 15 passed - the command saw
    `uncommitted.txt` and read the working tree's bytes.
    """
    monkeypatch.setattr(gate, "LINKED", ("ui/node_modules",))
    monkeypatch.chdir(repo)
    out = repo / "seen.txt"
    script = ("import os,pathlib;"
              "p=pathlib.Path('src');"
              f"open(r'{out}','w').write("
              "','.join(sorted(os.listdir(p)))+'|'+ (p/'committed.txt').read_text())")
    assert gate.main(["clean_tree_gate.py", "--cwd", "ui", "--",
                      sys.executable, "-c", script]) == 0
    listing, content = out.read_text(encoding="utf-8").split("|", 1)
    assert "uncommitted.txt" not in listing, (
        f"the command ran against the working tree: it saw {listing}")
    assert content.strip() == "from HEAD", (
        f"the command read the working tree's bytes, not HEAD's: {content!r}")


def test_the_linked_dependency_tree_is_reachable_from_the_worktree(repo, monkeypatch):
    """The link has to be a real one, or every gate run fails to resolve its
    tools and the gate is worse than no gate.

    MUTATION: replace the `_link(target, link)` call with `pass`. Observed:
    this case alone fails, 1 failed / 15 passed - the command recorded
    `missing`.
    """
    monkeypatch.setattr(gate, "LINKED", ("ui/node_modules",))
    monkeypatch.chdir(repo)
    out = repo / "seen.txt"
    script = ("import pathlib;"
              "p=pathlib.Path('ui/node_modules/pkg/index.js');"
              f"open(r'{out}','w').write(p.read_text() if p.is_file() else 'missing')")
    assert gate.main(["clean_tree_gate.py", "--", sys.executable, "-c", script]) == 0
    assert out.read_text(encoding="utf-8").strip() == "module.exports=1", (
        "the linked dependency directory was not reachable from inside the worktree")


def test_taking_the_worktree_down_leaves_the_link_target_alone(repo, monkeypatch):
    """Issue #88's shape, on the one path in this tool that can reach live
    files. After a completed run the real `node_modules` must still be there.

    MUTATION: `_unlink` becomes `shutil.rmtree(link)`. Observed on Windows:
    7 failed / 9 passed, this case among them - `rmtree` descended through
    the junction and emptied the target, so the fixture's `index.js` was
    gone. That is the hazard, reproduced: the same call against a real run
    would be walking the live `node_modules`.
    """
    monkeypatch.setattr(gate, "LINKED", ("ui/node_modules",))
    monkeypatch.chdir(repo)
    assert gate.main(["clean_tree_gate.py", "--", sys.executable, "-c", "pass"]) == 0
    kept = repo / "ui" / "node_modules" / "pkg" / "index.js"
    assert kept.is_file(), (
        "the dependency tree the gate linked into the worktree was deleted with it")
    assert kept.read_text(encoding="utf-8") == "module.exports=1\n"


def test_the_worktree_is_gone_and_so_is_its_registration(repo, monkeypatch):
    """A gate that leaves worktrees behind fills the temporary directory and,
    worse, leaves `git worktree list` describing trees that are not there.

    MUTATION: `worktree remove --force <tree>` becomes `worktree list`.
    Observed: this case alone fails, 1 failed / 15 passed - the listing still
    named the gate's tree.
    """
    monkeypatch.setattr(gate, "LINKED", ("ui/node_modules",))
    monkeypatch.chdir(repo)
    assert gate.main(["clean_tree_gate.py", "--", sys.executable, "-c", "pass"]) == 0
    listing = _git(repo, "worktree", "list")
    assert "astrodeck-clean-gate-" not in listing, (
        f"a gate worktree outlived its run:\n{listing}")


def test_the_command_s_exit_code_is_the_gate_s(repo, monkeypatch):
    """A gate that swallows a failure is the green check on the wrong tree
    with extra steps.

    MUTATION: `return 0` instead of the completed process's returncode.
    Observed: 3 failed / 13 passed - this case with "a failing gate command
    reported 0", and the two cases that read what the command wrote, because
    the command never ran at all.
    """
    monkeypatch.setattr(gate, "LINKED", ("ui/node_modules",))
    monkeypatch.chdir(repo)
    code = gate.main(["clean_tree_gate.py", "--",
                      sys.executable, "-c", "import sys; sys.exit(3)"])
    assert code == 3, f"a failing gate command reported {code}"


def test_a_missing_dependency_directory_stops_the_run(repo, monkeypatch):
    """Rather than running a gate whose tools cannot resolve and reading its
    import error as a source failure."""
    monkeypatch.setattr(gate, "LINKED", ("ui/node_modules", "ui/not_installed"))
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit) as e:
        gate.main(["clean_tree_gate.py", "--", sys.executable, "-c", "pass"])
    assert "not_installed" in str(e.value)


def test_it_refuses_to_manage_a_path_outside_the_temporary_directory(tmp_path):
    """`_require_temp` is the one guard between this tool and a recursive
    delete of something it did not create.

    MUTATION: the guard's condition becomes `if False`. Observed: this case
    alone fails, 1 failed / 15 passed - `Path.home()` was accepted.
    """
    import tempfile
    with pytest.raises(SystemExit):
        gate._require_temp(Path.home())
    with pytest.raises(SystemExit):
        gate._require_temp(Path(tempfile.gettempdir()))
    inside = Path(tempfile.gettempdir()) / "astrodeck-clean-gate-probe"
    assert gate._require_temp(inside) == inside.resolve()


@pytest.mark.parametrize("line,expected", [
    (" M ui/src/a.ts", ["ui/src/a.ts"]),
    ("?? ui/src/new.tsx", ["ui/src/new.tsx"]),
    ("R  ui/src/old.ts -> ui/src/new.ts", ["ui/src/new.ts"]),
    ("MM tools/photosphere_sim/score.py", ["tools/photosphere_sim/score.py"]),
    (" M docs/whatever.md", []),
    (" M ui/srcextra/a.ts", []),
    (" M ui/src", ["ui/src"]),
])
def test_dirty_under_reads_the_porcelain_forms(line, expected):
    """`ui/srcextra` is the one that matters: a prefix test written with a
    bare `startswith` calls it dirty, and the gate would then announce an
    isolation it is not performing.

    MUTATION: compare with a bare `startswith(p)`. Observed: the
    `ui/srcextra/a.ts` parameter alone fails, 1 failed / 15 passed.
    """
    assert gate.dirty_under(line, ("ui/src", "server/astrodeck", "tools")) == expected


def test_dirty_under_reports_nothing_for_a_clean_tree():
    assert gate.dirty_under("", ("ui/src",)) == []


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows thing")
def test_the_link_is_a_junction_and_rmdir_is_what_removes_it(tmp_path):
    """Directly, without a git repository: the property the teardown relies
    on. `os.rmdir` on a junction removes the entry; the target keeps its
    contents.

    MUTATION: make `_link` copy the tree instead. Observed: `os.rmdir` raises
    "Directory not empty" and this fails there, which is the signal that the
    teardown's safety argument no longer holds."""
    target = tmp_path / "target"
    target.mkdir()
    (target / "keep.txt").write_text("kept\n", encoding="utf-8")
    link = tmp_path / "link"
    gate._link(target, link)
    assert (link / "keep.txt").read_text(encoding="utf-8") == "kept\n"
    gate._unlink(link)
    assert not link.exists()
    assert (target / "keep.txt").read_text(encoding="utf-8") == "kept\n"


# ------------------------------------- the command imports HEAD's astrodeck (#1027)

#: What the gated HEAD's decoy astrodeck says about itself. A child that
#: imports anything else (the virtualenv's editable install is the main
#: checkout's real package) has no such attribute.
_DECOY = "the astrodeck committed at HEAD of the gated repository"

#: Run by path from inside the gated tree: which astrodeck does an import find,
#: and what PYTHONPATH did the command start with?
_REPORT = """\
import json, os, sys
import astrodeck
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump({"decoy": getattr(astrodeck, "DECOY", None),
               "astrodeck": astrodeck.__file__,
               "pythonpath": os.environ.get("PYTHONPATH")}, f)
"""


@pytest.fixture
def decoy_repo(repo: Path) -> Path:
    """`repo` with a DECOY astrodeck committed at `server/astrodeck`, so which
    tree a child imported is a value it prints and the real package cannot be
    the answer by accident (test_915_code_runs_from_its_own_tree.py does the
    same for scripts/gate_run.py)."""
    pkg = repo / "server" / "astrodeck"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f"DECOY = {_DECOY!r}\n", encoding="utf-8")
    _git(repo, "add", "--", "server/astrodeck/__init__.py")
    _git(repo, "commit", "-q", "-m", "decoy astrodeck")
    return repo


def _tree_with_server(root: Path, astrodeck: bool) -> Path:
    """A stand-in gated tree holding `server/`, with or without an astrodeck."""
    tree = root / ("with" if astrodeck else "without")
    (tree / "server" / ("astrodeck" if astrodeck else "other")).mkdir(parents=True)
    return tree


def test_a_python_command_imports_the_astrodeck_in_the_gated_tree(
        decoy_repo, tmp_path, monkeypatch):
    """The gate grades HEAD, so a python script it runs by path must import
    HEAD's astrodeck. The virtualenv's is an editable install of the MAIN
    checkout and the script's own directory is not the tree's `server/`, so
    without the tree first on PYTHONPATH the child grades the main tree: the
    green check on the wrong tree the gate exists to prevent (#1027). The
    caller's own PYTHONPATH follows the tree's.

    MUTATION: drop `env=child_env(tree)` from the `subprocess.run` in `run`
    (the gated command then inherits the caller's environment). Observed,
    verbatim: this case alone fails (the `child_env` cases below test the
    function, not that `run` hands its result on) -

        AssertionError: the command imported <main checkout>\\server\\astrodeck\\__init__.py,
        not the astrodeck committed at HEAD of the gated repository
    """
    monkeypatch.setattr(gate, "LINKED", ("ui/node_modules",))
    monkeypatch.setenv("PYTHONPATH", "KEEP-A")
    monkeypatch.chdir(decoy_repo)
    script = tmp_path / "report.py"
    script.write_text(_REPORT, encoding="utf-8")
    out = tmp_path / "report.json"
    assert gate.main(["clean_tree_gate.py", "--cwd", "ui", "--",
                      sys.executable, str(script), str(out)]) == 0
    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["decoy"] == _DECOY, (
        f"the command imported {got['astrodeck']}, not the astrodeck "
        f"committed at HEAD of the gated repository")
    entries = got["pythonpath"].split(os.pathsep)
    assert Path(entries[0]).parts[-2:] == ("tree", "server"), entries
    assert entries[1:] == ["KEEP-A"], entries


def test_child_env_puts_the_trees_server_first_and_keeps_the_callers_entries(
        tmp_path, monkeypatch):
    """The tree's `server/` goes first and the caller's entries follow, with no
    second copy of the server directory when the caller already named it, and
    this process's own environment is left as it was.

    MUTATION: `rest = []` (the caller's entries dropped). Observed: this case,
    the end-to-end case above and the one holding the two scripts together
    all fail, this one with -

        assert ['<tree>/server'] == ['<tree>/server', 'KEEP-A', 'KEEP-B']

    - and, with the `!= here` filter dropped instead, with the server
    directory listed twice.
    """
    tree = _tree_with_server(tmp_path, astrodeck=True)
    server = str((tree / "server").resolve())
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["KEEP-A", server, "KEEP-B"]))
    env = gate.child_env(tree)
    assert env["PYTHONPATH"].split(os.pathsep) == [server, "KEEP-A", "KEEP-B"]
    assert os.environ["PYTHONPATH"] == os.pathsep.join(["KEEP-A", server, "KEEP-B"])
    monkeypatch.delenv("PYTHONPATH")
    assert gate.child_env(tree)["PYTHONPATH"] == server


def test_child_env_leaves_a_tree_without_an_astrodeck_alone(tmp_path, monkeypatch):
    """Nothing to put first: a tree whose `server/` holds no astrodeck, or has
    no `server/` at all, keeps the environment the gate came in with. The
    gate is also pointed at trees that are not this repository."""
    monkeypatch.setenv("PYTHONPATH", "KEEP-A")
    bare = _tree_with_server(tmp_path, astrodeck=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    for tree in (bare, empty):
        assert gate.child_env(tree)["PYTHONPATH"] == "KEEP-A", tree.name
    monkeypatch.delenv("PYTHONPATH")
    for tree in (bare, empty):
        assert "PYTHONPATH" not in gate.child_env(tree), tree.name


def test_the_two_gate_scripts_build_the_same_child_environment(tmp_path, monkeypatch):
    """scripts/gate_run.py and scripts/clean_tree_gate.py each carry
    `child_env`, because each is copied on its own and cannot import the
    other. This holds them together (test_w15_gate_run.py does the same for
    `checkout_kind`): the same tree and the same caller PYTHONPATH give the
    same environment from both, including a caller entry that names the
    server directory in another spelling.

    MUTATION: drop the `!= here` filter from clean_tree_gate.py's copy only.
    Observed: this case fails on the caller entry that already names the
    server directory (it appears twice), and the direct case above fails too.
    """
    spec = importlib.util.spec_from_file_location(
        "astrodeck_gate_run_1027", _GATE.with_name("gate_run.py"))
    other = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = other
    spec.loader.exec_module(other)
    with_astrodeck = _tree_with_server(tmp_path, astrodeck=True)
    server = str((with_astrodeck / "server").resolve())
    trees = [with_astrodeck, _tree_with_server(tmp_path, astrodeck=False),
             tmp_path / "no-such-tree"]
    for raw in (None, "", "KEEP-A", os.pathsep.join(["KEEP-A", server, "KEEP-B"]),
                server + os.sep + ".", os.pathsep.join([server, server])):
        if raw is None:
            monkeypatch.delenv("PYTHONPATH", raising=False)
        else:
            monkeypatch.setenv("PYTHONPATH", raw)
        for tree in trees:
            assert gate.child_env(tree) == other.child_env(tree), (tree.name, raw)
