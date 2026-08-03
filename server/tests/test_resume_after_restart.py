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


# --------------------------------------------------------- recovery ladder

class _RecFoc:
    connected = True

    def __init__(self, pos=9935):
        self._pos = pos
        self.moves: list[int] = []

    async def get_position(self):
        return self._pos

    async def move_to(self, p):
        self.moves.append(p)


class _Connected:
    connected = True


class _RecHub:
    """Records what the ladder asked the rig to do. Nothing physical happens."""

    def __init__(self, *, solve_raises=None, center_raises=None, focuser=None):
        self.calls: list[str] = []
        self.centered: list[tuple] = []
        self._solve_raises = solve_raises
        self._center_raises = center_raises
        self.focuser = focuser or _RecFoc()
        # A READY rig: the readiness gate requires camera + telescope connected,
        # so a fake without them models a half-finished boot, not a working rig.
        self.devices = {"focuser": self.focuser,
                        "camera": _Connected(), "telescope": _Connected()}
        self.site = {}

    def require(self, role):
        if role == "focuser":
            return self.focuser
        if role == "camera":
            return object()
        raise RuntimeError(role)

    async def solve_and_sync(self, exposure_s: float = 3.0, *, blind: bool = False):
        self.blind_used = blind
        self.calls.append("solve")
        if self._solve_raises:
            raise self._solve_raises
        return {"ok": True}

    async def goto_and_center(self, ra, dec, *a, **k):
        self.calls.append("center")
        if self._center_raises:
            raise self._center_raises
        self.centered.append((ra, dec))


def _arm(hub, **kw):
    from astrodeck.sequence.resume_arm import ResumeArm
    return ResumeArm(_StubEngine(), hub, **kw)


class _StubEngine:
    running = False

    def __init__(self):
        self.started: list = []

    def start(self, plan, *, session=None):
        self.started.append(session)


def _light_session() -> Session:
    return _mk("dormant", auto_resume=True)


async def test_the_blind_solve_runs_even_when_nothing_changed(fp):
    """UNCONDITIONAL -- and this test exists to keep it that way.

    The AM5 is a harmonic drive with no brake, so a restart that preserved every
    byte of software state still cannot rule out that the tube sagged while the
    motors were unpowered, and the encoders cannot report a shift that happened
    while the mount was off. A later optimisation that skips verification
    'because the fingerprint matched' would silently reintroduce exactly that
    hazard -- so the PERFECTLY MATCHING fingerprint is the case asserted here."""
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    hub = _RecHub(focuser=_RecFoc(9935))
    arm = _arm(hub)
    assert await arm._recover(_light_session()) is None
    assert "solve" in hub.calls, "pointing must be re-measured even when nothing changed"


async def test_a_failed_solve_refuses_to_move(fp):
    """Too few stars under cloud. The alternative to refusing is slewing an OTA
    whose true position is unknown, toward a pier."""
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    hub = _RecHub(solve_raises=RuntimeError("Not enough stars"),
                  focuser=_RecFoc(9935))
    arm = _arm(hub)
    reason = await arm._recover(_light_session())
    assert reason is not None and "solve" in reason.lower()
    assert hub.centered == [], "must not slew on an unverified position"


async def test_untrusted_focus_runs_autofocus_and_never_restores_a_number(fp):
    """Measure, do not guess: driving the focuser to a remembered position is a
    guess about a device that just reported it lost count."""
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    foc = _RecFoc(0)                       # forgot its position
    hub = _RecHub(focuser=foc)
    arm = _arm(hub)
    ran = []
    arm._autofocus = lambda: (ran.append(1), None)[1] or _noop()
    await arm._recover(_light_session())
    assert ran == [1], "untrusted focus must be re-measured"
    assert foc.moves == [], "must never drive the focuser to a remembered number"


async def _noop():
    return None


