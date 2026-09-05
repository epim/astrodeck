"""OPEN-008: a redacted security audit log for auth events.

Failed and successful sign-ins must leave a durable, structured trace that
records who/outcome/ip and a machine reason -- and never a password or token.
"""
from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from astrodeck.auth import audit
from test_local_auth_routes import _make_app  # shared isolated-app harness

AUDIT = "astrodeck.audit"


# ------------------------------------------------------------- unit: record()

def test_success_is_info_with_fields(caplog):
    with caplog.at_level(logging.INFO, logger=AUDIT):
        audit.record("login", ok=True, user="alice")
    rec = [r for r in caplog.records if r.name == AUDIT][-1]
    assert rec.levelno == logging.INFO
    assert "auth login ok" in rec.message
    assert "user=alice" in rec.message


def test_failure_is_warning_with_reason(caplog):
    with caplog.at_level(logging.WARNING, logger=AUDIT):
        audit.record("login", ok=False, user="bob", reason="bad_credentials")
    rec = [r for r in caplog.records if r.name == AUDIT][-1]
    assert rec.levelno == logging.WARNING
    assert "auth login deny" in rec.message
    assert "reason=bad_credentials" in rec.message


def test_missing_request_records_unknown_ip(caplog):
    with caplog.at_level(logging.INFO, logger=AUDIT):
        audit.record("token_login", ok=True)
    rec = [r for r in caplog.records if r.name == AUDIT][-1]
    assert "ip=?" in rec.message
    assert "user=-" in rec.message


# ------------------------------------------------- integration: wired to routes

def test_bad_login_is_audited_without_the_password(tmp_path, monkeypatch, caplog):
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    users.create(username="obs@example.com", password="correct-horse-battery",
                 role="admin", email="obs@example.com", enabled=True)
    secret = "wr0ng-PASSWORD-do-not-log-me"
    with TestClient(app) as c, caplog.at_level(logging.WARNING, logger=AUDIT):
        r = c.post("/auth/local",
                   json={"username": "obs@example.com", "password": secret})
    assert r.status_code == 401
    audit_lines = [r.message for r in caplog.records if r.name == AUDIT]
    assert any("auth login deny" in m and "reason=bad_credentials" in m
               for m in audit_lines)
    # the supplied password must never appear anywhere in the audit output
    assert all(secret not in m for m in audit_lines)


def test_good_login_is_audited(tmp_path, monkeypatch, caplog):
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    users.create(username="obs2@example.com", password="correct-horse-battery",
                 role="admin", email="obs2@example.com", enabled=True)
    with TestClient(app) as c, caplog.at_level(logging.INFO, logger=AUDIT):
        r = c.post("/auth/local",
                   json={"username": "obs2@example.com",
                         "password": "correct-horse-battery"})
    assert r.status_code == 200, r.text
    audit_lines = [r.message for r in caplog.records if r.name == AUDIT]
    assert any("auth login ok" in m and "user=obs2@example.com" in m
               for m in audit_lines)
