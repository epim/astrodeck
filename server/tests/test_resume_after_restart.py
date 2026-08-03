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


# ------------------------------------------------------- device fingerprint

@pytest.fixture
def fp(tmp_path, monkeypatch):
    from astrodeck.devices import fingerprint as _fp
    monkeypatch.setattr(_fp, "_PATH", tmp_path / "fp.json")
    _fp.reset_for_tests()
    return _fp


def test_focus_is_untrusted_when_the_focuser_forgot_its_position(fp):
    """The EAF forgets its position on power loss, so a focuser reporting a
    different number than we last recorded is reporting a DEFAULT, not a
    measurement. That mismatch IS the power-loss tell -- and it catches a yanked
    USB hub too, which no OS shutdown event would."""
    fp.record(focuser_position=9935, filter_slot=7, ra_hours=1.0,
              dec_deg=2.0, parked=True, tracking=False)
    assert fp.verdict(focuser_position=9935).focus_trusted is True
    assert fp.verdict(focuser_position=0).focus_trusted is False


def test_a_missing_fingerprint_trusts_nothing(fp):
    """First-ever boot or a wiped state dir. Distrust costs an autofocus;
    misplaced trust costs a night of blurred frames."""
    assert fp.verdict(focuser_position=9935).focus_trusted is False


def test_an_unreadable_fingerprint_trusts_nothing(fp, tmp_path):
    """A truncated file must not read as agreement."""
    (tmp_path / "fp.json").write_text("{not json", encoding="utf-8")
    assert fp.verdict(focuser_position=9935).focus_trusted is False


def test_writes_are_coalesced(fp, monkeypatch):
    """The status poll runs several times a second for a value that rarely
    changes; without coalescing this rewrites the file continuously."""
    now = [1000.0]
    monkeypatch.setattr(fp, "_now", lambda: now[0])
    fp.record(focuser_position=1, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    fp.record(focuser_position=2, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    assert fp.verdict(focuser_position=1).focus_trusted is True   # 2nd coalesced
    now[0] += fp.FINGERPRINT_WRITE_INTERVAL_S + 1
    fp.record(focuser_position=2, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    assert fp.verdict(focuser_position=2).focus_trusted is True


def test_recording_never_raises(fp, monkeypatch):
    """Bookkeeping must never break a run, whatever the disk is doing."""
    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(fp, "write_json_atomic", _boom)
    fp.record(focuser_position=1, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)   # must not raise


async def test_poll_status_records_the_fingerprint(fp, monkeypatch):
    """The recorder must be fed by the real status path, not only by tests.

    Without this the module would be perfectly correct and never called -- the
    exact shape of the #112 autofocus bug, where a working metric was wired into
    a code path the rig does not run."""
    import astrodeck.hub as hubmod
    h = hubmod.Hub()
    await h.connect_sim()
    try:
        await h.poll_status()
    finally:
        await h.disconnect_all()
    raw = fp.read_json_or(fp._path(), None)
    assert isinstance(raw, dict), "poll_status never wrote a fingerprint"
    assert "focuser_position" in raw and "parked" in raw
