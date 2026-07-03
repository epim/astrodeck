"""HTTP surface for local auth + user management + the create-admin CLI (W2.6).

Covers, via FastAPI ``TestClient`` (repo convention: in-process fakes +
``monkeypatch``, NO ``unittest.mock``):

  * default-OPEN is byte-for-byte intact (no methods, no token -> everything
    serves with no creds, and the user-CRUD is reachable as admin);
  * local login: happy path mints a session cookie; wrong password / disabled
    user -> generic 401; local 404 when local is not an enabled method;
  * first-run setup: creates exactly one admin then auto-closes (409);
  * user CRUD is gated by ``admin.users`` (a viewer session -> 403);
  * the ``GET /api/auth/methods`` signal the UI reads;
  * the ``create-admin`` CLI underlying function seeds (and re-seeds) an admin.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.auth.local_routes as local_routes
import astrodeck.auth.routes as auth_routes
import astrodeck.auth.users as users_mod
from astrodeck.auth import reset_active_provider, sign_session
from astrodeck.auth.users import UserStore
from astrodeck.config import AuthConfig, ConfigStore

SESSION_COOKIE = "ad_session"


# --------------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _clean_provider():
    reset_active_provider()
    yield
    reset_active_provider()


def _make_app(tmp_path, monkeypatch, *, methods=None, first_run=True,
              token=None):
    """Build an isolated app: temp config store + temp user store, with the
    requested auth methods configured. The provider is rebuilt from the config
    at create_app() time."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    # Seed the auth config (methods drive local/google enablement).
    auth = AuthConfig(methods=list(methods or []),
                      local_enabled_first_run=first_run)
    temp_store.cfg().auth = auth

    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    # local_routes + the google/logout router read config_store off their own
    # module refs (logout's jti-revoke must hit the temp store, not the real one).
    monkeypatch.setattr(local_routes, "config_store", temp_store)
    monkeypatch.setattr(auth_routes, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")

    # Point the user-store singleton at a temp file so tests never touch
    # server/config/users.json.
    temp_users = UserStore(path=tmp_path / "users.json")
    monkeypatch.setattr(users_mod, "user_store", temp_users)

    if token is None:
        monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(app_module.AUTH_ENV_VAR, token)

    app = app_module.create_app()
    return app, temp_users, temp_store


def _login_cookie(role: str, email: str = "u@x") -> str:
    """A signed session cookie for ``role`` (mirrors what local login mints)."""
    return sign_session(role, email=email, jti=f"j-{role}")


# ----------------------------------------------------- non-breaking default open

def test_default_open_serves_everything_and_user_crud(tmp_path, monkeypatch):
    """No methods, no token: the open default. Every route serves with no creds,
    and the admin-gated user CRUD is reachable (caller == admin)."""
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=[])
    with TestClient(app) as c:
        assert c.get("/api/status").status_code == 200
        # admin.users CRUD is open (caller resolves to admin under no methods)
        assert c.get("/api/users").status_code == 200
        r = c.post("/api/users", json={"username": "v1", "password": "pw",
                                       "role": "viewer"})
        assert r.status_code == 201, r.text
        assert "password_hash" not in r.json()
        # methods signal reports "no login screen"
        m = c.get("/api/auth/methods").json()
        assert m["methods"] == [] and m["first_run"] is False


def test_local_login_404_when_local_not_enabled(tmp_path, monkeypatch):
    app, _, _ = _make_app(tmp_path, monkeypatch, methods=[])
    with TestClient(app) as c:
        r = c.post("/auth/local", json={"username": "x", "password": "y"})
        assert r.status_code == 404


# ----------------------------------------------------------------- local login

def test_local_login_happy_path_sets_cookie(tmp_path, monkeypatch):
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    users.create(username="alice", password="hunter2", role="operator",
                 email="a@rig")
    with TestClient(app) as c:
        r = c.post("/auth/local",
                   json={"username": "alice", "password": "hunter2"})
        assert r.status_code == 200, r.text
        assert r.json() == {"role": "operator", "email": "a@rig"}
        # a session cookie was set
        assert SESSION_COOKIE in r.cookies or SESSION_COOKIE in c.cookies
        # and it actually authenticates a subsequent gated call as operator
        me = c.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "operator"


