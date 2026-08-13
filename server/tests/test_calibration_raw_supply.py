"""The matrix counts what is on disk, not only what has been stacked.

THE DEFECT. ``/api/calibration/health`` shipped with ``counts_masters_only:
True`` — an honest flag on a dishonest number. ``have`` counted stacked masters
only, so a rig holding 174 perfectly good raw darks read MISSING across the
board, and the queue built on top of it could never answer "is the library
stale?" because it could not see the library.

``calibration_health.frame_from_header`` had asked for the fix by name: "the
route needs a walk to feed this, and there are already two … please extend one
rather than adding a third rglob over the capture root." So the walk was split
out of ``_bucket_raw`` as ``iter_cal_headers`` and both callers now share it —
which is what keeps the matrix and the stacker looking at one library instead of
two.

The split has a deliberate asymmetry, and it is the thing most worth pinning:
the walk yields CONTRADICTED frames and the stacker drops them. "You have 40
darks and the check threw 12 out" and "you have 28 darks" are different facts,
and only the first tells the operator what to do about it.
"""
from __future__ import annotations

import numpy as np
from astropy.io import fits

from astrodeck.calibration.library import DARK_OK_CARD, CalibrationLibrary
from astrodeck.devices.base import CameraFrame
from astrodeck.flows.calibration_health import (CalNeed, frame_from_header,
                                                health_matrix)
from astrodeck.calibration.matcher import LightNeed
from astrodeck.imaging.fitsio import save_fits


def _dark(tmp, i, *, exp=180.0, temp=-5.0, gain=125, dark_ok=None):
    f = CameraFrame(data=np.full((16, 12), 100 + i, np.uint16), exposure_s=exp,
                    gain=gain, offset=30, binning=1, bayer_pattern=None,
                    temperature_c=temp, timestamp=1_772_000_000.0 + i)
    path = save_fits(f, tmp / f"dark_{i}.fits", frame_type="Dark")
    if dark_ok is not None:
        with fits.open(path, mode="update") as hdul:
            hdul[0].header[DARK_OK_CARD] = dark_ok
            if dark_ok is False:
                hdul[0].header["DARKWHY"] = "pinned at the sensor ceiling"
    return path


def _scan(lib) -> list:
    return [f for f in (frame_from_header(h, ts=ts, path=str(p))
                        for p, h, ts in lib.iter_cal_headers())
            if f is not None]


def _need(exp=180.0, gain=125, temp=-5.0):
    return CalNeed(LightNeed(exposure_s=exp, gain=gain, offset=30, temp_c=temp,
                             binning=1, filter="Ha"))


class TestTheWalkFindsRawFrames:
    def test_raw_subs_are_supply_with_no_master_built(self, tmp_path):
        for i in range(6):
            _dark(tmp_path, i)
        lib = CalibrationLibrary(lambda: tmp_path)
        assert lib.list_masters() == [], "no master has been built"
        rows = health_matrix([_need()], _scan(lib), quota=20, kinds=("DARK",))
        assert rows and rows[0].have == 6, (
            "six usable darks are on disk and the matrix cannot see them")

    def test_the_capture_root_exclusions_still_apply(self, tmp_path):
        """A frame the user deleted is in `_trash` and must not come back as
        supply — the same rule that stops it being stacked into a master."""
        _dark(tmp_path, 0)
        trash = tmp_path / "_trash"
        trash.mkdir()
        for i in range(1, 4):
            _dark(trash, i)
        lib = CalibrationLibrary(lambda: tmp_path)
        rows = health_matrix([_need()], _scan(lib), quota=20, kinds=("DARK",))
        assert rows[0].have == 1, "a deleted frame was counted as supply"

    def test_an_unreadable_file_does_not_blind_the_whole_matrix(self, tmp_path):
        for i in range(3):
            _dark(tmp_path, i)
        (tmp_path / "truncated.fits").write_bytes(b"not a FITS file at all")
        lib = CalibrationLibrary(lambda: tmp_path)
        rows = health_matrix([_need()], _scan(lib), quota=20, kinds=("DARK",))
        assert rows[0].have == 3


class TestTheAsymmetryBetweenTheTwoCallers:
    """The walk yields contradicted frames; the stacker refuses them."""

    def test_the_stacker_still_refuses_a_contradicted_frame(self, tmp_path):
        for i in range(4):
            _dark(tmp_path, i, dark_ok=True)
        _dark(tmp_path, 99, dark_ok=False)
        lib = CalibrationLibrary(lambda: tmp_path)
        rep = lib.build(temp_bin_width=5.0)
        assert rep.frames_indexed == 4, (
            "the contradicted frame was stacked into a master — subtracting a "
            "master built from white frames erases the light")

    def test_the_walk_still_yields_it(self, tmp_path):
        for i in range(4):
            _dark(tmp_path, i, dark_ok=True)
        _dark(tmp_path, 99, dark_ok=False)
        lib = CalibrationLibrary(lambda: tmp_path)
        frames = _scan(lib)
        assert len(frames) == 5, "the matrix cannot report what it never sees"
        assert sum(1 for f in frames if f.dark_ok is False) == 1

    def test_an_unjudged_frame_is_not_a_bad_one(self, tmp_path):
        """No card at all = every dark shot before the check existed. Reading
        that as a rejection would delete a working library on upgrade day."""
        for i in range(3):
            _dark(tmp_path, i)                       # no DARKOK card
        lib = CalibrationLibrary(lambda: tmp_path)
        frames = _scan(lib)
        assert [f.dark_ok for f in frames] == [None, None, None]
        assert lib.build(temp_bin_width=5.0).frames_indexed == 3


class TestTheTemperatureActuallyMatters:
    def test_a_warm_dark_does_not_cover_a_cold_light(self, tmp_path):
        """The reason the route now passes the rig's setpoint instead of None:
        matched on None these rows would have been indistinguishable."""
        for i in range(5):
            _dark(tmp_path, i, temp=20.0)
        lib = CalibrationLibrary(lambda: tmp_path)
        rows = health_matrix([_need(temp=-5.0)], _scan(lib), quota=20,
                             kinds=("DARK",))
        assert rows[0].have == 0, "a +20 °C dark was counted for a -5 °C light"

    def test_a_matching_dark_does_cover_it(self, tmp_path):
        for i in range(5):
            _dark(tmp_path, i, temp=-5.0)
        lib = CalibrationLibrary(lambda: tmp_path)
        rows = health_matrix([_need(temp=-5.0)], _scan(lib), quota=20,
                             kinds=("DARK",))
        assert rows[0].have == 5
