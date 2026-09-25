"""#235: ``NativeGuider.stop_guiding`` lets a cancel aimed at its caller through.

The stop cancels its guide loop and waits for it to die. It used to wait under
``contextlib.suppress(asyncio.CancelledError, Exception)``. A caller cancelled
during that wait passes the cancel on to the loop task (``Task.cancel`` cancels
whatever its task is waiting on), the ``CancelledError`` that comes back is the
loop's and the caller's at once, and the suppress ate both: the caller ran on
past its cancel, into whatever came after the stop (the slow after a
stand-down, a retry loop), while whoever cancelled it waited for it to finish
on its own. ``astrodeck.aio.reap`` now does the wait.

The window is the time the loop takes to die after its cancel, longest with a
guide exposure in flight, so it is intermittent by construction. These tests
force it: the loop here takes 0.2 s to die, whatever else cancels it meanwhile,
and the caller is cancelled inside that 0.2 s.

Each test names the mutation of ``guide/native.py`` it was shown RED under,
run from a byte-for-byte backup and restored byte-identical afterwards, with
the observed failure quoted verbatim. The controls stay green under every one.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import astrodeck.config as configmod
from astrodeck import events
from astrodeck.guide.native import GP_MIN_MEASURED_POINTS, NativeGuider

pytestmark = pytest.mark.asyncio

#: How long the fake loop takes to die once cancelled.
_DYING_S = 0.2
#: How long a cancelled caller may take to end. Generous against the 0.2 s, so
#: a slow Windows runner cannot flake it, and far short of "ran on".
_BOUND_S = 2.0


async def _dies_slowly(dying: asyncio.Event, died: asyncio.Event,
                       seconds: float = _DYING_S) -> None:
    """A guide loop parked in an exposure that takes ``seconds`` to tear down
    once cancelled, and does not hurry for a second cancel meanwhile (a camera
    driver's abort does not get faster because it was asked twice)."""
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


class _Engine:
    """A trained PPEC engine: its window holds enough measured points to be
    saved (#243). Records whether the loop was dead when the stop asked for
    the window, which is when the persist reads it."""

    def __init__(self) -> None:
        self.loop: asyncio.Task | None = None
        self.dumped_with_loop_done: list[bool] = []

    def dump_gp_window(self):
        self.dumped_with_loop_done.append(
            self.loop is None or self.loop.done())
        return [[5.0 * i, 0.1, 0.5, 0.0]
                for i in range(GP_MIN_MEASURED_POINTS + 1)]

    def stats(self):
        return {"guiding": True, "settling": False, "recent": []}


class _Mount:
    name = "fake mount"
    can_pulse_guide = True

    async def pulse_guide(self, direction, ms):
        return None


@pytest.fixture
async def rig(tmp_path, monkeypatch, bus_lines):
    """A guiding native guider for ``prof1`` whose loop dies slowly. Yields
    ``(guider, engine, dying, died, ticks, lines)``; ``ticks`` is every
    ``guide`` publish, ``lines`` every ``bus.log`` line."""
    monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)
    ticks: list[dict] = []
    real_publish = events.bus.publish

    def _publish(type, **data):
        if type == "guide":
            ticks.append(data)
            return None
        return real_publish(type, **data)

    monkeypatch.setattr(events.bus, "publish", _publish)
    g = NativeGuider(None, _Mount(), config={"ra_algorithm": "ppec"},
                     profile_id="prof1")
    eng = _Engine()
    g._engine = eng
    g._active = True
    g._gp_fed_at = 40_000.0
    dying, died = asyncio.Event(), asyncio.Event()
    g._loop_task = asyncio.create_task(_dies_slowly(dying, died))
    eng.loop = g._loop_task
    first_loop = g._loop_task
    await asyncio.sleep(0)              # the loop is parked in its exposure
    yield g, eng, dying, died, ticks, bus_lines
    # A mutant can leave the loop running; never let it outlive the test.
    for t in (first_loop, g._loop_task):
        if t is not None and not t.done():
            t.cancel()
            await asyncio.gather(t, return_exceptions=True)


async def _cancel_mid_stop(g, dying):
    """Run ``stop_guiding`` in a caller, cancel the caller while the loop is
    dying, and wait up to ``_BOUND_S`` for it. Returns ``(caller, ran_after)``;
    ``ran_after`` is True when the code after the stop ran."""
    ran_after: list[bool] = []

    async def caller() -> None:
        await g.stop_guiding()
        ran_after.append(True)

    task = asyncio.create_task(caller())
    await asyncio.wait_for(dying.wait(), timeout=_BOUND_S)
    await asyncio.sleep(0)          # the caller is parked in the stop's wait
    task.cancel()
    await asyncio.wait({task}, timeout=_BOUND_S)
    return task, bool(ran_after)


async def test_a_cancelled_caller_does_not_run_on_past_the_stop(rig):
    """The #235 shape: a caller cancelled while the stop waits for a loop that
    takes 0.2 s to die ends with ``CancelledError`` within the bound, and the
    code after the stop does not run.

    MUTANT "restore suppress(CancelledError) around the await" (``await
    reap(task)`` put back as ``with contextlib.suppress(asyncio.CancelledError,
    Exception): await task``) -- RED, observed verbatim:

        AssertionError: the stop ate its caller's cancel: the caller ran on
        past the stop (the code after it ran: True) and ended normally

    (The next test fails its premise under it too, and the package guard in
    test_no_task_await_eats_its_callers_cancel.py names the line.)
    """
    g, _eng, dying, died, _ticks, _lines = rig
    task, ran_after = await _cancel_mid_stop(g, dying)
    assert task.done(), (
        f"the cancelled caller had not ended {_BOUND_S} s after its cancel")
    assert task.cancelled(), (
        f"the stop ate its caller's cancel: the caller ran on past the stop "
        f"(the code after it ran: {ran_after}) and ended normally")
    assert not ran_after
    # The loop was still waited for: the caller heard its cancel only once
    # the loop was dead, so a teardown cannot land after it.
    assert died.is_set()


