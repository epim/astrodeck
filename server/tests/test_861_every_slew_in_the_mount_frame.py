# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#861: every slew reaches the mount in the frame the mount expects.

Plate solves and the catalogue are J2000; a real Alpaca mount usually works in
JNOW, and ``Hub.to_mount_frame`` converts. Four paths handed the J2000 pair
straight to ``tel.slew``: the plain goto (``center=false``), the nudge (which
calls the plain goto), the engine's uncentred setup slew and its hold
re-point. On a JNOW mount each landed 0.04 to 0.38 deg off. Each now converts
exactly once.

The conversion stays at the call sites, not in the Alpaca driver: TPPA's goto
path reads the mount's position and slews in the mount's own frame, and a
driver that precessed every slew would bend those arcs.

Also here, found and fixed with #861 because its fix widens them:
N5, the comhost served no ``equatorialsystem``, so every ASCOM-local mount
was treated as JNOW; N6, one failed EquatorialSystem probe was cached as JNOW
for the whole connection.

Precession is stubbed with an unmistakable shift (RA +0.25 h, Dec +1 deg):
the tests are about WHICH coordinates reach the mount, not about astropy.
Every coordinate here is fictional. Each test names the mutant it was shown
red under; mutants were applied to a byte copy of the file and the file was
restored from that copy (sha256 checked).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.comhost.handlers_telescope as handlers_telescope
import astrodeck.comhost.server as comhost_server
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.comhost.device import ComDevice
from astrodeck.config import AppConfig, SafetyConfig
from astrodeck.devices.alpaca import AlpacaConnection, AlpacaTelescope
from astrodeck.devices.ascom_registry import AscomDriver
from astrodeck.devices.base import DeviceError
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan

from test_862_sync_read_back import FakeAlpacaMount, alpaca_tel
from test_centring_settings_reach_goto import _setup, _target
from test_cloud_hold_watch import _target as _hold_target
from test_idle_park_hold import sim_hub, temp_store  # noqa: F401 (fixtures)


def _stub_precession(monkeypatch) -> None:
    monkeypatch.setattr(hub_module, "precess_j2000_to_jnow",
                        lambda ra, dec, when=None: ((ra + 0.25) % 24.0,
                                                    dec + 1.0))
    monkeypatch.setattr(hub_module, "precess_jnow_to_j2000",
                        lambda ra, dec, when=None: ((ra - 0.25) % 24.0,
                                                    dec - 1.0))


@pytest.fixture
def app_hub(monkeypatch):
    """The process app hub, with its frame caches cleared for this test and
    every lane it spawned cancelled afterwards (xdist shares the process)."""
    hub = app_module.hub
    monkeypatch.setattr(hub, "_mount_wants_jnow", None)
    monkeypatch.setattr(hub, "_mount_jnow_reprobe", None)
    monkeypatch.setattr(hub, "_precess_memo", None)
    monkeypatch.setattr(hub, "_check_solar", lambda ra, dec, **kw: None,
                        raising=False)
    _stub_precession(monkeypatch)
    yield hub
    for name, task in list(hub._busy.items()):
        if task is not None and not task.done():
            task.cancel()
        hub._busy.pop(name, None)


def _await_goto(client) -> None:
    task = app_module.hub._busy.get("goto")
    assert task is not None, "the route never spawned a goto lane"

    async def _wait():
        await asyncio.wait_for(asyncio.shield(task), 5.0)

    client.portal.call(_wait)


def _post(app_hub, monkeypatch, tel, path, body) -> None:
    monkeypatch.setattr(app_hub, "require", lambda role: tel)
    app = app_module.create_app()
    with TestClient(app) as c:
        r = c.post(path, json=body)
        assert r.status_code == 200, r.text
        _await_goto(c)


def _slew_puts(fake: FakeAlpacaMount) -> list[tuple[float, float]]:
    return [(float(p["RightAscension"]), float(p["Declination"]))
            for v, m, p in fake.requests
            if v == "PUT" and m == "slewtocoordinatesasync"]


# ------------------------------------------------------- the API's two paths

def test_a_plain_goto_reaches_a_jnow_alpaca_mount_in_its_own_frame(
        app_hub, monkeypatch):
    """MUTANT M861-1 "``_plain_goto`` slews the J2000 pair" (``await
    tel.slew(slew_ra, slew_dec)`` back to ``await tel.slew(ra_hours,
    dec_deg)``): RED (observed), ``[(10.0, 40.0)] == [(10.25, 41.0)]``."""
    fake = FakeAlpacaMount(equatorial_system=1)
    tel = alpaca_tel(fake)
    _post(app_hub, monkeypatch, tel, "/api/mount/goto",
          {"ra_hours": 10.0, "dec_deg": 40.0, "center": False, "force": True})
    assert _slew_puts(fake) == [(pytest.approx(10.25), pytest.approx(41.0))]


