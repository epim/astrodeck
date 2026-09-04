"""Regression tests for the process-level network exposure interlock."""
from __future__ import annotations

import argparse

import pytest

import astrodeck.__main__ as cli
import astrodeck.auth.users as users_mod
import astrodeck.config as config_mod
from astrodeck.auth.users import UserStore
from astrodeck.config import AuthConfig, ConfigStore


def test_default_cli_bind_is_loopback():
    assert cli._run_only_parser().parse_args([]).host == "127.0.0.1"
    args = cli._build_parser().parse_args(["run"])
    assert args.host == "127.0.0.1"


def test_unauthenticated_non_loopback_bind_is_refused(monkeypatch):
    monkeypatch.setattr(cli, "_security_banner", lambda _host, _port: False)
    monkeypatch.delenv(cli.ALLOW_INSECURE_OPEN_ENV, raising=False)
    monkeypatch.delenv(cli.REQUIRE_AUTH_ENV, raising=False)
    args = argparse.Namespace(
        host="0.0.0.0", port=8800, allow_insecure_open=False)

    assert cli._cmd_run(args) == 2


def test_deployment_auth_interlock_refuses_even_loopback_and_override(monkeypatch):
    monkeypatch.setattr(cli, "_security_banner", lambda _host, _port: False)
    monkeypatch.setenv(cli.REQUIRE_AUTH_ENV, "true")
    monkeypatch.setenv(cli.ALLOW_INSECURE_OPEN_ENV, "true")
    args = argparse.Namespace(
        host="127.0.0.1", port=8800, allow_insecure_open=True)

    assert cli._cmd_run(args) == 2


def test_deployment_security_booleans_reject_ambiguous_values(monkeypatch):
    monkeypatch.setenv(cli.REQUIRE_AUTH_ENV, "sometimes")
    args = argparse.Namespace(
        host="127.0.0.1", port=8800, allow_insecure_open=False)

    with pytest.raises(RuntimeError, match=cli.REQUIRE_AUTH_ENV):
        cli._cmd_run(args)


def _install_auth_state(tmp_path, monkeypatch, auth: AuthConfig):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.cfg().auth = auth
    users = UserStore(path=tmp_path / "users.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(users_mod, "user_store", users)
    return users


def test_required_startup_rejects_unauthenticated_first_run(tmp_path, monkeypatch):
    _install_auth_state(
        tmp_path, monkeypatch,
        AuthConfig(methods=["local"], local_enabled_first_run=True),
    )
    monkeypatch.setenv(cli.REQUIRE_AUTH_ENV, "true")
    monkeypatch.delenv(config_mod.DIRECT_TOKEN_ENV, raising=False)
    with pytest.raises(ValueError, match="first-run"):
        cli._security_banner("127.0.0.1", 8800)


def test_required_startup_rejects_weak_direct_token(tmp_path, monkeypatch):
    _install_auth_state(tmp_path, monkeypatch, AuthConfig())
    monkeypatch.setenv(cli.REQUIRE_AUTH_ENV, "true")
    monkeypatch.setenv(config_mod.DIRECT_TOKEN_ENV, "guessable")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        cli._security_banner("127.0.0.1", 8800)


def test_required_startup_accepts_current_local_admin(tmp_path, monkeypatch):
    users = _install_auth_state(
        tmp_path, monkeypatch,
        AuthConfig(methods=["local"], local_enabled_first_run=False),
    )
    users.create(username="admin", password="correct-horse", role="admin")
    monkeypatch.setenv(cli.REQUIRE_AUTH_ENV, "true")
    monkeypatch.delenv(config_mod.DIRECT_TOKEN_ENV, raising=False)
    assert cli._security_banner("127.0.0.1", 8800) is True
