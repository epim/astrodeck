# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The position-in-doubt latch and the engine's safety around it (Oct-08
integration review, findings 0, 1, 2, 5, 7 and 9; #874).

- A ``PositionUnknownStop`` used to disarm only the session that stopped; the
  doubt is now the RIG's: `_run`'s arm marks it on the telescope
  (``Telescope.mark_position_unknown``, process memory only), so every
  consumer of ``position_known`` holds, the resume ladder for ANY armed
  session included, and both UIs show their Trust position button.
- Trust position, or a sync whose read-back passed away from the pole,
  clears it, on every driver (base, the AM5, ``sync_verify`` for Alpaca,
  NINA and the ASIAIR).
- The doubt survives a profile activate that builds a NEW telescope object
  (``devices.base.rig_position_known`` keeps a record on the hub): the
  ladder, its tick, the nudge gate, the jog and dawn park all read it, and
  Trust position forgets it.
- The stop's tracking stop is read back, and asked again on its own bounded
  clock when it is not confirmed.
- Setup's acquisition (every hop, the one after a StopTarget included) is
  gated on the position, and so is the no-light hold inside it.
- The run-ending stop for a sync refused in place, or an unsolved
  reset-sized jump, is kept for a mount that can lose its frame (the AM5);
  any other mount stops the target.
