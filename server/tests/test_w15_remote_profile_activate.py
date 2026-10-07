# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Activating a saved profile over the relay, without force (#685, WP-105).

``POST /api/profiles/{id}/activate`` is the documented way to reconnect the
whole rig (it is how an interrupted night is brought back), and the relay fence
refused it for every role because ``/api/profiles`` is a mutation prefix. The
carve-out is exactly one route, and it is safe to open for the reason the issue
asked about: the route takes a SAVED profile id and nothing else, the content of
a profile can only be written by the still-fenced profile routes, so a relayed
caller has no destination to choose. ``force`` is the exception, because it
aborts a running sequence and disarms auto-resume: a decision made at the rig,
refused 403 ``local_only`` over the relay.

Backlog ruling (orchestrator, from the owner's stated requirement): activate is
allowed over the relay WITHOUT force; saving, editing or deleting profiles,
``/apply``, ``/api/connect/*`` and ``/api/discover`` stay fenced; a relayed
caller gets nothing beyond what its capabilities already allow.

Named mutants, each run from a byte backup under
``pytest -n 0 tests/test_w15_remote_profile_activate.py`` and restored
byte-identically (sha256 compared) on 2026-10-07. What each one made the suite
print, verbatim:

* ACTIVATE_STILL_FENCED -- the allow-list row's pattern became
  ``^/api/profiles/[^/]+/activate-disabled$``, which never matches (the row is
  effectively gone). 6 failed, first
  ``test_relayed_admin_can_activate_a_saved_profile``: ``AssertionError:
  {"detail":"this security-sensitive operation is LAN-only","code":"local_only"}``
  / ``assert 403 == 200``.
* FORCE_ALLOWED_OVER_RELAY -- ``if force and _scope_is_remote(request):`` in
  ``activate_profile`` became ``if False:``. 1 failed:
  ``test_relayed_force_is_local_only_and_touches_nothing``: ``AssertionError:
  {"started":"profile"}`` / ``assert 200 == 403`` (the run is stopped and the rig
  reconnects).
* ALLOW_ROW_IS_A_PREFIX -- the row's pattern became ``^/api/profiles/[^/]+/`` so
  every sub-route of a profile passes. 3 failed:
  ``test_the_other_profile_and_connect_routes_stay_fenced`` (``('post',
  '/api/profiles/<id>/apply', 200, '{"started":"profile"}')`` / ``assert 200 ==
  403``) and two rows of ``test_fence_decision_table`` (``apply``, and ``activate/``
  with a trailing slash).
"""
from __future__ import annotations

import time
import types

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import reset_active_provider, sign_session
from astrodeck.profiles import Profile

from test_local_auth_routes import (SESSION_COOKIE, _make_app, _remote_asgi)

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _clean_provider():
    reset_active_provider()
    yield
    reset_active_provider()


class _FakeRunningTask:
    """Reports as still running, so ``engine.running`` is True without a real
    sequence (same stand-in ``test_activate_profile.py`` uses)."""

    def done(self) -> bool:
        return False


def _code(resp) -> str | None:
    body = resp.json()
    if isinstance(body.get("detail"), dict):
        return body["detail"].get("code")
    return body.get("code")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    app, users, store = _make_app(tmp_path, monkeypatch, methods=["local"])
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")
    admin = users.create(username="root@example.com", password=PASSWORD,
                         role="admin", require_email=True)
    operator = users.create(username="op@example.com", password=PASSWORD,
                            role="operator", require_email=True)
    prof = Profile(name="Sim Rig", primary_backend="sim")
    profile_lib.save(prof)
    return types.SimpleNamespace(app=app, store=store, admin=admin,
                                 operator=operator, pid=prof.id)


def _cookie(user) -> str:
    return sign_session(user.role, email=user.email, jti=f"j-{user.id}",
                        authn="local", subject=user.id,
                        account_epoch=user.session_epoch, now=time.time())


def _client(rig, user, *, remote: bool):
    """Sign in AFTER startup: the app arms the real session secret when it
    starts, and a cookie signed earlier is refused 401 (which would make every
    'refused' test here pass for the wrong reason)."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        with TestClient(_remote_asgi(rig.app) if remote else rig.app) as c:
            if user is not None:
                c.cookies.set(SESSION_COOKIE, _cookie(user))
            yield c
    return _cm()


def _wait_active(store, pid, c) -> None:
    for _ in range(200):
        if store.cfg().active_profile_id == pid:
            return
        c.get("/api/status")
        time.sleep(0.02)


class _Spy:
    """Records what the activate route would do to the rig, and does none of it."""

    def __init__(self, monkeypatch):
        self.calls: list[str] = []
        spy = self

        async def _abort():
            spy.calls.append("abort")

        def _stop_recovery(*a, **kw):
            spy.calls.append("stop_recovery")

        async def _connect(profile_id):
            spy.calls.append("connect")
            return {}

        monkeypatch.setattr(app_module.engine, "abort", _abort)
        monkeypatch.setattr(app_module.resume_arm, "stop_recovery", _stop_recovery)
        monkeypatch.setattr(app_module.hub, "connect_profile_id", _connect)


def test_relayed_admin_can_activate_a_saved_profile(rig):
    with _client(rig, rig.admin, remote=True) as c:
        r = c.post(f"/api/profiles/{rig.pid}/activate")
        assert r.status_code == 200, r.text
        assert r.json().get("started") == "profile"
        _wait_active(rig.store, rig.pid, c)
    assert rig.store.cfg().active_profile_id == rig.pid


def test_an_explicit_force_false_is_the_same_unforced_request(rig, monkeypatch):
    spy = _Spy(monkeypatch)
    with _client(rig, rig.admin, remote=True) as c:
        r = c.post(f"/api/profiles/{rig.pid}/activate", json={"force": False})
        assert r.status_code == 200, r.text
        deadline = time.monotonic() + 10
        while "connect" not in spy.calls and time.monotonic() < deadline:
            c.get("/api/status")  # lets the spawned connect run on the loop
            time.sleep(0.02)
    assert spy.calls == ["connect"]


def test_relayed_force_is_local_only_and_touches_nothing(rig, monkeypatch):
    """Force stops the running sequence and disarms auto-resume, so over the
    relay it is refused BEFORE any of that happens, whether or not a run is
    active and whether or not the id exists."""
    spy = _Spy(monkeypatch)
    monkeypatch.setattr(app_module.engine, "_task", _FakeRunningTask())
    with _client(rig, rig.admin, remote=True) as c:
        for pid in (rig.pid, "does-not-exist"):
            r = c.post(f"/api/profiles/{pid}/activate", json={"force": True})
            assert r.status_code == 403, r.text
            assert _code(r) == "local_only"
    assert spy.calls == []
    assert rig.store.cfg().active_profile_id != rig.pid


def test_relayed_unforced_activate_still_409s_while_a_run_is_active(rig, monkeypatch):
    spy = _Spy(monkeypatch)
    monkeypatch.setattr(app_module.engine, "_task", _FakeRunningTask())
    with _client(rig, rig.admin, remote=True) as c:
        r = c.post(f"/api/profiles/{rig.pid}/activate")
    assert r.status_code == 409, r.text
    assert _code(r) == "running"
    assert spy.calls == []


def test_force_still_works_on_the_lan(rig, monkeypatch):
    spy = _Spy(monkeypatch)
    monkeypatch.setattr(app_module.engine, "_task", _FakeRunningTask())
    with _client(rig, rig.admin, remote=False) as c:
        r = c.post(f"/api/profiles/{rig.pid}/activate", json={"force": True})
        assert r.status_code == 200, r.text
    assert "stop_recovery" in spy.calls and "abort" in spy.calls


def test_the_other_profile_and_connect_routes_stay_fenced(rig):
    pid = rig.pid
    fenced = [
        ("post", f"/api/profiles/{pid}/apply", {}),
        ("post", f"/api/profiles/{pid}/apply", {"force": False}),
        ("post", "/api/profiles", {"name": "x"}),
        ("post", "/api/profiles/capture", {}),
        ("patch", f"/api/profiles/{pid}", {"name": "renamed"}),
        ("delete", f"/api/profiles/{pid}", None),
        ("post", f"/api/profiles/{pid}/clear-overrides", {}),
        ("post", f"/api/profiles/{pid}/set-providers", {"providers": {}}),
        ("post", "/api/connect/rig", {}),
        ("post", "/api/connect/nina", {}),
        ("get", "/api/discover", None),
        ("get", "/api/discover/alpaca", None),
        # same path, other verbs and spellings: not the allow-listed row
        ("delete", f"/api/profiles/{pid}/activate", None),
        ("put", f"/api/profiles/{pid}/activate", {}),
        ("patch", f"/api/profiles/{pid}/activate", {}),
        ("post", f"/api/profiles/{pid}/activate/", {}),
        ("post", f"/api/profiles/{pid}/activate/extra", {}),
        ("post", "/api/profiles//activate", {}),
    ]
    with _client(rig, rig.admin, remote=True) as c:
        for method, path, body in fenced:
            kw = {} if body is None else {"json": body}
            r = getattr(c, method)(path, **kw)
            assert r.status_code == 403, (method, path, r.status_code, r.text)
            assert _code(r) == "local_only", (method, path, r.text)
    assert rig.store.cfg().active_profile_id != pid
    # nothing was deleted or renamed by a request that slipped through
    from astrodeck.profiles import profiles as profile_lib
    assert profile_lib.get(pid).name == "Sim Rig"


def test_a_relayed_operator_gets_nothing_a_role_did_not_hold(rig, monkeypatch):
    """The carve-out opens the FENCE, not a capability: activate still needs
    ``config.backend``, which the operator role does not hold, so a relayed
    operator is told 403 ``capability not held`` -- not ``local_only``, and the
    rig is not touched. (Letting operators reconnect gear is a capability
    decision, tracked on the work package, not something a fence can grant.)"""
    spy = _Spy(monkeypatch)
    with _client(rig, rig.operator, remote=True) as c:
        r = c.post(f"/api/profiles/{rig.pid}/activate")
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "capability not held"
    assert spy.calls == []


def test_an_unauthenticated_relayed_activate_is_401(rig):
    with _client(rig, None, remote=True) as c:
        r = c.post(f"/api/profiles/{rig.pid}/activate")
    assert r.status_code == 401, r.text


@pytest.mark.parametrize("method,path,denied", [
    ("POST", "/api/profiles/abc/activate", False),
    ("POST", "/api/profiles/abc/apply", True),
    ("POST", "/api/profiles", True),
    ("GET", "/api/profiles", False),
    ("GET", "/api/profiles/abc", False),
    ("POST", "/api/profiles/abc/activate/", True),
    ("GET", "/api/profiles/abc/activate", False),   # a read is not fenced anyway
    ("DELETE", "/api/profiles/abc/activate", True),
    ("POST", "/api/connect/rig", True),
    ("GET", "/api/discover", True),
])
def test_fence_decision_table(method, path, denied):
    assert app_module._remote_fence_denies(method, path) is denied
