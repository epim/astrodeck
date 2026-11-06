# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1094 (WP 3.10, owner ruling D-D): a per-target time bound, and the queue
handed on at every PLANNED ending.

THE RULING. Two ways to run "this, then that": one flow with both targets in
series, or two flows queued back to back (#598). In both, a target moves on by
frame count or by maximum time, whichever comes first. A planned ending (the
time bound, the stop time, ``max_run_min``, the window closing) promotes the
queued flow; an abort or a fault does not, and the log names the queued
session when a run ends without promoting it.

WHAT THESE TESTS HOLD.

* ``Target.max_time_min`` (0 = none) ends a target at that many minutes even
  with frames owed, on the clocked simulator, and the run goes on to the next
  target of the series. A count met first ends it as it always did.
* The bound is a night's, not a loop's: the target is set aside for tonight
  as it ends, so ResumeArm's same-night tick holds instead of shooting
  another full bound.
* Each planned ending arms the session queued behind it, and every other
  ending leaves it queued and unarmed and says so in the log.
* The doctor notes a target with neither bound under an unbounded stop.

NAMED MUTANTS (each run from a byte backup in the scratch directory, restored
and sha256-compared; the failing assertion is quoted in the test that catches
it):

* "promote on abort too": the planned-ending test in `_finalize_report` also
  accepts an ``aborted`` reason;
* "drop the time bound": `_target_complete` and the frame-boundary check stop
  reading ``max_time_min``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from _group_harness import Night, group_hub, group_store, single  # noqa: F401
from astrodeck.api.app import FlowWizardBody
from astrodeck.flows import RigFacts, check
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import GraphNotRunnable, to_sequence_plan
from astrodeck.flows.wizard import generate_answer
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target, quota_unbounded
from astrodeck.sequence.session import Session, SessionFrame, session_store
from test_trigger_frame_banked import _plan


def _lines(bus_lines) -> list[str]:
    return [m for _lvl, m, _src in bus_lines]


def _queued_plan(name: str) -> SequencePlan:
    return SequencePlan(
        name=name, guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name=name, ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=3)])])


def _stored(name: str, plan: SequencePlan, *, auto_resume: bool = False,
            queued_behind: str | None = None, done: int = 0) -> Session:
    s = Session(name=name, created_ts=1.0, status="dormant", plan=plan,
                auto_resume=auto_resume, queued_behind=queued_behind)
    target = plan.targets[0]
    for _ in range(done):
        s.frames.append(SessionFrame(ts=1.0, night="n1", target_id=target.id,
                                     step_id=target.steps[0].id,
                                     path="f.fits", metrics={},
                                     auto_accepted=True))
    session_store.save(s)
    return s


# ------------------------------------------------------------------- the model

def test_a_target_has_no_time_bound_unless_it_is_given_one():
    base = dict(name="A", ra_hours=1.0, dec_deg=10.0)
    assert Target(**base).max_time_min == 0
    assert Target(**base, max_time_min=60).max_time_min == 60
    with pytest.raises(ValueError):
        Target(**base, max_time_min=-1)


def test_the_bound_is_kept_by_a_plan_round_trip_and_absent_from_the_dump_at_zero():
    """A plan with a bound keeps it through a dump and a reload (a stored
    session reads back the same), and a target without one dumps exactly as
    before the field existed.

    MUTANT "the serializer drops the bound" (``_omit_no_time_bound`` popping
    the key whatever it holds) turns the first assertion red."""
    plan = _queued_plan("a")
    bounded = plan.model_copy(update={"targets": [
        plan.targets[0].model_copy(update={"max_time_min": 90})]})
    dumped = bounded.model_dump(mode="json")
    assert dumped["targets"][0]["max_time_min"] == 90
    assert SequencePlan.model_validate(dumped).targets[0].max_time_min == 90
    assert "max_time_min" not in plan.model_dump(mode="json")["targets"][0]


# ------------------------------------------------------------- the time bound

async def test_a_target_bounded_at_sixty_minutes_ends_at_sixty_with_frames_owed(
        group_hub, group_store, monkeypatch):
    """THE ISSUE'S OWN SHAPE: "grab some more of this for the next hour". A
    300-frame target of 30 s frames, bounded at 60 minutes, on the clocked
    simulator. It must stop taking frames at the hour, with most of its
    frames owed, and the run must end.

    MUTANT "drop the time bound" (`_target_complete` and the frame-boundary
    check no longer read ``max_time_min``) turned this red, run from a byte
    backup and restored and sha256-verified afterwards:

        AssertionError: the bound must end the target with frames owed
        assert 300 < 300
    """
    a = single("A", count=300, max_time_min=60)
    night = Night(group_hub, monkeypatch)
    try:
        night.done = await night.run(_plan(a), wall_s=120.0)
    finally:
        await night.close()
    assert night.done, night.lines[-6:]
    shots = [c["t"] for c in night.captures if c["target"] == "A"]
    assert len(shots) < 300, "the bound must end the target with frames owed"
    assert len(shots) >= 100, f"it shot for most of the hour: {len(shots)}"
    assert max(shots) - night.t0 < 60 * 60, (
        "no frame may START past the bound")
    assert night.said("time bound"), night.lines[-8:]


