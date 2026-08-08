"""Precomputed constellation lookup for every object in CATALOG.

The lookup itself (constellations.py) is a dict read from a flat file — no
astropy at request time. The thing worth testing is upstream of that: did
build_constellations.py hand astropy a properly-framed SkyCoord, so the J2000
catalog positions actually get precessed to the B1875 frame the IAU boundaries
are defined for? Get that wrong and most objects still land in the right
constellation (boundaries are usually nowhere near the object), which is
exactly why a boundary case is the one test that can actually catch it.
"""
from __future__ import annotations

import time

import pytest

from astrodeck.catalog.constellations import BY_ID, constellation_for
from astrodeck.catalog.objects import CATALOG

# id -> constellation, transcribed by hand from well-known facts, not derived
# from this table — the whole point is an assertion this table could fail.
SPOT_CHECKS = [
    ("M31", "Andromeda"),          # Andromeda Galaxy
    ("NGC 6543", "Draco"),         # Cat's Eye Nebula
    ("M42", "Orion"),              # Orion Nebula
    ("M13", "Hercules"),           # Hercules Cluster
    ("ESO056-115", "Dorado"),      # Large Magellanic Cloud
]


@pytest.mark.parametrize("obj_id,expected", SPOT_CHECKS)
def test_spot_check_known_objects(obj_id, expected):
    assert constellation_for(obj_id) == expected


def test_boundary_object_needs_correct_epoch_precession():
    """IC 1 (RA 0.140847h, Dec +27.717667deg J2000) resolves to Pegasus once
    precessed from J2000 to the B1875 frame get_constellation's boundaries
    use — but resolves to Andromeda if the J2000 numbers are matched straight
    against those boundaries with NO precession (i.e. treated as if they were
    already B1875). Measured directly (see build_constellations.py's
    docstring): skipping the precession step flips 1,127 of the catalog's
    13,369 OpenNGC objects, and this is one of them — the cheapest one to
    pin, since it sits right at 0h and needs no special RA-wrap handling to
    test.
    """
    assert constellation_for("IC 1") == "Pegasus"


def test_every_catalog_object_has_a_constellation():
    """All 13,370 rows, not a sample — a gap here means a describe() call for
    that one object silently drops its "in X" clause."""
    missing = [o.id for o in CATALOG if constellation_for(o.id) is None]
    assert missing == []


def test_constellation_names_are_the_88_iau_names_with_no_stray_whitespace():
    """astropy's own constellation-name table has a known quirk: it returns
    "Crux " (trailing space) for the Southern Cross. build_constellations.py
    strips every name before writing the sidecar specifically to avoid
    shipping that. If this test ever fails on a fresh regen, the strip was
    dropped."""
    names = set(BY_ID.values())
    assert len(names) == 88, f"expected all 88 IAU constellations, got {len(names)}"
    stray = [n for n in names if n != n.strip()]
    assert stray == []
    assert "Crux" in names


def test_lookup_is_actually_precomputed_not_astropy_per_call():
    """A dict-lookup timing guard. Measured directly: calling astropy's
    get_constellation once per object over this catalog takes ~30s (a
    2,000-row sample timed at ~4.6s, linearly extrapolated). 13,370 dict
    lookups finishing in well under a second is strong evidence this is
    reading a precomputed table, not resolving positions live."""
    ids = [o.id for o in CATALOG]
    t0 = time.perf_counter()
    for _ in range(3):
        for i in ids:
            constellation_for(i)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, (
        f"{len(ids) * 3} lookups took {elapsed:.2f}s — that's astropy-per-call "
        f"territory (~30s expected), not a dict read")
