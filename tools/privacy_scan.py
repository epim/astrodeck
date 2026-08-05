#!/usr/bin/env python3
"""Fail if the developer's real observing site is anywhere in the tree.

The standing rule is that the real coordinates and site label must NEVER appear
in code, tests, or docs. On 2026-08-04 they were found in 26 spec and plan
documents -- pasted there by the boilerplate that FORBIDS them, which spelled
both out in order to name what to avoid. 50 occurrences, live for two weeks,
and the rule's own text was the vector.

A rule nothing enforces is a suggestion. This is the enforcement: run it in CI
and from a pre-commit hook.

The forbidden values are NOT written here either -- that would reintroduce the
leak into the one file guaranteed to be read. They are assembled from parts at
runtime, which is enough to make grep-for-the-literal fail on this file while
still matching the real thing.

ONE file legitimately contains them: ui/src/lib/__tests__/troubleshoot.test.ts,
the test asserting they never reach a troubleshooting export. It is exempt BY
NAME so the exemption cannot quietly widen.

Usage:  python tools/privacy_scan.py            # scan tracked files
        python tools/privacy_scan.py --staged   # scan what is about to commit
Exit 0 = clean, 1 = found (prints file:line), 2 = could not run.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Assembled, never written whole -- see the module docstring.
_LAT = "37." + "348110"
_LON = "121." + "801704"
_LABEL = "My " + "Backyard"
FORBIDDEN = (_LAT, _LON, _LABEL)

#: The only file allowed to contain them, and why.
EXEMPT = {
    "ui/src/lib/__tests__/troubleshoot.test.ts":
        "the guard asserting these never reach a troubleshooting export",
    "tools/privacy_scan.py": "this scanner (assembles them at runtime)",
}

TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".json", ".md", ".txt", ".yml",
    ".yaml", ".toml", ".cfg", ".ini", ".sh", ".ps1", ".html", ".css", ".rs",
}


def _git(*args: str) -> list[str]:
    out = subprocess.run(("git", *args), capture_output=True, text=True,
                         check=True).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def main() -> int:
    staged = "--staged" in sys.argv
    try:
        files = (_git("diff", "--cached", "--name-only", "--diff-filter=ACM")
                 if staged else _git("ls-files"))
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"privacy scan could not list files: {exc}", file=sys.stderr)
        return 2

    hits: list[str] = []
    for rel in files:
        if rel in EXEMPT or Path(rel).suffix.lower() not in TEXT_SUFFIXES:
            continue
        path = Path(rel)
        if not path.is_file():
            continue                      # deleted in the index, or a submodule
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for needle in FORBIDDEN:
                if needle in line:
                    # Report the location, NOT the matched text -- printing it
                    # would put the value into CI logs, which are a publication
                    # channel of their own.
                    hits.append(f"{rel}:{lineno}: contains a forbidden site value")

    if hits:
        print("PRIVACY SCAN FAILED -- the real observing site is in the tree:")
        for h in hits:
            print("  " + h)
        print("\nUse placeholders. Invented coordinates for tests: 40.0 / -105.0.")
        return 1

    print(f"privacy scan clean ({len(files)} files checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
