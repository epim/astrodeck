"""The config file's version marker, and what it is honestly for.

It was added because the cloudmap platform default could not be migrated: a
written-out ``"G18"`` is byte-identical whether it was a default or a choice,
and there was no stamp to key a one-shot on. The marker does not recover that
lost information -- nothing can -- but it makes the NEXT such change tractable,
and it closes a data-loss path that was live in the meantime.
"""
from __future__ import annotations

import json

import pytest

from astrodeck.config import (
    CONFIG_SCHEMA,
    AppConfig,
    ConfigStore,
    _stored_schema,
    _unknown_keys,
)


def _write(path, body: dict) -> None:
    path.write_text(json.dumps(body), encoding="utf-8")


def _read(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------- the stamp

def test_a_file_with_no_stamp_reads_as_zero_not_as_current():
    """This is the whole point and the easiest thing to get backwards.

    ``schema_version`` DEFAULTS to CONFIG_SCHEMA, so building the model
    straight off the raw dict would invent a stamp for a file that has none --
    destroying the one fact a future migration needs, which is that this file
    predates the marker. `_load` therefore reads the stamp before pydantic
    touches it.
    """
    assert _stored_schema({}) == 0
    assert _stored_schema({"version": 9}) == 0, "the concurrency token is not the schema"


def test_an_untrustworthy_stamp_reads_LOW_rather_than_high():
    """Missing, null, junk and negative all mean "cannot be trusted as current".

    The safe reading is the OLDEST version, because that makes a future
    migration RUN. Rounding an unreadable stamp up would silently skip it, and
    a migration that is skipped leaves the config in a state nothing will ever
    revisit.
    """
    for junk in ({"schema_version": None}, {"schema_version": ""},
                 {"schema_version": "not a number"}, {"schema_version": []},
                 {"schema_version": -4}):
        assert _stored_schema(junk) == 0, junk


def test_a_real_stamp_is_read_verbatim():
    assert _stored_schema({"schema_version": 1}) == 1
    assert _stored_schema({"schema_version": 7}) == 7
    assert _stored_schema({"schema_version": "3"}) == 3, "a stringified int is still an int"


def test_a_fresh_config_is_stamped_and_the_stamp_reaches_disk(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    assert store.cfg().schema_version == CONFIG_SCHEMA
    assert _read(tmp_path / "astrodeck.json")["schema_version"] == CONFIG_SCHEMA


def test_an_unstamped_file_is_stamped_once_and_keeps_its_settings(tmp_path):
    """The upgrade path every existing rig takes. It must not lose anything."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"version": 12, "deadman_url": "https://example.invalid/ping",
                  "site": {"latitude": 37.348, "longitude": -121.802,
                           "elevation_m": 0.0, "is_default": False}})
    cfg = ConfigStore(path=path).cfg()
    assert cfg.schema_version == CONFIG_SCHEMA
    assert cfg.version == 12
    assert cfg.deadman_url == "https://example.invalid/ping"
    assert cfg.site.latitude == pytest.approx(37.348)
    assert _read(path)["schema_version"] == CONFIG_SCHEMA, (
        "the stamp has to be persisted, or every boot re-migrates for ever")


# ------------------------------------------- a file from a build we are not

def test_a_newer_builds_settings_survive_a_downgrade(tmp_path):
    """THE DATA-LOSS PATH THIS MARKER EXISTS TO CLOSE.

    AppConfig is a plain BaseModel, so pydantic's default extra="ignore" drops
    every key this build has never heard of -- and the next _save writes the
    stripped version back. An operator who ran a newer AstroDeck and then rolled
    back used to lose those settings permanently, with nothing said.
    """
    path = tmp_path / "astrodeck.json"
    _write(path, {
        "schema_version": CONFIG_SCHEMA + 3,
        "version": 5,
        "deadman_url": "https://example.invalid/ping",
        "a_feature_from_the_future": {"enabled": True, "threshold": 42},
        "another_one": "keep me",
    })
    store = ConfigStore(path=path)
    cfg = store.cfg()
    cfg.deadman_url = "https://example.invalid/changed"
    store._save()

    after = _read(path)
    assert after["a_feature_from_the_future"] == {"enabled": True, "threshold": 42}
    assert after["another_one"] == "keep me"
    assert after["deadman_url"] == "https://example.invalid/changed", (
        "and this build's own edit still wins on the keys it owns")


def test_the_stamp_of_a_newer_file_is_not_quietly_downgraded(tmp_path):
    """Stamping it down to ours would tell the newer build its migrations ran."""
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": CONFIG_SCHEMA + 3, "version": 1})
    store = ConfigStore(path=path)
    store.cfg()
    store._save()
    assert _read(path)["schema_version"] == CONFIG_SCHEMA + 3


def test_an_unknown_key_on_an_OLD_file_is_dropped_as_before(tmp_path):
    """Preservation is for a NEWER stamp only, and the asymmetry is deliberate.

    On an equal or older stamp an unknown key is a field we deliberately
    removed. Carrying those forward for ever is a different bug -- the config
    would accumulate every setting the project ever had.
    """
    path = tmp_path / "astrodeck.json"
    _write(path, {"schema_version": CONFIG_SCHEMA, "version": 1,
                  "a_field_we_deleted": "should not survive"})
    store = ConfigStore(path=path)
    store.cfg()
    store._save()
    assert "a_field_we_deleted" not in _read(path)


def test_unknown_keys_are_computed_against_the_real_model():
    known = {"schema_version": 1, "version": 2, "site": {}, "optics": {}}
    assert _unknown_keys(known) == {}
    assert _unknown_keys({**known, "zzz": 1}) == {"zzz": 1}
    for name in ("site", "optics", "safety", "auth", "cloudmap"):
        assert name in AppConfig.model_fields, (
            f"{name} moved or was renamed; _unknown_keys would start "
            "preserving it as foreign and it would never be edited again")


# ------------------------------------------------------ what it does NOT do

def test_the_marker_does_not_pretend_to_know_a_default_from_a_choice(tmp_path):
    """Stated as a test so nobody later assumes it does.

    Two configs, one where the operator deliberately pinned GOES-West and one
    where the old default was simply written out. They are the same bytes. No
    version number recovers that; only recording which fields were explicitly
    set would, and the format has never done so.
    """
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    _write(a, {"version": 1, "cloudmap": {"enabled": True, "platform": "G18",
                                          "poll_minutes": 10, "half_px": 100}})
    _write(b, {"version": 1, "cloudmap": {"enabled": True, "platform": "G18",
                                          "poll_minutes": 10, "half_px": 100}})
    ca = ConfigStore(path=a).cfg()
    cb = ConfigStore(path=b).cfg()
    assert ca.cloudmap.platform == cb.cloudmap.platform == "G18"
    assert ca.schema_version == cb.schema_version
    # Which is why the cloud model ASKS, in Settings, rather than rewriting it.


def test_the_backup_restore_path_preserves_them_too(tmp_path):
    """The restore writes straight to disk, bypassing `_save` and its merge.

    So the preservation has to be repeated there. Found by reading the file
    rather than by a failing test, which is exactly the kind of second write
    path that makes a guarantee true in one place and false in another.
    """
    path = tmp_path / "astrodeck.json"
    bak = tmp_path / "astrodeck.json.bak"
    good = {"schema_version": CONFIG_SCHEMA + 2, "version": 3,
            "future_thing": {"keep": True}}
    bak.write_text(json.dumps(good), encoding="utf-8")
    path.write_text("{ this is not json", encoding="utf-8")

    cfg = ConfigStore(path=path).cfg()
    assert cfg.version == 3, "the backup was used"
    assert _read(path)["future_thing"] == {"keep": True}, (
        "recovering from corruption is not licence to delete a newer build's "
        "settings as well")
