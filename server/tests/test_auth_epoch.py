"""Session-epoch invalidation on the auth-enable transition (R4B-AUTH-01).

Live-verified root cause (review 4b): a browser tab that signed in during an
EARLIER auth-enabled period kept its ``ad_session`` admin cookie through an
auth-off interval; re-enabling authentication reused the persisted signing
secret, so that old cookie still verified and the tab retained full admin --
even after a hard reload -- while a cookie-less fresh client correctly got 401.

Fix under test: ``AuthConfig.session_epoch`` advances on every OFF -> ON
methods transition (``ConfigStore.set_auth``); tokens carry an ``epoch`` claim
(``sign_session``); ``SessionCookieProvider`` rejects claims below the
configured floor. So enabling authentication starts a fresh session epoch and
NOTHING minted before it carries authority.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.auth.local_routes as local_routes
import astrodeck.auth.routes as auth_routes
import astrodeck.auth.users as users_mod
from astrodeck.auth import (CAP_VIEW_STATUS, Principal, build_provider,
                            require, reset_active_provider,
                            set_active_provider, sign_session)
from astrodeck.auth.providers import SessionCookieProvider
from astrodeck.auth.users import UserStore
from astrodeck.config import AuthConfig, ConfigStore

SESSION_COOKIE = "ad_session"


@pytest.fixture(autouse=True)
def _clean_provider():
    reset_active_provider()
    yield
    reset_active_provider()


def _make_app(tmp_path, monkeypatch, *, auth: AuthConfig):
    """Isolated app: temp config store + temp user store, seeded with ``auth``
    (mirrors test_local_auth_routes fixtures)."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    temp_store.cfg().auth = auth

    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(local_routes, "config_store", temp_store)
    monkeypatch.setattr(auth_routes, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")

    temp_users = UserStore(path=tmp_path / "users.json")
    monkeypatch.setattr(users_mod, "user_store", temp_users)
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)

    app = app_module.create_app()
    return app, temp_users, temp_store


# ------------------------------------------------ the exact review-4b repro seam

def test_provider_none_plus_methods_local_unauthenticated_is_401(
        tmp_path, monkeypatch):
    """The on-disk shape the review flagged: BOTH the legacy ``provider:
    "none"`` AND ``methods: ["local"]``. ``methods`` is the source of truth, so
    a request with no session cookie must get 401 -- never the open-admin
    default (no split-brain between the two fields)."""
    auth = AuthConfig(provider="none", methods=["local"],
                      local_enabled_first_run=False)
    app, _, _ = _make_app(tmp_path, monkeypatch, auth=auth)
    with TestClient(app) as c:
        assert c.get("/api/me").status_code == 401
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/config").status_code == 401
        # the open, unauthenticated login signal still serves
        m = c.get("/api/auth/methods")
        assert m.status_code == 200 and m.json()["methods"] == ["local"]


def test_stale_epoch_cookie_rejected_after_auth_reenable(tmp_path, monkeypatch):
    """The live repro, end-to-end over the API: a session minted under an
    earlier auth-enabled epoch must NOT survive auth-off -> auth-on."""
    auth = AuthConfig(methods=["local"], local_enabled_first_run=True)
    app, _, _ = _make_app(tmp_path, monkeypatch, auth=auth)
    with TestClient(app) as c:
        # earlier epoch: first-run setup mints an admin session cookie
        r = c.post("/auth/setup/local",
                   json={"username": "root", "password": "hunter2long"})
        assert r.status_code == 200, r.text
        old_cookie = c.cookies.get(SESSION_COOKIE)
        assert old_cookie
        assert c.get("/api/me").json()["role"] == "admin"

        # admin turns authentication OFF (open server) ...
        body = c.get("/api/config").json()["auth"]
        body["methods"] = []
        assert c.post("/api/auth/config", json=body).status_code == 200
        c.cookies.delete(SESSION_COOKIE)
        assert c.get("/api/me").json()["role"] == "admin"  # open default

        # ... and later re-enables it from the open-admin state (no cookie sent)
        body = c.get("/api/config").json()["auth"]
        body["methods"] = ["local"]
        assert c.post("/api/auth/config", json=body).status_code == 200

        # the old-epoch cookie is DEAD: hard-reloaded tab gets 401 like anyone
        c.cookies.set(SESSION_COOKIE, old_cookie)
        assert c.get("/api/me").status_code == 401
        c.cookies.delete(SESSION_COOKIE)
        assert c.get("/api/me").status_code == 401

        # a fresh login works and mints a NEW-epoch session that resolves
        r = c.post("/auth/local",
                   json={"username": "root", "password": "hunter2long"})
        assert r.status_code == 200, r.text
        assert c.get("/api/me").json()["role"] == "admin"