async def test_trusted_focus_skips_autofocus(fp):
    """A clean reboot preserved focus exactly; forcing an autofocus would spend
    ten minutes of dark sky fixing a problem that did not happen."""
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    hub = _RecHub(focuser=_RecFoc(9935))
    arm = _arm(hub)
    ran = []
    arm._autofocus = lambda: (ran.append(1), None)[1] or _noop()
    await arm._recover(_light_session())
    assert ran == []


async def test_a_calibration_only_session_never_centers(fp):
    """Darks never slew. The solve still runs and is harmless."""
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=True, tracking=False)
    s = _mk("dormant", auto_resume=True)
    s.plan.targets[0].calibration = True
    hub = _RecHub(focuser=_RecFoc(9935))
    arm = _arm(hub)
    assert await arm._recover(s) is None
    assert hub.centered == []


async def test_a_refusal_leaves_the_session_dormant_and_armed(fp, bus_lines, monkeypatch):
    """Fail safe: the next tick must retry, so the arming has to survive and the
    session must NOT be handed to the engine."""
    import astrodeck.sequence.resume_arm as ra
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    s = _mk("dormant", auto_resume=True)
    hub = _RecHub(solve_raises=RuntimeError("Not enough stars"),
                  focuser=_RecFoc(9935))
    arm = _arm(hub)
    monkeypatch.setattr(arm, "_window_open", lambda *a, **k: True)
    await arm.tick()
    assert session_store.load(s.id).status == "dormant"
    assert session_store.load(s.id).auto_resume is True, "must stay armed to retry"
    assert arm._retry_at > 0, "backoff must be armed"
    assert arm.engine.started == [], "must not resume on an unverified position"
    assert any("solve" in m.lower() for _l, m, _s in bus_lines), \
        "the operator must be told why the night is on hold"


async def test_a_half_finished_boot_is_not_a_refusal(fp, bus_lines, monkeypatch):
    """Observed on the rig 2026-08-02: boot sweep at 21:50:43, resume tick at
    21:50:44, devices connected at 21:50:46. In that two-second window the
    ladder's solve failed with 'no camera connected', which was read as a
    transient inability to verify the sky and armed the TEN MINUTE backoff. The
    rig then sat idle for ten minutes under clear sky with a working camera and
    an armed session.

    Still booting must cost one 60s tick, not ten minutes, and must not shout."""
    _mk("dormant", auto_resume=True)

    class _BootingHub(_RecHub):
        def __init__(self):
            super().__init__()
            self.devices = {}          # nothing connected yet

    hub = _BootingHub()
    arm = _arm(hub)
    monkeypatch.setattr(arm, "_window_open", lambda *a, **k: True)
    await arm.tick()
    assert arm._retry_at == 0.0, "a half-finished boot must not arm the backoff"
    assert "solve" not in hub.calls, "must not try to solve before devices exist"
    assert not any("refus" in m.lower() or "held" in m.lower()
                   for _l, m, _s in bus_lines), \
        "booting is not something to alarm the operator about"


async def test_ready_devices_proceed_to_the_ladder(fp, monkeypatch):
    """The readiness gate must not become a permanent block."""
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    _mk("dormant", auto_resume=True)

    class _Dev:
        connected = True

    hub = _RecHub(focuser=_RecFoc(9935))
    hub.devices = {"camera": _Dev(), "telescope": _Dev(), "focuser": hub.focuser}
    arm = _arm(hub)
    monkeypatch.setattr(arm, "_window_open", lambda *a, **k: True)
    await arm.tick()
    assert "solve" in hub.calls, "ready devices must reach the ladder"


async def test_the_recovery_solve_keeps_the_mount_hint(fp):
    """The hint BOUNDS ASTAP's search to a 15-degree radius, which covers the
    ~4 degrees of error a sagged mount showed on 2026-08-02 while keeping the
    search tractable. Dropping it was tried that night and regressed: a true
    all-sky search failed outright on a sparse field that solved instantly once
    bounded. Generous bounds beat none."""
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    hub = _RecHub(focuser=_RecFoc(9935))
    arm = _arm(hub)
    await arm._recover(_light_session())
    assert getattr(hub, "blind_used", False) is False, (
        "an all-sky search is not more reliable than a bounded one")