def test_a_plain_goto_leaves_a_j2000_alpaca_mount_alone(app_hub, monkeypatch):
    """MUTANT M861-2 "``_plain_goto`` precesses unconditionally" (the
    ``hub.to_mount_frame(...)`` call replaced by
    ``asyncio.to_thread(hub_module.precess_j2000_to_jnow, ra_hours,
    dec_deg)``): RED (observed), ``[(10.25, 41.0)] == [(10.0, 40.0)]``."""
    fake = FakeAlpacaMount(equatorial_system=2)
    tel = alpaca_tel(fake)
    _post(app_hub, monkeypatch, tel, "/api/mount/goto",
          {"ra_hours": 10.0, "dec_deg": 40.0, "center": False, "force": True})
    assert _slew_puts(fake) == [(pytest.approx(10.0), pytest.approx(40.0))]


def test_a_stop_during_the_frame_conversion_still_fences_the_goto(
        app_hub, monkeypatch, bus_lines):
    """The conversion's EquatorialSystem probe is a device read, so a STOP can
    land while it awaits. The epoch is read BEFORE the conversion, so the
    fence still sees the STOP.

    MUTANT M861-3 "conversion before the epoch read" (the inserted line moved
    above ``epoch = hub._motion_epoch``): RED (observed), the slew goes out."""
    fake = FakeAlpacaMount(equatorial_system=1,
                           on_get={"equatorialsystem":
                                   app_hub.bump_motion_epoch})
    tel = alpaca_tel(fake)
    _post(app_hub, monkeypatch, tel, "/api/mount/goto",
          {"ra_hours": 10.0, "dec_deg": 40.0, "center": False, "force": True})
    assert _slew_puts(fake) == []
    assert any(m == "goto abandoned: aborted before motion"
               for _, m, _ in bus_lines), bus_lines


def test_a_nudge_on_a_jnow_mount_lands_in_the_mount_frame(app_hub,
                                                          monkeypatch):
    """JNOW (6.0, 40.0) -> J2000 (5.75, 39.0) -> +30' Dec (5.75, 39.5) ->
    mount frame (6.0, 40.5). Converted exactly once each way.

    MUTANT M861-1 (``_plain_goto`` slews the J2000 pair): RED (observed),
    ``[(5.75, 39.5)] == [(6.0, 40.5)]``."""
    fake = FakeAlpacaMount(ra=6.0, dec=40.0, equatorial_system=1)
    tel = alpaca_tel(fake)
    _post(app_hub, monkeypatch, tel, "/api/mount/nudge",
          {"axis": "dec", "arcmin": 30.0})
    assert _slew_puts(fake) == [(pytest.approx(6.0), pytest.approx(40.5))]


# ----------------------------------------------------- the engine's two paths

def _real_conversion(hub) -> None:
    """Bind the REAL hub frame conversion onto a bare engine hub double."""
    hub._mount_wants_jnow = None
    hub._mount_expects_jnow = Hub._mount_expects_jnow.__get__(hub)
    hub.to_mount_frame = Hub.to_mount_frame.__get__(hub)


async def test_an_uncentred_target_slews_a_jnow_mount_in_its_own_frame(
        monkeypatch):
    """MUTANT M861-4 "the uncentred branch slews J2000" (``engine.py``'s
    ``_setup_target`` slew back to ``tel.slew(target.ra_hours,
    target.dec_deg)``): RED (observed), the J2000 pair in the calls."""
    _stub_precession(monkeypatch)
    t = _target(center=False)
    e, hub = _setup(t)
    _real_conversion(hub)
    hub.tel.backend = "alpaca"       # no ``_get``: the probe defaults to JNOW
    await e._setup_target(0, t)
    slews = [c for c in hub.tel.calls if isinstance(c, tuple)
             and c[0] == "slew"]
    assert slews == [("slew", pytest.approx(t.ra_hours + 0.25),
                      pytest.approx(t.dec_deg + 1.0))], hub.tel.calls


