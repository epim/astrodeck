# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#627: the daytime 'cooling left off' sentence must say what was actually
tested, not deny an armed session that exists.

Observed on astrotown after the 0.3.39 deploy, 2026-10-01 06:56 PDT
(captures/logs/2026-09-30.jsonl). Two consecutive lines contradicted each
other:

    06:56:41 warning sequence auto-resume is standing by: ... still owes
             48 frames. It stays armed and starts when the window opens.
    06:56:44 info camera cooling left off after connecting - it is
             daylight and no run is armed or due; ...

``Hub._cooling_restore_allowed`` returns "it is daylight and no run is
armed or due" whenever ``resume_arm.resume_expected_tonight`` answers
None -- which it does BOTH when nothing is armed at all AND when something
IS armed but its window has not opened yet (here, because it is still
daylight). The decision was already right (#557: the camera correctly
stays warm for an armed-but-not-yet-due session); only the sentence
claimed a fact nobody checked.

Reuses the ``rig``/``_Cam``/``SITE``/``DAY_TS`` fixtures from
test_w1_cooling_restore_daylight.py (WP-10, #557) rather than redefining
them, so this stays the same scenario with one more session armed on top.
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, session_store
from test_w1_cooling_restore_daylight import (DAY_TS, SITE, _Cam,  # noqa: F401
                                              rig)

pytestmark = pytest.mark.asyncio


def _light_plan(name: str = "lights-armed-for-tonight") -> SequencePlan:
    """A NON-calibration target, unlike the parent file's
    ``_calibration_plan``: calibration targets make ``resume_arm.
    window_open`` return True at any hour (darks/flats are shot in
    daylight on purpose), which would pass #557's own daylight check and
    never reach the branch this file is about. A light target's window
    depends on ``schedule.dark_enough``, which DAY_TS (local noon) fails --
    the exact "armed, but not due until dusk" shape #627 reports."""
    return SequencePlan(name=name, targets=[Target(
        name="light-frames", ra_hours=5.5, dec_deg=20.0, calibration=False,
        steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)])])


def _arm_light_session(name: str = "lights-armed-for-tonight") -> Session:
    s = Session(name=name, status="dormant", plan=_light_plan(name),
               auto_resume=True)
    session_store.save(s)
    return s


async def test_an_armed_light_session_in_daylight_is_not_denied(rig, bus_lines):
    """The defect itself: a session IS armed for tonight (daylight is the
    only reason it is not due yet), so the log must not say "no run is
    armed or due" -- and should name the session instead.

    MUTANT (verified): ``if armed is not None:`` in
    ``Hub._cooling_restore_allowed`` made ``if armed is not None and
    False:`` (the new branch disabled, falling through to the old
    unconditional sentence) turns this red, verbatim (the console renders
    the degree sign and the dash as a replacement character; written here
    as the route actually sends it):
        AssertionError: a session is armed for tonight, so the log must
        not deny it: 'cooling left off after connecting - it is
        daylight and no run is armed or due; the standing setpoint
        (-10 degC) will be applied when a run starts'
    """
    _arm_light_session()
    cam = rig.devices["camera"]
    assert await rig.restore_cooling(DAY_TS) is False
    assert cam.calls == [], "it commanded a cooler nobody is about to use yet"
    said = [m for lv, m, _s in bus_lines if lv == "info" and "left off" in m]
    assert len(said) == 1, said
    assert "no run is armed" not in said[0], (
        f"a session is armed for tonight, so the log must not deny it: "
        f"{said[0]!r}")
    assert "lights-armed-for-tonight" in said[0], said[0]
    assert "not due" in said[0], said[0]


async def test_truly_nothing_armed_keeps_the_old_true_sentence(rig, bus_lines):
    """Control: with NOTHING armed at all, "no run is armed or due" is
    simply true, and must be unchanged -- this is the branch
    test_w1_cooling_restore_daylight.py's own
    ``test_daylight_with_nothing_due_leaves_the_cooler_off`` already
    covers; repeated here, narrowly, so a future edit to this exact
    sentence is graded against both the true and the false case side by
    side in one file."""
    cam = rig.devices["camera"]
    assert await rig.restore_cooling(DAY_TS) is False
    said = [m for lv, m, _s in bus_lines if lv == "info" and "left off" in m]
    assert len(said) == 1, said
    assert "no run is armed or due" in said[0], said[0]
