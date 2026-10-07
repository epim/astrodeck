# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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

#252 found the same class surviving in two spellings the guard above did not
scan: ``with contextlib.suppress(BaseException):`` (``BaseException`` catches
``CancelledError`` too) and ``task.cancel(); try: await task; except
(CancelledError, ...): pass`` written out instead of a ``suppress``. WP-35
converted every site of both spellings outside ``engine.py``, ``app.py`` and
``resume_arm.py`` (``server/tests/test_w4_cancel_safe_awaits.py`` behaviourally
pins two of them) and widened the guard below to scan both. WP-59 converted
the three hot files' remaining sites too (``SequenceEngine.abort``, the
shielded flip-wait ``finally`` in ``SequenceEngine._flip_bounded``,
``_lifespan``, ``_spawn.wrapped``, ``_spawn_connect.wrapped`` and
``ResumeArm.stop`` -- see ``server/tests/test_w13_*cancel_safe*.py``), and
taught the scan to tell apart the one shape that LOOKS like #235/#252 but
isn't (a loop that re-awaits the same shielded future until it is actually
done -- ``_loop_rewait_exempt_line``, below), so ``_ALLOWLIST`` was empty.

#681 found the guard blind in two more places: it never looked at an await of
``asyncio.gather(<tasks>)`` (``SessionReporter.flush`` is written that way), and
its ``try`` spelling named only ``CancelledError``, not ``BaseException``. It
now reads both, and the widened scan found one real swallow that the narrow one
could not see (``Prefetch.settle``, now the one ``_ALLOWLIST`` entry, with its
reason beside it).

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
#: outright (#235's own shape), or ``BaseException``, which catches it too
#: (#252's first widening -- WP-35 converted the two sites that used to make
#: this unsafe, ``focus/autofocus.py`` and ``remote/relay_client.py``; a third,
#: ``sequence/engine.py``, is WP-59's and sits on the allowlist below).
_EATS_CANCEL = {"CancelledError", "BaseException"}


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


#: Calls that only re-wrap a collection of tasks (``*list(self._pending)``,
#: the shape ``SessionReporter.flush`` was written in), so the scan looks
#: through them at the one argument instead of mistaking them for a fresh call.
_COLLECTION_WRAPPERS = {"list", "tuple", "set", "frozenset", "sorted",
                        "reversed"}


