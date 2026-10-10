# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Five more moves aimed from the believed position ask the one gate (#888).

#886 put one gate on the rig position latch
(``devices.base.position_known_for_motion``) on the sites its issue listed.
A search for every ``park()``, ``slew(`` and ``goto_and_center(`` caller
found five more that never asked it. Each asks it now, and with the
position unknown nothing is aimed from the believed position:

- ``POST /api/dome/close`` and the engine's auto-reopen roof close
  (`_fenced_park`): no park; tracking stopped, read back and asked again
  (`_quiet_stop_for_unknown_position`); a roof that needs no parked tube
  still closes (rain included), one that needs one is left open with an
  error line in the safe order;
- the TPPA: ``POST /api/polar/start`` answers 409 ``position_unknown``, and
  the native driver refuses before it reads or moves anything;
- ``hub.goto_and_center``: the gate under its first motion lock, the aborted
  shape with ``position_unknown`` beside it; the engine stops the run on it,
  the meridian flip neither flips the calibration nor restarts guiding, and
  the resume ladder holds (test_888_ladder_reads_the_hub_refusal.py);
- the sun watch, OWNER RULING 4B (2026-10-09): no park and no tracking change
  while the position is unknown; one error line in fixed words, said again
  on the blind cadence while the Sun is above the dawn-park threshold, which
  the AlertDispatcher maps to an error alert; the state published beside the
  blind fields; the normal net back on the tick after Trust position;
- the wind-down's "set not to park" line no longer promises a dawn park
  while the latch is set (dawn park skips such a mount, #874).

Each case sets the latch and asserts no park, slew or unpark reached the
driver; each control shows the same path moving once Trust position cleared
it. Every mutant named below was applied to a byte backup of its production
file, run under the suite's normal command (xdist on), and the file restored
from the backup with its sha256 checked. Coordinates are made up; no mount
position is printed.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from starlette.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.sequence.engine as engine_mod
import astrodeck.sequence.roof as roof_mod
from astrodeck import events
from astrodeck.alerting import AlertDispatcher
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import SimTelescope
from astrodeck.mount_offset import (POSITION_UNKNOWN_CODE,
                                    POSITION_UNKNOWN_MOTION_DETAIL,
                                    POSITION_UNKNOWN_SAFE_ORDER)
from astrodeck.polar import native as nat
from astrodeck.sequence.engine import (NOT_PARKED_POSITION_UNKNOWN,
                                       ROOF_LEFT_OPEN_POSITION_UNKNOWN,
                                       PositionUnknownStop, SequenceEngine)
from astrodeck.sun_watch import (BLIND_LOG_EVERY, LEAD_TIME_S,
                                 POSITION_UNKNOWN_DAYLIGHT,
                                 POSITION_UNKNOWN_NIGHT_HOLD, closest_approach)

from test_850_engine_sync_refused import _humanizer_rewrites
from test_886_one_position_gate import _Mount, _NoParkRoof, _operator  # noqa: F401
from test_recovery_centring_is_measured import (  # noqa: F401 (fixtures)
    _engine, sim_hub, temp_store)
from test_sun_watch import FakeTel, JUNE_TS, SUN_DEC, SUN_RA_H  # noqa: F401
from test_sun_watch import cfg, pinned_sun  # noqa: F401 (fixtures)
from test_w14_sun_watch_blind_fallback import (WEST_H, _driven,
                                               _first_age_inside_cone,
                                               _tick_n)
from test_w15_sun_watch_follow_ups import SiteHub, sky  # noqa: F401

#: The humanizer's cut (ui/src/lib/humanize.ts): 137 characters plus "...".
_CUT = 137
_GOTO_WORDS = ("goto", "go to", "slew")
_REASON = "a fictional reason"


def _check_words(line: str, *, pad: str = "pad key") -> None:
    """The wording rules for every new line: the safe order inside the cut,
    no goto word anywhere, nothing the UI's humanizer rewrites."""
    head = line[:_CUT]
    assert "Trust position" in head or "Trust it" in head, head
    assert pad in head, head
    for word in _GOTO_WORDS:
        assert word not in line.lower(), (word, line)
    assert not _humanizer_rewrites(line), line


def test_the_new_lines_keep_the_wording_rules():
    """Every new operator line: the action inside the 137-character cut, no
    goto advice while the position is unknown, no humanizer pair."""
    _check_words(ROOF_LEFT_OPEN_POSITION_UNKNOWN)
    _check_words(nat.TPPA_POSITION_UNKNOWN)
    assert len("native TPPA: " + nat.TPPA_POSITION_UNKNOWN) <= _CUT
    _check_words(POSITION_UNKNOWN_DAYLIGHT)
    # Cover the tube, then the WHOLE shared safe order inside the cut
    # (round 3): "cover it, or bring it home by eye ..., then Trust
    # position" read as "cover it, then Trust position", and Trust on a
    # covered tube nobody brought home declares a wrong position known.
    # MUTANT W8 "the old daylight line" (the round-2 text put back in
    # ``POSITION_UNKNOWN_DAYLIGHT``): RED -
    #     AssertionError: sun watch cannot protect the tube, ...
    head = POSITION_UNKNOWN_DAYLIGHT[:_CUT]
    assert POSITION_UNKNOWN_SAFE_ORDER in head, POSITION_UNKNOWN_DAYLIGHT
    assert head.index("cover") < head.index(POSITION_UNKNOWN_SAFE_ORDER)
    # The night hold, said with the "sun watch held off: " prefix (round 3).
    # MUTANT W9 "goto advice in the hold" (``POSITION_UNKNOWN_NIGHT_HOLD``
    # made "position unknown: slew it to a target, then Trust position"):
    # RED - AssertionError: 'pad key' not in head
    held = "sun watch held off: " + POSITION_UNKNOWN_NIGHT_HOLD
    _check_words(held)
    assert POSITION_UNKNOWN_SAFE_ORDER in held[:_CUT], held
    # The whole safe order, not a bare "until Trust position" (round 2).
    # MUTANT W6 "the bare promise" (the old 137-character line put back):
    # RED - AssertionError: this run was set not to park when done, ...
    _check_words(NOT_PARKED_POSITION_UNKNOWN)
    assert len(NOT_PARKED_POSITION_UNKNOWN) <= _CUT
    for line in (ROOF_LEFT_OPEN_POSITION_UNKNOWN, nat.TPPA_POSITION_UNKNOWN,
                 POSITION_UNKNOWN_DAYLIGHT, NOT_PARKED_POSITION_UNKNOWN,
                 held):
        assert not any(ch.isdigit() for ch in line), line


# ================================ the auto-reopen roof close (_fenced_park)

def _recorded_close(monkeypatch) -> list[str]:
    closes: list[str] = []

    async def close_observatory(dome, tel, *, log):
        closes.append("close")
        return True
    monkeypatch.setattr(roof_mod, "close_observatory", close_observatory)
    return closes


def _needs_park_roof():
    """A connected roof with no ``requires_park_before_close``: the flag's
    fail-safe True, a roof that travels through the tube's volume."""
    return type("D", (), {"connected": True})()


async def test_the_reopen_close_parks_nothing_and_leaves_the_roof_open(
        sim_hub, monkeypatch, bus_lines):
    """The latch set, a roof that needs a parked tube: no park, tracking
    stopped and read back, the roof left open, one error line in the safe
    order. The close reports not closed, so the caller falls back to the
    open-sky pause.

    MUTANT F1 "reopen park ungated" (the first ``if self._position_unknown():
    ... return None`` block in `_fenced_park` removed): RED. The second ask
    under the lock still skips the park, so the loss shows as the park's
    announcement and its fence bump going out -
        AssertionError: assert not ['parking mount']
    MUTANT F3 "the roof closes anyway" (``if parked is None and bool(getattr(
    dome, "requires_park_before_close", True)):`` in `_close_for_reopen` made
    ``if False:``): RED -
        AssertionError: the roof close ran: ['close']
    """
    closes = _recorded_close(monkeypatch)
    tel = sim_hub.devices["telescope"]
    mount = _Mount(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    tel.mark_position_unknown(_REASON)
    closed = await e._close_for_reopen(_needs_park_roof(), _REASON)
    assert closes == [], f"the roof close ran: {closes}"
    assert closed is False
    assert mount.parks == 0, f"the park ran: {mount.parks}"
    assert mount.stops >= 1 and mount.tracking is False
    assert not [m for _l, m, _s in bus_lines if m == "parking mount"]
    said = [m for lvl, m, _s in bus_lines
            if lvl == "error" and m == ROOF_LEFT_OPEN_POSITION_UNKNOWN]
    assert len(said) == 1, bus_lines
    assert [m for _l, m, _s in bus_lines
            if "The roof close does not park first" in m], bus_lines


async def test_a_latch_set_while_the_reopen_park_waits_parks_nothing(
        sim_hub, monkeypatch, bus_lines):
    """The reopen's park can wait on the motion lock behind a bounded move
    while an AM5 reopen latches the position. It asks again under the lock.

    MUTANT F2 "the reopen park asks once" (the ``if self._position_unknown():``
    block inside ``async with lock:`` in `_fenced_park` removed): RED (the
    park ran and answered parked) -
        assert True is None
    """
    tel = sim_hub.devices["telescope"]
    mount = _Mount(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    async with sim_hub._motion_lock:
        parking = asyncio.ensure_future(e._fenced_park())
        deadline = time.monotonic() + 5.0
        while not [m for _l, m, _s in bus_lines if m == "parking mount"]:
            assert time.monotonic() < deadline, "the park never queued"
            await asyncio.sleep(0.01)
        tel.mark_position_unknown(_REASON)
    assert await parking is None
    assert mount.parks == 0, f"the park ran: {mount.parks}"
    assert mount.stops == 1 and mount.tracking is False


async def test_the_reopen_close_still_shuts_a_roof_that_needs_no_park(
        sim_hub, monkeypatch, bus_lines):
    """A roof that clears the tube wherever it points closes over it, in the
    rain too; still no park.

    MUTANT F4 "every reopen roof waits for a park" (the ``and bool(getattr(
    dome, "requires_park_before_close", True))`` dropped from the condition
    in `_close_for_reopen`): RED -
        AssertionError: the roof stayed open: []
    """
    closes = _recorded_close(monkeypatch)
    tel = sim_hub.devices["telescope"]
    mount = _Mount(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    tel.mark_position_unknown(_REASON)
    closed = await e._close_for_reopen(_NoParkRoof(), _REASON)
    assert closes == ["close"], f"the roof stayed open: {closes}"
    assert closed is True
    assert mount.parks == 0, mount.parks
    assert not [m for _l, m, _s in bus_lines
                if m == ROOF_LEFT_OPEN_POSITION_UNKNOWN]


async def test_control_after_trust_position_the_reopen_close_parks(
        sim_hub, monkeypatch):
    """CONTROL. Trust position cleared it: the reopen close parks and the
    roof closes."""
    closes = _recorded_close(monkeypatch)
    tel = sim_hub.devices["telescope"]
    mount = _Mount(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    tel.mark_position_unknown(_REASON)
    await tel.trust_position()
    assert await e._close_for_reopen(_needs_park_roof(), _REASON) is True
    assert mount.parks >= 1
    assert closes == ["close"]


# ===================================== the routes: dome close and TPPA start

class _AppTel(SimTelescope):
    """The simulator mount with every aimed command recorded and nothing
    sent, and a tracking stop that takes and reads back."""

    def __init__(self):
        super().__init__("plain")
        self.connected = True
        self.moves: list[str] = []
        self.tracking_now = True
        self.stops = 0

    async def is_parked(self) -> bool:
        return False

    async def unpark(self) -> None:
        self.moves.append("unpark")

    async def slew(self, ra_hours: float, dec_deg: float) -> None:
        self.moves.append("slew")

    async def park(self) -> None:
        self.moves.append("park")

    async def set_tracking(self, on: bool) -> None:
        if not on:
            self.stops += 1
        self.tracking_now = bool(on)

    async def get_tracking(self) -> bool:
        return self.tracking_now


@pytest.fixture
def app_rig(monkeypatch):
    """The shipped app, its lifespan running so a spawned close runs, over a
    recording mount; the roof close and the polar session's start are
    recorded."""
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    monkeypatch.setattr(engine_mod, "TRACKING_CONFIRM_S", 0.0)
    hub = app_module.hub
    tel = _AppTel()
    monkeypatch.setattr(hub, "require", lambda role: tel)
    monkeypatch.setitem(hub.devices, "telescope", tel)
    closes = _recorded_close(monkeypatch)
    started: list[str] = []

    async def start():
        started.append("start")
    monkeypatch.setattr(hub.polar, "start", start)
    with TestClient(app_module.create_app()) as c:
        yield c, tel, closes, started


def _settle_dome() -> None:
    """Wait (wall clock) for the spawned close's task to finish."""
    hub = app_module.hub
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        task = hub._busy.get("dome")
        if task is not None and task.done():
            return
        time.sleep(0.01)
    raise AssertionError("the spawned roof close never finished")


def test_the_dome_close_route_parks_nothing_while_latched(
        app_rig, monkeypatch, bus_lines):
    """``POST /api/dome/close`` with the latch set and a roof that needs a
    parked tube: no park, tracking stopped, the roof left open with one
    error line in the safe order.

    MUTANT D1 "dome close ungated" (``if tel is not None and not position_
    known_for_motion(hub, tel):`` in the route's ``_run`` made ``if
    False:``): RED -
        AssertionError: ['park']
    """
    c, tel, closes, _started = app_rig
    monkeypatch.setitem(app_module.hub.devices, "dome", _needs_park_roof())
    tel.mark_position_unknown(_REASON)
    r = c.post("/api/dome/close")
    assert r.status_code == 200, r.text
    _settle_dome()
    assert tel.moves == [], tel.moves
    assert closes == [], f"the roof close ran: {closes}"
    assert tel.stops >= 1 and tel.tracking_now is False
    said = [m for lvl, m, _s in bus_lines
            if lvl == "error" and m == ROOF_LEFT_OPEN_POSITION_UNKNOWN]
    assert len(said) == 1, bus_lines


def test_the_dome_close_route_shuts_a_roof_that_needs_no_park(
        app_rig, monkeypatch):
    """The same route, a roof that clears the tube wherever it points: it
    closes (rain included), still no park, tracking stopped.

    MUTANT D2 "every routed roof waits for a park" (``if needs_park:`` in the
    route's position-unknown arm made ``if True:``): RED -
        AssertionError: the roof stayed open: []
    """
    c, tel, closes, _started = app_rig
    monkeypatch.setitem(app_module.hub.devices, "dome", _NoParkRoof())
    tel.mark_position_unknown(_REASON)
    assert c.post("/api/dome/close").status_code == 200
    _settle_dome()
    assert closes == ["close"], f"the roof stayed open: {closes}"
    assert tel.moves == [], tel.moves
    assert tel.stops >= 1


def test_control_after_trust_position_the_dome_close_parks(
        app_rig, monkeypatch):
    """CONTROL. Trust position cleared it: the route parks, then closes."""
    c, tel, closes, _started = app_rig
    monkeypatch.setitem(app_module.hub.devices, "dome", _needs_park_roof())
    tel.mark_position_unknown(_REASON)
    assert c.post("/api/mount/trust-position").status_code == 200
    assert c.post("/api/dome/close").status_code == 200
    _settle_dome()
    assert tel.moves == ["park"], tel.moves
    assert closes == ["close"]


def test_the_polar_start_route_refuses_while_latched(app_rig):
    """``POST /api/polar/start`` answers 409 ``position_unknown`` with the
    safe order, and no session starts.

    MUTANT T1 "TPPA route ungated" (the ``_refuse_if_position_unknown(hub.
    devices.get("telescope"))`` call in ``polar_start`` removed): RED -
        AssertionError: (200, '{"started":true,...}')
    """
    c, tel, _closes, started = app_rig
    tel.mark_position_unknown(_REASON)
    r = c.post("/api/polar/start")
    assert r.status_code == 409, (r.status_code, r.text)
    assert r.json()["detail"] == {"detail": POSITION_UNKNOWN_MOTION_DETAIL,
                                  "code": POSITION_UNKNOWN_CODE}, r.text
    assert started == [], started


def test_control_after_trust_position_the_polar_start_runs(app_rig):
    """CONTROL. Trust position cleared it: the session starts."""
    c, tel, _closes, started = app_rig
    tel.mark_position_unknown(_REASON)
    assert c.post("/api/mount/trust-position").status_code == 200
    r = c.post("/api/polar/start")
    assert r.status_code == 200, r.text
    assert started == ["start"], started


# ======================================================== the native TPPA

class _Reached(Exception):
    """Raised by the double at the first device the run asks for after the
    gate, so a case can tell "stopped at the gate" from "went on"."""


class _TppaTel(SimTelescope):
    def __init__(self):
        super().__init__("plain")
        self.connected = True
        self.asked: list[str] = []

    async def get_position(self):
        self.asked.append("position")
        return 7.25, 30.0

    async def slew(self, ra_hours, dec_deg):
        self.asked.append("slew")


class _TppaHub:
    """Just what `_drive` reads before its first device call: a site that
    is set (made up) and the telescope."""

    def __init__(self, tel):
        self.devices = {"telescope": tel}
        self.site = {"latitude": 12.5, "longitude": -77.25,
                     "elevation_m": 0.0, "is_default": False}
        self.required: list[str] = []

    def require(self, role):
        self.required.append(role)
        if role == "telescope":
            return self.devices["telescope"]
        raise _Reached(role)


async def test_the_native_tppa_refuses_before_anything_is_read_or_moved():
    """The driver asks the gate itself, before the pole check (which would
    answer a reset mount with advice to point elsewhere) and before any
    device read or move.

    MUTANT T2 "native TPPA ungated" (the ``if not position_known_for_motion(
    hub, tel): raise DeviceError(TPPA_POSITION_UNKNOWN)`` in `_drive`
    removed): RED -
        test_888_every_move_asks_the_gate._Reached: camera
    """
    tel = _TppaTel()
    hub = _TppaHub(tel)
    tel.mark_position_unknown(_REASON)
    with pytest.raises(DeviceError) as caught:
        await nat._drive(None, hub)
    assert str(caught.value) == nat.TPPA_POSITION_UNKNOWN
    assert tel.asked == [], tel.asked
    assert hub.required == ["telescope"], hub.required


async def test_control_after_trust_position_the_native_tppa_goes_on():
    """CONTROL. Trust position cleared it: the run goes past the gate to
    its camera."""
    tel = _TppaTel()
    hub = _TppaHub(tel)
    tel.mark_position_unknown(_REASON)
    await tel.trust_position()
    with pytest.raises(_Reached):
        await nat._drive(None, hub)
    assert hub.required == ["telescope", "camera"], hub.required


# ================================================= hub.goto_and_center

class _Stop(Exception):
    """Ends a control's centring at its first slew."""


def _recorded_mount(sim_hub, monkeypatch, *, stop_at_slew: bool = False):
    tel = sim_hub.devices["telescope"]
    moves: list[str] = []

    async def slew(ra, dec):
        moves.append("slew")
        if stop_at_slew:
            raise _Stop()

    async def unpark():
        moves.append("unpark")

    async def set_tracking(on):
        moves.append(f"tracking {bool(on)}")
    monkeypatch.setattr(tel, "slew", slew)
    monkeypatch.setattr(tel, "unpark", unpark)
    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    return tel, moves


async def test_goto_and_center_moves_nothing_while_latched(
        sim_hub, monkeypatch, bus_lines):
    """The hub's own motion seam asks the gate: the aborted shape with
    ``position_unknown`` and the safe order, and nothing reaches the
    driver (no unpark, no tracking, no slew).

    MUTANT G1 "goto_and_center ungated" (the ``if not position_known_for_
    motion(self, tel):`` block under the first ``_motion_lock`` removed):
    RED -
        AssertionError: ['tracking True', 'slew', ...]
    """
    tel, moves = _recorded_mount(sim_hub, monkeypatch)
    tel.mark_position_unknown(_REASON)
    res = await sim_hub.goto_and_center(7.25, 30.0, max_attempts=1)
    assert moves == [], moves
    assert res["position_unknown"] is True and res["aborted"] is True
    assert res["centered"] is False
    assert res["reason"] == POSITION_UNKNOWN_MOTION_DETAIL
    said = [m for lvl, m, _s in bus_lines
            if lvl == "warning" and m.endswith("Nothing was moved (centring)")]
    assert len(said) == 1, bus_lines
    _check_words(said[0])


async def test_a_latch_set_while_goto_and_center_waits_moves_nothing(
        sim_hub, monkeypatch):
    """The latch set while the call waits for the motion lock (an AM5 link
    reopen that read the home pole): the gate is under the lock, so it is
    caught there. Same mutant G1: RED -
        AssertionError: ['tracking True', 'slew', ...]
    """
    tel, moves = _recorded_mount(sim_hub, monkeypatch)
    async with sim_hub._motion_lock:
        call = asyncio.ensure_future(
            sim_hub.goto_and_center(7.25, 30.0, max_attempts=1))
        await asyncio.sleep(0.2)
        assert not call.done()
        tel.mark_position_unknown(_REASON)
    res = await call
    assert moves == [], moves
    assert res["position_unknown"] is True


async def test_control_after_trust_position_goto_and_center_slews(
        sim_hub, monkeypatch):
    """CONTROL. Trust position cleared it: the same call slews."""
    tel, moves = _recorded_mount(sim_hub, monkeypatch, stop_at_slew=True)
    tel.mark_position_unknown(_REASON)
    await tel.trust_position()
    with pytest.raises(_Stop):
        await sim_hub.goto_and_center(7.25, 30.0, max_attempts=1)
    assert "slew" in moves, moves


_REFUSED = {"centered": False, "error_arcmin": None, "attempts": 0,
            "aborted": True, "rotation": None, "position_unknown": True,
            "reason": POSITION_UNKNOWN_MOTION_DETAIL}


async def test_the_engine_stops_the_run_on_the_hub_s_refusal(
        sim_hub, monkeypatch):
    """A caller of the shape: the engine's centring ends the run with
    ``PositionUnknownStop``, not a failed solve for the no-light hold.

    MUTANT G2 "the engine ignores the shape" (the ``await self._stop_if_
    centring_refused_unknown(result, target, "centring")`` in `_centre_once`
    removed): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    e, t = _engine(sim_hub)

    async def goto_and_center(*a, **kw):
        return dict(_REFUSED)
    monkeypatch.setattr(sim_hub, "goto_and_center", goto_and_center)
    with pytest.raises(PositionUnknownStop):
        await e._centre_once(t, None)


async def test_control_a_centred_result_goes_on(sim_hub, monkeypatch):
    """CONTROL. A centred answer from the same seam is returned."""
    e, t = _engine(sim_hub)

    async def goto_and_center(*a, **kw):
        return {"centered": True, "error_arcmin": 0.2, "attempts": 1}
    monkeypatch.setattr(sim_hub, "goto_and_center", goto_and_center)
    assert (await e._centre_once(t, None))["centered"] is True


class _Guider:
    connected = True

    def __init__(self):
        self.calls: list[str] = []

    async def is_active(self):
        return True

    async def stop_guiding(self):
        self.calls.append("stop")

    async def start_guiding(self):
        self.calls.append("start")

    async def flip_calibration(self):
        self.calls.append("flip calibration")


async def test_a_refused_flip_re_centre_flips_no_calibration(
        sim_hub, monkeypatch):
    """The meridian flip's re-centre refused for an unknown position:
    nothing moved, so nothing flipped. The pier side is unreadable here,
    which on its own would count as flipped, so the guard is what keeps
    the calibration and the guider restart away.

    MUTANT G3 "the flip reads the refusal as a flip" (the ``if isinstance(
    result, dict) and result.get("position_unknown"): return ...`` in
    `Hub.meridian_flip` removed): RED -
        AssertionError: ['stop', 'flip calibration', 'start']
    """
    guider = _Guider()
    monkeypatch.setattr(sim_hub, "guider", guider)

    async def goto_and_center(*a, **kw):
        return dict(_REFUSED)

    async def pier_side_now():
        return None
    monkeypatch.setattr(sim_hub, "goto_and_center", goto_and_center)
    monkeypatch.setattr(sim_hub, "pier_side_now", pier_side_now)
    res = await sim_hub.meridian_flip(7.25, 30.0)
    assert guider.calls == ["stop"], guider.calls
    assert res["flipped"] is False and res["position_unknown"] is True


async def test_control_a_flip_that_moved_restarts_guiding(
        sim_hub, monkeypatch):
    """CONTROL. A re-centre that moved, the side unreadable: the calibration
    is flipped and guiding restarted, as before."""
    guider = _Guider()
    monkeypatch.setattr(sim_hub, "guider", guider)

    async def goto_and_center(*a, **kw):
        return {"centered": True, "error_arcmin": 0.2, "attempts": 1}

    async def pier_side_now():
        return None
    monkeypatch.setattr(sim_hub, "goto_and_center", goto_and_center)
    monkeypatch.setattr(sim_hub, "pier_side_now", pier_side_now)
    await sim_hub.meridian_flip(7.25, 30.0)
    assert guider.calls == ["stop", "flip calibration", "start"]


# ===================================================== the wind-down line

async def test_a_run_set_not_to_park_promises_no_dawn_park_while_latched(
        sim_hub, monkeypatch, bus_lines):
    """The latch set some other way than a ``PositionUnknownStop`` (an AM5
    reopen that read the home pole): the "set not to park" line says dawn
    park will not park the mount until Trust position, never that it will.

    MUTANT W5 "the old promise" (``if self._position_unknown():`` before the
    new line in `_wind_down_park_and_close` made ``if False:``): RED -
        AssertionError: ['this run was set not to park ... Dawn park will
        park it after sunrise']
    """
    monkeypatch.delenv("ASTRODECK_NO_DAWN_PARK", raising=False)
    tel = sim_hub.devices["telescope"]
    e = SequenceEngine(sim_hub)
    e._frames_done = True
    tel.mark_position_unknown(_REASON)
    await e._wind_down_park_and_close(False, False)
    said = [m for _l, m, _s in bus_lines
            if m.startswith("this run was set not to park")
            or m == NOT_PARKED_POSITION_UNKNOWN]
    assert said == [NOT_PARKED_POSITION_UNKNOWN], said
    assert [lvl for lvl, m, _s in bus_lines
            if m == NOT_PARKED_POSITION_UNKNOWN] == ["warning"]


async def test_control_a_known_position_keeps_the_dawn_park_line(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL. With the position known, the line still names dawn park."""
    monkeypatch.delenv("ASTRODECK_NO_DAWN_PARK", raising=False)
    e = SequenceEngine(sim_hub)
    e._frames_done = True
    await e._wind_down_park_and_close(False, False)
    said = [m for _l, m, _s in bus_lines
            if m.startswith("this run was set not to park")]
    assert len(said) == 1 and said[0].endswith(
        "Dawn park will park it after sunrise"), said


# ============================================== the sun watch (ruling 4B)

class _LatchTel(FakeTel):
    """The sun watch's mount double with the position flag the gate reads,
    every tracking command recorded, and no slew at all."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.position_known = True
        self.tracking_calls: list[bool] = []

    async def set_tracking(self, on: bool) -> None:
        self.tracking_calls.append(bool(on))
        self.tracking = bool(on)

    async def unpark(self) -> None:
        self.calls.append("unpark")


def _rig_at(ra_hours: float, dec_deg: float, *, tracking: bool):
    hub = SiteHub()
    tel = _LatchTel(hub=hub, ra_hours=ra_hours, dec_deg=dec_deg,
                    tracking=tracking)
    hub.devices["telescope"] = tel
    return hub, tel


def _approach_rig(cfg):
    """A tracking tube 10 degrees west of the pinned Sun, inside the cone
    now: the live path parks it at once when the position is known."""
    ra = (SUN_RA_H - 10.0 / 15.0) % 24.0
    sep, _ = closest_approach(ra, SUN_DEC, tracking=True, now=JUNE_TS,
                              lead_s=LEAD_TIME_S)
    assert sep < cfg.safety.solar_exclusion_deg, "precondition: inside"
    return _rig_at(ra, SUN_DEC, tracking=True)


def _west_rig(cfg):
    """A STOPPED tube well west of the Sun: clear at read time, carried into
    the cone by the sky after ``age_s``, so a blind projection from its last
    read parks it then (test_w14's geometry)."""
    ra = (SUN_RA_H - WEST_H) % 24.0
    hub, tel = _rig_at(ra, 17.3, tracking=False)
    sep, _ = closest_approach(ra, 17.3, tracking=False, now=JUNE_TS,
                              lead_s=LEAD_TIME_S)
    assert sep > cfg.safety.solar_exclusion_deg, "precondition: clear now"
    age_s = _first_age_inside_cone(ra, 17.3, cfg.safety.solar_exclusion_deg)
    return hub, tel, age_s


async def test_the_sun_watch_parks_nothing_while_the_position_is_unknown(
        cfg, pinned_sun, sky, bus_lines):
    """OWNER RULING 4B. The tube is inside the cone and the Sun is up, the
    case the net exists for, and the latch is set: no park and no tracking
    command reach the driver, however many ticks.

    MUTANT SW1 "the sun watch parks regardless" (the ``if not position_
    known_for_motion(self.hub, tel): self._position_unknown_tick(cfg);
    return`` block in `SunWatch.tick` removed) was RED here in round 1
    (AssertionError: the park ran: 1). Round 2 made `SunWatch._park` ask
    the gate itself, so this case now passes under SW1 and guards the
    park's own asks; SW1 is killed by test_888_every_seam_asks_again.py's
    test_a_latched_tube_clear_of_the_sun_still_raises_the_alert.
    """
    hub, tel = _approach_rig(cfg)
    tel.position_known = False
    sky["alt"] = -2.0
    w, now = _driven(hub)
    await _tick_n(w, now, 5)
    assert tel.park_calls == 0, f"the park ran: {tel.park_calls}"
    assert tel.tracking_calls == [], tel.tracking_calls
    assert "unpark" not in tel.calls


async def test_a_latched_dark_mount_is_not_parked_from_its_last_read(
        cfg, pinned_sun, sky, bus_lines):
    """The latch set and the link down: the blind fallback would park from
    the last position read, aged into the cone. It does not run. Under
    SW1 this now passes too, held by the park's own ask (see above).
    """
    hub, tel, age_s = _west_rig(cfg)
    sky["alt"] = -2.0
    w, now = _driven(hub)
    await w.tick()                              # a good read, clear
    tel.position_known = False
    tel.connected = False
    now["t"] = JUNE_TS + age_s + 120.0
    await w.tick()
    assert tel.park_calls == 0, f"the park ran: {tel.park_calls}"
    assert tel.tracking_calls == []


async def test_control_after_trust_position_the_net_parks_on_the_next_tick(
        cfg, pinned_sun, sky):
    """CONTROL. Trust position clears the latch: the next tick reads the
    tube inside the cone and parks it."""
    hub, tel = _approach_rig(cfg)
    tel.position_known = False
    sky["alt"] = -2.0
    w, now = _driven(hub)
    await _tick_n(w, now, 2)
    assert tel.park_calls == 0
    tel.position_known = True
    await w.tick()
    assert tel.park_calls == 1, tel.park_calls


async def test_a_position_read_before_the_doubt_is_never_projected_after_it(
        cfg, pinned_sun, sky):
    """The last position read before the latch is forgotten: after Trust
    position, a mount that then goes dark is not parked from a reading
    taken before the doubt (the tube may have been brought home by eye).

    MUTANT SW6 "the old reading kept" (``self._last_good = None`` in
    `_position_unknown_tick` removed): RED -
        AssertionError: projected from a reading before the doubt: 1
    """
    hub, tel, age_s = _west_rig(cfg)
    sky["alt"] = -30.0
    w, now = _driven(hub)
    await w.tick()                              # a good read, clear
    tel.position_known = False
    now["t"] += 60.0
    await w.tick()                              # latched
    tel.position_known = True                   # Trust position
    tel.connected = False                       # and then the link drops
    now["t"] = JUNE_TS + age_s + 120.0
    await w.tick()
    assert tel.park_calls == 0, (
        f"projected from a reading before the doubt: {tel.park_calls}")


async def test_the_alert_is_said_once_and_again_on_the_blind_cadence(
        cfg, pinned_sun, sky, monkeypatch):
    """While latched and the Sun is above the dawn-park threshold: ONE
    error line in fixed words, said again every ``BLIND_LOG_EVERY`` ticks,
    on source "safety" and unflagged, which the AlertDispatcher maps to an
    error alert (the channel the blind-in-daylight line pages through).
    Below the threshold it says no error; the Sun going down and up again
    says it at once. No park throughout.

    MUTANT SW2 "said once only" (``if n % BLIND_LOG_EVERY == 0:`` in
    `_position_unknown_tick` made ``if n == 0:``): RED -
        AssertionError: assert 1 == 3
    MUTANT SW3 "not an alert" (the line's level ``"error"`` made
    ``"info"``): RED -
        AssertionError: [{'level': 'info', 'message': 'sun watch blind,
        position unknown: ...
    """
    published: list[tuple[str, dict]] = []

    def publish(type, **data):
        published.append((type, data))
    monkeypatch.setattr(events.bus, "publish", publish)

    def lines() -> list[dict]:
        return [d for t, d in published if t == "log"
                and d.get("message") == POSITION_UNKNOWN_DAYLIGHT]

    hub, tel = _approach_rig(cfg)
    tel.position_known = False
    w, now = _driven(hub)
    sky["alt"] = -4.0                           # below the -3 threshold
    await _tick_n(w, now, 5)
    assert lines() == [], "the Sun is below the threshold: no error"

    sky["alt"] = -2.0                           # above it
    await _tick_n(w, now, 2 * BLIND_LOG_EVERY + 1)
    said = lines()
    assert len(said) == 3
    assert all(d["level"] == "error" and d["source"] == "safety"
               and not d.get(events.SITE_DERIVED_KEY) for d in said), said
    dispatcher = AlertDispatcher(events.bus, lambda: cfg)
    alerts = [dispatcher._alert_for(events.Event("log", d)) for d in said]
    assert [a and (a.level, a.message) for a in alerts] == [
        ("error", POSITION_UNKNOWN_DAYLIGHT)] * 3, alerts

    sky["alt"] = -4.0
    await _tick_n(w, now, 1)
    sky["alt"] = -2.0
    await _tick_n(w, now, 1)
    assert len(lines()) == 4, "the next Sun-up says it at once"
    assert tel.park_calls == 0 and tel.tracking_calls == []


async def test_the_state_is_published_beside_the_blind_fields(
        cfg, pinned_sun, sky):
    """``state()`` (``/api/safety/state``'s ``sun_watch``) says the net is
    standing down for an unknown position and since when; Trust position
    clears it on the next tick. Times and booleans only.

    MUTANT SW4 "never published" (``"position_unknown": self._unknown_since
    is not None`` in `SunWatch.state` made ``False``): RED -
        AssertionError: assert False is True
    """
    hub, tel = _approach_rig(cfg)
    tel.position_known = False
    sky["alt"] = -30.0
    w, now = _driven(hub)
    await w.tick()
    st = w.state()
    assert st["position_unknown"] is True
    assert st["position_unknown_since"] == JUNE_TS
    assert st["blind"] is False
    assert all(isinstance(v, (bool, float, type(None))) for v in st.values())
    tel.position_known = True
    now["t"] += 60.0
    await w.tick()
    st = w.state()
    assert st["position_unknown"] is False
    assert st["position_unknown_since"] is None
