"""Task 8: ResumeArm auto-resume service (sessions spec §5) — injected clock
(monkeypatched time source / _window_open, schedule-test precedent), real sim
hub + engine (no FakeHub)."""
import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.devices.base import DeviceError
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.resume_arm import RETRY_INTERVAL_S, ResumeArm
from astrodeck.sequence.session import Session, session_store
from _site_tracking import numeric_tokens


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    # A CONFIGURED SITE (issue #121). These tests ask the sky questions - three
    # of them pick a clock time at which a target sits at a chosen altitude and
    # then assert what the resume ladder does about it. They were asking those
    # questions of the DEFAULT site, 0N 0E, because nothing here had ever set
    # one; it worked only because `_frame_altitude` answered from 0,0 as
    # confidently as from anywhere else.
    #
    # It no longer does, and that is the fix: an unset site is "nobody can say",
    # which is what both of its callers always documented. So a floor refusal
    # computed against a default site is not a refusal at all, and two of those
    # three cases would have gone on passing for the wrong reason.
    #
    # 40N 74W is an arbitrary real place. Patched as a property so monkeypatch
    # puts it back, rather than writing through the shared config store.
    real = dict(h.site, name="Test", latitude=40.0, longitude=-74.0,
                is_default=False)
    monkeypatch.setattr(type(h), "site", property(lambda self: real))
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return False


def _plan(count=6) -> SequencePlan:
    return SequencePlan(name="arm", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=count)])])


async def _dormant_armed(sim_hub, engine) -> str:
    """Real dormant session: start, get a frame in, abort, arm."""
    engine.start(_plan())
    sid = engine._session.id
    assert await wait_for(lambda: engine._frames_done >= 1)
    await engine.abort()
    s = session_store.load(sid)
    assert s.status == "dormant"
    s.auto_resume = True
    session_store.save(s)
    return sid


async def test_tick_noop_when_window_closed(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: False)
    await arm.tick()
    assert not engine.running


async def test_tick_resumes_when_window_open(sim_hub, monkeypatch, bus_lines):
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    # NOT `assert engine.running`. `running` is
    # `_task is not None and not _task.done()`, which conflates "a run was
    # started" with "the run has not finished yet" — and the sim plan here
    # completes almost instantly. On a fast CI runner under xdist the task was
    # already done by the time this line executed, so `running` read False while
    # the very next assertion (state == "complete") passed. The test was
    # asserting a transient the machine could blow straight through.
    #
    # `_retry_at == 0.0` is the deterministic signal for the property actually
    # under test: tick() sets it to 0 only on the success path, and to
    # now + RETRY_INTERVAL_S on every refusal. It distinguishes "resumed" from
    # "refused and backed off" without depending on how fast the run is.
    assert arm._retry_at == 0.0, (
        "the tick backed off instead of resuming. Every bus line it emitted:\n"
        + "\n".join(f"  [{lv}] {msg}" for lv, msg, _src in bus_lines))
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert session_store.load(sid).status == "complete"


async def test_refusal_retries_after_10_minutes(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    calls = {"n": 0}

    def refuse(role):
        # Count CAMERA acquisitions only. The counter stands for "how many times
        # did the service attempt a resume", and since 2026-08-02 a tick also
        # asks for the focuser (the post-restart recovery ladder reads its
        # position to decide whether focus survived). Counting every require
        # would make this assert on the ladder's internals rather than on the
        # backoff it exists to test.
        if role == "camera":
            calls["n"] += 1
        raise DeviceError(f"no {role}")
    monkeypatch.setattr(sim_hub, "require", refuse)
    await arm.tick()                                  # refusal
    assert calls["n"] == 1 and not engine.running
    assert arm._retry_at == now["t"] + RETRY_INTERVAL_S
    now["t"] += 300
    await arm.tick()                                  # still backing off
    assert calls["n"] == 1
    now["t"] += 301                                   # past the 10-min mark
    await arm.tick()
    assert calls["n"] == 2


async def test_give_up_when_window_closes_mid_retry(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    window = {"open": True}
    monkeypatch.setattr(ResumeArm, "_window_open",
                        lambda self, s, t: window["open"])
    monkeypatch.setattr(sim_hub, "require",
                        lambda role: (_ for _ in ()).throw(DeviceError("no cam")))
    await arm.tick()                                  # refusal -> backoff armed
    window["open"] = False                            # dawn passed
    now["t"] += RETRY_INTERVAL_S + 1
    await arm.tick()
    assert arm._gave_up_for == sid                    # one give-up, no attempt
    assert not engine.running


async def test_disarm_and_running_engine_stop_interest(sim_hub, monkeypatch):
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    s = session_store.load(sid)
    s.auto_resume = False                             # UI disarm
    session_store.save(s)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    assert not engine.running                         # nothing armed -> no-op


async def test_quota_unbounded_refused(sim_hub, monkeypatch):
    """HARD REQUIREMENT (Task 4 review carry-in): ResumeArm is a THIRD
    engine.start path and engine.start is deliberately unguarded, so ResumeArm
    MUST call quota_unbounded() itself and refuse (alert + backoff, stay
    dormant) an accepted-quota plan that could run forever under persistent
    rejects. The route gates never see this path."""
    engine = SequenceEngine(sim_hub)
    plan = _plan()
    plan.count_mode = "accepted"          # unbounded: accepted mode ...
    plan.max_consecutive_rejects = 0      # ... both reject guards off ...
    plan.max_consecutive_rejects_night = 0
    # ... and a non-calibration target with no stop boundary (default schedule).
    s = Session(name="unbounded", status="dormant", plan=plan, auto_resume=True)
    session_store.save(s)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    # require would succeed (real camera) — the refusal must come from the guard.
    await arm.tick()
    assert not engine.running                         # refused before start
    assert arm._retry_at == now["t"] + RETRY_INTERVAL_S
    assert session_store.load(s.id).status == "dormant"


# =================================================== weather veto (spec §4/§14)
# ResumeArm stays thin: veto logic lives in WeatherService; these tests inject
# a fake with a fixed veto_reason. The stale/disabled/ignore-tonight variants
# all collapse to veto_reason() -> None inside the real service and are
# covered service-level in tests/test_weather.py.


class _FakeWeather:
    def __init__(self, reason):
        self._reason = reason

    def veto_reason(self, now):
        return self._reason


async def test_weather_veto_blocks_resume_and_arms_retry(sim_hub, monkeypatch,
                                                         bus_lines):
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"],
                    weather=_FakeWeather(
                        "cloud cover 80% forecast within the next hour "
                        "(threshold 50%)"))
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    assert not engine.running                      # vetoed BEFORE any device touch
    assert arm._retry_at == now["t"] + RETRY_INTERVAL_S   # 10-min retry latch
    # bus_lines, NOT bus.log_history: the ring is a deque(maxlen=200) shared by
    # the whole process, so this scan goes red whenever an unrelated suite has
    # already filled it and the awaited line ages out before the assertion.
    assert any("auto-resume vetoed: cloud cover 80%" in m
               for _lvl, m, _src in bus_lines), "veto warning must be logged"


async def test_weather_veto_none_resumes(sim_hub, monkeypatch, bus_lines):
    """veto_reason None (the real service's stale/disabled/ignored outcomes)
    -> the run starts. Constructor default weather=None (no service injected,
    back-compat) is covered by the existing resume tests above."""
    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"],
                    weather=_FakeWeather(None))
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    # NOT `assert engine.running`. `running` is
    # `_task is not None and not _task.done()`, which conflates "a run was
    # started" with "the run has not finished yet" — and the sim plan here
    # completes almost instantly. On a fast CI runner under xdist the task was
    # already done by the time this line executed, so `running` read False while
    # the very next assertion (state == "complete") passed. The test was
    # asserting a transient the machine could blow straight through.
    #
    # `_retry_at == 0.0` is the deterministic signal for the property actually
    # under test: tick() sets it to 0 only on the success path, and to
    # now + RETRY_INTERVAL_S on every refusal. It distinguishes "resumed" from
    # "refused and backed off" without depending on how fast the run is.
    assert arm._retry_at == 0.0, (
        "the tick backed off instead of resuming. Every bus line it emitted:\n"
        + "\n".join(f"  [{lv}] {msg}" for lv, msg, _src in bus_lines))
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert session_store.load(sid).status == "complete"


