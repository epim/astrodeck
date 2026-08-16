"""A target that sinks below its floor is set aside, not shot through the trees.

`Schedule.min_altitude_deg` gated SELECTION and nothing else. A target picked at
40 degrees went on being imaged all the way down through its own floor - while
the Tonight page told the operator, in the future indicative, that

    "If the active target sinks to the 30 deg floor, it is set aside - resumed
     the next night, not retried tonight - and the next best takes over."

and the POOL node offered a dial to choose it: "Advance now; retry it next
night" / "Keep imaging (not recommended)". The sentence and the dial were the
entire implementation. `onFloor`'s only consumer in the whole codebase was the
sentence that described it.

That is the worst place for this defect to live: the Tonight page is what an
operator reads BEFORE leaving the rig for the night, and what it promised was
unattended behaviour they would not be awake to check.

THREE THINGS HAVE TO HOLD TOGETHER, and each has its own class below.

1. SUSPENDED, NOT DONE. The floor raises `StopTarget`, which the scheduler
   already handles as "skip this target, keep the night going" - it writes
   NOTHING to the frame ledger. So tomorrow's resume seeds `_done` from frames
   that exist, finds the target short, and shoots the remainder. Marking it
   complete would lose it forever.
2. THE DIAL IS OBEYED. "keep" is the default and is what every saved plan has
   always done. Dropping a target mid-run is not a change to make silently.
3. AN UNREADABLE ALTITUDE IS NOT A FLOOR HIT. `None` means nobody can say, and
   abandoning a target on it would be the same tri-state mistake that let a
   blind cloud probe release a hold.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import StopTarget
from astrodeck.sequence.instructions import TriggerContext, _eval_predicate
from astrodeck.sequence.models import (ExposureStep, Instruction, Schedule,
                                       Target, TriggerKind)


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _target(name="M31", floor=30.0, on_floor="advance", dec=41.27) -> Target:
    t = Target(name=name, ra_hours=0.712, dec_deg=dec, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=3)])
    t.schedule = Schedule(min_altitude_deg=floor, on_floor=on_floor)
    return t


def _engine(hub, target, instructions=None) -> SequenceEngine:
    plan = SequencePlan(targets=[target], instructions=instructions or [])
    e = SequenceEngine(hub)
    e.plan = plan
    return e


class TestTheFloorEndsTheTarget:
    async def test_a_sunk_target_is_stopped(self, sim_hub, monkeypatch):
        """The regression. Before this, nothing consulted the floor once the
        target was running, so this returned quietly and the run carried on."""
        t = _target(floor=30.0)
        monkeypatch.setattr(engine_mod, "_frame_altitude",
                            lambda *a, **k: 12.4)
        with pytest.raises(StopTarget) as ei:
            await _engine(sim_hub, t)._enforce_altitude_floor(t)
        assert "12.4" in str(ei.value) and "30" in str(ei.value), (
            f"the refusal has to name the measurement and the bar: {ei.value}")

    async def test_a_target_still_above_its_floor_is_left_alone(
            self, sim_hub, monkeypatch):
        t = _target(floor=30.0)
        monkeypatch.setattr(engine_mod, "_frame_altitude",
                            lambda *a, **k: 30.1)
        await _engine(sim_hub, t)._enforce_altitude_floor(t)

    async def test_it_raises_the_SAME_exception_the_scheduler_already_catches(
            self, sim_hub, monkeypatch):
        """StopTarget is the whole "suspended, not done" mechanism: the
        scheduler's handler logs a skip, calls `mark_skipped`, drops the target
        from tonight's rotation and writes nothing to the ledger. A new
        exception type here would end the night instead of handing it on."""
        assert issubclass(StopTarget, Exception)
        t = _target()
        monkeypatch.setattr(engine_mod, "_frame_altitude", lambda *a, **k: 1.0)
        with pytest.raises(StopTarget):
            await _engine(sim_hub, t)._enforce_altitude_floor(t)


class TestTheDialIsObeyed:
    async def test_keep_is_the_default_and_keeps_imaging(self, sim_hub,
                                                         monkeypatch):
        """Every plan saved before this change deserializes with on_floor
        absent. If the default were "advance", each of them would start dropping
        targets overnight without anyone asking for it."""
        assert Schedule().on_floor == "keep"
        t = _target(floor=30.0, on_floor="keep")
        monkeypatch.setattr(engine_mod, "_frame_altitude", lambda *a, **k: 2.0)
        await _engine(sim_hub, t)._enforce_altitude_floor(t)

    async def test_a_zero_floor_is_no_gate(self, sim_hub, monkeypatch):
        """0 means "no gate" everywhere else in this model. An altitude is
        always above 0 in the sky the mount can reach, but a negative one is
        not, and reading 0 as a real bar would set aside anything below the
        horizon rather than treating it as unset."""
        t = _target(floor=0.0, on_floor="advance")
        monkeypatch.setattr(engine_mod, "_frame_altitude", lambda *a, **k: -8.0)
        await _engine(sim_hub, t)._enforce_altitude_floor(t)

    async def test_a_calibration_target_has_no_sky_to_sink_from(
            self, sim_hub, monkeypatch):
        t = _target(floor=30.0)
        t.calibration = True
        monkeypatch.setattr(engine_mod, "_frame_altitude", lambda *a, **k: 1.0)
        await _engine(sim_hub, t)._enforce_altitude_floor(t)


class TestAnUnreadableAltitudeIsNotAFloorHit:
    async def test_None_does_not_set_the_target_aside(self, sim_hub,
                                                      monkeypatch):
        """`_frame_altitude` returns None for a missing site key or a bad
        coordinate - "nobody can say", not "below the floor". Treating it as a
        hit would abandon a target on a reading nobody took, which is the
        mistake that let a blind cloud probe call a black frame clear sky."""
        t = _target(floor=30.0)
        monkeypatch.setattr(engine_mod, "_frame_altitude",
                            lambda *a, **k: None)
        await _engine(sim_hub, t)._enforce_altitude_floor(t)


class TestTheTriggerReachesARule:
    def test_the_engine_can_produce_it_before_the_enum_offers_it(self):
        """The order constraint, as a test. `to_plan.LEGAL_TRIGGERS` reads
        `TriggerKind.__args__` directly, so listing a trigger the engine cannot
        raise turns an honest "this rule will not run" into silence."""
        assert "on_altitude_floor" in TriggerKind.__args__
        assert hasattr(SequenceEngine, "_enforce_altitude_floor")

    def test_the_predicate_reads_the_flag(self):
        assert _eval_predicate("altitude_floor", 0.0, None,
                               TriggerContext(now_ts=0.0,
                                              altitude_floor=True)) is True
        assert _eval_predicate("altitude_floor", 0.0, None,
                               TriggerContext(now_ts=0.0)) is False

    async def test_a_wired_notify_fires_with_the_SINKING_target_named(
            self, sim_hub, monkeypatch):
        """The alert has to name the target that sank, not the one that
        replaced it - which is why the rule is evaluated before StopTarget is
        raised rather than from the scheduler's handler."""
        seen: list[TriggerContext] = []

        async def _spy(ctx, target, step):
            seen.append(ctx)

        t = _target(name="NGC 6946", floor=30.0)
        e = _engine(sim_hub, t, [Instruction(trigger="on_altitude_floor",
                                             action="notify")])
        monkeypatch.setattr(engine_mod, "_frame_altitude", lambda *a, **k: 9.0)
        monkeypatch.setattr(e, "_run_instructions", _spy)
        with pytest.raises(StopTarget):
            await e._enforce_altitude_floor(t)
        assert seen, "the floor fired no instruction evaluation at all"
        assert seen[0].altitude_floor is True
        assert seen[0].active_target == "NGC 6946"

    async def test_nothing_is_evaluated_when_the_target_is_high(
            self, sim_hub, monkeypatch):
        seen = []

        async def _spy(ctx, target, step):
            seen.append(ctx)

        t = _target(floor=30.0)
        e = _engine(sim_hub, t, [Instruction(trigger="on_altitude_floor",
                                             action="notify")])
        monkeypatch.setattr(engine_mod, "_frame_altitude", lambda *a, **k: 55.0)
        monkeypatch.setattr(e, "_run_instructions", _spy)
        await e._enforce_altitude_floor(t)
        assert not seen, "a target well above its floor fired the floor rule"


