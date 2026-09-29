"""A long wheel-slot name no longer breaks every calibration build (#427).

A flat master's id is its file name (``<capture dir>/_masters/<id>.fits``),
and ``key_index_id`` copied the slot's sanitized name into it with no bound
on its length. A slot named with 300 characters minted a file name past the
255 a single path component may hold, ``hdu.writeto`` raised, and
``CalibrationLibrary.build()`` did not catch it: the build stopped before it
saved the manifest, so masters written earlier in that build stayed on disk
unrecorded and the manifest kept its old rows. One frame carrying such a
FILTER card anywhere under the capture root blocked every master after it,
because the scanner reads every header there, including files this process
did not write. The #277 class: a name somebody typed fails a write.

Now the id keeps at most ``FILTER_ID_MAX_BYTES`` of the sanitized name,
counted in UTF-8 bytes (the Pi's ext4 counts a file name in bytes; NTFS
counts UTF-16 units, never more than the bytes), and a cut counts as a
change the sanitizer made, so the id carries the digest of the full name
and two names that differ only past the cut are two files (#372). And the
build takes an OSError as the loss of ONE bucket: that bucket is left out
with a warning naming it, and every other master is built and recorded.

Every mutation named below was run in a private scratch copy of
``server/`` (issue #254), never in the shared tree. The failure each
produced is recorded verbatim on the test that caught it, the temporary
directory elided as <tmp> and runs of a repeated letter shortened as
``L...L``.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

import astrodeck.imaging.fitsio as fitsio
from astrodeck.calibration.keys import (FILTER_ID_MAX_BYTES, CalKey,
                                        key_index_id)
from astrodeck.calibration.library import MASTERS_DIRNAME, CalibrationLibrary
from astrodeck.calibration.matcher import (Gap, LightNeed, MatchTolerance,
                                           best_master)
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import save_fits
from astrodeck.persist import safe_id_path
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

#: The issue's own probe: a name the strict sanitizer leaves alone, so only
#: the cut can give its id a digest.
_LONG = "L" * 300
#: The same length, differing from ``_LONG`` only in its last character,
#: far past the cut.
_LONG_R = "L" * 299 + "R"
_PREFIX = "flat_g100_o30_b1_f"
#: A description of an OSError as the build's warning gives it: its type and
#: its errno or Windows code, never its text, which names the file.
_CODE = re.compile(r"\[(Errno|WinError) \d+\]")


def _save(path: Path, frame_type: str, filter_name: str = "", *,
          level: int = 30000, exposure_s: float = 1.0, i: int = 0) -> Path:
    """One frame through the frame writer, ``save_fits``, so every card is
    the one a capture writes. ``level`` marks whose pixels a master holds."""
    frame = CameraFrame(data=np.full((16, 16), level + i, np.uint16),
                        exposure_s=exposure_s, gain=100, offset=30, binning=1,
                        bayer_pattern=None, temperature_c=-10.0,
                        timestamp=1_772_000_000.0 + i)
    return save_fits(frame, path, frame_type=frame_type,
                     filter_name=filter_name)


def _flats(root: Path, tag: str, filter_name: str, level: int) -> None:
    """Two flats through ``filter_name``. ``tag`` names the FILES, never the
    filter, so a 300-character slot name is not also a source file's name."""
    for i in range(2):
        _save(root / f"flat_{tag}_{i}.fits", "Flat", filter_name,
              level=level, i=i)


def _flat_key(name: str) -> CalKey:
    return CalKey("FLAT", 1.0, 100, 30, None, 1, name)


def _level(path: str) -> float:
    return float(np.mean(fits.getdata(path)))


def _warnings(bus_lines) -> list[tuple[str, str, str]]:
    return [line for line in bus_lines if line[0] == "warning"]


# ------------------------------------------------------------ the id's cut


