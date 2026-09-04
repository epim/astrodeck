"""Unit tests for the standalone relay privilege interlock."""

from __future__ import annotations

import os

import pytest

from relay import runtime_security


def test_posix_detection_uses_effective_uid(monkeypatch):
    monkeypatch.setattr(runtime_security.os, "geteuid", lambda: 10001, raising=False)
    assert runtime_security._posix_is_elevated() is False

    monkeypatch.setattr(runtime_security.os, "geteuid", lambda: 0, raising=False)
    assert runtime_security._posix_is_elevated() is True


def test_guard_refuses_elevation_and_propagates_detection_failure(monkeypatch):
    monkeypatch.setattr(runtime_security, "is_elevated_runtime", lambda: False)
    assert runtime_security.require_unprivileged_runtime("astrodeck relay") is None

    monkeypatch.setattr(runtime_security, "is_elevated_runtime", lambda: True)
    with pytest.raises(RuntimeError, match=r"(?i)(refus|root|elevat)"):
        runtime_security.require_unprivileged_runtime("astrodeck relay")

    def failed_detection():
        raise runtime_security.PrivilegeDetectionError("token query failed")

    monkeypatch.setattr(runtime_security, "is_elevated_runtime", failed_detection)
    with pytest.raises(runtime_security.PrivilegeDetectionError):
        runtime_security.require_unprivileged_runtime("astrodeck relay")


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows token APIs")
def test_windows_token_query_returns_a_boolean():
    assert isinstance(runtime_security._windows_is_elevated(), bool)