# ------------------------------------------------------- the ladder moves FIRST
#
# This module's own header used to say every safety gate "runs inside
# engine.start / the run itself". That was true when the resume path WAS
# engine.start. Then the recovery ladder was added ahead of it, and the ladder
# plate-solves and RE-CENTERS — real motion, unattended, before a single gate the
# claim named has run. These two pin the gates onto the ladder.

def _force_unsafe(hub, reason="rain"):
    from astrodeck.devices.base import SafetyReading
    mon = hub.devices.get("safety")
    if mon is not None:
        mon.force_unsafe(reason)
    hub._safety_reading = SafetyReading(is_safe=False, reason=reason,
                                        source="Sim Safety Monitor")


def _record_motion(hub, monkeypatch) -> list[str]:
    """Record the ladder's motion calls instead of raising from them.

    A stub that raises passes this test for the WRONG reason: the ladder wraps
    both calls in ``except Exception`` and turns any failure into the same
    ten-minute hold, so "it refused" is indistinguishable from "it moved and the
    move blew up". Recording separates the two."""
    calls: list[str] = []

    def _stub(label, result=None):
        async def _f(*a, **kw):
            calls.append(label)
            return result
        return _f

    monkeypatch.setattr(hub, "goto_and_center", _stub("goto", {}))
    monkeypatch.setattr(hub, "solve_and_sync", _stub("solve", {}))
    return calls


async def test_recovery_refuses_to_move_while_the_monitor_says_unsafe(
        sim_hub, monkeypatch, bus_lines):
    """Rain, and the rig has just rebooted. The ladder must not slew."""
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    _force_unsafe(sim_hub)
    moved = _record_motion(sim_hub, monkeypatch)

    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()

    assert arm._retry_at == now["t"] + RETRY_INTERVAL_S, (
        "an unsafe monitor must hold the resume and arm the backoff. Bus lines:\n"
        + "\n".join(f"  [{lv}] {msg}" for lv, msg, _src in bus_lines))
    assert moved == [], f"the rig moved while the monitor said unsafe: {moved}"
    assert not engine.running
    assert any("rain" in m for _lv, m, _s in bus_lines), \
        f"the hold must name what the monitor reported: {bus_lines}"


