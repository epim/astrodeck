# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""(c) #626: R-4's learned sky/mechanical sign (#145) was threaded into only
two call sites by WP-32a -- the rotate loop's own move (`Hub._approach_
rotator`) and its range-fold (`rotation.map_sky_target`, called from
`_rotate_to_pa_attempts`). Every OTHER sky<->mechanical conversion kept the
pre-R-4, unsigned ``sky = mech - offset`` relationship: the manual move
route (`POST /api/rotator/move`) and every captured frame's
``rotator_angle_deg`` (the FITS ROTATANG header).

THE ANCHOR PROBLEM A BARE SIGN THREAD DOES NOT SOLVE. A SIGNED conversion
(`rotation.sky_to_mechanical`/`mechanical_to_sky`) is only correct when its
``mech_pos`` argument is the mechanical angle the rotator's
``sync_offset_deg`` was actually measured AT (see `sky_to_mechanical`'s own
docstring: for sign -1 the invariant is a SUM, not a difference, so an
anchor at the WRONG point is off by twice the drift since the real one).
The rotate loop's own calls are always made right after a fresh sync, so
"current reading" and "the sync point" are the same instant there --
``_rotation_already_set``'s docstring names this exactly: it is the one
unsigned reader that "stays correct regardless of sign" because it refuses
to trust a reading from "every point the rotator reaches by moving away
from that calibration". The manual route and a frame's metadata have no
solve of their own to anchor on, so this fix adds
`Hub._rotator_sync_anchor`, which recovers the real anchor from the newest
CALIBRATED sky-angle record when it still agrees with the rotator's current
``sync_offset_deg``, and falls back to today's naive "now" anchor
(anchor-independent for sign +1, so unchanged there) when there is none to
trust.

All the pure arithmetic below is worked out by hand against
`rotation.mechanical_to_sky`/`sky_to_mechanical` directly (both already
table-tested; test_w4_rotator_sign.py pins their sign behaviour), so every
assertion here is an independently-computed number, not a re-run of the
code under test.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.rotation import mechanical_to_sky, mod360

# Reuse the manual-move-route fixture and helper from the sibling WP-53 (b)
# file rather than duplicate the app/hub wiring: both are about the SAME
# route, the one-sided approach and the learned sign are independent halves
# of the same fix, and importing is read-only.
from test_w8_manual_rotator_approach import MECH, _approx, _move, rig_hub  # noqa: F401

#: A stand-in frame carrying only what `Hub._frame_meta` reads once
#: ra_hours/dec_deg are None (quality + EGAIN, never dereferenced for the
#: rotator block itself) -- these tests are about the rotator section only.
_BLANK_FRAME = SimpleNamespace(hfr=None, stars=None, gain=-1,
                               egain_e_per_adu=None, timestamp=0.0)


# --------------------------------------------------- the anchor, in isolation


async def test_anchor_uses_the_newest_matching_calibration(sim_hub):
    """`_rotator_sync_anchor` returns the sky-angle record's own
    (mechanical, offset) pair when it is CALIBRATED and still agrees with
    the rotator's current ``sync_offset_deg`` -- not the rotator's bare
    current reading.

    MUTANT "the anchor record is never read" (`_rotator_sync_anchor`'s body
    replaced with ``return mech_now, float(rot.sync_offset_deg)``
    unconditionally), run from a byte-for-byte backup of hub.py (sha256-
    verified restored afterwards): RED (observed):

        >       assert anchor == (100.0, 60.0), anchor
        E       AssertionError: (137.0, 60.0) == (100.0, 60.0)
    """
    rot = sim_hub.devices["rotator"]
    await rot.move_mechanical(100.0)
    await rot.sync(40.0)                   # offset = 100 - 40 = 60
    sim_hub.last_sky_angle = {
        "calibrated": True, "offset_deg": float(rot.sync_offset_deg),
        "mechanical_deg": 100.0,
    }
    await rot.move_mechanical(137.0)       # moved since the calibration

    anchor = sim_hub._rotator_sync_anchor(rot, 137.0)

    assert anchor == (100.0, 60.0), anchor


async def test_anchor_falls_back_when_the_record_does_not_match_the_device(
        sim_hub):
    """A reconnect resets ``sync_offset_deg``/``synced`` without touching
    ``hub.last_sky_angle`` (`Hub.disconnect_all`'s own comment: "a reconnect
    may bring back a DIFFERENT rotator, or the same one [...] unsynced").
    The anchor must not trust a stale record over the device it no longer
    describes -- it falls back to ``(mech_now, rot.sync_offset_deg)``,
    exactly the pre-R-4 formula."""
    rot = sim_hub.devices["rotator"]
    sim_hub.last_sky_angle = {
        "calibrated": True, "offset_deg": 60.0, "mechanical_deg": 100.0}
    # rot.synced is False and rot.sync_offset_deg is 0.0 (freshly connected,
    # never synced this process) -- disagrees with the stale record above.

    anchor = sim_hub._rotator_sync_anchor(rot, 12.0)

    assert anchor == (12.0, 0.0), anchor


def test_effective_sign_defaults_to_plus_one_when_unmeasured(sim_hub):
    sim_hub._rotator_sky_sign = None
    assert sim_hub._effective_rotator_sign() == 1
    sim_hub._rotator_sky_sign = -1
    assert sim_hub._effective_rotator_sign() == -1


# ----------------------------------------------------------- frame metadata


async def test_frame_metadata_applies_the_learned_sign_from_the_anchor(
        sim_hub):
    """The value that becomes the FITS ROTATANG header. Anchored at
    mechanical 100 / offset 60 (synced to sky PA 40 there), the rotator then
    moves +10 mechanical with NO new solve. Under the OLD unsigned formula
    the header would read `mod360(110 - 60) == 50`; the TRUE sky PA under a
    measured -1 sign is `mechanical_to_sky(110, 100, 60, -1) == 30` --
    computed here independently of `_frame_meta` as the check.

    MUTANT "frame metadata reads the bare position" (the fix's anchor+sign
    block replaced with ``meta.rotator_angle_deg = float(await
    rot.get_position())``), run from a byte-for-byte backup of hub.py
    (sha256-verified restored afterwards): RED (observed):

        >       assert meta.rotator_angle_deg == pytest.approx(30.0), meta.rotator_angle_deg
        E       assert 50.00000000000001 == 30.0 +/- 1e-06
    """
    rot = sim_hub.devices["rotator"]
    sim_hub._rotator_sky_sign = -1
    await rot.move_mechanical(100.0)
    await rot.sync(40.0)
    sim_hub.last_sky_angle = {
        "calibrated": True, "offset_deg": float(rot.sync_offset_deg),
        "mechanical_deg": 100.0,
    }
    await rot.move_mechanical(110.0)

    meta = await sim_hub._frame_meta(_BLANK_FRAME, None, None)

    want = mechanical_to_sky(110.0, 100.0, 60.0, -1)
    assert want == pytest.approx(30.0)        # the hand-worked check itself
    assert meta.rotator_angle_deg == pytest.approx(want), meta.rotator_angle_deg
    assert meta.rotator_angle_deg == pytest.approx(30.0), meta.rotator_angle_deg


async def test_control_frame_metadata_unchanged_with_no_anchor_to_trust(
        sim_hub):
    """Control: sign measured -1 but NEVER calibrated against a real sky
    measurement this process (no matching record) -- the anchor falls back
    to "now", which is sign-independent (`_rotator_sync_anchor`'s
    docstring), so the header reads exactly what the old unsigned formula
    always gave. This is the "no worse than before R-4" half of the fix."""
    rot = sim_hub.devices["rotator"]
    sim_hub._rotator_sky_sign = -1
    sim_hub.last_sky_angle = None
    await rot.move_mechanical(55.0)

    meta = await sim_hub._frame_meta(_BLANK_FRAME, None, None)
    unsigned = float(await rot.get_position())

    assert meta.rotator_angle_deg == pytest.approx(unsigned)


async def test_control_frame_metadata_unchanged_for_sign_plus_one(sim_hub):
    """Control: `connect_sim` measures sign +1 from the simulator's own
    hardcoded physics (R-4, #145 comment in `Hub.connect_sim`). Anchor-
    independent by construction, so a calibrated anchor changes nothing
    either: the header reads the same value a plain ``rot.get_position()``
    always has."""
    rot = sim_hub.devices["rotator"]
    assert sim_hub._rotator_sky_sign == 1
    await rot.move_mechanical(20.0)
    await rot.sync(5.0)
    sim_hub.last_sky_angle = {
        "calibrated": True, "offset_deg": float(rot.sync_offset_deg),
        "mechanical_deg": 20.0,
    }
    await rot.move_mechanical(77.0)

    meta = await sim_hub._frame_meta(_BLANK_FRAME, None, None)
    unsigned = float(await rot.get_position())

    assert meta.rotator_angle_deg == pytest.approx(unsigned)


# -------------------------------------------------------- the manual route


async def test_manual_move_applies_the_learned_sign_from_the_anchor(rig_hub):
    """The route used to call `map_sky_target` with no ``sky_sign`` at all
    (defaulting to +1) and then `rot.move_to(target)`, which re-applies the
    unsigned offset -- so a rig measured sign -1 (#145, pier west) turns the
    WRONG mechanical direction for the sky PA asked for. Anchored at
    mechanical 100 / offset 60 (synced to sky PA 40 there): asking for sky
    PA 30 under sign -1 commands mechanical 110
    (``sky_to_mechanical(30, 100, 60, -1) == 110``, the exact inverse the
    frame-metadata test above already checks by hand) -- the OLD, unsigned
    code would instead have commanded mechanical 90 (30 + 60).

    THE ROTATOR HAS MOVED SINCE THAT SYNC (to mechanical 95, outside this
    route -- an earlier automated correction, say) before this route runs,
    chosen BELOW the correct target (110) so the correct answer is still a
    single, plain move (no one-sided-approach overshoot, #589's own concern,
    to muddy this one). This also catches a fix that anchors on the CURRENT
    reading instead of the trusted calibration
    (``_rotator_sync_anchor``'s own mutant,
    test_anchor_uses_the_newest_matching_calibration): anchored on 95
    instead of 100, sign -1 would instead command mechanical 100
    (``sky_to_mechanical(30, 95, 60, -1) == 100``, which is NOT 110).

    MUTANT "the route keeps the unsigned offset" (the fix's ``sky_sign=sign``
    keyword dropped from the `map_sky_target` call and the ``mech_target``
    line reverted to ``mech_target = mod360(target + anchor_offset)``), run
    from a byte-for-byte backup of app.py (sha256-verified restored
    afterwards): RED (observed; the extra 85.0 leg is the one-sided approach
    (#589) correctly overshooting the WRONG, unsigned target of 90, since
    90 sits below the current mechanical 95):

        >       assert rot.moves == _approx([110.0]), rot.moves
        E       AssertionError: [85.0, 90.0]
        E       assert [85.0, 90.0] == [110.0 +/- 1.0e-09]
        E         At index 0 diff: 85.0 != 110.0 +/- 1.0e-09
        E         Left contains one more item: 90.0
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
    rig.rotator_mech_deg = 95.0    # moved since the sync, outside this route
    rot.moves.clear()

    result = await _move(endpoint, hub, 30.0)

    assert rot.moves == _approx([110.0]), rot.moves
    assert result["target_deg"] == pytest.approx(30.0)


async def test_control_manual_move_unchanged_for_sign_plus_one(rig_hub):
    """Control: the default/unmeasured sign (+1) reproduces the route's
    behaviour from before this fix exactly -- ``map_sky_target`` with no
    ``sky_sign`` IS ``sky_sign=1`` (test_w4_rotator_sign.py's own control:
    "the default must keep reading exactly as before R-4")."""
    endpoint, hub, rot, rig = rig_hub
    rig.rotator_mech_deg = 50.0
    rot.moves.clear()

    result = await _move(endpoint, hub, mod360(50.0 + 1.0))

    assert rot.moves == _approx([51.0]), rot.moves
    assert result["target_deg"] == pytest.approx(51.0)
