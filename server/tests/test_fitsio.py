"""FITS header correctness (save_fits)."""
import re

import numpy as np
import pytest
from astropy.io import fits
from astropy.time import Time

from astrodeck import __version__
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import FrameMeta, save_fits

# FITS 4.0 sec 4.4.2: 'YYYY-MM-DDThh:mm:ss[.s...]' with NO timezone designator.
_DATE_OBS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")


def _frame(timestamp: float = 1_772_775_791.123456) -> CameraFrame:
    return CameraFrame(
        data=np.zeros((16, 16), dtype=np.uint16),
        exposure_s=1.0,
        gain=100,
        offset=10,
        binning=1,
        bayer_pattern=None,
        temperature_c=-10.0,
        timestamp=timestamp,
    )


def _frame_binned(binning: int = 2):
    return CameraFrame(
        data=np.zeros((16, 16), dtype=np.uint16),
        exposure_s=30.0, gain=100, offset=10, binning=binning,
        bayer_pattern=None, temperature_c=-9.8, timestamp=1_772_775_791.0)


def test_all_new_cards_present_and_correct(tmp_path):
    meta = FrameMeta(
        focal_length_mm=530.0, pixel_size_um=3.76,
        site_lat_deg=40.0, site_lon_deg=-105.0, site_elev_m=1600.0,
        obj_alt_deg=55.0, airmass=1.221,
        objctra="05 35 17.9", objctdec="-05 23 28",
        set_temp_c=-10.0, focuser_pos=12345, focuser_temp_c=5.5,
        rotator_angle_deg=123.4, egain_e_per_adu=0.8, hfr=2.1, star_count=42)
    path = save_fits(_frame_binned(2), tmp_path / "light.fits",
                     ra_hours=5.5, dec_deg=-5.39, meta=meta)
    with fits.open(path) as hdul:
        h = hdul[0].header
    assert h["FOCALLEN"] == pytest.approx(530.0)
    assert h["XPIXSZ"] == pytest.approx(3.76 * 2)   # pixel_size_um x binning
    assert h["YPIXSZ"] == pytest.approx(h["XPIXSZ"])
    assert h["SITELAT"] == pytest.approx(40.0)
    assert h["SITELONG"] == pytest.approx(-105.0)
    assert h["SITEELEV"] == pytest.approx(1600.0)
    assert h["OBJCTALT"] == pytest.approx(55.0)
    assert h["AIRMASS"] == pytest.approx(1.221)
    assert h["OBJCTRA"] == "05 35 17.9"
    assert h["OBJCTDEC"] == "-05 23 28"
    assert h["SET-TEMP"] == pytest.approx(-10.0)
    assert h["FOCPOS"] == 12345
    assert h["FOCTEMP"] == pytest.approx(5.5)
    assert h["ROTATANG"] == pytest.approx(123.4)
    assert h["EGAIN"] == pytest.approx(0.8)
    assert h["HFR"] == pytest.approx(2.1)
    assert h["STARCNT"] == 42
    assert h["EQUINOX"] == pytest.approx(2000.0)
    assert h["RADESYS"] == "ICRS"


def test_swcreate_is_real_version(tmp_path):
    path = save_fits(_frame(), tmp_path / "light.fits")
    with fits.open(path) as hdul:
        sw = hdul[0].header["SWCREATE"]
    assert sw == f"AstroDeck {__version__}"
    assert "0.1.0" not in sw


def test_optional_cards_omitted_when_absent(tmp_path):
    # meta=None (throwaway-solve caller behavior): only core cards, no placeholders
    path = save_fits(_frame(), tmp_path / "light.fits")
    with fits.open(path) as hdul:
        h = hdul[0].header
    for absent in ("FOCPOS", "SITELAT", "SET-TEMP", "AIRMASS", "EGAIN",
                   "ROTATANG", "XPIXSZ", "OBJCTRA"):
        assert absent not in h, absent
    for present in ("EXPTIME", "GAIN", "DATE-OBS"):
        assert present in h, present


