# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#252: a run that ends with frames owed must stay resumable.

Every test here drives the REAL engine. The defect these cover shipped behind
a correct pure function (``Session.remaining()``) whose only caller asked it
the wrong question, and a pure-function test passes against it happily.

Rig evidence, 2026-08-16: session 18572134 ended ``complete`` with 117 of 175
frames and ``auto_resume`` set - armed, finished, and unreachable forever,
because ``session_store.armed()`` only ever returns a DORMANT session.
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Schedule, Target
from astrodeck.sequence.session import session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _target(name, count, schedule=None) -> Target:
    t = Target(name=name, ra_hours=5.5881, dec_deg=-5.3911, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=count)])
    if schedule is not None:
        t.schedule = schedule
    return t


def _plan(name, targets) -> SequencePlan:
    return SequencePlan(name=name, guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=targets)


def _close_the_window(eng, target) -> None:
    """Dawn arrives UNDER a running frame loop.

    The scheduler froze this target's window at run start and will never
    re-select it, so a closure that happens while the step is shooting can only
    be seen by the per-frame boundary check. That is the shape of every real
    single-target night on this rig, and the shape no existing test had: the
    two tests that guard this behaviour both close the window in the PAST, so
    the target is rejected at SELECTION and reaches a different branch.
    """
    start, _stop = eng._frozen.get(id(target)) or (time.time() - 100, None)
    eng._frozen[id(target)] = (start, time.time() - 1)


async def test_a_dawn_cut_mid_target_leaves_the_session_resumable(sim_hub):
    plan = _plan("dawncut", [_target("A", 8)])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 2)
    _close_the_window(eng, plan.targets[0])
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    s = session_store.load(sid)
    assert s.owed() == 8 - len(s.frames)
    assert 0 < s.owed() < 8, "the cut has to land mid-target for this to test anything"
    assert s.status == "dormant", (
        "a night cut short owes frames, and 'complete' is the one status that "
        "makes them unreachable")
    armed = session_store.armed()
    assert armed is not None and armed.id == sid, (
        "armed() only ever returns a dormant session - this is the whole "
        "reason the status matters")


async def test_the_dawn_cut_is_reported_as_a_dawn_cut(sim_hub):
    """The run's own ending has to match the session's. On 2026-08-16 the log
    read `sequence 'NGC 7129 - LRGB+SHO cycle' complete: 117 frames` over a
    night that owed 58 - the word "complete" was the entire record of it."""
    plan = _plan("dawncut2", [_target("A", 8)])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    assert await wait_for(lambda: eng._frames_done >= 2)
    _close_the_window(eng, plan.targets[0])
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    assert eng.state.get("end_reason") == "dawn_cutoff"


async def test_the_terminal_line_says_how_short_the_night_was(sim_hub, bus_lines):
    """The log is where an operator finds this out, hours later, in the dark.

    At 05:25 on 2026-08-16 the entire record of a 58-frame shortfall was
    `sequence 'NGC 7129 - LRGB+SHO cycle' complete: 117 frames` - a sentence
    with no shortfall in it and the wrong verb.

    NB `bus_lines` yields (level, message, source) TUPLES: `"text" in line` is
    whole-ELEMENT matching and matches nothing, ever. Read the message field.
    """
    plan = _plan("shortfall", [_target("A", 8)])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    assert await wait_for(lambda: eng._frames_done >= 2)
    _close_the_window(eng, plan.targets[0])
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    shot = eng._frames_done
    ends = [m for (_lvl, m, _src) in bus_lines
            if m.startswith("sequence 'shortfall'")]
    assert ends, "the night ended and said nothing"
    line = ends[-1]
    print("\nTERMINAL LINE: " + line)
    assert "stopped at dawn" in line
    assert f"{shot} of 8 frames" in line, "how much of the plan is in the bag"
    assert f"{8 - shot} remaining" in line, "how much is not"
    assert "resumes when the window opens" in line, (
        "and that nobody has to do anything about it tonight")


