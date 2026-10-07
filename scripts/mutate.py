# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A mutation harness that cannot leave a mutant in the tree (#96).

Every "show the test red under a named mutation" step patches a source file,
runs a suite, and puts the file back. Hand-rolled, that step damaged the
working tree twice in ten minutes on this machine:

    OSError: [Errno 22] Invalid argument: '.../photosphere.ts'

raised by the restore copy, at the moment the file was reopened for writing
right after a `node --import tsx` run had read it -- a watcher, an indexer or
the antivirus still holding the handle. The first time there was no
`try/finally`, so the mutant sat in the tree and the NEXT mutation run copied
it as its "clean" backup and reported a clean checksum for a mutated file: the
guard was satisfied by the corruption. The second time the failure was inside
the `finally` itself.

The mutant that survived was "delete the azimuth anchor test", which passes
`tsc` and fails only four of the suite's cases. A run that happened not to
include those would have reported green on a file that was not the file under
review -- a verification step grading something other than what ships, which
is the same class as the model-downgrade re-review.

Four rules, and the second is the one the incident turned on:

1. Restore in a `finally`, and RETRY it. Twenty attempts at 0.5 s cleared it
   both times.
2. Check the digest on the way IN as well as on the way out. A failed restore
   must stop the next mutation rather than become its baseline.
3. Take the backup once, from a file whose digest matches the one recorded
   when the task started.
4. Never snapshot in the repository's main checkout (#254). Parallel agents
   share that one working tree, and a mutant in it is graded by, and
   corrupts, every other agent's run. Work in a copy of the tree: a linked
   worktree from scripts/wp_worktree.py, or a byte copy outside any
   repository. `snapshot` refuses the main checkout, which is where every
   mutation starts, and `restore` never refuses, so a mutant that is already
   there can always be put back. ASTRODECK_MUTATE_SHARED_TREE=1 overrides it
   for an operator who means it.

Usage, from the repository root:

    python scripts/mutate.py snapshot <file>
    python scripts/mutate.py apply    <file> <old-text> <new-text>
    python scripts/mutate.py restore  <file>

`apply` refuses if the file no longer matches its snapshot, if the old text is
absent, or if it appears more than once -- an ambiguous anchor is how a
mutation lands somewhere other than where it was aimed.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

#: Attempts and spacing for the restore copy. Both incidents cleared well
#: inside this; it is deliberately generous because the cost of one more
#: second is nothing against the cost of a mutant reaching a commit.
RESTORE_ATTEMPTS = 20
RESTORE_WAIT_S = 0.5


def _state_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".mutation-state.json")


def _backup_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".mutation-backup")


def digest(target: Path) -> str:
    return hashlib.md5(target.read_bytes()).hexdigest()


def _refuse_shared_tree(target: Path) -> None:
    """Rule 4 (#254): stop a mutation that would start in the main checkout.

    In a linked worktree `git rev-parse --git-dir` names the worktree's own
    directory under `.git/worktrees/` while `--git-common-dir` names the main
    repository's, so the two differ; in the main checkout they are the same
    directory. The two are compared as resolved paths, not as printed: git
    prints `.git` and `.git` from the repository root but an absolute path
    and `../.git` from a directory below it.

    A file outside any repository (a byte copy) is allowed: git failing is
    the answer "there is no shared tree here". gate_run.py carries the same
    test; this script is copied on its own, so it does not import it.
    """
    if os.environ.get("ASTRODECK_MUTATE_SHARED_TREE") == "1":
        return
    here = target.resolve().parent
    try:
        out = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "--git-dir", "--git-common-dir"],
            capture_output=True, text=True)
    except OSError:                                # no git on this machine
        return
    lines = out.stdout.splitlines()
    if out.returncode != 0 or len(lines) != 2:
        return
    git_dir, common_dir = (os.path.normcase(os.path.realpath(here / line))
                           for line in lines)
    if git_dir != common_dir:
        return
    raise SystemExit(
        f"refusing to mutate {target.name}: it is in the repository's main "
        f"checkout, which parallel agents share (#254). A mutant here is "
        f"graded by every other run in the tree.\n"
        f"Work in a copy of the tree instead: a linked worktree "
        f"(`python scripts/wp_worktree.py add ...`) or a byte copy of the "
        f"directory outside any repository. To override on purpose, set "
        f"ASTRODECK_MUTATE_SHARED_TREE=1.")


