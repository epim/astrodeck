# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#873: the engine does not shoot a frame its own guide-RMS gate refuses.

2026-10-16 and 2026-10-17 (0.3.42, NGC 7331): after a meridian flip the east
side's guider calibrations ran away (#1071, intermittent). The #72 recovery
made its two attempts, then logged "standing down from recovery and continuing
unguided" and shot on. `standards.max_guide_rms` defaults to 5.0 (#854), so
the #142 / D-06 branch of `_check_quality` rejected every one of those frames
("shot with the guider stopped"): twenty of twenty on the 17th, then the
night-reject guard ended the run with "(clouds?)", about 80 minutes of sky
lost. On the 16th guiding came back on the sixth calibration, two hours after
the flip, so a paced retry can recover a night.

THE INVARIANT. Where the plan guides, a guider is connected, the frame is a
light and `max_guide_rms` is above 0, no exposure starts while the guider is
not active. One predicate (`SequenceEngine._guide_gate_refuses`) decides both
the D-06 reject and the frame loop's pre-shutter guard. The guard holds
(`_hold_for_guiding`): a short wait between the #72 attempts, then retry
spells of a long wait and fresh attempts, six per target per night, then the
target stops.

Every case but the three direct ones below runs the real scheduler, setup, frame
loop, recovery, grader and holds on the clocked simulator
(tests/_group_harness.py): only the guider's start and the sky are scripted.

