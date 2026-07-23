"""Regression tests for the Group-A2 sequence-engine major/minor fixes.

Each test targets ONE confirmed defect and would fail against the pre-fix code:

* floor guard evaluated the mount's CURRENT pointing, not the slew destination;
* the unsafe debounce counted FRAME boundaries, not safety-poller readings;
* the frozen stop/max-run/dawn boundary was never re-checked while a target ran;
* the mount was left tracking between targets during a scheduler wait;
* filter-wheel / focuser moves were awaited UNBOUNDED (stuck-wheel = all-night hang);
* the no-progress watchdog paged a false UNSAFE during scheduled waits / cooling;
* REJECTED frames fed the HFR running median, deactivating the reject gate;
* finalize() raced the threaded snapshot write on the shared ``<id>.json.tmp``.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

import astrodeck.hub as hub_module
import astrodeck.config as config_mod
import astrodeck.sequence.engine as engine_mod
import astrodeck.sequence.report as report_mod
from astrodeck.config import (AppConfig, ConfigStore, EscalationConfig,
                              SafetyConfig, Site)
from astrodeck.devices.base import SafetyReading
from astrodeck.events import bus
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.engine import SafetyAbort, StopTarget
from astrodeck.sequence.report import FrameRecord, SessionReporter


# --------------------------------------------------------------------- fixtures

@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Test", latitude=40.0, longitude=-74.0,
                        is_default=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    # collapse the safety re-read / poll cadences so debounce math resolves in
    # test time rather than wall-clock seconds.
    monkeypatch.setattr(engine_mod, "SAFETY_PAUSE_POLL_S", 0.02)
    monkeypatch.setattr(engine_mod, "SAFETY_SEED_WAIT_S", 0.5)
    return store


@pytest.fixture
async def sim_hub(temp_store, monkeypatch):
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def set_safety(store: ConfigStore, **kw) -> None:
    store.set_safety(SafetyConfig(**kw))


def force_cached_unsafe(hub: Hub, reason: str = "cloud sensor") -> None:
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


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


def _light_target(name="M42", **kw):
    base = dict(name=name, ra_hours=5.5881, dec_deg=-5.3911, center=False,
                autofocus_first=False,
                steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100, count=2)])
    base.update(kw)
    return Target(**base)


# ------------------------------------------------ floor guard: DESTINATION not current

async def test_mount_floor_evaluates_destination_not_current_pointing(sim_hub, monkeypatch):
    """The floor guard must judge the slew DESTINATION's altitude, never the
    mount's current pointing. A below-horizon target is refused even though the
    mount currently points elsewhere — and ``get_position`` (the old, wrong
    source) is never consulted."""
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(safety=SafetyConfig(enabled=True, min_alt_deg=89.0))

    tel = sim_hub.require("telescope")

    async def boom():
        raise AssertionError("floor guard must not read the mount's current pointing")
    monkeypatch.setattr(tel, "get_position", boom)

    # dec -80 from lat +40 never rises above ~ -30° → always below the 89° floor.
    low = Target(name="BelowHorizon", ra_hours=5.0, dec_deg=-80.0, steps=[])
    with pytest.raises(SafetyAbort):
        await engine._enforce_mount_floor(projected=True, target=low)

    # with no floor configured the same target passes (and still no get_position).
    engine._cfg = AppConfig(safety=SafetyConfig(enabled=True, min_alt_deg=0.0))
    await engine._enforce_mount_floor(projected=True, target=low)


async def test_mount_floor_does_not_false_abort_on_high_destination(sim_hub, monkeypatch):
    """A destination near the zenith clears a modest floor regardless of where the
    mount currently sits (the old guard could abort when parked horizon-low)."""
    engine = SequenceEngine(sim_hub)
    engine._cfg = AppConfig(safety=SafetyConfig(enabled=True, min_alt_deg=10.0))
    # a target at dec == site latitude transits through the zenith; pick its RA to
    # be near the local meridian NOW so it is high.
    from astrodeck.catalog.coords import lst_hours
    ra = lst_hours(sim_hub.site["longitude"]) % 24.0
    high = Target(name="NearZenith", ra_hours=ra, dec_deg=40.0, steps=[])
    await engine._enforce_mount_floor(projected=True, target=high)   # no raise


# --------------------------------------- unsafe debounce counts READINGS not frames

async def test_unsafe_debounce_counts_poller_readings_not_frames(sim_hub, temp_store):
    """With unsafe_consecutive greater than the number of frame boundaries in the
    run, the OLD per-frame counter never reached the threshold and the rig shot the
    whole plan through rain. The poll-cadence confirmation loop trips within a
    single frame boundary, so the first gate aborts the night."""
    set_safety(temp_store, enabled=True, on_unsafe="abort_park_warm",
               unsafe_consecutive=5, resume_safe_consecutive=1,
               max_pause_min=0, min_alt_deg=0.0)
    force_cached_unsafe(sim_hub, "rain")

    # only 2 frames → 1 slew gate + 2 frame gates = 3 per-frame increments < 5, so
    # the old code would COMPLETE without ever acting.
    plan = SequencePlan(name="dbtest", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        targets=[_light_target()])
    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    assert await wait_for(lambda: not engine.running, timeout=20), engine.state
    assert engine.state.get("state") == "aborted"
    assert engine.state.get("end_reason") == "unsafe"


async def test_unsafe_confirm_clears_on_transient_glitch(sim_hub, temp_store):
    """A single unsafe reading that clears before the debounce completes is a
    sensor glitch, not weather — the confirmation loop returns False and the run
    is NOT torn down."""
    set_safety(temp_store, enabled=True, on_unsafe="abort_park_warm",
               unsafe_consecutive=4, min_alt_deg=0.0)
    engine = SequenceEngine(sim_hub)
    engine._cfg = temp_store.cfg()
    engine.plan = SequencePlan(name="g", safety_check=True, targets=[_light_target()])

    force_cached_unsafe(sim_hub, "blip")

    async def clear_soon():
        await asyncio.sleep(0.03)      # after the first re-sample, before 4 accrue
        force_cached_safe(sim_hub)
    asyncio.ensure_future(clear_soon())

    confirmed = await engine._confirm_unsafe(engine._cfg)
    assert confirmed is False


# ----------------------------------- stop / max-run / dawn boundary enforced mid-run

async def test_stop_boundary_stops_running_target(sim_hub):
    """A frozen stop_ts in the past raises StopTarget at the frame boundary; an
    open (or absent) window does not."""
    engine = SequenceEngine(sim_hub)
    target = _light_target()
    now = time.time()

    engine._frozen = {id(target): (now - 100, now - 1)}          # closed
    with pytest.raises(StopTarget):
        engine._enforce_stop_boundary(target)

    engine._frozen = {id(target): (now - 100, now + 1000)}       # open
    engine._enforce_stop_boundary(target)                        # no raise

    engine._frozen = {id(target): (now - 100, None)}             # no stop boundary
    engine._enforce_stop_boundary(target)                        # no raise

    engine._frozen = {}                                          # not tracked
    engine._enforce_stop_boundary(target)                        # no raise


async def test_run_step_does_not_shoot_past_closed_window(sim_hub):
    """_run_step must stop BEFORE capturing a frame once the window has closed — no
    light frames are shot into daylight."""
    engine = SequenceEngine(sim_hub)
    target = Target(name="dawnT", ra_hours=5.5, dec_deg=-5.0, center=False,
                    autofocus_first=False,
                    steps=[ExposureStep(filter=None, exposure_s=0.05, count=3)])
    engine.plan = SequencePlan(name="p", targets=[target], guide=False,
                               safety_check=False)
    engine._cfg = None            # safety gate no-op
    engine._done = {}
    now = time.time()
    engine._frozen = {id(target): (now - 100, now - 1)}          # already closed

    with pytest.raises(StopTarget):
        await engine._run_step(0, 0, target, target.steps[0])
    assert engine._frames_done == 0


# ------------------------------------ mount stops tracking during inter-target wait

async def test_mount_stops_tracking_while_waiting_for_next_target(sim_hub, temp_store):
    """After target A completes, the scheduler waits hours for target B's window.
    The mount must NOT be left tracking A into the pier — a park-hold stops tracking
    before the long wait (the next target's setup restores it)."""
    set_safety(temp_store, enabled=False)
    a = _light_target(name="A", ra_hours=5.0, dec_deg=-5.0,
                      steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    # B waits ~3h (well beyond WAIT_TEARDOWN_S) on a clock start.
    future = time.strftime("%H:%M", time.localtime(time.time() + 3 * 3600))
    b = _light_target(name="B", ra_hours=6.0, dec_deg=-6.0,
                      steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    b.schedule.start_mode = "time"
    b.schedule.start_time = future
    plan = SequencePlan(name="waittest", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, safety_check=False,
                        targets=[a, b])

    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    try:
        assert await wait_for(
            lambda: "waiting for B" in (engine.state.get("detail") or ""),
            timeout=20), engine.state
        # the park-hold before the long wait turned tracking OFF.
        assert await wait_for(
            lambda: _tracking_is(sim_hub) is False, timeout=10)
    finally:
        await engine.abort()


def _tracking_is(hub):
    # synchronous best-effort peek at the sim mount's tracking flag.
    tel = hub.devices.get("telescope")
    return getattr(getattr(tel, "rig", tel), "tracking", None)


# --------------------------------- stale WS schedule sub-state cleared (wave-3 §2)

async def test_schedule_state_cleared_once_gated_target_starts(sim_hub, temp_store,
                                                                monkeypatch):
    """The ``schedule={"state": "waiting", ...}`` sub-block published while a
    target's window is gated must not survive past the wait: once the gated
    target actually starts running, ``engine.state`` must no longer carry a
    stale 'schedule' key (GET /api/sequence/state, the monitor snapshot, and the
    WS payload all serve ``engine.state`` verbatim)."""
    set_safety(temp_store, enabled=False)

    # Accelerate the engine's clock 3600x (1 real second = 1 fake hour) so a
    # target gated a few *wall-clock* minutes out becomes ready after only a
    # couple of real seconds. Exercises the SAME schedule.py window-resolution
    # math (HH:MM clock start) as the sibling "mount stops tracking" test above
    # — only the rate real time advances is sped up. asyncio's own scheduling
    # uses time.monotonic (unaffected by patching time.time), so this does not
    # break the event loop / wait_for polling below.
    real_time = time.time
    t0 = real_time()

    def fast_time():
        return t0 + (real_time() - t0) * 3600.0
    monkeypatch.setattr(time, "time", fast_time)

    future = time.strftime("%H:%M", time.localtime(t0 + 10 * 60))  # 10 min out
    b = _light_target(name="B", ra_hours=6.0, dec_deg=-6.0,
                      steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    b.schedule.start_mode = "time"
    b.schedule.start_time = future
    plan = SequencePlan(name="clearedtest", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, safety_check=False,
                        targets=[b])

    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    try:
        assert await wait_for(
            lambda: (engine.state.get("schedule") or {}).get("state") == "waiting",
            timeout=20), engine.state
        # the (accelerated) wait ends and B starts running — the stale schedule
        # sub-state must be cleared, not merged forward forever.
        assert await wait_for(
            lambda: engine.state.get("target") == "B"
                    and "schedule" not in engine.state,
            timeout=20), engine.state
    finally:
        await engine.abort()


async def test_schedule_state_cleared_after_abort_during_wait(sim_hub, temp_store):
    """Aborting mid-wait (operator cancels before the gated target's window
    opens) must also clear the stale schedule sub-state — the terminal
    'aborted' publish must not leave a dangling waiting block behind."""
    set_safety(temp_store, enabled=False)
    future = time.strftime("%H:%M", time.localtime(time.time() + 3 * 3600))
    b = _light_target(name="B", ra_hours=6.0, dec_deg=-6.0,
                      steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    b.schedule.start_mode = "time"
    b.schedule.start_time = future
    plan = SequencePlan(name="aborttest", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, safety_check=False,
                        targets=[b])

    engine = SequenceEngine(sim_hub)
    engine.start(plan)
    assert await wait_for(
        lambda: (engine.state.get("schedule") or {}).get("state") == "waiting",
        timeout=20), engine.state
    await engine.abort()
    assert engine.state.get("state") == "aborted"
    assert "schedule" not in engine.state


# ------------------------------------------ bounded filter-wheel / focuser moves

async def test_apply_filter_is_bounded_on_a_stuck_wheel(sim_hub, monkeypatch):
    """A wheel that never completes its move must not hang the night — the bounded
    await escalates to SafetyAbort."""
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(name="p", targets=[], apply_filter_offsets=False)

    fw = sim_hub.require("filterwheel")

    async def hang(slot):
        await asyncio.sleep(100)
    monkeypatch.setattr(fw, "set_position", hang)
    monkeypatch.setattr(engine_mod, "FILTER_MOVE_TIMEOUT_S", 0.1)

    step = ExposureStep(filter="Ha", exposure_s=0.05, count=1)   # Ha != current L
    with pytest.raises(SafetyAbort):
        await engine._apply_filter(step)


# ------------------------------------- watchdog silent during waits / cooling

async def test_watchdog_silent_when_no_frames_expected(sim_hub):
    """The no-progress watchdog must NOT fire while frames are not expected
    (scheduled wait / setup / cooling) even though state stays 'running' and the
    last-frame stamp is ancient — it must fire only during active capture."""
    engine = SequenceEngine(sim_hub)
    engine.state = {"state": "running"}
    engine._paused.set()
    engine._last_frame_at = time.time() - 100_000
    q = bus.subscribe()

    engine._progress_expected = False          # e.g. mid inter-target wait
    assert engine._watchdog_check(threshold=1.0, warned=False) is False
    # nothing published on the bus (no false UNSAFE page).
    assert q.empty()

    engine._progress_expected = True           # active capture, genuinely stalled
    assert engine._watchdog_check(threshold=1.0, warned=False) is True
    bus.unsubscribe(q)


# ------------------------------------- rejected frames excluded from HFR median

async def test_rejected_frames_do_not_feed_hfr_median(sim_hub):
    """A sustained cloud passage must keep getting rejected — rejected HFRs must
    never enter the running median (or it climbs to the cloud level and re-admits
    every subsequent bad frame)."""
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(name="p", targets=[], hfr_reject_factor=1.5)
    engine._recent_hfr = [2.0, 2.0, 2.0, 2.0]     # median 2.0 → threshold 3.0

    # 10 cloudy frames at HFR 5.0: every one stays rejected, median never climbs.
    for _ in range(10):
        assert engine._check_quality({"hfr": 5.0}) is False
    assert engine._recent_hfr == [2.0, 2.0, 2.0, 2.0]

    # a good frame is accepted AND anchors the median.
    assert engine._check_quality({"hfr": 2.2}) is True
    assert 2.2 in engine._recent_hfr


# ----------------------------------- per-frame eccentricity ceiling gate

async def test_max_eccentricity_gate(sim_hub):
    """The max_eccentricity ceiling rejects frames whose median star ecc is above
    the plan limit (trailing / tilt / coma), skips calibration frames, and abstains
    when no ecc scalar is present. 0 = off."""
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(name="p", targets=[], max_eccentricity=0.6)
    assert engine._check_quality({"ecc": 0.80}) is False   # elongated -> reject
    assert engine._check_quality({"ecc": 0.50}) is True    # round enough -> keep
    assert engine._check_quality({"ecc": 0.80}, calibration=True) is True  # calib skips
    assert engine._check_quality({}) is True               # no ecc -> abstain
    engine.plan = SequencePlan(name="p", targets=[])       # 0 = off
    assert engine._check_quality({"ecc": 0.99}) is True


# ----------------------------------- report finalize vs snapshot write do not race

async def test_finalize_does_not_race_snapshot_write(temp_store, tmp_path, monkeypatch):
    """finalize() (loop thread) and a scheduled snapshot write (worker thread) must
    never be inside write_json_atomic at the same time — else both truncate the
    shared ``<id>.json.tmp`` and publish corrupt JSON. The persist lock serializes
    them, so peak concurrency is 1."""
    conc = {"cur": 0, "max": 0}
    guard = threading.Lock()

    def slow_write(path, data, **kw):
        with guard:
            conc["cur"] += 1
            conc["max"] = max(conc["max"], conc["cur"])
        time.sleep(0.05)
        with guard:
            conc["cur"] -= 1

    monkeypatch.setattr(report_mod, "write_json_atomic", slow_write)

    plan = SequencePlan(name="racetest", targets=[])
    reporter = SessionReporter(plan)

    reporter.record_frame(FrameRecord(ts=time.time(), target="M42", filter="L",
                                      exposure_s=1.0, accepted=True))
    # let the create_task'd _write_async reach its to_thread worker write.
    await asyncio.sleep(0.02)
    # finalize writes SYNCHRONOUSLY on the loop thread while that worker write is
    # in flight — without the shared lock both would be inside slow_write.
    reporter.finalize("complete")
    await asyncio.sleep(0.12)

    assert conc["max"] == 1, f"finalize raced the snapshot write (peak {conc['max']})"
