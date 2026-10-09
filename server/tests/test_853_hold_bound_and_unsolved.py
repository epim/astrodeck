# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A failed solve in a hold is not a raw GoTo that worked, and the holds have
an exit (#853).

On 2026-10-07 the guiding-loss hold's solve failed on a trailed field, the hub
fell back to a raw GoTo the desynced mount did not move for, and the engine
walked a fresh calibration on a field nobody had located. The run took five
holds in 46 minutes, each ending in a fresh calibration, with no bound.

Graded here, on the REAL engine methods (`_hold_recentre_recalibrate`,
`_maybe_recover_guiding`, `_recentre_for_hold`, `_record_frame`, `start`):

- a failed solve is solved once more; failed twice after a sky reading that
  SAW STARS, the target stops; after a reading that saw none it holds for
  light (setup's bounded `_hold_for_light`) and stops only if that ends
  unsolved (ruling R2);
- walking-field holds are bounded per target per night (ruling R1), and so
  are re-centring guide-star recoveries (ruling R1b), whose #72 bound a
  guided frame clears;
- a cloudy reading charges neither;
- an operator's start resets the budgets, a ResumeArm start keeps them
  (ruling R5).

The hub is the recording double of test_centring_settings_reach_goto.py, its
``goto_and_center`` answering a script. The sky reading is set per case by
replacing `_sky_closed_before_recovery` with a function that answers as a
real reading would, flags included. Every mutant named below was applied to a
byte copy of ``sequence/engine.py``, run under the suite's normal command,
and the file restored from the copy with its sha256 checked.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import astrodeck.flows.tonight as tonight_mod
import astrodeck.sequence.engine as engine_mod
from astrodeck.hub import SOLVE_REASON_FILE_LOCKED
from astrodeck.sequence.engine import (
    CENTRING_NO_LIGHT, CENTRING_UNSOLVED_TWICE, FIELD_HOLDS_SPENT,
    MAX_FIELD_HOLDS_PER_TARGET_NIGHT, MAX_RECOVERY_RECENTRES_PER_TARGET_NIGHT,
    RECOVERY_RECENTRES_SPENT, PositionUnknownStop, SlewRefused, StopTarget)

from test_centring_settings_reach_goto import _setup, _target
from test_850_engine_sync_refused import _humanizer_rewrites, _scripted
from test_engine_safety import (light_plan, sim_hub, temp_store,  # noqa: F401
                                wait_for)

SF = {"centered": False, "error_arcmin": None, "attempts": 1,
      "solve_failed": True}
OK = {"centered": True, "error_arcmin": 0.4, "attempts": 1}
WALKED = "re-centring after the guided field walked"
MISSING = "re-centring after the guide star went missing"


@pytest.fixture(autouse=True)
def _the_window_is_open(monkeypatch):
    """The light hold bounds itself by the target's own window, computed for
    the hour the suite runs; "unknown" here, so its retries depend on its
    own count and not on the clock."""
    monkeypatch.setattr(tonight_mod, "target_own_window",
                        lambda *a, **kw: None)


class _Guider:
    """A connected guider that records stop, clear and start, and answers
    ``is_active`` from ``active``."""
    connected = True

    def __init__(self, active: bool = False) -> None:
        self.calls: list[str] = []
        self.active = active
        self.fail_start = False

    async def is_active(self) -> bool:
        return self.active

    async def stop_guiding(self) -> None:
        self.calls.append("stop")

    def clear_calibration(self) -> None:
        self.calls.append("clear")

    async def start_guiding(self) -> None:
        self.calls.append("start")
        if self.fail_start:
            raise RuntimeError("the calibration found no star")

    def stats(self):
        return None


def _engine(target=None, *, active: bool = False):
    t = target or _target(name="Fictional C")
    e, hub = _setup(t)
    e.plan.guide = True
    e._policy = SimpleNamespace(recover_guiding=True)
    hub.guider = _Guider(active=active)
    return e, hub, t


def _reading(e, *, saw_stars: bool, blind: bool = False,
             cloudy: list | None = None) -> list:
    """Replace the sky reading. ``cloudy`` lists the answers in order (True:
    the sky closed, held for); past its end the answer is False. Records each
    call's ``after_failure``."""
    calls: list = []

    async def reading(target, *, why, after_failure=False):
        calls.append(after_failure)
        e._pre_recovery_blind = blind
        e._pre_recovery_saw_stars = saw_stars
        if cloudy and len(calls) <= len(cloudy):
            e._pre_recovery_saw_stars = False
            return cloudy[len(calls) - 1]
        return False
    e._sky_closed_before_recovery = reading
    return calls


async def _walking(e, t):
    await e._hold_recentre_recalibrate("2 consecutive dither settles failed", t)


async def _recovery(e, t):
    e.hub.guider.active = False
    await e._maybe_recover_guiding(t)


# ===================================================== the second solve (#853)

async def test_unsolved_twice_after_a_reading_that_saw_stars_stops_without_recalibrating(
        bus_lines):
    """The 2026-10-07 shape, with the sky measurably open: the re-centre's
    solve fails twice. The target stops; nothing walks a calibration on a
    field nobody located.

    MUTANT "raw GoTo is fine" (``if not self._centring_unsolved(res):`` in
    `_recentre_for_hold`'s loop made ``if True:``): RED -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=True)
    _scripted(hub, [SF, SF])
    with pytest.raises(StopTarget) as ei:
        await _walking(e, t)
    assert str(ei.value) == f"{WALKED}: {CENTRING_UNSOLVED_TWICE}"
    assert hub.guider.calls == ["stop", "clear"], hub.guider.calls
    assert len(hub.gotos) == 2, hub.gotos
    assert e._pointing_unverified_for == t.id


async def test_a_second_solve_that_works_resumes_guiding(bus_lines):
    """The first solve failed, the second solved on target: the hold goes on
    and guiding restarts.

    MUTANT "no second solve" (``for attempt in (1, 2):`` made ``for attempt
    in (1,):``): RED -
        astrodeck.sequence.engine.StopTarget: re-centring after the guided
        field walked: the field did not solve twice, so the pointing is
        unknown
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=True)
    _scripted(hub, [SF, OK])
    await _walking(e, t)
    assert hub.guider.calls == ["stop", "clear", "start"], hub.guider.calls
    assert len(hub.gotos) == 2
    lines = [m for _l, m, _s in bus_lines if "solving once more" in m]
    assert lines == ["Fictional C: re-centring after the guided field walked: "
                     "the field did not solve; solving once more in 5 s"], lines


async def test_the_named_solve_reason_becomes_the_stop(bus_lines):
    """A failure the hub can name (#618) carries its fixed sentence into the
    stop, so a panel deferred on it twice reads as one reason (D-03).

    MUTANT "the reason dropped" (``res.get('solve_reason') or`` removed from
    the unsolved-twice stop): RED -
        AssertionError: assert '...so the pointing is unknown' == '...'
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=True)
    named = dict(SF, solve_reason=SOLVE_REASON_FILE_LOCKED)
    _scripted(hub, [named, named])
    with pytest.raises(StopTarget) as ei:
        await _walking(e, t)
    assert str(ei.value) == f"{WALKED}: {SOLVE_REASON_FILE_LOCKED}"


@pytest.mark.parametrize("path, where", [(_walking, WALKED),
                                         (_recovery, MISSING)],
                         ids=["walking hold", "guide-star recovery"])
@pytest.mark.parametrize("blind", [True, False],
                         ids=["reading blind", "reading not taken"])
async def test_unsolved_twice_without_stars_holds_for_light(path, where, blind,
                                                            bus_lines):
    """No reading saw stars (blind, or none taken): two failed solves are not
    blamed on the pointing. The re-centre holds for light, setup's bounded
    hold, and the retry that solves ends it; guiding restarts.

    MUTANT "the light hold arm removed" (``if getattr(self,
    "_pre_recovery_saw_stars", False):`` made ``if True:``): RED, all four -
        astrodeck.sequence.engine.StopTarget: re-centring after ...: the field
        did not solve twice, so the pointing is unknown
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=False, blind=blind)
    _scripted(hub, [SF, SF, SF, dict(OK, error_arcmin=0.5)])
    await path(e, t)
    held = [m for _l, m, _s in bus_lines if "holding for light" in m]
    assert len(held) == 1, bus_lines
    assert held[0].startswith("Fictional C: holding for light, retrying every "
                              "10 min;"), held
    assert len(hub.gotos) == 4, hub.gotos
    assert hub.guider.calls[-1] == "start", hub.guider.calls