async def test_the_series_goes_on_to_the_next_target_after_the_bound(
        group_hub, group_store, monkeypatch):
    """One flow, both targets in series: A is bounded at ten minutes with
    frames owed, B follows and shoots its own count. The bound hands the
    night on, it does not end it."""
    a = single("A", count=300, max_time_min=10)
    b = single("B", count=2)
    night = Night(group_hub, monkeypatch)
    try:
        night.done = await night.run(_plan(a, b), wall_s=120.0)
    finally:
        await night.close()
    assert night.done, night.lines[-6:]
    shots = night.shots()
    assert [t for t, _f in shots].count("B") == 2, shots[-4:]
    assert [t for t, _f in shots].count("A") < 300
    assert shots[-1][0] == "B", "B follows A"


async def test_a_count_met_before_the_bound_ends_the_target_as_it_always_did(
        group_hub, group_store, monkeypatch):
    """Whichever comes first: three frames of 30 s are done long before a
    600-minute bound, and the session completes."""
    a = single("A", count=3, max_time_min=600)
    night = Night(group_hub, monkeypatch)
    try:
        night.done = await night.run(_plan(a), wall_s=60.0)
    finally:
        await night.close()
    assert night.done
    assert night.shots() == [("A", "L")] * 3
    assert session_store.load(night.session_id).status == "complete"
    assert not night.said("time bound"), night.lines[-6:]


async def test_a_bounded_target_is_set_aside_for_tonight_so_no_tick_restarts_it(
        group_hub, group_store, monkeypatch):
    """The session ends dormant with frames owed. Without a record, the
    same-night ResumeArm tick would resume it and the bound would start
    again from zero, all night. The target is set aside for tonight, the
    record every 'tried tonight, not again' is."""
    a = single("A", count=300, max_time_min=10)
    night = Night(group_hub, monkeypatch)
    try:
        night.done = await night.run(_plan(a), wall_s=120.0)
    finally:
        await night.close()
    stored = session_store.load(night.session_id)
    assert stored.status == "dormant"
    kinds = [r.get("kind") for r in stored.set_aside
             if r.get("target_id") == "a"]
    assert kinds == ["time_bound"], stored.set_aside


async def test_no_bound_means_no_time_limit(group_hub, group_store,
                                            monkeypatch):
    """0 is none: a long target with no bound shoots every frame."""
    a = single("A", count=150)
    night = Night(group_hub, monkeypatch)
    try:
        night.done = await night.run(_plan(a), wall_s=120.0)
    finally:
        await night.close()
    assert len(night.shots()) == 150


# --------------------------------------------------------- planned endings

def _finish(hub, session: Session, reason: str, *, time_bound: bool = False,
            ) -> None:
    """End ``session`` the way a run ends it: a fresh engine over the stored
    session, finalized with ``reason``."""
    eng = SequenceEngine(hub)
    eng._session = session_store.load(session.id)
    eng._report_finalized = False
    eng.reporter = None
    if time_bound:
        eng._time_bounded.add(session.plan.targets[0].id)
    eng._finalize_report(reason)


@pytest.mark.parametrize("reason", ["dawn_cutoff"])
async def test_a_planned_ending_arms_the_session_queued_behind_it(
        group_hub, group_store, bus_lines, reason):
    """The stop time, ``max_run_min`` and the window closing all end a run as
    ``dawn_cutoff`` and owe frames: the session behind it is armed.

    MUTANT "promote only on complete" (the planned-ending test narrowed back
    to ``status == 'complete'``) turns this red:

        AssertionError: a 'dawn_cutoff' ending is planned and arms the queue
        assert False is True
    """
    a = _stored("tonight", _queued_plan("tonight"), auto_resume=True, done=1)
    b = _stored("mosaic", _queued_plan("mosaic"), queued_behind=a.id)
    _finish(group_hub, a, reason)
    assert session_store.load(a.id).status == "dormant"
    after = session_store.load(b.id)
    assert after.auto_resume is True, (
        f"a {reason!r} ending is planned and arms the queue")
    assert after.queued_behind is None
    assert any("'mosaic' is armed" in m for m in _lines(bus_lines)), \
        _lines(bus_lines)


