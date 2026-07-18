"""G4: loopback-trust test mode (``AuthConfig.trust_loopback``).

Codex R3-ROLE-01 (BLOCKED): the server treated EVERY loopback caller as
trusted local admin under the open/"none" provider, so a reviewer testing
from the same machine could create disposable operator/viewer accounts and
still never see anything but admin -- there was no way to prove operator/
viewer gating actually works from loopback. ``trust_loopback`` (default True,
today's behavior unchanged) removes that short-circuit when set False: a
loopback caller under the "none" provider is hard-denied EXACTLY like the
existing W3 remote-tunneled-caller model (see ``test_rbac_core.py::
test_remote_interlock_none_provider_hard_denies``) -- no new auth mechanism,
the SAME code path, just also gated on loopback origin.

Covers, via FastAPI ``TestClient`` (repo convention: in-process fakes +
``monkeypatch``, NO ``unittest.mock``, mirrors ``test_local_auth_routes.py``'s
harness):

  * default True -> loopback caller under "none" provider is STILL admin
    (HARD CONSTRAINT: byte-for-byte unchanged default).
  * False + no methods/token -> the SAME loopback caller is denied exactly
    like today's remote-under-"none" model (401, never a fabricated
    viewer-level default -- the "none" provider has no such thing).
  * False -> a non-loopback DIRECT caller is unaffected (never
    loopback-special-cased to begin with).
  * False + a real local login -> the caller's OWN role/caps are honored --
    the disposable-account role verification this flag exists to unblock.
  * the flag round-trips through ``AuthConfig``/``ConfigStore`` and is NOT
    scrubbed by ``redacted()`` (it is not a secret).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.auth.local_routes as local_routes
import astrodeck.auth.routes as auth_routes
import astrodeck.auth.users as users_mod
from astrodeck.auth import get_trust_loopback, reset_active_provider
from astrodeck.auth.users import UserStore
from astrodeck.config import AuthConfig, ConfigStore, redacted

LOOPBACK = ("127.0.0.1", 54321)
NON_LOOPBACK = ("203.0.113.7", 54321)


@pytest.fixture(autouse=True)
def _clean_provider():
    reset_active_provider()
    yield
    reset_active_provider()


def _make_app(tmp_path, monkeypatch, *, methods=None, trust_loopback=True):
    """Isolated app + temp config/user stores (mirrors ``test_local_auth_routes.
    _make_app``), with ``trust_loopback`` seeded into the persisted AuthConfig.
    The real ``configure_provider_from_auth`` runs at app-create/lifespan time
    (NOT monkeypatched away), so the flag takes the same path a live
    ``/api/auth/config`` save would."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    auth = AuthConfig(methods=list(methods or []), trust_loopback=trust_loopback)
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


# ============================================== default True: unchanged

def test_default_true_loopback_gets_admin(tmp_path, monkeypatch):
    """HARD CONSTRAINT: default trust_loopback=True -> a loopback caller under
    the open "none" provider is STILL admin (today's behavior, byte-for-byte
    unchanged for existing users)."""
    app, _, _ = _make_app(tmp_path, monkeypatch, methods=[], trust_loopback=True)
    with TestClient(app, client=LOOPBACK) as c:
        assert get_trust_loopback() is True
        r = c.get("/api/me")
        assert r.status_code == 200
        assert r.json()["role"] == "admin"
        # a control/admin surface is reachable too, not just the identity probe
        assert c.get("/api/users").status_code == 200


# ============================================== False: loopback denied

def test_false_loopback_unauthenticated_is_denied(tmp_path, monkeypatch):
    """trust_loopback=False + no methods/token: the loopback caller is treated
    exactly like today's remote-under-"none" model -- 401. The "none" provider
    is admin-or-nothing; there is no viewer-level default to fall back to, so
    401 (not a fabricated 200-as-viewer) IS what "the existing unauthenticated
    model" does here, mirroring test_rbac_core.py's
    test_remote_interlock_none_provider_hard_denies."""
    app, _, _ = _make_app(tmp_path, monkeypatch, methods=[], trust_loopback=False)
    with TestClient(app, client=LOOPBACK) as c:
        assert get_trust_loopback() is False
        assert c.get("/api/me").status_code == 401
        assert c.get("/api/users").status_code == 401
        assert c.get("/api/status").status_code == 401


