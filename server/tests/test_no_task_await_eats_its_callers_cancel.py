"""#235: no await of a task under ``astrodeck/`` eats a cancel of its caller.

The shape::

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task

cannot tell the task's cancellation from its caller's. A caller cancelled
while it waits there passes the cancel on to the task it is waiting on, and the
``CancelledError`` that comes back is suppressed with the task's own, so the
caller runs on past its cancel while whoever cancelled it waits. The native
guider's stop had it (``test_stop_guiding_lets_the_cancel_through.py``), and
so did three hub sites, which now wait through ``astrodeck.aio.reap``:

* ``Hub._cancel_warm_locked``, the warm ramp's cancel, whose caller
  ``cancel_warm`` then switches the cooler off;
* ``Hub.start_loop``, which then spawns the replacement live loop;
* ``Hub.stop_loop_and_wait``, whose callers (a sequence start, a single
  capture) then expose.

For each site a caller is cancelled while the task it waits on takes 0.2 s to
die: it must end with ``CancelledError`` within the bound, and the code after
the wait must not run. A guard then scans the whole package for the shape.

Each test names the mutation it was shown RED under, run from a byte-for-byte
backup of ``hub.py`` and restored byte-identical afterwards, with the observed
failure quoted verbatim. The controls stay green under every one.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

import astrodeck
from astrodeck.hub import Hub

_DYING_S = 0.2
_BOUND_S = 2.0


async def _dies_slowly(dying: asyncio.Event, died: asyncio.Event,
                       seconds: float = _DYING_S) -> None:
    """A task parked in device I/O whose teardown takes ``seconds`` once
    cancelled, and does not hurry for a second cancel meanwhile."""
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
    ``(caller, ran_after)``."""
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


async def _reap_quietly(*tasks) -> None:
    for t in tasks:
        if t is not None and not t.done():
            t.cancel()
        if t is not None:
            await asyncio.gather(t, return_exceptions=True)


# ------------------------------------------------------ the warm ramp's cancel


class _Camera:
    connected = True

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def set_cooler(self, on, setpoint_c=None):
        self.calls.append((on, setpoint_c))


@pytest.fixture
async def warm_hub(bus_lines):
    """A hub with a warm ramp running that takes 0.2 s to die, and whose own
    ``finally`` stamps "warm complete" on the way out, as ``_warm_ramp``'s
    does. Yields ``(hub, camera, dying, died)``."""
    h = Hub()
    cam = _Camera()
    h.devices["camera"] = cam
    h._warm_state = {"active": True, "note": "warming"}
    dying, died = asyncio.Event(), asyncio.Event()

    async def _ramp() -> None:
        try:
            await _dies_slowly(dying, died)
        finally:
            h._warm_state["active"] = False
            h._warm_state["note"] = "warm complete"

    task = asyncio.create_task(_ramp())
    h._warm_task = task
    await asyncio.sleep(0)
    yield h, cam, dying, died
    await _reap_quietly(task)


@pytest.mark.asyncio
async def test_a_cancelled_warm_cancel_does_not_run_on_to_the_cooler(warm_hub):
    """``cancel_warm(finalize=True)``'s caller is cancelled while the ramp
    dies: it ends with ``CancelledError`` within the bound, and the cooler
    command after the wait is not sent. The ramp's record still says it was
    stopped, not that it completed.

    MUTANT "restore suppress(CancelledError) around the await" (in
    ``_cancel_warm_locked``) -- RED, observed verbatim:

        AssertionError: the warm ramp's cancel ate its caller's cancel: the
        caller ran on (the code after it ran: True, cooler commands sent:
        [(False, None)])

    MUTANT "stamp the stop only on the normal path" (the state and the log
    line moved out of the ``finally`` to after it) -- RED, observed verbatim:

        AssertionError: a cancelled cancel left the ramp claiming 'warm
        complete'
    """
    h, cam, dying, died = warm_hub
    task, ran_after = await _cancel_mid_wait(
        lambda: h.cancel_warm("the operator stopped it", finalize=True), dying)
    assert task.done(), f"the caller had not ended {_BOUND_S} s after its cancel"
    assert task.cancelled(), (
        f"the warm ramp's cancel ate its caller's cancel: the caller ran on "
        f"(the code after it ran: {ran_after}, cooler commands sent: "
        f"{cam.calls})")
    assert not ran_after and cam.calls == []
    assert died.is_set()
    assert h._warm_state["note"].startswith("stopped:"), (
        f"a cancelled cancel left the ramp claiming "
        f"{h._warm_state['note']!r}")
    assert h._warm_state["active"] is False