async def test_a_light_hold_that_never_solves_stops(bus_lines):
    """The light hold runs out (six retries) with the field never solved:
    the target stops, and guiding is not restarted on it.

    MUTANT "after the light hold, return whatever came back" (the unsolved
    check after `_hold_for_light` made ``if False:``): RED -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=False, blind=True)
    _scripted(hub, [SF])
    with pytest.raises(StopTarget) as ei:
        await _walking(e, t)
    assert str(ei.value) == f"{WALKED}: {CENTRING_NO_LIGHT}"
    assert len(hub.gotos) == 2 + engine_mod.SequenceEngine._NO_LIGHT_MAX_RETRIES
    assert "start" not in hub.guider.calls, hub.guider.calls


async def test_the_light_hold_does_not_say_autofocus(bus_lines):
    """Mid-run, the light hold's own opening line (setup's, about sparing
    autofocus and calibration) is wrong; the re-centre said its own.

    MUTANT "announce dropped" (``announce=False`` removed from
    `_recentre_for_hold`'s `_hold_for_light` call): RED -
        AssertionError: ['Fictional C: centring found nothing to solve —
        holding for light before autofocus ...']
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=False, blind=True)
    _scripted(hub, [SF])
    with pytest.raises(StopTarget):
        await _walking(e, t)
    said = [m for _l, m, _s in bus_lines if "autofocus" in m]
    assert said == [], said
    for _l, m, _s in bus_lines:
        assert not _humanizer_rewrites(m), m


async def test_a_guide_star_recovery_gets_the_same_second_solve(bus_lines):
    """The guide-star recovery re-centres through the same code: two failed
    solves after a reading that saw stars stop the target, and the guider is
    not restarted.

    MUTANT "recovery left on a bare re-centre" (the recovery's ``await
    self._recentre_for_hold(...)`` replaced by ``await
    self.hub.goto_and_center(target.ra_hours, target.dec_deg,
    rotation_deg=self._commanded_rotation(target),
    **self._centring_kwargs(target))``): RED -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=True)
    _scripted(hub, [SF, SF])
    with pytest.raises(StopTarget) as ei:
        await _recovery(e, t)
    assert str(ei.value) == f"{MISSING}: {CENTRING_UNSOLVED_TWICE}"
    assert "start" not in hub.guider.calls, hub.guider.calls


# ============================ the position before every goto (fix round 1)

def _flips_unknown_after(hub, answers: list, n: int) -> None:
    """``goto_and_center`` answers ``answers`` in order, and from the
    ``n``-th call on the driver says "position unknown" (an AM5 link reopen
    mid-hold)."""
    async def goto(*args, **kwargs):
        hub.gotos.append((args, dict(kwargs)))
        if len(hub.gotos) >= n:
            hub.tel.position_known = False
        return dict(answers[min(len(hub.gotos) - 1, len(answers) - 1)])
    hub.goto_and_center = goto


async def test_position_turning_unknown_between_attempts_stops_the_run(
        bus_lines):
    """The first re-centre's field did not solve, and the driver latched
    "position unknown" before the second: the second goto is never made,
    the run ends without moving the mount, and guiding is not restarted.

    MUTANT "gate once, before the loop" (the `_position_unknown` check moved
    from the top of each attempt in `_recentre_for_hold` back to before
    ``for attempt in (1, 2):``): RED (the second goto is made and fails) -
        astrodeck.sequence.engine.StopTarget: re-centring after the guided
        field walked: the field did not solve twice, so the pointing is
        unknown
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=True)
    _flips_unknown_after(hub, [SF, SF], 1)
    with pytest.raises(PositionUnknownStop):
        await _walking(e, t)
    assert len(hub.gotos) == 1, f"a goto from an unknown position: {hub.gotos}"
    assert "start" not in hub.guider.calls, hub.guider.calls


