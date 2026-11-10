# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Find a bash that can run a script given by a Windows path (#1167).

`shutil.which("bash")` on a Windows machine returns the first bash on PATH,
which is often `System32\\bash.exe`, the WSL launcher. WSL's bash cannot open
`C:/.../step.sh`, so a test that hands it a scratch script fails with "No such
file or directory". Candidates are therefore tried, not trusted: each one runs a
scratch script by its Windows path, and the first that can is the answer. Git
for Windows' bash is tried before the PATH, because it is the one that can.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def _git_bashes() -> list[str]:
    git = shutil.which("git")
    if not git:
        return []
    root = Path(git).resolve().parent
    found = []
    for base in (root, root.parent, root.parent.parent):
        for rel in ("bin/bash.exe", "usr/bin/bash.exe"):
            if (base / rel).is_file():
                found.append(str(base / rel))
    return found


def _candidates() -> list[str]:
    on_path = [shutil.which("bash")]
    return list(dict.fromkeys(c for c in _git_bashes() + on_path if c))


def _runs_a_script_by_path(bash: str) -> bool:
    with tempfile.TemporaryDirectory(prefix="bash-probe-") as scratch:
        script = Path(scratch) / "probe.sh"
        script.write_bytes(b"exit 0\n")
        try:
            done = subprocess.run([bash, "--noprofile", "--norc", script.as_posix()],
                                  capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
    return done.returncode == 0


def find_bash() -> tuple[str | None, str]:
    """Return (path or None, the reason to give when the tests skip)."""
    tried = _candidates()
    for candidate in tried:
        if _runs_a_script_by_path(candidate):
            return candidate, ""
    if not tried:
        return None, "no bash on PATH or next to git"
    return None, "no bash can run a script by its path; tried " + ", ".join(tried)


BASH, BASH_SKIP_REASON = find_bash()
