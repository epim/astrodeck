# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1008: one saved location with an out-of-rule ``horizon_points`` no longer
makes ``locations.json`` read as empty, and the next save no longer overwrites
the library.

``LocationStore._parse`` built ``[SavedLocation(**r) for r in rows]`` inside one
try and returned ``None`` on the first ValidationError. ``SavedLocation``
validates ``horizon_points`` with ``normalize_horizon_points``, so a single row
with an altitude of 999, a NaN or more than 180 points failed the whole list;
``_load`` then fell back to the ``.bak`` and otherwise held an EMPTY library, and
the next create or update wrote that empty library over the file. This is the
locations-file counterpart of #982 (the same stored value, a different file).

Every case is graded on BEHAVIOUR: what the store holds after the load, what the
file holds after the next write, and what was said. Bad shapes are written to a
real locations.json (a NaN as the bare ``NaN`` literal an old build or a hand
edit leaves), never built in memory. Coordinates and names here are invented,
and the tests assert that no warning echoes any of them.

Repo convention (mirrors tests/test_982_stored_horizon_repaired_on_load.py):
in-process fakes via monkeypatch + TestClient, NO unittest.mock, an isolated
LocationStore per test.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.config import ConfigStore
from astrodeck.locations import (MAX_HORIZON_POINTS, LocationStore,
                                 normalize_horizon_points,
                                 sanitize_horizon_points)

NAN = float("nan")
INF = float("inf")
#: An altitude and an azimuth no horizon can have, and numbers that appear in no
#: rule text, so a line that echoes a stored VALUE is caught by a substring test.
LEAK_ALT = 987.25
LEAK_AZ = 731.5
#: Invented coordinates and names. They stand for the real site values, which
#: must never reach a log line, so every warning is scanned for each of these.
_GOOD = dict(id="g1", name="Alpha Field", latitude=11.1111, longitude=22.2222)
_BAD = dict(id="b1", name="Beta Ridge", latitude=33.3333, longitude=44.4444)
_NEEDLES = ("11.1111", "22.2222", "33.3333", "44.4444", "Alpha Field",
            "Beta Ridge", "987", "731", "Zulu Hollow", "999.125")


# --------------------------------------------------------------------- harness

def _row(site, **extra):
    return {**site, "elevation_m": 5.0, "created_ts": 1.0, "updated_ts": 1.0,
            **extra}


def _write(path, rows):
    path.write_text(json.dumps({"locations": rows}), encoding="utf-8")


def _said(bus_lines):
    """Every warning the config source logged, in order."""
    return [m for (lvl, m, src) in bus_lines
            if src == "config" and lvl == "warning"]


def _assert_no_value_said(lines):
    for line in lines:
        for needle in _NEEDLES:
            assert needle not in line, "a warning echoed a stored value"


def _by_id(store):
    return {r.id: r for r in store.list()}


class _FakeProvider:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


#: (stored horizon_points, what the bad site must load with, a phrase of the rule
#: said, how many times it was broken). Always shapes the issue names (an
#: altitude of 999, a NaN, more than 180 points) plus the other ways the rule
#: refuses, so no case rests on one rule alone.
_BAD_SHAPES = [
    pytest.param([[10.0, LEAK_ALT]], None, "altitude must be", 1,
                 id="altitude-out-of-range-only-point"),
    pytest.param([[0.0, 10.0], [90.0, NAN], [180.0, 40.0]],
                 [[0.0, 10.0], [180.0, 40.0]], "must be finite", 1,
                 id="nan-altitude-beside-good-points"),
    pytest.param([[NAN, 10.0], [90.0, INF], [180.0, 40.0]],
                 [[180.0, 40.0]], "must be finite", 2, id="two-non-finite"),
    pytest.param([[LEAK_AZ, 10.0], [180.0, 40.0]], [[180.0, 40.0]],
                 "azimuth must be", 1, id="azimuth-out-of-range"),
    pytest.param([[i * 1.8, 5.0] for i in range(200)],
                 [[i * 2.0, 5.0] for i in range(MAX_HORIZON_POINTS)],
                 f"at most {MAX_HORIZON_POINTS} horizon points", 1,
                 id="over-the-cap"),
    pytest.param("oops", None, "must be a list", 1, id="not-a-list"),
    pytest.param([[10 ** 400, 5.0], [180.0, 40.0]], [[180.0, 40.0]],
                 "must be numbers", 1, id="integer-too-large-for-a-float"),
]


# ============================================= the store loads and repairs

