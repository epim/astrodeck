# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#872: the comhost reports a COM driver exception as the Alpaca ErrorNumber it
IS, not as 0x400 for all of them.

In ASCOM/Alpaca 0x400 is NotImplemented: a capability answer. The comhost used
to answer every COM driver exception with it, so a parked mount, "not
tracking", a link timeout and a value out of range all read to the client as
"this driver cannot do that". Now an ASCOM HRESULT (0x80040400..0x80040FFF)
passes its low 12 bits through, a handful of generic HRESULTs map to their
ASCOM meaning, and any other driver exception is 0x500 (the first
driver-specific number). The ErrorMessage text is unchanged.

No comtypes and no hardware: the COM exceptions are fakes with comtypes'
COMError attribute shape (hresult, text, details), plus the REAL
``_ctypes.COMError`` on Windows to pin that shape. Each test names the mutant
of production code it was shown red under (applied to a byte copy of
``comhost/server.py``, restored from that copy and md5-checked).
"""
from __future__ import annotations

import sys

import pytest

import astrodeck.comhost.handlers_telescope as handlers_telescope
import astrodeck.comhost.server as server
from astrodeck.comhost.device import ComDevice
from astrodeck.devices.alpaca import (AlpacaConnection, AlpacaReplyError,
                                      AlpacaTelescope)
from astrodeck.devices.ascom_registry import AscomDriver
from astrodeck.devices.base import SyncRefused
from astrodeck.devices.sync_verify import SYNC_PARKED_REASON

DISP_E_EXCEPTION = 0x80020009


def _signed(hresult: int) -> int:
    """An HRESULT as comtypes hands it over: a signed 32-bit Python int."""
    return hresult - (1 << 32) if hresult & 0x80000000 else hresult


class FakeComError(Exception):
    """comtypes' COMError, by attribute shape: (hresult, text, details)."""

    def __init__(self, hresult, text="Exception occurred.", details=None):
        super().__init__(hresult, text, details)
        self.hresult = hresult
        self.text = text
        self.details = details


def _typed(hresult: int, message: str = "driver said no") -> FakeComError:
    """The driver's own HRESULT straight from a vtable call."""
    return FakeComError(_signed(hresult), "driver error",
                        (message, "Fictional.Driver", None, 0, None))


def _dispatch_exception(scode: int, message: str = "driver said no") -> FakeComError:
    """What IDispatch::Invoke gives comtypes: DISP_E_EXCEPTION, with the
    driver's own HRESULT as the EXCEPINFO scode, details[4]."""
    return FakeComError(_signed(DISP_E_EXCEPTION), "Exception occurred.",
                        (message, "Fictional.Driver", None, 0, _signed(scode)))


class _Dev:
    """The fake COM object: every call raises the exception it was given."""

    Connected = False
    RightAscension = 10.0
    Declination = 40.0

    def __init__(self, error):
        self._error = error

    def fail(self):
        raise self._error

    def SyncToCoordinates(self, ra, dec):
        raise self._error


def _make_host(monkeypatch, error) -> server.ComHost:
    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: _Dev(error),
                                 timeout_s=2.0))
    monkeypatch.setitem(server.DEVICE_API, "faketype", {
        "get": {"boom": lambda obj, params: obj.fail()}, "put": {}})
    host = server.ComHost(drivers=[
        AscomDriver("FakeType", "faketype", "Fake.Dev", "Fake", 0)])
    host.handle("put", "faketype", 0, "connected", {"Connected": ["true"]})
    return host


