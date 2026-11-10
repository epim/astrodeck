# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Carry the reviewed hashes of the version-bearing sources across a release (#1073, #1186).

    python tools/licence/version_bump_evidence.py NEW

A release commit changes the version string in two reviewed sources
(server/astrodeck/__init__.py and server/pyproject.toml). This writes their rows in
the reviewed-inputs ledger, and only when each file is HEAD's approved copy with
its single version line changed to NEW and nothing else. Any other difference is
refused: it is a source change and needs `release_evidence.py approve`, not a
version bump. It then drops the ledger rows the base makes redundant and prints
the ledger status. The other files that carry the version are checked, never
written: each one that lags is named with the command that fixes it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_evidence as evidence

CR, LF = "\r", "\n"
#: Each version-bearing reviewed source and the one line that carries its version.
VERSION_LINES = {
    "server/astrodeck/__init__.py": re.compile(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.M),
    "server/pyproject.toml": re.compile(r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.M),
}
_BADGE = re.compile(r"badge/version-([0-9]+\.[0-9]+\.[0-9]+)-")
_JSON_VERSION = re.compile(r'^  "version": "([^"]+)"', re.M)
#: Other files that name the release version, with the fix for one that lags.
CARRIERS = {
    "README.md": (_BADGE, "set the version badge in README.md to {new}"),
    "tools/docs/setup-claims.json": (_BADGE, "set the README claim in tools/docs/setup-claims.json to the README.md badge text for {new}"),
    "ui/package.json": (_JSON_VERSION, 'set "version" in ui/package.json to {new}'),
    "ui/src/credits.generated.json": (_JSON_VERSION, "run python tools/gen_credits.py"),
}


def normalized(text: str) -> str:
    return text.replace(CR + LF, LF)


def version_only_delta(path: str, base_text: str, current_text: str, new: str) -> bool:
    """True when ``current_text`` is ``base_text`` with its single version line
    changed to ``new`` and nothing else (line endings ignored)."""
    line = VERSION_LINES[path]
    base, current = normalized(base_text), normalized(current_text)
    found = line.findall(base)
    if len(found) != 1:
        return False
    swapped = line.sub(lambda m: m.group(0).replace(m.group(1), new), base)
    return swapped == current


def carry_ledger(root: Path, new: str) -> list[str]:
    """Write the ledger rows for the version-bearing sources; return what was done."""
    ledger, problems = evidence.read_ledger(root)
    if ledger is None:
        raise SystemExit("; ".join(problems))
    head = evidence.head_revision(root)
    done = []
    for path, line in VERSION_LINES.items():
        current = (root / path).read_bytes()
        if evidence.row_verdict(ledger, path, current):
            done.append(f"{path}: ledger row already current")
            continue
        shown = evidence.git(root, "show", f"HEAD:{path}")
        if shown.returncode:
            raise SystemExit(f"{path} is not in HEAD")
        if not evidence.approved(root, ledger, path, shown.stdout):
            raise SystemExit(f"HEAD's copy of {path} was not approved; approve it or run release_evidence.py sync first")
        previous = shown.stdout.decode("utf-8")
        if not version_only_delta(path, previous, current.decode("utf-8"), new):
            raise SystemExit(f"{path} differs from HEAD's approved copy by more than its version line; review it")
        old = line.search(normalized(previous)).group(1)
        row = {"path": path, "sha256": evidence.digest(current), "reviewed_revision": head,
               "review": (f"Carried by version_bump_evidence from the approved copy at {head[:12]}: "
                          f"the version line changed from {old} to {new} and nothing else.")}
        ledger["files"] = [r for r in ledger["files"] if r["path"] != path] + [row]
        done.append(f"{path}: ledger row carried to {new}")
    evidence.write_ledger(root, ledger)
    return done


def lagging_carriers(root: Path, new: str) -> list[str]:
    lagging = []
    for path, (pattern, fix) in CARRIERS.items():
        found = pattern.findall((root / path).read_text(encoding="utf-8"))
        if not found or any(version != new for version in found):
            lagging.append(f"{path} does not carry {new}: " + fix.format(new=new))
    return lagging


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", argv[0]):
        raise SystemExit("usage: version_bump_evidence.py NEW (plain X.Y.Z)")
    new = argv[0]
    print("\n".join(carry_ledger(ROOT, new)))
    dropped = evidence.sync(ROOT)
    print("\n".join(f"dropped redundant row {path}" for path in dropped))
    lagging = lagging_carriers(ROOT, new)
    print("\n".join(lagging))
    result = evidence.survey(ROOT)
    print(evidence.render(result))
    return 1 if lagging or evidence.work_remains(result) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