@pytest.mark.parametrize("stored, kept, rule, count", _BAD_SHAPES)
def test_a_good_row_and_a_bad_row_both_load_and_the_bad_site_survives(
        tmp_path, bus_lines, stored, kept, rule, count):
    """The issue's test: a good row and a bad row. The good row loads unchanged,
    the bad row keeps its site (id, name, coordinates), its horizon is repaired
    to something the rule takes, and ONE warning names the rule and the count."""
    path = tmp_path / "locations.json"
    _write(path, [_row(_GOOD), _row(_BAD, horizon_points=stored)])

    store = LocationStore(path=path)
    rows = _by_id(store)

    assert list(rows) == ["g1", "b1"], "the library lost a row"
    good, bad = rows["g1"], rows["b1"]
    assert (good.name, good.latitude, good.longitude, good.elevation_m) == (
        _GOOD["name"], _GOOD["latitude"], _GOOD["longitude"], 5.0)
    assert good.horizon_points is None
    assert (bad.name, bad.latitude, bad.longitude, bad.elevation_m) == (
        _BAD["name"], _BAD["latitude"], _BAD["longitude"], 5.0)
    assert bad.horizon_points == kept
    # What it kept is a horizon the API doors would take.
    if kept is not None:
        normalize_horizon_points(bad.horizon_points)
    said = _said(bus_lines)
    assert len(said) == 1, said
    assert "1 saved location(s)" in said[0] and "repaired on load" in said[0]
    assert rule in said[0], said
    assert f"(x{count})" in said[0], said
    _assert_no_value_said(said)


def test_an_over_cap_horizon_is_thinned_without_lowering_the_line(
        tmp_path, bus_lines):
    """More than 180 points is repaired by the shared thinning, which never lets
    the line fall: the one tall point survives as a tall stretch, not a hole."""
    pts = [[i * 1.8, 5.0] for i in range(200)]
    pts[100] = [pts[100][0], 61.0]
    path = tmp_path / "locations.json"
    _write(path, [_row(_BAD, horizon_points=pts)])

    bad = LocationStore(path=path).list()[0]

    assert len(bad.horizon_points) == MAX_HORIZON_POINTS
    assert max(alt for _az, alt in bad.horizon_points) == 61.0
    normalize_horizon_points(bad.horizon_points)


def test_the_next_save_keeps_every_site(tmp_path, bus_lines):
    """The data loss itself. Before the fix the library read as empty, so this
    create wrote ONE row over a file that held two, and a fresh store found only
    the new site."""
    path = tmp_path / "locations.json"
    _write(path, [_row(_GOOD), _row(_BAD, horizon_points=[[10.0, LEAK_ALT]])])
    store = LocationStore(path=path)

    store.create("Gamma Shore", 55.5, 66.6, 1.0)

    on_disk = json.loads(path.read_text(encoding="utf-8"))["locations"]
    assert [r["id"] for r in on_disk][:2] == ["g1", "b1"]
    assert len(on_disk) == 3
    fresh = _by_id(LocationStore(path=path))
    assert set(fresh) >= {"g1", "b1"} and len(fresh) == 3
    # The repair was written: the next load has nothing left to repair.
    mark = len(bus_lines)
    LocationStore(path=path).list()
    assert _said(bus_lines[mark:]) == []


def test_a_library_of_several_rows_counts_each_kind(tmp_path, bus_lines):
    """Five usable sites (two of them repaired) and one unusable: the library has
    five, and the two counts are said once each."""
    rows = [_row(dict(id=f"s{i}", name=f"Site {i}", latitude=float(i),
                      longitude=float(i) + 1.0)) for i in range(3)]
    rows.append(_row(dict(id="r1", name="Rep One", latitude=7.5, longitude=8.5),
                     horizon_points=[[10.0, LEAK_ALT]]))
    rows.append(_row(dict(id="r2", name="Rep Two", latitude=9.5, longitude=1.5),
                     horizon_points=[[0.0, NAN], [180.0, 30.0]]))
    rows.append(_row(dict(id="u1", name="Zulu Hollow", latitude=999.125,
                          longitude=2.5)))
    path = tmp_path / "locations.json"
    _write(path, rows)

    store = LocationStore(path=path)

    assert [r.id for r in store.list()] == ["s0", "s1", "s2", "r1", "r2"]
    said = _said(bus_lines)
    assert len(said) == 2, said
    assert "2 saved location(s) had horizon_points" in said[0]
    assert "1 saved location(s)" in said[1] and "skipped" in said[1]
    assert "the other 5 loaded" in said[1]
    _assert_no_value_said(said)