def test_telescop_written_only_when_named(tmp_path):
    p1 = save_fits(_frame(), tmp_path / "named.fits", telescope="Askar 71F")
    with fits.open(p1) as hdul:
        assert hdul[0].header["TELESCOP"] == "Askar 71F"
    p2 = save_fits(_frame(), tmp_path / "blank.fits", telescope="")
    with fits.open(p2) as hdul:
        assert "TELESCOP" not in hdul[0].header


def test_airmass_omitted_below_horizon(tmp_path):
    # caller passes airmass=None (its coords.airmass helper returns None <= horizon)
    meta = FrameMeta(obj_alt_deg=-3.0, airmass=None)
    path = save_fits(_frame(), tmp_path / "light.fits",
                     ra_hours=5.5, dec_deg=-5.39, meta=meta)
    with fits.open(path) as hdul:
        assert "AIRMASS" not in hdul[0].header


def test_date_obs_has_no_timezone_suffix(tmp_path):
    path = save_fits(_frame(), tmp_path / "light.fits")
    with fits.open(path) as hdul:
        date_obs = hdul[0].header["DATE-OBS"]
    assert "+" not in date_obs and "Z" not in date_obs
    assert _DATE_OBS_RE.match(date_obs), date_obs


def test_date_obs_parses_as_fits_time(tmp_path):
    # astropy's strict FITS-format Time parser rejects a timezone-suffixed
    # DATE-OBS -- this is exactly what downstream tools (plate-solvers,
    # session sorters) use, and what the malformed header used to break.
    path = save_fits(_frame(), tmp_path / "light.fits")
    with fits.open(path) as hdul:
        date_obs = hdul[0].header["DATE-OBS"]
    t = Time(date_obs, format="fits")
    assert t.isot.startswith("2026-03-")


def test_wcs_writeback_roundtrips(tmp_path):
    from astrodeck.imaging.fitsio import write_wcs
    from astrodeck.solve.base import WcsSolution
    from astropy.wcs import WCS
    path = save_fits(_frame(), tmp_path / "light.fits")   # 16x16 -> center 8.5
    scale = 1.5 / 3600.0
    wcs = WcsSolution(crval1=83.8221, crval2=-5.3911, crpix1=8.5, crpix2=8.5,
                      cd11=-scale, cd12=0.0, cd21=0.0, cd22=scale)
    write_wcs(path, wcs)
    with fits.open(path) as hdul:
        h = hdul[0].header
        assert h["CTYPE1"] == "RA---TAN"
        assert h["EQUINOX"] == 2000.0
        w = WCS(h)
        assert w.has_celestial
        world = w.wcs_pix2world([[wcs.crpix1 - 1, wcs.crpix2 - 1]], 0)[0]
        # Exercise the CD matrix OFF the reference pixel (at the reference, CD·0
        # vanishes and a sign/transpose bug hides). +1px in X must move RA (not
        # Dec) and decrease it (cd11 < 0); +1px in Y must move Dec north ~scale.
        px = w.wcs_pix2world([[wcs.crpix1, wcs.crpix2 - 1]], 0)[0]      # +1 in X
        py = w.wcs_pix2world([[wcs.crpix1 - 1, wcs.crpix2]], 0)[0]      # +1 in Y
    assert world[0] == pytest.approx(83.8221, abs=1e-6)
    assert world[1] == pytest.approx(-5.3911, abs=1e-6)
    # +X: RA decreases (cd11 < 0), Dec ~unchanged (diagonal CD, no transpose)
    assert px[0] < world[0]
    assert px[1] == pytest.approx(world[1], abs=1e-5)
    # +Y: Dec increases ~scale north (cd22 > 0), RA ~unchanged
    assert py[1] == pytest.approx(world[1] + scale, abs=5e-5)
    assert py[0] == pytest.approx(world[0], abs=1e-5)


