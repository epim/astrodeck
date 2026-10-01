"""PRO-1 calibration index keys — pure header → key extraction (no I/O)."""
from __future__ import annotations

import hashlib
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
    #: The slot's name as typed: the ``FILTUTF8`` card decoded when the frame
    #: carries one, else the FILTER card (``fitsio.full_name``); ``""`` for
    #: DARK/BIAS. An ASCII name is its own card, so a key minted from any
    #: header written before #332 equals the one minted now, and so does
    #: every seven-field ``CalKey(...)`` built by hand.
    #:
    #: The NAME and not the card (#371). Slots named H-alpha and H-beta in
    #: Greek both write ``FILTER = 'H?'``, so the card cannot tell their flats
    #: apart (#332); and the matcher and the health matrix compare this, as
    #: ``MasterRecord.filter``, with a plan step's filter as typed, so a key
    #: that kept the card matched a non-ASCII slot's flats to no plan at all.
    #: It could not be the name before #371 because ``library`` wrote it into
    #: the master's own FILTER card as it was, where a Greek letter makes
    #: astropy raise; the master now writes it through
    #: ``fitsio.write_name_card``, as a frame does.
    filter: str


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
    through ``fitsio.full_name`` (#332, #371), so two slots whose names fold
    to one FILTER card are two keys, each carrying its name as typed (see
    ``CalKey.filter``)."""
    ftype = _norm_imagetyp(header.get("IMAGETYP", ""))
    if ftype is None:
        return None
    exp = 0.0 if ftype == "BIAS" else round(float(header.get("EXPTIME", 0.0) or 0.0), 3)
    ccd = header.get("CCD-TEMP", None)
    temp = None if ccd is None else round(float(ccd), 3)
    if ftype in ("DARK", "BIAS"):
        filt = ""
    else:
        # Lazy, as ``naming`` is below: this module is the pure key, and the
        # decoder lives with the writer that encodes the card.
        from ..imaging.fitsio import full_name
        filt = full_name(header, "FILTER")
    return CalKey(
        frame_type=ftype,
        exposure_s=exp,
        gain=int(header.get("GAIN", 0) or 0),
        offset=int(header.get("OFFSET", 0) or 0),
        temp_c=temp,
        binning=int(header.get("XBINNING", 1) or 1),
        filter=filt,
    )


def _name_digest(name: str) -> str:
    """Eight hex digits of the SHA-256 of ``name``'s UTF-8: the same in every
    process, on every platform and in every release, which ``hash()`` is not
    (it is salted per process), so a master's id, which is its file name, is
    the same every build. ``surrogatepass`` because a lone surrogate is the
    one str with no UTF-8, and minting an id must not raise on it."""
    return hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest()[:8]


#: Joins a flat's id to the digest of its slot's name (#372). Never in the
#: strict sanitizer's output (alnum, ``-`` and ``_``), so an id with a digest
#: can never equal one without, whatever a slot is named: a slot literally
#: named ``O_III-8202bad7`` cannot take the file of 'O III'. RFC 3986
#: unreserved, so the id still travels unescaped in a URL path
#: (``DELETE /api/calibration/masters/{id}``), and allowed in a file name on
#: every platform ``persist.safe_id_path`` guards.
DIGEST_SEP = "~"

#: The most of a flat's sanitized slot name its id keeps, in UTF-8 bytes
#: (#427). The id is a file name, and one path component holds 255: bytes on
#: the Pi's ext4, UTF-16 units on NTFS, and a string's UTF-16 units never
#: outnumber its UTF-8 bytes, so a bound in bytes holds on both. A bound in
#: characters would not: 64 four-byte letters (``str.isalnum`` keeps the
#: mathematical alphabets) are 256 bytes, over the limit before the rest of
#: the id. With the rest, a cut id is 91 bytes and its file name 96
#: (``flat_g100_o30_b1_f``, the 64, ``~`` and eight hex digits, then
#: ``.fits``), which leaves the numbers room to be the camera's.
FILTER_ID_MAX_BYTES = 64


def _cut(safe_filter: str) -> str:
    """``safe_filter`` cut to at most ``FILTER_ID_MAX_BYTES`` of UTF-8, and
    never through a letter: a letter the cut would split is dropped whole.
    The strict sanitizer's output is alnum, ``-`` and ``_``, and a lone
    surrogate is none of those, so it always encodes."""
    return safe_filter.encode("utf-8")[:FILTER_ID_MAX_BYTES].decode(
        "utf-8", "ignore")


def key_index_id(key: CalKey, temp_bin_width: float, *,
                 digest: bool = False) -> str:
    """Stable filesystem-safe id for a key's BUCKET (temp binned). e.g.
    'dark_e300.000_g100_o30_t-10_b1' / 'flat_g100_o30_b1_fHa'. Exposure/temp
    omitted where irrelevant (BIAS: no exp; FLAT: no exp/temp).

    ONE ID IS ONE FILE, so two slots must never share one (#372). A flat's
    filter is written through the strict sanitizer below, which is
    many-to-one ('O III' and 'O_III' are both 'O_III'), so a name the
    sanitizer CHANGED carries a digest of the name as typed:
    'flat_g100_o30_b1_fO_III~8202bad7'. A name it left alone keeps, byte for
    byte, the id it always had, so no library built before #372 changes a
    file name, and neither does a flat with no filter ('fnone'). Two such
    names can still be one file where the disk ignores case ('Ha', 'HA') or
    where a slot is named 'none'; that takes the whole build to see, so
    ``library`` asks again with ``digest=True`` for every id of a colliding
    set. ``digest`` touches only the filter, which only a flat's id has: a
    dark's or a bias's id is numbers this function formats, and two of them
    never collide.

    ONE ID IS ONE FILE NAME, so it has a length bound (#427). A slot named
    with 300 characters minted a file name no disk holds, the master's write
    raised, and the build stopped before it saved the manifest. The
    sanitized name is cut to ``FILTER_ID_MAX_BYTES`` (``_cut``), and a cut
    counts as a change the sanitizer made: the id carries the digest of the
    whole name, so two long names that differ only past the cut are two
    files. So "left alone" above means a name of at most 64 bytes: a longer
    one (up to about 230 bytes, the most whose master could be written at
    all) keeps its name in its master's cards and its record but takes a
    new id, and the next build writes its master under that id, the file
    under the old one staying in ``_masters`` in no manifest row. The
    numbers are not cut, being the camera's and a few digits long; a header
    built to make one long (an EXPTIME of 1e300) still mints an id no disk
    holds, and ``library`` leaves that one bucket out.

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
        # sanitizer drops carry a digest of the name (#372; see above).
        #
        # The cut is compared, not the sanitized name: a name the sanitizer
        # left alone and the cut shortened is a changed name too, and
        # without its digest every name that begins with the same 64 bytes
        # would be one file (#427).
        from ..naming import sanitize_component
        safe_filter = _cut(sanitize_component(key.filter, "strict"))
        part = f"f{safe_filter or 'none'}"
        if digest or safe_filter != key.filter:
            part += f"{DIGEST_SEP}{_name_digest(key.filter)}"
        parts += [f"g{key.gain}", f"o{key.offset}", f"b{key.binning}", part]
    return "_".join(parts)
