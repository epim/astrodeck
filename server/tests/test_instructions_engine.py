"""PRO-3 engine integration: the conditional-sequencer eval + dispatch path.

Proves (a) empty instructions => the eval/dispatch path is never entered
(byte-identical no-op), (b) a high-HFR frame fires a refocus dispatched to the
existing ``_autofocus`` capability, and (c) an abort action tears the run down
through the existing SafetyAbort wind-down. No new capability code is exercised.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import (
    ExposureStep, Instruction, SequenceEngine, SequencePlan, Target,
)


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


@pytest.fixture
def temp_store():
    # this suite drives sim mechanics; the default store is fine.
    return None


async def _noop() -> None:
    return None


def _returns(info: dict):
    async def _cap(*a, **k):
        return dict(info)
    return _cap


def _tiny_plan(*, instructions=None) -> SequencePlan:
    return SequencePlan(
        name="pro3", guide=False, dither_every=0, autofocus_every=0,
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100, count=1)])],
        instructions=instructions or [])


async def _wait_done(eng, timeout=30.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if not eng.running:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"run did not finish: {eng.state}")


async def test_no_instructions_is_noop(sim_hub, temp_store, monkeypatch):
    eng = SequenceEngine(sim_hub)
    calls = []
    monkeypatch.setattr(eng, "_dispatch_actions",
                        lambda *a, **k: calls.append(a) or _noop())
    plan = _tiny_plan(instructions=[])
    eng.start(plan)
    await _wait_done(eng)
    assert calls == []                          # dispatch never called


async def test_high_hfr_triggers_refocus(sim_hub, temp_store, monkeypatch):
    eng = SequenceEngine(sim_hub)
    af = []

    async def fake_af(label):
        af.append(label)
    monkeypatch.setattr(eng, "_autofocus", fake_af)
    monkeypatch.setattr(eng, "_capture",
                        _returns({"hfr": 9.9, "stats": {"median": 100}, "saved_path": None}))
    plan = _tiny_plan(instructions=[Instruction(
        trigger="on_hfr_above", threshold=3.0, action="refocus")])
    eng.start(plan)
    await _wait_done(eng)
    assert af and af[0] == "triggered refocus"


def _multi_plan(names, *, count=1, instructions=None) -> SequencePlan:
    """N tiny non-calibration targets in plan order (all default 'now' windows,
    so schedule_order preserves that order)."""
    return SequencePlan(
        name="jumps", guide=False, dither_every=0, autofocus_every=0,
        targets=[Target(
            name=n, ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100,
                                count=count)])
            for n in names],
        instructions=instructions or [])


def _spy_steps(eng, monkeypatch) -> list[str]:
    """Record the target name of every step the engine actually enters."""
    seen: list[str] = []
    orig = eng._run_step

    async def spy(ti, si, target, step):
        seen.append(target.name)
        await orig(ti, si, target, step)
    monkeypatch.setattr(eng, "_run_step", spy)
    return seen


@pytest.mark.parametrize("action,expected,jumps,frames", [
    # run_target: abandon the running target (A stops after its 1st frame) and
    # make C the next one selected. That unwind spends jump budget.
    ("run_target", ["A", "C", "B"], 1, 5),
    # skip_target aimed at a FUTURE target: C is dropped from the night, but the
    # RUNNING target must NOT be abandoned — A shoots both its frames. No unwind,
    # so no jump budget is spent (the skip is queued, not raised).
    ("skip_target", ["A", "B"], 0, 4),
])
async def test_target_jump_actions(sim_hub, temp_store, monkeypatch, action,
                                   expected, jumps, frames):
    eng = SequenceEngine(sim_hub)
    shots = 0
    inner = _returns({"hfr": 9.9, "stats": {"median": 100}, "saved_path": None})

    async def counting_capture(*a, **k):
        nonlocal shots
        shots += 1
        return await inner(*a, **k)
    monkeypatch.setattr(eng, "_capture", counting_capture)
    seen = _spy_steps(eng, monkeypatch)
    plan = _multi_plan(["A", "B", "C"], count=2, instructions=[Instruction(
        trigger="on_hfr_above", threshold=3.0, action=action,
        target_arg="C", once=True)])
    eng.start(plan)
    await _wait_done(eng)
    assert seen == expected
    assert eng._jumps_spent == jumps
    # The frame count is what distinguishes "abandoned the running target" from
    # "let it finish" — the step-entry spy above cannot see the difference.
    assert shots == frames


async def test_skip_active_target_still_abandons_it(sim_hub, temp_store,
                                                    monkeypatch):
    """The other half of the rule: a skip_target aimed at the target being shot
    DOES abandon it (that is the requested behavior) — A stops after one frame
    and the night carries on with B."""
    eng = SequenceEngine(sim_hub)
    shots = 0
    inner = _returns({"hfr": 9.9, "stats": {"median": 100}, "saved_path": None})

    async def counting_capture(*a, **k):
        nonlocal shots
        shots += 1
        return await inner(*a, **k)
    monkeypatch.setattr(eng, "_capture", counting_capture)
    seen = _spy_steps(eng, monkeypatch)
    plan = _multi_plan(["A", "B"], count=2, instructions=[Instruction(
        trigger="on_hfr_above", threshold=3.0, action="skip_target",
        target_arg="A", once=True)])
    eng.start(plan)
    await _wait_done(eng)
    assert seen == ["A", "B"]
    assert eng._jumps_spent == 1          # skipping the ACTIVE target unwinds
    assert shots == 3                     # A: 1 frame then abandoned; B: 2


async def test_mutual_jumps_terminate_via_budget(sim_hub, temp_store, monkeypatch):
    """A -> run B and B -> run A, neither ever completing: the run MUST end via
    the hard jump budget (degrade + warn), never hang and never abort."""
    import astrodeck.sequence.engine as engine_mod

    assert engine_mod.MAX_JUMPS == 64          # pin the shipped budget
    monkeypatch.setattr(engine_mod, "MAX_JUMPS", 6)   # keep the test quick

    logs: list[tuple[str, str]] = []
    orig_log = engine_mod.bus.log
    monkeypatch.setattr(engine_mod.bus, "log",
                        lambda lvl, msg, src="hub": (logs.append((lvl, msg)),
                                                     orig_log(lvl, msg, src))[1])

    eng = SequenceEngine(sim_hub)
    monkeypatch.setattr(eng, "_capture",
                        _returns({"hfr": 1.0, "stats": {"median": 100}, "saved_path": None}))
    # every frame is rejected => on_frame_rejected re-fires each frame (no edge
    # latch to save us); _handle_reject short-circuits the escalation path.
    monkeypatch.setattr(eng, "_check_quality", lambda info, **kw: False)

    async def _handled(*a, **k):
        return True
    monkeypatch.setattr(eng, "_handle_reject", _handled)

    plan = _multi_plan(["A", "B"], count=1, instructions=[
        Instruction(trigger="on_frame_rejected", action="run_target",
                    target_arg="B", only_target="A"),
        Instruction(trigger="on_frame_rejected", action="run_target",
                    target_arg="A", only_target="B"),
    ])
    eng.start(plan)
    await _wait_done(eng, timeout=60.0)
    assert eng._jumps_spent == 6                       # budget spent, then capped
    assert any(lvl == "warning" and "jump budget exhausted" in msg
               for lvl, msg in logs)
    assert eng.state["state"] not in ("aborted",)      # degraded, never aborted


async def test_abort_action_ends_run(sim_hub, temp_store, monkeypatch):
    eng = SequenceEngine(sim_hub)
    monkeypatch.setattr(eng, "_capture",
                        _returns({"hfr": 9.9, "stats": {"median": 100}, "saved_path": None}))
    plan = _tiny_plan(instructions=[Instruction(
        trigger="on_hfr_above", threshold=3.0, action="abort", message="hfr runaway")])
    eng.start(plan)
    await _wait_done(eng)
    assert eng.state["state"] in ("aborted",)   # SafetyAbort => aborted, end_reason unsafe
