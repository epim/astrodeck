"""Task 4: accepted-frame quota engine (sessions spec §3).

Gates tested directly on _check_quality (crafted info dicts + a monkeypatched
_guide_rms); loop/guard behavior tested through real sim-hub runs with a
scripted quality gate (monkeypatching OUR OWN engine method, not the hub)."""
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target, quota_unbounded
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


def _plan(count=3, targets=1, exposure_s=0.05, **plan_kw) -> SequencePlan:
    return SequencePlan(name="q", guide=False, dither_every=0, autofocus_every=0,
                        meridian_flip=False, count_mode="accepted", **plan_kw,
                        targets=[Target(
                            name=f"T{n}", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=exposure_s,
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


# ---------------------------------------------------------- Fix round 1 (review)

# --------------------------------------------- MINOR: count_mode is a Literal

def test_count_mode_rejects_invalid_value():
    """A typo (e.g. "accpeted") used to silently degrade to "attempts" mode
    with no feedback (count_mode was a bare ``str``). Now a Literal, rejected
    at validation with a clear pydantic error."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SequencePlan(count_mode="accpeted")


def test_count_mode_still_accepts_the_two_real_values():
    assert SequencePlan(count_mode="attempts").count_mode == "attempts"
    assert SequencePlan(count_mode="accepted").count_mode == "accepted"


# ------------------------------------- IMPORTANT: unbounded accepted-quota gate

def test_quota_unbounded_helper():
    """Direct unit coverage of the ``quota_unbounded`` predicate: reviewer-
    verified that ``_enforce_stop_boundary`` never raises for a (now, None)
    window and the no-progress watchdog only WARNs, so BOTH reject guards off
    AND a boundary-less target is the exact unbounded combination."""
    unbounded = _plan(max_consecutive_rejects=0, max_consecutive_rejects_night=0)
    assert quota_unbounded(unbounded) is True

    # attempts mode never counts, regardless of guards
    attempts = _plan(max_consecutive_rejects=0, max_consecutive_rejects_night=0)
    attempts.count_mode = "attempts"
    assert quota_unbounded(attempts) is False

    # either guard alone is enough to make the run bounded
    assert quota_unbounded(
        _plan(max_consecutive_rejects=1, max_consecutive_rejects_night=0)) is False
    assert quota_unbounded(
        _plan(max_consecutive_rejects=0, max_consecutive_rejects_night=1)) is False

    # a stop boundary on every target is also enough
    bounded_run = _plan(max_consecutive_rejects=0, max_consecutive_rejects_night=0)
    bounded_run.targets[0].schedule.max_run_min = 30
    assert quota_unbounded(bounded_run) is False

    bounded_dawn = _plan(max_consecutive_rejects=0, max_consecutive_rejects_night=0)
    bounded_dawn.targets[0].schedule.stop_mode = "dawn"
    assert quota_unbounded(bounded_dawn) is False

    # reviewer-specified semantics (fix round 2): ANY non-calibration target
    # lacking a stop boundary fires the gate — a mixed plan's boundary-less
    # target's step loop is just as unbounded on its own.
    mixed = _plan(count=2, targets=2, max_consecutive_rejects=0,
                  max_consecutive_rejects_night=0)
    mixed.targets[0].schedule.max_run_min = 30
    assert quota_unbounded(mixed) is True

    # ...and bounding BOTH targets makes the same plan startable again
    mixed.targets[1].schedule.stop_mode = "dawn"
    assert quota_unbounded(mixed) is False

    # calibration targets never enter the accepted-mode quota loop -> a
    # calibration-only plan is never unbounded by this rule
    cal = SequencePlan(name="cal", count_mode="accepted", max_consecutive_rejects=0,
                       max_consecutive_rejects_night=0,
                       targets=[Target(name="darks", ra_hours=0.0, dec_deg=0.0,
                                       calibration=True,
                                       steps=[ExposureStep(exposure_s=1.0, count=3)])])
    assert quota_unbounded(cal) is False

    # ...and a boundary-less calibration target must not fire the gate when
    # mixed with a BOUNDED light target (only lights enter the quota loop)
    cal_mixed = _plan(max_consecutive_rejects=0, max_consecutive_rejects_night=0)
    cal_mixed.targets[0].schedule.stop_mode = "dawn"
    cal_mixed.targets.append(Target(name="darks", ra_hours=0.0, dec_deg=0.0,
                                    calibration=True,
                                    steps=[ExposureStep(exposure_s=1.0, count=3)]))
    assert quota_unbounded(cal_mixed) is False


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """Isolated TestClient exercising the REAL /api/sequence/start and
    /api/sequence/recover routes (mirrors tests/test_app_preflight.py's
    ``client`` fixture and test_session_engine.py's isolated-app pattern).
    The camera + engine.start are stubbed and solar is bypassed so only the
    NEW quota-unbounded start-gate is under test — a default (un-configured)
    site never blocks horizon (see app.py `_horizon_block`), so no site setup
    is needed either."""
    from astrodeck.config import ConfigStore
    import astrodeck.config as config_mod
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    from astrodeck.plans import PlanLibrary
    from astrodeck.profiles import ProfileLibrary
    monkeypatch.setattr(app_module, "plan_library",
                        PlanLibrary(directory=tmp_path / "plans"))
    monkeypatch.setattr(app_module, "profiles",
                        ProfileLibrary(directory=tmp_path / "profiles"))
    app = app_module.create_app()
    with TestClient(app) as c:
        monkeypatch.setattr(app_module.hub, "_check_solar", lambda *a, **kw: None)
        monkeypatch.setattr(app_module.hub, "require", lambda role: object())
        started = {"n": 0}
        monkeypatch.setattr(
            app_module.engine, "start",
            lambda plan, **kw: started.__setitem__("n", started["n"] + 1))
        c.started = started
        yield c


def _unbounded_accepted_payload() -> dict:
    return {
        "name": "q", "guide": False, "count_mode": "accepted",
        "max_consecutive_rejects": 0, "max_consecutive_rejects_night": 0,
        "targets": [{
            "name": "T1", "ra_hours": 5.5, "dec_deg": -5.0,
            "steps": [{"exposure_s": 1.0, "count": 3}],
        }],
    }


def _mutate_none(payload):
    return payload


def _mutate_one_guard(payload):
    payload["max_consecutive_rejects"] = 5      # one guard set -> bounded
    return payload


def _mutate_default_guards(payload):
    # dropping both keys falls back to the model defaults (10 / 20) -- the
    # existing accepted-mode default-guard behavior must be unaffected.
    del payload["max_consecutive_rejects"]
    del payload["max_consecutive_rejects_night"]
    return payload


def _mutate_stop_boundary(payload):
    payload["targets"][0]["schedule"] = {"max_run_min": 30}
    return payload


def _mutate_attempts_mode(payload):
    payload["count_mode"] = "attempts"
    return payload


@pytest.mark.parametrize("payload_mutator, expected_status, expected_started", [
    pytest.param(_mutate_none, 400, 0,
                 id="refuses_unbounded_accepted_quota"),
    pytest.param(_mutate_one_guard, 200, 1,
                 id="allows_accepted_quota_with_one_guard_set"),
    pytest.param(_mutate_default_guards, 200, 1,
                 id="allows_accepted_quota_with_default_guards"),
    pytest.param(_mutate_stop_boundary, 200, 1,
                 id="allows_unbounded_guards_with_a_stop_boundary"),
    pytest.param(_mutate_attempts_mode, 200, 1,
                 id="leaves_attempts_mode_unaffected"),
])
def test_sequence_start_quota_gate(api_client, payload_mutator, expected_status,
                                   expected_started):
    payload = payload_mutator(_unbounded_accepted_payload())
    r = api_client.post("/api/sequence/start", json=payload)
    assert r.status_code == expected_status, r.text
    if expected_status == 400:
        assert "unbounded" in r.json()["detail"].lower()
    assert api_client.started["n"] == expected_started        # never reached engine.start when refused


def test_sequence_start_mixed_plan_refused_until_every_target_bounded(api_client):
    """Fix round 2: with both guards 0, ONE bounded target does not save a plan
    whose OTHER target has no stop boundary (that target's step loop is just as
    unbounded on its own) -> 400. Bounding BOTH targets makes it startable."""
    payload = _unbounded_accepted_payload()
    payload["targets"] = [
        {"name": "T1", "ra_hours": 5.5, "dec_deg": -5.0,
         "schedule": {"stop_mode": "dawn"},
         "steps": [{"exposure_s": 1.0, "count": 3}]},
        {"name": "T2", "ra_hours": 6.5, "dec_deg": 10.0,
         "steps": [{"exposure_s": 1.0, "count": 3}]},        # no boundary
    ]
    r = api_client.post("/api/sequence/start", json=payload)
    assert r.status_code == 400, r.text
    assert "unbounded" in r.json()["detail"].lower()
    assert api_client.started["n"] == 0

    # inverse: bound the second target too -> the same plan starts
    payload["targets"][1]["schedule"] = {"max_run_min": 30}
    r = api_client.post("/api/sequence/start", json=payload)
    assert r.status_code == 200, r.text
    assert api_client.started["n"] == 1


def test_sequence_recover_refuses_unbounded_accepted_quota(api_client):
    from astrodeck.sequence.session import Session, SessionFrame
    plan = SequencePlan.model_validate(_unbounded_accepted_payload())
    s = Session(name="q", created_ts=1.0, status="dormant", plan=plan)
    s.frames.append(SessionFrame(ts=1.0, night="n1",
                                 target_id=plan.targets[0].id,
                                 step_id=plan.targets[0].steps[0].id,
                                 auto_accepted=False))
    session_store.save(s)
    r = api_client.post("/api/sequence/recover")
    assert r.status_code == 400, r.text
    assert "unbounded" in r.json()["detail"].lower()
    assert api_client.started["n"] == 0
    assert session_store.load(s.id).status == "dormant"   # untouched


# --------------------------------------------- COVERAGE: real gate + real loop

async def test_real_quality_gate_drives_real_capture_loop_reject(sim_hub):
    """Every reject-path test above scripts ``_check_quality`` (a canned verdict
    sequence via ``_script_gate``, monkeypatching OUR OWN engine method) — the
    REAL quality gate and the REAL capture loop were never exercised together
    with a genuine rejection. ``exposure_s=2.0`` is probe-verified (Task 3) to
    yield ~32 real detected stars in the sim, so ``min_stars=9999`` makes
    ``_check_quality`` genuinely reject every captured frame (unmocked); the
    per-step guard then ends the run after exactly 2 real rejected captures."""
    plan = _plan(count=1, exposure_s=2.0, min_stars=9999,
                max_consecutive_rejects=2, max_consecutive_rejects_night=0)
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    assert await wait_for(lambda: eng.state.get("state") == "complete", timeout=60)
    s = session_store.load(sid)
    # quota unmet (0 of 1 accepted) -> dormant, even though the scheduler
    # exhausted the single target naturally (the per-step guard returned out
    # of _run_step rather than raising NightQualityStop).
    assert s.status == "dormant"
    step = plan.targets[0].steps[0]
    assert s.accepted(step.id) == 0
    assert len(s.frames) == 2                        # guard tripped at 2
    for f in s.frames:
        assert f.auto_accepted is False               # genuinely rejected
        assert f.path                                 # accepted mode never unlinks
        assert Path(f.path).exists()                  # -- still on disk