async def test_a_time_bound_ending_arms_the_session_queued_behind_it(
        group_hub, group_store, bus_lines):
    """The per-target bound ends the run owing frames, through the
    'incomplete' ending ('targets set aside'), and that is planned too."""
    a = _stored("tonight", _queued_plan("tonight"), auto_resume=True, done=1)
    b = _stored("mosaic", _queued_plan("mosaic"), queued_behind=a.id)
    _finish(group_hub, a, "incomplete", time_bound=True)
    after = session_store.load(b.id)
    assert after.auto_resume is True and after.queued_behind is None
    assert any("time bound" in m and "'mosaic' is armed" in m
               for m in _lines(bus_lines)), _lines(bus_lines)


async def test_the_bound_hands_a_queued_session_on_end_to_end(
        group_hub, group_store, monkeypatch):
    """Two flows queued back to back, on the clocked simulator: A is bounded
    at ten minutes with frames owed, B waits behind it. A ends at the bound
    and B is armed and unqueued, the singleton moved to it."""
    a_plan = _plan(single("A", count=300, max_time_min=10))
    a = _stored("tonight", a_plan, auto_resume=True)
    b = _stored("mosaic", _queued_plan("mosaic"), queued_behind=a.id)
    night = Night(group_hub, monkeypatch)
    try:
        night.done = await night.run(a_plan, wall_s=120.0, session=a)
    finally:
        await night.close()
    assert night.done, night.lines[-6:]
    assert session_store.load(a.id).status == "dormant"
    after = session_store.load(b.id)
    assert after.auto_resume is True and after.queued_behind is None
    armed = session_store.armed()
    assert armed is not None and armed.id == b.id


@pytest.mark.parametrize("reason", [
    "aborted", "unsafe", "error", "quality", "cooling_skip", "shutdown",
    "incomplete"])
async def test_an_unplanned_ending_does_not_promote_and_names_the_queue(
        group_hub, group_store, bus_lines, reason):
    """An abort, an unsafe stop, an error, a quality stop, a cooling skip, a
    process shutdown and a target set aside by a guard are the operator or a
    guard saying stop, not next: the session behind stays queued and
    UNARMED, and the log says it will not start.

    MUTANT "promote on abort too" (the planned-ending test also accepting
    'aborted') turned the 'aborted' case red, run from a byte backup and
    restored and sha256-verified afterwards:

        AssertionError: a 'aborted' ending must not arm the session queued
        behind it
        assert True is False
    """
    a = _stored("tonight", _queued_plan("tonight"), auto_resume=True, done=1)
    b = _stored("mosaic", _queued_plan("mosaic"), queued_behind=a.id)
    _finish(group_hub, a, reason)
    after = session_store.load(b.id)
    assert after.auto_resume is False, (
        f"a {reason!r} ending must not arm the session queued behind it")
    assert after.queued_behind == a.id
    said = [m for m in _lines(bus_lines)
            if m.startswith("'mosaic' is queued behind this session and "
                            "will not start")]
    assert len(said) == 1, _lines(bus_lines)


async def test_no_line_when_nothing_is_queued(group_hub, group_store,
                                              bus_lines):
    a = _stored("tonight", _queued_plan("tonight"), auto_resume=True, done=1)
    _finish(group_hub, a, "aborted")
    assert not [m for m in _lines(bus_lines) if "queued behind" in m]


# ------------------------------------------- the doctor and the engine agree

GUARDS_OFF = SimpleNamespace(max_consecutive_rejects=0,
                             max_consecutive_rejects_night=0)


def _node(nid: str, ntype: str, x: float = 0.0, **params) -> FlowNode:
    return FlowNode(id=nid, type=ntype, x=float(x), params=params)


def _edge(src: str, sp: str, dst: str, dp: str) -> FlowEdge:
    return FlowEdge(**{"from": src, "fromPort": sp, "to": dst, "toPort": dp})


def _accepted_flow(max_time: int, *, second: int | None = None) -> FlowGraph:
    """An accepted-count flow with no stop at all (DUSK's Stop is None), one
    target bounded at ``max_time`` minutes (0 = none) and, with ``second``, a
    second target after it bounded at that."""
    nodes = [_node("d", "dusk", 0, stop="None"),
             _node("t", "target", 200, name="M31", ra="00h 42m 44s",
                   dec="+41 16 09", counts="Accepted subs", maxTime=max_time),
             _node("c", "capture", 400, exposure=60, count=5)]
    edges = [_edge("d", "window", "t", "arm"), _edge("t", "target", "c", "run")]
    if second is not None:
        nodes += [_node("t2", "target", 600, name="M33", ra="01h 33m 51s",
                        dec="+30 39 37", counts="Accepted subs",
                        maxTime=second),
                  _node("c2", "capture", 800, exposure=60, count=5,
                        filter="R")]
        edges += [_edge("c", "complete", "t2", "arm"),
                  _edge("t2", "target", "c2", "run")]
    return FlowGraph(nodes=nodes, edges=edges)


