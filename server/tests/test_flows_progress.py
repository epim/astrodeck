"""What a flow has banked and what it still owes (#189 S1 item 9, spec 8).

``flows.progress.flow_progress`` is the pure half of ``GET
/api/flows/{id}/progress``: given the compile, the plan it expands to and the
flow's newest session, it answers per block, per panel and per step how many
subs are banked and how many are owed, plus how many of the session's frames
sit on steps this flow no longer has. The card's state chip ("212/315 subs")
and the CONTINUE button's copy are read off it, so each rule below is a number
an operator will act on:

* a plan target is found by the identity functions, never by list position,
  so a block that compiled to nothing cannot inherit its neighbour's frames,
  and a plan whose targets the compile cannot account for (compiled without
  the flow id) is refused rather than read as "nothing banked";
* a frame counts the way the session's FROZEN ``count_mode`` says, capped at
  the step's count, which is exactly what ``Session.owed()`` says;
* a frame whose step id is in no plan step is orphaned, never banked on a
  step that merely looks like it, and every such frame counts, whatever the
  mode: an orphaned frame fills no quota for the mode to decide on;
* nothing derived from the site appears (spec 6.9): the route is
  ``CAP_VIEW_STATUS``, and a viewer can read it.

Every test names the mutation of ``flows/progress.py`` it guards and quotes
the failure that mutation produced, run from a byte backup of the file. The
controls pin the cases where nothing should change.
"""
from __future__ import annotations

import json

import pytest

from astrodeck.flows import identity
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.session import Session, SessionFrame

FLOW = "flow-progress-a"


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _graph(*, ha_exposure=120, l_exposure=60, count=10, cycles=3):
    """dusk -> TARGET M31 -> CAPTURE Ha -> FILTER CYCLE L/R/G.

    One target and four steps, in this order: Ha (``count``), then L, R and G
    (``cycles`` each). The exposure knobs exist so a test can drop a step: an
    exposure change is a new recipe, so a new step id (#77)."""
    return FlowGraph(
        nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
               _n("t", "target", x=100, name="M31", ra="00h 42m 44s",
                  dec="+41 16 09", rotation=-1),
               _n("c", "capture", x=200, filter="Ha", exposure=ha_exposure,
                  gain=100, bin="1", count=count, goal=0),
               _n("y", "cycle", x=300, plan=f"L {l_exposure}, R 60, G 60",
                  cycles=cycles, perCycle=1, gain=100, bin="1")],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run"),
               _e("c", "complete", "y", "run")])


def _pool_graph(members="M31, M42, M31"):
    """A POOL feeding one CAPTURE: every member owes 5 L subs."""
    return FlowGraph(
        nodes=[_n("p", "pool", members=members, minAlt=0, moonSep=0,
                  maxHA=0),
               _n("c", "capture", x=100, filter="L", exposure=60, gain=100,
                  bin="1", count=5, goal=0)],
        edges=[_e("p", "target", "c", "run")])


def _compile(graph):
    compiled = compile_plan(graph, "n")
    plan, _unmapped = to_sequence_plan(compiled, graph, flow_id=FLOW)
    return compiled, plan


def _session(plan, frames, *, count_mode="attempts", status="dormant",
             nights=("night-1", "night-2")):
    """A session of this flow: ``plan`` frozen, counted in ``count_mode``."""
    return Session(id="session-1", status=status, nights=list(nights),
                   plan=plan.model_copy(update={"count_mode": count_mode}),
                   frames=frames, origin="flow", origin_id=FLOW)


def _frames(step_id, n, *, accepted=True, override=None):
    return [SessionFrame(step_id=step_id, auto_accepted=accepted,
                         override=override) for _ in range(n)]


def _banked(progress):
    """``{(block node id, panel index, filter): banked}`` for every step."""
    return {(b["node_id"], i, s["filter"]): s["banked"]
            for b in progress["blocks"]
            for i, p in enumerate(b["panels"]) for s in p["steps"]}


def _step(plan, filter_name, target=0):
    return next(s for s in plan.targets[target].steps
                if s.filter == filter_name)


