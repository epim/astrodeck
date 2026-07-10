"""Shared sim_hub fixture: fresh Hub on the sim rig, isolated captures dir,
isolated rotator config, and a forced SimSolver (no real ASTAP pickup).
Used by test_rotate_to_pa.py and test_goto_rotation.py."""
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
