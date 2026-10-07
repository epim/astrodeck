# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Run a gate command in a copy of the tree, and record that the tree stayed quiet (#254).

Parallel agents have rewritten, mutated and deleted files in the one shared
working tree while another agent's suite ran, three times, and each time a
green or red result graded code that was not the code under review. Backlog
ruling D-01 (owner-approved 2026-09-30) closes when the isolation is
enforced, the discipline text says "in a copy of the tree", and each gate run
records that the tree was quiet. This is the wrapper that does the first and
the last:

    python scripts/gate_run.py --tree <worktree> [--cwd <subdir>] \\
        [--record <json>] [--ignore <prefix>]... [--shared-tree] \\
        -- <command...>

What it does, in order:

1. REFUSES (exit 2) when the tree is the repository's main checkout, the one
   every agent shares, unless `--shared-tree` is given, which is recorded as
   `isolated: false`. A linked worktree (scripts/wp_worktree.py makes them)
   has a `.git` FILE, so its `--git-dir` and `--git-common-dir` differ; the
   main checkout's are the same directory. It needs a git tree: a byte copy
   is not supported here, because there is nothing to compare.
2. REFUSES (exit 2) to START while a `*.mutation-state.json` or
   `*.mutation-backup` is in the tree. scripts/mutate.py leaves both beside
   its target and `.gitignore` does not hide them. A run that starts with a
   mutant in place grades the mutant from its first test, and nothing that
   watches for movement can see it, because nothing moves afterwards.
3. SNAPSHOTS the tree: HEAD, the `git status --porcelain=v1 -uall` entries,
   and a (size, mtime) stamp of every file `git ls-files -co
   --exclude-standard` lists. The porcelain alone is NOT enough. In a
   work-package worktree the owned files are already modified, so a mutant
   applied to one and restored leaves the status byte-identical. The restore
   still changes the modification time, and the stamp is what notices it.
   The porcelain is still compared, because staging a file changes the
   status and no file's size or time.
4. Runs the command with its cwd at `<tree>/<cwd>`, streaming its output.
5. SNAPSHOTS again. The tree is QUIET when HEAD is the same, the porcelain is
   the same and no path's stamp differs, appeared or vanished.
6. Writes the JSON record, if asked: schema_version, command, exit_code,
   head, head_after, started, finished, quiet, moved, porcelain_before_sha256,
   porcelain_after_sha256, isolated, files_watched. Names and hashes only,
   never contents. Write it OUTSIDE the tree: a record inside is an untracked
   file the next run's snapshot starts with.
7. Prints, as its last line, `gate run: tree QUIET (N files watched)` or
   `gate run: THIS RUN IS INVALID: the tree moved: <first 20 paths>`, and
   exits with the command's code, or 1 when the command exited 0 and the tree
   moved (the convention conftest.py's #250 plugin uses: a code that already
   says something is kept).

`--ignore PREFIX` drops paths under a prefix from both comparisons. A gate
that cries wolf gets bypassed, so a path the run legitimately writes belongs
here, but the first answer is to fix the run so it writes nothing into the
tree (the suite's own conftest guards say which tests do).

Not caught, and accepted: the stamp is size and `st_mtime_ns`. A rewrite that
keeps the size and lands inside the same filesystem timestamp tick as the
original write is invisible to it, and on Windows a tick is up to 15.6 ms.
The failure it was built for, an agent editing or mutating a file under a
suite that takes minutes, is far outside that. It also reads the tree before
and after, not during: a file changed and changed back inside the run, with
its mtime restored, is not seen.

scripts/clean_tree_gate.py is a different tool: it grades HEAD in a throwaway
worktree. This one grades the tree it is given and says whether it moved.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

SCHEMA_VERSION = 1

#: What scripts/mutate.py leaves beside its target while a mutation is live.
#: test_w15_gate_run.py pins these to the names mutate.py writes.
SENTINEL_SUFFIXES = (".mutation-state.json", ".mutation-backup")

#: How many moved paths the last line names before "and N more".
SHOWN = 20

#: A path in the listing whose file is gone (a tracked file deleted on disk)
#: stamps as None; a path that is not in the listing at all is this, so that
#: "vanished from disk" and "appeared in the listing" are different facts.
_ABSENT = object()


def _git(tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(tree), *args], capture_output=True)


