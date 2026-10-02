# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Run a gate command against HEAD's content, not the working tree (#78).

`feat/photosphere-production` reported a clean `tsc -b` for a whole pass of
work while two committed files did not compile on their own. The tree also
held another session's uncommitted `horizon.tsx`, which supplied the shape and
the prop the committed files needed, so every type-check of the pass graded a
tree that was not the branch. The class is "a green check on the wrong tree",
and the same shape produced the `-dirty` app_commit guard in the simulator
baseline.

    python scripts/clean_tree_gate.py -- node node_modules/typescript/bin/tsc -b
    python scripts/clean_tree_gate.py --cwd ui -- npm test

What it does: adds a detached worktree of HEAD under the system temporary
directory, LINKS the dependency directories into it rather than copying them
(a `node_modules` copy is minutes and gigabytes), runs the command there, and
takes the worktree down again whatever the command did.

Two things it refuses, both because the alternative has already cost this
repository real data (#88):

  * it never removes a path that is not the worktree it created under the
    system temporary directory;
  * it unlinks each link with `os.rmdir`, which removes the junction or
    symlink and never descends into the target. `shutil.rmtree` and
    `rm -rf` over a Windows junction are not reliably the same operation,
    and the target here is the live `node_modules`.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

#: Directories that must exist in the worktree for a gate to run, and that are
#: linked from the real tree instead of installed. Relative to the repo root.
LINKED = ("ui/node_modules",)


def repo_root(start: Path) -> Path:
    out = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, check=True)
    return Path(out.stdout.strip())


def dirty_under(status: str, prefixes: tuple[str, ...]) -> list[str]:
    """Paths in `git status --porcelain` output that sit under a prefix.

    Parses the rename form (`R  old -> new`), which names two paths, and
    reports the destination. Quoted paths (a non-ASCII name) keep their
    quotes: this decides whether to isolate, and a name it cannot unquote is
    still a name it must not ignore.
    """
    found: set[str] = set()
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        posix = path.strip('"').replace("\\", "/")
        if any(posix == p or posix.startswith(p.rstrip("/") + "/") for p in prefixes):
            found.add(posix)
    return sorted(found)


def _link(target: Path, link: Path) -> None:
    """A junction on Windows, a symlink elsewhere. A junction is chosen over a
    symlink on Windows because `os.symlink` to a directory needs developer
    mode or elevation there, and `mklink /J` needs neither."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True, text=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def _unlink(link: Path) -> None:
    """Remove the link itself. `os.rmdir` on a junction or a directory symlink
    removes the entry and leaves the target alone; a recursive delete over the
    same path is not guaranteed to."""
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        os.rmdir(link)


def _require_temp(path: Path) -> Path:
    root = Path(tempfile.gettempdir()).resolve()
    resolved = path.resolve()
    if resolved == root or root not in resolved.parents:
        raise SystemExit(f"refusing to manage {resolved}: it is not under {root}")
    return resolved


def run(command: list[str], cwd: str, root: Path) -> int:
    parent = Path(tempfile.mkdtemp(prefix="astrodeck-clean-gate-"))
    tree = _require_temp(parent) / "tree"
    linked: list[Path] = []
    try:
        subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach",
                        str(tree), "HEAD"], check=True, capture_output=True, text=True)
        for rel in LINKED:
            target = root / rel
            if not target.is_dir():
                raise SystemExit(f"{target} is not there; the gate cannot run without it")
            link = tree / rel
            _link(target, link)
            linked.append(link)
        print(f"clean-tree gate: {' '.join(command)} in {tree / cwd}", flush=True)
        return subprocess.run(command, cwd=str(tree / cwd)).returncode
    finally:
        for link in reversed(linked):
            _unlink(link)
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(tree)],
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", str(root), "worktree", "prune"],
                       capture_output=True, text=True)
        # Only the directory this function made, and only if the worktree
        # removal left it empty. A non-empty one is reported, never swept:
        # something is in it that this function did not put there.
        try:
            parent.rmdir()
        except OSError:
            print(f"clean-tree gate: left {parent} in place; it is not empty", file=sys.stderr)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cwd", default=".", help="directory inside the worktree to run in")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv[1:])
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("nothing to run: put the command after `--`")
    root = repo_root(Path.cwd())
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                            capture_output=True, text=True, check=True).stdout
    dirty = dirty_under(status, ("ui/src", "server/astrodeck", "tools"))
    if dirty:
        print(f"clean-tree gate: {len(dirty)} source path(s) differ from HEAD "
              f"(first: {dirty[0]}); HEAD is what runs", flush=True)
    return run(command, args.cwd, root)


if __name__ == "__main__":        # pragma: no cover - CLI
    sys.exit(main(sys.argv))