def test_exact_banked_and_owed_for_frames_on_two_steps():
    """Four Ha subs and two R subs against Ha 10 + L/R/G 3 each: every number
    in the payload, spelled out.

    Mutant "owed is the count" (a step's ``owed`` left at ``count``, the
    banked subs never taken off it) failed here, and in four other tests:
        AssertionError: assert {'blocks': [{...': 'dormant'}} ==
        {'blocks': [{...': 'dormant'}}
          Differing items:
          {'blocks': [{'banked': 6, 'kind': 'target', 'name': 'M31',
          'node_id': 't', ...}]} != {'blocks': [{'banked': 6, 'kind':
          'target', 'name': 'M31', 'node_id': 't', ...}]}
    Mutant "nights as the report ids" (``session.nights`` itself, not its
    length) failed:
          {'session': {'count_mode': 'attempts', 'id': 'session-1',
          'nights': ['night-1', 'night-2'], 'status': 'dormant'}} !=
          {'session': {'count_mode': 'attempts', 'id': 'session-1',
          'nights': 2, 'status': 'dormant'}}
    Mutant "add a transit altitude" and mutant "steps as a tuple" (see
    TestThePayloadCarriesNoSiteData) failed here too, with the same
    ``{'blocks': ...} != {'blocks': ...}`` diff as the first mutant above.
    """
    compiled, plan = _compile(_graph())
    target = plan.targets[0]
    ha, lum, red, green = target.steps
    assert [s.filter for s in target.steps] == ["Ha", "L", "R", "G"], \
        "premise: the capture step comes first, then the cycle's slots"
    assert target.id == identity.target_id(identity.group_id(
        FLOW, "t", identity.geometry_key(target.ra_hours, target.dec_deg,
                                         None)), 0, 0), \
        "premise: the plan was compiled with deterministic ids"

    got = flow_progress(compiled, plan,
                        _session(plan, _frames(ha.id, 4) + _frames(red.id, 2)),
                        flow_id=FLOW)

    def step(s, banked):
        return {"step_id": s.id, "filter": s.filter, "frame_type": "Light",
                "exposure_s": s.exposure_s, "count": s.count,
                "banked": banked, "owed": s.count - banked}

    assert got == {
        "flow_id": FLOW,
        "session": {"id": "session-1", "status": "dormant", "nights": 2,
                    "count_mode": "attempts"},
        "blocks": [{
            "node_id": "t", "name": "M31", "kind": "target",
            "banked": 6, "owed": 13, "total": 19,
            "panels": [{
                "target_id": target.id, "name": "M31", "row": 0, "col": 0,
                "banked": 6, "owed": 13, "total": 19,
                "steps": [step(ha, 4), step(lum, 0), step(red, 2),
                          step(green, 0)]}]}],
        "orphaned": {"frames": 0, "steps": 0}}
    assert (ha.exposure_s, ha.count, red.exposure_s, red.count) == \
        (120.0, 10, 60.0, 3), "premise: the recipe the graph drew"


class TestCountMode:
    """Counts follow the SESSION'S frozen ``count_mode``: the one the ledger
    was counted by. The new compile's mode is a proposal CONTINUE has to
    announce (spec 5.9, ``accept_recount``); reading it here would recount
    every banked frame on the card before anyone agreed to it."""

    # 3 accepted, 2 rejected, 1 accepted then overridden to reject, and 1
    # rejected then overridden to accept: 4 effective, 7 taken.
    @staticmethod
    def _mixed(step_id):
        return (_frames(step_id, 3) + _frames(step_id, 2, accepted=False)
                + _frames(step_id, 1, override="reject")
                + _frames(step_id, 1, accepted=False, override="accept"))

    def test_an_accepted_mode_session_ignores_rejected_frames(self):
        """Seven Ha subs taken, four effective: an accepted-mode session
        banks four, and an override counts the way the operator set it.

        Mutant "count every frame" (``session.recorded_by_step()`` in place
        of the session's mode-aware counts) failed:
            assert 7 == 4
        Mutant "read count_mode off the new plan" (below) failed here too,
        with the same ``assert 7 == 4``.
        Mutant "owed is the count" failed at the panel line:
            assert (4, 19) == (4, 15)
        """
        compiled, plan = _compile(_graph())
        ha = _step(plan, "Ha")
        assert plan.count_mode == "attempts", \
            "premise: the new compile counts attempts, the session does not"
        got = flow_progress(compiled, plan,
                            _session(plan, self._mixed(ha.id),
                                     count_mode="accepted"), flow_id=FLOW)
        assert got["session"]["count_mode"] == "accepted"
        assert _banked(got)[("t", 0, "Ha")] == 4
        panel = got["blocks"][0]["panels"][0]
        assert (panel["banked"], panel["owed"]) == (4, 15)

    def test_control_an_attempts_mode_session_counts_every_frame(self):
        """The same seven subs in an attempts-mode session are seven: the
        rejected ones were attempts, and that session counts attempts.

        Mutant "always count accepted" (``session.accepted_by_step()``
        whatever the mode) failed:
            assert 4 == 7
        """
        compiled, plan = _compile(_graph())
        ha = _step(plan, "Ha")
        got = flow_progress(compiled, plan,
                            _session(plan, self._mixed(ha.id),
                                     count_mode="attempts"), flow_id=FLOW)
        assert _banked(got)[("t", 0, "Ha")] == 7

    def test_the_new_compiles_count_mode_is_not_the_ledgers(self):
        """An attempts-mode session read against a compile that now asks for
        accepted subs still banks every attempt, until CONTINUE recounts.

        Mutant "read count_mode off the new plan" (the counts chosen by
        ``plan.count_mode`` instead of the session's) failed:
            assert 4 == 7
        Mutant "always count accepted" failed here too, the same way.
        """
        compiled, plan = _compile(_graph())
        ha = _step(plan, "Ha")
        session = _session(plan, self._mixed(ha.id), count_mode="attempts")
        accepted_plan = plan.model_copy(update={"count_mode": "accepted"})
        got = flow_progress(compiled, accepted_plan, session, flow_id=FLOW)
        assert got["session"]["count_mode"] == "attempts"
        assert _banked(got)[("t", 0, "Ha")] == 7


