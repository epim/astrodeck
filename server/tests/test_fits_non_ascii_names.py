"""A non-ASCII target name never fails a capture (#277; mosaic spec 9 U-08,
S3 orchestrator ruling 7).

astropy refuses any header value outside printable ASCII at assignment time,
so ``save_fits`` writing the operator's string as typed made one en dash
(which a phone's autocorrect makes out of a typed hyphen) fail every saved
light of that target, for the whole night. The same held for the other free
text cards the writer fills from a name somebody typed: FILTER (wheel slot
names), TELESCOP (the optics name) and INSTRUME (the camera's name).

The fix, pinned here:

* ONE fold for every free-text card ``save_fits`` writes
  (``fitsio._ascii_fold``). Printable ASCII passes unchanged. Accents are
  transliterated (NFKD, combining marks dropped). Typographic dashes and
  quotes become their ASCII forms. Anything else becomes one '?' per
  character. A card that folds to nothing is omitted, never written blank.
* When the fold changed the target's name, the name as typed is kept
  losslessly in ``OBJUTF8``: percent-encoded UTF-8, a long-string card when
  it needs to be. The first test below is the evidence for the encoding:
  astropy refuses raw UTF-8 in EVERY card, HISTORY and COMMENT included, so
  there is no card the name could be carried in as typed.
* File names keep ``naming.sanitize_component``, which already writes a
  non-ASCII name into the path safely.

The strings are written as escapes so this file stays ASCII:
``_ANDROMEDA`` is 'M31 Andromeda' with the name in Cyrillic, ``_CRABE`` is
'Nebuleuse du Crabe' with an e-acute, and ``_VEIL`` is 'Veil - east' with an
en dash.

Every mutation named below was run in a private scratch copy of ``server/``
(issue #254), never in the shared tree. The failure each produced is recorded
verbatim on the test that caught it; a mutated line is quoted as written.
"""
from __future__ import annotations

import warnings
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import numpy as np
import pytest
from astropy.io import fits

from _simhub import sim_hub  # noqa: F401 (fixture import)

import astrodeck.imaging.fitsio as fitsio
from astrodeck import naming
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import FrameMeta, save_fits

_ANDROMEDA = "M31 \u0410\u043d\u0434\u0440\u043e\u043c\u0435\u0434\u0430"
_CRABE = "N\u00e9buleuse du Crabe"
_VEIL = "Veil \u2013 east"


def _utf8_card(hdr) -> str:
    """``OBJUTF8`` decoded strictly, so a card that is not valid
    percent-encoded UTF-8 fails here instead of decoding to replacement
    characters that happen to compare unequal."""
    return unquote(hdr["OBJUTF8"], errors="strict")


# --- the evidence: why the full name is carried encoded ----------------------

_RAW_WAYS = {
    "OBJECT": lambda h, v: h.__setitem__("OBJECT", v),
    "FILTER": lambda h, v: h.__setitem__("FILTER", v),
    "TELESCOP": lambda h, v: h.__setitem__("TELESCOP", v),
    "INSTRUME": lambda h, v: h.__setitem__("INSTRUME", v),
    "HISTORY item": lambda h, v: h.__setitem__("HISTORY", v),
    "add_history": lambda h, v: h.add_history(v),
    "add_comment": lambda h, v: h.add_comment(v),
    "long string": lambda h, v: h.__setitem__("OBJUTF8", v * 10),
}


@pytest.mark.parametrize("way", sorted(_RAW_WAYS))
def test_astropy_refuses_a_raw_utf8_value_in_every_card(way):
    """The evidence for the design: there is no card, HISTORY and COMMENT
    included and a long-string card included, that astropy will write a
    UTF-8 name into. The name as typed therefore has to be carried ENCODED,
    and percent-encoded UTF-8 is printable ASCII that decodes back exactly.
    If an astropy upgrade ever accepts one of these, this goes red and says
    the encoding's reason has changed.

    Observed on astropy 7.2.0, the same for every way (the run's console
    printed the Cyrillic as escapes):

        ValueError: FITS header values must contain standard printable ASCII
        characters; 'M31 \\u0410\\u043d...' contains characters not
        representable in ASCII or non-printable characters.

    The UTF-8 BYTES are no way round it either: ``hdr["OBJECT"] =
    name.encode("utf-8")`` raised ``ValueError: Illegal value:
    b'M31 \\xd0\\x90...'``. That way is not a case here, because astropy
    refuses a bytes value whatever it holds: under the mutation below it
    stayed green, so it was no evidence about UTF-8.

    Mutation 'the probe is ASCII' (``_ANDROMEDA`` replaced by 'M31
    Andromeda' in the call below) went red on all eight ways, each with:

        >       with pytest.raises(ValueError):
        E       Failed: DID NOT RAISE <class 'ValueError'>
    """
    with pytest.raises(ValueError):
        _RAW_WAYS[way](fits.Header(), _ANDROMEDA)


