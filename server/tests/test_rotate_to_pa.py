"""rotate_to_pa end-to-end on the sim rig: the loop must DISCOVER the camera's
clock offset via solve+sync and converge — no shortcuts."""
import pytest

import astrodeck.hub as hub_module
from astrodeck.config import RotatorConfig, config_store
from astrodeck.hub import Hub


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    # Mirror tests/test_hub_solve.py's sim-hub fixture: fresh Hub, captures
    # routed to tmp_path so a solve/rotate exposure never touches the real
    # captures dir.
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    # Isolate the rotator config exactly like test_rotator_config.py: repoint
    # the singleton ConfigStore's path at tmp_path and drop its cached cfg so
    # config_store.set_rotator()/cfg() below never touch the real config file.
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    # ASTAP forced absent so providers.pick_solver's "solve" resolution falls
    # through to the SimSolver (no connected device on the sim rig is "real"
    # backend, so the motion guard allows it) instead of picking up a real
    # ASTAP install that happens to be present on the dev/CI box.
    import astrodeck.providers as providers_module
    monkeypatch.setattr(providers_module, "find_astap", lambda: None)
    h = Hub()
    await h.connect_sim()
    # After Task 5 the sim connect path auto-connects a SimRotator into the
    # "rotator" role; assert that rather than connecting one explicitly.
    assert h.devices.get("rotator") is not None
    assert h.devices["rotator"].connected
    yield h
    await h.disconnect_all()
    config_store.set_rotator(RotatorConfig())


@pytest.mark.asyncio
async def test_converges_on_target_pa(sim_hub):
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 30.0        # hidden truth the loop must discover
    rig.rotator_mech_deg = 10.0             # camera PA = 40.0
    result = await sim_hub.rotate_to_pa(120.0)
    assert result["rotated"] is True
    assert result["attempts"] >= 2          # solve → move → verify solve
    truth = (rig.rotator_mech_deg + rig.rotator_pa_offset_deg) % 360.0
    # mod-180 distance to target within default 1.0° tolerance
    d = abs((truth - 120.0 + 90.0) % 180.0 - 90.0)
    assert d <= 1.0
    assert result["adjusted_to"] is None    # FULL range: nothing adjusted


@pytest.mark.asyncio
async def test_full_range_may_take_180_twin(sim_hub):
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 0.0
    rig.rotator_mech_deg = 350.0
    result = await sim_hub.rotate_to_pa(175.0)   # twin at -5° is far closer
    assert result["rotated"] is True
    truth = rig.rotator_mech_deg % 360.0
    d = abs((truth - 175.0 + 90.0) % 180.0 - 90.0)
    assert d <= 1.0
    # the physical move stayed small (took the twin, not the 175° sweep)
    assert abs(((350.0 - truth + 180.0) % 360.0) - 180.0) < 30.0


@pytest.mark.asyncio
async def test_limited_range_reports_adjustment(sim_hub, monkeypatch):
    from astrodeck.config import RotatorConfig, config_store
    config_store.set_rotator(RotatorConfig(range_type="quarter",
                                           range_start_deg=0.0,
                                           tolerance_deg=1.0))
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 0.0
    rig.rotator_mech_deg = 10.0
    result = await sim_hub.rotate_to_pa(100.0)   # QUARTER [0,90): 100 maps to 10
    assert result["rotated"] is True
    assert result["adjusted_to"] is not None     # honesty: framing changed
    assert 0.0 <= rig.rotator_mech_deg % 360.0 < 91.0   # stayed in range (+tol)


@pytest.mark.asyncio
async def test_solve_failure_raises_device_error(sim_hub, monkeypatch):
    from astrodeck.devices.base import DeviceError
    from astrodeck.solve.base import SolveResult

    async def fail_solve(self, fits_path, **kw):
        return SolveResult(False, message="clouds")
    from astrodeck.solve.simsolver import SimSolver
    monkeypatch.setattr(SimSolver, "solve", fail_solve)
    with pytest.raises(DeviceError):
        await sim_hub.rotate_to_pa(120.0)


@pytest.mark.asyncio
async def test_requires_rotator(sim_hub):
    from astrodeck.devices.base import DeviceError
    sim_hub.devices.pop("rotator", None)
    with pytest.raises(DeviceError):
        await sim_hub.rotate_to_pa(120.0)