def test_false_non_loopback_direct_caller_unaffected(tmp_path, monkeypatch):
    """The flag ONLY narrows loopback -- a non-loopback DIRECT caller (not
    relay-tunneled) keeps the open-admin default either way; it was never
    loopback-special-cased and the flag does not touch it."""
    app, _, _ = _make_app(tmp_path, monkeypatch, methods=[], trust_loopback=False)
    with TestClient(app, client=NON_LOOPBACK) as c:
        r = c.get("/api/me")
        assert r.status_code == 200
        assert r.json()["role"] == "admin"


def test_loopback_detection_covers_whole_loopback_space(tmp_path, monkeypatch):
    """Hardening: loopback detection is not an exact-string match on
    "127.0.0.1" -- the whole 127.0.0.0/8 block, ::1, IPv4-mapped IPv6, and
    the "localhost" name all count; non-IP junk and null clients do not.
    End-to-end for 127.0.0.2 (any /8 address must be denied when the flag is
    off), unit-level for the rest via _is_loopback_request directly."""
    from astrodeck.auth.deps import _is_loopback_request

    app, _, _ = _make_app(tmp_path, monkeypatch, methods=[], trust_loopback=False)
    with TestClient(app, client=("127.0.0.2", 54321)) as c:
        assert c.get("/api/me").status_code == 401

    class _Client:
        def __init__(self, host):
            self.host = host

    class _Req:
        def __init__(self, host):
            self.client = _Client(host) if host is not None else None

    for host in ("127.0.0.1", "127.0.0.2", "127.255.255.254", "::1",
                 "::ffff:127.0.0.1", "localhost"):
        assert _is_loopback_request(_Req(host)) is True, host
    for host in ("203.0.113.7", "10.0.0.5", "::ffff:203.0.113.7",
                 "testclient", "", None):
        assert _is_loopback_request(_Req(host)) is False, host


# ============================================== False + valid login: role honored

@pytest.mark.parametrize(
    "username, role, email",
    [
        pytest.param("olivia", "operator", "o@rig", id="operator"),
        pytest.param("vic", "viewer", "v@rig", id="viewer"),
    ],
)
def test_false_plus_valid_login_honors_role(tmp_path, monkeypatch, username, role,
                                            email):
    """The unblocked scenario (R3-ROLE-01): with trust_loopback=False and a
    real local account, a loopback browser that actually logs in gets ITS OWN
    role/caps -- not admin -- so operator/viewer gating can finally be proven
    from the same machine the reviewer is running on (both operator AND
    viewer, per R3-ROLE-01)."""
    app, users, _ = _make_app(tmp_path, monkeypatch, methods=["local"],
                              trust_loopback=False)
    users.create(username=username, password="hunter2", role=role, email=email)
    with TestClient(app, client=LOOPBACK) as c:
        # methods=["local"] -> MultiAuthProvider is active, not "none" at all,
        # so trust_loopback was never in play here to begin with: unauthenticated
        # is 401 pre-login, same as it always was for a configured method.
        assert c.get("/api/me").status_code == 401
        r = c.post("/auth/local",
                   json={"username": username, "password": "hunter2"})
        assert r.status_code == 200, r.text
        me = c.get("/api/me")
        assert me.status_code == 200
        assert me.json()["role"] == role
        # this role does NOT hold admin.users -> the user-CRUD route 403s
        # (proves this is a REAL role, not a leaked admin principal)
        assert c.get("/api/users").status_code == 403


# ============================================== config round-trip + redaction

def test_trust_loopback_config_roundtrip_and_not_redacted(tmp_path):
    """The flag is a plain non-secret boolean: it round-trips through
    ConfigStore.set_auth and passes through redacted() untouched, exactly like
    the other auth booleans (item 6 -- no scrub needed, no redaction-test
    disturbance)."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = store.cfg()
    assert cfg.auth.trust_loopback is True  # default
    store.set_auth(cfg.auth.model_copy(update={"trust_loopback": False}))
    assert store.cfg().auth.trust_loopback is False
    out = redacted(store.cfg())
    assert out["auth"]["trust_loopback"] is False