def test_the_encoded_name_is_ascii_and_survives_a_long_string_card(tmp_path):
    """The other half of the evidence: the encoded form IS accepted, in a
    card long enough to need CONTINUE cards, and reads back exactly.

    Verifier's mutations of this test (the repeated Cyrillic name elided
    here as <name>):

    'the raw name in the card' (``= name`` in place of ``= quote(name,
    safe="")``):

        E   ValueError: FITS header values must contain standard printable
            ASCII characters; '<name> <name> ...' contains characters not
            representable in ASCII or non-printable characters.

    'a blank left unencoded' (``safe=" "``): the name ends in a blank,
    which FITS drops from the last CONTINUE card, so the round trip loses
    it:

        E   AssertionError: assert '<name> ...<name>' == '<name> ...<name> '
    """
    from urllib.parse import quote
    name = (_ANDROMEDA + " ") * 6
    hdu = fits.PrimaryHDU()
    hdu.header["OBJUTF8"] = quote(name, safe="")
    hdu.writeto(tmp_path / "f.fits")
    raw = (tmp_path / "f.fits").read_bytes()[:2880]
    assert b"CONTINUE" in raw
    assert unquote(fits.getheader(tmp_path / "f.fits")["OBJUTF8"],
                   errors="strict") == name


# --- through the capture path, on the simulator ------------------------------


async def _capture(hub, target: str) -> tuple[Path, fits.Header]:
    await hub.capture(0.2, 100, 30, 1, save=True, target=target)
    path = Path(hub.last_frame.saved_path)
    return path, fits.getheader(path)


async def test_a_name_in_a_non_latin_script_saves_and_keeps_the_full_name(
        sim_hub):
    """The frame is written. OBJECT carries one '?' per letter that has no
    ASCII form, so a reader can see a name was there; OBJUTF8 decodes back
    to the name exactly as typed; and the file name is the one
    ``naming.sanitize_component`` has always made, Cyrillic included.

    Mutation 'raw OBJECT' (``hdr["OBJECT"] = target`` in place of the folded
    value) went red here, on the accented and dash cases, on the three
    full-name length cases, and on the lone surrogate and reader cases. Here
    (the run's console printed the Cyrillic as escapes):

        >       path, hdr = await _capture(sim_hub, _ANDROMEDA)
        ...
        E                   ValueError: FITS header values must contain standard printable ASCII characters; 'M31 \\u0410\\u043d\\u0434\\u0440\\u043e\\u043c\\u0435\\u0434\\u0430' contains characters not representable in ASCII or non-printable characters.

    Mutation 'drop the UTF-8 card' (the ``if object_text != str(target):``
    block that writes ``OBJECT_UTF8`` removed) went red on the same eight
    cases; the reader case with an AssertionError, every other one with:

        >       assert _utf8_card(hdr) == _ANDROMEDA
        ...
        E           KeyError: "Keyword 'OBJUTF8' not found."

    Mutation 'the card carries the folded name' (``_encoded_name(object_text)``
    in place of ``_encoded_name(str(target))``) went red on the same cases
    but the lone surrogate one:

        >       assert _utf8_card(hdr) == _ANDROMEDA
        E       AssertionError: assert 'M31 ?????????' == 'M31 \\u0410\\u043d\\u0434\\u0440\\u043e\\u043c\\u0435\\u0434\\u0430'

    Mutation 'drop instead of ?' (see the fold table) went red here:

        >       assert hdr["OBJECT"] == "M31 ?????????", repr(hdr["OBJECT"])
        E       AssertionError: 'M31'
    """
    path, hdr = await _capture(sim_hub, _ANDROMEDA)
    assert hdr["OBJECT"] == "M31 ?????????", repr(hdr["OBJECT"])
    assert _utf8_card(hdr) == _ANDROMEDA
    # The path is untouched by the fold: sanitize_component keeps letters in
    # any script, which is safe in a path and is what the operator typed.
    assert path.parent.name == naming.sanitize_component(_ANDROMEDA, "loose")
    assert path.parent.name == _ANDROMEDA
    assert path.name.startswith("Light_" + _ANDROMEDA + "_"), path.name