async def test_recovery_refuses_a_recenter_that_breaks_the_altitude_limits(
        sim_hub, monkeypatch, bus_lines):
    """The re-centering slew is a slew. It gets the same floor and zenith
    keep-out every in-run slew gets — the ladder ran ahead of the only place
    those were enforced."""
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    # a floor above anything the plan's target can reach.
    safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(safety, "enabled", True)
    monkeypatch.setattr(safety, "min_alt_deg", 89.0)
    moved = _record_motion(sim_hub, monkeypatch)

    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()

    assert arm._retry_at == now["t"] + RETRY_INTERVAL_S, (
        "a re-center outside the altitude limits must hold the resume. Lines:\n"
        + "\n".join(f"  [{lv}] {msg}" for lv, msg, _src in bus_lines))
    assert "goto" not in moved, \
        "the ladder slewed to a target below the altitude floor"
    assert not engine.running
    # and for the RIGHT reason — the limits gate, not some other refusal that
    # happened to land first. The gate's own sentence names the altitude, the
    # floor and the azimuth it judged, which are the site re-encoded (#140),
    # so it is the hold's ``site_detail`` and nowhere a viewer reads: the
    # reason is words, and the held warning's one number is the retry
    # interval (#233).
    #
    # RED under mutant "limits reason keeps the gate sentence" (the limits
    # branch returns ``f"re-centering after restart refused: {e}"`` again),
    # observed verbatim (the gate reads the real clock, so its numbers vary
    # from run to run):
    #
    #     E   AssertionError: re-centering after restart refused: target M42 altitude 22° below safety floor 89° (az 119°)
    #     E   assert 'configured slew limits' in 're-centering after restart refused: target M42 altitude 22° below safety floor 89° (az 119°)'
    #
    # under "the held warning logs site_detail", observed verbatim:
    #
    #     E   AssertionError: the held warning carries more than the retry interval: "auto-resume held: re-centering after restart refused: the target is outside this rig's configured slew limits (altitude floor, horizon, no-go wedges, pier side or zenith keep-out); not slewing yet (target M42 altitude 22° below safety floor 89° (az 118°)) — retrying in 10 min"
    #
    # and under "the tick drops the detail" (``tick``'s ``_set_hold``
    # passes ``site_detail=None``), observed verbatim:
    #
    #     E   AssertionError: expected the altitude-limit refusal on site_detail, got: {'reason': "re-centering after restart refused: the target is outside this rig's configured slew limits (altitude floor, horizon, no-go wedges, pier side or zenith keep-out); not slewing yet", 'since': 1700000000.0, 'retry_at': 1700000600.0, 'session_id': '1dee71803e514926b445da0cee3e670f', 'session_name': 'arm', 'owed': 5}
    #     E   assert 'below safety floor' in ''
    #
    # THE GATE'S MESSAGE IS WORDS SINCE #233 (H3 T11), and its altitude,
    # floor and azimuth ride ``SlewRefused.site_detail``. ``_recover`` files
    # ``str(e)``, so until it reads that attribute the operator's
    # ``site_detail`` carries the gate's words, not its numbers. The pin
    # below is on words the message still has. The three mutants were run
    # again on a scratch copy of the tree (resume_arm.py is not T11's file),
    # each observed verbatim:
    #
    # "limits reason keeps the gate sentence", RED:
    #
    #     E   AssertionError: re-centering after restart refused: slew refused: M42 would be below the mount's altitude floor by the end of a slew there
    #     E   assert 'configured slew limits' in "re-centering after restart refused: slew refused: M42 would be below the mount's altitude floor by the end of a slew there"
    #
    # "the held warning logs site_detail": GREEN here now (1 passed). What it
    # would log is the gate's words, which have no number to catch. It goes
    # RED again once ``_recover`` files ``SlewRefused.site_detail``; the
    # floor refusal's form of the same mutant is still graded by
    # test_resume_arm_hold_is_site_free.py.
    #
    # "the tick drops the detail", RED:
    #
    #     E   AssertionError: expected the altitude-limit refusal on site_detail, got: {'reason': "re-centering after restart refused: the target is outside this rig's configured slew limits (altitude floor, horizon, no-go wedges, pier side or zenith keep-out); not slewing yet", 'since': 1700000000.0, 'retry_at': 1700000600.0, 'session_id': '41cb2d4ff4c1446493d8cd75b3319d03', 'session_name': 'arm', 'owed': 5}
    #     E   assert 'altitude floor' in ''
    #
    # AND NOW IT DOES (H3 integration, the T11 verifier's required follow-up):
    # the limits branch files ``SlewRefused.site_detail``, so the pin is back
    # on the gate's NUMERIC sentence ("target M42 altitude N° below safety
    # floor N° (az N°)"), which the words do not contain. Mutants run on a
    # scratch copy of the tree, each restored byte-identical:
    #
    # "the limits branch files the words" (``_refusal_site_detail = str(e)``,
    # the line before this change), RED, observed verbatim:
    #
    #     E       AssertionError: expected the altitude-limit refusal on site_detail, got: {'reason': "re-centering after restart refused: the target is outside this rig's configured slew limits (altitude floor, horizon, no-go wedges, pier side or zenith keep-out); not slewing yet", 'since': 1700000000.0, 'retry_at': 1700000600.0, 'session_id': '05db3df6afbf4fac8b9049dd2a78a249', 'session_name': 'arm', 'owed': 5, 'site_detail': "slew refused: M42 would be below the mount's altitude floor by the end of a slew there"}
    #     E       assert 'below safety floor' in "slew refused: M42 would be below the mount's altitude floor by the end of a slew there"
    #
    # "the held warning logs site_detail", RED again, observed verbatim:
    #
    #     E       AssertionError: the held warning carries more than the retry interval: "auto-resume held: re-centering after restart refused: the target is outside this rig's configured slew limits (altitude floor, horizon, no-go wedges, pier side or zenith keep-out); not slewing yet (target M42 altitude 35° below safety floor 89° (az 223°)) — retrying in 10 min"
    #     E       assert Counter({'35'...: 1, '10': 1}) == Counter({'10': 1})
    #
    # "the tick drops the detail", RED, observed verbatim:
    #
    #     E       AssertionError: expected the altitude-limit refusal on site_detail, got: {'reason': "re-centering after restart refused: the target is outside this rig's configured slew limits (altitude floor, horizon, no-go wedges, pier side or zenith keep-out); not slewing yet", 'since': 1700000000.0, 'retry_at': 1700000600.0, 'session_id': 'c4620d50d68c4cd48cf62c92ce44c767', 'session_name': 'arm', 'owed': 5}
    #     E       assert 'below safety floor' in ''
    assert arm.hold is not None, "a refused re-centre must leave a hold"
    assert "below safety floor" in arm.hold.get("site_detail", ""), (
        f"expected the altitude-limit refusal on site_detail, got: {arm.hold}")
    reason = arm.hold["reason"]
    assert "configured slew limits" in reason, reason
    assert numeric_tokens(reason) == numeric_tokens(""), (
        f"the limits reason carries the gate's numbers: {reason!r}")
    held = [m for lv, m, _s in bus_lines
            if lv == "warning" and m.startswith("auto-resume held:")]
    assert len(held) == 1, bus_lines
    assert numeric_tokens(held[0]) == numeric_tokens(
        f"retrying in {int(RETRY_INTERVAL_S / 60)} min"), (
            f"the held warning carries more than the retry interval: "
            f"{held[0]!r}")
    assert not any("below safety floor" in m for _lv, m, _s in bus_lines), (
        f"the gate's sentence reached the log: {bus_lines}")


