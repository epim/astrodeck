# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#267: a cancelled rig teardown still turns the cooler off and disconnects.

``Hub._teardown`` stops a warm ramp with ``cancel_warm(finalize=True)``, whose
cooler-off is the teardown's promised end state: the devices are about to be
disconnected, and a camera left on a mid-ramp setpoint holds a temperature
nobody chose with nothing talking to it. Since #235 the ramp's reap lets a
cancel of its caller through, as it must, so a teardown cancelled while the
ramp dies ended right there: no cooler-off, no polar stop, no disconnect. The
TEC stayed on at the dead ramp's last setpoint and every device stayed
connected. The class is "safety rides value paths": the end state was reached
only on the path where nothing interrupts the work.

The fix makes the rest of the teardown its cleanup. It runs in a ``finally``,
each command on its own task behind ``asyncio.shield`` and cut at its own
bound, and the cancel is raised again once it is done. The cooler-off is owed
only when a ramp was running, so the uncancelled teardown behaves as before.

Every test names a mutation of ``hub.py`` it was shown RED under, run in a
private scratch copy of ``server/`` (never the shared tree, #254), with the
observed failure quoted verbatim. Every mutant was run against the whole
file, and none turned red a test whose behaviour it leaves alone: the
uncancelled controls stay green under each mutant of the cancelled path.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from astrodeck import cooling
from astrodeck import hub as hub_mod
from astrodeck.hub import Hub

#: How long the warm ramp takes to die once cancelled, the same 0.2 s as
#: test_no_task_await_eats_its_callers_cancel: long enough that a cancel of
#: the teardown reliably lands while ``cancel_warm`` sits in its reap.
_DYING_S = 0.2
#: How long a test waits for the teardown to end. Every bound under test is
#: set well inside it.
_BOUND_S = 3.0
#: A short bound for the commands that hang, so a cut is quick to watch.
_CUT_S = 0.3
#: How long a slow command takes to finish on its own, inside its bound.
_SLOW_S = 0.3
#: Windows' clock ticks at 15.625 ms and ``asyncio.sleep`` can return early by
#: up to one tick, so an elapsed time is compared with this much slack.
_TICK_SLACK_S = 0.05

_ROLES = ("camera", "mount", "focuser", "filterwheel")


class _Device:
    """A device double that writes every call into the rig's shared event
    list, so the tests can read the ORDER as well as the calls."""

    def __init__(self, role: str, events: list, release: asyncio.Event) -> None:
        self.role = role
        self.connected = True
        self.events = events
        self.release = release
        self.disconnect_hangs = False
        self.disconnect_s = 0.0
        self.disconnect_started = asyncio.Event()

    async def disconnect(self) -> None:
        self.events.append(("disconnect", self.role))
        self.disconnect_started.set()
        if self.disconnect_hangs:
            await self.release.wait()
        if self.disconnect_s:
            await asyncio.sleep(self.disconnect_s)
        self.events.append(("disconnected", self.role))
        self.connected = False


class _Camera(_Device):
    """A coolable camera. ``("cooler", on, setpoint)`` is written when a
    command is SENT, ``("cooler done", ...)`` when it returns."""

    def __init__(self, events: list, release: asyncio.Event) -> None:
        super().__init__("camera", events, release)
        self.can_cool = True
        self.cooler_hangs = False
        self.cooler_s = 0.0
        self.cooler_sent = asyncio.Event()

    async def set_cooler(self, on, setpoint_c=None) -> None:
        self.events.append(("cooler", on, setpoint_c))
        self.cooler_sent.set()
        if self.cooler_hangs:
            await self.release.wait()
        if self.cooler_s:
            await asyncio.sleep(self.cooler_s)
        self.events.append(("cooler done", on, setpoint_c))


@pytest.fixture
async def rig(bus_lines):
    """A hub holding four device doubles. ``rig.start_ramp()`` starts a warm
    ramp that takes ``_DYING_S`` to die once cancelled and writes
    ``("ramp died",)`` on its way out. ``rig.teardown`` is set by the test to
    the teardown task it starts, so it is reaped however the test ends."""
    h = Hub()
    events: list = []
    release = asyncio.Event()
    devices = {}
    for role in _ROLES:
        dev = (_Camera(events, release) if role == "camera"
               else _Device(role, events, release))
        devices[role] = dev
        h.devices[role] = dev
    dying = asyncio.Event()
    ns = SimpleNamespace(hub=h, events=events, devices=devices, dying=dying,
                         ramp=None, teardown=None, logs=bus_lines)

    async def _ramp() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            dying.set()
            loop = asyncio.get_running_loop()
            end = loop.time() + _DYING_S
            while (left := end - loop.time()) > 0:
                try:
                    await asyncio.sleep(left)
                except asyncio.CancelledError:
                    pass
            raise
        finally:
            events.append(("ramp died",))
            if h._warm_state is not None:
                h._warm_state["active"] = False
                h._warm_state["note"] = "warm complete"

    async def start_ramp() -> None:
        h._warm_state = {"active": True, "note": "warming"}
        ns.ramp = asyncio.create_task(_ramp())
        h._warm_task = ns.ramp
        await asyncio.sleep(0)

    ns.start_ramp = start_ramp
    yield ns
    # Whatever a mutant left hanging is released, so the fixture never hangs
    # the suite behind a teardown that was never going to end.
    release.set()
    for t in (ns.teardown, ns.ramp):
        if t is not None and not t.done():
            t.cancel()
        if t is not None:
            await asyncio.wait({t}, timeout=_BOUND_S)


def _start_teardown(rig) -> asyncio.Task:
    rig.teardown = asyncio.create_task(rig.hub._teardown())
    return rig.teardown


async def _cancel_in_the_reap(rig) -> asyncio.Task:
    """Start the teardown and cancel it while ``cancel_warm`` waits out the
    ramp's death in its reap. Returns the teardown task, once it has ended
    or ``_BOUND_S`` has passed."""
    task = _start_teardown(rig)
    await asyncio.wait_for(rig.dying.wait(), timeout=_BOUND_S)
    await asyncio.sleep(0)
    assert not task.done(), "the teardown ended before the ramp died"
    task.cancel()
    await asyncio.wait({task}, timeout=_BOUND_S)
    return task


def _disconnected(rig) -> list[str]:
    return [e[1] for e in rig.events if e[0] == "disconnect"]


# ------------------------------------------------------------ the defect


@pytest.mark.asyncio
async def test_a_teardown_cancelled_in_the_warm_reap_still_cleans_up(rig):
    """The teardown is cancelled while ``cancel_warm(finalize=True)`` sits in
    its reap. The cooler-off is still sent, after the ramp has died and before
    the camera is disconnected; every device's disconnect is called; the hub
    no longer claims the devices; and ``CancelledError`` still propagates.

    MUTANT "cleanup outside the finally" (the cleanup moved out of the
    ``finally`` into an ``if True:`` block after the ``try``) -- RED,
    observed verbatim:

        AssertionError: a cancelled teardown sent no cooler-off, so the TEC
        stays at the dead ramp's last setpoint; events: [('ramp died',)]

    MUTANT "the cooler-off never owed" (``cooler_owed = False``): the
    disconnects now run, and the cooler-off still does not -- RED, observed
    verbatim:

        AssertionError: a cancelled teardown sent no cooler-off, so the TEC
        stays at the dead ramp's last setpoint; events: [('ramp died',),
        ('disconnect', 'camera'), ('disconnected', 'camera'), ('disconnect',
        'mount'), ('disconnected', 'mount'), ('disconnect', 'focuser'),
        ('disconnected', 'focuser'), ('disconnect', 'filterwheel'),
        ('disconnected', 'filterwheel')]

    MUTANT "the teardown eats its cancel" (``except BaseException`` around
    ``cancel_warm``, and the ``raise`` after the ``finally`` replaced by
    ``pass``): the cleanup runs in full and the cancel is gone -- RED,
    observed verbatim:

        AssertionError: the teardown's cleanup ate its caller's cancel (#235)
    """
    await rig.start_ramp()
    task = await _cancel_in_the_reap(rig)
    assert task.done(), f"the teardown had not ended {_BOUND_S} s after its cancel"
    assert ("cooler", False, None) in rig.events, (
        f"a cancelled teardown sent no cooler-off, so the TEC stays at the "
        f"dead ramp's last setpoint; events: {rig.events}")
    assert sorted(_disconnected(rig)) == sorted(_ROLES), (
        f"a cancelled teardown left devices connected; disconnected: "
        f"{_disconnected(rig)}")
    assert task.cancelled(), (
        "the teardown's cleanup ate its caller's cancel (#235)")
    ev = rig.events
    assert ev.index(("ramp died",)) < ev.index(("cooler", False, None)) < (
        ev.index(("disconnect", "camera"))), (
        f"the cooler-off must follow the ramp's death (no step can land after "
        f"it) and precede the camera's disconnect; events: {ev}")
    assert ev.count(("cooler", False, None)) == 1, ev
    assert rig.hub.devices == {} and rig.hub.mode == "none"
    assert rig.hub._warm_task is None
    assert rig.hub._warm_state["note"] == "stopped: the rig is disconnecting"


# -------------------------------------------------------------- controls


@pytest.mark.asyncio
async def test_control_an_uncancelled_teardown_behaves_as_before(rig):
    """CONTROL: nothing cancels the teardown. It returns normally, with the
    one cooler-off ``cancel_warm`` sends and the disconnects in the same
    order as before this change, and says so once.

    MUTANT "the cooler-off owed whatever cancel_warm did" (the
    ``cooler_owed = False`` after ``cancel_warm`` returns replaced by
    ``pass``) -- RED, observed verbatim:

        AssertionError: [('ramp died',), ('cooler', False, None), ('cooler
        done', False, None), ('cooler', False, None), ('cooler done', False,
        None), ('disconnect', 'camera'), ...]
        assert [('ramp died'...camera'), ...] == [('ramp died'...'mount'), ...]
          At index 3 diff: ('cooler', False, None) != ('disconnect', 'camera')
    """
    await rig.start_ramp()
    await asyncio.wait_for(_start_teardown(rig), timeout=_BOUND_S)
    assert rig.events == [
        ("ramp died",),
        ("cooler", False, None), ("cooler done", False, None),
        ("disconnect", "camera"), ("disconnected", "camera"),
        ("disconnect", "mount"), ("disconnected", "mount"),
        ("disconnect", "focuser"), ("disconnected", "focuser"),
        ("disconnect", "filterwheel"), ("disconnected", "filterwheel"),
    ], rig.events
    assert rig.hub.devices == {} and rig.hub.mode == "none"
    said = [m for (_lvl, m, _src) in rig.logs if m == "all equipment disconnected"]
    assert said == ["all equipment disconnected"]
    assert not [l for l in rig.logs if l[0] in ("warning", "error")], rig.logs


@pytest.mark.asyncio
async def test_control_no_ramp_means_no_cooler_command(rig):
    """CONTROL: with no warm ramp running the teardown owes the cooler
    nothing. A camera that was cooling for a run keeps its TEC as before;
    only the disconnects are sent.

    MUTANT "the cleanup's cooler-off unconditional" (``if True:`` in place
    of ``if cooler_owed:``) -- RED, observed verbatim:

        AssertionError: [('cooler', False, None), ('cooler done', False,
        None), ('disconnect', 'camera'), ('disconnected', 'camera'),
        ('disconnect', 'mount'), ('disconnected', 'mount'), ...]
        assert not [('cooler', False, None), ('cooler done', False, None)]
    """
    await asyncio.wait_for(_start_teardown(rig), timeout=_BOUND_S)
    assert not [e for e in rig.events if e[0].startswith("cooler")], rig.events
    assert _disconnected(rig) == list(_ROLES)


@pytest.mark.asyncio
async def test_control_a_cancelled_teardown_with_no_ramp_invents_no_cooler_off(
        rig):
    """CONTROL: no ramp, and the teardown is cancelled before its cleanup,
    while ``cancel_warm`` waits for a warm lock somebody else holds. The
    cleanup disconnects everything and sends no cooler command: the debt is
    a stopped ramp's, not every cancelled teardown's.

    MUTANT "the cooler-off owed whether or not a ramp runs"
    (``cooler_owed = True``) -- RED, observed verbatim:

        AssertionError: a cancelled teardown with no ramp to stop sent a
        cooler command; events: [('cooler', False, None), ('cooler done',
        False, None), ('disconnect', 'camera'), ('disconnected', 'camera'),
        ('disconnect', 'mount'), ('disconnected', 'mount'), ('disconnect',
        'focuser'), ('disconnected', 'focuser'), ('disconnect',
        'filterwheel'), ('disconnected', 'filterwheel')]
    """
    h = rig.hub
    await h._warm_lock.acquire()
    try:
        task = _start_teardown(rig)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # Parked on the lock: not ended, and nothing of the cleanup sent yet.
        assert not task.done() and rig.events == [], rig.events
        task.cancel()
        await asyncio.wait({task}, timeout=_BOUND_S)
    finally:
        h._warm_lock.release()
    assert task.done(), f"the teardown had not ended {_BOUND_S} s after its cancel"
    assert not [e for e in rig.events if e[0].startswith("cooler")], (
        f"a cancelled teardown with no ramp to stop sent a cooler command; "
        f"events: {rig.events}")
    assert _disconnected(rig) == list(_ROLES), rig.events
    assert task.cancelled()


@pytest.mark.asyncio
async def test_control_a_hanging_cooler_off_is_cut_and_the_disconnects_run(
        rig, monkeypatch):
    """CONTROL: the teardown is cancelled in the reap and the cleanup's
    cooler-off never answers. It is cut at its bound (no sooner), said in a
    warning, every device is still disconnected, and the cancel propagates.

    MUTANT "the cleanup's cooler-off unbounded" (``_teardown_cooler_off``
    passes ``None`` as the bound) -- RED, observed verbatim:

        AssertionError: a hanging cooler-off held the teardown past 3.0 s;
        events: [('ramp died',), ('cooler', False, None)]
    """
    monkeypatch.setattr(cooling, "WARM_CMD_TIMEOUT_S", _CUT_S)
    rig.devices["camera"].cooler_hangs = True
    await rig.start_ramp()
    loop = asyncio.get_running_loop()
    task = _start_teardown(rig)
    await asyncio.wait_for(rig.dying.wait(), timeout=_BOUND_S)
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait_for(rig.devices["camera"].cooler_sent.wait(),
                           timeout=_BOUND_S)
    sent_at = loop.time()
    await asyncio.wait({task}, timeout=_BOUND_S)
    assert task.done(), (
        f"a hanging cooler-off held the teardown past {_BOUND_S} s; events: "
        f"{rig.events}")
    assert task.cancelled()
    cut_at = loop.time()
    assert cut_at - sent_at >= _CUT_S - _TICK_SLACK_S, (
        f"the cooler-off was abandoned after {cut_at - sent_at:.3f} s, not "
        f"cut at its {_CUT_S} s bound")
    assert ("cooler done", False, None) not in rig.events
    assert sorted(_disconnected(rig)) == sorted(_ROLES), rig.events
    warned = [m for (lvl, m, _s) in rig.logs
              if lvl == "warning" and "cooler" in m]
    assert warned, rig.logs


@pytest.mark.asyncio
async def test_control_the_cleanup_sends_no_cooler_off_to_a_dropped_camera(rig):
    """CONTROL: the teardown is cancelled in the reap, but the camera has
    already dropped (``connected`` is False). The cleanup's cooler-off keeps
    ``cancel_warm``'s own guard, so nothing is sent into a dead link, where
    it could only wait out its bound; every device is still disconnected and
    the cancel propagates.

    MUTANT "the cleanup's cooler-off unguarded" (``if cam is None:`` in place
    of the connected check in ``_teardown_cooler_off``) -- RED, observed
    verbatim:

        AssertionError: the cleanup sent a cooler command to a camera that
        had dropped; events: [('ramp died',), ('cooler', False, None),
        ('cooler done', False, None), ('disconnect', 'camera'),
        ('disconnected', 'camera'), ('disconnect', 'mount'), ('disconnected',
        'mount'), ('disconnect', 'focuser'), ('disconnected', 'focuser'),
        ('disconnect', 'filterwheel'), ('disconnected', 'filterwheel')]
    """
    rig.devices["camera"].connected = False
    await rig.start_ramp()
    task = await _cancel_in_the_reap(rig)
    assert task.done(), f"the teardown had not ended {_BOUND_S} s after its cancel"
    assert not [e for e in rig.events if e[0].startswith("cooler")], (
        f"the cleanup sent a cooler command to a camera that had dropped; "
        f"events: {rig.events}")
    assert sorted(_disconnected(rig)) == sorted(_ROLES), rig.events
    assert task.cancelled()


@pytest.mark.asyncio
async def test_control_a_second_cancel_does_not_cut_the_cleanup(rig):
    """CONTROL: a second cancel lands while the cleanup's cooler-off is on the
    wire. The shield keeps it: the command finishes, every device is still
    disconnected after it, and the teardown ends cancelled.

    MUTANT "no shield" (``await step`` in place of ``await
    asyncio.shield(step)`` in ``_run_to_its_bound``) -- RED, observed
    verbatim:

        AssertionError: a second cancel cut the cooler-off mid-command;
        events: [('ramp died',), ('cooler', False, None), ('disconnect',
        'camera'), ('disconnected', 'camera'), ('disconnect', 'mount'),
        ('disconnected', 'mount'), ('disconnect', 'focuser'),
        ('disconnected', 'focuser'), ('disconnect', 'filterwheel'),
        ('disconnected', 'filterwheel')]
    """
    cam = rig.devices["camera"]
    cam.cooler_s = _SLOW_S
    await rig.start_ramp()
    task = _start_teardown(rig)
    await asyncio.wait_for(rig.dying.wait(), timeout=_BOUND_S)
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait_for(cam.cooler_sent.wait(), timeout=_BOUND_S)
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait({task}, timeout=_BOUND_S)
    assert task.done(), f"the teardown had not ended {_BOUND_S} s after its cancels"
    assert ("cooler done", False, None) in rig.events, (
        f"a second cancel cut the cooler-off mid-command; events: {rig.events}")
    ev = rig.events
    assert ev.index(("cooler done", False, None)) < ev.index(
        ("disconnect", "camera")), ev
    assert sorted(_disconnected(rig)) == sorted(_ROLES), ev
    assert task.cancelled()


@pytest.mark.asyncio
async def test_control_a_cancel_during_the_disconnects_waits_for_them(rig):
    """CONTROL: no ramp, and the cancel lands while the mount is
    disconnecting, on the path that was never cancelled before the cleanup.
    That disconnect finishes, the rest still run, the hub lets go of every
    device, and only then does the cancel propagate: it is not eaten (#235).

    MUTANT "the cancel that landed in the cleanup is dropped" (the ``raise``
    after the ``finally`` replaced by ``pass``) -- RED, observed verbatim:

        AssertionError: a cancel that landed during the teardown's cleanup
        was eaten (#235): the teardown returned as if nobody had asked

    MUTANT "no shield" (as in the test above) -- RED, observed verbatim:

        AssertionError: the cancel cut the mount's disconnect; events:
        [('disconnect', 'camera'), ('disconnected', 'camera'), ('disconnect',
        'mount'), ('disconnect', 'focuser'), ('disconnected', 'focuser'),
        ('disconnect', 'filterwheel'), ('disconnected', 'filterwheel')]
    """
    mount = rig.devices["mount"]
    mount.disconnect_s = _SLOW_S
    task = _start_teardown(rig)
    await asyncio.wait_for(mount.disconnect_started.wait(), timeout=_BOUND_S)
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait({task}, timeout=_BOUND_S)
    assert task.done(), f"the teardown had not ended {_BOUND_S} s after its cancel"
    assert ("disconnected", "mount") in rig.events, (
        f"the cancel cut the mount's disconnect; events: {rig.events}")
    assert _disconnected(rig) == list(_ROLES), rig.events
    assert rig.hub.devices == {}
    assert task.cancelled(), (
        "a cancel that landed during the teardown's cleanup was eaten (#235): "
        "the teardown returned as if nobody had asked")


@pytest.mark.asyncio
async def test_control_a_cancel_absorbed_before_the_teardown_is_not_raised(rig):
    """CONTROL: the teardown runs on a task that already absorbed a cancel
    without ``uncancel()``, so ``Task.cancelling()`` reads 1 on entry. The
    lifespan shutdown can hand it exactly that: its ``except
    (asyncio.CancelledError, Exception): pass`` around ``await bt`` also
    catches a cancel of the lifespan itself. Nothing cancels the teardown, so
    it returns normally. The raise after the cleanup is for a cancel that
    landed DURING it, counted against the baseline read on entry; one raised
    here would pass the shutdown's ``except Exception`` and skip everything
    after ``disconnect_all``.

    MUTANT "the baseline read as zero" (``asked = 0``) -- RED, observed
    verbatim:

        AssertionError: a teardown nobody cancelled raised CancelledError
        because its task had absorbed a cancel before it started
    """
    async def carrier() -> str:
        me = asyncio.current_task()
        me.cancel()
        try:
            await asyncio.sleep(0)      # the cancel is delivered here
        except asyncio.CancelledError:
            pass                        # absorbed, and never uncancelled
        assert me.cancelling() == 1
        await rig.hub._teardown()
        return "returned"

    task = rig.teardown = asyncio.create_task(carrier())
    await asyncio.wait({task}, timeout=_BOUND_S)
    assert task.done(), f"the teardown had not ended within {_BOUND_S} s"
    assert not task.cancelled(), (
        "a teardown nobody cancelled raised CancelledError because its task "
        "had absorbed a cancel before it started")
    assert task.result() == "returned"
    assert _disconnected(rig) == list(_ROLES), rig.events
    assert rig.hub.devices == {} and rig.hub.mode == "none"


@pytest.mark.asyncio
async def test_control_a_hanging_disconnect_is_cut_and_the_rest_run(
        rig, monkeypatch):
    """CONTROL: nothing cancels the teardown, and the mount's disconnect
    never answers. It is cut at its own bound, said in a warning naming the
    mount, and every device after it is still disconnected.

    MUTANT "the cleanup's steps unbounded" (``_teardown_step`` passes
    ``None`` as the bound) -- RED, observed verbatim:

        AssertionError: a hanging disconnect held the teardown past 3.0 s;
        events: [('disconnect', 'camera'), ('disconnected', 'camera'),
        ('disconnect', 'mount')]
    """
    monkeypatch.setattr(hub_mod, "TEARDOWN_STEP_TIMEOUT_S", _CUT_S)
    rig.devices["mount"].disconnect_hangs = True
    task = _start_teardown(rig)
    await asyncio.wait({task}, timeout=_BOUND_S)
    assert task.done(), (
        f"a hanging disconnect held the teardown past {_BOUND_S} s; events: "
        f"{rig.events}")
    assert task.exception() is None
    assert _disconnected(rig) == list(_ROLES), rig.events
    assert ("disconnected", "mount") not in rig.events
    assert rig.hub.devices == {}
    warned = [m for (lvl, m, _s) in rig.logs
              if lvl == "warning" and "mount" in m]
    assert warned, rig.logs


class _Bare:
    """A double with no ``disconnect`` and no ``close``, as the guider and
    NINA doubles of other suites are."""


@pytest.mark.asyncio
async def test_control_a_double_with_no_disconnect_ends_only_its_own_step(rig):
    """CONTROL: nothing cancels the teardown, and a device, the guider and
    the NINA client have no ``disconnect``/``close`` at all. Each ends only
    its own step, as the ``try``/``except`` around each await always let it:
    every other device is disconnected, the hub lets go of all three, and
    the teardown returns normally.

    The first cut of the #267 fix handed each step a bound method
    (``dev.disconnect``), looked up before the step's task could catch
    anything, and the full suite went red with 45 fixture teardowns of
    ``AttributeError: '_FakeGuider' object has no attribute 'disconnect'``.
    This test is the one that should have caught it.

    MUTANT "a bound method handed to the step", for the guider
    (``guider.disconnect`` in place of ``lambda: guider.disconnect()``) --
    RED, observed verbatim:

        >                                         guider.disconnect)
        E               AttributeError: '_Bare' object has no attribute
        'disconnect'

    and for the devices (``dev.disconnect``) -- RED, observed verbatim:

        >                                         dev.disconnect)
        E               AttributeError: '_Bare' object has no attribute
        'disconnect'

    and for the NINA client (``client.close``) -- RED, observed verbatim:

        >                                         client.close)
        E               AttributeError: '_Bare' object has no attribute 'close'
    """
    h = rig.hub
    # First in the walk, so a failure that ends the loop strands the rest.
    h.devices = {"dome": _Bare(), **h.devices}
    h.guider = _Bare()
    h.nina_client = _Bare()
    await asyncio.wait_for(_start_teardown(rig), timeout=_BOUND_S)
    assert _disconnected(rig) == list(_ROLES), rig.events
    assert h.devices == {} and h.guider is None and h.nina_client is None
    said = [m for (_lvl, m, _src) in rig.logs if m == "all equipment disconnected"]
    assert said == ["all equipment disconnected"]
