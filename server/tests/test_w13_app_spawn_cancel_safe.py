# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-59 (continues WP-35, #252): ``_spawn``'s and ``_spawn_connect``'s
``wrapped()`` closures carried the #252 try/except shape --

    async def wrapped():
        try:
            await coro
        except asyncio.CancelledError:
            bus.log("warning", f"{name} cancelled", name)
        except (DeviceError, Exception) as e:
            bus.log("error", f"{name} failed: {e}", name)

-- a DIFFERENT instance of the class from the other WP-59 sites: ``wrapped``
is a task's OWN top level, not a reap of some OTHER task, so there is no
``self._task``/caller to pass a cancel to. What it ate instead was its own
cancellation: a cancelled ``asyncio.Task`` is a promise that ``task.cancelled()``
answers True (the "broken promise" family this codebase names -- a claim
nothing keeps) and that an ``await`` of it raises ``CancelledError`` -- and
``wrapped()``'s catch-and-log-only handler broke that promise by letting
the task end as a plain, uncancelled completion. Nothing under
``server/astrodeck/`` awaits one of these tasks directly today (confirmed by
grep, see the issue), so no CALLER is eaten by this -- but the guard
(``test_no_task_await_eats_its_callers_cancel.py``) scans by AST SHAPE, not
by whether a caller exists yet, and a future one (or a test) that does await
``hub._busy[name]``/``_connect_task`` deserves an honest answer. The fix
(WP-59) is a bare ``raise`` after the log, the same shape
``resume_arm.py``'s ``ResumeArm._run`` already uses for its own
pass-through CancelledError handler.

Named mutant (one per function): the ``raise`` removed, restoring the
catch-and-log-only handler -- RED, observed verbatim (``_spawn``'s
``wrapped``; ``_spawn_connect``'s is byte-identical but for the name):

    AssertionError: _spawn's wrapped() ate its own task's cancellation: it
    ended as a normal completion, not cancelled (exception: None)
    assert False
     +  where False = <built-in method cancelled of ...>()
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.api.app as app_module

pytestmark = pytest.mark.asyncio

_BOUND_S = 2.0
_SPAWN_LANE = "w13_test_spawn_lane"


@pytest.fixture(autouse=True)
def _clean_busy_lane():
    """``_SPAWN_LANE`` is a name no route or ``_LANE_SUPERSEDES`` entry uses,
    so it cannot collide with -- or be refused by -- anything else in the
    process-wide ``hub._busy``. Swept after every test regardless of outcome."""
    yield
    t = app_module.hub._busy.pop(_SPAWN_LANE, None)
    if t is not None and not t.done():
        t.cancel()


@pytest.fixture(autouse=True)
def _clean_connect_task():
    yield
    t = app_module._connect_task
    if t is not None and not t.done():
        t.cancel()
    app_module._connect_task = None


async def test_spawn_wrapped_reports_cancelled_when_its_own_task_is():
    """A task ``_spawn`` creates, cancelled directly (as
    ``hub._teardown``/``mount_stop``/etc. all do to a busy-lane task, fire
    and forget), must itself end up ``cancelled()`` -- not a normal
    completion with a warning logged and nothing else to show for it."""
    gate = asyncio.Event()

    async def _never_finishes():
        await gate.wait()

    result = app_module._spawn(_SPAWN_LANE, _never_finishes())
    assert result == {"started": _SPAWN_LANE}
    task = app_module.hub._busy[_SPAWN_LANE]
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait({task}, timeout=_BOUND_S)
    assert task.done(), f"the task had not ended {_BOUND_S} s after its cancel"
    assert task.cancelled(), (
        "_spawn's wrapped() ate its own task's cancellation: it ended as a "
        f"normal completion, not cancelled (exception: "
        f"{task.exception() if not task.cancelled() else None!r})")


async def test_control_spawn_wrapped_completes_normally_when_not_cancelled():
    """CONTROL: nothing cancels the task. It runs to a normal completion,
    as it always did."""
    result = app_module._spawn(_SPAWN_LANE, asyncio.sleep(0))
    assert result == {"started": _SPAWN_LANE}
    task = app_module.hub._busy[_SPAWN_LANE]
    await asyncio.wait_for(task, timeout=_BOUND_S)
    assert not task.cancelled() and task.exception() is None


async def test_spawn_connect_wrapped_reports_cancelled_when_its_own_task_is():
    """Same shape, ``_spawn_connect``'s own ``wrapped()`` (``_connect_task``,
    tracked outside ``hub._busy`` -- see ``_spawn_connect``'s docstring)."""
    gate = asyncio.Event()

    async def _never_finishes():
        await gate.wait()

    result = app_module._spawn_connect(_never_finishes())
    assert result == {"started": "profile"}
    task = app_module._connect_task
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait({task}, timeout=_BOUND_S)
    assert task.done(), f"the task had not ended {_BOUND_S} s after its cancel"
    assert task.cancelled(), (
        "_spawn_connect's wrapped() ate its own task's cancellation: it "
        f"ended as a normal completion, not cancelled (exception: "
        f"{task.exception() if not task.cancelled() else None!r})")


async def test_control_spawn_connect_wrapped_completes_normally():
    """CONTROL: nothing cancels the task."""
    result = app_module._spawn_connect(asyncio.sleep(0))
    assert result == {"started": "profile"}
    task = app_module._connect_task
    await asyncio.wait_for(task, timeout=_BOUND_S)
    assert not task.cancelled() and task.exception() is None
