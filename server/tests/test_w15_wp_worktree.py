# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""One place makes and removes work-package worktrees, and it cannot eat a link's target (#254).

Backlog ruling D-01 (owner-approved 2026-09-30) wants every mutation and
every full-suite run in a copy of the tree. A work package's copy is a linked
worktree, and until now each wave's build script made its own with an inline
`git worktree add` and `mklink`, and removed it however it liked. #254's
comment 3 records what that costs: one agent's `rm -rf` of a generic scratch
path deleted another agent's copy of `server/`. And the removal is the
hazard #88 and #475 are about: a worktree's `ui/node_modules` is a junction
to the live dependency tree, and a recursive delete that follows it empties
the real one. scripts/wp_worktree.py is the one implementation of both:

  * `add` refuses a path that already exists (a unique name per task, never
    reused), links `ui/node_modules` the way scripts/clean_tree_gate.py does,
    and never touches a path it did not create;
  * `remove` refuses a path outside its root, unlinks every junction with
    `os.rmdir` BEFORE git is asked to remove the tree, never passes `--force`,
    and refuses a dirty tree rather than half-removing it.

These drive the real module against a real throwaway repository. A double
would agree with whatever the code did.

MUTANT, run from a byte backup of scripts/wp_worktree.py, the file restored
from it, compared by sha256, and the mutant text grepped out afterwards:

  W1 THE NAMED MUTANT, `_CTG._unlink(link)` replaced by
  `shutil.rmtree(link)` in `remove`: test_remove_leaves_the_junction_target_
  intact and test_remove_unlinks_every_junction. On this machine's Python
  3.12 rmtree REFUSES a junction ("OSError: Cannot call rmtree on a symbolic
  link"), so W1 goes red by crashing, not by emptying anything.
  W1b the same hazard as it behaves on an older Python or under `rm -rf`,
  a recursive delete THROUGH the link (`shutil.rmtree(os.path.realpath(
  link))`): the same two cases, this time on the target being gone.
  W2 the "already exists" refusal removed from `add`: test_add_refuses_an_
  existing_path.
  W3 the outside-the-root refusal removed from `remove`: test_remove_refuses_
  a_path_outside_its_root.
  W4 the uncommitted-work refusal removed from `remove`: test_remove_refuses_
  a_dirty_worktree_and_leaves_its_junction.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "astrodeck_wp_worktree", _SCRIPTS / "wp_worktree.py")
wp = importlib.util.module_from_spec(_spec)
sys.modules["astrodeck_wp_worktree"] = wp
_spec.loader.exec_module(wp)


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, check=True)
    return out.stdout


