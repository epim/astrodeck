"""Stage A (#239): stored plans stop pretending they chose the defaults.

Every plan saved before this change carries a concrete value for all twelve
moved fields, almost all of them the model default nobody ever chose. Left
alone they would every one read as a deliberate override, no Settings change
would ever reach an existing plan, and the migration would have done nothing
for the plans that actually exist.
"""
import json

from astrodeck.config import AppConfig
from astrodeck.plans import PlanLibrary, migrate_plan_policy_fields
from astrodeck.sequence.policy import resolve_policy


def _store(tmp_path, plan_fields: dict) -> tuple[PlanLibrary, str]:
    d = tmp_path / "plans"
    d.mkdir()
    pid = "abc123"
    (d / f"{pid}.json").write_text(json.dumps({
        "id": pid, "schema_version": 1, "name": "Old Plan",
        "plan": {"name": "Old Plan", "targets": [], **plan_fields},
    }), encoding="utf-8")
    return PlanLibrary(d), pid


def test_an_untouched_default_becomes_inherit_and_a_choice_survives(tmp_path):
    lib, pid = _store(tmp_path, {
        "refocus_on_temp_delta_c": 2.0,   # deliberate - last night's real value
        "dither_pixels": 3.0,             # the default nobody ever touched
        "min_stars": 0,                   # ditto
        "max_consecutive_rejects": 10,    # ditto
    })
    changed = migrate_plan_policy_fields(lib)
    assert changed == 1

    plan = lib.get(pid)
    assert plan.refocus_on_temp_delta_c == 2.0, "a real choice must survive"
    assert plan.dither_pixels is None
    assert plan.min_stars is None
    assert plan.max_consecutive_rejects is None


def test_the_migrated_plan_now_follows_the_rig(tmp_path):
    """The point of the whole exercise: after migrating, a Settings change
    reaches a plan that was saved years ago."""
    lib, pid = _store(tmp_path, {"min_stars": 0, "dither_pixels": 3.0})
    migrate_plan_policy_fields(lib)
    cfg = AppConfig()
    cfg.standards.min_stars = 40
    p = resolve_policy(lib.get(pid), cfg)
    assert p.min_stars == 40
    assert p.sources["min_stars"] == "rig"


def test_it_is_idempotent(tmp_path):
    lib, pid = _store(tmp_path, {"dither_pixels": 3.0})
    assert migrate_plan_policy_fields(lib) == 1
    assert migrate_plan_policy_fields(lib) == 0


def test_a_plan_with_nothing_to_null_is_left_alone(tmp_path):
    """Not merely "returns 0" - the file must not be rewritten, so a library of
    already-migrated plans does not churn every boot."""
    lib, pid = _store(tmp_path, {"refocus_on_temp_delta_c": 2.0})
    path = tmp_path / "plans" / f"{pid}.json"
    before = path.read_bytes()
    assert migrate_plan_policy_fields(lib) == 0
    assert path.read_bytes() == before


def test_an_explicit_non_default_zero_is_not_a_default(tmp_path):
    """max_consecutive_rejects=0 is "guard off", and its pre-migration default
    was 10 - so a stored 0 is a CHOICE and must survive. The migration compares
    against the old default, never against falsiness."""
    lib, pid = _store(tmp_path, {"max_consecutive_rejects": 0})
    assert migrate_plan_policy_fields(lib) == 0
    assert lib.get(pid).max_consecutive_rejects == 0
