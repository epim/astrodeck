# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#671 (part 1, the third #626 site): ``Hub.poll_status``'s published
rotator block built ``sky_deg`` as ``round(await rot.get_position(), 2)`` --
the pre-R-4, unsigned ``sky = mech - offset`` relationship, anchored at
whatever mechanical angle happens to be current -- while WP-53 (#589, #626)
already carried the LEARNED sign (``Hub._effective_rotator_sign``) and the
trusted-calibration anchor (``Hub._rotator_sync_anchor``) into the manual-move
route and a captured frame's metadata. The status block was the one caller
left on the old formula.

WHY THIS MATTERS ENOUGH TO BE ITS OWN BUG: both rotator panels
(RotatorCard.tsx, RotatorPanel.tsx) compute their nudge buttons' target as
``rot.sky_deg - 1`` / ``+ 1`` and POST that straight to ``/api/rotator/move``
-- the exact route this status value has to agree with. Under a measured -1
sign with a trusted calibration, the old unsigned ``sky_deg`` disagreed with
what that route treats as "the current sky angle" by more than the 1 degree
nudge itself, so pressing "+1" did not move the rotator anywhere near one
sky-degree from where the display said it was.

Fixed by reading the status block's ``sky_deg`` through the same
``_rotator_sync_anchor`` / ``_effective_rotator_sign`` pair the manual-move
route and the frame-metadata block already use -- all three now agree.

Numbers reused from test_w8_rotator_learned_sign.py's own anchor/sign tests
(anchored at mechanical 100 / offset 60, synced to sky PA 40 there, moved to
mechanical 110 with no new solve): the same scenario, now asked of the status
block instead of the frame metadata.
"""
from __future__ import annotations

import pytest

from astrodeck.rotation import mechanical_to_sky

# Reuse the manual-move-route fixture/helper rather than duplicate the
# app/hub wiring: this is the SAME route the status block's sky_deg has to
# agree with, and importing is read-only.
from test_w8_manual_rotator_approach import _approx, _move, rig_hub  # noqa: F401


async def test_status_sky_deg_matches_the_manual_route_s_current_angle(
        rig_hub):
    """Anchored at mechanical 100 / offset 60 (synced to sky PA 40 there),
    the rotator then moves to mechanical 110 with NO new solve. Under the
    OLD unsigned formula ``poll_status`` would publish
    ``mod360(110 - 60) == 50``; the TRUE sky PA under a measured -1 sign,
    anchored on the trusted calibration, is
    ``mechanical_to_sky(110, 100, 60, -1) == 30`` -- the exact number
    test_w8_rotator_learned_sign.py's frame-metadata test already proves by
    hand for the same scenario.

    MUTANT "the status block reads the bare position" (the fix's anchor+sign
    block in ``Hub.poll_status`` replaced with
    ``"sky_deg": round(await rot.get_position(), 2)``), run from a
    byte-for-byte backup of hub.py (sha256-verified restored afterwards):
    RED (observed):

        >       assert published == pytest.approx(want), published
        E       AssertionError: 50.0
        E       assert 50.0 == 30.0 +/- 3.0e-05
        E
        E         comparison failed
        E         Obtained: 50.0
        E         Expected: 30.0 +/- 3.0e-05
    """
    endpoint, hub, rot, rig = rig_hub
    hub._rotator_sky_sign = -1
    rig.rotator_backlash_deg = 0.0
    rig.rotator_slack_deg = 0.0
    rig.rotator_mech_deg = 100.0
    await rot.sync(40.0)                   # offset = 100 - 40 = 60
    hub.last_sky_angle = {
        "calibrated": True, "offset_deg": float(rot.sync_offset_deg),
        "mechanical_deg": 100.0,
    }
    rig.rotator_mech_deg = 110.0            # moved since the sync

    status = await hub.poll_status()
    published = status["rotator"]["sky_deg"]

    want = mechanical_to_sky(110.0, 100.0, 60.0, -1)
    assert want == pytest.approx(30.0)      # the hand-worked check itself
    assert published == pytest.approx(want), published
    assert published == pytest.approx(30.0), published
    assert status["rotator"]["mech_deg"] == pytest.approx(110.0)


async def test_a_plus_one_nudge_from_the_published_value_moves_the_sky_angle_by_one(
        rig_hub):
    """The end-to-end consequence of the fix: RotatorCard.tsx / RotatorPanel
    .tsx compute their nudge target as the published ``sky_deg`` plus one and
    POST it unchanged. Taking the TRUE sky angle before the nudge (computed
    independently, the same way the test above checks ``sky_deg`` itself) and
    the TRUE sky angle after the commanded move lands, the difference must be
    +1 -- not -1 (the sense a -1 sign would give a caller that mixed up which
    direction mechanical travel corresponds to), and not some other anchor's
    idea of "current" plus one.

    This is the sharper version of the test above: it fails the same way
    under the same mutant (byte-for-byte backup of hub.py, sha256-verified
    restored afterwards), because the identity only holds when the posted
    nudge target was computed from a ``sky_deg`` that already agreed with
    the route's own anchor -- RED under the unsigned-formula mutant
    (observed):

        >       assert true_after == pytest.approx(true_before + 1.0), (
                    true_before, true_after)
        E       AssertionError: (30.0, 51.0)
        E       assert 51.0 == 31.0 +/- 3.1e-05
        E
        E         comparison failed
        E         Obtained: 51.0
        E         Expected: 31.0 +/- 3.1e-05
    """
    endpoint, hub, rot, rig = rig_hub
    hub._rotator_sky_sign = -1
    rig.rotator_backlash_deg = 0.0
    rig.rotator_slack_deg = 0.0
    rig.rotator_mech_deg = 100.0
    await rot.sync(40.0)
    hub.last_sky_angle = {
        "calibrated": True, "offset_deg": float(rot.sync_offset_deg),
        "mechanical_deg": 100.0,
    }
    rig.rotator_mech_deg = 110.0

    anchor_mech, anchor_offset = hub._rotator_sync_anchor(rot, 110.0)
    sign = hub._effective_rotator_sign()
    true_before = mechanical_to_sky(110.0, anchor_mech, anchor_offset, sign)

    status = await hub.poll_status()
    published = status["rotator"]["sky_deg"]
    rot.moves.clear()

    await _move(endpoint, hub, published + 1.0)

    final_mech = await rot.get_mechanical_position()
    true_after = mechanical_to_sky(final_mech, anchor_mech, anchor_offset,
                                   sign)
    assert true_after == pytest.approx(true_before + 1.0), (
        true_before, true_after)


async def test_control_status_sky_deg_unchanged_for_sign_plus_one(rig_hub):
    """Control: sign +1 (unmeasured or measured) is anchor-independent by
    construction (``_rotator_sync_anchor``'s own docstring), so a calibrated
    anchor changes nothing -- ``sky_deg`` reads exactly what the old,
    unsigned ``rot.get_position()`` always gave."""
    endpoint, hub, rot, rig = rig_hub
    assert hub._rotator_sky_sign == 1
    rig.rotator_mech_deg = 20.0
    await rot.sync(5.0)
    hub.last_sky_angle = {
        "calibrated": True, "offset_deg": float(rot.sync_offset_deg),
        "mechanical_deg": 20.0,
    }
    rig.rotator_mech_deg = 77.0

    status = await hub.poll_status()
    unsigned = float(await rot.get_position())

    assert status["rotator"]["sky_deg"] == pytest.approx(unsigned)


async def test_control_status_sky_deg_unchanged_with_no_anchor_to_trust(
        rig_hub):
    """Control: sign measured -1 but NEVER calibrated against a real sky
    measurement this process -- the anchor falls back to "now", which is
    sign-independent, so ``sky_deg`` reads exactly what the old unsigned
    formula always gave. The "no worse than before R-4" half of the fix."""
    endpoint, hub, rot, rig = rig_hub
    hub._rotator_sky_sign = -1
    hub.last_sky_angle = None
    rig.rotator_mech_deg = 55.0

    status = await hub.poll_status()
    unsigned = float(await rot.get_position())

    assert status["rotator"]["sky_deg"] == pytest.approx(unsigned)
