# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The resumed recovery's moved-mount test (#849) after the Oct-08
integration review, findings 1 and 10.

- Finding 1: a target that does not re-centre, resumed on a mount whose
  position is unknown (power-cycled in the pause), ends the RUN without
  moving the mount. A StopTarget would hand the next target's setup a goto
  aimed from that position.
- Finding 10: a rig with no telescope device (a camera plus a guider that
  drives its own mount connection) is not "moved" across a pause, and one
  unreadable pose is read again before it counts as moved.

The harness is test_oct08_pause_stops_guiding.py's: the real Hub on the
simulator, the guider's calls and the mount's pose scripted. The pose
values are fixtures, never printed. Every mutant named below was applied to
a byte backup of sequence/engine.py, run under the suite's normal command,
and the file restored from the backup with its sha256 checked.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.devices.base import DeviceError
from astrodeck.sequence.engine import PositionUnknownStop, StopTarget

from test_oct08_pause_stops_guiding import (  # noqa: F401 (fixture too)
    _Guider, _Pose, _engine, _pause_and_park, _target, sim_hub)


@pytest.mark.asyncio
async def test_a_resume_on_a_mount_whose_position_is_unknown_ends_the_run(
        sim_hub, monkeypatch, bus_lines):
    """The mount was power-cycled in the pause: it now reports its home pole
    and its position is in doubt. The target does not re-centre. The run
    ends without moving the mount, guiding is not restarted, and the line
    gives the safe order with no goto in it.

    MUTANT R1 "moved before unknown" (the ``await self._gate_position_known(
    target, "resuming after the pause")`` in the resumed branch removed):
    RED -
        Failed: DID NOT RAISE <class '...PositionUnknownStop'>
        (StopTarget: the mount is not where it was when the run paused)
    """
    g = _Guider(sim_hub, monkeypatch, active=True)
    pose = _Pose(sim_hub, monkeypatch, ra=1.0, dec=40.0, pier="east")
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    pose.dec = 90.0
    sim_hub.devices["telescope"].mark_position_unknown("a fictional reset")
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    with pytest.raises(PositionUnknownStop):
        await e._maybe_recover_guiding(_target(center=False))
    assert "start" not in g.calls
    said = [m for lvl, m, _s in bus_lines if lvl == "warning"
            and "the run stops without moving the mount" in m]
    assert len(said) == 1, bus_lines
    assert said[0].endswith("(resuming after the pause)"), said
    for word in ("goto", "go to", "slew"):
        assert word not in said[0].lower()


@pytest.mark.asyncio
async def test_a_rig_with_no_telescope_resumes_its_target(sim_hub,
                                                         monkeypatch):
    """Finding 10. No telescope device at either end of the pause: nothing
    AstroDeck controls could have moved, so the target resumes and guiding
    restarts.

    MUTANT P1 "absent is unreadable" (the ``if a == POSE_NO_MOUNT and b ==
    POSE_NO_MOUNT: return False`` in `_pose_moved` removed): RED -
        astrodeck.sequence.engine.StopTarget: the mount is not where it was
        when the run paused
    """
    g = _Guider(sim_hub, monkeypatch, active=True)
    tel = sim_hub.devices.pop("telescope")
    try:
        e = _engine(sim_hub)
        task = await _pause_and_park(e)
        e.resume()
        await asyncio.wait_for(task, timeout=2.0)
        await e._maybe_recover_guiding(_target(center=False))
        assert g.calls == ["stop", "start"]
    finally:
        sim_hub.devices["telescope"] = tel


@pytest.mark.asyncio
async def test_one_unreadable_pose_is_read_again(sim_hub, monkeypatch):
    """Finding 10. The first position read after the resume fails once (a
    NINA ``DeviceError``, an ASIAIR read timeout); the second answers with
    the same pose. The target resumes.

    MUTANT P2 "one read" (`_read_pose_twice`'s ``if pose is None: pose =
    await self._read_pose()`` removed): RED -
        astrodeck.sequence.engine.StopTarget: the mount is not where it was
        when the run paused
    """
    g = _Guider(sim_hub, monkeypatch, active=True)
    pose = _Pose(sim_hub, monkeypatch, ra=1.0, dec=40.0, pier="east")
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    tel = sim_hub.devices["telescope"]
    good = tel.get_position
    fails = [1]

    async def flaky():
        if fails[0]:
            fails[0] -= 1
            raise DeviceError("a fictional transient read failure")
        return await good()
    monkeypatch.setattr(tel, "get_position", flaky)
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    await e._maybe_recover_guiding(_target(center=False))
    assert fails == [0], "premise: the first read failed"
    assert g.calls == ["stop", "start"]
    del pose


@pytest.mark.asyncio
async def test_two_unreadable_poses_still_count_as_moved(sim_hub,
                                                        monkeypatch):
    """CONTROL, the fail-closed half kept (DESIGN-P1): a mount that cannot
    be read twice running is still treated as moved.

    MUTANT P3 "unreadable is unmoved" (``if a is None or b is None:
    return False`` inserted at the top of `_pose_moved`): RED -
        Failed: DID NOT RAISE <class '...StopTarget'>
    """
    _Guider(sim_hub, monkeypatch, active=True)
    _Pose(sim_hub, monkeypatch, ra=1.0, dec=40.0, pier="east")
    e = _engine(sim_hub)
    task = await _pause_and_park(e)
    tel = sim_hub.devices["telescope"]

    async def dead():
        raise DeviceError("a fictional dead link")
    monkeypatch.setattr(tel, "get_position", dead)
    e.resume()
    await asyncio.wait_for(task, timeout=2.0)
    with pytest.raises(StopTarget):
        await e._maybe_recover_guiding(_target(center=False))
