# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#531 - a Rotate to PA solve must not shoot through the filter the run left.

The night this comes from: 2026-09-29, the first rig mosaic (NGC 1499,
3x2, "Rotate to PA 0") started right after an NGC 7331 run whose last frame was
SII 180 s. Every rotation solve read the bias level ("no light: the optic is
capped, covered or obstructed"), because ``Hub._rotate_to_pa_attempts`` exposed
through whatever the wheel held, while the centring solve 25 s later borrowed L
(#222) and saw the sky. Every panel then deferred on "the rotator did not turn
the camera to the mosaic's angle".

NAMED MUTANT, run in a private scratch copy of server/ (never the shared tree):
  M1 "the rotate path skips the borrow": the borrow/return pair around the
     rotate exposure removed, which is the code as it stood before this fix.
     Observed (1 failed, 2 passed; the other two are controls here):
       AssertionError: the rotate solve exposed through slot 4 (S), not the
       luminance slot: exposures at [4, 4]
"""
import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

WHEEL = ["L", "R", "G", "B", "S", "Ha", "Oiii", "Dark"]
NARROW = [False, False, False, False, True, True, True, False]
OPAQUE = [False, False, False, False, False, False, False, True]


def _flag_the_wheel(hub, slot):
    fw = hub.devices["filterwheel"]
    fw.filter_names = list(WHEEL)
    fw.filter_offsets = [0, -18, 0, -18, -18, -15, 2, 0]
    fw.filter_narrowband = list(NARROW)
    fw.filter_opaque = list(OPAQUE)
    fw.rig.filter_slot = slot
    return fw


def _record_exposure_slots(hub, monkeypatch):
    """Wrap the camera's expose so each exposure records the slot the wheel
    held while its shutter was open."""
    cam = hub.devices["camera"]
    fw = hub.devices["filterwheel"]
    slots: list[int] = []
    real = cam.expose

    async def expose(*a, **kw):
        slots.append(int(fw.rig.filter_slot))
        return await real(*a, **kw)
    monkeypatch.setattr(cam, "expose", expose)
    return slots


@pytest.mark.asyncio
async def test_a_rotation_after_a_narrowband_frame_solves_through_luminance(
        sim_hub, monkeypatch):
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 30.0
    rig.rotator_mech_deg = 10.0
    _flag_the_wheel(sim_hub, 4)                  # SII, as after the 7331 run
    slots = _record_exposure_slots(sim_hub, monkeypatch)
    result = await sim_hub.rotate_to_pa(120.0)
    assert result["rotated"] is True
    assert slots, "the rotation took no exposure"
    assert all(s == 0 for s in slots), (
        f"the rotate solve exposed through slot 4 (S), not the luminance "
        f"slot: exposures at {slots}")


@pytest.mark.asyncio
async def test_the_wheel_is_back_on_the_run_s_filter_afterwards(
        sim_hub, monkeypatch):
    """Symmetric, as #222's borrow is: the engine derives its focuser offset
    delta from the wheel's real position, so a borrow left unreturned leaves
    the focuser one filter's worth of steps out."""
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 30.0
    rig.rotator_mech_deg = 10.0
    fw = _flag_the_wheel(sim_hub, 4)
    _record_exposure_slots(sim_hub, monkeypatch)
    await sim_hub.rotate_to_pa(120.0)
    assert await fw.get_position() == 4, "the wheel was left on the solve slot"


@pytest.mark.asyncio
async def test_control_a_broadband_filter_is_left_where_it_is(
        sim_hub, monkeypatch):
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 30.0
    rig.rotator_mech_deg = 10.0
    _flag_the_wheel(sim_hub, 1)                  # R: no move is owed
    slots = _record_exposure_slots(sim_hub, monkeypatch)
    result = await sim_hub.rotate_to_pa(120.0)
    assert result["rotated"] is True
    assert slots and all(s == 1 for s in slots), slots