# ============================================ an unusable row is skipped

_UNUSABLE = [
    pytest.param(_row(dict(id="x1", name="Zulu Hollow", latitude=999.125,
                           longitude=5.0)), id="latitude-out-of-range"),
    pytest.param(_row(dict(id="x2", name="   ", latitude=1.0, longitude=2.0)),
                 id="empty-name"),
    pytest.param({"id": "x3", "name": "Zulu Hollow"}, id="missing-coordinates"),
    pytest.param("Zulu Hollow", id="row-is-text"),
    pytest.param(7, id="row-is-a-number"),
    pytest.param(None, id="row-is-null"),
    pytest.param(_row(dict(id="x4", name="Zulu Hollow", latitude=999.125,
                           longitude=5.0), horizon_points=[[10.0, LEAK_ALT]]),
                 id="bad-horizon-AND-bad-latitude"),
]


@pytest.mark.parametrize("unusable", _UNUSABLE)
def test_an_unusable_row_is_skipped_and_counted_not_the_library(
        tmp_path, bus_lines, unusable):
    """A row nothing can be kept of is skipped with one counted warning. The
    good row still loads, and no value from the bad row is said."""
    path = tmp_path / "locations.json"
    _write(path, [_row(_GOOD), unusable])

    store = LocationStore(path=path)

    assert [r.id for r in store.list()] == ["g1"]
    said = _said(bus_lines)
    assert len(said) == 1, said
    assert "1 saved location(s)" in said[0] and "unusable and skipped" in said[0]
    assert "the other 1 loaded" in said[0]
    _assert_no_value_said(said)


def test_a_skipped_row_is_not_dropped_from_the_file_without_saying_so(
        tmp_path, bus_lines):
    """Loading changes nothing on disk, so the unusable row is still in the file.
    The first write after it DOES drop the row: that is said, once, and the row
    is in the .bak the same write made."""
    path = tmp_path / "locations.json"
    unusable = _row(dict(id="x1", name="Zulu Hollow", latitude=999.125,
                         longitude=5.0))
    _write(path, [_row(_GOOD), unusable])
    store = LocationStore(path=path)
    assert [r.id for r in store.list()] == ["g1"]      # the load happens here
    assert [r["id"] for r in json.loads(
        path.read_text(encoding="utf-8"))["locations"]] == ["g1", "x1"]
    mark = len(bus_lines)

    store.create("Delta Bay", 5.5, 6.5, 1.0)

    said = _said(bus_lines[mark:])
    assert len(said) == 1, said
    assert "rewritten without the 1 unusable saved location(s)" in said[0]
    # #1034: the row is kept in the sidecar, which no write rewrites, and not
    # only in the .bak the next write overwrites.
    assert "locations.json.rejected" in said[0]
    _assert_no_value_said(said)
    bak = json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8"))
    assert [r["id"] for r in bak["locations"]] == ["g1", "x1"]
    assert [r["id"] for r in json.loads(path.with_suffix(".json.rejected")
            .read_text(encoding="utf-8"))["rejected"]] == ["x1"]
    on_disk = json.loads(path.read_text(encoding="utf-8"))["locations"]
    assert [r["id"] for r in on_disk][0] == "g1" and len(on_disk) == 2
    # Said once: the change after that rewrites a file that no longer has it.
    mark = len(bus_lines)
    store.create("Echo Cove", 6.5, 7.5, 1.0)
    assert _said(bus_lines[mark:]) == []


def test_a_write_after_a_repair_alone_says_nothing_was_dropped(
        tmp_path, bus_lines):
    """A repaired row is still a row: its site is in the rewritten file, so the
    "rewritten without" line is for dropped rows only."""
    path = tmp_path / "locations.json"
    _write(path, [_row(_GOOD), _row(_BAD, horizon_points=[[10.0, LEAK_ALT]])])
    store = LocationStore(path=path)
    assert len(store.list()) == 2                      # the load happens here
    mark = len(bus_lines)

    store.create("Delta Bay", 5.5, 6.5, 1.0)

    assert _said(bus_lines[mark:]) == []


# ============== the backup may hold a usable copy of a row the primary spoilt

def _write_bak(path, rows):
    path.with_suffix(".json.bak").write_text(
        json.dumps({"locations": rows}), encoding="utf-8")


def _ids_on_disk(path):
    return [r["id"] for r in json.loads(
        path.read_text(encoding="utf-8"))["locations"]]


