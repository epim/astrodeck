# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#967: ``Hub.reconnect_role`` could not reconnect an Alpaca-class role that came
up through a profile.

THE DEFECT. ``reconnect_role`` rebuilds an Alpaca device from the recorded
host/port/type/number. Only the direct single-role path
(``connect_alpaca_device``) writes those. The profile path
(``_apply_connect_result``) records the device's backend label and nothing else,
and an Alpaca device's label is ``"alpaca"`` - so the record said "rebuild me
from my address" and carried no address. ``reconnect_role`` indexed
``info["host"]``, the ``KeyError`` was swallowed by its own ``except``, and it
logged ``reconnect <role> failed: 'host'`` and returned False without ever
asking the device to connect. Every Alpaca role of a profile-activated rig was
therefore beyond the engine's reconnect gate: the native rig of this product,
and every ``ascom-local`` (comhost) role, since those are the same Alpaca
device classes pointed at the loopback port.

THE FIX. A record without the fields the rebuild needs is not a record the
rebuild can replay, so such a role is re-opened in place like any other native
device: the device object disconnects and connects itself. That is also the
right shape for a profile-owned device - the profile's session holds the
connection, and the guider (for a guide camera) holds the device object, so a
replacement object would strand both.

These tests use the REAL Alpaca device classes, the real native backend and the
real profile path, against an in-process Alpaca server that keeps the
server-side ``Connected`` state per device and can be taken down.
"""
from __future__ import annotations

import sys

import httpx
import pytest

import astrodeck.comhost.handlers_telescope  # noqa: F401 (registers the handlers)
import astrodeck.comhost.server as comhost_server
import astrodeck.devices.backends  # noqa: F401 (self-registers native, ascom-local)
import astrodeck.hub as hub_module
from astrodeck.comhost.device import ComDevice
from astrodeck.config import ConfigStore
from astrodeck.devices import alpaca
from astrodeck.devices.ascom_registry import AscomDriver
from astrodeck.devices.backend import BACKENDS
from astrodeck.hub import Hub
from astrodeck.profiles import Profile, ProfileDevice, ProfileLibrary

pytestmark = pytest.mark.asyncio

HOST = "alpaca.test"
PORT = 11111

#: What the in-process server answers per property. Anything not listed reads 0,
#: which every property the focuser and the wheel read at connect accepts.
_ANSWERS = {"maxstep": 100000, "stepsize": 4.0, "absolute": True, "names": ["L"]}


class FakeAlpacaServer:
    """One Alpaca server for every connection the code under test opens.

    Keeps the server-side ``Connected`` flag per device, and records every
    ``Connected`` PUT in order - including one that arrived while the server was
    down, because an attempt that never reached the wire is exactly what the
    defect looked like from the outside."""

    def __init__(self) -> None:
        self.up = True
        self.connected: dict[tuple, bool] = {}
        self.connect_puts: list[tuple[tuple, bool]] = []
        self.connections_opened = 0

    def connection_class(self):
        server = self

        class FakeAlpacaConnection:
            def __init__(self, host, port):
                self.host = host
                self.port = port
                self.base = f"http://{host}:{port}/api/v1"
                server.connections_opened += 1

            def _reach(self):
                if not server.up:
                    raise httpx.ConnectError("connection refused")

            async def get(self, dev_type, dev_num, method, **params):
                self._reach()
                return _ANSWERS.get(method, 0)

            async def put(self, dev_type, dev_num, method, **params):
                key = (self.host, self.port, dev_type, dev_num)
                if method == "connected":
                    server.connect_puts.append((key, bool(params["Connected"])))
                self._reach()
                if method == "connected":
                    server.connected[key] = bool(params["Connected"])
                return None

            async def close(self):
                return None

        return FakeAlpacaConnection


@pytest.fixture
def server(monkeypatch):
    srv = FakeAlpacaServer()
    monkeypatch.setattr(alpaca, "AlpacaConnection", srv.connection_class())
    return srv


@pytest.fixture
async def hub_env(tmp_path, monkeypatch):
    """An isolated Hub on a temp config store and temp profile library."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    lib = ProfileLibrary(directory=tmp_path / "profiles")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(hub_module, "profiles", lib)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    h = Hub()
    try:
        yield h, lib
    finally:
        await h.disconnect_all()


def _profile(role: str, dev_num: int = 2) -> Profile:
    """A profile with ONE Alpaca device row. ``primary_backend="none"`` is the
    Equipment surface's explicit-only rig: only the assigned role is asked for."""
    return Profile(name="alpaca rig", primary_backend="none", devices=[
        ProfileDevice(role=role, backend="alpaca", host=HOST, port=PORT,
                      dev_type=role, dev_num=dev_num, name=f"Test {role}")])


def _key(role: str, dev_num: int = 2) -> tuple:
    return (HOST, PORT, role, dev_num)


# ------------------------------------------------------------- profile-connected

