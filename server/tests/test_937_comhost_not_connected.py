# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#937: a comhost request to a device with no live COM object answers Alpaca
NotConnected (0x407), not a driver error from running the handler on None.

A ComDevice has no COM object until ``Connected=true`` is PUT, and a device
that wedged is fault-evicted and rebuilt empty. The dispatch used to run the
handler against that empty slot anyway: ``GET telescope/0/tracking`` before the
Connected PUT answered ErrorMessage "'NoneType' object has no attribute
'Tracking'" as 0x500 (0x400 before #872). Now it answers 0x407 with a plain
message, which is the answer a client reads as "reconnect".

No comtypes and no hardware: fake COM objects behind the real ComDevice, the
real telescope handlers, and (for the wire tests) the real Alpaca client.
Each test names the mutant of production code it was shown red under (applied
to a byte copy of ``comhost/server.py``, or ``comhost/device.py`` for M4,
restored from that copy and md5-checked).
"""
from __future__ import annotations

import threading

import pytest

import astrodeck.comhost.handlers_telescope as handlers_telescope
import astrodeck.comhost.server as server
from astrodeck.comhost.device import ComDevice
from astrodeck.devices.alpaca import AlpacaConnection, AlpacaReplyError
from astrodeck.devices.ascom_registry import AscomDriver

NOT_CONNECTED = 0x407
PLAIN = "telescope #0 is not connected"


class _Scope:
    """A fake telescope COM object that only exists once connected."""

    Connected = False
    RightAscension = 10.0
    Tracking = True

    def AbortSlew(self):
        return None


def _scope_host(monkeypatch, *, timeout_s: float = 2.0,
                make=_Scope) -> server.ComHost:
    handlers_telescope.register()  # undo any sibling's DEVICE_API mutation
    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: make(),
                                 timeout_s=timeout_s))
    return server.ComHost(drivers=[
        AscomDriver("Telescope", "telescope", "Fake.Scope", "Fake Scope", 0)])


def _connect(host: server.ComHost) -> None:
    host.handle("put", "telescope", 0, "connected", {"Connected": ["true"]})


def _assert_not_connected(status, body):
    assert status == 200
    assert body["ErrorNumber"] == NOT_CONNECTED
    assert body["ErrorMessage"] == PLAIN
    assert body["Value"] is None


@pytest.mark.parametrize("verb,method,params", [
    pytest.param("get", "tracking", {}, id="get-tracking"),
    pytest.param("get", "rightascension", {}, id="get-rightascension"),
    pytest.param("put", "tracking", {"Tracking": ["true"]}, id="put-tracking"),
    pytest.param("put", "abortslew", {}, id="put-abortslew"),
])
def test_a_request_before_connected_true_answers_not_connected(
        monkeypatch, verb, method, params):
    """The issue's repro, on the real telescope handlers: nothing has PUT
    Connected=true, so every read and write answers 0x407 in plain words, never
    the AttributeError text of a handler that ran on None.

    MUTANT M1 "no guard": the ``if not dev.connected`` test in
    ``ComHost._dispatch`` made ``if False``: RED (observed), all four cases,
    ``assert 1280 == 1031`` with ErrorMessage "'NoneType' object has no
    attribute ..." (0x500).
    MUTANT M2 "wrong number": ``_ALPACA_NOT_CONNECTED`` set to 0x500: RED
    (observed), all four cases, ``assert 1280 == 1031``.
    """
    host = _scope_host(monkeypatch)
    try:
        status, body, _ = host.handle(verb, "telescope", 0, method, params)
    finally:
        host.close()
    _assert_not_connected(status, body)
    assert "NoneType" not in body["ErrorMessage"]


def test_the_handler_does_not_run_while_the_device_is_not_connected(monkeypatch):
    """Not only the answer: the handler is never entered with the empty slot.

    MUTANT M1 (see above): RED (observed), ``assert 0 == 1031``: the spy ran
    on ``None`` and the call answered success.
    """
    ran: list[object] = []
    monkeypatch.setitem(server.DEVICE_API, "faketype", {
        "get": {"spy": lambda obj, params: ran.append(obj)}, "put": {}})
    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: _Scope(),
                                 timeout_s=2.0))
    host = server.ComHost(drivers=[
        AscomDriver("FakeType", "faketype", "Fake.Dev", "Fake", 0)])
    try:
        status, body, _ = host.handle("get", "faketype", 0, "spy", {})
    finally:
        host.close()
    assert body["ErrorNumber"] == NOT_CONNECTED
    assert status == 200
    assert ran == []


def test_connecting_lifts_the_refusal_and_disconnecting_restores_it(monkeypatch):
    """0x407 is the state, not a blanket refusal: the same request is served
    while connected and refused again after Connected=false.

    MUTANT M3 "always refuse": the guard made ``if True``: RED (observed), the
    connected read answers 0x407 where 10.0 was expected.
    """
    host = _scope_host(monkeypatch)
    try:
        _assert_not_connected(*host.handle(
            "get", "telescope", 0, "rightascension", {})[:2])
        _connect(host)
        status, body, _ = host.handle("get", "telescope", 0, "rightascension", {})
        assert (status, body["ErrorNumber"], body["Value"]) == (200, 0, 10.0)
        host.handle("put", "telescope", 0, "connected", {"Connected": ["false"]})
        _assert_not_connected(*host.handle(
            "get", "telescope", 0, "rightascension", {})[:2])
    finally:
        host.close()


def test_a_request_after_fault_eviction_answers_not_connected(monkeypatch):
    """The second case of the issue: a wedged call evicts the device, the host
    builds a fresh one on the next request, and that one has no COM object. It
    answers 0x407 (reconnect), and works again once the client does.

    MUTANT M1 (see above): RED (observed), the post-eviction read answers
    0x500 "'NoneType' object has no attribute 'RightAscension'".
    """
    release = threading.Event()

    class _Wedging(_Scope):
        wedge = True

        @property
        def RightAscension(self):
            if _Wedging.wedge:
                release.wait(5.0)
            return 10.0

    _Wedging.wedge = True
    host = _scope_host(monkeypatch, timeout_s=0.2, make=_Wedging)
    try:
        _connect(host)
        status, body, _ = host.handle("get", "telescope", 0, "rightascension", {})
        assert status == 500
        assert "exceeded" in body["ErrorMessage"]
        assert ("telescope", 0) not in host._devices  # evicted

        _assert_not_connected(*host.handle(
            "get", "telescope", 0, "rightascension", {})[:2])

        _Wedging.wedge = False
        _connect(host)
        status, body, _ = host.handle("get", "telescope", 0, "rightascension", {})
        assert (status, body["ErrorNumber"], body["Value"]) == (200, 0, 10.0)
    finally:
        release.set()
        host.close()


class _FirstConnectWedges:
    """A fake telescope whose FIRST instance blocks inside ``Connected = True``
    until released, and every later instance connects at once. Which instance
    wedges is decided at construction, so a host that builds a fresh object
    after an eviction gets a healthy one."""

    made: list["_FirstConnectWedges"] = []
    release = threading.Event()
    RightAscension = 10.0
    Tracking = True

    def __init__(self):
        self._wedge = not _FirstConnectWedges.made
        self._connected = False
        _FirstConnectWedges.made.append(self)

    @property
    def Connected(self):
        return self._connected

    @Connected.setter
    def Connected(self, value):
        if self._wedge and value:
            _FirstConnectWedges.release.wait(5.0)
        self._connected = value


def _wedged_connect_host(monkeypatch) -> server.ComHost:
    _FirstConnectWedges.made = []
    _FirstConnectWedges.release = threading.Event()
    return _scope_host(monkeypatch, timeout_s=0.2, make=_FirstConnectWedges)


def test_a_wedged_connect_is_evicted_so_the_retry_builds_a_fresh_device(
        monkeypatch):
    """The recovery the #937 guard must not remove. A driver that wedges inside
    its first ``Connected=true`` PUT used to be recovered by the NEXT data
    request, which queued behind the wedge, hit the submit deadline and evicted
    the slot. The guard now answers 0x407 before that submit, so the connect
    itself must be the thing that hits the deadline: HTTP 500 "exceeded", slot
    evicted, and the client's retry of Connected=true builds a fresh object that
    connects and serves reads. Without this, every retry queues behind the same
    wedged STA thread and the device is bricked until the host restarts.

    MUTANT M4 "connect waits with the bare result(timeout=)": ``ComDevice.connect``
    restored to ``fut.result(timeout=self._timeout_s)`` (round 1 code): RED
    (observed), the first PUT answers HTTP 200 / 0x500 with an EMPTY
    ErrorMessage and the slot is kept, so ``assert 200 == 500``.
    """
    host = _wedged_connect_host(monkeypatch)
    try:
        status, body, _ = host.handle(
            "put", "telescope", 0, "connected", {"Connected": ["true"]})
        assert status == 500
        assert "exceeded" in body["ErrorMessage"]
        assert ("telescope", 0) not in host._devices  # evicted

        status, body, _ = host.handle(
            "put", "telescope", 0, "connected", {"Connected": ["true"]})
        assert (status, body["ErrorNumber"]) == (200, 0)
        status, body, _ = host.handle("get", "telescope", 0, "rightascension", {})
        assert (status, body["ErrorNumber"], body["Value"]) == (200, 0, 10.0)
        assert len(_FirstConnectWedges.made) == 2  # a fresh object, not the wedge
    finally:
        _FirstConnectWedges.release.set()
        host.close()


@pytest.fixture()
def running_wedged_connect(monkeypatch):
    handlers_telescope.register()  # undo any sibling's DEVICE_API mutation
    _FirstConnectWedges.made = []
    _FirstConnectWedges.release = threading.Event()
    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: _FirstConnectWedges(),
                                 timeout_s=0.3))
    srv = server.serve(port=0, drivers=[
        AscomDriver("Telescope", "telescope", "Fake.Scope", "Fake Scope", 0)])
    yield srv.server_address[1]
    _FirstConnectWedges.release.set()
    srv.com_host.close()
    srv.shutdown()


async def test_the_alpaca_client_recovers_by_retrying_connected_true(
        running_wedged_connect):
    """Over the wire, doing only what ``AlpacaDevice.connect`` does: the first
    ``Connected=true`` raises (the wedge, HTTP 500), the retry connects, and a
    read answers its value.

    MUTANT M4 (see above): RED (observed), the first PUT is HTTP 200 / 0x500
    (``assert 200 == 500``) and the retry fails the same way.
    """
    conn = AlpacaConnection("127.0.0.1", running_wedged_connect)
    try:
        with pytest.raises(AlpacaReplyError) as info:
            await conn.put("telescope", 0, "connected", Connected=True)
        assert info.value.http_status == 500

        await conn.put("telescope", 0, "connected", Connected=True)
        assert await conn.get("telescope", 0, "rightascension") == 10.0
    finally:
        await conn.close()


def test_a_device_the_host_has_no_driver_for_is_still_not_implemented(
        monkeypatch):
    """The guard sits after the driver lookup: a device number the host does
    not list keeps its HTTP 500 / 0x400 "no ASCOM driver" answer (the host's own
    route table), it does not turn into NotConnected."""
    host = _scope_host(monkeypatch)
    try:
        status, body, _ = host.handle("get", "telescope", 7, "tracking", {})
    finally:
        host.close()
    assert status == 500
    assert body["ErrorNumber"] == 0x400
    assert "no ASCOM driver" in body["ErrorMessage"]


# --------------------------------------------------------------------------
# End to end: the real Alpaca client against a running comhost
# --------------------------------------------------------------------------
@pytest.fixture()
def running_scope(monkeypatch):
    handlers_telescope.register()  # undo any sibling's DEVICE_API mutation
    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: _Scope(),
                                 timeout_s=2.0))
    srv = server.serve(port=0, drivers=[
        AscomDriver("Telescope", "telescope", "Fake.Scope", "Fake Scope", 0)])
    yield srv.server_address[1]
    srv.com_host.close()
    srv.shutdown()


async def test_the_alpaca_client_sees_not_connected_before_it_connects(
        running_scope):
    """Over the wire: a read before the Connected PUT raises an
    AlpacaReplyError carrying 0x407 (HTTP 200, as a real Alpaca server
    answers), and the same read succeeds after the PUT.

    MUTANT M1 (see above): RED (observed), ``error_number`` is 0x500.
    """
    conn = AlpacaConnection("127.0.0.1", running_scope)
    try:
        with pytest.raises(AlpacaReplyError) as info:
            await conn.get("telescope", 0, "tracking")
        assert info.value.http_status == 200
        assert info.value.error_number == NOT_CONNECTED

        await conn.put("telescope", 0, "connected", Connected=True)
        assert await conn.get("telescope", 0, "tracking") is True
    finally:
        await conn.close()
