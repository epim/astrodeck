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


async def test_abort_action_ends_run(sim_hub, temp_store, monkeypatch):
    eng = SequenceEngine(sim_hub)
    monkeypatch.setattr(eng, "_capture",
                        _returns({"hfr": 9.9, "stats": {"median": 100}, "saved_path": None}))
    plan = _tiny_plan(instructions=[Instruction(
        trigger="on_hfr_above", threshold=3.0, action="abort", message="hfr runaway")])
    eng.start(plan)
    await _wait_done(eng)
    assert eng.state["state"] in ("aborted",)   # SafetyAbort => aborted, end_reason unsafe
