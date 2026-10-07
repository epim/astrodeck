# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Remote people management behind a fresh sign-in (#685, WP-105).

Since 173cc996 every ``/api/users`` request was refused ``403 local_only`` on a
relayed session, so an admin off the LAN could not list, add or change anyone.
The owner's requirement (2026-10-02, quoted on #685) is that user management
must not be LAN-only. The fence exists because a relay can observe and REPLAY a
signed cookie, so the replacement has to keep that property: a mutation over the
relay needs a sign-in less than ``STEP_UP_MAX_AGE_S`` old, which a replayed
cookie cannot have, and the operations a relayed admin may perform are narrowed
to the ones that cannot mint more authority than the cookie already held.

Backlog ruling (orchestrator, from the owner's stated requirement):

* every MUTATION needs a sign-in under 300 s old; the list read needs none;
* a relayed admin may create Google-only (password-less) viewer or operator
  accounts, change a non-admin's role between viewer and operator and its
  enabled flag, and delete non-admins;
* password reset, any admin grant or edit of an admin, and username/email edits
  stay LAN-only (403 ``local_only``);
* the middleware is a fail-closed ALLOW-list for ``/api/users``;
  ``/api/discover`` stays fenced.

Every denial is tested, and the LAN path is tested unchanged beside each one.

Named mutants, each run from a byte backup under
``pytest -n 0 tests/test_w15_remote_users_step_up.py`` and restored
byte-identically (sha256 compared) on 2026-10-07. What each one made the suite
print, verbatim:

* STEP_UP_WINDOW_OFF -- deps.py ``if age > STEP_UP_MAX_AGE_S or ...`` became
  ``if age >= 10**9 or ...``. 5 failed: ``test_stale_cookie_is_refused_step_up_required``
  (``assert (200 == 403)``), ``test_step_up_window_edges``,
  ``test_a_google_signin_is_a_step_up_too``,
  ``test_signing_in_again_over_the_relay_clears_the_gate`` and
  ``test_require_recent_signin_unit`` (``Failed: DID NOT RAISE <class
  'fastapi.exceptions.HTTPException'>``).
* ADMIN_GRANT_ALLOWED -- local_routes.py ``body.role not in _RELAY_ROLES``
  became ``body.role not in ROLES``. 1 failed:
  ``test_relayed_admin_cannot_grant_admin_or_edit_an_admin`` (``assert 200 == 403``,
  body ``"role":"admin"``).
* USERS_ALLOWLIST_IS_A_PREFIX -- app.py ``_remote_fence_denies`` gained
  ``path.startswith("/api/users") or`` ahead of the allow-list rows. 1 failed:
  ``test_users_allow_list_is_fail_closed`` (``AssertionError: ('put',
  '/api/users/<id>', 405)``, ``assert 405 == 403``). The password route stays
  refused under this mutant, because the explicit LAN-only pattern is checked
  BEFORE the allow-list; that guard has its own mutant, next.
* PASSWORD_DENY_DROPPED -- ``_REMOTE_LOCAL_ONLY_PATTERNS`` became ``()``. 1
  failed: ``test_password_route_cannot_be_reopened_by_the_allow_list``
  (``assert False`` where ``False = _remote_fence_denies('POST',
  '/api/users/abc/password')``).

Further mutants, one failing test named each:

* PROVIDER_DROPS_IAT (providers.py ``iat=iat`` -> ``iat=None``): 13 failed, first
  ``assert 403 == 200`` (every fresh cookie answers ``step_up_required``).
* PATCH_HAS_NO_STEP_UP / POST_HAS_NO_STEP_UP / DELETE_HAS_NO_STEP_UP (the route's
  ``_ADMIN_USERS_FRESH`` -> ``_ADMIN_USERS``): ``test_stale_cookie_is_refused_step_up_required``
  fails on ``assert (200 == 403)`` / ``(201 == 403)`` / ``(200 == 403)``.
* CREATE_PASSWORD_ALLOWED: ``test_relayed_create_with_a_password_is_local_only``
  (``assert 201 == 403``). ADMIN_TARGET_EDITABLE, EMAIL_EDIT_ALLOWED and
  DELETE_ADMIN_ALLOWED: ``test_relayed_admin_cannot_grant_admin_or_edit_an_admin``
  / ``test_relayed_admin_cannot_edit_username_or_email`` (``assert (200 == 403)``).
* STEP_UP_ANY_AUTHN (``principal.authn not in (...)`` dropped):
  ``test_break_glass_cookie_is_not_a_step_up`` and ``test_require_recent_signin_unit``.
  STEP_UP_FUTURE_IAT_ACCEPTED (``age < -skew`` dropped): ``test_require_recent_signin_unit``.
* AUDIT_STEP_UP_DROPPED, AUDIT_PATCH_SUCCESS_DROPPED and AUDIT_LAN_ALSO_AUDITED:
  ``test_relayed_user_changes_and_refusals_leave_audit_lines``.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import types

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import astrodeck.api.app as app_module
import astrodeck.auth.deps as deps
from astrodeck.auth import reset_active_provider, sign_session
from astrodeck.auth.principal import Principal, principal_for_role
from astrodeck.auth.providers import SessionCookieProvider

from test_local_auth_routes import (SESSION_COOKIE, _make_app, _remote_asgi)

PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _clean_provider():
    reset_active_provider()
    yield
    reset_active_provider()


def _code(resp) -> str | None:
    """The machine code of an error body in EITHER shape the rig uses: the
    middleware answers flat ``{"detail", "code"}``, a route's HTTPException
    nests it under ``detail`` (``ui/src/lib/apiError.ts`` reads both)."""
    body = resp.json()
    if isinstance(body.get("detail"), dict):
        return body["detail"].get("code")
    return body.get("code")


def _cookie(user, *, age_s: float = 0.0, authn: str | None = None) -> str:
    """A signed session cookie for ``user`` minted ``age_s`` seconds ago.

    ``authn`` defaults to the sign-in the account can actually use: a
    Google-only account has no password hash, and the provider refuses a
    ``local`` cookie for one.

    ``sign_session(now=...)`` writes ``iat`` from that clock exactly as a real
    login writes it from the wall clock, and carries no ``exp``, so a stale
    cookie is still a perfectly VALID admin cookie -- which is the replay case
    the fence exists for."""
    authn = authn or ("local" if user.password_hash else "google")
    kwargs = dict(jti=f"j-{user.id}-{int(age_s)}-{authn}", authn=authn,
                  subject=user.id, account_epoch=user.session_epoch,
                  now=time.time() - age_s)
    if authn == "google":
        return sign_session(user.role, email=user.login_email, **kwargs)
    return sign_session(user.role, email=user.email, **kwargs)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """An app with local+google enabled, two admins, an operator and a viewer."""
    app, users, store = _make_app(tmp_path, monkeypatch,
                                  methods=["local", "google"])
    root = users.create(username="root@example.com", password=PASSWORD,
                        role="admin", require_email=True)
    other_admin = users.create(username="second@example.com", password=PASSWORD,
                               role="admin", require_email=True)
    operator = users.create(username="op@example.com", password=PASSWORD,
                            role="operator", require_email=True)
    viewer = users.create(username="view@example.com", password="",
                          role="viewer", require_email=True)
    return types.SimpleNamespace(app=app, users=users, store=store, root=root,
                                 other_admin=other_admin, operator=operator,
                                 viewer=viewer)


@contextlib.contextmanager
def _signed_in(app, user, *, age_s: float, authn: str | None):
    """A client with ``user``'s cookie, minted AFTER the app has started.

    The order is load-bearing: the app's startup arms the real session secret,
    so a cookie signed before it is signed with the public dev secret and every
    request answers 401 -- which would make each "refused" test below pass for
    the wrong reason."""
    with TestClient(app) as c:
        c.cookies.set(SESSION_COOKIE, _cookie(user, age_s=age_s, authn=authn))
        yield c


def _remote(rig, user=None, *, age_s: float = 0.0, authn: str | None = None):
    """A relay-tunnelled client signed in as ``user`` (default: root)."""
    return _signed_in(_remote_asgi(rig.app), user or rig.root,
                      age_s=age_s, authn=authn)


def _lan(rig, user=None, *, age_s: float = 0.0):
    return _signed_in(rig.app, user or rig.root, age_s=age_s, authn=None)


# ------------------------------------------------------------ the step-up gate

def test_fresh_signin_lets_a_relayed_admin_change_a_role(rig):
    with _remote(rig) as c:
        r = c.patch(f"/api/users/{rig.viewer.id}", json={"role": "operator"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "operator"
    assert rig.users.get(rig.viewer.id).role == "operator"


def test_stale_cookie_is_refused_step_up_required(rig):
    """A valid admin cookie minted an hour ago is exactly what a relay can
    replay. It reads the list, and every mutation answers 403 with the code the
    UI turns into SIGN IN AGAIN -- and changes nothing."""
    with _remote(rig, age_s=3600) as c:
        r = c.patch(f"/api/users/{rig.viewer.id}", json={"role": "operator"})
        assert r.status_code == 403, r.text
        assert _code(r) == "step_up_required"
        r = c.post("/api/users", json={"username": "new@example.com",
                                       "role": "viewer"})
        assert r.status_code == 403 and _code(r) == "step_up_required"
        r = c.delete(f"/api/users/{rig.viewer.id}")
        assert r.status_code == 403 and _code(r) == "step_up_required"
    assert rig.users.get(rig.viewer.id).role == "viewer"
    assert rig.users.get_by_email("new@example.com") is None


def test_the_list_read_needs_no_step_up(rig):
    """Reading the list is not gated by a fresh sign-in: an admin cookie already
    reads every site-derived value, and the fence protects trust roots."""
    with _remote(rig, age_s=3600) as c:
        r = c.get("/api/users")
    assert r.status_code == 200, r.text
    rows = r.json()["users"]
    assert {u["username"] for u in rows} >= {"root@example.com", "view@example.com"}
    assert all("password_hash" not in u for u in rows)


def test_step_up_window_edges(rig):
    with _remote(rig, age_s=240) as c:
        assert c.patch(f"/api/users/{rig.viewer.id}",
                       json={"enabled": False}).status_code == 200
    with _remote(rig, age_s=360) as c:
        r = c.patch(f"/api/users/{rig.viewer.id}", json={"enabled": True})
        assert r.status_code == 403 and _code(r) == "step_up_required"


def test_a_google_signin_is_a_step_up_too(rig):
    google_admin = rig.users.create(username="g@example.com", password="",
                                    role="admin", require_email=True)
    with _remote(rig, google_admin, authn="google") as c:
        r = c.patch(f"/api/users/{rig.viewer.id}", json={"role": "operator"})
        assert r.status_code == 200, r.text
    with _remote(rig, google_admin, authn="google", age_s=3600) as c:
        r = c.patch(f"/api/users/{rig.viewer.id}", json={"role": "viewer"})
        assert r.status_code == 403 and _code(r) == "step_up_required"


def test_break_glass_cookie_is_not_a_step_up(rig):
    """The admin-token session is minted by a route the fence keeps LAN-only, so
    a copy of it over the relay is not evidence of a sign-in made over it."""
    store = rig.store
    token = "tok-secret-123-0123456789-abcdef"
    store.cfg().auth = store.cfg().auth.model_copy(update={"admin_token": token})
    from astrodeck.auth.session import credential_fingerprint
    with TestClient(_remote_asgi(rig.app)) as c:
        cookie = sign_session("admin", jti="j-bg", authn="admin_token",
                              credential_tag=credential_fingerprint(token),
                              now=time.time())
        c.cookies.set(SESSION_COOKIE, cookie)
        assert c.get("/api/users").status_code == 200
        r = c.patch(f"/api/users/{rig.viewer.id}", json={"role": "operator"})
    assert r.status_code == 403 and _code(r) == "step_up_required"


def test_signing_in_again_over_the_relay_clears_the_gate(rig):
    """The whole loop the UI drives: stale cookie -> step_up_required ->
    POST /auth/local mints a fresh cookie -> the held action goes through."""
    with _remote(rig, age_s=3600) as c:
        r = c.patch(f"/api/users/{rig.viewer.id}", json={"role": "operator"})
        assert r.status_code == 403 and _code(r) == "step_up_required"
        login = c.post("/auth/local", json={"username": "root@example.com",
                                            "password": PASSWORD})
        assert login.status_code == 200, login.text
        r = c.patch(f"/api/users/{rig.viewer.id}", json={"role": "operator"})
        assert r.status_code == 200, r.text


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/users", None),
    ("post", "/api/users", {"username": "n@example.com", "role": "viewer"}),
    ("patch", "/api/users/{viewer}", {"role": "operator"}),
    ("delete", "/api/users/{viewer}", None),
])
def test_a_relayed_operator_or_viewer_is_refused_on_capability(
        rig, method, path, body):
    """A fresh cookie does not widen a role: the capability check runs first, so
    a relayed operator or viewer is 403 ``capability not held`` -- never
    ``step_up_required``, which would invite a sign-in that cannot help.

    Both a fresh and a STALE cookie are tried. The stale one is what pins the
    ORDER of the two dependencies: with ``require_recent_signin`` ahead of
    ``require(admin.users)`` a fresh cookie still answers capability (it passes
    the step-up first), so only a stale cookie shows which check ran first.
    Named mutant CAP_AFTER_STEP_UP (``_ADMIN_USERS_FRESH`` reordered so the
    step-up runs first), run 2026-10-07 from a byte backup and restored
    byte-identically: 3 failed (the post, patch and delete rows; the list read
    has no step-up), each ``assert 'step_up_required' is None`` where
    ``'step_up_required' = _code(<Response [403 Forbidden]>)`` for the stale
    operator. Before this check the mutant passed all 27 tests."""
    for who in (rig.operator, rig.viewer):
        for age_s in (0, 3600):
            with _remote(rig, who, age_s=age_s) as c:
                kw = {"json": body} if body is not None else {}
                r = getattr(c, method)(path.format(viewer=rig.viewer.id), **kw)
            assert r.status_code == 403, (who.role, age_s, r.text)
            assert _code(r) is None, (who.role, age_s, r.text)
            assert r.json()["detail"] == "capability not held", (who.role, age_s)
    assert rig.users.get(rig.viewer.id) is not None
    assert rig.users.get_by_email("n@example.com") is None


# ------------------------------------------------ what a relayed admin may do

def test_relayed_create_without_a_password_is_a_google_only_account(rig):
    with _remote(rig) as c:
        r = c.post("/api/users", json={"username": "guest@example.com",
                                       "role": "operator"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "operator" and body["has_password"] is False
    assert rig.users.get_by_email("guest@example.com") is not None


def test_relayed_create_with_a_password_is_local_only(rig):
    for password in (PASSWORD, "   "):
        with _remote(rig) as c:
            r = c.post("/api/users", json={"username": "pw@example.com",
                                           "password": password,
                                           "role": "viewer"})
        assert r.status_code == 403, r.text
        assert _code(r) == "local_only"
    assert rig.users.get_by_email("pw@example.com") is None


@pytest.mark.parametrize("role", ["admin", "syncer", "root"])
def test_relayed_create_may_only_make_viewer_or_operator(rig, role):
    with _remote(rig) as c:
        r = c.post("/api/users", json={"username": "r@example.com",
                                       "role": role})
    assert r.status_code in (400, 403), r.text
    if r.status_code == 403:
        assert _code(r) == "local_only"
    assert rig.users.get_by_email("r@example.com") is None


def test_relayed_admin_can_disable_and_delete_a_non_admin(rig):
    with _remote(rig) as c:
        r = c.patch(f"/api/users/{rig.operator.id}", json={"enabled": False})
        assert r.status_code == 200 and r.json()["enabled"] is False
        r = c.delete(f"/api/users/{rig.operator.id}")
        assert r.status_code == 200, r.text
    assert rig.users.get(rig.operator.id) is None


def test_relayed_admin_cannot_grant_admin_or_edit_an_admin(rig):
    """The content rules, one denial each, every one 403 ``local_only`` and none
    of them touching the store."""
    before = {u.id: (u.role, u.enabled, u.username) for u in rig.users.list()}
    with _remote(rig) as c:
        # grant admin to a non-admin
        r = c.patch(f"/api/users/{rig.viewer.id}", json={"role": "admin"})
        assert r.status_code == 403, r.text
        assert _code(r) == "local_only"
        # an unknown role is not a way around the rule either
        r = c.patch(f"/api/users/{rig.viewer.id}", json={"role": "syncer"})
        assert r.status_code == 403 and _code(r) == "local_only"
        # edit ANOTHER admin: demote, disable, delete
        for body in ({"role": "viewer"}, {"enabled": False}):
            r = c.patch(f"/api/users/{rig.other_admin.id}", json=body)
            assert r.status_code == 403 and _code(r) == "local_only", body
        r = c.delete(f"/api/users/{rig.other_admin.id}")
        assert r.status_code == 403 and _code(r) == "local_only"
        # edit YOURSELF (the caller is an admin, so it is also an admin target)
        r = c.patch(f"/api/users/{rig.root.id}", json={"enabled": False})
        assert r.status_code == 403 and _code(r) == "local_only"
        r = c.delete(f"/api/users/{rig.root.id}")
        assert r.status_code == 403 and _code(r) == "local_only"
    after = {u.id: (u.role, u.enabled, u.username) for u in rig.users.list()}
    assert after == before


def test_relayed_admin_cannot_edit_username_or_email(rig):
    """An email change retargets a Google identity, so it is the same class as
    an admin grant."""
    with _remote(rig) as c:
        for body in ({"username": "renamed@example.com"},
                     {"email": "elsewhere@example.com"},
                     {"role": "operator", "email": "elsewhere@example.com"}):
            r = c.patch(f"/api/users/{rig.viewer.id}", json=body)
            assert r.status_code == 403 and _code(r) == "local_only", body
    row = rig.users.get(rig.viewer.id)
    assert row.username == "view@example.com" and row.role == "viewer"


def test_relayed_password_reset_is_local_only(rig):
    """Refused by the MIDDLEWARE, so it does not even reach a capability check:
    a fresh admin cookie, a stale one and an anonymous caller all see the same
    403 ``local_only``."""
    with _remote(rig) as c:
        r = c.post(f"/api/users/{rig.operator.id}/password",
                   json={"password": "a-brand-new-password"})
        assert r.status_code == 403 and _code(r) == "local_only"
    with _remote(rig, age_s=3600) as c:
        r = c.post(f"/api/users/{rig.operator.id}/password",
                   json={"password": "a-brand-new-password"})
        assert r.status_code == 403 and _code(r) == "local_only"
    assert rig.users.verify("op@example.com", PASSWORD) is not None
    assert rig.users.verify("op@example.com", "a-brand-new-password") is None


def test_users_allow_list_is_fail_closed(rig):
    """Only the four named (method, path) pairs pass the middleware. Everything
    else under /api/users answers 403 ``local_only`` from the FENCE, before the
    router can say 405 or 404 -- so a route added to /api/users later is LAN-only
    until somebody lists it."""
    vid = rig.viewer.id
    denied = [
        ("put", f"/api/users/{vid}"),
        ("delete", "/api/users"),
        ("patch", "/api/users"),
        ("post", f"/api/users/{vid}"),
        ("get", f"/api/users/{vid}"),
        ("post", "/api/users/"),
        ("get", "/api/users/"),
        ("patch", f"/api/users/{vid}/"),
        ("patch", f"/api/users/{vid}/anything"),
        ("delete", f"/api/users/{vid}/password"),
        ("get", f"/api/users/{vid}/password"),
        ("post", f"/api/users/{vid}/sessions"),
        ("head", "/api/users"),
        ("options", "/api/users"),
    ]
    with _remote(rig) as c:
        for method, path in denied:
            r = getattr(c, method)(path)
            assert r.status_code == 403, (method, path, r.status_code)
            if method not in ("head", "options"):
                assert _code(r) == "local_only", (method, path, r.text)


def test_other_fenced_routes_stay_fenced_for_a_fresh_admin(rig):
    with _remote(rig) as c:
        for method, path in [("get", "/api/discover"),
                             ("get", "/api/discover/nina"),
                             ("post", "/api/config"),
                             ("post", "/api/profiles"),
                             ("post", "/api/connect/rig"),
                             ("post", "/api/auth/config"),
                             ("post", "/auth/token"),
                             ("post", "/api/remote/config"),
                             ("post", "/api/system/factory-reset")]:
            r = getattr(c, method)(path, **({} if method == "get" else
                                            {"json": {}}))
            assert r.status_code == 403, (method, path, r.status_code)
            assert _code(r) == "local_only", (method, path, r.text)


def test_password_route_cannot_be_reopened_by_the_allow_list(rig, monkeypatch):
    """Defence in depth: the password route has its own entry in the LAN-only
    patterns, checked BEFORE the allow-list, so widening the allow-list (the way
    a careless ``/api/users/.*`` would) still does not reopen it."""
    wide = tuple((m, re.compile(r"^/api/users(/.*)?$"))
                 for m in ("GET", "POST", "PATCH", "DELETE", "PUT"))
    monkeypatch.setattr(app_module, "_REMOTE_ALLOWED_ROUTES", wide)
    assert app_module._remote_fence_denies("POST", "/api/users/abc/password")
    # the widened list does reopen an ordinary sub-route, which is what makes
    # the assertion above a statement about the pattern and not about the list.
    assert not app_module._remote_fence_denies("PUT", "/api/users/abc")


# ------------------------------------------------------------------- audit trail

def _audit_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "astrodeck.audit"]


def test_relayed_user_changes_and_refusals_leave_audit_lines(rig, caplog):
    """A replayed cookie that tries to change people is the event this whole gate
    exists for, so it has to leave a trace: the step-up refusal, a refused
    content rule and each change that went through, all on ``astrodeck.audit``.
    A change made on the LAN is not a relay event and leaves none of these."""
    with caplog.at_level(logging.INFO, logger="astrodeck.audit"):
        with _remote(rig, age_s=3600) as c:
            c.patch(f"/api/users/{rig.viewer.id}", json={"role": "operator"})
        with _remote(rig) as c:
            c.post("/api/users", json={"username": "a@example.com", "role": "viewer"})
            c.patch(f"/api/users/{rig.other_admin.id}", json={"enabled": False})
            c.patch(f"/api/users/{rig.viewer.id}", json={"enabled": False})
            c.delete(f"/api/users/{rig.operator.id}")
        relayed = _audit_lines(caplog)
        with _lan(rig, age_s=3600) as c:
            c.patch(f"/api/users/{rig.viewer.id}", json={"enabled": True})
        assert _audit_lines(caplog) == relayed, "a LAN change was audited as a relay event"
    assert any("auth step_up deny" in m and "reason=step_up_required" in m
               for m in relayed), relayed
    assert any("auth user_admin_relay ok" in m and "reason=create:viewer" in m
               and "user=a@example.com" in m for m in relayed), relayed
    assert any("auth user_admin_relay deny" in m and "reason=patch:local_only" in m
               for m in relayed), relayed
    assert any("auth user_admin_relay ok" in m and "reason=patch" in m
               and "user=view@example.com" in m for m in relayed), relayed
    assert any("auth user_admin_relay ok" in m and "reason=delete" in m
               and "user=op@example.com" in m for m in relayed), relayed


# ------------------------------------------------------------- LAN is unchanged

def test_the_same_requests_on_the_lan_behave_as_before(rig):
    """No step-up and no content rule applies off the relay: a stale cookie, a
    password, an admin grant and a password reset all still work on the LAN."""
    with _lan(rig, age_s=3600) as c:
        r = c.post("/api/users", json={"username": "lan@example.com",
                                       "password": PASSWORD, "role": "viewer"})
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        r = c.patch(f"/api/users/{uid}", json={"role": "admin"})
        assert r.status_code == 200 and r.json()["role"] == "admin"
        r = c.patch(f"/api/users/{uid}", json={"username": "lan2@example.com"})
        assert r.status_code == 200, r.text
        r = c.post(f"/api/users/{uid}/password", json={"password": "new-password-1"})
        assert r.status_code == 200, r.text
        r = c.delete(f"/api/users/{uid}")
        assert r.status_code == 200, r.text


# ------------------------------------------------------- the pieces underneath

def _req(*, remote: bool) -> Request:
    scope = {"type": "http", "method": "PATCH", "path": "/api/users/x",
             "headers": [], "query_string": b""}
    if remote:
        scope["state"] = {"astrodeck_remote": True}
    return Request(scope)


def _principal(**kw) -> Principal:
    base = principal_for_role("admin", email="a@example.com")
    return Principal(role=base.role, email=base.email, caps=base.caps, **kw)


def test_require_recent_signin_unit(monkeypatch):
    now = 1_900_000_000
    monkeypatch.setattr(deps, "time", types.SimpleNamespace(time=lambda: float(now)))
    # LAN: never gated, whatever the principal carries
    deps.require_recent_signin(_req(remote=False), _principal())
    # inside the window, both sign-in kinds
    for authn in ("local", "google"):
        deps.require_recent_signin(
            _req(remote=True), _principal(authn=authn, iat=now - 299))
    # exactly at the limit is still fresh; one second over is not
    deps.require_recent_signin(
        _req(remote=True), _principal(authn="local", iat=now - deps.STEP_UP_MAX_AGE_S))
    for bad in (_principal(authn="local", iat=now - deps.STEP_UP_MAX_AGE_S - 1),
                _principal(authn="local", iat=None),
                _principal(authn=None, iat=now),
                _principal(authn="admin_token", iat=now),
                _principal(authn="local", iat=now + 3600)):
        with pytest.raises(HTTPException) as exc:
            deps.require_recent_signin(_req(remote=True), bad)
        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == "step_up_required"


def test_session_provider_carries_authn_and_iat(rig):
    """``sign_session`` has always written both claims; the Principal now keeps
    them so the gate can read them, and they stay out of the public view."""
    issued = int(time.time()) - 42
    with TestClient(rig.app):  # startup arms the real session secret
        token = sign_session("admin", email=rig.root.email, jti="j",
                             authn="local", subject=rig.root.id,
                             account_epoch=rig.root.session_epoch, now=issued)
        req = Request({"type": "http", "method": "GET", "path": "/",
                       "query_string": b"",
                       "headers": [(b"cookie",
                                    f"{SESSION_COOKIE}={token}".encode())]})
        p = asyncio.run(SessionCookieProvider().resolve(req))
    assert p is not None and p.authn == "local" and p.iat == issued
    assert set(p.to_public()) == {"role", "email", "caps"}
    # the open-default principals carry neither, so they can never pass the gate
    assert principal_for_role("admin").authn is None
    assert principal_for_role("admin").iat is None