#: A copy of the "b1" site that cannot be loaded, three ways. The .bak holds the
#: usable "b1" in every case below.
_BROKEN_B1 = [
    pytest.param({"id": "b1", "name": "Beta Ridge"}, id="missing-coordinates"),
    pytest.param(_row(dict(_BAD, latitude=999.125)), id="latitude-out-of-range"),
    pytest.param(_row(dict(_BAD, name="   ")), id="empty-name"),
]


@pytest.mark.parametrize("broken", _BROKEN_B1)
def test_an_unusable_row_is_taken_from_the_backup_and_survives_a_write(
        tmp_path, bus_lines, broken):
    """The regression the review found. A row the primary cannot load, whose
    backup copy is usable, used to be skipped; the first write then copied the
    primary over the .bak and the usable copy was gone from both files. The
    backup copy is used, and the site is still there after a write and a
    fresh load."""
    path = tmp_path / "locations.json"
    _write(path, [_row(_GOOD), broken])
    _write_bak(path, [_row(_GOOD), _row(_BAD)])

    store = LocationStore(path=path)
    rows = _by_id(store)

    assert list(rows) == ["g1", "b1"]
    assert (rows["b1"].name, rows["b1"].latitude, rows["b1"].longitude) == (
        _BAD["name"], _BAD["latitude"], _BAD["longitude"])
    said = _said(bus_lines)
    assert len(said) == 1, said
    assert "1 saved location(s)" in said[0] and "unusable" in said[0]
    assert "taken from locations.json.bak" in said[0], said
    _assert_no_value_said(said)
    mark = len(bus_lines)

    store.create("Delta Bay", 5.5, 6.5, 1.0)

    assert _said(bus_lines[mark:]) == [], "nothing was dropped, so nothing said"
    fresh = _by_id(LocationStore(path=path))
    assert list(fresh)[:2] == ["g1", "b1"] and len(fresh) == 3
    assert (fresh["b1"].latitude, fresh["b1"].longitude) == (
        _BAD["latitude"], _BAD["longitude"])
    mark = len(bus_lines)
    LocationStore(path=path).list()
    assert _said(bus_lines[mark:]) == []


def test_a_primary_with_no_usable_row_falls_back_to_the_backup(
        tmp_path, bus_lines):
    """The other half of the review's finding: a primary whose rows are ALL
    unusable used to restore the backup. An empty library from rows that were
    there is a failed read, not a library, so the backup is tried and its site
    survives a write."""
    path = tmp_path / "locations.json"
    _write(path, [{"id": "x1", "name": "Zulu Hollow"}])
    _write_bak(path, [_row(_GOOD)])

    store = LocationStore(path=path)

    assert [r.id for r in store.list()] == ["g1"]
    said = _said(bus_lines)
    assert any("restored from backup" in m for m in said), said
    assert not any("reset to empty" in m for m in said), said
    _assert_no_value_said(said)
    store.create("Delta Bay", 5.5, 6.5, 1.0)
    fresh = LocationStore(path=path).list()
    assert fresh[0].id == "g1" and len(fresh) == 2


def test_a_primary_with_no_usable_row_and_no_backup_is_empty_and_says_so(
        tmp_path, bus_lines):
    """Nothing to fall back to: the library is empty, the file still holds the
    unusable row after the load, and the first write says it dropped it and
    that the row is in the .bak that write made."""
    path = tmp_path / "locations.json"
    _write(path, [{"id": "x1", "name": "Zulu Hollow"}])
    store = LocationStore(path=path)

    assert store.list() == []
    said = _said(bus_lines)
    assert any("unusable and skipped" in m for m in said), said
    _assert_no_value_said(said)
    assert _ids_on_disk(path) == ["x1"]
    mark = len(bus_lines)

    store.create("Delta Bay", 5.5, 6.5, 1.0)

    said = _said(bus_lines[mark:])
    assert len(said) == 1 and "rewritten without the 1 unusable" in said[0], said
    bak = json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8"))
    assert [r["id"] for r in bak["locations"]] == ["x1"]


def test_an_explicit_empty_library_is_empty_and_leaves_the_backup_alone(
        tmp_path, bus_lines):
    """``{"locations": []}`` is a library the operator emptied, not a failed
    read: it loads empty without trying the .bak, and says nothing."""
    path = tmp_path / "locations.json"
    _write(path, [])
    _write_bak(path, [_row(_GOOD)])

    assert LocationStore(path=path).list() == []
    assert _said(bus_lines) == []
    assert _ids_on_disk(path) == []
    assert [r["id"] for r in json.loads(path.with_suffix(".json.bak").read_text(
        encoding="utf-8"))["locations"]] == ["g1"]


