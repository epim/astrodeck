"""PRO-4 Task 2 — ``SimDome`` + ``build_sim_rig`` wiring (sim.py).

The self-checking collision model is the sim half of the correctness contract:
``close_shutter`` must RAISE (and fault the shutter) when the mount is unparked,
and close cleanly once parked. It stays byte-identical for every existing sim
test that never touches the dome (covered by the wider regression gate).
"""
import pytest

from astrodeck.devices.base import DeviceError, Dome, DomeShutterState
from astrodeck.devices.sim import SimDome, build_sim_rig


def test_sim_rig_includes_dome():
    rig = build_sim_rig()
    d = rig["dome"]
    assert isinstance(d, SimDome) and isinstance(d, Dome)


@pytest.mark.asyncio
async def test_open_then_close_after_park_reaches_closed():
    rig = build_sim_rig()
    d = rig["dome"]
    await d.connect()
    assert await d.shutter_state() is DomeShutterState.OPEN
    rig["_rig"].parked = True
    await d.close_shutter()
    assert await d.shutter_state() is DomeShutterState.CLOSED
    # re-open works too (ordering byte-check: open leaves OPEN).
    await d.open_shutter()
    assert await d.shutter_state() is DomeShutterState.OPEN


@pytest.mark.asyncio
async def test_close_while_unparked_raises_and_faults():
    rig = build_sim_rig()
    d = rig["dome"]
    await d.connect()
    assert rig["_rig"].parked is False
    with pytest.raises(DeviceError):
        await d.close_shutter()
    # the collision faults the shutter — it must NOT report CLOSED.
    assert await d.shutter_state() is DomeShutterState.ERROR


@pytest.mark.asyncio
async def test_derived_accessors_and_slaved_raise():
    rig = build_sim_rig()
    d = rig["dome"]
    await d.connect()
    assert await d.is_open() is True
    assert await d.is_closed() is False
    rig["_rig"].parked = True
    await d.close_shutter()
    assert await d.is_closed() is True
    assert await d.is_open() is False
    with pytest.raises(DeviceError):
        await d.set_slaved(True)
