"""No target is unreachable (2026-08-07).

An operator with a freshly-working polar alignment could not start a session on
the Cat's Eye Nebula. The catalogue was 64 hand-written rows, NGC 6543 was not
among them, every spelling returned zero, the empty result carried no
explanation, and no coordinate entry existed anywhere in the app — so an object
outside those 64 was unreachable by EVERY path at once.

Three claims are pinned here: the catalogue is real, a position is always a
valid answer, and a miss says so.
"""
from __future__ import annotations

import pytest

from astrodeck.catalog.objects import CATALOG, parse_coordinates, search


# ------------------------------------------------------------ the catalogue

def test_the_catalogue_is_not_a_demo():
    """64 objects is a demo. This is the one assertion that fails loudly if the
    generated data file goes missing from a release — the module degrades to
    the curated core rather than raising, which is right for the server and
    silent for a user typing a name that used to work."""
    assert len(CATALOG) > 5000, (
        f"only {len(CATALOG)} objects — is astrodeck/catalog/data/ngc.tsv "
        f"missing from the package?")


@pytest.mark.parametrize("query", ["cat's eye", "cats eye", "Cat's Eye Nebula",
                                   "NGC 6543", "ngc6543", "NGC6543", "6543"])
def test_the_cats_eye_is_findable_however_it_is_typed(query):
    """The object that started this. Apostrophe optional — nobody reproduces
    punctuation reliably on a phone in the dark, and "cats eye" found nothing
    while "cat's eye" found the nebula."""
    rows = search(query, limit=5).rows
    assert rows, f"{query!r} found nothing"
    assert rows[0]["id"] == "NGC 6543", rows[0]
    assert abs(rows[0]["ra_hours"] - 17.9759) < 0.01
    assert abs(rows[0]["dec_deg"] - 66.6332) < 0.01


def test_a_messier_object_answers_to_its_ngc_number_too():
    """M31 is NGC 224 to anyone reading a chart; the Ring Nebula is NGC 6720.
    The bulk rows carry the cross-reference as an alias."""
    for alias, expect in (("NGC 224", "M31"), ("NGC 6720", "M57")):
        rows = search(alias, limit=3).rows
        assert rows and rows[0]["id"] == expect, (alias, rows[:1])


def test_the_curated_names_survive_the_bulk_import():
    """A curated row keeps its hand-written name: the Ring Nebula must not
    become "NGC 6720" just because the generated file also carries it."""
    rows = search("M57", limit=1).rows
    assert rows[0]["name"] == "Ring Nebula", rows[0]


# ------------------------------------------------------------- coordinates

@pytest.mark.parametrize("text,ra,dec", [
    ("17 58 33 +66 38", 17.9758, 66.633),
    ("17h58m33s +66d38m", 17.9758, 66.633),
    ("17:58:33 +66:37:59", 17.9758, 66.633),
    ("17 58 +66 38", 17.9667, 66.633),
    ("269.64 +66.63", 17.976, 66.63),
    ("5 35 -5 23", 5.5833, -5.383),
])
def test_a_typed_position_is_a_target(text, ra, dec):
    got = parse_coordinates(text)
    assert got is not None, f"{text!r} was not read as a position"
    assert got["ra_hours"] == pytest.approx(ra, abs=1e-3)
    assert got["dec_deg"] == pytest.approx(dec, abs=1e-3)
    assert got["kind"] == "coordinates"


@pytest.mark.parametrize("text", [
    "6543", "M31", "NGC 6543", "M 42",      # designations, not positions
    "25 00 +10 00",                          # RA out of range
    "12 30 +95 00",                          # Dec out of range
    "12 30 66 38",                           # no sign: ambiguous, refuse
    "hello world", "3.5", "",
])
def test_what_is_not_a_position_is_refused(text):
    """A half-parsed position points a telescope somewhere nobody asked for, so
    the parser is strict about structure even while it is permissive about
    separators. A bare designation must NEVER be read as coordinates."""
    assert parse_coordinates(text) is None, f"{text!r} was read as a position"


def test_a_position_reaches_the_search_when_nothing_else_does():
    """The end-to-end claim: type a position for an object the catalogue does
    not carry, and you still get a target row."""
    rows = search("17 58 33 +66 38", limit=3).rows
    assert rows and rows[0]["kind"] == "coordinates"
    assert rows[0]["ra_hours"] == pytest.approx(17.9758, abs=1e-3)


def test_a_catalogue_hit_beats_a_coordinate_reading():
    """Coordinates are the FALLBACK. A query that names an object must return
    the object, never a position parsed out of its digits."""
    rows = search("M42", limit=3).rows
    assert rows[0]["kind"] == "dso" and rows[0]["id"] == "M42"


# -------------------------------------------------------------- the refusal

def test_a_miss_explains_itself():
    """`notes` existed for exactly this and was empty on the miss path, so a
    screen showing nothing could not tell "no such object" from "the search is
    broken"."""
    r = search("zzzznotathing", limit=5)
    assert r.rows == []
    assert r.notes, "an empty result said nothing at all"
    note = r.notes[0]
    assert "zzzznotathing" in note
    assert str(len(CATALOG)) in note, "the note must say how big the catalogue is"
    assert "position" in note.lower(), "the note must offer the way through"


def test_a_hit_carries_no_apology():
    assert search("M31", limit=3).notes == []