def test_local_login_wrong_password_is_generic_401(tmp_path, monkeypatch):
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    users.create(username="bob", password="right", role="admin")
    with TestClient(app) as c:
        r = c.post("/auth/local", json={"username": "bob", "password": "WRONG"})
        assert r.status_code == 401
        # generic detail -- no reason leaked
        assert "invalid" in r.json()["detail"].lower()
        # no cookie set on failure
        assert SESSION_COOKIE not in r.cookies


def test_local_login_unknown_user_401(tmp_path, monkeypatch):
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    users.create(username="bob", password="right", role="admin")
    with TestClient(app) as c:
        r = c.post("/auth/local", json={"username": "ghost", "password": "x"})
        assert r.status_code == 401


def test_local_login_disabled_user_denied(tmp_path, monkeypatch):
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    users.create(username="admin1", password="pw", role="admin")  # keep an admin
    u = users.create(username="carol", password="pw", role="operator")
    users.set_enabled(u.id, False)
    with TestClient(app) as c:
        r = c.post("/auth/local", json={"username": "carol", "password": "pw"})
        assert r.status_code == 401  # disabled -> same generic denial


# --------------------------------------------------------------- first-run setup

def test_first_run_creates_one_admin_then_closes(tmp_path, monkeypatch):
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    assert users.is_empty()
    with TestClient(app) as c:
        # the UI signal advertises first-run
        assert c.get("/api/auth/methods").json()["first_run"] is True
        r = c.post("/auth/setup/local",
                   json={"username": "root", "password": "s3cret",
                         "email": "root@rig"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["role"] == "admin" and body["username"] == "root"
        assert "password_hash" not in body
        # logged straight in
        assert SESSION_COOKIE in r.cookies or SESSION_COOKIE in c.cookies
        # store now has one admin; setup is auto-closed
        assert users.is_empty() is False
        again = c.post("/auth/setup/local",
                       json={"username": "root2", "password": "x2"})
        assert again.status_code == 409
        # and the signal no longer advertises first-run
        assert c.get("/api/auth/methods").json()["first_run"] is False


def test_first_run_404_when_local_disabled(tmp_path, monkeypatch):
    app, _, _ = _make_app(tmp_path, monkeypatch, methods=[])
    with TestClient(app) as c:
        r = c.post("/auth/setup/local",
                   json={"username": "root", "password": "pw"})
        assert r.status_code == 404


def test_first_run_404_when_flag_off(tmp_path, monkeypatch):
    app, _, _ = _make_app(tmp_path, monkeypatch, methods=["local"],
                          first_run=False)
    with TestClient(app) as c:
        r = c.post("/auth/setup/local",
                   json={"username": "root", "password": "pw"})
        assert r.status_code == 404


# --------------------------------------------------- W3 remote interlock (relay)

def _remote_asgi(app):
    """Wrap ``app`` so every HTTP request carries the W3 remote scope flag --
    EXACTLY as the relay client stamps a tunneled request (ASGI scope STATE, not a
    forgeable header). Non-http scopes (lifespan) pass straight through."""
    async def _wrapped(scope, receive, send):
        if scope.get("type") == "http":
            scope.setdefault("state", {})["astrodeck_remote"] = True
        await app(scope, receive, send)
    return _wrapped


def test_first_run_setup_denied_over_relay(tmp_path, monkeypatch):
    """W3: first-run admin bootstrap is LAN-ONLY. A relay-tunneled POST is 404'd
    BEFORE the store is touched, so a remote attacker cannot seize the rig during
    the first-run window -- while the SAME request over the LAN still succeeds."""
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    assert users.is_empty()
    # relay-tunneled attempt: hard-denied, store left untouched.
    with TestClient(_remote_asgi(app)) as rc:
        r = rc.post("/auth/setup/local",
                    json={"username": "attacker", "password": "pwned123"})
        assert r.status_code == 404, r.text
    assert users.is_empty(), "remote setup must NOT have created an admin"
    # the SAME request over the LAN (no remote flag) still creates the admin.
    with TestClient(app) as c:
        ok = c.post("/auth/setup/local",
                    json={"username": "root", "password": "s3cret"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["username"] == "root"
    assert users.is_empty() is False


def test_auth_me_remote_denied_on_open_default(tmp_path, monkeypatch):
    """W3: /auth/me must never serve the open ``none`` provider's admin identity
    over the relay. A LAN caller sees admin (open default); a relay-tunneled caller
    is 401 so the SPA shows login instead of learning the rig is fully open."""
    app, _users, _ = _make_app(tmp_path, monkeypatch, methods=[])
    # LAN: the open default resolves to admin.
    with TestClient(app) as c:
        me = c.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "admin"
    # relay-tunneled: hard-denied (never leaks the open-admin identity).
    with TestClient(_remote_asgi(app)) as rc:
        assert rc.get("/auth/me").status_code == 401


# ------------------------------------------------------- user CRUD is admin-gated

def test_user_crud_denied_for_viewer(tmp_path, monkeypatch):
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    users.create(username="root", password="pw", role="admin")
    with TestClient(app) as c:
        c.cookies.set(SESSION_COOKIE, _login_cookie("viewer"))
        # a viewer holds NOT admin.users -> 403 on every CRUD verb
        assert c.get("/api/users").status_code == 403
        assert c.post("/api/users",
                      json={"username": "x", "password": "pw",
                            "role": "viewer"}).status_code == 403


def test_user_crud_allowed_for_admin_session(tmp_path, monkeypatch):
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    users.create(username="root", password="pw", role="admin")
    with TestClient(app) as c:
        c.cookies.set(SESSION_COOKIE, _login_cookie("admin"))
        assert c.get("/api/users").status_code == 200
        r = c.post("/api/users", json={"username": "newbie", "password": "pw",
                                       "role": "operator"})
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        # patch role
        pr = c.patch(f"/api/users/{uid}", json={"role": "viewer"})
        assert pr.status_code == 200 and pr.json()["role"] == "viewer"
        # reset password
        assert c.post(f"/api/users/{uid}/password",
                      json={"password": "new"}).status_code == 200
        # disable
        assert c.patch(f"/api/users/{uid}",
                       json={"enabled": False}).json()["enabled"] is False
        # delete
        assert c.delete(f"/api/users/{uid}").status_code == 200
        assert c.get("/api/users").json()["users"][0]["username"] == "root"


def test_create_duplicate_user_409(tmp_path, monkeypatch):
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    users.create(username="root", password="pw", role="admin")
    with TestClient(app) as c:
        c.cookies.set(SESSION_COOKIE, _login_cookie("admin"))
        r = c.post("/api/users", json={"username": "ROOT", "password": "pw",
                                       "role": "viewer"})
        assert r.status_code == 409


def test_delete_last_admin_409(tmp_path, monkeypatch):
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    root = users.create(username="root", password="pw", role="admin")
    with TestClient(app) as c:
        c.cookies.set(SESSION_COOKIE, _login_cookie("admin"))
        r = c.delete(f"/api/users/{root.id}")
        assert r.status_code == 409
        assert "last admin" in r.json()["detail"]


def test_create_user_too_long_password_422(tmp_path, monkeypatch):
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    users.create(username="root", password="pw", role="admin")
    with TestClient(app) as c:
        c.cookies.set(SESSION_COOKIE, _login_cookie("admin"))
        r = c.post("/api/users", json={"username": "big", "role": "viewer",
                                       "password": "a" * 100})
        assert r.status_code == 422


# ---------------------------------------------------------------- create-admin CLI

def test_create_admin_cli_seeds_user(tmp_path, monkeypatch):
    """The underlying ``create_admin`` function writes an admin and never starts
    the server."""
    from astrodeck.__main__ import create_admin

    temp_users = UserStore(path=tmp_path / "users.json")
    monkeypatch.setattr(users_mod, "user_store", temp_users)

    pub = create_admin("rootadmin", "p@ssw0rd")
    assert pub["role"] == "admin" and pub["username"] == "rootadmin"
    assert "password_hash" not in pub
    # actually persisted + verifiable
    assert temp_users.verify("rootadmin", "p@ssw0rd") is not None


def test_create_admin_cli_resets_existing(tmp_path, monkeypatch):
    """Re-running create-admin on an existing username resets it to an enabled
    admin with a new password (lock-out recovery)."""
    from astrodeck.__main__ import create_admin

    temp_users = UserStore(path=tmp_path / "users.json")
    # keep a second admin so demote/disable churn never trips last-admin
    temp_users.create(username="keeper", password="pw", role="admin")
    u = temp_users.create(username="demoted", password="old", role="viewer")
    temp_users.set_enabled(u.id, False)
    monkeypatch.setattr(users_mod, "user_store", temp_users)

    pub = create_admin("demoted", "fresh")
    assert pub["role"] == "admin" and pub["enabled"] is True
    assert temp_users.verify("demoted", "fresh") is not None
    assert temp_users.verify("demoted", "old") is None


def test_create_admin_requires_password(tmp_path, monkeypatch):
    from astrodeck.__main__ import create_admin

    temp_users = UserStore(path=tmp_path / "users.json")
    monkeypatch.setattr(users_mod, "user_store", temp_users)
    with pytest.raises(ValueError):
        create_admin("x", "")


# =================================================== SECURITY REGRESSIONS (W2.6)

# ---- Fix 2 (high): logout revokes the cookie on its very NEXT request -------

def test_logout_kills_the_cookie_immediately(tmp_path, monkeypatch):
    """A user's OWN logout must revoke their session in the LIVE provider (not
    just on disk), so the same cookie stops authenticating on its next use --
    without waiting for an unrelated admin write or a restart."""
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    users.create(username="alice", password="hunter2", role="operator")
    with TestClient(app) as c:
        r = c.post("/auth/local",
                   json={"username": "alice", "password": "hunter2"})
        assert r.status_code == 200, r.text
        cookie = c.cookies.get(SESSION_COOKIE)
        assert cookie
        # The cookie authenticates before logout.
        assert c.get("/auth/me").status_code == 200
        # Log out (clears the client cookie AND must revoke the jti live).
        assert c.post("/auth/logout").status_code == 200
        # Re-present the SAME cookie: it must now be rejected (revoked live).
        c.cookies.set(SESSION_COOKIE, cookie)
        assert c.get("/auth/me").status_code == 401


# ---- Fix 4 (medium): no empty-password admin over the HTTP surfaces ---------

def test_first_run_setup_rejects_empty_password_422(tmp_path, monkeypatch):
    """The open first-run setup path must NOT let an empty-password ADMIN be
    created from the LAN (an empty-password admin is a real bypass)."""
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    with TestClient(app) as c:
        r = c.post("/auth/setup/local",
                   json={"username": "root", "password": ""})
        assert r.status_code == 422, r.text
        # whitespace-only is likewise rejected
        r2 = c.post("/auth/setup/local",
                    json={"username": "root", "password": "   "})
        assert r2.status_code == 422
        # nothing was seeded -> first-run is still open
        assert users.is_empty() is True
        assert c.get("/api/auth/methods").json()["first_run"] is True


def test_create_user_rejects_empty_password_422(tmp_path, monkeypatch):
    """``POST /api/users`` must reject a blank password with 422 (not 409/201)."""
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"])
    users.create(username="root", password="pw", role="admin")
    with TestClient(app) as c:
        c.cookies.set(SESSION_COOKIE, _login_cookie("admin"))
        r = c.post("/api/users", json={"username": "ghost", "password": "",
                                       "role": "admin"})
        assert r.status_code == 422, r.text
        # the empty-password admin was not created
        names = [u["username"] for u in c.get("/api/users").json()["users"]]
        assert "ghost" not in names