def test_a_backup_with_no_usable_row_does_not_overwrite_the_primary(
        tmp_path, bus_lines):
    """Both files hold only unusable rows. The backup is no recovery, so the
    primary is not rewritten as an empty library behind the operator's back."""
    path = tmp_path / "locations.json"
    _write(path, [{"id": "x1", "name": "Zulu Hollow"}])
    _write_bak(path, [{"id": "y1", "name": "Zulu Hollow"}])

    assert LocationStore(path=path).list() == []

    assert _ids_on_disk(path) == ["x1"], "the load rewrote the primary"
    assert not any("restored from backup" in m for m in _said(bus_lines))


def test_a_backup_copy_is_not_used_for_an_id_that_already_loaded(
        tmp_path, bus_lines):
    """Two rows with one id, one usable and one not: the usable one stands and
    the backup does not add a second row with that id."""
    path = tmp_path / "locations.json"
    _write(path, [_row(_GOOD), {"id": "g1", "name": "Zulu Hollow"}])
    _write_bak(path, [_row(_GOOD)])

    store = LocationStore(path=path)

    assert [r.id for r in store.list()] == ["g1"]
    said = _said(bus_lines)
    assert len(said) == 1 and "unusable and skipped" in said[0], said


def test_one_backup_copy_stands_in_for_one_row_only(tmp_path, bus_lines):
    """Two unusable rows share an id the .bak holds once: one is replaced, the
    other is skipped, and the library has the site once."""
    path = tmp_path / "locations.json"
    _write(path, [{"id": "b1", "name": "Zulu Hollow"},
                  {"id": "b1", "name": "Zulu Hollow", "latitude": 1.0}])
    _write_bak(path, [_row(_GOOD), _row(_BAD)])

    store = LocationStore(path=path)

    assert [r.id for r in store.list()] == ["b1"]
    said = _said(bus_lines)
    assert any("taken from" in m for m in said), said
    assert any("1 saved location(s)" in m and "unusable and skipped" in m
               for m in said), said


# ============================================== the repair is only in memory

def test_a_repair_is_said_at_every_load_until_a_change_writes_it(
        tmp_path, bus_lines):
    """Loading changes nothing on disk, so the line is repeated at every load
    until a change to the library writes the repair, and it says so: an
    operator who sees it twice must not read that as a failed repair."""
    path = tmp_path / "locations.json"
    _write(path, [_row(_GOOD), _row(_BAD, horizon_points=[[10.0, LEAK_ALT]])])
    store = LocationStore(path=path)
    store.list()
    said = _said(bus_lines)
    assert len(said) == 1, said
    assert "rewritten by the next change to the library" in said[0], said
    assert "said again" in said[0], said
    mark = len(bus_lines)

    LocationStore(path=path).list()                    # nothing written yet
    assert len(_said(bus_lines[mark:])) == 1

    store.create("Delta Bay", 5.5, 6.5, 1.0)           # the change writes it
    mark = len(bus_lines)
    LocationStore(path=path).list()
    assert _said(bus_lines[mark:]) == []


# =========================================== a good library is not touched

def test_a_library_that_passes_the_rule_loads_silently(tmp_path, bus_lines):
    """The repair only acts on what the rule refuses: valid horizons (including
    the explicit empty clear and an absent field) load as they always did and
    nothing is said, on load or on the next write."""
    path = tmp_path / "locations.json"
    _write(path, [
        _row(_GOOD),
        _row(_BAD, horizon_points=[[270.0, 30.0], [90.0, 8.0]]),
        _row(dict(id="c1", name="Clear Sky", latitude=1.5, longitude=2.5),
             horizon_points=[]),
    ])

    store = LocationStore(path=path)
    rows = _by_id(store)

    assert list(rows) == ["g1", "b1", "c1"]
    assert rows["b1"].horizon_points == [[90.0, 8.0], [270.0, 30.0]]
    assert rows["c1"].horizon_points == []
    store.create("Delta Bay", 5.5, 6.5, 1.0)
    assert _said(bus_lines) == []


# ======================================================= the other doors