async def test_no_autofocus_provider_warns_and_resumes_anyway(sim_hub, monkeypatch,
                                                              bus_lines):
    """A rig with NO autofocus provider must still auto-resume.

    Found by CI, not locally, and it was a product hole rather than a test
    artefact. The recovery ladder gave the SOLVER step a
    configuration-versus-conditions split — "no solver configured" warns and
    proceeds, "the solver failed" refuses — and never gave the focus step the
    same. So on a host with no native engine (no Rust wheel, no NINA) a restart
    that cost the focuser its position refused, armed the ten-minute backoff, and
    did it again forever. The feature was silently deleted for that class of rig,
    with the real reason only in a bus line nobody reads.

    This reproduces that host by turning NATIVE_AVAILABLE off, which is exactly
    what the CI `server` job is: only the `native` job builds the wheel.
    """
    import astrodeck.providers as _providers
    from astrodeck.devices import fingerprint as _fp
    monkeypatch.setattr(_providers, "NATIVE_AVAILABLE", False)
    # Force the "focuser forgot where it was" branch — the one that reaches
    # autofocus at all.
    monkeypatch.setattr(_fp, "verdict",
                        lambda **kw: _fp.Verdict(focus_trusted=False))

    engine = SequenceEngine(sim_hub)
    sid = await _dormant_armed(sim_hub, engine)
    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()

    assert arm._retry_at == 0.0, (
        "a rig that cannot autofocus must still resume. Bus lines:\n"
        + "\n".join(f"  [{lv}] {msg}" for lv, msg, _src in bus_lines))
    # ...and it must SAY so, because the frames may be soft and only the user
    # can judge that.
    warned = [m for lv, m, _ in bus_lines
              if lv == "warning" and "autofocus provider is configured" in m]
    assert warned, f"the resume must warn about unverified focus: {bus_lines}"
    assert "check focus" in warned[0]
    assert await wait_for(lambda: engine.state.get("state") == "complete")
    assert session_store.load(sid).status == "complete"


async def test_the_recenter_gate_gets_the_plan_not_just_the_config(sim_hub,
                                                                   monkeypatch):
    """The pier-collision branch reads ``plan.meridian_flip``.

    In a fresh post-reboot process the engine's own ``plan`` is still None — no
    run has started — so passing only ``cfg`` left the pier half of the gate
    inert while the altitude half ran. A partial guard that looks like a whole
    one is worse than an absent one, because the log says the limits were
    checked. The ladder passes the armed session's plan, which is the same
    object engine.start receives, so both gates read one setting."""
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    _record_motion(sim_hub, monkeypatch)

    seen: list = []

    async def spy(target, *, cfg=None, plan=None, projected=True):
        seen.append({"cfg": cfg, "plan": plan})
    monkeypatch.setattr(engine, "check_slew_limits", spy)

    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()

    assert seen, "the re-centering slew was not gated at all"
    assert seen[0]["cfg"] is not None, "cfg must be passed — engine._cfg is None here"
    assert seen[0]["plan"] is not None, (
        "plan must be passed, or the pier-collision branch is silently inert")
    assert engine.plan is None or seen[0]["plan"] is not None


# --------------------------------------------------------------- the target's
# own start floor (rig, 2026-09-06)

#: NGC 604 in M33 - the target the rig actually tried to re-centre on that
#: night, when it was nine degrees up and its own plan would not have started it
#: for another two hours.
NGC604_RA, NGC604_DEC = 1.5720, 30.7853


def _plan_with_floor(floor: float, ra: float = NGC604_RA,
                     dec: float = NGC604_DEC) -> SequencePlan:
    from astrodeck.sequence.models import Schedule
    return SequencePlan(
        name="floor", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name="NGC 604", ra_hours=ra, dec_deg=dec, center=False,
                        autofocus_first=False,
                        schedule=Schedule(min_altitude_deg=floor),
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            count=1)])])


def _when_altitude_between(site, ra: float, dec: float, lo: float,
                           hi: float, t0: float = 1_700_000_000.0) -> float:
    """A clock reading at which the target really is between ``lo`` and ``hi``
    degrees for THIS rig's configured site.

    Searched rather than hardcoded: the site comes from the config store, so a
    baked-in timestamp would silently stop meaning "nine degrees up" the day
    somebody changed the default site, and the test would go on passing against
    an altitude nobody chose.
    """
    from astrodeck.catalog import altaz
    for i in range(24 * 60):
        t = t0 + i * 60.0
        alt, _ = altaz(ra, dec, site["latitude"], site["longitude"], t)
        if lo <= alt <= hi:
            return t
    raise AssertionError(
        f"no time in the next day puts {ra}h {dec}deg between {lo} and {hi} "
        f"deg from {site}")


def _trust_focus(monkeypatch) -> None:
    """Keep the focus step out of the way: these tests are about step 3."""
    from astrodeck.devices import fingerprint as _fp
    monkeypatch.setattr(_fp, "verdict",
                        lambda **kw: _fp.Verdict(focus_trusted=True))


def _spy_slew_limits(engine, monkeypatch) -> list:
    """Stand in for the MOUNT floor gate so these tests see only the TARGET's
    own floor. That gate passed on the rig - which is the whole point."""
    seen: list = []

    async def spy(target, *, cfg=None, plan=None, projected=True):
        seen.append(target)
    monkeypatch.setattr(engine, "check_slew_limits", spy)
    return seen


