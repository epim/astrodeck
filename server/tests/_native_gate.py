# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The one gate on a test that needs the ``astrodeck_native`` wheel (#1255).

A test that cannot run without the wheel carries ``@pytest.mark.native`` (a
module-level ``pytestmark`` where the whole file needs it) and nothing else:
no ``skipif`` on an import flag, no ``importorskip``, no skip from inside the
body. ``conftest.py`` hands each such test to :func:`gate` as it is set up.

Without the wheel the test is skipped, with one reason. With
``ASTRODECK_REQUIRE_NATIVE=1`` (the ``native`` CI job, which has just built the
wheel) a missing wheel is an ERROR instead: a skip there would be a green job
that ran nothing, which is how 406 tests ran on no job at all.

A test that needs more than the wheel, a symbol only a newer build has, says so
with ``@pytest.mark.native(lacks=fn)``: ``fn()`` returns a reason when the
installed wheel lacks it, or "". The reason skips, or errors under the flag,
the same way.
"""
from __future__ import annotations

import importlib.util
import os
from types import ModuleType

import pytest

#: Set to "1" where the wheel is built; a test that cannot use it then errors.
REQUIRE_ENV = "ASTRODECK_REQUIRE_NATIVE"

#: The one reason a test skips for want of the wheel.
WHEEL_ABSENT = "the astrodeck_native wheel is not installed"


def _installed() -> bool:
    """True when a wheel is on the path, even one that fails to import."""
    try:
        return importlib.util.find_spec("astrodeck_native") is not None
    except (ImportError, ValueError):  # a finder hiding it, as the server job's path has none
        return False


def optional_native() -> ModuleType | None:
    """The wheel, or None where it is not installed.

    A module-level import of the wheel in a test file goes through this, so
    the file stays collectable on the ``server`` job. It cannot hide a real
    import error: when a wheel IS installed and fails to import (a missing
    shared library, an ABI mismatch), the ImportError propagates and the file
    errors at collection with the real text."""
    try:
        import astrodeck_native
    except ImportError:
        if _installed():
            raise
        return None
    return astrodeck_native


def require_native() -> bool:
    return os.environ.get(REQUIRE_ENV) == "1"


def _problem(item: pytest.Item) -> str:
    """Why this test cannot run on the wheel as installed, or ""."""
    if optional_native() is None:
        return WHEEL_ABSENT
    for marker in item.iter_markers("native"):
        lacks = marker.kwargs.get("lacks")
        reason = lacks() if lacks is not None else ""
        if reason:
            return reason
    return ""


def gate(item: pytest.Item) -> None:
    """Skip a ``native`` test the wheel cannot serve; error under the flag."""
    if item.get_closest_marker("native") is None:
        return
    reason = _problem(item)
    if not reason:
        return
    if require_native():
        pytest.fail(f"{REQUIRE_ENV}=1 and {reason}", pytrace=False)
    pytest.skip(reason)