def _task_like(arg) -> bool:
    """An argument to ``gather`` that names tasks that already exist: a bare
    name, attribute or subscript (``self._a``, ``tasks[0]``), the same
    behind a star (``*pending``), a collection wrapper around one
    (``*list(self._pending)``), or a comprehension whose ELEMENT is one
    (``*[t for t in self._pending]``). A fresh call (``fresh()``) or a
    comprehension of fresh calls (``*[_night(o) for o in CATALOG]``) is not
    this shape, for the reason a fresh ``sleep()`` is not: nothing there has a
    cancel of its own to be mistaken for the caller's."""
    if isinstance(arg, ast.Starred):
        arg = arg.value
    if isinstance(arg, (ast.Name, ast.Attribute, ast.Subscript)):
        return True
    if isinstance(arg, ast.Call):
        return (_name(arg.func) in _COLLECTION_WRAPPERS and len(arg.args) == 1
                and _task_like(arg.args[0]))
    if isinstance(arg, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return isinstance(arg.elt, (ast.Name, ast.Attribute, ast.Subscript))
    return False


def _awaits_a_task(node: ast.Await) -> bool:
    """An await of an existing task or future: a bare name, attribute or
    subscript (``await task``, ``await self._task``), one handed to
    ``wait_for``/``shield`` (``await asyncio.wait_for(task, 5)``), or several
    handed to ``gather`` (``await asyncio.gather(*pending,
    return_exceptions=True)``, #681; ``fut = gather(...)`` then ``await fut``
    is already the bare-name arm). Awaiting a fresh call (``await
    asyncio.sleep(1)``, ``await asyncio.gather(fresh(), fresh())``) is not
    this shape."""
    v = node.value
    if not isinstance(v, ast.Call):
        return True
    if _name(v.func) == "gather":
        return any(_task_like(a) for a in v.args)
    return (_name(v.func) in ("wait_for", "shield") and bool(v.args)
            and not isinstance(v.args[0], ast.Call))


def _eaten_cancels(source: str, filename: str) -> list[str]:
    """``file:line`` of every ``with suppress(CancelledError/BaseException)``
    whose body awaits a task (#235's ``suppress`` spelling)."""
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


def _handler_catches_cancel(h: ast.ExceptHandler) -> bool:
    """``except CancelledError:`` or ``except (CancelledError, ...):``,
    bare or ``asyncio.``-qualified either way -- and ``BaseException`` in
    either place, which catches it too (the ``suppress`` spelling above already
    counts it; #681 found the ``try`` spelling did not)."""
    t = h.type
    if t is None:          # a bare `except:` catches it too, but is its own,
        return False        # much louder, code smell -- not this scan's job.
    if isinstance(t, ast.Tuple):
        return any(_name(e) in _EATS_CANCEL for e in t.elts)
    return _name(t) in _EATS_CANCEL


def _handler_swallows(h: ast.ExceptHandler) -> bool:
    """True when the handler's body never raises -- #252's ``try`` spelling
    needs this in ADDITION to catching CancelledError, because ``except
    CancelledError: raise`` (plain control-flow passthrough, all over
    engine.py) is not the shape at all. A ``raise`` on only SOME branch
    (resume_arm.py's ladder cancel, which re-raises only when the cancel was
    not this task's own ``stop()``) also counts as "does not swallow": the
    naive scan has no way to know the branch is exhaustive, and a false
    negative there is far cheaper than another allowlist entry for a site
    that already gets this right."""
    return not any(isinstance(n, ast.Raise) for n in _walk_body(h.body))


def _loop_rewait_exempt_line(node: ast.While) -> int | None:
    """``while not X.done(): try: await asyncio.shield(X) except
    CancelledError: ...`` is NOT the eaten-cancel shape, whatever its handler
    does with the exception -- #252's own first comment calls this out by
    name for ``hub.py``'s ``_run_to_its_bound``, and ``SequenceEngine._run``'s
    UNSAFE wind-down loop (engine.py) has the identical shape for the
    identical reason.

    THE DISTINCTION. A one-shot ``try: await task except CancelledError:
    pass`` really does let the caller run on: the ``except`` is the last the
    wait is ever heard from, so whatever follows it runs whether the
    CancelledError was the task's own or the caller's. A loop that keeps
    re-awaiting the SAME future, still shielded, until ``X.done()`` is true
    cannot do that -- a cancel caught inside the ``try`` just ends that one
    iteration, and the very next thing the caller's task does is ``await
    asyncio.shield(X)`` again, not whatever follows the loop. The caller only
    ever gets past the loop once ``X`` is actually finished, which is the same
    postcondition ``astrodeck.aio.reap`` promises. What the two known sites do
    with the fact that a cancel arrived differs (``_run_to_its_bound`` hands
    it back as a return value for ``Hub._teardown`` to read against a
    cancel-count captured before the loop; ``SequenceEngine._run`` sets a
    local flag and raises right after the loop) and is NOT this scan's
    question to answer in general -- answering it would mean tracing
    arbitrary data flow out of the function, which is exactly the kind of
    allowlist-by-another-name this exemption exists to avoid. The loop shape
    alone already establishes the one fact #235/#252 cares about: the caller
    does not run on past its cancel while ``X`` is still alive.

    Returns the inner ``Try``'s line number when ``node`` is exactly this
    shape (a single ``Try`` as the whole loop body, awaiting
    ``asyncio.shield`` of the SAME name the loop's ``.done()`` test reads, with
    at least one handler that catches ``CancelledError``), else None. A false
    negative here (a loop just different enough not to match) falls through to
    the ordinary ``try``/``except`` scan below and is reported like any other
    site -- cheaper than widening this match until it risks exempting a real
    one-shot swallow that merely sits inside some unrelated loop.
    """
    test = node.test
    if not (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)):
        return None
    done_call = test.operand
    if not (isinstance(done_call, ast.Call)
            and isinstance(done_call.func, ast.Attribute)
            and done_call.func.attr == "done"):
        return None
    future = _name(done_call.func.value)
    if (future is None or len(node.body) != 1
            or not isinstance(node.body[0], ast.Try)):
        return None
    try_node = node.body[0]
    shields_same_future = any(
        isinstance(inner, ast.Await) and isinstance(inner.value, ast.Call)
        and _name(inner.value.func) == "shield" and inner.value.args
        and _name(inner.value.args[0]) == future
        for inner in _walk_body(try_node.body))
    if not shields_same_future:
        return None
    if not any(_handler_catches_cancel(h) for h in try_node.handlers):
        return None
    return try_node.lineno