async def test_position_turning_unknown_in_the_light_hold_stops_the_run(
        bus_lines):
    """No reading saw stars, so the re-centre holds for light; on its first
    retry the driver latches "position unknown". The next retry's goto is
    never made, and the run ends without moving the mount.

    MUTANT "the light hold ungated" (the ``position_gate`` check before
    `_hold_for_light`'s `_centre_once` deleted), and MUTANT "the gate not
    passed" (``position_gate=where`` in `_recentre_for_hold` made
    ``position_gate=None``): RED, both (every retry is made) -
        astrodeck.sequence.engine.StopTarget: re-centring after the guided
        field walked: the field never solved while holding for light
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=False, blind=True)
    _flips_unknown_after(hub, [SF], 3)
    with pytest.raises(PositionUnknownStop):
        await _walking(e, t)
    assert len(hub.gotos) == 3, f"a goto from an unknown position: {hub.gotos}"
    assert "start" not in hub.guider.calls, hub.guider.calls


async def test_control_setup_light_hold_is_not_gated(bus_lines):
    """CONTROL. Setup's own no-light hold passes no ``position_gate``, so it
    behaves as before this change whatever the flag says (setup's slews are
    not this package's to gate)."""
    t = _target(name="Fictional C")
    e, hub = _setup(t)
    hub.tel.position_known = False
    _scripted(hub, [SF, SF, dict(OK)])
    res = await e._hold_for_light(t, None, dict(SF))
    assert res["centered"] is True
    assert len(hub.gotos) == 3


# ============================================ a re-centre that raises (round 1)

async def test_a_second_attempt_that_raises_after_an_unsolved_first_stops(
        bus_lines):
    """The first re-centre's field did not solve and the second goto raised:
    the field has still never been located. With a reading that saw stars
    that is "did not solve twice", the target stops, and guiding is NOT
    restarted on a field nobody found (the #853 shape, through an
    exception).

    MUTANT "every raise carries on" (``if attempt > 1:`` in
    `_recentre_for_hold`'s ``except Exception`` arm made ``if False:``):
    RED -
        Failed: DID NOT RAISE <class '...StopTarget'>
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=True)

    async def goto(*args, **kwargs):
        hub.gotos.append((args, dict(kwargs)))
        if len(hub.gotos) == 1:
            return dict(SF)
        raise RuntimeError("the link dropped")
    hub.goto_and_center = goto
    with pytest.raises(StopTarget) as ei:
        await _walking(e, t)
    assert str(ei.value) == f"{WALKED}: {CENTRING_UNSOLVED_TWICE}"
    assert "start" not in hub.guider.calls, hub.guider.calls
    assert e._pointing_unverified_for == t.id


async def test_a_first_attempt_that_raises_carries_on_unverified(bus_lines):
    """The first re-centre goto raised: the mount is where it already was,
    the hold goes on as it always did (guiding restarted), and the target's
    lights from here are marked unverified (ruling R4).

    MUTANT "the failed goto marks nothing" (``self._mark_pointing_
    unverified(target)`` removed from `_recentre_for_hold`'s failed-goto
    arm): RED -
        AssertionError: assert None == '<the target id>'
    (observed with the id in full)
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=True)

    async def goto(*args, **kwargs):
        hub.gotos.append((args, dict(kwargs)))
        raise RuntimeError("the link dropped")
    hub.goto_and_center = goto
    await _walking(e, t)
    assert len(hub.gotos) == 1
    assert hub.guider.calls == ["stop", "clear", "start"], hub.guider.calls
    assert e._pointing_unverified_for == t.id


