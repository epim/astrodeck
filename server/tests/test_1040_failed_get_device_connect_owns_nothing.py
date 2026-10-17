# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1040: a device whose connect fails inside ``NativeSession.get_device``, after
the server took its ``Connected=True``, is not left connected with no owner.

``_AlpacaDevice.connect`` sends ``Connected=True`` and then the device class
reads its properties. A read that fails raised out of ``get_device``, the
device object was dropped, and the server kept ``Connected=True`` for the slot:
for a camera a cooler left engaged and a device busy to every other client,
retried on every backoff of the engine's reconnect gate while the failure
lasts. The same class as #1015, reached through ``get_device``.

The session now sends ``Connected=False`` (bounded, best effort, said in plain
words) when there is no held device for the role. When the hub holds one it
asks the session not to (``keep_unowned``) and releases the replacement itself,
because two spellings of one endpoint share one server slot and only the hub
knows the order against the held device's restore; the same device replaced is
never released.

The server is the in-process fake of #987/#991 (real device classes, real
native backend, real hub) with reads that fail after the PUT.

Each test names the mutant of ``native_backend.py`` or ``hub.py`` it was shown
red under (applied to a byte copy, restored from that copy and md5-checked):

* M1 "no release": ``_release_failed_connect`` returns at once.
* M2 "release when the PUT failed": the ``connected or link_lost`` guard is
  removed.
* M3 "hub does not release": the hub's handler around ``get_device`` does not
  release.
* M4 "release the held device": the hub releases also when the same device is
  replaced.
* M5 "no restore": the hub does not connect the old device again.
* M6 "release unbounded": the release is awaited directly.
* M7 "release unsaid": a release that fails is not said.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

import astrodeck.hub as hub_module
from astrodeck.devices import alpaca
from test_987_991_alpaca_rebuild_ends_connected import (  # noqa: F401  (fixtures)
    ALIAS, FOCUSER_2, FOCUSER_3, HOST, PORT, FakeAlpacaServer, _connect, hub)


class Server(FakeAlpacaServer):
    """The fake of #987/#991 plus reads that fail or hang after the PUT."""

    def __init__(self) -> None:
        super().__init__()
        #: slot -> {read number (1-based, counted per slot): exception}
        self.fail_read: dict[tuple, dict[int, Exception]] = {}
        #: slot -> the Nth read that never answers
        self.stall_read: dict[tuple, int] = {}
        self.stalled_read = asyncio.Event()
        #: slot -> the Nth Connected=False PUT that never answers
        self.stall_false_put: dict[tuple, int] = {}
        self.reads: dict[tuple, int] = {}
        self.false_attempts: dict[tuple, int] = {}

    def connection_class(self):
        server = self
        base = super().connection_class()

        class Conn(base):
            async def get(self, dev_type, dev_num, method, **params):
                if method != "connected":
                    slot = (dev_type, dev_num)
                    n = server.reads[slot] = server.reads.get(slot, 0) + 1
                    err = server.fail_read.get(slot, {}).get(n)
                    if err is not None:
                        raise err
                    if n == server.stall_read.get(slot):
                        server.stalled_read.set()
                        await asyncio.Event().wait()         # until cancelled
                return await super().get(dev_type, dev_num, method, **params)

            async def put(self, dev_type, dev_num, method, **params):
                if method == "connected" and not params["Connected"]:
                    slot = (dev_type, dev_num)
                    n = server.false_attempts[slot] = server.false_attempts.get(slot, 0) + 1
                    if n == server.stall_false_put.get(slot):
                        await asyncio.Event().wait()         # until cancelled
                return await super().put(dev_type, dev_num, method, **params)

        return Conn


@pytest.fixture
def server(monkeypatch):
    srv = Server()
    monkeypatch.setattr(alpaca, "AlpacaConnection", srv.connection_class())
    return srv


def _dropped():
    return httpx.ReadError("the server dropped a property read")


def _refused():
    return alpaca.AlpacaReplyError("driver refused the read", http_status=200,
                                   error_number=0x500)


# --------------------------------------------------------------------------
# nothing held: get_device releases for itself
# --------------------------------------------------------------------------
@pytest.mark.parametrize("make_error", [_dropped, _refused],
                         ids=["transport-failure", "driver-refusal"])
async def test_a_property_read_failing_after_the_put_leaves_the_server_disconnected(
        hub, server, make_error):
    """The issue's case: the PUT is answered, the first property read fails
    (the server dropping the link, or the driver refusing). The call raises the
    read's own error and the server must not keep the device connected under a
    hub that holds nothing.

    MUTANT M1 "no release": RED (observed), the server holds focuser 2 connected."""
    server.fail_read[FOCUSER_2] = {1: make_error()}

    with pytest.raises((httpx.ReadError, alpaca.AlpacaReplyError)) as caught:
        await _connect(hub)

    assert caught.type is type(make_error()), "the original error is the one raised"
    assert server.connected[FOCUSER_2] is False, (
        "the hub holds nothing but the server has the device connected; "
        "Connected PUTs were %r" % (server.puts[FOCUSER_2],))
    assert server.puts[FOCUSER_2] == [True, False]
    assert "focuser" not in hub.devices
    assert hub._alpaca_sessions == {}


