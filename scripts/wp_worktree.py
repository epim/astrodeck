# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Make and remove work-package worktrees: the copy of the tree a gate runs in (#254).

Backlog ruling D-01 (owner-approved 2026-09-30): every mutation and every
full-suite run happens in a copy of the tree, never the shared checkout. A
work package's copy is a linked worktree, and this is the one implementation
of making and removing it, so a wave's build script stops carrying its own
inline `git worktree add` and `mklink`:

    python scripts/wp_worktree.py add    --root <dir> --name <wave-WP> \\
        --base <ref> [--branch <name>] [--ui] [--repo <dir>]
    python scripts/wp_worktree.py remove --path <worktree> --root <dir>

`add` runs `git worktree add -b <branch> <root>/<name> <base>`, with the
branch defaulting to the name, and with `--ui` links `ui/node_modules` from
the MAIN checkout into the new tree (a junction on Windows, a symlink
elsewhere, by `clean_tree_gate._link`: a copy is minutes and gigabytes). It
prints `{"path": ..., "branch": ...}`. It refuses a path that already
exists. That is the unique-name rule from #254's comment 3: two agents that
picked the same generic scratch path collided, and one's `rm -rf` deleted the
other's copy of `server/`. So a name is one directory, chosen per task (the
task id, plus a random suffix where a rerun is possible), and `add` never
reuses, overwrites or deletes what it did not create.

`remove` refuses a path outside `--root`, refuses anything that is not a
linked worktree (so never the main checkout), and refuses one with
uncommitted or untracked work: it never passes `--force`. It unlinks every
junction or symlink in the tree with `clean_tree_gate._unlink` (`os.rmdir`,
which removes the link and never descends into its target) BEFORE git is
asked to remove the tree, because a recursive delete that follows a Windows
junction empties the live dependency tree it points at (#88, #475). The
dirty check comes first, so a refused removal does not leave a worktree that
has quietly lost its `node_modules`.

Exit codes: 0 done, 2 refused (nothing was changed), 1 git failed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "astrodeck_wp_worktree_ctg", Path(__file__).with_name("clean_tree_gate.py"))
_CTG = importlib.util.module_from_spec(_spec)
sys.modules["astrodeck_wp_worktree_ctg"] = _CTG
_spec.loader.exec_module(_CTG)

#: One directory name: no separators, no leading dash or dot, nothing a shell
#: or `git worktree add` would read as something else.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: Windows' FILE_ATTRIBUTE_REPARSE_POINT, which `stat` only defines there.
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class Refused(Exception):
    """The request would touch something it must not; nothing was changed."""


class GitFailed(Exception):
    """Git itself failed."""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def _git_dirs(tree: Path) -> tuple[Path, Path] | None:
    """(git dir, common git dir) of `tree`, resolved, or None if it is not in
    a repository. Equal in a main checkout, different in a linked worktree."""
    out = _git(tree, "rev-parse", "--git-dir", "--git-common-dir")
    lines = out.stdout.splitlines()
    if out.returncode != 0 or len(lines) != 2:
        return None
    return tuple(Path(os.path.realpath(tree / line)) for line in lines)  # type: ignore[return-value]


def _main_checkout(repo: Path) -> Path:
    """The main checkout of the repository `repo` belongs to: where the live
    `ui/node_modules` is, and where git is asked to remove a worktree from
    (not from inside the tree being removed)."""
    dirs = _git_dirs(repo)
    if dirs is None:
        raise Refused(f"{repo} is not in a git repository")
    common = dirs[1]
    return common.parent if common.name == ".git" else repo