async def test_an_accented_name_is_transliterated(sim_hub):
    """Mutation 'no transliteration' (``for c in ch`` in place of ``for c in
    unicodedata.normalize("NFKD", ch)``, so an accented letter falls through
    to '?') went red here, on the TELESCOP and INSTRUME cases, on the MOSAIC
    case, on the ideographic-space and no-break-space blank cases (NFKD is
    what makes those blanks) and on five fold rows:

        >       assert hdr["OBJECT"] == "Nebuleuse du Crabe", repr(hdr["OBJECT"])
        E       AssertionError: 'N?buleuse du Crabe'

    Mutation 'combining marks kept' (the ``if not
    unicodedata.category(c).startswith("M")`` filter removed) went red here
    with the same line, on the TELESCOP, INSTRUME and MOSAIC cases, and on
    four fold rows, among them the two with a free-standing combining acute.
    """
    _path, hdr = await _capture(sim_hub, _CRABE)
    assert hdr["OBJECT"] == "Nebuleuse du Crabe", repr(hdr["OBJECT"])
    assert _utf8_card(hdr) == _CRABE


async def test_a_typographic_dash_becomes_a_hyphen(sim_hub):
    """The case #277 was found with: autocorrect's en dash.

    Mutation 'no dash map' (the dash entries taken out of ``_TYPOGRAPHIC``)
    went red here, on the dash row of the fold table, and on
    test_panel_provenance's en-dash MOSAIC case, which now reads the same
    fold:

        >       assert hdr["OBJECT"] == "Veil - east", repr(hdr["OBJECT"])
        E       AssertionError: 'Veil ? east'
    """
    _path, hdr = await _capture(sim_hub, _VEIL)
    assert hdr["OBJECT"] == "Veil - east", repr(hdr["OBJECT"])
    # The fold changed the name, so the name as typed is kept.
    assert _utf8_card(hdr) == _VEIL


def _alpha_filter(hub, monkeypatch):
    fw = hub.devices["filterwheel"]
    # Every slot, so the name is in the beam wherever the sim wheel sits.
    monkeypatch.setattr(fw, "filter_names", ["H\u03b1"] * len(fw.filter_names))


def _epsilon_telescope(hub, monkeypatch):
    real = hub.effective_optics
    monkeypatch.setattr(hub, "effective_optics", lambda: {
        **real(), "telescope_name": "Takahashi \u00c9psilon-130D"})


def _camera_name(hub, monkeypatch):
    monkeypatch.setattr(hub.devices["camera"], "name", "Cam\u00e9ra QHY268M")


