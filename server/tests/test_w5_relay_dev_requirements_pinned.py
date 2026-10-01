"""The relay's DEV requirements are mirrored between requirements-dev.txt and
pyproject.toml's ``[project.optional-dependencies] dev`` list (#629, backlog
WP-71 remainder, W5 integration -- blocking).

WP-71 added ``httpx2`` to ``relay/requirements-dev.txt`` so a locally built
venv picks it up and ``starlette.testclient`` avoids its deprecated-httpx-shim
warning (test_healthz_build.py::
test_the_test_client_import_avoids_the_deprecated_httpx_shim passes locally
against that venv). But CI's relay job and deploy-relay's test job never
install from requirements-dev.txt -- both run ``pip install -e ".[dev]"``,
which resolves relay/pyproject.toml's ``dev`` extra instead
(test_w2_relay_requirements_pinned.py's own docstring traces the identical gap
for the runtime pins, there fixed by WP-13). Without this file, the new dev
pin would pass every local run and still fail the first real CI run, exactly
the shape #629 was filed to close.

This reads both files as text/TOML -- it does not import the relay package,
so it needs no relay venv and runs under the server suite's own PYTHONPATH.

MUTATION "the pyproject entry removed", 2026-10-01, run in a byte backup of
this worktree (sha256-verified restore): relay/pyproject.toml's
``"httpx2>=2.13"`` entry deleted from the ``dev`` extra. 2 failed, observed:
    test_pyproject_dev_extra_mirrors_requirements_dev_exactly:
        AssertionError: relay/pyproject.toml's [dev] extra and
        relay/requirements-dev.txt disagree: {'httpx2': (None, '>=2.13')}
    test_httpx2_is_pinned_in_the_pyproject_dev_extra_too:
        AssertionError: relay/pyproject.toml's [dev] extra no longer lists
        httpx2
Mutant text confirmed absent after restore (grep -c 'httpx2' relay/pyproject.toml
back to 1, sha256 of pyproject.toml matched the pre-mutation backup).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

#: relay/requirements-dev.txt, found relative to this file rather than the
#: current working directory, so the test passes no matter where pytest is
#: invoked from.
_REQUIREMENTS_DEV = (Path(__file__).resolve().parents[2] / "relay"
                      / "requirements-dev.txt")

#: relay/pyproject.toml, the file both CI's relay job and deploy-relay's test
#: job actually install from (``pip install -e ".[dev]"``).
_PYPROJECT = (Path(__file__).resolve().parents[2] / "relay" / "pyproject.toml")

#: The package #629 is specifically about: pinned in requirements-dev.txt to
#: dodge starlette.testclient's deprecated-httpx shim under the relay's
#: pinned starlette==1.7.0.
_HTTPX2_PACKAGE = "httpx2"


def _package_name(line: str) -> str:
    """The distribution name a requirement/dependency line names, with any
    extras stripped (none of the dev packages carry any today, but this
    stays consistent with test_w2_relay_requirements_pinned.py's own
    helper, which grades the runtime pins the same way)."""
    return (line.split("[", 1)[0].split("=", 1)[0].split(">", 1)[0]
            .split("<", 1)[0].strip())


def _specifier(line: str) -> str:
    """Everything after the package name: the comparator plus version (e.g.
    '>=2.13'). The dev deps are not pinned to an exact version the way the
    runtime ones are, so this grades the whole specifier rather than just an
    '==' pin."""
    name = _package_name(line)
    return line[len(name):].strip()


def _requirements_dev_lines() -> list[str]:
    lines = []
    for raw in _REQUIREMENTS_DEV.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r "):
            continue  # the runtime include, not a dev-only package
        lines.append(line)
    return lines


def _pyproject_dev_dependencies() -> list[str]:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return list(data["project"]["optional-dependencies"]["dev"])


def test_requirements_dev_file_exists_and_is_not_empty():
    """Control: if this fails, every other case here grades nothing."""
    assert _REQUIREMENTS_DEV.is_file(), f"missing {_REQUIREMENTS_DEV}"
    assert _requirements_dev_lines(), (
        f"{_REQUIREMENTS_DEV} has no dev package lines")


def test_pyproject_declares_a_dev_extra():
    """Control: if this fails, every other pyproject case here grades
    nothing."""
    assert _PYPROJECT.is_file(), f"missing {_PYPROJECT}"
    assert _pyproject_dev_dependencies(), (
        f"{_PYPROJECT} declares no [project.optional-dependencies] dev list")


def test_pyproject_dev_extra_mirrors_requirements_dev_exactly():
    """CI and deploy-relay install ``.[dev]``, never requirements-dev.txt: a
    package pinned only in the latter protects nothing either job actually
    installs from. The two lists must name the same packages at the same
    specifier."""
    dev_txt = {_package_name(line): _specifier(line)
               for line in _requirements_dev_lines()}
    pyproject_dev = {_package_name(line): _specifier(line)
                     for line in _pyproject_dev_dependencies()}
    names = set(dev_txt) | set(pyproject_dev)
    disagree = {name: (pyproject_dev.get(name), dev_txt.get(name))
                for name in names
                if pyproject_dev.get(name) != dev_txt.get(name)}
    assert not disagree, (
        f"relay/pyproject.toml's [dev] extra and relay/requirements-dev.txt "
        f"disagree: {disagree}")


def test_httpx2_is_pinned_in_the_pyproject_dev_extra_too():
    """#629: httpx2 must be installed by ``pip install -e ".[dev]"`` -- the
    command CI and deploy-relay actually run -- or the first real CI run
    hits the deprecated-httpx-shim warning requirements-dev.txt was meant to
    prevent."""
    by_name = {_package_name(line): line
               for line in _pyproject_dev_dependencies()}
    assert _HTTPX2_PACKAGE in by_name, (
        f"relay/pyproject.toml's [dev] extra no longer lists "
        f"{_HTTPX2_PACKAGE}")