def test_a_300_character_slot_builds_and_matches_its_plan_step(
        tmp_path, bus_lines):
    """The acceptance of #427: two flats through a slot named with 300
    characters build a master, the matcher picks it for a plan step whose
    filter is that name, and an ordinary slot beside it is built too. The
    coverage gaps name only the darks (none were shot), which shows both
    steps were looked at: an empty library gives no gaps at all.

    Before the fix the build raised, the manifest unsaved:

            report = library.build()
        E   OSError: [Errno 22] Invalid argument: '<tmp>\\test_a_300_character_slot_buil0\\_masters\\flat_g100_o30_b1_fL...L.fits'

    Mutation 'no length bound' (``safe_filter = sanitize_component(
    key.filter, "strict")`` in ``key_index_id``, the cut dropped) went red
    here, the build now leaving the bucket out and naming the OSError; and
    on the file-name, cut and past-the-cut cases below:

            assert _warnings(bus_lines) == [], bus_lines
        E   AssertionError: [('warning', "flats for filter 'L...L' left out of the masters: building their master failed with OSError: [Errno 22] Invalid argument", 'calibration')]

    Mutation 'cut without digest' (``if digest or sanitize_component(
    key.filter, "strict") != key.filter:``, the cut not counted as a
    change) went red here at the ids, and on every case of the three below:

        E   {'L...L': 'flat_g100_o30_b1_fL...L'} != {'L...L': 'flat_g100_o30_b1_fL...L~10bf7a12'}
    """
    _flats(tmp_path, "long", _LONG, 20000)
    _flats(tmp_path, "r", "R", 40000)
    library = CalibrationLibrary(lambda: tmp_path)
    report = library.build()
    # First, so a bucket the build left out shows the error it logged.
    assert _warnings(bus_lines) == [], bus_lines
    assert report.masters_built == 2, report
    masters = {m.filter: m for m in library.list_masters()}
    assert sorted(masters) == [_LONG, "R"], masters
    assert {n: m.id for n, m in masters.items()} == {
        _LONG: f"{_PREFIX}{'L' * FILTER_ID_MAX_BYTES}~10bf7a12",
        "R": f"{_PREFIX}R"}, masters
    assert {n: round(_level(m.path), -3) for n, m in masters.items()} == {
        _LONG: 20000, "R": 40000}
    # The master's own cards carry the whole name, which the id no longer
    # does: the id is a file name, the name is the slot's.
    assert fitsio.full_name(fits.getheader(masters[_LONG].path),
                            "FILTER") == _LONG
    need = LightNeed(exposure_s=1.0, gain=100, offset=30, temp_c=None,
                     binning=1, filter=_LONG)
    assert best_master(need, list(masters.values()), MatchTolerance(),
                       "FLAT") == masters[_LONG]
    plan = SequencePlan(targets=[Target(
        name="M31", ra_hours=0.71, dec_deg=41.27, steps=[
            ExposureStep(filter=_LONG, exposure_s=1.0, count=1),
            ExposureStep(filter="R", exposure_s=1.0, count=1)])])
    gaps = library.coverage(plan, MatchTolerance(), 5.0)
    assert gaps == [
        Gap(filter=_LONG, exposure_s=1.0, gain=100, binning=1,
            missing=("dark",)),
        Gap(filter="R", exposure_s=1.0, gain=100, binning=1,
            missing=("dark",))], gaps


@pytest.mark.parametrize("name, kept", [
    # ASCII: one byte a letter, so the cut keeps 64 letters.
    ("L" * 300, 64),
    # A CJK letter: three bytes in UTF-8, so 21 letters (63 bytes); a 22nd
    # would be split, and the cut drops it whole.
    ("\u6c22" * 300, 21),
    # A mathematical bold H, which ``str.isalnum`` keeps: four bytes in
    # UTF-8 and two UTF-16 units, so 16 letters.
    ("\U0001d407" * 300, 16),
], ids=["ascii", "cjk", "four-byte"])
def test_the_id_of_a_long_name_is_one_file_name_on_any_disk(
        tmp_path, name, kept):
    """The id of a 300-character name is a file this disk creates, and its
    file name is at most 255 bytes, which is what the Pi's ext4 allows (this
    Windows box counts UTF-16 units, and a four-byte letter is two of them,
    so a cut that counts characters passes the write here and fails there).
    The cut keeps whole letters from the front of the name, and the digest
    follows it.

    Before the fix, and under mutation 'no length bound', all three cases
    went red at the write; for the ASCII name:

            path.write_bytes(b"")
        E   OSError: [Errno 22] Invalid argument: '<tmp>\\test_the_id_of_a_long_name_is_0\\flat_g100_o30_b1_fL...L.fits'

    Mutation 'cut without digest' went red on all three at the last line,
    the id ending where the cut does:

        E   AssertionError: 'flat_g100_o30_b1_fL...L'
        E   assert (None is not None)

    Mutation 'cut counts characters' (``return safe_filter[:
    FILTER_ID_MAX_BYTES]`` in ``_cut``) went red on the four-byte case at
    the bound, having passed the write on NTFS (64 letters are 128 UTF-16
    units there, and 256 bytes on ext4), and on the CJK case at the cut,
    64 letters kept in 192 bytes, under the bound and the wrong cut; ASCII
    stayed green, one byte being one character:

            assert len(f"{kid}.fits".encode("utf-8")) <= 255, len(
        E   AssertionError: 288

        E   AssertionError: 'flat_g100_o30_b1_f\\u6c22...~73a09d5b'

    Mutation 'cut splits a letter' (``"utf-8", "replace"`` in ``_cut``)
    went red on the CJK case alone, the one whose 64th byte falls inside a
    letter:

        E   AssertionError: 'flat_g100_o30_b1_f\\u6c22...\\ufffd~73a09d5b'
    """
    kid = key_index_id(_flat_key(name), 5.0)
    path = safe_id_path(tmp_path, kid, ".fits")
    path.write_bytes(b"")
    assert [p.name for p in tmp_path.iterdir()] == [f"{kid}.fits"]
    assert len(f"{kid}.fits".encode("utf-8")) <= 255, len(
        f"{kid}.fits".encode("utf-8"))
    m = re.fullmatch(rf"{_PREFIX}(.*)~[0-9a-f]{{8}}", kid)
    assert m is not None and m.group(1) == name[:kept], ascii(kid)