def test_a_backup_with_a_bad_row_restores_every_site(tmp_path, bus_lines):
    """The .bak door goes through the same parse: a corrupt primary no longer
    turns a backup holding one bad row into an empty library."""
    path = tmp_path / "locations.json"
    path.with_suffix(".json.bak").write_text(json.dumps({"locations": [
        _row(_GOOD), _row(_BAD, horizon_points=[[10.0, LEAK_ALT]])]}),
        encoding="utf-8")
    path.write_text("{ not valid json", encoding="utf-8")

    store = LocationStore(path=path)

    rows = _by_id(store)
    assert list(rows) == ["g1", "b1"]
    assert rows["b1"].horizon_points is None
    said = _said(bus_lines)
    assert any("restored from backup" in m for m in said), said
    assert any("repaired on load" in m for m in said), said
    assert not any("reset to empty" in m for m in said), said
    _assert_no_value_said(said)
    # The primary was rewritten from the repaired rows.
    assert [r.id for r in LocationStore(path=path).list()] == ["g1", "b1"]


def test_a_restore_that_drops_an_unusable_backup_row_says_so_at_the_restore(
        tmp_path, bus_lines):
    """The restore writes the primary, so a row it leaves out is dropped there:
    the line is said at the restore (while the .bak still holds the row, which
    is what it claims), not at some later change after the .bak was copied over."""
    path = tmp_path / "locations.json"
    _write_bak(path, [_row(_GOOD), {"id": "x1", "name": "Zulu Hollow"}])
    path.write_text("{ not valid json", encoding="utf-8")

    store = LocationStore(path=path)

    assert [r.id for r in store.list()] == ["g1"]
    said = _said(bus_lines)
    assert any("rewritten without the 1 unusable" in m for m in said), said
    _assert_no_value_said(said)
    bak = json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8"))
    assert [r["id"] for r in bak["locations"]] == ["g1", "x1"]
    mark = len(bus_lines)

    store.create("Delta Bay", 5.5, 6.5, 1.0)

    assert _said(bus_lines[mark:]) == []


def test_a_wrong_shaped_file_still_falls_back_to_the_backup(tmp_path, bus_lines):
    """Whole-file shape errors are unchanged: ``locations`` that is not a list
    is no row to skip, so the backup is tried and an empty library only follows
    when it fails too."""
    path = tmp_path / "locations.json"
    path.with_suffix(".json.bak").write_text(
        json.dumps({"locations": [_row(_GOOD)]}), encoding="utf-8")
    path.write_text(json.dumps({"locations": "oops"}), encoding="utf-8")

    assert [r.id for r in LocationStore(path=path).list()] == ["g1"]

    path.with_suffix(".json.bak").unlink()
    path.write_text(json.dumps({"locations": "oops"}), encoding="utf-8")
    assert LocationStore(path=path).list() == []
    assert any("reset to empty (invalid shape)" in m for m in _said(bus_lines))


def test_the_locations_route_lists_every_site_over_a_bad_row(
        tmp_path, monkeypatch):
    """The symptom the operator met: ``GET /api/locations`` answered an empty
    list, so every saved site vanished from the UI."""
    path = tmp_path / "locations.json"
    _write(path, [_row(_GOOD), _row(_BAD, horizon_points=[[10.0, LEAK_ALT]])])
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    import astrodeck.locations as loc_mod
    cfg = ConfigStore(path=tmp_path / "astrodeck.json")
    locs = LocationStore(path=path)
    monkeypatch.setattr(config_mod, "config_store", cfg)
    monkeypatch.setattr(hub_mod, "config_store", cfg)
    monkeypatch.setattr(app_module, "config_store", cfg)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    monkeypatch.setattr(loc_mod, "location_store", locs)
    monkeypatch.setattr(app_module, "location_store", locs)
    reset_active_provider()
    set_active_provider(_FakeProvider(principal_for_role("admin")))

    with TestClient(app_module.create_app()) as c:
        r = c.get("/api/locations")

    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert [row["id"] for row in body] == ["g1", "b1"]
    assert body[1]["name"] == _BAD["name"]
    assert body[1]["horizon_points"] is None


# ============================================ the shared rule's contract

def test_the_horizon_rule_refuses_an_unrepresentable_integer_with_valueerror():
    """``normalize_horizon_points`` promises ValueError and nothing else. An
    integer too large for a float raised OverflowError out of it, which escaped
    the pydantic validator as a raw error and, at the load door, out of
    ``sanitize_horizon_points`` itself."""
    with pytest.raises(ValueError, match="must be numbers"):
        normalize_horizon_points([[10 ** 400, 5.0]])
    points, problems = sanitize_horizon_points([[10 ** 400, 5.0], [90.0, 20.0]])
    assert points == [[90.0, 20.0]]
    assert problems == {"horizon point values must be numbers": 1}
