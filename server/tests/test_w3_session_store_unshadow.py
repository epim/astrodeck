# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The ``session_store`` singleton's instance dict is unshadowed between
tests (#522, backlog WP-27b).

THE MECHANISM (quoted in the issue): a plain ``monkeypatch.setattr(
session_store, "save", ...)`` reads the OLD value with ``getattr`` before
patching, and that old value -- nothing having been set on the INSTANCE
before -- is the bound method the class descriptor hands out. The
``monkeypatch`` FIXTURE's own undo, at ITS OWN teardown, writes that bound
method back onto the instance with ``setattr``, where there was nothing
before, and it stays there for the rest of this worker process.
Instance-``__dict__`` lookup wins over the class for a plain attribute, so a
LATER test that does the correct thing -- ``monkeypatch.setattr(SessionStore,
"save", ...)``, patching the class to spy on or fault-inject the singleton --
patches something the instance never reaches again.

Re-pinned for WP-27 (#522, backlog wave 3 integration). This file used to be
two order-dependent tests (test A left a shadow with no cleanup of its own,
test B proved a class-level patch still reached the singleton), reasoning
that pytest collects one module's tests in definition order and so, with no
randomization plugin installed, test B always ran immediately after test A.
That reasoning named its constraint wrong: the docstring here used to cite
"see conftest.py's docstring note ... -p no:randomly", but no such note
exists in conftest.py, there is no ``pytest-randomly`` dependency anywhere in
this project (grep ``pyproject.toml``), and nothing passes ``-p no:randomly``
in the suite's ``addopts``. That citation was fabricated and is removed.

THE REAL CONSTRAINT is ``pyproject.toml``'s ``addopts = "-n 12 --dist
worksteal"``: xdist's worksteal scheduler hands out test items to worker
PROCESSES as they free up, and does not keep a module's tests on one worker --
it splits adjacent tests (including this file's test A and test B) across
different worker processes in the normal full-suite run. Different
processes do not share ``session_store``'s instance ``__dict__`` or any other
process-global state, so in the real scheduled run test B's fresh process
never sees test A's leftover shadow regardless of whether the conftest guard
exists. The pair could therefore never go red in the suite's own normal run,
only under ``-n0`` -- a mode nothing here actually exercises.

This file now runs the real conftest fixture's generator directly, which
needs no particular worker or scheduling order and goes red in the normal
xdist suite run, not only under ``-n0``.
"""
from __future__ import annotations

import inspect

import pytest

import conftest  # rootdir-relative, as test_capture_root_isolated does
from astrodeck.sequence.session import SessionStore, session_store

_FIXTURE_NAME = "_a_session_store_instance_dict_is_unshadowed"


def test_the_unshadow_fixture_removes_a_leftover_bound_method_shadow():
    """Self-contained, scheduler-independent replacement for the old
    two-test pair: seeds the exact shadow ``monkeypatch``'s own undo leaves,
    then drives the REAL autouse fixture from ``conftest.py`` -- not a
    reimplementation of its cleanup -- through both halves of its generator,
    and proves the shadow is gone and a class-level patch reaches the
    singleton again.

    MUTATION "the cleanup loop removed from the fixture" (the ``for name in
    stale: delattr(...)`` loop deleted, leaving the ``yield`` and nothing
    after it). Observed:

        AssertionError: the real autouse fixture left a save shadow in place

    This goes red in the normal ``-n 12 --dist worksteal`` suite run, because
    it needs no second test and no particular worker: everything happens
    inside one test function, against the one fixture object conftest.py
    actually defines.
    """
    fixture_def = getattr(conftest, _FIXTURE_NAME)
    # The undecorated generator function pytest's ``@fixture`` wraps --
    # calling it directly drives the REAL body in conftest.py, not a copy of
    # its logic. If the fixture were deleted, this attribute lookup itself
    # would raise AttributeError, which is also a legitimate red for that
    # mutant.
    raw = fixture_def.__wrapped__
    assert inspect.isgeneratorfunction(raw), (
        f"{_FIXTURE_NAME} must stay a yield-style fixture for this to drive "
        f"its teardown half directly")

    gen = raw()
    next(gen)  # setup half (empty, before the `yield`, in this fixture)

    # Seed the leak directly: the bound method the class descriptor hands
    # out, planted onto the INSTANCE -- exactly what `monkeypatch`'s own
    # undo writes at its teardown after `monkeypatch.setattr(session_store,
    # "save", ...)`, reproduced here without needing a second test or a
    # second process to supply it.
    shadow = session_store.save
    session_store.save = shadow
    assert vars(session_store).get("save") is shadow, (
        "precondition broken: the shadow was not planted on the instance")

    # Drive the fixture's teardown half -- the real cleanup code.
    with pytest.raises(StopIteration):
        next(gen)

    assert "save" not in vars(session_store), (
        "the real autouse fixture left a save shadow in place")

    # The round trip #522 cared about: with the shadow gone, a class-level
    # patch -- the pattern test_resume_crash_loop.py and the S7-LEDGER cost
    # tests use -- reaches the singleton again.
    calls: list[object] = []

    def fake_save(self, session):
        calls.append(session)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(SessionStore, "save", fake_save)
        sentinel = object()
        session_store.save(sentinel)
        assert calls == [sentinel]


def test_the_unshadow_fixture_sorts_before_every_autouse_fixture_that_takes_monkeypatch():
    """Pins the ordering fact WP-27's coder measured (conftest.py's own
    docstring on ``_a_session_store_instance_dict_is_unshadowed``): pytest
    sets up one conftest's same-scope autouse fixtures with no explicit
    interdependency in ``dir()`` (alphabetical name) order, and tears them
    down in reverse. For this fixture's teardown to see the shadow
    ``monkeypatch``'s own undo plants, its teardown must run AFTER every
    other autouse, function-scoped fixture that takes ``monkeypatch`` -- i.e.
    its name must sort alphabetically BEFORE theirs.

    MUTATION "fixture renamed to _unshadow_session_store_instance_dict"
    (sorts after ``_fast_sim_delays`` and ``_captures_are_the_tests_own``,
    both of which take ``monkeypatch``). Observed:

        AssertionError: these autouse, function-scoped, monkeypatch-taking
        fixtures sort BEFORE _unshadow_session_store_instance_dict and would
        see their teardown run first, leaving the shadow in place:
        ['_captures_are_the_tests_own', '_fast_sim_delays']

    Scheduler-independent: this reads conftest.py's fixtures directly, not a
    cross-test leak, so it goes red under the rename in the normal xdist
    suite run too.
    """
    fixture_def = getattr(conftest, _FIXTURE_NAME)
    marker = fixture_def._fixture_function_marker
    assert marker.autouse, f"{_FIXTURE_NAME} must stay autouse"
    assert marker.scope == "function"

    offenders = []
    for name, obj in vars(conftest).items():
        if name == _FIXTURE_NAME:
            continue
        other_marker = getattr(obj, "_fixture_function_marker", None)
        if other_marker is None or not other_marker.autouse:
            continue
        if other_marker.scope != "function":
            continue
        other_raw = obj.__wrapped__
        params = inspect.signature(other_raw).parameters
        if "monkeypatch" in params and name < _FIXTURE_NAME:
            offenders.append(name)

    assert offenders == [], (
        f"these autouse, function-scoped, monkeypatch-taking fixtures sort "
        f"BEFORE {_FIXTURE_NAME} and would see their teardown run first, "
        f"leaving the shadow in place: {sorted(offenders)}")
