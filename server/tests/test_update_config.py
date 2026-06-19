"""UpdateConfig defaults + typed setter + back-compat load + redaction passthrough."""
import json

import pytest

from astrodeck.config import AppConfig, ConfigStore, UpdateConfig, redacted


def test_defaults():
    u = UpdateConfig()
    assert u.enabled is True and u.auto_check is False
    assert u.channel == "stable" and u.check_interval_hours == 24
    assert u.repo == "epim/astrodeck" and u.signing_pubkey == ""
    assert u.health_timeout_s == 60 and u.last_check_ts is None


def test_appconfig_has_update_default():
    assert AppConfig().update.channel == "stable"


def test_set_update_config_persists_and_validates(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.cfg()
    with pytest.raises(ValueError):
        store.set_update_config(UpdateConfig(channel="weird"))
    cfg = store.set_update_config(
        UpdateConfig(channel="prerelease", signing_pubkey="PUB", auto_check=True))
    assert cfg.update.channel == "prerelease"
    assert cfg.update.signing_pubkey == "PUB"
    # survives a reload from disk
    assert ConfigStore(path=tmp_path / "astrodeck.json").cfg().update.auto_check is True


def test_old_config_without_update_block_loads(tmp_path):
    path = tmp_path / "astrodeck.json"
    path.write_text(json.dumps({"version": 1, "site": {"name": "x"}}))
    cfg = ConfigStore(path=path).cfg()
    assert cfg.update.channel == "stable"  # pydantic fills the default


def test_redacted_keeps_public_signing_key():
    cfg = AppConfig()
    cfg.update.signing_pubkey = "PUBLICKEY"
    out = redacted(cfg)
    assert out["update"]["signing_pubkey"] == "PUBLICKEY"  # public, never scrubbed