async def test_recover_will_not_slew_to_a_target_below_its_own_start_floor(
        sim_hub, monkeypatch):
    """2026-09-06. The armed session auto-resumed, the blind solve
    worked, the MOUNT floor gate passed - and the ladder then asked the AM5 to
    slew to a target at nine degrees that its own plan would not have started
    for two hours (``min_altitude_deg`` 30). The mount refused with ``e6`` and
    the resume held, retrying every ten minutes until the target rose.

    Nothing was wrong with the retry. What was wrong is that the plan already
    knew the answer and nobody asked it.

    RED under mutant "floor reason keeps the altitude" (the words-only reason
    becomes ``f"{tgt.name} is at {alt:.0f} deg, below its start floor; not
    slewing yet"``), observed verbatim:

        E   AssertionError: NGC 604 is at 10 deg, below its start floor; not slewing yet
        E   assert 'NGC 604 is below its start floor' in 'NGC 604 is at 10 deg, below its start floor; not slewing yet'

    RED under mutant "the detail is dropped" (the floor branch no longer
    writes ``_refusal_site_detail``), observed verbatim:

        E   AssertionError: the floor refusal kept no site detail
        E   assert None is not None
    """
    _trust_focus(monkeypatch)
    engine = SequenceEngine(sim_hub)
    moved = _record_motion(sim_hub, monkeypatch)
    gated = _spy_slew_limits(engine, monkeypatch)

    t = _when_altitude_between(sim_hub.site, NGC604_RA, NGC604_DEC, 8.0, 10.0)
    arm = ResumeArm(engine, sim_hub, clock=lambda: t)
    refusal = await arm._recover(Session(name="floor",
                                         plan=_plan_with_floor(30.0)))

    assert refusal is not None, "the ladder slewed to a target below its floor"
    # The reason is WORDS (#233): it is the hold a viewer reads and the
    # warning a viewer reads in /api/logs, and the altitude of a known target
    # at a known time is the site (#140). Its one number is the target's
    # catalogue number, which no site moves.
    assert "NGC 604 is below its start floor" in refusal, refusal
    assert numeric_tokens(refusal) == numeric_tokens("NGC 604"), (
        f"the floor reason carries a number: {refusal!r}")
    # The numbers move to ``site_detail``, for the operator...
    detail = arm._refusal_site_detail
    assert detail is not None, "the floor refusal kept no site detail"
    assert "below its 30 deg start floor" in detail, detail
    assert "NGC 604" in detail, detail
    # ...and say how long the wait is, from the scheduler's own gate-crossing
    # search. "Not yet" with no number is the hold that gets diagnosed at 2am.
    assert "reaches 30 deg in about" in detail, detail
    assert "goto" not in moved, f"the mount was moved anyway: {moved}"
    assert gated == [], (
        "the target's own floor must be read BEFORE the mount gate - that gate "
        "passed on the rig, which is how the slew got out")


async def test_a_floor_hold_keeps_its_numbers_off_the_reason_and_the_log(
        sim_hub, monkeypatch, bus_lines):
    """The same refusal through a whole tick (#233): the hold's ``reason``
    and the "auto-resume held" warning are words, and the altitude, the
    floor and the ETA are the hold's ``site_detail`` alone. The route
    withholds that key from a viewer (test_resume_arm_hold_is_site_free.py);
    this pins what the service puts where.

    RED under mutant "the held warning logs site_detail" (the warning
    formats ``self._refusal_site_detail`` after the reason), observed
    verbatim:

        E   AssertionError: the held warning carries more than the retry interval: 'auto-resume held: NGC 604 is below its start floor; not slewing yet (NGC 604 is at 10 deg, below its 30 deg start floor (it reaches 30 deg in about 12.2 h)) — retrying in 10 min'

    (the altitude there is 10, the same token as the retry interval's 10;
    the comparison counts tokens, so it saw two where one belongs.)

    RED under mutant "floor reason keeps the altitude", observed verbatim:

        E   AssertionError: NGC 604 is at 10 deg, below its start floor; not slewing yet
        E   assert 'NGC 604 is a...t slewing yet' == 'NGC 604 is b...t slewing yet'

    RED under mutant "the tick drops the detail" (``tick``'s ``_set_hold``
    passes ``site_detail=None``), and under "the detail is dropped",
    observed verbatim:

        E   AssertionError: the hold kept no site detail: {'reason': 'NGC 604 is below its start floor; not slewing yet', 'since': 1700041700.0, 'retry_at': 1700042300.0, 'session_id': 'a584592aef854da685b49b416e0e862b', 'session_name': 'floor', 'owed': 1}
        E   assert None is not None
    """
    _trust_focus(monkeypatch)
    engine = SequenceEngine(sim_hub)
    moved = _record_motion(sim_hub, monkeypatch)
    _spy_slew_limits(engine, monkeypatch)
    s = Session(name="floor", status="dormant", plan=_plan_with_floor(30.0),
                auto_resume=True)
    session_store.save(s)
    t = _when_altitude_between(sim_hub.site, NGC604_RA, NGC604_DEC, 8.0, 10.0)
    arm = ResumeArm(engine, sim_hub, clock=lambda: t)
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()

    assert "goto" not in moved, f"the mount was moved anyway: {moved}"
    assert arm.hold is not None, "a floor refusal must leave a hold"
    reason = arm.hold["reason"]
    assert reason == "NGC 604 is below its start floor; not slewing yet", reason
    assert numeric_tokens(reason) == numeric_tokens("NGC 604"), (
        f"the floor reason carries a number: {reason!r}")
    detail = arm.hold.get("site_detail")
    assert detail is not None, f"the hold kept no site detail: {arm.hold}"
    assert "below its 30 deg start floor" in detail, detail
    assert "reaches 30 deg in about" in detail, detail
    held = [m for lv, m, _s in bus_lines
            if lv == "warning" and m.startswith("auto-resume held:")]
    assert len(held) == 1, bus_lines
    assert numeric_tokens(held[0]) == numeric_tokens(
        f"NGC 604 retrying in {int(RETRY_INTERVAL_S / 60)} min"), (
            f"the held warning carries more than the retry interval: "
            f"{held[0]!r}")
    assert not any("deg" in m for _lv, m, _s in bus_lines), (
        f"a line carries degrees: {bus_lines}")


