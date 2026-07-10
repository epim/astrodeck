"""Rotator ABC sync math + SimRotator behavior + SimSolver rotation truth."""
import asyncio

import pytest

from astrodeck.devices.sim import SimRig, SimRotator, build_sim_rig
from astrodeck.solve.simsolver import SimSolver


@pytest.fixture()
def rig():
    return SimRig()


def test_sim_rig_has_rotator_fields_defaulting_inert(rig):
    assert rig.rotator_mech_deg == 0.0
    assert rig.rotator_pa_offset_deg == 0.0   # 0.0 keeps existing sim tests identical


def test_build_sim_rig_includes_rotator():
    devices = build_sim_rig()
    assert isinstance(devices["rotator"], SimRotator)


@pytest.mark.asyncio
async def test_unsynced_rotator_sky_equals_mechanical(rig):
    rot = SimRotator(rig)
    await rot.connect()
    rig.rotator_mech_deg = 78.5
    assert rot.synced is False
    assert await rot.get_position() == pytest.approx(78.5)


@pytest.mark.asyncio
async def test_sync_sets_offset_and_sky_moves_land(rig):
    rot = SimRotator(rig)
    await rot.connect()
    rig.rotator_mech_deg = 100.0
    await rot.sync(70.0)                       # offset = 100 - 70 = 30
    assert rot.synced is True
    assert rot.sync_offset_deg == pytest.approx(30.0)
    assert await rot.get_position() == pytest.approx(70.0)
    await rot.move_to(120.0)                   # mech target = 120 + 30 = 150
    assert rig.rotator_mech_deg == pytest.approx(150.0)
    assert await rot.get_position() == pytest.approx(120.0)


@pytest.mark.asyncio
async def test_halt_stops_a_move_short(rig):
    rot = SimRotator(rig)
    await rot.connect()
    task = asyncio.create_task(rot.move_mechanical(180.0))
    await asyncio.sleep(0.05)                  # let it start
    await rot.halt()
    await task
    assert rig.rotator_mech_deg < 180.0        # stopped short of the target


@pytest.mark.asyncio
async def test_sim_solver_reports_rotator_truth(rig):
    rig.rotator_mech_deg = 50.0
    rig.rotator_pa_offset_deg = 30.0           # tests OPT IN to the clock offset
    solver = SimSolver(rig, mode=None)
    result = await solver.solve(None)
    assert result.success
    assert result.rotation_deg == pytest.approx(80.0)


@pytest.mark.asyncio
async def test_sim_solver_rotation_zero_when_defaults(rig):
    # default rig (mech 0, offset 0) → rotation 0.0, byte-identical to before
    solver = SimSolver(rig, mode=None)
    result = await solver.solve(None)
    assert result.success
    assert result.rotation_deg == 0.0