NAMED MUTANTS (each applied to engine.py from a byte backup, run, and restored
from that backup, never with git checkout; the results are in the task report):
 * M1  the guard in `_run_step` deleted.
 * M2  the spell cap removed.
 * M3  the old stand-down sentence restored in `_maybe_recover_guiding`.
 * M4  `_guide_reject_armed` ignores `calibration`; M4b also the outer test in
       `_check_quality`.
 * M5a/M5b  the start-failure / restart lines back to "continuing unguided".
 * M6  `_hold_for_guiding` without the recover_guiding test.
 * M7  the per-reason count dropped (`_night_stop_words` always the old text).
 * M8  recovery attempts charge `_recovery_recentres` during spells (#853).
 * M9  `_guide_reject_armed` ignores a missing guider.
 * M10 `_guide_retry_wait` replaced by one bare sleep.
 * M11 `_guide_retry_wait` without its `_checkpoint`.
 * M12 a deferring mosaic panel held for a spell instead of deferred.
 * M13 an absent guider counted as stopped (`_frame_was_guided` and
       `_guide_reject_armed` both).
 * M14 the require_guiding abort/skip delegation removed from the guard.
 * M15 `_guide_reject_armed` ignores the ceiling.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import AppConfig, EscalationConfig
from astrodeck.devices.base import DeviceError
from astrodeck.guide.base import GuideStats, Guider
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import (
    GUIDE_RECOVERY_OFF_STOP, GUIDE_RETRIES_SPENT, GUIDE_RETRY_GAP_S,
    GUIDE_RETRY_WAIT_S, HOLD_WATCH_S, L_RESTART_FAIL_RETRY,
    L_START_FAIL_RETRY, L_START_FAIL_STOP, MAX_GUIDE_RETRIES_PER_TARGET_NIGHT,
    MAX_RECOVERY_RECENTRES_PER_TARGET_NIGHT, RECOVERY_RECENTRES_SPENT,
    StopTarget, _MAX_GUIDING_RECOVERIES)
from astrodeck.sequence.models import ExposureStep, Schedule, Target

from _group_harness import (  # noqa: F401
    GROUP_NAME, Night, grid_plan, group_hub, group_store, single)
from _humanizer_mirror import humanizer_rewrites, shown_whole

#: The words the old sentence used.
CONTINUING = "continuing unguided"
SPELL_WORDS = "is retried in 10 min"


def _plan(*, ceiling: float | None = 5.0, recover: bool = True, count: int = 3,
          targets=None, **over) -> SequencePlan:
    kw = dict(name="n873", guide=True, recover_guiding=recover,
              max_guide_rms=ceiling, dither_every=0, autofocus_every=0,
              meridian_flip=False, park_when_done=False,
              warm_cooler_when_done=False)
    kw.update(over)
    return SequencePlan(
        targets=targets if targets is not None
        else [single("Alpha", count=count)], **kw)


async def _night(hub, monkeypatch, plan, *, guide=None, stars=None,
                 on_capture=None, wall_s=60.0):
    night = Night(hub, monkeypatch, guide=guide, stars=stars)
    night.on_capture = on_capture
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    return night


def _never(who, n):
    return False


def _spell_lines(night):
    return [(t, m) for t, _lvl, m in night.lines if SPELL_WORDS in m]


# --------------------------------------------------------------------- 1, 8

async def test_a_guider_that_never_starts_shoots_nothing_and_is_retried_on_a_pace(
        group_hub, group_store, monkeypatch):
    """1. Ceiling 5.0, warn, a guider whose start always fails. No exposure
    opens at any point, the engine retries on the #72 attempts and then in
    spells whose starts are 10 minutes apart or a little more (the wait plus
    the next pass's two attempts and their 60 s gaps), and after six spells
    the target stops with the fixed reason. The old sentence is never said.

    Starts: the setup's, then two attempts on each of seven passes (the first
    and one after each of the six spells) = 15.

    MUTANT M1 (the guard in `_run_step` deleted): RED - shots are taken with
    the guider stopped, and rejected (`captures == []` fails).
    MUTANT M2 (the spell cap removed): RED - more than six spell lines, no
    GUIDE_RETRIES_SPENT.
    MUTANT M3 (the old stand-down sentence restored): RED - "continuing
    unguided" is in the log.
    MUTANT M10 (the wait is one bare sleep): RED in the ping-cadence case
    below, and the spacing here stays within bounds (this case does not grade
    the cadence).
    """
    night = await _night(group_hub, monkeypatch, _plan(), guide=_never)
    assert night.done, night.trace[-3:]
    assert night.captures == [], (
        f"a frame was shot with the guider stopped: {night.shots()}")
    spells = _spell_lines(night)
    assert len(spells) == MAX_GUIDE_RETRIES_PER_TARGET_NIGHT, spells
    gaps = [b[0] - a[0] for a, b in zip(spells, spells[1:])]
    assert all(GUIDE_RETRY_WAIT_S <= g <= GUIDE_RETRY_WAIT_S
               + _MAX_GUIDING_RECOVERIES * (GUIDE_RETRY_GAP_S + 30.0)
               for g in gaps), f"retries were not paced: {gaps}"
    assert night.hub.guider.attempts.get("Alpha") == \
        1 + _MAX_GUIDING_RECOVERIES * (MAX_GUIDE_RETRIES_PER_TARGET_NIGHT + 1)
    assert night.said(GUIDE_RETRIES_SPENT) or any(
        GUIDE_RETRIES_SPENT in (r.get("reason") or "")
        for r in getattr(night.engine._session, "set_aside", []) or []), (
        [m for _t, _l, m in night.lines][-6:])
    assert not [m for _t, _l, m in night.lines if CONTINUING in m.lower()], (
        night.said(CONTINUING))
    assert not night.said("Rejected: shot with the guider stopped")
    # R4: the detail never claims a stand-down the engine did not make.
    assert not [s for s in night.states
                if "stood down" in str(s.get("detail"))], [
        s.get("detail") for s in night.states][-5:]


async def test_the_nightly_recentre_bound_does_not_end_a_retry_spell(
        group_hub, group_store, monkeypatch):
    """8 (R5). The #853 bound of MAX_RECOVERY_RECENTRES_PER_TARGET_NIGHT (4)
    re-centres would stop the target on the third pass if spells charged it.
    Attempts before the first spell are charged (two); once a spell is
    charged tonight the cap on spells bounds the attempts instead, so the
    target ends on GUIDE_RETRIES_SPENT with six spells charged and the
    re-centre count still at two.

    MUTANT M8 (attempts charge `_recovery_recentres` during spells): RED -
    the target stops with RECOVERY_RECENTRES_SPENT after the second spell.
    """
    assert MAX_RECOVERY_RECENTRES_PER_TARGET_NIGHT == 4, "premise"
    night = await _night(group_hub, monkeypatch, _plan(), guide=_never)
    assert night.done
    eng = night.engine
    assert list(eng._guide_retry_spells.values()) == [
        MAX_GUIDE_RETRIES_PER_TARGET_NIGHT]
    assert list(eng._recovery_recentres.values()) == [
        _MAX_GUIDING_RECOVERIES], eng._recovery_recentres
    assert not night.said(RECOVERY_RECENTRES_SPENT)
    assert len(_spell_lines(night)) == MAX_GUIDE_RETRIES_PER_TARGET_NIGHT


async def test_a_mosaic_panel_is_deferred_not_held_for_ten_minutes(
        group_hub, group_store, monkeypatch):
    """A panel of a mosaic group whose guider will not come back is deferred
    to the next pass, the group's own answer (#303), and not held for a retry
    spell: the guard's spell would idle the whole night over one panel.
    Each panel starts guiding at its hop, loses the star in every exposure,
    and fails both recovery attempts.

    MUTANT M12 (the member exception removed from `_hold_for_guiding`): RED -
    the panel waits a spell first.
    """
    lose = {f"{GROUP_NAME} 1-1", f"{GROUP_NAME} 1-2"}

    def drop(rec):
        if rec["target"] in lose:
            group_hub.guider.active = False

    plan = grid_plan(rows=1, cols=2,
                     panel_kw={"filters": ("L",), "count": 4, "per_visit": 4},
                     guide=True, recover_guiding=True, count_mode="accepted",
                     min_stars=5, max_consecutive_rejects=20,
                     max_consecutive_rejects_night=0)
    night = await _night(group_hub, monkeypatch, plan,
                         guide=lambda who, n: n == 1, on_capture=drop,
                         stars=lambda who, f: 50 if group_hub.guider.active
                         else 0)
    assert night.done
    deferred = [t for t, _l, m in night.lines
                if "did not recover on 1-1" in m]
    # (Once a whole pass failed the group goes on unguided and its panels
    # keep the escalation, so later passes may hold; the first deferral is
    # the one that must not wait.)
    assert deferred, [m for _t, _l, m in night.lines][:30]
    assert not [t for t, _m in _spell_lines(night) if t <= deferred[0]], (
        "a panel was held for a retry spell before its deferral")
    last_attempt = [t for t, _l, m in night.lines
                    if "attempting recovery (2/2)" in m]
    assert deferred and last_attempt, [m for _t, _l, m in night.lines][:40]
    assert deferred[0] - last_attempt[0] < GUIDE_RETRY_WAIT_S / 2, (
        deferred[0] - last_attempt[0])


# ------------------------------------------------------------------------ 2

async def test_a_guider_that_comes_back_on_a_retry_spell_resumes_the_night(
        group_hub, group_store, monkeypatch):
    """2. The setup's start and both #72 attempts fail; the first attempt of
    the first spell starts. Frames resume, guided, and every one is accepted;
    one spell was charged, not more.

    MUTANT M1 (the guard deleted): RED - frames are shot between the failed
    attempts, with the guider stopped, and rejected.
    """
    night = await _night(group_hub, monkeypatch, _plan(count=3),
                         guide=lambda who, n: n >= 4)
    assert night.done
    assert len(night.captures) == 3, night.shots()
    assert night.engine._rejected == 0
    assert not night.said("Rejected:")
    assert list(night.engine._guide_retry_spells.values()) == [1]
    assert len(_spell_lines(night)) == 1
    first_shot = night.captures[0]["t"]
    assert first_shot - night.t0 >= GUIDE_RETRY_WAIT_S, (
        "the first frame was shot before the spell's wait ended")


# ------------------------------------------------------------------------ 3

async def test_with_the_ceiling_off_the_old_behaviour_is_unchanged(
        group_hub, group_store, monkeypatch):
    """3. `max_guide_rms` 0: unguided frames are accepted, so "continuing
    unguided" is true and stays. The same always-failing guider: the start
    failure says the old thing, recovery makes its two attempts and stands
    down in the old words, and every frame is shot and accepted.

    MUTANT M15 (`_guide_reject_armed` ignores the ceiling): RED - the night
    holds and shoots nothing.
    """
    night = await _night(group_hub, monkeypatch, _plan(ceiling=0.0, count=3),
                         guide=_never)
    assert night.done
    assert night.engine._policy.max_guide_rms == 0, "premise"
    assert len(night.captures) == 3, night.shots()
    assert night.engine._rejected == 0
    assert night.said("guiding failed to start")
    assert any("guiding failed to start" in m and CONTINUING in m
               for _t, _l, m in night.lines), night.said("failed to start")
    assert night.said("standing down from recovery and continuing unguided")
    assert not _spell_lines(night)
    assert night.engine._guide_retry_spells == {}


async def test_attempts_that_start_but_do_not_hold_end_in_a_spell_not_a_stand_down(
        group_hub, group_store, monkeypatch):
    """3b (R4). The guider STARTS on every attempt and loses the star in every
    exposure, so no guided frame banks and the #72 attempts are spent at the
    top of a pass (`_maybe_recover_guiding`'s spent branch, the old "standing
    down ... continuing unguided"). With the ceiling above 0 that branch
    returns quietly and the guard holds the spell: the old sentence is never
    said and the detail never says recovery stood down.

    MUTANT M3 (the old stand-down sentence restored for an armed ceiling):
    RED - "continuing unguided" is in the log.
    """
    def drop(rec):
        group_hub.guider.active = False

    night = await _night(
        group_hub, monkeypatch,
        _plan(count=3, count_mode="accepted", max_consecutive_rejects=0,
              max_consecutive_rejects_night=0),
        guide=lambda who, n: True, on_capture=drop)
    assert night.done
    assert len(_spell_lines(night)) == MAX_GUIDE_RETRIES_PER_TARGET_NIGHT, (
        [m for _t, _l, m in night.lines][-8:])
    assert night.said(GUIDE_RETRIES_SPENT)
    assert not [m for _t, _l, m in night.lines if CONTINUING in m.lower()]
    assert not night.said("standing down from recovery")
    assert not [s for s in night.states
                if "stood down" in str(s.get("detail"))]


@pytest.mark.parametrize("action", ["abort", "skip"])
async def test_a_required_guider_gets_its_abort_or_skip_not_a_spell(
        group_hub, group_store, monkeypatch, action):
    """3c. `require_guiding` with abort or skip is the operator's answer to a
    guider that cannot be kept, and it comes before any retry spell: the
    guider starts, the star is lost in every exposure, both attempts fail,
    and the bound's answer is taken on that pass.

    MUTANT M14 (the escalation delegation removed from `_hold_for_guiding`):
    RED - the target is held through six spells instead.
    """
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action=action))

    def drop(rec):
        group_hub.guider.active = False

    night = await _night(
        group_hub, monkeypatch,
        _plan(count=3, count_mode="accepted", max_consecutive_rejects=0,
              max_consecutive_rejects_night=0),
        guide=lambda who, n: n == 1, on_capture=drop)
    gave_up = ("guiding could not be kept after 2 recovery attempts without "
               "a frame")
    assert night.done
    assert not _spell_lines(night), "held for a spell before the answer"
    if action == "abort":
        assert night.engine.state.get("end_reason") == "unsafe"
        assert night.engine.state.get("detail") == gave_up,             night.engine.state.get("detail")
    else:
        assert night.said(f"Alpha: skipped — {gave_up}") or             night.said(f"{gave_up} — skipping Alpha"),             [m for _t, _l, m in night.lines][-4:]


