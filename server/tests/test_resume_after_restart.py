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


def _record_then_restart(fp, position: int = 9935) -> None:
    """What the rig recorded BEFORE the cut, and then the cut.

    The restart is load-bearing in every ladder test below. A number THIS
    process recorded is this boot's own reading of the device, so comparing the
    device against it says 'nothing changed' whatever happened while the power
    was off; only a record that predates the process is evidence."""
    fp.record(focuser_position=position, filter_slot=0, ra_hours=1.0,
              dec_deg=2.0, parked=False, tracking=True)
    fp.reset_for_tests()


def test_focus_is_untrusted_when_the_focuser_forgot_its_position(fp):
    """The EAF forgets its position on power loss, so a focuser reporting a
    different number than we last recorded is reporting a DEFAULT, not a
    measurement. That mismatch IS the power-loss tell -- and it catches a yanked
    USB hub too, which no OS shutdown event would.

    The restart in the middle is not decoration: what a process recorded itself
    is not evidence about a cut, so the record has to come from BEFORE this
    process started."""
    fp.record(focuser_position=9935, filter_slot=7, ra_hours=1.0,
              dec_deg=2.0, parked=True, tracking=False)
    fp.reset_for_tests()                    # the restart; the file survives it
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
    changes; without coalescing this rewrites the file continuously. Asserted
    against the FILE, which is the thing being spared."""
    now = [1000.0]
    monkeypatch.setattr(fp, "_now", lambda: now[0])
    fp.record(focuser_position=1, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    fp.record(focuser_position=2, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    assert fp.read_json_or(fp._path(), None)["focuser_position"] == 1  # coalesced
    now[0] += fp.FINGERPRINT_WRITE_INTERVAL_S + 1
    fp.record(focuser_position=2, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    assert fp.read_json_or(fp._path(), None)["focuser_position"] == 2


def test_coalescing_does_not_hide_a_drop(fp, monkeypatch):
    """The WRITE is coalesced; the watching is not.

    A focuser that vanished and came back inside one 10 s window would look
    continuous to anything sampling at the write interval, and its post-reset
    default would be adopted as the remembered number — the module would then
    vouch for the exact event it exists to catch."""
    now = [1000.0]
    monkeypatch.setattr(fp, "_now", lambda: now[0])
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    fp.reset_for_tests()
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    now[0] += 3.0                            # inside the window: never written
    fp.record(focuser_position=None, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    now[0] += fp.FINGERPRINT_WRITE_INTERVAL_S + 1
    fp.record(focuser_position=0, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)
    assert fp.verdict(focuser_position=0).focus_trusted is False


def test_recording_never_raises(fp, monkeypatch):
    """Bookkeeping must never break a run, whatever the disk is doing."""
    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(fp, "write_json_atomic", _boom)
    fp.record(focuser_position=1, filter_slot=0, ra_hours=0.0, dec_deg=0.0,
              parked=True, tracking=False)   # must not raise


async def test_the_boot_poll_does_not_destroy_the_power_cut_record(fp):
    """THE RECORD MUST OUTLIVE THE BOOT IT EXISTS TO DESCRIBE.

    The status poll runs every ~2 s and records the focuser's live position, so
    within seconds of connect the file holds the POST-restart reading. A reader
    consulting the file then is comparing this boot against itself: it answers
    'trusted' after a cut that moved nothing AND after one that moved
    everything. The comparison basis has to predate the boot, which is the same
    read-before-write shape zwo_usb._check_position_reference uses.

    Driven through the REAL recorder (hub.poll_status), not a stub, because the
    whole defect lives in WHEN that caller runs relative to the reader."""
    import astrodeck.hub as hubmod
    before_the_cut = 9935
    fp.record(focuser_position=before_the_cut, filter_slot=0, ra_hours=1.0,
              dec_deg=2.0, parked=False, tracking=True)
    fp.reset_for_tests()          # the cut: the file survives, the process does not
    h = hubmod.Hub()
    await h.connect_sim()
    try:
        out = await h.poll_status()
    finally:
        await h.disconnect_all()
    live = (out.get("focuser") or {}).get("position")
    assert live is not None and live != before_the_cut, (
        "the rig came back reading the pre-cut number, so this test proves "
        "nothing — pick a different pre-cut position")
    assert fp.verdict(focuser_position=live).focus_trusted is False, (
        "the boot's own poll overwrote the evidence it was meant to be "
        "compared against")
    assert fp.verdict(focuser_position=before_the_cut).focus_trusted is True, (
        "the pre-cut number must still be the basis — a blanket 'untrusted' "
        "would pass the assertion above while remembering nothing")
    raw = fp.read_json_or(fp._path(), None)
    assert raw["focuser_position"] == live, (
        "the file still records what the device reports; only the COMPARISON "
        "basis is frozen at boot")


def test_a_disconnected_focuser_does_not_blank_the_remembered_number(
        fp, monkeypatch):
    """A USB drop at 3am must not erase the only evidence of where the focuser
    was. Writing the None straight through would leave the next boot with
    nothing to compare against — the power cut would become undetectable
    because of an unrelated cable."""
    now = [1000.0]
    monkeypatch.setattr(fp, "_now", lambda: now[0])
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    now[0] += fp.FINGERPRINT_WRITE_INTERVAL_S + 1
    fp.record(focuser_position=None, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    raw = fp.read_json_or(fp._path(), None)
    assert raw["focuser_position"] == 9935


def test_a_focuser_that_drops_and_comes_back_changed_is_untrusted(
        fp, monkeypatch):
    """The in-process half of the same event, which the header promises: a
    browned-out powered hub resets the EAF while the PC stays up. Moves we
    WATCHED are legitimate — the focuser was never out of sight — but the first
    reading after it disappears has to match what it held or it is a default,
    not a measurement."""
    now = [1000.0]
    monkeypatch.setattr(fp, "_now", lambda: now[0])
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    fp.reset_for_tests()                    # boot with 9935 already on disk
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    assert fp.verdict(focuser_position=9935).focus_trusted is True
    now[0] += fp.FINGERPRINT_WRITE_INTERVAL_S + 1
    fp.record(focuser_position=10500, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)  # autofocus, watched the whole way
    assert fp.verdict(focuser_position=10500).focus_trusted is True
    now[0] += fp.FINGERPRINT_WRITE_INTERVAL_S + 1
    fp.record(focuser_position=None, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)  # the hub browns out
    now[0] += fp.FINGERPRINT_WRITE_INTERVAL_S + 1
    fp.record(focuser_position=0, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)  # back, and it forgot
    assert fp.verdict(focuser_position=0).focus_trusted is False
    assert fp.verdict(focuser_position=10500).focus_trusted is True, (
        "the carried number must survive the drop, not be replaced by the "
        "device's post-reset default")


def test_reset_for_tests_drops_the_boot_snapshot(fp):
    """Asserted rather than assumed: a boot snapshot that leaked into the next
    test in the worker would read as trust that test never established, and it
    would only show up as an order-dependent flake in the full suite."""
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    fp.reset_for_tests()
    assert fp._boot is None and fp._boot_loaded is False
    assert fp._boot_path is None
    assert fp._known is None and fp._last_pos is None
    assert fp._confirmed is False


def test_the_snapshot_does_not_follow_the_state_dir(fp, tmp_path, monkeypatch):
    """A snapshot describes ONE state directory. Left keyed to nothing, the
    first test in a worker would freeze a reference every later test inherited
    — the same shared-state flake _path() was uncached to kill, and it would
    show up only in the full suite."""
    now = [1000.0]
    monkeypatch.setattr(fp, "_now", lambda: now[0])
    _record_then_restart(fp)
    fp.record(focuser_position=9935, filter_slot=0, ra_hours=1.0, dec_deg=2.0,
              parked=False, tracking=True)
    assert fp.verdict(focuser_position=9935).focus_trusted is True
    # A different state dir, as the next test in the same worker would have.
    monkeypatch.setattr(fp, "_PATH", tmp_path / "elsewhere.json")
    assert fp.verdict(focuser_position=9935).focus_trusted is False, (
        "a directory with no record trusts nothing, whatever this process "
        "happens to remember about a different one")
    now[0] += fp.FINGERPRINT_WRITE_INTERVAL_S + 1
    fp.record(focuser_position=1234, filter_slot=0, ra_hours=1.0,
              dec_deg=2.0, parked=False, tracking=True)
    assert fp.read_json_or(fp._path(), None)["focuser_position"] == 1234
    assert fp._boot is None, "the new directory had no record to inherit"


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
    """The engine surface the recovery ladder actually uses.

    ``current_safety`` and ``check_slew_limits`` are part of it because the
    ladder MOVES THE MOUNT before ``start`` runs, so the gates that used to live
    only inside the run have to be reachable from out here. Permissive by
    default (safe verdict, limits pass) — the tests that care about a refusal
    override them per-case, and a stub that silently lacked them would let the
    ladder's gating rot without a single test noticing."""

    running = False

    def __init__(self):
        self.started: list = []
        self.limit_checks: list = []

    def start(self, plan, *, session=None):
        self.started.append(session)

    async def current_safety(self):
        from astrodeck.devices.base import SafetyReading
        return SafetyReading(is_safe=True, source="stub")

    async def check_slew_limits(self, target, *, cfg=None, plan=None,
                                projected=True):
        # ``plan`` is part of the surface because the pier-collision branch
        # reads plan.meridian_flip and this process has no run in flight.
        self.limit_checks.append((target, plan))


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
    _record_then_restart(fp)
    hub = _RecHub(focuser=_RecFoc(9935))
    arm = _arm(hub)
    assert await arm._recover(_light_session()) is None
    assert "solve" in hub.calls, "pointing must be re-measured even when nothing changed"


