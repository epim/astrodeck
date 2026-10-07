# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Frames record the rotator's MECHANICAL angle; flats are keyed and matched
by it (#176 a, b).

THE DEFECT. Once mosaic panels sit at different camera angles (#175), the
lights of one night are taken at several rotator positions, and a flat only
divides out the dust that was where the dust is at ITS angle: a mote at
radius r moves r * d_theta across the sensor when the rotator turns, and past
about one degree the flat inverts the correction instead of applying it. The
calibration index keyed flats by filter, gain, offset and binning only, and a
frame recorded no rotator angle at all, so a light could not be matched to
the flat shot at its own angle.

THE FIELD. ``ROTMECH`` (the rotator's mechanical angle), never ``ROTATANG``:
ROTATANG is the SKY position angle, which differs between nights for the
same physical angle whenever the rotator is re-synced, and dust follows the
metal. A flat or a light written before this change has no ROTMECH and reads
as unknown, and unknown on either side is no constraint (the reading
``matcher._temp_ok`` already applies to temperature), so an old library keeps
working exactly as it did.

Job (c), shooting flats at each distinct angle, is a separate issue: a
calibration target skips slew, centring and rotation, so the engine would
need a FLAT target to carry a mechanical angle and turn to it first.

NAMED MUTANTS. Each was run from a byte backup of the one source file inside
this worktree (sha256 of the file compared after the restore, and the mutant
text grepped out), under the same command this file normally runs under. The
assertion each one broke is recorded verbatim on the test that caught it;
the temporary directory is elided as <tmp>.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.calibration.keys import (CalKey, key_from_header, key_index_id,
                                        mech_angle, rotator_bin)
from astrodeck.calibration.library import CalibrationLibrary
from astrodeck.calibration.matcher import (ROTATION_TOL_DEG, LightNeed,
                                           MasterRecord, MatchTolerance,
                                           best_master, coverage_for,
                                           flat_matches)
from astrodeck.devices.base import CameraFrame
from astrodeck.flows.calibration_health import (CalFrame, CalNeed,
                                                frame_from_header,
                                                health_matrix)
from astrodeck.imaging.fitsio import FrameMeta, save_fits
from astrodeck.rotation import mechanical_to_sky

TOL = MatchTolerance(exposure_tol_pct=5.0, temp_tol_c=2.0)
NOW = 1_754_000_000.0


def _flat_header(**over) -> dict:
    h = {"IMAGETYP": "Flat", "FILTER": "Ha", "GAIN": 100, "OFFSET": 30,
         "XBINNING": 1, "EXPTIME": 2.0}
    h.update(over)
    return h


def _flat_key(angle=None, filt="Ha") -> CalKey:
    return CalKey("FLAT", 2.0, 100, 30, None, 1, filt, angle)


# ------------------------------------------------------------- the card (a)

class TestTheCard:
    def test_a_frame_writes_its_mechanical_angle_beside_the_sky_angle(
            self, tmp_path):
        """ROTMECH is the rotator's own angle and ROTATANG stays the sky PA:
        two cards, two numbers, neither shadowing the other.

        MUTANT "ROTMECH written from the sky angle" (``fitsio.save_fits``:
        ``float(m.rotator_mech_deg)`` replaced by
        ``float(m.rotator_angle_deg or 0.0)`` in the ROTMECH card). Observed:

            >       assert h["ROTMECH"] == pytest.approx(10.0)
            E       assert 77.0 == 10.0 +/- 1.0e-05

        (also red in the key round-trip test below)
        """
        frame = CameraFrame(np.full((8, 8), 100, np.uint16), 2.0, 100, 30, 1,
                            None, -10.0, 1_772_000_000.0)
        path = save_fits(frame, tmp_path / "f.fits", filter_name="Ha",
                         frame_type="Flat",
                         meta=FrameMeta(rotator_mech_deg=10.0,
                                        rotator_angle_deg=77.0))
        card = fits.getheader(path).cards["ROTMECH"]
        h = fits.getheader(path)
        assert h["ROTMECH"] == pytest.approx(10.0)
        assert card.comment == "Rotator mechanical angle (deg)"
        assert h["ROTATANG"] == pytest.approx(77.0)

    def test_no_angle_means_no_card_never_a_placeholder(self, tmp_path):
        """A rig with no rotator writes neither card; zero is a real angle,
        so a placeholder would claim one. NaN is not writable either."""
        frame = CameraFrame(np.full((8, 8), 100, np.uint16), 2.0, 100, 30, 1,
                            None, -10.0, 1_772_000_000.0)
        for meta in (FrameMeta(), FrameMeta(rotator_mech_deg=None),
                     FrameMeta(rotator_mech_deg=float("nan"))):
            path = save_fits(frame, tmp_path / "f.fits", meta=meta)
            assert "ROTMECH" not in fits.getheader(path)

    def test_the_card_the_writer_writes_is_the_card_the_key_reads(
            self, tmp_path):
        """One name on both sides: a flat saved by ``save_fits`` and read by
        ``key_from_header`` carries the MECHANICAL angle, whatever the sky
        angle says.

        MUTANT "the card is read from ROTATANG instead of ROTMECH"
        (``keys.mech_angle_from_header`` reading ``"ROTATANG"``). Observed:

            >       assert key.rotator_mech_deg == pytest.approx(10.0)
            E       assert 77.0 == 10.0 +/- 1.0e-05
        """
        frame = CameraFrame(np.full((8, 8), 100, np.uint16), 2.0, 100, 30, 1,
                            None, -10.0, 1_772_000_000.0)
        path = save_fits(frame, tmp_path / "f.fits", filter_name="Ha",
                         frame_type="Flat",
                         meta=FrameMeta(rotator_mech_deg=10.0,
                                        rotator_angle_deg=77.0))
        key = key_from_header(fits.getheader(path))
        assert key.rotator_mech_deg == pytest.approx(10.0)


# --------------------------------------------- the hub's frame-meta builder

_BLANK_FRAME = SimpleNamespace(hfr=None, stars=None, gain=-1,
                               egain_e_per_adu=None, timestamp=0.0)


class TestTheHubRecordsIt:
    async def test_the_mechanical_angle_survives_a_resync(self, sim_hub):
        """Re-syncing the rotator moves the SKY angle a frame reports and
        leaves the metal where it was, and a dust shadow follows the metal.
        Both frames below sit at mechanical 100; the second was taken after a
        re-sync, so its sky PA differs and its ROTMECH must not.

        MUTANT "ROTMECH is the sky formula" (``hub._frame_meta``:
        ``meta.rotator_mech_deg = mech_now`` replaced by
        ``meta.rotator_mech_deg = _rotation.mod360(mech_now -
        float(rot.sync_offset_deg))``, the unsigned mechanical-to-sky
        arithmetic). Observed:

            >       assert meta1.rotator_mech_deg == pytest.approx(100.0)
            E       assert 40.0 == 100.0 +/- 1.0e-04
        """
        rot = sim_hub.devices["rotator"]
        sim_hub.last_sky_angle = None
        await rot.move_mechanical(100.0)
        await rot.sync(40.0)
        meta1 = await sim_hub._frame_meta(_BLANK_FRAME, None, None)
        await rot.sync(70.0)
        meta2 = await sim_hub._frame_meta(_BLANK_FRAME, None, None)

        assert meta1.rotator_mech_deg == pytest.approx(100.0)
        assert meta2.rotator_mech_deg == pytest.approx(100.0)
        assert meta1.rotator_angle_deg != pytest.approx(meta2.rotator_angle_deg)

    async def test_the_mechanical_angle_is_folded_into_a_turn(self, sim_hub):
        """The header says 0..360, as the sky angle beside it does."""
        rot = sim_hub.devices["rotator"]
        await rot.move_mechanical(-20.0)
        meta = await sim_hub._frame_meta(_BLANK_FRAME, None, None)
        assert meta.rotator_mech_deg == pytest.approx(340.0)

    async def test_a_failed_sky_conversion_does_not_lose_the_mechanical_angle(
            self, sim_hub, monkeypatch):
        """The sky angle needs the sync anchor and the learned sign; the
        mechanical angle needs nothing but the reading. When the first
        raises, the second is still a true fact about the frame.

        MUTANT "the mechanical angle is recorded after the sky conversion"
        (``hub._frame_meta``: the ROTMECH assignment moved below the
        ``meta.rotator_angle_deg = _rotation.mechanical_to_sky(...)``
        statement). Observed:

            >       assert meta.rotator_mech_deg == pytest.approx(100.0)
            E       assert None == 100.0 +/- 1.0e-04
        """
        rot = sim_hub.devices["rotator"]
        await rot.move_mechanical(100.0)

        def _boom(*_a, **_k):
            raise RuntimeError("anchor unavailable")

        monkeypatch.setattr(sim_hub, "_rotator_sync_anchor", _boom)
        meta = await sim_hub._frame_meta(_BLANK_FRAME, None, None)
        assert meta.rotator_angle_deg is None
        assert meta.rotator_mech_deg == pytest.approx(100.0)

    async def test_no_rotator_no_angle(self, sim_hub):
        sim_hub.devices.pop("rotator", None)
        meta = await sim_hub._frame_meta(_BLANK_FRAME, None, None)
        assert meta.rotator_mech_deg is None and meta.rotator_angle_deg is None

    async def test_the_sky_angle_is_unchanged(self, sim_hub):
        """The sky PA block is untouched: same anchor, same sign."""
        rot = sim_hub.devices["rotator"]
        sim_hub.last_sky_angle = None
        await rot.move_mechanical(100.0)
        await rot.sync(40.0)
        meta = await sim_hub._frame_meta(_BLANK_FRAME, None, None)
        assert meta.rotator_angle_deg == pytest.approx(
            mechanical_to_sky(100.0, 100.0, 60.0, 1))


# ---------------------------------------------------------- the key (b)

class TestTheKey:
    def test_a_flat_key_carries_the_mechanical_angle(self):
        k = key_from_header(_flat_header(ROTMECH=10.0))
        assert k.rotator_mech_deg == pytest.approx(10.0)

    def test_only_a_flat_carries_an_angle(self):
        """A dark is shot with the shutter closed and a bias has no light on
        it: the angle is no part of either's identity, and a card on a dark
        must not split a dark library by where the camera happened to point.

        MUTANT "a dark keeps the angle" (``keys.key_from_header``: the DARK
        and BIAS branch's ``angle = None`` replaced by
        ``angle = mech_angle_from_header(header)``). Observed:

            >           assert k.rotator_mech_deg is None
            E           AssertionError: assert 10.0 is None
        """
        for kind in ("Dark", "Bias"):
            k = key_from_header({"IMAGETYP": kind, "EXPTIME": 5.0, "GAIN": 100,
                                 "OFFSET": 30, "ROTMECH": 10.0})
            assert k.rotator_mech_deg is None

    def test_the_sky_card_alone_is_unknown(self):
        """A frame written before ROTMECH has only ROTATANG, a SKY angle.
        Reading it as mechanical would match a flat to the wrong light."""
        assert key_from_header(_flat_header(ROTATANG=77.0)).rotator_mech_deg is None

    @pytest.mark.parametrize("raw, want", [
        (10.0, 10.0), ("10.5", 10.5), (370.0, 10.0), (-0.4, 359.6),
        (0.0, 0.0), (360.0, 0.0), (359.99999, 0.0),
    ])
    def test_the_angle_is_a_turn(self, raw, want):
        """MUTANT "an angle is not folded" (``keys.mech_angle``: the return
        replaced by ``round(value, 3)``). Observed, on the 370.0 case (and
        -0.4, 360.0 and 359.99999 with it):

            >       assert got == pytest.approx(want, abs=1e-6)
            E       assert 370.0 == 10.0 +/- 1.0e-06
        """
        got = mech_angle(raw)
        assert got == pytest.approx(want, abs=1e-6)
        assert 0.0 <= got < 360.0

    @pytest.mark.parametrize("raw", [None, "n/a", "", float("nan"),
                                     float("inf"), -math.inf, [1.0], {}])
    def test_junk_is_unknown_never_zero(self, raw):
        """Zero is a real camera angle. A junk card must not claim it.

        MUTANT "a non-finite angle reads zero" (``keys.mech_angle``: the
        ``return None`` under ``not math.isfinite(value)`` replaced by
        ``return 0.0``). Observed, on nan, inf and -inf:

            >       assert mech_angle(raw) is None
            E       assert 0.0 is None
            E        +  where 0.0 = mech_angle(nan)
        """
        assert mech_angle(raw) is None

    def test_a_seven_field_key_still_builds(self):
        """The new field trails and defaults, so every CalKey(...) built by
        hand before this change still works and still equals what a header
        without the card reads as."""
        k = CalKey("FLAT", 2.0, 100, 30, None, 1, "Ha")
        assert k.rotator_mech_deg is None
        assert k == key_from_header(_flat_header(EXPTIME=2.0))


class TestTheId:
    def test_an_id_without_an_angle_is_byte_identical(self):
        """No existing master file may rename: the suffix is added ONLY when
        the angle is known, so every id a library holds today is unchanged."""
        assert key_index_id(_flat_key(), 5.0) == "flat_g100_o30_b1_fHa"
        assert (key_index_id(_flat_key(filt="O III"), 5.0)
                == "flat_g100_o30_b1_fO_III~8202bad7")

    def test_a_known_angle_adds_its_bin_last(self):
        """MUTANT "key_index_id ignores the angle" (``keys.key_index_id``:
        ``if rbin is not None:`` replaced by ``if False:``, so the
        ``_r<bin>`` suffix is never appended). Observed:

            >       assert key_index_id(_flat_key(10.0), 5.0) == "flat_g100_o30_b1_fHa_r5"
            E       AssertionError: assert 'flat_g100_o30_b1_fHa' == 'flat_g100_o30_b1_fHa_r5'
            E         - flat_g100_o30_b1_fHa_r5
            E         ?                     ---
            E         + flat_g100_o30_b1_fHa

        (12 of this file's cases go red under it, the library's among them:
        see ``test_two_angles_are_two_buckets_two_masters``)
        """
        assert key_index_id(_flat_key(10.0), 5.0) == "flat_g100_o30_b1_fHa_r5"
        assert key_index_id(_flat_key(40.0), 5.0) == "flat_g100_o30_b1_fHa_r20"
        assert (key_index_id(_flat_key(10.0, filt="O III"), 5.0)
                == "flat_g100_o30_b1_fO_III~8202bad7_r5")

    def test_angles_in_one_bin_share_an_id_and_angles_in_two_do_not(self):
        assert (key_index_id(_flat_key(10.0), 5.0)
                == key_index_id(_flat_key(10.9), 5.0))
        assert (key_index_id(_flat_key(10.0), 5.0)
                != key_index_id(_flat_key(13.0), 5.0))

    def test_the_bin_wraps_at_a_full_turn(self):
        """359.6 and 0.4 are 0.8 degree apart and one set of flats: a bin
        that did not wrap would split them into bin 180 and bin 0.

        MUTANT "the bin does not wrap" (``keys.rotator_bin``:
        ``round(angle / width) % count`` replaced by
        ``round(angle / width)``). Observed:

            >       assert (key_index_id(_flat_key(359.6), 5.0)
            E       AssertionError: assert 'flat_g100_o30_b1_fHa_r180' == 'flat_g100_o30_b1_fHa_r0'
            E         - flat_g100_o30_b1_fHa_r0
            E         + flat_g100_o30_b1_fHa_r180
            E         ?                       ++
        """
        assert (key_index_id(_flat_key(359.6), 5.0)
                == key_index_id(_flat_key(0.4), 5.0)
                == "flat_g100_o30_b1_fHa_r0")

    def test_the_bin_width_is_a_parameter(self):
        assert rotator_bin(14.0, 10.0) == 1 and rotator_bin(16.0, 10.0) == 2
        assert rotator_bin(None, 2.0) is None
        assert (key_index_id(_flat_key(14.0), 5.0, rotator_bin_deg=10.0)
                == "flat_g100_o30_b1_fHa_r1")

    def test_a_width_of_zero_disables_binning(self):
        """The ``temp_bin`` convention: zero means exact, not 'divide by it'."""
        assert (key_index_id(_flat_key(10.5), 5.0, rotator_bin_deg=0)
                == "flat_g100_o30_b1_fHa_r10.5")

    def test_darks_and_bias_ids_never_carry_an_angle(self):
        dark = CalKey("DARK", 300.0, 100, 30, -10.0, 1, "", 10.0)
        bare = CalKey("DARK", 300.0, 100, 30, -10.0, 1, "")
        assert key_index_id(dark, 5.0) == key_index_id(bare, 5.0)

    def test_a_long_name_with_an_angle_still_fits_a_file_name(self):
        """The suffix goes after the 64-byte cut and the digest, so the id
        gains a few bytes and no more: the file name stays far under the 255
        a path component holds (#427)."""
        kid = key_index_id(_flat_key(350.0, filt="N" * 300), 5.0)
        assert kid.endswith("_r175")
        assert len((kid + ".fits").encode("utf-8")) < 110


# ------------------------------------------------------------ the match (b)

def _flat_master(angle, *, mid="f", filt="Ha", count=20) -> MasterRecord:
    return MasterRecord(mid, "FLAT", 2.0, 100, 30, None, 1, filt, count,
                        "/m/" + mid, 0.0, angle)


def _dark_master(mid="d") -> MasterRecord:
    return MasterRecord(mid, "DARK", 300.0, 100, 30, -10.0, 1, "", 20,
                        "/m/" + mid, 0.0)


def _need(angle=None, filt="Ha") -> LightNeed:
    return LightNeed(300.0, 100, 30, -10.0, 1, filt, angle)


class TestTheMatch:
    def test_a_light_gets_the_flat_shot_at_its_own_angle(self):
        """The issue's own test: two lights at mechanical angles 10 and 40,
        flats at both; each light matches its own angle and no other.

        MUTANT "flat_matches ignores the angle" (``matcher.flat_matches``:
        ``and _rotator_ok(need, m, tol.rotator_tol_deg))`` replaced by
        ``and True)``). Observed:

            >       assert flat_matches(_need(10.0), _flat_master(40.0), TOL) is False
            E       AssertionError: assert True is False
            E        +  where True = flat_matches(LightNeed(... rotator_mech_deg=10.0),
            E           MasterRecord(... rotator_mech_deg=40.0), MatchTolerance(...))

        (10 of this file's cases go red under it, the library's and the
        matrix's among them)
        """
        masters = [_flat_master(10.0, mid="f10"), _flat_master(40.0, mid="f40")]
        assert best_master(_need(10.0), masters, TOL, "FLAT").id == "f10"
        assert best_master(_need(40.0), masters, TOL, "FLAT").id == "f40"
        assert flat_matches(_need(10.0), _flat_master(40.0), TOL) is False
        assert flat_matches(_need(40.0), _flat_master(10.0), TOL) is False

    def test_a_light_between_the_two_angles_matches_neither(self):
        masters = [_flat_master(10.0, mid="f10"), _flat_master(40.0, mid="f40"),
                   _dark_master()]
        assert best_master(_need(25.0), masters, TOL, "FLAT") is None
        gaps = coverage_for([_need(25.0)], masters, TOL)
        assert [g.missing for g in gaps] == [("flat",)]
        assert gaps[0].rotator_mech_deg == 25.0

    def test_unknown_on_either_side_is_no_constraint(self):
        """The reading ``_temp_ok`` applies to temperature: an old library, a
        light with no rotator and a flat with no card all keep matching.

        MUTANT "an unknown angle is a mismatch" (``matcher._rotator_ok``:
        the ``return True`` under ``not _known(m.rotator_mech_deg)``
        replaced by ``return False``). Observed:

            >       assert flat_matches(_need(None), _flat_master(40.0), TOL)
            E       AssertionError: assert False
            E        +  where False = flat_matches(LightNeed(... rotator_mech_deg=None),
            E           MasterRecord(... rotator_mech_deg=40.0), MatchTolerance(...))

        (8 of this file's cases go red under it)
        """
        assert flat_matches(_need(None), _flat_master(40.0), TOL)
        assert flat_matches(_need(10.0), _flat_master(None), TOL)
        assert flat_matches(_need(None), _flat_master(None), TOL)
        masters = [_flat_master(10.0, mid="f10"), _flat_master(40.0, mid="f40")]
        assert best_master(_need(None), masters, TOL, "FLAT") is not None

    def test_the_comparison_wraps_at_a_full_turn(self):
        """359.6 and 0.4 are 0.8 degree apart; a subtraction says 359.2."""
        assert flat_matches(_need(0.4), _flat_master(359.6), TOL)
        assert flat_matches(_need(359.6), _flat_master(0.4), TOL)
        assert not flat_matches(_need(2.0), _flat_master(359.6), TOL)

    def test_the_tolerance_is_the_dust_mote_one_and_is_a_parameter(self):
        assert ROTATION_TOL_DEG == 1.0 and TOL.rotator_tol_deg == ROTATION_TOL_DEG
        assert flat_matches(_need(10.0), _flat_master(10.9), TOL)
        assert not flat_matches(_need(10.0), _flat_master(11.5), TOL)
        wide = MatchTolerance(rotator_tol_deg=2.0)
        assert flat_matches(_need(10.0), _flat_master(11.5), wide)

    def test_a_dark_ignores_the_angle(self):
        """Shutter closed: a light's angle is no part of its dark."""
        from astrodeck.calibration.matcher import dark_matches
        assert dark_matches(_need(10.0), _dark_master(), TOL)

    def test_two_flats_in_tolerance_the_nearest_angle_wins(self):
        """Both pass, so the closer angle is the better flat, whatever the
        two stacks' depths: a deeper stack 0.9 degree off is worse than a
        shallower one at 0.1.

        MUTANT "best_master ranks flats without the angle"
        (``matcher.best_master``: ``(dang, dexp, dtemp, -m.frame_count)``
        replaced by ``(dexp, dtemp, -m.frame_count)``). Observed:

            >       assert best_master(_need(10.0), masters, TOL, "FLAT").id == "near"
            E       AssertionError: assert 'deep' == 'near'
            E         - near
            E         + deep
        """
        masters = [_flat_master(10.9, mid="deep", count=90),
                   _flat_master(10.1, mid="near", count=5)]
        assert best_master(_need(10.0), masters, TOL, "FLAT").id == "near"

    def test_a_measured_angle_beats_an_unknown_one(self):
        """An old master of no known angle still matches, but a flat measured
        at the light's own angle is the better evidence.

        MUTANT "an unknown angle ranks as a perfect one"
        (``matcher._rotator_gap``: the unknown-master ``return tol_deg``
        replaced by ``return 0.0``, so the deeper old stack wins the tie).
        Observed:

            >       assert best_master(_need(10.0), masters, TOL, "FLAT").id == "known"
            E       AssertionError: assert 'old' == 'known'
        """
        masters = [_flat_master(None, mid="old", count=90),
                   _flat_master(10.0, mid="known", count=5)]
        assert best_master(_need(10.0), masters, TOL, "FLAT").id == "known"

    def test_the_nearest_angle_is_the_nearest_across_the_wrap(self):
        """The rank is the WRAPPED difference, as the match is: a master at
        359.5 is 0.7 degree from a light at 0.2, and a master at 0.95 is 0.75
        from it. A plain subtraction ranks the first at 359.3 and picks the
        wrong flat, which the tolerance check alone (it wraps) cannot catch.

        MUTANT "the rank gap does not wrap" (``matcher._rotator_gap``: the
        return ``min(d, 360.0 - d)`` replaced by ``return d``). Observed:

            >       assert best_master(_need(0.2), masters, TOL, "FLAT").id == "across"
            E       AssertionError: assert 'same_side' == 'across'
            E         - across
            E         + same_side
        """
        masters = [_flat_master(0.95, mid="same_side"),
                   _flat_master(359.5, mid="across")]
        assert best_master(_need(0.2), masters, TOL, "FLAT").id == "across"

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -math.inf])
    def test_a_non_finite_angle_is_unknown_not_a_refusal(self, bad):
        """NaN compares false with everything, so letting one through to the
        comparison would refuse every flat: a constraint nobody wrote. It is
        unknown on either side, and unknown is no constraint.

        MUTANT "a non-finite angle is known" (``matcher._known``: the
        ``and math.isfinite(angle)`` dropped). Observed:

            >       assert flat_matches(_need(bad), _flat_master(40.0), TOL)
            E       AssertionError: assert False
            E        +  where False = flat_matches(LightNeed(... rotator_mech_deg=nan),
            E           MasterRecord(... rotator_mech_deg=40.0), MatchTolerance(...))

        (red on nan, inf and -inf alike)
        """
        assert flat_matches(_need(bad), _flat_master(40.0), TOL)
        assert flat_matches(_need(10.0), _flat_master(bad), TOL)
        assert best_master(_need(bad), [_flat_master(40.0)], TOL, "FLAT")


# ------------------------------------------------------------ the library

def _save_flat(tmp, name, mech, *, filt="Ha", exp=2.0, gain=100, offset=30,
               binning=1, sky=None, level=30000):
    """A flat on disk with ROTMECH written as a bare header card, so these
    tests read the library's behaviour and not the frame writer's (the
    writer has its own tests above)."""
    cards = []
    if mech is not None:
        cards.append(("ROTMECH", mech, "Rotator mechanical angle (deg)"))
    if sky is not None:
        cards.append(("ROTATANG", sky, "Rotator sky PA (deg)"))
    frame = CameraFrame(np.full((16, 16), level, np.uint16), exp, gain, offset,
                        binning, None, -10.0, 1_772_000_000.0)
    return save_fits(frame, tmp / f"{name}.fits", filter_name=filt,
                     frame_type="Flat", extra_cards=cards)


def _two_angle_library(tmp_path, angles=(10.0, 40.0), n=3):
    for a in angles:
        for i in range(n):
            _save_flat(tmp_path, f"flat_{a:g}_{i}", a, exp=2.0 + i / 10)
    return CalibrationLibrary(lambda: tmp_path)


class TestTheLibrary:
    def test_two_angles_are_two_buckets_two_masters(self, tmp_path):
        """Identical filter, gain, offset and binning at ROTMECH 10 and 40:
        two buckets with distinct ids, two masters with their own angles.

        MUTANT "key_index_id ignores the angle" (``keys.key_index_id``:
        ``if rbin is not None:`` replaced by ``if False:``). Observed:

            >       assert len(raw) == 2 and len({len(v) for v in raw.values()}) == 1
            E       AssertionError: assert (1 == 2)
            E        +  where 1 = len({'flat_g100_o30_b1_fHa': [<six paths>]})

        MUTANT "the record gets no angle" (``library.build``: the
        MasterRecord's ``rotator_mech_deg=key.rotator_mech_deg`` replaced by
        ``rotator_mech_deg=None``). Observed, on the angles of the two
        masters:

            >       assert sorted(m.rotator_mech_deg for m in masters
            E       assert [] == approx([10.0 ....0 +/- 4.0e-05])
            E         Impossible to compare lists with different sizes.
            E         Lengths: 2 and 0
        """
        lib = _two_angle_library(tmp_path)
        raw = lib.scan_raw(5.0)
        assert len(raw) == 2 and len({len(v) for v in raw.values()}) == 1
        rep = lib.build(temp_bin_width=5.0)
        assert (rep.masters_built, rep.frames_indexed) == (2, 6)
        masters = lib.list_masters()
        assert sorted(m.rotator_mech_deg for m in masters
                      if m.rotator_mech_deg is not None
                      ) == pytest.approx([10.0, 40.0])
        assert len({m.id for m in masters}) == 2
        assert all(m.frame_count == 3 for m in masters)

    def test_each_light_gets_its_own_master_and_a_gap_is_a_gap(self, tmp_path):
        lib = _two_angle_library(tmp_path)
        lib.build(temp_bin_width=5.0)
        masters = lib.list_masters()
        by_angle = {round(m.rotator_mech_deg): m.id for m in masters}
        assert best_master(_need(10.0), masters, TOL, "FLAT").id == by_angle[10]
        assert best_master(_need(40.0), masters, TOL, "FLAT").id == by_angle[40]
        assert best_master(_need(25.0), masters, TOL, "FLAT") is None
        # a light with no known angle matches either: one of the two
        assert best_master(_need(None), masters, TOL, "FLAT").id in by_angle.values()
        # a dark master is not in this library, so a covered need still
        # reports the dark gap and only the dark gap
        assert [g.missing for g in coverage_for([_need(10.0)], masters, TOL)
                ] == [("dark",)]
        assert [g.missing for g in coverage_for([_need(25.0)], masters, TOL)
                ] == [("dark", "flat")]

    def test_a_master_is_one_bin_of_frames(self, tmp_path):
        """Frames 0.9 degree apart share a bin and one master, whose angle is
        the mean of its frames' and whose card says so.

        MUTANT "the master's file carries no angle" (``library.
        _write_master_fits``: ``if key.rotator_mech_deg is not None:``
        replaced by ``if False:``). Observed:

            >       assert "ROTMECH" in hdr
            E       AssertionError: assert 'ROTMECH' in SIMPLE  = ...
        """
        for i, a in enumerate((10.0, 10.4, 10.8)):
            _save_flat(tmp_path, f"f{i}", a)
        lib = CalibrationLibrary(lambda: tmp_path)
        lib.build(temp_bin_width=5.0)
        (m,) = lib.list_masters()
        assert m.rotator_mech_deg == pytest.approx(10.4, abs=1e-3)
        hdr = fits.getheader(m.path)
        assert "ROTMECH" in hdr
        assert hdr["ROTMECH"] == pytest.approx(10.4, abs=1e-3)

    def test_a_bin_across_the_wrap_is_one_master_at_the_circular_mean(
            self, tmp_path):
        """359.6 and 0.4 are one set of flats, and their mean angle is 0.0,
        not the 180 an arithmetic mean calls it.

        MUTANT "the master's angle is the arithmetic mean"
        (``library._circular_mean``: the atan2 return replaced by
        ``round(sum(angles) / len(angles), 3)``). Observed:

            >       assert min(a, 360.0 - a) < 0.01
            E       assert 180.0 < 0.01
            E        +  where 180.0 = min(180.0, (360.0 - 180.0))
        """
        for i, a in enumerate((359.6, 0.4, 359.8, 0.2)):
            _save_flat(tmp_path, f"f{i}", a)
        lib = CalibrationLibrary(lambda: tmp_path)
        rep = lib.build(temp_bin_width=5.0)
        assert rep.masters_built == 1
        (m,) = lib.list_masters()
        a = m.rotator_mech_deg
        assert min(a, 360.0 - a) < 0.01
        assert flat_matches(_need(0.0), m, TOL)

    def test_the_sky_angle_does_not_split_a_flat_set(self, tmp_path):
        """Same physical angle, two nights, two syncs: the sky PA differs and
        the metal does not. One set."""
        _save_flat(tmp_path, "night1", 10.0, sky=33.0)
        _save_flat(tmp_path, "night2", 10.0, sky=91.0)
        lib = CalibrationLibrary(lambda: tmp_path)
        assert len(lib.scan_raw(5.0)) == 1

    def test_flats_without_the_card_keep_their_old_id_and_match_any_light(
            self, tmp_path):
        """A library built before ROTMECH: ids do not change and nothing is
        rebuilt under a new name."""
        for i in range(3):
            _save_flat(tmp_path, f"old{i}", None)
        lib = CalibrationLibrary(lambda: tmp_path)
        assert list(lib.scan_raw(5.0)) == ["flat_g100_o30_b1_fHa"]
        lib.build(temp_bin_width=5.0)
        (m,) = lib.list_masters()
        assert m.rotator_mech_deg is None
        assert m.path.endswith("flat_g100_o30_b1_fHa.fits")
        assert flat_matches(_need(10.0), m, TOL)
        assert "ROTMECH" not in fits.getheader(m.path)

    def test_old_and_new_flats_of_one_slot_are_separate_masters(self, tmp_path):
        for i in range(2):
            _save_flat(tmp_path, f"old{i}", None)
            _save_flat(tmp_path, f"new{i}", 10.0)
        lib = CalibrationLibrary(lambda: tmp_path)
        ids = sorted(lib.scan_raw(5.0))
        assert ids == ["flat_g100_o30_b1_fHa", "flat_g100_o30_b1_fHa_r5"]

    def test_the_bin_width_is_a_parameter_of_the_build(self, tmp_path):
        for i, a in enumerate((10.0, 14.0)):
            _save_flat(tmp_path, f"f{i}", a)
        lib = CalibrationLibrary(lambda: tmp_path)
        assert len(lib.scan_raw(5.0)) == 2                      # 2-degree bins
        assert len(lib.scan_raw(5.0, rotator_bin_deg=10.0)) == 1  # one 10-degree bin
        rep = lib.build(temp_bin_width=5.0, rotator_bin_deg=10.0)
        assert rep.masters_built == 1

    def test_a_slot_literally_named_like_a_suffix_does_not_share_a_file(
            self, tmp_path):
        """A slot named 'Ha_r5' with no angle and a slot 'Ha' at bin 5 would
        mint one id, one file. ``_distinct_ids`` finds the collision and
        digests both, so the longer id keeps the casefold guard."""
        for i in range(2):
            _save_flat(tmp_path, f"a{i}", None, filt="Ha_r5")
            _save_flat(tmp_path, f"b{i}", 10.0, filt="Ha")
        lib = CalibrationLibrary(lambda: tmp_path)
        raw = lib.scan_raw(5.0)
        assert len(raw) == 2 and len({k.casefold() for k in raw}) == 2
        assert lib.build(temp_bin_width=5.0).masters_built == 2
        filters = sorted(m.filter for m in lib.list_masters())
        assert filters == ["Ha", "Ha_r5"]
        # each master still belongs to its own slot and its own angle
        by_filter = {m.filter: m for m in lib.list_masters()}
        assert by_filter["Ha"].rotator_mech_deg == pytest.approx(10.0)
        assert by_filter["Ha_r5"].rotator_mech_deg is None

    def test_slots_that_differ_only_in_case_keep_the_casefold_guard(
            self, tmp_path):
        """'Ha' and 'HA' at one angle bin are one file on NTFS: both ids take
        a digest and both keep the angle suffix."""
        # File names that differ in more than case: on NTFS 'Ha0' and 'HA0'
        # are one file, and the second save would overwrite the first.
        for k, name in enumerate(("Ha", "HA")):
            for i in range(2):
                _save_flat(tmp_path, f"set{k}_{i}", 10.0, filt=name)
        lib = CalibrationLibrary(lambda: tmp_path)
        ids = sorted(lib.scan_raw(5.0))
        assert len(ids) == 2 and len({i.casefold() for i in ids}) == 2
        assert all(i.endswith("_r5") and "~" in i for i in ids)

    def test_the_digested_ids_keep_the_builds_bin_width(self, tmp_path):
        """The digest pass re-mints the colliding ids, and it must mint them
        at the width the build was asked for: at the default two degrees these
        14-degree flats are bin 7, at ten degrees they are bin 1, and a digest
        id minted at the wrong width would put two bins' flats in one file
        name and a bin's flats under another's.

        MUTANT "the digest pass forgets the width" (``library._distinct_ids``:
        the ``rotator_bin_deg=rotator_bin_deg`` argument of the
        ``digest=True`` call dropped). Observed:

            >       assert [i.rsplit("_", 1)[1] for i in ids] == ["r1", "r1"]
            E       AssertionError: assert ['r7', 'r7'] == ['r1', 'r1']
            E         At index 0 diff: 'r7' != 'r1'
        """
        for k, name in enumerate(("Ha", "HA")):
            for i in range(2):
                _save_flat(tmp_path, f"wide{k}_{i}", 14.0, filt=name)
        lib = CalibrationLibrary(lambda: tmp_path)
        ids = sorted(lib.scan_raw(5.0, rotator_bin_deg=10.0))
        assert len(ids) == 2 and "~" in ids[0] and "~" in ids[1]
        assert [i.rsplit("_", 1)[1] for i in ids] == ["r1", "r1"]


class TestTheManifest:
    def _write_manifest(self, tmp_path, rows):
        d = tmp_path / "_masters"
        d.mkdir(parents=True)
        (d / "masters.json").write_text(json.dumps(
            {"schema_version": 1, "masters": rows}), encoding="utf-8")

    def _old_row(self, **over):
        row = {"id": "flat_g100_o30_b1_fHa", "frame_type": "FLAT",
               "exposure_s": 2.0, "gain": 100, "offset": 30, "temp_c": None,
               "binning": 1, "filter": "Ha", "frame_count": 20,
               "path": "/m/flat.fits", "built_ts": 1.0}
        row.update(over)
        return row

    def test_a_manifest_written_before_the_field_still_lists_its_masters(
            self, tmp_path):
        """A manifest an older build wrote has no ``rotator_mech_deg`` key,
        and requiring it would drop every master the library holds from the
        list, so the operator's whole library would read empty after the
        upgrade.

        MUTANT "_valid_row left requiring the new field"
        (``library._valid_row``: ``for k in _MASTER_FIELDS`` replaced by
        ``for k in _MASTER_FIELDS + _OPTIONAL_MASTER_FIELDS``). Observed:

            >       assert [m.id for m in masters] == ["flat_g100_o30_b1_fHa"]
            E       AssertionError: assert [] == ['flat_g100_o30_b1_fHa']
            E         Right contains one more item: 'flat_g100_o30_b1_fHa'
        """
        self._write_manifest(tmp_path, [self._old_row()])
        lib = CalibrationLibrary(lambda: tmp_path)
        masters = lib.list_masters()
        assert [m.id for m in masters] == ["flat_g100_o30_b1_fHa"]
        assert masters[0].rotator_mech_deg is None

    def test_a_row_with_the_field_round_trips_and_a_junk_field_reads_unknown(
            self, tmp_path):
        """MUTANT "list_masters drops the angle" (``library.list_masters``:
        ``mech_angle(r.get(k))`` replaced by ``None``). Observed:

            >       assert got == {"a": 40.0, "b": None, "c": None}
            E       AssertionError: assert {'a': None, '...ne, 'c': None} == {'a': 40.0, '...ne, 'c': None}
        """
        self._write_manifest(tmp_path, [
            self._old_row(id="a", rotator_mech_deg=40.0),
            self._old_row(id="b", rotator_mech_deg="n/a"),
            self._old_row(id="c", rotator_mech_deg=None)])
        got = {m.id: m.rotator_mech_deg
               for m in CalibrationLibrary(lambda: tmp_path).list_masters()}
        assert got == {"a": 40.0, "b": None, "c": None}

    def test_a_row_missing_a_required_field_is_still_dropped(self, tmp_path):
        row = self._old_row()
        del row["gain"]
        self._write_manifest(tmp_path, [row, self._old_row(id="ok")])
        got = [m.id for m in CalibrationLibrary(lambda: tmp_path).list_masters()]
        assert got == ["ok"]

    def test_the_record_serialises_its_angle_for_the_masters_route(self):
        """``/api/calibration/masters`` returns ``vars(m)``."""
        assert vars(_flat_master(10.0))["rotator_mech_deg"] == 10.0
        assert vars(_flat_master(None))["rotator_mech_deg"] is None

    def test_a_built_manifest_round_trips_the_angle(self, tmp_path):
        lib = _two_angle_library(tmp_path)
        lib.build(temp_bin_width=5.0)
        again = CalibrationLibrary(lambda: tmp_path).list_masters()
        assert sorted(m.rotator_mech_deg for m in again) == pytest.approx(
            [10.0, 40.0])


# ------------------------------------------------------------ the health matrix

def _frames_at(angle, n=20, **kw):
    return [CalFrame(key=CalKey("FLAT", 2.0, 100, 30, None, 1, "Ha", angle),
                     ts=NOW, path=f"cal/f{angle}_{i}.fits", **kw)
            for i in range(n)]


def _row(rows, kind="FLAT"):
    return next(r for r in rows if r.kind == kind)


def _health_need(angle) -> CalNeed:
    return CalNeed(LightNeed(300.0, 100, 30, -5.0, 1, "Ha"), rotation_deg=angle)


class TestTheHealthMatrix:
    def test_the_matrix_reads_the_mechanical_card(self):
        """MUTANT "the card is read from ROTATANG instead of ROTMECH"
        (``keys.mech_angle_from_header``: ``header.get(ROTATOR_CARD, None)``
        replaced by ``header.get("ROTATANG", None)``). Observed:

            >       assert f.rotation_deg == pytest.approx(23.4)
            E       assert 99.0 == 23.4 +/- 2.3e-05
            E         Obtained: 99.0

        (16 of this file's cases go red under it; the frame-header bridge
        has no reading of its own to mutate)
        """
        f = frame_from_header({"IMAGETYP": "FLAT", "FILTER": "Ha",
                               "ROTMECH": 23.4, "ROTATANG": 99.0}, ts=NOW)
        assert f.rotation_deg == pytest.approx(23.4)
        assert f.key.rotator_mech_deg == pytest.approx(23.4)

    def test_a_frame_with_only_the_sky_card_is_unknown_not_compared(self):
        f = frame_from_header({"IMAGETYP": "FLAT", "FILTER": "Ha",
                               "ROTATANG": 99.0}, ts=NOW)
        assert f.rotation_deg is None and f.key.rotator_mech_deg is None

    def test_a_flat_at_another_mechanical_angle_is_STALE_from_its_own_header(
            self):
        """End to end through the header bridge, with the need's angle given
        to the matrix and the frames' read off their own headers: the matrix
        and the matcher read one number.

        MUTANT "the matrix's row light carries no angle"
        (``calibration_health._build_row``: ``lt = _light_with_angle(need)``
        replaced by ``lt = need.light``). Observed:

            >       assert row.verdict == "STALE"
            E       AssertionError: assert 'OK' == 'STALE'
        """
        frames = [frame_from_header(_flat_header(ROTMECH=40.0), ts=NOW,
                                    path=f"f{i}.fits") for i in range(20)]
        row = _row(health_matrix([_health_need(10.0)], frames,
                                 kinds=["FLAT"], now=NOW))
        assert row.verdict == "STALE"
        assert "drift" in [r.code for r in row.reasons]

    def test_a_flat_at_the_needed_angle_is_OK_across_the_wrap(self):
        frames = _frames_at(359.6)
        row = _row(health_matrix([_health_need(0.4)], frames,
                                 kinds=["FLAT"], now=NOW))
        assert row.verdict == "OK"

    def test_a_master_at_another_angle_is_not_credited(self):
        """The row must credit the master the PIPELINE would apply, and the
        matcher now refuses a flat master 30 degrees from the light.

        MUTANT "the matrix asks the matcher without the angle"
        (``calibration_health._build_row``: ``_pick_master(kind, lt, ...)``
        replaced by ``_pick_master(kind, need.light, ...)``). Observed:

            >       assert row.from_master == 0 and row.verdict == "MISSING"
            E       AssertionError: assert (20 == 0)
            E        +  where 20 = HealthRow(kind='FLAT', ... from_master=20).from_master
        """
        rows = health_matrix([_health_need(10.0)], [],
                             masters=[_flat_master(40.0)], kinds=["FLAT"],
                             now=NOW)
        row = _row(rows)
        assert row.from_master == 0 and row.verdict == "MISSING"
        assert row.master_id is None

    def test_a_master_at_the_needed_angle_is_credited(self):
        rows = health_matrix([_health_need(10.0)], [],
                             masters=[_flat_master(40.0, mid="far"),
                                      _flat_master(10.2, mid="near")],
                             kinds=["FLAT"], now=NOW)
        row = _row(rows)
        assert (row.master_id, row.from_master) == ("near", 20)

    def test_wrong_angle_frames_are_the_same_set_drifted_not_never_shot(self):
        """STALE ('refresh these') and MISSING ('you have never had these')
        are different jobs. The angle is a CONDITION axis, lifted out of the
        family test as temperature is.

        MUTANT "family keeps the angle" (``calibration_health._in_family``:
        ``replace(need, temp_c=None, rotator_mech_deg=None)`` replaced by
        ``replace(need, temp_c=None)``). Observed:

            >       assert row.verdict == "STALE" and row.family == 20 and row.have == 0
            E       AssertionError: assert ('MISSING' == 'STALE'
            E         - STALE
            E         + MISSING)
        """
        row = _row(health_matrix([_health_need(10.0)], _frames_at(40.0),
                                 kinds=["FLAT"], now=NOW))
        assert row.verdict == "STALE" and row.family == 20 and row.have == 0

    def test_a_flat_row_names_the_angle_it_is_for(self):
        """The row says which angle it asks for, whether the need carried it
        on the ``CalNeed`` or on its ``LightNeed``, and a dark's row says none.

        MUTANT "the flat row drops its angle" (``calibration_health.
        _build_row``: ``rotation_deg=lt.rotator_mech_deg if kind == "FLAT"
        else None`` replaced by ``rotation_deg=None``). Observed:

            >       assert _row(rows).rotation_deg == pytest.approx(10.0)
            E       assert None == 10.0 +/- 1.0e-05
            E         Obtained: None
        """
        rows = health_matrix([_health_need(10.0)], _frames_at(10.0),
                             kinds=["DARK", "FLAT"], now=NOW)
        assert _row(rows).rotation_deg == pytest.approx(10.0)
        assert _row(rows, "DARK").rotation_deg is None
        own = CalNeed(LightNeed(300.0, 100, 30, -5.0, 1, "Ha", 25.0))
        assert _row(health_matrix([own], [], kinds=["FLAT"],
                                  now=NOW)).rotation_deg == pytest.approx(25.0)

    def test_a_need_carrying_its_own_angle_is_honoured(self):
        """``LightNeed.rotator_mech_deg`` set directly is the same demand as
        ``CalNeed.rotation_deg``."""
        need = CalNeed(LightNeed(300.0, 100, 30, -5.0, 1, "Ha", 10.0))
        row = _row(health_matrix([need], _frames_at(40.0), kinds=["FLAT"],
                                 now=NOW))
        assert row.verdict == "STALE"

    def test_the_tolerance_comes_from_the_matchers_one_number(self):
        """No second constant: the matrix's default is the matcher's.

        MUTANT "the matrix's own tolerance is dropped"
        (``calibration_health.health_matrix``: the ``tol = replace(tol,
        rotator_tol_deg=...)`` override replaced by ``pass``). Observed:

            >       assert _row(far).verdict == "STALE"
            E       AssertionError: assert 'OK' == 'STALE'
        """
        from astrodeck.flows import calibration_health as ch
        assert ch.ROTATION_TOL_DEG is ROTATION_TOL_DEG or (
            ch.ROTATION_TOL_DEG == ROTATION_TOL_DEG == 1.0)
        near = health_matrix([_health_need(10.0)], _frames_at(10.9),
                             kinds=["FLAT"], now=NOW)
        assert _row(near).verdict == "OK"
        far = health_matrix([_health_need(10.0)], _frames_at(10.9),
                            kinds=["FLAT"], rotation_tol_deg=0.5, now=NOW)
        assert _row(far).verdict == "STALE"
        via_tol = health_matrix([_health_need(10.0)], _frames_at(10.9),
                                kinds=["FLAT"],
                                tol=MatchTolerance(rotator_tol_deg=0.5), now=NOW)
        assert _row(via_tol).verdict == "STALE"
