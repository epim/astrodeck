# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#195: DUSK WINDOW's `autoResume` -> `SequencePlan.resume_across_nights`,
through `compile.compile_plan` and `to_plan.plan_extras`.

RE-PINNED IN WP-85 (wave 14, #195). This file was written in wave 4 (WP-34)
for a mapping the owner had not ruled: DUSK WINDOW's `repeat`, read so that its
DEFAULT, "Single night", wrote ``resume_across_nights = False``. That is the
opposite of the owner's ruling 7 on #189 (the option defaults ON, and a saved
"Single night" must NOT be read as Off), and it stopped every untouched
DUSK WINDOW flow resuming after its first night. The file now drives the
mapping the ruling asks for, and still holds the convention it was written
around:

ABSENT WHEN TRUE, the same convention `campaign` already uses (compile.py),
and for the same reason: `SequencePlan.resume_across_nights` already defaults
to True, so a compile that writes nothing here must produce the exact plan it
always has - every flow compiled before this field existed, every flow with
no DUSK WINDOW, and every DUSK WINDOW that did not choose Off. Only an
explicit `autoResume` "Off" names the field at all, and only as False.

The wave-14 cases that grade the P0 itself, the engine's dawn-boundary disarm
and the mutants are in ``test_w14_autoresume_option.py``; the Tonight copy,
the store migration and the claims table have their own ``test_w14_*`` files.
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import plan_extras, to_sequence_plan


def _n(nid: str, ntype: str, x: float = 0.0, **params) -> FlowNode:
    return FlowNode(id=nid, type=ntype, x=float(x), params=params)


def _e(src: str, sp: str, dst: str, dp: str) -> FlowEdge:
    return FlowEdge(**{"from": src, "fromPort": sp, "to": dst, "toPort": dp})


def _graph(*, auto_resume: str | None = None,
           repeat: str | None = None) -> FlowGraph:
    """TARGET -> CAPTURE, with a DUSK WINDOW whose `autoResume` is
    ``auto_resume`` and whose `repeat` is ``repeat`` (each omitted from params
    entirely when None, the "saved before the key existed" case)."""
    dusk_params: dict = {}
    if auto_resume is not None:
        dusk_params["autoResume"] = auto_resume
    if repeat is not None:
        dusk_params["repeat"] = repeat
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


def test_off_writes_the_key_false():
    """The one way to False, and what makes `ResumeArm` refuse a later night.

    MUTANT "resume_across_nights always true" (`resume_across_nights = True`
    unconditionally, ignoring `autoResume`) turns this red, run from a byte
    backup and restored and sha256-verified afterwards:

        AssertionError: assert None is False
         +  where None = <built-in method get of dict ...>('resume_across_nights')

    - the key never appears, because an always-True value is never written
    (the absent-when-true convention)."""
    compiled = compile_plan(_graph(auto_resume="Off"), "n")
    assert compiled.get("resume_across_nights") is False
    plan, _ = to_sequence_plan(compiled)
    assert plan.resume_across_nights is False


def test_a_dusk_window_that_says_nothing_leaves_the_key_absent():
    """A DUSK WINDOW saved before `autoResume` existed carries no key at all,
    and reads as On, which is what every flow did before 0.3.40. (In 0.3.40
    this case read as Off through `repeat`'s own default, the P0 of #195.)"""
    compiled = compile_plan(_graph(), "n")
    assert "resume_across_nights" not in compiled
    plan, _ = to_sequence_plan(compiled)
    assert plan.resume_across_nights is True


def test_on_leaves_the_key_absent():
    compiled = compile_plan(_graph(auto_resume="On"), "n")
    assert "resume_across_nights" not in compiled
    plan, _ = to_sequence_plan(compiled)
    assert plan.resume_across_nights is True


def test_single_night_repeat_is_not_read_as_off():
    """The old field's default, stored explicitly (every flow the wizard made
    carries it): the ruling is that it must NOT be read as Off, because that
    silently disarms every saved flow."""
    compiled = compile_plan(_graph(repeat="Single night"), "n")
    assert "resume_across_nights" not in compiled
    plan, _ = to_sequence_plan(compiled)
    assert plan.resume_across_nights is True


def test_nightly_repeats_leave_the_key_absent():
    for repeat in ("Nightly until pool complete", "Nightly ×30"):
        compiled = compile_plan(_graph(repeat=repeat), "n")
        assert "resume_across_nights" not in compiled, repeat


def test_off_with_a_nightly_repeat_is_still_off():
    """`autoResume` decides, whatever `repeat` says: a flow stored with both
    (the campaign block is still keyed on `repeat` until that is retired) is Off."""
    compiled = compile_plan(
        _graph(auto_resume="Off", repeat="Nightly until pool complete"), "n")
    assert compiled.get("resume_across_nights") is False


def test_no_dusk_window_leaves_the_key_absent():
    """A flow with no DUSK WINDOW carries no opinion on resuming at all, so it
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