# ------------------------------------------------------------------------ 4

def _dark_target() -> Target:
    return Target(name="Darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
                  autofocus_first=False,
                  steps=[ExposureStep(frame_type="Dark", exposure_s=5.0,
                                      count=2)])


async def test_a_dark_is_shot_and_accepted_with_the_guider_stopped(
        group_hub, group_store, monkeypatch):
    """4. A calibration frame keeps today's behaviour: shot, not held, and
    not judged by the guide-RMS gate even when the last light frame left
    `_frame_guided` False (a cloud hold's darks follow an unguided light).
    The run is a calibration target in a plan that guides, with a guider that
    never starts.

    MUTANT M4b (`calibration` ignored by `_guide_reject_armed` AND by the
    outer test in `_check_quality`): RED - the stale False rejects both
    darks as "shot with the guider stopped".
    """
    def stale_unguided(rec):
        night.engine._frame_guided = False

    night = Night(group_hub, monkeypatch, guide=_never)
    night.on_capture = stale_unguided
    try:
        night.done = await night.run(_plan(targets=[_dark_target()]))
    finally:
        await night.close()
    assert night.done
    assert len(night.captures) == 2, night.shots()
    assert night.engine._rejected == 0
    assert not night.said("Rejected:")
    assert not _spell_lines(night)


