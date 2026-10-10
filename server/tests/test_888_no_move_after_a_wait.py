# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""No park or slew aimed after an await that could have latched the
position, and no needs-park roof closed over a tube nobody parked
(#888, fix round 3).

A third review found the gate asked too early on six paths. On the AM5 any
read can relink the mount and latch the position unknown (a power cycle
reads home, the pole), so an ask that comes before such an await says
nothing about the move after it:

- the roof closes asked the gate only while the link was up. With the link
  down (the reset case itself) `_fenced_park` and ``POST /api/dome/close``
  went straight to ``close_observatory``, which asks no parked state of a
  mount that is not connected, so a roof that needs a parked tube closed
  over one nobody knew the position of. Both now ask whatever the link
  says, and neither pages "roof left open" for a roof that already reads
  closed;
- `_park_and_read_back` sent its second park after reads that can latch;
- dawn park asked once, before it waited on the motion lock;
- the limit recovery's park, the uncentred acquisition's slew and the hold
  re-point's slew each followed mount reads with no second ask;
- the dome close route's under-the-lock ask had no test.

Each case latches inside the await named and asserts nothing further
reached the driver; each control shows the same path moving. Every mutant
named below was applied to a byte backup of its production file, run under
the suite's normal command (xdist on), and the file restored from the
backup with its sha256 checked. Coordinates are made up; no mount position
is printed.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.api.app as app_module
import astrodeck.sequence.engine as engine_mod
import astrodeck.sequence.roof as roof_mod
from astrodeck import dawn_park as dawn_park_mod
from astrodeck.dawn_park import DawnPark
from astrodeck.devices.base import DomeShutterState
from astrodeck.sequence.engine import (ROOF_LEFT_OPEN_POSITION_UNKNOWN,
                                       PositionUnknownStop, SequenceEngine)

from test_886_one_position_gate import _Mount, _hold_engine, _operator  # noqa: F401
from test_888_every_move_asks_the_gate import (_needs_park_roof,  # noqa: F401
                                               _settle_dome, app_rig)
from test_centring_settings_reach_goto import _setup, _target
from test_dawn_park import (FakeCam, FakeEngine, FakeHub,  # noqa: F401
                            _daytime, cfg, nothing_armed)
from test_oct08_position_latch import _DoubtTel
from test_recovery_centring_is_measured import (  # noqa: F401 (fixtures)
    _engine, _pinned_mount, sim_hub, temp_store)

_REASON = "a fictional reason"
#: The real roof close, taken before any fixture replaces it.
_REAL_CLOSE = roof_mod.close_observatory


class _RealRoof:
    """A connected roof that travels through the tube's volume, its shutter
    closes recorded. The REAL ``close_observatory`` runs over it."""
    requires_park_before_close = True

    def __init__(self, *, closed: bool = False):
        self.connected = True
        self.closed = closed
        self.closes = 0

    async def is_closed(self) -> bool:
        return self.closed

    async def close_shutter(self) -> None:
        self.closes += 1
        self.closed = True

    async def shutter_state(self):
        return (DomeShutterState.CLOSED if self.closed
                else DomeShutterState.OPEN)


def _left_open(bus_lines) -> list[str]:
    return [m for lvl, m, _s in bus_lines
            if lvl == "error" and m == ROOF_LEFT_OPEN_POSITION_UNKNOWN]


def _end_retry(e) -> None:
    """End the tracking stop's retry task a dead link leaves asking."""
    task = e._idle_stop_task
    if task is not None and not task.done():
        task.cancel()


def _end_app_retry(c) -> None:
    eng = app_module.engine

    async def end():
        _end_retry(eng)
    c.portal.call(end)


def _quiet_app_engine(monkeypatch) -> None:
    """The app's engine is process-wide: what a stop leaves on it is put
    back after the case."""
    eng = app_module.engine
    for name in ("_idle_stop_task", "_idle_stop_epoch",
                 "_idle_stop_first_made"):
        monkeypatch.setattr(eng, name, getattr(eng, name))


# ======================================== the roof closes, link down (#888)

async def test_a_dropped_link_does_not_close_a_needs_park_roof_on_reopen(
        sim_hub, monkeypatch, bus_lines):
    """The auto-reopen close with the latch set and the mount's link down
    (an AM5 power cycle): the REAL ``close_observatory`` asks no parked
    state of a mount that is not connected, so only the gate keeps the roof
    open. No park, no shutter close, one error line in the safe order.

    MUTANT L1 "the link check first" (in `_fenced_park`, ``if not (tel and
    getattr(tel, "connected", False)): return False`` moved back above the
    ``if self._position_unknown():`` block): RED -
        AssertionError: the roof closed over the tube: 1
    """
    tel = sim_hub.devices["telescope"]
    mount = _Mount(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    tel.mark_position_unknown(_REASON)
    monkeypatch.setattr(tel, "connected", False)
    roof = _RealRoof()
    try:
        closed = await e._close_for_reopen(roof, _REASON)
    finally:
        _end_retry(e)
    assert roof.closes == 0, f"the roof closed over the tube: {roof.closes}"
    assert closed is False
    assert mount.parks == 0, mount.parks
    assert len(_left_open(bus_lines)) == 1, bus_lines


async def test_control_a_dropped_link_with_a_known_position_closes(
        sim_hub, monkeypatch):
    """CONTROL. The same close with the position known: the roof closes as
    it did before #888 (``close_observatory``'s own rule for a mount that is
    not connected)."""
    tel = sim_hub.devices["telescope"]
    _Mount(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    monkeypatch.setattr(tel, "connected", False)
    roof = _RealRoof()
    assert await e._close_for_reopen(roof, _REASON) is True
    assert roof.closes == 1


def test_a_dropped_link_does_not_close_a_needs_park_roof_from_the_route(
        app_rig, monkeypatch, bus_lines):
    """``POST /api/dome/close`` with the latch set and the link down, the
    REAL ``close_observatory``: no park, no shutter close, the error line.

    MUTANT L2 "the route asks only a live link" (``if tel is not None and
    not position_known_for_motion(hub, tel):`` in the route's ``_run`` made
    ``if live and not position_known_for_motion(hub, tel):``): RED -
        AssertionError: the roof closed over the tube: 1
    """
    c, tel, _closes, _started = app_rig
    _quiet_app_engine(monkeypatch)
    monkeypatch.setattr(roof_mod, "close_observatory", _REAL_CLOSE)
    roof = _RealRoof()
    monkeypatch.setitem(app_module.hub.devices, "dome", roof)
    tel.mark_position_unknown(_REASON)
    tel.connected = False
    assert c.post("/api/dome/close").status_code == 200
    _settle_dome()
    _end_app_retry(c)
    assert roof.closes == 0, f"the roof closed over the tube: {roof.closes}"
    assert tel.moves == [], tel.moves
    assert len(_left_open(bus_lines)) == 1, bus_lines


# ============================================ a roof that is already closed

def test_the_route_does_not_page_left_open_for_a_closed_roof(
        app_rig, monkeypatch, bus_lines):
    """Close roof pressed on a roof that is already closed, the latch set:
    no "roof left open" page, no park, no shutter command.

    MUTANT C1 "a closed roof pages" (``if await engine._roof_reads_closed(
    dome): return True`` removed from the route's position-unknown arm):
    RED -
        AssertionError: [...'roof left open, position unknown: ...']
    """
    c, tel, _closes, _started = app_rig
    monkeypatch.setattr(roof_mod, "close_observatory", _REAL_CLOSE)
    roof = _RealRoof(closed=True)
    monkeypatch.setitem(app_module.hub.devices, "dome", roof)
    tel.mark_position_unknown(_REASON)
    assert c.post("/api/dome/close").status_code == 200
    _settle_dome()
    assert _left_open(bus_lines) == [], bus_lines
    assert tel.moves == [] and roof.closes == 0, (tel.moves, roof.closes)
    assert tel.stops >= 1


async def test_the_reopen_close_counts_a_closed_roof_as_closed(
        sim_hub, monkeypatch, bus_lines):
    """The auto-reopen close over a roof that already reads closed, the
    latch set: closed, and nothing pages.

    MUTANT C2 "the reopen close never looks" (``closed = await self._roof_
    reads_closed(dome)`` in `_close_for_reopen` made ``closed = False``):
    RED -
        assert False is True
    """
    tel = sim_hub.devices["telescope"]
    mount = _Mount(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    tel.mark_position_unknown(_REASON)
    roof = _RealRoof(closed=True)
    assert await e._close_for_reopen(roof, _REASON) is True
    assert _left_open(bus_lines) == [], bus_lines
    assert mount.parks == 0 and roof.closes == 0


# =========================================== the park read-back's second park

def _a_park_lost_in_a_reset(tel, monkeypatch) -> list[str]:
    """The first park goes out, and the first parked read relinks the mount
    and latches the position (an AM5 power-cycled mid-park reads home): not
    parked, not slewing, the read-back's "lost". Every park and tracking
    command recorded in order."""
    events: list[str] = []
    reads = {"n": 0}

    async def park():
        events.append("park")

    async def is_parked():
        reads["n"] += 1
        if reads["n"] == 1:
            tel.mark_position_unknown(_REASON)
        return False

    async def is_slewing():
        return False

    async def set_tracking(on):
        events.append(f"tracking {bool(on)}")

    async def get_tracking():
        return False
    monkeypatch.setattr(tel, "park", park)
    monkeypatch.setattr(tel, "is_parked", is_parked)
    monkeypatch.setattr(tel, "is_slewing", is_slewing)
    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    return events


@pytest.mark.parametrize("path", ["roof close", "wind-down"])
async def test_a_latch_set_in_the_read_back_sends_no_second_park(
        sim_hub, monkeypatch, bus_lines, path):
    """No second park after the latch, and tracking is stopped instead;
    both callers answer None, the skipped park.

    MUTANT RB1 "the read-back parks again" (the ``if attempt == 1 and
    self._position_unknown(): ... return None`` block in
    `_park_and_read_back` removed): RED, both -
        AssertionError: ['park', 'park', ...]
    MUTANT RB2 "the roof close forgets the stop" (the ``if parked is None:
    await self._quiet_stop_for_unknown_position(...); return None`` after
    the read-back in `_fenced_park` removed): RED, roof close -
        AssertionError: ['park']
    MUTANT RB3 "the wind-down forgets the stop" (the ``if parked is None:
    await self._quiet_stop_for_unknown_position()`` after the read-back in
    `_wind_down_park` removed): RED, wind-down -
        AssertionError: ['park']
    """
    monkeypatch.setattr(engine_mod, "TRACKING_CONFIRM_S", 0.0)
    tel = sim_hub.devices["telescope"]
    events = _a_park_lost_in_a_reset(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    got = await (e._fenced_park() if path == "roof close"
                 else e._wind_down_park(tel))
    assert events == ["park", "tracking False"], events
    assert got is None
    said = [m for lvl, m, _s in bus_lines if lvl == "warning"
            and m.endswith("its position is now unknown: no second park")]
    assert len(said) == 1, bus_lines


@pytest.mark.parametrize("path", ["roof close", "wind-down"])
async def test_control_a_lost_park_with_a_known_position_parks_again(
        sim_hub, monkeypatch, path):
    """CONTROL. The same lost park with no latch: parked once more."""
    tel = sim_hub.devices["telescope"]
    events = _a_park_lost_in_a_reset(tel, monkeypatch)

    async def is_parked():
        return False
    monkeypatch.setattr(tel, "is_parked", is_parked)
    e = SequenceEngine(sim_hub)
    await (e._fenced_park() if path == "roof close"
           else e._wind_down_park(tel))
    assert events[:2] == ["park", "park"], events


# ===================================================== dawn park (#874)

def _dawn_rig():
    hub = FakeHub()
    tel = _DoubtTel(hub=hub)
    tel.position_known = True
    hub.devices["telescope"] = tel
    hub.devices["camera"] = FakeCam()
    ts, _alt = _daytime()
    return hub, tel, DawnPark(hub, FakeEngine(), clock=lambda: ts)


async def _latch_while_the_park_waits(hub, tel, d, *, latch: bool) -> None:
    async with hub._motion_lock:
        ticking = asyncio.ensure_future(d.tick())
        deadline = time.monotonic() + 5.0
        while hub.epoch_bumps == 0:
            assert time.monotonic() < deadline, "the park never queued"
            await asyncio.sleep(0.01)
        if latch:
            tel.position_known = False
    await ticking


async def test_dawn_park_asks_again_under_the_motion_lock(cfg, bus_lines):
    """The Sun is up, the mount unparked, and the latch is set while dawn
    park's park waits on the motion lock behind another motion: no park,
    tracking stopped and read back instead, the fixed-words line said.

    MUTANT DP1 "dawn park asks once" (``latched = not position_known_for_
    motion(self.hub, tel)`` under the lock in `DawnPark.tick` made
    ``latched = False``): RED -
        AssertionError: the park ran: 1
    """
    hub, tel, d = _dawn_rig()
    await _latch_while_the_park_waits(hub, tel, d, latch=True)
    assert tel.park_calls == 0, f"the park ran: {tel.park_calls}"
    assert tel.stops == 1 and tel.tracking is False
    said = [m for _l, m, _s in bus_lines
            if m == dawn_park_mod.POSITION_UNKNOWN_LINE]
    assert len(said) == 1, bus_lines


async def test_control_dawn_park_parks_after_the_lock_wait(cfg):
    """CONTROL. No latch: the park that waited on the lock is sent."""
    hub, tel, d = _dawn_rig()
    await _latch_while_the_park_waits(hub, tel, d, latch=False)
    assert tel.park_calls == 1, tel.park_calls


# ================================ engine moves that follow a mount read

async def test_the_limit_recovery_asks_again_before_its_park(
        sim_hub, monkeypatch, bus_lines):
    """The latch lands inside the recovery's pier-side read, after its
    first ask: the run ends before the park (a goto to the model's home).

    MUTANT LR1 "the recovery's park asks nothing after the reads" (the
    ``await self._gate_position_known(target, where)`` right after
    ``side_before = await self._pier_side_now()`` in `_do_tracking_recovery`
    removed): RED -
        AssertionError: ['park', 'unpark']
    """
    st = _pinned_mount(sim_hub, monkeypatch,
                       [{"centered": True, "error_arcmin": 0.2,
                         "attempts": 1}])
    tel = sim_hub.devices["telescope"]
    real_side = tel.pier_side

    async def pier_side():
        tel.mark_position_unknown(_REASON)
        return await real_side()
    monkeypatch.setattr(tel, "pier_side", pier_side)
    e, t = _engine(sim_hub)
    with pytest.raises(PositionUnknownStop):
        await e._enforce_tracking(t.steps[0], t)
    assert st["events"] == [], st["events"]


async def test_the_uncentred_acquisition_asks_again_before_its_slew():
    """A target with centring off: the latch lands inside the parked read
    between the setup's gate and its slew. The run ends; no slew.

    MUTANT US1 "the uncentred slew asks nothing after the reads" (the
    ``await self._gate_position_known(target, "acquiring the target")``
    right before ``slew_in_mount_frame`` in `_setup_target`'s uncentred
    branch removed): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    t = _target(name="Fictional C", center=False)
    e, hub = _setup(t)
    tel = hub.tel
    tel.position_known = True

    async def is_parked():
        tel.position_known = False
        return False
    tel.is_parked = is_parked
    with pytest.raises(PositionUnknownStop):
        await e._setup_target(0, t)
    assert not [c for c in tel.calls
                if isinstance(c, tuple) and c[0] == "slew"], tel.calls


async def test_control_the_uncentred_acquisition_slews():
    """CONTROL. No latch: the uncentred slew is sent."""
    t = _target(name="Fictional C", center=False)
    e, hub = _setup(t)
    await e._setup_target(0, t)
    assert [c for c in hub.tel.calls
            if isinstance(c, tuple) and c[0] == "slew"], hub.tel.calls


async def test_the_hold_re_point_asks_again_before_its_slew(
        sim_hub, monkeypatch):
    """The latch lands inside the re-point's parked read, after its second
    ask: the run ends with no slew.

    MUTANT HP1 "the re-point slew asks nothing after the reads" (the
    ``await self._gate_position_known(target, "re-pointing after the
    hold")`` inside the ``try`` before ``slew_in_mount_frame`` in
    `_hold_repoint` removed): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    e, t, tel, moves, _gates = _hold_engine(sim_hub, monkeypatch)

    async def is_parked():
        tel.mark_position_unknown(_REASON)
        return False
    monkeypatch.setattr(tel, "is_parked", is_parked)
    with pytest.raises(PositionUnknownStop):
        await e._hold_repoint(t, elsewhere=True)
    assert moves == [], moves


# ========================= the dome close route asks under the motion lock

class _LatchOnEnter:
    """The hub's motion lock, with the position latched as the dome
    close's own task takes it: an AM5 reopen landing while the close
    waited behind another motion."""

    def __init__(self, real, tel):
        self.real = real
        self.tel = tel

    def locked(self) -> bool:
        return self.real.locked()

    async def __aenter__(self):
        if asyncio.current_task() is app_module.hub._busy.get("dome"):
            self.tel.mark_position_unknown(_REASON)
        return await self.real.__aenter__()

    async def __aexit__(self, *exc):
        return await self.real.__aexit__(*exc)


def test_the_dome_close_asks_the_gate_under_the_motion_lock(
        app_rig, monkeypatch, bus_lines):
    """The route's gate is asked under the motion lock, so a latch set
    while the close waited for the lock gets no park.

    MUTANT A5 "asked before the lock" (the gate computed before ``async
    with hub._motion_lock:`` and read under it): RED -
        AssertionError: ['park']
    """
    c, tel, closes, _started = app_rig
    monkeypatch.setitem(app_module.hub.devices, "dome", _needs_park_roof())
    monkeypatch.setattr(app_module.hub, "_motion_lock",
                        _LatchOnEnter(app_module.hub._motion_lock, tel))
    assert c.post("/api/dome/close").status_code == 200
    _settle_dome()
    assert tel.moves == [], tel.moves
    assert closes == [], closes
    assert len(_left_open(bus_lines)) == 1, bus_lines
