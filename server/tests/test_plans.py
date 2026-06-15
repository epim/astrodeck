"""Server-side plan library: uuid upsert, name-collision detection, export
bytes, import version handling (version_too_new vs invalid), and path-traversal
hardening of the client-controllable id."""
import json
import uuid

import pytest

from astrodeck.plans import (
    LibraryFull,
    MAX_PLANS,
    PLAN_SCHEMA,
    PlanImportError,
    PlanLibrary,
)
from astrodeck.sequence import ExposureStep, SequencePlan, Target


def _lib(tmp_path):
    return PlanLibrary(directory=tmp_path / "plans")


def _plan(name="Tonight"):
    return SequencePlan(name=name, targets=[
        Target(name="M31", ra_hours=0.71, dec_deg=41.27, steps=[
            ExposureStep(filter="L", exposure_s=120, count=10),
        ])])


def test_save_assigns_uuid_and_summarizes(tmp_path):
    lib = _lib(tmp_path)
    row = lib.save(_plan())
    assert row["id"]                      # a fresh uuid was assigned
    assert row["name"] == "Tonight"
    assert row["frames"] == 10
    assert row["targets"] == 1
    assert row["integration_min"] == pytest.approx(20.0)


def test_upsert_by_id_overwrites_in_place(tmp_path):
    lib = _lib(tmp_path)
    row = lib.save(_plan("First"))
    pid = row["id"]
    lib.save(_plan("Renamed"), plan_id=pid)
    # same id, no second file
    assert lib.get(pid).name == "Renamed"
    files = list((tmp_path / "plans").glob("*.json"))
    assert len(files) == 1


def test_name_collision_detection(tmp_path):
    lib = _lib(tmp_path)
    row = lib.save(_plan("Andromeda Night"))
    assert lib.name_exists("Andromeda Night") is True
    assert lib.name_exists("Andromeda Night", exclude_id=row["id"]) is False
    assert lib.name_exists("Different") is False


def test_list_sorted_newest_first(tmp_path):
    lib = _lib(tmp_path)
    a = lib.save(_plan("A"))
    b = lib.save(_plan("B"))
    rows = lib.list()
    ids = [r["id"] for r in rows]
    assert set(ids) == {a["id"], b["id"]}
    # newest mtime first (b saved after a)
    assert rows[0]["mtime"] >= rows[1]["mtime"]


def test_export_bytes_is_valid_envelope(tmp_path):
    lib = _lib(tmp_path)
    row = lib.save(_plan("Export Me"))
    raw = lib.export_bytes(row["id"])
    env = json.loads(raw)
    assert env["schema_version"] == PLAN_SCHEMA
    assert env["name"] == "Export Me"
    assert env["plan"]["targets"][0]["name"] == "M31"


def test_export_missing_raises(tmp_path):
    lib = _lib(tmp_path)
    with pytest.raises(KeyError):
        lib.export_bytes("nope")


def test_import_round_trips_and_rekeys(tmp_path):
    lib = _lib(tmp_path)
    original = lib.save(_plan("Imported"))
    env = json.loads(lib.export_bytes(original["id"]))
    new_row = lib.import_plan(env)
    assert new_row["name"] == "Imported"
    assert new_row["id"] != original["id"]   # import never clobbers by id
    assert lib.get(new_row["id"]).targets[0].name == "M31"


def test_import_version_too_new(tmp_path):
    lib = _lib(tmp_path)
    env = {"schema_version": PLAN_SCHEMA + 1, "name": "Future", "plan": {}}
    with pytest.raises(PlanImportError) as ei:
        lib.import_plan(env)
    assert ei.value.code == "version_too_new"


def test_import_invalid_plan(tmp_path):
    lib = _lib(tmp_path)
    # a step with exposure_s <= 0 fails SequencePlan validation
    env = {"schema_version": PLAN_SCHEMA, "name": "Bad", "plan": {
        "targets": [{"name": "X", "ra_hours": 1, "dec_deg": 1,
                     "steps": [{"exposure_s": -5, "count": 1}]}]}}
    with pytest.raises(PlanImportError) as ei:
        lib.import_plan(env)
    assert ei.value.code == "invalid"


def test_delete_removes_file(tmp_path):
    lib = _lib(tmp_path)
    row = lib.save(_plan())
    lib.delete(row["id"])
    assert not (tmp_path / "plans" / f"{row['id']}.json").exists()


# ---------------------------------------------------------- path-traversal guard

# Decoded forms of the exploit ids — Starlette/the API decode %2F/%5C before the
# library sees them, so these are the literals the library must reject.
TRAVERSAL_IDS = [
    "../../pwned",
    "..\\victim",            # decoded "..%5Cvictim" — the Windows backslash vector
    "../astrodeck",          # would exfiltrate/clobber the AppConfig if unguarded
    "subdir/escape",
    "C:\\Windows\\System32\\evil",   # absolute path
    "/etc/passwd",           # absolute POSIX path
]


@pytest.mark.parametrize("bad_id", TRAVERSAL_IDS)
def test_path_traversal_ids_raise_keyerror(tmp_path, bad_id):
    """A client-controlled id that escapes the plans dir must raise KeyError (the
    API maps KeyError → 404) on every id-keyed operation."""
    lib = _lib(tmp_path)
    with pytest.raises(KeyError):
        lib.get(bad_id)
    with pytest.raises(KeyError):
        lib.export_bytes(bad_id)
    with pytest.raises(KeyError):
        lib.save(_plan(), plan_id=bad_id)
    with pytest.raises(KeyError):
        lib.delete(bad_id)


def test_no_file_written_outside_dir_on_traversal(tmp_path):
    """Saving with a traversal id must not create a file above the plans dir."""
    lib = _lib(tmp_path)
    sentinel = tmp_path / "pwned.json"
    with pytest.raises(KeyError):
        lib.save(_plan(), plan_id="../pwned")
    assert not sentinel.exists()


def test_normal_uuid_id_round_trips(tmp_path):
    """A well-formed uuid id is accepted and round-trips through save/get."""
    lib = _lib(tmp_path)
    pid = str(uuid.uuid4())
    row = lib.save(_plan("Good Plan"), plan_id=pid)
    assert row["id"] == pid
    assert lib.get(pid).name == "Good Plan"
    assert (tmp_path / "plans" / f"{pid}.json").exists()


# ------------------------------------------------------------------- quota cap

def test_save_rejects_new_plan_at_cap(tmp_path, monkeypatch):
    """At the cap, a *new* plan is rejected with LibraryFull; upserting an
    existing one is still allowed."""
    import astrodeck.plans as plans_mod
    monkeypatch.setattr(plans_mod, "MAX_PLANS", 2)
    lib = _lib(tmp_path)
    a = lib.save(_plan("A"))
    lib.save(_plan("B"))
    with pytest.raises(LibraryFull):
        lib.save(_plan("C"))                 # over cap → rejected
    # upsert of an existing id is still fine even at cap
    assert lib.save(_plan("A2"), plan_id=a["id"])["name"] == "A2"