@pytest.mark.parametrize("card,setup,expected", [
    ("FILTER", _alpha_filter, "H?"),
    ("TELESCOP", _epsilon_telescope, "Takahashi Epsilon-130D"),
    ("INSTRUME", _camera_name, "Camera QHY268M"),
], ids=["FILTER", "TELESCOP", "INSTRUME"])
async def test_a_non_ascii_rig_name_saves(sim_hub, monkeypatch, card, setup,
                                          expected):
    """Wheel slot names, the optics name and the camera's name are typed by
    somebody too, and went into the header as typed. An ASCII target, so the
    only non-ASCII value is the one under test.

    Mutation 'fold only OBJECT' (FILTER, TELESCOP and INSTRUME written by
    the lines they had before #277, ``if filter_name: hdr["FILTER"] =
    filter_name`` and its two siblings) went red on all three and on the
    three blank cases; FILTER (the run's console printed the alpha as an
    escape):

        >       path, hdr = await _capture(sim_hub, "Veil east")
        ...
        E                   ValueError: FITS header values must contain standard printable ASCII characters; 'H\\u03b1' contains characters not representable in ASCII or non-printable characters.

    Mutations 'raw TELESCOP' and 'raw INSTRUME' (one card each restored to
    its old line) went red on their own case and on the three blank cases,
    with the same ValueError naming 'Takahashi \\u00c9psilon-130D' and
    'Cam\\u00e9ra QHY268M' (written here as escapes; the console mangled
    them).

    Mutation 'drop instead of ?' went red on FILTER:

        >       assert hdr[card] == expected, repr(hdr[card])
        E       AssertionError: 'H'
    """
    setup(sim_hub, monkeypatch)
    path, hdr = await _capture(sim_hub, "Veil east")
    assert hdr[card] == expected, repr(hdr[card])
    # No OBJUTF8: that card is the target's, and this target is ASCII.
    assert "OBJUTF8" not in hdr
    if card == "FILTER":
        # The FILTER token keeps its own sanitizer (strict), untouched.
        assert naming.sanitize_component("H\u03b1", "strict") in path.name


async def test_an_ascii_target_writes_exactly_what_was_typed(sim_hub):
    """CONTROL: nothing about an ASCII name changes.

    Mutation 'OBJUTF8 always' (``if True:`` in place of ``if object_text !=
    str(target):``) went red here, on the three rig-name cases, on both
    byte-for-byte cases and on five of test_panel_provenance's golden cases.
    Here (the header's repr elided):

        >       assert "OBJUTF8" not in hdr
        E       AssertionError: assert 'OBJUTF8' not in SIMPLE  =                    T / conforms to FITS standard ...
    """
    path, hdr = await _capture(sim_hub, "Veil east")
    assert hdr["OBJECT"] == "Veil east"
    assert "OBJUTF8" not in hdr
    assert path.parent.name == "Veil east"


# --- the writer --------------------------------------------------------------

_TS = 1790000000.0
_EVERY_ASCII = "".join(chr(i) for i in range(32, 127))