class TestTheRealFrameLoopCallsIt:
    """THE CALL SITE, not the method.

    Every other test in this file invokes `_enforce_altitude_floor` directly, so
    all of them would still pass with the call deleted from `_run_steps` - a
    perfect implementation wired to nothing, which is the defect this whole
    ticket is about. This one drives the actual engine and watches a target get
    set aside while the next one takes the night.
    """

    async def test_a_sinking_target_hands_the_night_to_the_next_one(
            self, sim_hub, monkeypatch):
        sunk = _target(name="SINKING", floor=30.0, on_floor="advance")
        rises = _target(name="STAYSUP", floor=30.0, on_floor="advance")

        def _alt(target, site, when):
            return 5.0 if getattr(target, "name", "") == "SINKING" else 61.0

        monkeypatch.setattr(engine_mod, "_frame_altitude", _alt)
        # Both targets are always selectable: this test is about what the FRAME
        # LOOP does once a target is running, not about window resolution, and
        # leaving the real gate in made it a clock-dependent coin flip.
        monkeypatch.setattr(
            engine_mod.schedule, "gating_status",
            lambda target, site, twilight, now, window=None: {"state": "ready"})

        e = SequenceEngine(sim_hub)
        plan = SequencePlan(targets=[sunk, rises])
        e.start(plan)
        # Generous on purpose: with the floor check REMOVED both targets shoot
        # their full quota, which is roughly double the work, and a deadline
        # tight enough to trip on that turns a semantic failure into a
        # timeout - the assertion below should be what fails, and it should
        # say which target shot through its floor.
        deadline = time.monotonic() + 180.0
        while e.running and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not e.running, "the run never finished"

        # `_done` is keyed "<target id>:<step id>", not by name - the ids are
        # what survive a resume, where two targets may share a label.
        done = e._done if isinstance(getattr(e, "_done", None), dict) else {}

        def _frames(t):
            return sum(v for k, v in done.items()
                       if str(k).split(":")[0] == str(t.id))

        # THE PRECONDITION, ASSERTED FIRST. Everything below is vacuous if no
        # frame was ever taken, and that is exactly the state this test spent a
        # day in without saying so.
        assert done, (
            "the run finished without entering the frame loop at all, so this "
            "test is asserting nothing about the call site")
        assert _frames(rises) >= 3, (
            f"the target that stayed up did not get its frames: {done}")
        assert _frames(sunk) < 3, (
            f"the sinking target shot its whole quota straight through its own "
            f"floor - the frame loop is not calling the check: {done}")