def checkout_kind(tree: Path) -> str | None:
    """`"main"` for a repository's own checkout, `"linked"` for a linked
    worktree, None where `tree` is not in a git repository at all.

    `--git-dir` and `--git-common-dir` are the same directory in the main
    checkout and differ in a linked worktree. They are compared as resolved
    paths, not as printed: git prints `.git` and `.git` from the root of the
    repository but an absolute path and `../.git` from a directory below it.
    scripts/mutate.py carries the same test (`_refuse_shared_tree`); it is
    copied on its own, so it cannot import this one, and a case in
    test_w15_gate_run.py holds the two together.
    """
    try:
        out = _git(tree, "rev-parse", "--git-dir", "--git-common-dir")
    except OSError:
        return None
    lines = out.stdout.decode("utf-8", "replace").splitlines()
    if out.returncode != 0 or len(lines) != 2:
        return None
    git_dir, common_dir = (os.path.normcase(os.path.realpath(tree / line))
                           for line in lines)
    return "main" if git_dir == common_dir else "linked"


def _under(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _prefixes(raw: list[str]) -> tuple[str, ...]:
    return tuple(p.replace("\\", "/").strip("/") for p in raw if p.strip("/\\"))


class Snapshot:
    """HEAD, the status entries and the file stamp, as read at one moment.

    `porcelain` is a list of (status, path, original path or "") so that a
    path can be filtered and named; `text()` is the `git status` text form
    the record hashes, with unquoted paths.
    """

    def __init__(self, head: str, porcelain: list[tuple[str, str, str]],
                 stamp: dict[str, tuple[int, int] | None]) -> None:
        self.head = head
        self.porcelain = porcelain
        self.stamp = stamp

    def view(self, ignore: tuple[str, ...]) -> "Snapshot":
        if not ignore:
            return self
        return Snapshot(
            self.head,
            [e for e in self.porcelain if not _under(e[1], ignore)],
            {p: s for p, s in self.stamp.items() if not _under(p, ignore)})

    def text(self) -> str:
        return "".join(f"{xy} {orig + ' -> ' if orig else ''}{path}\n"
                       for xy, path, orig in self.porcelain)

    def names(self) -> set[str]:
        return set(self.stamp) | {e[1] for e in self.porcelain}


def _read_porcelain(tree: Path) -> list[tuple[str, str, str]]:
    """`git status --porcelain=v1 -z -uall`: NUL-separated, never quoted, with
    a rename or copy followed by a second field holding its source."""
    out = _git(tree, "status", "--porcelain=v1", "-z", "-uall")
    if out.returncode != 0:
        raise SystemExit(f"git status failed in {tree}: "
                         f"{out.stderr.decode('utf-8', 'replace').strip()}")
    fields = out.stdout.decode("utf-8", "replace").split("\0")
    entries: list[tuple[str, str, str]] = []
    i = 0
    while i < len(fields):
        field = fields[i]
        i += 1
        if len(field) < 4:
            continue
        xy, path = field[:2], field[3:]
        orig = ""
        if "R" in xy or "C" in xy:
            orig = fields[i] if i < len(fields) else ""
            i += 1
        entries.append((xy, path, orig))
    return entries


def _read_stamp(tree: Path) -> dict[str, tuple[int, int] | None]:
    out = _git(tree, "ls-files", "-z", "-c", "-o", "--exclude-standard")
    if out.returncode != 0:
        raise SystemExit(f"git ls-files failed in {tree}: "
                         f"{out.stderr.decode('utf-8', 'replace').strip()}")
    stamp: dict[str, tuple[int, int] | None] = {}
    for raw in out.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "replace")
        try:
            st = os.lstat(tree / rel)
            stamp[rel] = (st.st_size, st.st_mtime_ns)
        except OSError:
            stamp[rel] = None
    return stamp


def snapshot(tree: Path) -> Snapshot:
    head = _git(tree, "rev-parse", "HEAD").stdout.decode("utf-8", "replace").strip()
    return Snapshot(head, _read_porcelain(tree), _read_stamp(tree))


def stamp_moved(before: Snapshot, after: Snapshot) -> list[str]:
    """Paths whose (size, mtime) differs, or that appeared or vanished."""
    return [p for p in before.stamp.keys() | after.stamp.keys()
            if before.stamp.get(p, _ABSENT) != after.stamp.get(p, _ABSENT)]