async def test_the_terminal_line_of_a_flow_with_automatic_resume_off_says_continue(
        sim_hub, bus_lines):
    """THE OFF TWIN of the case above (#195, WP-85, wave 14 integration).

    A flow whose DUSK WINDOW has Automatic resume Off compiles
    ``resume_across_nights: false``, and `_finalize_report` disarms its
    session at a dawn cut and logs "CONTINUE it by hand". The terminal line
    written a moment before it used to say, for every plan, that the session
    "stays armed and resumes when the window opens": a claim nothing keeps
    for this flow, in the sentence the operator reads hours later. It now
    says the session will not resume on a later night and to CONTINUE it by
    hand, and it says the same how-short numbers as the On line.

    MUTANT "the phrase ignores the plan" (``_shortfall_phrase`` returns the
    On sentence whatever ``resume_across_nights`` says): RED -
        AssertionError: the line promised a resume the session will not make:
        "sequence 'shortfall_off' stopped at dawn: 3 of 8 frames, 5 remaining
        — the session stays armed and resumes when the window opens"
    """
    plan = _plan("shortfall_off", [_target("A", 8)])
    plan.resume_across_nights = False
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 2)
    _close_the_window(eng, plan.targets[0])
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    shot = eng._frames_done
    ends = [m for (_lvl, m, _src) in bus_lines
            if m.startswith("sequence 'shortfall_off'")]
    assert ends, "the night ended and said nothing"
    line = ends[-1]
    assert "stopped at dawn" in line
    assert f"{shot} of 8 frames" in line, "how much of the plan is in the bag"
    assert f"{8 - shot} remaining" in line, "how much is not"
    assert "resumes when the window opens" not in line, (
        f"the line promised a resume the session will not make: {line!r}")
    assert "will not resume on a later night" in line and "CONTINUE" in line, line
    # The premise the line states is the engine's own: disarmed, dormant.
    s = session_store.load(sid)
    assert s.status == "dormant" and s.auto_resume is False, (
        s.status, s.auto_resume)


def _phrase(owed, *, engine_resumes, session_resumes=None, shot=3, total=8):
    """``SequenceEngine._shortfall_phrase`` on a bare engine: its own plan,
    and a session whose frozen plan is ``session_resumes`` (None: no session,
    as before a run has one)."""
    plan = SimpleNamespace(resume_across_nights=engine_resumes,
                           total_frames=lambda: total)
    session = None if session_resumes is None else SimpleNamespace(
        plan=SimpleNamespace(resume_across_nights=session_resumes))
    eng = SimpleNamespace(plan=plan, _session=session, _frames_done=shot)
    return SequenceEngine._shortfall_phrase(eng, owed)


_ARMED = "the session stays armed and resumes when the window opens"


def test_an_off_flow_that_owes_nothing_is_not_told_to_continue_by_hand():
    """THE QUIET HALF OF THE OFF TWIN (#195, WP-85, wave 14 integration). A
    dawn cut can end with nothing owed (the flag is set as the last frame
    lands). `_finalize_report` logs "CONTINUE it by hand" only when frames are
    owed, so for an Off flow with none the phrase says how short the night
    was and stops: no "stays armed" (the session is disarmed) and no CONTINUE
    (there is no rest to shoot). The On flow's sentence is unchanged, owed or
    not, which is the control.

    MUTANT "an Off flow with nothing owed is still told to CONTINUE" (the
    ``if not owed: return head`` of ``_shortfall_phrase`` removed): RED -
        AssertionError: nothing is owed, so there is nothing to continue by
        hand: '3 of 8 frames, 0 remaining — the session will not resume on
        a later night (automatic resume is off for this flow); CONTINUE it by
        hand to shoot the rest'
    """
    said = _phrase(0, engine_resumes=False)
    assert said == "3 of 8 frames, 0 remaining", (
        f"nothing is owed, so there is nothing to continue by hand: {said!r}")
    assert _phrase(0, engine_resumes=True) == f"3 of 8 frames, 0 remaining — {_ARMED}"
    assert _phrase(5, engine_resumes=True) == f"3 of 8 frames, 5 remaining — {_ARMED}"