async def test_a_floor_hold_keeps_its_since_while_the_altitude_changes(
        sim_hub, monkeypatch):
    """Two floor refusals, the second past the ten-minute backoff, with the
    target a couple of degrees higher: the words are the same, so the hold
    is the same hold and ``since`` is the first refusal's; ``site_detail``
    carries the new numbers. Before #233 the altitude was in the reason, so
    every retry restamped ``since`` and the Monitor could never say how long
    the session had been waiting on its floor.

    RED under mutant "since keys on site_detail" (``_set_hold``'s ``same``
    also asks ``prior.get("site_detail") == site_detail``), observed
    verbatim:

        E   AssertionError: the hold restarted its clock on a retry: 1700042360.0 != 1700041700.0
        E   assert 1700042360.0 == 1700041700.0

    RED under mutant "floor reason keeps the altitude", one check earlier:
    the words themselves moved with the altitude, observed verbatim:

        E   AssertionError: ({'owed': 1, 'reason': 'NGC 604 is at 10 deg, below its start floor; not slewing yet', 'retry_at': 1700042300.0, 'sess...ow its start floor; not slewing yet', 'retry_at': 1700042960.0, 'session_id': '7eb8f182006a4c0b91c5314b28018d26', ...})
        E   assert 'NGC 604 is a...t slewing yet' == 'NGC 604 is a...t slewing yet'
        E     - NGC 604 is at 10 deg, below its start floor; not slewing yet
        E     + NGC 604 is at 8 deg, below its start floor; not slewing yet
    """
    _trust_focus(monkeypatch)
    engine = SequenceEngine(sim_hub)
    _record_motion(sim_hub, monkeypatch)
    _spy_slew_limits(engine, monkeypatch)
    session_store.save(Session(name="floor", status="dormant",
                               plan=_plan_with_floor(30.0), auto_resume=True))
    t0 = _when_altitude_between(sim_hub.site, NGC604_RA, NGC604_DEC, 8.0, 10.0)
    now = {"t": t0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    first = dict(arm.hold)
    now["t"] = t0 + RETRY_INTERVAL_S + 60.0
    await arm.tick()
    second = dict(arm.hold)

    assert first["site_detail"] != second["site_detail"], (
        f"premise: the target's numbers moved between the two refusals: "
        f"{first['site_detail']!r}")
    assert second["reason"] == first["reason"], (first, second)
    assert second["since"] == first["since"], (
        f"the hold restarted its clock on a retry: {second['since']} != "
        f"{first['since']}")
    assert first["since"] == t0
    assert second["retry_at"] == now["t"] + RETRY_INTERVAL_S


async def test_a_later_refusal_does_not_carry_an_earlier_floor_detail(
        sim_hub, monkeypatch):
    """A floor refusal, then, past the backoff and with the target risen
    above its floor, a refusal of another kind (the re-centring goto fails):
    the second hold is the goto's, and it has no ``site_detail``, because
    nothing site-derived is behind it (#233). ``tick`` clears the detail
    before each ladder. Without that, the first refusal's altitude, floor and
    ETA ride the second hold beside a reason they do not explain, and an
    operator reads an altitude from an earlier retry, below a floor the
    target has since cleared, as the reason nothing started.

    RED under mutant "the stale detail rides the next refusal" (``tick`` no
    longer sets ``self._refusal_site_detail = None`` before the ladder),
    observed verbatim:

        E   AssertionError: an earlier refusal's site detail rode a later hold: {'reason': 're-centering after restart failed: the mount did not answer', 'since': 1700088320.0, 'retry_at': 1700088920.0, 'session_id': '6ce7f1dbef74489a9eedda6e8c50492d', 'session_name': 'floor', 'owed': 1, 'site_detail': 'NGC 604 is at 10 deg, below its 30 deg start floor (it reaches 30 deg in about 12.2 h)'}
        E   assert 'site_detail' not in {'owed': 1, 'reason': 're-centering after restart failed: the mount did not answer', 'retry_at': 1700088920.0, 'session_id': '6ce7f1dbef74489a9eedda6e8c50492d', ...}

    (Mutant run by the H3 T1 verifier from a byte backup of resume_arm.py in
    a scratch copy of ``server/``; the file was restored byte-identical,
    SHA-256 compared.)
    """
    _trust_focus(monkeypatch)
    engine = SequenceEngine(sim_hub)
    moved = _record_motion(sim_hub, monkeypatch)
    _spy_slew_limits(engine, monkeypatch)
    session_store.save(Session(name="floor", status="dormant",
                               plan=_plan_with_floor(30.0), auto_resume=True))
    t0 = _when_altitude_between(sim_hub.site, NGC604_RA, NGC604_DEC, 8.0, 10.0)
    t1 = _when_altitude_between(sim_hub.site, NGC604_RA, NGC604_DEC, 40.0,
                                60.0, t0=t0 + RETRY_INTERVAL_S + 60.0)
    now = {"t": t0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()
    assert "site_detail" in (arm.hold or {}), (
        f"premise: the first tick held on the floor, with a detail: "
        f"{arm.hold}")

    async def goto_fails(*a, **kw):
        moved.append("goto")
        raise RuntimeError("the mount did not answer")
    monkeypatch.setattr(sim_hub, "goto_and_center", goto_fails)
    now["t"] = t1
    await arm.tick()

    assert moved.count("goto") == 1, (
        f"premise: the second ladder cleared the floor and reached its "
        f"goto: {moved}")
    assert arm.hold is not None, "a failed re-centre must leave a hold"
    assert arm.hold["reason"] == ("re-centering after restart failed: the "
                                  "mount did not answer"), arm.hold
    assert "site_detail" not in arm.hold, (
        f"an earlier refusal's site detail rode a later hold: {arm.hold}")


async def test_a_limit_check_that_fails_keeps_its_text_off_the_reason(
        sim_hub, monkeypatch):
    """The slew-limit check raises something other than its SafetyAbort (a
    bad target, a horizon file it could not read). The ladder cannot tell
    what that text carries, so it is treated as the gate's sentence is:
    the reason says in words that the check failed, and the text goes to
    ``site_detail`` (#233). The reason does not claim the target is outside
    the limits, because nothing said it was.

    RED under mutant "a failed check keeps its text in the reason" (the
    non-SafetyAbort arm returns ``f"re-centering after restart refused:
    {e}"``), observed verbatim:

        E   AssertionError: assert 're-centering...d at az 212.5' == 're-centering...; not slewing'
        E     - re-centering after restart refused: the slew-limit check failed; not slewing
        E     + re-centering after restart refused: the horizon mask could not be read at az 212.5

    RED under mutant "every exception reads as the gate" (``isinstance(e,
    SafetyAbort)`` replaced by ``True``), observed verbatim:

        E   AssertionError: assert 're-centering...t slewing yet' == 're-centering...; not slewing'
        E     - re-centering after restart refused: the slew-limit check failed; not slewing
        E     + re-centering after restart refused: the target is outside this rig's configured slew limits (altitude floor, horizon, no-go wedges, pier side or zenith keep-out); not slewing yet
    """
    _trust_focus(monkeypatch)
    engine = SequenceEngine(sim_hub)
    moved = _record_motion(sim_hub, monkeypatch)

    async def broken(target, *, cfg=None, plan=None, projected=True):
        raise RuntimeError("the horizon mask could not be read at az 212.5")
    monkeypatch.setattr(engine, "check_slew_limits", broken)
    arm = ResumeArm(engine, sim_hub, clock=lambda: 1_700_000_000.0)
    refusal = await arm._recover(Session(name="limits",
                                         plan=_plan_with_floor(0.0)))

    assert refusal == ("re-centering after restart refused: the slew-limit "
                       "check failed; not slewing")
    assert numeric_tokens(refusal) == numeric_tokens("")
    assert arm._refusal_site_detail == (
        "the horizon mask could not be read at az 212.5")
    assert "goto" not in moved, f"the mount was moved anyway: {moved}"


async def test_recover_slews_when_the_target_is_above_its_start_floor(
        sim_hub, monkeypatch):
    """The same session two hours later: nothing on this path changes."""
    _trust_focus(monkeypatch)
    engine = SequenceEngine(sim_hub)
    moved = _record_motion(sim_hub, monkeypatch)
    _spy_slew_limits(engine, monkeypatch)

    t = _when_altitude_between(sim_hub.site, NGC604_RA, NGC604_DEC, 40.0, 60.0)
    arm = ResumeArm(engine, sim_hub, clock=lambda: t)
    refusal = await arm._recover(Session(name="floor",
                                         plan=_plan_with_floor(30.0)))

    assert refusal is None, refusal
    assert moved == ["solve", "goto"], moved


async def test_recover_ignores_the_altitude_when_the_plan_sets_no_floor(
        sim_hub, monkeypatch):
    """``min_altitude_deg`` 0 is "no gate" everywhere else in this model and it
    stays "no gate" here: a plan that never asked for a floor resumes from
    exactly where it always did, nine degrees or not."""
    _trust_focus(monkeypatch)
    engine = SequenceEngine(sim_hub)
    moved = _record_motion(sim_hub, monkeypatch)
    _spy_slew_limits(engine, monkeypatch)

    t = _when_altitude_between(sim_hub.site, NGC604_RA, NGC604_DEC, 8.0, 10.0)
    arm = ResumeArm(engine, sim_hub, clock=lambda: t)
    refusal = await arm._recover(Session(name="floor",
                                         plan=_plan_with_floor(0.0)))

    assert refusal is None, refusal
    assert moved == ["solve", "goto"], moved


async def test_recover_skips_the_floor_check_when_the_site_is_unknown(
        sim_hub, monkeypatch):
    """No lat/lon means nobody can say how high the target is, and "nobody can
    say" must not read as "below the floor" - the same tri-state the engine's
    own floor gate keeps."""
    _trust_focus(monkeypatch)
    engine = SequenceEngine(sim_hub)
    moved = _record_motion(sim_hub, monkeypatch)
    _spy_slew_limits(engine, monkeypatch)
    monkeypatch.setattr(type(sim_hub), "site",
                        property(lambda self: {"name": "nowhere"}))

    arm = ResumeArm(engine, sim_hub, clock=lambda: 1_700_000_000.0)
    refusal = await arm._recover(Session(name="floor",
                                         plan=_plan_with_floor(30.0)))

    assert refusal is None, refusal
    assert moved == ["solve", "goto"], moved


async def test_a_mount_goto_refusal_reaches_the_hold_in_words(
        sim_hub, monkeypatch, bus_lines):
    """What the operator read that night was "goto rejected (reply 'e6')". The
    code is worth keeping; on its own it is not a sentence anybody can act on,
    and the repo documented only ``e14``."""
    from astrodeck.devices.base import GotoRefused

    _trust_focus(monkeypatch)
    engine = SequenceEngine(sim_hub)
    await _dormant_armed(sim_hub, engine)
    _record_motion(sim_hub, monkeypatch)
    _spy_slew_limits(engine, monkeypatch)

    async def refuse(*a, **kw):
        raise GotoRefused(
            "ZWO AM5 (native serial): goto rejected (the target is outside the "
            "mount's slew limits; reply 'e6')",
            code="e6",
            reason="the target is outside the mount's slew limits")
    monkeypatch.setattr(sim_hub, "goto_and_center", refuse)

    now = {"t": 1_700_000_000.0}
    arm = ResumeArm(engine, sim_hub, clock=lambda: now["t"])
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    await arm.tick()

    assert arm.hold is not None, "a refused goto must leave a visible hold"
    reason = arm.hold["reason"]
    assert "refused by the mount" in reason, reason
    assert "slew limits" in reason, reason
    assert any("refused by the mount" in m for _lv, m, _s in bus_lines), \
        f"the hold must be logged in words: {bus_lines}"
    assert not engine.running


async def test_the_ladder_says_when_each_recovery_solve_exposed(
        sim_hub, monkeypatch, bus_lines):
    """#402: on 2026-09-27 the ladder's centring solve, seconds after a good
    blind solve of the same sky, failed with no solution, once, and nothing
    could say whether the shutter had opened while the mount was still
    settling from its GoTo. Each recovery solve now logs when its exposure
    started against the GoTo it followed: the blind solve, made before any
    GoTo, says there was none; the re-centre's solve says how long after
    its GoTo came to rest. The hub's own record agrees, and puts that
    exposure at or after the settle.

    The simulator solves (``find_astap`` forced absent, so the resolver
    takes the SimSolver whatever this box has installed), and the focus
    step is trusted, so the ladder is its solve and its re-centre.

    MUTANT "the ladder says nothing of its solves" (both
    ``_say_solve_timing`` calls in `_recover` removed): RED (observed):
        AssertionError: the ladder did not say when its solves exposed: []
    MUTANT "the settle is never stamped" (`Hub.goto_and_center` no longer
    setting ``goto_settled_at`` after its centring slew): RED (observed):
        AssertionError: the ladder did not say when its solves exposed: [
        'auto-resume: the blind solve: the exposure started with no GoTo
        made since the server started, so none was settling', 'auto-resume:
        the re-centre: the exposure started with no GoTo made since the
        server started, so none was settling']
    """
    import re

    import astrodeck.providers as providers_module
    monkeypatch.setattr(providers_module, "find_astap", lambda: None)
    _trust_focus(monkeypatch)
    engine = SequenceEngine(sim_hub)
    arm = ResumeArm(engine, sim_hub, clock=lambda: 1_700_000_000.0)
    assert arm._can_solve(), "premise: the simulator's solver is resolved"
    refusal = await arm._recover(Session(name="solves", plan=_plan()))
    assert refusal is None, refusal

    said = [m for _lv, m, _s in bus_lines if m.startswith("auto-resume: the ")]
    ok = (len(said) == 2
          and said[0] == ("auto-resume: the blind solve: the exposure started "
                          "with no GoTo made since the server started, so "
                          "none was settling")
          and re.fullmatch(r"auto-resume: the re-centre: the exposure started "
                           r"\d+\.\d s after the last GoTo came to rest",
                           said[1]) is not None)
    assert ok, f"the ladder did not say when its solves exposed: {said}"
    recs = list(sim_hub.solve_exposures)
    assert [r["settled_at"] is None for r in recs] == [True, False], recs
    assert recs[1]["exposed_at"] >= recs[1]["settled_at"], recs


async def test_only_tonights_recovery_sweep_is_handed_to_the_start(
        sim_hub, monkeypatch):
    """#402: the ladder keeps its good sweep in memory across its retries,
    and hands it to a start as an AGE, measured on its own clock: 90 s after
    the sweep, the start is told 90 s. A sweep made on another night is
    not the focus of this one: it is dropped, not handed over.

    MUTANT "a sweep from another night is handed over" (the night check in
    ``_sweep_for_start`` removed): RED (observed):
        AssertionError: last night's sweep was handed to tonight's start:
        RecoverySweep(position=11022, temp_c=7.5, binning=2, age_s=86400.0)
    """
    from astrodeck.events import night_key
    from astrodeck.sequence.engine import RecoverySweep
    from astrodeck.sequence.resume_arm import _LadderSweep

    t0 = 1_700_000_000.0
    now = {"t": t0 + 90.0}
    arm = ResumeArm(SequenceEngine(sim_hub), sim_hub, clock=lambda: now["t"])
    arm._recovery_sweep = _LadderSweep(night=night_key(t0), at=t0,
                                       position=11022, temp_c=7.5, binning=2)
    assert night_key(now["t"]) == night_key(t0), "premise: the same night"
    assert arm._sweep_for_start() == RecoverySweep(
        position=11022, temp_c=7.5, binning=2, age_s=90.0)

    now["t"] = t0 + 86400.0
    assert night_key(now["t"]) != night_key(t0), "premise: the next night"
    handed = arm._sweep_for_start()
    assert handed is None, (
        f"last night's sweep was handed to tonight's start: {handed}")
    assert arm._recovery_sweep is None, "last night's sweep was kept"


@pytest.mark.parametrize("case", ["kept", "moved"])
async def test_a_kept_sweep_the_focuser_has_left_is_dropped(
        sim_hub, monkeypatch, case):
    """#402: the ladder keeps its good sweep across its retries, and the
    focus it stands for is where the sweep left the drawtube. A run can
    come and go between two ladders, its own sweeps moving the focuser, so
    a later ladder that finds the focuser somewhere else drops the sweep
    rather than hand it to the start. Control: a focuser still reading the
    sweep's position keeps it.

    MUTANT "a sweep the focuser has left is handed over" (the position
    check at the ladder's focus step removed): RED on moved, kept green
    (observed):
        AssertionError: moved: the ladder kept a sweep the focuser has left:
        _LadderSweep(night='2023-11-14', at=1700000000.0, position=19450,
        temp_c=7.5, binning=2)
    """
    from astrodeck.events import night_key
    from astrodeck.sequence.resume_arm import _LadderSweep

    _trust_focus(monkeypatch)
    engine = SequenceEngine(sim_hub)
    _record_motion(sim_hub, monkeypatch)
    _spy_slew_limits(engine, monkeypatch)
    t0 = 1_700_000_000.0
    arm = ResumeArm(engine, sim_hub, clock=lambda: t0)
    here = int(await sim_hub.devices["focuser"].get_position())
    left_at = here if case == "kept" else here + 250
    arm._recovery_sweep = _LadderSweep(night=night_key(t0), at=t0,
                                       position=left_at, temp_c=7.5,
                                       binning=2)
    refusal = await arm._recover(Session(name="kept", plan=_plan()))
    assert refusal is None, refusal
    if case == "kept":
        assert arm._recovery_sweep is not None, (
            "the ladder dropped a sweep the focuser still stands on")
    else:
        assert arm._recovery_sweep is None, (
            f"{case}: the ladder kept a sweep the focuser has left: "
            f"{arm._recovery_sweep}")
