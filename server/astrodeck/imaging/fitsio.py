# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""FITS output with proper astro headers."""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote

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
    #: Field AZIMUTH at exposure, degrees east of north (issue #23). Computed
    #: alongside the altitude and then discarded until now, which left every
    #: frame saying how high the field was and nothing about which way it faced.
    #: Altitude alone cannot separate a tree from a cloud bank: both are "low".
    obj_az_deg: float | None = None
    airmass: float | None = None
    equinox: float = 2000.0
    radesys: str = "ICRS"
    objctra: str | None = None              # 'HH MM SS.s' (J2000)
    objctdec: str | None = None             # '+DD MM SS'  (J2000)
    # GN-07: the mount's own raw report, recorded alongside whichever pointing
    # actually won OBJCTRA/OBJCTDEC above -- a plate-solved centre, a known
    # goto target, or (last resort) this same mount report. `pointing_source`
    # says which. Written even when it equals OBJCTRA/OBJCTDEC (source
    # "mount"), so a reader never has to guess.
    mountra: str | None = None              # 'HH MM SS.s' (J2000), mount's raw report
    mountdec: str | None = None             # '+DD MM SS'  (J2000), mount's raw report
    mount_ra_hours: float | None = None
    mount_dec_deg: float | None = None
    pointing_source: str | None = None      # "solved" | "target" | "mount"
    # per-frame device telemetry
    set_temp_c: float | None = None
    focuser_pos: int | None = None
    focuser_temp_c: float | None = None
    rotator_angle_deg: float | None = None
    #: The rotator's MECHANICAL angle, 0..360 (#176): where the metal is,
    #: which ``rotator_angle_deg`` (the SKY position angle) is not. The sky
    #: angle moves whenever the rotator is re-synced and the metal does not,
    #: and a dust shadow follows the metal, so this is the angle a flat is
    #: keyed and matched by. Written as ROTMECH, beside ROTATANG, never in
    #: its place.
    rotator_mech_deg: float | None = None
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


#: The four things a capture can be (#334), spelled as ``IMAGETYP`` has always
#: carried them: every frame this rig wrote says one of these, the UI sends
#: only these, and the calibration library (``calibration.keys``) and every
#: ``== "Light"`` in the engine and the UI compare against these spellings.
#: ``IMAGETYP`` is NOT free text, so it is not folded like the names below: a
#: frame type that is not one of these is refused where it comes in (the
#: capture body and the plan step, ``sequence.models.FrameType``), and
#: ``save_fits`` omits the card rather than write one.
FRAME_TYPES = ("Light", "Dark", "Bias", "Flat")
_BY_FOLDED = {t.lower(): t for t in FRAME_TYPES}


