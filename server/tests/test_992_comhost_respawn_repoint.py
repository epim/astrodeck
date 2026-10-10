# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#992: reconnecting an ``ascom-local`` device could not recover a dead comhost.

THE DEFECT. ``ComHostManager.ensure()`` respawns a dead comhost on a NEW
ephemeral port, and its only caller was ``AscomLocalBackend.open`` (profile
connect). Every ascom-local device held an ``AlpacaConnection`` built with the
port that was current when it connected. So when the comhost process died
mid-night, ``Hub.reconnect_role`` re-opened the device on the OLD port, the
``Connected=true`` PUT was refused, and the engine's reconnect gate gave up,
although activating the profile again would have worked.

THE FIX. The session's connection (``ComhostConnection``) treats a
``Connected=true`` PUT as a (re)connect: it asks the manager where the comhost
is now (``ensure()``, which respawns a dead one) and repoints before sending.
The devices read their address from the connection, so every device sharing it
follows and reports the address it really uses.

No hardware and no subprocess: the comhost is an in-process ``serve()`` behind a
real ``ComHostManager`` whose spawn hook starts one, and "the process died" is
the listening socket and every accepted connection closing plus ``poll()``
reporting an exit - what the manager and the clients see of a real crash. The fake COM objects are per comhost, so a respawn comes
up with no device connected, like the real thing.

Each test names the mutant of production code it was shown red under (applied to
a byte copy of the file, restored from that copy and md5-checked):

