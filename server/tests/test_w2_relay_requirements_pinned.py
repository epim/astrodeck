"""The relay's runtime requirements are pinned to an exact version (#597).

Since relay v9 (deployed 2026-09-28) the rig's tunnel has dropped once an
hour where v8 held it for 13-38 h at a time. `relay/requirements.txt` pinned
nothing (`uvicorn[standard]>=0.30`, `websockets>=14.0`, `starlette>=1.6.0`),
so that rebuild silently pulled whatever was newest on PyPI the day it ran
(uvicorn 0.54.0, websockets 17.1, starlette 1.7.0) -- the only things that
changed between the two images. An unpinned line makes every future rebuild
the same gamble: a routine `docker build` can change the wire-protocol
libraries without anyone deciding to.

This reads `relay/requirements.txt` as text -- it does not import the relay
package, so it needs no relay venv and runs under the server suite's own
PYTHONPATH.

MUTATION "a pin goes back to a range", 2026-09-30, run in a byte backup of
this worktree (sha256-verified restore): `requirements.txt`'s
`websockets==17.1` line changed to `websockets>=14.0`. 1 failed,
test_every_requirement_line_is_pinned_to_an_exact_version:
    AssertionError: relay/requirements.txt line 'websockets>=14.0' is not
    pinned to an exact version (no '=='): ['websockets>=14.0']
Mutant text confirmed absent after restore (grep -c 'websockets>=14.0' == 0).
"""
from __future__ import annotations

from pathlib import Path

#: relay/requirements.txt, found relative to this file rather than the
#: current working directory, so the test passes no matter where pytest is
#: invoked from.
_REQUIREMENTS = (Path(__file__).resolve().parents[2] / "relay"
                 / "requirements.txt")

#: The three libraries #597 traced the hourly tunnel drop to: pinned to the
#: exact versions read off the live v9 Fly machine (2026-09-30), since the
#: v8 versions that held the tunnel for days could not be recovered.
_V9_PINS = {
    "starlette": "1.7.0",
    "uvicorn": "0.54.0",
    "websockets": "17.1",
}


def _requirement_lines() -> list[str]:
    text = _REQUIREMENTS.read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def _package_name(line: str) -> str:
    """The distribution name a requirement line names, with any extras
    (``uvicorn[standard]``) stripped, so it can be matched against _V9_PINS."""
    return line.split("[", 1)[0].split("=", 1)[0].split(">", 1)[0].strip()


def test_requirements_file_exists_and_is_not_empty():
    """Control: if this fails, every other case here is grading nothing."""
    assert _REQUIREMENTS.is_file(), f"missing {_REQUIREMENTS}"
    assert _requirement_lines(), f"{_REQUIREMENTS} has no requirement lines"


def test_every_requirement_line_is_pinned_to_an_exact_version():
    unpinned = [line for line in _requirement_lines() if "==" not in line]
    assert not unpinned, (
        f"relay/requirements.txt line {unpinned[0]!r} is not pinned to an "
        f"exact version (no '=='): {unpinned}")


def test_the_v9_libraries_are_pinned_to_the_versions_read_off_the_v9_machine():
    """#597's reading: a rebuild must reproduce the v9 image, not drift past
    it, until the drop is diagnosed against a known, stable stack."""
    by_name = {_package_name(line): line for line in _requirement_lines()}
    for name, version in _V9_PINS.items():
        assert name in by_name, f"relay/requirements.txt no longer lists {name}"
        assert by_name[name].endswith(f"=={version}"), (
            f"relay/requirements.txt pins {name} as {by_name[name]!r}, "
            f"not =={version} (the version read off the v9 Fly machine)")
