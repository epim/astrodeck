"""Saved-locations LocationStore unit tests (spec §4/§6): round-trip, atomic
write + .bak recovery, case-insensitive name-collision, library-full, rename
collision vs OTHER ids, unknown-id delete. Pure store — no TestClient."""
from __future__ import annotations

import pytest

from astrodeck.locations import (LocationLibraryFull, LocationNameCollision,
                                 LocationStore, MAX_LOCATIONS)


def _store(tmp_path):
    return LocationStore(path=tmp_path / "locations.json")


def test_roundtrip_persists_to_disk(tmp_path):
    s = _store(tmp_path)
    loc = s.create("Backyard", 40.0, -74.0, 12.0, 15.0)
    assert loc.id and loc.name == "Backyard" and loc.horizon_min_deg == 15.0
    # a fresh store over the same file reads it back
    rows = _store(tmp_path).list()
    assert len(rows) == 1
    assert rows[0].id == loc.id and rows[0].latitude == 40.0
    assert rows[0].horizon_min_deg == 15.0


def test_horizon_optional(tmp_path):
    s = _store(tmp_path)
    loc = s.create("NoHorizon", 10.0, 20.0, 0.0)
    assert loc.horizon_min_deg is None


def test_name_empty_after_trim_rejected(tmp_path):
    """Spec §4: a name must be non-empty after trimming — '' and '   ' both
    raise (pydantic ValidationError from the field validator) and nothing is
    persisted."""
    from pydantic import ValidationError
    s = _store(tmp_path)
    with pytest.raises(ValidationError):
        s.create("", 1.0, 2.0, 0.0)
    with pytest.raises(ValidationError):
        s.create("   ", 1.0, 2.0, 0.0)
    assert s.list() == []
    assert _store(tmp_path).list() == []   # nothing hit the disk either


def test_atomic_write_keeps_bak_and_recovers(tmp_path):
    p = tmp_path / "locations.json"
    s = LocationStore(path=p)
    s.create("A", 1.0, 2.0, 0.0)
    s.create("B", 3.0, 4.0, 0.0)   # second write copies the old primary -> .bak
    assert p.with_suffix(".json.bak").exists()
    # corrupt the primary; a fresh store recovers from .bak (the post-A state)
    p.write_text("{ not valid json", encoding="utf-8")
    rows = LocationStore(path=p).list()
    assert any(r.name == "A" for r in rows)


def test_name_collision_case_insensitive_trimmed(tmp_path):
    s = _store(tmp_path)
    a = s.create("Home", 1.0, 2.0, 0.0)
    with pytest.raises(LocationNameCollision) as ei:
        s.create("  home  ", 3.0, 4.0, 0.0)
    assert ei.value.existing_id == a.id


def test_library_full(tmp_path):
    s = _store(tmp_path)
    for i in range(MAX_LOCATIONS):
        s.create(f"L{i}", 1.0, 2.0, 0.0)
    with pytest.raises(LocationLibraryFull):
        s.create("overflow", 1.0, 2.0, 0.0)


def test_update_rename_collision_vs_other_id(tmp_path):
    s = _store(tmp_path)
    a = s.create("A", 1.0, 2.0, 0.0)
    b = s.create("B", 3.0, 4.0, 0.0)
    with pytest.raises(LocationNameCollision) as ei:
        s.update(b.id, "a", 3.0, 4.0, 0.0)   # rename B->A collides with A
    assert ei.value.existing_id == a.id
    # renaming to its OWN (unchanged) name is allowed
    out = s.update(b.id, "B", 9.0, 9.0, 0.0)
    assert out.latitude == 9.0


@pytest.mark.parametrize("method_name, args", [
    pytest.param("update", ("nope", "X", 1.0, 2.0, 0.0), id="update-unknown-id"),
    pytest.param("delete", ("nope",), id="delete-unknown-id"),
])
def test_unknown_id_raises_keyerror(tmp_path, method_name, args):
    s = _store(tmp_path)
    with pytest.raises(KeyError):
        getattr(s, method_name)(*args)
