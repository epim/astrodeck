"""Two wheel slots whose names fold alike never share a grouping key (#332).

The #277 fold writes every free-text card as printable ASCII, and a letter
with no ASCII form becomes one '?'. So two slots named H-alpha and H-beta in
Greek both write ``FILTER = 'H?'``. Before #332 only OBJECT kept the name as
typed (``OBJUTF8``), and three readers grouped by the FILTER card: the
calibration key (so the flats of both slots went into one bucket and one
master), the night stack's flats (one group) and the session stack's
backfill (one filter). The fix, pinned here:

* ``save_fits`` writes ``FILTUTF8`` beside FILTER, percent-encoded as
  ``OBJUTF8`` is, and only when the fold changed the name;
* ``fitsio.full_name(header, "FILTER")`` is the one decoder, beside
  ``full_object_name``;
* ``calibration/keys.py``, ``imaging/nightstack.py`` and
  ``imaging/stackbackfill.py`` key on the decoded name. Since #371
  ``CalKey.filter`` IS the decoded name, and a master writes it through the
  frame writer's own ``fitsio.write_name_card``
  (``test_master_filter_keys.py``).

The frames are the simulator's own: two slots of the sim wheel renamed, one
flat and one light shot through each by ``Hub.capture``, so every card is
the one the capture path writes. The Greek letters are escapes so this file
stays ASCII.

Every mutation named below was run in a private scratch copy of ``server/``
(issue #254), never in the shared tree. The failure each produced is
recorded verbatim on the test that caught it (the console printed the Greek
letters as escapes, and they are written so here).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from astropy.io import fits

from _simhub import sim_hub  # noqa: F401 (fixture import)

import astrodeck.imaging.fitsio as fitsio
from astrodeck.calibration.keys import CalKey, key_from_header, key_index_id
from astrodeck.calibration.library import CalibrationLibrary
from astrodeck.imaging import nightstack
from astrodeck.imaging.stackbackfill import BackfillItem, read_backfill_frame

_ALPHA = "H\u03b1"
_BETA = "H\u03b2"
#: The sim wheel's slots 4 and 5 ("Ha" and "OIII" out of the box).
_SLOTS = {_ALPHA: 4, _BETA: 5}


def _name_the_slots(hub, monkeypatch, names: dict[str, int]) -> None:
    fw = hub.devices["filterwheel"]
    slots = list(fw.filter_names)
    for name, slot in names.items():
        slots[slot] = name
    monkeypatch.setattr(fw, "filter_names", slots)


async def _shoot(hub, slot: int, frame_type: str, target: str) -> Path:
    """One saved frame through ``slot``. The wheel is set directly: the sim's
    ``set_position`` only adds the travel time, and the hub reads the slot
    back from the wheel at capture either way."""
    hub.devices["filterwheel"].rig.filter_slot = slot
    await hub.capture(0.2, 100, 30, 1, save=True, target=target,
                      frame_type=frame_type)
    return Path(hub.last_frame.saved_path)


@pytest.fixture
async def two_slots(sim_hub, monkeypatch):
    """``{name: (flat, light)}`` for the two Greek slots, as saved."""
    _name_the_slots(sim_hub, monkeypatch, _SLOTS)
    out = {}
    for name, slot in _SLOTS.items():
        out[name] = (await _shoot(sim_hub, slot, "Flat", "Flats"),
                     await _shoot(sim_hub, slot, "Light", "M31"))
    return out


def test_both_slots_write_one_filter_and_keep_their_own_name(two_slots):
    """The writer: FILTER is the fold, the same for both, and FILTUTF8
    carries each slot's name, which the one decoder gives back.

    Mutation 'no FILTUTF8' (the ``if filter_text != str(filter_name):``
    block removed from ``save_fits``) went red here and on every case of
    this file but the control:

        >       assert got == {n: ("H?", n) for n in _SLOTS}, got
        E       AssertionError: {'H\\u03b1': ('H?', 'H?'), 'H\\u03b2': ('H?', 'H?')}

    Since #371 that block is ``fitsio.write_name_card``'s, and the mutation
    re-run there (``and keyword != "FILTER"`` added to ``if text !=
    str(value):``) went red on the same four cases with the same assertion,
    and on the eight cases of test_master_filter_keys that shoot or save a
    non-ASCII slot.

    Mutation 'one card for all' (``full_name`` reading ``OBJUTF8`` for
    every keyword) went red on the same four cases.
    """
    got = {}
    for name, (flat, _light) in two_slots.items():
        hdr = fits.getheader(flat)
        got[name] = (hdr["FILTER"], fitsio.full_name(hdr, "FILTER"))
    assert got == {n: ("H?", n) for n in _SLOTS}, got


def test_the_two_slots_give_distinct_calibration_keys(two_slots):
    """The calibration key, its bucket id, and the library's own buckets:
    one flat each, never one bucket of two. And the build that follows
    makes two masters and does not fail: since #371 ``CalKey.filter`` is
    the name as typed, and the master writes it through
    ``fitsio.write_name_card``, so its FILTER card is the fold and no Greek
    letter reaches astropy.

    Mutation 'readers key on FILTER' (all three readers restored to read
    the FILTER card: ``key_from_header``'s ``filt``,
    ``nightstack.flats_by_filter`` and ``read_backfill_frame`` each reading
    ``header.get("FILTER")``) went red on this case, the night-stack case
    and the backfill case, and on the eight cases of
    test_master_filter_keys that shoot or save a non-ASCII slot;
    test_calibration_keys, test_calibration_library, test_nightstack,
    test_session_stack_backfill, test_gallery, test_flows_calibration_health,
    test_file_safety_sweep, test_fits_non_ascii_names and
    test_panel_provenance all stayed green under it (322 passed). Here, one bucket for the two slots:

            assert len(set(keys.values())) == 2, keys
        E   AssertionError: {'H\\u03b1': CalKey(frame_type='FLAT', exposure_s=0.2, gain=100, offset=30, temp_c=12.3, binning=1, filter='H?'), 'H\\u03b2': CalKey(frame_type='FLAT', exposure_s=0.2, gain=100, offset=30, temp_c=12.3, binning=1, filter='H?')}
        E   assert 1 == 2

    Mutation 'keys reads FILTER' (that reader alone, which is the key
    before #371) went red here with the same lines, and on the same eight
    cases of test_master_filter_keys.

    Mutation 'raw name in the card' (the master's FILTER written as
    ``key.filter``, the line before #371) went red here at the build:

            report = library.build()
        E   ValueError: FITS header values must contain standard printable ASCII characters; 'H\\u03b1' contains characters not representable in ASCII or non-printable characters.

    The earlier mutation 'the bucket id reads FILTER' has no form since
    #371: the key carries the name and nothing else.
    """
    keys = {name: key_from_header(fits.getheader(flat))
            for name, (flat, _light) in two_slots.items()}
    assert len(set(keys.values())) == 2, keys
    assert {n: k.filter for n, k in keys.items()} == {n: n for n in _SLOTS}
    ids = {name: key_index_id(k, 5.0) for name, k in keys.items()}
    assert len(set(ids.values())) == 2, ids

    root = two_slots[_ALPHA][0].parent.parent
    library = CalibrationLibrary(lambda: root)
    buckets = library.scan_raw(5.0)
    assert sorted(len(paths) for paths in buckets.values()) == [1, 1], buckets
    report = library.build()
    assert report.masters_built == 2, report
    assert sorted(m.id for m in library.list_masters()) == sorted(ids.values())


def test_the_two_slots_give_distinct_night_stack_groups(two_slots):
    """The night stack's flats are grouped by the filter they carry, and a
    master flat is built per group: the name as typed, upper-cased as the
    night stack has always keyed its flats. The lights are grouped by the
    filter in the FILE NAME, which ``naming.sanitize_component`` writes with
    the Greek letter in it, so that half never depended on FILTER; it is
    here so both of the night stack's groupings are shown apart.

    Mutation 'readers key on FILTER' went red here with one group, and
    'nightstack reads FILTER' (that reader alone) here alone, with the
    same lines (the temporary directory elided as <tmp>):

        >       assert sorted(groups) == sorted(n.upper() for n in _SLOTS), groups
        E       AssertionError: {'H?': [WindowsPath('<tmp>/Flat...<tmp>/Flats/Flat_Flats_H\\u03b2_2026-09-26_100323_0002.fits')]}
        E       assert ['H?'] == ['H\\u0391', 'H\\u0392']
    """
    flats_dir = two_slots[_ALPHA][0].parent
    groups = nightstack.flats_by_filter(sorted(flats_dir.glob("*.fits")))
    assert sorted(groups) == sorted(n.upper() for n in _SLOTS), groups
    assert {k: len(v) for k, v in groups.items()} == {
        n.upper(): 1 for n in _SLOTS}

    notes: list[str] = []
    _bias, flats = nightstack.load_masters(None, flats_dir, list(_SLOTS),
                                           notes=notes)
    assert sorted(flats) == sorted(n.upper() for n in _SLOTS), notes

    lights_dir = two_slots[_ALPHA][1].parent
    for name in _SLOTS:
        frames, _notes = nightstack.scan_frames(lights_dir, filter_name=name)
        assert [f.path for f in frames] == [two_slots[name][1]], frames


def test_the_backfill_reads_each_frame_under_its_own_name(two_slots):
    """The stack backfill hands the session stacker the filter off the
    frame's header, and the live path hands it the wheel's slot name. Read
    through FILTER, both slots came back as 'H?', one filter for two, and
    not the name the live frame loop gives the same frames.

    Mutation 'readers key on FILTER' went red here, and 'backfill reads
    FILTER' (that reader alone) here alone:

        >       assert got == {n: n for n in _SLOTS}, got
        E       AssertionError: {'H\\u03b1': 'H?', 'H\\u03b2': 'H?'}
    """
    got = {name: read_backfill_frame(BackfillItem(path=light))[1]
           for name, (_flat, light) in two_slots.items()}
    assert got == {n: n for n in _SLOTS}, got


async def test_an_ascii_slot_writes_no_filtutf8_and_keys_as_before(sim_hub):
    """CONTROL: the sim wheel's own "Ha" slot. No FILTUTF8 card; the key is
    the key a pre-#332 header gave (the seven-field ``CalKey``, compared
    equal), its bucket id is unchanged, and the night stack and the backfill
    read "Ha" exactly as before.

    Mutation 'FILTUTF8 always' (``if True:`` in place of ``if filter_text
    != str(filter_name):``) went red here alone in this file; in
    test_fits_non_ascii_names on the TELESCOP and INSTRUME rig-name cases
    (an ASCII slot of the sim wheel is in the beam there), both
    byte-for-byte cases and both filter cases of the ASCII control; and on
    five of test_panel_provenance's cases that compare whole headers:

        >       assert fitsio.FILTER_UTF8 not in hdr
        E       AssertionError: assert 'FILTUTF8' not in SIMPLE  =                    T / conforms to FITS standard ...

    (the header's repr elided). Since #371 that test is
    ``fitsio.write_name_card``'s, which writes OBJUTF8 too, and the mutation
    re-run there (``if True:`` in place of ``if text != str(value):``) went
    red here with the same assertion, on seventeen cases of
    test_fits_non_ascii_names (the ASCII target's among them now), on the
    same five of test_panel_provenance, and on the ASCII case of
    test_master_filter_keys' frame-writer test.

    Mutation 'digest always' (every flat's id given a digest in
    ``key_index_id``, #372) went red here:

            assert key_index_id(key, 5.0) == "flat_g100_o30_b1_fHa"
        E   AssertionError: assert 'flat_g100_o3..._fHa~72aa80bf' == 'flat_g100_o30_b1_fHa'

    The earlier mutation 'every key carries the name' has no form since
    #371: ``CalKey`` has no second filter field for an ASCII key to fill.

    Mutation 'no fallback' (``full_name`` returning ``""`` when the frame
    carries no as-typed card) went red here too:

        >       assert list(groups) == ["HA"], groups
        E       AssertionError: {'': [WindowsPath('<tmp>/Flats/Flat_Flats_Ha_2026-09-26_100650_0001.fits')]}
    """
    flat = await _shoot(sim_hub, 4, "Flat", "Flats")
    light = await _shoot(sim_hub, 4, "Light", "M31")
    hdr = fits.getheader(flat)
    assert hdr["FILTER"] == "Ha"
    assert fitsio.FILTER_UTF8 not in hdr
    key = key_from_header(hdr)
    temp = round(float(hdr["CCD-TEMP"]), 3)
    assert key == CalKey("FLAT", 0.2, 100, 30, temp, 1, "Ha"), key
    assert key_index_id(key, 5.0) == "flat_g100_o30_b1_fHa"
    groups = nightstack.flats_by_filter([flat])
    assert list(groups) == ["HA"], groups
    assert read_backfill_frame(BackfillItem(path=light))[1] == "Ha"
