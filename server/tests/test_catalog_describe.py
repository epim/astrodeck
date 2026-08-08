"""describe() — one true sentence for every catalog object, composed from
whatever fields it actually has. See describe.py's module docstring for the
degrade rules; these tests check the two things that can quietly go wrong:
a type code with no phrase (renders the raw code, or crashes at the worst
possible moment — request time), and a row that degrades all the way to
nothing instead of to a shorter true sentence.
"""
from __future__ import annotations

import pytest

from astrodeck.catalog.describe import TYPE_PHRASE, _type_phrase, describe
from astrodeck.catalog.objects import CATALOG, _TYPE_NAMES


def test_every_type_code_present_in_the_data_has_a_phrase():
    """objects._TYPE_NAMES is the authoritative set of codes CATALOG can ever
    contain (_load_bulk skips anything not in it). TYPE_PHRASE must cover the
    same set — not a superset that happens to include them, the SAME set, so
    a code retired from one side is caught on the other too."""
    assert set(TYPE_PHRASE) == set(_TYPE_NAMES)
    # And every code actually present in the loaded catalog right now:
    codes_in_use = {o.type for o in CATALOG}
    assert codes_in_use <= set(TYPE_PHRASE)


def test_unmapped_type_code_fails_loudly():
    """A fifth OpenNGC type introduced by a future data pull must not render
    as its raw two-letter code ("PN in Draco") — it must fail a test before
    it ever reaches a user."""
    with pytest.raises(KeyError, match="ZZ"):
        _type_phrase("ZZ")


@pytest.mark.parametrize("obj_id,expected", [
    # Has a common name AND a published magnitude.
    ("NGC 6543", "Cat's Eye Nebula — planetary nebula in Draco · mag 9.0"),
    ("M31", "Andromeda Galaxy — galaxy in Andromeda · mag 3.4"),
    # No common name (name == id in the data), has a magnitude.
    ("ESO107-004", "Galaxy in Pavo · mag 12.0"),
    # Has a common name, no published magnitude (objects.MAG_UNKNOWN).
    ("B033", "Horsehead Nebula — dark nebula in Orion"),
    # Neither a common name nor a magnitude — the floor case.
    ("H13", "Open cluster in Ara"),
])
def test_specific_examples(obj_id, expected):
    by_id = {o.id: o for o in CATALOG}
    assert describe(by_id[obj_id]) == expected


def test_describe_never_returns_empty_or_placeholder_for_any_object():
    """Run over the entire 13,370-object catalog, not a sample. Never "",
    never whitespace-only, and never the specific anti-pattern this task
    calls out by name."""
    for obj in CATALOG:
        s = describe(obj)
        assert isinstance(s, str)
        assert s.strip() != "", f"{obj.id} described as empty/whitespace"
        assert "unknown object" not in s.lower(), (
            f"{obj.id} rendered a placeholder instead of a real sentence: {s!r}")