#: The header card images the UNMODIFIED writer (fitsio.py at e673dff8,
#: sha256 230b873a...) produced through ``_write`` below for each of the
#: ``_CONTROLS`` inputs, recorded by running exactly those inputs and slicing
#: the file into 80-byte cards (trailing blanks stripped here, restored on
#: comparison). ``{local}`` is
#: DATE-LOC's value, which depends on the machine's time zone and is always
#: 19 characters, so substituting it moves no column.
_GOLDEN_HEAD = [
    'SIMPLE  =                    T / conforms to FITS standard',
    'BITPIX  =                   16 / array data type',
    'NAXIS   =                    2 / number of array dimensions',
    'NAXIS1  =                    4',
    'NAXIS2  =                    3',
    'EXTEND  =                    T',
    'BSCALE  =                    1',
    'BZERO   =                32768',
    'EXPTIME =                300.0 / Exposure time (s)',
    'GAIN    =                  100',
    'OFFSET  =                   30',
    'XBINNING=                    1',
    'YBINNING=                    1',
    "IMAGETYP= 'Light   '",
    "DATE-OBS= '2026-09-21T14:13:20'",
    "DATE-LOC= '{local}' / Local civil time of DATE-OBS",
    'CCD-TEMP=                -10.0 / Sensor temperature (C)',
]
_GOLDEN_TAIL = [
    'RA      =   314.16600000000005 / RA of telescope (deg, J2000)',
    'DEC     =              31.7167 / Dec of telescope (deg, J2000)',
]
_GOLDEN_META = [
    'FOCALLEN=                250.0 / Focal length (mm)',
    'XPIXSZ  =                 3.76 / Binned pixel size (um)',
    'YPIXSZ  =                 3.76 / Binned pixel size (um)',
    'AIRMASS =                 1.21 / Airmass (Kasten-Young)',
    "OBJCTRA = '20 56 40.0'         / RA of target (J2000)",
    "OBJCTDEC= '+31 43 00'          / Dec of target (J2000)",
    "PNTGSRC = 'target  '           / OBJCTRA/OBJCTDEC source (solved/target/mount)",
    'HFR     =                  2.1 / Half-flux radius (px)',
    'STARCNT =                  812 / Detected star count',
    'EQUINOX =               2000.0 / Equinox of RA/Dec',
    "RADESYS = 'ICRS    '           / Reference frame",
    "SWCREATE= 'AstroDeck 0.0.0-golden' / Creating software",
    'END',
]
_CONTROLS = {
    "plain": (
        dict(target="Veil east", filter_name="L", telescope="RedCat 51",
             instrument="ZWO ASI2600MM Pro"),
        _GOLDEN_HEAD + [
            "OBJECT  = 'Veil east'",
            "FILTER  = 'L       '",
        ] + _GOLDEN_TAIL + [
            "TELESCOP= 'RedCat 51'",
            "INSTRUME= 'ZWO ASI2600MM Pro'",
        ] + _GOLDEN_META),
    # Every printable ASCII character, a leading blank, a '%' and an '&'
    # included, as the target: long enough that today's writer continues it.
    # FILTER, TELESCOP and INSTRUME each lead with a blank too, which FITS
    # keeps (only trailing blanks are insignificant), so a fold that strips
    # any one of the four cards moves bytes here. This case's cards were
    # re-recorded by the verifier from ``git show e673dff8:server/astrodeck/
    # imaging/fitsio.py`` (sha256 6a31ffa6...), the unmodified writer, run on
    # exactly these inputs.
    "every": (
        dict(target=_EVERY_ASCII, filter_name=" O-III (3nm) 100%",
             telescope=" Scope's \"best\" #1 / f?",
             instrument=" Cam & co ~ {x}"),
        _GOLDEN_HEAD + [
            "OBJECT  = ' &'",
            'CONTINUE  \'!"#$%&\'\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`ab&\'',
            "CONTINUE  'cdefghijklmnopqrstuvwxyz{|}~'",
            "FILTER  = ' O-III (3nm) 100%'",
        ] + _GOLDEN_TAIL + [
            'TELESCOP= \' Scope\'\'s "best" #1 / f?\'',
            "INSTRUME= ' Cam & co ~ {x}'",
        ] + _GOLDEN_META),
}


def _frame() -> CameraFrame:
    return CameraFrame(data=np.arange(12, dtype=np.uint16).reshape(3, 4),
                       exposure_s=300.0, gain=100, offset=30, binning=1,
                       bayer_pattern=None, temperature_c=-10.0, timestamp=_TS)


def _write(path: Path, monkeypatch, **kw) -> Path:
    monkeypatch.setattr(fitsio, "__version__", "0.0.0-golden")
    meta = FrameMeta(focal_length_mm=250.0, pixel_size_um=3.76, airmass=1.21,
                     objctra="20 56 40.0", objctdec="+31 43 00",
                     pointing_source="target", hfr=2.1, star_count=812)
    return save_fits(_frame(), path, frame_type="Light", ra_hours=20.9444,
                     dec_deg=31.7167, meta=meta, **kw)


def _header_block(path: Path) -> bytes:
    raw = path.read_bytes()
    for i in range(0, len(raw), 80):
        if raw[i:i + 80].startswith(b"END "):
            end = i + 80
            return raw[:end + (-end) % 2880]
    raise AssertionError("no END card")


