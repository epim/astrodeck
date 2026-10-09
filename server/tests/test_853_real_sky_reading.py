# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Ruling R2 (#853) decided by the REAL sky reading.

A hold re-centre whose field will not solve twice stops the target when the
reading taken before the hold SAW STARS, and holds for light otherwise. That
flag (``_pre_recovery_saw_stars``) is set by `_sky_closed_before_recovery`,
and test_853_hold_bound_and_unsolved.py replaces that method with a double
that writes the flag itself, so the line that decides R2 in production was
never run by a test. Here the real reading runs: one unsaved frame through
the hub's capture, judged by the real `verdict_from_info`, on the
test_w14_sky_before_recovery.py rig (a simulator hub whose ``mode`` is
"native", ``safety.sky_fallback_hold`` on, the capture scripted, the wheel a
recording double). Only ``goto_and_center`` answers a script.

Every mutant named below was applied to a byte copy of
``sequence/engine.py``, run under the suite's normal command, and the file
restored from the copy with its sha256 checked.
"""
from __future__ import annotations

import pytest

import astrodeck.flows.tonight as tonight_mod
from astrodeck.sequence.engine import (CENTRING_UNSOLVED_TWICE, StopTarget)

from test_w14_sky_before_recovery import (CLEAR, CLEAR_NO_STARS, _rig,
                                          sim_hub)  # noqa: F401

SF = {"centered": False, "error_arcmin": None, "attempts": 1,
      "solve_failed": True}
OK = {"centered": True, "error_arcmin": 0.4, "attempts": 1}
WALKED = "re-centring after the guided field walked"


@pytest.fixture(autouse=True)
def _the_window_is_open(monkeypatch):
    """The light hold bounds itself by the target's own window, computed for
    the hour the suite runs; "unknown" here, so its retries depend on its
    own count and not on the clock."""
    monkeypatch.setattr(tonight_mod, "target_own_window",
                        lambda *a, **kw: None)


def _answers(hub, monkeypatch, rig, answers: list) -> None:
    """``goto_and_center`` answers ``answers`` in order (copies), and each
    call is recorded in the rig's call list as "center"."""
    gotos: list = []

    async def goto(ra, dec, rotation_deg=None, **kw):
        gotos.append((ra, dec))
        rig.calls.append("center")
        return dict(answers[min(len(gotos) - 1, len(answers) - 1)])
    monkeypatch.setattr(hub, "goto_and_center", goto)


async def test_a_reading_that_saw_stars_makes_an_unsolved_re_centre_a_stop(
        sim_hub, monkeypatch, bus_lines):
    """The real reading: a clear frame with 200 stars. The walking hold's
    re-centre then fails to solve twice, so the unsolved field is the
    pointing's fault: the target stops, and the guider is not restarted on
    a field nobody located.

    MUTANT A "the reading never saw stars" (the setter in
    `_sky_closed_before_recovery`, ``self._pre_recovery_saw_stars = (
    isinstance(stars, (int, float)) and not isinstance(stars, bool) and
    stars > 0)``, made ``= False``): RED -
        AssertionError: assert ['probe', 'stop', 'clear', 'center', 'center',
        'center', ...] == ['probe', 'stop', 'clear', 'center', 'center']
    (it holds for light instead and stops with the no-light reason)
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    t = eng.plan.targets[0]
    _answers(sim_hub, monkeypatch, rig, [SF, SF])
    with pytest.raises(StopTarget) as ei:
        await eng._hold_recentre_recalibrate(
            "2 consecutive dither settles failed", t)
    assert rig.calls == ["probe", "stop", "clear", "center", "center"], (
        rig.calls)
    assert str(ei.value) == f"{WALKED}: {CENTRING_UNSOLVED_TWICE}"


async def test_control_a_reading_with_no_stars_holds_for_light(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL. The real reading judged the sky clear but counted no stars:
    two failed solves are not blamed on the pointing. The re-centre holds
    for light, the retry that solves ends it, and guiding restarts.

    MUTANT "the reading always saw stars" (the same setter made ``= True``):
    RED -
        astrodeck.sequence.engine.StopTarget: re-centring after the guided
        field walked: the field did not solve twice, so the pointing is
        unknown
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR_NO_STARS])
    t = eng.plan.targets[0]
    _answers(sim_hub, monkeypatch, rig, [SF, SF, OK])
    await eng._hold_recentre_recalibrate(
        "2 consecutive dither settles failed", t)
    assert rig.calls == ["probe", "stop", "clear", "center", "center",
                         "center", "start"], rig.calls
    held = [m for _l, m, _s in bus_lines if "holding for light" in m]
    assert len(held) == 1, held
