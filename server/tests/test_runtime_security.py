"""Unit tests for the pre-listener privilege interlock."""

from __future__ import annotations

import os

import pytest

from astrodeck import runtime_security


def test_posix_detection_uses_effective_not_real_uid(monkeypatch):
    monkeypatch.setattr(runtime_security.os, "geteuid", lambda: 10001, raising=False)
    assert runtime_security._posix_is_elevated() is False

    monkeypatch.setattr(runtime_security.os, "geteuid", lambda: 0, raising=False)
    assert runtime_security._posix_is_elevated() is True


def test_posix_detection_fails_closed_on_bad_query(monkeypatch):
    def failed_query():
        raise OSError("query unavailable")

    monkeypatch.setattr(runtime_security.os, "geteuid", failed_query, raising=False)
    with pytest.raises(runtime_security.PrivilegeDetectionError):
        runtime_security._posix_is_elevated()

    monkeypatch.setattr(runtime_security.os, "geteuid", lambda: -1, raising=False)
    with pytest.raises(runtime_security.PrivilegeDetectionError):
        runtime_security._posix_is_elevated()


def test_guard_refuses_elevation_and_propagates_detection_failure(monkeypatch):
    monkeypatch.setattr(runtime_security, "is_elevated_runtime", lambda: False)
    assert runtime_security.require_unprivileged_runtime("astrodeck") is None

    monkeypatch.setattr(runtime_security, "is_elevated_runtime", lambda: True)
    with pytest.raises(RuntimeError, match=r"(?i)(refus|root|elevat)"):
        runtime_security.require_unprivileged_runtime("astrodeck")

    def failed_detection():
        raise runtime_security.PrivilegeDetectionError("token query failed")

    monkeypatch.setattr(runtime_security, "is_elevated_runtime", failed_detection)
    with pytest.raises(runtime_security.PrivilegeDetectionError):
        runtime_security.require_unprivileged_runtime("astrodeck")


def test_trusted_proxy_parser_is_exact_and_rejects_global_trust():
    assert runtime_security.parse_forwarded_allow_ips(
        " 127.0.0.1,10.2.3.4/24,2001:db8::1 "
    ) == "127.0.0.1,10.2.3.0/24,2001:db8::1"
    for raw in (
        "*", "0.0.0.0/0", "::/0", "proxy.internal",
        "127.0.0.1,,10.0.0.1", "127.0.0.1,127.0.0.1",
    ):
        with pytest.raises(ValueError):
            runtime_security.parse_forwarded_allow_ips(raw)


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows token APIs")
def test_windows_token_query_returns_a_boolean():
    assert isinstance(runtime_security._windows_is_elevated(), bool)
