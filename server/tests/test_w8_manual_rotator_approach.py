# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""(b) #589: manual Go and the +-1 degree nudges (POST /api/rotator/move)
used to command `rot.move_to(target)` straight at the target, bypassing H4
orchestrator ruling 3's one-sided approach (#526) -- the rotate loop's own
moves go through `Hub._approach_rotator`, which overshoots
`ROTATOR_BACKLASH_DEG` and returns when a move runs against the approach
direction, and this route did not. Both rotator panels (RotatorCard.tsx,
RotatorPanel.tsx) post Go and both nudges to this ONE route, so fixing the
route fixes all three.

Fixed by routing the move through `Hub._approach_rotator_mechanical`, the
same one-sided-move executor the rotate loop's own `Hub._approach_rotator`
now delegates to.

Calls the route's endpoint function directly (``route.endpoint``, the plain
coroutine `@declare` returns unchanged per its own docstring) against a bare
Hub with a sim rotator wired in by hand -- the same device-injection idiom
test_rotator_api.py uses, but awaited directly here (rather than through
TestClient's blocking portal) so the `_spawn`ed background task can be
awaited to completion in this test's own event loop.
"""
from __future__ import annotations

import pytest

from astrodeck.devices.sim import SimCamera, SimRig, SimRotator

#: The 2026-09-28 numbers test_h4_rotator_backlash.py uses for the rotate
#: loop's own approach: the rotator at mechanical 137.53. Reused here so a
#: reader can compare the two directly.
MECH = 137.53


def _approx(values):
    return [pytest.approx(v, abs=1e-9) for v in values]


@pytest.fixture()
def rig_hub(tmp_path, monkeypatch):
    """A bare app + hub with a sim rotator and camera wired in directly (no
    ``connect_sim``, so the rig's physics play no part -- only the rotator
    device's own mechanical model and ``SimRig``'s backlash fields do)."""
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    hub = app_module.hub
    rig = SimRig()
    rot = SimRotator(rig)
    rot.connected = True
    cam = SimCamera(rig)
    cam.connected = True
    hub.devices["rotator"] = rot
    hub.devices["camera"] = cam
    hub._rotator_sky_sign = 1
    # ``app.routes`` is not a flat list (FastAPI 0.141 made ``include_router``
    # lazy; see ``rbac.iter_app_routes``'s docstring) -- walk the same way
    # the RBAC boot assertion does rather than miss a nested router.
    from astrodeck.auth.rbac import iter_app_routes
    route = next(r for r in iter_app_routes(app)
                if getattr(r, "path", None) == "/api/rotator/move")
    # ``app_module.hub`` is a PROCESS-WIDE singleton (``from .hub import
    # hub``), not a fresh instance per test -- unlike ``_simhub.sim_hub``'s
    # own ``Hub()``. Every field this fixture or a test built on it mutates
    # must come back exactly as found, or it leaks into test_rotator_api.py
    # and test_nina_rotator.py's own cases against the same object when they
    # share a worker (xdist worksteal, or this worktree's own ``-n 0``).
    sign_before = hub._rotator_sky_sign
    angle_before = hub.last_sky_angle
    yield route.endpoint, hub, rot, rig
    hub.devices.pop("rotator", None)
    hub.devices.pop("camera", None)
    hub._busy.pop("rotator", None)
    hub._rotator_sky_sign = sign_before
    hub.last_sky_angle = angle_before


def _rig_at(rig, *, play: float, slack: float) -> None:
    """The motor at ``MECH``, the camera ``slack`` below it -- same shape as
    test_h4_rotator_backlash.py's helper of the same name."""
    rig.rotator_backlash_deg = play
    rig.rotator_slack_deg = slack
    rig.rotator_mech_deg = MECH - slack


async def _move(endpoint, hub, position_deg: float) -> dict:
    from astrodeck.api.app import RotatorMoveBody

    result = await endpoint(RotatorMoveBody(position_deg=position_deg))
    await hub._busy["rotator"]
    return result


async def test_manual_move_against_the_approach_overshoots_and_returns(
        rig_hub):
    """-5.7 degrees from 137.53, 4 degrees of play: two moves, 5 past and
    back, exactly as test_h4_rotator_backlash.py's
    ``test_a_minus_5_7_correction_overshoots_and_returns`` proves for the
    rotate loop's own ``_approach_rotator``. ``sync_offset_deg`` is the
    device default (0.0, unsynced), so with sign +1 the sky position asked
    for IS the mechanical target, no folding: the sim's default "full" range
    folds nothing either.

    MUTANT "the route calls move_to directly" (the fix's `_spawn` line
    reverted to ``_spawn("rotator", rot.move_to(target))``), run from a
    byte-for-byte backup of app.py (sha256-verified restored afterwards):
    RED here and on the nudge and the STOP-mid-overshoot cases below,
    green on the "with the approach" control. Observed:

        >       assert rot.moves == _approx([MECH - 10.7, MECH - 5.7]), rot.moves
        E       AssertionError: [131.83]
        E       assert [131.83] == [126.83 +/- 1.0e-09, 131.83 +/- 1.0e-09]
        E         At index 0 diff: 131.83 != 126.83 +/- 1.0e-09
        E         Right contains one more item: 131.83 +/- 1.0e-09
    """
    endpoint, hub, rot, rig = rig_hub
    _rig_at(rig, play=4.0, slack=2.0)
    rot.moves.clear()

    result = await _move(endpoint, hub, MECH - 5.7)

    assert rot.moves == _approx([MECH - 10.7, MECH - 5.7]), rot.moves
    assert result["target_deg"] == pytest.approx(MECH - 5.7, abs=1e-6)


async def test_control_manual_move_with_the_approach_is_one_move(rig_hub):
    """Control: +5.7 (the approach direction, increasing mechanical angle)
    on the same train is a single move, unaffected by the one-sided
    approach -- exactly as the rotate loop's own control
    (``test_a_correction_in_the_approach_direction_is_one_move``)."""
    endpoint, hub, rot, rig = rig_hub
    _rig_at(rig, play=4.0, slack=2.0)
    rot.moves.clear()

    await _move(endpoint, hub, MECH + 5.7)

    assert rot.moves == _approx([MECH + 5.7]), rot.moves


async def test_a_nudge_against_the_approach_also_overshoots(rig_hub):
    """The nudge buttons post the SAME route with ``sky_deg -+ 1`` as their
    target (RotatorCard.tsx:170-172, RotatorPanel.tsx:307-318) -- there is
    no second code path to miss. One degree against the approach direction
    still overshoots and returns, proving the fix covers the nudge, not
    only a large manual Go."""
    endpoint, hub, rot, rig = rig_hub
    _rig_at(rig, play=4.0, slack=2.0)
    rot.moves.clear()

    await _move(endpoint, hub, MECH - 1.0)

    assert rot.moves == _approx([MECH - 6.0, MECH - 1.0]), rot.moves


async def test_a_stop_mid_overshoot_abandons_the_return_leg(rig_hub):
    """#574, now reachable from the manual route too (#589): a STOP landing
    right after the overshoot leg must still stop the return leg.
    ``Hub._approach_rotator_mechanical`` re-checks the motion fence before
    every leg; bumping the epoch inside the first ``move_mechanical`` call
    stands in for an operator's STOP landing there."""
    endpoint, hub, rot, rig = rig_hub
    _rig_at(rig, play=0.0, slack=0.0)
    rot.moves.clear()

    real_move = type(rot).move_mechanical
    calls = {"n": 0}

    async def move_then_stop(self, mech_deg):
        await real_move(self, mech_deg)
        calls["n"] += 1
        if calls["n"] == 1:
            hub.bump_motion_epoch()

    import astrodeck.devices.sim as sim_mod
    orig = sim_mod.SimRotator.move_mechanical
    sim_mod.SimRotator.move_mechanical = move_then_stop
    try:
        await _move(endpoint, hub, MECH - 5.7)
    finally:
        sim_mod.SimRotator.move_mechanical = orig

    assert rot.moves == _approx([MECH - 10.7]), rot.moves