def test_a_cut_is_a_change_the_sanitizer_made():
    """A name at the bound keeps the id it always had, byte for byte; one
    letter past it is cut, and the cut id carries the digest of the whole
    name, pinned here as literals. Two 300-character names that differ only
    past the cut are two ids. A name the sanitizer changed AND cut carries
    one digest, not two.

    Mutation 'cut without digest' went red at the collision:

            assert ids[_LONG] != ids[_LONG_R], ids[_LONG]
        E   AssertionError: flat_g100_o30_b1_fL...L
        E   assert 'flat_g100_o30_b1_fL...L' != 'flat_g100_o30_b1_fL...L'

    Mutation 'no length bound' went red at the literals, the two long ids
    whole and different:

            assert ids == {
        E   AssertionError: {'L...L': 'flat_g100_o30_b1_fL...LR'}

    Mutation 'a digest for each change' (``if digest or
    sanitize_component(key.filter, "strict") != key.filter:`` and a second
    ``if safe_filter != sanitize_component(key.filter, "strict"):`` each
    adding the digest) went red here at the last line, and on no other case
    of the eight files run, a name the sanitizer left alone being changed
    by the cut alone:

            assert both.count("~") == 1 and both.startswith(
        E   AssertionError: flat_g100_o30_b1_fO_III_O_III_O_III_O_III_O_III_O_III_O_III_O_III_O_III_O_III_O_II~3e27bd40~3e27bd40
    """
    ids = {name: key_index_id(_flat_key(name), 5.0)
           for name in ("L" * 64, "L" * 65, _LONG, _LONG_R)}
    # The collision on its own line, before the literals: two slots, one
    # file.
    assert ids[_LONG] != ids[_LONG_R], ids[_LONG]
    cut = f"{_PREFIX}{'L' * 64}"
    assert ids == {
        "L" * 64: cut,
        "L" * 65: f"{cut}~0aa592a5",
        _LONG: f"{cut}~10bf7a12",
        _LONG_R: f"{cut}~47cb9c13",
    }, ids
    both = key_index_id(_flat_key("O III " * 60), 5.0)
    assert both.count("~") == 1 and both.startswith(
        f"{_PREFIX}O_III_O_III"), both


def test_long_names_that_differ_only_past_the_cut_get_their_own_masters(
        tmp_path):
    """Three slots whose ids would be one file without the digest: a name
    exactly at the bound, and two 300-character names that begin with it.
    Three masters holding their own flats (told apart by pixel level), and
    the name at the bound keeps the id it always had.

    Mutation 'cut without digest' went red here. The build's own collision
    check (``_distinct_ids``) still parted the three, so three masters in
    three files, but the name at the bound lost the id it always had:

        E   {'L...L': 'flat_g100_o30_b1_fL...L~7280df47'} != {'L...L': 'flat_g100_o30_b1_fL...L'}

    Mutation 'no length bound' went red at the build, the two long buckets
    left out:

            assert library.build().masters_built == 3
        E   assert 1 == 3
        E    +  where 1 = BuildReport(masters_built=1, frames_indexed=2, buckets=3).masters_built
    """
    _flats(tmp_path, "a", "L" * 64, 10000)
    _flats(tmp_path, "b", _LONG, 20000)
    _flats(tmp_path, "c", _LONG_R, 30000)
    library = CalibrationLibrary(lambda: tmp_path)
    assert library.build().masters_built == 3
    masters = {m.filter: m for m in library.list_masters()}
    cut = f"{_PREFIX}{'L' * 64}"
    assert {n: m.id for n, m in masters.items()} == {
        "L" * 64: cut,
        _LONG: f"{cut}~10bf7a12",
        _LONG_R: f"{cut}~47cb9c13"}, masters
    assert {n: round(_level(m.path), -3) for n, m in masters.items()} == {
        "L" * 64: 10000, _LONG: 20000, _LONG_R: 30000}


# ------------------------------------------------- one bucket, not the build


