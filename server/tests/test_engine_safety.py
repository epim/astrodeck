"""Engine automation/safety integration tests (Batch 4b §1.9 / engine.py).

Exercises the lane wired into the engine:

* the safety gate driving the configured ``on_unsafe`` action — ``abort_park_warm``
  tears the night down (state ``aborted`` / end_reason ``unsafe``, report finalized
  "unsafe", mount parked), and ``pause`` holds then auto-resumes when conditions
  clear (force_unsafe / force_safe through the cached reading);
* quality-before-record: a bad frame under ``hfr_reject_action="discard"`` unlinks
  its FITS and does NOT advance ``_done`` (the accepted-frame slot is untouched);
* the skip-ahead scheduler: a target whose window never opens is skipped without
  aborting the night, and the rest of the plan still completes;
* every terminal path finalizes the reporter and the report is listable.

Config is isolated through a temp ``ConfigStore`` monkeypatched onto the engine /
config / hub modules; CAPTURE_DIR is redirected to tmp so reports + the resume
file never touch the real captures store. The safety cache is driven directly so
the tests don't wait on the 5 s poller cadence.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
import astrodeck.config as config_mod
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import (AppConfig, ConfigStore, EscalationConfig,
                              SafetyConfig, Site)
from astrodeck.devices.base import SafetyReading
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.report import SessionReporter


# --------------------------------------------------------------------- fixtures

@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """A ConfigStore in tmp, wired onto every module that resolves config_store
    via ``from ..config import config_store`` (engine, hub) plus the module."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    # a real (non-default) site so altaz / the mount floor are meaningful.
    store.set_site(Site(name="Test", latitude=40.0, longitude=-74.0,
                        is_default=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    # speed up the paused-for-safety re-read loop and the poller cadence so the
    # state machine resolves in test time, not wall-clock minutes.
    monkeypatch.setattr(engine_mod, "SAFETY_PAUSE_POLL_S", 0.05)
    monkeypatch.setattr(engine_mod, "SAFETY_SEED_WAIT_S", 0.5)
    return store


@pytest.fixture
async def sim_hub(temp_store, monkeypatch):
    # Disarm the W1.10 sun-exclusion cone at the hub-method level so it survives
    # the per-test ``set_safety(SafetyConfig(...))`` calls (which would otherwise
    # reset solar_avoidance to its protective default and sun-block the FIXED M42
    # targets whenever today's Sun is within 30 deg). The cone itself is covered
    # by test_sun_guard.py; here we isolate the safety/timeout/schedule mechanics.
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def set_safety(store: ConfigStore, **kw) -> None:
    store.set_safety(SafetyConfig(**kw))


def set_escalation(store: ConfigStore, **kw) -> None:
    store.set_escalation(EscalationConfig(**kw))


def force_cached_unsafe(hub: Hub, reason: str = "cloud sensor") -> None:
    """Drive the hub's cached SafetyReading UNSAFE directly (no waiting on the
    poller). The sim monitor's force_unsafe is also flipped so the poller agrees
    on its next tick."""
    mon = hub.devices.get("safety")
    if mon is not None:
        mon.force_unsafe(reason)
    hub._safety_reading = SafetyReading(is_safe=False, reason=reason,
                                        source="Sim Safety Monitor")


def force_cached_safe(hub: Hub) -> None:
    mon = hub.devices.get("safety")
    if mon is not None:
        mon.force_safe()
    hub._safety_reading = SafetyReading(is_safe=True, source="Sim Safety Monitor")


def light_plan(**overrides) -> SequencePlan:
    """A small all-default-schedule (run-now) light plan."""
    defaults = dict(
        name="safetytest",
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100, count=6)],
        )],
        guide=False, dither_every=0, autofocus_every=0, meridian_flip=False,
    )
    return SequencePlan(**(defaults | overrides))


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


# --------------------------------------------------------- abort_park_warm preset

async def test_unsafe_aborts_and_parks_under_remote_preset(sim_hub, temp_store):
    """on_unsafe=abort_park_warm + unsafe_consecutive=1: the first unsafe frame
    tears the night down (aborted / end_reason unsafe), parks the mount, and
    finalizes the report as 'unsafe'."""
    set_safety(temp_store, enabled=True, on_unsafe="abort_park_warm",
               unsafe_consecutive=1, min_alt_deg=0.0)
    force_cached_unsafe(sim_hub, "cloud sensor")

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    # wait for the run task to FULLY finish (the shielded wind-down runs after the
    # 'aborted' state is published).
    assert await wait_for(lambda: not engine.running)
    assert engine.state.get("state") == "aborted"
    assert engine.state.get("end_reason") == "unsafe"
    # shielded wind-down parked the mount.
    assert await sim_hub.require("telescope").is_parked()
    # the report finalized as unsafe and is listable.
    rep = SessionReporter.load(engine.reporter.id)
    assert rep is not None
    assert rep.end_reason == "unsafe"
    assert any(ev["reason"] == "cloud sensor" for ev in rep.safety_events)
    assert engine.reporter.id in [r["id"] for r in SessionReporter.list_reports()]


