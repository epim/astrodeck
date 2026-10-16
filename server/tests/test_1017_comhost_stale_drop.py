# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1017: a disconnect that finishes late must not drop the slot that replaced
its device (the #988 class, on the disconnect path).

THE DEFECT. ``ComHost._connected`` (a ``Connected=false`` PUT) calls
``dev.disconnect()``, which can take as long as the call deadline plus a 5 s
join, and then ``_drop(dev_type, dev_num)``, which popped whatever the key held.
While the disconnect was still running the slot could have been replaced: a
reconnect that timed out against the half-closed device evicts it (#988), and
the next ``Connected=true`` builds a fresh slot. The late ``_drop`` then removed
that healthy device, so the client that had just reconnected was answered 0x407
"not connected" and had to reconnect a second time.

THE FIX. ``_drop`` takes the device the disconnect was about and pops the slot
only while it still holds that object, as ``_evict`` does.

The window is real: the first COM object's ``Connected = False`` blocks on the
real STA thread until the test lets it go, the disconnect's own call deadline
fires for real (0.3 s), and the join is widened so the disconnect stays open
until the release rather than for 5 s of wall clock. What the test takes off the
wall clock is WHEN THE DISCONNECT ENDS: an event, set after the replacement is
built, so the interleaving is the same on a loaded machine as on an idle one.
Everything else, the eviction, the rebuild and the late drop, is production code.

Not changed by this fix, and not asserted: a ``Connected=true`` that arrives
while the disconnect is still on the device's own healthy thread is served by
that same device (the slot still holds it), and the drop that follows removes
it. That is the device being disconnected, not a replacement.

MUTANT M1 "drop by key": ``ComHost._drop`` made to pop ``(dev_type, dev_num)``
whatever it holds (the pre-fix body): RED (observed), ``AssertionError: a late
disconnect dropped the slot that replaced its device`` /
``assert None is <ComDevice ...>``. The other test stays green.
MUTANT M2 "never drop": the identity test in ``ComHost._drop`` made ``if
True: return``: RED (observed), the ordinary disconnect leaves its slot in the
table, ``AssertionError: a disconnect must drop its own slot``.
"""
from __future__ import annotations

import threading
import time

import astrodeck.comhost.device as device_mod
import astrodeck.comhost.server as server
from astrodeck.comhost.device import ComDevice
from astrodeck.devices.ascom_registry import AscomDriver

KEY = ("telescope", 0)


def _host(monkeypatch):
    """A telescope host whose FIRST COM object blocks inside ``Connected =
    False`` until ``hold`` is set (``entered`` says it got there) and whose
    later objects never block. Returns ``(host, made, entered, hold)``."""
    entered = threading.Event()
    hold = threading.Event()
    made: list[object] = []

    class _Scope:
        def __init__(self):
            self.holds = not made
            made.append(self)
            self._connected = False

        @property
        def Connected(self):
            return self._connected

        @Connected.setter
        def Connected(self, value):
            if not value and self.holds:
                entered.set()
                hold.wait(10.0)
            self._connected = bool(value)

    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: _Scope(),
                                 timeout_s=0.3))
    host = server.ComHost(drivers=[
        AscomDriver("Telescope", "telescope", "Fake.Scope", "Fake Scope", 0)])
    return host, made, entered, hold


def _put_connected(host: server.ComHost, want: bool):
    return host.handle("put", "telescope", 0, "connected",
                       {"Connected": ["true" if want else "false"]})


def _wait_until(predicate, what: str) -> None:
    deadline = time.monotonic() + 5.0
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.01)


def test_a_late_disconnect_does_not_drop_the_slot_that_replaced_its_device(
        monkeypatch):
    """The issue's interleaving. A disconnect is stuck on a device whose COM
    object will not close. Its own deadline fires, the client reconnects, that
    connect times out against the half-closed device and evicts it, and the next
    connect builds a fresh slot. Only then does the disconnect end; it must leave
    the fresh slot alone, which stays connected and in the table.

    MUTANT M1 (see the module docstring): RED (observed).
    """
    # Keep the disconnect inside dev.disconnect() until the release, not 5 s.
    monkeypatch.setattr(device_mod, "_THREAD_JOIN_TIMEOUT_S", 30.0)
    host, made, entered, hold = _host(monkeypatch)
    answers: dict[str, tuple] = {}
    thread_d = threading.Thread(
        target=lambda: answers.update(D=_put_connected(host, False)),
        name="disconnect-D")
    try:
        status, body, _ = _put_connected(host, True)
        assert (status, body["ErrorNumber"]) == (200, 0), "precondition"
        slot1 = host._devices[KEY]

        thread_d.start()
        assert entered.wait(5.0), "the disconnect never reached the COM object"
        # Its call deadline fires (swallowed: it is best-effort) and the device
        # reports itself disconnected while its thread is still stuck.
        _wait_until(lambda: not slot1.connected,
                    "the disconnect's own deadline never fired")
        assert thread_d.is_alive(), "precondition: D is still disconnecting"

        # The client reconnects. The slot still holds the half-closed device,
        # whose thread cannot run the connect: it times out and evicts it.
        status, body, _ = _put_connected(host, True)
        assert status == 500 and "exceeded" in body["ErrorMessage"]
        assert KEY not in host._devices, "the timeout must evict the stuck slot"

        # The client tries again: a fresh device, a fresh COM object.
        status, body, _ = _put_connected(host, True)
        assert (status, body["ErrorNumber"]) == (200, 0)
        slot2 = host._devices[KEY]
        assert slot2 is not slot1 and slot2.connected, "precondition"
        assert len(made) == 2 and not made[1].holds, "precondition"
        assert thread_d.is_alive(), "precondition: D is still disconnecting"

        # Only now does the disconnect end.
        hold.set()
        thread_d.join(5.0)
        assert not thread_d.is_alive()
        assert answers["D"][0] == 200

        assert host._devices.get(KEY) is slot2, (
            "a late disconnect dropped the slot that replaced its device")
        assert slot2.connected is True and slot2._evicted is False
        status, body, _ = host.handle("get", "telescope", 0, "connected", {})
        assert (status, body["Value"]) == (200, True), (
            "the healthy device must still be connected")
    finally:
        hold.set()
        if thread_d.ident is not None:
            thread_d.join(5.0)
        host.close()


def test_a_disconnect_still_drops_its_own_slot(monkeypatch):
    """The identity check must not turn the drop off: a disconnect that ends
    while its slot still holds its device removes the slot, and the next connect
    builds a fresh device that is connected.

    MUTANT M2 (see the module docstring): RED (observed).
    """
    host, made, entered, hold = _host(monkeypatch)
    hold.set()                              # nothing wedges in this test
    try:
        assert _put_connected(host, True)[1]["ErrorNumber"] == 0
        slot1 = host._devices[KEY]

        status, body, _ = _put_connected(host, False)
        assert (status, body["ErrorNumber"]) == (200, 0)
        assert KEY not in host._devices, "a disconnect must drop its own slot"
        assert slot1.connected is False
        status, body, _ = host.handle("get", "telescope", 0, "connected", {})
        assert (status, body["Value"]) == (200, False)

        status, body, _ = _put_connected(host, True)
        assert (status, body["ErrorNumber"]) == (200, 0)
        slot2 = host._devices[KEY]
        assert slot2 is not slot1 and slot2.connected is True
        assert len(made) == 2
    finally:
        host.close()
