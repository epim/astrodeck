# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""One gate on the rig position latch, at every move aimed from the believed
position (#886, "safety rides value paths").

The latch (``Telescope.position_known`` False, carried across a replaced
telescope object by ``devices.base.rig_position_known``) was read by the
paths that produced the evidence: the resume ladder, setup, the nudge and
dawn park. Home, the meridian flip, the hold re-point, the tracking recovery,
the run-end park and the plain goto and park routes did not read it, so each
could aim a goto or a park from a position nothing knew. They all ask one
helper now (``devices.base.position_known_for_motion``):

- the routes through ``api.app._refuse_if_position_unknown`` (409
  ``position_unknown``, the safe order in the detail, before the fence bump)
  and ``_abandon_if_position_unknown`` (the same gate asked again under the
  motion lock, right before the device command);
- the engine through ``SequenceEngine._position_unknown``: the flip, the hold
  re-point and the tracking recovery end the RUN with ``PositionUnknownStop``
  and no motion; the run-end park is skipped and tracking stopped, read back
  and asked again instead, and a roof that needs a parked tube is not
  closed over the tube nobody parked (a roof that needs none still closes).
  The hold re-point and the run-end park ask again after a wait that can
  latch it (the safety gate's pause, the motion lock).

A pad jog stays allowed (test_oct08_position_latch.py's jog case): it
computes no destination and is the way home by eye. The home route's cases
are in test_w15_mount_home_log_needs_a_known_position.py.

Each case drives the latch set and asserts no slew, park, unpark or goto
reached the driver; each control shows the same path moving once Trust
position cleared it. Every mutant named below was applied to a byte backup
of its production file, run under the suite's normal command, and the file
restored from the backup with its sha256 checked. Coordinates are made up;
no mount position is printed.
"""
from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.sequence.engine as engine_mod
import astrodeck.sequence.roof as roof_mod
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.devices.base import RIG_DOUBT_ATTR, rig_position_known
from astrodeck.devices.sim import SimTelescope
from astrodeck.mount_offset import (POSITION_UNKNOWN_CODE,
                                    POSITION_UNKNOWN_MOTION_DETAIL)
from astrodeck.sequence.engine import PositionUnknownStop, SequenceEngine

from test_850_engine_sync_refused import _humanizer_rewrites, flip_hub  # noqa: F401
from test_api_mount import FakeAuthProvider
from test_recovery_centring_is_measured import (  # noqa: F401 (fixtures)
    _engine, _pinned_mount, sim_hub, temp_store)
import test_the_flip_stops_paying_for_itself as flp

#: The humanizer's cut (ui/src/lib/humanize.ts): 137 characters plus "...".
_CUT = 137
_GOTO_WORDS = ("goto", "go to", "slew")
_REASON = "a fictional reason"


def _check_words(line: str) -> None:
    """The wording rules for every new line: the safe order inside the cut,
    no goto word anywhere, nothing the UI's humanizer rewrites."""
    head = line[:_CUT]
    assert "Trust position" in head and "pad key" in head, head
    for word in _GOTO_WORDS:
        assert word not in line.lower(), (word, line)
    assert not _humanizer_rewrites(line), line


def test_the_detail_keeps_the_rules():
    """The routes' 409 detail and the engine's line are one copy."""
    assert len(POSITION_UNKNOWN_MOTION_DETAIL) <= _CUT
    _check_words(POSITION_UNKNOWN_MOTION_DETAIL)
    assert (SequenceEngine._POSITION_UNKNOWN_ACTION
            == POSITION_UNKNOWN_MOTION_DETAIL)


# ============================================================ the routes

class _RouteTel(SimTelescope):
    """The simulator mount with every command that aims a move recorded and
    nothing sent. The latch is the base class's own."""

    def __init__(self):
        super().__init__("plain")
        self.moves: list[str] = []
        self.parked = False

    async def is_parked(self) -> bool:
        return self.parked

    async def unpark(self) -> None:
        self.moves.append("unpark")

    async def set_tracking(self, on: bool) -> None:
        pass

    async def slew(self, ra_hours: float, dec_deg: float) -> None:
        self.moves.append("slew")

    async def park(self) -> None:
        self.moves.append("park")


@pytest.fixture(autouse=True)
def _operator():
    set_active_provider(FakeAuthProvider(principal_for_role("operator")))
    yield
    reset_active_provider()


@pytest.fixture
def routes(monkeypatch):
    """The shipped app, its lifespan running so a spawned move runs, over a
    recording mount the hub returns for every ``require``. ``calls`` counts
    the requires: the route's own is the first, the spawned task's the
    second."""
    hub = app_module.hub
    tel = _RouteTel()
    calls = {"n": 0, "latch_on": None}

    def require(role):
        calls["n"] += 1
        if calls["latch_on"] is not None and calls["n"] >= calls["latch_on"]:
            tel.mark_position_unknown(_REASON)
        return tel
    monkeypatch.setattr(hub, "require", require)
    monkeypatch.setitem(hub.devices, "telescope", tel)
    monkeypatch.setattr(hub, "_check_solar",
                        lambda ra, dec, *, force=False: None)
    monkeypatch.setattr(hub, "_check_horizon", lambda ra, dec: None,
                        raising=False)
    gotos: list[tuple] = []

    async def goto_and_center(*a, **kw):
        gotos.append(a)
        return {"centered": True, "error_arcmin": 0.1}
    monkeypatch.setattr(hub, "goto_and_center", goto_and_center)
    with TestClient(app_module.create_app()) as c:
        yield c, tel, calls, gotos


def _settle(calls, n: int = 2) -> None:
    """Wait (wall clock, #669) for the spawned move's task to finish."""
    hub = app_module.hub
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        task = hub._busy.get("goto")
        if calls["n"] >= n and (task is None or task.done()):
            return
        time.sleep(0.01)
    raise AssertionError(f"the spawned move never finished: {calls}")


_GOTO = {"ra_hours": 7.25, "dec_deg": 30.0, "center": False, "force": True}


def _refused(r) -> None:
    assert r.status_code == 409, (r.status_code, r.text)
    assert r.json()["detail"] == {"detail": POSITION_UNKNOWN_MOTION_DETAIL,
                                  "code": POSITION_UNKNOWN_CODE}, r.text


@pytest.mark.parametrize("center", [False, True], ids=["plain", "centred"])
def test_a_goto_from_an_unknown_position_is_refused(routes, center):
    """A goto, plain or centred, is aimed from the believed position.

    MUTANT P1 "goto ungated" (the ``_refuse_if_position_unknown(tel)`` call
    in the goto route removed): RED, both -
        AssertionError: (200, '{"started":"goto"}')
    """
    c, tel, _calls, gotos = routes
    tel.mark_position_unknown(_REASON)
    r = c.post("/api/mount/goto", json=_GOTO | {"center": center})
    _refused(r)
    time.sleep(0.2)
    assert tel.moves == [] and gotos == [], (tel.moves, gotos)


def test_a_park_from_an_unknown_position_is_refused(routes):
    """On the AM5 a park is a goto to the MODEL's home.

    MUTANT P2 "park ungated" (the ``_refuse_if_position_unknown(tel)`` call
    in the park route removed): RED -
        AssertionError: (200, '{"started":"goto"}')
    """
    c, tel, _calls, _gotos = routes
    tel.mark_position_unknown(_REASON)
    _refused(c.post("/api/mount/park"))
    time.sleep(0.2)
    assert tel.moves == [], tel.moves


def test_the_route_gate_reads_the_carried_doubt(routes, monkeypatch):
    """A doubt the hub carries from the telescope object a profile activate
    replaced refuses too, before any tick moved it onto the new object."""
    c, tel, _calls, _gotos = routes
    monkeypatch.setattr(app_module.hub, RIG_DOUBT_ATTR,
                        (SimTelescope("plain"), _REASON), raising=False)
    _refused(c.post("/api/mount/park"))
    assert tel.position_known is False


@pytest.mark.parametrize("path, body, what", [
    ("/api/mount/goto", _GOTO, "pointing"),
    ("/api/mount/park", None, "park"),
], ids=["goto", "park"])
def test_a_position_lost_while_the_move_waits_sends_nothing(
        routes, bus_lines, path, body, what):
    """The gate at the seam: the route's gate passed, and the latch was set
    before the spawned move's turn at the motion lock (an AM5 link reopen
    that reads the home pole). Nothing reaches the driver, and one warning
    in the safe order says nothing was moved.

    MUTANT P3 "plain goto ungated at the seam" (``if _abandon_if_position_
    unknown(tel, "pointing"): return`` in ``_plain_goto`` removed): RED,
    goto -
        AssertionError: ['slew']
    MUTANT P4 "park ungated at the seam" (``if _abandon_if_position_
    unknown(tel, "park"): return`` in ``_park`` removed): RED, park -
        AssertionError: ['park']
    """
    c, tel, calls, _gotos = routes
    calls["latch_on"] = 2
    r = c.post(path, json=body) if body else c.post(path)
    assert r.status_code == 200, r.text
    _settle(calls)
    assert tel.moves == [], tel.moves
    said = [m for _l, m, _s in bus_lines
            if m.endswith(f"Nothing was moved ({what})")]
    assert len(said) == 1, bus_lines
    _check_words(said[0])


@pytest.mark.parametrize("path, body, moved", [
    ("/api/mount/goto", _GOTO, ["slew"]),
    ("/api/mount/park", None, ["park"]),
], ids=["goto", "park"])
def test_control_after_trust_position_the_route_moves(routes, path, body,
                                                      moved):
    """CONTROL. Trust position clears the latch and the same request moves
    the mount."""
    c, tel, calls, _gotos = routes
    tel.mark_position_unknown(_REASON)
    _refused(c.post(path, json=body) if body else c.post(path))
    assert c.post("/api/mount/trust-position").status_code == 200
    base = calls["n"]
    r = c.post(path, json=body) if body else c.post(path)
    assert r.status_code == 200, r.text
    _settle(calls, n=base + 2)
    assert tel.moves == moved, tel.moves


# ================================================= the engine: the flip

async def test_the_flip_ends_the_run_without_moving_the_mount(
        flip_hub, monkeypatch, bus_lines):
    """The flip is a goto from the believed position: the run ends with
    ``PositionUnknownStop`` and the flip's goto never runs.

    MUTANT E1 "flip ungated" (the ``await self._gate_position_known(target,
    "the meridian flip")`` in `_maybe_meridian_flip` removed): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    e, t, st = flp._flip_engine(flip_hub, monkeypatch, the_slew_flips=True)
    tel = flip_hub.devices["telescope"]
    tel.mark_position_unknown(_REASON)
    with pytest.raises(PositionUnknownStop):
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert st["gotos"] == [], st["gotos"]
    said = [m for lvl, m, _s in bus_lines if lvl == "warning"]
    assert said and said[-1].endswith(
        "the run stops without moving the mount (the meridian flip)"), said
    _check_words(said[-1])


async def test_control_after_trust_position_the_flip_moves(flip_hub,
                                                           monkeypatch):
    """CONTROL. The same flip, once Trust position cleared the latch."""
    e, t, st = flp._flip_engine(flip_hub, monkeypatch, the_slew_flips=True)
    tel = flip_hub.devices["telescope"]
    tel.mark_position_unknown(_REASON)
    await tel.trust_position()
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert len(st["gotos"]) == 1, st["gotos"]


# ========================================== the engine: the hold re-point

def _hold_engine(sim_hub, monkeypatch):
    e, t = _engine(sim_hub)
    tel = sim_hub.devices["telescope"]
    moves: list[str] = []
    gates: list[str] = []

    async def slew(ra, dec):
        moves.append("slew")

    async def unpark():
        moves.append("unpark")

    async def gate(*, context, target):
        gates.append(context)
    monkeypatch.setattr(tel, "slew", slew)
    monkeypatch.setattr(tel, "unpark", unpark)
    e._safety_gate = gate
    return e, t, tel, moves, gates


async def test_the_hold_re_point_ends_the_run_without_moving_the_mount(
        sim_hub, monkeypatch, bus_lines):
    """The cloud hold's re-point is a goto from the believed position.

    MUTANT E2 "hold re-point ungated" (the ``await self._gate_position_
    known(target, "re-pointing after the hold")`` in `_hold_repoint`
    removed): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    e, t, tel, moves, gates = _hold_engine(sim_hub, monkeypatch)
    tel.mark_position_unknown(_REASON)
    with pytest.raises(PositionUnknownStop):
        await e._hold_repoint(t, elsewhere=True)
    assert moves == [] and gates == [], (moves, gates)
    said = [m for lvl, m, _s in bus_lines if lvl == "warning"]
    assert said[-1].endswith("(re-pointing after the hold)"), said
    _check_words(said[-1])


async def test_control_after_trust_position_the_hold_re_points(
        sim_hub, monkeypatch):
    """CONTROL. The same re-point slews once Trust position cleared it."""
    e, t, tel, moves, _gates = _hold_engine(sim_hub, monkeypatch)
    tel.mark_position_unknown(_REASON)
    await tel.trust_position()
    assert await e._hold_repoint(t, elsewhere=True) is None
    assert moves == ["slew"], moves


async def test_a_latch_set_in_the_hold_s_own_gate_stops_the_re_point(
        sim_hub, monkeypatch, bus_lines):
    """The safety gate can pause for its whole bound and return into the
    re-point, and a link reopen in that pause can latch the position. The
    re-point asks again after the gate, as the flip and the recovery do,
    so nothing reaches the driver (round 2).

    MUTANT E6 "the hold re-point asked once" (the second ``await self._gate_
    position_known(target, "re-pointing after the hold")``, after
    ``_cancel_idle_stop_retry`` in `_hold_repoint`, removed): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    e, t, tel, moves, gates = _hold_engine(sim_hub, monkeypatch)

    async def gate_that_latches(*, context, target):
        gates.append(context)
        tel.mark_position_unknown(_REASON)
    e._safety_gate = gate_that_latches
    with pytest.raises(PositionUnknownStop):
        await e._hold_repoint(t, elsewhere=True)
    assert gates == ["slew"], gates
    assert moves == [], moves
    said = [m for lvl, m, _s in bus_lines if lvl == "warning"]
    assert said[-1].endswith("(re-pointing after the hold)"), said


async def test_the_engine_reads_a_doubt_carried_from_a_replaced_object(
        sim_hub, monkeypatch):
    """A profile activate built a NEW telescope object, which reads known;
    the hub still carries the doubt taken on the old one. The engine asks
    the one gate (``position_known_for_motion``), which reads the hub's
    record, so the re-point stops the run with no slew (round 2).

    MUTANT X3 "the engine reads only the object" (``return tel is not None
    and not position_known_for_motion(self.hub, tel)`` in
    `SequenceEngine._position_unknown` made ``return tel is not None and
    not getattr(tel, "position_known", True)``): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    e, t, tel, moves, _gates = _hold_engine(sim_hub, monkeypatch)
    assert tel.position_known is True
    monkeypatch.setattr(sim_hub, RIG_DOUBT_ATTR,
                        (SimTelescope("plain"), _REASON), raising=False)
    with pytest.raises(PositionUnknownStop):
        await e._hold_repoint(t, elsewhere=True)
    assert moves == [], moves


# ===================================== the engine: the tracking recovery

async def test_the_recovery_ends_the_run_before_it_spends_its_attempt(
        sim_hub, monkeypatch, bus_lines):
    """The recovery is a park, an unpark and a goto, all aimed. The run ends
    before the safety gate and before the one attempt is spent; nothing
    reaches the driver.

    MUTANT E3 "recovery ungated at its entry" (the ``await self._gate_
    position_known(target, "recovering the mount from its limit")`` in
    `_recover_from_tracking_refusal` removed): RED (the inner gate still
    stops the run, after the attempt is spent and the line said) -
        AssertionError: the one attempt was spent: {'<the target's id>'}
    """
    st = _pinned_mount(sim_hub, monkeypatch, [{"centered": True}])
    e, t = _engine(sim_hub)
    gates: list[str] = []

    async def gate(*, context, target):
        gates.append(context)
    e._safety_gate = gate
    sim_hub.devices["telescope"].mark_position_unknown(_REASON)
    with pytest.raises(PositionUnknownStop):
        await e._recover_from_tracking_refusal(t)
    assert st["events"] == [], st["events"]
    assert e._tracking_recovered == set(), (
        f"the one attempt was spent: {e._tracking_recovered}")
    assert gates == [], gates
    assert not [m for _l, m, _s in bus_lines if "parking, unparking" in m]


async def test_the_recovery_sequence_is_gated_before_its_park(
        sim_hub, monkeypatch):
    """The sequence asks again itself: the caller's safety gate can pause
    for its whole bound while a link reopen latches the position.

    MUTANT E4 "recovery sequence ungated before the park" (the first
    ``await self._gate_position_known(target, where)`` in
    `_do_tracking_recovery` removed): RED (the re-slew's gate still stops
    it, after the park) -
        AssertionError: ['park', 'unpark']
    """
    st = _pinned_mount(sim_hub, monkeypatch, [{"centered": True}])
    e, t = _engine(sim_hub)
    tel = sim_hub.devices["telescope"]
    tel.mark_position_unknown(_REASON)
    with pytest.raises(PositionUnknownStop):
        await e._do_tracking_recovery(tel, t, report_centring=True)
    assert st["events"] == [], st["events"]


async def test_the_recovery_is_gated_again_before_its_re_slew(
        sim_hub, monkeypatch):
    """A latch set during the park (a link reopen on the AM5) stops the run
    before the re-slew's goto.

    MUTANT E5 "recovery re-slew ungated" (the ``await self._gate_position_
    known(target, where)`` before the re-centre in `_do_tracking_recovery`
    removed): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    st = _pinned_mount(sim_hub, monkeypatch, [{"centered": True}])
    e, t = _engine(sim_hub)
    tel = sim_hub.devices["telescope"]
    park = tel.park

    async def park_that_latches():
        await park()
        tel.mark_position_unknown(_REASON)
    monkeypatch.setattr(tel, "park", park_that_latches)
    with pytest.raises(PositionUnknownStop):
        await e._do_tracking_recovery(tel, t, report_centring=True)
    assert st["events"] == ["park", "unpark"], st["events"]


async def test_control_after_trust_position_the_recovery_runs(
        sim_hub, monkeypatch):
    """CONTROL. The same recovery parks, unparks and re-centres once Trust
    position cleared the latch."""
    st = _pinned_mount(sim_hub, monkeypatch, [{"centered": True}])
    e, t = _engine(sim_hub)

    async def gate(*, context, target):
        return None
    e._safety_gate = gate
    tel = sim_hub.devices["telescope"]
    tel.mark_position_unknown(_REASON)
    await tel.trust_position()
    assert await e._recover_from_tracking_refusal(t) is True
    assert st["events"] == ["park", "unpark", "goto"], st["events"]


# ============================================ the engine: the run-end park

class _Mount:
    """The simulator mount's park and tracking replaced: every park and
    every stop recorded, a stop that takes only when ``obey`` is set."""

    def __init__(self, tel, monkeypatch, *, obey: bool = True):
        self.parks = 0
        self.stops = 0
        self.obey = obey
        self.tracking = True
        park = tel.park

        async def recorded_park():
            self.parks += 1
            await park()

        async def set_tracking(on):
            if not on:
                self.stops += 1
                if self.obey:
                    self.tracking = False
            else:
                self.tracking = True

        async def get_tracking():
            return self.tracking
        monkeypatch.setattr(tel, "park", recorded_park)
        monkeypatch.setattr(tel, "set_tracking", set_tracking)
        monkeypatch.setattr(tel, "get_tracking", get_tracking)


async def test_the_run_end_park_is_skipped_and_tracking_stopped(
        sim_hub, monkeypatch, bus_lines):
    """The latch set during the run some other way than a
    ``PositionUnknownStop`` (an AM5 reopen that read the home pole): the
    run-end park is skipped, tracking is stopped and read back, and one
    warning says so in the safe order.

    MUTANT W1 "the run-end park ungated" (``if self._position_unknown():
    await self._quiet_stop_for_unknown_position(); return None`` in
    `_wind_down_park` removed): RED. Since round 2 the second ask under
    the motion lock (mutant W4) still skips the park, so the outer gate's
    loss shows as the park's announcement and its fence bump going out -
        AssertionError: assert not ['parking mount']
    """
    tel = sim_hub.devices["telescope"]
    mount = _Mount(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    tel.mark_position_unknown(_REASON)
    await e._wind_down_park_and_close(True, False)
    assert mount.parks == 0, f"the park ran: {mount.parks}"
    assert mount.stops == 1 and mount.tracking is False
    said = [m for _l, m, _s in bus_lines if "The run ends without parking" in m]
    assert len(said) == 1, bus_lines
    _check_words(said[0])
    assert not [m for _l, m, _s in bus_lines if "parking mount" in m]


async def test_a_skipped_park_s_stop_is_read_back_and_asked_again(
        sim_hub, monkeypatch, bus_lines):
    """The mount ignores the stop: the read-back says so and the stop is
    asked again on its own clock, as `_confirm_quiet_stop` does after a
    ``PositionUnknownStop``.

    MUTANT W2 "fire and forget" (the ``await self._confirm_quiet_stop()``
    in `_quiet_stop_for_unknown_position` removed): RED -
        AssertionError: [] (no "tracking not confirmed off" line)
    """
    monkeypatch.setattr(engine_mod, "IDLE_STOP_RETRY_S", 0.05)
    monkeypatch.setattr(engine_mod, "TRACKING_CONFIRM_S", 0.0)
    tel = sim_hub.devices["telescope"]
    mount = _Mount(tel, monkeypatch, obey=False)
    e = SequenceEngine(sim_hub)
    tel.mark_position_unknown(_REASON)
    await e._wind_down_park_and_close(True, False)
    said = [m for _l, m, _s in bus_lines
            if m.startswith("tracking not confirmed off")]
    assert len(said) == 1, said
    before = mount.stops
    mount.obey = True
    deadline = time.monotonic() + 5.0
    while mount.stops == before and time.monotonic() < deadline:
        await engine_mod.asyncio.sleep(0.02)
    assert mount.stops > before, "the stop was never asked again"
    assert mount.parks == 0


async def test_the_roof_is_not_closed_over_the_tube_nobody_parked(
        sim_hub, monkeypatch, bus_lines):
    """With the park skipped for an unknown position the roof is left
    alone, as `_run`'s position-unknown arm leaves it, and said at error
    level.

    The stub roof has no ``requires_park_before_close``, so it takes the
    flag's fail-safe True: a roof that needs a parked tube.

    MUTANT W3 "the roof closes anyway" (``if close_dome and unknown and
    self._roof_needs_a_parked_tube():`` in `_wind_down_park_and_close` made
    ``if False:``): RED -
        AssertionError: the roof close ran: ['close']
    """
    closes: list[str] = []

    async def close_observatory(dome, tel, *, log):
        closes.append("close")
        return True
    monkeypatch.setattr(roof_mod, "close_observatory", close_observatory)
    monkeypatch.setitem(sim_hub.devices, "dome",
                        type("D", (), {"connected": True})())
    tel = sim_hub.devices["telescope"]
    _Mount(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    tel.mark_position_unknown(_REASON)
    await e._wind_down_park_and_close(True, True)
    assert closes == [], f"the roof close ran: {closes}"
    said = [m for lvl, m, _s in bus_lines
            if lvl == "error" and m.startswith("the roof was not closed")]
    assert len(said) == 1, bus_lines
    for word in _GOTO_WORDS:
        assert word not in said[0].lower()
    assert not _humanizer_rewrites(said[0])


async def test_control_a_known_position_parks_and_closes(
        sim_hub, monkeypatch):
    """CONTROL. Once Trust position cleared it, the run-end park parks and
    the roof close runs."""
    closes: list[str] = []

    async def close_observatory(dome, tel, *, log):
        closes.append("close")
        return True
    monkeypatch.setattr(roof_mod, "close_observatory", close_observatory)
    monkeypatch.setitem(sim_hub.devices, "dome",
                        type("D", (), {"connected": True})())
    tel = sim_hub.devices["telescope"]
    mount = _Mount(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    tel.mark_position_unknown(_REASON)
    await tel.trust_position()
    await e._wind_down_park_and_close(True, True)
    assert mount.parks >= 1
    assert closes == ["close"]


class _NoParkRoof:
    """A connected roof that clears the tube wherever it points
    (``requires_park_before_close`` False)."""
    connected = True
    requires_park_before_close = False


def _recorded_close(monkeypatch) -> list[str]:
    closes: list[str] = []

    async def close_observatory(dome, tel, *, log):
        closes.append("close")
        return True
    monkeypatch.setattr(roof_mod, "close_observatory", close_observatory)
    return closes


async def test_a_roof_that_needs_no_park_still_closes(
        sim_hub, monkeypatch, bus_lines):
    """The doubt keeps a roof open only when the roof needs a parked tube.
    One that clears the tube wherever it points closes as it would, in
    the rain too: `_on_unsafe`'s roof branch winds down with a park and a
    close, the park is skipped, and the roof must still shut (round 2).

    MUTANT R1 "every roof waits for a park" (``and self._roof_needs_a_
    parked_tube()`` dropped from ``if close_dome and unknown and ...`` in
    `_wind_down_park_and_close`, the round-1 condition): RED -
        AssertionError: the roof stayed open: []
    """
    closes = _recorded_close(monkeypatch)
    monkeypatch.setitem(sim_hub.devices, "dome", _NoParkRoof())
    tel = sim_hub.devices["telescope"]
    mount = _Mount(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    tel.mark_position_unknown(_REASON)
    await e._wind_down_park_and_close(True, True)
    assert mount.parks == 0, mount.parks
    assert closes == ["close"], f"the roof stayed open: {closes}"
    assert not [m for _l, m, _s in bus_lines
                if m.startswith("the roof was not closed")]


async def test_a_position_unknown_stop_closes_a_roof_that_needs_no_park(
        sim_hub, temp_store, monkeypatch):
    """The REAL run's position-unknown arm, ``close_dome_on_unsafe`` set: a
    roof that needs no parked tube is closed; still no park. The roof that
    does need one stays open (test_851_pointing_recheck.py's
    ``test_the_position_unknown_stop_does_not_park_or_close``).

    MUTANT R2 "the quiet arm never closes" (``and (not quiet or not
    self._roof_needs_a_parked_tube())`` in `_run`'s unsafe arm made ``and
    not quiet``, the round-1 condition): RED -
        AssertionError: the roof stayed open: []
    """
    from astrodeck.config import SafetyConfig
    from test_851_pointing_recheck import WHERE
    from test_engine_safety import light_plan, wait_for
    temp_store.set_safety(SafetyConfig(enabled=False,
                                       close_dome_on_unsafe=True))
    closes = _recorded_close(monkeypatch)
    monkeypatch.setitem(sim_hub.devices, "dome", _NoParkRoof())
    mount = _Mount(sim_hub.devices["telescope"], monkeypatch)
    e = SequenceEngine(sim_hub)

    async def recheck(target=None):
        await e._stop_run_position_unknown(target, WHERE)
    e._maybe_recheck_pointing = recheck
    e.start(light_plan())
    assert await wait_for(lambda: e.state.get("state") == "aborted",
                          timeout=30), e.state
    await wait_for(lambda: bool(closes), timeout=5)
    assert closes == ["close"], f"the roof stayed open: {closes}"
    assert mount.parks == 0, mount.parks


async def test_a_latch_set_while_the_park_waits_for_the_lock_parks_nothing(
        sim_hub, monkeypatch, bus_lines):
    """The run-end park can wait on the motion lock behind a bounded move
    while an AM5 reopen latches the position. It asks again under the
    lock, as the routes do, and stops tracking instead (round 2).

    MUTANT W4 "the park asks once" (the ``if self._position_unknown():``
    block inside ``async with lock:`` in `_wind_down_park` removed): RED -
        AssertionError: the park ran: 1
    """
    tel = sim_hub.devices["telescope"]
    mount = _Mount(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    lock = sim_hub._motion_lock
    async with lock:
        winding = engine_mod.asyncio.ensure_future(
            e._wind_down_park_and_close(True, False))
        deadline = time.monotonic() + 5.0
        while not [m for _l, m, _s in bus_lines if m == "parking mount"]:
            assert time.monotonic() < deadline, "the park never queued"
            await engine_mod.asyncio.sleep(0.01)
        tel.mark_position_unknown(_REASON)
    await winding
    assert mount.parks == 0, f"the park ran: {mount.parks}"
    assert mount.stops == 1 and mount.tracking is False
    assert len([m for _l, m, _s in bus_lines
                if "The run ends without parking" in m]) == 1, bus_lines


async def test_a_cancel_during_a_skipped_park_s_stop_is_no_failed_park(
        sim_hub, monkeypatch, bus_lines):
    """An Abort lands while the skipped park's tracking stop is running.
    The skipped park answers None, not False, so the cancel arm does not
    run the failed park's stop: no second stop and no "the park did not
    complete" line, which would say a park was tried (round 2).

    MUTANT X8 "a skipped park is a failed one" (``if parked is False:`` in
    the cancel arm of `_wind_down_park_and_close` made ``if not
    parked:``): RED -
        AssertionError: a failed park's line: ['the park did not ...']
    """
    tel = sim_hub.devices["telescope"]
    mount = _Mount(tel, monkeypatch)
    entered = engine_mod.asyncio.Event()
    release = engine_mod.asyncio.Event()
    stop = tel.set_tracking

    async def slow_stop(on):
        entered.set()
        await release.wait()
        await stop(on)
    monkeypatch.setattr(tel, "set_tracking", slow_stop)
    e = SequenceEngine(sim_hub)
    tel.mark_position_unknown(_REASON)
    winding = engine_mod.asyncio.ensure_future(
        e._wind_down_park_and_close(True, False))
    await engine_mod.asyncio.wait_for(entered.wait(), 5.0)
    winding.cancel()
    await engine_mod.asyncio.sleep(0)
    release.set()
    with pytest.raises(engine_mod.asyncio.CancelledError):
        await winding
    failed = [m for _l, m, _s in bus_lines
              if m.startswith("the park did not complete")]
    assert failed == [], f"a failed park's line: {failed}"
    assert mount.parks == 0 and mount.stops == 1, (mount.parks, mount.stops)


# ======================================================= the status block

@pytest.mark.parametrize("carried", [True, False],
                         ids=["carried doubt", "control: no doubt"])
async def test_the_status_block_reads_the_rig_latch(sim_hub, monkeypatch,
                                                    carried):
    """After a reconnect the hub carries the doubt from the replaced
    telescope object; the status poll reads through it, so both UIs show
    Trust position on the first poll, not on the next ladder tick. Only
    the flag is asserted; no position is printed.

    MUTANT S1 "status reads the object" (``"position_known":
    rig_position_known(self)`` in `poll_status` made ``bool(getattr(tel,
    "position_known", True))``): RED, "carried doubt" -
        AssertionError: position_known True
    """
    if carried:
        monkeypatch.setattr(sim_hub, RIG_DOUBT_ATTR,
                            (SimTelescope("plain"), _REASON), raising=False)
    out = await sim_hub.poll_status()
    known = out["mount"]["position_known"]
    assert known is (not carried), f"position_known {known}"
    assert rig_position_known(sim_hub) is (not carried)
