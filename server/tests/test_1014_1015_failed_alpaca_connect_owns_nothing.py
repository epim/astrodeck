# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1014 / #1015: an Alpaca connect that fails leaves nothing behind.

``Hub._connect_alpaca_device_unlocked`` builds a native session for the call,
and the session builds the device, whose ``get_device`` already sends
``Connected=True``. Until the call stores the session in ``_alpaca_sessions`` and
the device in ``devices``, nothing owns either.

#1014. A call that failed before that point never closed the session, so its
``httpx`` client and keep-alive sockets stayed open until garbage collection:
one client per failed attempt, and the engine's reconnect gate retries a role
whose far end is down. The final ``connect()`` failing had been closed by WP-203
(#987, #991); ``get_device`` refusing (the device's own connect, an unsupported
device type) and a cancel in the old device's disconnect still leaked.

#1015. A call whose final ``connect()`` failed left the replacement device
``Connected=True`` on the server with no owner. For a camera that can be a
cooler left engaged and a device busy to every other client. The replacement is
now disconnected, bounded and best effort, BEFORE the old device is connected
again: two spellings of one endpoint share one server slot, and the old
device's ``Connected=True`` has to be the last word on it. It is not
disconnected when the same device is replaced, because the slot is then the held
device's.

The server is the in-process fake of ``test_987_991_alpaca_rebuild_ends_connected``
(real device classes, real native backend, real hub), with two more knobs and a
record of every connection it opened, so "leaked" is a list and not a count.

Each test names the mutant of ``hub.py`` it was shown red under (applied to a
byte copy, restored from that copy and md5-checked):

