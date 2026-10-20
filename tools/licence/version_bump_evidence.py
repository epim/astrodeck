# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Carry the reviewed source hashes across a release version bump (#1073).

    python tools/licence/version_bump_evidence.py NEW

A release commit changes the version string in two reviewed sources
(server/astrodeck/__init__.py and server/pyproject.toml). Their reviewed hashes
in artifact-registry.json would then mismatch and the source gate would fail.
This refreshes exactly those two rows, and only when each file is the reviewed
base's copy (audit_release.BASE) with the base's own version string replaced by
NEW at exactly that one place. Any other difference is refused: it is a source
change and needs a review, not a version bump.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CR, LF = "\r", "\n"
#: Each version-bearing reviewed source and the one line that carries its version.
VERSION_LINES = {
    "server/astrodeck/__init__.py": re.compile(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.M),
    "server/pyproject.toml": re.compile(r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.M),
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


def reviewed_base(root: Path) -> str:
    text = (root / "tools/licence/audit_release.py").read_text(encoding="utf-8")
    match = re.search(r'^BASE = "([0-9a-f]{40})"', text, re.M)
    if not match:
        raise SystemExit("audit_release.BASE not found")
    return match.group(1)


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", argv[0]):
        raise SystemExit("usage: version_bump_evidence.py NEW (plain X.Y.Z)")
    new = argv[0]
    base = reviewed_base(ROOT)
    reg_path = ROOT / "tools/licence/artifact-registry.json"
    raw = reg_path.read_bytes().decode("utf-8")
    crlf = CR + LF in raw
    reg = json.loads(normalized(raw))
    rows = {row["path"]: row for row in reg["reviewed_sources"]}
    for path in VERSION_LINES:
        shown = subprocess.run(["git", "show", f"{base}:{path}"], cwd=ROOT, capture_output=True)
        if shown.returncode:
            raise SystemExit(f"{path} is not in the reviewed base {base[:12]}")
        current = (ROOT / path).read_bytes().decode("utf-8")
        if not version_only_delta(path, shown.stdout.decode("utf-8"), current, new):
            raise SystemExit(f"{path} differs from the reviewed base by more than its version line; review it")
        rows[path]["sha256"] = hashlib.sha256(normalized(current).encode("utf-8")).hexdigest()
        print(f"{path}: reviewed hash carried to {new}")
    text = json.dumps(reg, indent=2, ensure_ascii=True) + LF
    reg_path.write_bytes((text.replace(LF, CR + LF) if crlf else text).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
