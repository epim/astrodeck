# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The #522 unshadow sweep covers the engine, not only ``session_store`` (#833).

``monkeypatch.setattr(app_module.engine, "abort", ...)`` reads the old value
with ``getattr`` (the class's bound method) and its undo writes it back with
``setattr``, which leaves ``abort`` in ``vars(engine)``. A later
``monkeypatch.setattr(SequenceEngine, "abort", ...)`` on the same xdist
worker then patches a function the engine never looks up again, and wave
16's CI went red on Linux on exactly that pair
(test_w15_remote_profile_activate.py, then test_connect_rig_guard.py).

Like test_w3_session_store_unshadow.py, this drives the REAL conftest
fixture's generator inside one test, so it needs no second test and no
particular worker or order.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

import conftest  # rootdir-relative, as test_w3_session_store_unshadow does
import astrodeck.api.app as app_module
from astrodeck.sequence.engine import SequenceEngine

_FIXTURE_NAME = "_a_session_store_instance_dict_is_unshadowed"


def test_the_sweep_removes_the_engines_leftover_abort(monkeypatch):
    """Seed the shadow monkeypatch's undo leaves on the engine, drive the
    fixture's teardown, and show a class-level spy reaches ``engine.abort``.

    Mutant "sweep only session_store" (``*_singletons()`` dropped from the
    fixture's swept list) failed:
        AssertionError: the sweep left the engine's abort shadow in place
    """
    engine = app_module.engine
    raw = getattr(conftest, _FIXTURE_NAME).__wrapped__
    assert inspect.isgeneratorfunction(raw)
    gen = raw()
    next(gen)

    shadow = engine.abort
    engine.abort = shadow
    assert vars(engine).get("abort") is shadow, (
        "precondition broken: the shadow was not planted on the engine")

    with pytest.raises(StopIteration):
        next(gen)
    assert "abort" not in vars(engine), (
        "the sweep left the engine's abort shadow in place")

    calls: list[bool] = []

    async def _spy(self):
        calls.append(True)

    monkeypatch.setattr(SequenceEngine, "abort", _spy)
    asyncio.run(engine.abort())
    assert calls == [True], "a class-level spy still does not reach the engine"


def test_the_sweep_leaves_what_a_test_set_on_purpose():
    """Only the class's own function bound to the same instance goes: a
    different callable or plain data under any name stays, so the sweep
    cannot undo something a test (or the code) put there deliberately.

    Mutant "strip every bound method" (the ``getattr(cls, name, None) is
    value.__func__`` term dropped) failed:
        AssertionError: the sweep removed a deliberate override: ['other']
    """
    class Thing:
        def run(self):
            return "class"

        def other(self):
            return "other"

    t = Thing()
    t.run = t.run              # the leftover shape
    t.other = t.run            # a deliberate rebinding to another method
    t.data = 3
    removed = conftest._strip_restored_bound_methods(t)
    assert removed == ["run"], (
        f"the sweep removed a deliberate override: "
        f"{[n for n in removed if n != 'run']}")
    assert t.other() == "class" and t.data == 3
