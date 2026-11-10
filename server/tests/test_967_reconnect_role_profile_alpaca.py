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

import asyncio
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
from astrodeck.devices.alpaca import AlpacaReplyError
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
        #: Awaited with the method name before every GET is answered; a test
        #: sets it to land another coroutine at an exact point of a connect.
        self.on_get = None

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
                if server.on_get is not None:
                    await server.on_get(method)
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

    The server must end holding the device connected: the replaced object
    addresses the same remote device, and its disconnect used to undo the
    rebuild's connect (#987, #991: test_987_991_alpaca_rebuild_ends_connected)."""
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
    assert server.connected[_key("focuser")] is True


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


# --------------------------------------------------- the status poll (#1173)

async def test_a_status_poll_inside_a_reconnect_does_not_undo_it(hub_env, server):
    """#1173: the poll read the device between the reconnect's two halves, got
    NotConnected, and set ``connected`` False under the connect that was still
    running. The interleaving is forced, not timed: the server holds the
    focuser's first connect probe, the poll runs to completion inside that
    gap, and any ``position`` read it issues is answered NotConnected."""
    h, lib = hub_env
    prof = _profile("focuser")
    lib.save(prof)
    await h.connect_profile_id(prof.id)
    dev = h.devices["focuser"]

    in_gap = asyncio.Event()
    release = asyncio.Event()
    position_reads: list[str] = []

    async def on_get(method):
        if method == "maxstep" and not in_gap.is_set():
            in_gap.set()
            await release.wait()
        if method == "position" and in_gap.is_set():
            position_reads.append(method)
            raise AlpacaReplyError("not connected", http_status=200,
                                   error_number=0x407)

    server.on_get = on_get
    reconnect = asyncio.create_task(h.reconnect_role("focuser"))
    await asyncio.wait_for(in_gap.wait(), 5)

    status = await asyncio.wait_for(h.poll_status(), 5)
    assert "focuser" not in status, (
        "the poll must leave a role that is mid-reconnect out of the frame")
    assert position_reads == [], "the poll read a device that is mid-reconnect"

    release.set()
    assert await asyncio.wait_for(reconnect, 5) is True
    assert dev.connected is True


async def test_a_status_poll_still_reads_the_other_roles_during_a_reconnect(
        hub_env, server):
    """The hold is per role: a reconnect of the focuser must not blank the rest
    of the status frame."""
    h, lib = hub_env
    prof = Profile(name="two roles", primary_backend="none", devices=[
        ProfileDevice(role=r, backend="alpaca", host=HOST, port=PORT,
                      dev_type=r, dev_num=2, name=f"Test {r}")
        for r in ("focuser", "filterwheel")])
    lib.save(prof)
    await h.connect_profile_id(prof.id)

    in_gap = asyncio.Event()
    release = asyncio.Event()

    async def on_get(method):
        if method == "maxstep" and not in_gap.is_set():
            in_gap.set()
            await release.wait()

    server.on_get = on_get
    reconnect = asyncio.create_task(h.reconnect_role("focuser"))
    await asyncio.wait_for(in_gap.wait(), 5)
    try:
        status = await asyncio.wait_for(h.poll_status(), 5)
        assert "filterwheel" in status
        assert "focuser" not in status
    finally:
        release.set()
        await asyncio.wait_for(reconnect, 5)


async def test_a_reconnect_waits_for_a_status_read_already_in_flight(
        hub_env, server):
    """A read the poll started before the reconnect did can still answer
    NotConnected in the gap, so the reconnect lets it finish first."""
    h, lib = hub_env
    prof = _profile("focuser")
    lib.save(prof)
    await h.connect_profile_id(prof.id)
    dev = h.devices["focuser"]

    probe = asyncio.get_running_loop().create_future()
    h._status_reads = {"focuser.position": (dev, probe)}
    disconnects: list[int] = []
    real_disconnect = dev.disconnect

    async def spy_disconnect():
        disconnects.append(1)
        await real_disconnect()

    dev.disconnect = spy_disconnect
    reconnect = asyncio.create_task(h.reconnect_role("focuser"))
    await asyncio.sleep(0.05)
    assert disconnects == [], "the reconnect began under a read still in flight"

    probe.set_result(1)
    assert await asyncio.wait_for(reconnect, 5) is True
    assert disconnects == [1]


async def test_a_reconnect_that_hangs_stops_holding_the_poll_off(
        hub_env, server, monkeypatch):
    """The bound: the hold lasts ``RECONNECT_STATUS_HOLD_S`` and no longer, so a
    reconnect that never returns cannot blind the poll for ever."""
    h, lib = hub_env
    prof = _profile("focuser")
    lib.save(prof)
    await h.connect_profile_id(prof.id)
    monkeypatch.setattr(hub_module, "RECONNECT_STATUS_HOLD_S", 0.2)

    in_gap = asyncio.Event()

    async def on_get(method):
        if method == "maxstep" and not in_gap.is_set():
            in_gap.set()
            await asyncio.Event().wait()        # never answers

    server.on_get = on_get
    reconnect = asyncio.create_task(h.reconnect_role("focuser"))
    try:
        await asyncio.wait_for(in_gap.wait(), 5)
        server.on_get = None
        assert "focuser" not in await asyncio.wait_for(h.poll_status(), 5)
        await asyncio.sleep(0.3)
        assert "focuser" in await asyncio.wait_for(h.poll_status(), 5)
    finally:
        reconnect.cancel()
        await asyncio.gather(reconnect, return_exceptions=True)
