"""THE TUBE NOT HITTING THE PIER IS NOT THE ONLY REASON TO FLIP.

`_maybe_meridian_flip` declines the flip when the target's lower culmination
clears the horizon — true and worth saving, because the tube never points down
and the flip costs a re-slew, a re-solve and 180 degrees of field rotation.

But it `return`ed on that basis BEFORE consulting `tel.time_to_meridian_flip()`,
and a ZWO AM5 enforces its own meridian limit whatever the geometry says. On
2026-08-19 the engine declined the flip at 00:49 for NGC 7129 (dec +66.1, lower
culmination 13 degrees up) and the mount stopped tracking at 00:54 — five
minutes later, on its own authority. The night ended there, and the run kept
shooting streaks for an hour because nothing was checking.

Two different questions. Only one was being asked.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence import schedule


def test_the_geometry_shortcut_is_still_right_about_geometry():
    """Not removing it — a circumpolar target really does not need the flip for
    pier-clearance reasons, and taking one costs a re-centre and a field
    rotation mid-stack."""
    assert schedule.flip_unnecessary_over_pole(66.1, 37.35) is True
    assert schedule.flip_unnecessary_over_pole(-5.4, 37.35) is False


def test_a_mount_that_reports_its_own_limit_overrides_the_shortcut():
    """The device's countdown is the mount saying 'I will stop tracking'. That
    outranks our opinion about the tube, because it is not an opinion."""
    assert schedule.flip_forced_by_mount(device_hours=0.08) is True
    assert schedule.flip_forced_by_mount(device_hours=6.0) is False


def test_a_mount_with_no_opinion_leaves_the_shortcut_alone():
    """Most mounts return nothing useful. Absence must not manufacture a flip
    that the geometry says is pure cost."""
    assert schedule.flip_forced_by_mount(device_hours=None) is False
    assert schedule.flip_forced_by_mount(device_hours=-1.0) is False


def test_the_threshold_is_wide_enough_to_act_on():
    """The AM5 stopped five minutes after the meridian. A window narrower than
    that would notice too late to do anything about it."""
    from astrodeck.sequence.schedule import MOUNT_LIMIT_FLIP_WINDOW_H
    assert MOUNT_LIMIT_FLIP_WINDOW_H * 60 >= 10, (
        f"{MOUNT_LIMIT_FLIP_WINDOW_H * 60:.0f} min of warning is not enough to "
        "stop guiding, flip, re-solve and re-centre")
