# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The moved-since-solve detector acts, and nothing aims a move from the
report it doubts (#851, the software part).

On 2026-10-07 the hub's "moved N degrees since the last plate solve" check
fired three times (2.03, 2.34, 2.77 degrees) and only cleared a field caption
while the run imaged the wrong field. Now:

- the hub COUNTS each such move (``pointing_disagreements``), once per move
  across the two readers of a saved light, and its line no longer reads to
  the UI's humanizer as a solve failure;
- the engine acts on each new one at a frame boundary by SOLVING AND SYNCING
  IN PLACE (no motion): within the centring ceiling it carries on, past it
  the walking-field hold re-centres from the model the sync just corrected;
- with the driver saying the position is unknown, a sync the mount would not
  take in place, or a reset-sized jump whose field will not solve, the RUN
  ends without moving the mount (`PositionUnknownStop`: no park, no roof
  close, tracking stopped), and every hold re-centre refuses to slew first;
- the checks are bounded by measured time per target per night.

The hub is the real ``Hub`` on the simulator where the detector itself is
graded, and the recording double of test_centring_settings_reach_goto.py
where the engine is (``solve_and_sync`` answering at its real keyword
signature). Coordinates are fictional, away from the pole, and no line here
prints one. Every mutant named below was applied to a byte copy of the
production file, run under the suite's normal command, and the file restored
from the copy with its sha256 checked.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import astrodeck.config as config_mod
import astrodeck.flows.tonight as tonight_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.catalog.coords import angular_sep_deg
from astrodeck.config import ConfigStore, SafetyConfig, Site
from astrodeck.devices.base import DeviceError, SyncRefused, SyncUnverified
from astrodeck.hub import Hub
from astrodeck.sequence.engine import (CENTRING_UNSOLVED_TWICE,
                                       POSITION_UNKNOWN_STOP,
                                       PositionUnknownStop, SafetyAbort,
                                       SequenceEngine, StopTarget)
from astrodeck.sequence.session import session_store

from test_centring_settings_reach_goto import (_after_a_lost_star,
                                               _after_a_walking_field,
                                               _after_the_sweep, _setup,
                                               _target)
from test_850_engine_sync_refused import _humanizer_rewrites, _scripted
from test_engine_safety import (light_plan, sim_hub, temp_store,  # noqa: F401
                                wait_for)
from test_field_identification import _adopt

OPTICS = {"fov_w_deg": 1.123, "fov_h_deg": 0.75}
WHERE = "re-checking the pointing"


@pytest.fixture(autouse=True)
def _the_window_is_open(monkeypatch):
    monkeypatch.setattr(tonight_mod, "target_own_window",
                        lambda *a, **kw: None)


# ============================================================ the hub's count

@pytest.fixture
async def real_hub(tmp_path, monkeypatch):
    """test_field_identification.py's hub: the real Hub on the simulator."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(config_mod, "config_store", store)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def test_a_report_that_moved_past_the_threshold_counts_a_disagreement(
        real_hub):
    """The report moved several degrees from the one the solve was adopted
    at, with no slew clearing it: one disagreement, with its separation.

    MUTANT "a caption only" (the ``self.pointing_disagreements += 1`` line
    in `_current_field_solve` deleted): RED -
        AssertionError: assert 0 == 1
    """
    h = real_hub
    h._note_pointing(5.0, 30.0)
    await _adopt(h)
    h._note_pointing(5.0, 36.0)
    assert h.field_identification() is None
    assert h.pointing_disagreements == 1
    assert h.last_pointing_disagreement["moved_deg"] == pytest.approx(
        angular_sep_deg(5.0, 30.0, 5.0, 36.0))


async def test_control_a_dither_and_an_explicit_slew_count_nothing(real_hub):
    """CONTROL. A dither-sized nudge is no move, and a slew that cleared the
    field solve itself leaves nothing for the detector to see.

    MUTANT "count every invalidation" (the increment moved into
    `invalidate_field_solve`): RED -
        AssertionError: an explicit slew counted as a disagreement: 1
    """
    h = real_hub
    h._note_pointing(5.0, 30.0)
    await _adopt(h)
    h._note_pointing(5.0 + 0.0002, 30.003)          # ~11 arcsec
    assert h.field_identification() is not None
    h.invalidate_field_solve("the mount is slewing to a new target")
    h._note_pointing(5.0, 36.0)
    h.field_identification()
    assert h.pointing_disagreements == 0, (
        f"an explicit slew counted as a disagreement: "
        f"{h.pointing_disagreements}")


async def test_the_cleared_line_does_not_read_as_a_solve_failure(real_hub,
                                                                 bus_lines):
    """The UI's humanizer rewrites any line with "plate" and "solve" into
    "Plate-solve failed - check focus/exposure"; the detector's line is about
    the mount, not the optics.

    MUTANT "the old words" (the reason put back as ``f"the mount has moved
    {moved:.2f}° since the last plate solve"``): RED -
        AssertionError: the humanizer rewrites: 'field identification
        cleared: the mount has moved 6.00° since the last plate solve'
    """
    h = real_hub
    h._note_pointing(5.0, 30.0)
    await _adopt(h)
    h._note_pointing(5.0, 36.0)
    h.field_identification()
    cleared = [m for _l, m, _s in bus_lines
               if m.startswith("field identification cleared")]
    assert len(cleared) == 1, cleared
    assert not _humanizer_rewrites(cleared[0]), (
        f"the humanizer rewrites: {cleared[0]!r}")


async def test_one_move_counts_once_across_both_readers(real_hub):
    """A saved light reaches the detector twice (the header's
    `field_identification` and the preview's `_field_block`). One move of the
    mount's report counts once, and the frames after it count nothing more.

    MUTANT "a caption that stays" (the ``invalidate_field_solve(...)`` call
    in the ``moved > threshold`` branch removed): RED -
        AssertionError: one move counted 2 times after one frame
    """
    h = real_hub
    tel = h.devices["telescope"]
    await tel.slew(5.0, 30.0)
    ra, dec = await tel.get_position()
    h._note_pointing(ra, dec)
    await _adopt(h)
    await tel.slew(5.0, 32.0)
    await h.capture(0.2, 100, 30, 1, save=True, target="x")
    assert h.pointing_disagreements == 1, (
        f"one move counted {h.pointing_disagreements} times after one frame")
    await h.capture(0.2, 100, 30, 1, save=True, target="x")
    assert h.pointing_disagreements == 1, (
        f"a later frame counted the same move again: "
        f"{h.pointing_disagreements}")


# =============================================================== the engine

class _Guider:
    connected = True

    def __init__(self, active: bool) -> None:
        self.calls: list[str] = []
        self.active = active

    async def is_active(self) -> bool:
        return self.active

    async def stop_guiding(self) -> None:
        self.calls.append("stop")

    def clear_calibration(self) -> None:
        self.calls.append("clear")

    async def start_guiding(self) -> None:
        self.calls.append("start")

    def stats(self):
        return None


def _engine(*, moved: float = 1.9, gen: int = 1, active: bool = True,
            solve=None, target=None, optics: bool = True):
    """An engine whose hub has counted ``gen`` disagreements, the latest a
    move of ``moved`` degrees, and whose ``solve_and_sync`` answers ``solve``
    (a dict, an exception, or a callable) and records each call's keywords."""
    t = target or _target(name="Fictional F")
    e, hub = _setup(t)
    e.plan.guide = True
    e._policy = SimpleNamespace(recover_guiding=True)
    hub.guider = _Guider(active)
    if optics:
        hub.effective_optics = lambda: dict(OPTICS)
    hub.pointing_disagreements = gen
    hub.last_pointing_disagreement = {"moved_deg": moved, "at": time.time()}
    hub.solves = []

    async def solve_and_sync(exposure_s=3.0, *, blind=False,
                             refusal_level="warning"):
        hub.solves.append({"exposure_s": exposure_s, "blind": blind,
                           "refusal_level": refusal_level})
        answer = solve
        if callable(answer) and not isinstance(answer, type):
            answer = answer()
        if isinstance(answer, BaseException):
            raise answer
        return dict(answer)
    hub.solve_and_sync = solve_and_sync
    _scripted(hub, [{"centered": True, "error_arcmin": 0.3}])
    return e, hub, t


def _off(t, arcmin: float) -> dict:
    """A solve ``arcmin`` north of the target (fictional coordinates)."""
    return {"ra_hours": t.ra_hours, "dec_deg": t.dec_deg + arcmin / 60.0,
            "solver": "stub", "pixel_scale": 1.0}


async def test_a_disagreement_is_checked_in_place_without_moving(bus_lines):
    """The autofocus case: the report walked 1.9 deg, the tube held. One
    blind solve and sync where the mount points, no goto, guiding untouched,
    and one info line.

    MUTANT "slew first" (``await self._hold_recentre_recalibrate("x", target,
    after_inplace_miss=True); return`` inserted before the in-place solve):
    RED -
        AssertionError: the re-check moved the mount: 1 gotos
    """
    t = _target(name="Fictional F")
    e, hub, t = _engine(target=t, solve=_off(t, 2.0))
    await e._maybe_recheck_pointing(t)
    assert hub.gotos == [], f"the re-check moved the mount: {len(hub.gotos)} gotos"
    assert len(hub.solves) == 1, hub.solves
    assert hub.solves[0]["blind"] is True
    assert hub.solves[0]["refusal_level"] == "info"
    assert hub.guider.calls == [], hub.guider.calls
    said = [m for lvl, m, _s in bus_lines if lvl == "info"
            and "synced, carrying on" in m]
    assert said == ["Fictional F: re-checked in place after the mount's report "
                    "moved 1.90°: 2.0' off target, synced, carrying on"], said


async def test_an_in_place_miss_holds_and_re_centres(bus_lines):
    """The field itself is 30' off, past the 11.25' ceiling: the walking
    hold stops, clears and restarts the running guider around ONE re-centre,
    aimed from the model the in-place sync corrected, and is charged to
    tonight's budget.

    MUTANT "never re-centre" (``if err <= ceiling:`` in
    `_maybe_recheck_pointing` made ``if True:``): RED -
        AssertionError: assert [] == [((22.6182, 34.4098), {...})]
    """
    t = _target(name="Fictional F")
    e, hub, t = _engine(target=t, solve=_off(t, 30.0))
    await e._maybe_recheck_pointing(t)
    assert len(hub.solves) == 1
    assert len(hub.gotos) == 1, hub.gotos
    assert hub.guider.calls == ["stop", "clear", "start"], hub.guider.calls
    assert e._field_holds[e._budget_key(t)] == 1
    for _l, m, _s in bus_lines:
        assert not _humanizer_rewrites(m), m
    said = [m for _l, m, _s in bus_lines if "holding to re-centre" in m]
    assert said == ["Fictional F: holding to re-centre: the field is 30.0' off "
                    "target, past the 11.2' limit, after the mount's report "
                    "moved 1.90°"], said


async def test_the_third_hold_from_a_re_check_names_the_target_once(
        bus_lines):
    """Two holds tonight already; the third comes from a pointing re-check
    whose in-place solve found the field 30' off. The target stops before
    any goto, and the line names it once: the re-check's reason carries no
    second copy of the name in front of it.

    MUTANT "the name twice" (``f"{name}: "`` put back in front of the
    reason `_maybe_recheck_pointing` hands `_hold_recentre_recalibrate`):
    RED -
        AssertionError: ["Fictional F: stopping this target; it has held 2
        times tonight to re-centre, and now Fictional F: the field is off
        target after the mount's report moved 1.90°"]
    """
    from astrodeck.sequence.engine import FIELD_HOLDS_SPENT
    t = _target(name="Fictional F")
    e, hub, t = _engine(target=t, solve=_off(t, 30.0))
    e._field_holds[e._budget_key(t)] = 2
    with pytest.raises(StopTarget) as ei:
        await e._maybe_recheck_pointing(t)
    assert str(ei.value) == FIELD_HOLDS_SPENT
    assert hub.gotos == []
    said = [m for _l, m, _s in bus_lines if "it has held 2 times" in m]
    assert said == ["Fictional F: stopping this target; it has held 2 times "
                    "tonight to re-centre, and now the field is off target "
                    "after the mount's report moved 1.90°"], said
    for _l, m, _s in bus_lines:
        assert not _humanizer_rewrites(m), m


async def test_an_inactive_guider_is_not_restarted(bus_lines):
    """A guider the engine stood down (or one that never started under
    ``guiding_action = warn``) is inactive: the hold re-centres and leaves it
    alone.

    MUTANT "guided from the plan" (``guided = await
    self._guiding_active_now()`` made ``guided = bool(self.plan.guide and g
    is not None and g.connected)``): RED -
        AssertionError: ['stop', 'clear', 'start']
    """
    t = _target(name="Fictional F")
    e, hub, t = _engine(target=t, solve=_off(t, 30.0), active=False)
    await e._maybe_recheck_pointing(t)
    assert len(hub.gotos) == 1
    assert hub.guider.calls == [], hub.guider.calls


async def test_position_unknown_stops_the_run_without_moving(bus_lines):
    """The driver latched "position unknown" (an AM5 reset reports its home
    pole wherever the tube is): no solve, no sync, no goto, the run ends,
    and the one line gives the safe order with no goto or slew in it.

    MUTANT "no gate" (the `_position_unknown` check in
    `_maybe_recheck_pointing` removed): RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>

    The RUNNING guider is stood down before the stop is raised: in `_run`'s
    quiet arm the tracking stop comes BEFORE the wind-down's own guider
    stop, and an AM5 east pulse is a tracking suspend ending in ``:Te#``,
    which would turn tracking back on behind it (#311).
    MUTANT "no stand-down" (the ``await self._stand_down_guider()`` in
    `_stop_run_position_unknown` removed): RED -
        AssertionError: the running guider was left pulsing: []
    """
    t = _target(name="Fictional F")
    e, hub, t = _engine(target=t, moved=88.0, solve=_off(t, 2.0), active=True)
    hub.tel.position_known = False
    with pytest.raises(PositionUnknownStop) as ei:
        await e._maybe_recheck_pointing(t)
    assert str(ei.value) == POSITION_UNKNOWN_STOP
    assert hub.solves == [] and hub.gotos == []
    assert hub.guider.calls == ["stop"], (
        f"the running guider was left pulsing: {hub.guider.calls}")
    warned = [m for lvl, m, _s in bus_lines if lvl == "warning"]
    assert len(warned) == 1 and "Trust position" in warned[0], warned
    for word in ("goto", "slew", "go to"):
        assert word not in warned[0].lower(), warned[0]
    assert warned[0].endswith(f"the run stops without moving the mount "
                              f"({WHERE})"), warned[0]


@pytest.mark.parametrize(
    "recentre", [_after_a_walking_field, _after_a_lost_star, _after_the_sweep],
    ids=["walking hold", "guide-star recovery", "post-sweep re-centre"])
async def test_position_unknown_gates_every_hold_re_centre(recentre, bus_lines):
    """Every mid-run re-centre is a goto aimed from the mount's position: with
    the driver saying it is unknown, each ends the run before slewing.

    MUTANT "the hold re-centre ungated" (the gate at the top of each attempt
    in `_recentre_for_hold` removed; re-run in fix round 1 as P9b): RED,
    "walking hold" and "guide-star recovery" -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    MUTANT "the sweep re-centre ungated" (the gate in
    `_recentre_after_unguided_focus` removed): RED, "post-sweep re-centre" -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    e, hub, t = _engine(active=False)
    hub.tel.position_known = False
    with pytest.raises(PositionUnknownStop):
        await recentre(e, t)
    assert hub.gotos == [], hub.gotos


@pytest.mark.parametrize("exc", [SyncRefused, SyncUnverified])
async def test_a_refused_in_place_sync_stops_the_run(exc, bus_lines):
    """The field solved and a mount that CAN LOSE ITS FRAME (the AM5,
    ``frame_can_reset``) would not take where it is, after its report moved
    without a slew: the model is the thing in doubt, so the run ends rather
    than letting the scheduler's next goto aim from it (ruling R10).

    MUTANT "a refusal is a failed solve" (the ``except (SyncRefused,
    SyncUnverified)`` arm removed, so both fall to the generic arm): RED,
    both -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    MUTANT F9b "never a frame reset" (``if self._frame_can_reset():`` in
    that arm made ``if False:``): RED, both -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    """
    e, hub, t = _engine(moved=1.9,
                        solve=exc("refused", code="e11", reason="no sync here"))
    hub.tel.frame_can_reset = True
    with pytest.raises(PositionUnknownStop):
        await e._maybe_recheck_pointing(t)
    assert hub.gotos == []
    assert any("the mount did not take the sync" in m
               for _l, m, _s in bus_lines), bus_lines
    for _l, m, _s in bus_lines:
        assert not _humanizer_rewrites(m), m
        assert "goto" not in m.lower() and "go to" not in m.lower(), m


@pytest.mark.parametrize("exc", [SyncRefused, SyncUnverified])
async def test_a_refused_in_place_sync_on_another_mount_stops_the_target(
        exc, bus_lines):
    """Finding 9: on a mount that cannot lose its frame (Alpaca, NINA,
    ASIAIR: ``frame_can_reset`` False), a refused or unverified in-place
    sync, often a transport hiccup on those drivers, stops the TARGET in the
    hub's fixed words and marks its pointing unverified. The run goes on,
    with its normal park and roof close, and nothing is moved.

    MUTANT F9a "every mount ends the run" (``if self._frame_can_reset():``
    in the ``except (SyncRefused, SyncUnverified)`` arm made ``if True:``):
    RED, both -
        astrodeck.sequence.engine.PositionUnknownStop: the mount's position
        is unknown, so the run stopped without moving it
    """
    from astrodeck.sequence.engine import (SOLVE_REASON_SYNC_REFUSED,
                                           SOLVE_REASON_SYNC_UNVERIFIED)
    e, hub, t = _engine(moved=1.9,
                        solve=exc("refused", code="e11", reason="no sync here"))
    assert not getattr(hub.tel, "frame_can_reset", False), "premise"
    with pytest.raises(StopTarget) as ei:
        await e._maybe_recheck_pointing(t)
    assert not isinstance(ei.value, SafetyAbort)
    assert str(ei.value) == (SOLVE_REASON_SYNC_REFUSED if exc is SyncRefused
                             else SOLVE_REASON_SYNC_UNVERIFIED)
    assert e._pointing_unverified_for == t.id
    assert hub.gotos == []
    for _l, m, _s in bus_lines:
        assert "Trust position" not in m, m
        assert not _humanizer_rewrites(m), m


@pytest.mark.parametrize("moved, stops", [(6.0, True), (1.9, False)])
async def test_a_large_move_that_will_not_solve_stops_the_run(moved, stops,
                                                              bus_lines):
    """A field that will not solve after a jump past 5 deg (reset-sized) is
    "position unknown"; after a 1.9 deg walk it marks the pointing
    unverified and moves nothing.

    MUTANT "never a reset" (``moved > POINTING_RESET_PLAUSIBLE_DEG`` made
    ``False``): RED, 6.0 -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
    MUTANT "always a reset" (made ``True``): RED, 1.9 -
        astrodeck.sequence.engine.PositionUnknownStop: the mount's position
        is unknown, so the run stopped without moving it
    """
    e, hub, t = _engine(moved=moved, solve=DeviceError("no solution"))
    hub.tel.frame_can_reset = True      # the AM5: a jump can be a reset
    if stops:
        with pytest.raises(PositionUnknownStop):
            await e._maybe_recheck_pointing(t)
    else:
        await e._maybe_recheck_pointing(t)
        assert e._pointing_unverified_for == t.id
    assert hub.gotos == []
    for _l, m, _s in bus_lines:
        assert not _humanizer_rewrites(m), m


async def test_a_large_unsolved_move_on_another_mount_stops_the_target(
        bus_lines):
    """Finding 9: a reset-sized jump whose field will not solve is a lost
    frame only on a mount that can lose one. Elsewhere the target stops in
    fixed words (``POINTING_JUMP_UNSOLVED``), unverified, nothing moved.

    MUTANT F9c "every jump is a reset" (``if self._frame_can_reset():`` in
    the generic arm made ``if True:``): RED -
        astrodeck.sequence.engine.PositionUnknownStop: ...
    """
    from astrodeck.sequence.engine import POINTING_JUMP_UNSOLVED
    e, hub, t = _engine(moved=6.0, solve=DeviceError("no solution"))
    with pytest.raises(StopTarget) as ei:
        await e._maybe_recheck_pointing(t)
    assert not isinstance(ei.value, SafetyAbort)
    assert str(ei.value) == POINTING_JUMP_UNSOLVED
    assert not any(ch.isdigit() for ch in str(ei.value))
    assert e._pointing_unverified_for == t.id
    assert hub.gotos == []
    assert not _humanizer_rewrites(POINTING_JUMP_UNSOLVED)


async def test_the_position_unknown_stop_does_not_park_or_close(
        sim_hub, temp_store, monkeypatch):
    """The REAL run's terminal arm: a `PositionUnknownStop` from the frame
    loop ends the run "unsafe", stops tracking first, and winds down with
    neither a park nor a roof close (ruling R9), even with
    ``close_dome_on_unsafe`` set.

    MUTANT "an unsafe stop like any other" (``quiet =
    isinstance(e, PositionUnknownStop)`` made ``quiet = False``): RED -
        AssertionError: [('wind_down', True, True)]
    """
    temp_store.set_safety(SafetyConfig(enabled=False,
                                       close_dome_on_unsafe=True))
    e = SequenceEngine(sim_hub)
    order: list = []
    real_wind, real_stop = e._wind_down, e._stop_tracking_quietly

    async def wind(park, warm, close_dome=False, day_darks=False):
        order.append(("wind_down", park, close_dome))
        await real_wind(park, warm, close_dome=close_dome, day_darks=day_darks)

    async def stop(*, fence=None):
        order.append("stop_tracking")
        await real_stop(fence=fence)

    async def recheck(target=None):
        await e._stop_run_position_unknown(target, WHERE)

    e._wind_down = wind
    e._stop_tracking_quietly = stop
    e._maybe_recheck_pointing = recheck
    e.start(light_plan())
    assert await wait_for(lambda: e.state.get("state") == "aborted",
                          timeout=30), e.state
    assert await wait_for(lambda: any(isinstance(o, tuple) for o in order),
                          timeout=10), order
    assert e.state.get("end_reason") == "unsafe"
    winds = [o for o in order if isinstance(o, tuple)]
    assert winds == [("wind_down", False, False)], winds
    assert order.index("stop_tracking") < order.index(winds[0]), order


@pytest.mark.parametrize("position_unknown", [True, False],
                         ids=["position unknown", "control: another unsafe"])
async def test_a_position_unknown_run_end_is_not_resumed_by_itself(
        position_unknown, sim_hub, temp_store, bus_lines):
    """The REAL run's terminal arm and `_finalize_report`: a run that ended
    because the mount's position is unknown leaves its session DISARMED, so
    ResumeArm does not restart it minutes later and aim its ladder (or the
    next setup's goto) from the model the run had just declared unknown.
    One info line says how to pick it up, with no goto in it. CONTROL: any
    other unsafe stop keeps the session armed for a same-night restart, the
    continuity promise.

    MUTANT "the disarm removed" (the ``if reason == "unsafe" and getattr(
    self, "_ended_position_unknown", False):`` block in `_finalize_report`
    made ``if False:``): RED, "position unknown" -
        AssertionError: a position-unknown stop left the session armed
    MUTANT "every unsafe stop disarms" (``self._ended_position_unknown =
    isinstance(e, PositionUnknownStop)`` made ``= True``): RED, "control" -
        AssertionError: another unsafe stop disarmed the session
    """
    temp_store.set_safety(SafetyConfig(enabled=False))
    e = SequenceEngine(sim_hub)

    async def recheck(target=None):
        if position_unknown:
            await e._stop_run_position_unknown(target, WHERE)
        raise SafetyAbort("a fictional unsafe stop")

    e._maybe_recheck_pointing = recheck
    e.start(light_plan())
    sid = e._session.id
    assert await wait_for(lambda: e._task is not None and e._task.done(),
                          timeout=30), e.state
    assert e.state.get("end_reason") == "unsafe", e.state
    stored = session_store.load(sid)
    said = [m for _l, m, _s in bus_lines
            if m.startswith("auto-resume disarmed, position unknown")]
    if position_unknown:
        assert stored.auto_resume is False, (
            "a position-unknown stop left the session armed")
        assert len(said) == 1, said
        assert said[0].startswith(
            "auto-resume disarmed, position unknown: once Trust position or "
            "a sync away from the pole clears it, arm it from the session "
            "list ("), said
        for word in ("goto", "slew", "go to"):
            assert word not in said[0].lower(), said[0]
    else:
        assert stored.auto_resume is True, (
            "another unsafe stop disarmed the session")
        assert said == [], said


# ======================================== the in-place miss's re-centre (B, I)

async def test_an_in_place_miss_whose_re_centre_will_not_solve_stops(
        bus_lines):
    """The in-place solve found the field 30' off; the hold's re-centre then
    fails to solve twice. The in-place solve SAW STARS, so the unsolved
    field is the pointing's fault: the target stops, and guiding is not
    restarted on it.

    MUTANT "the in-place solve saw nothing" (``self._pre_recovery_saw_stars
    = True`` in `_hold_recentre_recalibrate`'s in-place arm made ``=
    False``): RED -
        AssertionError: assert 're-centring ...ing for light' ==
        're-centring ...ng is unknown'
    (it holds for light and stops with the no-light reason instead)
    """
    t = _target(name="Fictional F")
    e, hub, t = _engine(target=t, solve=_off(t, 30.0))
    sf = {"centered": False, "error_arcmin": None, "attempts": 1,
          "solve_failed": True}
    _scripted(hub, [sf, sf])
    with pytest.raises(StopTarget) as ei:
        await e._maybe_recheck_pointing(t)
    assert str(ei.value) == ("re-centring after the pointing re-check: "
                             f"{CENTRING_UNSOLVED_TWICE}")
    assert len(hub.gotos) == 2, hub.gotos
    assert "start" not in hub.guider.calls, hub.guider.calls


async def test_a_good_in_place_check_clears_the_unverified_mark(bus_lines):
    """A target marked unverified (an earlier re-centre found nothing) is
    re-checked in place and solves 2' off, inside the ceiling: that is good
    evidence, so its lights count again (ruling R4).

    MUTANT "the in-place success says nothing" (the `_note_centring_evidence`
    call after a solve inside the ceiling in `_maybe_recheck_pointing`
    removed): RED -
        AssertionError: the in-place solve left the target unverified
    """
    t = _target(name="Fictional F")
    e, hub, t = _engine(target=t, solve=_off(t, 2.0))
    e._pointing_unverified_for = t.id
    await e._maybe_recheck_pointing(t)
    assert len(hub.solves) == 1, "premise: the check solved"
    assert e._pointing_unverified_for is None, (
        "the in-place solve left the target unverified")


async def test_one_disagreement_is_acted_on_once(bus_lines):
    """Two frame boundaries, one disagreement: one solve. The field does not
    solve, so no good evidence re-bases the count and only consuming the
    disagreement stops a second check.

    MUTANT "never consumed" (``self._disagreements_seen = gen`` deleted):
    RED -
        AssertionError: assert 2 == 1
    (First written with a check that SOLVED, whose good evidence re-based
    the count and hid the mutant: it survived. Rewritten on that run.)
    """
    t = _target(name="Fictional F")
    e, hub, t = _engine(target=t, solve=DeviceError("no solution"))
    await e._maybe_recheck_pointing(t)
    await e._maybe_recheck_pointing(t)
    assert len(hub.solves) == 1


async def test_a_new_acquisition_rebases_the_disagreements(bus_lines):
    """Disagreements counted before a target's setup belong to the last
    pointing. Setup re-bases at its top, so even a setup whose centring
    found nothing to solve (no good evidence to re-base on) does not hand
    the next frame a re-check.

    MUTANT "no re-base at setup" (``self._disagreements_seen =
    self._hub_disagreements()`` at the top of `_setup_target` deleted): RED -
        AssertionError: assert 1 == 0
    """
    t = _target(name="Fictional F")
    e, hub, t = _engine(target=t, gen=5, solve=_off(t, 2.0))
    _scripted(hub, [{"centered": False, "error_arcmin": None}])
    await e._setup_target(0, t)
    assert e._pointing_unverified_for == t.id, "premise: no good evidence"
    await e._maybe_recheck_pointing(t)
    assert len(hub.solves) == 0


async def test_good_centring_evidence_rebases_the_disagreements(bus_lines):
    """A disagreement counted from frame N's header just before the flip's
    own centring is retired by that centring.

    MUTANT "evidence does not re-base" (the re-base line in
    `_note_centring_evidence` deleted): RED -
        AssertionError: assert 1 == 0
    """
    t = _target(name="Fictional F")
    e, hub, t = _engine(target=t, gen=2, solve=_off(t, 2.0))
    e._disagreements_seen = 1
    e._note_centring_evidence({"centered": True, "error_arcmin": 0.3}, t)
    await e._maybe_recheck_pointing(t)
    assert len(hub.solves) == 0


class _Clock:
    """``engine_mod.time`` with a monotonic clock the solve stub advances;
    everything else is the real module (asyncio keeps the real clock)."""

    def __init__(self) -> None:
        self.mono = 1000.0

    def monotonic(self) -> float:
        return self.mono

    def __getattr__(self, name):
        return getattr(time, name)


async def test_in_place_checks_are_bounded_by_time(monkeypatch, bus_lines):
    """Each check took 300 s by the clock: 0, 300 and 600 s spent before the
    first three (all under 744 s), so three solves; the fourth finds the
    budget spent and says so once, and a fifth says nothing more.

    MUTANT "no budget" (``if spent >= POINTING_INPLACE_BUDGET_S:`` made
    ``if False:``): RED -
        AssertionError: assert 5 == 3
    """
    clock = _Clock()
    monkeypatch.setattr(engine_mod, "time", clock)
    t = _target(name="Fictional F")

    def solve():
        clock.mono += 300.0
        return _off(t, 2.0)
    e, hub, t = _engine(target=t, solve=solve)
    for gen in range(1, 6):
        hub.pointing_disagreements = gen
        await e._maybe_recheck_pointing(t)
    assert len(hub.solves) == 3
    spent = [m for _l, m, _s in bus_lines if "re-check time is spent" in m]
    assert spent == ["Fictional F: the mount's reported position moved 1.90° "
                     "from the last solved field; not re-checked, tonight's "
                     "re-check time is spent"], spent
    for _l, m, _s in bus_lines:
        assert not _humanizer_rewrites(m), m


async def test_a_centring_off_target_is_not_rechecked(bus_lines):
    """A target that opted out of centring is not checked; the disagreement
    is consumed all the same.

    MUTANT "everyone re-checked" (``not getattr(target, "center", False)``
    dropped from the gate): RED -
        AssertionError: assert 1 == 0
    """
    t = _target(name="Fictional F", center=False)
    e, hub, t = _engine(target=t, solve=_off(t, 2.0))
    await e._maybe_recheck_pointing(t)
    assert len(hub.solves) == 0
    assert e._disagreements_seen == 1


async def test_the_frame_loop_asks_for_the_recheck(sim_hub, temp_store):
    """The REAL run: the frame loop asks for the re-check before the first
    exposure, and at every frame boundary after it.

    MUTANT "unwired" (the ``await self._maybe_recheck_pointing(target)`` line
    in `_run_step` deleted): RED -
        AssertionError: the frame loop never asked: []
    """
    e = SequenceEngine(sim_hub)
    asked: list[int] = []

    async def recheck(target=None):
        asked.append(e._frames_done)
    e._maybe_recheck_pointing = recheck
    e.start(light_plan())
    assert await wait_for(lambda: e.state.get("state") == "complete",
                          timeout=40), e.state
    assert asked, f"the frame loop never asked: {asked}"
    assert asked[0] == 0, f"first asked after {asked[0]} frames"
    assert len(asked) >= 6, asked


async def test_the_engines_uncentred_slew_retires_the_field_solve(real_hub,
                                                                  monkeypatch):
    """A centring-off target's slew is the engine's own: it retires the field
    solve explicitly, so it never reads to the detector as an uncommanded
    move of the report.

    MUTANT "the slew forgets" (the ``invalidate_field_solve`` call beside
    `note_pointing_moved` in `_setup_target` deleted): RED -
        AssertionError: the engine's own slew left the old field solve
        standing
    """
    h = real_hub
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    tel = h.devices["telescope"]
    await tel.slew(5.0, 30.0)
    ra, dec = await tel.get_position()
    h._note_pointing(ra, dec)
    await _adopt(h)
    t = _target(name="Fictional G", ra_hours=6.0, dec_deg=40.0, center=False)
    e = SequenceEngine(h)
    e._cfg = None
    e.plan = engine_mod.SequencePlan(name="x", guide=False,
                                     meridian_flip=False, safety_check=False,
                                     targets=[t])
    await e._setup_target(0, t)
    assert h.field_solve is None, (
        "the engine's own slew left the old field solve standing")
    await h.capture(0.2, 100, 30, 1, save=True, target="x")
    assert h.pointing_disagreements == 0