@pytest.fixture
def main_repo(tmp_path: Path) -> Path:
    """A repository with one commit and, in its main checkout, an ignored
    `ui/node_modules` holding a file: the live dependency tree."""
    r = tmp_path / "main"
    r.mkdir()
    _git(r.parent, "init", "-q", str(r))
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    _git(r, "config", "core.autocrlf", "false")
    (r / "ui" / "src").mkdir(parents=True)
    (r / "ui" / "src" / "a.txt").write_text("from HEAD\n", encoding="utf-8")
    (r / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _git(r, "add", "--", "ui/src/a.txt", ".gitignore")
    _git(r, "commit", "-q", "-m", "one")
    (r / "ui" / "node_modules" / "pkg").mkdir(parents=True)
    (r / "ui" / "node_modules" / "pkg" / "index.js").write_text(
        "module.exports=1\n", encoding="utf-8")
    return r


def _run(*argv: str) -> int:
    return wp.main(["wp_worktree.py", *argv])


def _add(main_repo: Path, root: Path, name: str = "w15-WP-9", *extra: str) -> int:
    return _run("add", "--repo", str(main_repo), "--root", str(root),
                "--name", name, "--base", "HEAD", *extra)


def _remove(path: Path, root: Path) -> int:
    return _run("remove", "--path", str(path), "--root", str(root))


def _registered(main_repo: Path) -> list[str]:
    return [line.split()[0] for line in _git(main_repo, "worktree", "list").splitlines()]


def _kept(main_repo: Path) -> Path:
    return main_repo / "ui" / "node_modules" / "pkg" / "index.js"


def test_add_makes_a_linked_worktree_on_a_new_branch(main_repo, tmp_path, capsys):
    root = tmp_path / "scratch"
    assert _add(main_repo, root) == 0
    out = json.loads(capsys.readouterr().out)
    path = Path(out["path"])
    assert path == root / "w15-WP-9" and out["branch"] == "w15-WP-9"
    assert (path / ".git").is_file(), "not a linked worktree: .git is not a file"
    assert (path / "ui" / "src" / "a.txt").read_text(encoding="utf-8") == "from HEAD\n"
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD").strip() == "w15-WP-9"
    assert not (path / "ui" / "node_modules").exists(), "linked without --ui"


def test_add_can_name_the_branch_apart_from_the_directory(main_repo, tmp_path, capsys):
    """Branches here are `w15/WP-9` and their directories `w15-WP-9`."""
    assert _add(main_repo, tmp_path / "scratch", "w15-WP-9",
                "--branch", "w15/WP-9") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["branch"] == "w15/WP-9"
    assert _git(Path(out["path"]), "rev-parse", "--abbrev-ref", "HEAD").strip() == "w15/WP-9"


def test_add_with_ui_links_the_live_node_modules_and_stays_clean(
        main_repo, tmp_path, capsys):
    """The link must resolve, or every UI gate in the worktree fails to find
    its tools; and it must be ignored, or the worktree is born dirty and
    `remove` (which refuses a dirty tree) could never take it down."""
    assert _add(main_repo, tmp_path / "scratch", "w15-WP-9", "--ui") == 0
    path = Path(json.loads(capsys.readouterr().out)["path"])
    seen = path / "ui" / "node_modules" / "pkg" / "index.js"
    assert seen.read_text(encoding="utf-8") == "module.exports=1\n"
    assert _git(path, "status", "--porcelain").strip() == "", (
        "the linked node_modules made the worktree dirty")


def test_add_refuses_an_existing_path(main_repo, tmp_path, capsys):
    """The unique-name rule of #254's comment 3: a path that is already there
    belongs to someone, and `add` never reuses, overwrites or deletes it.

    RED under W2, the refusal removed: git is asked to add a worktree over a
    non-empty directory and fails, so the exit is 1 and the message is git's,
    not the rule's (observed, verbatim):

        AssertionError: an existing path was not refused by the rule: 1
        assert 1 == 2
    """
    root = tmp_path / "scratch"
    taken = root / "w15-WP-9"
    taken.mkdir(parents=True)
    (taken / "precious.txt").write_text("someone else's\n", encoding="utf-8")
    before = _registered(main_repo)
    code = _add(main_repo, root)
    assert code == 2, f"an existing path was not refused by the rule: {code}"
    assert "already exists" in capsys.readouterr().err
    assert (taken / "precious.txt").read_text(encoding="utf-8") == "someone else's\n"
    assert _registered(main_repo) == before
    assert _git(main_repo, "branch", "--list", "w15-WP-9").strip() == "", (
        "a refused add still created the branch")


@pytest.mark.parametrize("name", ["../escape", "a/b", "a\\b", "", ".", "..", "has space"])
def test_add_refuses_a_name_that_is_not_one_directory(main_repo, tmp_path, name):
    root = tmp_path / "scratch"
    assert _add(main_repo, root, name) == 2
    assert not (tmp_path / "escape").exists()
    assert len(_registered(main_repo)) == 1, "a refused name still made a worktree"


def test_add_refuses_ui_when_the_dependencies_are_not_there(tmp_path, capsys):
    bare = tmp_path / "bare"
    bare.mkdir()
    _git(bare.parent, "init", "-q", str(bare))
    _git(bare, "config", "user.email", "t@example.invalid")
    _git(bare, "config", "user.name", "t")
    (bare / "f.txt").write_text("x\n", encoding="utf-8")
    _git(bare, "add", "--", "f.txt")
    _git(bare, "commit", "-q", "-m", "one")
    code = _add(bare, tmp_path / "scratch", "w15-WP-9", "--ui")
    assert code == 2
    assert "node_modules" in capsys.readouterr().err
    assert not (tmp_path / "scratch" / "w15-WP-9").exists(), (
        "a refused add left a worktree behind")


def test_remove_leaves_the_junction_target_intact(main_repo, tmp_path, capsys):
    """Issue #88 and #475's shape, on the one path in this tool that can reach
    live files: after a completed removal the real `node_modules` is still
    there, the worktree directory is gone, and git no longer lists it.

    RED under W1b, a recursive delete through the link in place of the
    unlink: the target is emptied (observed, verbatim, on Windows):

        AssertionError: the dependency tree the worktree linked was deleted
        with it
        assert False
         +  where False = is_file()

    RED under W1, `shutil.rmtree(link)`: this Python refuses a junction
    before it can empty it, so the case dies on (observed, verbatim):

        OSError: Cannot call rmtree on a symbolic link
    """
    root = tmp_path / "scratch"
    assert _add(main_repo, root, "w15-WP-9", "--ui") == 0
    path = Path(json.loads(capsys.readouterr().out)["path"])
    code = _remove(path, root)
    assert _kept(main_repo).is_file(), (
        "the dependency tree the worktree linked was deleted with it")
    assert _kept(main_repo).read_text(encoding="utf-8") == "module.exports=1\n"
    assert code == 0
    assert not path.exists()
    assert str(path).replace("\\", "/") not in [p.replace("\\", "/")
                                                 for p in _registered(main_repo)]


def test_remove_unlinks_every_junction(main_repo, tmp_path, capsys):
    """Not just the one `add` made: an agent that linked something else into
    its worktree (a second dependency tree, a captured fixture directory) has
    the same hazard, so the removal finds every link in the tree.

    RED under W1b, as above, on the extra link's target (observed,
    verbatim):

        AssertionError: a junction other than node_modules was followed and
        emptied

    and under W1 with the same OSError.
    """
    root = tmp_path / "scratch"
    assert _add(main_repo, root, "w15-WP-9", "--ui") == 0
    path = Path(json.loads(capsys.readouterr().out)["path"])
    extra = tmp_path / "extra_target"
    extra.mkdir()
    (extra / "keep.txt").write_text("kept\n", encoding="utf-8")
    # Named `node_modules` so the repository's own ignore rule hides it, as
    # it hides a real one; an unignored link would make the tree dirty and
    # `remove` would refuse for that reason instead.
    link = path / "tools" / "nested" / "node_modules"
    wp._CTG._link(extra, link)
    assert _git(path, "status", "--porcelain").strip() == ""
    code = _remove(path, root)
    assert (extra / "keep.txt").exists(), (
        "a junction other than node_modules was followed and emptied")
    assert (extra / "keep.txt").read_text(encoding="utf-8") == "kept\n"
    assert _kept(main_repo).is_file()
    assert code == 0
    assert not path.exists()


def test_remove_refuses_a_path_outside_its_root(main_repo, tmp_path, capsys):
    """The one guard between this tool and removing something it did not
    create.

    RED under W3, the refusal removed (observed, verbatim):

        AssertionError: a path outside the root was removed: 0
        assert 0 == 2
    """
    root = tmp_path / "scratch"
    elsewhere = tmp_path / "elsewhere"
    assert _add(main_repo, elsewhere, "w15-WP-9", "--ui") == 0
    path = Path(json.loads(capsys.readouterr().out)["path"])
    code = _remove(path, root)
    assert code == 2, f"a path outside the root was removed: {code}"
    assert path.is_dir(), "the worktree outside the root was removed"
    assert (path / "ui" / "node_modules" / "pkg" / "index.js").is_file(), (
        "its junction was unlinked although the path was refused")
    assert "outside" in capsys.readouterr().err


def test_remove_refuses_the_root_itself_and_the_main_checkout(main_repo, tmp_path):
    root = tmp_path / "scratch"
    root.mkdir()
    assert _remove(root, root) == 2
    assert _remove(main_repo, tmp_path) == 2, "the main checkout is not removable"
    assert (main_repo / ".git").is_dir()
    assert _kept(main_repo).is_file()


def test_remove_refuses_a_dirty_worktree_and_leaves_its_junction(
        main_repo, tmp_path, capsys):
    """`--force` is never passed, so uncommitted work stops the removal; and
    it stops it BEFORE the junction is unlinked, so a refused removal does
    not leave a worktree that has quietly lost its dependencies.

    RED under W4, the refusal removed: the junction is unlinked and git then
    refuses the dirty tree itself, so the exit is 1, not the rule's 2, and
    the tree is left without its dependencies (observed, verbatim):

        AssertionError: assert 1 == 2
         +  where 1 = _remove(WindowsPath('...scratch/w15-WP-9'), ...)
    """
    root = tmp_path / "scratch"
    assert _add(main_repo, root, "w15-WP-9", "--ui") == 0
    path = Path(json.loads(capsys.readouterr().out)["path"])
    (path / "ui" / "src" / "a.txt").write_text("uncommitted work\n", encoding="utf-8")
    capsys.readouterr()
    assert _remove(path, root) == 2
    assert "ui/src/a.txt" in capsys.readouterr().err, (
        "the refusal did not name what is in the way")
    assert (path / "ui" / "src" / "a.txt").read_text(encoding="utf-8") == "uncommitted work\n"
    assert (path / "ui" / "node_modules" / "pkg" / "index.js").is_file(), (
        "a refused removal unlinked the junction anyway")


def test_remove_of_a_missing_path_is_refused(main_repo, tmp_path):
    root = tmp_path / "scratch"
    root.mkdir()
    assert _remove(root / "never-existed", root) == 2


def test_the_command_line_entry_point_end_to_end(main_repo, tmp_path):
    """`__main__`, subcommands and the JSON as a build script reads them."""
    root = tmp_path / "scratch"
    made = subprocess.run(
        [sys.executable, str(_SCRIPTS / "wp_worktree.py"), "add", "--repo",
         str(main_repo), "--root", str(root), "--name", "w15-WP-9", "--base",
         "HEAD", "--ui"], capture_output=True, text=True)
    assert made.returncode == 0, made.stderr
    out = json.loads(made.stdout)
    assert out["branch"] == "w15-WP-9" and Path(out["path"]).is_dir()
    gone = subprocess.run(
        [sys.executable, str(_SCRIPTS / "wp_worktree.py"), "remove", "--path",
         out["path"], "--root", str(root)], capture_output=True, text=True)
    assert gone.returncode == 0, gone.stderr
    assert not Path(out["path"]).exists()
    assert _kept(main_repo).is_file()
