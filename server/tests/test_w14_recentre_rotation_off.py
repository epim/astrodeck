# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The three recovery re-centres honour D-05's "rotation is off for the night"
(#648, wave 14 integration of WP-90).

THE GAP. WP-90 made ``_setup_target`` shoot at a fixed angle once the nightly
self-test has MEASURED the camera not following the rotator (the hub's
``_rotation_trusted`` is False), but ``_recentre_after_unguided_focus``,
``_maybe_recover_guiding`` and ``_hold_recentre_recalibrate`` each called
``hub.goto_and_center(..., rotation_deg=target.rotation_deg)`` directly. With
trust False the hub refuses the rotate, ``goto_and_center`` degrades to
``rotation_skipped`` and says so, and the re-centre still asked: one refused
request and one warning at each, on a night that was supposed to be shooting
at a fixed angle. The same three also ignored ruling 9's locked angle (an
unframed target's first-shot angle, which "every later acquisition ...
commands"), a divergence from ``_setup_target``, the flip and the
tracking-refusal recovery.

THE FIX. ``_commanded_rotation`` is the one choke point: it returns None when
rotation is off for the night (saying so once), and the three re-centres read
it instead of ``target.rotation_deg``.

The hub here is test_centring_settings_reach_goto.py's recording double, whose
``goto_and_center`` records its call: what is graded is the angle each
re-centre ASKS for. Every mutant below was run from a byte-for-byte backup of
``sequence/engine.py`` and restored byte-identical.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_centring_settings_reach_goto import (_RECENTRES, _after_a_lost_star,
                                               _after_a_walking_field,
                                               _after_the_sweep, _guided,
                                               _target)

_LABEL = {_after_the_sweep: "after the unguided sweep",
          _after_a_lost_star: "after a lost star",
          _after_a_walking_field: "after a walking field"}

_SAID = "rotation is off for the night"


def _said(lines) -> list[str]:
    return [m for lvl, m, _src in lines if _SAID in m]


@_RECENTRES
async def test_an_untrusted_rotator_gets_no_angle_on_a_recovery_recentre(
        recentre, bus_lines):
    """The target plans PA 30 and the rotator's coupling has been measured
    bad. Each re-centre asks the hub for NO angle, and the night says once
    that rotation is off.

    MUTANT "the choke point does not honour rotation off" (the ``if planned
    is not None and self._rotation_off_tonight():`` block of
    ``_commanded_rotation`` removed): RED, all three cases -
        AssertionError: after the unguided sweep, the rotation is off for the
        night (the self-test measured the camera not following the rotator)
        and the hub was still asked for {'rotation_deg': 30.0}
    MUTANT "a re-centre reads the plan directly" (one call put back to
    ``rotation_deg=target.rotation_deg``): RED, that case alone, with the
    same line naming it.
    """
    t = _target(rotation_deg=30.0)
    e, hub = _guided(t)
    hub._rotation_trusted = False
    await recentre(e, t)
    assert len(hub.gotos) == 1, f"premise: one re-centre: {hub.gotos}"
    _args, kw = hub.gotos[0]
    assert kw == {"rotation_deg": None}, (
        f"{_LABEL[recentre]}, the rotation is off for the night (the "
        f"self-test measured the camera not following the rotator) and the "
        f"hub was still asked for {kw}")
    assert len(_said(bus_lines)) == 1, (
        f"the night did not say once that rotation is off: {_said(bus_lines)}")


async def test_rotation_off_is_said_once_across_the_recentres(bus_lines):
    """The three re-centres of one run share one sentence: said at the first,
    not repeated at the second and third (``_say_rotation_off`` is once per
    run).

    MUTANT "said at every re-centre" (``_say_rotation_off``'s once-per-run
    return removed): RED -
        AssertionError: the sentence was said 3 times, not once
    """
    t = _target(rotation_deg=30.0)
    e, hub = _guided(t)
    hub._rotation_trusted = False
    for recentre in (_after_the_sweep, _after_a_lost_star,
                     _after_a_walking_field):
        await recentre(e, t)
    assert len(hub.gotos) == 3, f"premise: three re-centres: {hub.gotos}"
    assert all(kw == {"rotation_deg": None} for _a, kw in hub.gotos), hub.gotos
    assert len(_said(bus_lines)) == 1, (
        f"the sentence was said {len(_said(bus_lines))} times, not once")


@_RECENTRES
@pytest.mark.parametrize("trust", [True, None], ids=["trusted", "unmeasured"])
async def test_control_a_trusted_or_unmeasured_rotator_is_still_asked(
        recentre, trust, bus_lines):
    """CONTROL. Only a MEASURED failure turns the angle off: a coupling the
    self-test passed (True), and one nobody has measured (None), still get the
    plan's angle at every re-centre, and nothing is said.

    MUTANT "any trust value turns rotation off" (``_rotation_off_tonight``
    returns True unless trust is True, so None reads as off): RED on the
    unmeasured case -
        AssertionError: ... the hub was asked for {'rotation_deg': None}
    MUTANT "rotation always off" (``_rotation_off_tonight`` returns True):
    RED on both.
    """
    t = _target(rotation_deg=30.0)
    e, hub = _guided(t)
    hub._rotation_trusted = trust
    await recentre(e, t)
    _args, kw = hub.gotos[0]
    assert kw == {"rotation_deg": 30.0}, (
        f"{_LABEL[recentre]}, rotation is not off (trust {trust!r}) and the "
        f"hub was asked for {kw}")
    assert _said(bus_lines) == [], _said(bus_lines)


@_RECENTRES
async def test_control_a_target_that_planned_no_angle_says_nothing(
        recentre, bus_lines):
    """CONTROL. A target with no planned angle and no lock has no angle to
    withhold, so with rotation off its re-centre asks for none, as it always
    did, and the night is not told rotation is off over a flow that never
    rotated.

    MUTANT "the sentence is said whatever was asked" (the off test hoisted
    above the ``planned is not None`` test in ``_commanded_rotation``): RED -
        AssertionError: a target that asked for no angle was told rotation is
        off: ['rotation is off for the night: ...']
    """
    t = _target(rotation_deg=None)
    e, hub = _guided(t)
    hub._rotation_trusted = False
    await recentre(e, t)
    _args, kw = hub.gotos[0]
    assert kw == {"rotation_deg": None}, kw
    assert _said(bus_lines) == [], (
        f"a target that asked for no angle was told rotation is off: "
        f"{_said(bus_lines)}")


# ---------------------------------------------------------------- ruling 9


def _locked(t, pa: float = 33.0):
    """An engine whose session holds the target's locked angle (ruling 9) and
    whose rig has a connected rotator, so the lock is commanded."""
    e, hub = _guided(t)
    # RE-PINNED FOR BACKLOG WP-125 (#516, wave 16 integration): `_set_state`
    # reads the ledger through the engine's one memo, which asks `frames` and
    # `accepted_by_step()`; the double used to answer `total_accepted()`.
    e._session = SimpleNamespace(
        id="s-lock", name="locked", frames=[], accepted_by_step=lambda: {},
        locked_angle=lambda tid: ({"pa_deg": pa} if tid == t.id else None))
    hub.devices["rotator"] = SimpleNamespace(connected=True)
    return e, hub


@_RECENTRES
async def test_a_recovery_recentre_commands_the_locked_angle(recentre):
    """RULING 9 reaches the three re-centres. An unframed target (no planned
    angle) that locked PA 33 on its first shot is commanded it at every later
    acquisition, and the three re-centres used to read the plan's None
    instead, so they turned the camera back to wherever it sat.

    MUTANT "a re-centre reads the plan directly" (one call put back to
    ``rotation_deg=target.rotation_deg``): RED, that case alone -
        AssertionError: after a lost star, the target locked PA 33 and the
        hub was asked for {'rotation_deg': None}
    """
    t = _target(rotation_deg=None)
    e, hub = _locked(t)
    hub._rotation_trusted = True
    await recentre(e, t)
    _args, kw = hub.gotos[0]
    assert kw == {"rotation_deg": 33.0}, (
        f"{_LABEL[recentre]}, the target locked PA 33 and the hub was asked "
        f"for {kw}")


@_RECENTRES
async def test_an_untrusted_rotator_withholds_the_locked_angle_too(
        recentre, bus_lines):
    """The lock is an angle like any other: with rotation off, no re-centre
    commands it, and the night says so once.

    MUTANT "the choke point does not honour rotation off": RED, all three -
        AssertionError: after the unguided sweep, the rotation is off ... and
        the hub was still asked for {'rotation_deg': 33.0}
    """
    t = _target(rotation_deg=None)
    e, hub = _locked(t)
    hub._rotation_trusted = False
    await recentre(e, t)
    _args, kw = hub.gotos[0]
    assert kw == {"rotation_deg": None}, (
        f"{_LABEL[recentre]}, the rotation is off for the night and the hub "
        f"was still asked for {kw}")
    assert len(_said(bus_lines)) == 1, _said(bus_lines)
