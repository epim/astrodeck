# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#989, review round: a blip on an Alpaca device is not an absence.

Making every Alpaca device's ``connected`` a measurement (a transport failure
sets it false) turned ``connected`` into a one-way latch for a stateless HTTP
device: nothing but ``connect()`` put it back. Several consumers read
``connected == False`` as 'no such device here', so one ReadTimeout on a mount
(a synchronous COM ``Park()`` that outran the 30 s client timeout, a comhost
restarting) left the roof's never-crush guard skipped, the mount STOP route
refusing, the safety poller silent and the roof close refusing, until somebody
reconnected by hand.

The shape that replaces it, all pinned here:

* the device records ``link_lost`` beside ``connected = False`` when a CONNECTED
  device fails at the transport, and a good reply, a probe that hears
  Connected=True, ``connect()`` or ``disconnect()`` ends it;
* the hub probes every device in doubt on the status cadence, because a device
  that reads not-connected is polled by nothing else;
* the consumers that must not mistake a blip for an absence ask
  ``base.is_present`` (connected OR in doubt).

Real device classes over ``httpx.MockTransport`` (a fake Alpaca server that can
go away, hold a reply, or say NotConnected), the real hub, the real app, the
real sim rig for the engine. Each test names the mutant it was shown red under.
"""
from __future__ import annotations

import asyncio
import itertools
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import ConfigStore, SafetyConfig, Site
from astrodeck.devices.alpaca import (
    DEVICE_CLASSES,
    AlpacaConnection,
    AlpacaReplyError,
)
from astrodeck.devices.base import (
    DeviceError,
    DomeShutterState,
    SafetyReading,
    is_present,
    link_in_doubt,
)
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.roof import close_observatory

NOT_CONNECTED = 0x407
_ORDER = itertools.count()


def _json(value=None, number=0, message=""):
    return {"Value": value, "ErrorNumber": number, "ErrorMessage": message,
            "ClientTransactionID": 0, "ServerTransactionID": 0}


class FakeAlpaca:
    """One Alpaca server for one device.

    ``mode``: ``up`` answers from ``values`` (the ``connected`` property reads
    True unless a test says otherwise); ``down`` is a ReadTimeout (a server that
    took the socket and said nothing); ``not_connected`` answers ErrorNumber
    0x407; ``error`` answers ErrorNumber 0x500; ``partial`` answers only the
    routes in ``answers`` and refuses the connection to the rest; ``refuse``
    answers ``connected`` and says 0x400 (not implemented) to everything else.
    ``on_put`` runs a side effect when a PUT lands (a park that parks).
    ``hold`` maps ``(METHOD, route)`` to an Event the reply waits for.
    ``calls`` is every request as ``(METHOD, route)``; ``stamps`` adds a
    process-wide order so two servers can be compared."""

    def __init__(self, **values):
        self.values = {"connected": True} | values
        self.mode = "up"
        self.answers: set[str] = set()
        self.on_put: dict = {}
        self.hold: dict = {}
        self.calls: list[tuple[str, str]] = []
        self.stamps: list[tuple[int, str, str]] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        route = request.url.path.rsplit("/", 1)[-1]
        key = (request.method, route)
        self.calls.append(key)
        self.stamps.append((next(_ORDER), *key))
        gate = self.hold.get(key)
        if gate is not None:
            await gate.wait()
        if self.mode == "down":
            raise httpx.ReadTimeout("no answer", request=request)
        if self.mode == "partial" and route not in self.answers:
            raise httpx.ConnectError("no answer", request=request)
        if self.mode == "not_connected":
            return httpx.Response(200, json=_json(None, NOT_CONNECTED, "x"))
        if self.mode == "error":
            return httpx.Response(200, json=_json(None, 0x500, "x"))
        if self.mode == "refuse" and route != "connected":
            return httpx.Response(200, json=_json(None, 0x400, "x"))
        if request.method == "PUT" and route in self.on_put:
            self.on_put[route](self)
        return httpx.Response(200, json=_json(self.values.get(route)))


def _make(dev_type: str, server: FakeAlpaca, *, connected: bool = True):
    conn = AlpacaConnection("alpaca.invalid", 11111)
    conn.http = httpx.AsyncClient(transport=httpx.MockTransport(server))
    dev = DEVICE_CLASSES[dev_type](conn, 0, "Fictional Alpaca Device")
    dev.connected = connected
    return dev


async def _blip(dev, server: FakeAlpaca) -> None:
    """One transport failure on a connected device, then the link is back."""
    server.mode = "down"
    with pytest.raises(httpx.TransportError):
        await dev._get("x")
    server.mode = "up"
    assert (dev.connected, dev.link_lost) == (False, True)


async def _until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


@pytest.fixture
async def build():
    made = []

    def _build(dev_type, server, **kw):
        dev = _make(dev_type, server, **kw)
        made.append(dev)
        return dev

    yield _build
    for dev in made:
        await dev.conn.close()


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Test", latitude=40.0, longitude=-74.0,
                        is_default=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(engine_mod, "SAFETY_PAUSE_POLL_S", 0.05)
    monkeypatch.setattr(engine_mod, "SAFETY_SEED_WAIT_S", 0.5)
    return store


# ==========================================================================
# The device: the measurement runs both ways
# ==========================================================================
@pytest.mark.parametrize("verb", ["_get", "_put"])
@pytest.mark.parametrize("dev_type", sorted(DEVICE_CLASSES))
async def test_a_good_reply_after_a_blip_puts_connected_back(build, dev_type, verb):
    """A transport failure puts a CONNECTED device in doubt (``connected``
    false, ``link_lost`` true, and ``is_present`` still true), and the next good
    reply, on either verb, ends it: the #16 rule read the other way.

    MUTANT M9 "never heals": ``_AlpacaDevice._note_answered``'s body made
    ``pass``: RED (observed), all 18 cases at the last assertion,
    ``assert (False, True) == (True, False)``.
    """
    server = FakeAlpaca(x=7)
    dev = build(dev_type, server)

    server.mode = "down"
    with pytest.raises(httpx.ReadTimeout):
        await getattr(dev, verb)("x")
    assert (dev.connected, dev.link_lost) == (False, True)
    assert link_in_doubt(dev) and is_present(dev)

    server.mode = "up"
    assert await getattr(dev, verb)("x") == 7
    assert (dev.connected, dev.link_lost) == (True, False)
    assert not link_in_doubt(dev) and is_present(dev)


async def test_the_connected_property_is_not_a_good_reply(build):
    """The ``connected`` property is the QUESTION a probe asks; the value of its
    answer decides. A server that answers 'Connected=False' to a read has not
    said the device is back.

    MUTANT M10 "any reply heals": the ``method.lower() != "connected"`` test in
    ``_note_answered`` removed: RED (observed), ``assert (True, False) ==
    (False, True)``.
    """
    server = FakeAlpaca(connected=False)
    dev = build("telescope", server)
    await _blip(dev, server)

    assert await dev._get("connected") is False
    assert (dev.connected, dev.link_lost) == (False, True)


@pytest.mark.parametrize("how", ["never connected", "disconnected"])
async def test_a_device_nobody_connected_is_never_put_in_doubt(build, how):
    """Nothing may 'heal' a device the operator disconnected, or one that was
    never connected: a failed read of it is no news, and a later reply is not a
    reconnect.

    MUTANT M11 "doubt for everyone": the ``if self.connected:`` guard in
    ``_note_link_lost`` removed: RED (observed), both cases,
    ``assert True is False`` on ``link_lost`` after the failed read.
    """
    server = FakeAlpaca(x=7)
    dev = build("telescope", server, connected=(how == "disconnected"))
    if how == "disconnected":
        await dev.disconnect()
    assert (dev.connected, dev.link_lost) == (False, False)

    server.mode = "down"
    with pytest.raises(httpx.ReadTimeout):
        await dev._get("x")
    assert dev.link_lost is False

    server.mode = "up"
    assert await dev._get("x") == 7
    assert dev.connected is False
    assert not is_present(dev)


async def test_a_not_connected_answer_ends_the_doubt(build):
    """NotConnected (0x407) is the server saying the device is not connected
    THERE: not a blip, and no reply heals it; only ``connect()`` does.

    MUTANT M12 "0x407 leaves the doubt": ``_note_not_connected`` no longer
    clears ``link_lost``: RED (observed), the last assertion,
    ``assert True is False``.
    """
    server = FakeAlpaca(x=7)
    dev = build("focuser", server)
    await _blip(dev, server)

    server.mode = "not_connected"
    with pytest.raises(AlpacaReplyError):
        await dev._get("x")
    assert (dev.connected, dev.link_lost) == (False, False)

    server.mode = "up"
    assert await dev._get("x") == 7
    assert dev.connected is False
    assert not is_present(dev)


@pytest.mark.parametrize("server_mode", ["up", "down"])
async def test_disconnect_ends_the_doubt(build, server_mode):
    """An operator's disconnect, even one that cannot reach the server, ends
    the doubt, so a late reply does not revive a device they put away.

    MUTANT M13 "disconnect keeps the doubt": ``disconnect``'s ``finally`` no
    longer clears ``link_lost``: RED (observed), both cases, ``assert (True,
    True) == (False, False)``... after the next read heals it.
    """
    server = FakeAlpaca(x=7)
    dev = build("telescope", server)
    await _blip(dev, server)

    server.mode = server_mode
    if server_mode == "down":
        with pytest.raises(httpx.TransportError):
            await dev.disconnect()
    else:
        await dev.disconnect()
    assert (dev.connected, dev.link_lost) == (False, False)

    server.mode = "up"
    assert await dev._get("x") == 7
    assert (dev.connected, dev.link_lost) == (False, False)


async def test_connect_ends_the_doubt(build):
    server = FakeAlpaca(x=7)
    dev = build("focuser", server)
    await _blip(dev, server)
    server.values.update(maxstep=1000, stepsize=1.0, absolute=True)

    await dev.connect()
    assert (dev.connected, dev.link_lost) == (True, False)


# ---------------------------------------------------------------- probe_link
@pytest.mark.parametrize("mode,connected_value,returns,after", [
    ("up", True, True, (True, False)),
    ("up", False, False, (False, False)),
    ("not_connected", True, False, (False, False)),
    ("down", True, False, (False, True)),
    ("error", True, False, (False, True)),
])
async def test_probe_link_asks_the_server_whether_the_device_is_connected(
        build, mode, connected_value, returns, after):
    """Connected=True restores the device; Connected=False or NotConnected says
    the server is up and the device is not connected there (the doubt ends, the
    flag stays false, only ``connect()`` restores it); a dead server or an
    answered error leaves the doubt standing. It never raises.

    MUTANT M14 "any answer heals": ``if up is True:`` in ``probe_link`` made
    ``if True:``: RED (observed), the Connected=False case, ``assert (True,
    False) == (False, False)``.
    """
    server = FakeAlpaca(connected=connected_value)
    dev = build("telescope", server)
    await _blip(dev, server)
    server.mode = mode

    assert await dev.probe_link() is returns
    assert (dev.connected, dev.link_lost) == after


async def test_probe_link_sends_nothing_for_a_device_not_in_doubt(build):
    """MUTANT M16 "probe always asks": the ``if not self.link_lost:`` return at
    the top of ``probe_link`` removed: RED (observed), ``calls`` is not empty.
    """
    server = FakeAlpaca()
    dev = build("telescope", server)

    assert await dev.probe_link() is True
    assert server.calls == []


async def test_a_disconnect_while_the_probe_is_out_stands(build):
    """The operator disconnects while the probe's question is in flight; the
    answer that comes back afterwards ('connected') must not revive the device.

    MUTANT M15 "no recheck": the ``if not self.link_lost:`` recheck after the
    await in ``probe_link`` removed: RED (observed), ``assert True is False``.
    """
    server = FakeAlpaca()
    dev = build("telescope", server)
    await _blip(dev, server)

    gate = asyncio.Event()
    server.hold[("GET", "connected")] = gate
    probe = asyncio.create_task(dev.probe_link())
    assert await _until(lambda: ("GET", "connected") in server.calls)

    await dev.disconnect()
    gate.set()
    assert await probe is False
    assert (dev.connected, dev.link_lost) == (False, False)


# ------------------------------------------------ connect() among its probes
@pytest.mark.parametrize("dev_type", ["telescope", "dome", "covercalibrator"])
async def test_a_connect_that_loses_its_link_among_its_probes_does_not_return(
        build, dev_type):
    """These three connects swallow their best-effort probes' errors. A server
    that answers the Connected PUT and then goes away used to leave connect()
    returning normally for a device that read not connected; the hub registered
    it as connected.

    MUTANT M17 "connect trusts its probes": ``_connected_after_probes`` made
    ``pass``: RED (observed), all 3 cases, ``DID NOT RAISE``.
    """
    server = FakeAlpaca()
    server.mode = "partial"
    server.answers = {"connected"}
    dev = build(dev_type, server, connected=False)

    with pytest.raises(DeviceError, match="stopped answering while"):
        await dev.connect()
    assert (dev.connected, dev.link_lost) == (False, False)


@pytest.mark.parametrize("dev_type", ["telescope", "dome", "covercalibrator"])
async def test_a_connect_whose_probes_are_merely_refused_still_connects(
        build, dev_type):
    """CONTROL. A mount that lacks a property, a roof that cannot bind: the
    driver ANSWERED, so the probe's refusal is business as usual.

    MUTANT M18 "connect always raises": ``_connected_after_probes`` made to
    raise unconditionally: RED (observed), all 3 cases.
    """
    server = FakeAlpaca()
    server.mode = "refuse"
    dev = build(dev_type, server, connected=False)

    await dev.connect()
    assert (dev.connected, dev.link_lost) == (True, False)


# ==========================================================================
# The roof's never-crush guard
# ==========================================================================
class _Roof:
    requires_park_before_close = True

    def __init__(self):
        self.closed = False

    async def is_closed(self):
        return False

    async def close_shutter(self):
        self.closed = True

    async def shutter_state(self):
        return DomeShutterState.CLOSED if self.closed else DomeShutterState.OPEN


def _lines():
    seen: list[tuple] = []
    return seen, (lambda level, msg, source: seen.append((level, msg, source)))


async def test_the_roof_guard_still_asks_a_mount_whose_link_blipped(build):
    """The reviewer's scenario: one ReadTimeout on the mount, the link back, the
    mount answering AtPark=False. The guard must ask and REFUSE; with
    ``connected`` read as 'no mount here' it skipped the guard and the roof
    came down over an unparked tube.

    MUTANT M19 "guard trusts the flag": the guard in ``close_observatory``
    reverted to ``getattr(telescope, "connected", False)``: RED (observed),
    ``assert True is False`` (the roof closed).
    """
    server = FakeAlpaca(atpark=False)
    tel = build("telescope", server)
    await _blip(tel, server)
    roof, (seen, log) = _Roof(), _lines()

    assert await close_observatory(roof, tel, log=log) is False
    assert roof.closed is False
    assert any(lvl == "error" and "REFUSED" in m for lvl, m, _s in seen), seen


async def test_the_roof_closes_once_the_blipped_mount_reads_parked(build):
    """The same mount, parked: the guard asks, is answered, and lets the roof
    close; the answer is also what puts the mount's ``connected`` back.

    MUTANT M19 (see above) leaves this green by accident (the guard is skipped
    and the roof closes); the assertion that matters here is the heal.
    """
    server = FakeAlpaca(atpark=True)
    tel = build("telescope", server)
    await _blip(tel, server)
    roof, (_seen, log) = _Roof(), _lines()

    assert await close_observatory(roof, tel, log=log) is True
    assert roof.closed is True
    assert (tel.connected, tel.link_lost) == (True, False)


async def test_the_roof_refuses_while_the_mount_link_is_still_down(build):
    """A mount in doubt whose link is STILL dead cannot be confirmed parked:
    refuse, as an Alpaca mount with a dead link always did.

    MUTANT M19 (see above): RED (observed), the roof closed.
    """
    server = FakeAlpaca(atpark=True)
    tel = build("telescope", server)
    await _blip(tel, server)
    server.mode = "down"
    roof, (seen, log) = _Roof(), _lines()

    assert await close_observatory(roof, tel, log=log) is False
    assert roof.closed is False
    assert any("REFUSED" in m for _l, m, _s in seen), seen


async def test_control_a_mount_nobody_connected_is_not_asked(build):
    """CONTROL: the contract test_888 pins stays: a mount that is not connected
    and not in doubt (never connected, disconnected on purpose) is not asked,
    and the roof closes.

    MUTANT M20 "everything counts as present": ``is_present`` in base.py made
    ``return True``: RED (observed), the roof refused.
    """
    server = FakeAlpaca(atpark=False)
    tel = build("telescope", server, connected=False)
    roof, (_seen, log) = _Roof(), _lines()

    assert await close_observatory(roof, tel, log=log) is True
    assert roof.closed is True
    assert server.calls == []


# ==========================================================================
# The hub and the routes: STOP, the roof close
# ==========================================================================
def _app_hub(monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    return app_module.hub


def test_require_returns_a_device_whose_link_blipped(monkeypatch):
    """MUTANT M21 "require trusts the flag": ``Hub.require`` reverted to ``not
    dev.connected``: RED (observed), the blipped mount raises 'no telescope
    connected'.
    """
    hub = _app_hub(monkeypatch)
    server = FakeAlpaca()
    tel = _make("telescope", server)
    asyncio.run(_blip(tel, server))
    monkeypatch.setitem(hub.devices, "telescope", tel)
    assert hub.require("telescope") is tel

    gone = _make("telescope", FakeAlpaca(), connected=False)
    monkeypatch.setitem(hub.devices, "telescope", gone)
    with pytest.raises(DeviceError, match="no telescope connected"):
        hub.require("telescope")


def test_the_mount_stop_route_works_after_a_blip(monkeypatch):
    """STOP is the one command that must never be refused for a latched flag.

    MUTANT M21 (see above): RED (observed), 409.
    """
    hub = _app_hub(monkeypatch)
    server = FakeAlpaca()
    tel = _make("telescope", server)
    asyncio.run(_blip(tel, server))
    monkeypatch.setitem(hub.devices, "telescope", tel)

    with TestClient(app_module.create_app()) as c:
        r = c.post("/api/mount/stop")

    assert r.status_code == 200, r.text
    assert ("PUT", "abortslew") in server.calls


def test_control_the_mount_stop_route_refuses_a_mount_nobody_connected(monkeypatch):
    hub = _app_hub(monkeypatch)
    tel = _make("telescope", FakeAlpaca(), connected=False)
    monkeypatch.setitem(hub.devices, "telescope", tel)

    with TestClient(app_module.create_app()) as c:
        r = c.post("/api/mount/stop")

    assert r.status_code == 409, r.text
    assert "no telescope connected" in r.text


def _settle_dome(hub) -> None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        task = hub._busy.get("dome")
        if task is not None and task.done():
            return
        time.sleep(0.01)
    raise AssertionError("the spawned roof close never finished")


def _park_and_close_rig(monkeypatch):
    """A mount and a roof, both having blipped, over fake servers where a park
    parks and a close closes."""
    hub = _app_hub(monkeypatch)
    mount = FakeAlpaca(atpark=False)
    mount.on_put["park"] = lambda s: s.values.update(atpark=True)
    roof = FakeAlpaca(shutterstatus=0)
    roof.on_put["closeshutter"] = lambda s: s.values.update(shutterstatus=1)
    tel, dome = _make("telescope", mount), _make("dome", roof)
    asyncio.run(_blip(tel, mount))
    asyncio.run(_blip(dome, roof))
    monkeypatch.setitem(hub.devices, "telescope", tel)
    monkeypatch.setitem(hub.devices, "dome", dome)
    return hub, mount, roof


def test_the_dome_close_route_parks_then_closes_after_blips(monkeypatch):
    """``POST /api/dome/close`` with both devices having blipped: it neither
    answers 'no dome connected' nor leaves the park out; the mount is parked and
    THEN the roof closes.

    MUTANT M22 "route refuses a roof in doubt": the route's ``not
    is_present(dome)`` reverted to ``not getattr(dome, "connected", False)``:
    RED (observed), 409.
    MUTANT M23 "route skips the park": ``live = tel is not None and
    is_present(tel)`` reverted to the ``connected`` flag: RED (observed), no
    park request and (the guard asking AtPark=False) no close request.
    """
    hub, mount, roof = _park_and_close_rig(monkeypatch)

    with TestClient(app_module.create_app()) as c:
        r = c.post("/api/dome/close")
        assert r.status_code == 200, r.text
        _settle_dome(hub)

    parked = [s for s in mount.stamps if s[1:] == ("PUT", "park")]
    closed = [s for s in roof.stamps if s[1:] == ("PUT", "closeshutter")]
    assert parked and closed, (mount.calls, roof.calls)
    assert parked[0][0] < closed[0][0], "the roof closed before the mount parked"


# ==========================================================================
# The hub: the safety poller and the link probes
# ==========================================================================
async def test_the_safety_poller_keeps_polling_a_monitor_whose_link_blipped(
        temp_store, build, monkeypatch):
    """A blip must not take the safety monitor out of the poll: while the link
    is down the cache holds the STALE reading that fail-closes (as it always
    did), and the first good read puts the monitor back and caches a safe one.

    MUTANT M24 "poller trusts the flag": ``_safety_loop``'s ``not
    is_present(mon)`` reverted to ``not getattr(mon, "connected", False)``: RED
    (observed), the cache stays None and the first wait times out.
    """
    monkeypatch.setattr(hub_module, "SAFETY_POLL_INTERVAL_S", 0.01)
    server = FakeAlpaca(issafe=True)
    mon = build("safetymonitor", server)
    hub = Hub()
    hub.devices["safety"] = mon
    await _blip(mon, server)
    server.mode = "down"

    task = asyncio.create_task(hub._safety_loop())
    try:
        assert await _until(lambda: hub._safety_reading is not None
                            and hub._safety_reading.stale), hub._safety_reading
        assert hub._safety_reading.is_safe is False

        server.mode = "up"
        assert await _until(lambda: hub._safety_reading is not None
                            and hub._safety_reading.is_safe), hub._safety_reading
        assert (mon.connected, mon.link_lost) == (True, False)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_the_hub_hears_a_device_come_back(temp_store, build):
    """``_kick_link_probes`` probes a device in doubt; Connected=True restores
    it.

    MUTANT M25 "no probes": ``_kick_link_probes``'s body made ``return``: RED
    (observed), ``KeyError: 'telescope'``.
    """
    hub = Hub()
    server = FakeAlpaca()
    tel = build("telescope", server)
    hub.devices["telescope"] = tel
    await _blip(tel, server)

    hub._kick_link_probes()
    await asyncio.wait_for(hub._link_probes["telescope"], 3)

    assert (tel.connected, tel.link_lost) == (True, False)


async def test_one_probe_is_in_flight_per_device(temp_store, build):
    """A dead host must not pile a probe up on every status tick.

    MUTANT M26 "probe per tick": the ``held is not None and not held.done()``
    skip in ``_kick_link_probes`` removed: RED (observed), 3 requests.
    """
    hub = Hub()
    server = FakeAlpaca()
    tel = build("telescope", server)
    hub.devices["telescope"] = tel
    await _blip(tel, server)
    gate = asyncio.Event()
    server.hold[("GET", "connected")] = gate

    hub._kick_link_probes()
    assert await _until(lambda: ("GET", "connected") in server.calls)
    hub._kick_link_probes()
    hub._kick_link_probes()
    await asyncio.sleep(0.05)
    assert server.calls.count(("GET", "connected")) == 1

    gate.set()
    await asyncio.wait_for(hub._link_probes["telescope"], 3)
    assert tel.connected is True


async def test_the_hub_probes_only_devices_in_doubt(temp_store, build):
    """A healthy device, a device nobody connected and a device with no probe
    (the simulator's) are left alone.

    MUTANT M27 "probe everything": the ``link_in_doubt`` skip in
    ``_kick_link_probes`` removed: RED (observed), the healthy mount is asked.
    """
    hub = Hub()
    healthy_server, idle_server = FakeAlpaca(), FakeAlpaca()
    healthy = build("telescope", healthy_server)
    idle = build("focuser", idle_server, connected=False)
    hub.devices["telescope"] = healthy
    hub.devices["focuser"] = idle

    class _NoProbe:
        connected = False
        link_lost = True

    hub.devices["camera"] = _NoProbe()
    hub._kick_link_probes()
    await asyncio.sleep(0.05)

    assert hub._link_probes == {}
    assert healthy_server.calls == [] and idle_server.calls == []


async def test_the_status_loop_runs_the_probes(temp_store, build, monkeypatch):
    """The probes ride the status cadence: nothing else polls a device that
    reads not-connected, so without this the doubt never ends.

    MUTANT M28 "the loop forgets the probes": the ``_kick_link_probes()`` call
    removed from ``_status_loop``: RED (observed), the mount is never restored.
    """
    hub = Hub()
    server = FakeAlpaca()
    tel = build("telescope", server)
    hub.devices["telescope"] = tel
    await _blip(tel, server)

    async def poll():
        return {}

    monkeypatch.setattr(hub, "poll_status", poll)
    task = asyncio.create_task(hub._status_loop())
    try:
        assert await _until(lambda: tel.connected), (tel.connected, tel.link_lost)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_the_teardown_cancels_a_probe_in_flight(temp_store, build):
    """MUTANT M29 "probes outlive the rig": the cancel loop over
    ``_link_probes`` in ``_teardown`` removed: RED (observed), the probe task is
    still pending and the map is not empty.
    """
    hub = Hub()
    server = FakeAlpaca()
    tel = build("telescope", server)
    hub.devices["telescope"] = tel
    await _blip(tel, server)
    gate = asyncio.Event()
    server.hold[("GET", "connected")] = gate
    hub._kick_link_probes()
    probe = hub._link_probes["telescope"]
    assert await _until(lambda: ("GET", "connected") in server.calls)

    await hub.disconnect_all()
    _done, pending = await asyncio.wait([probe], timeout=2.0)
    if pending:
        gate.set()          # a failing run must not leave the task hanging
        probe.cancel()
        await asyncio.gather(probe, return_exceptions=True)

    assert not pending, "the teardown left a link probe running"
    assert hub._link_probes == {}


# ==========================================================================
# The engine: the roof and the monitor are judged by presence
# ==========================================================================
@pytest.fixture
async def sim_hub(temp_store, monkeypatch):
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _plan(**overrides) -> SequencePlan:
    defaults = dict(
        name="presence",
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100, count=4)],
        )],
        guide=False, dither_every=0, autofocus_every=0, meridian_flip=False,
    )
    return SequencePlan(**(defaults | overrides))


async def _wait_for(predicate, timeout=40.0):
    return await _until(predicate, timeout)


async def test_the_wind_down_closes_a_roof_whose_link_blipped(sim_hub, temp_store):
    """The wind-down's roof close was gated on ``dome.connected``: one blip on
    an Alpaca roof and the end of the night silently skipped the close (no
    record, no page).

    MUTANT M30 "wind-down trusts the flag": the ``is_present(dome)`` test at
    the wind-down's ``elif close_dome:`` reverted to ``getattr(dome,
    "connected", False)``: RED (observed), the roof is still OPEN.
    """
    dome = sim_hub.devices["dome"]
    dome.connected, dome.link_lost = False, True
    temp_store.set_safety(SafetyConfig(enabled=False, close_dome_when_done=True,
                                       min_alt_deg=0.0))

    engine = SequenceEngine(sim_hub)
    engine.start(_plan(park_when_done=True))
    assert await _wait_for(lambda: not engine.running), engine.state
    assert engine.state.get("state") == "complete"
    assert await dome.shutter_state() is DomeShutterState.CLOSED


async def test_an_unsafe_verdict_closes_a_roof_whose_link_blipped(
        sim_hub, temp_store):
    """``_on_unsafe`` counted the roof as closeable only while ``dome.connected``,
    so after one blip the engine held under open sky instead of closing.

    MUTANT M31 "escalation trusts the flag": the ``is_present(dome)`` test in
    ``_on_unsafe``'s ``closing`` reverted to the ``connected`` flag: RED
    (observed), the run pauses with the roof OPEN.
    """
    dome = sim_hub.devices["dome"]
    dome.connected, dome.link_lost = False, True
    temp_store.set_safety(SafetyConfig(
        enabled=True, on_unsafe="pause", unsafe_consecutive=1,
        resume_safe_consecutive=1, max_pause_min=0, min_alt_deg=0.0,
        close_dome_on_unsafe=True))
    sim_hub.devices["safety"].force_unsafe("cloud sensor")
    sim_hub._safety_reading = SafetyReading(
        is_safe=False, reason="cloud sensor", source="Sim Safety Monitor")

    engine = SequenceEngine(sim_hub)
    engine.start(_plan())
    assert await _wait_for(lambda: not engine.running), engine.state
    assert engine.state.get("state") == "aborted"
    assert engine.state.get("end_reason") == "unsafe"
    assert await dome.shutter_state() is DomeShutterState.CLOSED


def _safety_on(temp_store) -> None:
    temp_store.set_safety(SafetyConfig(
        enabled=True, on_unsafe="pause", unsafe_consecutive=2,
        resume_safe_consecutive=1, max_pause_min=0, min_alt_deg=0.0))


async def test_the_debounce_does_not_count_a_monitor_in_doubt_as_unsafe(
        sim_hub, temp_store):
    """A debounce sample counted a monitor that read not-connected as unsafe
    outright, before it looked at the cached reading. A monitor in doubt is
    judged by the reading the poller cached, which is what a blip looked like
    before #989: here the cache says safe, so the glitch has cleared.

    MUTANT M33 "debounce trusts the flag": the ``is_present(mon)`` test in
    ``_confirm_unsafe`` reverted to ``getattr(mon, "connected", False)``: RED
    (observed), it confirms an unsafe verdict that the cached reading cleared.
    """
    mon = sim_hub.devices["safety"]
    mon.connected, mon.link_lost = False, True
    _safety_on(temp_store)
    sim_hub._safety_reading = SafetyReading(
        is_safe=True, source="Sim Safety Monitor")

    engine = SequenceEngine(sim_hub)
    assert await engine._confirm_unsafe(temp_store.cfg()) is False


@pytest.mark.parametrize("in_doubt", [True, False])
async def test_the_gate_fails_closed_on_a_monitor_only_when_it_is_absent(
        sim_hub, temp_store, bus_lines, in_doubt):
    """The engine failed closed on 'safety monitor disconnected' as soon as the
    flag read false, before it looked at any reading. A monitor that is merely
    in doubt goes on to be judged by its cached reading (safe here); one that
    is genuinely disconnected still fails closed, which the second case pins,
    and which also proves the gate is reached at all.

    MUTANT M32 "gate trusts the flag": the ``is_present(mon)`` test at the
    engine's main safety gate reverted to ``getattr(mon, "connected", False)``:
    RED (observed), the in-doubt case logs 'UNSAFE: safety monitor
    disconnected'.
    """
    mon = sim_hub.devices["safety"]
    mon.connected, mon.link_lost = False, in_doubt
    _safety_on(temp_store)
    sim_hub._safety_reading = SafetyReading(
        is_safe=True, source="Sim Safety Monitor")

    def said() -> bool:
        return any("safety monitor disconnected" in m for _l, m, _s in bus_lines)

    engine = SequenceEngine(sim_hub)
    engine.start(_plan())
    try:
        if in_doubt:
            assert await _wait_for(lambda: not engine.running), engine.state
            assert engine.state.get("state") == "complete", engine.state
            assert not said(), bus_lines
        else:
            assert await _wait_for(said), bus_lines
    finally:
        if engine.running:
            await engine.abort()