- Dawn park stops tracking instead of parking a mount whose position is
  unknown (#874).

Every mutant named below was applied to a byte backup of its production
file, run under the suite's normal command, and the file restored from the
backup with its sha256 checked. Coordinates are made up; none is printed.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.flows.tonight as tonight_mod
import astrodeck.sequence.engine as engine_mod
import astrodeck.sequence.resume_arm as ra
from astrodeck import dawn_park as dawn_park_mod
from astrodeck.config import SafetyConfig
from astrodeck.dawn_park import DawnPark, park_threshold_deg
from astrodeck.devices import sync_verify as sv
from astrodeck.devices.backends import zwo_am5 as am5
from astrodeck.devices.base import (RIG_DOUBT_ATTR, SYNC_POLE_BLIND_DEG,
                                     SyncRefused, rig_position_known)
from astrodeck.devices.sim import SimTelescope
from astrodeck.sequence.engine import (L_SKIP, PositionUnknownStop,
                                       SequenceEngine)

from test_850_engine_sync_refused import _humanizer_rewrites, _scripted
from test_850_resume_sync_refused import (  # noqa: F401 (fixtures too)
    _arm, _isolated_sessions, _refusal, _session, fp)
from test_851_pointing_recheck import WHERE
from test_867_ladder_position_unknown import (_CENTRED, _hub_with,
                                              _script_moved, _script_noop)
from test_centring_settings_reach_goto import _setup, _target
from test_dawn_park import (FakeCam, FakeEngine, FakeHub, FakeTel,  # noqa: F401
                            _daytime, cfg, nothing_armed)
from test_engine_safety import (light_plan, sim_hub, temp_store,  # noqa: F401
                                wait_for)
from test_zwo_am5 import FakeLink, _connect_script, fixed_env  # noqa: F401

#: The humanizer's cut (ui/src/lib/humanize.ts): 137 characters plus "...".
_CUT = 137
_RA_H = 7.4321
_GOTO_WORDS = ("goto", "go to", "slew")


# ===================================================== the latch, every driver

def test_the_base_latch_is_set_and_cleared_by_evidence():
    """``mark_position_unknown`` latches ``position_known`` False on a plain
    driver; a sync proved away from the pole and Trust position each clear
    it; a sync proved within ``SYNC_POLE_BLIND_DEG`` of a pole does not.

    MUTANT B1 "the latch ignored" (the base ``position_known`` getter made
    ``return True``): RED -
        AssertionError: assert True is False
    MUTANT B2 "the pole rule dropped" (``far = abs(float(dec_deg)) <= 90.0
    - SYNC_POLE_BLIND_DEG`` made ``far = True``): RED -
        AssertionError: a near-pole sync cleared the latch
    """
    tel = SimTelescope("plain")
    assert tel.position_known is True, "premise: nothing marked"
    tel.mark_position_unknown("a fictional reason")
    assert tel.position_known is False
    for dec in (90.0 - SYNC_POLE_BLIND_DEG + 0.1, -89.0):
        tel.note_verified_sync(dec)
        assert tel.position_known is False, (
            "a near-pole sync cleared the latch")
    tel.note_verified_sync(90.0 - SYNC_POLE_BLIND_DEG - 0.1)
    assert tel.position_known is True


async def test_trust_position_clears_the_base_latch():
    """The route's verb on any driver that does not override it.

    MUTANT B3 "trust leaves the latch" (the base ``trust_position`` body made
    ``return None``): RED -
        AssertionError: assert False is True
    """
    tel = SimTelescope("plain")
    tel.mark_position_unknown("a fictional reason")
    await tel.trust_position()
    assert tel.position_known is True


async def _known_am5():
    """A real AM5 driver whose connect read a declination away from the
    pole, so its own latch is clear."""
    link = FakeLink(_connect_script(GD="+40*00:00"))
    tel = am5.ZwoAm5Telescope(link, name="Mount")
    await tel.connect()
    return link, tel


async def test_the_am5_honours_the_rig_latch(fixed_env):
    """The AM5 overrides ``position_known``; it must read the rig latch too,
    and its Trust position must clear it.

    MUTANT A1 "the AM5 ignores the latch" (``and self._position_doubt is
    None`` dropped from its ``position_known``): RED -
        AssertionError: assert True is False
    MUTANT A2 "the AM5's trust keeps it" (``await super().trust_position()``
    removed from its ``trust_position``): RED -
        AssertionError: assert False is True
    """
    _link, tel = await _known_am5()
    assert tel.position_known is True, "premise: the connect did not latch"
    assert tel.frame_can_reset is True
    tel.mark_position_unknown("a fictional reason")
    assert tel.position_known is False
    await tel.trust_position()
    assert tel.position_known is True


@pytest.mark.parametrize("dec, clears", [(40.0, True), (89.97, False)],
                         ids=["away", "near-pole"])
async def test_an_am5_sync_clears_the_rig_latch_by_the_pole_rule(
        fixed_env, bus_lines, dec, clears):
    """A sync the AM5's read-back proved clears the rig latch away from the
    pole, and keeps it near the pole, saying so once in the safe order.

    MUTANT A3 "the AM5's sync leaves the rig latch" (``self.note_verified_
    sync(dec_deg)`` removed from its ``sync``): RED, "away" -
        AssertionError: assert False is True
    """
    link, tel = await _known_am5()
    tel.mark_position_unknown("a fictional reason")
    (_script_moved if clears else _script_noop)(link, _RA_H, dec)
    if not clears:
        link.script["GD"] = "+89*58:12"
    await tel.sync(_RA_H, dec)
    assert tel.position_known is clears
    said = [m for lvl, m, s in bus_lines if lvl == "warning" and s == "mount"
            and "position still unknown" in m]
    assert len(said) == (0 if clears else 1), said


class _ReadBackTel:
    """A driver-like object whose bound read the read-back uses, and which
    keeps the base latch rules (``note_verified_sync``)."""

    def __init__(self, pos):
        self.pos = pos
        self.inner = SimTelescope("plain")
        self.inner.mark_position_unknown("a fictional reason")

    async def get_position(self):
        return self.pos

    def note_verified_sync(self, dec_deg):
        self.inner.note_verified_sync(dec_deg)


async def test_a_proved_sync_through_sync_verify_clears_the_latch(
        monkeypatch):
    """Alpaca, NINA and the ASIAIR read their syncs back through
    ``verify_sync``, which tells the driver it bound ``read_position`` to.
    A refused sync tells it nothing.

    MUTANT S1 "no word to the driver" (the ``_note_proved(...)`` call in
    ``verify_sync`` removed): RED -
        AssertionError: assert False is True
    """
    monkeypatch.setattr(sv, "SYNC_READBACK_RETRY_S", 0.0)
    good = _ReadBackTel((5.0, 30.0))
    await sv.verify_sync("Fictional mount", good.get_position, 5.0, 30.0)
    assert good.inner.position_known is True

    bad = _ReadBackTel((5.0, 33.0))
    with pytest.raises(SyncRefused):
        await sv.verify_sync("Fictional mount", bad.get_position, 5.0, 30.0)
    assert bad.inner.position_known is False


# ============================================= the run end marks it (0, 5)

async def _run_to_a_position_unknown_stop(e) -> None:
    async def recheck(target=None):
        await e._stop_run_position_unknown(target, WHERE)
    e._maybe_recheck_pointing = recheck
    e.start(light_plan())
    assert await wait_for(lambda: e._task is not None and e._task.done(),
                          timeout=30), e.state
    assert e.state.get("end_reason") == "unsafe", e.state


async def test_a_second_armed_session_is_held_after_the_stop(
        sim_hub, temp_store, monkeypatch, bus_lines):
    """Finding 0. A run ends with ``PositionUnknownStop`` on the simulator's
    mount, whose own driver never doubted its frame. ANOTHER armed session's
    ladder then gets a refused blind-solve sync within its 5 degree
    go-ahead: it must hold in the safe order, not slew. Finding 5: the same
    mark makes ``position_known`` False, which is what puts both UIs' Trust
    position button on screen, and Trust position lets the ladder go ahead.

    MUTANT E1 "the doubt stays the session's" (the
    ``self._mark_rig_position_unknown()`` call in `_run`'s unsafe arm
    removed): RED -
        AssertionError: assert ['solve', 'center'] == ['solve']
    """
    temp_store.set_safety(SafetyConfig(enabled=False))
    e = SequenceEngine(sim_hub)
    await _run_to_a_position_unknown_stop(e)
    tel = sim_hub.devices["telescope"]

    hub = _hub_with(tel, solve_raises=_refusal(0.4), centring=_CENTRED)
    reason = await _arm(hub, monkeypatch)._recover(_session())
    assert hub.calls == ["solve"], hub.calls
    assert reason == ra.POSITION_UNKNOWN_WORDS
    assert tel.position_known is False

    await tel.trust_position()
    hub = _hub_with(tel, solve_raises=_refusal(0.4), centring=_CENTRED)
    assert await _arm(hub, monkeypatch)._recover(_session()) is None
    assert hub.calls == ["solve", "center"]


# ================================== the stop of tracking is read back (2)

class _Stuck:
    """The simulator mount's tracking calls replaced: a stop that does not
    take until ``obey`` is set, every ask recorded."""

    def __init__(self, tel, monkeypatch):
        self.asks = 0
        self.obey = False
        self.tracking = True

        async def set_tracking(on):
            if not on:
                self.asks += 1
                if self.obey:
                    self.tracking = False
            else:
                self.tracking = True

        async def get_tracking():
            return self.tracking

        monkeypatch.setattr(tel, "set_tracking", set_tracking)
        monkeypatch.setattr(tel, "get_tracking", get_tracking)


@pytest.fixture
def quick_retries(monkeypatch):
    monkeypatch.setattr(engine_mod, "IDLE_STOP_RETRY_S", 0.05)
    monkeypatch.setattr(engine_mod, "TRACKING_CONFIRM_S", 0.0)


def _said(bus_lines, start: str) -> list[str]:
    return [m for _l, m, _s in bus_lines if m.startswith(start)]


async def test_an_unconfirmed_stop_is_said_and_asked_again_within_a_bound(
        sim_hub, temp_store, monkeypatch, bus_lines, quick_retries):
    """Finding 2. The mount ignores the stop: one error line, the cause and
    the whole safe order inside the cut, no goto word; then the stop is
    asked again on its own clock, at most ``POSITION_UNKNOWN_STOP_ASKS``
    times, and a last error line says nothing will ask again.

    MUTANT Q1 "fire and forget" (the ``await self._confirm_quiet_stop()``
    call in `_run`'s quiet wind-down removed): RED -
        AssertionError: [] (no "tracking not confirmed off" line)
    MUTANT Q2 "no bound" (``for _ in range(POSITION_UNKNOWN_STOP_ASKS):``
    in `_quiet_stop_retry` made ``for _ in range(10 ** 9):``): RED -
        AssertionError: the retries never gave up
    """
    monkeypatch.setattr(engine_mod, "POSITION_UNKNOWN_STOP_ASKS", 3)
    temp_store.set_safety(SafetyConfig(enabled=False))
    stuck = _Stuck(sim_hub.devices["telescope"], monkeypatch)
    e = SequenceEngine(sim_hub)
    await _run_to_a_position_unknown_stop(e)
    first = _said(bus_lines, "tracking not confirmed off")
    assert len(first) == 1, first
    head = first[0][:_CUT]
    for part in ("position unknown", "Trust position", "pad key",
                 "then Trust it."):
        assert part in head, (part, head)
    assert await wait_for(
        lambda: _said(bus_lines, "tracking never confirmed off"),
        timeout=5), "the retries never gave up"
    assert stuck.asks == 1 + 3, stuck.asks
    for m in first + _said(bus_lines, "tracking never confirmed off"):
        assert not _humanizer_rewrites(m), m
        for word in _GOTO_WORDS:
            assert word not in m.lower(), (word, m)


async def test_the_retries_end_once_the_position_is_known(
        sim_hub, temp_store, monkeypatch, bus_lines, quick_retries):
    """Once Trust position clears the doubt the operator has the mount: the
    retries stop asking, so a tracking they turn on is not turned off.

    MUTANT Q3 "asks whatever the position" (``if not
    self._position_unknown(): return`` in `_quiet_stop_retry` removed): RED -
        AssertionError: still asking after Trust position
    """
    monkeypatch.setattr(engine_mod, "POSITION_UNKNOWN_STOP_ASKS", 10_000)
    temp_store.set_safety(SafetyConfig(enabled=False))
    tel = sim_hub.devices["telescope"]
    stuck = _Stuck(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    await _run_to_a_position_unknown_stop(e)
    assert await wait_for(lambda: stuck.asks >= 2, timeout=5), stuck.asks
    await tel.trust_position()
    await asyncio.sleep(0.2)
    before = stuck.asks
    await asyncio.sleep(0.4)
    assert stuck.asks == before, "still asking after Trust position"
    task = e._idle_stop_task
    assert task is None or task.done()


async def test_a_stop_that_takes_late_is_said_once_confirmed(
        sim_hub, temp_store, monkeypatch, bus_lines, quick_retries):
    """CONTROL for the retry's exit: the mount takes the second ask, one
    info line says so, and nothing asks again.

    MUTANT Q4 "never believes the read-back" (``if await
    self._tracking_now() is False:`` in `_quiet_stop_retry` made ``if
    False:``): RED -
        AssertionError: assert False (the retry task never ended)
    """
    monkeypatch.setattr(engine_mod, "POSITION_UNKNOWN_STOP_ASKS", 10_000)
    temp_store.set_safety(SafetyConfig(enabled=False))
    stuck = _Stuck(sim_hub.devices["telescope"], monkeypatch)
    e = SequenceEngine(sim_hub)
    await _run_to_a_position_unknown_stop(e)
    stuck.obey = True
    assert await wait_for(
        lambda: e._idle_stop_task is None or e._idle_stop_task.done(),
        timeout=5)
    said = [m for _l, m, _s in bus_lines
            if m == "the mount has now confirmed it stopped tracking"]
    assert len(said) == 1


@pytest.mark.parametrize("unknown", [True, False],
                         ids=["position unknown", "control: set not to park"])
async def test_the_set_not_to_park_line_is_not_said_on_this_ending(
        sim_hub, temp_store, bus_lines, unknown):
    """A position-unknown stop is not a run "set not to park": its own line
    says what happened, and "Dawn park will park it" would be false.

    MUTANT Q5 "the line on every ending" (``and not getattr(self,
    "_ended_position_unknown", False)`` dropped): RED, "position unknown" -
        AssertionError: ['this run was set not to park ...']
    """
    e = SequenceEngine(sim_hub)
    e._frames_done = 3
    e._ended_position_unknown = unknown
    await e._wind_down_park_and_close(False, False)
    said = [m for _l, m, _s in bus_lines if "set not to park" in m]
    assert len(said) == (0 if unknown else 1), said


# ======================================== setup's acquisition is gated (1)

async def test_setup_is_gated_before_the_rotator_test(bus_lines):
    """Finding 1 / N6. A position the driver calls unknown ends the run
    before anything of the acquisition moves the mount, the rotator
    self-test (which slews) included.

    MUTANT G1 "the first setup gate removed" (the ``await self._gate_
    position_known(target, "acquiring the target")`` after
    `_await_target_window` removed): RED -
        AssertionError: the rotator test ran: ['rotator']
    """
    t = _target(name="Fictional F")
    e, hub = _setup(t)
    ran: list[str] = []

    async def rotator(target):
        ran.append("rotator")
    e._ensure_rotator_self_test = rotator
    hub.tel.position_known = False
    with pytest.raises(PositionUnknownStop):
        await e._setup_target(0, t)
    assert ran == [], f"the rotator test ran: {ran}"
    assert hub.gotos == []


async def test_setup_is_gated_again_right_before_its_goto(bus_lines):
    """The flag can turn False while setup waits (an AM5 link reopen latches
    it on the link's clock): the gate right before the acquisition goto
    still ends the run without moving the mount. This is also the hop after
    a StopTarget, which goes through the same setup.

    MUTANT G2 "the goto ungated" (the second ``await self._gate_position_
    known(...)`` in `_setup_target`, after the guider stand-down, removed):
    RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    t = _target(name="Fictional F")
    e, hub = _setup(t)

    async def rotator(target):
        hub.tel.position_known = False
    e._ensure_rotator_self_test = rotator
    with pytest.raises(PositionUnknownStop):
        await e._setup_target(0, t)
    assert hub.gotos == []
    said = [m for lvl, m, _s in bus_lines if lvl == "warning"]
    assert said and said[-1].endswith(
        "the run stops without moving the mount (acquiring the target)")
    for word in _GOTO_WORDS:
        assert word not in said[-1].lower()


async def test_setups_hold_for_light_is_gated_before_every_retry(
        monkeypatch, bus_lines):
    """The acquisition's no-light hold can run for an hour; a flag that
    turns False during it ends the run before the next retry's goto.

    MUTANT G3 "setup's hold ungated" (the ``position_gate=...`` argument
    removed from setup's `_hold_for_light` call): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    monkeypatch.setattr(tonight_mod, "target_own_window",
                        lambda *a, **kw: None)
    t = _target(name="Fictional F")
    e, hub = _setup(t)
    _scripted(hub, [{"centered": False, "error_arcmin": None}])
    plain = hub.goto_and_center

    async def goto(*a, **kw):
        res = await plain(*a, **kw)
        hub.tel.position_known = False   # a link reopen read the home pole
        return res
    hub.goto_and_center = goto
    with pytest.raises(PositionUnknownStop):
        await e._setup_target(0, t)
    assert len(hub.gotos) == 1, hub.gotos


# ============================================================ wording (7)

def test_the_skip_line_does_not_promise_a_re_centre():
    """Finding 7. Recovery re-centres only a target that centres, so the
    skip line says so, inside the cut and in words the UI shows as they are.

    MUTANT W1 "the old words" (L_SKIP back to "... recovery re-centres and
    restarts guiding first"): RED -
        AssertionError: assert 're-centring if this target centres' in ...
    """
    assert "re-centring if this target centres" in L_SKIP
    assert "recovery re-centres" not in L_SKIP
    assert len(L_SKIP) <= _CUT
    assert not _humanizer_rewrites(L_SKIP)


# ============================================================ dawn park (#874)

class _DoubtTel(FakeTel):
    """test_dawn_park's mount, with its position in doubt and a tracking
    state. ``obey`` False: a stop of tracking does not take."""

    def __init__(self, *, hub=None, tracking=True, obey=True):
        super().__init__(hub=hub)
        self.position_known = False
        self.tracking = tracking
        self.obey = obey
        self.stops = 0

    async def get_tracking(self) -> bool:
        return self.tracking

    async def set_tracking(self, on: bool) -> None:
        if not on:
            self.stops += 1
            if self.obey:
                self.tracking = False


def _doubt_rig(**kw):
    hub = FakeHub()
    tel = _DoubtTel(hub=hub, **kw)
    hub.devices["telescope"] = tel
    hub.devices["camera"] = FakeCam()
    ts, alt = _daytime()
    return hub, tel, ts, alt


async def test_dawn_park_stops_tracking_instead_of_parking(cfg, bus_lines):
    """#874. The Sun is up, nothing runs, the mount is unparked and its
    position is unknown: no park (a goto to where the mount BELIEVES home
    is), tracking stopped and read back, one line in fixed words with the
    safe order inside the cut, the cooler released, the night settled.

    MUTANT D1 "parks whatever the position" (the ``if not getattr(tel,
    "position_known", True):`` block in `tick` removed): RED -
        AssertionError: assert 1 == 0 (park_calls)
    MUTANT D2 "the cooler kept" (``await self._release_cooler(alt)`` in
    `_stop_tracking_instead` removed): RED -
        AssertionError: assert [] == ['dawn']
    """
    hub, tel, ts, alt = _doubt_rig()
    assert alt > park_threshold_deg(cfg), "precondition: the Sun is up"
    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    assert tel.park_calls == 0
    assert tel.stops == 1 and tel.tracking is False
    assert hub.warm_calls == ["dawn"]
    said = [m for _l, m, _s in bus_lines
            if m == dawn_park_mod.POSITION_UNKNOWN_LINE]
    assert len(said) == 1, bus_lines
    head = said[0][:_CUT]
    assert head.index("Trust position") < head.index("pad key") < head.index(
        "then Trust it."), head
    assert not any(ch.isdigit() for ch in said[0])
    assert not _humanizer_rewrites(said[0])
    for word in ("goto", "go to", "slew"):
        assert word not in said[0].lower()
    await d.tick()
    assert tel.stops == 1 and tel.park_calls == 0, "settled for the day"


async def test_dawn_park_retries_a_stop_that_does_not_take(cfg, bus_lines):
    """A stop of tracking the mount does not confirm is a failed attempt:
    loud once, not settled, asked again on the next tick; never a park.

    MUTANT D3 "settles on any answer" (the ``if tracking is not False:
    self._fail(...); return`` after the second read in
    `_stop_tracking_instead` removed): RED -
        AssertionError: [... no "DAWN PARK FAILED" line: it settled]
    """
    hub, tel, ts, _alt = _doubt_rig(obey=False)
    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    assert tel.park_calls == 0 and tel.stops == 1
    assert any("DAWN PARK FAILED" in m and "position is unknown" in m
               for _l, m, _s in bus_lines), bus_lines
    tel.obey = True
    await d.tick()
    assert tel.stops == 2, tel.stops
    assert tel.tracking is False and tel.park_calls == 0


async def test_an_unreadable_park_state_does_not_promise_a_park(cfg,
                                                               bus_lines):
    """The park-state read fails on a mount whose position is unknown: the
    line must not say "parking anyway", since no park follows.

    MUTANT D4 "the old words" (the ``parking=`` argument at `tick`'s
    `_is_parked` call removed, so it defaults to True): RED -
        AssertionError: ['dawn park could not read ... parking anyway ...']
    """
    hub, tel, ts, _alt = _doubt_rig()
    tel.query_error = RuntimeError("no answer")
    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()
    said = [m for _l, m, _s in bus_lines if "could not read" in m]
    assert len(said) == 1 and "parking anyway" not in said[0], said
    assert tel.park_calls == 0


# ================================ the doubt survives a profile activate

def _activated(hub) -> SimTelescope:
    """What a profile activate does to the telescope role: a NEW driver
    object, which has seen nothing."""
    new = SimTelescope("plain")
    hub.devices["telescope"] = new
    assert new.position_known is True, "premise: a fresh object knows nothing"
    return new


def _stopped_hub(sim_hub, **kw):
    """A ladder hub whose telescope the engine's run-ending arm has marked
    (`_mark_rig_position_unknown`, the call `_run`'s unsafe arm makes)."""
    old = SimTelescope("plain")
    hub = _hub_with(old, **kw)
    e = SequenceEngine(sim_hub)
    e.hub = hub
    e._mark_rig_position_unknown()
    assert old.position_known is False
    return hub


async def test_the_doubt_survives_a_profile_activate(sim_hub, monkeypatch):
    """Re-review, major. The run ended with ``PositionUnknownStop``, then the
    operator clicked Reconnect (a profile activate builds a NEW telescope
    object). Another armed session's ladder gets a refused sync within its
    5 degree go-ahead: it must still hold, and the new object must read
    unknown. Trust position on the new object then lets it go ahead.

    MUTANT C1 "the run end records nothing on the hub" (the
    ``rig_position_known(self.hub)`` call in `_mark_rig_position_unknown`
    removed): RED -
        AssertionError: ['solve', 'center']
    MUTANT C2 "the ladder reads only the object" (`_mount_position_known`
    made ``return bool(getattr(self.hub.devices.get("telescope"),
    "position_known", True))``): RED -
        AssertionError: ['solve', 'center']
    MUTANT C3 "no carry" (``if held is not None and held[0] is not tel:``
    in `rig_position_known` made ``if False:``): RED -
        AssertionError: ['solve', 'center']
    """
    hub = _stopped_hub(sim_hub, solve_raises=_refusal(0.4),
                       centring=_CENTRED)
    new = _activated(hub)
    reason = await _arm(hub, monkeypatch)._recover(_session())
    assert hub.calls == ["solve"], hub.calls
    assert reason == ra.POSITION_UNKNOWN_WORDS
    assert new.position_known is False

    await new.trust_position()
    hub.calls.clear()
    assert await _arm(hub, monkeypatch)._recover(_session()) is None
    assert hub.calls == ["solve", "center"]


async def test_the_ladder_tick_moves_the_doubt_onto_the_new_object(
        sim_hub, monkeypatch):
    """Nothing is armed, so no ladder runs, yet the new object must read
    unknown within one tick: that is what puts the UIs' Trust position
    button (which reads ``position_known``) back on screen.

    MUTANT C4 "the tick does not carry it" (the ``rig_position_known(
    self.hub)`` line at the top of `ResumeArm.tick` removed): RED -
        AssertionError: assert True is False
    """
    hub = _stopped_hub(sim_hub)
    new = _activated(hub)
    await _arm(hub, monkeypatch).tick()
    assert new.position_known is False


async def test_a_sync_proved_on_the_holding_object_clears_the_record(
        sim_hub):
    """CONTROL for the record's exit: evidence on the object that holds the
    doubt clears it for good, so a later activate starts known.

    MUTANT C5 "the record is never dropped" (``_set_rig_doubt(hub, None)``
    under ``if known:`` in `rig_position_known` removed): RED -
        AssertionError: assert False is True
    """
    hub = _stopped_hub(sim_hub)
    hub.devices["telescope"].note_verified_sync(30.0)
    assert rig_position_known(hub) is True
    new = _activated(hub)
    assert rig_position_known(hub) is True
    assert new.position_known is True


@pytest.fixture
def app_rig(monkeypatch):
    """The shipped app with a NEW telescope object on its hub, and the hub's
    record of a doubt taken on the object the activate replaced. The cases
    run the app WITHOUT its lifespan, so no ResumeArm tick carries the doubt
    first and each route's own read is what is tested.

    A nudge past its gate runs to the end here: the position read answers
    made-up coordinates, the horizon and Sun checks pass, and ``_spawn``
    records the lane and closes the coroutine (no lifespan, so nothing
    would run it). So a nudge the gate let through returns 200 with a
    recorded goto, and only the gate can make it a 409 (round 2)."""
    from astrodeck.api import app as app_module
    tel = SimTelescope("plain")
    moves: list[tuple[str, float]] = []
    spawned: list[str] = []

    async def move_axis(axis, rate):
        moves.append((axis, rate))

    async def get_position():
        return 7.25, 30.0

    def spawn(name, coro, *, replace=False):
        spawned.append(name)
        coro.close()
        return {"started": name}
    monkeypatch.setattr(tel, "move_axis", move_axis)
    monkeypatch.setattr(tel, "get_position", get_position)
    monkeypatch.setattr(app_module, "_spawn", spawn)
    monkeypatch.setattr(app_module.hub, "_check_solar",
                        lambda ra, dec, *, force=False: None)
    monkeypatch.setattr(app_module.hub, "_check_horizon",
                        lambda ra, dec: None, raising=False)
    monkeypatch.setattr(app_module.hub, "require", lambda role: tel)
    monkeypatch.setitem(app_module.hub.devices, "telescope", tel)
    monkeypatch.setattr(app_module.hub, RIG_DOUBT_ATTR,
                        (SimTelescope("plain"), "a fictional reason"),
                        raising=False)
    tel.spawned = spawned
    return app_module, tel, moves


def test_the_nudge_gate_reads_the_carried_doubt(app_rig):
    """A nudge computes its destination from the believed position, so the
    carried doubt refuses it as the driver's own would.

    MUTANT C6 "the nudge ungated" (since #886 the nudge asks the one route
    gate, ``_refuse_if_position_unknown(tel, POSITION_UNKNOWN_DETAIL)``,
    which reads the hub's record; the call made ``pass``): RED - the
    nudge read the position, computed its destination and spawned the
    goto -
        AssertionError: (200, '{"started":"goto","from":{"ra_hours":7.2')
    """
    from fastapi.testclient import TestClient

    from astrodeck.mount_offset import POSITION_UNKNOWN_CODE
    app_module, tel, _moves = app_rig
    c = TestClient(app_module.create_app())     # no lifespan: no tick
    r = c.post("/api/mount/nudge", json={"axis": "dec", "arcmin": 30.0})
    assert r.status_code == 409, (r.status_code, r.text[:40])
    assert POSITION_UNKNOWN_CODE in r.text
    assert tel.spawned == [], tel.spawned
    assert tel.position_known is False


def test_a_jog_under_the_carried_doubt_skips_the_sun_check_and_says_so(
        app_rig):
    """The jog still moves (it is the only way home by eye) but answers
    that the position is unknown, which is what the pad shows.

    MUTANT C7 "the jog reads only the object" (``or not
    rig_position_known(hub)`` dropped from the jog's ``position_unknown``):
    RED -
        AssertionError: {'ok': True}
    """
    from fastapi.testclient import TestClient
    app_module, _tel, moves = app_rig
    c = TestClient(app_module.create_app())     # no lifespan: no tick
    r = c.post("/api/mount/move", json={"axis": "ra", "rate_deg_s": 0.5})
    c.post("/api/mount/move", json={"axis": "ra", "rate_deg_s": 0.0})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "position_known": False}, r.json()
    assert moves and moves[0][0] == "ra"


def test_trust_position_forgets_the_carried_doubt(app_rig):
    """Trust position is the operator's word about the TUBE, so it drops
    the hub's record whichever object it was taken on.

    MUTANT C8 "trust keeps the record" (``forget_rig_position_doubt(hub)``
    removed from the trust-position route): RED -
        AssertionError: assert False is True
    """
    from fastapi.testclient import TestClient
    app_module, tel, _moves = app_rig
    c = TestClient(app_module.create_app())     # no lifespan: no tick
    r = c.post("/api/mount/trust-position")
    assert r.status_code == 200, r.text
    assert rig_position_known(app_module.hub) is True
    assert tel.position_known is True


# =================================== the re-review's untested arms (minor)

class _Unreadable(_Stuck):
    """The simulator mount whose tracking state cannot be read at all."""

    def __init__(self, tel, monkeypatch):
        super().__init__(tel, monkeypatch)

        async def get_tracking():
            raise RuntimeError("no answer")
        monkeypatch.setattr(tel, "get_tracking", get_tracking)


async def test_an_unreadable_tracking_state_is_not_a_confirmed_stop(
        sim_hub, temp_store, monkeypatch, bus_lines, quick_retries):
    """Finding 2's flaky link: the stop's read-back cannot be read. Unknown
    is not stopped: the error line names it and the stop is asked again.

    MUTANT RC "unknown counts as stopped" (``if tracking is False: return``
    in `_confirm_quiet_stop` made ``if tracking is not True: return``): RED -
        AssertionError: [] (no "tracking not confirmed off" line)
    """
    monkeypatch.setattr(engine_mod, "POSITION_UNKNOWN_STOP_ASKS", 3)
    temp_store.set_safety(SafetyConfig(enabled=False))
    mount = _Unreadable(sim_hub.devices["telescope"], monkeypatch)
    e = SequenceEngine(sim_hub)
    await _run_to_a_position_unknown_stop(e)
    first = _said(bus_lines, "tracking not confirmed off")
    assert len(first) == 1, first
    assert "its tracking state cannot be read" in first[0]
    assert await wait_for(
        lambda: _said(bus_lines, "tracking never confirmed off"),
        timeout=5), "the retries never gave up"
    assert mount.asks == 1 + 3, mount.asks


async def test_a_retry_whose_slot_moved_on_mid_ask_ends_quietly(
        sim_hub, temp_store, monkeypatch, bus_lines, quick_retries):
    """A parking ending moves the idle-stop slot on while a retry's ask is
    in flight: the retry ends there, with no further ask and no escalation
    line claiming nothing will ask again (the slot's new owner decides).

    MUTANT RG "no fence after the ask" (``if fence != self._idle_stop_epoch:
    return`` after the ask in `_quiet_stop_retry` removed): RED -
        AssertionError: ['tracking never confirmed off, ...']
    """
    monkeypatch.setattr(engine_mod, "POSITION_UNKNOWN_STOP_ASKS", 3)
    temp_store.set_safety(SafetyConfig(enabled=False))
    tel = sim_hub.devices["telescope"]
    stuck = _Stuck(tel, monkeypatch)
    e = SequenceEngine(sim_hub)
    plain = tel.set_tracking

    async def set_tracking(on):
        await plain(on)
        if not on and stuck.asks == 2:      # the retry's first ask
            e._idle_stop_epoch += 1
    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    await _run_to_a_position_unknown_stop(e)
    assert await wait_for(
        lambda: e._idle_stop_task is None or e._idle_stop_task.done(),
        timeout=5)
    await asyncio.sleep(0.3)
    late = _said(bus_lines, "tracking never confirmed off")
    assert late == [], late
    assert stuck.asks == 2, stuck.asks


async def test_the_disarm_line_keeps_its_action_inside_the_cut(
        sim_hub, temp_store, bus_lines):
    """The disarm line's action comes first and the session's name last, so
    a long name cannot push "arm it from the session list" past the UI's
    cut; and it names both things that clear the doubt.

    MUTANT W2 "the name first" (the line back to ``f"'{self._session.name}':
    auto-resume disarmed, position unknown: once Trust position or a sync
    away from the pole clears it, arm it from the session list"``): RED -
        AssertionError: ('sync away from the pole', "'A fictional session
        with a rather long name, too': auto-resume disarmed, ...")
    """
    temp_store.set_safety(SafetyConfig(enabled=False))
    e = SequenceEngine(sim_hub)

    async def recheck(target=None):
        await e._stop_run_position_unknown(target, WHERE)
    e._maybe_recheck_pointing = recheck
    e.start(light_plan())
    name = "A fictional session with a rather long name, too"
    e._session.name = name
    assert await wait_for(lambda: e._task is not None and e._task.done(),
                          timeout=30), e.state
    said = [m for _l, m, _s in bus_lines
            if "auto-resume" in m and "position unknown" in m]
    assert len(said) == 1, said
    head = said[0][:_CUT]
    for part in ("auto-resume disarmed", "Trust position",
                 "sync away from the pole", "arm it from the session list"):
        assert part in head, (part, head)
    assert said[0].endswith(f"('{name}')"), said[0]
    assert not _humanizer_rewrites(said[0])
    for word in _GOTO_WORDS:
        assert word not in said[0].lower()


async def test_dawn_park_does_not_settle_on_an_unreadable_tracking_state(
        cfg, bus_lines):
    """#874. The mount's tracking state cannot be read at all: unknown is
    not stopped, so the day is not settled and the next tick asks again.

    MUTANT RD "unknown settles the day" (the second ``if tracking is not
    False:`` in `_stop_tracking_instead` made ``if tracking is True:``):
    RED -
        AssertionError: [... no "DAWN PARK FAILED" line: it settled]
    """
    hub, tel, ts, _alt = _doubt_rig()

    async def get_tracking():
        raise RuntimeError("no answer")
    tel.get_tracking = get_tracking
    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    assert tel.park_calls == 0 and tel.stops == 1
    assert any("DAWN PARK FAILED" in m and "position is unknown" in m
               for _l, m, _s in bus_lines), bus_lines
    await d.tick()
    assert tel.stops == 2, "settled on an unreadable state"
    assert tel.park_calls == 0
