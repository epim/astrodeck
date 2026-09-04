"""Relay secrets load from inline env vars (Fly secrets-as-env), not just files."""
import base64
import json

import pytest

from relay.config import RelayConfig, load_device_tokens, load_seed

TOKEN_A = "a" * 43
TOKEN_B = "b" * 43


def test_device_tokens_from_inline_env(monkeypatch):
    monkeypatch.delenv("RELAY_DEVICE_TOKENS_FILE", raising=False)
    monkeypatch.setenv("RELAY_DEVICE_TOKENS", json.dumps({TOKEN_A: "home-1"}))
    assert load_device_tokens() == {TOKEN_A: "home-1"}


def test_device_tokens_empty_when_unset(monkeypatch):
    monkeypatch.delenv("RELAY_DEVICE_TOKENS_FILE", raising=False)
    monkeypatch.delenv("RELAY_DEVICE_TOKENS", raising=False)
    assert load_device_tokens() == {}


def test_device_tokens_reject_multiple_browser_security_origins(monkeypatch):
    monkeypatch.delenv("RELAY_DEVICE_TOKENS_FILE", raising=False)
    monkeypatch.setenv("RELAY_DEVICE_TOKENS", json.dumps({
        TOKEN_A: "home-a", TOKEN_B: "home-b",
    }))
    with pytest.raises(ValueError, match="exactly one home security origin"):
        load_device_tokens()


def test_device_tokens_allow_rotation_tokens_for_one_home(monkeypatch):
    monkeypatch.delenv("RELAY_DEVICE_TOKENS_FILE", raising=False)
    monkeypatch.setenv("RELAY_DEVICE_TOKENS", json.dumps({
        TOKEN_A: "home-a", TOKEN_B: "home-a",
    }))
    assert load_device_tokens() == {
        TOKEN_A: "home-a", TOKEN_B: "home-a"}


def test_device_tokens_reject_short_human_passwords(monkeypatch):
    monkeypatch.delenv("RELAY_DEVICE_TOKENS_FILE", raising=False)
    monkeypatch.setenv(
        "RELAY_DEVICE_TOKENS", json.dumps({"short-password": "home-a"}))
    with pytest.raises(ValueError, match="32-256"):
        load_device_tokens()


def test_device_tokens_reject_non_url_safe_home_id(monkeypatch):
    monkeypatch.delenv("RELAY_DEVICE_TOKENS_FILE", raising=False)
    monkeypatch.setenv(
        "RELAY_DEVICE_TOKENS", json.dumps({TOKEN_A: "../other-home"}))
    with pytest.raises(ValueError, match="URL-safe"):
        load_device_tokens()


def test_device_tokens_file_takes_precedence(tmp_path, monkeypatch):
    f = tmp_path / "toks.json"
    f.write_text(json.dumps({TOKEN_A: "home-file"}))
    monkeypatch.setenv("RELAY_DEVICE_TOKENS_FILE", str(f))
    monkeypatch.setenv("RELAY_DEVICE_TOKENS", json.dumps({TOKEN_B: "home-inline"}))
    assert load_device_tokens() == {TOKEN_A: "home-file"}


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


@pytest.mark.parametrize("field,value", [
    ("http_rate", float("nan")),
    ("ws_burst", float("inf")),
    ("request_body_timeout_s", float("nan")),
    ("request_total_timeout_s", float("inf")),
])
def test_relay_limits_reject_non_finite_values(field, value):
    kwargs = {"bind_host": "127.0.0.1", field: value}
    with pytest.raises(ValueError, match="finite"):
        RelayConfig(**kwargs)


@pytest.mark.parametrize("name,value", [
    ("RELAY_BIND_PORT", "not-an-int"),
    ("RELAY_HTTP_RATE", "not-a-number"),
    ("RELAY_HTTPS", "sometimes"),
    ("RELAY_UVICORN_ACCESS_LOG", "perhaps"),
])
def test_malformed_environment_values_fail_closed(monkeypatch, name, value):
    monkeypatch.setenv("RELAY_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        RelayConfig.from_env()