class TestTheCampaignFlowNowCarriesIt:
    """End to end from the shipped graph, because the dial reaching the plan is
    a separate promise from the engine obeying it - and `onFloor` spent its
    whole life reaching a paragraph and nothing else."""

    def _campaign(self):
        from astrodeck.flows import examples as ex
        from astrodeck.flows.compile import compile_plan
        from astrodeck.flows.to_plan import to_sequence_plan
        rec = [f for f in ex.examples() if f.id == "example-campaign"][0]
        return to_sequence_plan(compile_plan(rec.graph, rec.name), rec.graph)

    def test_every_pool_member_carries_the_dial(self):
        plan, _ = self._campaign()
        assert plan.targets, "the campaign compiled no targets"
        for t in plan.targets:
            assert t.schedule.on_floor == "advance", (
                f"{t.name} lost the pool's onFloor dial between the node and "
                f"the plan, so the engine cannot obey it")
            assert t.schedule.min_altitude_deg > 0, (
                f"{t.name} has no floor for the policy to act on")

    def test_the_floor_rule_is_no_longer_reported_as_dead(self):
        plan, unmapped = self._campaign()
        assert any(i.trigger == "on_altitude_floor" for i in plan.instructions), (
            "the campaign's floor alert still does not reach the plan")
        dead = [u for u in unmapped if "on_altitude_floor" in u["key"]]
        assert not dead, f"still reported as unmapped: {dead}"
