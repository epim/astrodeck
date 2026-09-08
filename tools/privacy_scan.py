#!/usr/bin/env python
"""Refuse to let the real observing site into the repository.

The standing rule: the developer's observing-site latitude, longitude and site
label must NEVER appear in code, tests, docs or fixtures. The site default in
any fixture or example is ``"My Observatory"`` at ``0.0 / 0.0``; invented
coordinates are fine and encouraged.

WHY THE VALUES ARE NOT IN THIS FILE
-----------------------------------
The previous version of this scanner carried them, assembled from string parts
at import time so that grepping the source would not print them. That is
obfuscation, not secrecy -- anything a scanner can compare against, a reader
can print, and reconstructing the values from a public clone took one line of
Python. The file that existed to keep the site out of the repository was
therefore the single most reliable place to recover it, which is why the
history rewrite of 2026-09-08 purged this path from every revision alongside
the values it was hiding.

So the needles now live OUTSIDE the tree, and this file holds none of them:

    ASTRODECK_PRIVACY_NEEDLES       the values themselves, separated by
                                    newlines or commas
    ASTRODECK_PRIVACY_NEEDLES_FILE  a file holding them, one per line
                                    (default: ~/.astrodeck/privacy-needles.txt)

The environment variable wins when it is set and non-empty; otherwise the file
is read. CI supplies the variable from a repository secret. A fork has no
secret and a fresh clone has no file, so BOTH must be a clean no-op -- see
"when nothing is configured" below.

WHAT COUNTS AS A HIT
--------------------
For every needle, the literal string. For a NUMERIC needle, also its
truncation to three decimals: a three-decimal prefix still places the rig to
about 100 m, so publishing it is publishing the site. Two decimals are NOT
forbidden -- roughly 1 km, and that shape occurs legitimately all over the tree
in catalog coordinates and flip-geometry fixtures, so forbidding it would make
the scanner cry wolf until somebody turned it off. A TEXT needle (the site
label) matches case-insensitively.

A hit prints ``path:line: contains a forbidden site value`` and nothing else.
The scanner never echoes the value it matched: its output goes to CI logs,
which are as public as the repository.

WHEN NOTHING IS CONFIGURED
--------------------------
Exit 0 with a single notice line. The scan cannot run, and saying so is more
honest than a silent pass -- but it must not fail a fork's build or a fresh
clone's first commit. Pass ``--require`` where the needles are supposed to be
present (CI with the secret set, a pre-push check) and an unconfigured scanner
exits 2 instead, so "the gate quietly stopped grading" cannot happen twice.

USAGE
-----
    python tools/privacy_scan.py              # every tracked file
    python tools/privacy_scan.py --staged     # the index, for the commit hook
    python tools/privacy_scan.py --require    # unconfigured is an error

Exit codes: 0 clean (or unconfigured without ``--require``), 1 a hit was
found, 2 misconfiguration.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

#: Suffixes worth reading. Carried over unchanged from the pre-rewrite
#: scanner: a value can only be pasted into something a human types.
TEXT_SUFFIXES = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".json", ".md", ".txt",
    ".yml", ".yaml", ".toml", ".cfg", ".ini", ".sh", ".ps1", ".html",
    ".css", ".rs",
})

#: Paths the scan skips. Only this file -- and only as belt and braces, since
#: it no longer holds any value to skip past: it names the MECHANISM, never
#: the values. The exemption survives so that a label needle which happens to
#: be an ordinary English word cannot be tripped by this file's own prose
#: about the rule. Nothing else may be added here; an exemption is how a guard
#: stops guarding.
EXEMPT = frozenset({"tools/privacy_scan.py"})

DEFAULT_NEEDLE_FILE = Path("~/.astrodeck/privacy-needles.txt")

#: A needle is numeric if it is a plain signed decimal. Only those get the
#: three-decimal truncation; a label has no decimals to truncate.
_NUMERIC = re.compile(r"^[+-]?\d+\.\d+$")

_HOW_TO_CONFIGURE = (
    "privacy_scan: no needles configured. Set ASTRODECK_PRIVACY_NEEDLES to the "
    "values (newline- or comma-separated), or put them one per line in the file "
    "named by ASTRODECK_PRIVACY_NEEDLES_FILE (default "
    "~/.astrodeck/privacy-needles.txt). They are deliberately not stored in the "
    "repository."
)


def load_needles(env=None) -> list[str]:
    """The forbidden values, from the environment or the out-of-tree file.

    Returns ``[]`` when neither source yields anything, which is a legitimate
    state (a fork, a fresh clone) and not an error here -- the caller decides
    whether it is one.
    """
    env = os.environ if env is None else env

    inline = (env.get("ASTRODECK_PRIVACY_NEEDLES") or "").strip()
    if inline:
        return _split_needles(inline)

    named = (env.get("ASTRODECK_PRIVACY_NEEDLES_FILE") or "").strip()
    path = Path(named).expanduser() if named else DEFAULT_NEEDLE_FILE.expanduser()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return _split_needles(raw)


def _split_needles(raw: str) -> list[str]:
    """Newlines OR commas, blank entries dropped, order preserved."""
    out: list[str] = []
    for chunk in raw.replace(",", "\n").splitlines():
        value = chunk.strip()
        if value and value not in out:
            out.append(value)
    return out


def patterns_for(needles: list[str]) -> list[tuple[str, bool]]:
    """Expand needles into ``(pattern, fold_case)`` pairs to search for.

    A numeric needle contributes itself and its three-decimal truncation; the
    truncation is a PREFIX, never a rounding, because a rounded last digit
    would invent a string the file does not contain.
    """
    out: list[tuple[str, bool]] = []
    for needle in needles:
        if _NUMERIC.match(needle):
            _add(out, needle, False)
            whole, _, frac = needle.partition(".")
            if len(frac) > 3:
                _add(out, whole + "." + frac[:3], False)
        else:
            _add(out, needle.casefold(), True)
    return out


def _add(out: list[tuple[str, bool]], pattern: str, fold: bool) -> None:
    if pattern and (pattern, fold) not in out:
        out.append((pattern, fold))


def _deescaped(line: str) -> str:
    """The line with backslashes removed, so an ESCAPED value still matches.

    THIS IS NOT A REFINEMENT, IT IS THE CLASS THE FIRST SWEEP MISSED. On
    2026-09-08 the history rewrite replaced every literal occurrence of the
    site values and left two behind, in
    ``docs/superpowers/plans/2026-07-22-fits-header-completeness.md``: the
    plan's own "grep the diff before you commit" checklist, which wrote the
    coordinates as a regex with the dots escaped. A substring search for
    ``37.123456`` does not match ``37\\.123456``, so neither the rewrite's
    literal replacement nor this scanner's first version could see them --
    and the file that leaked was, once again, the one telling everybody not
    to leak.

    Removing backslashes before searching costs nothing (a needle never
    contains one) and catches every escaping of them: regex, shell, JSON.
    Searched IN ADDITION to the raw line, never instead of it.
    """
    return line.replace("\\", "") if "\\" in line else line


def scan_text(text: str, patterns: list[tuple[str, bool]]) -> list[int]:
    """1-based numbers of the lines carrying at least one pattern.

    Each line is searched twice: as written, and with its backslashes removed
    (see ``_deescaped`` for the leak that bought that second look).
    """
    hits: list[int] = []
    for number, line in enumerate(text.splitlines(), 1):
        bare = _deescaped(line)
        folded = line.casefold()
        folded_bare = bare.casefold() if bare is not line else folded
        for pattern, fold in patterns:
            haystacks = (folded, folded_bare) if fold else (line, bare)
            if any(pattern in h for h in haystacks):
                hits.append(number)
                break
    return hits


def repo_root() -> Path:
    done = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, errors="replace",
    )
    if done.returncode != 0:
        raise SystemExit("privacy_scan: not inside a git work tree")
    return Path(done.stdout.strip())


def _git_paths(root: Path, args: list[str]) -> list[str]:
    done = subprocess.run(
        ["git", "-C", str(root)] + args,
        capture_output=True, text=True, errors="replace",
    )
    if done.returncode != 0:
        raise SystemExit("privacy_scan: " + done.stderr.strip())
    return [p for p in done.stdout.split("\0") if p]


def tracked_files(root: Path) -> list[str]:
    return _git_paths(root, ["ls-files", "-z"])


def staged_files(root: Path) -> list[str]:
    """Paths added/copied/modified/renamed in the index.

    ``--diff-filter=ACMR`` drops deletions, whose staged content is nothing.
    A repository with no commits yet has no HEAD to diff against, so fall back
    to the whole index there.
    """
    done = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "-q", "HEAD"],
        capture_output=True, text=True, errors="replace",
    )
    if done.returncode != 0:
        return _git_paths(root, ["ls-files", "-z", "--cached"])
    return _git_paths(
        root, ["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"]
    )


def read_worktree(root: Path, rel: str):
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_index(root: Path, rel: str):
    """The STAGED content, which is what a commit would carry.

    Reading the working tree here would grade a file the commit does not
    contain, and it fails both ways round: an unstaged fix would excuse a
    staged leak, and an unstaged leak would block a clean commit.
    """
    done = subprocess.run(
        ["git", "-C", str(root), "show", ":" + rel],
        capture_output=True, text=True, errors="replace",
    )
    if done.returncode != 0:
        return None
    return done.stdout


def scan(root: Path, paths: list[str], patterns: list[tuple[str, bool]],
         read) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for rel in paths:
        if rel in EXEMPT:
            continue
        if Path(rel).suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = read(root, rel)
        if text is None:
            continue
        for line in scan_text(text, patterns):
            found.append((rel, line))
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Refuse the real observing site "
                    "(the values come from outside the tree).")
    parser.add_argument("--staged", action="store_true",
                        help="scan the index instead of every tracked file")
    parser.add_argument("--require", action="store_true",
                        help="exit 2 when no needles are configured")
    args = parser.parse_args(argv)

    needles = load_needles()
    if not needles:
        if args.require:
            print(_HOW_TO_CONFIGURE, file=sys.stderr)
            return 2
        print("privacy_scan: no needles configured, nothing scanned "
              "(pass --require to make that an error)")
        return 0

    root = repo_root()
    patterns = patterns_for(needles)
    if args.staged:
        found = scan(root, staged_files(root), patterns, read_index)
    else:
        found = scan(root, tracked_files(root), patterns, read_worktree)

    for rel, line in found:
        print(rel + ":" + str(line) + ": contains a forbidden site value")
    if found:
        print("privacy_scan: " + str(len(found)) + " line(s) carry a forbidden "
              "site value. The value itself is deliberately not printed.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