def _warned(graph: FlowGraph) -> bool:
    return any("Run will refuse it" in i.text for i in
               check(graph, rig=RigFacts(reject_guards_off=True)))


@pytest.mark.parametrize("first, second, bounded", [
    (0, None, False), (60, None, True), (60, 0, False), (60, 90, True)],
    ids=["none", "one-bounded", "second-unbounded", "both-bounded"])
def test_the_doctor_note_is_the_refusal_and_a_time_bound_lifts_both(
        first, second, bounded):
    """Accepted counts, both reject guards off, no stop: a sub the grader
    never accepts is retried without end UNLESS every target ends by its own
    maximum time. The doctor's note and the engine's refusal
    (``quota_unbounded``) are asked of the same flow and must agree.

    MUTANT "the doctor ignores the bound" (M9's ``all(_time_bounded...)``
    dropped) turned the bounded cases red:
        AssertionError: the doctor says True, the engine says False
    """
    graph = _accepted_flow(first, second=second)
    plan, _ = to_sequence_plan(compile_plan(graph), graph)
    refuses = quota_unbounded(plan, GUARDS_OFF)
    assert refuses is (not bounded)
    assert _warned(graph) == refuses, (
        f"the doctor says {_warned(graph)}, the engine says {refuses}")


def test_a_maximum_time_on_a_mosaic_block_is_said_to_be_unused():
    graph = FlowGraph(nodes=[
        _node("t", "target", 0, name="M31", ra="00h 42m 44s",
              dec="+41 16 09", rows=2, cols=2, maxTime=45)], edges=[])
    texts = [i.text for i in check(graph)]
    assert any("a maximum time bounds a single target" in t for t in texts), \
        texts


# ----------------------------------------------- the flow reaches the engine

def test_a_targets_maximum_time_reaches_the_plan():
    graph = _accepted_flow(90)
    plan, _ = to_sequence_plan(compile_plan(graph), graph)
    assert [t.max_time_min for t in plan.targets] == [90]
    unbounded = _accepted_flow(0)
    plan0, _ = to_sequence_plan(compile_plan(unbounded), unbounded)
    assert [t.max_time_min for t in plan0.targets] == [0]


def test_a_maximum_time_the_target_cannot_hold_is_refused_naming_the_block():
    graph = _accepted_flow(1500)
    with pytest.raises(GraphNotRunnable) as e:
        to_sequence_plan(compile_plan(graph), graph)
    assert "TARGET M31" in str(e.value) and "maximum time" in str(e.value)


# ---------------------------------------------------------- the wizard answer

def _wizard_target(**answers):
    rec = generate_answer("Deep-sky target", ["Guiding"], "M16",
                          ra="18h 18m 48s", dec="-13 49 00",
                          **answers).record
    return next(n for n in rec.graph.nodes if n.type == "target")


def test_the_wizard_writes_the_bound_on_the_target_and_nothing_without_it():
    assert _wizard_target(max_time_min=60).params["maxTime"] == 60
    assert _wizard_target().params["maxTime"] == 0
    assert _wizard_target(max_time_min=0).params["maxTime"] == 0


@pytest.mark.parametrize("bad", [True, 1.5, "60", -1, 1441, [60]])
def test_the_wizard_refuses_a_bound_that_is_not_whole_minutes(bad):
    with pytest.raises(ValueError, match="max_time_min"):
        generate_answer("Deep-sky target", [], "M16", max_time_min=bad)


@pytest.mark.parametrize("kind", ["Best of several", "Mosaic"])
def test_the_wizard_refuses_a_bound_for_a_kind_with_no_single_target(kind):
    with pytest.raises(ValueError, match="single target"):
        generate_answer(kind, [], "M16, M17", max_time_min=30,
                        **({"rows": 2, "cols": 2, "angle_mode": "Any angle"}
                           if kind == "Mosaic" else {}))


def test_the_wizard_route_refuses_a_boolean_bound_before_coercion():
    with pytest.raises(ValidationError):
        FlowWizardBody(max_time_min=True)
    assert FlowWizardBody(max_time_min=60).max_time_min == 60
    assert FlowWizardBody().max_time_min is None