async def test_a_cancelled_stop_releases_the_dither_waiter_and_saves_nothing(
        rig, tmp_path):
    """A stop whose caller is cancelled has still stopped guiding: the dither
    waiter is released (a waiter left pending would hang on a guider nothing
    is guiding), the stopped guider is published, and the PPEC model is NOT
    saved, which the stop says in the log once.

    MUTANT "release the dither waiter only on the normal path" (the release
    moved out of the ``finally`` to after it) -- RED, observed verbatim:

        AssertionError: the cancelled stop left the dither waiter pending
        (settle_done set: False, error: None)

    MUTANT "persist on the cancelled path too" (``if loop_dead:`` in the
    ``finally`` made unconditional) -- RED, observed verbatim:

        AssertionError: the cancelled stop saved the PPEC model

    MUTANT "a silent cancelled stop" (the warning on the cancelled path
    removed) -- RED, observed verbatim:

        AssertionError: the cancelled stop did not say it saved nothing: []
    """
    g, _eng, dying, _died, ticks, lines = rig
    task, _ran_after = await _cancel_mid_stop(g, dying)
    assert task.cancelled(), "premise: the caller's cancel got through"
    assert g._settle_done.is_set() and g._settle_error == "guiding stopped", (
        f"the cancelled stop left the dither waiter pending (settle_done set: "
        f"{g._settle_done.is_set()}, error: {g._settle_error})")
    assert not (tmp_path / "guider" / "prof1-gp.json").exists(), (
        "the cancelled stop saved the PPEC model")
    said = [m for lvl, m, _src in lines if "not saved" in m]
    assert len(said) == 1, (
        f"the cancelled stop did not say it saved nothing: {said}")
    assert ticks and ticks[-1]["guiding"] is False, ticks[-1:]
    assert g._gp_fed_at is None


async def test_control_an_uncancelled_stop_returns_persists_and_publishes(
        rig, tmp_path):
    """CONTROL, green under every mutant above: nothing cancels the caller.
    The stop returns normally, once the loop is dead; it saves the PPEC model,
    reading the window only then, stamped with the feed time; it releases the
    dither waiter and publishes the stopped guider.

    MUTANT "persist before waiting for the loop" (the persist moved above the
    wait) -- RED, observed verbatim:

        AssertionError: the stop read the PPEC window while the loop was still
        alive: [False]

    (It turns the test above RED as well: "the cancelled stop saved the PPEC
    model".)
    """
    g, eng, _dying, died, ticks, lines = rig
    await asyncio.wait_for(g.stop_guiding(), timeout=_BOUND_S)
    assert died.is_set(), "the stop returned before its loop was dead"
    assert eng.dumped_with_loop_done == [True], (
        f"the stop read the PPEC window while the loop was still alive: "
        f"{eng.dumped_with_loop_done}")
    saved = json.loads((tmp_path / "guider" / "prof1-gp.json").read_text(
        encoding="utf-8"))
    assert saved["dumped_at"] == 40_000.0
    assert g._settle_done.is_set() and g._settle_error == "guiding stopped"
    assert ticks and ticks[-1]["guiding"] is False
    assert not [m for _lvl, m, _src in lines if "not saved" in m]
    assert g._gp_fed_at is None


async def _raises_on_the_way_out() -> None:
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        raise RuntimeError("the camera driver threw while aborting")


async def _died_of_an_error() -> None:
    raise RuntimeError("the guide camera vanished")


@pytest.mark.parametrize("loop_kind", ["dies_slowly", "raises_on_the_way_out",
                                       "died_of_an_error_before_the_stop"])
async def test_control_cancelling_only_the_loop_never_raises_out_of_the_stop(
        rig, tmp_path, loop_kind):
    """CONTROL: the loop's own end never escapes the stop, whichever way it
    ends: its ``CancelledError`` after a slow teardown, an exception thrown on
    its way out, or an exception it died of before anyone stopped it. Only a
    cancel of the caller is raised, and the stop still saves the model.

    MUTANT "reap awaits the task bare" (``astrodeck/aio.py``: ``await task``
    in place of the gather) -- RED in all three cases, observed verbatim, in
    order:

        asyncio.exceptions.CancelledError
        RuntimeError: the camera driver threw while aborting
        RuntimeError: the guide camera vanished

    (It also turns the uncancelled control above RED with the first, and the
    three hub controls in test_no_task_await_eats_its_callers_cancel.py.)
    """
    g, _eng, _dying, died, _ticks, _lines = rig
    if loop_kind != "dies_slowly":
        g._loop_task.cancel()
        await asyncio.gather(g._loop_task, return_exceptions=True)
        coro = (_raises_on_the_way_out() if loop_kind == "raises_on_the_way_out"
                else _died_of_an_error())
        g._loop_task = asyncio.create_task(coro)
        await asyncio.sleep(0)
        if loop_kind == "died_of_an_error_before_the_stop":
            assert g._loop_task.done(), "premise: the loop is already dead"
    await asyncio.wait_for(g.stop_guiding(), timeout=_BOUND_S)
    assert (tmp_path / "guider" / "prof1-gp.json").exists()
