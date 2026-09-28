"""The gallery lists a target by the name as typed (#333).

Since #277 a target whose name has no ASCII form is saved with the name
folded in OBJECT (Cyrillic 'M31 Andromeda' reads 'M31 ?????????') and kept
as typed in ``OBJUTF8``. The gallery read OBJECT alone, so the frame library
listed and grouped that target as question marks while the capture folder
on disk carried the real name. It now reads the target through
``fitsio.full_object_name``, and the filter through ``fitsio.full_name``
(#332: two Greek slots otherwise list, and group, as one 'H?').

The disk index of header metadata outlives a restart, so a frame the old
reader indexed would have gone on listing its question marks until the file
changed; the index's stamp now carries the reader's version.

The names are escapes so this file stays ASCII: ``_ANDROMEDA`` is 'M31
Andromeda' with the name in Cyrillic.

Every mutation named below was run in a private scratch copy of ``server/``
(issue #254), never in the shared tree; the failure each produced is
recorded verbatim on the test that caught it.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck import gallery, gallery_index

_ANDROMEDA = "M31 \u0410\u043d\u0434\u0440\u043e\u043c\u0435\u0434\u0430"
_ALPHA = "H\u03b1"


async def _saved(hub, target: str) -> Path:
    await hub.capture(0.2, 100, 30, 1, save=True, target=target)
    return Path(hub.last_frame.saved_path)


def _row_for(rows: list[dict], path: Path, root: Path) -> dict:
    rel = path.relative_to(root).as_posix()
    (row,) = [r for r in rows if r["path"] == rel]
    return row


async def test_a_cyrillic_target_is_listed_as_typed(sim_hub, tmp_path,
                                                     monkeypatch):
    """A frame saved on the sim with a Cyrillic target, through a wheel slot
    named in Greek: the gallery row carries both names as typed.

    Mutation 'read OBJECT' (``meta["target"] = str(hdr.get("OBJECT", "") or
    "").strip()`` restored in ``_read_header_meta``) went red here and on
    the index case, test_gallery and test_gallery_performance green:

        >       assert (row["target"], row["filter"]) == (_ANDROMEDA, _ALPHA), row
        E       AssertionError: {'bin_x': 1, 'bin_y': 1, 'bytes': 2226240, 'exposure_s': 0.2, ...}
        E       assert ('M31 ?????????', 'H\\u03b1') == ('M31 \\u0410\\u043d\\u0434\\u0440\\u043e\\u043c\\u0435\\u0434\\u0430', 'H\\u03b1')

    Mutation 'read FILTER' (``meta["filter"] = str(hdr.get("FILTER", "") or
    "").strip()`` restored) went red here alone:

        E       assert ('M31 \\u0410\\u043d\\u0434\\u0440\\u043e\\u043c\\u0435\\u0434\\u0430', 'H?') == ('M31 \\u0410\\u043d\\u0434\\u0440\\u043e\\u043c\\u0435\\u0434\\u0430', 'H\\u03b1')

    Mutations 'no FILTUTF8' and 'one card for all' in fitsio.py went red
    here with the same 'H?'."""
    fw = sim_hub.devices["filterwheel"]
    monkeypatch.setattr(fw, "filter_names", [_ALPHA] * len(fw.filter_names))
    path = await _saved(sim_hub, _ANDROMEDA)
    gallery.clear_meta_cache()
    rows, _truncated = gallery.scan(tmp_path)
    row = _row_for(rows, path, tmp_path)
    assert (row["target"], row["filter"]) == (_ANDROMEDA, _ALPHA), row


async def test_an_index_row_the_old_reader_wrote_is_read_again(
        sim_hub, tmp_path):
    """The metadata index is keyed on the file's stamp, and the file does not
    change when the reader does. A row the pre-#333 reader wrote (target
    'M31 ?????????', under the bare file signature) must be read again, not
    served for the life of the file.

    Mutation 'the stamp is the bare signature' (``stamp =
    signature(st)`` restored in ``_meta_for``) went red here alone,
    test_gallery and test_gallery_performance green. The row served is the
    stale one, its binning None as this test wrote it:

        >       assert row["target"] == _ANDROMEDA, row
        E       AssertionError: {'bin_x': None, 'bin_y': None, 'bytes': 2226240, 'exposure_s': 0.2, ...}
        E       assert 'M31 ?????????' == 'M31 \\u0410\\u043d\\u0434\\u0440\\u043e\\u043c\\u0435\\u0434\\u0430'
    """
    path = await _saved(sim_hub, _ANDROMEDA)
    rel = path.relative_to(tmp_path).as_posix()
    gallery.clear_meta_cache()
    gallery.scan(tmp_path)                 # creates the index, as a boot would
    old = {"target": "M31 ?????????", "filter": "", "frame_type": "Light",
           "exposure_s": 0.2, "ts": None, "width": None, "height": None,
           "bin_x": None, "bin_y": None, "_header_ok": True}
    db = tmp_path / gallery_index.DIRECTORY / "metadata-v1.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?, ?)",
                     (rel, gallery_index.signature(path.stat()),
                      json.dumps(old)))
    conn.close()
    gallery.clear_meta_cache()              # a restart: the disk index only
    rows, _truncated = gallery.scan(tmp_path)
    row = _row_for(rows, path, tmp_path)
    assert row["target"] == _ANDROMEDA, row


async def test_an_ascii_target_lists_exactly_as_before(sim_hub, tmp_path):
    """CONTROL: an ASCII target and the sim's own ASCII slot list as the
    OBJECT and FILTER cards say, which is what they always listed.

    Mutation 'OBJUTF8 always' in fitsio.py (``if True:`` in place of ``if
    object_text != str(target):``) left this file green, as it should: the
    card then decodes to the same ASCII name. Mutation 'no fallback' (the
    decoder returning ``""`` when there is no as-typed card) went red here:

        >       assert (row["target"], row["filter"]) == ("Veil east", "L"), row
        E       AssertionError: {'bin_x': 1, 'bin_y': 1, 'bytes': 2226240, 'exposure_s': 0.2, ...}
        E       assert ('Veil east', '') == ('Veil east', 'L')

    (the target survives it only because a row with no target falls back to
    its folder, which is the target's name.)"""
    sim_hub.devices["filterwheel"].rig.filter_slot = 0
    path = await _saved(sim_hub, "Veil east")
    gallery.clear_meta_cache()
    rows, _truncated = gallery.scan(tmp_path)
    row = _row_for(rows, path, tmp_path)
    assert (row["target"], row["filter"]) == ("Veil east", "L"), row
