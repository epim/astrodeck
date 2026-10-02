# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The flow compiler warns, in the engine's own words, that a dusk/dawn
boundary needs a saved site (backlog ruling D-08, owner-approved
2026-09-30; #559, #582).

``compile_plan`` and ``to_sequence_plan`` are pure: no site, no clock beyond
``when`` (``to_plan.py``'s own module docstring). Neither can say whether a
site is actually saved, so instead of staying silent on a flow that WILL
refuse to start on an unconfigured rig (``sequence.engine.start``,
D-08's fail-closed refusal), ``to_sequence_plan`` names the dependency as a
compile-time note, in the EXACT words ``schedule.sun_window_needs_a_site``
would hand the engine for this same plan -- one function, shared by both
callers, so the two can never say it two different ways. The engine's own
refusal and its two-clause controls are covered by
``test_h4_dusk_unresolved_is_said.py``; this file covers only that the
compile step surfaces the identical sentence as a warning, and only when
the compiled schedule actually depends on dusk/dawn.

Mutant applied in a private byte-backed copy of this worktree's ``server/``
(scratchpad ``w9-WP-55-mut``), restored and sha256-checked after. Observed
failure quoted verbatim.
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import default_params
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence.schedule import sun_window_needs_a_site

#: A target card, wired to a capture card, every test below shares.
_TARGET = FlowNode(id="t", type="target", x=0, y=0,
                   params={**default_params("target"), "name": "M31",
                           "ra": "0h 42m 44s", "dec": "+41 16 09"})
_CAPTURE = FlowNode(id="c", type="capture", x=300, y=0,
                    params=default_params("capture"))
_WIRE = FlowEdge(**{"from": "t", "fromPort": "target",
                    "to": "c", "toPort": "run"})


def _dusk_graph(*, start: str = "Astro dusk", stop: str = "Dawn") -> FlowGraph:
    """One TARGET feeding one CAPTURE, with a DUSK WINDOW card."""
    dusk = FlowNode(id="d", type="dusk", x=0, y=200,
                    params={"start": start, "offset": 0, "startClock": "",
                            "stop": stop, "stopClock": "", "minAlt": 0})
    return FlowGraph(nodes=[_TARGET, _CAPTURE, dusk], edges=[_WIRE])


def _plain_graph() -> FlowGraph:
    """No DUSK WINDOW card at all: the compiled schedule is the plain
    ``start_mode: "now"`` default, which needs no site."""
    return FlowGraph(nodes=[_TARGET, _CAPTURE], edges=[_WIRE])


def _schedule_warnings(g: FlowGraph) -> tuple[list[str], list]:
    plan, unmapped = to_sequence_plan(compile_plan(g), g)
    return [n["detail"] for n in unmapped if n["key"] == "schedule"], plan.targets


def test_a_dusk_window_flow_is_warned_the_engines_own_sentence():
    """A DUSK WINDOW compiles to a dusk start and a dawn stop
    (``flows.compile._dusk_schedule``); the compile warns the IDENTICAL
    sentence ``sun_window_needs_a_site`` would hand the engine for a plan
    with this one target and this schedule.

    RED under mutant "warning dropped" (the ``to_sequence_plan`` call to
    ``sun_window_needs_a_site`` taken out), observed:

        AssertionError: no compile warning named the site dependency: []
    """
    warned, targets = _schedule_warnings(_dusk_graph())
    assert warned, f"no compile warning named the site dependency: {warned}"
    expected = sun_window_needs_a_site(targets)
    assert expected is not None, "premise: this plan's schedule needs a site"
    assert warned == [expected], warned


def test_control_a_plain_now_start_flow_is_not_warned():
    """CONTROL. No DUSK WINDOW card: the compile says nothing about a site,
    since nothing here depends on one.

    RED under mutant "warned unconditionally" (the ``sun_window_needs_a_site
    (...) is not None`` guard dropped, the note appended regardless -- for a
    plan with nothing to warn about, ``sun_window_needs_a_site`` answers
    None, so the dropped note carries ``None`` for its detail), observed:

        AssertionError: a plain now-start flow was warned about a site:
        [None]
    """
    warned, _targets = _schedule_warnings(_plain_graph())
    assert warned == [], (
        f"a plain now-start flow was warned about a site: {warned}")


def test_control_a_dusk_window_with_no_stop_is_warned_the_start_clause_only():
    """CONTROL. A DUSK WINDOW with Stop "None" compiles to a dusk start and
    NO stop (``_dusk_schedule``'s own "Stop is None" branch, stop_mode
    "none"): the shared sentence names only the start clause, exactly as it
    does for the engine's own refusal (see
    ``test_h4_dusk_unresolved_is_said.py``'s matching control).
    """
    warned, targets = _schedule_warnings(_dusk_graph(stop="None"))
    assert warned, f"no compile warning for a dusk start: {warned}"
    expected = sun_window_needs_a_site(targets)
    assert warned == [expected], warned
    assert "find dusk to start by" in warned[0]
    assert "find dawn to stop by" not in warned[0], warned[0]
