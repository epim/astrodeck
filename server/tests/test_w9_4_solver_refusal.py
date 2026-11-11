# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 9.4 (#721): a run that centres by plate solve does not start on a rig
with no plate solver, and auto-resume does not arm such a session.

THE NIGHT IT COMES FROM. 2026-10-18: a rig with no plate solver held a single
target for its whole window under a sentence about waiting for light. WP 2.2
ended the hold as a fault on the first attempt; what was left is that the
night began at all. ``Hub.solver_refusal`` is the start gate's question: when
the plan carries a target that centres (a sky target, ``center`` on) and
``resolve("solve")`` raises ``SolverUnavailable``, it answers one fixed
sentence naming what to install or set. A plan with nothing that centres
(darks, bias, flats, or every target ``center`` off) starts as it always did,
and so does a rig with a solver or the simulator.

The five start doors are ``test_start_paths_refuse_camera_owned.py``'s. The
solver is resolved through ``astrodeck.providers.resolve``, replaced here by a
function that raises, so nothing reads this machine's disk for ASTAP.

NAMED MUTANT: the missing-solver case falls through to no-light (the
``except SolverUnavailable`` branch of ``Hub.solver_refusal`` answers None).
Every refusal test below turns red.
"""
from __future__ import annotations

import pytest

import astrodeck.api.app as app_module
import astrodeck.providers as providers
from astrodeck.hub import SOLVER_MISSING_REFUSAL
from astrodeck.providers import ProviderChoice, SolverUnavailable
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.resume_arm import ResumeArm
from astrodeck.sequence.session import session_store
from test_flows_continue import rig  # noqa: F401 (fixture)
from test_resume_arm import _dormant_armed, sim_hub, wait_for  # noqa: F401
from test_start_paths_refuse_camera_owned import ROUTES, _bytes, _dormant


def _missing(capability, hub, *args, **kw):
    raise SolverUnavailable("plate solving unavailable: ASTAP not found")


def _no_solver(monkeypatch):
    monkeypatch.setattr(providers, "resolve", _missing)


def _plan(*, center=True, calibration=False) -> SequencePlan:
    return SequencePlan(name="w94", targets=[Target(
        name="T", ra_hours=5.0, dec_deg=10.0, center=center,
        calibration=calibration,
        steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)])])


# ------------------------------------------------------------ the question

def test_a_centring_target_with_no_solver_is_refused_in_one_sentence(
        sim_hub, monkeypatch):
    _no_solver(monkeypatch)
    why = sim_hub.solver_refusal(_plan())
    assert why == SOLVER_MISSING_REFUSAL
    assert "plate solver" in why and "ASTAP" in why and "ASTAP_PATH" in why
    assert "light" not in why, "a missing solver was worded as a wait for light"


def test_the_refusal_is_not_the_no_light_or_the_hold_sentence(sim_hub):
    from astrodeck.hub import SOLVE_REASON_SOLVER_MISSING
    assert SOLVER_MISSING_REFUSAL != SOLVE_REASON_SOLVER_MISSING
    assert "retry" not in SOLVER_MISSING_REFUSAL.lower()


@pytest.mark.parametrize("plan", [
    _plan(center=False),
    _plan(calibration=True),
    SequencePlan(name="empty", targets=[]),
], ids=["centring off", "calibration only", "no targets"])
def test_a_plan_that_needs_no_solve_is_not_asked(sim_hub, monkeypatch, plan):
    _no_solver(monkeypatch)
    assert sim_hub.solver_refusal(plan) is None


def test_a_rig_with_a_solver_is_not_refused(sim_hub, monkeypatch):
    monkeypatch.setattr(providers, "resolve", lambda cap, hub, *a, **k:
                        ProviderChoice("astap", "ASTAP", "ASTAP found"))
    assert sim_hub.solver_refusal(_plan()) is None


def test_the_simulator_rig_is_not_refused(sim_hub):
    """The real resolver, no patch: the sim rig has no real motion device, so
    the simulator solver serves it."""
    assert sim_hub.solver_refusal(_plan()) is None


# ------------------------------------------------------------- the routes

@pytest.mark.parametrize("name,request_", ROUTES, ids=[n for n, _ in ROUTES])
async def test_every_start_refuses_a_centring_run_with_no_solver(
        rig, monkeypatch, name, request_):
    fid, sid = await _dormant(rig)
    _no_solver(monkeypatch)
    starts_before = len(rig.starts)
    session_before = _bytes(sid)

    r = await request_(rig, fid, sid)

    assert r.status_code == 409, f"{name} answered {r.status_code}: {r.text}"
    detail = r.json()["detail"]
    assert detail["code"] == "solver_missing", detail
    assert detail["detail"] == SOLVER_MISSING_REFUSAL, detail
    assert len(rig.starts) == starts_before, f"{name} reached engine.start"
    assert _bytes(sid) == session_before, f"{name}'s refusal wrote the session"


@pytest.mark.parametrize("name,request_", ROUTES, ids=[n for n, _ in ROUTES])
async def test_control_with_a_solver_every_start_starts(
        rig, name, request_):
    fid, sid = await _dormant(rig)
    starts_before = len(rig.starts)

    r = await request_(rig, fid, sid)

    assert r.status_code == 200, f"{name} answered {r.status_code}: {r.text}"
    assert len(rig.starts) == starts_before + 1


async def test_force_does_not_waive_the_missing_solver(rig, monkeypatch):
    fid, sid = await _dormant(rig)
    _no_solver(monkeypatch)
    r = await rig.client.post("/api/sequence/start", json={
        "name": "t", "force": True, "targets": [
            {"name": "M42", "ra_hours": 5.588, "dec_deg": -5.39,
             "steps": [{"filter": None, "exposure_s": 0.05, "count": 1}]}]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "solver_missing"


async def test_a_start_that_centres_nothing_still_starts_with_no_solver(
        rig, monkeypatch):
    fid, sid = await _dormant(rig)
    _no_solver(monkeypatch)
    starts_before = len(rig.starts)
    r = await rig.client.post("/api/sequence/start", json={
        "name": "t", "targets": [
            {"name": "M42", "ra_hours": 5.588, "dec_deg": -5.39,
             "center": False,
             "steps": [{"filter": None, "exposure_s": 0.05, "count": 1}]}]})
    assert r.status_code == 200, r.text
    assert len(rig.starts) == starts_before + 1


# ------------------------------------------------------------- auto-resume

def _make_session_centre(sid: str) -> None:
    s = session_store.load(sid)
    for t in s.plan.targets:
        t.center = True
    session_store.save(s)


async def test_auto_resume_disarms_a_session_the_start_would_refuse(
        sim_hub, monkeypatch, bus_lines):
    """The precheck refuses this start every time, so the tick must not hold
    it for a retry that cannot succeed: it disarms, says why, and starts
    nothing, as it does for the single-night session whose night has passed."""
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    _make_session_centre(sid)
    _no_solver(monkeypatch)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)

    await arm.tick()

    assert not engine.running, "auto-resume started a run with no solver"
    assert session_store.load(sid).auto_resume is False, "the session stayed armed"
    assert arm._retry_at == 0.0, "a permanent refusal was held for a retry"
    assert any(SOLVER_MISSING_REFUSAL in m and lvl == "error"
               for lvl, m, _src in bus_lines), bus_lines


async def test_auto_resume_with_a_solver_is_unchanged(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    _make_session_centre(sid)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)

    await arm.tick()

    assert session_store.load(sid).auto_resume is True
    assert arm._retry_at == 0.0
    assert await wait_for(lambda: engine.state.get("state") == "complete")


async def test_auto_resume_of_a_session_that_centres_nothing_is_unchanged(
        sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)      # its plan has center off
    _no_solver(monkeypatch)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)

    await arm.tick()

    assert session_store.load(sid).auto_resume is True
    assert arm._retry_at == 0.0