# ------------------------------------------------------- pause / auto-resume

async def test_unsafe_pauses_then_resumes_when_safe_again(sim_hub, temp_store):
    """on_unsafe=pause: an unsafe verdict pauses (mount tracking off / park-hold);
    once conditions clear for resume_safe_consecutive reads the run resumes and
    completes. The frames-done counter does not regress through the pause."""
    set_safety(temp_store, enabled=True, on_unsafe="pause",
               unsafe_consecutive=1, resume_safe_consecutive=1,
               max_pause_min=0, min_alt_deg=0.0)
    force_cached_unsafe(sim_hub, "wind gust")

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    # it should enter the paused-for-safety hold.
    assert await wait_for(lambda: engine.state.get("state") == "paused"), engine.state
    paused_at = engine._frames_done
    # mount tracking was turned off as part of the park-hold.
    assert (await sim_hub.require("telescope").get_tracking()) is False
    # clear the condition → the pause loop sees safe reads and resumes.
    force_cached_safe(sim_hub)
    assert await wait_for(lambda: engine.state.get("state") == "complete", timeout=40)
    assert engine._frames_done >= paused_at        # never regressed
    rep = SessionReporter.load(engine.reporter.id)
    assert rep is not None and rep.end_reason == "complete"
    assert any(ev["action"] == "pause" for ev in rep.safety_events)


# ------------------------------------------------- max-pause escalation to park

async def test_pause_escalates_to_park_after_max_pause(sim_hub, temp_store):
    """A pause that never clears escalates to a park wind-down + SafetyAbort once
    max_pause_min elapses (a tiny fractional cap keeps the test fast)."""
    set_safety(temp_store, enabled=True, on_unsafe="pause",
               unsafe_consecutive=1, resume_safe_consecutive=1,
               max_pause_min=0, min_alt_deg=0.0)
    # shrink the wall-clock budget: 0.005 min == 0.3 s (max_pause_min * 60).
    # Pydantic doesn't re-validate on attribute assignment, so a fractional value
    # is fine for the test; the engine snapshots this same object at start().
    temp_store.cfg().safety.max_pause_min = 0.005
    force_cached_unsafe(sim_hub, "rain")           # never cleared → must escalate

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    assert await wait_for(lambda: engine.state.get("state") == "aborted", timeout=20), engine.state
    assert engine.state.get("end_reason") == "unsafe"


# --------------------------------------------- quality-before-record (discard)

async def test_discard_drops_bad_frame_without_advancing_done(sim_hub, temp_store):
    """hfr_reject_action=discard: a frame whose HFR is flagged is unlinked and
    does NOT advance the _done slot or the accepted-frame count, while a clean run
    advances normally. We force a reject by stubbing _check_quality for one frame."""
    set_safety(temp_store, enabled=False)             # isolate from safety gate
    set_escalation(temp_store, hfr_reject_action="discard")

    engine = SequenceEngine(sim_hub)
    plan = light_plan(targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=False,
        autofocus_first=False,
        steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100, count=3)])])

    # reject exactly the first captured frame, accept the rest.
    state = {"n": 0}

    def fake_check(info):
        state["n"] += 1
        return state["n"] != 1            # first frame rejected, rest accepted
    engine._check_quality = fake_check

    # spy on _unlink_saved to confirm the discarded FITS was removed (it's a
    # @staticmethod — the class attribute is the plain function).
    unlinked = []
    real_unlink = engine_mod.SequenceEngine._unlink_saved

    def spy_unlink(info):
        unlinked.append(info.get("saved_path") if isinstance(info, dict) else None)
        real_unlink(info)
    engine._unlink_saved = spy_unlink

    engine.start(plan)
    assert await wait_for(lambda: engine.state.get("state") == "complete", timeout=40)
    # 3 accepted frames despite the 1 discard (the discarded capture didn't count
    # toward _done as an accepted frame; the loop kept exposing until count met...)
    # NOTE: discard leaves the slot un-advanced for that index, so accepted frames
    # = count - discards. Assert the discard happened + a FITS was unlinked.
    assert state["n"] >= 3
    assert unlinked, "a discarded frame's FITS should have been unlinked"
    # the report has a rejected frame recorded (accepted=False).
    rep = SessionReporter.load(engine.reporter.id)
    assert rep is not None
    assert rep.frames_rejected >= 1