# ------------------------------------------------------------------------ 5

def _operator_line_ok(line: str) -> None:
    low = line.lower()
    assert "set reject above (guide rms) to 0" in low[:137 + 20], line
    assert "guid" not in low or "lost" not in low, line
    assert not any(ch.isdigit() for ch in line.replace("(guide RMS) to 0", "")
                   ), f"a figure in fixed words: {line!r}"
    assert not humanizer_rewrites(line), line
    assert shown_whole(line) == line, line


async def test_the_start_failure_says_no_frames_are_shot_and_none_are(
        group_hub, group_store, monkeypatch):
    """5a. Ceiling armed, recovery on: the start failure says no frames are
    shot until guiding runs and that it is retried (not "continuing
    unguided"), the raw error is on its own evidence line, and then the real
    frame gate holds: no exposure. With recovery off the line says the target
    stops, and it does.

    MUTANT M5 (the line back to "guiding failed to start: ... continuing
    unguided"): RED.
    """
    night = await _night(group_hub, monkeypatch, _plan(), guide=_never,
                         wall_s=60.0)
    lines = [m for _t, _l, m in night.lines]
    i = lines.index(L_START_FAIL_RETRY)
    assert lines[i + 1].startswith("the guiding start failed with: "), lines[i:i + 2]
    assert "no guide star found" in lines[i + 1]
    assert not [m for m in lines if "guiding failed to start" in m]
    _operator_line_ok(L_START_FAIL_RETRY)
    _operator_line_ok(L_START_FAIL_STOP)
    _operator_line_ok(L_RESTART_FAIL_RETRY)
    assert night.captures == []


