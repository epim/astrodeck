"""A weather hold tops the dark library up; it does not re-shoot it.

The darks leg landed taking the full quota on every hold. On a cloudy night that
spends the dead time re-taking darks the rig already owns, fills the disk, and
buys nothing — the queue node's own vocabulary says "If library stale", and
nothing was reading the library.

It could not: until the matrix counted raw subs, `have` saw only stacked
masters, so a rig with 174 darks on disk looked empty. That fixed, the engine
asks the SAME question the Calibration Matrix panel asks — through
`health_matrix` — so a panel saying "20/20 OK" and a hold shooting 20 more
cannot both be true at once.
"""
from __future__ import annotations

import numpy as np

from astrodeck.calibration.library import CalibrationLibrary
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import save_fits
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan


def _dark(tmp, i, *, exp=180.0, gain=125, temp=-5.0):
    f = CameraFrame(data=np.full((16, 12), 100 + i, np.uint16), exposure_s=exp,
                    gain=gain, offset=30, binning=1, bayer_pattern=None,
                    temperature_c=temp, timestamp=1_772_000_000.0 + i)
    return save_fits(f, tmp / f"dark_{i}.fits", frame_type="Dark")


class _Hub:
    def __init__(self, library=None):
        self.master_library = library
        self.devices = {}
        self.guider = None
        self.site = {}


def _engine(tmp_path, *, library=True, cool_to=-5.0):
    lib = CalibrationLibrary(lambda: tmp_path) if library else None
    eng = SequenceEngine(_Hub(lib))
    eng.plan = SequencePlan(name="n", cloud_hold_darks=20, cool_to=cool_to)
    return eng


STEP = ExposureStep(exposure_s=180.0, gain=125, offset=30, binning=1,
                    count=30, filter="Ha")


class TestTheShortfall:
    def test_an_empty_library_wants_the_whole_quota(self, tmp_path):
        want, have = _engine(tmp_path)._hold_darks_shortfall(STEP, 20)
        assert (want, have) == (20, 0)

    def test_a_partly_stocked_library_wants_only_the_remainder(self, tmp_path):
        for i in range(6):
            _dark(tmp_path, i)
        want, have = _engine(tmp_path)._hold_darks_shortfall(STEP, 20)
        assert (want, have) == (14, 6)

    def test_a_full_library_wants_none(self, tmp_path):
        for i in range(20):
            _dark(tmp_path, i)
        want, have = _engine(tmp_path)._hold_darks_shortfall(STEP, 20)
        assert want == 0 and have >= 20

    def test_an_overfull_library_never_asks_for_a_negative_count(self, tmp_path):
        for i in range(35):
            _dark(tmp_path, i)
        want, _ = _engine(tmp_path)._hold_darks_shortfall(STEP, 20)
        assert want == 0

    def test_darks_at_the_wrong_settings_do_not_count(self, tmp_path):
        """The whole point of matching. 30 darks at 60 s are not cover for a
        180 s light, and a hold that counted them would leave the night's frames
        uncalibrated while reporting the library full."""
        for i in range(30):
            _dark(tmp_path, i, exp=60.0)
        want, have = _engine(tmp_path)._hold_darks_shortfall(STEP, 20)
        assert (want, have) == (20, 0)

    def test_a_warm_dark_does_not_count_for_a_cooled_run(self, tmp_path):
        for i in range(30):
            _dark(tmp_path, i, temp=20.0)
        want, have = _engine(tmp_path, cool_to=-5.0)._hold_darks_shortfall(STEP, 20)
        assert (want, have) == (20, 0)


class TestItFailsToTheOldBehaviourNotToZero:
    """A hold that cannot count is still a hold that should build darks."""

    def test_no_library_on_the_hub_shoots_the_full_quota(self, tmp_path):
        want, have = _engine(tmp_path, library=False)._hold_darks_shortfall(STEP, 20)
        assert (want, have) == (20, 0)

    def test_a_library_that_raises_shoots_the_full_quota(self, tmp_path):
        class Exploding:
            def iter_cal_headers(self):
                raise OSError("the capture volume went away mid-walk")

            def list_masters(self):
                return []

        eng = SequenceEngine(_Hub(Exploding()))
        eng.plan = SequencePlan(name="n", cloud_hold_darks=20, cool_to=-5.0)
        assert eng._hold_darks_shortfall(STEP, 20) == (20, 0)

    def test_one_unreadable_file_does_not_cancel_the_feature(self, tmp_path):
        for i in range(6):
            _dark(tmp_path, i)
        (tmp_path / "truncated.fits").write_bytes(b"not FITS")
        want, have = _engine(tmp_path)._hold_darks_shortfall(STEP, 20)
        assert (want, have) == (14, 6), "a junk file changed the count"
