"""GN-08: the HFR watchdog can be RELATIVE to the post-focus baseline.

Evidence (docs/superpowers/specs/2026-09-06-guider-night-defects-triage.md):
this rig's L filter runs at 3.5 px at PERFECT focus, so the flow template's
absolute "HFR above" 3.2 px default fired a full 8-minute refocus on every
single pass of the cycle - the threshold never related to what the autofocus
actually achieved. `hfr_above` now accepts a threshold expressed as a FACTOR
of the baseline HFR (the first accepted frame after each autofocus), and the
CONDITION node / wizard / templates default to that form at 1.3x.

Layers covered here: `_eval_predicate` (pure eval), the engine's real per-frame
path (baseline seeding + the autofocus reset), `to_plan`'s flow-to-plan
mapping, the node/wizard/example defaults, and the pydantic model bounds.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import default_params
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.wizard import KIND_DEEP_SKY, OPT_GUIDING, OPT_WATCHDOG, generate
from astrodeck.sequence import ExposureStep, Instruction, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.instructions import TriggerContext, _eval_predicate


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    """Same minimal fixture as test_instructions_engine.py (copied rather than
    shared, per this suite's own convention - see tests/_simhub.py's docstring
    for the OTHER shared variant, which does rotator/solver setup this GN-08
    suite does not need)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


# ============================================================ (a) eval-level
# 1.3 x 3.5 = 4.55: 4.2 stays under it, 4.5 stays under it, 4.6 crosses it.

def test_relative_hfr_above_compares_against_the_baseline_factor():
    below = TriggerContext(now_ts=0.0, frame_hfr=4.2, focus_baseline_hfr=3.5)
    assert _eval_predicate("hfr_above", 1.3, None, below, relative=True) is False

    at_the_edge = TriggerContext(now_ts=0.0, frame_hfr=4.5, focus_baseline_hfr=3.5)
    assert _eval_predicate("hfr_above", 1.3, None, at_the_edge, relative=True) is False

    over = TriggerContext(now_ts=0.0, frame_hfr=4.6, focus_baseline_hfr=3.5)
    assert _eval_predicate("hfr_above", 1.3, None, over, relative=True) is True


def test_relative_hfr_above_is_indeterminate_with_no_baseline_yet():
    ctx = TriggerContext(now_ts=0.0, frame_hfr=99.0, focus_baseline_hfr=None)
    assert _eval_predicate("hfr_above", 1.3, None, ctx, relative=True) is None


def test_absolute_hfr_above_is_unchanged():
    ctx = TriggerContext(now_ts=0.0, frame_hfr=3.5)
    assert _eval_predicate("hfr_above", 3.2, None, ctx) is True
    assert _eval_predicate("hfr_above", 3.2, None, ctx, relative=False) is True


# ======================================================= (b) engine-level
# Same shape as test_instructions_engine.py's `_tiny_plan`/`_multi_plan`: a
# minimal sim run, `_capture` faked to hand back a scripted HFR per frame.

async def _wait_done(eng, timeout=30.0) -> None:
    import asyncio
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if not eng.running:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"run did not finish: {eng.state}")


async def _noop() -> None:
    return None


def _one_target_plan(count: int, *, autofocus_every: int = 0,
                     instructions=None) -> SequencePlan:
    return SequencePlan(
        name="gn08", guide=False, dither_every=0,
        autofocus_every=autofocus_every, meridian_flip=False,
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100,
                                count=count)])],
        instructions=instructions or [])


async def test_relative_watchdog_fires_only_past_the_baseline_factor(
        sim_hub, monkeypatch, bus_lines):
    """3.5, 3.6, 4.8 px against a 1.3x rule: baseline seeds at 3.5 (the first
    accepted frame), 1.3 x 3.5 = 4.55, so only the 4.8 px frame crosses it."""
    eng = SequenceEngine(sim_hub)
    af_calls: list[str] = []

    async def fake_af(label, **_ctx):
        af_calls.append(label)
    monkeypatch.setattr(eng, "_autofocus", fake_af)

    hfrs = iter([3.5, 3.6, 4.8])

    async def sequenced_capture(*a, **k):
        return {"hfr": next(hfrs), "stats": {"median": 100}, "saved_path": None}
    monkeypatch.setattr(eng, "_capture", sequenced_capture)

    plan = _one_target_plan(3, instructions=[Instruction(
        trigger="on_hfr_above", threshold=1.3, relative=True, action="refocus")])
    eng.start(plan)
    await _wait_done(eng)

    assert af_calls == ["triggered refocus"], (
        f"expected exactly one refocus (only the 4.8 px frame beats "
        f"1.3 x 3.5 = 4.55), got {af_calls}")
    baseline_msgs = [m for (_lvl, m, _src) in bus_lines
                     if "focus baseline HFR" in m]
    assert baseline_msgs, "the seeded baseline must be logged"
    assert "3.50" in baseline_msgs[0], baseline_msgs[0]


