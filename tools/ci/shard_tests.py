# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Say which server test files one shard of the CI `server` job runs (WP 8.11).

    python tools/ci/shard_tests.py --shard K --of N

prints the test files of shard K of N, one per line, relative to server/ (the
job's working directory), so the job can hand them to pytest as arguments.

    python tools/ci/shard_tests.py --record <pytest-output.txt>... --label <text>

rewrites tools/ci/shard-weights.json from the `--durations` lists the job keeps
on main as the `server-durations-<shard>` artifacts (all the shards of one run
together). Do it when the shards have drifted apart: the three jobs' wall times
in a run show it, and the weights are the only thing that moves.

The scheme. A file is never split across shards (module-scoped fixtures), and
the shards are balanced by measured time:

  - A file in the weight table is placed by longest-first greedy packing over the
    TABLE's files: heaviest first, each onto the lightest shard so far, ties
    broken by the file name. The packing reads the table and N, never the tree,
    so adding, renaming or deleting a test file moves no recorded file.
  - A file the table does not know is placed by a hash of its path, which reads
    nothing but the path. A new file therefore never reshuffles anything and never
    fails the job; it is only balanced by luck until the table is re-recorded,
    and the tool says on stderr how many files that applies to.

The partition holds with any table, including none: every file is placed exactly
once, for any N. Standard library only: it runs before anything is installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path

DEFAULT_TESTS_DIR = Path(__file__).resolve().parents[2] / "server" / "tests"
DEFAULT_WEIGHTS = Path(__file__).resolve().with_name("shard-weights.json")

# Seconds given to a file whose tests are all under pytest's 0.005 s duration
# cut-off: collecting and importing it is not free.
FLOOR_S = 0.1

PATTERNS = ("test_*.py", "*_test.py")
DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)s (?:call|setup|teardown)\s+(tests/\S+?\.py)(?:::|\s|$)")


def test_files(tests_dir: Path) -> list[str]:
    """The files pytest collects under `tests_dir`, as `tests/...` paths, sorted."""
    found = {p for pattern in PATTERNS for p in tests_dir.rglob(pattern)
             if "__pycache__" not in p.parts}
    return sorted("tests/" + p.relative_to(tests_dir).as_posix() for p in found)


def load_weights(path: Path) -> dict[str, float]:
    """The recorded seconds per test file; none when the table is missing."""
    if not path.is_file():
        return {}
    return {f: float(w) for f, w in json.loads(path.read_text(encoding="utf-8"))["weights"].items()}


def assignment(weights: dict[str, float], n: int) -> dict[str, int]:
    """Shard (1..n) of every file in the table: heaviest first onto the lightest shard.

    Weights are packed as whole tenths of a second so the sums are exact.
    """
    totals = [0] * n
    placed = {}
    for tenths, name in sorted((-round(w * 10), f) for f, w in weights.items()):
        lightest = min(range(n), key=lambda i: (totals[i], i))
        totals[lightest] -= tenths
        placed[name] = lightest + 1
    return placed


def fallback_shard(path: str, n: int) -> int:
    """Shard (1..n) of a file the table does not know: a hash of the path alone."""
    digest = hashlib.sha1(path.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % n + 1


def shard_files(files: Iterable[str], shard: int, n: int, weights: dict[str, float]) -> list[str]:
    """The files of shard `shard` of `n`, sorted."""
    placed = assignment(weights, n)
    return sorted(f for f in files
                  if (placed[f] if f in placed else fallback_shard(f, n)) == shard)


def parse_durations(text: str) -> dict[str, float]:
    """Seconds per test file from a pytest `--durations` list (call, setup and teardown added)."""
    totals: dict[str, float] = {}
    for line in text.splitlines():
        m = DURATION.match(line)
        if m:
            totals[m.group(2)] = totals.get(m.group(2), 0.0) + float(m.group(1))
    return {f: round(s, 2) for f, s in totals.items()}


def merge_durations(parts: Iterable[dict[str, float]]) -> dict[str, float]:
    """The shards of one run ran disjoint files, so merging is adding."""
    merged: dict[str, float] = {}
    for part in parts:
        for f, s in part.items():
            merged[f] = merged.get(f, 0.0) + s
    return merged


def weights_from(files: Iterable[str], durations: dict[str, float]) -> dict[str, float]:
    """A weight for every file in the tree, to a tenth of a second, at least FLOOR_S."""
    return {f: max(FLOOR_S, round(durations.get(f, 0.0), 1)) for f in files}


def write_weights(path: Path, label: str, weights: dict[str, float]) -> None:
    body = {"recorded_from": label, "weights": dict(sorted(weights.items()))}
    text = json.dumps(body, indent=0, ensure_ascii=True) + "\n"
    path.write_bytes(text.encode("ascii"))


def record(logs: list[Path], label: str, tests_dir: Path, weights_path: Path) -> None:
    durations = merge_durations(parse_durations(p.read_text(encoding="utf-8", errors="replace"))
                                for p in logs)
    write_weights(weights_path, label, weights_from(test_files(tests_dir), durations))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--shard", type=int, help="the shard to print, 1..N")
    parser.add_argument("--of", type=int, dest="total", help="N, the number of shards")
    parser.add_argument("--record", type=Path, nargs="+", metavar="PYTEST_OUTPUT",
                        help="rewrite the weight table from these --durations lists")
    parser.add_argument("--label", help="what --record was taken from (a CI run), kept in the table")
    parser.add_argument("--tests-dir", type=Path, default=DEFAULT_TESTS_DIR)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    args = parser.parse_args(argv)

    if args.record:
        if not args.label:
            parser.error("--record needs --label")
        record(args.record, args.label, args.tests_dir, args.weights)
        return 0
    if args.shard is None or args.total is None:
        parser.error("give --shard K --of N, or --record")
    if args.total < 1 or not 1 <= args.shard <= args.total:
        parser.error("--shard must be between 1 and --of, and --of at least 1")

    files = test_files(args.tests_dir)
    weights = load_weights(args.weights)
    mine = shard_files(files, args.shard, args.total, weights)
    if not mine:
        print("shard %d of %d has no test files" % (args.shard, args.total), file=sys.stderr)
        return 1
    unrecorded = sum(1 for f in files if f not in weights)
    if unrecorded:
        print("%d of %d test files have no recorded weight and are placed by hash; "
              "re-record with --record" % (unrecorded, len(files)), file=sys.stderr)
    sys.stdout.write("".join(f + "\n" for f in mine))
    return 0


if __name__ == "__main__":
    sys.exit(main())