@pytest.mark.parametrize("case", sorted(_CONTROLS))
def test_an_ascii_header_is_byte_for_byte_todays(tmp_path, monkeypatch, case):
    """CONTROL: the whole header block, byte for byte, against the one the
    unmodified writer produced. "every" is the whole printable ASCII range as
    the target, and each of the four name cards leads with a blank, so it
    also proves no name card is stripped: ASCII passes unchanged.

    Mutation 'strip the name' (OBJECT, FILTER, TELESCOP and INSTRUME folded
    through ``_header_text``, which strips, instead of ``_card_text``) went
    red on "every" alone:

        >       assert _header_block(path) == expected
        E       AssertionError: assert b'SIMPLE  =  ...             ' == b'SIMPLE  =  ...             '
        E         At index 1371 diff: b'!' != b' '

    Verifier's mutations, one card at a time through ``_header_text`` (before
    FILTER, TELESCOP and INSTRUME led with a blank in "every", all three
    survived: the whole file stayed green). Each went red on "every" alone:
    'strip FILTER' ``At index 1611 diff: b'O' != b' '``, 'strip TELESCOP'
    ``At index 1851 diff: b'S' != b' '``, 'strip INSTRUME' ``At index 1931
    diff: b'C' != b' '``.

    Mutation 'OBJUTF8 always' went red on both, an OBJUTF8 card standing
    where FILTER should ("At index 1440 diff: b'O' != b'F'" on "plain",
    index 1600 on "every").

    Run against the unmodified writer itself (``git show e673dff8:`` of
    fitsio.py swapped in), both cases pass: the goldens are today's header.
    """
    kw, cards = _CONTROLS[case]
    path = _write(tmp_path / "f.fits", monkeypatch, **kw)
    local = datetime.fromtimestamp(_TS).isoformat(timespec="seconds")
    body = b"".join(c.replace("{local}", local).ljust(80).encode("ascii")
                    for c in cards)
    expected = body + b" " * ((-len(body)) % 2880)
    assert _header_block(path) == expected


@pytest.mark.parametrize("blank", ["   ", "\u3000", "\u00a0"],
                         ids=["spaces", "ideographic-space", "nbsp"])
def test_a_name_that_folds_to_nothing_writes_no_card(tmp_path, monkeypatch,
                                                     blank):
    """A card that folds to blank is omitted, as MOSAIC and PANEL already
    are, never written blank. All four name cards. Before #277 a blank of
    spaces wrote four blank cards, and an ideographic or no-break space
    failed the save. With no OBJECT there is no OBJUTF8 either: a blank
    name has nothing to keep.

    Mutation 'blank written' (``return folded`` in place of ``return folded
    if folded.strip() else ""``) went red on all three. On "spaces" the
    four cards came back blank:

        >       assert missing == ["FILTER", "INSTRUME", "OBJECT", "OBJUTF8", "TELESCOP"], missing
        E       AssertionError: ['OBJUTF8']

    and on the other two, whose blank the fold had changed, OBJUTF8 was
    written as well: ``E       AssertionError: []``.

    Mutations 'fold only OBJECT', 'raw TELESCOP' and 'raw INSTRUME' went
    red on all three too: on "spaces" the restored card came back blank
    (``AssertionError: ['FILTER', 'INSTRUME', 'OBJECT', 'OBJUTF8']`` for
    'raw TELESCOP'), on the other two the save raised the ValueError.
    """
    path = _write(tmp_path / "f.fits", monkeypatch, target=blank,
                  filter_name=blank, telescope=blank, instrument=blank)
    hdr = fits.getheader(path)
    missing = sorted(k for k in ("OBJECT", "OBJUTF8", "FILTER", "TELESCOP",
                                 "INSTRUME") if k not in hdr)
    assert missing == ["FILTER", "INSTRUME", "OBJECT", "OBJUTF8", "TELESCOP"], missing