async def test_the_hold_repoint_slews_a_jnow_mount_in_its_own_frame(
        sim_hub, monkeypatch):
    """The real ``_hold_repoint`` on the simulator's hub and mount, with the
    mount flagged as a JNOW Alpaca mount. The slew gate is answered "clear"
    (its altitude half depends on the wall clock, and this test is about the
    coordinates that reach the mount, not about the gate).

    MUTANT M861-5 "the hold re-point slews J2000" (``_hold_repoint``'s slew
    back to ``tel.slew(target.ra_hours, target.dec_deg)``): RED (observed),
    ``[(7.0, 40.0)] == [(7.25, 41.0)]``."""
    _stub_precession(monkeypatch)
    e = SequenceEngine(sim_hub)
    alpha = _hold_target("Alpha", 5.0, 20.0)
    bravo = _hold_target("Bravo", 7.0, 40.0)
    e.plan = SequencePlan(name="r", meridian_flip=False, safety_check=False,
                          targets=[alpha, bravo])
    e._cfg = AppConfig(safety=SafetyConfig(enabled=False))
    e._tracked_target = alpha

    async def clear_gate(*args, **kwargs):
        return None

    monkeypatch.setattr(e, "_safety_gate", clear_gate)
    tel = sim_hub.devices["telescope"]
    monkeypatch.setattr(tel, "backend", "alpaca", raising=False)
    monkeypatch.setattr(sim_hub, "_mount_wants_jnow", True)
    slews: list[tuple[float, float]] = []
    inner = tel.slew

    async def slew(ra_hours, dec_deg):
        slews.append((ra_hours, dec_deg))
        return await inner(ra_hours, dec_deg)

    monkeypatch.setattr(tel, "slew", slew)
    assert await e._hold_repoint(bravo, elsewhere=True) is None
    assert slews == [(pytest.approx(7.25), pytest.approx(41.0))], slews


async def test_the_conversion_runs_inside_the_slews_bound(monkeypatch):
    """A conversion that never answers is bounded by the slew's own bound
    and ends as the SafetyAbort every bounded mount call ends as.

    MUTANT M861-6 "conversion outside the bound" (the uncentred call site
    changed to ``ra, dec = await self.hub.to_mount_frame(...)`` before
    ``_bounded(tel.slew(ra, dec), ...)``): RED (observed), the outer
    TimeoutError, not SafetyAbort."""
    monkeypatch.setattr(engine_mod, "SLEW_TIMEOUT_S", 0.05)
    t = _target(center=False)
    e, hub = _setup(t)
    never = asyncio.Event()

    async def to_mount_frame(tel, ra_hours, dec_deg):
        await never.wait()
        return ra_hours, dec_deg

    hub.to_mount_frame = to_mount_frame
    with pytest.raises(engine_mod.SafetyAbort):
        await asyncio.wait_for(e._setup_target(0, t), 5.0)


# --------------------------------------------------------------- N6 and N5

async def test_a_failed_frame_probe_is_not_remembered(monkeypatch):
    """N6: a failed EquatorialSystem probe is JNOW for that call only; the
    next conversion asks again and caches the definite answer.

    MUTANT M861-7 "a failed probe is cached" (the ``return True`` in the
    ``except`` arm replaced by ``equ = 1``, so the JNOW verdict is cached as
    before): RED (observed), the second call ``(10.25, 41.0)``."""
    _stub_precession(monkeypatch)
    asked: list[str] = []

    async def _get(method, **params):
        asked.append(method)
        if len(asked) == 1:
            raise DeviceError("Alpaca HTTP 500: fake timeout")
        return 2

    tel = SimpleNamespace(backend="alpaca", _get=_get)
    hub = SimpleNamespace()
    _real_conversion(hub)
    assert await hub.to_mount_frame(tel, 10.0, 40.0) == (
        pytest.approx(10.25), pytest.approx(41.0))
    assert await hub.to_mount_frame(tel, 10.0, 40.0) == (10.0, 40.0)
    assert asked == ["equatorialsystem", "equatorialsystem"]
    assert await hub.to_mount_frame(tel, 10.0, 40.0) == (10.0, 40.0)
    assert len(asked) == 2, "a definite answer is cached"


