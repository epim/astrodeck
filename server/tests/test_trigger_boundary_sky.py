"""The TARGET-COMPLETE boundary has a sky too.

The engine evaluates instructions at two places: every frame, and once when a
target finishes. The per-frame context has carried the tri-state sky and safety
readings since the sky triggers landed. The target-complete one did not, and
the omission was invisible in every test because a missing tri-state reading is
indistinguishable from an honest "nobody can say".

It is not the same thing. `target_complete` is True at exactly ONE boundary per
target, and that is the only boundary where a rule mentioning it can fire. If
the sky is reported as unknown there, then a compound rule reading "the target
finished AND the sky is clear" is indeterminate every single time it is
evaluated - so it can never fire, on any night, under any sky. Not rarely: never.
The engine knew the sky. It was not asked.

These tests drive the REAL engine through a real sim rig and a real
`CloudState` fed by real frame verdicts, because the defect lives in which
arguments one call site passes. A fake that built the context itself would
reimplement the very thing under test and pass either way.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import (
    ExposureStep, Instruction, SequenceEngine, SequencePlan, Target,
)
from astrodeck.sequence.models import Condition, Predicate


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _clear_sky_capture(engine, monkeypatch, *, cloudy: bool | None = False):
    """Make every captured frame carry a real cloud verdict.

    `cloudy=None` means the frames carry NO verdict at all, which is what a
    stretched frame does on the rig - the contrast metric needs linear pixels.
    That is the control case: the state stays unknown and must stay unknown.

    The key is REMOVED rather than merely not added, and the first draft of this
    helper got that wrong. The sim's capture path supplies its own verdict, so
    "do not add one" left a clear sky in place and the control case fired -
    which read as a broken fix rather than as a broken test.
    """
    inner = engine._capture

    async def _cap(*a, **k):
        info = dict(await inner(*a, **k))
        if cloudy is None:
            info.pop("cloud", None)
        else:
            info["cloud"] = {"cloudy": cloudy, "score": 10.0,
                             "reason": "clear (test)"}
        return info

    monkeypatch.setattr(engine, "_capture", _cap)


def _plan(rule: Instruction, *, frames: int = 3) -> SequencePlan:
    """One target, enough frames for CloudState's two-frame debounce to settle
    before the target finishes."""
    return SequencePlan(
        name="boundary", guide=False, dither_every=0, autofocus_every=0,
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100,
                                count=frames)])],
        instructions=[rule])


def _finished_and_clear() -> Instruction:
    """The rule that could never fire: it needs the target-complete boundary AND
    a definite sky, and only one boundary supplies the first."""
    return Instruction(
        id="done-and-clear", trigger="on_target_complete", action="notify",
        message="target finished under a clear sky",
        when=Condition(op="all", terms=[Predicate(kind="target_complete"),
                                        Predicate(kind="clouds_clear")]))


async def _run(engine, plan, timeout=60.0) -> None:
    engine.start(plan)
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if not engine.running:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"run did not finish: {engine.state}")


def _fired(engine, monkeypatch) -> list[str]:
    """Every action the engine actually dispatched, in order."""
    seen: list[str] = []
    inner = engine._dispatch_actions

    async def _spy(fired, target, step):
        seen.extend(f.action for f in fired)
        return await inner(fired, target, step)

    monkeypatch.setattr(engine, "_dispatch_actions", _spy)
    return seen


async def test_a_rule_needing_the_target_AND_the_sky_can_fire(
        sim_hub, monkeypatch):
    """The regression. Before the fix this fired zero times, because the only
    boundary where `target_complete` is True reported the sky as unknown."""
    eng = SequenceEngine(sim_hub)
    seen = _fired(eng, monkeypatch)
    _clear_sky_capture(eng, monkeypatch, cloudy=False)
    await _run(eng, _plan(_finished_and_clear()))
    assert seen.count("notify") == 1, (
        "a rule reading 'target finished AND sky clear' must fire exactly once, "
        f"at the target-complete boundary; dispatched {seen}")


async def test_it_stays_silent_when_NOBODY_JUDGED_THE_SKY(
        sim_hub, monkeypatch):
    """The other half, and the reason this is not just "pass more arguments".

    With frames that carry no verdict the state is genuinely unknown, and the
    same rule must fire nothing. A fix that hard-coded a sky here would pass the
    test above and turn every unread night into a clear one."""
    eng = SequenceEngine(sim_hub)
    seen = _fired(eng, monkeypatch)
    _clear_sky_capture(eng, monkeypatch, cloudy=None)
    await _run(eng, _plan(_finished_and_clear()))
    assert "notify" not in seen, (
        "an unjudged sky is not a clear one - the rule must not fire; "
        f"dispatched {seen}")


async def test_a_CLOUDY_sky_at_the_boundary_does_not_read_as_clear(
        sim_hub, monkeypatch):
    """The third state. `clouds_clear` is derived by negating the same reading
    `clouds_in` uses, so a definite cloudy sky must decide the rule False rather
    than leave it indeterminate."""
    eng = SequenceEngine(sim_hub)
    seen = _fired(eng, monkeypatch)
    _clear_sky_capture(eng, monkeypatch, cloudy=True)
    await _run(eng, _plan(_finished_and_clear()))
    assert "notify" not in seen, f"dispatched {seen}"
