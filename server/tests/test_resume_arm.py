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
    # happened to land first.
    assert any("below safety floor" in m for _lv, m, _s in bus_lines), \
        f"expected the altitude-limit refusal, got: {bus_lines}"


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

#: NGC 604 in M33 - the target the rig actually tried to re-centre on at 20:47
#: PDT, when it was nine degrees up and its own plan would not have started it
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
    """2026-09-06, 20:47 PDT. The armed session auto-resumed, the blind solve
    worked, the MOUNT floor gate passed - and the ladder then asked the AM5 to
    slew to a target at nine degrees that its own plan would not have started
    for two hours (``min_altitude_deg`` 30). The mount refused with ``e6`` and
    the resume held, retrying every ten minutes until the target rose.

    Nothing was wrong with the retry. What was wrong is that the plan already
    knew the answer and nobody asked it.
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
    assert "below its 30 deg start floor" in refusal, refusal
    assert "NGC 604" in refusal, refusal
    # ...and say how long the wait is, from the scheduler's own gate-crossing
    # search. "Not yet" with no number is the hold that gets diagnosed at 2am.
    assert "reaches 30 deg in about" in refusal, refusal
    assert "goto" not in moved, f"the mount was moved anyway: {moved}"
    assert gated == [], (
        "the target's own floor must be read BEFORE the mount gate - that gate "
        "passed on the rig, which is how the slew got out")


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
    """What the operator read at 20:47 was "goto rejected (reply 'e6')". The
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
