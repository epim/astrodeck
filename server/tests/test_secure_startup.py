"""Regression tests for the process-level network exposure interlock."""
from __future__ import annotations

import argparse

import astrodeck.__main__ as cli


def test_default_cli_bind_is_loopback():
    assert cli._run_only_parser().parse_args([]).host == "127.0.0.1"
    args = cli._build_parser().parse_args(["run"])
    assert args.host == "127.0.0.1"


def test_unauthenticated_non_loopback_bind_is_refused(monkeypatch):
    monkeypatch.setattr(cli, "_security_banner", lambda _host, _port: False)
    monkeypatch.delenv(cli.ALLOW_INSECURE_OPEN_ENV, raising=False)
    args = argparse.Namespace(
        host="0.0.0.0", port=8800, allow_insecure_open=False)

    assert cli._cmd_run(args) == 2
