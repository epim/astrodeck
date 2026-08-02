"""The release workflow must actually PASS --strict.

`build_release.py --strict` was written and then wired nowhere: for six releases
the workflow called the bundler with the flag off, so any asset it was asked for
and did not get produced a WARNING line and a published tarball. That is how
0.2.18 shipped with no survey pack (#100).

A YAML line in .github/workflows/ is executed only by a tag push, and only on
GitHub. Nothing in this repository runs it, so nothing in this repository
noticed it was wrong. These tests read the workflow as text and assert the two
things that would make the flag stop working: that it is absent, and that it is
spelled something the script no longer accepts.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
SCRIPT = REPO / "scripts" / "build_release.py"


def _bundler_invocations() -> list[tuple[Path, str]]:
    """Every `build_release.py ...` command line in every workflow, as
    (workflow, command). Backslash continuations are joined first — a wrapped
    invocation carrying the flag on its second line must not read as a flagless
    one — and YAML comments are dropped so prose about the script is not
    mistaken for a call to it."""
    found: list[tuple[Path, str]] = []
    for wf in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        joined = re.sub(r"\\\s*\n\s*", " ", wf.read_text(encoding="utf-8"))
        for line in joined.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "build_release.py" not in stripped:
                continue
            found.append((wf, stripped))
    return found


def test_every_release_build_in_ci_passes_strict():
    """Without --strict the bundler warns and ships. Unattended, that is not a
    control: a tag push has no one reading the log."""
    calls = _bundler_invocations()
    assert calls, (f"no workflow under {WORKFLOWS} invokes build_release.py — "
                   "the release bundle is not being built by CI at all")
    flagless = [f"{wf.name}: {cmd}" for wf, cmd in calls if "--strict" not in cmd]
    assert not flagless, (
        "release build without --strict, so a missing UI bundle, ASTAP database "
        "or survey pack would publish a warning and a tarball:\n  "
        + "\n  ".join(flagless))


@pytest.mark.parametrize("wf,cmd", _bundler_invocations(),
                         ids=lambda v: v.name if isinstance(v, Path) else "cmd")
def test_the_flags_the_workflow_passes_are_flags_the_script_accepts(wf, cmd):
    """The other way the wiring rots: argparse gets renamed and the workflow
    keeps passing the old spelling. That is not a warning, it is an argparse
    exit 2 on a tag push — but only discovered by cutting a release."""
    help_text = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                               capture_output=True, text=True, timeout=120).stdout
    passed = set(re.findall(r"--[A-Za-z][A-Za-z0-9-]*",
                            cmd.split("build_release.py", 1)[1]))
    unknown = sorted(f for f in passed if f not in help_text)
    assert not unknown, f"{wf.name} passes {unknown}, which {SCRIPT.name} does not accept"
