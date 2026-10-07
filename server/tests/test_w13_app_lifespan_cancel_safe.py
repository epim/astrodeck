# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-59 (continues WP-35, #252): ``_lifespan``'s shutdown ``finally`` carried
three instances of the #252 try/except shape, one per background task it
stops directly (the dispatcher's own ``run()`` task, the boot-cause logger,
and a still-running boot auto-connect) --

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

-- which cannot tell that task's own cancellation (``_lifespan`` cancelling
``dispatcher.run()``'s task on the way out, its OWN deliberate action, not
triggered by anybody cancelling the lifespan itself -- the ASGI shutdown
message that gets ``_lifespan`` here arrives NORMALLY, no cancellation
involved) from a cancel aimed at the CALLER of the lifespan's shutdown
landing WHILE that reap is in flight: a supervisor's shutdown-timeout
escalation (the same shape a Fly/systemd SIGKILL does) forcibly cancelling
the task running the ASGI lifespan mid-grace-period. The caller runs on
past that cancel instead of ending with it, so whatever shutdown step comes
after the swallowed reap (clearing the boot-cause task, tearing the rig
down) still runs to completion on a process the supervisor already gave up
waiting for, UNBOUNDED BY WHATEVER BOUND THE ESCALATION WAS FOR. ``app.py``
was one of the three "hot" files WP-35 (#252) deferred; WP-59 converts all
three sites to ``astrodeck.aio.reap``.

This test drives the first of the three (``task``, ``dispatcher.run()``'s
own) directly against the REAL ``_lifespan`` (an ``@asynccontextmanager``
function, entered here exactly as Starlette's own lifespan handling does --
the ``async with`` body returns NORMALLY, as it does once the ASGI
"lifespan.shutdown" message arrives in production, not via a cancellation --
with fine-grained control over WHEN the CALLER is cancelled that
``TestClient`` cannot give: it runs the ASGI lifespan on its own
thread/portal with no such hook). Boot auto-connect is opted out
(``ASTRODECK_NO_AUTOCONNECT``) so the scenario needs no profile/backend
fixture at all; every other background service ``_lifespan`` starts
(weather, resume-arm, dawn-park, ...) stops in well under a millisecond on a
freshly constructed, never-ticked instance, so the one slow site is the
dispatcher's.

Named mutant: ``await reap(task)`` put back as the #252 try/except shape
above (``dispatcher.run()``'s site only) -- RED, observed verbatim:

    AssertionError: _lifespan's shutdown ate its caller's cancel: the caller
    ran on (the code after it ran: True)
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.api.app as app_module

pytestmark = pytest.mark.asyncio

_DYING_S = 0.2
_BOUND_S = 3.0


async def _dies_slowly(dying: asyncio.Event, died: asyncio.Event,
                       seconds: float = _DYING_S) -> None:
    """A task parked in work that takes ``seconds`` to tear down once
    cancelled, standing in for ``dispatcher.run()`` (mirrors
    test_w5_weather_stop_reaps.py's helper)."""
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        dying.set()
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while (left := end - loop.time()) > 0:
            try:
                await asyncio.sleep(left)
            except asyncio.CancelledError:
                pass
        died.set()
        raise


@pytest.fixture
def lifespan_env(tmp_path, monkeypatch):
    """Isolated config dir (belt and braces over the suite's own session-wide
    CONFIG_DIR swap) and boot auto-connect opted out, so ``_lifespan`` runs
    its full real startup/shutdown with no device, profile or network
    behind it. Returns nothing; the fixture is its side effects."""
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")


async def _enter_and_wait(dying: asyncio.Event) -> tuple[asyncio.Task, list]:
    """Run ``_lifespan`` as Starlette's own lifespan handling does: the
    ``async with`` body returns NORMALLY (as it does in production once the
    ASGI "lifespan.shutdown" message arrives -- nothing cancels the context
    manager to get there), which drives ``_lifespan``'s ``finally`` the same
    way ``abort()``'s own ``self._task.cancel()`` drives its reap: on
    purpose, not because anything external was already cancelling the
    caller. The caller (``caller_task``) is cancelled from OUTSIDE once the
    dispatcher's task is confirmed dying -- simulating a supervisor's
    shutdown-timeout escalation landing mid-grace-period. Returns
    ``(caller_task, ran_after)``."""
    ran_after: list[bool] = []

    async def caller() -> None:
        async with app_module._lifespan(object()):
            pass   # the ASGI shutdown message "arrived" at once
        ran_after.append(True)

    task = asyncio.create_task(caller())
    await asyncio.wait_for(dying.wait(), timeout=_BOUND_S)
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait({task}, timeout=_BOUND_S)
    return task, ran_after


async def test_a_cancelled_lifespan_shutdown_does_not_run_on(
        lifespan_env, monkeypatch):
    """The lifespan's caller is cancelled ONCE, while its shutdown is
    already inside ``task.cancel(); await reap(task)`` for the dispatcher's
    own run loop (made to take 0.2 s to die so the race is deterministic):
    it must end with ``CancelledError`` within the bound, and the shutdown
    steps after that reap (clearing the boot-cause task, tearing the rig
    down) must not run."""
    dying, died = asyncio.Event(), asyncio.Event()

    async def _slow_dispatcher_run() -> None:
        await _dies_slowly(dying, died)

    monkeypatch.setattr(app_module.dispatcher, "run", _slow_dispatcher_run)

    task, ran_after = await _enter_and_wait(dying)
    assert task.done(), f"the caller had not ended {_BOUND_S} s after its cancel"
    assert task.cancelled(), (
        f"_lifespan's shutdown ate its caller's cancel: the caller ran on "
        f"(the code after it ran: {bool(ran_after)})")
    assert not ran_after
    assert died.is_set(), "the caller heard its cancel before the dispatcher died"


async def test_control_an_uncancelled_lifespan_shuts_down_cleanly(
        lifespan_env, monkeypatch):
    """CONTROL: nothing cancels the caller. The lifespan starts and stops
    cleanly, including the (fast, here) dispatcher task."""
    died = asyncio.Event()

    async def _quick_dispatcher_run() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            died.set()
            raise

    monkeypatch.setattr(app_module.dispatcher, "run", _quick_dispatcher_run)

    async def body() -> None:
        async with app_module._lifespan(object()):
            pass

    await asyncio.wait_for(body(), timeout=_BOUND_S)
    assert died.is_set()
