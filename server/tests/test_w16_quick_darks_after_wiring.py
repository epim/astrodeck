# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""What the quick sheet's DARKS AFTER chip actually funds (WP-144, #745).

The chip's hold-to-learn card (``ui/src/next/hubs/sky/sheets/quickCopy.ts``,
``INFO.darks``) used to say "Queues darks and bias at the end of the night".
``w16QuickDarksCopy.test.ts`` holds the card's words and the UI's wiring
(``flowGraphExtras.withDarksAfter`` draws ``report.done -> calib.do``). This
file holds the half only the server can answer: what the compile of THAT wiring
funds in the plan.

The wiring is mirrored here by hand (a CALIBRATION QUEUE with darks and bias
``Always`` and flats ``Skip``, started by the REPORT's ``done`` event), onto the
graph ``wizard.quick`` really generates. The UI test pins the other end of the
mirror: it fails if ``withDarksAfter`` stops drawing that wire, which is when
this mirror and the card both need to be read again.

THE FINDING THIS PINS. The engine's end-of-night darks are the wind-down's
day-darks lane (``plan.day_darks``), funded by ``to_plan.plan_extras`` only for
a queue wired to SHUTDOWN COMPLETE (a PARK + CLOSE node's ``closed`` port). The
chip wires the queue from the report instead, which compiles to the rule
``on_target_complete -> calib`` that the engine has no action for. So the chip
funds ``cloud_hold_darks`` (a cloud hold takes darks) and not ``day_darks``:
the card may promise the first and must not promise the second.
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowNode
from astrodeck.flows.nodes import create_params
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.wizard import quick

_TARGET = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41° 16' 09\""}


def _quick_with_darks_after():
    """The quick flow with ``withDarksAfter``'s edit applied, mirrored."""
    graph = quick(_TARGET, subs_per_filter=10, filters=["L", "R"],
                  wheel=["L", "R", "G", "B"]).graph
    report = next(n for n in graph.nodes if n.type == "report")
    graph.nodes.append(FlowNode(
        id="darksQueue", type="calib", x=0.0, y=900.0,
        params={**create_params("calib"), "darks": "Always", "bias": "Always",
                "flats": "Skip"}))
    graph.edges.append(FlowEdge(**{"from": report.id, "fromPort": "done",
                                   "to": "darksQueue", "toPort": "do"}))
    return graph


def _plan_and_notes(graph):
    plan, unmapped = to_sequence_plan(compile_plan(graph, "quick"), graph)
    return plan, {u["key"]: u["detail"] for u in unmapped}


def test_the_chip_funds_no_end_of_night_darks():
    """THE CLAIM THE CARD NO LONGER MAKES. With the queue started by the
    report, the wind-down's day-darks lane has no funding, so nothing is taken
    at the end of the night, and the compile says the wire does not run.

    Named mutant "the report wire funds the lane" (``to_plan.plan_extras``'s
    day-darks condition widened from ``== "on_shutdown_complete"`` to ``in
    ("on_shutdown_complete", "on_target_complete")``). ``to_plan.py`` is not
    this work package's file, so the mutant was run in a private copy of the
    ``astrodeck`` package under the session scratchpad (this file copied beside
    it, since ``conftest.py`` would put the worktree's own package first), never
    in the worktree, from a byte backup that was restored and compared by
    sha256: RED, ``AssertionError: the DARKS AFTER chip now funds the
    end-of-night darks lane: the card (quickCopy.ts INFO.darks) may say 'at the
    end of the night' again``.
    """
    plan, notes = _plan_and_notes(_quick_with_darks_after())
    assert plan.day_darks == 0, (
        "the DARKS AFTER chip now funds the end-of-night darks lane: the card "
        "(quickCopy.ts INFO.darks) may say 'at the end of the night' again")
    detail = notes.get("instructions[on_target_complete -> calib]", "")
    assert "will not run" in detail, (
        f"the report -> queue wire is no longer reported as not running: "
        f"{notes}")


def test_the_chip_funds_the_cloud_hold_darks():
    """WHAT THE CARD DOES SAY. The queue's quota is the cloud hold's dark
    quota, so a hold takes darks, and the compile says the queue's bias and
    flat legs are not wired (the card says bias is not run)."""
    plan, notes = _plan_and_notes(_quick_with_darks_after())
    assert plan.cloud_hold_darks > 0, (
        "the queue no longer funds the cloud hold's darks, which is the one "
        "thing the card says it does")
    queue = notes.get("automation.calibration_queue", "")
    assert "bias and flat legs" in queue and "not wired" in queue, (
        f"the compile no longer says the bias leg is unwired: {queue!r}")


def test_control_a_queue_on_shutdown_complete_does_fund_the_lane():
    """THE CONTROL that makes the zero above mean something: the same queue
    started from a PARK + CLOSE node's ``closed`` port (SHUTDOWN COMPLETE) is
    funded. Were this red, the first case would be passing against a lane that
    can never be funded at all.

    Named mutant "the lane is never funded" (same private copy and method;
    ``plan_extras``'s ``out["day_darks"] = min(int(quota), 40)`` made
    ``out["day_darks"] = 0``): the case above stays green, which is the false
    comfort this one exists to break, and this one is RED, ``AssertionError: a
    queue wired to SHUTDOWN COMPLETE no longer funds the day-darks lane, so
    the zero in the case above proves nothing``."""
    graph = quick(_TARGET, subs_per_filter=10, filters=["L", "R"],
                  wheel=["L", "R", "G", "B"]).graph
    dusk = next(n for n in graph.nodes if n.type == "dusk")
    graph.nodes.append(FlowNode(
        id="parkNode", type="parkclose", x=0.0, y=800.0,
        params=create_params("parkclose")))
    graph.nodes.append(FlowNode(
        id="darksQueue", type="calib", x=0.0, y=900.0,
        params={**create_params("calib"), "darks": "Always", "bias": "Always",
                "flats": "Skip"}))
    graph.edges.append(FlowEdge(**{"from": dusk.id, "fromPort": "nightend",
                                   "to": "parkNode", "toPort": "do"}))
    graph.edges.append(FlowEdge(**{"from": "parkNode", "fromPort": "closed",
                                   "to": "darksQueue", "toPort": "do"}))
    plan, _ = _plan_and_notes(graph)
    assert plan.day_darks > 0, (
        "a queue wired to SHUTDOWN COMPLETE no longer funds the day-darks "
        "lane, so the zero in the case above proves nothing")
