"""A set-aside lasts for the rest of THIS run, and the log has to say so (#147).

S0 made the per-step reject guard set a step aside in engine memory only:
`_set_aside` is cleared at run start and is not persisted until S2 writes it to
`Session.set_aside`. The sentences logged for it promised more than that - its
frames "stay owed in the ledger for another night", the ledger "keeps them owed
for another night" - and the altitude floor's line said "so it resumes
tomorrow". But a restart or an auto-resume the SAME night starts a new run, the
new run clears the set-aside, and the step is back on the shutter. An operator
who reads "another night" at 1 a.m. does not expect the filter that just failed
ten times in a row to be shooting again at 2 a.m.

So the copy now says what the code does: set aside for the rest of this run,
still owed in the ledger, and tried again by a restart or an auto-resume. The
first case holds the sentence and the behaviour together. It trips the guard,
starts the SAME session again on the SAME engine, and checks both that the step
is attempted again and that nothing either run logged about the set-aside
promises a later night.

The harness is the real `_run_step` on the simulator hub, with the quality
verdict scripted per filter, as in test_accepted_visit_bound.py (whose module
docstring says why the filter wheel is taken off the hub).
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

FILTERS = ("B", "Ha", "OIII")
TERMINAL = ("complete", "aborted", "error")
#: What the copy used to promise and the engine never kept.
LATER_NIGHT = ("another night", "later night", "tomorrow")


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
    assert not eng.running, "the first run never finished winding down"


def _promises(line: str) -> list[str]:
    return [p for p in LATER_NIGHT if p in line.lower()]


async def test_a_restart_the_same_night_tries_the_step_again_and_the_log_says_so(
        sim_hub, monkeypatch, bus_lines):
    """Run 1: Ha rejects three times and is set aside; B and OIII complete, so
    the visit loop ends on "every step still owed is set aside". Run 2 starts
    the SAME session on the SAME engine, which is what a restart or an
    auto-resume later that night does: Ha is on the shutter again. Every
    set-aside sentence from both runs must say exactly that, and none may
    promise another night.

    MUTATION "restore the old sentence", run on each line separately.
    The `_set_step_aside` line put back ("set aside for tonight after ... stay
    owed in the ledger for another night"). Observed:
        AssertionError: the set-aside copy promises a later night the engine
        does not keep: ['M42: Ha set aside for tonight after 3 consecutive
        rejects — its 3 frame(s) stay owed in the ledger for another night',
        'M42: Ha set aside for tonight after 3 consecutive rejects — its 3
        frame(s) stay owed in the ledger for another night']
    The `_run_steps` line put back ("... the ledger keeps them owed for
    another night"). Observed:
        AssertionError: the set-aside copy promises a later night the engine
        does not keep: ['M42: every step still owed is set aside for tonight
        (Ha) — moving on; the ledger keeps them owed for another night',
        'M42: every step still owed is set aside for tonight (Ha) — moving
        on; the ledger keeps them owed for another night']
    """
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
    again = [x for x in shots[mark:] if x[0] == "Ha"]
    assert again == [("Ha", False)] * 3, (
        f"the next run on the same session did not try Ha again, so 'a "
        f"restart or an auto-resume tries it again' would be false: "
        f"{shots[mark:]}")

    said = [m for _lvl, m, src in bus_lines
            if src == "sequence" and "set aside" in m]
    step = [m for m in said if "consecutive rejects" in m]
    loop_end = [m for m in said if "every step still owed" in m]
    assert len(step) == 2 and len(loop_end) == 2, (
        f"premise: each run logged the step line and the loop line: {said}")
    promised = [m for m in step + loop_end if _promises(m)]
    assert promised == [], (
        f"the set-aside copy promises a later night the engine does not "
        f"keep: {promised}")
    for m in step + loop_end:
        assert "for the rest of this run" in m, m
        assert "owed" in m and "ledger" in m, m
        assert "a restart or an auto-resume" in m, m
    assert "Ha" in step[0] and "3 consecutive rejects" in step[0], step[0]


async def test_the_altitude_floor_line_says_what_the_code_does(
        sim_hub, monkeypatch, bus_lines):
    """The same class on the floor's own line. A target that sinks below its
    floor is dropped from THIS run's rotation by `StopTarget`, with nothing
    written to the ledger, so the next run - a restart or an auto-resume, not
    only tomorrow's - takes it up again once it is back above the floor. The
    line used to say "so it resumes tomorrow".

    MUTATION "restore the old sentence" (the floor's "setting it aside for
    tonight (its frames stay in the ledger, so it resumes tomorrow)" put
    back). Observed:
        AssertionError: the floor line promises a later night the engine does
        not keep (tomorrow): 'M31: sank to 12.4°, below its 30° floor —
        setting it aside for tonight (its frames stay in the ledger, so it
        resumes tomorrow)'
    """
    t = Target(name="M31", ra_hours=0.712, dec_deg=41.27, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=3)])
    t.schedule = Schedule(min_altitude_deg=30.0, on_floor="advance")
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t])
    monkeypatch.setattr(engine_mod, "_frame_altitude", lambda *a, **k: 12.4)
    with pytest.raises(StopTarget):
        await e._enforce_altitude_floor(t)
    lines = [m for _lvl, m, _src in bus_lines if "sank to" in m]
    assert len(lines) == 1, bus_lines
    line = lines[0]
    assert not _promises(line), (
        f"the floor line promises a later night the engine does not keep "
        f"({', '.join(_promises(line))}): {line!r}")
    assert "for the rest of this run" in line, line
    assert "a restart or an auto-resume" in line, line
    assert "12.4" in line and "30" in line, line
