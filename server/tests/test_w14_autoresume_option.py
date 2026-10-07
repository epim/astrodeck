# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#195 (WP-85): DUSK WINDOW's `autoResume` option, default ON, and the undoing
of 0.3.40's "Single night" default.

THE DEFECT THIS UNDOES. Wave 4's WP-34 (19f97067) compiled DUSK WINDOW's
`repeat` default, "Single night", to ``resume_across_nights = False``, the
OPPOSITE of the owner's ruling 7 on #189 (quoted in #195's body): the option
defaults ON, and a saved "Single night" must NOT read as Off. So every
DUSK WINDOW flow nobody had touched stopped resuming after its first night.
This file holds the three promises the fix makes, in the order #195 lists
them:

* the COMPILE: a flow with no `autoResume`, whatever its `repeat` says,
  compiles exactly as it did before 0.3.40 (no key, a plan whose field is
  True); only an explicit "Off" writes False;
* the ENGINE: an Off session that ends at its stop boundary is left dormant
  and DISARMED, with the line that says why; a session that ends any other
  way keeps the arming (a crash, a restart and a stop by hand each have a
  promise of their own), and an On session is untouched;
* the NEXT-NIGHT NET, which is `ResumeArm`'s and is graded in
  ``test_w4_resume_arm_single_night.py``: a crash before dawn followed by a
  restart after it does not resume an Off flow the next night.

The engine half uses the harness of ``test_a_short_night_stays_resumable.py``
(a real sim hub and engine, the window closed under a running frame loop).

NAMED MUTANTS (each run from a byte backup inside this worktree, restored and
sha256-compared, the mutant text grepped out afterwards; the failing assertion
is quoted in the docstring of the test that catches it):

* "repeat default read as Off": compile.py's decision put back to
  ``resume_across_nights = repeat != "Single night"``;
* "disarm at start": ``engine.start`` clears ``auto_resume`` for an Off plan;
* "finalize disarms on every reason": `_finalize_report`'s reason filter
  dropped.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import NODE_DEFS, dusk_auto_resume
from astrodeck.flows.to_plan import plan_extras, to_sequence_plan
from astrodeck.flows.wizard import KIND_DEEP_SKY, generate_record
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.session import session_store


def _n(nid: str, ntype: str, x: float = 0.0, **params) -> FlowNode:
    return FlowNode(id=nid, type=ntype, x=float(x), params=params)


def _e(src: str, sp: str, dst: str, dp: str) -> FlowEdge:
    return FlowEdge(**{"from": src, "fromPort": sp, "to": dst, "toPort": dp})