def test_the_phrase_reads_the_plan_the_session_froze_not_the_engines():
    """`_finalize_report` disarms on the SESSION's frozen plan, so the phrase
    written a moment before it reads the same one, and falls back to the
    engine's own plan only when there is no session. Two plans that disagree
    show which is read.

    MUTANT "the engine's plan wins" (``plan = getattr(self._session, "plan",
    None) or self.plan`` made ``plan = self.plan``): RED, at the first
    assertion (an engine plan Off over a session frozen On) -
        AssertionError: the session froze Automatic resume On and the line
        did not say the session resumes: '... will not resume on a later
        night ...'
    """
    on_in_session = _phrase(5, engine_resumes=False, session_resumes=True)
    assert _ARMED in on_in_session, (
        f"the session froze Automatic resume On and the line did not say "
        f"the session resumes: {on_in_session!r}")
    off_in_session = _phrase(5, engine_resumes=True, session_resumes=False)
    assert "will not resume on a later night" in off_in_session and (
        "CONTINUE" in off_in_session) and _ARMED not in off_in_session, (
        f"the session froze Automatic resume Off and the line promised a "
        f"resume: {off_in_session!r}")
    # No session yet: the engine's own plan answers.
    assert "CONTINUE" in _phrase(5, engine_resumes=False)
    assert _ARMED in _phrase(5, engine_resumes=True)


async def test_a_target_set_aside_leaves_the_night_incomplete(sim_hub):
    """Not every short night is a dawn cut. A target the run set aside for its
    own reasons - here ``on_missed="skip"`` - still owes its frames, and
    reporting COMPLETE over it is the same lie in a smaller font."""
    past = time.strftime("%H:%M", time.localtime(time.time() - 3 * 3600))
    b = _target("B", 2, Schedule(start_mode="time", start_time=past,
                                 on_missed="skip"))
    plan = _plan("missed", [_target("A", 2), b])
    a_step, b_step = plan.targets[0].steps[0], plan.targets[1].steps[0]
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    assert eng.state.get("end_reason") == "incomplete"
    s = session_store.load(sid)
    assert s.status == "dormant"
    assert s.accepted(a_step.id) == 2
    assert s.accepted(b_step.id) == 0
    assert s.owed() == 2


async def test_a_mixed_night_owes_only_the_target_that_was_cut(sim_hub):
    """A cut on one target must not drag a finished one back into the debt."""
    plan = _plan("mixed", [_target("A", 8), _target("B", 2)])
    a_step, b_step = plan.targets[0].steps[0], plan.targets[1].steps[0]
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 2)
    _close_the_window(eng, plan.targets[0])
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    assert eng.state.get("end_reason") == "dawn_cutoff"
    s = session_store.load(sid)
    assert s.status == "dormant"
    assert s.accepted(b_step.id) == 2, "B had an open window and its own frames"
    assert s.owed() == 8 - s.accepted(a_step.id)


async def test_no_ending_may_stamp_complete_over_owed_frames(sim_hub):
    """The backstop, tested head-on at the method that writes the status.

    ``_run`` now picks ``dawn_cutoff``/``incomplete`` whenever frames are owed,
    so nothing in the engine reaches ``_finalize_report("complete")`` with an
    unfinished ledger any more - which is exactly why this calls it directly.
    The rule being guarded is that the writer of the status does not take its
    caller's word for it, and #252 is why that rule exists: a caller got it
    wrong, and a correct-looking ``remaining()`` sat one line away unasked.

    Reverting the ledger check in ``_finalize_report`` must turn this red. If
    it does not, the check is unguarded and should be deleted rather than kept
    as decoration.
    """
    plan = _plan("owing", [_target("A", 4)])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 1)
    await eng.abort()

    owing = session_store.load(sid)
    assert owing.owed() > 0, "the session has to be short for this to test anything"

    # A fresh engine, that same unfinished session, and the one ending that
    # claims the plan is finished.
    eng2 = SequenceEngine(sim_hub)
    eng2._session = owing
    eng2._finalize_report("complete")

    assert session_store.load(sid).status == "dormant"


async def test_a_finished_run_still_completes(sim_hub):
    """The other direction. Over-correcting into false dormancy would re-arm
    every finished session and re-shoot it at the next dusk."""
    plan = _plan("short", [_target("A", 2)])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    s = session_store.load(sid)
    assert s.owed() == 0
    assert s.status == "complete"
    assert session_store.armed() is None


async def test_a_calibration_only_plan_still_completes(sim_hub):
    """Calibration frames reach the same ledger as lights (the rig's
    'Calib 2026-08-11' session holds 94 of them), so the ledger-truth rule must
    not strand a dark set in a nightly resume loop."""
    darks = Target(name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
                   center=False, autofocus_first=False,
                   steps=[ExposureStep(filter=None, exposure_s=0.05, count=2,
                                       frame_type="Dark")])
    eng = SequenceEngine(sim_hub)
    eng.start(_plan("calib", [darks]))
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    s = session_store.load(sid)
    assert s.owed() == 0
    assert s.status == "complete"
