"""``escalation.reconnect_resume`` — the setting that was read by nothing.

Before 2026-08-06 the whole feature was a claim nothing kept, in the project's
dominant shape: ``reconnect_resume`` and ``reconnect_retries`` were stored,
echoed by ``/api/config``, and rendered as a working Settings toggle labelled
"Reconnect and resume after a dropout" — while no production code read either
one. ``Hub.reconnect_role`` had no caller. The ``_last_connect`` replay map was
maintained at three connect sites purely to feed a method nobody called. And
``reconnect_role`` only ever handled Alpaca, so even a caller would have done
nothing for a native rig, which is what this product is for.

These tests cover both halves — the hub can now re-open a native device, and
the engine actually asks it to — plus the two ways the old shape could quietly
come back: a gate that runs when the setting is off, and a "reconnect" that
reports success against a device that is still down.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import (AppConfig, ConfigStore, EscalationConfig, SafetyConfig,
                              Site)
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.engine import SafetyAbort

pytestmark = pytest.mark.asyncio


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Test", latitude=45.0, longitude=-122.0,
                        is_default=False))
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(engine_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(engine_mod, "RECONNECT_BACKOFF_S", 0.01)
    return store


@pytest.fixture
async def sim_hub(temp_store, monkeypatch):
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _plan(**overrides) -> SequencePlan:
    defaults = dict(
        name="reconnect", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(name="Darks", ra_hours=0, dec_deg=0, calibration=True,
                        autofocus_first=False,
                        steps=[ExposureStep(exposure_s=0.05, count=2,
                                            frame_type="Dark")])])
    return SequencePlan(**(defaults | overrides))


def _engine(hub, *, resume: bool, retries: int = 1) -> SequenceEngine:
    eng = SequenceEngine(hub)
    eng._cfg = AppConfig(
        escalation=EscalationConfig(reconnect_resume=resume,
                                    reconnect_retries=retries),
        safety=SafetyConfig(enabled=False))
    eng.plan = _plan()
    return eng


# --------------------------------------------------------------- the hub half

async def test_a_native_device_reopens_itself(sim_hub):
    """The branch that did not exist. ``reconnect_role`` returned False for
    anything that was not Alpaca, so on a native rig the answer was always "no"."""
    cam = sim_hub.devices["camera"]
    await cam.disconnect()
    assert cam.connected is False

    assert await sim_hub.reconnect_role("camera") is True
    assert cam.connected is True


async def test_a_role_that_was_never_connected_is_not_invented(sim_hub):
    """No replay record means nothing to replay — not a fabricated success."""
    sim_hub._last_connect.pop("camera", None)
    assert await sim_hub.reconnect_role("camera") is False


async def test_a_reconnect_that_does_not_take_reports_failure(sim_hub, monkeypatch):
    """A driver that returns without raising and without connecting has NOT
    reconnected. Reporting True would let the run carry straight on into the
    next exposure against a dead handle — the failure this whole path exists to
    prevent, arriving one frame later and harder to read."""
    cam = sim_hub.devices["camera"]
    await cam.disconnect()

    async def connect_but_stay_down():
        return None
    monkeypatch.setattr(cam, "connect", connect_but_stay_down)

    assert await sim_hub.reconnect_role("camera") is False


# ------------------------------------------------------------ the engine half

async def test_the_gate_is_inert_when_the_setting_is_off(sim_hub, monkeypatch):
    """Off is the default, and off must cost nothing and change nothing — the
    abort path stays exactly as it was for anyone who has not asked for this."""
    tried: list[str] = []
    monkeypatch.setattr(sim_hub, "reconnect_role",
                        lambda role: tried.append(role) or _true())
    eng = _engine(sim_hub, resume=False)
    await sim_hub.devices["camera"].disconnect()

    await asyncio.wait_for(eng._reconnect_gate(), timeout=5)
    assert tried == []


async def test_the_gate_heals_a_dropped_camera_and_the_run_continues(sim_hub):
    eng = _engine(sim_hub, resume=True, retries=3)
    cam = sim_hub.devices["camera"]
    await cam.disconnect()

    await asyncio.wait_for(eng._reconnect_gate(), timeout=5)
    assert cam.connected is True


async def test_the_gate_retries_up_to_the_configured_count(sim_hub, monkeypatch):
    attempts = {"n": 0}

    async def flaky(role):
        attempts["n"] += 1
        return attempts["n"] >= 3        # succeeds on the third try
    monkeypatch.setattr(sim_hub, "reconnect_role", flaky)

    eng = _engine(sim_hub, resume=True, retries=5)
    await sim_hub.devices["camera"].disconnect()
    await asyncio.wait_for(eng._reconnect_gate(), timeout=5)
    assert attempts["n"] == 3, "stops as soon as it works"


async def test_giving_up_aborts_through_the_normal_park_and_warm_teardown(
        sim_hub, monkeypatch):
    """Out of attempts is not a new abort path: SafetyAbort is what parks and
    warms, and the run cannot shoot without the camera."""
    async def never(role):
        return False
    monkeypatch.setattr(sim_hub, "reconnect_role", never)

    eng = _engine(sim_hub, resume=True, retries=2)
    await sim_hub.devices["camera"].disconnect()

    with pytest.raises(SafetyAbort, match="camera dropped out"):
        await asyncio.wait_for(eng._reconnect_gate(), timeout=5)


async def test_only_the_roles_the_plan_actually_needs_are_healed(sim_hub, monkeypatch):
    """A rotator that drops out of a plan with no rotation angle must not end the
    night, and attempts against a device nothing will use are delay on the
    critical path."""
    tried: list[str] = []

    async def record(role):
        tried.append(role)
        return True
    monkeypatch.setattr(sim_hub, "reconnect_role", record)

    eng = _engine(sim_hub, resume=True)
    eng.plan = _plan()                      # calibration only: no slew, no filter
    for role in ("camera", "rotator", "focuser", "telescope"):
        dev = sim_hub.devices.get(role)
        if dev is not None:
            await dev.disconnect()

    await asyncio.wait_for(eng._reconnect_gate(), timeout=5)
    assert tried == ["camera"], tried


async def test_a_plan_that_names_a_filter_also_heals_the_wheel(sim_hub, monkeypatch):
    tried: list[str] = []

    async def record(role):
        tried.append(role)
        return True
    monkeypatch.setattr(sim_hub, "reconnect_role", record)

    eng = _engine(sim_hub, resume=True)
    eng.plan = _plan(targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=False,
        autofocus_first=False,
        steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])])
    for role in ("camera", "filterwheel", "telescope"):
        await sim_hub.devices[role].disconnect()

    await asyncio.wait_for(eng._reconnect_gate(), timeout=5)
    assert set(tried) == {"camera", "filterwheel", "telescope"}, tried


def _true():
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    fut.set_result(True)
    return fut
