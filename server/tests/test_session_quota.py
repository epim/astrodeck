"""Task 4: accepted-frame quota engine (sessions spec §3).

Gates tested directly on _check_quality (crafted info dicts + a monkeypatched
_guide_rms); loop/guard behavior tested through real sim-hub runs with a
scripted quality gate (monkeypatching OUR OWN engine method, not the hub)."""
import asyncio
from pathlib import Path

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.session import session_store


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return False


def _plan(count=3, targets=1, **plan_kw) -> SequencePlan:
    return SequencePlan(name="q", guide=False, dither_every=0, autofocus_every=0,
                        meridian_flip=False, count_mode="accepted", **plan_kw,
                        targets=[Target(
                            name=f"T{n}", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=count)])
                            for n in range(targets)])


def _script_gate(monkeypatch, verdicts):
    """Scripted quality gate: pops the next verdict per capture, True after."""
    seq = list(verdicts)

    def fake(self, info, *, record=True, calibration=False):
        return seq.pop(0) if seq else True
    monkeypatch.setattr(SequenceEngine, "_check_quality", fake)


# ---------------------------------------------------------------- gate logic

async def test_quality_gates_and_together(sim_hub, monkeypatch):
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(name="g", min_stars=50, max_guide_rms=1.5)
    monkeypatch.setattr(eng, "_guide_rms", lambda: None)
    assert eng._check_quality({"hfr": 2.0, "stars": 60}) is True
    assert eng._check_quality({"hfr": 2.0, "stars": 10}) is False   # star floor
    monkeypatch.setattr(eng, "_guide_rms", lambda: 2.0)
    assert eng._check_quality({"hfr": 2.0, "stars": 60}) is False   # RMS ceiling
    monkeypatch.setattr(eng, "_guide_rms", lambda: 1.0)
    assert eng._check_quality({"hfr": 2.0, "stars": 60}) is True
    # calibration frames skip the star/RMS gates entirely
    monkeypatch.setattr(eng, "_guide_rms", lambda: 9.9)
    assert eng._check_quality({"stars": 0}, calibration=True) is True
    # both gates 0 = off (default): nothing rejects
    eng2 = SequenceEngine(sim_hub)
    eng2.plan = SequencePlan(name="off")
    assert eng2._check_quality({"hfr": 9.0, "stars": 0}) is True


async def test_hfr_median_gate_still_works_with_new_gates_off(sim_hub):
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(name="h", hfr_reject_factor=1.5)
    for _ in range(4):
        assert eng._check_quality({"hfr": 2.0}) is True   # seed the median
    assert eng._check_quality({"hfr": 9.0}) is False      # spike rejected
    assert eng._recent_hfr == [2.0, 2.0, 2.0, 2.0]        # reject NOT folded in


# ------------------------------------------------------------- quota loop

async def test_quota_mode_shoots_until_accepted_and_keeps_rejects(sim_hub, monkeypatch):
    plan = _plan(count=3)
    _script_gate(monkeypatch, [False, False, True, True, True])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    s = session_store.load(sid)
    assert s.status == "complete"
    step = plan.targets[0].steps[0]
    assert len(s.frames) == 5                      # 2 rejects + 3 accepted
    assert s.accepted(step.id) == 3
    rejected = [f for f in s.frames if not f.auto_accepted]
    assert len(rejected) == 2
    # accepted mode NEVER unlinks a rejected frame (regrading needs the file)
    for f in rejected:
        if f.path:
            assert Path(f.path).exists()
    # progress counted accepted-only (frames_done advances on accept only)
    assert eng.state["progress"]["frames_done"] == 3
    assert eng.state["progress"]["frames_total"] == 3


async def test_per_step_guard_skips_step_and_leaves_dormant(sim_hub, monkeypatch):
    plan = _plan(count=3, max_consecutive_rejects=2,
                 max_consecutive_rejects_night=0)      # night guard OFF
    _script_gate(monkeypatch, [False] * 50)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    s = session_store.load(sid)
    # quota unmet -> dormant even though the scheduler exhausted naturally
    assert s.status == "dormant"
    step = plan.targets[0].steps[0]
    assert s.accepted(step.id) == 0
    assert len(s.frames) == 2                      # guard tripped at 2


async def test_per_night_guard_crosses_targets_sets_quality(sim_hub, monkeypatch):
    plan = _plan(count=2, targets=2, max_consecutive_rejects=2,
                 max_consecutive_rejects_night=3)
    _script_gate(monkeypatch, [False] * 50)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("end_reason") == "quality")
    s = session_store.load(sid)
    assert s.status == "dormant"
    # target 1 tripped the per-step guard at 2; target 2's first reject made 3
    # ACROSS the boundary -> night guard -> early dormancy
    assert len(s.frames) == 3


async def test_night_counter_resets_on_accept(sim_hub, monkeypatch):
    plan = _plan(count=2, max_consecutive_rejects=0,
                 max_consecutive_rejects_night=3)
    _script_gate(monkeypatch, [False, False, True, False, False, False])
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("end_reason") == "quality")
    s = session_store.load(sid)
    # 2 rejects, then an accept RESET the counter, then 3 more tripped it
    assert len(s.frames) == 6
    assert s.total_accepted() == 1


async def test_attempts_mode_unchanged_by_default(sim_hub):
    plan = _plan(count=3)
    plan.count_mode = "attempts"
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete")
    s = session_store.load(sid)
    assert s.status == "complete"
    assert len(s.frames) == 3                      # exactly count attempts