@pytest.mark.parametrize("name", [
    "\u03c9 Cen",                # 12 encoded: the comment fits beside it
    _ANDROMEDA,                  # 60 encoded: one card, but no comment fits
    (_ANDROMEDA + " ") * 5,      # 305 encoded: CONTINUE cards
], ids=["short", "medium", "long"])
def test_the_full_name_card_fits_at_any_length_and_warns_nothing(
        tmp_path, monkeypatch, name):
    """The encoded name is six characters per Cyrillic letter, so a name of
    nine letters is already past what fits beside the comment. astropy then
    truncates the comment and warns, once per frame of the night, unless the
    comment is dropped (``_with_comment``, as MOSAIC does). Past 68
    characters astropy continues the value and carries the comment on the
    last CONTINUE card without complaint.

    Mutation 'always comment' (``(_encoded_name(str(target)),
    _OBJECT_UTF8_COMMENT)`` in place of the ``_with_comment`` call) went red
    on "medium" alone: "short" fits beside its comment and "long" is
    continued:

        >           path = _write(tmp_path / "f.fits", monkeypatch, target=name)
        ...
        E               astropy.io.fits.verify.VerifyWarning: Card is too long, comment will be truncated.

    Mutations 'raw OBJECT', 'drop the UTF-8 card', 'the card carries the
    folded name' and 'reader ignores OBJUTF8' went red on all three.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        path = _write(tmp_path / "f.fits", monkeypatch, target=name)
    hdr = fits.getheader(path)
    assert _utf8_card(hdr) == name
    assert fitsio.full_object_name(hdr) == name
    # The short case keeps its comment: dropping it everywhere is not the fix.
    if name == "\u03c9 Cen":
        assert hdr.comments["OBJUTF8"] != "", repr(hdr.comments["OBJUTF8"])


def test_a_lone_surrogate_never_fails_the_save(tmp_path, monkeypatch):
    """A JSON body may carry ``"\\ud800"``, which Python decodes to a lone
    surrogate: not a character, and not encodable as UTF-8. The save must
    still succeed. OBJECT reads '?' for it and OBJUTF8 carries a '?' in its
    place, which is valid UTF-8 a strict decoder accepts.

    Mutation 'strict encoding' (``errors="strict"`` in the ``quote`` call)
    went red here alone:

        >       path = _write(tmp_path / "f.fits", monkeypatch, target="M31 \\ud800")
        ...
        E           UnicodeEncodeError: 'utf-8' codec can't encode character '\\ud800' in position 4: surrogates not allowed

    Mutations 'raw OBJECT' (the ValueError), 'drop the UTF-8 card' (the
    KeyError) and 'drop instead of ?' (``AssertionError: assert 'M31' ==
    'M31 ?'``) went red here too.
    """
    path = _write(tmp_path / "f.fits", monkeypatch, target="M31 \ud800")
    hdr = fits.getheader(path)
    assert hdr["OBJECT"] == "M31 ?"
    assert _utf8_card(hdr) == "M31 ?"


def test_the_reader_prefers_the_full_name_and_falls_back_to_object(
        tmp_path, monkeypatch):
    """``fitsio.full_object_name`` is the one decoder, for readers (the
    gallery reads OBJECT today): the name as typed when the frame carries
    OBJUTF8, OBJECT when it does not, "" for neither.

    Mutation 'reader ignores OBJUTF8' (``if False:`` in place of ``if
    encoded:``, so it returns OBJECT only) went red here and on all three
    full-name length cases:

        >       assert fitsio.full_object_name(fits.getheader(p1)) == _ANDROMEDA
        E       AssertionError: assert 'M31 ?????????' == 'M31 \\u0410\\u043d\\u0434\\u0440\\u043e\\u043c\\u0435\\u0434\\u0430'

    'drop the UTF-8 card' and 'the card carries the folded name' failed on
    the same line.
    """
    p1 = _write(tmp_path / "a.fits", monkeypatch, target=_ANDROMEDA)
    p2 = _write(tmp_path / "b.fits", monkeypatch, target="M31")
    p3 = _write(tmp_path / "c.fits", monkeypatch)
    assert fitsio.full_object_name(fits.getheader(p1)) == _ANDROMEDA
    assert fitsio.full_object_name(fits.getheader(p2)) == "M31"
    assert fitsio.full_object_name(fits.getheader(p3)) == ""


def test_a_mosaic_name_goes_through_the_same_fold(tmp_path, monkeypatch):
    """MOSAIC was folded by the dark check's evidence fold, which DROPS what
    it cannot map: "Nebuleuse du Crabe" with its e-acute read "Nbuleuse du
    Crabe", and a Cyrillic group name lost its name and read plain "M31",
    which a stitching tool cannot tell from a group that really is "M31".
    One fold now serves every free-text card.

    Mutation 'MOSAIC keeps the dark-check fold' (``_header_text`` folding
    with ``darks._ascii_card`` as before) went red here alone:

        >       assert hdr["MOSAIC"] == "Nebuleuse du Crabe", repr(hdr["MOSAIC"])
        E       AssertionError: 'Nbuleuse du Crabe'

    Mutation 'drop instead of ?' failed the second half:

        >       assert fits.getheader(path)["MOSAIC"] == "M31 ?????????"
        E       AssertionError: assert 'M31' == 'M31 ?????????'
    """
    path = _write(tmp_path / "a.fits", monkeypatch, target="M1",
                  mosaic=_CRABE, panel="1-1")
    hdr = fits.getheader(path)
    assert hdr["MOSAIC"] == "Nebuleuse du Crabe", repr(hdr["MOSAIC"])
    path = _write(tmp_path / "b.fits", monkeypatch, target="M31",
                  mosaic=_ANDROMEDA, panel="1-2")
    assert fits.getheader(path)["MOSAIC"] == "M31 ?????????"


# --- the fold itself ---------------------------------------------------------

_FOLD_TABLE = [
    # printable ASCII passes unchanged, every character of it
    (_EVERY_ASCII, _EVERY_ASCII),
    ("  M31  ", "  M31  "),
    # accents, precomposed and decomposed; compatibility forms
    ("N\u00e9buleuse", "Nebuleuse"),
    ("Ne\u0301buleuse", "Nebuleuse"),
    ("\u00c5ngstr\u00f6m cr\u00e8me br\u00fbl\u00e9e", "Angstrom creme brulee"),
    ("\uff2d\uff13\uff11", "M31"),                  # fullwidth
    ("Horsehead\u2026", "Horsehead..."),            # ellipsis
    ("M31\u00a0Andromeda", "M31 Andromeda"),        # no-break space
    # typographic dashes
    ("a\u2010b\u2011c\u2012d\u2013e\u2014f\u2015g\u2212h", "a-b-c-d-e-f-g-h"),
    # typographic quotes
    ("\u2018a\u2019 \u201cb\u201d \u201ec\u201f \u00abd\u00bb", "'a' \"b\" \"c\" \"d\""),
    ("Barnard\u2019s Loop", "Barnard's Loop"),
    ("Barnard\u02bcs Loop", "Barnard's Loop"),
    ("Barnard\u00b4s Loop", "Barnard's Loop"),         # spacing acute
    # anything else: ONE '?' per character, never dropped
    (_ANDROMEDA, "M31 ?????????"),
    ("H\u03b1", "H?"),
    ("Stra\u00dfe", "Stra?e"),
    ("\uc548\ub4dc\ub85c\uba54\ub2e4", "?????"),  # Hangul: 1 '?' per syllable
    ("\u0439", "?"),  # Cyrillic short i: base + breve
    ("a\tb\x00c\x7f", "a?b?c?"),                    # control characters
    ("M31 \ud800", "M31 ?"),                        # lone surrogate
    # a combining mark with nothing under it is an accent: dropped
    ("\u0301M31", "M31"),
    ("", ""),
]


@pytest.mark.parametrize("text,expected", _FOLD_TABLE,
                         ids=[ascii(t)[:24] for t, _e in _FOLD_TABLE])
def test_the_fold(text, expected):
    """Mutation 'drop instead of ?' (``else "")`` in place of ``else "?")``,
    the dark check's rule: a character with no ASCII form is dropped) went
    red on all seven '?' rows, and on the hub Cyrillic, FILTER, lone
    surrogate and MOSAIC cases. The Cyrillic row:

        >       assert folded == expected
        E       AssertionError: assert 'M31 ' == 'M31 ?????????'

    Mutation 'one ? per decomposed character' (the '?' decided per NFKD
    part, ``"".join(c if " " <= c <= "~" else "?" for c in parts)``, not
    per character typed) went red on the Hangul row alone; five syllables
    are eleven jamo:

        E       AssertionError: assert '???????????' == '?????'

    Mutation 'no quote map' (the quote entries taken out of
    ``_TYPOGRAPHIC``) went red on the four quote rows:

        E       assert '?a? ?b? ?c? ?d?' == '\\'a\\' "b" "c" "d"'
        E       assert 'Barnard s Loop' == "Barnard's Loop"

    (the second is the spacing acute, which NFKD turns into a blank and a
    combining acute). 'no transliteration', 'combining marks kept' and 'no
    dash map' are recorded on the hub cases above.
    """
    folded = fitsio._ascii_fold(text)
    assert folded == expected
    assert all(" " <= ch <= "~" for ch in folded)