def test_a_bucket_the_disk_refuses_is_left_out_and_the_rest_are_built(
        tmp_path, bus_lines):
    """The disk refuses the 'L' master's file (a directory stands where it
    goes, as a master held open by another program would on Windows). The
    build leaves that bucket out with one warning naming the filter, builds
    the darks and the 'R' master after it, and saves the manifest with
    both. The warning describes the error by its type and code: its text
    names the file, absolutely, and no absolute path leaves the process
    (#421), graded on the test's own directory name, which every spelling
    of the path contains.

    Before the fix, and under mutation 'no per-bucket catch' (``except
    ZeroDivisionError`` in place of ``except OSError`` in ``build``):

            report = library.build()
        E   PermissionError: [Errno 13] Permission denied: '<tmp>\\test_a_bucket_the_disk_refuses0\\_masters\\flat_g100_o30_b1_fL.fits'

    Mutation 'the path in the warning' (``{e}`` in place of
    ``{_described(e)}``) went red at the last line; the line before it
    stayed green, ``str(e)`` carrying the errno too:

            assert [line for line in bus_lines if tmp_path.name in line[1]] == []
        E   Left contains one more item: ('warning', "flats for filter 'L' left out of the masters: building their master failed with [Errno 13] Permission den...<tmp>\\\\test_a_bucket_the_disk_refuses0\\\\_masters\\\\flat_g100_o30_b1_fL.fits'", 'calibration')

    Mutation 'the first refusal ends the build' (``break`` in place of
    ``continue``) went red here, the 'R' bucket coming after 'L':

            assert report.masters_built == 2, report
        E   AssertionError: BuildReport(masters_built=1, frames_indexed=2, buckets=3)
    """
    _flats(tmp_path, "L", "L", 20000)
    _flats(tmp_path, "R", "R", 40000)
    for i in range(2):
        _save(tmp_path / f"dark_{i}.fits", "Dark", level=100,
              exposure_s=300.0, i=i)
    (tmp_path / MASTERS_DIRNAME / f"{_PREFIX}L.fits").mkdir(parents=True)
    library = CalibrationLibrary(lambda: tmp_path)
    report = library.build()
    assert report.masters_built == 2, report
    assert (report.frames_indexed, report.buckets) == (4, 3), report
    assert sorted((m.frame_type, m.filter)
                  for m in library.list_masters()) == [
        ("DARK", ""), ("FLAT", "R")]
    (warning,) = _warnings(bus_lines)
    assert warning[2] == "calibration", warning
    assert warning[1].startswith("flats for filter 'L' "), warning
    assert _CODE.search(warning[1]), warning
    assert [line for line in bus_lines if tmp_path.name in line[1]] == []


def _hostile_dark(path: Path, i: int) -> None:
    """A dark this process did not write, whose EXPTIME no camera gives:
    the id formats it in full, 306 characters of it."""
    hdu = fits.PrimaryHDU(np.full((16, 16), 100 + i, np.uint16))
    for card, value in (("IMAGETYP", "Dark"), ("EXPTIME", 1e300),
                        ("GAIN", 100), ("OFFSET", 30), ("CCD-TEMP", -10.0),
                        ("XBINNING", 1)):
        hdu.header[card] = value
    hdu.writeto(path)


def test_a_dark_whose_id_no_disk_can_hold_is_left_out(tmp_path, bus_lines):
    """The cut bounds the one string in an id; the numbers are the camera's,
    a few digits each, but a header built to make one long (an EXPTIME of
    1e300 formats to 306 characters) still mints an id no disk can hold.
    That bucket is left out with a warning naming it, and the flats beside
    it are built.

    Before the fix, and under mutation 'no per-bucket catch':

            report = library.build()
        E   OSError: [Errno 22] Invalid argument: '<tmp>\\test_a_dark_whose_id_no_disk_c0\\_masters\\dark_e1000000000000000052504760255...000.000_g100_o30_t-10_b1.fits'

    Mutation 'the path in the warning' went red at the last line:

        E   Left contains one more item: ('warning', "darks of 1e+300 s at gain 100 left out of the masters: building their master failed with [Errno 22] Inval...0.000_g100_o30_t-10_b1.fits'", 'calibration')

    Mutation 'the first refusal ends the build' went red here, the darks'
    bucket coming first:

            assert report.masters_built == 1, report
        E   AssertionError: BuildReport(masters_built=0, frames_indexed=0, buckets=2)
    """
    for i in range(2):
        _hostile_dark(tmp_path / f"dark_{i}.fits", i)
    _flats(tmp_path, "R", "R", 40000)
    library = CalibrationLibrary(lambda: tmp_path)
    report = library.build()
    assert report.masters_built == 1, report
    assert [(m.frame_type, m.filter) for m in library.list_masters()] == [
        ("FLAT", "R")]
    (warning,) = _warnings(bus_lines)
    assert warning[1].startswith("darks of 1e+300 s at gain 100 "), warning
    assert _CODE.search(warning[1]), warning
    assert [line for line in bus_lines if tmp_path.name in line[1]] == []