CASES = [
    # ---- the driver's ASCOM HRESULT straight from a vtable call
    pytest.param(_typed(0x80040400), 0x400, id="typed-NotImplemented"),
    pytest.param(_typed(0x80040401), 0x401, id="typed-InvalidValue"),
    pytest.param(_typed(0x80040402), 0x402, id="typed-ValueNotSet"),
    pytest.param(_typed(0x80040407), 0x407, id="typed-NotConnected"),
    pytest.param(_typed(0x80040408), 0x408, id="typed-InvalidWhileParked"),
    pytest.param(_typed(0x80040409), 0x409, id="typed-InvalidWhileSlaved"),
    pytest.param(_typed(0x8004040B), 0x40B, id="typed-InvalidOperation"),
    # a driver's own number in 0x500..0xFFF passes through, whole
    pytest.param(_typed(0x80040500), 0x500, id="typed-driver-first"),
    pytest.param(_typed(0x80040A12), 0xA12, id="typed-driver-specific"),
    pytest.param(_typed(0x80040FFF), 0xFFF, id="typed-driver-last"),
    # ---- through IDispatch: DISP_E_EXCEPTION, the driver's code in the scode
    pytest.param(_dispatch_exception(0x80040408), 0x408,
                 id="dispatch-InvalidWhileParked"),
    pytest.param(_dispatch_exception(0x8004040B), 0x40B,
                 id="dispatch-InvalidOperation"),
    pytest.param(_dispatch_exception(0x80040401), 0x401,
                 id="dispatch-InvalidValue"),
    pytest.param(_dispatch_exception(0x80040407), 0x407,
                 id="dispatch-NotConnected"),
    pytest.param(_dispatch_exception(0x80040400), 0x400,
                 id="dispatch-NotImplemented"),
    pytest.param(_dispatch_exception(0x80040C01), 0xC01,
                 id="dispatch-driver-specific"),
    # a DISP_E_EXCEPTION that names no driver code is a driver error, not
    # "not implemented"
    pytest.param(_dispatch_exception(0), 0x500, id="dispatch-no-scode"),
    pytest.param(FakeComError(_signed(DISP_E_EXCEPTION), "Exception occurred.",
                              None), 0x500, id="dispatch-no-details"),
    pytest.param(FakeComError(_signed(DISP_E_EXCEPTION), "Exception occurred.",
                              ("short",)), 0x500, id="dispatch-short-details"),
    # ---- generic HRESULTs that mean what an ASCOM code means
    pytest.param(_typed(0x80004001), 0x400, id="E_NOTIMPL"),
    pytest.param(_typed(0x80020003), 0x400, id="DISP_E_MEMBERNOTFOUND"),
    pytest.param(_typed(0x80070057), 0x401, id="E_INVALIDARG"),
    pytest.param(_typed(0x80131502), 0x401, id="COR_E_ARGUMENTOUTOFRANGE"),
    pytest.param(_typed(0x80131509), 0x40B, id="COR_E_INVALIDOPERATION"),
    pytest.param(_dispatch_exception(0x80131509), 0x40B,
                 id="dispatch-COR_E_INVALIDOPERATION"),
    # ---- any other COM failure: a driver error, never a capability answer
    pytest.param(_typed(0x800706BA), 0x500, id="RPC_S_SERVER_UNAVAILABLE"),
    pytest.param(_typed(0x80004005), 0x500, id="E_FAIL"),
    pytest.param(_typed(0x800403FF), 0x500, id="just-below-the-ASCOM-window"),
    pytest.param(_typed(0x80041000), 0x500, id="just-above-the-ASCOM-window"),
    pytest.param(_typed(0x00040408), 0x500, id="not-a-failure-HRESULT"),
    pytest.param(FakeComError("0x80040408"), 0x500, id="hresult-not-an-int"),
    pytest.param(FakeComError(True), 0x500, id="hresult-is-a-bool"),
    # ---- not COM at all
    pytest.param(NotImplementedError("host stub"), 0x400, id="NotImplementedError"),
    pytest.param(RuntimeError("serial timeout"), 0x500, id="RuntimeError"),
    pytest.param(OSError("link down"), 0x500, id="OSError"),
    pytest.param(ValueError("out of range"), 0x500, id="ValueError"),
    pytest.param(AttributeError("'NoneType' object has no attribute 'Tracking'"),
                 0x500, id="AttributeError"),
]


