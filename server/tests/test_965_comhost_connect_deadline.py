# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#965: ``Connected=true`` on the comhost is bounded by the same deadline as
every other COM call, and only the deadline fault-evicts.

#965 was filed against an intermediate state of the #937/#938 work, in which
``ComDevice.connect`` waited with ``fut.result(timeout=...)`` and so never
raised ``ComTimeoutError``. The merged code waits with ``ComDevice._await``
(shared with ``submit``), and the wedged-connect half is covered by
``test_937_comhost_not_connected.py``
(``test_a_wedged_connect_is_evicted_so_the_retry_builds_a_fresh_device``) and
``test_938_comhost_driver_timeout.py``
(``test_a_connect_that_wedges_is_the_deadline``).

What neither of them says through the HOST is the other case the issue lists: a
TimeoutError the driver raises from inside ``Connected = True`` is a driver
error, so the answer is HTTP 200 / 0x500 with the driver's own words and the
slot is kept (the device-level twin, ``test_connect_hands_back_a_drivers_
timeout_as_itself``, stops at ``ComDevice``). This file pins it at the host.

No comtypes and no hardware: a fake COM object behind the real ComDevice.
"""
from __future__ import annotations

import errno
import threading
from concurrent.futures import TimeoutError as FuturesTimeout

import pytest

import astrodeck.comhost.handlers_telescope as handlers_telescope
import astrodeck.comhost.server as server
from astrodeck.comhost.device import ComDevice
from astrodeck.devices.ascom_registry import AscomDriver


class _DriverTimeout(TimeoutError):
    """A driver library's own subclass of the builtin."""


DRIVER_TIMEOUTS = [
    pytest.param(lambda: TimeoutError("link timed out while opening"),
                 id="TimeoutError"),
    pytest.param(lambda: _DriverTimeout("driver library timeout"),
                 id="subclass"),
    pytest.param(lambda: OSError(errno.ETIMEDOUT, "port timed out"),
                 id="OSError-ETIMEDOUT"),
    pytest.param(lambda: FuturesTimeout("handler waited on its own future"),
                 id="concurrent.futures.TimeoutError"),
]


class _Scope:
    """A telescope whose ``Connected = True`` raises ``error`` until it is
    cleared, the way a driver whose serial link timed out while opening does."""

    RightAscension = 10.0
    Tracking = True

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


@pytest.mark.parametrize("make_error", DRIVER_TIMEOUTS)
def test_a_drivers_timeout_during_connect_is_a_driver_error_and_the_slot_stays(
        monkeypatch, make_error):
    """Through the host: HTTP 200 with 0x500 and the driver's own words, the
    slot kept (not fault-evicted), and a retry of ``Connected=true`` connects on
    the SAME device and STA thread once the driver stops failing.

    MUTANT M5 "catch the class": ``ComDevice.connect`` made ``try:
    fut.result(timeout=self._timeout_s) except TimeoutError: raise
    ComTimeoutError(...)`` (the pre-#938 wait): RED (observed), all four
    cases, HTTP 500 "exceeded" and the slot dropped.
    """
    handlers_telescope.register()  # undo any sibling's DEVICE_API mutation
    error = make_error()
    scope = _Scope(error)
    monkeypatch.setattr(
        server, "_make_com_device",
        lambda progid: ComDevice(progid, create=lambda pid: scope,
                                 timeout_s=2.0))
    host = server.ComHost(drivers=[
        AscomDriver("Telescope", "telescope", "Fake.Scope", "Fake Scope", 0)])
    try:
        status, body, _ = host.handle(
            "put", "telescope", 0, "connected", {"Connected": ["true"]})
        assert status == 200
        assert body["ErrorNumber"] == 0x500
        assert body["ErrorMessage"] == str(error)
        assert "exceeded" not in body["ErrorMessage"]
        dev = host._devices.get(("telescope", 0))
        assert dev is not None  # not evicted
        assert dev.connected is False

        scope.error = None
        status, body, _ = host.handle(
            "put", "telescope", 0, "connected", {"Connected": ["true"]})
        assert (status, body["ErrorNumber"]) == (200, 0)
        assert host._devices[("telescope", 0)] is dev
        status, body, _ = host.handle("get", "telescope", 0, "rightascension", {})
        assert (status, body["ErrorNumber"], body["Value"]) == (200, 0, 10.0)
        assert len(set(scope.threads)) == 1  # the same STA thread, never replaced
    finally:
        host.close()
