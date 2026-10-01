"""One definition of done: `_target_complete` asks `_step_complete` (#158).

The scheduler skips a target as "already complete" on `_target_complete`; the
cycle driver finishes a target on `_step_complete`. They used to be two
definitions. `_target_complete` compared the SUM of the engine's `_done` map
with the sum of the counts, while `_step_complete` asks the ledger in accepted
mode. They agreed only by construction: a regrade is refused during a run and
`_done` is re-seeded from the ledger at start. Any divergence, or ONE step
over its count hiding another step under it, made the scheduler skip a target
the session still owed.

The fold keeps the `total > 0` guard on purpose. A target with no steps, or
with only zero-count steps, has never been "already complete", and a bare
`all()` over an empty list would make it so.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.session import Session, SessionFrame, session_store

TERMINAL = ("complete", "aborted", "error")


class _Hub:
    """Enough of a hub to construct an engine for the unit cases."""
    def __init__(self):
        self.devices = {}
        self.guider = None
        self.site = {}
        self.master_library = None


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _target(counts=(2, 2)) -> Target:
    return Target(name="NGC 7331", ra_hours=22.62, dec_deg=34.42,
                  center=False, autofocus_first=False,
                  steps=[ExposureStep(filter=f, exposure_s=0.05, count=c)
                         for f, c in zip(("L", "R"), counts)])


def _plan(target: Target, count_mode="accepted") -> SequencePlan:
    return SequencePlan(name="done", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        count_mode=count_mode, max_consecutive_rejects=10,
                        targets=[target])


def _ledger(plan: SequencePlan, accepted: dict[str, int]) -> Session:
    """A session whose ledger holds ``accepted[step_id]`` accepted frames."""
    t = plan.targets[0]
    frames = [SessionFrame(ts=float(i), target_id=t.id, step_id=sid,
                           auto_accepted=True)
              for sid, n in accepted.items() for i in range(n)]
    return Session(name=plan.name, plan=plan, frames=frames,
                   status="dormant", created_ts=1.0)


def _unit_engine(plan: SequencePlan, session: Session | None,
                 done: dict[str, int]) -> SequenceEngine:
    eng = SequenceEngine(_Hub())
    eng.plan = plan
    eng._session = session
    eng._done = dict(done)
    return eng


class TestTheSchedulerAsksTheSameQuestion:
    async def test_a_step_over_its_count_does_not_hide_one_under_it(
            self, sim_hub, monkeypatch, bus_lines):
        """Accepted mode, steps A (L) and B (R), count 2 each. `_done` says A 3
        (over its count) and B 1 (one short); the ledger agrees B is one short.
        The SUM of `_done` is 4 of 4 and read "complete"; B still owes a frame.

        The assertion is on SELECTION, so `_setup_target` and `_run_steps` are
        stubbed to record their calls; nothing here needs a frame shot.

        MUTATION "sum of `_done`" (the old `_target_complete`). Observed:
            AssertionError: the scheduler skipped a target the ledger still
            owes: setups [], lines ['NGC 7331: already complete — skipping']
        """
        t = _target((2, 2))
        plan = _plan(t)
        a, b = t.steps
        session = _ledger(plan, {a.id: 2, b.id: 1})
        session_store.save(session)
        calls: list[tuple[str, str]] = []

        async def _setup(ti, target):
            calls.append(("setup", target.name))

        async def _steps(ti, target):
            calls.append(("steps", target.name))

        eng = SequenceEngine(sim_hub)
        monkeypatch.setattr(eng, "_setup_target", _setup)
        monkeypatch.setattr(eng, "_run_steps", _steps)
        eng.start(plan, session=session)
        # start() is synchronous and its task has not had a turn yet: seed the
        # divergence the ledger cannot express (done_map caps at the count).
        eng._done[f"{t.id}:{a.id}"] = 3
        eng._done[f"{t.id}:{b.id}"] = 1
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 30
        while eng.state.get("state") not in TERMINAL and loop.time() < deadline:
            await asyncio.sleep(0.05)
        if eng.state.get("state") not in TERMINAL:
            await eng.abort()
            pytest.fail(f"the run never ended: {eng.state}")

        skipped = [m for _, m, _ in bus_lines if "already complete" in m]
        assert calls == [("setup", t.name), ("steps", t.name)] and not skipped, (
            f"the scheduler skipped a target the ledger still owes: "
            f"setups {calls}, lines {skipped}")

    async def test_the_control_a_finished_target_is_still_skipped(
            self, sim_hub, monkeypatch, bus_lines):
        """Every step complete in both `_done` and the ledger: the scheduler
        says "already complete" and never slews. Without this, a
        `_target_complete` that always answered False would pass the case
        above, and would re-slew to finished targets all night.

        MUTATION "`_target_complete` always False". Observed:
            AssertionError: a finished target was set up again: [('setup',
            'NGC 7331'), ('steps', 'NGC 7331')]
        """
        t = _target((2, 2))
        plan = _plan(t)
        a, b = t.steps
        session = _ledger(plan, {a.id: 2, b.id: 2})
        session_store.save(session)
        calls: list[tuple[str, str]] = []

        async def _setup(ti, target):
            calls.append(("setup", target.name))

        async def _steps(ti, target):
            calls.append(("steps", target.name))

        eng = SequenceEngine(sim_hub)
        monkeypatch.setattr(eng, "_setup_target", _setup)
        monkeypatch.setattr(eng, "_run_steps", _steps)
        eng.start(plan, session=session)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 30
        while eng.state.get("state") not in TERMINAL and loop.time() < deadline:
            await asyncio.sleep(0.05)
        if eng.state.get("state") not in TERMINAL:
            await eng.abort()
            pytest.fail(f"the run never ended: {eng.state}")

        assert calls == [], f"a finished target was set up again: {calls}"
        assert [m for _, m, _ in bus_lines if "already complete" in m]


class TestTheTwoAnswersAgree:
    def test_a_frame_regraded_to_rejected_makes_both_say_not_done(self):
        """Accepted mode, one step of 3, three accepted frames: both say done
        (the control). Regrade one to rejected: the ledger now holds 2, and
        both must say not done. `_done` still says 3, which is exactly the
        divergence a regrade produces.

        MUTATION "sum of `_done`". Observed:
            AssertionError: after a regrade _step_complete says False and
            _target_complete says True
        """
        t = Target(name="M33", ra_hours=1.56, dec_deg=30.66,
                   steps=[ExposureStep(filter="L", exposure_s=60, count=3)])
        plan = _plan(t)
        step = t.steps[0]
        session = _ledger(plan, {step.id: 3})
        eng = _unit_engine(plan, session, {f"{t.id}:{step.id}": 3})
        assert eng._step_complete(t, step) is True
        assert eng._target_complete(0, t) is True

        session.frames[0].override = "reject"
        step_says = eng._step_complete(t, step)
        target_says = eng._target_complete(0, t)
        assert step_says is False, "the ledger regrade did not reach _step_complete"
        assert target_says == step_says, (
            f"after a regrade _step_complete says {step_says} and "
            f"_target_complete says {target_says}")

    def test_attempts_mode_one_step_over_does_not_mask_one_under(self):
        """The masking in attempts mode, where `_step_complete` reads `_done`
        per step: A 3 of 2, B 1 of 2 is not done.

        MUTATION "sum of `_done`". Observed:
            AssertionError: 3 of 2 plus 1 of 2 read as a finished target
        """
        t = _target((2, 2))
        plan = _plan(t, count_mode="attempts")
        a, b = t.steps
        eng = _unit_engine(plan, None, {f"{t.id}:{a.id}": 3,
                                        f"{t.id}:{b.id}": 1})
        assert eng._target_complete(0, t) is False, (
            "3 of 2 plus 1 of 2 read as a finished target")
        eng._done[f"{t.id}:{b.id}"] = 2
        assert eng._target_complete(0, t) is True     # the control

    @pytest.mark.parametrize("count_mode", ["accepted", "attempts"])
    def test_a_target_with_no_steps_is_not_complete(self, count_mode):
        """`all()` over nothing is True. A stepless target was never "already
        complete" and must not become so in the fold.

        MUTATION "vacuous `all()`" (drop the `total > 0` guard). Observed:
            AssertionError: a target with no steps reads as complete
        """
        t = Target(name="empty", ra_hours=1.0, dec_deg=1.0, steps=[])
        plan = _plan(t, count_mode=count_mode)
        eng = _unit_engine(plan, _ledger(plan, {}), {})
        assert eng._target_complete(0, t) is False, (
            "a target with no steps reads as complete")

    @pytest.mark.parametrize("count_mode", ["accepted", "attempts"])
    def test_a_target_of_zero_count_steps_is_not_complete(self, count_mode):
        """The model refuses count 0 at validation, but a step can still be
        assigned one; every such step is vacuously complete on its own, so
        only the `total > 0` guard keeps the target from reading done.

        MUTATION "vacuous `all()`". Observed:
            AssertionError: a target of zero-count steps reads as complete
        """
        t = _target((2, 2))
        for s in t.steps:
            s.count = 0
        plan = _plan(t, count_mode=count_mode)
        eng = _unit_engine(plan, _ledger(plan, {}), {})
        assert eng._target_complete(0, t) is False, (
            "a target of zero-count steps reads as complete")