@pytest.mark.parametrize("error,expected", CASES)
def test_a_com_exception_answers_its_own_alpaca_error_number(
        monkeypatch, error, expected):
    """A driver exception rides HTTP 200 with the ErrorNumber it IS and its
    text unchanged.

    MUTANT M1 "one size": ``_alpaca_error_number`` made ``return 1024``, the
    constant this issue removed: RED (observed), 32 of the 37 cases (the five
    that expect 0x400 pass) -- ``assert 1024 == 1033`` and the like. The whole
    file is 40 red; the unfixed ``server.py`` itself is 41 red.
    MUTANT M2 "no unwrap": the DISP_E_EXCEPTION branch of ``_com_hresult``
    made ``if False``: RED (observed) on all seven ``dispatch-*`` cases that
    name a driver code, 14 red in the file.
    MUTANT M3 "driver error is 0x400": ``_ALPACA_DRIVER_ERROR`` set back to
    0x400: RED (observed) on every case that expects 0x500, 16 red in the file.
    """
    host = _make_host(monkeypatch, error)
    try:
        status, body, _ = host.handle("get", "faketype", 0, "boom", {})
    finally:
        host.close()
    assert status == 200
    assert body["ErrorNumber"] == expected
    assert body["ErrorMessage"] == str(error)
    assert body["Value"] is None


@pytest.mark.skipif(sys.platform != "win32",
                    reason="_ctypes.COMError exists on Windows only")
@pytest.mark.parametrize("make,expected", [
    pytest.param(lambda C: C(_signed(0x80040408), "driver error",
                             ("Parked", "Fictional.Driver", None, 0, None)),
                 0x408, id="typed"),
    pytest.param(lambda C: C(_signed(DISP_E_EXCEPTION), "Exception occurred.",
                             ("Parked", "Fictional.Driver", None, 0,
                              _signed(0x80040408))),
                 0x408, id="dispatch"),
    pytest.param(lambda C: C(_signed(DISP_E_EXCEPTION), "Exception occurred.",
                             ("Broke", "Fictional.Driver", None, 0, 0)),
                 0x500, id="dispatch-no-scode"),
])
def test_the_real_comerror_is_read_by_the_same_attributes(
        monkeypatch, make, expected):
    """The fakes above claim comtypes' COMError shape; this is the real class
    (``_ctypes.COMError``, which comtypes raises), so a rename of what the
    mapping reads cannot hide behind the fake.

    MUTANT M4 "wrong attribute": ``_com_hresult`` reading ``exc.hr`` instead of
    ``exc.hresult``: RED (observed), 29 red in the file, including the typed
    and dispatch cases here (0x500 where 0x408 was raised).
    """
    from _ctypes import COMError
    error = make(COMError)
    host = _make_host(monkeypatch, error)
    try:
        status, body, _ = host.handle("get", "faketype", 0, "boom", {})
    finally:
        host.close()
    assert status == 200
    assert body["ErrorNumber"] == expected
    assert body["ErrorMessage"] == str(error)


def test_a_wedged_com_call_is_a_driver_error_not_not_implemented(monkeypatch):
    """The per-call deadline (a link that never answers) is HTTP 500 with 0x500.
    A timeout is the commonest transient failure; 0x400 would tell a client the
    driver cannot do the call at all.

    MUTANT M5 "timeout is NotImplemented": the ComTimeoutError arm passing
    ``_ALPACA_NOT_IMPLEMENTED``: RED (observed), this test alone,
    ``assert 1024 == 1280``.
    """
    import time

    class _Wedge:
        Connected = False

        def hang(self):
            time.sleep(1.0)

    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: _Wedge(),
                                 timeout_s=0.1))
    monkeypatch.setitem(server.DEVICE_API, "faketype", {
        "get": {"hang": lambda obj, params: obj.hang()}, "put": {}})
    host = server.ComHost(drivers=[
        AscomDriver("FakeType", "faketype", "Fake.Dev", "Fake", 0)])
    try:
        host.handle("put", "faketype", 0, "connected", {"Connected": ["true"]})
        status, body, _ = host.handle("get", "faketype", 0, "hang", {})
    finally:
        host.close()
    assert status == 500
    assert body["ErrorNumber"] == 0x500
    assert "exceeded" in body["ErrorMessage"]