def _graph(**dusk_params) -> FlowGraph:
    """DUSK -> TARGET -> CAPTURE, the DUSK carrying exactly ``dusk_params``
    (no `autoResume` and no `repeat` when none is given: a flow saved before
    either key existed)."""
    return FlowGraph(
        nodes=[_n("d", "dusk", **dusk_params),
               _n("t", "target", 200, name="M42", ra="05h 34m 32s",
                  dec="+22 00 52"),
               _n("c", "capture", 400, exposure=60, count=10, filter="L")],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run")])


def _compiled(**dusk_params) -> dict:
    return compile_plan(_graph(**dusk_params), "n")


def _blank_ids(node):
    """A plan dump with every ``id`` blanked: the ids are minted per call
    (uuid4), so two compiles of one graph are equal in everything else."""
    if isinstance(node, dict):
        return {k: ("" if k == "id" else _blank_ids(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_blank_ids(v) for v in node]
    return node


# ============================================================== the compile

def test_a_flow_with_no_auto_resume_key_compiles_with_no_resume_key():
    """THE P0. A DUSK WINDOW that carries neither `autoResume` nor `repeat`
    (the shape of every flow saved before 0.3.40) compiles with no
    ``resume_across_nights`` key, and the plan it makes resumes.

    MUTANT "repeat default read as Off" (compile.py's decision put back to
    ``resume_across_nights = repeat != "Single night"``, the 0.3.40 line)
    turned this red, run from a byte backup and restored and sha256-verified
    afterwards:

        AssertionError: a DUSK WINDOW that never chose Off must not write
        resume_across_nights, got False
        assert 'resume_across_nights' not in {...}
    """
    compiled = _compiled()
    assert "resume_across_nights" not in compiled, (
        f"a DUSK WINDOW that never chose Off must not write "
        f"resume_across_nights, got {compiled.get('resume_across_nights')!r}")
    plan, _ = to_sequence_plan(compiled)
    assert plan.resume_across_nights is True


def test_the_single_night_repeat_does_not_read_as_off():
    """`repeat: "Single night"` is what the editor showed as the default and
    what every wizard flow stores: the owner's ruling is that it must NOT be
    read as Off, because that silently disarms every saved flow. Held for the
    explicit value and for each of the other two the old field offered."""
    for repeat in ("Single night", "Nightly until pool complete",
                   "Nightly ×30"):
        compiled = _compiled(repeat=repeat)
        assert "resume_across_nights" not in compiled, (
            f"repeat={repeat!r} wrote resume_across_nights="
            f"{compiled.get('resume_across_nights')!r}")
        plan, _ = to_sequence_plan(compiled)
        assert plan.resume_across_nights is True, repeat


def test_the_wizards_graph_resumes():
    """A flow the guided wizard makes (`generate_record`) is the commonest
    DUSK WINDOW there is, and the one 0.3.40 stopped resuming: it carries the
    DUSK defaults `create_params` writes, `repeat: "Single night"` among them.
    It compiles without the key and its plan resumes, and it states its
    `autoResume` outright, so the choice is visible in the file."""
    record = generate_record(KIND_DEEP_SKY, None, "M31")
    dusk = next(n for n in record.graph.nodes if n.type == "dusk")
    assert dusk.params.get("repeat") == "Single night", (
        "premise: the wizard's DUSK still carries the old default")
    assert dusk.params.get("autoResume") == "On"
    compiled = compile_plan(record.graph, record.name)
    assert "resume_across_nights" not in compiled
    plan, _ = to_sequence_plan(compiled)
    assert plan.resume_across_nights is True


def test_an_explicit_off_writes_the_key_false():
    """The only way to the field being False. It rides in the compiled dict
    and into the plan, whatever `repeat` says."""
    for extra in ({}, {"repeat": "Single night"},
                  {"repeat": "Nightly until pool complete"}):
        compiled = _compiled(autoResume="Off", **extra)
        assert compiled.get("resume_across_nights") is False, extra
        plan, _ = to_sequence_plan(compiled)
        assert plan.resume_across_nights is False, extra


def test_an_explicit_on_is_byte_identical_to_saying_nothing():
    """ON IS TODAY'S PLAN, BYTE FOR BYTE. The compiled dict of an explicit On
    is the compiled dict of a node that says nothing, so every plan compiled
    before 0.3.40, and every one since that was not Off, is reproduced
    exactly: the key is written only when False (compile.py), and the model's
    own default is True."""
    silent = json.dumps(_compiled(), sort_keys=True)
    assert json.dumps(_compiled(autoResume="On"), sort_keys=True) == silent
    assert json.dumps(_compiled(autoResume="On", repeat="Single night"),
                      sort_keys=True) == silent
    plan, _ = to_sequence_plan(_compiled(autoResume="On"))
    bare, _ = to_sequence_plan(_compiled())
    assert _blank_ids(plan.model_dump()) == _blank_ids(bare.model_dump())


def test_an_unrecognised_value_reads_on():
    """Permissive, like every param read in this package: a value this build
    does not offer, a blank, or a hand-written boolean is not a request for
    Off, and a typo must not silently disarm a flow."""
    for value in ("", "off", "OFF", "No", "false", None, 0, False, "Maybe"):
        assert dusk_auto_resume({"autoResume": value}) is True, repr(value)
        assert "resume_across_nights" not in _compiled(autoResume=value), (
            repr(value))
    assert dusk_auto_resume({}) is True
    assert dusk_auto_resume(None) is True
    assert dusk_auto_resume({"autoResume": "Off"}) is False


def test_a_flow_with_no_dusk_window_resumes():
    """No DUSK WINDOW carries no opinion at all, so it keeps doing what every
    flow has always done."""
    graph = FlowGraph(
        nodes=[_n("t", "target", name="M42", ra="05h 34m 32s",
                  dec="+22 00 52"),
               _n("c", "capture", 200, exposure=60, count=10, filter="L")],
        edges=[_e("t", "target", "c", "run")])
    compiled = compile_plan(graph, "n")
    assert "resume_across_nights" not in compiled
    plan, _ = to_sequence_plan(compiled)
    assert plan.resume_across_nights is True


def test_the_vocabulary_has_the_option_and_still_keeps_repeat():
    """`autoResume` is a DUSK param, "On" by default; `repeat` stays in the
    table so a stored file still loads and the campaign block keeps its key
    (the retirement of `repeat` is a later change)."""
    params = NODE_DEFS["dusk"].params
    assert params["autoResume"] == "On"
    assert params["repeat"] == "Single night"


def test_plan_extras_still_only_ever_writes_false():
    """The model's default covers True, and writing it would re-open the
    byte-identical breakage the absent-when-True convention exists to avoid."""
    assert "resume_across_nights" not in plan_extras({})
    assert "resume_across_nights" not in plan_extras(
        {"resume_across_nights": True})
    assert plan_extras({"resume_across_nights": False})[
        "resume_across_nights"] is False


def test_the_campaign_block_is_still_keyed_on_repeat():
    """Ruling 5 of this work package: `campaign` stays keyed on `repeat`, so
    every campaign compile is byte-identical to before. Turning auto-resume
    Off does not remove it (retiring `repeat` is a later change), and the
    default flow still has none."""
    assert "campaign" not in _compiled()
    assert "campaign" not in _compiled(autoResume="Off")
    nightly = _compiled(repeat="Nightly until pool complete")
    assert nightly["campaign"] == {"repeat": "nightly",
                                   "until": "pool_complete",
                                   "resume": "cursor"}
    both = _compiled(repeat="Nightly until pool complete", autoResume="Off")
    assert both["campaign"] == nightly["campaign"]
    assert both["resume_across_nights"] is False


# ============================================================== the engine

@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _plan(*, resume_across_nights: bool, count: int = 8) -> SequencePlan:
    return SequencePlan(
        name="resume-option", guide=False, dither_every=0,
        autofocus_every=0, meridian_flip=False,
        resume_across_nights=resume_across_nights,
        targets=[Target(name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=count)])])


def _close_the_window(eng, target) -> None:
    """Dawn arrives UNDER a running frame loop (the harness of
    ``test_a_short_night_stays_resumable.py``): the target's frozen window is
    closed behind it, which only the per-frame boundary check can see."""
    start, _stop = eng._frozen.get(id(target)) or (time.time() - 100, None)
    eng._frozen[id(target)] = (start, time.time() - 1)


def _messages(bus_lines) -> list[str]:
    return [msg for _lvl, msg, _src in bus_lines]


OFF_LINE = ("automatic resume on later nights is off for this flow; "
            "CONTINUE it by hand to shoot the rest.")


async def test_an_off_flow_that_ends_at_dawn_is_left_dormant_and_disarmed(
        sim_hub, bus_lines):
    """The dawn-boundary disarm. An Off plan whose night is cut by its stop
    boundary with frames owed ends `dawn_cutoff`; the session is dormant (it
    still owes the frames, and CONTINUE can shoot them), `auto_resume` is
    False so nothing starts it on its own, and the log says why in the words
    the operator needs: it is off, and CONTINUE is the way.

    MUTANT "finalize never disarms" (the whole disarm block this fix adds to
    `_finalize_report` skipped) turned this red, run from a byte backup and
    restored and sha256-verified afterwards:

        AssertionError: an Off flow that ended at dawn must be disarmed, or
        the next tick starts it on a night it never agreed to
        assert True is False
    """
    plan = _plan(resume_across_nights=False)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 2)
    _close_the_window(eng, plan.targets[0])
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    assert eng.state.get("end_reason") == "dawn_cutoff"

    s = session_store.load(sid)
    assert 0 < s.owed() < 8, "the cut must land mid-target to test anything"
    assert s.status == "dormant"
    assert s.auto_resume is False, (
        "an Off flow that ended at dawn must be disarmed, or the next tick "
        "starts it on a night it never agreed to")
    assert session_store.armed() is None
    assert any(OFF_LINE in m and "resume-option" in m
               for m in _messages(bus_lines)), _messages(bus_lines)