def test_banked_is_capped_at_the_count():
    """Twelve Ha subs against a count of ten bank ten and owe nothing. The
    two extra are real frames, but a step cannot owe a negative number and
    the chip must not read "12/10".

    Mutant "no cap" (``banked`` is the raw count) failed:
        AssertionError: assert ('Ha', 10, 12, -2) == ('Ha', 10, 10, 0)
          At index 2 diff: 12 != 10
    Mutant "owed is the count" failed here too:
        AssertionError: assert ('Ha', 10, 10, 10) == ('Ha', 10, 10, 0)
    """
    compiled, plan = _compile(_graph())
    ha = _step(plan, "Ha")
    got = flow_progress(compiled, plan, _session(plan, _frames(ha.id, 12)),
                        flow_id=FLOW)
    step = got["blocks"][0]["panels"][0]["steps"][0]
    assert (step["filter"], step["count"], step["banked"], step["owed"]) == \
        ("Ha", 10, 10, 0)
    assert (got["blocks"][0]["banked"], got["blocks"][0]["owed"]) == (10, 9)
    assert got["orphaned"] == {"frames": 0, "steps": 0}, \
        "frames past the count are on a live step, so they are not orphaned"


def test_the_chip_agrees_with_session_owed():
    """When the session's plan IS the plan, what the blocks owe is exactly
    ``Session.owed()``, the one definition of finished. The chip and the run's
    ending must never answer "is this done" differently (#252).

    Mutant "no cap" failed here as well:
        AssertionError: assert 6 == 8
    and so did three others: "count every frame" and "read count_mode off
    the new plan" (``assert 5 == 8``), and "owed is the count"
    (``assert 19 == 8``).
    """
    compiled, plan = _compile(_graph())
    ha, lum, red, _green = plan.targets[0].steps
    session = _session(plan, _frames(ha.id, 12) + _frames(lum.id, 1)
                       + _frames(red.id, 3, accepted=False)
                       + _frames("a-step-no-plan-has", 4),
                       count_mode="accepted")
    got = flow_progress(compiled, plan, session, flow_id=FLOW)
    # Ha 10 owes 0 (twelve banked, capped), L 3 owes 2, R 3 owes 3 (its three
    # subs were rejected), G 3 owes 3.
    assert sum(b["owed"] for b in got["blocks"]) == session.owed() == 8
    assert sum(b["total"] for b in got["blocks"]) == plan.total_frames()


