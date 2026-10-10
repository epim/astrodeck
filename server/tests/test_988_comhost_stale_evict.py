# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#988: a late timeout from a call queued behind a wedge must not evict the
healthy device that replaced it.

THE DEFECT. ``ComHost.handle`` answered a ``ComTimeoutError`` by popping
whatever ComDevice sat in the slot under that (type, number) key. Two calls
queued behind one wedged STA thread (the status poller reads several properties
at once) time out a deadline apart. The first evicts slot 1; the client
reconnects and the host builds slot 2; the second call's timeout then arrives,
is about slot 1, and popped slot 2. The just-reconnected device was torn down
and the next read answered 0x407 "not connected", so the client reconnected a
second time (and the run lost a frame in between).

THE FIX. The timeout names the device it happened to (``ComTimeoutError.device``)
and ``ComHost._evict`` pops the slot only while it still holds that object.

The wedge is real (a fake COM object that blocks inside a property read on the
real STA thread, and a real deadline for the first call). What the test takes
off the wall clock is WHEN THE SECOND CALL'S DEADLINE FIRES: it is released by an
event, after the reconnect, so the interleaving is the same on a loaded machine
as on an idle one. Everything the second call does with that deadline (the
``ComTimeoutError`` that names its device, the 500, the host's eviction decision)
is the production code.

MUTANT M1 "evict by key": ``ComHost._evict`` made to pop ``(dev_type, dev_num)``
whatever it holds (the pre-fix body): RED (observed), the late timeout removed
slot 2, ``AssertionError: a stale timeout evicted the device that replaced the
wedged one`` / ``assert None is <ComDevice ...>``. The other test stays green.
MUTANT M2 "never evict": the identity test in ``ComHost._evict`` made ``if
True: return``: RED (observed), both tests here and
``test_comhost_fault_eviction``: ``assert ('telescope', 0) not in {...}``, the
wedged slot is never dropped.
"""
from __future__ import annotations

import threading

import astrodeck.comhost.device as device_mod
import astrodeck.comhost.handlers_telescope as handlers_telescope
import astrodeck.comhost.server as server
from astrodeck.comhost.device import ComDevice
from astrodeck.devices.ascom_registry import AscomDriver

KEY = ("telescope", 0)


def _wedging_host(monkeypatch, *, timeout_s: float):
    """A telescope host whose FIRST COM object wedges inside ``RightAscension``
    until ``release`` is set, and whose later objects answer 10.0 at once.
    Returns ``(host, made, release)``."""
    handlers_telescope.register()  # undo any sibling's DEVICE_API mutation
    release = threading.Event()
    made: list[object] = []

    class _Scope:
        Connected = False
        Declination = 20.0

        def __init__(self):
            self.wedges = not made
            made.append(self)

        @property
        def RightAscension(self):
            if self.wedges:
                release.wait(10.0)
            return 10.0

    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: _Scope(),
                                 timeout_s=timeout_s))
    host = server.ComHost(drivers=[
        AscomDriver("Telescope", "telescope", "Fake.Scope", "Fake Scope", 0)])
    return host, made, release


def _connect(host: server.ComHost) -> None:
    status, body, _ = host.handle(
        "put", "telescope", 0, "connected", {"Connected": ["true"]})
    assert (status, body["ErrorNumber"]) == (200, 0)


def test_a_late_timeout_does_not_evict_the_device_that_replaced_the_wedged_one(
        monkeypatch):
    """The issue's interleaving. Call B is queued behind the wedge as well as
    call A. A's deadline fires first and evicts slot 1; the client reconnects
    into slot 2; only then does B's deadline fire. B still answers 500 (its call
    did time out), and slot 2 is left alone and serves.

    MUTANT M1 (see the module docstring): RED (observed).
    """
    host, made, release = _wedging_host(monkeypatch, timeout_s=0.3)

    # Hold call B's deadline until the test says it fires. Every other wait,
    # including call A's, is the real one.
    real_wait = device_mod.wait
    b_waiting = threading.Event()
    b_deadline = threading.Event()

    def controlled_wait(fs, timeout=None):
        if threading.current_thread().name == "call-B":
            b_waiting.set()
            b_deadline.wait(10.0)
            return set(), set(fs)           # not finished: the deadline fired
        return real_wait(fs, timeout=timeout)

    monkeypatch.setattr(device_mod, "wait", controlled_wait)

    answers: dict[str, tuple] = {}

    def call_b() -> None:
        answers["B"] = host.handle("get", "telescope", 0, "rightascension", {})

    thread_b = threading.Thread(target=call_b, name="call-B")
    try:
        _connect(host)
        slot1 = host._devices[KEY]

        thread_b.start()
        assert b_waiting.wait(5.0), "call B never started waiting"

        # Call A: queued behind the same wedge, and it times out for real.
        status, body, _ = host.handle("get", "telescope", 0, "rightascension", {})
        assert status == 500 and "exceeded" in body["ErrorMessage"]
        assert KEY not in host._devices, "A's timeout must evict the wedged slot"

        # The client reconnects: a fresh device, a fresh COM object.
        _connect(host)
        slot2 = host._devices[KEY]
        assert slot2 is not slot1 and slot2.connected, "precondition"
        assert len(made) == 2 and not made[1].wedges, "precondition"

        # Only now does B's deadline fire, about slot 1.
        assert thread_b.is_alive(), "precondition: B is still waiting"
        b_deadline.set()
        thread_b.join(5.0)
        assert not thread_b.is_alive()
        status_b, body_b, _ = answers["B"]
        assert status_b == 500 and "exceeded" in body_b["ErrorMessage"]

        assert host._devices.get(KEY) is slot2, (
            "a stale timeout evicted the device that replaced the wedged one")
        assert slot2._evicted is False
        status, body, _ = host.handle("get", "telescope", 0, "rightascension", {})
        assert (status, body["ErrorNumber"], body["Value"]) == (200, 0, 10.0), (
            "the healthy device must still be connected and serving")
    finally:
        b_deadline.set()
        release.set()
        thread_b.join(5.0)
        host.close()


def test_the_timed_out_device_is_still_evicted_when_it_is_the_one_in_the_slot(
        monkeypatch):
    """The identity check must not turn eviction off: a timeout that names the
    device the slot holds still drops the slot, abandons the wedged device, and
    lets the next connect build a fresh one that serves.

    MUTANT M2 (see the module docstring): RED (observed).
    """
    host, made, release = _wedging_host(monkeypatch, timeout_s=0.3)
    try:
        _connect(host)
        slot1 = host._devices[KEY]

        status, body, _ = host.handle("get", "telescope", 0, "rightascension", {})
        assert status == 500 and "exceeded" in body["ErrorMessage"]
        assert KEY not in host._devices
        assert slot1._evicted is True

        _connect(host)
        slot2 = host._devices[KEY]
        assert slot2 is not slot1
        status, body, _ = host.handle("get", "telescope", 0, "rightascension", {})
        assert (status, body["ErrorNumber"], body["Value"]) == (200, 0, 10.0)
    finally:
        release.set()
        host.close()