MUTANT M1 "never follow": ``ComhostConnection.put`` made to skip the manager
(``if False and ...``): RED (observed), 3 of 4 tests (the fourth is the
fixed-port default, green by design; the hub test runs on Windows only).
MUTANT M2 "ask but do not repoint": the ``self.repoint(port)`` call replaced by
``pass``: RED (observed), the scenario test and the hub test.
MUTANT M3 "repoint without the port": ``AlpacaConnection.repoint`` no longer sets
``self.port`` (requests move, the address the devices report does not): RED
(observed), the scenario test and the hub test.
"""
from __future__ import annotations

import contextlib
import socket
import sys

import pytest

import astrodeck.comhost.handlers_telescope  # noqa: F401 (registers the handlers)
import astrodeck.comhost.manager as manager_mod
import astrodeck.comhost.server as server
import astrodeck.devices.backends  # noqa: F401 (self-registers native, ascom-local)
import astrodeck.hub as hub_module
from astrodeck.comhost.device import ComDevice
from astrodeck.comhost.manager import ComHostManager
from astrodeck.config import ConfigStore
from astrodeck.devices.ascom_registry import AscomDriver
from astrodeck.devices.backend import ConnSpec
from astrodeck.devices.backends.ascom_local import (AscomLocalBackend,
                                                    AscomLocalSession)
from astrodeck.hub import Hub
from astrodeck.profiles import Profile, ProfileDevice, ProfileLibrary

pytestmark = pytest.mark.asyncio

_DRIVERS = [
    AscomDriver("Telescope", "telescope", "Fake.Scope.0", "Scope 0", 0),
    AscomDriver("Telescope", "telescope", "Fake.Scope.1", "Scope 1", 1),
]


class _FakeScope:
    Connected = False
    RightAscension = 7.0
    Declination = 41.0
    Slewing = False
    AtPark = False
    CanPulseGuide = True


class _Comhosts:
    """The comhost process, as the manager sees it. Each ``spawn()`` starts a new
    in-process comhost on a port that differs from the last one, with fresh fake
    COM objects; ``crash()`` ends the running one the way a dead process ends:
    its port refuses connections and ``poll()`` reports an exit code."""

    def __init__(self, portfile: str):
        self.portfile = portfile
        self.spawns = 0
        self.ports: list[int] = []
        self.scopes: list[_FakeScope] = []
        self.fail_next_spawn = False
        self._srv = None
        self._accepted: list[socket.socket] = []
        self._exited = False

    def spawn(self):
        if self.fail_next_spawn:
            raise OSError("spawn refused")
        self.spawns += 1
        self._exited = False
        avoid = self.ports[-1] if self.ports else None
        held = []
        for _ in range(20):
            srv = server.serve(port=0, portfile=self.portfile, drivers=_DRIVERS)
            if srv.server_address[1] != avoid:
                break
            held.append(srv)             # keep the old number taken, bind again
        else:  # pragma: no cover - the OS handing out the same port 20 times
            raise AssertionError("could not get a port different from the last")
        for rejected in held:
            rejected.shutdown()
            rejected.server_close()
        self._srv = srv
        self.ports.append(srv.server_address[1])
        # Remember every connection the server accepts, so that a crash can end
        # them too: a client's keep-alive connection would otherwise keep being
        # served by the "dead" comhost's handler thread.
        accepted: list[socket.socket] = []
        self._accepted = accepted
        accept = srv.get_request

        def get_request():
            pair = accept()
            accepted.append(pair[0])
            return pair

        srv.get_request = get_request
        comhosts = self

        class _Proc:
            pid = 4242

            def poll(self):
                return 1 if comhosts._exited else None

            def terminate(self):
                comhosts.crash()

            def wait(self, timeout=None):
                return 0

        return _Proc()

    @property
    def port(self) -> int:
        return self.ports[-1]

    def crash(self) -> None:
        if self._srv is None or self._exited:
            return
        self._exited = True
        self._srv.com_host.close()
        self._srv.shutdown()
        self._srv.server_close()
        for sock in self._accepted:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                sock.close()


@pytest.fixture()
def comhosts(tmp_path, monkeypatch):
    portfile = str(tmp_path / "comhost.json")
    fake = _Comhosts(portfile)

    def make(progid):
        def create(pid):
            scope = _FakeScope()
            fake.scopes.append(scope)
            return scope
        return ComDevice(progid, create=create)

    monkeypatch.setattr(server, "_make_com_device", make)
    manager = ComHostManager(spawn=fake.spawn, portfile=portfile)
    monkeypatch.setattr(manager_mod, "get_manager", lambda: manager)
    try:
        yield fake
    finally:
        manager.stop()
        fake.crash()


def _conn(num: int) -> ConnSpec:
    return ConnSpec(backend="ascom-local", dev_type="telescope", dev_num=num,
                    role="telescope")


async def test_a_device_reconnecting_after_a_respawn_reaches_the_new_comhost(
        comhosts):
    """The defect, one level below the hub. Two devices share the session; the
    comhost dies and is respawned on another port. The first device to connect
    again must reach the new comhost, report the port it now uses, and be served
    by a COM object on the new host; the second must follow it without another
    respawn. Before that, with the comhost alive, a reconnect stays where it is.

    MUTANT M1: RED (observed), ``await dev.connect()`` raises
    ``httpx.ConnectError`` (the PUT went to the dead port).
    MUTANT M2: RED (observed), the same ``httpx.ConnectError``.
    MUTANT M3: RED (observed), ``AssertionError: the device must report the
    address it now uses`` / ``assert <dead port> == <new port>``.
    """
    session = await AscomLocalBackend().open(ConnSpec(backend="ascom-local"))
    try:
        dev = await session.get_device("telescope", _conn(0))
        # Two telescope drivers stand in for two roles on the one session.
        other = await session.get_device("rotator", ConnSpec(
            backend="ascom-local", dev_type="telescope", dev_num=1,
            role="rotator"))
        first_port = comhosts.port
        assert dev.conn is other.conn, "precondition: one shared connection"
        assert dev.connected and dev.port == first_port, "precondition"

        # The comhost is alive: a reconnect stays put, with no respawn.
        await dev.disconnect()
        await dev.connect()
        assert comhosts.spawns == 1 and dev.port == first_port == comhosts.port
        assert dev.connected is True

        comhosts.crash()                 # the process is gone mid-night

        with contextlib.suppress(Exception):   # what reconnect_role does first
            await dev.disconnect()
        await dev.connect()

        assert comhosts.spawns == 2, "ensure() must have respawned the comhost"
        assert comhosts.port != first_port, "precondition: it moved"
        assert dev.connected is True
        assert dev.port == comhosts.port, (
            "the device must report the address it now uses")
        assert dev.describe()["port"] == comhosts.port
        assert await dev.get_position() == (7.0, 41.0)
        assert comhosts.scopes[-1].Connected is True, (
            "the COM object on the NEW comhost must be the one connected")

        # The other device shares the connection: it already points at the new
        # comhost, and connecting it does not respawn anything.
        assert other.port == comhosts.port
        with contextlib.suppress(Exception):
            await other.disconnect()
        await other.connect()
        assert comhosts.spawns == 2, "the second device must not respawn again"
        assert other.connected is True
        assert await other.get_position() == (7.0, 41.0)
    finally:
        await session.close()


async def test_a_comhost_that_cannot_be_respawned_fails_the_reconnect(comhosts):
    """No fabricated success: when the manager cannot bring the comhost back, the
    connect raises and the device does not claim to be connected.

    MUTANT M1: RED (observed), ``httpx.ConnectError`` where ``OSError`` (the
    spawn failure) was expected - the manager was never asked. MUTANT M2: green
    by design (the manager IS asked and raises).
    """
    session = await AscomLocalBackend().open(ConnSpec(backend="ascom-local"))
    try:
        dev = await session.get_device("telescope", _conn(0))
        comhosts.crash()
        comhosts.fail_next_spawn = True

        with contextlib.suppress(Exception):
            await dev.disconnect()
        with pytest.raises(OSError, match="spawn refused"):
            await dev.connect()
        assert dev.connected is False
    finally:
        await session.close()


async def test_a_session_built_on_a_fixed_port_never_follows(comhosts):
    """``AscomLocalSession(port)`` with no manager behind it (how the backend
    tests build one) keeps the port it was given: no manager is consulted, so no
    comhost is spawned from under it.

    MUTANT M1/M2: green by design; this guards the default.
    """
    comhosts.spawn()                     # a comhost the test owns, no manager
    session = AscomLocalSession(port=comhosts.port)
    try:
        dev = await session.get_device("telescope", _conn(0))
        assert dev.connected and dev.port == comhosts.port
        assert comhosts.spawns == 1
    finally:
        await session.close()


# --------------------------------------------------------------- through the hub

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


@pytest.mark.skipif(sys.platform != "win32",
                    reason="ascom-local registers only on Windows")
async def test_hub_reconnect_role_recovers_a_dead_comhost(hub_env, comhosts):
    """The issue's scenario end to end: a rig connected through a profile on the
    ascom-local backend, the comhost process dies, and ``Hub.reconnect_role`` (the
    engine's reconnect gate) brings the role back on the respawned comhost.

    MUTANT M1: RED (observed), ``assert False is True``: the reconnect PUT went
    to the dead port and ``reconnect_role`` answered False.
    MUTANT M2: RED (observed), the same.
    MUTANT M3: RED (observed), ``assert (True is True and <dead> == <new>)``: the
    role came back but the device still reported the dead port.
    """
    h, lib = hub_env
    prof = Profile(name="com rig", primary_backend="none", devices=[
        ProfileDevice(role="telescope", backend="ascom-local",
                      dev_type="telescope", dev_num=0, name="Scope 0")])
    lib.save(prof)
    await h.connect_profile_id(prof.id)
    dev = h.devices["telescope"]
    first_port = comhosts.port
    assert dev.connected and dev.port == first_port, "precondition"

    comhosts.crash()

    assert await h.reconnect_role("telescope") is True
    assert comhosts.spawns == 2 and comhosts.port != first_port
    assert h.devices["telescope"] is dev, "re-opened in place, not replaced"
    assert dev.connected is True and dev.port == comhosts.port
    assert await dev.get_position() == (7.0, 41.0)
    assert comhosts.scopes[-1].Connected is True