class TestOrphans:
    """A frame whose step id is in no plan step belongs to a step the flow no
    longer has: an exposure change, a re-framed target, a deleted stage. It
    is ORPHANED. The ledger counts by step id alone, so banking it on a step
    that merely shares its filter would credit 120 s subs to a 180 s quota,
    the #77 mix the step id exists to prevent. Every such frame is counted,
    whatever the count mode."""

    def _two_compiles(self):
        _old_compiled, old_plan = _compile(_graph(ha_exposure=120,
                                                  l_exposure=60))
        compiled, plan = _compile(_graph(ha_exposure=180, l_exposure=90))
        assert _step(old_plan, "Ha").id != _step(plan, "Ha").id
        assert _step(old_plan, "L").id != _step(plan, "L").id
        assert _step(old_plan, "R").id == _step(plan, "R").id, \
            "premise: an unchanged recipe keeps its step id"
        return old_plan, compiled, plan

    def test_frames_on_a_dropped_step_are_orphaned_not_banked(self):
        """Five subs on the old Ha 120 s step and two on the old L 60 s step,
        read against a compile that now shoots Ha 180 s and L 90 s: seven
        orphaned frames on two steps, and the new steps bank nothing. The one
        R sub, whose recipe did not change, stays banked.

        Mutant "match by filter" (a frame's filter looked up in the session's
        plan and banked on the new step with that filter) failed:
            AssertionError: assert {('t', 0, 'G'...', 0, 'R'): 1} ==
            {('t', 0, 'G'...', 0, 'R'): 1}
              {('t', 0, 'L'): 2} != {('t', 0, 'L'): 0}
              {('t', 0, 'Ha'): 5} != {('t', 0, 'Ha'): 0}
        """
        old_plan, compiled, plan = self._two_compiles()
        session = _session(old_plan, _frames(_step(old_plan, "Ha").id, 5)
                           + _frames(_step(old_plan, "L").id, 2)
                           + _frames(_step(old_plan, "R").id, 1))
        got = flow_progress(compiled, plan, session, flow_id=FLOW)
        assert _banked(got) == {("t", 0, "Ha"): 0, ("t", 0, "L"): 0,
                                ("t", 0, "R"): 1, ("t", 0, "G"): 0}
        assert got["orphaned"] == {"frames": 7, "steps": 2}

    def test_orphans_count_every_frame_whatever_the_mode(self):
        """Three accepted and two rejected subs on the dropped Ha 120 s step
        are five orphaned frames in either mode. The count mode decides what
        fills a quota, and an orphaned frame fills none: it is a file on disk
        the flow has no step for, and CONTINUE's dropped-steps refusal
        (``continuation.plan_replace_report``) counts it the same way, so the
        card and the refusal quote one number.

        Mutant "orphans counted by the session's mode" (orphans read off
        ``session._counts()``) failed the accepted half:
            AssertionError: assert {'frames': 3, 'steps': 1} ==
            {'frames': 5, 'steps': 1}
        Mutant "match by filter" failed at the last line, the orphans banked
        on the new Ha step:
            AssertionError: an orphaned frame is never banked, in either mode
            assert 3 == 0
        """
        old_plan, compiled, plan = self._two_compiles()
        old_ha = _step(old_plan, "Ha").id
        frames = _frames(old_ha, 3) + _frames(old_ha, 2, accepted=False)
        accepted = flow_progress(
            compiled, plan, _session(old_plan, frames, count_mode="accepted"),
            flow_id=FLOW)
        attempts = flow_progress(
            compiled, plan, _session(old_plan, frames, count_mode="attempts"),
            flow_id=FLOW)
        assert accepted["orphaned"] == {"frames": 5, "steps": 1}
        assert attempts["orphaned"] == {"frames": 5, "steps": 1}
        assert _banked(accepted)[("t", 0, "Ha")] == 0, \
            "an orphaned frame is never banked, in either mode"

    def test_a_step_holding_only_rejected_frames_is_still_orphaned(self):
        """A dropped step whose every sub was rejected still holds frames the
        flow no longer has a step for, so it is an orphaned step.

        Mutant "orphans counted by the session's mode" failed here as well:
            AssertionError: assert {'frames': 0, 'steps': 0} ==
            {'frames': 2, 'steps': 1}
        """
        old_plan, compiled, plan = self._two_compiles()
        frames = _frames(_step(old_plan, "Ha").id, 2, accepted=False)
        got = flow_progress(
            compiled, plan, _session(old_plan, frames, count_mode="accepted"),
            flow_id=FLOW)
        assert got["orphaned"] == {"frames": 2, "steps": 1}

    def test_a_frame_the_sessions_own_plan_already_dropped_is_orphaned(self):
        """After an accepted drop the session's plan is replaced wholesale and
        its old frames stay in the ledger, on step ids that are now in NO plan,
        the session's own included. They are still frames this flow has no
        step for. (``continuation.plan_replace_report`` compares the session's
        plan with the new one, so it does not count these a second time; the
        card counts what is orphaned now.)

        Mutant "orphans limited to the session plan's steps" (only step ids
        the session's frozen plan still has) failed:
            AssertionError: assert {'frames': 0, 'steps': 0} ==
            {'frames': 5, 'steps': 1}
        """
        old_plan, compiled, plan = self._two_compiles()
        session = _session(plan, _frames(_step(old_plan, "Ha").id, 5)
                           + _frames(_step(plan, "R").id, 1))
        got = flow_progress(compiled, plan, session, flow_id=FLOW)
        assert got["orphaned"] == {"frames": 5, "steps": 1}
        assert _banked(got)[("t", 0, "R")] == 1


