# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Shared sim_hub fixture: fresh Hub on the sim rig, isolated captures dir,
isolated rotator config, and a forced SimSolver (no real ASTAP pickup).
Used by test_rotate_to_pa.py and test_goto_rotation.py."""
import pytest

import astrodeck.hub as hub_module
from astrodeck.config import RotatorConfig, Site, config_store
from astrodeck.hub import Hub

# See the note in the fixture. Not anybody's rig.
_TEST_SITE = Site(name="fixture", latitude=40.0, longitude=-74.0,
                  elevation_m=10.0, is_default=False)


def a_real_site(monkeypatch) -> None:
    """Point the config store's cached site at a real location (#24).

    For the fixtures that do NOT isolate the config store the way `sim_hub`
    below does, and so come up on whatever the box has - which on a clean
    checkout is the 0,0 default with `is_default` True. Five test files build
    their own sim hub that way, and all five compute meridian geometry from it.

    Monkeypatching the fields of the cached `Site` is the idiom those files
    already use for latitude, so this is the same move made complete: a
    latitude alone leaves `is_default` True, and `is_default` is the flag every
    guard added for #24 actually reads.

    Call it BEFORE `Hub()`. 40 N 74 W, and it is not anybody's rig.
    """
    from astrodeck.config import config_store
    site = config_store.cfg().site
    for field, value in (("name", "fixture"), ("latitude", 40.0),
                         ("longitude", -74.0), ("elevation_m", 10.0),
                         ("is_default", False)):
        monkeypatch.setattr(site, field, value)

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
    # A REAL OBSERVING SITE (#24). The fixture repoints the config store at an
    # empty tmp_path above, so without this the hub's site is the 0,0 default
    # with `is_default` True - and every test built on this fixture that touches
    # sky geometry has been computing for the Gulf of Guinea and asserting on
    # the answer. The same shape was found in test_resume_arm.py's fixture,
    # where three of four cases were graded against 0,0 and only one noticed.
    #
    # 40 N 74 W is a mid-northern site with a large longitude offset, chosen so
    # that a latitude/longitude mix-up, a sign error and a missing offset all
    # produce visibly different answers. It is not anybody's rig.
    config_store.set_site(_TEST_SITE)
    h = Hub()
    await h.connect_sim()
    assert not h.site.get("is_default", False), (
        "the sim hub came up on the default site, so anything this fixture "
        "grades about the sky is graded at 0,0")
    # After Task 5 the sim connect path auto-connects a SimRotator into the
    # "rotator" role; assert that rather than connecting one explicitly.
    assert h.devices.get("rotator") is not None
    assert h.devices["rotator"].connected
    yield h
    await h.disconnect_all()
    config_store.set_rotator(RotatorConfig())
