"""Resume after a mid-night restart (spec docs/superpowers/specs/2026-08-02)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, session_store


@pytest.fixture
def client():
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _isolated_sessions(tmp_path, monkeypatch):
    """Sessions land under a temp CAPTURE_DIR, never the developer's real one."""
    import astrodeck.hub as hubmod
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", tmp_path)
    yield


class _StubHub:
    """Enough hub for SequenceEngine.start to attach a session. The engine's
    run loop is aborted immediately, so no device is ever touched."""
    devices: dict = {}
    site: dict = {}
    looping = False

    def require(self, role):
        raise RuntimeError(f"no {role} in this test")

    def get(self, role, default=None):
        return default


def _mk(status: str, **kw) -> Session:
    plan = SequencePlan(name="p", targets=[Target(
        name="t", ra_hours=1.0, dec_deg=2.0,
        steps=[ExposureStep(filter="L", exposure_s=1, gain=100, count=2)])])
    s = Session(name="p", status=status, plan=plan, **kw)
    session_store.save(s)
    return s


def test_arming_an_active_session_is_allowed(client):
    """The session a crash destroys is ACTIVE, so it is the one that must be
    armable. Arming was dormant-only, which meant the run that most needed to
    come back was the exact run that could not be told to. Demonstrated on the
    rig 2026-08-02: a mid-run reboot left the session dormant, correct, inert."""
    s = _mk("active")
    r = client.patch(f"/api/sessions/{s.id}", json={"auto_resume": True})
    assert r.status_code == 200, r.text
    assert r.json()["auto_resume"] is True
    assert session_store.load(s.id).auto_resume is True


def test_arming_still_disarms_every_other_session(client):
    """The server-enforced singleton must survive allowing active sessions."""
    old = _mk("dormant", auto_resume=True)
    new = _mk("active")
    r = client.patch(f"/api/sessions/{new.id}", json={"auto_resume": True})
    assert r.status_code == 200, r.text
    assert session_store.load(old.id).auto_resume is False
    assert session_store.load(new.id).auto_resume is True


def test_a_finished_session_still_refuses_arming(client):
    """'complete'/'abandoned' have nothing left to resume — still a 409."""
    s = _mk("abandoned")
    r = client.patch(f"/api/sessions/{s.id}", json={"auto_resume": True})
    assert r.status_code == 409


async def test_a_new_run_is_armed_by_default():
    """An opt-in flag that must be remembered before every night is a flag that
    is not set on the night it was needed. Asserted at the ENGINE, which is the
    seam every fresh-run path (start route, recover route) goes through."""
    from astrodeck.sequence.engine import SequenceEngine
    plan = SequencePlan(name="d", targets=[Target(
        name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
        center=False, autofocus_first=False,
        steps=[ExposureStep(filter="Dark", exposure_s=1, gain=100, count=1)])])
    eng = SequenceEngine(_StubHub())
    eng.start(plan)
    try:
        assert eng._session.auto_resume is True, (
            "a started run must be armed, or a 2am restart leaves it inert")
    finally:
        await eng.abort()


async def test_starting_a_fresh_run_disarms_the_previous_one():
    """The server-enforced singleton must hold for engine-side arming too."""
    from astrodeck.sequence.engine import SequenceEngine
    old = _mk("dormant", auto_resume=True)
    plan = SequencePlan(name="d2", targets=[Target(
        name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
        center=False, autofocus_first=False,
        steps=[ExposureStep(filter="Dark", exposure_s=1, gain=100, count=1)])])
    eng = SequenceEngine(_StubHub())
    eng.start(plan)
    try:
        assert session_store.load(old.id).auto_resume is False
    finally:
        await eng.abort()


async def test_resuming_keeps_the_existing_arm():
    """ResumeArm passes session=..., which must keep the session armed rather
    than running the fresh-session arming path."""
    from astrodeck.sequence.engine import SequenceEngine
    s = _mk("dormant", auto_resume=True)
    eng = SequenceEngine(_StubHub())
    eng.start(s.plan, session=s)
    try:
        assert s.auto_resume is True
    finally:
        await eng.abort()