def porcelain_moved(before: Snapshot, after: Snapshot) -> list[str]:
    """Paths of the status entries that are in one listing and not the other."""
    changed = set(before.porcelain) ^ set(after.porcelain)
    return [path for _xy, path, _orig in changed]


def leftover_mutations(snap: Snapshot) -> list[str]:
    return sorted(n for n in snap.names() if n.endswith(SENTINEL_SUFFIXES))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def _write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _verdict(quiet: bool, watched: int, head: tuple[str, str], moved: list[str]) -> str:
    if quiet:
        return f"gate run: tree QUIET ({watched} files watched)"
    parts = []
    if head[0] != head[1]:
        parts.append(f"HEAD {head[0][:8]} -> {head[1][:8]}")
    parts.extend(moved[:SHOWN])
    more = len(moved) - SHOWN
    tail = f", and {more} more" if more > 0 else ""
    return f"gate run: THIS RUN IS INVALID: the tree moved: {', '.join(parts)}{tail}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", required=True, help="the worktree the command runs against")
    ap.add_argument("--cwd", default=".", help="directory inside the tree to run in")
    ap.add_argument("--record", help="write the JSON record here (outside the tree)")
    ap.add_argument("--shared-tree", action="store_true",
                    help="run in the main checkout anyway; recorded as isolated:false")
    ap.add_argument("--ignore", action="append", default=[], metavar="PREFIX",
                    help="a path prefix, relative to the tree, that is not watched")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv[1:])
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        print("gate run: nothing to run: put the command after `--`", file=sys.stderr)
        return 2

    tree = Path(args.tree)
    kind = checkout_kind(tree)
    if kind is None:
        print(f"gate run: {tree} is not in a git tree. A gate run needs a git "
              f"worktree (scripts/wp_worktree.py makes one); a byte copy has "
              f"nothing to compare.", file=sys.stderr)
        return 2
    if kind == "main" and not args.shared_tree:
        print(f"gate run: refusing {tree}: it is the repository's main checkout, "
              f"which parallel agents share (#254). Run in a copy of the tree: "
              f"a linked worktree from scripts/wp_worktree.py. To run here "
              f"anyway, pass --shared-tree (it is recorded as isolated:false).",
              file=sys.stderr)
        return 2
    cwd = tree / args.cwd
    if not cwd.is_dir():
        print(f"gate run: --cwd {args.cwd} is not a directory in {tree}", file=sys.stderr)
        return 2

    ignore = _prefixes(args.ignore)
    raw_before = snapshot(tree)
    left = leftover_mutations(raw_before)
    if left:
        print(f"gate run: refusing to start: a mutation is in place or was left "
              f"behind: {', '.join(left)}. A run that starts over a mutant grades "
              f"the mutant from its first test. Put the file back with "
              f"`python scripts/mutate.py restore <the file named before the "
              f"suffix>`, then run again.", file=sys.stderr)
        return 2
    before = raw_before.view(ignore)

    started = _now()
    print(f"gate run: {' '.join(command)} in {cwd}", flush=True)
    try:
        rc = subprocess.run(command, cwd=str(cwd)).returncode
    except OSError as e:
        print(f"gate run: cannot start {command[0]!r}: {e}", file=sys.stderr, flush=True)
        rc = 127
    except KeyboardInterrupt:
        rc = 130
    finished = _now()

    after = snapshot(tree).view(ignore)
    moved = sorted(set(stamp_moved(before, after)) | set(porcelain_moved(before, after)))
    quiet = (before.head == after.head
             and before.porcelain == after.porcelain
             and not moved)
    if args.record:
        _write_record(Path(args.record), {
            "schema_version": SCHEMA_VERSION,
            "command": command,
            "tree": str(tree.resolve()),
            "cwd": args.cwd,
            "exit_code": rc,
            "head": before.head,
            "head_after": after.head,
            "started": started,
            "finished": finished,
            "quiet": quiet,
            "moved": moved,
            "porcelain_before_sha256": _sha256(before.text()),
            "porcelain_after_sha256": _sha256(after.text()),
            "isolated": kind == "linked",
            "ignore": list(ignore),
            "files_watched": len(before.stamp),
        })
    print(_verdict(quiet, len(before.stamp), (before.head, after.head), moved), flush=True)
    return rc if rc != 0 else (0 if quiet else 1)


if __name__ == "__main__":        # pragma: no cover - CLI
    sys.exit(main(sys.argv))