# ------------------------------------------------------- GN-07 (mount lies)
# Evidence: OBJCTDEC +30 47 -> +31 51 across subs whose star fields matched
# within a dither. The header must carry the BEST KNOWN pointing (a plate
# solve, when there is one) in OBJCTRA/OBJCTDEC/RA/DEC, the mount's own raw
# report in MOUNTRA/MOUNTDEC, and which one OBJCTRA/OBJCTDEC actually is in
# PNTGSRC -- so a stacker (or a human) can tell the two apart instead of
# silently trusting a card that walked 50 arcmin between subs of the same
# field.

def test_objctra_carries_the_solved_pointing_not_the_mount(tmp_path):
    # A solved centre 50 arcmin from what the mount itself reports -- the
    # measured gap from the night this row exists for.
    solved_ra, solved_dec = 5.5, -5.39
    mount_ra, mount_dec = 5.5 + (50.0 / 60.0) / 15.0, -5.39  # +50' of RA
    meta = FrameMeta(
        objctra="05 30 00.0", objctdec="-05 23 24",
        mountra="05 33 20.0", mountdec="-05 23 24",
        mount_ra_hours=mount_ra, mount_dec_deg=mount_dec,
        pointing_source="solved")
    path = save_fits(_frame(), tmp_path / "light.fits",
                     ra_hours=solved_ra, dec_deg=solved_dec, meta=meta)
    with fits.open(path) as hdul:
        h = hdul[0].header
    assert h["OBJCTRA"] == "05 30 00.0"
    assert h["OBJCTDEC"] == "-05 23 24"
    assert h["RA"] == pytest.approx(solved_ra * 15.0)
    assert h["DEC"] == pytest.approx(solved_dec)
    assert h["MOUNTRA"] == "05 33 20.0"
    assert h["MOUNTDEC"] == "-05 23 24"
    assert h["MOUNTRAD"] == pytest.approx(mount_ra * 15.0)
    assert h["MOUNTDCD"] == pytest.approx(mount_dec)
    assert h["PNTGSRC"] == "solved"
    # the two really are 50' apart in this fixture -- prove the test itself
    # exercises the defect, not a no-op
    assert abs(h["RA"] - h["MOUNTRAD"]) == pytest.approx(50.0 / 60.0, abs=1e-3)


def test_objctra_falls_back_to_the_mount_when_that_is_all_there_is(tmp_path):
    meta = FrameMeta(
        objctra="05 33 20.0", objctdec="-05 23 24",
        mountra="05 33 20.0", mountdec="-05 23 24",
        mount_ra_hours=5.5556, mount_dec_deg=-5.39,
        pointing_source="mount")
    path = save_fits(_frame(), tmp_path / "light.fits",
                     ra_hours=5.5556, dec_deg=-5.39, meta=meta)
    with fits.open(path) as hdul:
        h = hdul[0].header
    assert h["OBJCTRA"] == h["MOUNTRA"] == "05 33 20.0"
    assert h["OBJCTDEC"] == h["MOUNTDEC"] == "-05 23 24"
    assert h["PNTGSRC"] == "mount"


def test_mount_cards_omitted_when_absent_not_placeholdered(tmp_path):
    # omit-not-placeholder (spec Sec8): no mount fields on this meta at all
    # (the throwaway-solve caller shape) -> no MOUNT* cards, no PNTGSRC.
    meta = FrameMeta(objctra="05 33 20.0", objctdec="-05 23 24")
    path = save_fits(_frame(), tmp_path / "light.fits",
                     ra_hours=5.5556, dec_deg=-5.39, meta=meta)
    with fits.open(path) as hdul:
        h = hdul[0].header
    assert h["OBJCTRA"] == "05 33 20.0"
    for absent in ("MOUNTRA", "MOUNTDEC", "MOUNTRAD", "MOUNTDCD", "PNTGSRC"):
        assert absent not in h, absent