async def test_a_failed_solve_refuses_to_move(fp):
    """Too few stars under cloud. The alternative to refusing is slewing an OTA
    whose true position is unknown, toward a pier."""
    _record_then_restart(fp)
    hub = _RecHub(solve_raises=RuntimeError("Not enough stars"),
                  focuser=_RecFoc(9935))
    arm = _arm(hub)
    reason = await arm._recover(_light_session())
    assert reason is not None and "solve" in reason.lower()
    assert hub.centered == [], "must not slew on an unverified position"


async def test_untrusted_focus_runs_autofocus_and_never_restores_a_number(
        fp, monkeypatch):
    """Measure, do not guess: driving the focuser to a remembered position is a
    guess about a device that just reported it lost count.

    Forces `_can_autofocus` true because THIS test is about what the ladder does
    when it CAN autofocus. Since 2026-08-04 a rig with no autofocus provider
    warns and resumes instead — a deliberate split, covered by its own test in
    test_resume_arm.py — and without this the assertion silently changed meaning
    on any host without the Rust wheel, which is every CI runner in the `server`
    job.
    """
    _record_then_restart(fp)
    foc = _RecFoc(0)                       # forgot its position
    hub = _RecHub(focuser=foc)
    arm = _arm(hub)
    monkeypatch.setattr(arm, "_can_autofocus", lambda: True)
    ran = []
    arm._autofocus = lambda: (ran.append(1), None)[1] or _noop()
    await arm._recover(_light_session())
    assert ran == [1], "untrusted focus must be re-measured"
    assert foc.moves == [], "must never drive the focuser to a remembered number"