async def test_an_on_flow_that_ends_at_dawn_stays_armed(sim_hub, bus_lines):
    """The default, and the control for the test above: an On plan ending the
    same way stays dormant AND armed, and says nothing about being off."""
    plan = _plan(resume_across_nights=True)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 2)
    _close_the_window(eng, plan.targets[0])
    assert await wait_for(lambda: eng.state.get("state") == "complete")

    s = session_store.load(sid)
    assert s.status == "dormant" and s.owed() > 0
    assert s.auto_resume is True
    armed = session_store.armed()
    assert armed is not None and armed.id == sid
    assert not any(OFF_LINE in m for m in _messages(bus_lines))


async def test_an_off_flow_that_sets_a_target_aside_is_disarmed_too(sim_hub):
    """`incomplete` is the other ending that leaves frames owed because the
    SKY or the schedule ran out rather than anything breaking: a target set
    aside by `on_missed="skip"`. An Off plan is disarmed there too."""
    from astrodeck.sequence.models import Schedule
    past = time.strftime("%H:%M", time.localtime(time.time() - 3 * 3600))
    skipped = Target(name="B", ra_hours=5.5881, dec_deg=-5.3911,
                     center=False, autofocus_first=False,
                     schedule=Schedule(start_mode="time", start_time=past,
                                       on_missed="skip"),
                     steps=[ExposureStep(filter="L", exposure_s=0.05,
                                         count=2)])
    plan = _plan(resume_across_nights=False, count=2)
    plan.targets.append(skipped)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    assert eng.state.get("end_reason") == "incomplete"
    s = session_store.load(sid)
    assert s.status == "dormant" and s.owed() == 2
    assert s.auto_resume is False


