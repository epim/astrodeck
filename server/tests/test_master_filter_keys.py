"""A master flat keeps its slot's name, and never shares a file (#371, #372).

#371. Since #332 a frame whose slot name the #277 fold changed carries the
name as typed in ``FILTUTF8``, and the calibration key read it; but
``CalKey.filter`` stayed the FILTER card, the fold, because the library
wrote ``key.filter`` straight into the master's own FILTER card and astropy
raises on a Greek letter in any card. So a master built from flats shot
through a slot named H-alpha in Greek was recorded as filter 'H?', and the
matcher and the health matrix, which compare that with the plan step's
filter as typed, never matched it: a Greek slot's flats read MISSING however
many were banked. Now ``CalKey.filter`` is the decoded name, and the master
writes FILTER through ``fitsio.write_name_card``, the helper ``save_fits``
writes a frame's FILTER through, so the master carries the fold and the
``FILTUTF8`` beside it that the frame did.

#372. The bucket id is the master's file name. ``key_index_id`` passed the
slot name through the strict sanitizer, which maps many names to one ('O III'
and 'O_III' are both 'O_III'), and NTFS ignores case ('Ha' and 'HA' are one
file there). Now a name the sanitizer changed carries a digest of the name,
an id the sanitizer left alone is byte for byte what it was, and a build in
which two ids still collide under casefold digests them both.

The Greek letters are escapes so this file stays ASCII.

Every mutation named below was run in a private scratch copy of ``server/``
(issue #254), never in the shared tree. The failure each produced is
recorded verbatim on the test that caught it; the console printed the Greek
letters as escapes, and they are written so here, and the temporary
directory is elided as <tmp>.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from _simhub import sim_hub  # noqa: F401 (fixture import)

import astrodeck.calibration.keys as keys_mod
import astrodeck.imaging.fitsio as fitsio
from astrodeck.calibration.keys import CalKey, key_from_header, key_index_id
from astrodeck.calibration.library import MASTERS_DIRNAME, CalibrationLibrary
from astrodeck.calibration.matcher import (Gap, LightNeed, MatchTolerance,
                                           best_master)
from astrodeck.devices.base import CameraFrame
from astrodeck.flows.calibration_health import (CalNeed, frame_from_header,
                                                health_matrix)
from astrodeck.imaging.fitsio import save_fits
from astrodeck.persist import safe_id_path
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

_ALPHA = "H\u03b1"
#: The sim wheel's slot 4 ("Ha" out of the box).
_SLOT = 4


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
    """Two flats through ``filter_name``. ``tag`` names the FILES, and never
    the filter, so two filters that differ only in case do not share a source
    file on NTFS before the library is even asked."""
    for i in range(2):
        _save(root / f"flat_{tag}_{i}.fits", "Flat", filter_name,
              level=level, i=i)


def _flat_key(name: str) -> CalKey:
    return CalKey("FLAT", 1.0, 100, 30, None, 1, name)


def _level(path: str) -> float:
    return float(np.mean(fits.getdata(path)))


# ----------------------------------------------------------------- #371


@pytest.fixture
async def greek_flats(sim_hub, monkeypatch, tmp_path):
    """Two flats shot by ``Hub.capture`` through the sim wheel's slot 4,
    renamed to H-alpha in Greek; the capture root is ``tmp_path``."""
    fw = sim_hub.devices["filterwheel"]
    slots = list(fw.filter_names)
    slots[_SLOT] = _ALPHA
    monkeypatch.setattr(fw, "filter_names", slots)
    # The sim's set_position only adds the travel time; the hub reads the
    # slot back from the wheel at capture either way.
    fw.rig.filter_slot = _SLOT
    paths = []
    for _ in range(2):
        await sim_hub.capture(0.2, 100, 30, 1, save=True, target="Flats",
                              frame_type="Flat")
        paths.append(Path(sim_hub.last_frame.saved_path))
    return tmp_path, paths


def test_a_greek_slot_master_carries_the_frame_writers_cards(greek_flats):
    """The master's FILTER is the fold and its FILTUTF8 the name, card for
    card what the flats themselves carry, so the one decoder reads the slot
    back off the master; and the key and the manifest's record carry the
    name as typed.

    The named mutation 'master FILTER is the fold' was run in three forms.
    As the WRITER (``write_name_card(h, "FILTER", key.filter)`` in
    ``_write_master_fits`` replaced by writing ``_card_text(key.filter)``,
    the fold, into FILTER alone) it went red here and on the three
    non-ASCII cases of the frame-writer test below, and on nothing else of
    the eleven files run (329 passed): the manifest's record still carries
    the name, so the matcher and both health cases stayed green, which is
    why this case reads the master's own cards:

            assert cards == {k: flat_hdr[k] for k in ("FILTER", fitsio.FILTER_UTF8)}
        E   AssertionError: assert {'FILTER': 'H...LTUTF8': None} == {'FILTER': 'H...8': 'H%CE%B1'}

    As the RECORD (``filter=_card_text(key.filter)`` in the
    ``MasterRecord`` that ``build`` puts in the manifest) it went red here,
    on the matcher case, the health-from-master case, the three non-ASCII
    frame-writer cases and the control; the raw-flats health case stayed
    green:

            assert master.filter == _ALPHA, master
        E   AssertionError: MasterRecord(id='flat_g100_o30_b1_fH\\u03b1', frame_type='FLAT', exposure_s=0.2, gain=100, offset=30, temp_c=12.3, binning=1...
        E   assert 'H?' == 'H\\u03b1'

    As the KEY, which is the code before #371 (``filt = str(header.get(
    "FILTER", "") or "")`` in ``key_from_header``), it went red on all of
    those and on the raw-flats health case, and in
    test_fits_filter_names_distinct on the calibration-key case:

            assert key_from_header(flat_hdr).filter == _ALPHA
        E   AssertionError: assert 'H?' == 'H\\u03b1'

    Mutation 'raw name in the card' (the line before #371, ``if key.filter:
    h["FILTER"] = key.filter``, with the key now carrying the name) went red
    at the build on every case that builds a non-ASCII slot's master, the
    control among them:

            report = library.build()
        E   ValueError: FITS header values must contain standard printable ASCII characters; 'H\\u03b1' contains characters not representable in ASCII or non-printable characters.
    """
    root, flats = greek_flats
    flat_hdr = fits.getheader(flats[0])
    assert flat_hdr["FILTER"] == "H?"
    assert key_from_header(flat_hdr).filter == _ALPHA

    library = CalibrationLibrary(lambda: root)
    report = library.build()
    assert report.masters_built == 1, report
    (master,) = library.list_masters()
    assert master.filter == _ALPHA, master
    hdr = fits.getheader(master.path)
    cards = {k: hdr.get(k) for k in ("FILTER", fitsio.FILTER_UTF8)}
    assert cards == {k: flat_hdr[k] for k in ("FILTER", fitsio.FILTER_UTF8)}
    assert fitsio.full_name(hdr, "FILTER") == _ALPHA


def _greek_need() -> LightNeed:
    """A light through the Greek slot, as a plan step names it."""
    return LightNeed(exposure_s=0.2, gain=100, offset=30, temp_c=None,
                     binning=1, filter=_ALPHA)


def test_a_greek_slot_master_matches_a_plan_step_of_that_filter(greek_flats):
    """The acceptance of #371 on the simulator: the master is the one the
    matcher picks for a plan step whose filter is the slot's name as typed.
    The coverage gap names only the dark (none was shot), which shows the
    step was looked at and not skipped: an empty library gives no gaps.

    Mutation 'master FILTER is the fold', as the record, went red here:

            assert best_master(_greek_need(), [master], MatchTolerance(),
        E   AssertionError: assert None == MasterRecord(id='flat_g100_o30_b1_fH\\u03b1', frame_type='FLAT', exposure_s=0.2, gain=100, offset=30, temp_c=12.3, binning=1...<tmp>...flat_g100_o30_b1_fH\\u03b1.fits', built_ts=1790597953.145875)

    As the key it went red on the same line, the master's id then
    'flat_g100_o30_b1_fH~1bb56127' (the fold 'H?', which the sanitizer
    changes, and its digest), and so did 'no FILTUTF8' and 'readers key on
    FILTER', both recorded in test_fits_filter_names_distinct. As the
    writer it stayed green here (see the case above).
    """
    root, _flats_ = greek_flats
    library = CalibrationLibrary(lambda: root)
    library.build()
    (master,) = library.list_masters()
    assert best_master(_greek_need(), [master], MatchTolerance(),
                       "FLAT") == master
    plan = SequencePlan(targets=[Target(
        name="M31", ra_hours=0.71, dec_deg=41.27, steps=[ExposureStep(
            filter=_ALPHA, exposure_s=0.2, count=1)])])
    gaps = library.coverage(plan, MatchTolerance(), 5.0)
    assert gaps == [Gap(filter=_ALPHA, exposure_s=0.2, gain=100, binning=1,
                        missing=("dark",))], gaps


def test_health_does_not_read_missing_for_a_greek_slots_master(greek_flats):
    """The health row for the Greek slot, fed by the master alone (the raws
    pruned, as a tidied library is): credited to that master, OK.

    Mutation 'master FILTER is the fold', as the record, went red here and
    not on the raw-flats case below:

            assert [(r.filter, r.verdict, r.master_id) for r in rows] == [
        E   AssertionError: [HealthRow(kind='FLAT', exposure_s=None, gain=100, offset=30, temp_c=None, binning=1, filter='H\\u03b1', rotation_deg=None, ... of the quota'),), family=0, contradicted=0, measured=0, newest_ts=None, age_days=None, master_id=None, from_master=0)]
        E   assert [('H\\u03b1', 'MISSING', None)] == [('H\\u03b1', 'OK',..._o30_b1_fH\\u03b1')]

    As the key, and under 'no FILTUTF8' and 'readers key on FILTER', it
    went red with the same lines, the expected master id ending
    'fH~1bb56127'.
    """
    root, _flats_ = greek_flats
    library = CalibrationLibrary(lambda: root)
    library.build()
    (master,) = library.list_masters()
    rows = health_matrix([CalNeed(_greek_need())], (), masters=[master],
                         kinds=("FLAT",), quota=1)
    assert [(r.filter, r.verdict, r.master_id) for r in rows] == [
        (_ALPHA, "OK", master.id)], rows


def test_health_does_not_read_missing_for_a_greek_slots_raw_flats(
        greek_flats):
    """The same row fed by the raw flats alone, through the library's own
    walk and ``frame_from_header``: both flats counted, OK.

    Mutation 'master FILTER is the fold', as the key, went red here; as the
    record it did not, the raw flats never passing through a record:

            assert [(r.verdict, r.have) for r in rows] == [("OK", 2)], rows
        E   AssertionError: [HealthRow(kind='FLAT', exposure_s=None, gain=100, offset=30, temp_c=None, binning=1, filter='H\\u03b1', rotation_deg=None, ... of the quota'),), family=0, contradicted=0, measured=0, newest_ts=None, age_days=None, master_id=None, from_master=0)]
        E   assert [('MISSING', 0)] == [('OK', 2)]

    'no FILTUTF8' and 'readers key on FILTER' went red here with the same
    lines.
    """
    root, _flats_ = greek_flats
    library = CalibrationLibrary(lambda: root)
    frames = [frame_from_header(h, ts=ts, path=str(p))
              for p, h, ts in library.iter_cal_headers()]
    rows = health_matrix([CalNeed(_greek_need())], frames, kinds=("FLAT",),
                         quota=1)
    assert [(r.verdict, r.have) for r in rows] == [("OK", 2)], rows


@pytest.mark.parametrize("name", [
    "H\u03b1",           # a Greek letter: one '?'
    "O\u2013III",        # an en dash: the fold's own '-'
    "\uff33ii",          # a fullwidth S: NFKD reads it as 'S'
    "Ha",                # ASCII: no FILTUTF8 at all
])
def test_the_master_writes_the_slot_exactly_as_the_frame_did(tmp_path, name):
    """One helper, not two folds that agree on the case someone tested. The
    master's FILTER and FILTUTF8 are the flat's own, for a name the fold
    transliterates as well as one it marks, and an ASCII slot's master has
    no FILTUTF8 card, as it never had.

    Mutation 'library has its own fold' (the master's FILTER written as
    ``key.filter.encode("ascii", "replace")`` and its FILTUTF8 as
    ``quote(key.filter, safe="")``: the same cards for the Greek name, a
    different fold for the rest) went red on the en-dash and fullwidth cases
    and on nothing else of the eleven files run:

            assert got == want, (got, want)
        E   AssertionError: ({'FILTER': 'O?III', 'FILTUTF8': 'O%E2%80%93III'}, {'FILTER': 'O-III', 'FILTUTF8': 'O%E2%80%93III'})

        E   AssertionError: ({'FILTER': '?ii', 'FILTUTF8': '%EF%BC%B3ii'}, {'FILTER': 'Sii', 'FILTUTF8': '%EF%BC%B3ii'})

    Mutation 'as-typed card always' (``if True:`` in place of ``if text !=
    str(value):`` in ``write_name_card``) went red on the ASCII case, at
    the assertion of its own; before that assertion was added this case
    stayed green under it, the frame and the master both carrying the card:

            assert (fitsio.FILTER_UTF8 in hdr) is (not name.isascii()), got
        E   AssertionError: {'FILTER': 'Ha', 'FILTUTF8': 'Ha'}

    'master FILTER is the fold' went red on the three non-ASCII cases, as
    the writer at the cards and as the record at the last line; for the
    Greek name:

            assert got == want, (got, want)
        E   AssertionError: ({'FILTER': 'H?', 'FILTUTF8': None}, {'FILTER': 'H?', 'FILTUTF8': 'H%CE%B1'})

            assert master.filter == name
        E   AssertionError: assert 'H?' == 'H\\u03b1'
    """
    _flats(tmp_path, "a", name, 30000)
    flat_hdr = fits.getheader(tmp_path / "flat_a_0.fits")
    library = CalibrationLibrary(lambda: tmp_path)
    library.build()
    (master,) = library.list_masters()
    hdr = fits.getheader(master.path)
    got = {k: hdr.get(k) for k in ("FILTER", fitsio.FILTER_UTF8)}
    want = {k: flat_hdr.get(k) for k in ("FILTER", fitsio.FILTER_UTF8)}
    assert got == want, (got, want)
    # Its own assertion, not only "the same as the frame": the frame and the
    # master share the helper, so a helper that wrote FILTUTF8 for every name
    # would keep the two equal.
    assert (fitsio.FILTER_UTF8 in hdr) is (not name.isascii()), got
    assert master.filter == name


def test_a_build_with_a_greek_slot_still_writes_its_darks_and_bias(tmp_path):
    """CONTROL: a library holding a Greek slot's flats beside darks and
    bias builds all three masters, and no header write raises. A dark and a
    bias master carry no FILTER card and no FILTUTF8, exactly as before.

    Mutation 'raw name in the card' went red here, at the build:

            report = library.build()
        E   ValueError: FITS header values must contain standard printable ASCII characters; 'H\\u03b1' contains characters not representable in ASCII or non-printable characters.

    and 'master FILTER is the fold', as the record or as the key, at the
    last line:

            assert by_kind["FLAT"].filter == _ALPHA
        E   AssertionError: assert 'H?' == 'H\\u03b1'
    """
    _flats(tmp_path, "a", _ALPHA, 30000)
    for i in range(2):
        _save(tmp_path / f"dark_{i}.fits", "Dark", level=100, exposure_s=300.0,
              i=i)
        _save(tmp_path / f"bias_{i}.fits", "Bias", level=50, exposure_s=0.001,
              i=i)
    library = CalibrationLibrary(lambda: tmp_path)
    report = library.build()
    assert report.masters_built == 3, report
    by_kind = {m.frame_type: m for m in library.list_masters()}
    assert sorted(by_kind) == ["BIAS", "DARK", "FLAT"], by_kind
    for kind in ("DARK", "BIAS"):
        hdr = fits.getheader(by_kind[kind].path)
        assert "FILTER" not in hdr and fitsio.FILTER_UTF8 not in hdr, kind
    assert by_kind["FLAT"].filter == _ALPHA


# ----------------------------------------------------------------- #372


def test_an_id_the_sanitizer_leaves_alone_is_byte_identical():
    """CONTROL: every name the strict sanitizer leaves as it is keeps the
    id it always had, so no library built before #372 changes a file name;
    so does a flat with no FILTER at all, a one-shot-colour camera's, and
    so do the darks and the bias, which have no filter.

    Mutation 'digest always' (``if True:`` in place of ``if digest or
    safe_filter != key.filter:`` in ``key_index_id``) went red here, on the
    stable-digest case, the case and the sanitizer library cases, on
    test_file_safety_sweep's keeps_real_filter_names_intact and on
    test_fits_filter_names_distinct's ASCII control:

            assert key_index_id(_flat_key(name), 5.0) == (
        E   AssertionError: assert 'flat_g100_o30_b1_fL~72dfcfb0' == 'flat_g100_o30_b1_fL'
    """
    for name in ("L", "Ha", "OIII"):
        assert key_index_id(_flat_key(name), 5.0) == (
            f"flat_g100_o30_b1_f{name}")
    assert key_index_id(_flat_key(""), 5.0) == "flat_g100_o30_b1_fnone"
    assert key_index_id(CalKey("DARK", 300.0, 100, 30, -10.0, 1, ""),
                        5.0) == "dark_e300.000_g100_o30_t-10_b1"
    assert key_index_id(CalKey("BIAS", 0.0, 100, 30, -10.0, 1, ""),
                        5.0) == "bias_g100_o30_t-10_b1"


def test_a_name_the_sanitizer_changed_gets_a_stable_digest():
    """The digest is the first eight hex digits of the SHA-256 of the name's
    UTF-8, pinned here as literals: ``hash()`` is salted per process, and a
    master whose id changed between two builds would be a new file each
    time. 'O III' and 'O_III' sanitize alike and now differ; a name the
    sanitizer empties is not the empty filter's 'none'; and every such id is
    still one contained file name.

    Mutation 'no digest' (``if digest:`` in place of ``if digest or
    safe_filter != key.filter:`` in ``key_index_id``) went red here and on
    the sanitizer library case:

            assert ids == {
        E   AssertionError: {'..': 'flat_g100_o30_b1_fnone', '../../x': 'flat_g100_o30_b1_fx', 'O III': 'flat_g100_o30_b1_fO_III', 'O_III': 'flat_g100_o30_b1_fO_III'}

    Mutation 'digest from hash()' (``format(hash(name) & 0xFFFFFFFF,
    "08x")`` in ``_name_digest``) went red here and on the sanitizer and
    'none' library cases:

        E   AssertionError: {'..': 'flat_g100_o30_b1_fnone~96d07cb2', '../../x': 'flat_g100_o30_b1_fx~76db3d13', 'O III': 'flat_g100_o30_b1_fO_III~24cf2066', 'O_III': 'flat_g100_o30_b1_fO_III'}
    """
    ids = {name: key_index_id(_flat_key(name), 5.0)
           for name in ("O III", "O_III", "..", "../../x")}
    assert ids == {
        "O III": "flat_g100_o30_b1_fO_III~8202bad7",
        "O_III": "flat_g100_o30_b1_fO_III",
        "..": "flat_g100_o30_b1_fnone~5ec1f7e7",
        "../../x": "flat_g100_o30_b1_fx~9cdf6a50",
    }, ids
    for kid in ids.values():
        safe_id_path(Path("masters"), kid, ".fits")


def test_names_that_differ_only_in_case_get_two_masters_in_two_files(
        tmp_path, bus_lines):
    """'Ha' and 'HA' are two filters, and on NTFS 'flat_..._fHa.fits' and
    'flat_..._fHA.fits' are one file: the second master overwrote the first
    and both records pointed at it. Both ids now carry a digest, the two
    masters hold their own flats (told apart by pixel level), each master's
    cards give back its own name, and one log line names both filters.

    Before the fix, on this Windows box, the 'HA' record came back naming
    the 'Ha' file:

        >       assert len({m.id.casefold() for m in masters.values()}) == 2, masters
        E       AssertionError: {'HA': MasterRecord(id='flat_g100_o30_b1_fHA', frame_type='FLAT', exposure_s=1.0, gain=100, offset=30, temp_c=-10.0, b...<tmp>...flat_g100_o30_b1_fHa.fits', built_ts=1790596511.7233722)}
        E       assert 1 == 2

    Mutation 'case collision not checked' (the ids grouped by ``kid`` in
    ``_distinct_ids``, not by ``kid.casefold()``) went red here, and on the
    last-guard test's 'Ha' case, whose master then kept the undigested id
    'flat_g100_o30_b1_fHa'. The last guard refused 'HA', so one master:

            assert report.masters_built == 2, report
        E   AssertionError: BuildReport(masters_built=1, frames_indexed=2, buckets=1)

    Mutation 'collision never re-minted' (each id of a colliding set kept as
    it was) went red here and on the 'none' case with the same lines, and
    on the last-guard test's 'Ha' case; 'digest always' went red here at
    the log line, there being no collision to name:

            assert len(named) == 1 and named[0][2] == "calibration", bus_lines
        E   AssertionError: []
    """
    _flats(tmp_path, "a", "Ha", 20000)
    _flats(tmp_path, "b", "HA", 40000)
    library = CalibrationLibrary(lambda: tmp_path)
    report = library.build()
    assert report.masters_built == 2, report
    masters = {m.filter: m for m in library.list_masters()}
    assert sorted(masters) == ["HA", "Ha"], masters
    assert len({m.id.casefold() for m in masters.values()}) == 2, masters
    assert len({m.path.casefold() for m in masters.values()}) == 2, masters
    files = sorted(p.name for p in (tmp_path / MASTERS_DIRNAME).glob("*.fits"))
    assert len(files) == 2, files
    assert {n: round(_level(m.path), -3) for n, m in masters.items()} == {
        "Ha": 20000, "HA": 40000}
    assert {n: fitsio.full_name(fits.getheader(m.path), "FILTER")
            for n, m in masters.items()} == {"Ha": "Ha", "HA": "HA"}
    named = [line for line in bus_lines
             if "'Ha'" in line[1] and "'HA'" in line[1]]
    assert len(named) == 1 and named[0][2] == "calibration", bus_lines


def test_names_the_sanitizer_folds_together_get_two_masters_in_two_files(
        tmp_path):
    """'O III' and 'O_III' are both 'O_III' to the strict sanitizer; the
    one it changed carries the digest and the other keeps its id.

    Before the fix the two were one bucket and one master
    (``BuildReport(masters_built=1, frames_indexed=4, buckets=1)``).
    Mutation 'no digest' went red here. The build's own collision check
    still parted the two, so two masters in two files, but 'O_III' lost the
    id it always had:

            assert {n: m.id for n, m in masters.items()} == {
        E   AssertionError: {'O III': MasterRecord(id='flat_g100_o30_b1_fO_III~8202bad7', frame_type='FLAT', exposure_s=1.0, gain=100, offset=30, ...<tmp>...flat_g100_o30_b1_fO_III~6fc2c5fa.fits', built_ts=1790597223.5603924)}

    'digest always' and 'digest from hash()' went red on the same line.
    """
    _flats(tmp_path, "a", "O III", 20000)
    _flats(tmp_path, "b", "O_III", 40000)
    library = CalibrationLibrary(lambda: tmp_path)
    assert library.build().masters_built == 2
    masters = {m.filter: m for m in library.list_masters()}
    assert {n: m.id for n, m in masters.items()} == {
        "O III": "flat_g100_o30_b1_fO_III~8202bad7",
        "O_III": "flat_g100_o30_b1_fO_III"}, masters
    assert {n: round(_level(m.path), -3) for n, m in masters.items()} == {
        "O III": 20000, "O_III": 40000}


def test_no_filter_and_a_slot_named_none_get_two_masters(tmp_path):
    """A flat with no FILTER card, a one-shot-colour camera's, has always
    had the id 'fnone'; so does a slot named 'none', which the sanitizer
    leaves alone. Neither may change on its own (the control above), so a
    build holding both digests both, and the two sets are two masters.

    Before the fix: one master of all four flats. Mutation 'collision never
    re-minted' went red here, the last guard refusing the second set:

            assert library.build().masters_built == 2
        E   assert 1 == 2

    'case collision not checked' stays green here: these two ids are
    equal, not only equal under casefold.
    """
    _flats(tmp_path, "a", "", 20000)
    _flats(tmp_path, "b", "none", 40000)
    library = CalibrationLibrary(lambda: tmp_path)
    assert library.build().masters_built == 2
    masters = {m.filter: m for m in library.list_masters()}
    assert {n: m.id for n, m in masters.items()} == {
        "": "flat_g100_o30_b1_fnone~e3b0c442",
        "none": "flat_g100_o30_b1_fnone~140bedbf"}, masters
    assert {n: round(_level(m.path), -3) for n, m in masters.items()} == {
        "": 20000, "none": 40000}


@pytest.mark.parametrize("first, second, kept", [
    # The sanitizer's fold: the two ids are equal, digest and all.
    ("O III", "O/III", "flat_g100_o30_b1_fO_III~00000000"),
    # The disk's fold: the two ids differ only in case, digest and all, and
    # are one file on NTFS, so the guard compares them under casefold too.
    ("Ha", "HA", "flat_g100_o30_b1_fHa~00000000"),
])
def test_a_collision_the_digest_cannot_part_refuses_the_second(
        tmp_path, monkeypatch, bus_lines, first, second, kept):
    """The last guard: when two ids collide even with their digests (forced
    here by a digest that is the same for every name; SHA-256 cut to eight
    hex digits makes it a one-in-four-billion accident, or a header built
    to cause it), the second bucket is refused and one log line names both
    filters. One master, holding the first bucket's flats, rather than two
    records sharing one file.

    Mutation 'no final guard' (``if False:`` in place of ``if first is not
    None:`` in ``_distinct_ids``) went red on both cases. 'O III': the
    second set took the first's id, and the master holds its flats:

            assert round(_level(master.path), -3) == 20000
        E   AssertionError: assert 40000.0 == 20000

    'Ha': two masters, whose files are one on NTFS:

            assert library.build().masters_built == 1
        E   assert 2 == 1

    Mutation 'the last guard ignores case' (``first = out.get(kid)`` in
    place of ``first = taken.get(kid.casefold())``) went red on the 'Ha'
    case alone, with the same two lines; every other case of this file
    stayed green under it, which is why the 'Ha' case exists.
    """
    monkeypatch.setattr(keys_mod, "_name_digest", lambda name: "00000000")
    _flats(tmp_path, "a", first, 20000)
    _flats(tmp_path, "b", second, 40000)
    library = CalibrationLibrary(lambda: tmp_path)
    assert library.build().masters_built == 1
    (master,) = library.list_masters()
    assert master.id == kept, master
    assert round(_level(master.path), -3) == 20000
    refused = [line for line in bus_lines if line[0] == "warning"
               and repr(first) in line[1] and repr(second) in line[1]]
    assert len(refused) == 1 and refused[0][2] == "calibration", bus_lines
