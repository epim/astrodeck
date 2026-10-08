# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The Target modal's RUN readouts, answered by the compile (#189 spec 2.4
RUN, S4 item 1; 5.3 visit bound, 5.5, 5.7 cost 1, A.3, A.4, 5.6 step 6, 6.9).

"Every number comes from the server compile, never computed in the client"
(spec 2.4). ``flows/readouts.py`` computes them, purely, from the plan the
preview compiled, the compile entry, the rig facts, the plan's flip lead and
the two focus settings; ``_compile_payload`` answers them as ``readouts``
(per TARGET block, keyed by node id) and ``rig``.

What is graded here, each against a number the spec computed or a rule it
states:

* the spec's 3x2 on the shipped default cycle: 1890 subs, 9.75 h per panel,
  58.5 h in all and 270 visits (5.5, A.3);
* the pre-flip idle with cut visits (5.7 cost 1, A.4): 0 min for the 3x2 of
  2.0 x 1.33 deg at Dec 41, 10.6 min for the 2x2 of 0.9 x 0.6 deg;
* the visit bound ``max(passes, ceil(minVisit x 60 / pass_s))`` (5.3):
  ``minVisit`` 30 on a 13-minute pass is 3 passes;
* the efficiency, present only with a measured hop (2.4: "the efficiency
  figure appears only with a measured hop");
* the angle tolerance, ``framing.angle_tolerance_deg``'s;
* the focus line's three readings (5.6 step 6);
* which plan targets are which block's, through a dropped block, a block
  whose every panel is skipped and a pool;
* NO SITE DATA (6.9, the #19 class): the readouts are byte-identical under
  two synthetic sites and with the hub reading a third;
* the eighth Example's answer is ``fixtures/flow_readouts_m31.json``, which
  the modal's UI tests (UMODAL) read, so the UI is tested against the
  numbers the server really answers.

THE FIXTURE is the route's answer for the eighth Example on a rig that knows
nothing (no optics, no measured hop, no profile), ``{readouts, rig}`` as
``_compile_payload`` answers them, written by ``readouts`` and
``rig_readout`` themselves and never by hand. When the answer changes on
purpose, regenerate it from the code and say why in the commit.

Each mutation was applied to a byte-for-byte copy of the file it changes in a
private copy of ``server/`` (scratchpad ``s4-routes-mut``), never in the
shared tree, and only this file was run against it; failures are quoted as
observed (``--tb=short``), wrapped to fit.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.catalog import framing
from astrodeck.config import ConfigStore, Site
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.readouts import (FOCUS_FRAMES, FOCUS_ONCE,
                                      FOCUS_TEMPERATURE, readouts,
                                      rig_readout, visit_passes)
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.store import FlowStore
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.engine import HOP_COST_S
from astrodeck.sequence.models import SequencePlan

FIXTURE = Path(__file__).parent / "fixtures" / "flow_readouts_m31.json"

#: The synthetic sites of tests/test_no_route_leaks_the_site_coordinates.py,
#: and a third for the hub: far from 0 and from each other, not round, and
#: no rounding of one is a rounding of another.
SITE_A = (41.2345678, -73.9876543)
SITE_B = (12.3456789, -45.6789012)
SITE_C = (-33.8765432, 151.2345678)


# ------------------------------------------------------------------ graphs

def _target(node_id: str = "t", **over) -> dict:
    """A TARGET at Dec +41, laid out at PA 0 for the rotator: a 3x2 of 2.0 x
    1.33 deg panels at 25% overlap unless ``over`` says otherwise (A.1's and
    A.4's panels; RA is M31's, and the RA span does not depend on it)."""
    return {"id": node_id, "type": "target", "x": 0, "y": 0,
            "params": {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 00 00",
                       "rows": 2, "cols": 3, "overlap": 25, "fovX": 2.0,
                       "fovY": 1.33, "angle": "Rotate to PA", "rotation": 0,
                       **over}}


def _looped(**over) -> dict:
    """The TARGET feeding the SHIPPED DEFAULT FILTER CYCLE (L R G B at 60 s,
    Ha OIII SII at 180 s, 45 cycles, one per pass: its ``params`` are left
    to the node's defaults) with the loop wire, so the panels rotate."""
    return {"nodes": [_target(**over),
                      {"id": "c", "type": "cycle", "x": 0, "y": 0,
                       "params": {}}],
            "edges": [{"from": "t", "fromPort": "target", "to": "c",
                       "toPort": "run"},
                      {"from": "c", "fromPort": "pass", "to": "t",
                       "toPort": "next"}]}


def _capture(node_id: str, filt: str = "L", *, exposure: float = 60.0,
             count: int = 3) -> dict:
    return {"id": node_id, "type": "capture", "x": 0, "y": 0,
            "params": {"filter": filt, "exposure": exposure, "gain": 100,
                       "bin": "1", "count": count, "goal": 0}}


def _answer(graph: dict, rig: RigFacts | None = None, **focus) -> dict:
    """``readouts`` for ``graph`` compiled as the preview compiles an
    unsaved draft (no flow id)."""
    g = FlowGraph.model_validate(graph)
    compiled = compile_plan(g, "x")
    plan, _ = to_sequence_plan(compiled, g, rig=rig)
    return readouts(compiled, plan, rig, **focus)


def _diff(a: dict, b: dict, path: str = "") -> list[str]:
    """Every leaf that differs between two answers, as ``path: a != b``, so
    a failure names the readout that moved instead of pytest's truncated
    ``{'readouts': ...} != {'readouts': ...}``."""
    out: list[str] = []
    for k in sorted(set(a) | set(b)):
        here = f"{path}.{k}" if path else str(k)
        x, y = a.get(k, "<absent>"), b.get(k, "<absent>")
        if isinstance(x, dict) and isinstance(y, dict):
            out.extend(_diff(x, y, here))
        elif x != y:
            out.append(f"{here}: {x!r} != {y!r}")
    return out


def _block(graph: dict, rig: RigFacts | None = None, node: str = "t",
           **focus) -> dict:
    got = _answer(graph, rig, **focus)
    assert node in got, f"premise: block {node!r} has readouts, got {got}"
    return got[node]


# ============================================================ the arithmetic

class TestTheSpecsThreeByTwo:
    def test_the_default_cycle_on_a_3x2(self):
        """Spec 5.5 and A.3, the RUN section's worked readouts: "6 panels x
        7 filters x 45 = 1890 subs", "9.75 h per panel, 58.5 h in all",
        "270 visits at 1 pass per visit"."""
        b = _block(_looped())
        assert (b["mode"], b["panels"], b["steps"], b["rounds"]) == \
            ("rotate", 6, 7, 45)
        assert (b["subs_per_panel"], b["subs_total"]) == (315, 1890)
        assert b["pass_s"] == 780.0, "one pass: 4 x 60 + 3 x 180"
        assert b["panel_s"] / 3600 == 9.75
        assert b["total_s"] / 3600 == 58.5
        assert (b["visit_passes"], b["visits_per_panel"],
                b["visits_total"]) == (1, 45, 270)

    def test_a_block_that_does_not_rotate_visits_each_panel_once(self):
        """No loop wire: panel-first ("sequential"), one visit per panel, the
        engine's own rule (``_visits_owed``); the visit bound does not apply,
        so it is null rather than a pass count nobody runs."""
        graph = _looped()
        graph["edges"] = graph["edges"][:1]
        b = _block(graph)
        assert b["mode"] == "sequential"
        assert (b["visit_passes"], b["visits_per_panel"],
                b["visits_total"]) == (None, 1, 6)
        assert b["subs_total"] == 1890

    def test_a_single_target_has_no_group_numbers(self):
        """A TARGET of one panel owns its stages too, so it has a RUN
        section, but no hops, no group rule and no layout: the group numbers
        are null, and a CAPTURE LOOP has no pass.

        RED under mutation "a single reports the rig's hop" (a single
        block's ``hop_measured`` set from the rig facts), observed:

            AssertionError: ('hop_measured', False)
            assert False is None
        """
        graph = {"nodes": [{"id": "t", "type": "target", "x": 0, "y": 0,
                            "params": {"name": "M42", "ra": "05h 35m 17s",
                                       "dec": "-05 23 28"}},
                           _capture("c1", count=3)],
                 "edges": [{"from": "t", "fromPort": "target", "to": "c1",
                            "toPort": "run"}]}
        b = _block(graph)
        assert b["mode"] == "single"
        assert (b["panels"], b["steps"], b["subs_total"], b["panel_s"]) == \
            (1, 1, 3, 180.0)
        for key in ("rounds", "pass_s", "visit_passes", "visits_total",
                    "hop_s", "hop_measured", "preflip_idle_s",
                    "angle_tolerance_deg"):
            assert b[key] is None, (key, b[key])
        assert "efficiency" not in b
        # ... WHATEVER THE RIG KNOWS: a block that makes no hop reads the
        # same with a measured one, so a flow with no mosaic compiles to the
        # same readouts on every rig (only the ``rig`` block says what the
        # rig knows).
        assert _block(graph, RigFacts(hop_cost_s=160.0, hop_samples=6)) == b


class TestThePreflipIdle:
    def test_a4_three_by_two_at_dec_41_idles_zero(self):
        """A.4, cut visits: the 3x2 of 2.0 x 1.33 deg at Dec 41 spans 16.0
        min of RA, more than lead + hop + one frame (10 + 2.5 + 1.67 min),
        so "meridian: no idle before the flip".

        RED under mutation "idle from the whole-visit formula"
        (``preflip_idle_cut_h`` replaced by ``preflip_idle_whole_h`` over a
        one-pass ``whole_visit_s``), observed:

            AssertionError: 10.7 min against 0
            assert 10.7 == 0.0
        """
        b = _block(_looped())
        minutes = round(b["preflip_idle_s"] / 60.0, 1)
        assert minutes == 0.0, f"{minutes} min against 0"

    def test_a4_compact_two_by_two_idles_ten_point_six(self):
        """A.4, cut visits: the 2x2 of 0.9 x 0.6 deg at Dec 41 spans 3.6
        min, so "meridian: up to 10.6 min idle before the flip".

        RED under mutation "idle from the whole-visit formula", observed:

            AssertionError: 23.1 min against 10.6
            assert 23.1 == 10.6
        """
        b = _block(_looped(rows=2, cols=2, fovX=0.9, fovY=0.6))
        minutes = round(b["preflip_idle_s"] / 60.0, 1)
        assert minutes == 10.6, f"{minutes} min against 10.6"

    def test_the_idle_is_priced_with_the_measured_hop(self):
        """A measured hop replaces the seed in the idle: at 600 s the 3x2
        idles 10 + 10 + 1.67 - 15.97 = 5.7 min. The seed is the engine's
        (``HOP_COST_S``), with ``hop_measured`` false.

        RED under mutation "idle from the whole-visit formula", observed:

            assert 18.2 == 5.7
             +  where 18.2 = round((1091.7 / 60.0), 1)
        """
        seed = _block(_looped())
        assert (seed["hop_s"], seed["hop_measured"]) == (HOP_COST_S, False)
        slow = _block(_looped(), RigFacts(hop_cost_s=600.0, hop_samples=3))
        assert (slow["hop_s"], slow["hop_measured"]) == (600.0, True)
        assert round(slow["preflip_idle_s"] / 60.0, 1) == 5.7

    def test_the_idle_reads_the_plans_own_lead(self):
        """The margin is the PLAN's lead (spec 5.7, "never the learned one"),
        so a plan asking for 20 min idles 10 min longer than one at the
        default 10: minute for minute. No flow sets the lead today (every
        flow plan compiles at the default), so the plan is re-validated with
        one; every other test here runs at the default and could not tell the
        plan's lead from the schedule's constant.

        Added by the S4-ROUTES verifier (second pass): the mutation below left
        every other test in this file green.

        RED under mutation "the idle ignores the plan's lead"
        (``plan_lead_s``'s ``getattr(plan, "meridian_flip_lead_min", None)``
        -> ``None``, so every plan reads the default), observed:

            AssertionError: a 20 min lead idled 635.2 s against 635.2 s at
            the default
            assert 0.0 == 600.0
             +  where 0.0 = round((635.2 - 635.2), 1)
        """
        g = FlowGraph.model_validate(_looped(rows=2, cols=2, fovX=0.9,
                                             fovY=0.6))
        compiled = compile_plan(g, "x")
        plan, _ = to_sequence_plan(compiled, g)
        assert plan.meridian_flip_lead_min == 10.0, (
            "premise: a flow plan compiles at the default lead")
        base = readouts(compiled, plan, None)["t"]["preflip_idle_s"]
        later = SequencePlan.model_validate(
            {**plan.model_dump(), "meridian_flip_lead_min": 20.0})
        got = readouts(compiled, later, None)["t"]["preflip_idle_s"]
        assert round(got - base, 1) == 600.0, (
            f"a 20 min lead idled {got} s against {base} s at the default")

    def test_a_plan_that_does_not_flip_has_no_idle(self):
        """No flip, no pre-flip idle: the readout is null, not the idle a
        flipping plan would have (10.6 min for this compact 2x2), which the
        modal would print as "up to 10.6 min idle before the flip" for a
        night that never flips. The rest of the block is unchanged.

        Added by the S4-ROUTES verifier (second pass), for the same reason
        as the test above.

        RED under mutation "the idle ignores meridian_flip" (both
        ``meridian_flip=bool(plan.meridian_flip)`` in ``readouts`` ->
        ``meridian_flip=True``), observed:

            AssertionError: a plan that does not flip idles 635.2 s
            assert 635.2 is None
        """
        g = FlowGraph.model_validate(_looped(rows=2, cols=2, fovX=0.9,
                                             fovY=0.6))
        compiled = compile_plan(g, "x")
        plan, _ = to_sequence_plan(compiled, g)
        flips = readouts(compiled, plan, None)["t"]
        assert flips["preflip_idle_s"] and flips["preflip_idle_s"] > 0, (
            "premise: the flipping plan idles before its flip")
        still = SequencePlan.model_validate(
            {**plan.model_dump(), "meridian_flip": False})
        b = readouts(compiled, still, None)["t"]
        assert b["preflip_idle_s"] is None, (
            f"a plan that does not flip idles {b['preflip_idle_s']} s")
        assert {k: v for k, v in b.items() if k != "preflip_idle_s"} == \
            {k: v for k, v in flips.items() if k != "preflip_idle_s"}


class TestTheVisitBound:
    def test_min_visit_30_on_a_13_minute_pass_is_3_passes(self):
        """Spec 5.3: a visit ends at a round boundary once it has made
        ``passes`` rounds AND run ``minVisit``, so 30 min on a 13-minute
        pass is 3 passes; 45 rounds at 3 per visit is 15 visits a panel.

        RED under mutation "visits ignore minVisit" (``visit_passes``
        returns ``passes``), observed:

            assert 1 == 3
             +  where 1 = visit_passes(1, (30 * 60.0), 780.0)

        and in the next test, ``assert 1 == 2`` for ``visit_passes(1, (26 *
        60.0), 780.0)``.
        """
        assert visit_passes(1, 30 * 60.0, 780.0) == 3
        b = _block(_looped(minVisit=30))
        assert (b["passes"], b["visit_min_s"]) == (1, 1800.0)
        assert (b["visit_passes"], b["visits_per_panel"],
                b["visits_total"]) == (3, 15, 90)

    def test_the_bound_is_the_larger_and_exact_on_a_boundary(self):
        """``max``: 4 passes outlast 30 min, so 4. Exactly 2 passes of
        minimum (26 min of 13) is 2, not 3 by a float's last place. No
        minimum, or no pass to count, leaves ``passes``."""
        assert visit_passes(4, 1800.0, 780.0) == 4
        assert visit_passes(1, 26 * 60.0, 780.0) == 2
        assert visit_passes(2, 0.0, 780.0) == 2
        assert visit_passes(2, 1800.0, 0.0) == 2


class TestTheEfficiency:
    def test_absent_until_a_hop_is_measured(self):
        """2.4: "The efficiency figure appears only with a measured hop."
        With the seed it is ABSENT, not null, so no reading of the answer
        prints a figure nobody timed.

        RED under mutation "efficiency with the seed hop" (``if
        hop_measured:`` -> ``if True:``), observed:

            AssertionError: the seed hop priced an efficiency: 0.8387
            assert 'efficiency' not in {'angle_tolerance_deg': 5.974,
            'autofocus_every': 0, 'efficiency': 0.8387, 'focus': 'once',
            ...}
        """
        b = _block(_looped())
        assert "efficiency" not in b, (
            f"the seed hop priced an efficiency: {b.get('efficiency')}")

    def test_with_a_measured_hop_it_is_shutter_over_shutter_plus_hops(self):
        """A.3's ``4K / (4K + H)`` over the whole block: 58.5 h of shutter
        and 270 hops of 160 s."""
        b = _block(_looped(), RigFacts(hop_cost_s=160.0, hop_samples=6))
        assert b["efficiency"] == round(210600 / (210600 + 270 * 160), 4)


class TestTheAngleAndTheFocus:
    def test_the_angle_tolerance_is_frameings(self):
        """The group's ``angle_tolerance_deg``, which ``to_plan`` takes from
        ``framing.angle_tolerance_deg``. This block rotates and turns 1.3 deg
        at its corners, over the rotator's 1 deg, so its panels are each
        commanded their own angle (#175) and convergence takes nothing from
        the budget: the single-panel figure, about 6.4 deg for 2.0 x 1.33 deg
        at 25% (A.2's k = 0.5 table), where a fixed 3x2 at Dec 41 keeps 6.0."""
        b = _block(_looped())
        spec = {"ra_hours": framing_ra(), "dec_deg": 41.0, "rows": 1,
                "cols": 1, "overlap": 0.25, "rotation_deg": 0.0,
                "fov_x_deg": 2.0, "fov_y_deg": 1.33}
        assert b["angle_tolerance_deg"] == round(
            framing.angle_tolerance_deg(spec), 3)
        assert 6.3 < b["angle_tolerance_deg"] < 6.4

    @pytest.mark.parametrize("focus, expected", [
        ({}, FOCUS_ONCE),
        ({"autofocus_every": 30}, FOCUS_FRAMES),
        ({"refocus_on_temp_delta_c": 1.0}, FOCUS_TEMPERATURE),
        ({"autofocus_every": 30, "refocus_on_temp_delta_c": 1.0},
         FOCUS_TEMPERATURE)])
    def test_the_focus_line(self, focus, expected):
        """5.6 step 6: "focus: refocus on temperature" when the delta is
        armed, every N frames when only that is set, and otherwise "a sweep
        only at the first panel" (a hop owes no sweep then)."""
        b = _block(_looped(), **focus)
        assert b["focus"] == expected
        assert b["autofocus_every"] == focus.get("autofocus_every", 0)
        assert b["refocus_delta_c"] == focus.get("refocus_on_temp_delta_c",
                                                 0.0)


def framing_ra() -> float:
    """00h 42m 44s in hours, the TARGET's RA as ``to_plan`` parses it."""
    return 42 / 60 + 44 / 3600


# ======================================================= which block is which

class TestWhichTargetsAreWhichBlocks:
    def test_a_dropped_block_never_takes_its_namesakes_target(self):
        """Two TARGETs called M31; the first has an RA that does not parse,
        so ``to_plan`` drops it and the plan holds the second's target only.
        The readouts are the second's, keyed by its node id; matched on
        names, the dropped block would have taken them.

        RED under mutation "no drop test" (the ``_coords(entry, when) is
        None`` check made ``False``), observed:

            AssertionError: assert {'t1'} == {'t2'}
              Extra items in the left set:
              't1'
              Extra items in the right set:
              't2'
        """
        bad = {"id": "t1", "type": "target", "x": 0, "y": 0,
               "params": {"name": "M31", "ra": "not an angle",
                          "dec": "+41 16 09"}}
        good = {"id": "t2", "type": "target", "x": 0, "y": 500,
                "params": {"name": "M31", "ra": "00h 42m 44s",
                           "dec": "+41 16 09"}}
        graph = {"nodes": [bad, _capture("c1"), good, _capture("c2")],
                 "edges": [{"from": "t1", "fromPort": "target", "to": "c1",
                            "toPort": "run"},
                           {"from": "t2", "fromPort": "target", "to": "c2",
                            "toPort": "run"}]}
        assert set(_answer(graph)) == {"t2"}

    def test_a_block_with_every_panel_skipped_is_passed_over(self):
        """A mosaic whose ``skip`` names every panel shoots nothing and makes
        no group (``to_plan``); the TARGET after it still has its readouts.

        RED under mutation "no all-skipped rule" (the walk's
        ``_every_panel_skipped(entry)`` made ``False``), observed:

            AssertionError: assert set() == {'t2'}
              Extra items in the right set:
              't2'
        """
        skipped = _target("t1", rows=2, cols=1, skip="1-1, 2-1")
        single = {"id": "t2", "type": "target", "x": 0, "y": 500,
                  "params": {"name": "M42", "ra": "05h 35m 17s",
                             "dec": "-05 23 28"}}
        graph = {"nodes": [skipped, _capture("c1"), single, _capture("c2")],
                 "edges": [{"from": "t1", "fromPort": "target", "to": "c1",
                            "toPort": "run"},
                           {"from": "t2", "fromPort": "target", "to": "c2",
                            "toPort": "run"}]}
        assert set(_answer(graph)) == {"t2"}

    def test_a_pool_is_walked_past_and_has_no_readouts(self):
        """A POOL's members are plan targets but not a TARGET block: they
        are consumed in order and answered nothing, and the capture after
        the pool is not the ARMING target's either.

        RE-PINNED (W2 integration, backlog WP-19(a), #151, owner-approved
        2026-09-30): this graph has TWO owner-type blocks (``t``, a TARGET,
        and ``p``, a POOL -- ``OWNER_TYPES`` names both), so
        ``needs_wire_scoping`` now turns on for it, same as any other
        two-owner flow. The capture's wire leads to ``p`` (``p.target ->
        c.run``), not to ``t`` (``t`` only ARMS the pool, on the ``arm``
        port), so ``owner_of`` now correctly gives it to the POOL, which
        means it is no longer ``t``'s 5 subs at all -- those subs are the
        pool's members' (M31, M33), which readouts does not report per
        block (the pool itself gets no entry, by the same design this
        test's own docstring already named). ``t`` keeps its entry (every
        TARGET does) but now honestly reports owning nothing, rather than
        the pool's capture it never actually ran. Before #151 this flow's
        only owner was ``t`` (a POOL was never a mosaic, so wire scoping
        never turned on for a lone POOL+TARGET pair), so canvas order gave
        the capture to ``t`` by position -- the same class of leak #151
        closes elsewhere, here showing up as a target credited with frames
        a pool actually ran.
        """
        graph = {"nodes": [
            {"id": "t", "type": "target", "x": 0, "y": 0,
             "params": {"name": "M42", "ra": "05h 35m 17s",
                        "dec": "-05 23 28"}},
            {"id": "p", "type": "pool", "x": 50, "y": 0,
             "params": {"members": "M31, M33", "minAlt": 0, "moonSep": 0,
                        "maxHA": 0}},
            _capture("c", count=5)],
            "edges": [{"from": "t", "fromPort": "target", "to": "p",
                       "toPort": "arm"},
                      {"from": "p", "fromPort": "target", "to": "c",
                       "toPort": "run"}]}
        got = _answer(graph)
        assert set(got) == {"t"}
        assert got["t"]["subs_total"] == 0, (
            "the capture belongs to the POOL's wire, not to the arming "
            "TARGET, so t must not be credited with the pool's subs")

    def test_a_refused_compile_has_no_readouts(self):
        """No plan (the compile refused a half-built graph): nothing to
        read, an empty answer, never an error."""
        assert readouts({"targets": []}, None, None) == {}


class TestTheRigReadout:
    def test_unknown_is_null_never_the_seed(self):
        """A rig nobody measured: every fact null, the hop null with 0
        samples, never the engine's 150 s seed."""
        assert rig_readout(None) == rig_readout(RigFacts()) == {
            "fov_deg": None, "fov_from": "", "has_rotator": None,
            "hop_s": None, "hop_samples": 0, "hop_measured": False}

    def test_known_facts_are_carried(self):
        got = rig_readout(RigFacts(fov_deg=(1.346, 0.9), fov_from="profile R",
                                   hop_cost_s=160.04, hop_samples=6,
                                   has_rotator=True))
        assert got == {"fov_deg": [1.346, 0.9], "fov_from": "profile R",
                       "has_rotator": True, "hop_s": 160.0, "hop_samples": 6,
                       "hop_measured": True}


# ================================================================ the route

@pytest.fixture
def client(isolated_config, tmp_path, monkeypatch):
    """conftest's ``isolated_config`` (the store swept into every module, no
    optics, no profile, no camera or rotator on the hub), a flow library of
    the test's own, and no hop measured: the module's engine outlives every
    test in the process."""
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    monkeypatch.setattr(app_module.engine, "_event_costs", {})
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        c.store = isolated_config.store
        c.tmp = tmp_path
        yield c


def _compile(client, graph: dict) -> dict:
    r = client.post("/api/flows/compile", json={"graph": graph, "name": "m"})
    assert r.status_code == 200, r.text
    return r.json()


def _site(store: ConfigStore, where: tuple[float, float]) -> None:
    store.set_site(Site(name="fixture", latitude=where[0], longitude=where[1],
                        elevation_m=10.0, is_default=False))


class TestTheRoute:
    def test_the_compile_answers_readouts_and_the_rig(self, client):
        """``_compile_payload`` adds both, computed by ``flows.readouts``
        from the plan it compiled and the rig facts it read."""
        out = _compile(client, _looped())
        assert out["readouts"]["t"]["subs_total"] == 1890
        assert out["rig"] == rig_readout(None), (
            "premise: this rig knows nothing")

    def test_the_route_passes_the_resolved_temperature_delta(self, client):
        """The focus line reads the delta the run would: the rig's standing
        ``refocus_on_temp_delta_c`` as ``resolve_policy`` resolves it.

        RED under mutation "focus delta not passed" (the route hands
        ``refocus_on_temp_delta_c=0.0``), observed:

            AssertionError: assert 'once' == 'temperature'
              - temperature
              + once
        """
        client.store.set_standards(client.store.cfg().standards.model_copy(
            update={"refocus_on_temp_delta_c": 1.5}))
        b = _compile(client, _looped())["readouts"]["t"]
        assert b["focus"] == FOCUS_TEMPERATURE
        assert b["refocus_delta_c"] == 1.5

    def test_the_readouts_do_not_move_with_the_site(self, client,
                                                    monkeypatch):
        """No site data (spec 6.9, the #19 class): the same flow compiled
        under two synthetic sites, and with the hub itself reading a third
        (its module's store moved to one at ``SITE_C``, so a readout taken
        through ``hub.site`` would see neither configured site), answers the
        same ``readouts`` and ``rig`` byte for byte. Each move is shown to
        reach where a route would read it, so the equality is not the
        vacuum of a site that never moved.

        RED under mutation "readouts read hub.site" (each block gains
        ``transit_alt_deg = round(90 - |hub.site latitude - first panel
        dec|, 3)`` in ``_compile_payload``'s answer), observed:

            AssertionError: the readouts moved with site B:
            ['readouts.t.transit_alt_deg: 89.753 != 60.864']

        RED under mutation "readouts read the configured site" (the same
        from ``config_store.cfg().site``), observed the same line. Both
        fail at site B first, because the hub follows the swept store; the
        third reading (the hub's own store at ``SITE_C``, the configured
        site back at A) is the belt to that brace, for a reading that
        reaches the hub by some path that does not follow the store.
        """
        graph = _looped()
        answers: list[dict] = []
        for where in (SITE_A, SITE_B):
            _site(client.store, where)
            assert app_module.hub.site["latitude"] == where[0], (
                "premise: the hub reads the site this test configured")
            out = _compile(client, graph)
            answers.append({"readouts": out["readouts"], "rig": out["rig"]})
        _site(client.store, SITE_A)
        third = ConfigStore(path=client.tmp / "third" / "astrodeck.json")
        _site(third, SITE_C)
        monkeypatch.setattr(hub_module, "config_store", third)
        assert app_module.hub.site["latitude"] == SITE_C[0], (
            "premise: the hub reads the third site")
        out = _compile(client, graph)
        answers.append({"readouts": out["readouts"], "rig": out["rig"]})
        got = answers[0]["readouts"]["t"]
        assert got["preflip_idle_s"] is not None and \
            got["angle_tolerance_deg"] is not None, (
                "premise: the numbers nearest the sky are in the answer")
        for other, label in ((answers[1], "site B"),
                             (answers[2], "the hub at a third site")):
            assert json.dumps(answers[0], sort_keys=True) == json.dumps(
                other, sort_keys=True), (
                f"the readouts moved with {label}: {_diff(answers[0], other)}")


# ============================================================== the fixture

class TestTheEighthExamplesFixture:
    def test_the_pure_answer_is_the_fixture(self):
        """The eighth Example, "M31 3x2, rotating", on a rig that knows
        nothing: ``{readouts, rig}`` equals the fixture UMODAL's tests read.

        RED under mutation "fixture hand-edited" (``"visits_total": 120``
        -> ``121`` in the fixture), observed:

            AssertionError: the fixture is not what the server answers:
            ['readouts.n2.visits_total: 121 != 120']

        and, since the fixture pins every number, under "efficiency with
        the seed hop" (``["readouts.n2.efficiency: '<absent>' !=
        0.7619"]``), "idle from the whole-visit formula"
        (``['readouts.n2.preflip_idle_s: 365.2 != 725.2']``) and both site
        mutations of the route test (``["readouts.n2.transit_alt_deg:
        '<absent>' != 49.37"]``, through the route) too.
        """
        ex = next(e for e in examples() if e.id == "example-m31-mosaic")
        compiled = compile_plan(ex.graph, ex.name)
        plan, _ = to_sequence_plan(compiled, ex.graph, flow_id=ex.id)
        got = {"readouts": readouts(compiled, plan, None),
               "rig": rig_readout(None)}
        held = json.loads(FIXTURE.read_text(encoding="utf-8"))
        assert held == got, (
            f"the fixture is not what the server answers: {_diff(held, got)}")
        b = got["readouts"]["n2"]
        assert (b["panels"], b["steps"], b["rounds"], b["visits_total"]) == \
            (6, 4, 20, 120), "premise: 6 panels of LRGB x 20, rotating"

    def test_the_route_answers_the_fixture(self, client):
        """The same numbers through ``/api/flows/{id}/compile``: the fixture
        is the route's answer, not only the pure function's."""
        out = client.post("/api/flows/example-m31-mosaic/compile")
        assert out.status_code == 200, out.text
        held = json.loads(FIXTURE.read_text(encoding="utf-8"))
        got = {"readouts": out.json()["readouts"], "rig": out.json()["rig"]}
        assert got == held, (
            f"the route does not answer the fixture: {_diff(held, got)}")

    def test_the_fixture_is_utf8_json_without_a_bom(self):
        raw = FIXTURE.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert math.isfinite(json.loads(raw)["readouts"]["n2"]["total_s"])


# ======================================================= a single target

SINGLE_FIXTURE = (Path(__file__).parent / "fixtures"
                  / "flow_readouts_single.json")


class TestASingleTargetExamplesFixture:
    """The Example ``example-cycle`` ("M33 - LRGBSHO cycle"), one TARGET of
    one panel owning the shipped FILTER CYCLE, on a rig that knows nothing:
    ``fixtures/flow_readouts_single.json``, which the modal's reader test
    (framingReadoutsFixture.test.ts) reads.

    WHY A SECOND FIXTURE (#409). The eighth Example is the only block the
    first fixture holds, and it is a mosaic, so every value in it is a
    number or a boolean. A single target's group fields are null, ``hop_measured``
    included (``test_a_single_target_has_no_group_numbers`` holds that),
    and the modal's reader required a boolean there: five of the eight
    shipped Examples, and every single-target TARGET with a stage, read in
    the RUN section as "the compile's RUN numbers could not be read:
    readouts.n2.hop_measured is not a boolean". The UI's own single-target
    record was built by hand with ``hop_measured: false``, so no case saw
    it. The UI now reads this answer, written by the server, not by hand.
    """

    def test_the_pure_answer_is_the_fixture(self):
        """RED under mutation "single fixture hand-edited to the UI's old
        double" (``"hop_measured": null`` -> ``false`` in the fixture's
        block), observed:

            AssertionError: the fixture is not what the server answers:
            ['readouts.n2.hop_measured: False != None']
        """
        ex = next(e for e in examples() if e.id == "example-cycle")
        compiled = compile_plan(ex.graph, ex.name)
        plan, _ = to_sequence_plan(compiled, ex.graph, flow_id=ex.id)
        got = {"readouts": readouts(compiled, plan, None),
               "rig": rig_readout(None)}
        held = json.loads(SINGLE_FIXTURE.read_text(encoding="utf-8"))
        assert held == got, (
            f"the fixture is not what the server answers: {_diff(held, got)}")
        b = got["readouts"]["n2"]
        assert (b["mode"], b["panels"], b["steps"], b["rounds"]) == \
            ("single", 1, 7, 45), "premise: one panel of LRGBSHO x 45"
        assert b["hop_measured"] is None and b["hop_s"] is None, (
            "premise: the null the modal's reader must take")

    def test_the_route_answers_the_fixture(self, client):
        """The same answer through ``/api/flows/{id}/compile``, so the
        fixture is the route's and not only the pure function's."""
        out = client.post("/api/flows/example-cycle/compile")
        assert out.status_code == 200, out.text
        held = json.loads(SINGLE_FIXTURE.read_text(encoding="utf-8"))
        got = {"readouts": out.json()["readouts"], "rig": out.json()["rig"]}
        assert got == held, (
            f"the route does not answer the fixture: {_diff(held, got)}")

    def test_the_fixture_is_utf8_json_without_a_bom(self):
        raw = SINGLE_FIXTURE.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert json.loads(raw)["readouts"]["n2"]["hop_measured"] is None
