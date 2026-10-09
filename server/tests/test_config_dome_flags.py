# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""PRO-4 Task 5 — the two additive ``SafetyConfig`` dome flags.

``close_dome_on_unsafe`` defaults False (every existing rig/test
byte-identical; #657, #192, an open owner question left alone).
``close_dome_when_done`` defaults True since W7 (D-16, owner-approved
2026-09-30, #192, #561, #562): leaving the roof open through the day is
the worse of the two failures #192 found, and closing at a normal
end-of-night is not a surprise escalation the way an unsafe-trip close
is. A round-trip through ``set_safety`` persists both; an old config
JSON without the keys deserializes with the protective (False) default
for ``close_dome_on_unsafe`` and the new True default for
``close_dome_when_done``. The ``remote`` preset carries
close_dome_on_unsafe.

WP-95 (#657, migration part): D-16 changed the FIELD default and shipped no
migration, and ``_save`` writes ``model_dump()`` with no ``exclude_defaults``,
so every config file already on disk carries a literal
``close_dome_when_done: false`` that the new default can never reach. The
tests at the bottom pin the one-shot CONFIG_SCHEMA 2 -> 3 step that raises it
once (backlog ruling D-16, owner-approved 2026-09-30) and leaves a False
written at schema 3 alone. ``close_dome_on_unsafe`` is NOT migrated: whether
it should default True is the owner's open question (b) on #657.

MUTATIONS RUN FOR WP-95 (each from a byte backup inside the worktree, restored
byte-identically by sha256, mutant text grepped out afterwards):

  c1, key the 2 -> 3 step on ``stored < 2`` (so a stamped-2 file never runs it).
  5 failed; first: test_a_schema_2_false_is_raised_to_true_once_and_stamped,
  ``AssertionError: assert False is True`` on cfg.safety.close_dome_when_done.

  c2, drop the ``stored < 3`` key so the step runs on every load.
  4 failed: test_a_false_written_at_schema_3_or_later_is_the_operators_and_stays
  [3], [4], [9] and test_the_operators_choice_after_the_migration_survives_a_restart,
  ``AssertionError: assert True is False`` on cfg.safety.close_dome_when_done.

  c3, have the step also set close_dome_on_unsafe True (the owner's open
  question (b)). 1 failed: test_the_unsafe_close_and_the_neighbours_are_not_touched,
  ``AssertionError: assert True is False`` on
  SafetyConfig(... close_dome_on_unsafe=True ...).close_dome_on_unsafe.

  c4, drop ``_apply_migrations`` from ``_restore_from_bak``. 1 failed:
  test_a_config_restored_from_its_backup_is_migrated_as_well,
  ``AssertionError: assert False is True`` on cfg.safety.close_dome_when_done.

  c5, leave CONFIG_SCHEMA at 2. 6 failed; first: test_the_schema_is_3,
  ``assert 2 == 3``; the migration cases follow, because a stamped-2 file is
  then "current" and nothing persists the raise.

  c6, drop ``and not cfg.safety.close_dome_when_done`` so the line is said for
  a file that already had the flag True. 1 failed:
  test_the_raise_is_said_once_and_only_when_it_changed_something,
  ``assert not [1]`` -- the captured line read "close_dome_when_done is now on
  by default and this config had it off", false for that file.
"""
import json

import pytest

import astrodeck.config as config_mod
from astrodeck.config import (
    CONFIG_SCHEMA,
    AppConfig,
    ConfigStore,
    SAFETY_PRESETS,
    SafetyConfig,
)


def test_defaults_are_false():
    # W7 INTEGRATION RE-PIN (WP-50, D-16, owner-approved 2026-09-30):
    # close_dome_when_done now defaults True (#192, #561, #562); every
    # other default here, close_dome_on_unsafe included, is unchanged.
    # Deliberate, not a regression.
    s = SafetyConfig()
    assert s.close_dome_on_unsafe is False
    assert s.close_dome_when_done is True
    assert s.reopen_dome_when_safe is False       # PRO-4 D3 opt-in, OFF by default


def test_set_safety_round_trip(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_safety(SafetyConfig(close_dome_on_unsafe=True,
                                  close_dome_when_done=True,
                                  reopen_dome_when_safe=True))
    got = store.reload().safety
    assert got.close_dome_on_unsafe is True
    assert got.close_dome_when_done is True
    assert got.reopen_dome_when_safe is True


def test_old_config_json_without_keys_deserializes_false():
    # A config file written before PRO-4 has neither key — pydantic fills
    # each field's current default. W7 INTEGRATION RE-PIN (WP-50, D-16,
    # owner-approved 2026-09-30): close_dome_when_done's default is now
    # True (#192, #561, #562), so an old file without the key reads True,
    # not the protective False it used to; close_dome_on_unsafe and
    # reopen_dome_when_safe keep their protective False default.
    # Deliberate, not a regression.
    cfg = AppConfig(**{"version": 3, "safety": {"enabled": True,
                                                "preset": "backyard"}})
    assert cfg.safety.close_dome_on_unsafe is False
    assert cfg.safety.close_dome_when_done is True
    assert cfg.safety.reopen_dome_when_safe is False


def test_remote_preset_enables_close_on_unsafe():
    assert SAFETY_PRESETS["remote"]["close_dome_on_unsafe"] is True
    # backyard + the base default stay OFF (no surprise roof move for a
    # supervised backyard operator).
    assert "close_dome_on_unsafe" not in SAFETY_PRESETS["backyard"]
    assert SafetyConfig().close_dome_on_unsafe is False


def test_reopen_is_not_a_preset_flag():
    # PRO-4 D3: reopen is an ADVANCED opt-in — deliberately absent from every
    # preset (no surprise roof cycling driven by picking a preset). Only an
    # explicit user toggle sets it.
    for name, preset in SAFETY_PRESETS.items():
        assert "reopen_dome_when_safe" not in preset, name


# ------------------------------------------- the CONFIG_SCHEMA 2 -> 3 migration
# WP-95 (#657 migration part). Every file below goes through a real
# ConfigStore, because the defect is that the field default never reaches a
# config already on disk: a bare ``AppConfig(**raw)`` cannot see it.


def _write(path, body: dict) -> None:
    path.write_text(json.dumps(body), encoding="utf-8")


def _read(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _config_lines(monkeypatch) -> list[tuple[str, str]]:
    """Capture what ``config.py`` says to the bus, as (level, message)."""
    seen: list[tuple[str, str]] = []

    def _log(level, message, source="hub", **_kw):
        if source == "config":
            seen.append((level, message))

    monkeypatch.setattr(config_mod.bus, "log", _log)
    return seen


def test_the_schema_reaches_3():
    # DELIBERATE PIN CHANGE (WP-95): this is the stamp the migration is keyed
    # on. The 2 -> 3 step is `stored < 3`; with CONFIG_SCHEMA left at 2 a
    # stamped-2 file is "current" and the step could never run.
    # DELIBERATE PIN CHANGE (#854): `>= 3`, since the 3 -> 4 guide-RMS step
    # moved the stamp on; what this guards is that the 2 -> 3 step can run.
    # The number 4 is pinned once, in test_guide_rms_ceiling_default.py.
    assert CONFIG_SCHEMA >= 3


def test_a_schema_2_false_is_raised_to_true_once_and_stamped(tmp_path):
    """The file every existing rig has: written by a build whose default was
    False, so it carries a literal ``close_dome_when_done: false`` that is
    usually the old default written out (a file cannot tell that from a
    deliberate off; the log line says how to turn it back off). D-16 said the
    default is True; without this step it reaches nobody."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": 2, "version": 7,
                  "safety": {"close_dome_when_done": False}})
    cfg = ConfigStore(path=path).cfg()
    assert cfg.safety.close_dome_when_done is True
    # DELIBERATE PIN CHANGE (#854): the current stamp, not 3.
    assert cfg.schema_version == CONFIG_SCHEMA
    on_disk = _read(path)
    assert on_disk["schema_version"] == CONFIG_SCHEMA
    assert on_disk["safety"]["close_dome_when_done"] is True, (
        "the raise has to be persisted, or every boot re-runs it for ever")


def test_an_unstamped_file_is_raised_too(tmp_path):
    """A file older than the marker reads as schema 0 and is below 3 as well."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"version": 3, "safety": {"close_dome_when_done": False}})
    assert ConfigStore(path=path).cfg().safety.close_dome_when_done is True


@pytest.mark.parametrize("stamp", [3, 4, 9])
def test_a_false_written_at_schema_3_or_later_is_the_operators_and_stays(
        tmp_path, stamp):
    """After the migration, False means somebody turned the close off. A newer
    stamp is a build this one must not second-guess either."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": stamp, "version": 7,
                  "safety": {"close_dome_when_done": False}})
    store = ConfigStore(path=path)
    assert store.cfg().safety.close_dome_when_done is False
    assert store.reload().safety.close_dome_when_done is False
    assert _read(path)["safety"]["close_dome_when_done"] is False


