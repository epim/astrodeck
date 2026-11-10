# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Say which CI areas a change touches, as GitHub step outputs (#1259).

    python tools/ci/changed_areas.py --base <sha> --head <sha> --event <name> --ref <ref>

prints one `<area>=true|false` line per area in tools/ci/areas.json on stdout,
which the `changes` job appends to $GITHUB_OUTPUT. The reasoning goes to stderr.

The rule is fail open: whenever the tool cannot be sure what changed, it turns
every area on. That covers a path no area owns, a change to the workflow, to
areas.json or to this file, a tag, a manual dispatch, an event it does not
know, an empty diff, a zero or missing base sha (a new branch), and a git
failure. A job that does not run because of this tool is one whose paths did
not change, never one the tool could not judge.

Standard library only: it runs before anything is installed on the runner.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

DEFAULT_AREAS = Path(__file__).resolve().with_name("areas.json")
FILTERED_EVENTS = ("pull_request", "push")


def validate_config(config: dict) -> dict:
    areas = config.get("areas")
    if not isinstance(areas, dict) or not areas:
        raise ValueError("areas.json needs a non-empty 'areas' object")
    for key in ("always_all", "inert"):
        if not isinstance(config.get(key), list):
            raise ValueError("areas.json needs a '%s' list" % key)
    for name, globs in areas.items():
        if not isinstance(globs, list) or not all(isinstance(g, str) for g in globs):
            raise ValueError("area %r must be a list of glob strings" % name)
    return config


def load_config(path: Path) -> dict:
    return validate_config(json.loads(Path(path).read_text(encoding="utf-8")))


def _regex(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def matches(path: str, pattern: str) -> bool:
    return _regex(pattern).fullmatch(path) is not None


def in_any(path: str, patterns: Iterable[str]) -> bool:
    return any(matches(path, p) for p in patterns)


def _normal(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def owners(path: str, config: dict) -> list[str]:
    return [name for name, globs in config["areas"].items() if in_any(path, globs)]


def placement(path: str, config: dict) -> str | None:
    """Where the map puts a path: always_all, inert, owned, or None (unplaced)."""
    path = _normal(path)
    if in_any(path, config["always_all"]):
        return "always_all"
    if in_any(path, config["inert"]):
        return "inert"
    if owners(path, config):
        return "owned"
    return None


def select_areas(paths: Iterable[str], event: str, config: dict, *, ref: str = "") -> list[str]:
    """The areas a change turns on, sorted; every area whenever in doubt."""
    every = sorted(config["areas"])
    paths = [_normal(p) for p in paths if p]
    if event not in FILTERED_EVENTS or ref.startswith("refs/tags/") or not paths:
        return every
    chosen: set[str] = set()
    for path in paths:
        where = placement(path, config)
        if where is None or where == "always_all":
            return every
        if where == "owned":
            chosen.update(owners(path, config))
    return sorted(chosen)


def changed_paths(base: str, head: str) -> list[str] | None:
    """Paths that differ between two commits, or None when git cannot say.

    Renames are reported as a delete plus an add, so the old path counts too.
    """
    if not base or set(base) <= {"0"}:
        return None
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", base, head],
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return [p for p in result.stdout.decode("utf-8", "replace").split("\0") if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="", help="commit to diff from (empty or zero: select every area)")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--event", required=True, help="github.event_name")
    parser.add_argument("--ref", default="", help="github.ref")
    parser.add_argument("--areas", type=Path, default=DEFAULT_AREAS)
    args = parser.parse_args(argv)

    config = load_config(args.areas)
    paths: list[str] | None = []
    if args.event in FILTERED_EVENTS and not args.ref.startswith("refs/tags/"):
        paths = changed_paths(args.base, args.head)
    chosen = select_areas(paths or [], args.event, config, ref=args.ref)

    count = "unknown" if paths is None else str(len(paths))
    print("event=%s ref=%s changed paths=%s" % (args.event, args.ref or "-", count), file=sys.stderr)
    for name in sorted(config["areas"]):
        print("%s=%s" % (name, "true" if name in chosen else "false"))
    print("areas on: %s" % (", ".join(chosen) or "none"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