# ------------------------------------------------------- set_auth bump semantics

def test_set_auth_bumps_epoch_only_on_off_to_on(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    assert store.cfg().auth.session_epoch == 0

    # OFF -> ON bumps
    store.set_auth(AuthConfig(methods=["local"]))
    assert store.cfg().auth.session_epoch == 1
    # ON -> ON (settings tweak) does NOT bump -- no mass logout on a TTL change
    store.set_auth(AuthConfig(methods=["local"], session_ttl_s=3600,
                              session_epoch=1))
    assert store.cfg().auth.session_epoch == 1
    # ON -> OFF does not bump (server is open; sessions carry no authority)
    store.set_auth(AuthConfig(methods=[], session_epoch=1))
    assert store.cfg().auth.session_epoch == 1
    # OFF -> ON again bumps from the retained value
    store.set_auth(AuthConfig(methods=["local"], session_epoch=1))
    assert store.cfg().auth.session_epoch == 2


def test_set_auth_client_echo_cannot_lower_epoch(tmp_path):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_auth(AuthConfig(methods=["local"]))
    assert store.cfg().auth.session_epoch == 1
    # a stale client echo carrying epoch 0 is clamped up, never honored down
    store.set_auth(AuthConfig(methods=["local"], session_epoch=0))
    assert store.cfg().auth.session_epoch == 1


# ------------------------------------------------------ provider epoch floor

def _guarded_client(dep, *, cookie: str | None = None) -> TestClient:
    app = FastAPI()

    @app.get("/g")
    async def g(principal: Principal = Depends(dep)):
        return {"role": principal.role}

    c = TestClient(app)
    if cookie is not None:
        c.cookies.set(SessionCookieProvider.COOKIE_NAME, cookie)
    return c


def _install(auth_cfg):
    p = build_provider(auth_cfg)
    set_active_provider(p)
    return p


def test_provider_rejects_below_epoch_accepts_at_or_above():
    _install(AuthConfig(methods=["local"], session_epoch=2))
    below = sign_session("admin", jti="j0", epoch=1)
    at = sign_session("admin", jti="j1", epoch=2)
    above = sign_session("admin", jti="j2", epoch=3)
    dep = require(CAP_VIEW_STATUS)
    assert _guarded_client(dep, cookie=below).get("/g").status_code == 401
    assert _guarded_client(dep, cookie=at).get("/g").status_code == 200
    assert _guarded_client(dep, cookie=above).get("/g").status_code == 200


def test_provider_rejects_legacy_no_epoch_claim_once_bumped():
    # a pre-fix token (no epoch claim) reads as epoch 0 -> dead after any bump
    _install(AuthConfig(methods=["local"], session_epoch=1))
    legacy = sign_session("admin", jti="jl", epoch=0)
    assert _guarded_client(require(CAP_VIEW_STATUS),
                           cookie=legacy).get("/g").status_code == 401


def test_epoch_zero_floor_accepts_everything_byte_for_byte():
    # session_epoch == 0 (every existing deployment): no behavior change
    _install(AuthConfig(methods=["local"]))
    tok = sign_session("operator", email="op@rig", jti="jz", epoch=0)
    assert _guarded_client(require(CAP_VIEW_STATUS),
                           cookie=tok).get("/g").status_code == 200
