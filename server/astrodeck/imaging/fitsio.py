"""FITS output with proper astro headers."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from astropy.io import fits

from ..devices.base import CameraFrame


def save_fits(frame: CameraFrame, path: Path, *, target: str = "",
              filter_name: str = "", frame_type: str = "Light",
              ra_hours: float | None = None, dec_deg: float | None = None,
              telescope: str = "", instrument: str = "") -> Path:
    hdu = fits.PrimaryHDU(frame.data)
    hdr = hdu.header
    hdr["EXPTIME"] = (frame.exposure_s, "Exposure time (s)")
    hdr["GAIN"] = frame.gain
    hdr["OFFSET"] = frame.offset
    hdr["XBINNING"] = frame.binning
    hdr["YBINNING"] = frame.binning
    hdr["IMAGETYP"] = frame_type
    hdr["DATE-OBS"] = datetime.fromtimestamp(frame.timestamp, tz=timezone.utc).isoformat()
    if frame.temperature_c is not None:
        hdr["CCD-TEMP"] = (frame.temperature_c, "Sensor temperature (C)")
    if frame.bayer_pattern:
        hdr["BAYERPAT"] = frame.bayer_pattern
    if target:
        hdr["OBJECT"] = target
    if filter_name:
        hdr["FILTER"] = filter_name
    if ra_hours is not None:
        hdr["RA"] = (ra_hours * 15.0, "RA of telescope (deg)")
    if dec_deg is not None:
        hdr["DEC"] = (dec_deg, "Dec of telescope (deg)")
    if telescope:
        hdr["TELESCOP"] = telescope
    if instrument:
        hdr["INSTRUME"] = instrument
    hdr["SWCREATE"] = "AstroDeck 0.1.0"

    path.parent.mkdir(parents=True, exist_ok=True)
    hdu.writeto(path, overwrite=True)
    return path
