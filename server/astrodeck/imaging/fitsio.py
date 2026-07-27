"""FITS output with proper astro headers."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from astropy.io import fits

from .. import __version__
from ..devices.base import CameraFrame


@dataclass
class FrameMeta:
    """Everything Hub.capture reads from config/site/coords/devices and hands to
    the pure writer. Every field is optional; save_fits emits a card only for a
    non-None, finite value (omit-not-placeholder, spec §8)."""
    # optics
    focal_length_mm: float | None = None
    pixel_size_um: float | None = None      # UNBINNED µm; save_fits x frame.binning
    # site + pointing geometry
    site_lat_deg: float | None = None       # +N
    site_lon_deg: float | None = None       # +E
    site_elev_m: float | None = None
    obj_alt_deg: float | None = None
    airmass: float | None = None
    equinox: float = 2000.0
    radesys: str = "ICRS"
    objctra: str | None = None              # 'HH MM SS.s' (J2000)
    objctdec: str | None = None             # '+DD MM SS'  (J2000)
    # per-frame device telemetry
    set_temp_c: float | None = None
    focuser_pos: int | None = None
    focuser_temp_c: float | None = None
    rotator_angle_deg: float | None = None
    egain_e_per_adu: float | None = None
    # quality (already on the frame)
    hfr: float | None = None
    star_count: int | None = None
    # astrometry (applied in Task 6)
    wcs: "WcsSolution | None" = None         # noqa: F821 - str annotation, Task 6


def _finite(x) -> bool:
    """True when x is a real, writable number (not None / NaN / inf)."""
    if x is None:
        return False
    if isinstance(x, float) and not math.isfinite(x):
        return False
    return True


def save_fits(frame: CameraFrame, path: Path, *, target: str = "",
              filter_name: str = "", frame_type: str = "Light",
              ra_hours: float | None = None, dec_deg: float | None = None,
              telescope: str = "", instrument: str = "",
              meta: "FrameMeta | None" = None) -> Path:
    hdu = fits.PrimaryHDU(frame.data)
    hdr = hdu.header
    hdr["EXPTIME"] = (frame.exposure_s, "Exposure time (s)")
    hdr["GAIN"] = frame.gain
    hdr["OFFSET"] = frame.offset
    hdr["XBINNING"] = frame.binning
    hdr["YBINNING"] = frame.binning
    hdr["IMAGETYP"] = frame_type
    # FITS 4.0 sec 4.4.2 requires 'YYYY-MM-DDThh:mm:ss[.s...]' with NO timezone
    # designator (UTC implied); isoformat() on a tz-aware datetime would append
    # '+00:00', which strict FITS parsers reject.
    hdr["DATE-OBS"] = datetime.fromtimestamp(frame.timestamp, tz=timezone.utc).replace(tzinfo=None).isoformat()
    # DATE-LOC (the NINA/ACP convention): the SAME instant in the observer's local
    # civil time. The filename's $$DATE$$/$$TIME$$ tokens are local while DATE-OBS
    # is UTC — up to a whole day apart in the filename's date, with nothing in the
    # file saying so (UX #50). Writing both makes the pairing self-evident in the
    # data itself instead of only in the docs.
    try:
        hdr["DATE-LOC"] = (
            datetime.fromtimestamp(frame.timestamp).isoformat(timespec="seconds"),
            "Local civil time of DATE-OBS")
    except (ValueError, OSError, OverflowError):
        pass
    if frame.temperature_c is not None:
        hdr["CCD-TEMP"] = (frame.temperature_c, "Sensor temperature (C)")
    if frame.bayer_pattern:
        hdr["BAYERPAT"] = frame.bayer_pattern
    if target:
        hdr["OBJECT"] = target
    if filter_name:
        hdr["FILTER"] = filter_name
    if ra_hours is not None:
        hdr["RA"] = (ra_hours * 15.0, "RA of telescope (deg, J2000)")
    if dec_deg is not None:
        hdr["DEC"] = (dec_deg, "Dec of telescope (deg, J2000)")
    if telescope:
        hdr["TELESCOP"] = telescope
    if instrument:
        hdr["INSTRUME"] = instrument

    m = meta or FrameMeta()
    # --- optics ---
    if _finite(m.focal_length_mm) and m.focal_length_mm > 0:
        hdr["FOCALLEN"] = (float(m.focal_length_mm), "Focal length (mm)")
    if _finite(m.pixel_size_um) and m.pixel_size_um > 0:
        eff = float(m.pixel_size_um) * max(1, int(frame.binning or 1))
        hdr["XPIXSZ"] = (eff, "Binned pixel size (um)")
        hdr["YPIXSZ"] = (eff, "Binned pixel size (um)")
    # --- site (only when a real site is configured; caller passes None on default) ---
    if _finite(m.site_lat_deg):
        hdr["SITELAT"] = (float(m.site_lat_deg), "Observatory latitude (deg, +N)")
    if _finite(m.site_lon_deg):
        hdr["SITELONG"] = (float(m.site_lon_deg), "Observatory longitude (deg, +E)")
    if _finite(m.site_elev_m):
        hdr["SITEELEV"] = (float(m.site_elev_m), "Observatory elevation (m)")
    # --- pointing geometry ---
    if _finite(m.obj_alt_deg):
        hdr["OBJCTALT"] = (float(m.obj_alt_deg), "Altitude of target (deg)")
    if _finite(m.airmass):
        hdr["AIRMASS"] = (float(m.airmass), "Airmass (Kasten-Young)")
    if m.objctra:
        hdr["OBJCTRA"] = (m.objctra, "RA of target (J2000)")
    if m.objctdec:
        hdr["OBJCTDEC"] = (m.objctdec, "Dec of target (J2000)")
    # --- device telemetry ---
    if _finite(m.set_temp_c):
        hdr["SET-TEMP"] = (float(m.set_temp_c), "Cooler setpoint (C)")
    if m.focuser_pos is not None:
        hdr["FOCPOS"] = (int(m.focuser_pos), "Focuser position (steps)")
    if _finite(m.focuser_temp_c):
        hdr["FOCTEMP"] = (float(m.focuser_temp_c), "Focuser temperature (C)")
    if _finite(m.rotator_angle_deg):
        hdr["ROTATANG"] = (float(m.rotator_angle_deg), "Rotator sky PA (deg)")
    if _finite(m.egain_e_per_adu):
        hdr["EGAIN"] = (float(m.egain_e_per_adu), "Gain (e-/ADU)")
    # --- quality ---
    if _finite(m.hfr):
        hdr["HFR"] = (float(m.hfr), "Half-flux radius (px)")
    if m.star_count is not None:
        hdr["STARCNT"] = (int(m.star_count), "Detected star count")
    # --- frame of the written RA/Dec: always paired when RA/Dec are present ---
    if ra_hours is not None and dec_deg is not None:
        hdr["EQUINOX"] = (float(m.equinox), "Equinox of RA/Dec")
        hdr["RADESYS"] = (m.radesys, "Reference frame")

    # --- WCS (from a plate-solve; save_fits handles the solve-then-save case,
    #     write_wcs the post-hoc case; both funnel through _apply_wcs) ---
    if m.wcs is not None:
        _apply_wcs(hdr, m.wcs)

    hdr["SWCREATE"] = (f"AstroDeck {__version__}", "Creating software")

    path.parent.mkdir(parents=True, exist_ok=True)
    hdu.writeto(path, overwrite=True)
    return path


def _apply_wcs(hdr, wcs) -> None:
    """Merge a WcsSolution's cards into a header — shared by save_fits and
    write_wcs so both emit an identical WCS block. A scale-less WCS (no CD*,
    no CDELT*) is skipped whole: astropy would read it back as a silent
    1 deg/pixel solution, and a bogus WCS is worse than none (spec §8)."""
    if wcs.cd11 is None and wcs.cdelt1 is None:
        return
    hdr["CTYPE1"] = (wcs.ctype1, "WCS projection")
    hdr["CTYPE2"] = (wcs.ctype2, "WCS projection")
    hdr["CUNIT1"] = wcs.cunit
    hdr["CUNIT2"] = wcs.cunit
    hdr["CRVAL1"] = (float(wcs.crval1), "RA at reference (deg)")
    hdr["CRVAL2"] = (float(wcs.crval2), "Dec at reference (deg)")
    hdr["CRPIX1"] = (float(wcs.crpix1), "Reference pixel X")
    hdr["CRPIX2"] = (float(wcs.crpix2), "Reference pixel Y")
    if wcs.cd11 is not None:
        hdr["CD1_1"] = float(wcs.cd11)
        hdr["CD1_2"] = float(wcs.cd12) if wcs.cd12 is not None else 0.0
        hdr["CD2_1"] = float(wcs.cd21) if wcs.cd21 is not None else 0.0
        hdr["CD2_2"] = float(wcs.cd22) if wcs.cd22 is not None else 0.0
    elif wcs.cdelt1 is not None:
        hdr["CDELT1"] = float(wcs.cdelt1)
        hdr["CDELT2"] = float(wcs.cdelt2) if wcs.cdelt2 is not None else float(wcs.cdelt1)
        if wcs.crota2 is not None:
            hdr["CROTA2"] = float(wcs.crota2)
    hdr["EQUINOX"] = (float(wcs.equinox), "Equinox of WCS")
    hdr["RADESYS"] = wcs.radesys


def write_wcs(path: Path, wcs) -> Path:
    """Merge a plate-solved WCS into an existing FITS in place. Non-fatal: a
    missing/locked/corrupt file (or a None wcs) is swallowed and the path is
    returned unchanged — WCS write-back must never break a save (spec §9)."""
    if wcs is None:
        return path
    try:
        with fits.open(path, mode="update") as hdul:
            _apply_wcs(hdul[0].header, wcs)
            hdul.flush()
    except Exception:
        pass
    return path
