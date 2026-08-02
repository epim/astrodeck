"""The release workflow must actually PASS --strict, with a command line the
script still parses.

`build_release.py --strict` was written and then wired nowhere: for six releases
the workflow called the bundler with the flag off, so any asset it was asked for
and did not get produced a WARNING line and a published tarball. That is how
0.2.18 shipped with no survey pack (#100).

A YAML line in .github/workflows/ is executed only by a tag push, and only on
GitHub. Nothing in this repository runs it, so nothing in this repository
noticed it was wrong. These tests read the workflow as text and cover the ways
the wiring rots: the flag goes missing, the flag is spelled something argparse
no longer accepts, or the build is made green by waiving the asset instead of
producing it.
"""
import re
import shlex
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
def test_the_workflow_command_line_is_one_the_script_still_parses(wf, cmd, tmp_path):
    """The other way the wiring rots: a flag (or the name of an asset it takes)
    is renamed in argparse and the workflow keeps passing the old spelling. That
    is not a warning, it is an argparse exit 2 on a tag push, discovered only by
    cutting a release.

    So RUN the workflow's own command line, pointed at an empty --repo-root so
    the build dies on the first missing input and only the parser is under test.
    Comparing the flags against `--help` TEXT — which is what this test used to
    do — cannot detect the rot: --help prints the module docstring, and the
    docstring names every flag in prose, so a flag argparse no longer registers
    is still a substring of the help output. Verified by renaming --strict in a
    copy of the script and leaving its docstring alone: the old check passed
    while the real command exited 2 with `unrecognized arguments: --strict`.
    """
    argv = shlex.split(cmd.split("build_release.py", 1)[1])
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *argv,
         "--repo-root", str(tmp_path / "not-a-repo"), "--out", str(tmp_path / "out")],
        capture_output=True, text=True, timeout=120)
    # argparse rejects a command line as "<prog>: error: ..." and exits 2. Any
    # other failure is the build refusing to run against an empty repo, which is
    # what we asked it to do.
    assert f"{SCRIPT.name}: error:" not in proc.stderr, (
        f"{wf.name} passes a command line {SCRIPT.name} rejects — a tag push "
        f"would exit 2 here:\n{proc.stderr}")


def test_the_release_build_never_waives_the_built_ui():
    """--allow-missing is the escape hatch for an asset a build genuinely cannot
    produce — a mirror outage during a hotfix. The UI is never that: `npm run
    build` runs two steps above it in the same job, and a release with no
    interface is the exact outcome --strict exists to prevent. A red release is
    fixed by producing the asset, not by declaring it expendable."""
    waived = [f"{wf.name}: {cmd}" for wf, cmd in _bundler_invocations()
              if re.search(r"--allow-missing[=\s]+ui\b", cmd)]
    assert not waived, (
        "the release build waives the built UI, which would publish a tarball "
        "whose server comes up with /healthz green and no interface:\n  "
        + "\n  ".join(waived))