async def test_a_failed_frame_probe_spares_the_status_poll(monkeypatch):
    """Fix round 1: a mount that never answers the probe is asked again by
    the READ path (``from_mount_frame``, run by every 2 s status poll) only
    after ``_JNOW_REPROBE_HOLDOFF_S``; a slew or sync (``to_mount_frame``)
    always asks; the hold-off belongs to the mount that failed.

    MUTANT M861-9 "no hold-off" (the hold-off's ``return True`` deleted, so
    every read asks): RED (observed), the second read asks.
    MUTANT M861-10 "a slew honours the hold-off" (the
    ``self._mount_jnow_reprobe = None`` line in ``to_mount_frame`` deleted):
    RED (observed), the slew does not ask.
    MUTANT M861-11 "one hold-off for every mount" (``held[0] is tel``
    dropped): RED (observed), the second mount is not asked.
    MUTANT M861-12 "the hold-off never ends" (``time.monotonic() <
    held[1]`` dropped): RED (observed), the expired hold-off still spares
    the read."""
    _stub_precession(monkeypatch)
    asked: list[str] = []

    def never_answers(label):
        async def _get(method, **params):
            asked.append(label)
            raise DeviceError("Alpaca HTTP 500: fake")
        return SimpleNamespace(backend="alpaca", _get=_get)

    tel = never_answers("first")
    hub = SimpleNamespace(_precess_memo=None)
    _real_conversion(hub)
    hub.from_mount_frame = Hub.from_mount_frame.__get__(hub)
    jnow_read = (pytest.approx(10.0), pytest.approx(40.0))

    # Two status polls: the first asks and fails, the second does not ask.
    assert await hub.from_mount_frame(tel, 10.25, 41.0) == jnow_read
    assert await hub.from_mount_frame(tel, 10.25, 41.5) == (
        pytest.approx(10.0), pytest.approx(40.5))
    assert asked == ["first"], "a status poll inside the hold-off asked"

    # A slew asks whatever the hold-off says, and still precesses.
    assert await hub.to_mount_frame(tel, 10.0, 40.0) == (
        pytest.approx(10.25), pytest.approx(41.0))
    assert asked == ["first", "first"], "a slew did not ask"
    assert await hub.from_mount_frame(tel, 10.25, 41.0) == jnow_read
    assert asked == ["first", "first"]

    # Another mount is asked at once.
    other = never_answers("other")
    assert await hub.from_mount_frame(other, 10.25, 41.0) == jnow_read
    assert asked == ["first", "first", "other"], "the second mount was held"

    # Once the hold-off has run out, the read asks again.
    monkeypatch.setattr(hub_module, "_JNOW_REPROBE_HOLDOFF_S", 0.0)
    assert await hub.to_mount_frame(tel, 10.0, 40.0) == (
        pytest.approx(10.25), pytest.approx(41.0))
    assert await hub.from_mount_frame(tel, 10.25, 41.0) == jnow_read
    assert asked == ["first", "first", "other", "first", "first"], (
        "an expired hold-off still spared the read")


class _J2000Scope:
    """A COM telescope that works in J2000 (ASCOM EquatorialSystem 2)."""

    def __init__(self):
        self.Connected = False
        self.EquatorialSystem = 2


@pytest.fixture()
def j2000_comhost(monkeypatch):
    """The real comhost, serving the PRODUCTION telescope table, over a fake
    COM factory (no comtypes)."""
    monkeypatch.setattr(comhost_server, "_make_com_device",
                        lambda progid: ComDevice(
                            progid, create=lambda pid: _J2000Scope()))
    monkeypatch.setitem(comhost_server.DEVICE_API, "telescope",
                        {"get": {}, "put": {}})
    handlers_telescope.register()
    srv = comhost_server.serve(port=0, drivers=[
        AscomDriver("Telescope", "telescope", "Fake.Scope", "Fake Scope", 0)])
    yield srv.server_address[1]
    srv.shutdown()


async def test_an_ascom_local_j2000_driver_is_not_precessed(j2000_comhost,
                                                            monkeypatch):
    """N5: the comhost now serves ``equatorialsystem``, so a COM driver that
    works in J2000 is not precessed.

    MUTANT M861-8 "no equatorialsystem route" (the new
    ``g["equatorialsystem"]`` line deleted): RED (observed), the comhost
    answers 500, the probe falls back to JNOW, ``(10.25, 41.0)``."""
    _stub_precession(monkeypatch)
    conn = AlpacaConnection("127.0.0.1", j2000_comhost)
    try:
        tel = AlpacaTelescope(conn, 0, "Fake Scope")
        await conn.put("telescope", 0, "connected", Connected=True)
        hub = SimpleNamespace()
        _real_conversion(hub)
        assert await hub.to_mount_frame(tel, 10.0, 40.0) == (10.0, 40.0)
    finally:
        await conn.close()