@pytest.mark.parametrize("path", [_walking, _recovery],
                         ids=["walking hold", "guide-star recovery"])
async def test_a_refused_slew_in_a_hold_re_centre_passes_through(path,
                                                                 bus_lines):
    """The rig's own limits refuse the re-centre's slew (the horizon, the
    sun): a SafetyAbort, not "a failed re-centre". It propagates, and the
    guider is not restarted on a field the mount never reached.

    MUTANT "a refusal is a failed re-centre" (the ``except SafetyAbort:
    raise`` arm in `_recentre_for_hold` removed): RED, both -
        Failed: DID NOT RAISE <class '...SlewRefused'>
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=True)

    async def goto(*args, **kwargs):
        hub.gotos.append((args, dict(kwargs)))
        raise SlewRefused("below the floor", words="below the floor",
                          kind="floor")
    hub.goto_and_center = goto
    with pytest.raises(SlewRefused):
        await path(e, t)
    assert "start" not in hub.guider.calls, hub.guider.calls


async def test_a_light_hold_retry_that_raises_stops_for_no_light(bus_lines):
    """The light hold's retry raised a plain error: the field has still
    never solved, so it is the same stop as a hold that ran out, and
    guiding is not restarted.

    MUTANT "a raised hold carries on" (the synthesized result after a raised
    `_hold_for_light` made ``{"centered": True, "error_arcmin": None}``):
    RED -
        Failed: DID NOT RAISE <class '...StopTarget'>
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=False, blind=True)

    async def goto(*args, **kwargs):
        hub.gotos.append((args, dict(kwargs)))
        if len(hub.gotos) <= 2:
            return dict(SF)
        raise RuntimeError("the link dropped")
    hub.goto_and_center = goto
    with pytest.raises(StopTarget) as ei:
        await _walking(e, t)
    assert str(ei.value) == f"{WALKED}: {CENTRING_NO_LIGHT}"
    assert "start" not in hub.guider.calls, hub.guider.calls


