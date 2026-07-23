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