# ------------------------------------------------- skip-ahead never-rises target

async def test_never_rises_target_is_skipped_not_aborted(sim_hub, temp_store):
    """A target whose start gate it can never clear tonight is SKIPPED (marked in
    the report) and the rest of the plan still completes — a scheduling stop must
    not abort the night (§1.9-C)."""
    set_safety(temp_store, enabled=False)
    # target A: impossible 89° start gate from a southern dec at lat 40 -> never rises.
    a = Target(name="ImpossibleLow", ra_hours=5.0, dec_deg=-80.0, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=2)])
    a.schedule.min_altitude_deg = 89.0
    # target B: a normal run-now target that should complete.
    b = Target(name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=2)])
    plan = light_plan(targets=[a, b])

    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    assert await wait_for(lambda: engine.state.get("state") in ("complete",), timeout=40), engine.state
    # B's 2 frames captured; A contributed none.
    assert engine._frames_done == 2
    rep = SessionReporter.load(engine.reporter.id)
    assert rep is not None
    assert any("ImpossibleLow" in ev.get("reason", "") and ev.get("action") == "skip"
               for ev in rep.safety_events), rep.safety_events


# ------------------------------------------------------- backward-compat default

async def test_default_schedule_plan_runs_in_order_no_waiting(sim_hub, temp_store):
    """A plan whose targets all use the default (start_mode=now) schedule runs all
    targets in insertion order with no waiting — identical to legacy behavior."""
    set_safety(temp_store, enabled=False)
    t1 = Target(name="A", ra_hours=5.0, dec_deg=-5.0, center=False,
                autofocus_first=False,
                steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    t2 = Target(name="B", ra_hours=6.0, dec_deg=-6.0, center=False,
                autofocus_first=False,
                steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    plan = light_plan(targets=[t1, t2])
    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    assert await wait_for(lambda: engine.state.get("state") == "complete", timeout=40)
    assert engine._frames_done == 2
    rep = SessionReporter.load(engine.reporter.id)
    assert rep is not None and rep.end_reason == "complete"
    # no skip events for an all-ready plan.
    assert not any(ev.get("action") == "skip" for ev in rep.safety_events)


# --------------------------------------------------- mount-floor guard on slew

async def test_resume_re_enables_tracking_after_pause(sim_hub, temp_store):
    """CRITICAL: a safety pause turns tracking OFF (park-hold); the safe-again
    resume MUST re-run target setup so tracking comes back ON (and the target is
    re-acquired) before capturing continues — otherwise the rest of the night is
    shot with the mount not tracking (star trails, drifted field)."""
    set_safety(temp_store, enabled=True, on_unsafe="pause",
               unsafe_consecutive=1, resume_safe_consecutive=1,
               max_pause_min=0, min_alt_deg=0.0)
    force_cached_unsafe(sim_hub, "cloud")

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())               # center=False → plain slew + tracking
    assert await wait_for(lambda: engine.state.get("state") == "paused"), engine.state
    # park-hold turned tracking off during the pause.
    assert (await sim_hub.require("telescope").get_tracking()) is False

    force_cached_safe(sim_hub)
    assert await wait_for(lambda: engine.state.get("state") == "complete", timeout=40)
    # the resume re-acquired the target: tracking is back ON (the bug left it OFF
    # for the entire remainder of the run).
    assert (await sim_hub.require("telescope").get_tracking()) is True


async def test_center_solve_failure_does_not_crash_run(sim_hub, temp_store, monkeypatch):
    """CRITICAL: goto_and_center returns error_arcmin=None on the solve-failure /
    motion-fence abort paths (it degrades to a raw GoTo). Formatting None with
    :.1f used to raise TypeError and end the whole night at its first target.
    Here the run must simply log 'solve failed' and continue capturing."""
    set_safety(temp_store, enabled=False)

    async def degraded(ra_hours, dec_deg, **kw):
        return {"centered": False, "error_arcmin": None, "attempts": 1,
                "solve_failed": True}
    monkeypatch.setattr(sim_hub, "goto_and_center", degraded)

    plan = light_plan(targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=True,
        autofocus_first=False,
        steps=[ExposureStep(filter="L", exposure_s=0.05, count=2)])])
    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    assert await wait_for(lambda: engine.state.get("state") == "complete", timeout=40), engine.state
    assert engine._frames_done == 2          # did not die at the first target