def test_the_operators_choice_after_the_migration_survives_a_restart(tmp_path):
    """The step runs ONCE per file. Migrate, let the operator turn the close
    off through the product's own setter, restart: still off."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": 2, "version": 7,
                  "safety": {"close_dome_when_done": False}})
    store = ConfigStore(path=path)
    assert store.cfg().safety.close_dome_when_done is True
    store.set_safety(SafetyConfig(close_dome_when_done=False))
    assert ConfigStore(path=path).cfg().safety.close_dome_when_done is False


def test_the_unsafe_close_and_the_neighbours_are_not_touched(tmp_path):
    """D-16 covers close_dome_when_done ONLY. Whether close_dome_on_unsafe
    defaults True is the owner's open question (b) on #657, and a roof that
    closes on a rain trip is a surprise escalation, so a migration that rode
    along with it would be deciding that question without the owner."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": 2, "version": 7, "safety": {
        "close_dome_when_done": False, "close_dome_on_unsafe": False,
        "reopen_dome_when_safe": False, "on_unsafe": "park",
        "max_pause_min": 45}})
    s = ConfigStore(path=path).cfg().safety
    assert s.close_dome_when_done is True
    assert s.close_dome_on_unsafe is False
    assert s.reopen_dome_when_safe is False
    assert s.on_unsafe == "park"
    assert s.max_pause_min == 45


