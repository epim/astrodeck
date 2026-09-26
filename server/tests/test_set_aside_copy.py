"""A set-aside lasts for TONIGHT, and the log has to say so (#147, #208,
#189 S2).

The copy has had to follow the code twice. Before S0 the per-step reject
guard's lines promised the frames stayed owed "for another night", and the
altitude floor's that the target "resumes tomorrow", while a restart the
same night took both straight back. S0 made the copy say what the code did:
set aside for the rest of this run, and a restart or an auto-resume tries
it again, even tonight. S2 then made the set-aside itself last the night:
each is a ``Session.set_aside`` record with tonight's night key, which a
restart tonight reads and a later night does not. So the S0 sentences are
now false the other way, and every set-aside line says what is true: set
aside for tonight; a restart tonight does not retry it; the next night
does.

The first case holds the sentences and the behaviour together, on the real
`_run_step` against the simulator hub with the quality verdict scripted per
filter, as test_accepted_visit_bound.py does (whose module docstring says
why the filter wheel is taken off the hub). It trips the guard, starts the
SAME session again the same night, where the step is not shot, and again
the next night, where it is. The night is `engine.night_key`'s answer, fixed
by the test, so no run depends on the wall clock's date.

The behaviour on its own, on the clocked simulator, is
test_group_set_aside_persisted.py.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import StopTarget
from astrodeck.sequence.models import ExposureStep, Schedule, Target
from astrodeck.sequence.session import session_store
from _site_tracking import numeric_tokens
from _group_harness import GROUP_NAME, Night, grid_plan, group_hub, group_store

FILTERS = ("B", "Ha", "OIII")
TERMINAL = ("complete", "aborted", "error")
#: What S0's copy promised and S2 no longer does.
S0_PROMISE = ("tries it again", "tries them again", "takes it up again",
              "even tonight")
#: What every set-aside line must now say.
TONIGHT = ("a restart tonight does not retry", "the next night does")


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    wheel = h.devices.pop("filterwheel", None)
    yield h
    await h.disconnect_all()
    if wheel is not None:
        await wheel.disconnect()


def _cycle_plan(count: int) -> SequencePlan:
    """One accepted-mode cycle target, B, Ha and OIII, one attempt a visit,
    with a step guard of 3 and a night guard of 12."""
    return SequencePlan(
        name="set-aside-copy", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False, count_mode="accepted",
        max_consecutive_rejects=3, max_consecutive_rejects_night=12,
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=False,
            autofocus_first=False, acquisition="cycle",
            steps=[ExposureStep(filter=f, exposure_s=0.05, count=count,
                                per_visit=1) for f in FILTERS])])


def _ha_rejects(monkeypatch) -> list[tuple[str, bool]]:
    """Ha always rejects, B and OIII always accept. Returns every science
    exposure as ``(filter, verdict)`` in capture order."""
    shots: list[tuple[str, bool]] = []

    def fake(self, info, *, record=True, calibration=False):
        ti, si = self._active_step
        f = self.plan.targets[ti].steps[si].filter
        verdict = f != "Ha"
        shots.append((f, verdict))
        return verdict

    monkeypatch.setattr(SequenceEngine, "_check_quality", fake)
    return shots


async def _finish(eng: SequenceEngine, timeout: float) -> bool:
    """Wait for a terminal state; on the wall bound, abort and answer False."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if eng.state.get("state") in TERMINAL:
            return True
        await asyncio.sleep(0.05)
    await eng.abort()
    return False