def snapshot(target: Path) -> str:
    """Record the clean digest and take the one backup. Rules 3 and 4."""
    _refuse_shared_tree(target)
    state, backup = _state_path(target), _backup_path(target)
    if state.exists():
        raise SystemExit(
            f"{target.name} is already under a mutation snapshot. Restore it "
            f"first: a second snapshot would record whatever is in the tree "
            f"now as clean, which is exactly how a mutant became a baseline.")
    d = digest(target)
    shutil.copyfile(target, backup)
    state.write_text(json.dumps({"digest": d, "path": str(target)}), encoding="utf-8")
    return d


def _require_clean(target: Path) -> dict:
    state = _state_path(target)
    if not state.exists():
        raise SystemExit(f"no mutation snapshot for {target.name}; run `snapshot` first")
    rec = json.loads(state.read_text(encoding="utf-8"))
    now = digest(target)
    if now != rec["digest"]:
        raise SystemExit(
            f"{target.name} does not match its snapshot ({now} != {rec['digest']}).\n"
            f"A previous restore did not take. Refusing to mutate, because the "
            f"next mutant would be measured against a file that is already wrong.")
    return rec


def apply(target: Path, old: str, new: str) -> None:
    """Mutate, having first proved the file is the one we snapshotted. Rule 2."""
    _require_clean(target)
    text = io.open(target, encoding="utf-8", newline="").read()
    hits = text.count(old)
    if hits == 0:
        raise SystemExit(f"anchor not found in {target.name}: {old!r}")
    if hits > 1:
        raise SystemExit(
            f"anchor appears {hits} times in {target.name}; an ambiguous anchor "
            f"mutates somewhere other than where it was aimed: {old!r}")
    io.open(target, "w", encoding="utf-8", newline="").write(text.replace(old, new, 1))


def restore(target: Path) -> int:
    """Put the file back, retrying the copy. Rule 1. Returns the attempts used."""
    rec = _require_snapshot(target)
    backup = _backup_path(target)
    last: Exception | None = None
    for attempt in range(1, RESTORE_ATTEMPTS + 1):
        try:
            shutil.copyfile(backup, target)
        except OSError as e:                       # the Errno 22 this exists for
            last = e
            time.sleep(RESTORE_WAIT_S)
            continue
        now = digest(target)
        if now != rec["digest"]:
            last = RuntimeError(f"restored bytes do not match: {now} != {rec['digest']}")
            time.sleep(RESTORE_WAIT_S)
            continue
        _state_path(target).unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
        return attempt
    raise SystemExit(
        f"COULD NOT RESTORE {target}. after {RESTORE_ATTEMPTS} attempts: {last}\n"
        f"The mutated file is STILL IN THE TREE. Its clean copy is at {backup} "
        f"and its expected digest is {rec['digest']}. Do not commit until "
        f"`python scripts/mutate.py restore {target}` succeeds.")


def _require_snapshot(target: Path) -> dict:
    state = _state_path(target)
    if not state.exists():
        raise SystemExit(f"no mutation snapshot for {target.name}")
    return json.loads(state.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        raise SystemExit(__doc__.strip().splitlines()[-6])
    cmd, target = argv[1], Path(argv[2])
    if cmd == "snapshot":
        print(f"snapshot {target.name}: {snapshot(target)}")
    elif cmd == "apply":
        apply(target, argv[3], argv[4])
        print(f"MUTATION applied to {target.name}")
    elif cmd == "restore":
        print(f"restored {target.name} in {restore(target)} attempt(s)")
    else:
        raise SystemExit(f"unknown command {cmd!r}")
    return 0


if __name__ == "__main__":        # pragma: no cover - CLI
    sys.exit(main(sys.argv))
