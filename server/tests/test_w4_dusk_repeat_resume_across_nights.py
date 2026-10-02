# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#195: DUSK WINDOW's `repeat` -> `SequencePlan.resume_across_nights`, through
`compile.compile_plan` and `to_plan.plan_extras`.

ABSENT WHEN TRUE, the same convention `campaign` already uses (compile.py),
and for the same reason: `SequencePlan.resume_across_nights` already defaults
to True, so a compile that writes nothing here must produce the exact plan it
always has - every flow compiled before this field existed, every flow with
no DUSK WINDOW, and every DUSK WINDOW whose `repeat` is not "Single night".
Only "Single night" (the DUSK WINDOW's own default - #189 S7, nodes.py) names
the field at all, and only as False.

This file is deliberately independent of `server/tests/test_flows_compile.py`
and `test_mosaic_spec_claims.py`'s byte-identical/exact-key-count fixtures,
which this field's FIRST appearance (on a "Single night" DUSK WINDOW, which is
most of them) now trips - see this WP's return for the full list; those files
are outside this WP's owned files and are not edited here.
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import plan_extras, to_sequence_plan


def _n(nid: str, ntype: str, x: float = 0.0, **params) -> FlowNode:
    return FlowNode(id=nid, type=ntype, x=float(x), params=params)


def _e(src: str, sp: str, dst: str, dp: str) -> FlowEdge:
    return FlowEdge(**{"from": src, "fromPort": sp, "to": dst, "toPort": dp})


def _graph(*, repeat: str | None) -> FlowGraph:
    """TARGET -> CAPTURE, with a DUSK WINDOW whose `repeat` is ``repeat``
    (omitted from params entirely when None, the "saved before `repeat`
    existed" case)."""
    dusk_params = {} if repeat is None else {"repeat": repeat}
    return FlowGraph(
        nodes=[_n("d", "dusk", **dusk_params),
               _n("t", "target", 200, name="M42", ra="05h 34m 32s",
                  dec="+22 00 52"),
               _n("c", "capture", 400, exposure=60, count=10, filter="L")],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run")])


def _no_dusk_graph() -> FlowGraph:
    return FlowGraph(
        nodes=[_n("t", "target", name="M42", ra="05h 34m 32s", dec="+22 00 52"),
               _n("c", "capture", 200, exposure=60, count=10, filter="L")],
        edges=[_e("t", "target", "c", "run")])


def test_single_night_writes_the_key_false():
    """"Single night" is the DUSK WINDOW's OWN default (nodes.py), so this is
    what most flows compile to right now, and it is exactly the case #195
    exists to fix.

    MUTANT "resume_across_nights always true" (`resume_across_nights = True`
    unconditionally, ignoring `repeat`) turned this red (and the next test,
    `test_missing_repeat_key_reads_as_single_night`, the same way), run from
    a byte backup and restored and sha256-verified afterwards:

        AssertionError: assert None is False
         +  where None = <built-in method get of dict ...>('resume_across_nights')

    - the key never appears, because an always-True value is never written
    (the absent-when-true convention)."""
    compiled = compile_plan(_graph(repeat="Single night"), "n")
    assert compiled.get("resume_across_nights") is False
    plan, _ = to_sequence_plan(compiled)
    assert plan.resume_across_nights is False


def test_missing_repeat_key_reads_as_single_night():
    """A DUSK WINDOW saved before `repeat` existed carries no key at all, and
    must read exactly as "Single night" does - the same missing-key rule
    `compile._trigger_for`'s sibling logic already applies to `repeat`."""
    compiled = compile_plan(_graph(repeat=None), "n")
    assert compiled.get("resume_across_nights") is False


def test_nightly_until_pool_complete_leaves_the_key_absent():
    compiled = compile_plan(_graph(repeat="Nightly until pool complete"), "n")
    assert "resume_across_nights" not in compiled
    plan, _ = to_sequence_plan(compiled)
    assert plan.resume_across_nights is True


def test_nightly_x30_leaves_the_key_absent():
    compiled = compile_plan(_graph(repeat="Nightly ×30"), "n")
    assert "resume_across_nights" not in compiled


def test_no_dusk_window_leaves_the_key_absent():
    """A flow with no DUSK WINDOW carries no opinion on repeat at all, so it
    keeps doing what it has always done."""
    compiled = compile_plan(_no_dusk_graph(), "n")
    assert "resume_across_nights" not in compiled
    plan, _ = to_sequence_plan(compiled)
    assert plan.resume_across_nights is True


def test_plan_extras_only_ever_writes_false():
    """`plan_extras` must not promote a True value some other caller might
    someday write into `compiled` - the model's own default already covers
    True, and writing it here would re-open the exact byte-identical breakage
    this convention exists to avoid."""
    assert "resume_across_nights" not in plan_extras({})
    assert "resume_across_nights" not in plan_extras({"resume_across_nights": True})
    assert plan_extras({"resume_across_nights": False})["resume_across_nights"] \
        is False