def frame_type_name(value) -> str | None:
    """``value`` as one of ``FRAME_TYPES``, or None when it is none of them.

    CASE-INSENSITIVE, and surrounding blanks are ignored: 'light', 'LIGHT'
    and ' Light ' are all Light, because a hand-edited plan or a script
    posting to the capture route means the frame type whatever its case, and
    the engine and the UI compare against one spelling. A blank is Light,
    the default, which is what every reader's ``frame_type or "Light"``
    already made of one. Only ASCII is looked up: ``str.lower`` maps the
    Kelvin sign onto 'k', and a value that needs a non-ASCII character to
    spell a frame type is not one. Anything else is None, never a guess."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return "Light"
    if not text.isascii():
        return None
    return _BY_FOLDED.get(text.lower())


#: Typographic dashes and quotes, mapped to the ASCII a keyboard types for
#: them. A phone's autocorrect turns a typed hyphen into an en dash and a typed
#: apostrophe into a curly one, so "Veil - east" arrives with an en dash in it
#: (#277): the operator meant the hyphen, and a '?' there would read as
#: corruption. Listed because NFKD leaves every one of them alone; none has a
#: compatibility decomposition to fold through.
_TYPOGRAPHIC = {
    # hyphen, non-breaking hyphen, figure dash, en dash, em dash, horizontal
    # bar, minus sign
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
    # single quotes and apostrophes: curly, low, reversed, prime, single
    # guillemets, the spacing acute and the modifier-letter apostrophe
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u2032": "'", "\u2039": "'", "\u203a": "'", "\u00b4": "'",
    "\u02bc": "'",
    # double quotes: curly, low, reversed, double prime, guillemets
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2033": '"', "\u00ab": '"', "\u00bb": '"',
}


def _ascii_fold(text: str) -> str:
    """``text`` as the printable ASCII a FITS header value may hold: the ONE
    fold every free-text card ``save_fits`` writes goes through (#277).

    Printable ASCII passes unchanged, so every value a header could already
    carry is written exactly as before. Anything else, character by
    character:

      * a typographic dash or quote becomes its ASCII form (``_TYPOGRAPHIC``);
      * an accented letter is transliterated: NFKD, combining marks dropped,
        so an e-acute reads "e" and fullwidth letters read as ASCII ones;
      * anything else becomes ONE '?' per character typed, so a name in
        Cyrillic keeps its shape ("M31 ?????????"), and a Hangul syllable,
        which NFKD splits into two or three jamo, is still one '?'.

    '?' and not nothing, unlike the dark check's evidence fold
    (``darks._ascii_card``), which drops what it cannot map because a stray
    '?' in the middle of an evidence sentence reads as corruption. A NAME is
    different: with its Cyrillic dropped, "M31 <name in Cyrillic>" becomes
    plain "M31", which a stacker grouping by OBJECT files with the galaxy's
    own frames. The name as typed survives beside it in ``OBJUTF8``, and a
    slot's or a group's in ``FILTUTF8`` or ``MOSUTF8`` (#332)."""
    out: list[str] = []
    for ch in text:
        if " " <= ch <= "~":
            out.append(ch)
        elif ch in _TYPOGRAPHIC:
            out.append(_TYPOGRAPHIC[ch])
        else:
            parts = "".join(
                _TYPOGRAPHIC.get(c, c)
                for c in unicodedata.normalize("NFKD", ch)
                if not unicodedata.category(c).startswith("M"))
            if parts:
                out.append(parts if all(" " <= c <= "~" for c in parts)
                           else "?")
            # No parts: a combining mark on its own, an accent with no letter
            # under it. Dropped, exactly as it is when it sits on one.
    return "".join(out)


def _card_text(value: str | None) -> str:
    """A free-text card's value (OBJECT, FILTER, TELESCOP, INSTRUME), folded
    by ``_ascii_fold``; ``""`` when nothing but blanks is left, and the card is
    then omitted, never written blank. NOT stripped: an ASCII value is written
    byte for byte as it always was, blanks and all."""
    if value is None:
        return ""
    folded = _ascii_fold(str(value))
    return folded if folded.strip() else ""


def _header_text(value: str | None) -> str:
    """``value`` as a header string: folded by ``_ascii_fold`` and stripped;
    ``""`` for nothing. MOSAIC and PANEL, written stripped since they
    existed."""
    if not value or not str(value).strip():
        return ""
    return _ascii_fold(str(value).strip()).strip()


#: The target's name as typed, whenever the fold changed it (#277). astropy
#: refuses raw UTF-8 in every card, HISTORY and COMMENT included, so no card
#: can carry the name as typed; this one carries it percent-encoded: UTF-8,
#: every byte outside RFC 3986's unreserved ``A-Za-z0-9-._~`` escaped, blanks
#: too, so a trailing blank survives a format that discards trailing blanks.
#: A long name becomes a long-string (CONTINUE) card, which astropy writes and
#: reads without being asked. Never written for an ASCII name, so that header
#: is exactly what it always was.
OBJECT_UTF8 = "OBJUTF8"
_OBJECT_UTF8_COMMENT = "OBJECT as typed: UTF-8, percent-encoded"

#: The wheel slot's name and the mosaic group's name as typed, whenever the
#: fold changed them (#332), encoded exactly as ``OBJUTF8`` is. The fold is
#: many-to-one: slots named H-alpha and H-beta in Greek both fold to 'H?', so
#: FILTER alone put the flats of both into one calibration bucket, the night
#: stack's flats into one group and the backfill's frames under one filter,
#: and two mosaics named in Cyrillic with as many letters wrote one MOSAIC.
#: Every reader that groups by one of these names reads it through
#: ``full_name``. Never written for an ASCII name, so that header is exactly
#: what it always was. TELESCOP and INSTRUME have no such card: nothing groups
#: frames by them.
FILTER_UTF8 = "FILTUTF8"
_FILTER_UTF8_COMMENT = "FILTER as typed: UTF-8, percent-encoded"
MOSAIC_UTF8 = "MOSUTF8"
_MOSAIC_UTF8_COMMENT = "MOSAIC as typed: UTF-8, percent-encoded"

#: Each folded card that keeps its value as typed, and the card that keeps it.
_AS_TYPED = {"OBJECT": OBJECT_UTF8, "FILTER": FILTER_UTF8,
             "MOSAIC": MOSAIC_UTF8}


def _encoded_name(name: str) -> str:
    """``name`` as ``OBJUTF8`` carries it. ``errors="replace"`` is for the
    one thing that is not text: a lone surrogate, which a JSON body can carry
    (``"\\ud800"``) and Python decodes as given. It has no UTF-8, so it is
    carried as '?', the mark OBJECT gives it too, rather than failing the
    capture. Every real character round-trips exactly."""
    return quote(name, safe="", errors="replace")


def full_name(header, keyword: str) -> str:
    """The value of ``keyword`` (``OBJECT``, ``FILTER`` or ``MOSAIC``) as it
    was typed, from a header ``save_fits`` wrote: its as-typed card
    (``OBJUTF8``, ``FILTUTF8``, ``MOSUTF8``) decoded when the frame carries
    one, else the card itself, else ``""``. THE decoder of all three (#277,
    #332), for every reader that shows or groups by one of these names: a
    reader of the bare card files two names that fold alike as one.

    Any other keyword is a KeyError: a card with no as-typed copy has nothing
    to decode, and a reader asking for one is reading the wrong card."""
    encoded = header.get(_AS_TYPED[keyword])
    if encoded:
        return unquote(str(encoded), errors="replace")
    return str(header.get(keyword, "") or "")


def full_object_name(header) -> str:
    """The target's name as the operator typed it: ``full_name(header,
    "OBJECT")``, the spelling every reader of the target has used since
    #277."""
    return full_name(header, "OBJECT")


def _with_comment(value: str, comment: str):
    """``(value, comment)`` when that card fits in 80 columns, else the value
    alone. A group name is free text: from about 25 characters a comment no
    longer fits beside it, and astropy then truncates the comment and warns,
    once per frame. The value is the part a stitching tool reads.

    The arithmetic is the fixed-format string card: ``KEYWORD = `` (10), the
    quoted value with quotes doubled, padded to at least 20, then ``" / "``."""
    quoted = len(value.replace("'", "''")) + 2
    if 10 + max(quoted, 20) + 3 + len(comment) <= 80:
        return (value, comment)
    return value


#: The names ``write_name_card`` writes: each card, the card that keeps its
#: value as typed, and that card's comment. MOSAIC is not here: it is written
#: stripped, by ``save_fits``'s own block.
_NAME_CARDS = {"OBJECT": (OBJECT_UTF8, _OBJECT_UTF8_COMMENT),
               "FILTER": (FILTER_UTF8, _FILTER_UTF8_COMMENT)}


def write_name_card(hdr, keyword: str, value: str | None) -> str:
    """Write ``value``, a name somebody typed, into ``hdr`` as ``keyword``
    (``OBJECT`` or ``FILTER``): the card folded by ``_ascii_fold``, and
    beside it its as-typed card (``OBJUTF8``, ``FILTUTF8``) when the fold
    changed the name. Returns the card's value, ``""`` when nothing but
    blanks was left and no card was written. An ASCII name is written byte
    for byte and gets no as-typed card, so that header is what it always was.

    THE ONE WRITER of these cards (#371). ``save_fits`` writes a frame's
    target and slot through it, and the calibration library a master's slot.
    The library used to write its key's filter into the master's FILTER card
    as it was, which could only ever be the fold: the Greek letter the key
    now carries would make astropy raise and fail the build (the #277
    class), and a master with the fold alone is one a reader of FILTUTF8
    cannot tell from any other slot that folds alike (#332). A second fold,
    kept in step with this one by hand, is the same bug a release later.

    Any other keyword is a KeyError, as in ``full_name``."""
    as_typed, comment = _NAME_CARDS[keyword]
    text = _card_text(value)
    if text:
        hdr[keyword] = text
        if text != str(value):
            hdr[as_typed] = _with_comment(_encoded_name(str(value)), comment)
    return text


def save_fits(frame: CameraFrame, path: Path, *, target: str = "",
              filter_name: str = "", frame_type: str = "Light",
              ra_hours: float | None = None, dec_deg: float | None = None,
              telescope: str = "", instrument: str = "",
              meta: "FrameMeta | None" = None,
              extra_cards: "list[tuple[str, object, str]] | None" = None,
              mosaic: str | None = None, panel: str | None = None) -> Path:
    """Write one frame with its header.

    ``mosaic`` and ``panel`` are a mosaic panel's provenance (#189 U-08): the
    group (``naming.mosaic_label``) and the 1-based ``row-col`` label
    (``naming.panel_label``). Each card is written only when its value is set,
    so a frame that is not a panel has exactly the header it always had.

    Every free-text value (``target``, ``filter_name``, ``telescope``,
    ``instrument``, ``mosaic``, ``panel``) goes through ``_ascii_fold``, so no
    name anybody typed can fail the write (#277); ASCII passes unchanged.
    ``frame_type`` is not free text: it is written as one of ``FRAME_TYPES``
    or not at all (#334)."""
    hdu = fits.PrimaryHDU(frame.data)
    hdr = hdu.header
    hdr["EXPTIME"] = (frame.exposure_s, "Exposure time (s)")
    hdr["GAIN"] = frame.gain
    hdr["OFFSET"] = frame.offset
    hdr["XBINNING"] = frame.binning
    hdr["YBINNING"] = frame.binning
    # ONLY A FRAME TYPE (#334). The capture body and the plan step refuse
    # anything else before a night starts; this is the writer's own check for
    # a caller that reached it past both. The value it was given went in as
    # given, so one non-ASCII character failed every save of that step (the
    # #277 class), and an ASCII value that is no frame type was written where
    # the calibration library reads what a frame IS. Such a value is omitted,
    # never a placeholder: a frame with no IMAGETYP is stacked into no master.
    imagetyp = frame_type_name(frame_type)
    if imagetyp:
        hdr["IMAGETYP"] = imagetyp
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
    # NAMES SOMEBODY TYPED, FOLDED (#277): the target, the wheel slot, and
    # below the optics and the camera. astropy raises on any value outside
    # printable ASCII, so writing one as typed made a single en dash fail every
    # saved frame of the night, and a header write must never fail a capture.
    # ASCII passes unchanged. When the fold changed the target's name, OBJUTF8
    # keeps the name as typed, right beside OBJECT; FILTUTF8 and MOSUTF8 do
    # the same for the slot and the group (#332). The slot's copy matters
    # most: H-alpha and H-beta in Greek both fold to 'H?', and a reader
    # grouping by FILTER alone calibrates and stacks the two as one filter.
    # Both through ``write_name_card``, which a master flat's FILTER goes
    # through too (#371).
    write_name_card(hdr, "OBJECT", target)
    write_name_card(hdr, "FILTER", filter_name)
    # WHICH MOSAIC AND WHICH PANEL (#189 U-08). A stacker that groups by OBJECT
    # or by folder cannot tell two panels of one mosaic from two targets, or
    # worse from one target, and co-adds two pieces of sky; these two cards let
    # a stitching tool group the panels and never co-add them. Folded to the
    # printable ASCII a header value permits: a group is named by whoever
    # framed it, astropy raises on anything else, and a header write must
    # never fail a capture. Omitted when empty, never written blank.
    mosaic_text = _header_text(mosaic)
    if mosaic_text:
        hdr["MOSAIC"] = _with_comment(
            mosaic_text, "Mosaic group; stitch panels, never co-add")
        # The group's name as typed, when the fold changed it (#332): two
        # groups named in Cyrillic with as many letters fold to one MOSAIC.
        # Stripped, as MOSAIC always has been: a blank at either end is not
        # a change the fold made.
        if mosaic_text != str(mosaic).strip():
            hdr[MOSAIC_UTF8] = _with_comment(_encoded_name(str(mosaic).strip()),
                                             _MOSAIC_UTF8_COMMENT)
    panel_text = _header_text(panel)
    if panel_text:
        hdr["PANEL"] = _with_comment(
            panel_text, "Panel row-col, 1-based; 1-1 is NW at PA 0")
    if ra_hours is not None:
        hdr["RA"] = (ra_hours * 15.0, "RA of telescope (deg, J2000)")
    if dec_deg is not None:
        hdr["DEC"] = (dec_deg, "Dec of telescope (deg, J2000)")
    telescope_text = _card_text(telescope)
    if telescope_text:
        hdr["TELESCOP"] = telescope_text
    instrument_text = _card_text(instrument)
    if instrument_text:
        hdr["INSTRUME"] = instrument_text

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
        # CENTALT/CENTAZ beside it (issue #23). Same numbers, the names NINA
        # writes and most downstream tooling greps for; OBJCTALT stays because
        # this repo's own readers already use it. A duplicated card is cheaper
        # than an analysis that cannot find the altitude it needs.
        hdr["CENTALT"] = (float(m.obj_alt_deg), "Altitude of field centre (deg)")
    if _finite(m.obj_az_deg):
        hdr["CENTAZ"] = (float(m.obj_az_deg), "Azimuth of field centre (deg E of N)")
    if _finite(m.airmass):
        hdr["AIRMASS"] = (float(m.airmass), "Airmass (Kasten-Young)")
    if m.objctra:
        hdr["OBJCTRA"] = (m.objctra, "RA of target (J2000)")
    if m.objctdec:
        hdr["OBJCTDEC"] = (m.objctdec, "Dec of target (J2000)")
    # --- GN-07: the mount's raw report + which pointing OBJCTRA/OBJCTDEC is.
    #     Sexagesimal cards always; the numeric pair (MOUNTRAD/MOUNTDCD, deg)
    #     added too since it's one line each and saves a re-parse of the
    #     sexagesimal string for any consumer that just wants a float. ---
    if m.mountra:
        hdr["MOUNTRA"] = (m.mountra, "RA reported by the mount (J2000)")
    if m.mountdec:
        hdr["MOUNTDEC"] = (m.mountdec, "Dec reported by the mount (J2000)")
    if _finite(m.mount_ra_hours):
        hdr["MOUNTRAD"] = (float(m.mount_ra_hours) * 15.0,
                           "RA reported by the mount (deg, J2000)")
    if _finite(m.mount_dec_deg):
        hdr["MOUNTDCD"] = (float(m.mount_dec_deg),
                           "Dec reported by the mount (deg, J2000)")
    if m.pointing_source:
        # Spelled out (solved/target/mount) rather than the fuller sentence
        # the spec first phrased this as -- that sentence plus any of the
        # three values overruns FITS's 80-column card and astropy silently
        # truncates the comment mid-word ("...target or m"). The VALUE is
        # unaffected either way; this only keeps the comment itself legible.
        hdr["PNTGSRC"] = (m.pointing_source,
                          "OBJCTRA/OBJCTDEC source (solved/target/mount)")
    # --- device telemetry ---
    if _finite(m.set_temp_c):
        hdr["SET-TEMP"] = (float(m.set_temp_c), "Cooler setpoint (C)")
    if m.focuser_pos is not None:
        hdr["FOCPOS"] = (int(m.focuser_pos), "Focuser position (steps)")
    if _finite(m.focuser_temp_c):
        hdr["FOCTEMP"] = (float(m.focuser_temp_c), "Focuser temperature (C)")
    if _finite(m.rotator_angle_deg):
        hdr["ROTATANG"] = (float(m.rotator_angle_deg), "Rotator sky PA (deg)")
    if _finite(m.rotator_mech_deg):
        # The card the calibration index reads (``calibration.keys``), on
        # lights and flats alike. Not ROTATANG: that one is a SKY angle, and
        # the same physical angle reads differently after a re-sync. A frame
        # with no rotator writes neither card, and a reader takes the
        # absence as "unknown", never as zero.
        hdr["ROTMECH"] = (float(m.rotator_mech_deg),
                          "Rotator mechanical angle (deg)")
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

    # Caller-supplied ``(keyword, value, comment)`` — the dark check's DARKOK /
    # DARKCHK / DARKWHY verdict is the one user (see ``imaging.darks``). It has
    # to reach the FILE and not just a log line, because the calibration library
    # rebuilds masters by walking every *.fits under the capture root and reads
    # nothing but headers: a frame judged and rejected, with no card on it, is
    # stacked into a master dark exactly as if it had passed. Applied last so a
    # verdict can never be shadowed by a metadata card written above.
    for keyword, value, comment in (extra_cards or []):
        hdr[keyword] = (value, comment)

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