@pytest.mark.parametrize("reason", ["error", "aborted", "shutdown", "unsafe",
                                    "quality", "cooling_skip"])
async def test_an_off_flow_keeps_its_arming_for_every_other_ending(
        sim_hub, bus_lines, reason):
    """SAME-NIGHT CONTINUITY IS A SEPARATE PROMISE, and the label says
    "subsequent nights". A crash, a process teardown, a safety stop and the
    rest end a night the SKY did not end, so the session stays armed and a
    restart the same night resumes it: only the boundary the sky set
    (dawn_cutoff, incomplete) disarms an Off plan. The one exception already
    in `_finalize_report`, an abort by hand, disarms for its own reason; it
    is driven here with `_aborting` unset, the way a process teardown ends.

    MUTANT "finalize disarms on every reason" (the reason filter dropped, so
    any ending disarms an Off plan) turned the `error` case red, run from a
    byte backup and restored and sha256-verified afterwards:

        AssertionError: a 'error' ending must keep an Off flow armed: a
        restart the same night is a separate promise from later nights
        assert False is True
    """
    plan = _plan(resume_across_nights=False)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 1)
    await eng.abort()
    # A fresh engine over that same dormant, armed session, finished with the
    # ending under test: the ending is the only free variable.
    owing = session_store.load(sid)
    assert owing.owed() > 0
    owing.auto_resume = True
    session_store.save(owing)
    eng2 = SequenceEngine(sim_hub)
    eng2._session = owing
    eng2._report_finalized = False
    eng2.reporter = None
    eng2._finalize_report(reason)
    after = session_store.load(sid)
    assert after.status == "dormant"
    assert after.auto_resume is True, (
        f"a {reason!r} ending must keep an Off flow armed: a restart the "
        f"same night is a separate promise from later nights")
    assert not any(OFF_LINE in m for m in _messages(bus_lines))


async def test_a_crash_mid_night_still_resumes_the_same_night(sim_hub):
    """The promise Off leaves alone, graded through ResumeArm's own tick: a
    run of an Off plan that dies mid-night (its task cancelled the way a
    process teardown cancels it, which is not an operator's abort, so
    `_finalize_report` ends it `aborted` and leaves the arming `start` gave it)
    is found armed and dormant, resumed by the next tick the same night, and
    finishes. `test_w4_resume_arm_single_night` holds the matching refusal for
    a LATER night.

    NOTHING RE-ARMS IT BY HAND: the arming under test is the one `engine.start`
    wrote, which is what lets this catch a start that disarms an Off plan
    (mutant "disarm at start", below, red here and in the test after this one).

    MUTANT "disarm at start" (`engine.start` arming ``bool(plan.
    resume_across_nights)`` instead of True) turned this red, run from a byte
    backup and restored and sha256-verified afterwards:

        AssertionError: a cancelled run of an Off plan must stay armed: a
        restart the same night has nothing to resume without it
        assert False is True
    """
    from astrodeck.sequence.resume_arm import ResumeArm
    plan = _plan(resume_across_nights=False, count=4)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 1)
    eng._task.cancel()                      # a teardown, not engine.abort()
    try:
        await eng._task
    except (asyncio.CancelledError, Exception):
        pass
    crashed = session_store.load(sid)
    assert crashed.status == "dormant" and crashed.owed() > 0
    assert crashed.auto_resume is True, (
        "a cancelled run of an Off plan must stay armed: a restart the same "
        "night has nothing to resume without it")

    now = {"t": time.time()}
    arm = ResumeArm(eng, sim_hub, clock=lambda: now["t"])
    arm._window_open = lambda s, t: True            # the tick never asks the sky
    await arm.tick()
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    assert session_store.load(sid).status == "complete"


async def test_engine_start_still_arms_an_off_plan(sim_hub):
    """`engine.start` arms EVERY run, an Off plan's included: the disarm is
    the stop boundary's, never the start's, which is what lets a crash or a
    reboot the same night resume.

    MUTANT "disarm at start" (`engine.start` clearing `auto_resume` for an
    Off plan) turned this red, and the same-night crash test above with it,
    run from a byte backup and restored and sha256-verified afterwards:

        AssertionError: engine.start must arm an Off plan too: a crash the
        same night has nothing to resume without it
        assert False is True
    """
    plan = _plan(resume_across_nights=False)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng._frames_done >= 1)
    assert session_store.load(sid).auto_resume is True, (
        "engine.start must arm an Off plan too: a crash the same night has "
        "nothing to resume without it")
    await eng.abort()