# ================================================= the walking-hold bound (R1)

async def test_the_third_walking_hold_tonight_stops_the_target(bus_lines):
    """Two holds go through; the third tonight stops the target before any
    goto, and stands the guider down.

    MUTANT "off by one" (``if n >= MAX_FIELD_HOLDS_PER_TARGET_NIGHT:`` in
    `_charge_field_hold` made ``>``): RED -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    assert MAX_FIELD_HOLDS_PER_TARGET_NIGHT == 2
    e, hub, t = _engine()
    _reading(e, saw_stars=True)
    _scripted(hub, [OK])
    await _walking(e, t)
    await _walking(e, t)
    assert len(hub.gotos) == 2, "premise: two holds went through"
    hub.guider.calls.clear()
    with pytest.raises(StopTarget) as ei:
        await _walking(e, t)
    assert str(ei.value) == FIELD_HOLDS_SPENT
    assert len(hub.gotos) == 2, "the third hold slewed"
    assert hub.guider.calls == ["stop"], hub.guider.calls
    said = [m for _l, m, _s in bus_lines if "it has held 2 times tonight" in m]
    assert said == ["Fictional C: stopping this target; it has held 2 times "
                    "tonight to re-centre, and now 2 consecutive dither "
                    "settles failed"], said


async def test_the_bound_is_per_target(bus_lines):
    """Two holds on one target leave another target's budget whole.

    MUTANT "one budget for all" (`_hold_key` made to return ``"all"``): RED -
        astrodeck.sequence.engine.StopTarget: the field kept moving off
        target and tonight's holds for it are spent
    """
    e, hub, a = _engine()
    b = _target(name="Fictional D")
    _reading(e, saw_stars=True)
    _scripted(hub, [OK])
    await _walking(e, a)
    await _walking(e, a)
    await _walking(e, b)
    assert len(hub.gotos) == 3


async def test_the_bound_resets_the_next_night(monkeypatch, bus_lines):
    """A new observing night starts the count clean.

    MUTANT "no night in the key" (`_budget_key` made to return ``("",
    self._hold_key(target))``): RED -
        astrodeck.sequence.engine.StopTarget: the field kept moving off
        target and tonight's holds for it are spent
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=True)
    _scripted(hub, [OK])
    monkeypatch.setattr(engine_mod, "night_key", lambda ts=None: "2026-10-07")
    await _walking(e, t)
    await _walking(e, t)
    monkeypatch.setattr(engine_mod, "night_key", lambda ts=None: "2026-10-08")
    await _walking(e, t)
    assert len(hub.gotos) == 3


async def test_a_cloudy_reading_charges_no_hold(bus_lines):
    """Three readings find cloud (the cloud hold's business, #621), then two
    find a clear sky: both later holds are still allowed.

    MUTANT "charge first" (the ``await self._charge_field_hold(why, target)``
    moved above the walking detectors' sky reading): RED -
        astrodeck.sequence.engine.StopTarget: the field kept moving off
        target and tonight's holds for it are spent
    """
    e, hub, t = _engine()
    calls = _reading(e, saw_stars=True, cloudy=[True, True, True])
    _scripted(hub, [OK])
    for _ in range(5):
        await _walking(e, t)
    assert len(calls) == 5
    assert len(hub.gotos) == 2


# ============================================= the recovery bound (R1b)

def _frame(e, t) -> None:
    """Bank one frame the way `_run_step` does once guiding held: the real
    `_record_frame`, with ``guided`` as `_frame_was_guided` answers it."""
    e._record_frame("k", e._frames_done, t, t.steps[0], {}, guided=True)


