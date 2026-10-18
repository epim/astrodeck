# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1084: the native guider must not mirror a fresh calibration under the
default, and says so when the pier read changed during the walk (#1071).

``_maybe_flip_for_pier`` used to mirror whenever the calibration's pier stamp
differed from a live read, ignoring ``recalibrate_after_pier_change``. A FRESH
calibration is stamped when its walk STARTS and the walk runs minutes, so a
late change of the mount's pier read turned a correct calibration into a
runaway. These tests drive the real ``start_guiding`` -> ``_calibrate`` path
on the sim rig with the pier report faked (reusing the GN-01 test fakes).
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest
from astrodeck.devices.base import DeviceError
from astrodeck.providers import NATIVE_AVAILABLE

from test_native_guider_pier_change import (  # noqa: F401  (fixture re-export)
    _guider,
    _isolated_config_dir,
    _Logs,
)

pytestmark = [
    pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent"),
]

_WARN = "pier side read changed during the calibration walk"


def _flip_pier_after_first_pulse(tel, fake, to: str) -> None:
    """The mount's pier read changes once the walk is under way (after the
    walk-start stamp, before the walk ends)."""
    real = tel.pulse_guide

    async def _pulse(*a, **k):
        fake.side = to
        return await real(*a, **k)

    tel.pulse_guide = _pulse


@pytest.mark.asyncio
async def test_default_does_not_mirror_and_warns_once(monkeypatch):
    logs = _Logs(monkeypatch)
    g, tel, fake = await _guider("w2e", pier="west")
    _flip_pier_after_first_pulse(tel, fake, "east")

    await asyncio.wait_for(g.start_guiding(), timeout=120.0)

    assert not logs.has("flipped calibration"), logs.matching("flipped")
    warns = logs.matching(_WARN)
    assert len(warns) == 1, warns
    assert "west at the start" in warns[0] and "east at the end" in warns[0]
    await g.stop_guiding()
    await g.disconnect()


@pytest.mark.asyncio
async def test_setting_off_still_mirrors(monkeypatch):
    logs = _Logs(monkeypatch)
    g, tel, fake = await _guider(
        "off", pier="west", cfg={"recalibrate_after_pier_change": False})
    _flip_pier_after_first_pulse(tel, fake, "east")

    # The sim mount does not really flip, so the mirrored calibration fails
    # its probation (#848) and the start raises; the mirror line is what
    # this test is about and is logged before that.
    with contextlib.suppress(DeviceError):
        await asyncio.wait_for(g.start_guiding(), timeout=120.0)

    assert logs.has("mount pier side changed (west->east) since calibration; "
                    "flipped calibration"), logs.lines[-12:]
    with contextlib.suppress(Exception):
        await g.stop_guiding()
    await g.disconnect()


@pytest.mark.asyncio
async def test_unchanged_pier_read_neither_warns_nor_mirrors(monkeypatch):
    logs = _Logs(monkeypatch)
    g, _tel, _fake = await _guider("same", pier="west")

    await asyncio.wait_for(g.start_guiding(), timeout=120.0)

    assert not logs.has(_WARN)
    assert not logs.has("flipped calibration")
    await g.stop_guiding()
    await g.disconnect()
