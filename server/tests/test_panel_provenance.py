"""Per-panel provenance: FITS ``MOSAIC`` / ``PANEL`` and the ``$$PANEL$$`` token
(#189 U-08; mosaic spec 2.3 and section 8, S2 item 7).

A mosaic is shot as many panels of one group, and the files have to say so.
A stacker that groups frames by OBJECT, or by folder, sees two panels as two
targets at best and as one target at worst, and co-adds pixels from two
different pieces of sky. The frames must carry, in the file, which group they
belong to and which panel they are, so a stitching tool can group the panels
and never co-add them.

* ``MOSAIC`` is the group's name, or its id when the name is empty
  (``naming.mosaic_label``). ``PANEL`` is the 1-based ``row-col`` label of
  section 2.3 (``naming.panel_label``): row 1 is the north edge and col 1 the
  west edge at angle 0, the same label as the Plan's ``<name> r-c``.
* Both are written ONLY when set. A frame that is not a panel has a header
  identical to today's, card for card: the golden below was recorded from the
  unmodified writer before either card existed.
* ``$$PANEL$$`` renders the label in a capture path and drops out when unset,
  as every token does. ``ui/src/lib/naming.ts`` mirrors it; the shared golden
  vectors here and in ``ui/src/lib/__tests__/naming.test.ts`` are the pin.

Every mutation named below was run in a private scratch copy of ``server/``
(issue #254), never in the shared tree. The failure each produced is recorded
verbatim on the test that caught it.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from _simhub import sim_hub  # noqa: F401 (fixture import)

import astrodeck.imaging.fitsio as fitsio
from astrodeck import naming
from astrodeck.config import NamingConfig, config_store
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import FrameMeta, save_fits

# --- the writer ------------------------------------------------------------

_TS = 1790000000.0

#: The header the writer produced for ``_write`` below BEFORE the MOSAIC and
#: PANEL cards existed, recorded from the unmodified ``save_fits`` (hub.py and
#: fitsio.py at 32107076) by running exactly this input and printing every
#: card. DATE-LOC is the one value that depends on the machine's time zone, so
#: it is compared against the same conversion the writer makes.
_GOLDEN = [
    ('SIMPLE', True, 'conforms to FITS standard'),
    ('BITPIX', 16, 'array data type'),
    ('NAXIS', 2, 'number of array dimensions'),
    ('NAXIS1', 4, ''),
    ('NAXIS2', 3, ''),
    ('EXTEND', True, ''),
    ('BSCALE', 1, ''),
    ('BZERO', 32768, ''),
    ('EXPTIME', 300.0, 'Exposure time (s)'),
    ('GAIN', 100, ''),
    ('OFFSET', 30, ''),
    ('XBINNING', 1, ''),
    ('YBINNING', 1, ''),
    ('IMAGETYP', 'Light', ''),
    ('DATE-OBS', '2026-09-21T14:13:20', ''),
    ('DATE-LOC', '<DATE-LOC>', 'Local civil time of DATE-OBS'),
    ('CCD-TEMP', -10.0, 'Sensor temperature (C)'),
    ('OBJECT', 'M31', ''),
    ('FILTER', 'L', ''),
    ('RA', 10.6845, 'RA of telescope (deg, J2000)'),
    ('DEC', 41.269, 'Dec of telescope (deg, J2000)'),
    ('TELESCOP', 'Scope', ''),
    ('INSTRUME', 'Cam', ''),
    ('FOCALLEN', 530.0, 'Focal length (mm)'),
    ('XPIXSZ', 3.76, 'Binned pixel size (um)'),
    ('YPIXSZ', 3.76, 'Binned pixel size (um)'),
    ('OBJCTALT', 55.5, 'Altitude of target (deg)'),
    ('CENTALT', 55.5, 'Altitude of field centre (deg)'),
    ('CENTAZ', 120.25, 'Azimuth of field centre (deg E of N)'),
    ('AIRMASS', 1.21, 'Airmass (Kasten-Young)'),
    ('OBJCTRA', '00 42 44.3', 'RA of target (J2000)'),
    ('OBJCTDEC', '+41 16 09', 'Dec of target (J2000)'),
    ('PNTGSRC', 'solved', 'OBJCTRA/OBJCTDEC source (solved/target/mount)'),
    ('SET-TEMP', -10.0, 'Cooler setpoint (C)'),
    ('FOCPOS', 11218, 'Focuser position (steps)'),
    ('ROTATANG', 23.4, 'Rotator sky PA (deg)'),
    ('HFR', 2.1, 'Half-flux radius (px)'),
    ('STARCNT', 812, 'Detected star count'),
    ('EQUINOX', 2000.0, 'Equinox of RA/Dec'),
    ('RADESYS', 'ICRS', 'Reference frame'),
    ('SWCREATE', 'AstroDeck 0.0.0-golden', 'Creating software'),
    ('DARKOK', True, 'verdict'),
]


def _golden() -> list[tuple]:
    local = datetime.fromtimestamp(_TS).isoformat(timespec="seconds")
    return [(k, local if v == '<DATE-LOC>' else v, c) for (k, v, c) in _GOLDEN]


def _write(path: Path, monkeypatch, **extra) -> list[tuple]:
    """Write the golden input, plus ``extra``, and return every card."""
    monkeypatch.setattr(fitsio, "__version__", "0.0.0-golden")
    frame = CameraFrame(data=np.arange(12, dtype=np.uint16).reshape(3, 4),
                        exposure_s=300.0, gain=100, offset=30, binning=1,
                        bayer_pattern=None, temperature_c=-10.0, timestamp=_TS)
    meta = FrameMeta(focal_length_mm=530.0, pixel_size_um=3.76,
                     obj_alt_deg=55.5, obj_az_deg=120.25, airmass=1.21,
                     objctra="00 42 44.3", objctdec="+41 16 09",
                     pointing_source="solved", set_temp_c=-10.0,
                     focuser_pos=11218, rotator_angle_deg=23.4, hfr=2.1,
                     star_count=812)
    save_fits(frame, path, target="M31", filter_name="L", frame_type="Light",
              ra_hours=0.7123, dec_deg=41.269, telescope="Scope",
              instrument="Cam", meta=meta,
              extra_cards=[("DARKOK", True, "verdict")], **extra)
    return [(c.keyword, c.value, c.comment) for c in fits.getheader(path).cards]


@pytest.mark.parametrize("extra", [
    {},
    {"mosaic": None, "panel": None},
    {"mosaic": "", "panel": ""},
    {"mosaic": "   ", "panel": " "},
], ids=["omitted", "none", "empty", "blank"])
def test_a_frame_that_is_not_a_panel_has_todays_header(tmp_path, monkeypatch,
                                                        extra):
    """Card for card against the golden recorded from the unmodified writer,
    for every spelling of "not a panel".

    Mutation 'cards always written' (``if True:`` in place of ``if
    mosaic_text:`` and ``if panel_text:``, so both cards are written empty)
    went red on all four spellings, and on the hub capture case, whose frame
    that is not a panel then carried a MOSAIC card. Each spelling:

        >       assert cards == _golden()
        E       AssertionError: assert [('SIMPLE', T...rue, ''), ...] == [('SIMPLE', T...rue, ''), ...]
        E
        E         At index 19 diff: ('MOSAIC', '', 'Mosaic group; stitch panels, never co-add') != ('RA', 10.6845, 'RA of telescope (deg, J2000)')
        E         Left contains 2 more items, first extra item: ('SWCREATE', 'AstroDeck 0.0.0-golden', 'Creating software')
    """
    cards = _write(tmp_path / "f.fits", monkeypatch, **extra)
    assert cards == _golden()


def test_a_panel_carries_both_cards_and_nothing_else_moves(tmp_path,
                                                           monkeypatch):
    """Mutation 'PANEL never written' (``if False:`` in place of ``if
    panel_text:``) went red here, and on the non-ASCII, hub capture and
    promote cases:

        >       assert by_key["PANEL"] == "1-2", cards
        E       KeyError: 'PANEL'
    """
    cards = _write(tmp_path / "f.fits", monkeypatch, mosaic="M31 mosaic",
                   panel="1-2")
    by_key = {k: v for (k, v, _c) in cards}
    assert by_key["MOSAIC"] == "M31 mosaic", cards
    assert by_key["PANEL"] == "1-2", cards
    # Additive only: without the two new cards, today's header exactly.
    assert [c for c in cards if c[0] not in ("MOSAIC", "PANEL")] == _golden()


def test_a_group_name_outside_ascii_is_folded_and_never_fails_the_save(
        tmp_path, monkeypatch):
    """A FITS header value must be printable ASCII, and astropy raises on
    anything else. A group is named by whoever framed it, so "Veil - east"
    typed with an en dash would have failed the save of every frame of that
    mosaic; a header write must never fail a capture. The value is folded the
    way the dark check's evidence cards are (``darks._ascii_card``).

    Mutation 'no fold' (``_header_text`` returns ``str(value).strip()``)
    went red here alone. The message printed the en dash itself, written
    below as an escape so this file stays ASCII:

        >       cards = _write(tmp_path / "f.fits", monkeypatch,
        ...
        E                   ValueError: FITS header values must contain standard printable ASCII characters; 'Veil \\u2013 east' contains characters not representable in ASCII or non-printable characters.
    """
    cards = _write(tmp_path / "f.fits", monkeypatch,
                   mosaic="Veil \u2013 east", panel="2-1")
    by_key = {k: v for (k, v, _c) in cards}
    assert by_key["MOSAIC"] == "Veil - east", cards
    assert by_key["PANEL"] == "2-1", cards


@pytest.mark.parametrize("name", [
    "x" * 24,                                   # the longest that keeps it
    "Andromeda Galaxy wide field mosaic north",  # 40: the comment goes
    "M31 " + "wide " * 20,                       # past 68: a CONTINUE card
], ids=["24", "40", "long"])
def test_a_long_group_name_is_written_whole_and_warns_nothing(
        tmp_path, monkeypatch, name):
    """From about 25 characters a comment no longer fits beside the value, and
    astropy truncates the comment and warns, once per frame of the night. The
    name is written whole; the comment is dropped instead.

    Mutation 'always comment' (``if True:`` in place of the width test in
    ``_with_comment``) went red on the 40 case alone; the 24 case fits, and
    past 68 characters astropy writes a CONTINUE card without complaint:

        >           cards = _write(tmp_path / "f.fits", monkeypatch, mosaic=name,
        >               warnings.warn(
        E               astropy.io.fits.verify.VerifyWarning: Card is too long, comment will be truncated.
    """
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cards = _write(tmp_path / "f.fits", monkeypatch, mosaic=name,
                       panel="1-1")
    hdr = fits.getheader(tmp_path / "f.fits")
    assert hdr["MOSAIC"] == name.strip(), cards
    # The short case keeps its comment: dropping it everywhere is not the fix.
    if len(name) == 24:
        assert hdr.comments["MOSAIC"] != "", cards


# --- the labels --------------------------------------------------------------


def test_the_mosaic_label_is_the_name_or_else_the_id():
    """Mutation 'id always' (``return (group_id or "").strip()``) went red
    on the first line, and on the kept-name line of the next case; mutation
    'name always' (``return n``) went red on the second line, and on the
    fallback line of the next case:

        >       assert naming.mosaic_label("M31 mosaic", "a1b2") == "M31 mosaic"
        E       AssertionError: assert 'a1b2' == 'M31 mosaic'

        >       assert naming.mosaic_label("", "a1b2") == "a1b2"
        E       AssertionError: assert '' == 'a1b2'
    """
    assert naming.mosaic_label("M31 mosaic", "a1b2") == "M31 mosaic"
    assert naming.mosaic_label("", "a1b2") == "a1b2"
    assert naming.mosaic_label(None, "a1b2") == "a1b2"
    assert naming.mosaic_label("  M31  ", "a1b2") == "M31"
    assert naming.mosaic_label("   ", "a1b2") == "a1b2"
    assert naming.mosaic_label("", "") == ""
    assert naming.mosaic_label(None, None) == ""


def test_a_name_with_nothing_a_header_can_carry_falls_back_to_the_id():
    """A name with no printable ASCII in it at all would fold to an empty
    MOSAIC card, and an empty card groups nothing. The id is the one value
    every group has, so it stands in. A name with some ASCII in it is kept:
    the writer folds the rest.

    Mutation 'emptiness only' (the printable-ASCII test removed, so only an
    empty name falls back) went red here alone. The output printed the three
    characters themselves, written below as escapes so this file stays ASCII:

        >       assert naming.mosaic_label("\\u4ed9\\u5973\\u5ea7", "a1b2") == "a1b2"
        E       AssertionError: assert '\\u4ed9\\u5973\\u5ea7' == 'a1b2'
    """
    assert naming.mosaic_label("\u4ed9\u5973\u5ea7", "a1b2") == "a1b2"
    assert naming.mosaic_label("Veil \u2013 east", "a1b2") == "Veil \u2013 east"


def test_the_panel_label_is_one_based_row_then_col():
    """Section 2.3's label from the server's 0-based row and column: row 0 is
    the north edge, so server panel (0, 1) is ``1-2``, the same label as the
    Plan's ``<name> r-c`` (3.3: ``<row+1>-<col+1>``).

    Mutation '0-based' (``f"{row}-{col}"``) went red here:

        >       assert naming.panel_label(0, 1) == "1-2"
        E       AssertionError: assert '0-1' == '1-2'

    Mutation 'col-row' (``f"{c + 1}-{r + 1}"``) went red on the first line
    too, which is the one that decides it:

        >       assert naming.panel_label(0, 1) == "1-2"
        E       AssertionError: assert '2-1' == '1-2'
    """
    assert naming.panel_label(0, 1) == "1-2"
    assert naming.panel_label(2, 0) == "3-1"
    assert naming.panel_label(None, 1) == ""
    assert naming.panel_label(0, None) == ""
    with pytest.raises(ValueError):
        naming.panel_label(-1, 0)


# --- the token -----------------------------------------------------------------

#: Shared with ui/src/lib/__tests__/naming.test.ts ("PANEL golden vectors").
_PANEL_TEMPLATE = "$$TARGET$$/$$FRAMETYPE$$_$$TARGET$$_$$PANEL$$_$$FILTER$$_$$FRAMENR$$"
_F = {"TARGET": "M31", "FRAMETYPE": "Light", "FILTER": "Ha",
      "DATE": "2026-07-23", "TIME": "213045",
      "DATETIME": "2026-07-23_213045", "NIGHT": "2026-07-23",
      "FRAMENR": "0001"}


def test_the_panel_token_renders_the_label():
    """Mutation 'PANEL unknown to the token engine' (the ``"PANEL"`` entry
    removed from ``KNOWN_TOKENS``) went red here, rendering empty for a panel,
    and on the validator, sanitize and hub capture cases (the last because
    ``set_naming`` refuses the template):

        >       assert str(rel.as_posix()) == "M31/Light_M31_1-2_Ha_0001.fits"
        E       AssertionError: assert 'M31/Light_M31_Ha_0001.fits' == 'M31/Light_M3..._Ha_0001.fits'
    """
    rel = naming.render_relative_path(_PANEL_TEMPLATE, {**_F, "PANEL": "1-2"})
    assert str(rel.as_posix()) == "M31/Light_M31_1-2_Ha_0001.fits"
    assert "PANEL" in naming.token_names()


def test_an_unset_panel_drops_out_with_no_double_underscore():
    """The control: a frame that is not a panel renders as if the token were
    not there. Needs no mutant of its own; it is what every other token does,
    and it passes under 'PANEL unknown' too, which is why the case above
    exists."""
    for fields in (_F, {**_F, "PANEL": ""}):
        rel = naming.render_relative_path(_PANEL_TEMPLATE, fields)
        assert str(rel.as_posix()) == "M31/Light_M31_Ha_0001.fits"


def test_a_template_naming_the_panel_token_is_valid():
    """Mutation 'PANEL unknown to the token engine' went red here too. The
    message's separator is an em dash, written below as an escape so this
    file stays ASCII:

        >       naming.validate_template(_PANEL_TEMPLATE)
        E           ValueError: unknown token(s): $$PANEL$$ \\u2014 valid: $$TARGET$$, $$FRAMETYPE$$, $$FILTER$$, $$DATE$$, $$TIME$$, $$DATETIME$$, $$NIGHT$$, $$FRAMENR$$, $$GAIN$$, $$EXPOSURE$$, $$BINNING$$, $$SENSORTEMP$$
    """
    naming.validate_template(_PANEL_TEMPLATE)


def test_the_panel_value_is_sanitized_like_a_filter():
    """``strict``, like FILTER: a label is digits and a hyphen, and anything
    else that ever reached this token (a separator, a traversal) is folded.
    The mode is mirrored in naming.ts's MODE table.

    Mutation 'loose PANEL' (``"PANEL": "loose"``) went red here alone:

        >       assert rel.as_posix() == "M31/1_2_0001.fits"
        E       AssertionError: assert 'M31/___1 2_0001.fits' == 'M31/1_2_0001.fits'
    """
    rel = naming.render_relative_path("$$TARGET$$/$$PANEL$$_$$FRAMENR$$",
                                      {**_F, "PANEL": "../1 2"})
    assert rel.as_posix() == "M31/1_2_0001.fits"
    assert naming.KNOWN_TOKENS["PANEL"] == "strict"


# --- through the hub -------------------------------------------------------------


def _header(path) -> fits.Header:
    return fits.getheader(Path(path))


async def test_a_panel_capture_carries_the_cards_and_the_token(sim_hub, tmp_path):
    """End to end through ``Hub.capture``: the saved file lands where the
    template says, with the label in its name, and carries both cards; the
    next capture, not a panel, carries neither and drops the token, and its
    header differs from the panel's only by the two cards.

    Mutation 'hub drops the cards' (``_save_captured_frame`` stops passing
    ``mosaic`` and ``panel`` to ``save_fits``) went red here and on the
    promote case:

        >       assert hdr["MOSAIC"] == "M31" and hdr["PANEL"] == "1-2"
        E           KeyError: "Keyword 'MOSAIC' not found."

    Mutation 'hub drops the token' (``_capture_path`` called without
    ``panel``) went red here alone (the temp directory elided):

        >       assert saved.name == "Light_M31 1-2_1-2_0001.fits", saved
        E       AssertionError: WindowsPath('.../M31 1-2/Light_M31 1-2_0001.fits')
        E       assert 'Light_M31 1-2_0001.fits' == 'Light_M31 1-2_1-2_0001.fits'
    """
    config_store.set_naming(NamingConfig(
        template="$$TARGET$$/$$FRAMETYPE$$_$$TARGET$$_$$PANEL$$_$$FRAMENR$$"))

    await sim_hub.capture(0.2, 100, 30, 1, save=True, target="M31 1-2",
                          mosaic="M31", panel="1-2")
    saved = Path(sim_hub.last_frame.saved_path)
    assert saved.name == "Light_M31 1-2_1-2_0001.fits", saved
    hdr = _header(saved)
    assert hdr["MOSAIC"] == "M31" and hdr["PANEL"] == "1-2"

    await sim_hub.capture(0.2, 100, 30, 1, save=True, target="M31 1-2")
    plain = Path(sim_hub.last_frame.saved_path)
    assert plain.name == "Light_M31 1-2_0002.fits", plain
    plain_hdr = _header(plain)
    assert "MOSAIC" not in plain_hdr and "PANEL" not in plain_hdr
    assert list(plain_hdr.keys()) == [k for k in hdr.keys()
                                      if k not in ("MOSAIC", "PANEL")]


async def test_a_promoted_panel_frame_keeps_its_cards(sim_hub, tmp_path):
    """The promote buffer writes from the snapshot frozen at exposure time,
    so a panel frame saved after the fact is still a panel frame.

    Mutation 'snapshot forgets' (``CaptureSnapshot`` built without the two
    values) went red here, and on the hub capture case, on its path, because
    the saved path is built from the snapshot too:

        >       assert hdr["MOSAIC"] == "M31" and hdr["PANEL"] == "2-2"
        E           KeyError: "Keyword 'MOSAIC' not found."
    """
    await sim_hub.capture(0.2, 100, 30, 1, save=False, target="M31 2-2",
                          mosaic="M31", panel="2-2")
    await sim_hub.promote_last_frame()
    hdr = _header(sim_hub.last_frame.saved_path)
    assert hdr["MOSAIC"] == "M31" and hdr["PANEL"] == "2-2"