def _eaten_cancels_tryexcept(source: str, filename: str) -> list[str]:
    """``file:line`` of every ``try: await <task> except CancelledError...:
    <no raise>`` -- #252's second spelling (``task.cancel()`` then a plain
    ``try``/``except`` where a ``suppress`` would have been, as the first
    spelling is nothing else) -- except a ``Try`` that is itself a
    ``_loop_rewait_exempt_line`` loop body, which is a different, safe shape
    (see there)."""
    tree = ast.parse(source, filename=filename)
    exempt = {ln for node in ast.walk(tree) if isinstance(node, ast.While)
             for ln in (_loop_rewait_exempt_line(node),) if ln is not None}
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if node.lineno in exempt:
            continue
        body_awaits_a_task = any(
            isinstance(inner, ast.Await) and _awaits_a_task(inner)
            for inner in _walk_body(node.body))
        if not body_awaits_a_task:
            continue
        if any(_handler_catches_cancel(h) and _handler_swallows(h)
              for h in node.handlers):
            hits.append(f"{filename}:{node.lineno}")
    return hits


def _qualname_at(tree: ast.AST, lineno: int) -> str | None:
    """The dotted name of the innermost function containing ``lineno``
    (``Class.method``, or ``outer.inner`` for a nested ``def``), or None at
    module level. Resolves the allowlist below, which is keyed by function
    name rather than line number because the issue's own line numbers (taken
    from the 2026-09-24 tree) had already drifted by the time WP-35 ran."""
    best: str | None = None

    def visit(node: ast.AST, stack: list[str]) -> None:
        nonlocal best
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stack = stack + [node.name]
        start, end = getattr(node, "lineno", None), getattr(node, "end_lineno", None)
        if (start is not None and end is not None and start <= lineno <= end
                and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
            best = ".".join(stack)
        for child in ast.iter_child_nodes(node):
            visit(child, stack)

    visit(tree, [])
    return best


#: Real matches of the shape that are not WP-35's (#252) to fix, kept out of
#: ``hits`` below instead of being silently out of scan scope, so a NEW
#: instance appearing anywhere else still fails loudly. Keyed by (path
#: relative to the package, the enclosing function's qualified name).
#:
#: ONE ENTRY (WP-92, #681): ``Prefetch.settle`` in ``focus/pipeline.py``, which
#: widening the scan to the ``BaseException`` spelling of the handler turned up.
#: It is ``try: return await self.task / except BaseException: return None``, a
#: real swallow, not a false positive: a caller cancelled while it waits in
#: ``settle`` has its cancel delivered to the speculative exposure it awaits,
#: and the ``CancelledError`` that comes back is eaten with the task's own, so
#: the focus sweep runs on past a halt the operator asked for. (Shown by a
#: scratch run: a caller cancelled mid-``settle`` while the exposure took 0.2 s
#: to die ended un-cancelled and ran its next line.) WP-92 is test-only by
#: ruling, so the site is not converted here; it is reported as a separate
#: defect. The fix is to wait through ``astrodeck.aio.reap`` (or ``gather(...,
#: return_exceptions=True)``) and read the task's result afterwards; delete
#: this entry then (the stale-entry check below fails until it is).
#:
#: Before that it was EMPTY (WP-59), and had held two kinds of entry:
#:
#: * The three "hot" files WP-35's plan explicitly deferred (``engine.py``,
#:   ``app.py``, ``resume_arm.py``) -- ``SequenceEngine.abort``, the shielded
#:   flip-wait ``finally`` in ``SequenceEngine._flip_bounded``, ``_lifespan``,
#:   ``_spawn.wrapped``, ``_spawn_connect.wrapped`` and ``ResumeArm.stop``.
#:   WP-59 converted all six to ``astrodeck.aio.reap`` (or, for the two
#:   ``wrapped`` closures, a bare ``raise`` after the log -- they are a
#:   task's OWN top level catching its OWN cancellation, not a reap of some
#:   other task, so there is nothing to hand ``reap`` there; the fix is the
#:   same one ``resume_arm.py``'s ``_run`` already used).
#: * ``hub.py``'s ``_run_to_its_bound`` and ``SequenceEngine._run``'s own
#:   wind-down loop at the UNSAFE teardown are not bugs at all: each
#:   re-awaits the SAME shielded task in a loop until it is done, so a
#:   cancel caught inside the ``try`` never lets the caller past it -- the
#:   next thing the caller's task does is ``await`` the same shielded future
#:   again, not whatever follows the loop (#252's first comment calls this
#:   out by name for ``_run_to_its_bound`` and recommends either teaching the
#:   scan the shape or allowlisting it). ``_loop_rewait_exempt_line`` above
#:   now recognises the shape directly, so neither needs an entry here.
#:   ``test_teardown_cancel_cleanup.py::test_control_a_cancel_during_the_
#:   disconnects_waits_for_them`` separately pins that ``_run_to_its_bound``'s
#:   cancel does get out to ``Hub._teardown``'s caller.
#: * ``weather.py``'s ``WeatherService.stop`` turned up widening this scan
#:   for #252 and was reported as its own defect (#628); WP-37 (f) fixed it
#:   by switching to ``aio.reap``, so its entry here is removed rather than
#:   kept -- it no longer matches anything, and the stale-entry check below
#:   would fail if it stayed.
_ALLOWLIST: set[tuple[str, str]] = {
    ("focus/pipeline.py", "Prefetch.settle"),
}


#: The #235 shape as ``stop_guiding`` had it, the same with a bare
#: ``CancelledError`` and an attribute, and ``BaseException`` instead
#: (#252's first widening): the scan must flag all three, so it is shown
#: able to fire.
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

async def teardown(self):
    self._task.cancel()
    with contextlib.suppress(BaseException):
        await self._task

async def teardown_many(self):
    with suppress(CancelledError):
        await asyncio.gather(self._a, self._b)
'''

#: #252's second spelling: the same two shapes written as a plain
#: ``try``/``except`` where a ``suppress`` would have been (``pass``), and
#: one that re-raises -- which is NOT this shape and must not be flagged.
#: ``near_miss_loop`` guards WP-59's ``_loop_rewait_exempt_line`` against
#: over-matching: it LOOKS like the safe re-await-until-done loop, but shields
#: a DIFFERENT future (``other``) than the one its ``.done()`` test reads
#: (``step``) -- a cut-and-paste of the safe shape onto the wrong variable
#: would still be a genuine one-shot swallow of whatever ``other`` is, and
#: must still be flagged.
_KNOWN_POSITIVE_TRYEXCEPT = '''
import asyncio

async def stop(self):
    self._task.cancel()
    try:
        await self._task
    except (asyncio.CancelledError, Exception):
        pass

async def cancel(self):
    self._task.cancel()
    try:
        await self._task
    except asyncio.CancelledError:
        pass

async def propagates(self):
    self._task.cancel()
    try:
        await self._task
    except asyncio.CancelledError:
        raise

async def near_miss_loop(self, step, other):
    while not step.done():
        try:
            await asyncio.shield(other)
        except asyncio.CancelledError:
            pass

async def flush(self):
    pending = list(self._pending)
    try:
        await asyncio.gather(*pending, return_exceptions=True)
    except BaseException:
        pass

async def flush_inline(self):
    try:
        await asyncio.gather(*list(self._pending), return_exceptions=True)
    except BaseException:
        pass

async def stop_base(self):
    self._task.cancel()
    try:
        await self._task
    except BaseException:
        pass

async def stop_tuple_base(self):
    self._task.cancel()
    try:
        await self._task
    except (OSError, BaseException):
        pass

async def flush_filtered(self):
    try:
        await asyncio.gather(*[t for t in self._pending if not t.done()])
    except BaseException:
        pass
'''

#: What it must not flag: the fix, a suppress/except that cannot catch a
#: cancel, a suppress around a fresh call rather than a task, a try/except
#: that re-raises on some branch (resume_arm.py's ladder cancel shape: it only
#: sometimes propagates, by design, and the scan must trust a ``raise``
#: anywhere in the handler rather than guess which branch runs), and
#: ``rewaits_until_done`` -- ``hub.py``'s ``_run_to_its_bound`` /
#: ``SequenceEngine._run``'s UNSAFE wind-down shape (WP-59): a loop that
#: re-awaits the SAME shielded future until it is done.
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
    try:
        await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        pass

async def sometimes_propagates(self, condition):
    try:
        result = await self._task
    except asyncio.CancelledError:
        if condition:
            raise
        result = None
    return result

async def rewaits_until_done(self, step):
    while not step.done():
        try:
            await asyncio.shield(step)
        except asyncio.CancelledError:
            pass
    return step.exception()

async def flush(self):
    pending = list(self._pending)
    await asyncio.gather(*pending, return_exceptions=True)

async def base_exception_propagates(self):
    try:
        await self._task
    except BaseException:
        raise

async def gather_of_fresh_calls(self):
    try:
        await asyncio.gather(fresh(), fresh())
    except BaseException:
        pass
    with contextlib.suppress(BaseException):
        await asyncio.gather(fresh(), fresh())

async def gather_of_a_comprehension_of_fresh_calls(self, items):
    try:
        await asyncio.gather(*[work(i) for i in items])
    except BaseException:
        pass
'''


def _by_line(hits: list[str]) -> list[str]:
    """``hits`` ordered by line number (their ``file:line`` strings do not
    sort numerically as text)."""
    return sorted(hits, key=lambda h: int(h.rsplit(":", 1)[1]))


def test_the_scan_fires_on_the_known_shapes_and_only_them():
    """The scan's own known positives and negative. A scan that cannot fire
    would pass the package whatever it held.

    #681: the positives now include a swallow around ``asyncio.gather`` of
    existing tasks (under ``suppress`` and under ``try``, in the three spellings
    the issue's ``flush`` can take), and an ``except BaseException`` (alone and
    in a tuple); the negatives include the real ``flush`` (a plain gather, no
    ``try``), a ``BaseException`` handler that re-raises, and a swallowed gather
    of FRESH calls (the ``wait_for``/``shield`` negative's twin).

    MUTANT "the gather arm of ``_awaits_a_task`` returns False" (the scan is
    back to not seeing ``gather``) -- RED, observed verbatim:

        AssertionError: the suppress scan's known positives: ['known.py:10',
        'known.py:15', 'known.py:20'], expected ['known.py:10', 'known.py:15',
        'known.py:20', 'known.py:24']

    MUTANT "``_handler_catches_cancel`` back to ``_name(t) ==
    "CancelledError"``" (the ``try`` spelling stops reading ``BaseException``)
    -- RED, observed verbatim:

        AssertionError: the try/except scan's known positives:
        ['known.py:6', 'known.py:13', 'known.py:27'], expected ['known.py:6',
        'known.py:13', 'known.py:27', 'known.py:34', 'known.py:40',
        'known.py:47', 'known.py:54', 'known.py:60']

    The same message, minus 'known.py:60' (a comprehension whose element is a
    name), under "``_task_like`` calls a comprehension not task-like", and
    minus 'known.py:40' (``*list(self._pending)``) under "``_task_like`` looks
    through no collection wrapper". The two controls run the other way: "a
    comprehension of fresh calls counts as tasks" and "a fresh call counts as
    a task" each flag one of the negatives, and are RED, observed verbatim, as

        AssertionError: assert ['known.py:54'] == []

    and ``['known.py:50'] == []``.
    """
    # The line numbers are counted off the fixture text above. The tryexcept
    # list is sorted by line because ``ast.walk`` is breadth first, so the
    # near-miss loop's nested ``try`` comes out after the later top-level ones.
    suppressed = _by_line(_eaten_cancels(_KNOWN_POSITIVE, "known.py"))
    expected = ["known.py:10", "known.py:15", "known.py:20", "known.py:24"]
    assert suppressed == expected, (
        f"the suppress scan's known positives: {suppressed}, expected "
        f"{expected}")
    tried = _by_line(
        _eaten_cancels_tryexcept(_KNOWN_POSITIVE_TRYEXCEPT, "known.py"))
    expected = ["known.py:6", "known.py:13", "known.py:27", "known.py:34",
                "known.py:40", "known.py:47", "known.py:54", "known.py:60"]
    assert tried == expected, (
        f"the try/except scan's known positives: {tried}, expected {expected}")
    assert _eaten_cancels(_KNOWN_NEGATIVE, "known.py") == []
    assert _eaten_cancels_tryexcept(_KNOWN_NEGATIVE, "known.py") == []


def test_no_await_of_a_task_under_astrodeck_suppresses_a_cancel():
    """No ``with suppress(CancelledError/BaseException)`` around an await of
    a task, and no ``try: await <task> except CancelledError...: <no
    raise>`` either (#252's two spellings added to #235's own), anywhere
    under ``astrodeck/`` that is not on ``_ALLOWLIST`` above. Use
    ``astrodeck.aio.reap``.

    MUTANT "restore suppress(CancelledError) around the await", at each of
    the four #235 sites in turn -- RED, observed verbatim (the line numbers
    are the tree's at the time):

        AssertionError: these await a task under suppress(CancelledError) or
        a try/except that swallows it (#235, widened by #252), which eats a
        cancel of their caller too; wait through astrodeck.aio.reap instead:
        ['guide/native.py:1048']

    and the same with ``['hub.py:4519']`` (``_cancel_warm_locked``),
    ``['hub.py:5257']`` (``start_loop``) and ``['hub.py:5322']``
    (``stop_loop_and_wait``).

    MUTANT "restore try/except around the await" in ``dusk_arm.py``'s
    ``stop`` (WP-35, #252) -- RED, observed verbatim:

        AssertionError: these await a task under suppress(CancelledError) or
        a try/except that swallows it (#235, widened by #252), which eats a
        cancel of their caller too; wait through astrodeck.aio.reap instead:
        ['dusk_arm.py:62']

    MUTANT "restore suppress(BaseException) around the await" in
    ``remote/relay_client.py``'s ``_serve_once`` (WP-35, #252) -- RED,
    observed verbatim:

        AssertionError: these await a task under suppress(CancelledError) or
        a try/except that swallows it (#235, widened by #252), which eats a
        cancel of their caller too; wait through astrodeck.aio.reap instead:
        ['remote/relay_client.py:886']

    WP-59: the three "hot" files' six sites (``SequenceEngine.abort``, the
    shielded flip-wait ``finally`` in ``SequenceEngine._flip_bounded``,
    ``_lifespan``, ``_spawn.wrapped``, ``_spawn_connect.wrapped`` and
    ``ResumeArm.stop``) are converted and exercised by their own dedicated
    tests (``server/tests/test_w13_*cancel_safe*.py``), each with its own
    named mutant restoring the pre-WP-59 shape at that one site -- not
    repeated here, since a revert at any of the six is already a known
    positive for THIS scan (every site above is simultaneously a
    ``_KNOWN_POSITIVE``/``_KNOWN_POSITIVE_TRYEXCEPT`` shape).

    MUTANT "neutralize ``_loop_rewait_exempt_line`` to always return None"
    (WP-59) -- RED, observed verbatim (both real sites the exemption was
    added for, now unmasked):

        AssertionError: these await a task under suppress(CancelledError) or
        a try/except that swallows it (#235, widened by #252), which eats a
        cancel of their caller too; wait through astrodeck.aio.reap instead:
        ['hub.py:735', 'sequence/engine.py:3064']

    MUTANT "``SessionReporter.flush`` swallows the cancel around its gather"
    (#681; ``sequence/report.py``'s ``await asyncio.gather(*running,
    return_exceptions=True)`` wrapped in ``try: ... except BaseException:
    pass``) -- RED, observed verbatim (it was GREEN before #681 widened the
    scan, because the gather was never looked at and the handler was never
    read; the real flush, with no ``try``, is a negative and stays so):

        AssertionError: these await a task under suppress(CancelledError) or
        a try/except that swallows it (#235, widened by #252), which eats a
        cancel of their caller too; wait through astrodeck.aio.reap instead:
        ['sequence/report.py:848']
    """
    files = [p for p in _PACKAGE.rglob("*.py") if "__pycache__" not in p.parts]
    rel = {p.relative_to(_PACKAGE).as_posix() for p in files}
    # The scan reached the files the fix touched, and the package as a whole.
    assert {"guide/native.py", "hub.py", "aio.py"} <= rel
    assert len(files) > 100, len(files)
    hits: list[str] = []
    allowed_seen: set[tuple[str, str]] = set()
    for p in files:
        relpath = p.relative_to(_PACKAGE).as_posix()
        source = p.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relpath)
        found = (_eaten_cancels(source, relpath)
                + _eaten_cancels_tryexcept(source, relpath))
        for hit in found:
            lineno = int(hit.rsplit(":", 1)[1])
            key = (relpath, _qualname_at(tree, lineno))
            if key in _ALLOWLIST:
                allowed_seen.add(key)
                continue
            hits.append(hit)
    assert not hits, (
        f"these await a task under suppress(CancelledError) or a try/except "
        f"that swallows it (#235, widened by #252), which eats a cancel of "
        f"their caller too; wait through astrodeck.aio.reap instead: {hits}")
    # A stale allowlist entry (its site converted, or moved/renamed without a
    # matching update here) would hide a REAL gap in the scan's coverage
    # rather than a known deferral -- so every entry must still be live.
    stale = _ALLOWLIST - allowed_seen
    assert not stale, f"these allowlist entries no longer match anything (fixed already, or renamed?): {stale}"