def test_the_raise_is_said_once_and_only_when_it_changed_something(
        tmp_path, monkeypatch):
    """One info line when the flag was actually raised, naming the setting and
    the way back; none on the next boot; none for a file that already had it
    True (a line saying 'was off' there would be false)."""
    seen = _config_lines(monkeypatch)
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": 2, "version": 7,
                  "safety": {"close_dome_when_done": False}})
    ConfigStore(path=path).cfg()
    mine = [(lvl, msg) for lvl, msg in seen if "close_dome_when_done" in msg]
    assert len(mine) == 1, seen
    assert mine[0][0] == "info"
    ConfigStore(path=path).cfg()            # the next boot
    assert len([1 for _l, m in seen if "close_dome_when_done" in m]) == 1, seen

    seen.clear()
    other = tmp_path / "other.json"
    _write(other, {"schema_version": 2, "version": 7,
                   "safety": {"close_dome_when_done": True}})
    assert ConfigStore(path=other).cfg().safety.close_dome_when_done is True
    assert not [1 for _l, m in seen if "close_dome_when_done" in m], seen
    # DELIBERATE PIN CHANGE (#854): the current stamp, not 3.
    assert _read(other)["schema_version"] == CONFIG_SCHEMA, (
        "still stamped, so it never re-checks")


def test_the_steps_compose_from_schema_1(tmp_path):
    """A rig that skipped a build runs 1 -> 2 and 2 -> 3 in one load."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": 1, "version": 7,
                  "standards": {"max_eccentricity": 0.0},
                  "safety": {"close_dome_when_done": False}})
    cfg = ConfigStore(path=path).cfg()
    assert cfg.standards.max_eccentricity == 0.65
    assert cfg.safety.close_dome_when_done is True
    # DELIBERATE PIN CHANGE (#854): the current stamp, not 3.
    assert cfg.schema_version == CONFIG_SCHEMA


def test_a_config_restored_from_its_backup_is_migrated_as_well(tmp_path):
    """`_load` is not the only way a config comes off disk: a corrupt primary
    is rebuilt from the `.bak` by `_restore_from_bak`, which returned before
    any migration and kept the OLD stamp. The restored config then ran
    un-migrated for the whole process, and any save in that process re-wrote
    the old stamp, so an operator's choice made meanwhile would have been
    overwritten by the step on the next boot."""
    path = tmp_path / "astrodeck.json"
    bak = tmp_path / "astrodeck.json.bak"
    bak.write_text(json.dumps({"schema_version": 2, "version": 4, "safety": {
        "close_dome_when_done": False}}), encoding="utf-8")
    path.write_text("{ this is not json", encoding="utf-8")
    cfg = ConfigStore(path=path).cfg()
    assert cfg.version == 4, "the backup was used"
    assert cfg.safety.close_dome_when_done is True
    # DELIBERATE PIN CHANGE (#854): the current stamp, not 3.
    assert _read(path)["schema_version"] == CONFIG_SCHEMA
    assert _read(path)["safety"]["close_dome_when_done"] is True