class TestPools:
    def test_each_member_is_a_panel_with_no_grid_position(self):
        """"M31, M42, M31" is three members, each its own panel with its own
        quota; a pool is not a grid, so row and col are null. The second M31
        is found by its occurrence suffix, exactly as ``to_plan`` keyed it.

        Mutant "every member keyed as occurrence 0" failed, the second M31
        finding the first one's target and banking its 2 subs, not its own 3:
            AssertionError: assert {'banked': 5,...id': 'p', ...} ==
            {'banked': 6,...id': 'p', ...}
              {'banked': 5} != {'banked': 6}
              {'owed': 10} != {'owed': 9}
        Mutant "pool members at row 0, col 0" failed:
              At index 0 diff: ('7a53c83cb97052289bb06a9857c38014', 'M31',
              0, 0, 2, 3, 5) != ('7a53c83cb97052289bb06a9857c38014', 'M31',
              None, None, 2, 3, 5)
        Mutant "pool block named after its first member" (the members-box
        name never written) failed:
              {'name': 'M31'} != {'name': 'M31, M42, M31'}
        """
        compiled, plan = _compile(_pool_graph())
        assert [t.name for t in plan.targets] == ["M31", "M42", "M31"]
        first, m42, second = plan.targets
        assert first.id == identity.member_id(FLOW, "p", "M31", 0)
        assert second.id == identity.member_id(FLOW, "p", "M31", 1)
        session = _session(plan, _frames(first.steps[0].id, 2)
                           + _frames(m42.steps[0].id, 1)
                           + _frames(second.steps[0].id, 3))
        got = flow_progress(compiled, plan, session, flow_id=FLOW)

        block, = got["blocks"]
        assert {k: block[k] for k in ("node_id", "name", "kind", "banked",
                                      "owed", "total")} == {
            "node_id": "p", "name": "M31, M42, M31", "kind": "pool",
            "banked": 6, "owed": 9, "total": 15}
        assert [(p["target_id"], p["name"], p["row"], p["col"], p["banked"],
                 p["owed"], p["total"]) for p in block["panels"]] == [
            (first.id, "M31", None, None, 2, 3, 5),
            (m42.id, "M42", None, None, 1, 4, 5),
            (second.id, "M31", None, None, 3, 2, 5)]

    def test_a_dropped_member_takes_no_key_as_to_plan_gives_it_none(self):
        """``to_plan`` drops a member with no usable coordinates BEFORE keying
        it, so it uses up no occurrence suffix. Here the dropped member is
        literally named "M31#1", which is also the key of the second M31: it
        must neither claim that target (its name is not "M31") nor use up the
        key the second M31 was given.

        Mutant "no name check" (any target whose id matches is claimed)
        failed, the dropped member taking the second M31's target and subs:
              At index 1 diff: ('a4e8bc54a6585fdda56139c34a287722', 'M31', 3,
              5) != (None, 'M31#1', 0, 0)
        Mutant "a miss uses up the key" (the key issued before the lookup)
        failed, the second M31 looked up one suffix too far:
              At index 2 diff: (None, 'M31', 0, 0) !=
              ('a4e8bc54a6585fdda56139c34a287722', 'M31', 3, 5)
        It is also a control for the foreign-plan refusal: a dropped entry is
        not a stray target. Mutant "check fires on a dropped entry" (the
        refusal counts panels with no target, not targets with no panel)
        failed:
            ValueError: 1 plan target(s) match no block of this compile
            (M31#1): the plan was not compiled from it with
            flow_id='flow-progress-a', so every count read against it would
            be wrong
        """
        def member(name, rank, ra="00h 42m 44s", dec="+41 16 09"):
            return {"name": name, "pool_rank": rank, "node_id": "p",
                    "ra": ra, "dec": dec,
                    "steps": [{"filter": "L", "exposure_s": 60, "gain": 100,
                               "binning": 1, "count": 5,
                               "frame_type": "Light", "node_id": "c"}]}
        compiled = {"name": "n", "schedule": {"start_mode": "now"},
                    "targets": [member("M31", 1),
                                member("M31#1", 2, ra="bogus", dec="bogus"),
                                member("M31", 3)],
                    "automation": {}, "instructions": []}
        plan, _ = to_sequence_plan(compiled, flow_id=FLOW)
        assert [t.name for t in plan.targets] == ["M31", "M31"], \
            "premise: to_plan dropped the member that has no coordinates"
        first, second = plan.targets
        assert second.id == identity.member_id(FLOW, "p", "M31#1", 0), \
            "premise: the dropped member's own key names the second M31"
        got = flow_progress(compiled, plan,
                            _session(plan, _frames(second.steps[0].id, 3)),
                            flow_id=FLOW)
        assert [(p["target_id"], p["name"], p["banked"], p["total"])
                for p in got["blocks"][0]["panels"]] == [
            (first.id, "M31", 0, 5), (None, "M31#1", 0, 0),
            (second.id, "M31", 3, 5)]

    def test_occurrence_suffixes_are_counted_per_pool(self):
        """Two POOLs that both list M31 give two FIRST copies: ``to_plan``
        counts the suffixes per pool node, so the second pool's M31 is keyed
        "M31", not "M31#1". Each pool's M31 keeps its own subs.

        Mutant "member keys shared across pools" (one issued-key set for
        every pool, not one per node) failed: the second pool's M31 was
        looked up as "M31#1", found nothing, and the refusal caught it:
            ValueError: 1 plan target(s) match no block of this compile
            (M31): the plan was not compiled from it with
            flow_id='flow-progress-a', so every count read against it would
            be wrong
        """
        graph = FlowGraph(
            nodes=[_n("a", "pool", members="M31, M42", minAlt=0, moonSep=0,
                      maxHA=0),
                   _n("c", "capture", x=100, filter="L", exposure=60,
                      gain=100, bin="1", count=5, goal=0),
                   _n("b", "pool", x=200, members="M31", minAlt=0,
                      moonSep=0, maxHA=0),
                   _n("k", "capture", x=300, filter="R", exposure=60,
                      gain=100, bin="1", count=4, goal=0)],
            edges=[_e("a", "target", "c", "run"),
                   _e("c", "complete", "b", "arm"),
                   _e("b", "target", "k", "run")])
        compiled, plan = _compile(graph)
        assert [t.name for t in plan.targets] == ["M31", "M42", "M31"]
        a_m31, a_m42, b_m31 = plan.targets
        assert b_m31.id == identity.member_id(FLOW, "b", "M31", 0), \
            "premise: to_plan keys the second pool's M31 as its first copy"
        session = _session(plan, _frames(a_m31.steps[0].id, 2)
                           + _frames(b_m31.steps[0].id, 3))
        got = flow_progress(compiled, plan, session, flow_id=FLOW)
        assert [(b["node_id"], [(p["target_id"], p["name"], p["banked"])
                                for p in b["panels"]])
                for b in got["blocks"]] == [
            ("a", [(a_m31.id, "M31", 2), (a_m42.id, "M42", 0)]),
            ("b", [(b_m31.id, "M31", 3)])]


