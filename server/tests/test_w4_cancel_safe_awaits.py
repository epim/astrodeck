# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#252: two representative sites WP-35 converted to ``astrodeck.aio.reap``.

#235 found the "eats its caller's cancel" shape once, as ``with
contextlib.suppress(asyncio.CancelledError, Exception): await task`` around an
await of a task the same code had just cancelled. #252 found the SAME class
surviving in two spellings the #235 guard did not scan:

* ``with contextlib.suppress(BaseException): await task`` (``BaseException``
  catches ``CancelledError`` too) -- ``remote/relay_client.py``'s
  ``_serve_once``, below;
* ``task.cancel(); try: await task; except (CancelledError, ...): pass``,
  written out instead of a ``suppress`` -- ``dusk_arm.py``'s ``stop``, below.

WP-35 converted every site of both spellings outside ``engine.py``, ``app.py``
and ``resume_arm.py`` (WP-59's), and
``test_no_task_await_eats_its_callers_cancel.py`` now scans for both spellings
across the whole package, so every OTHER converted site is pinned there
structurally: a reverted site anywhere under ``astrodeck/`` fails that guard.
These two are chosen to pin the RUNTIME behaviour as well, because a scan can
only prove the shape is gone from the source, not that ``reap`` actually
carries the cancel through at a real call site: ``_serve_once``'s fix sits
inside a ``finally`` (the issue's own "worth a look first" list), and
``dusk_arm.py`` is the plain ``try``/``except`` spelling's simplest instance.

Each forces the race the same way #235's own tests did: a task that takes
0.2 s to die once cancelled, with the caller cancelled while it waits.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.config import RemoteConfig
from astrodeck.dusk_arm import DuskArm
from astrodeck.remote.protocol import (CONTROL_STREAM_ID, FrameType,
                                       encode_frame)
from astrodeck.remote.relay_client import RelayClient

pytestmark = pytest.mark.asyncio

_DYING_S = 0.2
_BOUND_S = 2.0
_TEST_DEVICE_TOKEN = "t" * 43


async def _dies_slowly(dying: asyncio.Event, died: asyncio.Event,
                       seconds: float = _DYING_S) -> None:
    """A task parked in work that takes ``seconds`` to tear down once
    cancelled, and does not hurry for a second cancel meanwhile (mirrors
    ``test_no_task_await_eats_its_callers_cancel.py``'s helper of the same
    name, kept local so this file stands on its own)."""
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


async def _cancel_mid_wait(call, dying: asyncio.Event):
    """Run ``await call()`` in a caller, cancel the caller while the task it
    waits on is dying, and wait up to ``_BOUND_S`` for it. Returns
    ``(caller_task, ran_after)``."""
    ran_after: list[bool] = []

    async def caller() -> None:
        await call()
        ran_after.append(True)

    task = asyncio.create_task(caller())
    await asyncio.wait_for(dying.wait(), timeout=_BOUND_S)
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait({task}, timeout=_BOUND_S)
    return task, bool(ran_after)


# ------------------------------------------------- dusk_arm.py: try/except


@pytest.fixture
async def dusk_arm():
    """A ``DuskArm`` whose background task takes 0.2 s to die, standing in
    for ``_run`` (never started: ``hub``/``engine`` are not needed to test
    ``stop`` alone). Yields ``(arm, dying, died)``."""
    arm = DuskArm(hub=None, engine=None)
    dying, died = asyncio.Event(), asyncio.Event()
    arm._task = asyncio.create_task(_dies_slowly(dying, died))
    await asyncio.sleep(0)
    yield arm, dying, died
    if arm._task is not None and not arm._task.done():
        arm._task.cancel()
        await asyncio.gather(arm._task, return_exceptions=True)


async def test_a_cancelled_dusk_arm_stop_does_not_run_on(dusk_arm):
    """``DuskArm.stop``'s caller is cancelled while the task it waits on
    dies: it ends with ``CancelledError`` within the bound, and the code
    after the stop (in production, the route's response) does not run.

    MUTANT "restore try/except around the await" (``await reap(self._task)``
    put back as ``try: await self._task except asyncio.CancelledError:
    pass``) -- RED, observed verbatim:

        AssertionError: DuskArm.stop ate its caller's cancel: the caller ran
        on (the code after it ran: True) and self._task was cleared: True
    """
    arm, dying, died = dusk_arm
    task, ran_after = await _cancel_mid_wait(arm.stop, dying)
    assert task.done(), f"the caller had not ended {_BOUND_S} s after its cancel"
    assert task.cancelled(), (
        f"DuskArm.stop ate its caller's cancel: the caller ran on (the code "
        f"after it ran: {ran_after}) and self._task was cleared: "
        f"{arm._task is None}")
    assert not ran_after
    assert died.is_set(), "the caller heard its cancel before the task died"


async def test_control_an_uncancelled_dusk_arm_stop_returns(dusk_arm):
    """CONTROL: nothing cancels the caller. ``stop`` returns once the task is
    dead, and clears it."""
    arm, _dying, died = dusk_arm
    await asyncio.wait_for(arm.stop(), timeout=_BOUND_S)
    assert died.is_set()
    assert arm._task is None


# ------------------------------------------- relay_client.py: suppress(BaseException)


class _Channel:
    """The HELLO handshake, then silence: one ack frame, then the message
    loop parks forever (the relay has gone quiet, not closed). This is where
    a real connection sits once ``config_watch`` is running -- exactly the
    state ``_serve_once`` is in when something ends the loop and its
    ``finally`` cancels ``config_watch``."""

    def __init__(self) -> None:
        self._acked = False
        self.sent: list[bytes] = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._acked:
            self._acked = True
            return encode_frame(FrameType.HELLO_ACK, CONTROL_STREAM_ID,
                                {"ok": True})
        # A real second frame would cost at least one network round trip;
        # yield once so config_watch (just spawned by _serve_once after the
        # ack) gets to actually START and park in its own wait -- without
        # this, cancelling it below would land before it had run its first
        # line, which a generator delivers as if raised at the top, past the
        # try/except _dies_slowly needs to see it land.
        await asyncio.sleep(0)
        raise StopAsyncIteration   # the relay socket ends right after HELLO

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


async def _silent_app(scope, receive, send) -> None:
    return None


@pytest.fixture
async def serving(monkeypatch):
    """A ``RelayClient`` wired to ``_Channel``, whose ``config_watch`` (the
    task ``_serve_once``'s ``finally`` cancels once the message loop ends)
    takes 0.2 s to die. Yields ``(client, cfg, dying, died, teardown_calls)``;
    ``teardown_calls`` records each ``_teardown_connection`` call so a
    cancel landing before it must be seen to have skipped it.

    Uses ``monkeypatch`` (not a bare assign-then-``del``) for the CLASS-level
    patch: ``del`` removes whatever is on the class when it runs, not what
    was there before the patch, so it would delete the real
    ``_watch_connection_config`` off ``RelayClient`` for the rest of the
    process instead of restoring it -- exactly the kind of cross-test
    pollution a fixture must never leave behind."""
    channel = _Channel()

    async def _connect(url: str):
        return channel

    cfg = RemoteConfig(enabled=True, relay_url="wss://relay.test/scope",
                       device_token=_TEST_DEVICE_TOKEN, home_id="home-1")
    client = RelayClient(_silent_app, lambda: cfg, connect=_connect)
    dying, died = asyncio.Event(), asyncio.Event()

    async def _slow_watch(self, ws, initial) -> None:
        await _dies_slowly(dying, died)

    monkeypatch.setattr(RelayClient, "_watch_connection_config", _slow_watch)
    teardown_calls: list[bool] = []
    real_teardown = client._teardown_connection

    async def _tracked_teardown(ws) -> None:
        teardown_calls.append(True)
        await real_teardown(ws)

    monkeypatch.setattr(client, "_teardown_connection", _tracked_teardown)
    yield client, cfg, dying, died, teardown_calls


async def test_a_cancelled_serve_once_does_not_tear_down_past_its_cancel(
        serving):
    """``_serve_once``'s caller is cancelled while its ``finally`` waits for
    ``config_watch`` to die: it ends with ``CancelledError`` within the
    bound, and ``_teardown_connection`` (the next line of that same
    ``finally``) does not run before it -- a caller that ran on into the
    teardown connection cleanup is exactly #252's "running on past a cancel"
    (here: a connection already treated as torn down gets closed a second
    time, or a fresh connection's teardown races a stale one's).

    MUTANT "restore suppress(BaseException) around the await"
    (``await reap(config_watch)`` put back as ``with
    contextlib.suppress(BaseException): await config_watch``) -- RED,
    observed verbatim:

        AssertionError: _serve_once ate its caller's cancel: the caller ran
        on (the code after it ran: True) and tore down the connection:
        [True]
    """
    client, cfg, dying, died, teardown_calls = serving
    task, ran_after = await _cancel_mid_wait(lambda: client._serve_once(cfg),
                                             dying)
    assert task.done(), f"the caller had not ended {_BOUND_S} s after its cancel"
    assert task.cancelled(), (
        f"_serve_once ate its caller's cancel: the caller ran on (the code "
        f"after it ran: {ran_after}) and tore down the connection: "
        f"{teardown_calls}")
    assert not ran_after
    assert not teardown_calls, (
        "the cancelled caller tore down the connection anyway -- it ran on "
        "past its cancel")
    assert died.is_set(), "the caller heard its cancel before config_watch died"


async def test_control_an_uncancelled_serve_once_tears_down_once_dead(
        serving):
    """CONTROL: nothing cancels the caller. ``_serve_once`` returns once
    ``config_watch`` is dead, having torn the connection down exactly once."""
    client, cfg, _dying, died, teardown_calls = serving
    await asyncio.wait_for(client._serve_once(cfg), timeout=_BOUND_S)
    assert died.is_set(), "the connection was torn down before config_watch died"
    assert teardown_calls == [True]
