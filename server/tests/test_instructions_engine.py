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


@pytest.mark.parametrize("target_arg", ["ZZZ", "A"])
async def test_degenerate_run_target_does_not_abandon_the_active_target(
        sim_hub, temp_store, monkeypatch, target_arg):
    """The two documented ``run_target`` NO-OPS must really be no-ops.

    ``ZZZ`` = a typo'd/unknown name; ``A`` = a run aimed at the target already
    being shot (the natural "re-run this" intent). Both log "ignored"/"no-op",
    and both used to still cost the user the running target because the caller
    removed it unconditionally. A is re-selected and finishes its quota."""
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
        trigger="on_hfr_above", threshold=3.0, action="run_target",
        target_arg=target_arg, once=True)])
    eng.start(plan)
    await _wait_done(eng)
    # A is re-entered (resuming from its persisted per-step count) rather than
    # vanishing from the night, and every planned frame is still shot.
    assert seen == ["A", "A", "B"]
    # 5, not 4: the instruction eval runs before `_done` advances for the frame
    # that raised, so the resumed step re-shoots that one sub (it is on disk and
    # in the report either way). One duplicate sub beats losing the target.
    assert shots == 5
    assert eng._jumps_spent == 1          # the unwind still spent budget


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


# ------------------------------------------------- a failed action must re-arm

async def test_a_refocus_that_FAILED_leaves_the_rule_armed(sim_hub, temp_store,
                                                           monkeypatch):
    """AN EDGE TRIGGER ASSUMES ITS ACTION WORKED.

    `on_hfr_above` fires on the rising edge and re-arms only when HFR drops back
    to or below the threshold. That is right when the refocus fixes the focus.
    When the refocus FAILS, HFR stays high, the rule stays disarmed, and the
    promise 'refocus when HFR exceeds 3.2' quietly expires for the rest of the
    night after exactly one attempt.

    Measured on the rig 2026-08-18: the rule fired at 21:22, the sweep aborted
    at 21:27 ('triggered refocus failed'), and nothing tried again. The operator
    read that as the analyser never noticing.
    """
    eng = SequenceEngine(sim_hub)
    af = []

    async def failing_af(label):
        af.append(label)
        return False                    # the sweep could not find focus

    monkeypatch.setattr(eng, "_autofocus", failing_af)
    monkeypatch.setattr(eng, "_capture",
                        _returns({"hfr": 9.9, "stats": {"median": 100}, "saved_path": None}))
    plan = _tiny_plan(instructions=[Instruction(
        trigger="on_hfr_above", threshold=3.0, action="refocus")])
    plan.targets[0].steps[0].count = 4
    eng.start(plan)
    await _wait_done(eng)
    assert len(af) > 1, (
        f"the rule fired {len(af)} time(s) across 4 high-HFR frames — a refocus "
        "that failed must leave the trigger armed, or one bad sweep retires the "
        "rule for the night")


async def test_a_refocus_that_WORKED_does_not_re_fire(sim_hub, temp_store,
                                                      monkeypatch):
    """The no-double-fire guarantee still holds for the case it was written for:
    a successful refocus must not run again on every subsequent frame just
    because HFR is still being reported above the threshold."""
    eng = SequenceEngine(sim_hub)
    af = []

    async def ok_af(label):
        af.append(label)
        return True

    monkeypatch.setattr(eng, "_autofocus", ok_af)
    monkeypatch.setattr(eng, "_capture",
                        _returns({"hfr": 9.9, "stats": {"median": 100}, "saved_path": None}))
    plan = _tiny_plan(instructions=[Instruction(
        trigger="on_hfr_above", threshold=3.0, action="refocus")])
    plan.targets[0].steps[0].count = 4
    eng.start(plan)
    await _wait_done(eng)
    assert len(af) == 1, (
        f"a successful refocus fired {len(af)} times over 4 frames — the rising "
        "edge must still be a rising edge")


async def test_a_rule_that_keeps_failing_gives_up_and_says_so(sim_hub, temp_store,
                                                              monkeypatch):
    """Re-arming must be BOUNDED. A focuser that cannot find focus at all would
    otherwise run a full sweep between every frame for the rest of the night,
    spending the whole session on a sweep that is never going to work."""
    eng = SequenceEngine(sim_hub)
    af = []

    async def failing_af(label):
        af.append(label)
        return False

    monkeypatch.setattr(eng, "_autofocus", failing_af)
    monkeypatch.setattr(eng, "_capture",
                        _returns({"hfr": 9.9, "stats": {"median": 100}, "saved_path": None}))
    plan = _tiny_plan(instructions=[Instruction(
        trigger="on_hfr_above", threshold=3.0, action="refocus")])
    plan.targets[0].steps[0].count = 12
    eng.start(plan)
    await _wait_done(eng)
    assert 1 < len(af) <= 4, (
        f"fired {len(af)} times over 12 frames — re-arming after a failure must "
        "be capped, not unbounded")


async def test_autofocus_reports_whether_it_actually_found_focus(sim_hub,
                                                                 temp_store,
                                                                 monkeypatch):
    """The re-arm rests entirely on `_autofocus` telling the truth about its
    outcome. Every other test here patches `_autofocus` out, so without this one
    the method could `return True` unconditionally and nothing would notice —
    and the rule would be retired by the first failed sweep exactly as before.
    """
    import astrodeck.sequence.engine as eng_mod
    from astrodeck.focus.autofocus import AutofocusResult

    eng = SequenceEngine(sim_hub)
    outcome = {"success": True}

    async def fake_run_autofocus(*a, **k):
        return AutofocusResult(outcome["success"], 11193, 3.05, [],
                               "" if outcome["success"] else "could not measure 9750")

    monkeypatch.setattr(eng_mod, "run_autofocus", fake_run_autofocus)
    assert await eng._autofocus("test") is True

    outcome["success"] = False
    assert await eng._autofocus("test") is False, (
        "a sweep that could not find focus reported success — the instruction "
        "dispatcher believes this, so the rule would never be re-armed")
