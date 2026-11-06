# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1076: a pending Windows restart holds AUTO-RESUME, not only the HTTP start
routes, and the operator's stored acknowledgement lets it resume.

The start routes refuse a night while Windows has a restart pending and part of
the night lies outside Windows Update's active hours (``Hub.arm_refusal``,
WP 3.7). ``ResumeArm.tick`` starts an armed session from its own loop, so
without its own check it would start straight into that restart.

MUTANT "the tick never asks arm_refusal" (the block after the weather veto in
``ResumeArm.tick`` deleted): RED, the pending-restart test starts the run and
no hold is set.
"""
from __future__ import annotations

from astrodeck.sequence import SequenceEngine
from astrodeck.sequence.resume_arm import RETRY_INTERVAL_S, ResumeArm
from astrodeck.sequence.session import session_store

from test_resume_arm import _dormant_armed, sim_hub, wait_for  # noqa: F401

REFUSAL = ("Windows has a restart pending and tonight runs outside Windows "
           "Update's active hours.")


async def test_a_pending_restart_holds_auto_resume_before_any_device_moves(
        sim_hub, monkeypatch, bus_lines):
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    monkeypatch.setattr(type(sim_hub), "arm_refusal",
                        lambda self, **kw: REFUSAL)
    await arm.tick()
    assert not engine.running, "auto-resume started into a pending restart"
    assert arm._retry_at == now["t"] + RETRY_INTERVAL_S
    assert arm.hold is not None and REFUSAL in arm.hold["reason"]
    assert any("auto-resume held: Windows has a restart pending" in m
               for _lvl, m, _src in bus_lines)


async def test_an_acknowledged_restart_lets_auto_resume_start(
        sim_hub, monkeypatch, bus_lines):
    """The real gate, with the restart pending and acknowledged: the stored
    acknowledgement is honoured inside ``arm_refusal``, so the tick resumes."""
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    seen = {}

    def gate(self, *, acknowledged=False, now=None):
        seen["asked"] = True
        return None             # what arm_refusal answers once acknowledged
    monkeypatch.setattr(type(sim_hub), "arm_refusal", gate)
    await arm.tick()
    assert seen.get("asked"), "the tick never asked the restart gate"
    assert arm._retry_at == 0.0, (
        "the tick backed off instead of resuming:\n"
        + "\n".join(f"  [{lv}] {msg}" for lv, msg, _src in bus_lines))
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert session_store.load(sid).status == "complete"


async def test_the_stored_acknowledgement_reaches_the_tick_through_the_real_gate(
        sim_hub, monkeypatch):
    """No fake gate: the real ``Hub.arm_refusal`` with a pending restart faked
    at its source. Unacknowledged it holds; after ``acknowledge_restart`` the
    next tick resumes."""
    import astrodeck.bootcause as bootcause
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    pending = bootcause.RestartState(True, ("windows_update",),
                                     *bootcause.DEFAULT_ACTIVE_HOURS)
    monkeypatch.setattr(bootcause, "restart_state", lambda *a, **k: pending)
    await arm.tick()
    assert not engine.running and arm._retry_at > 0.0, (
        "an unacknowledged pending restart did not hold auto-resume")
    assert sim_hub.acknowledge_restart() is True
    now["t"] += RETRY_INTERVAL_S + 1
    await arm.tick()
    assert arm._retry_at == 0.0, "an acknowledged restart still held auto-resume"