def _is_link(path: Path) -> bool:
    """A symlink, or a Windows junction (which `Path.is_symlink` does not
    report): anything `lstat` marks as a reparse point."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


def _find_links(tree: Path) -> list[Path]:
    """Every link below `tree`, found WITHOUT descending into one: `os.walk`
    reports a junction as an ordinary directory, so the walk prunes each link
    out of `dirnames` the moment it sees it."""
    found: list[Path] = []
    for here, dirnames, _files in os.walk(tree):
        keep = []
        for name in dirnames:
            child = Path(here) / name
            if _is_link(child):
                found.append(child)
            else:
                keep.append(name)
        dirnames[:] = keep
    return found


def add(repo: Path, root: Path, name: str, base: str, *, ui: bool = False,
        branch: str | None = None) -> dict:
    if not _NAME.fullmatch(name) or name.endswith("."):
        raise Refused(f"--name {name!r} must be one directory name: letters, "
                      f"digits, dot, dash and underscore, starting with a "
                      f"letter or digit")
    dest = root / name
    if os.path.lexists(dest):
        raise Refused(f"{dest} already exists. A worktree path is made once per "
                      f"task and never reused (#254): choose another name; do "
                      f"not delete a path you did not create.")
    main = _main_checkout(repo)
    source = main / "ui" / "node_modules"
    if ui and not source.is_dir():
        raise Refused(f"--ui needs {source}, and it is not there")
    root.mkdir(parents=True, exist_ok=True)
    made = _git(main, "worktree", "add", "-b", branch or name, str(dest), base)
    if made.returncode != 0:
        raise GitFailed(f"git worktree add failed: {made.stderr.strip()}")
    if ui:
        try:
            _CTG._link(source, dest / "ui" / "node_modules")
        except (OSError, subprocess.CalledProcessError) as e:
            raise GitFailed(f"{dest} was made but linking {source} failed: {e}. "
                            f"Remove it with `wp_worktree.py remove`.") from e
    return {"path": str(dest), "branch": branch or name}


def remove(path: Path, root: Path) -> dict:
    resolved, base = path.resolve(), root.resolve()
    if base not in resolved.parents:
        raise Refused(f"{resolved} is outside {base}: refusing to remove a path "
                      f"this root did not make")
    if not resolved.is_dir():
        raise Refused(f"{resolved} is not a directory")
    dirs = _git_dirs(resolved)
    if dirs is None or dirs[0] == dirs[1]:
        raise Refused(f"{resolved} is not a linked worktree (it is "
                      f"{'a main checkout' if dirs else 'not in a repository'}): "
                      f"refusing")
    main = _main_checkout(resolved)
    dirty = [line for line in _git(resolved, "status", "--porcelain=v1", "-uall"
                                   ).stdout.splitlines() if line.strip()]
    if dirty:
        raise Refused(
            f"{resolved} has {len(dirty)} uncommitted or untracked path(s) "
            f"(first: {dirty[0][3:]}); this never passes --force. Commit or "
            f"copy the work out, then remove it.")
    unlinked = []
    for link in _find_links(resolved):
        _CTG._unlink(link)
        unlinked.append(str(link))
    gone = _git(main, "worktree", "remove", str(resolved))
    if gone.returncode != 0:
        raise GitFailed(f"git worktree remove failed: {gone.stderr.strip()}")
    _git(main, "worktree", "prune")
    return {"removed": str(resolved), "links_unlinked": unlinked}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="make a worktree for one work package")
    a.add_argument("--root", required=True, help="directory the worktree is made under")
    a.add_argument("--name", required=True, help="the directory name: wave and work package")
    a.add_argument("--base", required=True, help="the commit or ref the worktree is cut from")
    a.add_argument("--branch", help="the branch to create (default: the name)")
    a.add_argument("--ui", action="store_true", help="link ui/node_modules from the main checkout")
    a.add_argument("--repo", default=".", help="a directory in the repository (default: here)")
    r = sub.add_parser("remove", help="remove a worktree this root made")
    r.add_argument("--path", required=True)
    r.add_argument("--root", required=True)
    args = ap.parse_args(argv[1:])
    try:
        if args.cmd == "add":
            out = add(Path(args.repo), Path(args.root), args.name, args.base,
                      ui=args.ui, branch=args.branch)
        else:
            out = remove(Path(args.path), Path(args.root))
    except Refused as e:
        print(f"wp_worktree: refused: {e}", file=sys.stderr)
        return 2
    except GitFailed as e:
        print(f"wp_worktree: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":        # pragma: no cover - CLI
    sys.exit(main(sys.argv))