async def _settled(eng: SequenceEngine, timeout: float = 30.0) -> None:
    """Let the run task finish its wind-down, so a second start() is legal."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while eng.running and loop.time() < deadline:
        await asyncio.sleep(0.05)
    assert not eng.running, "the run never finished winding down"


def _wrong(line: str) -> list[str]:
    """What ``line`` gets wrong: an S0 promise it still makes, or a thing it
    must say and does not."""
    low = line.lower()
    return ([p for p in S0_PROMISE if p in low]
            + [f"missing {p!r}" for p in TONIGHT if p not in low])


async def test_the_step_lines_say_tonight_and_the_nights_bear_them_out(
        sim_hub, monkeypatch, bus_lines):
    """Run 1: Ha rejects three times and is set aside; B and OIII complete,
    so the visit loop ends on "every step still owed is set aside". Run 2
    starts the SAME session the same night, as a restart would: Ha is not
    on the shutter, and the line says it was set aside earlier tonight. Run
    3 is the next night: Ha is tried again, three times. Every set-aside
    line says tonight, that a restart tonight does not retry it and that the
    next night does, and none makes S0's promise.

    MUTATION "restore the S0 sentence", run on each line separately.
    The `_set_step_aside` line put back ("set aside for the rest of this run
    after ... so a restart or an auto-resume tries it again, even tonight").
    Observed:
        AssertionError: the set-aside copy says what the engine does not do:
        {'M42: Ha set aside for the rest of this run after 3 consecutive
        rejects — its 3 frame(s) stay owed in the ledger, so a restart or an
        auto-resume tries it again, even tonight': ['tries it again', 'even
        tonight', "missing 'a restart tonight does not retry'", "missing 'the
        next night does'"]}
    The `_say_every_owed_step_set_aside` line put back ("... set aside for
    the rest of this run (Ha) — moving on; the ledger keeps them owed, so a
    restart or an auto-resume tries them again, even tonight"). Observed:
        AssertionError: the set-aside copy says what the engine does not do:
        {'M42: every step still owed is set aside for the rest of this run
        (Ha) — moving on; the ledger keeps them owed, so a restart or an
        auto-resume tries them again, even tonight': ['tries them again',
        'even tonight', "missing 'a restart tonight does not retry'", "missing
        'the next night does'"]}
    """
    tonight = {"key": "2026-09-24"}
    monkeypatch.setattr(engine_mod, "night_key",
                        lambda ts=None: tonight["key"])
    plan = _cycle_plan(3)
    shots = _ha_rejects(monkeypatch)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await _finish(eng, 90), f"run 1 never ended: {shots}"
    assert [x for x in shots if x[0] == "Ha"] == [("Ha", False)] * 3, (
        f"premise: run 1 set Ha aside after 3 rejects: {shots}")
    await _settled(eng)

    session = session_store.load(sid)
    assert session.status == "dormant", "premise: Ha is still owed"
    mark = len(shots)
    eng.start(session.plan, session=session)
    assert await _finish(eng, 90), f"run 2 never ended: {shots[mark:]}"
    await _settled(eng)
    assert [x for x in shots[mark:] if x[0] == "Ha"] == [], (
        f"a restart tonight shot Ha again, so 'a restart tonight does not "
        f"retry it' would be false: {shots[mark:]}")

    tonight["key"] = "2026-09-25"
    session = session_store.load(sid)
    mark = len(shots)
    eng.start(session.plan, session=session)
    assert await _finish(eng, 90), f"run 3 never ended: {shots[mark:]}"
    assert [x for x in shots[mark:] if x[0] == "Ha"] == [("Ha", False)] * 3, (
        f"the next night did not try Ha again, so 'the next night does' "
        f"would be false: {shots[mark:]}")

    said = [m for _lvl, m, src in bus_lines
            if src == "sequence" and "set aside" in m]
    step = [m for m in said if "consecutive rejects" in m]
    loop_end = [m for m in said if "every step still owed" in m]
    earlier = [m for m in said if "set aside earlier tonight" in m]
    assert len(step) == 2 and len(loop_end) == 2 and len(earlier) == 1, (
        f"premise: runs 1 and 3 logged the step line and the loop line, run "
        f"2 the earlier-tonight line: {said}")
    wrong = {m: _wrong(m) for m in step + loop_end + earlier if _wrong(m)}
    assert wrong == {}, (
        f"the set-aside copy says what the engine does not do: {wrong}")
    assert "set aside for tonight" in step[0], step[0]
    assert "Ha" in step[0] and "3 consecutive rejects" in step[0], step[0]
    assert "owed" in step[0] and "ledger" in step[0], step[0]


async def test_the_altitude_floor_line_says_what_the_code_does(
        sim_hub, monkeypatch, bus_lines):
    """The same class on the floor's own line. A target that sinks below its
    floor is dropped from the rotation by `FloorStop` with nothing written to
    the frame ledger, and since S2 the scheduler records it for tonight in
    ``Session.set_aside``: a restart tonight does not take it up, the next
    night does. The line used to say "so it resumes tomorrow" (before S0),
    then "a restart or an auto-resume takes it up again" (S0).

    MUTATION "restore the S0 sentence" (the floor's "setting it aside for
    the rest of this run (its frames stay owed in the ledger, so a restart
    or an auto-resume takes it up again once it is back above its floor)"
    put back). Observed:
        AssertionError: the floor line says what the engine does not do
        (['takes it up again', "missing 'a restart tonight does not retry'",
        "missing 'the next night does'"]): 'M31: sank below its own altitude
        floor — setting it aside for the rest of this run (its frames stay
        owed in the ledger, so a restart or an auto-resume takes it up again
        once it is back above its floor)'

    WORDS ONLY SINCE #233 (H3 T11; H3 orchestrator ruling 1, spec "Still
    waiting on the owner" item 10). The line and the reason used to carry
    the altitude and the floor ("sank to 12.4°, below its 30° floor"): the
    line is served to a viewer by ``/api/logs``, the reason is the
    scheduler's "skipped" line, and a named target's altitude at a logged
    time is the site (#140). This file pins the numbers out. The two-site
    grade of the same line on the clocked simulator is
    test_engine_logs_carry_no_site_numbers.py.

    MUTATION "restore the 'sank to' numbers" (the line and the reason put
    back to ``f"sank to {alt:.1f}°, below its {floor:.0f}° ..."``).
    Observed:
        AssertionError: the set-aside carries numbers a viewer reads: line
        'M31: sank to 12.4°, below its 30° floor — setting it aside for the
        rest of this run and the rest of tonight (its frames stay owed in the
        ledger; a restart tonight does not retry it; the next night does, once
        it is back above its floor)', reason 'sank to 12.4°, below its 30°
        altitude floor'
    """
    t = Target(name="M31", ra_hours=0.712, dec_deg=41.27, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=3)])
    t.schedule = Schedule(min_altitude_deg=30.0, on_floor="advance")
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t])
    monkeypatch.setattr(engine_mod, "_frame_altitude", lambda *a, **k: 12.4)
    with pytest.raises(StopTarget) as stopped:
        await e._enforce_altitude_floor(t)
    lines = [m for _lvl, m, _src in bus_lines if "setting it aside" in m]
    assert len(lines) == 1, bus_lines
    line = lines[0]
    assert _wrong(line) == [], (
        f"the floor line says what the engine does not do "
        f"({_wrong(line)}): {line!r}")
    assert "for the rest of this run and the rest of tonight" in line, line
    reason = str(stopped.value)
    assert not numeric_tokens(line) and not numeric_tokens(reason), (
        f"the set-aside carries numbers a viewer reads: line {line!r}, "
        f"reason {reason!r}")
    assert "M31" in line and "sank below its own altitude floor" in line, line
    assert isinstance(stopped.value, engine_mod.FloorStop), (
        "the floor's stop is not the kind the scheduler records for tonight")


async def test_a_panel_set_aside_says_tonight(group_hub, monkeypatch):
    """The group driver's set-aside line, on the clocked simulator: panel
    2-2 never centres, and the warning that sets it aside names the mosaic,
    the panel, the cause and the last error, and says it is set aside for
    tonight, that a restart tonight does not retry it and that the next
    night does (spec 6.7, 6.8).

    MUTATION "the S0 phrasing on the panel line" (`_set_panel_aside` saying
    "set aside for the rest of this run"). Observed:
        AssertionError: (["missing 'a restart tonight does not retry'",
        "missing 'the next night does'"], 'M31: centring failed on 2-2 on 3
        consecutive visits: plate solve failed — used raw GoTo; set aside for
        the rest of this run')
        assert ('set aside for tonight' in 'M31: centring failed on 2-2 on 3
        consecutive visits: plate solve failed — used raw GoTo; set aside for
        the rest of this run')
    """
    def goto(who, n, result):
        if who == f"{GROUP_NAME} 2-2":
            return {**result, "centered": False, "error_arcmin": None}
        return result

    night = Night(group_hub, monkeypatch, goto=goto)
    try:
        assert await night.run(grid_plan())
    finally:
        await night.close()
    lines = [m for _t, lvl, m in night.lines
             if lvl == "warning" and "set aside" in m]
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.startswith(f"{GROUP_NAME}: centring failed on 2-2 on 3 "
                           f"consecutive visits: "), line
    assert "set aside for tonight" in line and _wrong(line) == [], (
        _wrong(line), line)
