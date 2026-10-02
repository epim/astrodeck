# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#22: the Poseidon-M PRO's hot-side fan (config 21) can be read and set.

During the 2026-09-12 cooler failure, ruling the fan out took poking config
ids from a script. Hot-side heat rejection is the dominant TEC failure mode on
this camera, and the fan is the only part of it software can control. So it
is read from the camera and published in status (never a remembered value:
a server restart would otherwise show whatever was last written), and it can
be set through one route, bounded 0-100 at the boundary.

Hardware: config 21 and its default of 70 were OBSERVED on the rig's body
(issue #22). These cases run against the adapter tests' fake SDK; the first
real read on the rig is the check that the id is right.

MUTATIONS RUN, and what each printed:

  M1, POA_FAN_POWER = 20 (the dew heater's id, one off). 6 failed, led by
  test_the_id_is_config_21; the others because the write landed on config 20
  and every read came from it.

  M2, declare has_fan_control=True on an UNCOOLED body. 1 failed:
  test_an_uncooled_body_has_no_fan.

  M3, in the hub's status block, publish `fan_power` as 0 when the read is
  unknown. 1 failed: test_the_hub_omits_an_unknown_fan.
"""
from __future__ import annotations

import dataclasses

import pytest

from astrodeck.devices.base import DeviceError
from astrodeck.devices.cameras.engine import NativeCamera
from astrodeck.devices.cameras.player_one import PlayerOneAdapter
from astrodeck.devices.cameras.player_one_sdk import POA_FAN_POWER

from test_player_one_adapter import FakePoaSdk  # rootdir-relative


class _FanSdk(FakePoaSdk):
    def __init__(self, *a, cooled=True, fan=70, **k):
        super().__init__(*a, **k)
        self._cooled, self._cfg = cooled, {21: fan}

    def get_properties(self, i):
        return dataclasses.replace(super().get_properties(i), is_cooled=self._cooled)

    def set_config(self, cid, k, v, is_auto=False):
        super().set_config(cid, k, v, is_auto)
        self._cfg[k] = v

    def get_config(self, cid, k):
        if k in self._cfg:
            if self._cfg[k] is None:
                raise RuntimeError("CONF_CANNOT_READ")
            return self._cfg[k]
        return super().get_config(cid, k)


def test_the_id_is_config_21():
    """The number observed on the rig's body. Everything below rides on it."""
    assert POA_FAN_POWER == 21


async def test_the_fan_is_read_from_the_camera():
    cam = NativeCamera(PlayerOneAdapter(sdk=_FanSdk(8, 6, fan=70)))
    await cam.connect()
    assert cam.has_fan_control is True
    assert await cam.get_fan_power() == 70


async def test_the_fan_is_set_on_config_21():
    sdk = _FanSdk(8, 6)
    cam = NativeCamera(PlayerOneAdapter(sdk=sdk))
    await cam.connect()
    await cam.set_fan_power(100)
    assert "cfg:21=100" in sdk.calls, sdk.calls
    assert await cam.get_fan_power() == 100


async def test_out_of_range_is_refused_before_the_camera_sees_it():
    sdk = _FanSdk(8, 6)
    cam = NativeCamera(PlayerOneAdapter(sdk=sdk))
    await cam.connect()
    with pytest.raises(DeviceError, match="0-100"):
        await cam.set_fan_power(150)
    assert not any(c.startswith("cfg:21=") for c in sdk.calls)


async def test_an_uncooled_body_has_no_fan():
    cam = NativeCamera(PlayerOneAdapter(sdk=_FanSdk(8, 6, cooled=False)))
    await cam.connect()
    assert cam.has_fan_control is False
    assert await cam.get_fan_power() is None
    with pytest.raises(DeviceError):
        await cam.set_fan_power(50)


async def test_an_unreadable_fan_is_absent_not_zero():
    """None is UNKNOWN. A status that turned a refused read into 0 would say
    the fan is OFF during the one incident this exists to diagnose."""
    cam = NativeCamera(PlayerOneAdapter(sdk=_FanSdk(8, 6, fan=None)))
    await cam.connect()
    assert await cam.get_fan_power() is None


def test_the_route_bounds_the_value():
    from pydantic import ValidationError
    from astrodeck.api.app import FanBody
    assert FanBody(power=0).power == 0 and FanBody(power=100).power == 100
    for bad in (-1, 101):
        with pytest.raises(ValidationError):
            FanBody(power=bad)


# ---------------------------------------------------------------- hub status

async def _status_camera(cam) -> dict:
    from astrodeck.hub import Hub
    hub = Hub()
    await cam.connect()
    hub.devices["camera"] = cam
    try:
        status = await hub.poll_status()
    finally:
        await hub.disconnect_all()
    assert "camera" in status, "premise: the camera block was built"
    return status["camera"]


async def test_the_hub_publishes_the_fan():
    block = await _status_camera(NativeCamera(PlayerOneAdapter(sdk=_FanSdk(8, 6, fan=70))))
    assert block["has_fan_control"] is True
    assert block["fan_power"] == 70


async def test_the_hub_omits_an_unknown_fan():
    block = await _status_camera(NativeCamera(PlayerOneAdapter(sdk=_FanSdk(8, 6, fan=None))))
    assert block["has_fan_control"] is True, "premise: the body has a fan"
    assert "fan_power" not in block, f"an unreadable fan was published: {block.get('fan_power')}"
