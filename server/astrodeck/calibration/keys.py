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
    filter: str            # the FILTER card; "" for DARK/BIAS
    #: The slot's name as typed when FILTER is only its fold (#332): the
    #: ``FILTUTF8`` card, decoded. ``""`` whenever FILTER is the name itself,
    #: which is every ASCII name, so a key minted from any header written
    #: before #332 is equal to the one minted now, and so are its bucket id and
    #: every seven-field ``CalKey(...)`` built by hand. Part of the key's
    #: equality: slots named H-alpha and H-beta in Greek both write
    #: ``FILTER = 'H?'`` and differ here, and nowhere else.
    #:
    #: ``filter`` keeps the card and not the decoded name on purpose.
    #: ``library._write_master_fits`` writes ``key.filter`` into the master's
    #: own FILTER card, and astropy raises on a raw Greek letter in any card,
    #: which would fail the whole build (the #277 class). The matcher and the
    #: health matrix compare ``MasterRecord.filter`` (``key.filter``) with the
    #: plan step's filter as typed, so a flat for a slot named outside ASCII
    #: still matches no plan; that is unchanged by #332 and needs the master's
    #: card folded in ``library.py`` before ``filter`` can carry the name
    #: (#371).
    filter_utf8: str = ""

    @property
    def filter_name(self) -> str:
        """The slot's name as typed: what two flats must share to be one
        bucket. ``filter_utf8`` when the frame carried one, else FILTER."""
        return self.filter_utf8 or self.filter


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
    the filter (shutter closed, mono-agnostic). A flat's slot name is read
    through ``fitsio.full_name`` (#332), so two slots whose names fold to one
    FILTER card are two keys (see ``CalKey.filter_utf8``)."""
    ftype = _norm_imagetyp(header.get("IMAGETYP", ""))
    if ftype is None:
        return None
    exp = 0.0 if ftype == "BIAS" else round(float(header.get("EXPTIME", 0.0) or 0.0), 3)
    ccd = header.get("CCD-TEMP", None)
    temp = None if ccd is None else round(float(ccd), 3)
    if ftype in ("DARK", "BIAS"):
        filt = typed = ""
    else:
        # Lazy, as ``naming`` is below: this module is the pure key, and the
        # decoder lives with the writer that encodes the card.
        from ..imaging.fitsio import full_name
        filt = str(header.get("FILTER", "") or "")
        typed = full_name(header, "FILTER")
        if typed == filt:
            typed = ""
    return CalKey(
        frame_type=ftype,
        exposure_s=exp,
        gain=int(header.get("GAIN", 0) or 0),
        offset=int(header.get("OFFSET", 0) or 0),
        temp_c=temp,
        binning=int(header.get("XBINNING", 1) or 1),
        filter=filt,
        filter_utf8=typed,
    )


def key_index_id(key: CalKey, temp_bin_width: float) -> str:
    """Stable filesystem-safe id for a key's BUCKET (temp binned). e.g.
    'dark_e300.000_g100_o30_t-10_b1' / 'flat_g100_o30_b1_fHa'. Exposure/temp
    omitted where irrelevant (BIAS: no exp; FLAT: no exp/temp).

    "Filesystem-safe" was a claim, not a fact, until 2026-08-03. Every other
    component here is a number this function formats itself, but the filter is
    a string copied verbatim out of a FITS ``FILTER`` card (or, since #332,
    decoded out of ``FILTUTF8``, which can spell ``/`` and ``..`` just as
    well), and this id becomes a
    FILENAME at ``library.py`` (``masters_dir() / f"{kid}.fits"``) on a path that
    then does ``mkdir(parents=True)`` + ``writeto(overwrite=True)``. A filter
    named ``../../../../pwned`` produced ``flat_g100_o30_b1_f../../../../pwned``
    and resolved four levels above the masters directory — an arbitrary ``.fits``
    write plus arbitrary directory creation.

    Two ways a hostile value gets into that card, both ordinary operator
    traffic: ``POST /api/filterwheel/names`` only ``.strip()``s what it is given
    and the name is written to the header unsanitized, and the calibration
    scanner ``rglob``s every ``*.fits`` under the capture dir and trusts headers
    it did not write. So the name is sanitized HERE, at the point identity is
    minted, and ``library`` additionally routes the write through
    ``safe_id_path`` — the string check makes the id honest, the containment
    check makes it enforced."""
    tb = temp_bin(key.temp_c, temp_bin_width)
    ts = "NA" if tb is None else f"{tb:g}"
    parts = [key.frame_type.lower()]
    if key.frame_type == "DARK":
        parts += [f"e{key.exposure_s:.3f}", f"g{key.gain}", f"o{key.offset}",
                  f"t{ts}", f"b{key.binning}"]
    elif key.frame_type == "BIAS":
        parts += [f"g{key.gain}", f"o{key.offset}", f"t{ts}", f"b{key.binning}"]
    else:  # FLAT — filter + binning + gain; exposure/temp are not identity
        # "strict" == alnum + -_ only, which is what a filter name legitimately
        # is (L, R, G, B, Ha, Oiii, Sii all survive unchanged); anything that
        # could steer a path does not.
        #
        # The slot's name as typed (#332), not the FILTER card: 'H?' is the
        # card of H-alpha and H-beta in Greek alike, and one id is one master
        # file, so the flats of both slots were stacked into one master. The
        # strict sanitizer keeps letters in any script (``str.isalnum``), as
        # it does in every light's file name through the $$FILTER$$ token, so
        # the two ids differ; an ASCII name is its own card, and its id is
        # exactly the id it always had. Names that differ only in what this
        # sanitizer drops, or only in case on NTFS, still share a file (#372).
        from ..naming import sanitize_component
        safe_filter = sanitize_component(key.filter_name, "strict")
        parts += [f"g{key.gain}", f"o{key.offset}", f"b{key.binning}",
                  f"f{safe_filter or 'none'}"]
    return "_".join(parts)
