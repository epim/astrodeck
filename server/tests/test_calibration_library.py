import numpy as np
from astropy.io import fits
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import save_fits
from astrodeck.calibration.library import CalibrationLibrary
from astrodeck.calibration.stacker import sigma_clip_mean


def _dark(tmp, i, exp=300.0, temp=-10.0):
    f = CameraFrame(data=np.full((32, 24), 100 + i, np.uint16), exposure_s=exp,
                    gain=100, offset=30, binning=1, bayer_pattern=None,
                    temperature_c=temp, timestamp=1_772_000_000.0 + i)
    return save_fits(f, tmp / f"dark_{i}.fits", frame_type="Dark")


def test_build_creates_one_master_per_bucket(tmp_path):
    for i in range(5):
        _dark(tmp_path, i)
    lib = CalibrationLibrary(lambda: tmp_path)
    rep = lib.build(temp_bin_width=5.0)
    assert rep.masters_built == 1 and rep.frames_indexed == 5
    masters = lib.list_masters()
    assert len(masters) == 1 and masters[0].frame_type == "DARK"
    assert masters[0].frame_count == 5


def test_streamed_build_equals_in_memory_stack(tmp_path):
    arrs = [np.random.default_rng(i).integers(90, 110, (40, 24), np.uint16) for i in range(6)]
    for i, a in enumerate(arrs):
        save_fits(CameraFrame(a, 300.0, 100, 30, 1, None, -10.0, 1.0 + i),
                  tmp_path / f"d{i}.fits", frame_type="Dark")
    lib = CalibrationLibrary(lambda: tmp_path)
    lib.build(temp_bin_width=5.0, strip_rows=7)          # strip does not divide 40
    m = lib.list_masters()[0]
    got = fits.getdata(m.path)
    want = sigma_clip_mean(arrs)                          # pure reference
    assert np.allclose(got, want, atol=1e-3)


def test_masters_dir_and_solve_are_excluded(tmp_path):
    _dark(tmp_path, 0)
    lib = CalibrationLibrary(lambda: tmp_path)
    lib.build()
    # a second build must not re-index the master it just wrote under _masters/
    rep = lib.build()
    assert rep.frames_indexed == 1


def test_delete_removes_fits_and_row(tmp_path):
    _dark(tmp_path, 0)
    lib = CalibrationLibrary(lambda: tmp_path)
    lib.build()
    mid = lib.list_masters()[0].id
    lib.delete(mid)
    assert lib.list_masters() == []
    assert not (tmp_path / "_masters" / f"{mid}.fits").exists()


def test_flat_bucket_ignores_exposure(tmp_path):
    for i, exp in enumerate((2.0, 3.5, 2.8)):
        save_fits(CameraFrame(np.full((16, 16), 30000, np.uint16), exp, 100, 30, 1,
                              None, -10.0, 1.0 + i), tmp_path / f"flat_{i}.fits",
                  frame_type="Flat", filter_name="Ha")
    lib = CalibrationLibrary(lambda: tmp_path)
    rep = lib.build()
    assert rep.masters_built == 1 and lib.list_masters()[0].filter == "Ha"


# ------------------------------------------------- white frames are not darks
#
# On 2026-08-01 the wheel slot ticked ``filter_opaque`` turned out to be EMPTY,
# and three daylight "darks" came back at median 65535 — white frames, filed as
# a dark library. ``imaging.darks`` was written to catch exactly that, and its
# own docstring describes the contract that closes the damage path: the capture
# stamps ``DARKOK``, and "the library skips a frame whose DARKOK is False and
# MUST default it to True when the card is absent".
#
# Neither half existed. judge_dark had NO caller anywhere in astrodeck/ — a
# fully built, thoroughly tested detector wired to nothing, describing a
# protection it did not provide. Subtract one of those masters from a light and
# the light is gone.

def _dark_with_card(tmp, i, *, ok, exp=300.0, temp=-10.0):
    p = _dark(tmp, i, exp=exp, temp=temp)
    with fits.open(p, mode="update") as hdul:
        hdul[0].header["DARKOK"] = ok
        hdul[0].header["DARKCHK"] = "dark" if ok else "saturated"
        hdul[0].header["DARKWHY"] = "test fixture"
    return p


def test_a_frame_the_dark_check_rejected_is_never_stacked(tmp_path):
    for i in range(3):
        _dark_with_card(tmp_path, i, ok=True)
    for i in range(3, 6):
        _dark_with_card(tmp_path, i, ok=False)
    lib = CalibrationLibrary(lambda: tmp_path)
    rep = lib.build(temp_bin_width=5.0)
    assert rep.masters_built == 1
    assert lib.list_masters()[0].frame_count == 3, \
        "the three rejected frames were stacked into the master dark"


def test_a_bucket_of_nothing_but_rejects_builds_no_master(tmp_path):
    """Not an empty master, and not a master of three white frames: no master.
    Something has to be left for the operator to notice."""
    for i in range(3):
        _dark_with_card(tmp_path, i, ok=False)
    lib = CalibrationLibrary(lambda: tmp_path)
    rep = lib.build(temp_bin_width=5.0)
    assert rep.masters_built == 0
    assert lib.list_masters() == []


def test_frames_with_no_card_still_index(tmp_path):
    """Back-compat, and it is load-bearing: every dark taken before the check
    existed has no DARKOK card, and defaulting a missing card to False would
    silently delete an existing library."""
    for i in range(4):
        _dark(tmp_path, i)
    lib = CalibrationLibrary(lambda: tmp_path)
    rep = lib.build(temp_bin_width=5.0)
    assert rep.masters_built == 1
    assert lib.list_masters()[0].frame_count == 4
