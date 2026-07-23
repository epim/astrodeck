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


def _logged(substr: str) -> bool:
    # log_history entries are {"type":"log","data":{level,message,source},"ts":..}.
    return any(substr in (e.get("data") or {}).get("message", "")
               for e in bus.log_history)


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


async def test_park_fails_refuses_close_and_pages(sim_hub, temp_store):
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
    assert _logged("AUTOMATED ROOF CLOSE FAILED") or _logged("REFUSED"), \
        "a refused auto-close must page via an error log"
