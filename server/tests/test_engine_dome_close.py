"""PRO-4 Task 6 — engine wiring: end-of-night + unsafe roof close through the
shielded ``_wind_down`` teardown, and the ``_on_unsafe`` escalation.

The SimDome's self-checking collision model makes the ordering PROVABLE: if the
close ever ran before the mount reached parked, ``close_shutter`` would RAISE and
the shutter would never reach CLOSED. So "dome CLOSED" is itself proof that the
park happened first.

Harness mirrors test_engine_safety.py (isolated temp ConfigStore + a sim hub).
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
import astrodeck.config as config_mod
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import ConfigStore, SafetyConfig, Site
from astrodeck.devices.base import DomeShutterState, SafetyReading
from astrodeck.events import bus
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target


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
    monkeypatch.setattr(engine_mod, "SAFETY_PAUSE_POLL_S", 0.05)
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


def light_plan(**overrides) -> SequencePlan:
    defaults = dict(
        name="dometest",
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="L", exposure_s=0.05, gain=100, count=4)],
        )],
        guide=False, dither_every=0, autofocus_every=0, meridian_flip=False,
    )
    return SequencePlan(**(defaults | overrides))


async def wait_for(predicate, timeout=40.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _logged(lines, substr: str) -> bool:
    """Did THIS test provoke a line containing ``substr``?

    Takes the ``bus_lines`` capture rather than reading ``bus.log_history``. The
    ring is a deque(maxlen=200) shared by the whole process: a positive check
    goes red when an unrelated suite fills it and the awaited line ages out, and
    a negative check is meaningless because the ring holds other tests' lines
    (the comment below at the debounce test says exactly this). The capture holds
    this test's lines, all of them, and nobody else's.
    """
    return any(substr in m for _lvl, m, _src in lines)


# ----------------------------------------------------------- end-of-night close

async def test_end_of_night_closes_roof(sim_hub, temp_store):
    """close_dome_when_done=True + park_when_done=True: a completed plan parks the
    mount then closes the roof over the parked gear."""
    dome = sim_hub.devices.get("dome")
    assert dome is not None and dome.connected, "sim rig must connect a dome"
    set_safety(temp_store, enabled=False, close_dome_when_done=True, min_alt_deg=0.0)

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan(park_when_done=True))
    assert await wait_for(lambda: not engine.running), engine.state
    assert engine.state.get("state") == "complete"
    assert await sim_hub.require("telescope").is_parked()
    assert await dome.shutter_state() is DomeShutterState.CLOSED


async def test_flag_off_leaves_roof_open(sim_hub, temp_store):
    """close_dome_when_done=False: the roof is left OPEN at end-of-night even
    though the mount parks."""
    dome = sim_hub.devices.get("dome")
    set_safety(temp_store, enabled=False, close_dome_when_done=False, min_alt_deg=0.0)

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan(park_when_done=True))
    assert await wait_for(lambda: not engine.running), engine.state
    assert engine.state.get("state") == "complete"
    assert await dome.shutter_state() is DomeShutterState.OPEN


# ------------------------------------------------ unsafe escalation → park+close

async def test_unsafe_pause_preset_escalates_to_close(sim_hub, temp_store):
    """on_unsafe=pause + close_dome_on_unsafe=True: an unsafe verdict must NOT
    pause-hold under an open roof. It ESCALATES to the shielded park-and-close
    teardown — the run ends aborted/unsafe, the mount is parked, the roof CLOSED.

    'dome CLOSED' proves the park ran BEFORE the close (the SimDome collision
    model would have raised otherwise)."""
    dome = sim_hub.devices.get("dome")
    set_safety(temp_store, enabled=True, on_unsafe="pause", unsafe_consecutive=1,
               resume_safe_consecutive=1, max_pause_min=0, min_alt_deg=0.0,
               close_dome_on_unsafe=True)
    force_cached_unsafe(sim_hub, "cloud sensor")

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    assert await wait_for(lambda: not engine.running), engine.state
    # It aborted (did NOT sit in 'paused' with the roof open).
    assert engine.state.get("state") == "aborted"
    assert engine.state.get("end_reason") == "unsafe"
    assert await sim_hub.require("telescope").is_parked()
    assert await dome.shutter_state() is DomeShutterState.CLOSED


async def test_no_dome_leaves_pause_behavior_unchanged(sim_hub, temp_store):
    """close_dome_on_unsafe=True but NO dome connected → today's pause/resume
    behavior is unchanged (regression guard): the run pauses, then resumes+completes
    when conditions clear."""
    # Remove the dome so the escalation branch is skipped.
    sim_hub.devices.pop("dome", None)
    set_safety(temp_store, enabled=True, on_unsafe="pause", unsafe_consecutive=1,
               resume_safe_consecutive=1, max_pause_min=0, min_alt_deg=0.0,
               close_dome_on_unsafe=True)
    force_cached_unsafe(sim_hub, "wind gust")

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    # It pauses (does not abort) — exactly today's behavior with no dome.
    assert await wait_for(lambda: engine.state.get("state") == "paused"), engine.state
    # Clear the condition → resumes and completes.
    mon = sim_hub.devices.get("safety")
    mon.force_safe()
    sim_hub._safety_reading = SafetyReading(is_safe=True, source="Sim Safety Monitor")
    assert await wait_for(lambda: engine.state.get("state") == "complete", timeout=40), engine.state


async def test_park_fails_refuses_close_and_pages(sim_hub, temp_store, bus_lines):
    """If the fenced park does not confirm parked, close_observatory REFUSES: the
    roof stays OPEN (never ERROR — close_shutter is never called) and an error is
    logged. No crash."""
    dome = sim_hub.devices.get("dome")
    tel = sim_hub.devices.get("telescope")

    async def broken_park():
        # Slew home but DON'T set rig.parked — model a park that never confirms.
        pass

    tel.park = broken_park
    set_safety(temp_store, enabled=False, close_dome_when_done=True, min_alt_deg=0.0)

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan(park_when_done=True))
    assert await wait_for(lambda: not engine.running), engine.state
    assert engine.state.get("state") == "complete"     # teardown didn't crash
    # Refused (not crushed): the shutter was never actuated → still OPEN.
    assert await dome.shutter_state() is DomeShutterState.OPEN
    assert not await tel.is_parked()
    assert _logged(bus_lines, "AUTOMATED ROOF CLOSE FAILED") or _logged(bus_lines, "REFUSED"), \
        "a refused auto-close must page via an error log"


# =============================================================== auto-reopen (D3)
#
# reopen_dome_when_safe=True (+ close_dome_on_unsafe) turns an unsafe trip into
# CLOSE → wait-for-safe (debounced) → REOPEN → re-acquire → RESUME instead of
# ending the run. The SimDome's collision model still makes ordering PROVABLE: a
# recorded "close" event means the mount was parked first (else close_shutter
# would have raised). A "close" THEN "open" in the spy proves close-before-reopen.


def _spy_shutter(dome):
    """Record every actual open/close on the dome (wraps the real methods so the
    SimDome collision model + state transitions still run). ``events`` is the
    ordered list of "close"/"open"; proving order and that reopen used ONLY
    open_shutter (never a raw close)."""
    events: list[str] = []
    orig_open = dome.open_shutter
    orig_close = dome.close_shutter

    async def spy_open():
        events.append("open")
        await orig_open()

    async def spy_close():
        events.append("close")
        await orig_close()

    dome.open_shutter = spy_open
    dome.close_shutter = spy_close
    return events


def force_cached_safe(hub: Hub) -> None:
    mon = hub.devices.get("safety")
    if mon is not None:
        mon.force_safe()
    hub._safety_reading = SafetyReading(is_safe=True, source="Sim Safety Monitor")


async def test_reopen_happy_path_closes_waits_reopens_resumes(sim_hub, temp_store, bus_lines):
    """close_dome_on_unsafe + reopen_dome_when_safe: an unsafe trip CLOSES the roof
    over the PARKED mount (via close_observatory — a recorded 'close' proves the
    park ran first), waits, then on safe-again REOPENS (open_shutter) and
    re-acquires the target and RESUMES to completion. INVARIANTS 1 (close via the
    never-crush guard), 3 (reopen = open_shutter), 4/happy (resume streak)."""
    dome = sim_hub.devices.get("dome")
    tel = sim_hub.require("telescope")
    events = _spy_shutter(dome)
    set_safety(temp_store, enabled=True, on_unsafe="pause", unsafe_consecutive=1,
               resume_safe_consecutive=1, max_pause_min=0, min_alt_deg=0.0,
               close_dome_on_unsafe=True, reopen_dome_when_safe=True)
    force_cached_unsafe(sim_hub, "cloud sensor")

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    # The roof CLOSES over the parked gear and the run holds paused (does not end).
    # Wait for BOTH the close AND the paused state: the close event fires inside
    # close_observatory a hair before _await_safe_and_reopen sets state=paused.
    assert await wait_for(lambda: "close" in events
                          and engine.state.get("state") == "paused"), engine.state
    assert await tel.is_parked(), "mount must be PARKED before the roof closed"
    assert await dome.shutter_state() is DomeShutterState.CLOSED
    assert "open" not in events, "must not reopen while still unsafe"

    # Conditions clear → REOPEN (open_shutter), re-acquire, resume, complete.
    force_cached_safe(sim_hub)
    assert await wait_for(lambda: engine.state.get("state") == "complete",
                          timeout=40), engine.state
    assert events == ["close", "open"], events        # close BEFORE reopen
    assert await dome.shutter_state() is DomeShutterState.OPEN
    assert not await tel.is_parked(), "target must be re-acquired (unparked)"
    assert _logged(bus_lines, "reopening roof")
    assert _logged(bus_lines, "re-acquiring")


async def test_reopen_close_refused_falls_back_to_open_sky_pause(sim_hub, temp_store, bus_lines):
    """INVARIANT 2: if the never-crush close REFUSES (mount won't confirm parked),
    the run does NOT enter the wait-reopen loop — it FALLS BACK to the open-sky
    park-hold pause (the roof is never actuated → stays OPEN, never crushed), and
    resumes when safe. A refused close never touches the shutter."""
    dome = sim_hub.devices.get("dome")
    tel = sim_hub.require("telescope")
    events = _spy_shutter(dome)

    async def broken_park():        # slews home but never confirms parked
        pass

    tel.park = broken_park
    set_safety(temp_store, enabled=True, on_unsafe="pause", unsafe_consecutive=1,
               resume_safe_consecutive=1, max_pause_min=0, min_alt_deg=0.0,
               close_dome_on_unsafe=True, reopen_dome_when_safe=True)
    force_cached_unsafe(sim_hub, "cloud sensor")

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    # It fell back to the OPEN-sky park-hold pause (never crushed).
    assert await wait_for(lambda: engine.state.get("state") == "paused"), engine.state
    assert await dome.shutter_state() is DomeShutterState.OPEN, "roof never actuated"
    assert "close" not in events, "a REFUSED close must never touch the shutter"
    assert _logged(bus_lines, "close refused") or _logged(bus_lines, "holding under open sky")

    # Clear the condition → the fallback pause resumes and completes normally.
    force_cached_safe(sim_hub)
    assert await wait_for(lambda: engine.state.get("state") == "complete",
                          timeout=40), engine.state
    assert "open" not in events, "fallback pause must not drive the shutter"
    assert await dome.shutter_state() is DomeShutterState.OPEN


async def test_reopen_max_pause_aborts_leaving_roof_closed(sim_hub, temp_store):
    """INVARIANT 5: with the roof CLOSED, if conditions stay unsafe past
    max_pause_min the run SafetyAborts and the roof is LEFT CLOSED (fail-safe) —
    it is NEVER reopened. A tiny fractional cap keeps the test fast."""
    dome = sim_hub.devices.get("dome")
    tel = sim_hub.require("telescope")
    events = _spy_shutter(dome)
    set_safety(temp_store, enabled=True, on_unsafe="pause", unsafe_consecutive=1,
               resume_safe_consecutive=1, max_pause_min=0, min_alt_deg=0.0,
               close_dome_on_unsafe=True, reopen_dome_when_safe=True)
    # 0.005 min == 0.3 s (pydantic doesn't re-validate on attribute assignment;
    # the engine snapshots this same object at start()).
    temp_store.cfg().safety.max_pause_min = 0.005
    force_cached_unsafe(sim_hub, "rain")              # never cleared → must abort

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    assert await wait_for(lambda: engine.state.get("state") == "aborted",
                          timeout=20), engine.state
    assert engine.state.get("end_reason") == "unsafe"
    # Roof LEFT CLOSED (fail-safe), never reopened.
    assert await dome.shutter_state() is DomeShutterState.CLOSED
    assert "open" not in events, "max-pause must NOT reopen the roof"
    assert await tel.is_parked()


async def test_reopen_off_is_byte_identical_still_aborts(sim_hub, temp_store):
    """INVARIANT 6: reopen_dome_when_safe=False (default) ⇒ close_dome_on_unsafe is
    byte-identical to before — an unsafe trip ESCALATES to the park-and-close
    teardown and the run ENDS aborted/unsafe. NO reopen loop, open_shutter never
    called."""
    dome = sim_hub.devices.get("dome")
    tel = sim_hub.require("telescope")
    events = _spy_shutter(dome)
    set_safety(temp_store, enabled=True, on_unsafe="pause", unsafe_consecutive=1,
               resume_safe_consecutive=1, max_pause_min=0, min_alt_deg=0.0,
               close_dome_on_unsafe=True, reopen_dome_when_safe=False)
    force_cached_unsafe(sim_hub, "cloud sensor")

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    assert await wait_for(lambda: not engine.running), engine.state
    assert engine.state.get("state") == "aborted"
    assert engine.state.get("end_reason") == "unsafe"
    assert await tel.is_parked()
    assert await dome.shutter_state() is DomeShutterState.CLOSED
    # The per-test shutter spy is the reliable proof of no reopen (bus.log_history
    # is a cross-test accumulator, so a negative _logged() check is unreliable).
    assert "open" not in events, "reopen OFF must never reopen the roof"


async def test_reopen_debounce_single_safe_does_not_reopen(sim_hub, temp_store):
    """INVARIANT 4: reopen requires resume_safe_consecutive CONSECUTIVE safe reads.
    A single safe read INTERRUPTED by an unsafe read resets the streak — a passing
    cloud must NOT cycle the roof. Driven deterministically by scripting
    _read_safety once the roof is closed: [safe, UNSAFE, safe, safe] with
    resume=2 must reopen only on the 4th scripted read (the interposed unsafe
    delayed it), never the 3rd (which is what a missing reset would do)."""
    dome = sim_hub.devices.get("dome")
    events = _spy_shutter(dome)
    set_safety(temp_store, enabled=True, on_unsafe="pause", unsafe_consecutive=1,
               resume_safe_consecutive=2, max_pause_min=0, min_alt_deg=0.0,
               close_dome_on_unsafe=True, reopen_dome_when_safe=True)
    force_cached_unsafe(sim_hub, "cloud sensor")

    engine = SequenceEngine(sim_hub)
    engine.start(light_plan())
    # Wait until the roof has closed and we are in the wait-for-safe loop.
    assert await wait_for(lambda: "close" in events), engine.state

    # Now drive the safety readings deterministically. A single SAFE then UNSAFE
    # must reset the streak, so the 2 consecutive safes required come only from
    # the LAST two scripted reads.
    SAFE = SafetyReading(is_safe=True, source="Sim Safety Monitor")
    UNSAFE = SafetyReading(is_safe=False, reason="passing cloud",
                           source="Sim Safety Monitor")
    script = [SAFE, UNSAFE, SAFE, SAFE]
    st = {"i": 0}

    async def scripted_read():
        i = st["i"]
        st["i"] += 1
        return script[i] if i < len(script) else SAFE

    reopen_at = {}
    spy_open = dome.open_shutter           # the _spy_shutter wrapper (records "open")

    async def counting_open():
        reopen_at["reads"] = st["i"]       # scripted reads consumed at reopen time
        await spy_open()

    dome.open_shutter = counting_open      # wrap the spy: count reads, then record "open"
    engine._read_safety = scripted_read

    assert await wait_for(lambda: "reads" in reopen_at, timeout=20), engine.state
    # The interposed UNSAFE reset the streak → reopen only after the 4th scripted
    # read (2 consecutive safes AFTER the reset). A missing reset would reopen at 3.
    assert reopen_at["reads"] >= 4, reopen_at
    assert await wait_for(lambda: engine.state.get("state") == "complete",
                          timeout=40), engine.state
