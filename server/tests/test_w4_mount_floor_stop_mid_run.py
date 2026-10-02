# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#604 (backlog shape c; orchestrator ruling 2026-10-01): mid-run, a
destination below the mount's own floor or horizon mask used to raise
SafetyAbort through the slew gate and end the WHOLE night, whether or not
another target in the plan could still be shot before dawn. "A multi-target
night should not lose its remaining targets to one that has set."

Now, at exactly one place -- the meridian flip's own pre-flip gate
(`SequenceEngine._flip_safety_gate`, factored out of `_maybe_meridian_flip`
so this is independently callable) -- a "floor" refusal for a SINGLE
(non-mosaic) target ends only that target (:class:`MountFloorStop`, a
:class:`StopTarget`) when `_another_target_reachable` finds something else
still worth trying, and ends the night exactly as before when it does not.
Every other refusal kind (pier, sun, ceiling), and every refusal for a
mosaic panel, is unchanged -- #604 was filed on two plain targets, not a
zenith crossing, the Sun, or a mosaic.

Real sim hub (test_resume_arm.py's precedent); the slew gate itself and the
mount's own geometry read are stubbed per test, since what is under test is
the DECISION made once a refusal has already happened, never the refusal's
own astronomy (that is `_mount_floor_verdict`'s, exercised elsewhere).
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.config import config_store
from astrodeck.events import bus
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import (MountFloorStop, ReachVerdict,
                                       SlewRefused, StopTarget)
from astrodeck.sequence.models import ExposureStep, Target, TargetGroup
from astrodeck.sequence.session import Session


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _target(name: str, **kw) -> Target:
    base = dict(name=name, ra_hours=5.0, dec_deg=10.0,
               steps=[ExposureStep(filter="L", exposure_s=1.0, count=5)])
    base.update(kw)
    return Target(**base)


def _rigged(engine: SequenceEngine, plan: SequencePlan, *,
           frames_done: dict[str, int] | None = None) -> None:
    """The minimum state `_another_target_reachable` and `_flip_safety_gate`
    read: a plan, a session (whose `remaining()` is the real definition
    `owed()` and the API layer use), and a config snapshot -- without the
    rest of a real `engine.start()` (no task, no report, no site)."""
    engine.plan = plan
    session = Session(plan=plan)
    if frames_done:
        session.frames = []
        for t in plan.targets:
            for s in t.steps:
                for _ in range(frames_done.get(s.id, 0)):
                    from astrodeck.sequence.session import SessionFrame
                    session.frames.append(SessionFrame(
                        target_id=t.id, step_id=s.id))
    engine._session = session
    engine._cfg = config_store.cfg()


def _warnings(*fragments: str) -> list[dict]:
    out = []
    for e in bus.log_history:
        if e["data"].get("level") != "warning":
            continue
        msg = e["data"].get("message", "")
        if all(f in msg for f in fragments):
            out.append(e)
    return out


# ------------------------------------------------- _another_target_reachable

async def test_finds_the_other_target_with_work_and_sky_left(sim_hub, monkeypatch):
    a = _target("A")
    b = _target("B")
    plan = SequencePlan(name="two", guide=False, targets=[a, b])
    engine = SequenceEngine(sim_hub)
    _rigged(engine, plan)

    async def verdict(t, projected=True, **kw):
        return None if t is b else ReachVerdict(
            "wait", "floor", "x", "x", None)
    monkeypatch.setattr(engine, "_mount_floor_verdict", verdict)

    found = await engine._another_target_reachable(a)
    assert found is b


async def test_a_finished_target_does_not_count(sim_hub, monkeypatch):
    """B is geometrically reachable, but every frame it owes is already
    banked -- #604's question is whether anything is still WORTH shooting,
    and a complete target is not.

    NAMED MUTANT (run from a byte backup, restored and sha256-verified):
    in ``_another_target_reachable`` (engine.py), change
    ``if sum(owed.get(s.id, 0) for s in t.steps) <= 0:`` to ``if False:``.
    This test then fails with:

        AssertionError: assert Target(id='...', name='B', ...) is None

    -- a finished target is offered as the "something else" worth ending
    the current one for."""
    a = _target("A")
    b = _target("B", steps=[ExposureStep(filter="L", exposure_s=1.0, count=2)])
    plan = SequencePlan(name="two", guide=False, targets=[a, b])
    engine = SequenceEngine(sim_hub)
    _rigged(engine, plan, frames_done={b.steps[0].id: 2})

    async def verdict(t, projected=True, **kw):
        return None            # everything "reachable" by geometry
    monkeypatch.setattr(engine, "_mount_floor_verdict", verdict)

    assert await engine._another_target_reachable(a) is None


async def test_nothing_reachable_when_the_only_other_target_also_refuses(
        sim_hub, monkeypatch):
    a = _target("A")
    b = _target("B")
    plan = SequencePlan(name="two", guide=False, targets=[a, b])
    engine = SequenceEngine(sim_hub)
    _rigged(engine, plan)

    async def verdict(t, projected=True, **kw):
        return ReachVerdict("refuse", "pier", "x", "x", None)
    monkeypatch.setattr(engine, "_mount_floor_verdict", verdict)

    assert await engine._another_target_reachable(a) is None


async def test_no_other_target_in_a_single_target_plan(sim_hub):
    a = _target("A")
    plan = SequencePlan(name="solo", guide=False, targets=[a])
    engine = SequenceEngine(sim_hub)
    _rigged(engine, plan)

    assert await engine._another_target_reachable(a) is None


# ------------------------------------------------------- _flip_safety_gate

async def test_flip_safety_gate_ends_only_this_target_when_another_can_be_shot(
        sim_hub, monkeypatch):
    """NAMED MUTANT, the orchestrator's own (run from a byte backup,
    restored and sha256-verified), "always SafetyAbort": in
    ``_flip_safety_gate`` (engine.py), change ``if other is None:`` to
    ``if True:`` (keeping the ``other = await
    self._another_target_reachable(target)`` call above it, so the mutant
    is the DECISION alone). This test then fails with the original
    ``SlewRefused`` propagating instead of ``MountFloorStop``:

        astrodeck.sequence.engine.SlewRefused: A is below the floor

    -- exactly the ruling's point: without this branch, a multi-target
    night always loses its remaining targets to the one that set."""
    a = _target("A")
    b = _target("B")
    plan = SequencePlan(name="two", guide=False, targets=[a, b])
    engine = SequenceEngine(sim_hub)
    _rigged(engine, plan)

    async def refuse(*, context, target):
        raise SlewRefused("A is below the floor", words="A has set",
                          kind="floor")
    monkeypatch.setattr(engine, "_safety_gate", refuse)

    async def reachable(exclude):
        return b
    monkeypatch.setattr(engine, "_another_target_reachable", reachable)

    with pytest.raises(MountFloorStop) as exc:
        await engine._flip_safety_gate(a)
    assert isinstance(exc.value, StopTarget)
    assert "A" in str(exc.value) and "B" in str(exc.value)
    hits = _warnings("A", "B", "floor")
    assert len(hits) == 1, bus.log_history[-5:]


async def test_flip_safety_gate_ends_the_night_when_nothing_else_can_be_shot(
        sim_hub, monkeypatch):
    """The single-target case, and the regression guard: today's behaviour
    (SafetyAbort, via SlewRefused) is EXACTLY unchanged when
    `_another_target_reachable` finds nothing."""
    a = _target("A")
    plan = SequencePlan(name="solo", guide=False, targets=[a])
    engine = SequenceEngine(sim_hub)
    _rigged(engine, plan)

    refusal = SlewRefused("A is below the floor", words="A has set",
                          kind="floor")
    async def refuse(*, context, target):
        raise refusal
    monkeypatch.setattr(engine, "_safety_gate", refuse)

    async def nothing_else(exclude):
        return None
    monkeypatch.setattr(engine, "_another_target_reachable", nothing_else)

    with pytest.raises(SlewRefused) as exc:
        await engine._flip_safety_gate(a)
    assert exc.value is refusal     # the ORIGINAL exception, not a new one
    assert not isinstance(exc.value, MountFloorStop)


async def test_flip_safety_gate_never_converts_a_mosaic_panels_refusal(
        sim_hub, monkeypatch):
    """A panel's own floor/ceiling reach-wait is `_visit_panel`'s question,
    asked every pass; this gate must not give it a second, disagreeing
    answer. `_another_target_reachable` is not even asked.

    NAMED MUTANT (run from a byte backup, restored and sha256-verified):
    in ``_flip_safety_gate`` (engine.py), change
    ``if e.kind == "floor" and self._group_of(target) is None:`` to
    ``if e.kind == "floor":``. This test then fails with the panel's
    refusal converted anyway:

        astrodeck.sequence.engine.MountFloorStop: panel-1-1: below the
        mount's floor/horizon at its meridian flip, ending it for B

    -- a mosaic panel would get a second, disagreeing answer about its own
    floor reach-wait, the one `_visit_panel` already owns."""
    group = TargetGroup(id="g1", name="mosaic")
    panel = _target("panel-1-1", mosaic_group="g1")
    other = _target("B")
    plan = SequencePlan(name="mosaic-plus-one", guide=False,
                        targets=[panel, other], groups=[group])
    engine = SequenceEngine(sim_hub)
    _rigged(engine, plan)

    refusal = SlewRefused("panel is below the floor", words="panel has set",
                          kind="floor")
    async def refuse(*, context, target):
        raise refusal
    monkeypatch.setattr(engine, "_safety_gate", refuse)

    asked = {"n": 0}
    async def reachable(exclude):
        asked["n"] += 1
        return other
    monkeypatch.setattr(engine, "_another_target_reachable", reachable)

    with pytest.raises(SlewRefused) as exc:
        await engine._flip_safety_gate(panel)
    assert exc.value is refusal
    assert asked["n"] == 0


@pytest.mark.parametrize("kind", ["pier", "sun", "ceiling", None])
async def test_flip_safety_gate_only_converts_the_floor_kind(
        sim_hub, monkeypatch, kind):
    """#604 is a target that SET -- the floor. The pier guard, the Sun's
    cone and the zenith keep-out are different hazards the issue never
    asked about, and a kind-less refusal (#313) is the "no site"/dead-link
    shape nothing here should guess about either."""
    a = _target("A")
    b = _target("B")
    plan = SequencePlan(name="two", guide=False, targets=[a, b])
    engine = SequenceEngine(sim_hub)
    _rigged(engine, plan)

    refusal = SlewRefused("refused", words="refused", kind=kind)
    async def refuse(*, context, target):
        raise refusal
    monkeypatch.setattr(engine, "_safety_gate", refuse)

    asked = {"n": 0}
    async def reachable(exclude):
        asked["n"] += 1
        return b
    monkeypatch.setattr(engine, "_another_target_reachable", reachable)

    with pytest.raises(SlewRefused) as exc:
        await engine._flip_safety_gate(a)
    assert exc.value is refusal
    assert asked["n"] == 0


async def test_flip_safety_gate_is_silent_when_the_slew_is_not_refused(
        sim_hub, monkeypatch):
    a = _target("A")
    engine = SequenceEngine(sim_hub)
    calls = {"n": 0}
    async def ok(*, context, target):
        calls["n"] += 1
    monkeypatch.setattr(engine, "_safety_gate", ok)
    await engine._flip_safety_gate(a)
    assert calls["n"] == 1