def test_targets_are_matched_by_identity_not_by_list_position():
    """A TARGET with no coordinates compiles to an entry but no plan target.
    Matched by position, the entry after it would slide up and hand its frames
    to the block that has none; matched by identity, the empty block owes
    nothing and M31 keeps its own.

    Mutant "match by position" (each TARGET entry takes the next unclaimed
    plan target in order) failed, the empty block holding M31's target:
        AssertionError: assert ('blank', 'a9...a', ...}], 10) ==
        ('blank', None, [], 0)
          At index 1 diff: 'a94ea9cd1a4c51d7ab1deb3fa2fdd946' != None
    Mutant "check fires on a dropped entry" failed here too (the blank block
    has no name, hence the empty brackets):
        ValueError: 1 plan target(s) match no block of this compile (): the
        plan was not compiled from it with flow_id='flow-progress-a', so
        every count read against it would be wrong
    """
    graph = FlowGraph(
        nodes=[_n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
               _n("blank", "target", x=100, name="", ra="", dec="",
                  rotation=-1),
               _n("t", "target", x=100, y=100, name="M31", ra="00h 42m 44s",
                  dec="+41 16 09", rotation=-1),
               _n("c", "capture", x=200, filter="Ha", exposure=120,
                  gain=100, bin="1", count=10, goal=0)],
        edges=[_e("d", "window", "blank", "arm"),
               _e("d", "window", "t", "arm"), _e("t", "target", "c", "run")])
    compiled, plan = _compile(graph)
    assert [e["node_id"] for e in compiled["targets"]] == ["blank", "t"]
    assert [t.name for t in plan.targets] == ["M31"], \
        "premise: to_plan dropped the target with no coordinates"
    m31 = plan.targets[0]
    got = flow_progress(compiled, plan,
                        _session(plan, _frames(m31.steps[0].id, 4)),
                        flow_id=FLOW)
    blank, target = got["blocks"]
    assert (blank["node_id"], blank["panels"][0]["target_id"],
            blank["panels"][0]["steps"], blank["total"]) == \
        ("blank", None, [], 0)
    assert (target["node_id"], target["panels"][0]["target_id"],
            target["banked"], target["total"]) == ("t", m31.id, 4, 10)


def test_entries_with_no_node_id_are_a_block_each():
    """A compiled dict built by hand, or by a caller older than S1, carries no
    node ids, and ``to_plan`` leaves its ids uuid4, so nothing in it can be
    matched. Each such entry is still its own block: grouped under "" the two
    TARGETs below would be one block with two panels at row 0, col 0.

    Mutant "group id-less entries under ''" (the block key is the node id
    alone) failed:
        AssertionError: assert [('', 'M31', 2, None)] ==
        [('', 'M31', ...in', 1, None)]
          At index 0 diff: ('', 'M31', 2, None) != ('', 'M31', 1, None)
    Mutant "match by position" failed here too, an id-less entry taking a
    target no identity names (the id is a uuid4, so it differs every run):
          At index 0 diff: ('', 'M31', 1, '0fbe735af5564c43a0aedac3ddbb1b4b')
          != ('', 'M31', 1, None)
    It is also the control for the foreign-plan refusal: these targets are
    unclaimable by design. Mutant "check without the id-less exemption"
    failed:
        ValueError: 2 plan target(s) match no block of this compile (M31, M31
        again): the plan was not compiled from it with
        flow_id='flow-progress-a', so every count read against it would be
        wrong
    """
    def entry(name):
        return {"name": name, "ra": "00h 42m 44s", "dec": "+41 16 09",
                "rotation_deg": -1,
                "steps": [{"filter": "L", "exposure_s": 60, "gain": 100,
                           "binning": 1, "count": 5, "frame_type": "Light"}]}
    compiled = {"name": "n", "schedule": {"start_mode": "now"},
                "targets": [entry("M31"), entry("M31 again")],
                "automation": {}, "instructions": []}
    plan, _ = to_sequence_plan(compiled, flow_id=FLOW)
    assert len(plan.targets) == 2
    got = flow_progress(compiled, plan, None, flow_id=FLOW)
    assert [(b["node_id"], b["name"], len(b["panels"]),
             b["panels"][0]["target_id"]) for b in got["blocks"]] == [
        ("", "M31", 1, None), ("", "M31 again", 1, None)]


