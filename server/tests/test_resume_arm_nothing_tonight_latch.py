# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A night with nothing left to shoot is said once, and dawn says why at info
level (#284, #159 follow-up).

WHAT WAS WRONG. ``ResumeArm._recover`` refuses a session with nothing it can
shoot tonight (``nothing_to_shoot_tonight``), because started, such a run
ends at once and the next tick would start it again every minute. ``tick``
then treated that refusal like any other: an "auto-resume held" warning every
ten-minute retry, and when the window closed mid-backoff at dawn, the error
"auto-resume gave up for tonight", which a default ntfy sink delivers as
urgent. Nothing can change within the night (set-aside records are only
added, a closed window stays closed), and the run already said so when it set
each target aside, so a held night cost the operator a push every ten minutes
and an urgent page saying auto-resume "gave up" when it held on purpose.

WHAT IT DOES NOW. The refusal is latched per session and night key: one
warning line, then quiet re-checks on the same ten-minute retry (an edited
plan can still change the answer). When the window closes on a session whose
last refusal was this one, one info line says its remaining work was set
aside for the night, and no line says auto-resume gave up. Any other refusal
keeps today's warning on every retry and today's dawn error.

THE HARNESS. The real ``Hub`` on the simulator rig (``_simhub.sim_hub``), a
real ``SequenceEngine`` and a real ``ResumeArm`` whose ``tick`` and
``_recover`` run unmodified, driven through a night by an injected clock and
a window the case opens and closes (``_window_open``, as
``test_resume_arm_no_light_backoff.py`` drives it). The session's owing
target is set aside for the clock's night key, so nothing depends on where
the Sun is. The solve and the goto are recorded, and ``engine.start``
records its calls.

MUTANTS. Each named mutant was applied to ``astrodeck/sequence/resume_arm.py``
in a private scratch copy of ``server/`` (issue #254), and each failure is
quoted verbatim (``--tb=short``, ``E`` lines only). Mutant "treated as any
refusal" is ``tick`` with the latch removed: the nothing-tonight refusal goes
through the ordinary branch, and the dawn line is the ordinary error.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.events import night_key
from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.models import (ExposureStep, Schedule, SequencePlan,
                                       Target)
from astrodeck.sequence.resume_arm import (NOTHING_TONIGHT, RETRY_INTERVAL_S,
                                           ResumeArm)
from astrodeck.sequence.session import Session, session_store

#: A clock reading to start the night from. Any would do: nothing here
#: depends on the Sun, only on the night key the clock falls in.
T0 = 1_700_000_000.0


def _plan() -> SequencePlan:
    return SequencePlan(name="aside", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[
                            Target(id="t-a", name="NGC 604", ra_hours=1.572,
                                   dec_deg=30.7853, center=False,
                                   autofocus_first=False, schedule=Schedule(),
                                   steps=[ExposureStep(id="s-a", filter="L",
                                                       exposure_s=0.05,
                                                       count=3)])])


def _armed(name: str = "aside", sid: str = "s-aside",
           nights: tuple[str, ...] = ()) -> Session:
    """An armed dormant session whose one target is set aside on each of
    ``nights``."""
    s = Session(id=sid, name=name, status="dormant", plan=_plan(),
                auto_resume=True)
    for night in nights:
        s.note_set_aside("t-a", "centring failed on 3 passes", night=night)
    session_store.save(s)
    return s


@pytest.fixture
def night(sim_hub, monkeypatch, bus_lines):
    """The arm, its clock and its window, with the moves recorded."""
    from astrodeck.devices import fingerprint as _fp
    monkeypatch.setattr(_fp, "verdict",
                        lambda **kw: _fp.Verdict(focus_trusted=True))
    solves: list[float] = []
    gotos: list[tuple] = []
    starts: list[str] = []

    async def solve(*a, **kw):
        solves.append(kw.get("exposure_s", 0.0))
        if script:
            raise script.pop(0)
        return {}

    async def goto(*a, **kw):
        gotos.append(a)
        raise RuntimeError("the test's mount does not slew")

    script: list[BaseException] = []
    monkeypatch.setattr(sim_hub, "solve_and_sync", solve)
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    engine = SequenceEngine(sim_hub)

    async def limits_pass(target, *, cfg=None, plan=None, projected=True):
        return None

    def start(plan, *, session=None, tracking=None):
        starts.append(session.id)

    monkeypatch.setattr(engine, "check_slew_limits", limits_pass)
    monkeypatch.setattr(engine, "start", start)
    window = {"open": True}
    clock = {"t": T0}
    monkeypatch.setattr(ResumeArm, "_window_open",
                        lambda arm, s, t: window["open"])
    monkeypatch.setattr(ResumeArm, "_can_solve", lambda arm: True)
    arm = ResumeArm(engine, sim_hub, clock=lambda: clock["t"])

    async def tick_at(t: float) -> None:
        clock["t"] = t
        await arm.tick()

    return SimpleNamespace(arm=arm, window=window, tick_at=tick_at,
                           solves=solves, gotos=gotos, starts=starts,
                           script=script, lines=bus_lines)


def _held(lines) -> list[str]:
    return [m for lv, m, _s in lines
            if lv == "warning" and m.startswith("auto-resume held:")]


def _errors(lines) -> list[str]:
    return [m for lv, m, _s in lines if lv == "error"]


def _set_aside_notes(lines) -> list[str]:
    return [m for lv, m, _s in lines
            if lv == "info" and "set aside" in m and "auto-resume" in m]


async def test_a_night_with_nothing_to_shoot_warns_once_and_ends_at_info(
        night):
    """Four retries through a night on a session whose only owing target is
    set aside tonight: one warning, then quiet, the hold standing all the
    while with its first ``since``. The window closes at dawn: one info line
    saying the remaining work was set aside, no error, and no line anywhere
    saying auto-resume gave up. Nothing is solved, slewed or started.

    RED under mutant "treated as any refusal", observed verbatim (one
    warning per retry; the dawn error follows):

            assert len(held) == 1 and NOTHING_TONIGHT in held[0], held
        E   AssertionError: ['auto-resume held: nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past i...ight, past its observing window or never above its start floor; not slewing until the next night — retrying in 10 min']
        E   assert (4 == 1)
        E    +  where 4 = len(['auto-resume held: nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past i...ight, past its observing window or never above its start floor; not slewing until the next night — retrying in 10 min'])

    and with its first assertion taken out, so the case reaches dawn, on
    the same mutant:

            assert _errors(night.lines) == [], _errors(night.lines)
        E   AssertionError: ["auto-resume gave up for tonight: 'aside' window closed before a successful start"]
        E   assert ['auto-resume...essful start'] == []
        E     Left contains one more item: "auto-resume gave up for tonight: 'aside' window closed before a successful start"
        E     Use -v to get more diff
    """
    _armed(nights=(night_key(T0),))
    retries = [T0 + i * RETRY_INTERVAL_S for i in range(4)]
    for t in retries:
        await night.tick_at(t)
        assert night.arm.hold["reason"] == NOTHING_TONIGHT, night.arm.hold
        assert night.arm.hold["since"] == T0, night.arm.hold
    held = _held(night.lines)
    assert len(held) == 1 and NOTHING_TONIGHT in held[0], held
    assert night.arm._retry_at == retries[-1] + RETRY_INTERVAL_S

    night.window["open"] = False
    await night.tick_at(T0 + 8 * 3600.0)
    await night.tick_at(T0 + 9 * 3600.0)

    assert _errors(night.lines) == [], _errors(night.lines)
    notes = _set_aside_notes(night.lines)
    assert len(notes) == 1 and "aside" in notes[0], notes
    said = [m for _lv, m, _s in night.lines]
    assert not [m for m in said if "gave up" in m], said
    assert night.solves == [] and night.gotos == [] and night.starts == []


async def test_the_latch_is_per_night_and_per_session(night):
    """The next night is a new night key, so its first refusal is said again
    (someone else may be reading tomorrow), and quiet after. Another session
    armed in its place is said about on its own.

    RED under mutant "latched per session only" (the latch keyed on the
    session id alone), on the second night, observed verbatim:

            assert len(_held(night.lines)) == 2, _held(night.lines)
        E   AssertionError: ['auto-resume held: nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past i...its start floor; not slewing until the next night — checking again every 10 min, without a word, until the night ends']
        E   assert 1 == 2
        (the ``where`` lines after these are left out)
    """
    tonight, tomorrow = T0, T0 + 86400.0
    assert night_key(tonight) != night_key(tomorrow), "premise: two nights"
    s = _armed(nights=(night_key(tonight), night_key(tomorrow)))
    await night.tick_at(tonight)
    await night.tick_at(tonight + RETRY_INTERVAL_S)
    night.window["open"] = False
    await night.tick_at(tonight + 8 * 3600.0)
    night.window["open"] = True
    await night.tick_at(tomorrow)
    await night.tick_at(tomorrow + RETRY_INTERVAL_S)
    assert len(_held(night.lines)) == 2, _held(night.lines)

    s.auto_resume = False
    session_store.save(s)
    _armed(name="other", sid="s-other", nights=(night_key(tomorrow),))
    await night.tick_at(tomorrow + 2 * RETRY_INTERVAL_S)
    await night.tick_at(tomorrow + 3 * RETRY_INTERVAL_S)
    assert len(_held(night.lines)) == 3, _held(night.lines)
    assert night.arm.hold["session_name"] == "other", night.arm.hold


async def test_any_other_refusal_keeps_todays_warnings_and_dawn_error(night):
    """Control: the latch is for this refusal only. First the session holds
    on nothing to shoot tonight; then the operator lifts the set-aside
    record, and every later retry's blind solve fails, an ordinary hold.
    That hold warns on every retry, and since it was the last refusal
    before dawn, dawn gives today's error, not the set-aside note.

    RED under mutant "any nothing-tonight that night softens the dawn" (the
    dawn line asks whether the latch was ever set for the session, not
    whether the last refusal was this one), observed verbatim:

            assert len(errors) == 1 and "gave up for tonight" in errors[0], errors
        E   AssertionError: []
        E   assert (0 == 1)
        E    +  where 0 = len([])
    """
    s = _armed(nights=(night_key(T0),))
    await night.tick_at(T0)
    s.set_aside = []
    session_store.save(s)
    night.script.extend(RuntimeError("plate solve failed: Not enough stars.")
                        for _ in range(3))
    for i in range(1, 4):
        await night.tick_at(T0 + i * RETRY_INTERVAL_S)
    held = _held(night.lines)
    assert len(held) == 4, held
    assert [NOTHING_TONIGHT in h for h in held] == [True, False, False,
                                                    False], held

    night.window["open"] = False
    await night.tick_at(T0 + 8 * 3600.0)
    errors = _errors(night.lines)
    assert len(errors) == 1 and "gave up for tonight" in errors[0], errors
    assert _set_aside_notes(night.lines) == [], _set_aside_notes(night.lines)


async def test_a_start_between_two_refusals_makes_the_second_news(
        night, sim_hub, monkeypatch):
    """The latch is cleared by a start. The session holds on nothing to
    shoot tonight (one warning); the operator lifts the set-aside record,
    and the next retry re-centres and starts it; that run sets the target
    aside again and ends, dormant and armed. The refusal after it is a new
    fact about a night that has changed, so it is said once more, and quiet
    after that.

    RED under mutant "the latch outlives a start" (``tick``'s
    ``self._nothing_tonight_said = None`` after a start removed), observed
    verbatim (the refusal after the run is silent):

            assert len(held) == 2 and all(NOTHING_TONIGHT in h for h in held), held
        E   AssertionError: ['auto-resume held: nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past i...its start floor; not slewing until the next night — checking again every 10 min, without a word, until the night ends']
        E   assert (1 == 2)
        E    +  where 1 = len(['auto-resume held: nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past i...its start floor; not slewing until the next night — checking again every 10 min, without a word, until the night ends'])
    """
    async def centred(*a, **kw):
        night.gotos.append(a)
        return {"centered": True, "error_arcmin": 0.2, "attempts": 1,
                "rotation": None}

    s = _armed(nights=(night_key(T0),))
    await night.tick_at(T0)
    s.set_aside = []
    session_store.save(s)
    monkeypatch.setattr(sim_hub, "goto_and_center", centred)
    await night.tick_at(T0 + RETRY_INTERVAL_S)
    assert night.starts == ["s-aside"], "premise: the lifted night started"
    s.note_set_aside("t-a", "centring failed on 3 passes", night=night_key(T0))
    session_store.save(s)
    for i in (1, 2, 3):
        await night.tick_at(T0 + RETRY_INTERVAL_S + i * RETRY_INTERVAL_S)
    held = _held(night.lines)
    assert len(held) == 2 and all(NOTHING_TONIGHT in h for h in held), held
    assert night.starts == ["s-aside"], night.starts


async def test_a_re_arm_makes_the_refusal_news_again(night):
    """The latch is cleared by a disarm. Someone who disarms and re-arms a
    held session is asking the rig to try again tonight, and should hear
    what it finds, as a re-armed no-light spell re-alerts: the refusal after
    the re-arm is said once, and quiet after that.

    RED under mutant "the latch outlives a disarm" (``tick``'s
    ``self._nothing_tonight_said = None`` on no armed session removed),
    observed verbatim (the refusal after the re-arm is silent):

            assert len(held) == 2 and all(NOTHING_TONIGHT in h for h in held), held
        E   AssertionError: ['auto-resume held: nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past i...its start floor; not slewing until the next night — checking again every 10 min, without a word, until the night ends']
        E   assert (1 == 2)
        E    +  where 1 = len(['auto-resume held: nothing this session still owes can be shot tonight: what it owes is set aside for tonight, past i...its start floor; not slewing until the next night — checking again every 10 min, without a word, until the night ends'])
    """
    s = _armed(nights=(night_key(T0),))
    await night.tick_at(T0)
    s.auto_resume = False
    session_store.save(s)
    await night.tick_at(T0 + 60.0)
    assert night.arm.hold is None, "premise: the disarm was seen"
    s.auto_resume = True
    session_store.save(s)
    for i in (2, 3, 4):
        await night.tick_at(T0 + i * 60.0 + (i - 2) * RETRY_INTERVAL_S)
    held = _held(night.lines)
    assert len(held) == 2 and all(NOTHING_TONIGHT in h for h in held), held
    assert night.solves == [] and night.starts == []