async def test_a_refused_put_sends_no_disconnect(hub, server):
    """The PUT itself fails: the server never took the connect, so there is
    nothing to undo and no Connected=False goes out.

    MUTANT M2 "release when the PUT failed": RED (observed), a False is on the
    wire."""
    server.refuse_true_put[FOCUSER_2] = {1}

    with pytest.raises(httpx.ConnectError):
        await _connect(hub)

    assert server.puts[FOCUSER_2] == [True]


async def test_a_release_the_server_never_answers_is_cut_and_said(
        hub, server, monkeypatch, bus_lines):
    """The read fails and then the server stops answering, so the release
    hangs. The call must not hang with it: the release is cut at its bound, the
    original error is raised, and the line says which step it gave up on (the
    exception type, not its text).

    MUTANT M6 "release unbounded": RED (observed), the call outlives five
    seconds.
    MUTANT M7 "release unsaid": RED (observed), nothing is logged."""
    monkeypatch.setattr(hub_module, "TEARDOWN_STEP_TIMEOUT_S", 0.2)
    server.fail_read[FOCUSER_2] = {1: _dropped()}
    server.stall_false_put[FOCUSER_2] = 1

    with pytest.raises(httpx.ReadError):
        await asyncio.wait_for(_connect(hub), 5)

    said = [m for lvl, m, src in bus_lines
            if lvl == "warning" and "could not be disconnected" in m]
    assert len(said) == 1, bus_lines
    assert "focuser" in said[0] and "TimeoutError" in said[0], said[0]


async def test_a_cancel_while_a_property_read_is_pending_still_releases_the_device(
        hub, server):
    """The caller gives up while the read is in flight. The cancel is
    delivered, and the device the server already holds connected is let go of.

    MUTANT M1 "no release": RED (observed)."""
    server.stall_read[FOCUSER_2] = 1
    task = asyncio.ensure_future(_connect(hub))
    await asyncio.wait_for(server.stalled_read.wait(), 5)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert server.connected[FOCUSER_2] is False
    assert "focuser" not in hub.devices


# --------------------------------------------------------------------------
# a device is held: the hub releases, in the right order
# --------------------------------------------------------------------------
async def test_a_failed_move_releases_the_replacement_and_keeps_the_old_device(
        hub, server):
    """Focuser 2 is held; moving the role to focuser 3 fails on a property read
    inside get_device after focuser 3 was connected. Focuser 3 is released, and
    the held focuser 2 is untouched and still the role's device.

    No mutant is red on this test alone: with the hub's handler not releasing,
    the final ``finally`` releases the replacement instead (M3 is red on the
    alias test below, where the ORDER matters)."""
    await _connect(hub, dev_num=2)
    old = hub.devices["focuser"]
    server.fail_read[FOCUSER_3] = {1: _dropped()}

    with pytest.raises(httpx.ReadError):
        await _connect(hub, dev_num=3)

    assert server.connected[FOCUSER_3] is False
    assert server.puts[FOCUSER_3] == [True, False]
    assert hub.devices["focuser"] is old
    assert old.connected is True
    assert server.connected[FOCUSER_2] is True


async def test_a_failed_rebuild_under_another_host_spelling_releases_then_restores(
        hub, server):
    """The alias case: both names reach ONE server slot and the comparison
    reads them as two devices. The replacement's get_device connects the slot,
    its read fails, and the release flips the slot to disconnected, so the held
    device must be connected again: the slot ends with the OLD device's
    Connected=True as the last word.

    MUTANT M3 "hub does not release": RED (observed), the release comes after
    the restore.
    MUTANT M5 "no restore": RED (observed), the server ends disconnected under
    a hub that holds the device."""
    await _connect(hub, HOST)
    old = hub.devices["focuser"]
    # the alias's first read is the next one on this slot
    server.fail_read[FOCUSER_2] = {server.reads[FOCUSER_2] + 1: _dropped()}

    with pytest.raises(httpx.ReadError):
        await _connect(hub, ALIAS)

    assert hub.devices["focuser"] is old
    assert server.puts[FOCUSER_2][-3:] == [True, False, True], server.puts[FOCUSER_2]
    assert server.connected[FOCUSER_2] is True
    assert old.connected is True


async def test_a_failed_reconnect_of_the_same_device_sends_it_no_disconnect(
        hub, server):
    """The same device again: the replacement addresses the slot the held
    device holds, so releasing it would disconnect the held one. The call fails
    on the read and the server never sees a Connected=False.

    MUTANT M4 "release the held device": RED (observed), a False is on the wire
    and the server holds the held device disconnected."""
    await _connect(hub)
    old = hub.devices["focuser"]
    server.fail_read[FOCUSER_2] = {server.reads[FOCUSER_2] + 1: _dropped()}

    with pytest.raises(httpx.ReadError):
        await _connect(hub)

    assert hub.devices["focuser"] is old
    assert False not in server.puts[FOCUSER_2], server.puts[FOCUSER_2]
    assert server.connected[FOCUSER_2] is True
    assert old.connected is True