def test_with_no_session_nothing_is_banked_and_everything_is_owed():
    """A flow that has never run: no session, nothing banked, every sub owed
    and nothing orphaned.

    Mutant "no session reported as an empty summary" (a session dict with
    every value None instead of null) failed:
        AssertionError: assert {'count_mode': None, 'id': None,
        'nights': None, 'status': None} is None
    Mutant "no session guard in the orphan count" failed here (and in every
    other no-session case):
        AttributeError: 'NoneType' object has no attribute 'recorded_by_step'
    """
    compiled, plan = _compile(_graph())
    got = flow_progress(compiled, plan, None, flow_id=FLOW)
    assert got["session"] is None
    assert got["orphaned"] == {"frames": 0, "steps": 0}
    block, = got["blocks"]
    assert (block["banked"], block["owed"], block["total"]) == (0, 19, 19)
    assert all(s["banked"] == 0 and s["owed"] == s["count"]
               for s in block["panels"][0]["steps"])


def test_a_progress_without_a_flow_id_is_refused():
    """With no flow id nothing can be matched by identity, and every block
    would read "nothing banked" while the ledger holds a campaign. That is a
    caller's mistake, so it is an error, not a plausible zero.

    Mutant "no guard" (the flow id check never raises) failed:
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    compiled, plan = _compile(_graph())
    with pytest.raises(ValueError, match="flow id"):
        flow_progress(compiled, plan, None, flow_id="")


class TestAForeignPlanIsRefused:
    """A plan whose targets this compile does not account for is refused. The
    likely caller mistake is a compile WITHOUT ``flow_id`` (``app.py`` did not
    pass one when S1-05 landed): its uuid4 ids match no block, and the payload
    would say "nothing banked, every frame orphaned" about a live campaign."""

    def test_a_plan_compiled_without_the_flow_id_is_refused(self):
        """Mutant "no foreign-plan check" (the call to
        ``_refuse_a_foreign_plan`` removed) failed:
            Failed: DID NOT RAISE <class 'ValueError'>
        Mutant "match by position" failed here too, the same way: it hands
        the uuid4 target to the block by its place in the list.
        """
        graph = _graph()
        compiled = compile_plan(graph, "n")
        plan, _ = to_sequence_plan(compiled, graph)
        session = _session(plan, _frames(plan.targets[0].steps[0].id, 4))
        with pytest.raises(ValueError, match="not compiled from it"):
            flow_progress(compiled, plan, session, flow_id=FLOW)

    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_control_every_shipped_example_is_accounted_for(self, ex):
        """Each shipped example, compiled with its own id, is read without a
        refusal: every plan target is exactly one panel's, and with no session
        the blocks owe the whole plan. example-m31 is the one with a set angle
        (PA 23.4), and example-pool and example-campaign the ones with pools.

        Mutant "the angle left out of the match" (``_single`` keys every
        target as "any angle") failed example-m31 only:
            ValueError: 1 plan target(s) match no block of this compile (M31 -
            Andromeda): the plan was not compiled from it with
            flow_id='example-m31', so every count read against it would be
            wrong
        """
        compiled = compile_plan(ex.graph, ex.name)
        plan, _ = to_sequence_plan(compiled, ex.graph, flow_id=ex.id)
        got = flow_progress(compiled, plan, None, flow_id=ex.id)
        claimed = [p["target_id"] for b in got["blocks"] for p in b["panels"]]
        assert sorted(claimed) == sorted(t.id for t in plan.targets)
        assert sum(b["owed"] for b in got["blocks"]) == plan.total_frames()


class TestASingleTargetIsKeyedAsToPlanKeysIt:
    """Since S3 ``to_plan`` keys a 1x1 block on its stored ANCHOR when it has
    one (a nudge the save carried keeps its ids) and on the angle it is LAID
    OUT at, which for "Camera fixed at PA" is a PA the rotator is never told
    (spec 3.3, ruling 3). ``_single`` has to ask the same question, or the
    card finds no target and ``_refuse_a_foreign_plan`` fails the whole
    answer with a 500."""

    @staticmethod
    def _target_graph(**params):
        return FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09", **params),
                   _n("c", "capture", x=100, filter="L", exposure=60,
                      gain=100, bin="1", count=5, goal=0)],
            edges=[_e("t", "target", "c", "run")])

    def test_a_camera_fixed_at_a_pa_is_found(self):
        """Mutant "key on the commanded angle" (``_single`` keys on the plan
        target's ``rotation_deg``, which is None for a fixed camera, as S1
        did) failed:
            E   ValueError: 1 plan target(s) match no block of this compile
                (M31): the plan was not compiled from it with
                flow_id='flow-progress-a', so every count read against it
                would be wrong
        """
        graph = self._target_graph(rotation=30, angle="Camera fixed at PA")
        compiled, plan = _compile(graph)
        target = plan.targets[0]
        assert target.rotation_deg is None, \
            "premise: a fixed camera commands no angle"
        got = flow_progress(compiled, plan,
                            _session(plan, _frames(target.steps[0].id, 2)),
                            flow_id=FLOW)
        assert got["blocks"][0]["panels"][0]["target_id"] == target.id
        assert got["blocks"][0]["banked"] == 2

    def test_a_nudged_target_is_found_through_its_anchor(self):
        """The save carried a 1' nudge, so the anchor is the old position and
        the ids are the ones the frames were banked on.

        Mutant "no anchor for a single target" (``_single`` passes no
        anchor, so it keys the position the block is drawn at now) failed:
            E   ValueError: 1 plan target(s) match no block of this compile
                (M31): the plan was not compiled from it with
                flow_id='flow-progress-a', so every count read against it
                would be wrong
        """
        from astrodeck.catalog.coords import parse_dec, parse_ra
        anchor = identity.anchor_for(
            {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09"},
            parse_ra("00h 42m 44s"), parse_dec("+41 16 09"), None,
            canonical=None)
        graph = self._target_graph(rotation=-1, frameAnchor=anchor)
        graph.nodes[0].params["dec"] = "+41 17 09"
        compiled, plan = _compile(graph)
        target = plan.targets[0]
        assert target.id == identity.target_id(identity.group_id(
            FLOW, "t", identity.anchor_key(anchor))), \
            "premise: to_plan keyed the target on its anchor"
        got = flow_progress(compiled, plan,
                            _session(plan, _frames(target.steps[0].id, 3)),
                            flow_id=FLOW)
        assert got["blocks"][0]["panels"][0]["target_id"] == target.id
        assert got["blocks"][0]["banked"] == 3


class TestThePayloadCarriesNoSiteData:
    """The route is ``CAP_VIEW_STATUS`` and a viewer can read it (spec 6.9).
    A key-name filter downstream cannot withhold a value this function computes
    and names itself (#19), so the payload is held to an allow-list HERE: any
    key added later has to be added to this list, in a diff someone reads."""

    ALLOWED = {
        "top": {"flow_id", "session", "blocks", "orphaned"},
        "session": {"id", "status", "nights", "count_mode"},
        "block": {"node_id", "name", "kind", "banked", "owed", "total",
                  "panels"},
        "panel": {"target_id", "name", "row", "col", "banked", "owed",
                  "total", "steps"},
        "step": {"step_id", "filter", "frame_type", "exposure_s", "count",
                 "banked", "owed"},
        "orphaned": {"frames", "steps"},
    }

    def _full_payload(self):
        """A TARGET and a POOL in one flow with a session, so every level of
        the payload is populated."""
        graph = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09", rotation=-1),
                   _n("p", "pool", x=50, members="M42, M31", minAlt=0,
                      moonSep=0, maxHA=0),
                   _n("c", "capture", x=100, filter="L", exposure=60,
                      gain=100, bin="1", count=5, goal=0)],
            edges=[_e("t", "target", "p", "arm"),
                   _e("p", "target", "c", "run")])
        compiled, plan = _compile(graph)
        frames = [f for t in plan.targets
                  for f in _frames(t.steps[0].id, 1)]
        return flow_progress(compiled, plan,
                             _session(plan, frames + _frames("gone", 2)),
                             flow_id=FLOW)

    def test_every_key_is_in_the_allow_list(self):
        """Mutant "add a transit altitude" (each panel gains
        ``transit_alt_deg``) failed:
            AssertionError: panel carries keys outside the allow-list
            assert {'banked', 'c... 'steps', ...} <= {'banked', 'c...
            'steps', ...}
              Extra items in the left set:
              'transit_alt_deg'
        """
        got = self._full_payload()
        assert [b["kind"] for b in got["blocks"]] == ["target", "pool"], \
            "premise: both kinds of block are present"
        seen = {level: set() for level in self.ALLOWED}
        seen["top"] |= set(got)
        seen["session"] |= set(got["session"])
        seen["orphaned"] |= set(got["orphaned"])
        for block in got["blocks"]:
            seen["block"] |= set(block)
            for panel in block["panels"]:
                seen["panel"] |= set(panel)
                for step in panel["steps"]:
                    seen["step"] |= set(step)
        for level, keys in seen.items():
            assert keys <= self.ALLOWED[level], \
                f"{level} carries keys outside the allow-list"
        assert all(seen.values()), "premise: every level was walked"

    def test_the_payload_is_json_ready(self):
        """The route returns this dict as it is, so it must survive JSON
        unchanged: no tuples, no models, no NaN.

        Mutant "steps as a tuple" (each panel's steps returned as a tuple)
        failed:
            AssertionError: assert {'blocks': [{...': 'dormant'}} ==
            {'blocks': [{...': 'dormant'}}
              Differing items:
              {'blocks': [{'banked': 1, 'kind': 'target', 'name': 'M31',
              'node_id': 't', ...}, {'banked': 2, 'kind': 'pool', 'name':
              'M42, M31', 'node_id': 'p', ...}]} != {'blocks': [{'banked': 1,
              'kind': 'target', 'name': 'M31', 'node_id': 't', ...},
              {'banked': 2, 'kind': 'pool', 'name': 'M42, M31', 'node_id':
              'p', ...}]}
        The two sides print alike because a tuple and a list of the same
        dicts abbreviate alike; they are unequal all the same.
        """
        got = self._full_payload()
        assert json.loads(json.dumps(got, allow_nan=False)) == got
