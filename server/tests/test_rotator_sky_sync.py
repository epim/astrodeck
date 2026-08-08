"""#168 — sync the rotator against the sky WITHOUT moving it (2026-08-07 22:02).

The sky↔mechanical offset always existed (``Rotator.sync_offset_deg``) and
``rotate_to_pa`` always established it — but only ever inside a rotation. The
four rotator routes were move / halt / reverse / rotate-to-pa, so the only way
to learn the current angle was to command a rotation you might not want, and
the offset was lost on reconnect with no way to re-establish it.

The claim pinned here: a sync measures, records and MOVES NOTHING.
"""
from __future__ import annotations

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
from astrodeck.devices.base import DeviceError

pytestmark = pytest.mark.asyncio


async def test_a_sync_records_the_offset_and_moves_nothing(sim_hub):
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 20.0
    rig.rotator_mech_deg = 35.0
    before = rig.rotator_mech_deg
    rot = sim_hub.require("rotator")

    out = await sim_hub.sync_rotator_to_sky(exposure_s=0.05)

    assert out["synced"] is True
    assert rig.rotator_mech_deg == pytest.approx(before), (
        "a sync must never move the rotator — that is the whole point")
    assert rot.synced is True
    # offset is mechanical − sky, and the sim's truth is mech + pa_offset
    assert out["mechanical_deg"] == pytest.approx(before, abs=0.5)
    assert rot.sync_offset_deg == pytest.approx(out["offset_deg"], abs=1e-6)


async def test_the_measured_angle_is_the_skys(sim_hub):
    """The number it records is the SOLVED position angle, not the mechanical
    reading — that difference is the entire content of a sync."""
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 40.0
    rig.rotator_mech_deg = 10.0
    out = await sim_hub.sync_rotator_to_sky(exposure_s=0.05)
    truth = (rig.rotator_mech_deg + rig.rotator_pa_offset_deg) % 360.0
    assert abs((out["pa_deg"] - truth + 180.0) % 360.0 - 180.0) <= 1.5, (
        out["pa_deg"], truth)


async def test_a_rig_without_a_rotator_refuses_clearly(sim_hub):
    sim_hub.devices.pop("rotator", None)
    with pytest.raises(DeviceError):
        await sim_hub.sync_rotator_to_sky(exposure_s=0.05)


async def test_a_failed_solve_refuses_rather_than_syncing_to_nothing(
        sim_hub, monkeypatch):
    """A rotator that quietly stays unsynced points every later framing wrong,
    so a failed solve must raise, not return a cheerful no-op."""
    class _NoSolve:
        name = "broken"
        async def solve(self, path, **kw):
            class _R:
                success = False
                message = "no stars"
            return _R()

    import astrodeck.providers as _pv
    monkeypatch.setattr(_pv, "pick_solver", lambda hub: _NoSolve())
    rot = sim_hub.require("rotator")
    was = rot.sync_offset_deg
    with pytest.raises(DeviceError, match="plate solve failed"):
        await sim_hub.sync_rotator_to_sky(exposure_s=0.05)
    assert rot.sync_offset_deg == was, "a failed solve must not move the offset"


async def test_the_route_exists_and_is_capability_gated():
    """The gap was the ROUTE, not the machinery — so the route's existence is
    the thing worth asserting."""
    import inspect

    from astrodeck.api import app as app_mod
    src = inspect.getsource(app_mod)
    assert "/api/rotator/sync-to-sky" in src
    i = src.index("/api/rotator/sync-to-sky")
    assert "CAP_CONTROL_CAPTURE" in src[i - 200:i + 400]
