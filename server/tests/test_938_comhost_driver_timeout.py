# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#938: only the submit deadline is a deadline. A TimeoutError a COM driver
raises is a driver error like any other, and the device stays.

On Python 3.11+ ``concurrent.futures.TimeoutError`` IS the builtin
``TimeoutError``. ``ComDevice.submit`` waited with ``fut.result(timeout=...)``
and caught that class, so it also caught a TimeoutError the handler raised and
marshalled back through the Future: the answer was HTTP 500 "COM call on ...
exceeded 2s deadline" (false: the call returned at once) and the host
fault-evicted a healthy device, abandoning its STA thread. Now ``submit`` waits
for completion without raising, and a finished call's own exception comes back
as itself, to be mapped by ``_alpaca_error_number`` (0x500) like a
RuntimeError. ``connect`` waits the same way (one ``ComDevice._await``), so a
``Connected=true`` that wedges is the deadline too and gets the fault-eviction.

No comtypes and no hardware: fake COM objects behind the real ComDevice. Each
test names the mutant of production code it was shown red under (applied to a
byte copy of ``comhost/device.py``, restored from that copy and md5-checked).
"""
from __future__ import annotations

import errno
import threading
from concurrent.futures import TimeoutError as FuturesTimeout

import pytest

import astrodeck.comhost.handlers_telescope as handlers_telescope
import astrodeck.comhost.server as server
from astrodeck.comhost.device import ComDevice, ComTimeoutError
from astrodeck.devices.alpaca import AlpacaConnection, AlpacaReplyError
from astrodeck.devices.ascom_registry import AscomDriver


class _DriverTimeout(TimeoutError):
    """A driver library's own subclass of the builtin."""


DRIVER_TIMEOUTS = [
    pytest.param(lambda: TimeoutError("socket-ish timeout from inside the call"),
                 id="TimeoutError"),
    pytest.param(lambda: _DriverTimeout("driver library timeout"),
                 id="subclass"),
    pytest.param(lambda: OSError(errno.ETIMEDOUT, "link timed out"),
                 id="OSError-ETIMEDOUT"),
    pytest.param(lambda: FuturesTimeout("handler waited on its own future"),
                 id="concurrent.futures.TimeoutError"),
]


class _Dev:
    """The fake COM object: ``fail`` raises what it was given, ``ok`` answers."""

    Connected = False

    def __init__(self, error=None):
        self.error = error
        self.threads: list[int] = []

    def ok(self):
        self.threads.append(threading.get_ident())
        return 7

    def fail(self):
        self.threads.append(threading.get_ident())
        raise self.error


@pytest.mark.parametrize("make_error", DRIVER_TIMEOUTS)
def test_submit_hands_back_a_drivers_timeout_as_itself(make_error):
    """``submit`` re-raises the exception the call raised, not
    ``ComTimeoutError``, and the device is neither evicted nor stuck: the next
    call runs on the same STA thread.

    MUTANT M1 "the old wait": the wait in ``submit`` restored to ``try:
    return fut.result(timeout=self._timeout_s)`` with ``except TimeoutError:
    raise ComTimeoutError(...)`` (the unfixed code): RED (observed), all four
    cases, ``ComTimeoutError: COM call on Fake.Dev exceeded 2s deadline`` where
    the driver's own error was expected. The file has 9 red under M1.
    """
    error = make_error()
    obj = _Dev(error)
    dev = ComDevice("Fake.Dev", create=lambda pid: obj, timeout_s=2.0)
    try:
        dev.connect()
        with pytest.raises(type(error)) as info:
            dev.submit(lambda o: o.fail())
        assert info.value is error
        assert not isinstance(info.value, ComTimeoutError)
        assert dev._evicted is False

        assert dev.submit(lambda o: o.ok()) == 7
        assert len(set(obj.threads)) == 1  # the same STA thread, never replaced
    finally:
        dev.disconnect()