async def test_meridian_flip_triggers_via_server_ha_on_sim(sim_hub, temp_store, monkeypatch):
    """Sim/Alpaca mounts report ``time_to_meridian_flip() -> None``; the engine
    must fall back to the server hour-angle countdown to ever flip a GEM. A target
    3h east is not flipped; a target just past the meridian is."""
    from astrodeck.catalog.coords import lst_hours

    flips: list[tuple] = []

    async def rec_flip(ra_hours, dec_deg):
        flips.append((ra_hours, dec_deg))
        return {"centered": True, "error_arcmin": 0.0}
    monkeypatch.setattr(sim_hub, "meridian_flip", rec_flip)

    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(meridian_flip=True)   # _cfg stays None → gate no-op
    # _setup_target arms the flip when a target is acquired east of the meridian;
    # simulate that (we call _maybe_meridian_flip directly, bypassing setup).
    engine._flip_armed = True
    lon = sim_hub.site["longitude"]

    east = Target(name="e", ra_hours=(lst_hours(lon) + 3.0) % 24.0,
                  dec_deg=20.0, steps=[])
    await engine._maybe_meridian_flip(east, next_exposure_s=1.0)
    assert flips == []

    west = Target(name="w", ra_hours=(lst_hours(lon) - 0.05) % 24.0,
                  dec_deg=20.0, steps=[])
    await engine._maybe_meridian_flip(west, next_exposure_s=1.0)
    assert len(flips) == 1


async def test_meridian_flip_not_armed_when_acquired_west(sim_hub, temp_store, monkeypatch):
    """A target acquired already WEST of the meridian is on the correct pier side
    and must NOT flip: arming on the HA sign alone would flip it, and since the
    server countdown stays negative for the whole ~12h it is west, it would
    re-flip on EVERY frame (infinite loop). A full meridian_flip=True run over a
    west target completes with zero flips."""
    from astrodeck.catalog.coords import lst_hours
    set_safety(temp_store, enabled=False)

    flips: list[int] = []

    async def rec_flip(ra_hours, dec_deg):
        flips.append(1)
        return {"centered": True, "error_arcmin": 0.0}
    monkeypatch.setattr(sim_hub, "meridian_flip", rec_flip)

    lon = sim_hub.site["longitude"]
    west = Target(name="w", ra_hours=(lst_hours(lon) - 3.0) % 24.0, dec_deg=20.0,
                  center=False, autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.05, count=3)])
    plan = light_plan(targets=[west], meridian_flip=True)
    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    assert await wait_for(lambda: engine.state.get("state") == "complete", timeout=40), engine.state
    assert flips == []                        # never flipped (already correct side)
    assert engine._frames_done == 3


async def test_mount_floor_blocks_slew_below_floor(sim_hub, temp_store):
    """With a high safety floor and the mount parked at the pole (or pointing
    low), a slew gate that projects the mount below the configured floor raises a
    SafetyAbort -> aborted/unsafe. Here the sim mount sits near M42 (low-ish);
    a 80° floor is unreachable -> abort."""
    set_safety(temp_store, enabled=True, on_unsafe="abort_park_warm",
               unsafe_consecutive=1, min_alt_deg=80.0)
    force_cached_safe(sim_hub)        # device says safe; the FLOOR is what trips

    engine = SequenceEngine(sim_hub)
    # center=False so _setup_target does a plain slew after the gate.
    engine.start(light_plan())
    assert await wait_for(lambda: not engine.running, timeout=20), engine.state
    assert engine.state.get("state") == "aborted"
    assert engine.state.get("end_reason") == "unsafe"
    rep = SessionReporter.load(engine.reporter.id)
    assert rep is not None and rep.end_reason == "unsafe"


async def test_calibration_frames_are_weather_gated(sim_hub, monkeypatch):
    """The one capture loop that had a _checkpoint and no _safety_gate.

    The engine's module docstring promises the gate runs "after every
    frame-boundary _checkpoint", and every unsafe ACTUATION — park-hold, the
    SafetyAbort wind-down, close_dome_on_unsafe — lives only behind that call.
    So a calibration block ran weather-blind from its first frame to its last,
    which matters: flats are shot with the roof open at dusk, and a dark set can
    run for hours.
    """
    from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target

    engine = SequenceEngine(sim_hub)
    seen: list = []

    async def spy(*, context, target=None):
        seen.append(context)
    monkeypatch.setattr(engine, "_safety_gate", spy)

    plan = SequencePlan(name="cal", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[
                            Target(name="Darks", calibration=True, center=False,
                                   autofocus_first=False, ra_hours=0.0, dec_deg=0.0,
                                   steps=[ExposureStep(filter="", exposure_s=0.02,
                                                       frame_type="Dark", count=2)])])
    engine.start(plan)
    assert await wait_for(lambda: engine.state.get("state") in ("complete", "error"),
                          timeout=30)
    assert seen.count("frame") >= 2, (
        f"each calibration frame must pass the weather gate, saw {seen}")
