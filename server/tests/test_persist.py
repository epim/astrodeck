"""Persistence durability: atomic write keeps a recoverable ``.bak`` *copy*, the
replace-retry count is exact (and raises the last error), and ``ConfigStore``
restores from ``.bak`` before falling back to defaults."""
import json
import os

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


@pytest.mark.parametrize("scenario", ["files_absent", "files_present_but_corrupt"])
def test_config_falls_back_to_defaults(tmp_path, scenario):
    """files_absent: no primary and no usable backup -> clean defaults + a
    freshly-saved file. files_present_but_corrupt: a corrupt primary AND an
    unparseable ``.bak`` -> defaults (no crash)."""
    path = tmp_path / "astrodeck.json"
    bak = path.with_suffix(path.suffix + ".bak")
    if scenario == "files_present_but_corrupt":
        path.write_text("nonsense", encoding="utf-8")
        bak.write_text("also nonsense", encoding="utf-8")

    store = ConfigStore(path=path)
    cfg = store.cfg()
    assert cfg.site.is_default is True
    assert cfg.site.latitude == 0.0
    if scenario == "files_absent":
        assert path.exists()
