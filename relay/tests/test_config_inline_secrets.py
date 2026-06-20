"""Relay secrets load from inline env vars (Fly secrets-as-env), not just files."""
import base64
import json

import pytest

from relay.config import load_device_tokens, load_seed


def test_device_tokens_from_inline_env(monkeypatch):
    monkeypatch.delenv("RELAY_DEVICE_TOKENS_FILE", raising=False)
    monkeypatch.setenv("RELAY_DEVICE_TOKENS", json.dumps({"tok-abc": "home-1"}))
    assert load_device_tokens() == {"tok-abc": "home-1"}


def test_device_tokens_empty_when_unset(monkeypatch):
    monkeypatch.delenv("RELAY_DEVICE_TOKENS_FILE", raising=False)
    monkeypatch.delenv("RELAY_DEVICE_TOKENS", raising=False)
    assert load_device_tokens() == {}


def test_device_tokens_file_takes_precedence(tmp_path, monkeypatch):
    f = tmp_path / "toks.json"
    f.write_text(json.dumps({"file-tok": "home-file"}))
    monkeypatch.setenv("RELAY_DEVICE_TOKENS_FILE", str(f))
    monkeypatch.setenv("RELAY_DEVICE_TOKENS", json.dumps({"inline-tok": "home-inline"}))
    assert load_device_tokens() == {"file-tok": "home-file"}


def test_seed_from_inline_hex(monkeypatch):
    seed = bytes(range(32))
    monkeypatch.delenv("RELAY_OIDC_SEED_FILE", raising=False)
    monkeypatch.setenv("RELAY_OIDC_SEED", seed.hex())
    assert load_seed("RELAY_OIDC_SEED_FILE") == seed


def test_seed_from_inline_base64(monkeypatch):
    seed = bytes(range(1, 33))
    monkeypatch.delenv("RELAY_VIEWER_SEED_FILE", raising=False)
    monkeypatch.setenv("RELAY_VIEWER_SEED", base64.b64encode(seed).decode())
    assert load_seed("RELAY_VIEWER_SEED_FILE") == seed


def test_seed_empty_when_unset(monkeypatch):
    monkeypatch.delenv("RELAY_OIDC_SEED_FILE", raising=False)
    monkeypatch.delenv("RELAY_OIDC_SEED", raising=False)
    assert load_seed("RELAY_OIDC_SEED_FILE") == b""


def test_seed_rejects_wrong_length(monkeypatch):
    monkeypatch.delenv("RELAY_OIDC_SEED_FILE", raising=False)
    monkeypatch.setenv("RELAY_OIDC_SEED", "abcd")  # not 32 bytes / 64 hex / b64-32
    with pytest.raises(ValueError):
        load_seed("RELAY_OIDC_SEED_FILE")
