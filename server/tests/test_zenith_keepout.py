"""A ceiling, not just a floor.

Every altitude limit in the codebase was a MINIMUM. But a strain-wave mount with
a long imaging train reaches its own tripod legs near the zenith while the
optics are still on open sky — observed on the AM5N 2026-07-30, with the scope
resting against a leg and no guard having an opinion about it.

The confusion that hid this: a no-go wedge's field is called ``alt_max`` and is
a FLOOR ("inside this azimuth range, stay ABOVE"). It reads like a ceiling and
is the opposite.
"""
import pytest

import astrodeck.config as config_mod
import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import AppConfig, ConfigStore, SafetyConfig, Site
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, Target
from astrodeck.sequence.engine import SafetyAbort
from astrodeck.sequence.schedule import (
    NO_CEILING_DEG, effective_ceiling, effective_floor, nogo_floor,
)


def test_unset_means_no_ceiling():
    """Default must leave existing rigs untouched."""
    assert effective_ceiling(None) == NO_CEILING_DEG == 90.0


def test_a_real_limit_is_honoured():
    assert effective_ceiling(80.0) == 80.0


def test_a_nonsense_value_never_silently_disarms_the_guard():
    """Junk must not read as "no limit" — a user who typed something wrong
    should get a conservative answer, not an unguarded mount."""
    assert effective_ceiling("not a number") == NO_CEILING_DEG
    assert effective_ceiling(None) == NO_CEILING_DEG


def test_a_value_past_the_zenith_clamps_instead_of_disabling():
    assert effective_ceiling(120.0) == 90.0


def test_a_negative_value_clamps_to_zero_rather_than_wrapping():
    """0 means "nothing is reachable", which is safe and obvious. A wrap to a
    large number would silently disable the keep-out."""
    assert effective_ceiling(-5.0) == 0.0


def test_the_wedge_field_named_alt_max_is_still_a_FLOOR():
    """Pinned so the naming cannot quietly change meaning under someone. At az
    180 inside the wedge the mount must stay ABOVE 40, i.e. 40 is a minimum."""
    wedge = [{"az_min": 170.0, "az_max": 190.0, "alt_max": 40.0}]
    assert nogo_floor(wedge, 180.0) == 40.0
    assert nogo_floor(wedge, 0.0) == 0.0
    assert effective_floor(0.0, None, 180.0, wedge) == 40.0


def test_floor_and_ceiling_are_independent():
    """They constrain opposite ends; a rig can legitimately have both (a tree
    line at 20 and a tripod at 80)."""
    assert effective_floor(20.0, None, 90.0, None) == 20.0
    assert effective_ceiling(80.0) == 80.0


@pytest.mark.parametrize("alt,expected_ok", [
    (79.0, True), (80.0, True), (80.1, False), (89.0, False),
])
def test_the_gate_is_strictly_above(alt, expected_ok):
    """Exactly at the limit is allowed; past it is not. An off-by-one here is a
    mount against a tripod leg."""
    assert (alt <= effective_ceiling(80.0)) is expected_ok


# ------------------------------------------------------------------- the WIRING
#
# Everything above tests the pure function, and the pure function was always
# right. The keep-out still did not run: ``_enforce_mount_floor`` returned early
# whenever no FLOOR was configured, and the ceiling check sits after that return.
# So a rig with min_alt_deg = 0 — a perfectly ordinary setting, and the one that
# says "I have no tree line" — had the zenith keep-out silently disarmed while
# the Settings panel went on promising it. A guard nothing calls is not a guard.

@pytest.fixture
def _engine(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Test", latitude=40.0, longitude=-74.0,
                        is_default=False))
    for mod in (config_mod, hub_module, engine_mod):
        monkeypatch.setattr(mod, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    h = Hub()
    yield SequenceEngine(h), h


def _zenith_target(hub) -> Target:
    """A target transiting the zenith right now (dec == site latitude, RA at the
    local meridian), so its altitude is ~90°."""
    from astrodeck.catalog.coords import lst_hours
    return Target(name="Overhead", steps=[],
                  ra_hours=lst_hours(hub.site["longitude"]) % 24.0,
                  dec_deg=hub.site["latitude"])


async def test_the_ceiling_is_enforced_with_no_floor_configured(_engine):
    """THE defect: min_alt_deg = 0 must not disarm the zenith keep-out."""
    engine, hub = _engine
    await hub.connect_sim()
    try:
        engine._cfg = AppConfig(safety=SafetyConfig(
            enabled=True, min_alt_deg=0.0, max_alt_deg=80.0))
        with pytest.raises(SafetyAbort, match="zenith keep-out"):
            await engine._enforce_mount_floor(projected=True,
                                              target=_zenith_target(hub))
    finally:
        await hub.disconnect_all()


async def test_the_ceiling_is_enforced_with_a_floor_configured(_engine):
    """The path that DID work stays working — the fix must not trade one end of
    the sky for the other."""
    engine, hub = _engine
    await hub.connect_sim()
    try:
        engine._cfg = AppConfig(safety=SafetyConfig(
            enabled=True, min_alt_deg=20.0, max_alt_deg=80.0))
        with pytest.raises(SafetyAbort, match="zenith keep-out"):
            await engine._enforce_mount_floor(projected=True,
                                              target=_zenith_target(hub))
    finally:
        await hub.disconnect_all()


async def test_no_limits_at_all_still_slews(_engine):
    """Neither end configured is the DEFAULT rig, and it must stay unguarded —
    otherwise the fix turns into an upgrade-day surprise that refuses targets
    the user has been imaging for months."""
    engine, hub = _engine
    await hub.connect_sim()
    try:
        engine._cfg = AppConfig(safety=SafetyConfig(
            enabled=True, min_alt_deg=0.0, max_alt_deg=90.0))
        await engine._enforce_mount_floor(projected=True,
                                          target=_zenith_target(hub))
    finally:
        await hub.disconnect_all()


async def test_the_mount_limits_survive_the_safety_toggle(_engine):
    """A WEATHER toggle must not disarm a COLLISION guard.

    ``_safety_gate`` returned early when ``safety.enabled`` or
    ``plan.safety_check`` was off — and the mount-limit call sits after that
    return, under a comment claiming the floor 'is enforced on every slew even
    with NO safety device'. So unticking "Safety check" on a plan, whose tooltip
    said only "Off runs without the safety abort", also silently disabled the
    pier-collision guard and the zenith keep-out. Nothing about "I have no cloud
    sensor" implies "my tripod moved".

    Safe to enforce unconditionally because every one of these limits is INERT
    until explicitly configured: min_alt_deg 0, max_alt_deg 90, horizon None,
    nogo None, enforce_pier_limits False. A rig that never set them sees no
    change; a rig that set them gets what it asked for."""
    from astrodeck.sequence import SequencePlan

    engine, hub = _engine
    await hub.connect_sim()
    try:
        engine._cfg = AppConfig(safety=SafetyConfig(
            enabled=False, min_alt_deg=0.0, max_alt_deg=80.0))
        engine.plan = SequencePlan(name="p", targets=[], safety_check=False)
        with pytest.raises(SafetyAbort, match="zenith keep-out"):
            await engine._safety_gate(context="slew",
                                      target=_zenith_target(hub))
    finally:
        await hub.disconnect_all()
