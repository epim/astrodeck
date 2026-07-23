"""PRO-1 calibration index keys — pure header → key extraction (no I/O)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

CAL_FRAME_TYPES = frozenset({"DARK", "BIAS", "FLAT"})


# IMAGETYP normalization: NINA/ASCOM write "Dark", "Dark Frame", "DARK",
# "Bias Frame", "Flat", "FlatField", etc. Map the leading word to our enum.
def _norm_imagetyp(raw: str) -> str | None:
    t = "".join(ch for ch in str(raw).upper() if ch.isalpha() or ch.isspace()).strip()
    if not t:
        return None
    head = t.split()[0]
    if head in ("DARK",):
        return "DARK"
    if head in ("BIAS", "ZERO"):
        return "BIAS"
    if head in ("FLAT", "FLATFIELD"):
        return "FLAT"
    return None


@dataclass(frozen=True)
class CalKey:
    frame_type: str        # "DARK" | "BIAS" | "FLAT" (upper-cased)
    exposure_s: float      # rounded to 3 dp; 0.0 for BIAS
    gain: int
    offset: int
    temp_c: float | None   # sensor temp from CCD-TEMP; None if absent
    binning: int
    filter: str            # "" for DARK/BIAS


def temp_bin(temp_c: float | None, width: float) -> float | None:
    """Quantize temp to the nearest ``width``-degree bin CENTER. None passthrough.
    ``width <= 0`` disables binning (returns ``temp_c`` unchanged)."""
    if temp_c is None or width <= 0:
        return temp_c
    return round(round(temp_c / width) * width, 3)


def key_from_header(header: Mapping) -> CalKey | None:
    """CalKey from a FITS header, or None when IMAGETYP is absent/not a cal type.

    Case/space-insensitive IMAGETYP ('Dark Frame' -> 'DARK'). Missing GAIN/OFFSET
    default 0; missing XBINNING -> 1; BIAS folds exposure to 0.0; DARK/BIAS drop
    the filter (shutter closed, mono-agnostic)."""
    ftype = _norm_imagetyp(header.get("IMAGETYP", ""))
    if ftype is None:
        return None
    exp = 0.0 if ftype == "BIAS" else round(float(header.get("EXPTIME", 0.0) or 0.0), 3)
    ccd = header.get("CCD-TEMP", None)
    temp = None if ccd is None else round(float(ccd), 3)
    filt = "" if ftype in ("DARK", "BIAS") else str(header.get("FILTER", "") or "")
    return CalKey(
        frame_type=ftype,
        exposure_s=exp,
        gain=int(header.get("GAIN", 0) or 0),
        offset=int(header.get("OFFSET", 0) or 0),
        temp_c=temp,
        binning=int(header.get("XBINNING", 1) or 1),
        filter=filt,
    )


def key_index_id(key: CalKey, temp_bin_width: float) -> str:
    """Stable filesystem-safe id for a key's BUCKET (temp binned). e.g.
    'dark_e300.000_g100_o30_t-10_b1' / 'flat_g100_o30_b1_fHa'. Exposure/temp
    omitted where irrelevant (BIAS: no exp; FLAT: no exp/temp)."""
    tb = temp_bin(key.temp_c, temp_bin_width)
    ts = "NA" if tb is None else f"{tb:g}"
    parts = [key.frame_type.lower()]
    if key.frame_type == "DARK":
        parts += [f"e{key.exposure_s:.3f}", f"g{key.gain}", f"o{key.offset}",
                  f"t{ts}", f"b{key.binning}"]
    elif key.frame_type == "BIAS":
        parts += [f"g{key.gain}", f"o{key.offset}", f"t{ts}", f"b{key.binning}"]
    else:  # FLAT — filter + binning + gain; exposure/temp are not identity
        parts += [f"g{key.gain}", f"o{key.offset}", f"b{key.binning}",
                  f"f{key.filter or 'none'}"]
    return "_".join(parts)