class _ConnectFails:
    """A fake COM object whose ``Connected = True`` raises ``error`` until
    ``error`` is cleared (a driver whose link timed out while opening)."""

    def __init__(self, error):
        self.error = error
        self.threads: list[int] = []
        self._connected = False

    @property
    def Connected(self):
        return self._connected

    @Connected.setter
    def Connected(self, value):
        self.threads.append(threading.get_ident())
        if value and self.error is not None:
            raise self.error
        self._connected = value


def test_a_connect_that_wedges_is_the_deadline():
    """``connect`` waits the way ``submit`` does, so a ``Connected = True`` that
    never returns raises ``ComTimeoutError`` (which the host turns into the
    fault-eviction) and not the bare builtin ``TimeoutError`` that
    ``fut.result(timeout=)`` raises, which the host answers as an empty 0x500
    and keeps the wedged slot for.

    MUTANT M4 "connect waits with the bare result(timeout=)": ``connect``
    restored to ``fut.result(timeout=self._timeout_s)`` (round 1 code): RED
    (observed), ``TimeoutError`` raised where ``ComTimeoutError`` was expected.
    """
    release = threading.Event()

    class _Wedge:
        @property
        def Connected(self):
            return False

        @Connected.setter
        def Connected(self, value):
            release.wait(5.0)

    dev = ComDevice("Fake.Dev", create=lambda pid: _Wedge(), timeout_s=0.1)
    try:
        with pytest.raises(ComTimeoutError):
            dev.connect()
        assert dev.connected is False
    finally:
        release.set()
        dev.abandon()


@pytest.mark.parametrize("make_error", DRIVER_TIMEOUTS)
def test_connect_hands_back_a_drivers_timeout_as_itself(make_error):
    """The other half, at the sibling site: a TimeoutError the driver raises
    from inside ``Connected = True`` is a driver error, not the deadline. It
    comes back as the same object, the device is not marked evicted, and a
    retry connects on the same STA thread.

    Green before the fix too (``fut.result(timeout=)`` re-raises a finished
    call's own exception); it guards the fix for the wedge above from swinging
    the other way. MUTANT M5 "catch the class": ``connect`` made ``try:
    fut.result(timeout=...) except TimeoutError: raise ComTimeoutError(...)``:
    RED (observed), all four cases, ``ComTimeoutError`` where the driver's own
    error was expected.
    """
    error = make_error()
    obj = _ConnectFails(error)
    dev = ComDevice("Fake.Dev", create=lambda pid: obj, timeout_s=2.0)
    try:
        with pytest.raises(type(error)) as info:
            dev.connect()
        assert info.value is error
        assert not isinstance(info.value, ComTimeoutError)
        assert dev.connected is False
        assert dev._evicted is False

        obj.error = None
        dev.connect()
        assert dev.connected is True
        assert len(set(obj.threads)) == 1  # the same STA thread, never replaced
    finally:
        dev.disconnect()


def _host(monkeypatch, make, *, timeout_s: float = 2.0) -> server.ComHost:
    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: make(),
                                 timeout_s=timeout_s))
    monkeypatch.setitem(server.DEVICE_API, "faketype", {
        "get": {"boom": lambda obj, params: obj.fail(),
                "ok": lambda obj, params: obj.ok()},
        "put": {}})
    host = server.ComHost(drivers=[
        AscomDriver("FakeType", "faketype", "Fake.Dev", "Fake", 0)])
    host.handle("put", "faketype", 0, "connected", {"Connected": ["true"]})
    return host


