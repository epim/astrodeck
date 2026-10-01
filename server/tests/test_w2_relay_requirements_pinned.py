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

W2 INTEGRATION addendum (backlog WP-13, owner-approved 2026-09-30): CI's
relay job and deploy-relay's test job both install from
`relay/pyproject.toml`, not from `requirements.txt` -- WP-13 pinned only the
latter, so a routine `pip install -e relay` (what both jobs run) still
resolved whatever range `pyproject.toml`'s `[project] dependencies` named.
`pyproject.toml` now mirrors the same four exact pins, and the cases below
grade it the same way: every dependency line pinned with `==`, and the three
#597 libraries at the v9 versions above (``cryptography`` has no v8 baseline
to compare against, so it is graded for exactness only, against whatever
`requirements.txt` itself pins -- the two files must never disagree, which is
what `test_pyproject_mirrors_requirements_exactly` is for).

MUTATION "one pyproject line unpinned", 2026-09-30, run in a byte backup of
this worktree (sha256-verified restore): `pyproject.toml`'s
`"websockets==17.1"` line changed to `"websockets>=14.0"`. 3 failed, observed:
    test_every_pyproject_dependency_is_pinned_to_an_exact_version:
        AssertionError: relay/pyproject.toml dependency 'websockets>=14.0'
        is not pinned to an exact version (no '=='): ['websockets>=14.0']
    test_pyproject_mirrors_requirements_exactly:
        AssertionError: relay/pyproject.toml and relay/requirements.txt
        disagree: {'websockets': (None, '==17.1')}
    test_the_v9_libraries_are_pinned_in_pyproject_too:
        AssertionError: relay/pyproject.toml pins websockets as
        'websockets>=14.0', not ==17.1 (the version read off the v9 Fly
        machine)
Mutant text confirmed absent after restore (grep -c 'websockets>=14.0' == 0,
sha256 of pyproject.toml matched the pre-mutation backup).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

#: relay/requirements.txt, found relative to this file rather than the
#: current working directory, so the test passes no matter where pytest is
#: invoked from.
_REQUIREMENTS = (Path(__file__).resolve().parents[2] / "relay"
                 / "requirements.txt")

#: relay/pyproject.toml, the file both CI's relay job and deploy-relay's
#: test job actually install from (``pip install -e relay``).
_PYPROJECT = (Path(__file__).resolve().parents[2] / "relay" / "pyproject.toml")

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


# ------------------------------------------- relay/pyproject.toml (W2 WP-13)
# CI's relay job and deploy-relay's test job both ``pip install -e relay``,
# which resolves dependencies from THIS file, never from requirements.txt --
# so requirements.txt being pinned protected nothing either job actually
# installs from. Graded the same two ways: every line an exact pin, and (new,
# since the two files could otherwise just as easily drift from EACH OTHER)
# every pin here identical to requirements.txt's.

def _pyproject_dependencies() -> list[str]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


def _version_pin(line: str) -> str | None:
    """The ``==`` pin a requirement/dependency line carries (``>=1.0``'s
    comparator and version both come back in ``_package_name``'s leftovers,
    so this reads the OTHER side: the exact pin, or None when there is
    none)."""
    if "==" not in line:
        return None
    return "==" + line.split("==", 1)[1].strip()


def test_pyproject_file_exists_and_declares_dependencies():
    """Control: if this fails, every other pyproject case here grades
    nothing."""
    assert _PYPROJECT.is_file(), f"missing {_PYPROJECT}"
    assert _pyproject_dependencies(), (
        f"{_PYPROJECT} declares no [project] dependencies")


def test_every_pyproject_dependency_is_pinned_to_an_exact_version():
    unpinned = [line for line in _pyproject_dependencies() if "==" not in line]
    assert not unpinned, (
        f"relay/pyproject.toml dependency {unpinned[0]!r} is not pinned to "
        f"an exact version (no '=='): {unpinned}")


def test_pyproject_mirrors_requirements_exactly():
    """The two files must never disagree about what version ships: a mismatch
    is a gap for the *installed* package (pyproject's) to be older or newer
    than the one requirements.txt documents and the next person reads."""
    req = {_package_name(line): _version_pin(line)
          for line in _requirement_lines()}
    pyp = {_package_name(line): _version_pin(line)
          for line in _pyproject_dependencies()}
    names = set(req) | set(pyp)
    disagree = {name: (pyp.get(name), req.get(name)) for name in names
               if pyp.get(name) != req.get(name)}
    assert not disagree, (
        f"relay/pyproject.toml and relay/requirements.txt disagree: "
        f"{disagree}")


def test_the_v9_libraries_are_pinned_in_pyproject_too():
    by_name = {_package_name(line): line for line in _pyproject_dependencies()}
    for name, version in _V9_PINS.items():
        assert name in by_name, f"relay/pyproject.toml no longer lists {name}"
        assert by_name[name].endswith(f"=={version}"), (
            f"relay/pyproject.toml pins {name} as {by_name[name]!r}, not "
            f"=={version} (the version read off the v9 Fly machine)")