class _FailingGuider(Guider):
    name = "failing guider"

    def __init__(self) -> None:
        self.connected = True
        self.active = True
        self.starts = 0

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def start_guiding(self) -> None:
        self.starts += 1
        self.active = False
        raise DeviceError("native guider: no guide star")

    async def stop_guiding(self) -> None:
        self.active = False

    async def is_active(self) -> bool:
        return self.active

    async def dither(self, pixels: float = 3.0, settle=None) -> None: ...

    def stats(self):
        return GuideStats()


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def test_the_restart_after_a_limit_recovery_says_it_and_the_gate_holds(
        sim_hub, monkeypatch, bus_lines):
    """5b. DRIVEN DIRECTLY (not a whole night): `_recover_from_tracking_
    refusal` is run to its guide restart, which fails. With the ceiling armed
    it says no frames are shot and the error on its own line; then the real
    `_run_step` is entered on the same engine and reaches the guard
    (`_hold_for_guiding`, spied to stop the loop there) before any exposure.

    MUTANT M5 (the old "continuing unguided" line): RED.
    MUTANT M1 (the guard deleted): RED - `_begin_frame` is reached.
    """
    from astrodeck.sequence import engine as E

    g = _FailingGuider()
    sim_hub.guider = g
    eng = SequenceEngine(sim_hub)
    eng._cfg = AppConfig()
    target = Target(name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=False,
                    autofocus_first=False,
                    steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    eng.plan = SequencePlan(name="p", guide=True, meridian_flip=False,
                            autofocus_every=0, dither_every=0,
                            max_guide_rms=5.0, targets=[target])
    monkeypatch.setattr(E.schedule, "dark_enough", lambda *a, **k: True)
    monkeypatch.setattr(hub_module.config_store.cfg().site, "is_default", False)

    ok = await eng._recover_from_tracking_refusal(target)
    assert ok is True and g.starts >= 1, "premise: the restart was tried"
    said = [m for (_lvl, m, _s) in bus_lines]
    assert L_RESTART_FAIL_RETRY in said, said[-6:]
    assert any(m == "M42: the guiding restart failed with: native guider: "
                    "no guide star" for m in said), said[-6:]
    assert not [m for m in said if CONTINUING in m.lower()]

    held = []

    async def spy(tgt):
        held.append(tgt)
        raise StopTarget("held")

    began = []
    monkeypatch.setattr(eng, "_hold_for_guiding", spy)
    monkeypatch.setattr(eng, "_begin_frame",
                        lambda *a, **k: began.append(a))

    async def noop(*a, **k):
        return None

    for name in ("_safety_gate", "_enforce_altitude_floor",
                 "_maybe_meridian_flip", "_enforce_flip_owed"):
        monkeypatch.setattr(eng, name, noop)
    monkeypatch.setattr(eng, "_enforce_stop_boundary", lambda t: None)
    eng._done = {}
    eng._frames_done = 0
    with pytest.raises(StopTarget, match="held"):
        await eng._run_step(0, 0, target, target.steps[0])
    assert held == [target] and began == []


# ------------------------------------------------------------------------ 6

async def test_with_recovery_off_the_target_stops_at_once(
        group_hub, group_store, monkeypatch):
    """6. Ceiling 5.0, `recover_guiding` off, the guider never starts. Nothing
    would restart guiding, so the target stops at the first pass with the
    fixed R3a reason and the operator's way out in a warning; no frame is
    shot and no wait is spent.

    MUTANT M6 (`_hold_for_guiding` without the recover_guiding test): RED -
    the target waits and retries instead of stopping at once.
    """
    night = await _night(group_hub, monkeypatch, _plan(recover=False),
                         guide=_never)
    assert night.done
    assert night.captures == []
    assert night.hub.guider.attempts.get("Alpha") == 1
    stops = [(t, m) for t, _l, m in night.lines
             if "turn guiding recovery on" in m]
    assert len(stops) == 1, night.lines[-6:]
    assert stops[0][0] - night.t0 < GUIDE_RETRY_GAP_S, stops
    assert "Set Reject above (guide RMS) to 0" in stops[0][1][:60]
    assert night.said(L_START_FAIL_STOP)
    assert night.said(GUIDE_RECOVERY_OFF_STOP) or any(
        GUIDE_RECOVERY_OFF_STOP in (r.get("reason") or "")
        for r in getattr(night.engine._session, "set_aside", []) or [])
    assert not _spell_lines(night)


# ------------------------------------------------------------------------ 7

async def test_a_streak_of_guider_stopped_rejects_is_named_not_blamed_on_clouds(
        group_hub, group_store, monkeypatch):
    """7. BUILT THROUGH THE REAL FRAME LOOP. The guider starts and stops
    itself during every exposure (the shape of a native runaway); each frame
    is rejected by the D-06 gate, recovery restarts it, and the third reject
    trips the night guard. The stop names the reason and does not guess at
    clouds.

    MUTANT M7 (the per-reason count dropped): RED - the old text, with
    "(clouds?)".
    """
    def stops_itself(rec):
        group_hub.guider.active = False

    night = await _night(
        group_hub, monkeypatch,
        _plan(count=10, count_mode="accepted", max_consecutive_rejects=0,
              max_consecutive_rejects_night=3),
        guide=lambda who, n: True, on_capture=stops_itself)
    assert night.done
    stop = [m for _t, lvl, m in night.lines
            if lvl == "error" and "stopped early" in m]
    assert len(stop) == 1, night.lines[-5:]
    assert "3 consecutive rejects across targets, 3 of them shot with the "\
           "guider stopped" in stop[0], stop
    assert "(clouds?)" not in stop[0], stop
    assert night.engine.state.get("end_reason") == "quality"


async def test_a_streak_of_star_floor_rejects_keeps_the_cloud_guess(
        group_hub, group_store, monkeypatch):
    """7b. The same guard on a sky-like reason. The star floor is cloud-like
    like the HFR reject (`_REJECT_CLOUDLIKE`); the harness's frames carry a
    fixed HFR so the star floor builds the streak through the real loop, and
    the HFR branch is graded in `test_the_dominant_reason_decides_the_guess`.

    MUTANT M7 (the guess made unconditionally absent): RED.
    """
    night = await _night(
        group_hub, monkeypatch,
        _plan(count=10, count_mode="accepted", min_stars=10,
              max_consecutive_rejects=0, max_consecutive_rejects_night=3),
        guide=lambda who, n: True, stars=lambda who, f: 0)
    assert night.done
    stop = [m for _t, lvl, m in night.lines
            if lvl == "error" and "stopped early" in m]
    assert len(stop) == 1, night.lines[-5:]
    assert "3 of them below the star floor" in stop[0], stop
    assert stop[0].endswith("(clouds?)"), stop


def test_the_dominant_reason_decides_the_guess():
    """7c. DIRECT (the words alone): a mixed streak names the dominant reason
    and a tie names the latest; HFR is cloud-like; no reasons at all keeps
    today's text with its guess."""
    eng = SequenceEngine.__new__(SequenceEngine)
    eng._night_rejects = 5
    eng._night_reject_reasons = {"hfr": 2, "guider_stopped": 3}
    assert eng._night_stop_words() == (
        "5 consecutive rejects across targets, 3 of them shot with the "
        "guider stopped", False)
    eng._night_reject_reasons = {"guider_stopped": 2, "hfr": 2}
    words, cloudlike = eng._night_stop_words()
    assert words.endswith("2 of them with a poor HFR") and cloudlike
    eng._night_reject_reasons = {}
    assert eng._night_stop_words() == (
        "5 consecutive rejects across targets", True)


# ------------------------------------------------------------------------ 9

async def test_no_guider_connected_frames_are_shot_and_accepted(
        group_hub, group_store, monkeypatch):
    """9 (R8). The line "this plan asks for guiding but no guider is
    connected - continuing UNGUIDED" is true: `_frame_was_guided` answers
    True with no guider, so the gate accepts the frames. Ceiling 5.0.

    MUTANT M13 (`_frame_was_guided` answers False for an absent guider):
    RED - the guard holds the night and no frame is shot.
    """
    group_hub.guider = None
    night = await _night(group_hub, monkeypatch, _plan(count=3))
    assert night.done
    assert len(night.captures) == 3, night.shots()
    assert night.engine._rejected == 0
    assert night.said("continuing UNGUIDED")
    assert not _spell_lines(night)


# ----------------------------------------------------------------------- 10

async def test_the_retry_wait_is_a_watched_wait(
        group_hub, group_store, monkeypatch):
    """10. The 600 s retry wait is a hold like the engine's others.

    * the dead-man ping fires at least as often as a frame (every
      HOLD_WATCH_S) throughout the wait;
    * a pause lands within one tick and holds the wait (the clock stops);
    * an abort ends the paused run promptly.

    MUTANT M10 (the wait is one bare sleep): RED - no ping inside the wait.
    MUTANT M11 (the wait without its `_checkpoint`): RED - the pause does
    not stop the clock.
    """
    night = Night(group_hub, monkeypatch, guide=_never)
    eng = night.engine
    ticks: list[float] = []
    real_tick = eng._frame_alerts_tick

    async def tick():
        ticks.append(night.clock.t)
        return await real_tick()

    monkeypatch.setattr(eng, "_frame_alerts_tick", tick)
    try:
        eng.start(_plan())
        # In the first spell's wait: the setup's start, two attempts and two
        # gaps are over by +120 s.
        mid = night.t0 + 2 * GUIDE_RETRY_GAP_S + GUIDE_RETRY_WAIT_S / 2
        assert await night.until(mid)
        wait_from = next(t for t, _l, m in night.lines if SPELL_WORDS in m)
        inside = [t for t in ticks if wait_from <= t <= mid]
        assert len(inside) >= (mid - wait_from) / HOLD_WATCH_S - 1, (
            f"the ping went quiet inside the wait: {inside}")
        gaps = [b - a for a, b in zip(inside, inside[1:])]
        assert all(g <= HOLD_WATCH_S + 1e-6 for g in gaps), gaps

        eng.pause()
        night.release()
        stood = night.clock.t
        await asyncio.sleep(0.5)       # real time: a held wait stays held
        assert night.clock.t <= stood + HOLD_WATCH_S + 1e-6, (
            f"a pause did not hold the wait: {night.clock.t - stood} s on")
        await asyncio.wait_for(eng.abort(), timeout=30)
        assert not eng.running
    finally:
        await night.close()


async def test_the_frozen_window_ends_a_retry_wait(
        group_hub, group_store, monkeypatch):
    """10b. A target whose window (``max_run_min``) closes inside the retry
    wait is stopped at the next tick, not after the 600 s.

    MUTANT M10 (the wait is one bare sleep): RED - the target outlives its
    window by the rest of the wait.
    """
    target = single("Alpha", count=3)
    target.schedule = Schedule(max_run_min=8)
    night = await _night(group_hub, monkeypatch, _plan(targets=[target]),
                         guide=_never)
    assert night.done
    assert night.captures == []
    ended = night.rel(max(t for t, _l, _m in night.lines))
    assert ended <= 8 * 60 + HOLD_WATCH_S + 60, (
        f"the window closed at +480 s and the night ran to +{ended} s")
    assert len(_spell_lines(night)) == 1


# ------------------------------------------------- the predicate, not the loop

def test_the_guard_and_the_gate_ask_one_predicate():
    """The guard and the D-06 reject share `_guide_gate_refuses`: it is true
    exactly for a light frame seen unguided at the shutter with the ceiling
    above 0. (A direct table; the loop cases above prove each side reaches
    it.)

    MUTANT M4 (calibration ignored) and M9 (a missing guider ignored): RED.
    """
    class _G:
        connected = True

    class _Hub:
        guider = _G()

    eng = SequenceEngine.__new__(SequenceEngine)
    eng.hub = _Hub()
    eng._plan = SequencePlan(guide=True)

    class _P:
        max_guide_rms = 5.0

    eng._policy = _P()
    assert eng._guide_gate_refuses(False) is True
    assert eng._guide_gate_refuses(True) is False
    assert eng._guide_gate_refuses(None) is False
    assert eng._guide_gate_refuses(False, calibration=True) is False
    eng._policy.max_guide_rms = 0.0
    assert eng._guide_gate_refuses(False) is False
    eng._policy.max_guide_rms = 5.0
    # A guider that dropped off the link between the shutter and the grade:
    # the frame was still seen unguided, so it is still refused. MUTANT
    # (the gate asks the live guider's connection again): RED here.
    _G.connected = False
    eng.hub.guider = _G()
    assert eng._guide_gate_refuses(False) is True
    eng.hub.guider = None
    assert eng._guide_gate_refuses(False) is True
    _G.connected = True
    # The live guider still decides where a LINE is chosen before any frame
    # (`_guide_reject_armed`): no guider, no promise to hold.
    assert eng._guide_reject_armed() is False
    eng.hub.guider = _G()
    assert eng._guide_reject_armed() is True
    eng._plan = SequencePlan(guide=False)
    assert eng._guide_reject_armed() is False