def test_a_method_the_host_does_not_serve_is_still_not_implemented(monkeypatch):
    """0x400 stays for the one case it is true: the host's own route table has
    no such method. HTTP 500 as before, so the client's _unwrap still raises."""
    host = _make_host(monkeypatch, RuntimeError("never reached"))
    try:
        status, body, _ = host.handle("get", "faketype", 0, "nosuchmethod", {})
    finally:
        host.close()
    assert status == 500
    assert body["ErrorNumber"] == 0x400


# --------------------------------------------------------------------------
# End to end: the real Alpaca client against a running comhost
# --------------------------------------------------------------------------
@pytest.fixture()
def running_scope(monkeypatch):
    """A running comhost whose one telescope refuses with ``scope.error``."""
    handlers_telescope.register()  # undo any sibling's DEVICE_API mutation
    holder = {"error": RuntimeError("unset")}

    class _Scope:
        Connected = False
        RightAscension = 10.0
        Declination = 40.0

        def SyncToCoordinates(self, ra, dec):
            raise holder["error"]

        @property
        def Tracking(self):
            raise holder["error"]

        @Tracking.setter
        def Tracking(self, value):
            raise holder["error"]

    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: _Scope(),
                                 timeout_s=2.0))
    srv = server.serve(port=0, drivers=[
        AscomDriver("Telescope", "telescope", "Fake.Scope", "Fake Scope", 0)])
    holder["port"] = srv.server_address[1]
    yield holder
    srv.com_host.close()
    srv.shutdown()


@pytest.mark.parametrize("error,expected", [
    pytest.param(_dispatch_exception(0x8004040B, "not tracking"), 0x40B,
                 id="not-tracking"),
    pytest.param(_dispatch_exception(0x80040408, "mount is parked"), 0x408,
                 id="parked"),
    pytest.param(_dispatch_exception(0x80040401, "rate out of range"), 0x401,
                 id="out-of-range"),
    pytest.param(_dispatch_exception(0x80040407, "serial link down"), 0x407,
                 id="link-down"),
    pytest.param(_dispatch_exception(0x80040400, "no such property"), 0x400,
                 id="really-not-implemented"),
])
async def test_the_alpaca_client_sees_the_drivers_error_number(
        running_scope, error, expected):
    """Over the wire, through the real client: the AlpacaReplyError carries the
    number the driver raised, and its message is the driver's text.

    MUTANT M1 (see above): RED (observed) on the four cases that expect a
    number other than 0x400, ``assert 1024 == 1035`` and the like.
    """
    running_scope["error"] = error
    conn = AlpacaConnection("127.0.0.1", running_scope["port"])
    try:
        await conn.put("telescope", 0, "connected", Connected=True)
        with pytest.raises(AlpacaReplyError) as info:
            await conn.put("telescope", 0, "tracking", Tracking=True)
    finally:
        await conn.close()
    assert info.value.http_status == 200
    assert info.value.error_number == expected
    assert str(error) == str(info.value)


async def test_a_sync_on_a_parked_comhost_mount_reads_as_parked(running_scope):
    """The reason #862 keyed its sync read-back on 0x408: a COM mount that is
    parked now reaches ``AlpacaTelescope.sync`` as InvalidWhileParked, so the
    refusal says "parked" (SYNC_PARKED_REASON), not the generic driver refusal
    it fell into while every COM exception was 0x400.

    MUTANT M1 (see above): RED (observed), the reason is
    SYNC_REFUSED_BY_DRIVER_REASON, not SYNC_PARKED_REASON.
    """
    running_scope["error"] = _dispatch_exception(0x80040408, "mount is parked")
    conn = AlpacaConnection("127.0.0.1", running_scope["port"])
    tel = AlpacaTelescope(conn, 0, "Fictional Scope")
    try:
        await conn.put("telescope", 0, "connected", Connected=True)
        with pytest.raises(SyncRefused) as info:
            await tel.sync(10.0, 42.5)
    finally:
        await conn.close()
    assert info.value.reason == SYNC_PARKED_REASON
