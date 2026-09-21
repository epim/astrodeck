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