@pytest.mark.parametrize("make_error", DRIVER_TIMEOUTS)
def test_a_drivers_timeout_is_a_driver_error_and_the_device_stays(
        monkeypatch, make_error):
    """Through the host: HTTP 200 with 0x500 and the driver's own words, the
    slot kept, the device still connected, and the next call served on the same
    device without a reconnect.

    MUTANT M1 (see above): RED (observed), all four cases, status 500 and
    ErrorMessage "COM call on Fake.Dev exceeded 2s deadline", the slot gone.
    """
    error = make_error()
    obj = _Dev(error)
    host = _host(monkeypatch, lambda: obj)
    try:
        dev = host._devices[("faketype", 0)]
        status, body, _ = host.handle("get", "faketype", 0, "boom", {})
        assert status == 200
        assert body["ErrorNumber"] == 0x500
        assert body["ErrorMessage"] == str(error)
        assert "exceeded" not in body["ErrorMessage"]
        assert body["Value"] is None

        assert host._devices.get(("faketype", 0)) is dev  # not evicted
        status, body, _ = host.handle("get", "faketype", 0, "connected", {})
        assert (status, body["Value"]) == (200, True)

        status, body, _ = host.handle("get", "faketype", 0, "ok", {})
        assert (status, body["ErrorNumber"], body["Value"]) == (200, 0, 7)
        assert len(set(obj.threads)) == 1
    finally:
        host.close()


def test_a_call_that_overruns_the_deadline_is_still_a_deadline(monkeypatch):
    """The other half of the rule: a call still running at the deadline is the
    deadline, whatever it later raises. This one overruns, then raises a
    TimeoutError of its own; the answer is still the fault-eviction (HTTP 500
    "exceeded", 0x500, slot dropped). Green before the fix too: it guards the
    fix from swinging the other way.

    MUTANT M2 "no deadline": ``if not done:`` in ``submit`` made ``if False``:
    RED (observed), this test (the call blocks until the handler gives up and
    the answer is HTTP 200, ``assert 200 == 500``), and with it the existing
    test_comhost_sta_harness wedge test (``DID NOT RAISE ComTimeoutError``),
    test_comhost_fault_eviction and the #937 post-eviction test.
    """
    release = threading.Event()

    class _Overrun(_Dev):
        def fail(self):
            release.wait(5.0)
            raise TimeoutError("late and unwanted")

    host = _host(monkeypatch, _Overrun, timeout_s=0.1)
    try:
        status, body, _ = host.handle("get", "faketype", 0, "boom", {})
        assert status == 500
        assert body["ErrorNumber"] == 0x500
        assert "exceeded" in body["ErrorMessage"]
        assert ("faketype", 0) not in host._devices  # evicted
    finally:
        release.set()
        host.close()


# --------------------------------------------------------------------------
# End to end: the real Alpaca client against a running comhost
# --------------------------------------------------------------------------
async def test_the_alpaca_client_sees_a_driver_error_and_keeps_its_connection(
        monkeypatch):
    """Over the wire: a telescope read that times out inside the driver raises
    an AlpacaReplyError with HTTP 200 / 0x500, and the very next read on the
    same connection succeeds with no new Connected PUT (an evicted device would
    have answered 0x407 here).

    MUTANT M1 (see above): RED (observed), ``http_status`` is 500.
    """
    handlers_telescope.register()  # undo any sibling's DEVICE_API mutation
    holder = {"timeout": True}

    class _Scope:
        Connected = False

        @property
        def RightAscension(self):
            if holder["timeout"]:
                raise TimeoutError("mount link timed out")
            return 10.0

    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: _Scope(),
                                 timeout_s=2.0))
    srv = server.serve(port=0, drivers=[
        AscomDriver("Telescope", "telescope", "Fake.Scope", "Fake Scope", 0)])
    conn = AlpacaConnection("127.0.0.1", srv.server_address[1])
    try:
        await conn.put("telescope", 0, "connected", Connected=True)
        with pytest.raises(AlpacaReplyError) as info:
            await conn.get("telescope", 0, "rightascension")
        assert info.value.http_status == 200
        assert info.value.error_number == 0x500

        holder["timeout"] = False
        assert await conn.get("telescope", 0, "rightascension") == 10.0
    finally:
        await conn.close()
        srv.com_host.close()
        srv.shutdown()
