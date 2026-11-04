# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1066: the rotation warning names the guider in use and fires only for a
guider whose calibration a rotation touches.

Observed 2026-10-16 (0.3.42, the native guider on a guide-scope rig): after
every rotation that moved the camera, `Hub.rotate_to_pa` logged "camera rotated
- guide calibration may be stale; PHD2 will re-calibrate or flip as needed".
The guider was the native one, not PHD2, and a guide-scope rig's guide camera
does not turn with the imaging camera, so nothing was stale. The line fired on
every rotation and promised a recovery no code performs.

A rotation touches the guide calibration only when the guide camera IS the
imaging camera (the native guider's ``shares_the_imaging_sensor``, which the
native backend sets exactly when no separate guide camera is assigned). Then
the warning names that guider and says what is true: the calibration no longer
matches the camera angle and has to be walked again. Every other guider gets
no line.
"""
from __future__ import annotations

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.events import bus
from astrodeck.guide.base import GuideStats


class _Guider:
    """Just enough guider for the hub's rotation warning."""

    connected = True

    def __init__(self, name: str, **attrs) -> None:
        self.name = name
        for key, value in attrs.items():
            setattr(self, key, value)

    def stats(self) -> GuideStats:
        return GuideStats()


@pytest.fixture
def guide_lines(monkeypatch):
    """Every ``guide``-source line the rotation logs, still forwarded."""
    seen: list[tuple[str, str]] = []
    real = bus.log

    def record(level, message, source="hub", **kw):
        if source == "guide":
            seen.append((level, message))
        real(level, message, source, **kw)

    monkeypatch.setattr(bus, "log", record)
    return seen


async def _rotate(hub) -> dict:
    rig = hub.sim_rig
    rig.rotator_pa_offset_deg = 30.0
    rig.rotator_mech_deg = 10.0
    result = await hub.rotate_to_pa(120.0)
    assert result["rotated"] is True, result
    return result


async def test_an_on_axis_guider_is_named_and_told_its_calibration_is_stale(
        sim_hub, guide_lines):
    """The guider shares the imaging sensor, so turning the camera turns it:
    one warning, naming the guider, and never promising PHD2.

    Red before the fix: the line read "PHD2 will re-calibrate or flip as
    needed" whatever the guider was.
    """
    sim_hub.guider = _Guider("AstroDeck native",
                             shares_the_imaging_sensor=True)
    await _rotate(sim_hub)
    lines = [m for lvl, m in guide_lines if lvl == "warning"]
    assert len(lines) == 1, guide_lines
    assert "AstroDeck native" in lines[0], lines
    assert "PHD2" not in lines[0], lines
    assert "calibrat" in lines[0], lines


async def test_a_guide_scope_rig_gets_no_stale_calibration_warning(
        sim_hub, guide_lines):
    """The native guider on its own guide camera: the rotation does not touch
    its calibration, so the guide lane stays quiet.

    Red before the fix: the line fired for any connected guider.
    """
    sim_hub.guider = _Guider("AstroDeck native",
                             shares_the_imaging_sensor=False)
    await _rotate(sim_hub)
    assert [m for lvl, m in guide_lines if lvl == "warning"] == [], guide_lines


async def test_a_guider_that_cannot_say_gets_no_warning_that_names_phd2(
        sim_hub, guide_lines):
    """A guider with no such attribute (the PHD2 bridge, the simulator's):
    nothing says its calibration is touched, so nothing is claimed."""
    sim_hub.guider = _Guider("PHD2")
    await _rotate(sim_hub)
    assert [m for lvl, m in guide_lines if lvl == "warning"] == [], guide_lines