async def test_recovery_recentres_are_bounded_per_night(bus_lines):
    """The #72 bound counts attempts without a frame, and a guided frame
    clears it, so a field that walks between losses re-centred once per loss
    all night. Four re-centring recoveries tonight, a guided frame after
    each (the real reset), and the fifth stops the target before any goto.

    MUTANT "no per-night bound" (``if n >= MAX_RECOVERY_RECENTRES_PER_TARGET_
    NIGHT:`` in `_maybe_recover_guiding` made ``if False:``): RED -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    assert MAX_RECOVERY_RECENTRES_PER_TARGET_NIGHT == 4
    e, hub, t = _engine()
    _reading(e, saw_stars=True)
    _scripted(hub, [OK])
    peak = 0
    for _ in range(4):
        await _recovery(e, t)
        peak = max(peak, e._guiding_recoveries)
        hub.guider.active = True
        assert await e._frame_was_guided(), "premise: the frame was guided"
        _frame(e, t)
        assert e._guiding_recoveries == 0, "premise: the real reset ran"
    assert len(hub.gotos) == 4
    with pytest.raises(StopTarget) as ei:
        await _recovery(e, t)
    assert str(ei.value) == RECOVERY_RECENTRES_SPENT
    assert len(hub.gotos) == 4, "the fifth recovery slewed"
    assert peak == 1, f"the #72 bound, not the nightly one, was reached: {peak}"


async def test_a_cloudy_after_failure_gives_the_recentre_back(bus_lines):
    """The guider restart failed, and the sky asked again has closed: the
    #72 attempt is given back, and so is tonight's re-centre.

    MUTANT "nothing given back" (the ``self._recovery_recentres[charged_key]
    = max(...)`` give-back removed): RED -
        AssertionError: assert 1 == 0
    """
    e, hub, t = _engine()
    _reading(e, saw_stars=True, cloudy=[False, True])
    hub.guider.fail_start = True
    _scripted(hub, [OK])
    await _recovery(e, t)
    assert hub.guider.calls == ["start"], "premise: the restart was tried"
    assert e._recovery_recentres.get(e._budget_key(t), 0) == 0


async def test_a_centring_off_recovery_is_not_charged(bus_lines):
    """A target with centring off is never re-centred, so its recoveries are
    not charged: five losses, a frame after each, no stop.

    MUTANT "every recovery charged" (``recentres`` computed without
    ``getattr(target, "center", False)``): RED -
        astrodeck.sequence.engine.StopTarget: the guide star kept going
        missing; tonight's re-centres for it are spent
    """
    e, hub, t = _engine(_target(name="Fictional E", center=False))
    _reading(e, saw_stars=True)
    _scripted(hub, [OK])
    for _ in range(5):
        await _recovery(e, t)
        hub.guider.active = True
        _frame(e, t)
    assert e._recovery_recentres == {}


# =============================================== who resets the budgets (R5)

async def test_an_operator_start_resets_the_budgets_and_a_resume_does_not(
        sim_hub):
    """An operator's start gets fresh budgets; ResumeArm's (``operator=
    False``) keeps tonight's.

    MUTANT "always reset" (``if operator:`` in `start` made ``if True:``):
    RED, the resume half -
        AssertionError: a ResumeArm start reset tonight's budgets: {}
    MUTANT "never reset" (that block removed): RED, the operator half -
        AssertionError: an operator's start kept the old budgets: {('n', 'x'): 2}
    """
    e = engine_mod.SequenceEngine(sim_hub)

    def fill():
        e._field_holds[("n", "x")] = 2
        e._recovery_recentres[("n", "x")] = 4
        e._inplace_spent_s[("n", "x")] = 700.0

    fill()
    e.start(light_plan())
    try:
        assert e._field_holds == {} and e._recovery_recentres == {} \
            and e._inplace_spent_s == {}, (
            f"an operator's start kept the old budgets: {e._field_holds}")
    finally:
        await e.abort()
    fill()
    e.start(light_plan(), operator=False)
    try:
        assert e._field_holds == {("n", "x"): 2}, (
            f"a ResumeArm start reset tonight's budgets: {e._field_holds}")
        assert e._recovery_recentres == {("n", "x"): 4}
        assert e._inplace_spent_s == {("n", "x"): 700.0}
    finally:
        await e.abort()


# ==================================================================== control

async def test_control_existing_hold_order_is_kept(bus_lines):
    """CONTROL. The order the hold has always kept: stop, clear, centre,
    start. A double whose ``goto_and_center`` answers None (the existing
    relock tests' stub) is "nothing to judge" and must not raise."""
    e, hub, t = _engine()
    _reading(e, saw_stars=True)

    async def goto(*args, **kwargs):
        hub.guider.calls.append("center")
        return None
    hub.goto_and_center = goto
    await _walking(e, t)
    assert hub.guider.calls == ["stop", "clear", "center", "start"]
