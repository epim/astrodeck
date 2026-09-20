"""Dusk preparation against fake devices and a controlled night, never a rig."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from astrodeck import config
from astrodeck.config import AppConfig, DuskConfig
from astrodeck import dusk_arm as mod
from astrodeck.dusk_arm import DuskArm


@pytest.fixture
def rig(tmp_path, monkeypatch):
    cfg = AppConfig()
    cfg.dusk = DuskConfig(enabled=True, profile_id="test-rig")
    cfg.cooling.setpoint_c = -10
    cfg.active_profile_id = "test-rig"
    monkeypatch.setattr(config, "config_store", NS(cfg=lambda: cfg))
    monkeypatch.setattr(mod, "observing_night", lambda *args: (100, 2000))
    monkeypatch.setattr(mod.profiles, "get", lambda pid: NS(id=pid, to_rigspec=lambda: "spec"))
    camera = NS(connected=True, can_cool=True, get_temperature=AsyncMock(return_value=-10),
                get_cooler=AsyncMock(return_value={"on": True, "target_c": -10}))
    mount = NS(connected=True)
    hub = NS(site={"is_default": False, "latitude": 45., "longitude": -110.},
             devices={"camera": camera, "telescope": mount}, _connect_lock=asyncio.Lock(),
             busy_lanes=lambda: [], guider=None, cool_camera=AsyncMock(), _warm_task=None)

    async def connect(*args, **kwargs):
        hub.devices.update(camera=camera, telescope=mount)
    hub._connect_rigspec_unlocked = AsyncMock(side_effect=connect)
    engine = NS(running=False, current_safety=AsyncMock(return_value=NS(stale=False, is_safe=True)))
    clock = [200.]
    arm = DuskArm(hub, engine, clock=lambda: clock[0], state_path=tmp_path / "dusk.json")
    hub.dusk_arm = arm
    return NS(cfg=cfg, hub=hub, camera=camera, engine=engine, arm=arm, clock=clock)


@pytest.mark.asyncio
async def test_connects_explicit_imaging_target_and_waits_for_stability(rig):
    rig.hub.devices.clear()
    # The device retained its dawn warm-up target. The command must replace it.
    rig.camera.get_cooler.return_value = {"on": False, "target_c": 8}
    await rig.arm.tick()
    rig.hub._connect_rigspec_unlocked.assert_awaited_once()
    rig.hub.cool_camera.assert_awaited_once_with(-10)
    assert rig.arm.status["state"] == "cooling"
    assert rig.arm.resume_veto() is not None
    rig.camera.get_cooler.return_value = {"on": True, "target_c": -10}
    await rig.arm.tick()
    rig.clock[0] += 119
    await rig.arm.tick()
    assert rig.arm.status["state"] == "cooling"
    rig.clock[0] += 1
    await rig.arm.tick()
    assert rig.arm.status["state"] == "ready"
    assert rig.arm.resume_veto() is None
    await rig.arm.tick()
    assert rig.hub.cool_camera.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["disabled", "default_site", "unknown_site", "day", "polar_day",
                                    "missing_target", "missing_profile", "sequence", "lane", "guider",
                                    "connecting", "different_profile", "partial", "weather"])
async def test_holds_without_touching_hardware(rig, monkeypatch, reason):
    if reason == "disabled": rig.cfg.dusk.enabled = False
    elif reason == "default_site": rig.hub.site["is_default"] = True
    elif reason == "unknown_site": rig.hub.site.pop("latitude")
    elif reason == "day": rig.clock[0] = 50
    elif reason == "polar_day": monkeypatch.setattr(mod, "observing_night", lambda *args: None)
    elif reason == "missing_target": rig.cfg.cooling.setpoint_c = None
    elif reason == "missing_profile": rig.cfg.dusk.profile_id = None
    elif reason == "sequence": rig.engine.running = True
    elif reason == "lane": rig.hub.busy_lanes = lambda: ["capture"]
    elif reason == "guider": rig.hub.guider = NS(running=True)
    elif reason == "connecting": rig.arm.connection_busy = lambda: True
    elif reason == "different_profile": rig.cfg.active_profile_id = "other-rig"
    elif reason == "partial": rig.hub.devices.pop("telescope")
    elif reason == "weather": rig.arm.weather = NS(veto_reason=lambda now: "rain")
    await rig.arm.tick()
    rig.hub.cool_camera.assert_not_awaited()
    rig.hub._connect_rigspec_unlocked.assert_not_awaited()
    if reason == "sequence":
        assert rig.arm.status["state"] == "in_use"
        assert rig.arm.resume_veto() is None
    else:
        assert not rig.arm.path.exists()


@pytest.mark.asyncio
async def test_already_connected_profile_is_not_reconnected(rig):
    await rig.arm.tick()
    rig.hub._connect_rigspec_unlocked.assert_not_awaited()
    rig.hub.cool_camera.assert_awaited_once_with(-10)


@pytest.mark.asyncio
async def test_failed_connection_is_latched_even_after_restart(rig):
    rig.hub.devices.clear()
    rig.hub._connect_rigspec_unlocked.side_effect = RuntimeError("private driver error")
    await rig.arm.tick()
    assert rig.arm.status["state"] == "failed"
    assert "private" not in rig.arm.status["detail"]
    restarted = DuskArm(rig.hub, rig.engine, clock=lambda: rig.clock[0], state_path=rig.arm.path)
    await restarted.tick()
    assert restarted.status["state"] == "interrupted"
    assert rig.hub._connect_rigspec_unlocked.await_count == 1


@pytest.mark.asyncio
async def test_corrupt_latch_fails_closed(rig):
    rig.arm.path.write_text("broken", encoding="utf-8")
    with pytest.raises(mod.DuskStateError):
        await rig.arm.tick()
    rig.hub.cool_camera.assert_not_awaited()


@pytest.mark.asyncio
async def test_cannot_record_attempt_does_not_touch_hardware(rig, monkeypatch):
    def fail(*args, **kwargs): raise OSError("disk full")
    monkeypatch.setattr(mod, "write_json_atomic", fail)
    with pytest.raises(mod.DuskStateError):
        await rig.arm.tick()
    rig.hub.cool_camera.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disconnect", "warm", "cooler_off", "new_target", "busy", "disabled"])
async def test_manual_actions_are_not_undone(rig, change):
    await rig.arm.tick()
    if change == "disconnect": rig.hub.devices.clear()
    elif change == "warm": rig.hub._warm_task = NS(done=lambda: False)
    elif change == "cooler_off": rig.camera.get_cooler.return_value = {"on": False}
    elif change == "new_target": rig.camera.get_cooler.return_value = {"on": True, "target_c": -5}
    elif change == "busy": rig.engine.running = True
    elif change == "disabled": rig.cfg.dusk.enabled = False
    await rig.arm.tick()
    assert rig.arm.status["state"] in ("interrupted", "disabled", "in_use")
    rig.cfg.dusk.enabled = True
    await rig.arm.tick()
    assert rig.hub.cool_camera.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("temperature", [None, float("nan"), 8.0])
async def test_bad_or_warm_sensor_never_reports_ready(rig, temperature):
    await rig.arm.tick()
    rig.camera.get_temperature.return_value = temperature
    await rig.arm.tick()
    rig.clock[0] = rig.arm._deadline + 1
    await rig.arm.tick()
    assert rig.arm.status["state"] == "failed"


@pytest.mark.asyncio
async def test_temperature_excursion_restarts_stability_clock(rig):
    await rig.arm.tick()
    await rig.arm.tick()
    rig.clock[0] += 100
    rig.camera.get_temperature.return_value = -5
    await rig.arm.tick()
    rig.clock[0] += 30
    rig.camera.get_temperature.return_value = -10
    await rig.arm.tick()
    assert rig.arm.status["state"] == "cooling"
    rig.clock[0] += 120
    await rig.arm.tick()
    assert rig.arm.status["state"] == "ready"


@pytest.mark.asyncio
async def test_new_night_allows_new_attempt(rig, monkeypatch):
    await rig.arm.tick()
    rig.hub.devices.clear()
    await rig.arm.tick()
    rig.clock[0] = 3000
    monkeypatch.setattr(mod, "observing_night", lambda *args: (2500, 4000))
    await rig.arm.tick()
    assert rig.hub.cool_camera.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reading", [None, NS(stale=True, is_safe=True), NS(stale=False, is_safe=False)])
async def test_safety_monitor_blocks_cooling(rig, reading):
    rig.hub.devices["safety"] = NS()
    rig.engine.current_safety.return_value = reading
    await rig.arm.tick()
    assert rig.arm.status["state"] == "failed"
    rig.hub.cool_camera.assert_not_awaited()


@pytest.mark.asyncio
async def test_configuration_change_during_connect_prevents_cooling(rig):
    rig.hub.devices.clear()
    original = rig.hub._connect_rigspec_unlocked.side_effect
    async def connect(*args, **kwargs):
        assert rig.arm.connecting
        await original(*args, **kwargs)
        rig.cfg.dusk.enabled = False
    rig.hub._connect_rigspec_unlocked.side_effect = connect
    await rig.arm.tick()
    rig.hub.cool_camera.assert_not_awaited()
    assert not rig.arm.connecting


@pytest.mark.parametrize("value", [-19, -5, float("nan"), float("inf")])
def test_threshold_validation(value):
    with pytest.raises(ValidationError):
        DuskConfig(sun_alt_deg=value)


def test_old_config_does_not_enable_automation():
    assert AppConfig().dusk.enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, "unreadable"])
async def test_real_guider_interface_is_respected(rig, active):
    probe = AsyncMock(return_value=True)
    if active == "unreadable": probe.side_effect = TimeoutError()
    rig.hub.guider = NS(connected=True, is_active=probe)
    await rig.arm.tick()
    probe.assert_awaited_once()
    rig.hub.cool_camera.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_attempt_too_close_to_dawn(rig):
    rig.clock[0] = 1950
    await rig.arm.tick()
    assert "Dawn is too close" in rig.arm.status["detail"]
    rig.hub.cool_camera.assert_not_awaited()
    assert not rig.arm.path.exists()


@pytest.mark.asyncio
async def test_late_driver_reply_does_not_cool_after_dawn(rig):
    rig.hub.devices.clear()
    original = rig.hub._connect_rigspec_unlocked.side_effect
    async def connect(*args, **kwargs):
        await original(*args, **kwargs)
        rig.clock[0] = 2001
    rig.hub._connect_rigspec_unlocked.side_effect = connect
    await rig.arm.tick()
    assert rig.arm.status["state"] == "interrupted"
    rig.hub.cool_camera.assert_not_awaited()


@pytest.mark.asyncio
async def test_sensor_reply_cannot_hide_disconnect(rig):
    await rig.arm.tick()
    await rig.arm.tick()
    rig.clock[0] += 150
    async def read():
        rig.hub.devices.clear()
        return -10
    rig.camera.get_temperature.side_effect = read
    await rig.arm.tick()
    assert rig.arm.status["state"] == "interrupted"


@pytest.mark.asyncio
async def test_real_sun_window_opens_at_dusk_not_daylight(rig, monkeypatch):
    from datetime import datetime, timezone
    from astrodeck.sequence.schedule import observing_night
    monkeypatch.setattr(mod, "observing_night", observing_night)
    noon = datetime(2026, 9, 19, 19, tzinfo=timezone.utc).timestamp()
    dusk, dawn = observing_night(rig.hub.site, -12, noon)
    assert dusk < dawn
    rig.clock[0] = dusk - 120
    await rig.arm.tick()
    rig.hub.cool_camera.assert_not_awaited()
    rig.clock[0] = dusk + 120
    await rig.arm.tick()
    rig.hub.cool_camera.assert_awaited_once_with(-10)


@pytest.mark.asyncio
async def test_manual_run_takes_over_without_losing_automatic_recovery(rig):
    await rig.arm.tick()
    rig.engine.running = True
    await rig.arm.tick()
    rig.engine.running = False
    await rig.arm.tick()
    assert rig.arm.status["state"] == "in_use"
    assert rig.arm.resume_veto() is None
    restarted = DuskArm(rig.hub, rig.engine, clock=lambda: rig.clock[0], state_path=rig.arm.path)
    await restarted.tick()
    assert restarted.resume_veto() is None
    assert rig.hub.cool_camera.await_count == 1


@pytest.mark.asyncio
async def test_instant_warm_during_safety_read_is_not_undone(rig):
    rig.hub.devices["safety"] = NS()
    async def reading():
        rig.hub._warm_state = {"active": False, "note": "warm complete"}
        return NS(stale=False, is_safe=True)
    rig.engine.current_safety.side_effect = reading
    await rig.arm.tick()
    assert rig.arm.status["state"] == "interrupted"
    rig.hub.cool_camera.assert_not_awaited()