async def _noop():
    return None


async def test_autofocus_is_not_repeated_on_every_retry(fp, monkeypatch):
    """The ladder autofocuses, then the solve fails under cloud and the tick
    refuses. Ten minutes later it ticks again — and the focuser has now been
    MEASURED, so autofocus must not run a second time.

    Nothing inside the fingerprint can re-establish trust once a gap has opened,
    which is correct (a number the device reports after a power cut is a
    default, not a measurement). An autofocus IS the measurement, so the ladder
    says so. Without that, every retry spends minutes of mount time and focuser
    travel re-solving a problem it already solved, under exactly the conditions
    that caused the retry."""
    _record_then_restart(fp)
    foc = _RecFoc(0)                       # forgot its position across the cut
    hub = _RecHub(solve_raises=RuntimeError("Not enough stars"), focuser=foc)
    arm = _arm(hub)
    monkeypatch.setattr(arm, "_can_autofocus", lambda: True)
    ran = []
    arm._autofocus = lambda: (ran.append(1), None)[1] or _noop()

    first = await arm._recover(_light_session())
    assert first is not None and "solve" in first.lower()
    assert ran == [1], "the first tick must measure the focus it cannot trust"

    second = await arm._recover(_light_session())
    assert second is not None and "solve" in second.lower()
    assert ran == [1], "the focuser was measured on the first tick — not again"


async def test_trusted_focus_skips_autofocus(fp):
    """A clean reboot preserved focus exactly; forcing an autofocus would spend
    ten minutes of dark sky fixing a problem that did not happen."""
    _record_then_restart(fp)
    hub = _RecHub(focuser=_RecFoc(9935))
    arm = _arm(hub)
    ran = []
    arm._autofocus = lambda: (ran.append(1), None)[1] or _noop()
    await arm._recover(_light_session())
    assert ran == []


async def test_a_calibration_only_session_never_centers(fp):
    """Darks never slew. The solve still runs and is harmless."""
    _record_then_restart(fp)
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
    _record_then_restart(fp)
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
    _record_then_restart(fp)
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
    _record_then_restart(fp)
    hub = _RecHub(focuser=_RecFoc(9935))
    arm = _arm(hub)
    await arm._recover(_light_session())
    assert getattr(hub, "blind_used", False) is False, (
        "an all-sky search is not more reliable than a bounded one")