* M1 "session not closed": the ``finally`` that closes an un-adopted session is
  removed (the code before this change closes only the final connect's).
* M2 "no release": ``_release_unowned_alpaca_device`` is not called.
* M3 "release after restore": the old device is connected again BEFORE the
  replacement is released.
* M4 "release the held device": the release is sent also when the same device
  is replaced.
* M5 "release unbounded": the release is awaited directly, with no bound.
* M6 "release error escapes": the release's own failure is raised in place of
  the connect's.
* M7 "release unsaid": a release that fails is not said.
* M8 "cancel eaten": the cancel of the caller that lands while the release
  runs is not raised after the restore, so the connect's own error wins (#235).
* M9 "release only on the connect's failure": a call ended before the final
  connect (a cancel in the old device's disconnect) does not release the
  replacement.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

import astrodeck.hub as hub_module
from test_987_991_alpaca_rebuild_ends_connected import (  # noqa: F401  (fixtures)
    ALIAS, FOCUSER_2, FOCUSER_3, HOST, PORT, FakeAlpacaServer, _connect, hub)
from astrodeck.devices import alpaca


class Server(FakeAlpacaServer):
    """The fake of #987/#991 plus: ``Connected=False`` PUTs that are refused or
    never answered, and every connection the hub opened, in order."""

    def __init__(self) -> None:
        super().__init__()
        #: slot -> the Nth ``Connected=False`` PUT attempt that is refused
        self.refuse_false_put: dict[tuple, set[int]] = {}
        #: slot -> the Nth ``Connected=False`` PUT attempt that never answers
        self.stall_false_put: dict[tuple, int] = {}
        self.stalled_false = asyncio.Event()
        self.false_attempts: dict[tuple, int] = {}
        self.conns: list = []

    def leaked(self) -> list:
        """Connections opened and not closed."""
        return [c for c in self.conns if c not in self.closed_conns]

    def connection_class(self):
        server = self
        base = super().connection_class()

        class Conn(base):
            def __init__(self, host, port):
                super().__init__(host, port)
                server.conns.append(self)

            async def put(self, dev_type, dev_num, method, **params):
                if method == "connected" and not params["Connected"]:
                    slot = (dev_type, dev_num)
                    n = server.false_attempts[slot] = server.false_attempts.get(slot, 0) + 1
                    if n in server.refuse_false_put.get(slot, ()):
                        raise httpx.ConnectError(f"refused Connected=False #{n}")
                    if n == server.stall_false_put.get(slot):
                        server.stalled_false.set()
                        await asyncio.Event().wait()         # until cancelled
                return await super().put(dev_type, dev_num, method, **params)

        return Conn


@pytest.fixture
def server(monkeypatch):
    srv = Server()
    monkeypatch.setattr(alpaca, "AlpacaConnection", srv.connection_class())
    return srv


# --------------------------------------------------------------------------
# #1014: the session of a failed attempt is closed
# --------------------------------------------------------------------------
async def test_attempts_whose_device_connect_fails_leak_no_connection(hub, server):
    """The far end refuses the device's connect (``get_device`` raises) on every
    attempt, as it does for as long as the server is down and the reconnect
    gate keeps retrying. Every attempt opens a connection; none may outlive its
    attempt, and the hub holds nothing.

    MUTANT M1 "session not closed": RED (observed), three connections opened
    and none closed."""
    server.refuse_true_put[FOCUSER_2] = {1, 2, 3}

    for _ in range(3):
        with pytest.raises(httpx.ConnectError):
            await _connect(hub)

    assert len(server.conns) == 3, "premise: one connection per attempt"
    assert server.leaked() == [], (
        "%d of 3 failed attempts left their connection open"
        % len(server.leaked()))
    assert "focuser" not in hub.devices
    assert hub._alpaca_sessions == {}


async def test_an_unsupported_device_type_leaks_no_connection(hub, server):
    """``get_device`` builds the endpoint's connection before it checks the
    device type, so a type it has no class for raised with the connection open.

    MUTANT M1 "session not closed": RED (observed)."""
    with pytest.raises(KeyError):
        await hub.connect_alpaca_device("focuser", HOST, PORT, "nosuchtype", 1, "x")

    assert len(server.conns) == 1, "premise: the connection was opened"
    assert server.leaked() == []
    assert hub._alpaca_sessions == {}


async def test_a_failed_move_keeps_the_held_device_untouched_and_leaks_nothing(
        hub, server):
    """Focuser 2 is held. Moving the role to focuser 3 fails inside
    ``get_device``, before the old device is touched: the old device is still
    connected on the server, never sent a disconnect, still the role's device,
    and its own connection is the only one open.

    MUTANT M1 "session not closed": RED (observed), the replacement's
    connection is also open."""
    await _connect(hub, dev_num=2)
    old = hub.devices["focuser"]
    puts = list(server.puts[FOCUSER_2])
    server.refuse_true_put[FOCUSER_3] = {1}

    with pytest.raises(httpx.ConnectError):
        await _connect(hub, dev_num=3)

    assert hub.devices["focuser"] is old
    assert old.connected is True
    assert server.connected[FOCUSER_2] is True
    assert server.puts[FOCUSER_2] == puts, "the held device was sent something"
    assert server.leaked() == [old.conn]


async def test_a_move_cancelled_in_the_old_devices_disconnect_leaks_no_connection(
        hub, server):
    """The caller gives up (a timeout, a closed request) while the old device's
    disconnect is in flight, so the failure is a cancellation, which is not an
    ``Exception``. The replacement's connection is closed all the same, the
    replacement itself is sent its disconnect (its get_device already connected
    it, and the hub will never hold it), and the cancellation is still
    delivered.

    MUTANT M1 "session not closed": RED (observed).
    MUTANT M9 "release only on the connect's failure": RED (observed), focuser
    3 stays connected on the server."""
    await _connect(hub, dev_num=2)
    old = hub.devices["focuser"]
    server.stall_false_put[FOCUSER_2] = 1
    task = asyncio.ensure_future(_connect(hub, dev_num=3))
    await asyncio.wait_for(server.stalled_false.wait(), 5)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert hub.devices["focuser"] is old
    assert server.leaked() == [old.conn]
    assert hub._alpaca_sessions.keys() == {"focuser"}
    assert server.connected[FOCUSER_3] is False, (
        "the hub does not hold focuser 3 but the server has it connected; "
        "Connected PUTs were %r" % (server.puts[FOCUSER_3],))


async def test_a_successful_connect_keeps_its_connection_and_closes_the_replaced_one(
        hub, server):
    """The control: the session that IS stored must not be closed by the
    cleanup that closes the others, and the one it replaces is."""
    await _connect(hub, dev_num=2)
    first = hub.devices["focuser"]

    await _connect(hub, dev_num=3)

    second = hub.devices["focuser"]
    assert second is not first
    assert server.leaked() == [second.conn]
    assert server.closed_conns == [first.conn]


# --------------------------------------------------------------------------
# #1015: the replacement of a failed connect is released
# --------------------------------------------------------------------------
async def test_a_failed_move_disconnects_the_replacement_and_keeps_the_old_device(
        hub, server):
    """The issue's case. Focuser 2 is held; moving the role to focuser 3, the
    server answers the device's connect and drops before the hub's own. The
    call raises, and the server must not be left with focuser 3 connected under
    a hub that holds focuser 2.

    MUTANT M2 "no release": RED (observed), focuser 3 is still connected on the
    server."""
    await _connect(hub, dev_num=2)
    old = hub.devices["focuser"]
    server.refuse_true_put[FOCUSER_3] = {2}

    with pytest.raises(httpx.ConnectError, match="Connected=True #2"):
        await _connect(hub, dev_num=3)

    assert server.connected[FOCUSER_3] is False, (
        "the hub does not hold focuser 3 but the server has it connected; "
        "Connected PUTs were %r" % (server.puts[FOCUSER_3],))
    assert server.puts[FOCUSER_3][-1] is False
    assert hub.devices["focuser"] is old
    assert old.connected is True
    assert server.connected[FOCUSER_2] is True
    assert server.puts[FOCUSER_2][-1] is True
    assert server.leaked() == [old.conn]


async def test_a_failed_first_connect_leaves_the_device_disconnected_on_the_server(
        hub, server):
    """No old device at all: the role was empty. The replacement's connect
    failed after its device's did, and there is nobody to restore, so the only
    thing to undo is the replacement's own Connected=True.

    MUTANT M2 "no release": RED (observed), the server holds focuser 2
    connected and the hub holds nothing."""
    server.refuse_true_put[FOCUSER_2] = {2}

    with pytest.raises(httpx.ConnectError, match="Connected=True #2"):
        await _connect(hub)

    assert server.connected[FOCUSER_2] is False
    assert "focuser" not in hub.devices
    assert server.leaked() == []


async def test_a_failed_rebuild_under_another_host_spelling_releases_before_it_restores(
        hub, server):
    """The alias case: both objects address ONE server slot and the comparison
    reads them as two devices. The old device is disconnected, the
    replacement's connect fails, and the slot must end with the OLD device's
    Connected=True as the last word, after the replacement's Connected=False.

    MUTANT M2 "no release": RED (observed), there is no Connected=False between
    the refused connect and the restore.
    MUTANT M3 "release after restore": RED (observed), the server ends with the
    slot disconnected under a hub that holds the device."""
    await _connect(hub, HOST)                 # Connected=True attempts #1 and #2
    old = hub.devices["focuser"]
    server.refuse_true_put[FOCUSER_2] = {4}   # the alias's get_device is #3, its final connect #4

    with pytest.raises(httpx.ConnectError, match="Connected=True #4"):
        await _connect(hub, ALIAS)

    assert hub.devices["focuser"] is old
    assert server.puts[FOCUSER_2][-3:] == [True, False, True], (
        "expected the refused connect, the replacement's release, then the "
        "old device's restore; got %r" % (server.puts[FOCUSER_2],))
    assert server.connected[FOCUSER_2] is True
    assert old.connected is True


async def test_a_failed_reconnect_of_the_same_device_sends_it_no_disconnect(
        hub, server):
    """The same device again (reconnect_role, or the UI's connect pressed
    twice): the replacement addresses the slot the held device holds, so
    releasing it would disconnect the held one. The call fails and the server
    never sees a Connected=False.

    MUTANT M4 "release the held device": RED (observed), a ``False`` is on the
    wire and the server holds the device the hub holds disconnected."""
    await _connect(hub)
    old = hub.devices["focuser"]
    server.refuse_true_put[FOCUSER_2] = {4}   # the second connect's get_device is #3, its final #4

    with pytest.raises(httpx.ConnectError, match="Connected=True #4"):
        await _connect(hub)

    assert hub.devices["focuser"] is old
    assert False not in server.puts[FOCUSER_2], server.puts[FOCUSER_2]
    assert server.connected[FOCUSER_2] is True
    assert old.connected is True
    assert server.leaked() == [old.conn]


async def test_a_move_cancelled_in_its_final_connect_releases_the_replacement(
        hub, server):
    """The same, ended by a cancellation while the hub's own connect is in
    flight: the handler runs on a cancelled task, and the release still goes
    out, the old device is restored, and the cancellation is delivered.

    MUTANT M2 "no release": RED (observed)."""
    await _connect(hub, dev_num=2)
    old = hub.devices["focuser"]
    server.stall_true_put[FOCUSER_3] = 2
    task = asyncio.ensure_future(_connect(hub, dev_num=3))
    await asyncio.wait_for(server.stalled.wait(), 5)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert server.connected[FOCUSER_3] is False
    assert server.connected[FOCUSER_2] is True
    assert hub.devices["focuser"] is old
    assert server.leaked() == [old.conn]


async def test_a_release_the_server_never_answers_is_cut_and_the_hub_still_restores(
        hub, server, monkeypatch, bus_lines):
    """The server is up for the replacement's connect and then stops answering,
    so the release hangs. The call must not hang with it: the release is cut at
    its bound, the connect lock is freed, the old device is connected again, the
    connection is closed, the connect's own error is raised, and the
    hub says which step it gave up on (the exception type, not its text).

    MUTANT M5 "release unbounded": RED (observed), the call outlives five
    seconds.
    MUTANT M7 "release unsaid": RED (observed), nothing is logged."""
    monkeypatch.setattr(hub_module, "TEARDOWN_STEP_TIMEOUT_S", 0.2)
    await _connect(hub, dev_num=2)
    old = hub.devices["focuser"]
    server.refuse_true_put[FOCUSER_3] = {2}
    server.stall_false_put[FOCUSER_3] = 1

    with pytest.raises(httpx.ConnectError, match="Connected=True #2"):
        await asyncio.wait_for(_connect(hub, dev_num=3), 5)

    assert server.stalled_false.is_set(), "premise: the release was sent"
    assert hub.devices["focuser"] is old
    assert server.connected[FOCUSER_2] is True
    assert server.puts[FOCUSER_2][-1] is True
    assert server.leaked() == [old.conn]
    said = [m for lvl, m, src in bus_lines
            if lvl == "warning" and "could not be disconnected" in m]
    assert len(said) == 1, bus_lines
    assert "focuser" in said[0] and "TimeoutError" in said[0], said[0]


async def test_a_cancel_that_lands_in_the_release_is_delivered_not_eaten(
        hub, server, monkeypatch, bus_lines):
    """The connect fails with an ordinary error (the server drops the hub's own
    connect), and the caller is cancelled while the release of the replacement
    is in flight. The release finishes whatever cancels the caller, which leaves
    the cancel counted on the task and not raised; the connect must raise it
    itself. Else the caller sees the ConnectError, which both of its callers
    (a profile apply and a role reconnect) catch before going on to connect
    the next device, with the cancel gone.

    The release is cut at its bound (patched short), the old device is
    restored, the connection is closed and the lock is free, as when nothing
    cancelled.

    MUTANT M8 "cancel eaten": RED (observed), the call ends with the connect's
    ConnectError and not a cancellation."""
    monkeypatch.setattr(hub_module, "TEARDOWN_STEP_TIMEOUT_S", 0.2)
    await _connect(hub, dev_num=2)
    old = hub.devices["focuser"]
    server.refuse_true_put[FOCUSER_3] = {2}
    server.stall_false_put[FOCUSER_3] = 1
    task = asyncio.ensure_future(_connect(hub, dev_num=3))
    await asyncio.wait_for(server.stalled_false.wait(), 5)

    task.cancel()
    done, _ = await asyncio.wait({task}, timeout=5)

    assert done, "premise: the call ended (the release is cut at its bound)"
    assert task.cancelled(), (
        "the cancel of the caller was eaten: the call ended with %r"
        % (task.exception(),))
    assert hub.devices["focuser"] is old
    assert server.connected[FOCUSER_2] is True
    assert server.puts[FOCUSER_2][-1] is True
    assert server.leaked() == [old.conn]
    assert not hub._connect_lock.locked()
    said = [m for lvl, m, src in bus_lines
            if lvl == "warning" and "could not be disconnected" in m]
    assert len(said) == 1 and "TimeoutError" in said[0], bus_lines


async def test_a_release_the_server_refuses_does_not_replace_the_connects_error(
        hub, server, bus_lines):
    """The release itself fails (the server is gone for good). The caller gets
    the error that says what was being attempted, the hub still restores the old
    device and closes the connection, and the failed release is said, by type
    and not by text.

    MUTANT M6 "release error escapes": RED (observed), the release's 'refused
    Connected=False' is raised in place of the connect's.
    MUTANT M7 "release unsaid": RED (observed), nothing is logged."""
    await _connect(hub, dev_num=2)
    old = hub.devices["focuser"]
    server.refuse_true_put[FOCUSER_3] = {2}
    server.refuse_false_put[FOCUSER_3] = {1}

    with pytest.raises(httpx.ConnectError, match="Connected=True #2"):
        await _connect(hub, dev_num=3)

    assert hub.devices["focuser"] is old
    assert server.connected[FOCUSER_2] is True
    assert server.leaked() == [old.conn]
    said = [m for lvl, m, src in bus_lines
            if lvl == "warning" and "could not be disconnected" in m]
    assert len(said) == 1, bus_lines
    assert "ConnectError" in said[0] and "refused" not in said[0], said[0]