async def test_a_successful_autofocus_resets_the_baseline_for_the_next_frame(
        sim_hub, monkeypatch):
    """autofocus_every=2 fires a real cadence refocus before frame 3. The
    baseline must come from frame 3 (5.0), the first accepted frame AFTER that
    refocus - not the stale pre-refocus one (3.0) from frame 1."""
    import astrodeck.sequence.engine as engine_mod
    from astrodeck.focus.autofocus import AutofocusResult

    af_calls: list[int] = []

    async def fake_run_autofocus(cam, foc, **_k):
        af_calls.append(1)
        return AutofocusResult(True, 20000, 4.0, [], "ok")
    monkeypatch.setattr(engine_mod, "run_autofocus", fake_run_autofocus)

    eng = SequenceEngine(sim_hub)
    dispatched: list[object] = []
    monkeypatch.setattr(eng, "_dispatch_actions",
                        lambda *a, **k: dispatched.append(a) or _noop())

    hfrs = iter([3.0, 3.0, 5.0, 6.0])

    async def sequenced_capture(*a, **k):
        return {"hfr": next(hfrs), "stats": {"median": 100}, "saved_path": None}
    monkeypatch.setattr(eng, "_capture", sequenced_capture)

    plan = _one_target_plan(4, autofocus_every=2, instructions=[Instruction(
        trigger="on_hfr_above", threshold=1.3, relative=True, action="notify",
        message="watchdog")])
    eng.start(plan)
    await _wait_done(eng)

    assert len(af_calls) == 1, "exactly one cadence autofocus should have run"
    assert eng._focus_baseline_hfr == 5.0, (
        "the baseline after the run must be 5.0 (frame 3, seeded fresh after "
        "the refocus), not the stale pre-refocus 3.0")
    assert dispatched == [], (
        "6.0 px is BELOW 1.3 x 5.0 = 6.5 (the correct, reseeded baseline); a "
        "stale 3.0 baseline (1.3 x 3.0 = 3.9) would have wrongly fired here")


# ============================================================= (c) to_plan

def _condition_graph(when: str, threshold: float) -> FlowGraph:
    return FlowGraph(
        nodes=[_n("t", "target", name="M31", ra="00h 42m 44s", dec="+41 16 09"),
               _n("c", "capture", x=100, exposure=60, count=5),
               _n("k", "condition", x=200, when=when, threshold=threshold),
               _n("r", "refocus", x=300)],
        edges=[_e("t", "target", "c", "run"), _e("k", "fire", "r", "do")])


def test_the_relative_condition_form_maps_to_a_relative_instruction():
    g = _condition_graph("HFR above (x focus)", 1.3)
    plan, _ = to_sequence_plan(compile_plan(g, "n"))
    assert len(plan.instructions) == 1
    assert plan.instructions[0].trigger == "on_hfr_above"
    assert plan.instructions[0].relative is True
    assert plan.instructions[0].threshold == 1.3


def test_the_absolute_condition_form_stays_absolute():
    g = _condition_graph("HFR above", 3.2)
    plan, _ = to_sequence_plan(compile_plan(g, "n"))
    assert plan.instructions[0].trigger == "on_hfr_above"
    assert plan.instructions[0].relative is False
    assert plan.instructions[0].threshold == 3.2


def test_an_illegal_relative_factor_is_noted_and_dropped():
    g = _condition_graph("HFR above (x focus)", 1.0)     # 1.0 fires on the baseline itself
    plan, un = to_sequence_plan(compile_plan(g, "n"), g)
    assert plan.instructions == [], "an out-of-range factor must not reach the engine"
    notes = [u for u in un if "instructions[on_hfr_above]" in u["key"]]
    assert notes and notes[0]["level"] == "warn"
    assert "1x" in notes[0]["detail"] or "1.0" in notes[0]["detail"] \
        or "factor" in notes[0]["detail"]


# ==================================================== (d) node/wizard/example defaults

def test_condition_node_default_is_relative_1_3():
    p = default_params("condition")
    assert p["when"] == "HFR above (x focus)"
    assert p["threshold"] == 1.3


def test_wizard_watchdog_defaults_to_relative():
    graph = generate(KIND_DEEP_SKY, {OPT_GUIDING, OPT_WATCHDOG})
    cond = next(n for n in graph.nodes if n.type == "condition")
    assert cond.params["when"] == "HFR above (x focus)"
    assert cond.params["threshold"] == 1.3


def test_every_example_condition_node_defaults_to_relative():
    """M31, the pool night and the LRGBSHO cycle each carry an untouched
    CONDITION node; the NB example explicitly overrides it to Guide RMS above
    and is excluded, since that override is a deliberate absolute-metric
    choice this change does not touch."""
    checked = 0
    for rec in examples():
        for node in rec.graph.with_defaults().nodes:
            if node.type != "condition":
                continue
            if node.params.get("when") == "Guide RMS above":
                continue
            assert node.params.get("when") == "HFR above (x focus)", rec.name
            assert node.params.get("threshold") == 1.3, rec.name
            checked += 1
    assert checked >= 3, "expected at least M31, the pool night and the cycle"


# ===================================================================== (e) models

def test_relative_threshold_must_exceed_one():
    with pytest.raises(ValidationError):
        Instruction(trigger="on_hfr_above", threshold=1.0, relative=True,
                   action="refocus")


def test_relative_threshold_cannot_exceed_five():
    with pytest.raises(ValidationError):
        Instruction(trigger="on_hfr_above", threshold=5.1, relative=True,
                   action="refocus")


def test_relative_threshold_in_range_is_accepted():
    i = Instruction(trigger="on_hfr_above", threshold=1.3, relative=True,
                    action="refocus")
    assert i.relative is True
    assert i.threshold == 1.3


def test_a_plan_saved_before_relative_existed_loads_as_absolute():
    raw = {"trigger": "on_hfr_above", "threshold": 3.2, "action": "refocus"}
    i = Instruction(**raw)
    assert i.relative is False
    # round-trips through SequencePlan the same way test_instructions_model.py
    # already pins for `threshold`.
    plan = SequencePlan(instructions=[i])
    assert SequencePlan(**plan.model_dump()).instructions[0].relative is False
