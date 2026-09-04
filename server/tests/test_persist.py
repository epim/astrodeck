"""Persistence durability: atomic write keeps a recoverable ``.bak`` *copy*, the
replace-retry count is exact (and raises the last error), and ``ConfigStore``
restores from ``.bak`` and fails closed rather than replacing corrupt security
state with defaults."""
import json
import os
import stat

import pytest

import astrodeck.persist as persist
from astrodeck.config import AppConfig, ConfigStore, Site
from astrodeck.persist import (
    _REPLACE_RETRIES,
    read_json,
    write_json_atomic,
)


# ----------------------------------------------------------------- atomic write

def test_write_round_trips(tmp_path):
    p = tmp_path / "x.json"
    write_json_atomic(p, {"a": 1, "b": [2, 3]})
    assert read_json(p) == {"a": 1, "b": [2, 3]}


def test_backup_is_a_copy_not_a_move(tmp_path):
    """Overwriting an existing file leaves BOTH the new primary AND a ``.bak`` of
    the old contents — the primary is never momentarily absent (power-loss safe)."""
    p = tmp_path / "x.json"
    write_json_atomic(p, {"v": 1})
    write_json_atomic(p, {"v": 2})
    bak = p.with_suffix(p.suffix + ".bak")
    assert p.exists()                      # primary present (was a copy, not a move)
    assert bak.exists()
    assert read_json(p) == {"v": 2}        # new contents
    assert read_json(bak) == {"v": 1}      # previous contents preserved


def test_no_tmp_left_behind(tmp_path):
    p = tmp_path / "x.json"
    write_json_atomic(p, {"v": 1})
    assert not p.with_suffix(p.suffix + ".tmp").exists()


def test_first_write_has_no_backup(tmp_path):
    p = tmp_path / "x.json"
    write_json_atomic(p, {"v": 1})
    assert not p.with_suffix(p.suffix + ".bak").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_persisted_json_and_backup_are_owner_only(tmp_path):
    p = tmp_path / "astrodeck.json"
    write_json_atomic(p, {"secret": "one"})
    write_json_atomic(p, {"secret": "two"})
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.with_suffix(".json.bak").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_read_repairs_legacy_world_readable_json(tmp_path):
    p = tmp_path / "astrodeck.json"
    p.write_text('{"secret": "legacy"}', encoding="utf-8")
    p.chmod(0o644)
    assert read_json(p) == {"secret": "legacy"}
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


# ------------------------------------------------------- replace-retry exactness

def test_replace_retry_attempts_exactly_n_times_then_raises(tmp_path, monkeypatch):
    """``_replace_with_retry`` makes exactly ``_REPLACE_RETRIES`` os.replace calls
    on persistent PermissionError, then re-raises the last one (no off-by-one 4th
    replace)."""
    calls = {"n": 0}

    def always_locked(src, dst):
        calls["n"] += 1
        raise PermissionError("locked by AV")

    monkeypatch.setattr(persist.os, "replace", always_locked)
    monkeypatch.setattr(persist.time, "sleep", lambda *_: None)  # no real backoff

    with pytest.raises(PermissionError):
        persist._replace_with_retry(tmp_path / "a", tmp_path / "b")
    assert calls["n"] == _REPLACE_RETRIES


def test_replace_retry_succeeds_after_transient_lock(tmp_path, monkeypatch):
    calls = {"n": 0}
    real_replace = os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < _REPLACE_RETRIES:
            raise PermissionError("transient")
        return real_replace(src, dst)

    monkeypatch.setattr(persist.os, "replace", flaky)
    monkeypatch.setattr(persist.time, "sleep", lambda *_: None)

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.write_text("hi", encoding="utf-8")
    persist._replace_with_retry(src, dst)
    assert dst.read_text(encoding="utf-8") == "hi"
    assert calls["n"] == _REPLACE_RETRIES


# ----------------------------------------------------- ConfigStore .bak recovery

def _good_config_dict() -> dict:
    return AppConfig(site=Site(name="Backyard", latitude=42.0, longitude=-71.0,
                               is_default=False)).model_dump()


def test_config_restores_from_bak_when_primary_missing(tmp_path):
    """If the live config file vanishes (power-loss between backup and replace)
    but a valid ``.bak`` exists, ``_load`` restores it instead of resetting to
    the lat0/lon0 defaults — and re-establishes the primary."""
    path = tmp_path / "astrodeck.json"
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_text(json.dumps(_good_config_dict()), encoding="utf-8")

    store = ConfigStore(path=path)
    cfg = store.cfg()
    assert cfg.site.name == "Backyard"
    assert cfg.site.latitude == 42.0
    assert cfg.site.is_default is False
    assert path.exists()                   # primary re-established from the bak


def test_config_restores_from_bak_when_primary_corrupt(tmp_path):
    """A corrupt primary with a valid ``.bak`` recovers the backup (reading the
    ``.bak`` BEFORE _recover_from_corrupt would overwrite it)."""
    path = tmp_path / "astrodeck.json"
    bak = path.with_suffix(path.suffix + ".bak")
    path.write_text("{ not valid json", encoding="utf-8")
    bak.write_text(json.dumps(_good_config_dict()), encoding="utf-8")

    store = ConfigStore(path=path)
    cfg = store.cfg()
    assert cfg.site.name == "Backyard"
    assert cfg.site.is_default is False
    # primary now parses again
    assert read_json(path)["site"]["name"] == "Backyard"
    # the known-good .bak must NOT have been clobbered by the corrupt primary
    assert read_json(bak)["site"]["name"] == "Backyard"


def test_new_config_store_falls_back_to_defaults(tmp_path):
    """No primary and no backup means a genuine first run, so defaults are safe."""
    path = tmp_path / "astrodeck.json"

    store = ConfigStore(path=path)
    cfg = store.cfg()
    assert cfg.site.is_default is True
    assert cfg.site.latitude == 0.0
    assert path.exists()


@pytest.mark.parametrize("primary", ["nonsense", "[]"])
def test_corrupt_or_invalid_config_without_backup_fails_closed(tmp_path, primary):
    path = tmp_path / "astrodeck.json"
    path.write_text(primary, encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"configuration.*(corrupt|invalid)"):
        ConfigStore(path=path).cfg()

    assert path.read_text(encoding="utf-8") == primary


def test_corrupt_primary_and_backup_fail_closed_without_overwrite(tmp_path):
    path = tmp_path / "astrodeck.json"
    bak = path.with_suffix(path.suffix + ".bak")
    path.write_text("nonsense", encoding="utf-8")
    bak.write_text("also nonsense", encoding="utf-8")

    with pytest.raises(RuntimeError, match="backup"):
        ConfigStore(path=path).cfg()

    assert path.read_text(encoding="utf-8") == "nonsense"
    assert bak.read_text(encoding="utf-8") == "also nonsense"