@pytest.mark.parametrize("role", ["focuser", "filterwheel"])
async def test_a_profile_connected_alpaca_role_reconnects(hub_env, server, role):
    """The defect: ``reconnect_role`` returned False without asking the device to
    connect. Now the device that dropped is brought back, and the server agrees."""
    h, lib = hub_env
    prof = _profile(role)
    lib.save(prof)
    await h.connect_profile_id(prof.id)
    dev = h.devices[role]
    assert dev.connected and server.connected[_key(role)] is True, "precondition"

    server.up = False
    dev.connected = False           # what a failed poll leaves behind
    server.connected[_key(role)] = False
    puts_before = len(server.connect_puts)
    server.up = True                # the far end is back

    assert await h.reconnect_role(role) is True
    assert dev.connected is True
    assert server.connected[_key(role)] is True, (
        "the server must end with the device connected, not just the client "
        "believing it is")
    assert len(server.connect_puts) > puts_before, (
        "a reconnect that never asks the device to connect is the defect")


async def test_a_profile_role_is_reopened_in_place_not_replaced(hub_env, server):
    """The profile owns the device: its session holds the connection and a guider
    holds the object. A reconnect must hand back the SAME device, on the SAME
    connection, and open no new connection beside the profile's."""
    h, lib = hub_env
    prof = _profile("focuser")
    lib.save(prof)
    await h.connect_profile_id(prof.id)
    dev = h.devices["focuser"]
    conn = dev.conn
    opened = server.connections_opened
    dev.connected = False

    assert await h.reconnect_role("focuser") is True

    assert h.devices["focuser"] is dev
    assert dev.conn is conn
    assert server.connections_opened == opened, (
        "a single-role reconnect must not open a connection of its own beside "
        "the profile's")


async def test_a_profile_role_whose_server_is_still_down_reports_failure(
        hub_env, server):
    """An attempt that is made and fails is False, with the attempt on record -
    not a fabricated success, and not the silent no-attempt of the defect."""
    h, lib = hub_env
    prof = _profile("focuser")
    lib.save(prof)
    await h.connect_profile_id(prof.id)
    dev = h.devices["focuser"]
    dev.connected = False
    server.up = False
    puts_before = len(server.connect_puts)

    assert await h.reconnect_role("focuser") is False

    assert dev.connected is False
    attempts = server.connect_puts[puts_before:]
    assert (_key("focuser"), True) in attempts, (
        "the reconnect must have tried to connect, got %r" % (attempts,))


# ------------------------------------------------------------ directly connected

async def test_a_directly_connected_alpaca_role_is_still_rebuilt_from_its_address(
        hub_env, server):
    """The other half, which must not regress: a role connected by
    ``connect_alpaca_device`` records its address and is rebuilt from it -
    a NEW device on a NEW connection to the recorded endpoint.

    What the server holds AFTERWARDS is deliberately not asserted: the rebuild
    disconnects the replaced object once the new one is connected, and both
    address the same remote device. That is a separate defect, outside #967."""
    h, _lib = hub_env
    await h.connect_alpaca_device("focuser", HOST, PORT, "focuser", 2, "Test focuser")
    old = h.devices["focuser"]
    assert h._last_connect["focuser"]["host"] == HOST, "precondition"
    opened = server.connections_opened
    old.connected = False
    puts_before = len(server.connect_puts)

    assert await h.reconnect_role("focuser") is True

    new = h.devices["focuser"]
    assert new is not old, "a direct role is rebuilt, not re-opened in place"
    assert new.connected is True
    assert (new.host, new.port, new.dev_type, new.dev_num) == (
        HOST, PORT, "focuser", 2)
    assert server.connections_opened == opened + 1
    assert (_key("focuser"), True) in server.connect_puts[puts_before:]


# ------------------------------------------------------------------- ascom-local

@pytest.mark.skipif(sys.platform != "win32",
                    reason="ascom-local registers only on Windows")
async def test_a_profile_connected_ascom_local_role_reconnects(
        hub_env, monkeypatch):
    """The issue's other named case: an ``ascom-local`` role is the same Alpaca
    device class pointed at the comhost's loopback port, and came up through the
    same profile path. Driven against a real in-process comhost whose fake COM
    mount can lose its connection."""

    class _Scope:
        Connected = False
        RightAscension = 7.0
        Declination = 41.0
        Slewing = False
        AtPark = False
        CanPulseGuide = True

    scope = _Scope()
    monkeypatch.setattr(comhost_server, "_make_com_device", lambda progid: ComDevice(
        progid, create=lambda pid: scope))
    srv = comhost_server.serve(port=0, drivers=[
        AscomDriver("Telescope", "telescope", "ASCOM.Simulator.Telescope", "S", 0)])
    port = srv.server_address[1]

    class _Manager:
        async def ensure(self):
            return port

    import astrodeck.comhost.manager as manager_mod
    monkeypatch.setattr(manager_mod, "get_manager", lambda: _Manager())
    assert "ascom-local" in BACKENDS

    h, lib = hub_env
    prof = Profile(name="com rig", primary_backend="none", devices=[
        ProfileDevice(role="telescope", backend="ascom-local",
                      dev_type="telescope", dev_num=0, name="Sim scope")])
    lib.save(prof)
    try:
        await h.connect_profile_id(prof.id)
        dev = h.devices["telescope"]
        assert dev.connected and scope.Connected, "precondition"

        scope.Connected = False     # the COM driver let go of the mount
        dev.connected = False

        assert await h.reconnect_role("telescope") is True
        assert dev.connected is True
        assert scope.Connected is True, (
            "the COM driver itself must have been connected again")
    finally:
        await h.disconnect_all()
        srv.com_host.close()
        srv.shutdown()