@pytest.mark.asyncio
async def test_control_an_uncancelled_warm_cancel_switches_the_cooler_off(
        warm_hub):
    """CONTROL: nothing cancels the caller. ``cancel_warm`` returns True once
    the ramp is dead, switches the cooler off, and the record says stopped."""
    h, cam, _dying, died = warm_hub
    assert await asyncio.wait_for(
        h.cancel_warm("the operator stopped it", finalize=True),
        timeout=_BOUND_S) is True
    assert died.is_set()
    assert cam.calls == [(False, None)]
    assert h._warm_state["note"] == "stopped: the operator stopped it"


# ------------------------------------------------- the live loop's two waits


@pytest.fixture
async def loop_hub(bus_lines, monkeypatch):
    """A hub whose live loop takes 0.2 s to die. ``capture`` is a double that
    never returns, so a replacement loop does nothing but exist. Yields
    ``(hub, old_loop, dying, died)``."""
    h = Hub()

    async def _capture(*a, **k):
        await asyncio.Event().wait()

    monkeypatch.setattr(h, "capture", _capture)
    dying, died = asyncio.Event(), asyncio.Event()
    old = asyncio.create_task(_dies_slowly(dying, died))
    h._loop_task = old
    await asyncio.sleep(0)
    yield h, old, dying, died
    await _reap_quietly(old, h._loop_task)


@pytest.mark.asyncio
async def test_a_cancelled_start_loop_spawns_no_replacement(loop_hub):
    """``start_loop``'s caller is cancelled while the old loop dies: it ends
    with ``CancelledError`` within the bound, and neither the replacement
    loop nor the caller's next line runs.

    MUTANT "restore suppress(CancelledError) around the await" (in
    ``start_loop``) -- RED, observed verbatim:

        AssertionError: start_loop ate its caller's cancel: the caller ran on
        (the code after it ran: True) and a replacement loop was spawned: True
    """
    h, _old, dying, died = loop_hub
    task, ran_after = await _cancel_mid_wait(
        lambda: h.start_loop(0.01, 100, 10), dying)
    assert task.done(), f"the caller had not ended {_BOUND_S} s after its cancel"
    assert task.cancelled(), (
        f"start_loop ate its caller's cancel: the caller ran on (the code "
        f"after it ran: {ran_after}) and a replacement loop was spawned: "
        f"{h._loop_task is not None}")
    assert not ran_after and h._loop_task is None
    assert died.is_set()


@pytest.mark.asyncio
async def test_control_an_uncancelled_start_loop_replaces_the_loop(loop_hub):
    """CONTROL: nothing cancels the caller. ``start_loop`` returns once the
    old loop is dead, with a new loop running in its place."""
    h, old, _dying, died = loop_hub
    await asyncio.wait_for(h.start_loop(0.01, 100, 10), timeout=_BOUND_S)
    assert died.is_set(), "the new loop was spawned before the old one died"
    assert h._loop_task is not None and h._loop_task is not old
    assert not h._loop_task.done()


@pytest.mark.asyncio
async def test_a_cancelled_stop_loop_and_wait_ends_its_caller(loop_hub):
    """``stop_loop_and_wait``'s caller is cancelled while the loop dies: it
    ends with ``CancelledError`` within the bound, and its next line (in a
    sequence start or a capture, the exposure) does not run.

    MUTANT "restore suppress(CancelledError) around the await" (in
    ``stop_loop_and_wait``) -- RED, observed verbatim:

        AssertionError: stop_loop_and_wait ate its caller's cancel: the caller
        ran on (the code after it ran: True)
    """
    h, _old, dying, died = loop_hub
    task, ran_after = await _cancel_mid_wait(h.stop_loop_and_wait, dying)
    assert task.done(), f"the caller had not ended {_BOUND_S} s after its cancel"
    assert task.cancelled(), (
        f"stop_loop_and_wait ate its caller's cancel: the caller ran on (the "
        f"code after it ran: {ran_after})")
    assert not ran_after
    assert died.is_set()


@pytest.mark.asyncio
async def test_control_an_uncancelled_stop_loop_and_wait_returns(loop_hub):
    """CONTROL: nothing cancels the caller. ``stop_loop_and_wait`` returns
    normally once the loop is dead; the loop's own cancel never escapes."""
    h, _old, _dying, died = loop_hub
    await asyncio.wait_for(h.stop_loop_and_wait(), timeout=_BOUND_S)
    assert died.is_set()
    assert h._loop_task is None


# -------------------------------------------------------------- the guard


_PACKAGE = Path(astrodeck.__file__).resolve().parent

