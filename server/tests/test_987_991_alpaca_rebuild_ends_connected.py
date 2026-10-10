# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#987 / #991: rebuilding an Alpaca role must leave the device connected on
the server.

THE DEFECT. ``Hub._connect_alpaca_device_unlocked`` builds the new device,
connects it (PUT ``Connected=True``), and only then disconnects the object it
replaces (PUT ``Connected=False``). An Alpaca server keeps ONE ``Connected``
state per device, so when the replaced object addressed the same device the
second PUT undid the first: ``reconnect_role`` answered True, the engine's
reconnect gate wrote "back after 1 attempt", and the new object's first read
answered NotConnected (0x407).

WP-188 (#966) closed the common case by not disconnecting an old object whose
host, port, type and number equal the new one's. That is a string comparison,
so two SPELLINGS of one endpoint ('localhost' and '127.0.0.1', a LAN name and
its address) still fell back to the unfixed order. The rebuild now also sends
the new device's connect AFTER the old one's disconnect, so whatever the
comparison could not see, the last word on the server's slot is the new
device's.

A replacement whose connect then FAILS leaves the hub as it was: the old
device, released a moment earlier, is connected again, and the session opened
for the replacement is closed. The reorder alone made that case worse than the
unfixed order (which raised before touching anything): the hub kept an old
device the server no longer held connected.

These tests use the real Alpaca device classes, the real native backend and the
real hub, against an in-process Alpaca server that keeps the server-side
``Connected`` flag per device slot and counts every ``Connected`` PUT. The
server is reachable under more than one host spelling, as a real one is.

Each test names the mutant of ``hub.py`` it was shown red under (applied to a
byte copy, restored from that copy and md5-checked):

* M1 "no skip": ``replaced_same`` made ``False`` (the same device is
  disconnected again, as before WP-188).
* M2 "connect first": the new device's final ``connect()`` moved back above the
  old device's disconnect (the unfixed order).
* M3 "both": M1 and M2 together, which is the code before WP-188.
* M4 "never disconnect": ``replaced_same`` made ``True``.
* M5 "swallowed": the failure of the final ``connect()`` is swallowed.
* M6 "no restore": the old device is not connected again after the final
  ``connect()`` fails.
* M7 "session leaked": the replacement's session is not closed after the final
  ``connect()`` fails.
* M8 "cancel not caught": the failure handler catches ``Exception`` only, so a
  cancellation skips the restore and the close.
* M9 "restore error escapes": the old device's reconnect is not guarded, so its
  failure replaces the error being raised.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore
from astrodeck.devices import alpaca
from astrodeck.hub import Hub

HOST = "alpaca.test"
ALIAS = "alpaca-alias.test"
PORT = 11111

#: What the in-process server answers per property. Anything not listed reads 0,
#: which every property the focuser reads at connect accepts.
_ANSWERS = {"maxstep": 100000, "stepsize": 4.0, "absolute": True}


class FakeAlpacaServer:
    """One Alpaca server, reachable as ``HOST`` and as ``ALIAS``.

    A device slot is ``(dev_type, dev_num)``: the server holds one ``Connected``
    flag per slot whatever name the client reached it by, which is the property
    the defect depends on. Every ``Connected`` PUT is recorded, in order, per
    slot."""

    def __init__(self) -> None:
        self.names = {HOST, ALIAS}
        self.connected: dict[tuple, bool] = {}
        self.puts: dict[tuple, list[bool]] = {}
        #: slot -> the Nth ``Connected=True`` PUT attempts to it (1-based,
        #: refused ones counted) that the server refuses, as if it had gone
        #: away between two PUTs.
        self.refuse_true_put: dict[tuple, set[int]] = {}
        #: slot -> the Nth ``Connected=True`` PUT attempt that never answers
        #: (the caller has to give up on it); ``stalled`` is set when it starts.
        self.stall_true_put: dict[tuple, int] = {}
        self.stalled = asyncio.Event()
        self.connections_opened = 0
        #: every connection that was ``close()``d, in order
        self.closed_conns: list = []

    def connection_class(self):
        server = self

        class FakeAlpacaConnection:
            def __init__(self, host, port):
                self.host = host
                self.port = port
                self.base = f"http://{host}:{port}/api/v1"
                server.connections_opened += 1

            def _reach(self):
                if self.host.lower() not in server.names or self.port != PORT:
                    raise httpx.ConnectError("connection refused")

            async def get(self, dev_type, dev_num, method, **params):
                self._reach()
                if method == "connected":
                    return server.connected.get((dev_type, dev_num), False)
                return _ANSWERS.get(method, 0)

            async def put(self, dev_type, dev_num, method, **params):
                self._reach()
                if method == "connected":
                    slot = (dev_type, dev_num)
                    value = bool(params["Connected"])
                    server.puts.setdefault(slot, []).append(value)
                    if value:
                        attempt = server.puts[slot].count(True)
                        if attempt in server.refuse_true_put.get(slot, ()):
                            raise httpx.ConnectError(
                                f"connection reset on Connected=True #{attempt}")
                        if attempt == server.stall_true_put.get(slot):
                            server.stalled.set()
                            await asyncio.Event().wait()     # until cancelled
                    server.connected[slot] = value
                return None

            async def close(self):
                server.closed_conns.append(self)

        return FakeAlpacaConnection


@pytest.fixture
def server(monkeypatch):
    srv = FakeAlpacaServer()
    monkeypatch.setattr(alpaca, "AlpacaConnection", srv.connection_class())
    return srv


@pytest.fixture
async def hub(tmp_path, monkeypatch):
    """An isolated Hub on a temp config store, with the status loop off: it
    reads devices on its own clock and is not what is under test."""
    import astrodeck.config as config_mod
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(Hub, "ensure_status_poller", lambda self: None)
    h = Hub()
    try:
        yield h
    finally:
        await h.disconnect_all()


FOCUSER_2 = ("focuser", 2)
FOCUSER_3 = ("focuser", 3)


async def _connect(hub: Hub, host: str = HOST, dev_num: int = 2) -> None:
    await hub.connect_alpaca_device("focuser", host, PORT, "focuser", dev_num,
                                    f"Test focuser {dev_num}")


# --------------------------------------------------------------------------
# #987: reconnect_role
# --------------------------------------------------------------------------
async def test_reconnect_role_leaves_the_device_connected_and_never_disconnects_it(
        hub, server):
    """The recovery the reconnect gate runs: the far end lost the device, the
    hub rebuilds the role from its record, and the SERVER ends with the device
    connected. Because the rebuilt role addresses the same device, the old
    object's disconnect is not sent at all: Connected=False reaches a real
    driver, and for a camera that can mean the cooler.

    MUTANT M1 "no skip": RED (observed), a ``False`` PUT is on the wire.
    MUTANT M3 "both": RED (observed), the server ends with the device
    disconnected, ``assert False is True``.
    """
    await _connect(hub)
    old = hub.devices["focuser"]
    server.connected[FOCUSER_2] = False      # the far end lost it
    old.connected = False
    sent = len(server.puts[FOCUSER_2])

    assert await hub.reconnect_role("focuser") is True

    new = hub.devices["focuser"]
    assert new is not old, "a direct Alpaca role is rebuilt, not re-opened"
    assert new.connected is True
    assert server.connected[FOCUSER_2] is True, (
        "reconnect_role reported success while the server holds the device "
        "disconnected")
    assert False not in server.puts[FOCUSER_2][sent:], (
        "the rebuilt role must not disconnect the device it just connected, "
        "got %r" % (server.puts[FOCUSER_2][sent:],))
    assert new.conn is not old.conn, "rebuilt on a connection of its own"


async def test_reconnect_role_after_a_reconnect_still_ends_connected(hub, server):
    """The gate can pass more than once in a night. The record the first rebuild
    wrote must itself be one the second can replay.

    MUTANT M3 "both": RED (observed), the second rebuild leaves the server with
    the device disconnected."""
    await _connect(hub)
    for _ in range(2):
        hub.devices["focuser"].connected = False
        assert await hub.reconnect_role("focuser") is True
        assert server.connected[FOCUSER_2] is True


# --------------------------------------------------------------------------
# #991: the direct single-role rebuild
# --------------------------------------------------------------------------
async def test_connecting_the_same_alpaca_device_again_keeps_it_connected(
        hub, server):
    """The direct path: ``connect_alpaca_device`` for the device a role already
    holds (the UI's connect pressed again). The device is connected afterwards,
    on the server and in the hub.

    MUTANT M3 "both": RED (observed), ``assert False is True`` on the server."""
    await _connect(hub)

    await _connect(hub)

    assert hub.devices["focuser"].connected is True
    assert server.connected[FOCUSER_2] is True
    assert server.puts[FOCUSER_2][-1] is True, (
        "the last Connected PUT the server saw must be the connect, got %r"
        % (server.puts[FOCUSER_2],))


@pytest.mark.parametrize("again", [ALIAS, HOST.upper()],
                         ids=["another-name-for-the-server", "host-in-capitals"])
async def test_the_same_device_under_another_host_spelling_ends_connected(
        hub, server, again):
    """The case the comparison cannot see. 'alpaca-alias.test' is another name
    for the server 'alpaca.test' (as 'localhost' is for '127.0.0.1'): one
    device slot, two spellings. The old object's disconnect reaches the slot
    the new one holds, so the connect has to come after it.

    The capitals case is the control: lower-casing already made that one
    'same', so it ends connected by the skip, with no disconnect.

    MUTANT M2 "connect first": RED (observed) for the other name, the server
    ends with the device disconnected while the new object reports
    connected=True; the capitals control stays green under it."""
    await _connect(hub, HOST)
    old = hub.devices["focuser"]

    await _connect(hub, again)

    new = hub.devices["focuser"]
    assert new is not old
    assert new.connected is True
    assert server.connected[FOCUSER_2] is True, (
        "the hub says connected while the server holds the device "
        "disconnected; Connected PUTs were %r" % (server.puts[FOCUSER_2],))
    assert server.puts[FOCUSER_2][-1] is True


# --------------------------------------------------------------------------
# The other half: a different device is still released
# --------------------------------------------------------------------------
async def test_moving_a_role_to_another_device_releases_the_old_one_only(
        hub, server):
    """Moving the role from focuser #2 to focuser #3 on the same server: #2 is
    disconnected, #3 ends connected. The fix must not turn into 'never release
    a replaced device'.

    MUTANT M4 "never disconnect": RED (observed), focuser #2 is still
    connected on the server."""
    await _connect(hub, dev_num=2)
    assert server.connected[FOCUSER_2] is True

    await _connect(hub, dev_num=3)

    assert server.connected[FOCUSER_3] is True
    assert server.connected[FOCUSER_2] is False
    assert server.puts[FOCUSER_2][-1] is False
    assert hub.devices["focuser"].dev_num == 3


# --------------------------------------------------------------------------
# A rebuild that fails part way leaves a coherent hub
# --------------------------------------------------------------------------
async def test_a_rebuild_whose_final_connect_fails_leaves_the_hub_as_it_was(
        hub, server):
    """The server answers the first connect and drops before the second. The
    call raises; the role still holds the device it held AND that device is
    connected again (it was disconnected a moment earlier, to make room for a
    replacement that never took), the record that ``reconnect_role`` replays
    still names it, and the session opened for the replacement is closed, not
    leaked. The failure is raised, not reported as a success.

    MUTANT M5 "swallowed": the failure of the final ``dev.connect()`` is
    swallowed: RED (observed), the call returns and the role holds a device
    the server refused.
    MUTANT M6 "no restore": RED (observed), ``old.connected`` is False and the
    server holds focuser #2 disconnected.
    MUTANT M7 "session leaked": RED (observed), no connection was closed."""
    await _connect(hub, dev_num=2)
    old = hub.devices["focuser"]
    record = dict(hub._last_connect["focuser"])
    server.refuse_true_put[FOCUSER_3] = {2}   # get_device's connect lands, the next does not

    with pytest.raises(httpx.ConnectError):
        await _connect(hub, dev_num=3)

    assert hub.devices["focuser"] is old
    assert hub._last_connect["focuser"] == record
    assert old.connected is True
    assert server.connected[FOCUSER_2] is True, (
        "the hub still holds focuser #2 but the server has it disconnected; "
        "Connected PUTs were %r" % (server.puts[FOCUSER_2],))
    assert server.puts[FOCUSER_2][-1] is True
    assert len(server.closed_conns) == 1, (
        "the replacement's connection must be closed, got %d closed"
        % len(server.closed_conns))
    assert old.conn not in server.closed_conns, "the held device's own connection stays open"


async def test_a_failed_rebuild_under_another_host_spelling_still_ends_connected(
        hub, server):
    """The alias case, failing: both objects address ONE server slot, the
    comparison reads them as two devices, so the old object is disconnected
    before the replacement connects. If that connect fails, the slot is the old
    device's again rather than left disconnected under a hub that still holds
    it.

    MUTANT M6 "no restore": RED (observed), the server ends with the slot
    disconnected."""
    await _connect(hub, HOST)                 # Connected=True attempts #1 and #2
    old = hub.devices["focuser"]
    server.refuse_true_put[FOCUSER_2] = {4}   # the alias's get_device is #3, its final connect #4

    with pytest.raises(httpx.ConnectError):
        await _connect(hub, ALIAS)

    assert hub.devices["focuser"] is old
    assert old.connected is True
    assert server.connected[FOCUSER_2] is True, (
        "the hub says connected while the server holds the device "
        "disconnected; Connected PUTs were %r" % (server.puts[FOCUSER_2],))
    assert server.puts[FOCUSER_2][-1] is True


async def test_a_rebuild_cancelled_during_its_final_connect_leaves_the_hub_as_it_was(
        hub, server):
    """The caller gives up on the call (a timeout, a closed request) while the
    replacement's second connect is in flight. A cancellation is not an
    ``Exception``, and the hub must come out the same: the old device
    connected again, the replacement's connection closed, the cancellation
    still delivered.

    MUTANT M8 "cancel not caught": RED (observed), the old device stays
    disconnected and nothing is closed."""
    await _connect(hub, dev_num=2)
    old = hub.devices["focuser"]
    server.stall_true_put[FOCUSER_3] = 2      # get_device's connect lands, the next never answers
    task = asyncio.ensure_future(_connect(hub, dev_num=3))
    await asyncio.wait_for(server.stalled.wait(), 5)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert hub.devices["focuser"] is old
    assert old.connected is True
    assert server.connected[FOCUSER_2] is True
    assert len(server.closed_conns) == 1
    assert old.conn not in server.closed_conns


async def test_a_failed_rebuild_whose_restore_also_fails_raises_the_original_error(
        hub, server):
    """The server is gone for good: the replacement's connect fails, and so
    does connecting the old device again. The caller gets the replacement's
    error (the one that says what was being attempted), not the restore's. The
    hub still holds the old device, honestly flagged disconnected so the
    reconnect gate sees it, and the replacement's connection is closed.

    MUTANT M9 "restore error escapes": RED (observed), the restore's error
    ('#3') is raised in place of the replacement's ('#2')."""
    await _connect(hub, dev_num=2)            # focuser #2 attempts #1 and #2
    old = hub.devices["focuser"]
    server.refuse_true_put[FOCUSER_3] = {2}
    server.refuse_true_put[FOCUSER_2] = {3}   # the restore is the third attempt

    with pytest.raises(httpx.ConnectError, match="Connected=True #2"):
        await _connect(hub, dev_num=3)

    assert hub.devices["focuser"] is old
    assert old.connected is False
    assert len(server.closed_conns) == 1