#: What the scan counts as suppressing a cancel: ``CancelledError`` named
#: outright, the #235 shape. NOT YET ``BaseException``, which swallows it
#: too: ``suppress(BaseException)`` around an await of a cancelled task
#: stands in three files this change does not own (focus/autofocus.py,
#: remote/relay_client.py, sequence/engine.py), and the same class written
#: as ``try: await task`` / ``except CancelledError: pass`` in more. Both are
#: #252; add "BaseException" here, and a scan of the try form, as they close.
_EATS_CANCEL = {"CancelledError"}


def _name(node) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _suppresses_cancel(item: ast.withitem) -> bool:
    call = item.context_expr
    return (isinstance(call, ast.Call) and _name(call.func) == "suppress"
            and any(_name(a) in _EATS_CANCEL for a in call.args))


def _walk_body(body):
    """Every node in ``body``, not descending into nested definitions (an
    await in a nested function is not awaited under the suppress)."""
    stack = list(body)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.Lambda, ast.ClassDef)):
                stack.append(child)


def _awaits_a_task(node: ast.Await) -> bool:
    """An await of an existing task or future: a bare name, attribute or
    subscript (``await task``, ``await self._task``), or one handed to
    ``wait_for``/``shield`` (``await asyncio.wait_for(task, 5)``). Awaiting
    a fresh call (``await asyncio.sleep(1)``) is not this shape."""
    v = node.value
    if not isinstance(v, ast.Call):
        return True
    return (_name(v.func) in ("wait_for", "shield") and bool(v.args)
            and not isinstance(v.args[0], ast.Call))


def _eaten_cancels(source: str, filename: str) -> list[str]:
    """``file:line`` of every ``with suppress(CancelledError...)`` whose body
    awaits a task."""
    hits = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        if not any(_suppresses_cancel(i) for i in node.items):
            continue
        for inner in _walk_body(node.body):
            if isinstance(inner, ast.Await) and _awaits_a_task(inner):
                hits.append(f"{filename}:{node.lineno}")
                break
    return hits


#: The #235 shape as ``stop_guiding`` had it, and the same with a bare
#: ``CancelledError`` and an attribute: the scan must flag both, so it is
#: shown able to fire.
_KNOWN_POSITIVE = '''
import asyncio
import contextlib
from asyncio import CancelledError
from contextlib import suppress

async def stop(self):
    task = self._loop_task
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task

async def cancel(self):
    self._task.cancel()
    with suppress(CancelledError):
        await asyncio.wait_for(self._task, 5.0)
'''

#: What it must not flag: the fix, a suppress that cannot catch a cancel, and
#: a suppress around a fresh call rather than a task.
_KNOWN_NEGATIVE = '''
import asyncio
import contextlib
from astrodeck.aio import reap

async def stop(self, task):
    task.cancel()
    await reap(task)
    with contextlib.suppress(Exception):
        await task
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.sleep(1.0)
'''


def test_the_scan_fires_on_the_known_shapes_and_only_them():
    """The scan's own known positive and negative. A scan that cannot fire
    would pass the package whatever it held."""
    assert _eaten_cancels(_KNOWN_POSITIVE, "known.py") == [
        "known.py:10", "known.py:15"]
    assert _eaten_cancels(_KNOWN_NEGATIVE, "known.py") == []


def test_no_await_of_a_task_under_astrodeck_suppresses_a_cancel():
    """No ``with suppress(CancelledError...)`` around an await of a task,
    anywhere under ``astrodeck/``. Use ``astrodeck.aio.reap``.

    MUTANT "restore suppress(CancelledError) around the await", at each of
    the four sites in turn -- RED, observed verbatim (the line numbers are
    the tree's at the time):

        AssertionError: these await a task under suppress(CancelledError),
        which eats a cancel of their caller too (#235); wait through
        astrodeck.aio.reap instead: ['guide/native.py:1048']

    and the same with ``['hub.py:4519']`` (``_cancel_warm_locked``),
    ``['hub.py:5257']`` (``start_loop``) and ``['hub.py:5322']``
    (``stop_loop_and_wait``).
    """
    files = [p for p in _PACKAGE.rglob("*.py") if "__pycache__" not in p.parts]
    rel = {p.relative_to(_PACKAGE).as_posix() for p in files}
    # The scan reached the files the fix touched, and the package as a whole.
    assert {"guide/native.py", "hub.py", "aio.py"} <= rel
    assert len(files) > 100, len(files)
    hits: list[str] = []
    for p in files:
        hits += _eaten_cancels(p.read_text(encoding="utf-8"),
                               p.relative_to(_PACKAGE).as_posix())
    assert not hits, (
        f"these await a task under suppress(CancelledError), which eats a "
        f"cancel of their caller too (#235); wait through astrodeck.aio.reap "
        f"instead: {hits}")
