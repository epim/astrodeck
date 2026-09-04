"""Local-auth core tests (W2.3-bis + W2.6): bcrypt helpers, UserStore CRUD,
multi-method resolution.

Repo convention: in-process fakes + FastAPI ``TestClient`` + ``monkeypatch``,
NO ``unittest.mock``. These cover the CORE primitives only -- password hashing,
the JSON user store (atomic write + secrecy of ``password_hash``), and the
multi-method resolution chain (empty methods => admin; a local session => that
role; google still resolves; break-glass token => admin; disabled user denied).
The HTTP login/setup/user-management routes are a separate owner.
"""
from __future__ import annotations

import json
import os
import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from astrodeck.auth import (CAP_CONTROL_MOUNT, CAP_VIEW_STATUS, Principal,
                            UserStore, build_provider, hash_password,
                            reset_active_provider, require, set_active_provider,
                            sign_session, verify_password)
from astrodeck.auth import session as session_mod
from astrodeck.auth.passwords import (MAX_PASSWORD_BYTES, PasswordTooLongError,
                                      PasswordTooShortError, dummy_verify,
                                      BCRYPT_ROUNDS)
from astrodeck.auth.providers import SessionCookieProvider
from astrodeck.auth.session import (InsecureSessionSecretError, secret_is_default,
                                    set_require_real_secret, sign_session as
                                    _sign_session, verify_session)
from astrodeck.config import AuthConfig


@pytest.fixture(autouse=True)
def _open_default():
    """Start/end each test on the open-default provider so a leftover provider
    never leaks into the next test."""
    import astrodeck.config as config_mod
    # ``_install`` below writes the test's AuthConfig into the worker-shared
    # store (the provider re-reads it on every request). Restore it, or
    # every later test on this worker that builds an app on the shared
    # store installs a real provider and answers 401.
    saved_auth = config_mod.config_store.cfg().auth
    reset_active_provider()
    yield
    config_mod.config_store.cfg().auth = saved_auth
    reset_active_provider()


# ============================================================ bcrypt helpers

def test_hash_password_roundtrips():
    h = hash_password("hunter2-long")
    assert h.startswith("$2b$") and f"${BCRYPT_ROUNDS:02d}$" in h[:7]
    assert verify_password("hunter2-long", h) is True


def test_verify_rejects_wrong_password():
    h = hash_password("correct horse")
    assert verify_password("wrong horse", h) is False
    assert verify_password("", h) is False


def test_verify_rejects_tampered_hash():
    h = hash_password("s3cret-longer")
    # Flip a byte deep in the digest -> bcrypt can't match -> False (no raise).
    tampered = h[:-1] + ("A" if h[-1] != "A" else "B")
    assert verify_password("s3cret-longer", tampered) is False
    # Structurally broken hashes are denied, not exceptions.
    assert verify_password("s3cret-longer", "not-a-bcrypt-hash") is False
    assert verify_password("s3cret-longer", "") is False
    assert verify_password("s3cret-longer", None) is False


def test_hash_rejects_overlong_password():
    over = "a" * (MAX_PASSWORD_BYTES + 1)
    with pytest.raises(PasswordTooLongError):
        hash_password(over)
    # PasswordTooLongError is a ValueError so route ``except ValueError`` catches it.
    assert isinstance(PasswordTooLongError(), ValueError)
    # A 72-byte password is fine.
    assert verify_password("a" * MAX_PASSWORD_BYTES,
                           hash_password("a" * MAX_PASSWORD_BYTES)) is True
    # An over-length candidate never matches an existing hash (no silent truncation).
    h = hash_password("a" * MAX_PASSWORD_BYTES)
    assert verify_password("a" * (MAX_PASSWORD_BYTES + 1), h) is False


def test_distinct_hashes_for_same_password():
    # gensalt() means two hashes of the same password differ but both verify.
    a, b = hash_password("same-password"), hash_password("same-password")
    assert a != b
    assert verify_password("same-password", a) and verify_password("same-password", b)


# ============================================================ UserStore CRUD

def _store(tmp_path):
    return UserStore(path=tmp_path / "users.json")


def test_store_starts_empty(tmp_path):
    s = _store(tmp_path)
    assert s.is_empty() is True
    assert s.list() == []


def test_create_and_query(tmp_path):
    s = _store(tmp_path)
    u = s.create(username="Alice", password="alice-password", role="admin",
                 email="a@x")
    assert s.is_empty() is False
    # username stored case-folded; lookup is case-insensitive
    assert u.username == "alice"
    assert s.get(u.id).id == u.id
    assert s.get_by_username("ALICE").id == u.id
    assert s.get_by_username("alice").id == u.id


def test_duplicate_username_rejected(tmp_path):
    s = _store(tmp_path)
    s.create(username="bob", password="correct-horse", role="operator")
    with pytest.raises(ValueError):
        s.create(username="BOB", password="correct-horse-2", role="viewer")  # case-folded dup


def test_unknown_role_rejected(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(ValueError):
        s.create(username="x", password="correct-horse", role="superuser")


def test_blank_username_rejected(tmp_path):
    s = _store(tmp_path)
    with pytest.raises(ValueError):
        s.create(username="   ", password="correct-horse", role="viewer")


def test_set_password_and_role_and_enabled(tmp_path):
    s = _store(tmp_path)
    admin = s.create(username="root", password="correct-horse", role="admin")
    u = s.create(username="joe", password="old-password", role="viewer")
    assert s.verify("joe", "old-password") is not None
    s.set_password(u.id, "new-password")
    assert s.verify("joe", "old-password") is None
    assert s.verify("joe", "new-password") is not None
    s.set_role(u.id, "operator")
    assert s.get(u.id).role == "operator"
    s.set_enabled(u.id, False)
    assert s.get(u.id).enabled is False
    # a disabled user fails verify even with the right password
    assert s.verify("joe", "new-password") is None
    # admin still around so the above demotions/disables are allowed
    assert admin.role == "admin"


def test_rename(tmp_path):
    s = _store(tmp_path)
    s.create(username="root", password="correct-horse", role="admin")
    u = s.create(username="old", password="correct-horse", role="viewer")
    s.rename(u.id, "NewName")
    assert s.get(u.id).username == "newname"
    assert s.get_by_username("newname").id == u.id
    # rename onto an existing username is rejected
    with pytest.raises(ValueError):
        s.rename(u.id, "root")


def test_delete(tmp_path):
    s = _store(tmp_path)
    s.create(username="root", password="correct-horse", role="admin")
    u = s.create(username="temp", password="correct-horse", role="viewer")
    s.delete(u.id)
    assert s.get(u.id) is None
    assert s.get_by_username("temp") is None


# -------------------------------------------------- last-admin protection

def test_cannot_delete_disable_or_demote_last_admin(tmp_path):
    s = _store(tmp_path)
    a = s.create(username="only-admin", password="correct-horse", role="admin")
    with pytest.raises(ValueError, match="last admin"):
        s.delete(a.id)
    with pytest.raises(ValueError, match="last admin"):
        s.set_enabled(a.id, False)
    with pytest.raises(ValueError, match="last admin"):
        s.set_role(a.id, "operator")
    # with a SECOND enabled admin, demoting the first is allowed
    s.create(username="admin2", password="correct-horse", role="admin")
    s.set_role(a.id, "viewer")
    assert s.get(a.id).role == "viewer"


def test_disabled_admin_does_not_count_as_last_admin_guard(tmp_path):
    s = _store(tmp_path)
    a1 = s.create(username="a1", password="correct-horse", role="admin")
    a2 = s.create(username="a2", password="correct-horse", role="admin")
    s.set_enabled(a2.id, False)            # a2 disabled -> a1 is the only ENABLED admin
    with pytest.raises(ValueError, match="last admin"):
        s.delete(a1.id)                    # deleting the only enabled admin denied


# -------------------------------------------------- atomic persistence + secrecy

def test_persists_atomically_and_reloads(tmp_path):
    path = tmp_path / "users.json"
    s = UserStore(path=path)
    u = s.create(username="alice", password="correct-horse", role="admin")
    # a fresh store over the SAME file reads the user back
    s2 = UserStore(path=path)
    assert s2.get_by_username("alice").id == u.id
    assert s2.verify("alice", "correct-horse") is not None
    # the on-disk file is valid JSON with a users array
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(on_disk["users"], list) and len(on_disk["users"]) == 1


def test_password_hash_absent_from_public_shape(tmp_path):
    s = _store(tmp_path)
    u = s.create(username="alice", password="correct-horse", role="admin")
    pub = u.to_public()
    # structurally ABSENT (not blanked) from the public view
    assert "password_hash" not in pub
    assert set(pub) == {"id", "username", "email", "login_email", "role",
                        "enabled", "created", "has_password"}
    # `has_password` is a BOOL derived from whether a hash exists — never the
    # hash, never a prefix of it, never its length.
    assert pub["has_password"] is True
    assert not any(isinstance(v, str) and v.startswith("$2b$") for v in pub.values())
    # ...and from every listed user
    for shape in (x.to_public() for x in s.list()):
        assert "password_hash" not in shape
    # the hash IS retained at rest (so verify works), just never surfaced.
    assert s.get(u.id).password_hash.startswith("$2b$")


def test_verify_is_the_only_path_reading_hash(tmp_path):
    s = _store(tmp_path)
    s.create(username="alice", password="correct-horse", role="admin")
    assert s.verify("alice", "correct-horse") is not None
    assert s.verify("alice", "nope") is None
    assert s.verify("ghost", "correct-horse") is None  # unknown -> None


# ============================================================ multi-method resolve

def _guarded_client(dep, *, cookie: str | None = None) -> TestClient:
    app = FastAPI()

    @app.get("/g")
    async def g(principal: Principal = Depends(dep)):
        return {"role": principal.role, "email": principal.email}

    c = TestClient(app)
    if cookie is not None:
        # Set the session cookie on the client instance (per-request cookies= is
        # deprecated in newer starlette/httpx).
        c.cookies.set(SessionCookieProvider.COOKIE_NAME, cookie)
    return c


def _install(auth_cfg):
    # SessionCookieProvider re-reads authorization state on every request, so
    # its unit harness must install the same config into the live store.
    import astrodeck.config as config_mod
    config_mod.config_store.cfg().auth = auth_cfg
    p = build_provider(auth_cfg)
    set_active_provider(p)
    return p


def test_empty_methods_resolve_admin():
    # methods == [] and no token => open/admin (today's byte-for-byte default).
    _install(AuthConfig())
    r = _guarded_client(require(CAP_CONTROL_MOUNT)).get("/g")
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_break_glass_token_always_admin():
    # local method enabled AND a break-glass admin_token: the token wins -> admin.
    token = "g" * 32
    _install(AuthConfig(methods=["local"], admin_token=token))
    c = _guarded_client(require(CAP_CONTROL_MOUNT))
    # no creds -> 401 (a method is enabled, no open default)
    assert c.get("/g").status_code == 401
    # token via any carrier -> admin
    assert c.get("/g", headers={"X-Auth-Token": token}).json()["role"] == "admin"
    assert c.get("/g", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert c.get(f"/g?token={token}").status_code == 200


def test_local_session_resolves_to_its_role(tmp_path, monkeypatch):
    # A local login mints the SAME ad_session cookie; the provider resolves it.
    import astrodeck.auth.users as users_mod
    users = _store(tmp_path)
    user = users.create(username="op", password="correct-horse",
                        role="operator", email="op@rig")
    monkeypatch.setattr(users_mod, "user_store", users)
    _install(AuthConfig(methods=["local"]))
    tok = sign_session("operator", email="op@rig", jti="j1", authn="local",
                       subject=user.id, account_epoch=user.session_epoch)
    r = _guarded_client(require(CAP_VIEW_STATUS), cookie=tok).get("/g")
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "operator"
    assert body["email"] == "op@rig"
    # operator holds control.mount (2026-07-17 decisions wave I1) -> 200
    assert _guarded_client(require(CAP_CONTROL_MOUNT), cookie=tok).get(
        "/g").status_code == 200


def test_google_session_still_resolves_under_multi():
    # A google-minted cookie (same sign_session) resolves identically.
    _install(AuthConfig(methods=["google", "local"],
                        role_allowlist={"g@x": "admin"}))
    tok = sign_session("admin", email="g@x", jti="jg", authn="google")
    r = _guarded_client(require(CAP_CONTROL_MOUNT), cookie=tok).get("/g")
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_no_cookie_with_method_enabled_is_401():
    _install(AuthConfig(methods=["local"]))
    assert _guarded_client(require(CAP_VIEW_STATUS)).get("/g").status_code == 401


def test_revoked_session_denied(tmp_path):
    # A jti in the revoke registry is rejected on its next request.
    _install(AuthConfig(methods=["google"], revoked_jti=["dead"],
                        role_allowlist={"a@x": "admin"}))
    tok = sign_session("admin", email="a@x", jti="dead", authn="google")
    assert _guarded_client(require(CAP_VIEW_STATUS), cookie=tok).get(
        "/g").status_code == 401


def test_provider_name_never_none_when_method_enabled():
    # The W3 interlock keys off name=="none"; a multi-method provider must not be.
    assert build_provider(AuthConfig(methods=["local"])).name == "local"
    assert build_provider(AuthConfig(methods=["google"])).name == "google"
    assert build_provider(AuthConfig(methods=["local", "google"])).name == "local+google"
    assert build_provider(AuthConfig(provider="google")).name == "google"  # migrated


def test_disabled_user_denied_end_to_end(tmp_path):
    # A full local path: a disabled user can't authenticate via verify(), so no
    # cookie is ever minted for them. (Login route is a separate owner; here we
    # assert the store gate that the route depends on.)
    s = _store(tmp_path)
    s.create(username="root", password="correct-horse", role="admin")
    u = s.create(username="banned", password="correct-horse", role="operator")
    s.set_enabled(u.id, False)
    assert s.verify("banned", "correct-horse") is None


# =================================================== SECURITY REGRESSIONS (W2.6)

# ---- Fix 1 (critical): session-secret fail-closed interlock -----------------

def test_default_secret_session_refused_when_interlock_armed(monkeypatch):
    """With a real method enabled (interlock ARMED) and ONLY the public dev
    secret, ``sign_session`` must REFUSE to mint and ``verify_session`` must
    refuse to accept -- otherwise anyone knowing the public key mints an admin."""
    monkeypatch.delenv(session_mod.SECRET_ENV_VAR, raising=False)
    # No persisted secret either => the effective secret is the dev default.
    monkeypatch.setattr(session_mod, "_persisted_secret", lambda: None)
    assert secret_is_default() is True

    # Pre-mint a token on the dev default BEFORE arming, then arm the interlock.
    forged = _sign_session("admin", ttl_s=99999)
    set_require_real_secret(True)
    try:
        # Minting on the default is now a hard error (no forgeable admin cookie).
        with pytest.raises(InsecureSessionSecretError):
            _sign_session("admin", ttl_s=99999)
        # And a previously-forged default-secret token no longer verifies.
        assert verify_session(forged) is None
    finally:
        set_require_real_secret(False)
    # Disarmed again: the default-secret path works for local/open dev use.
    assert verify_session(_sign_session("viewer", ttl_s=99999)) is not None


def test_ensure_real_secret_generates_and_persists(tmp_path, monkeypatch):
    """When a method is enabled without ASTRODECK_SECRET, the boot path generates
    and PERSISTS a random secret, so sessions are never on the public dev key."""
    monkeypatch.delenv(session_mod.SECRET_ENV_VAR, raising=False)
    # Point the secret dir at a temp location (mirrors a temp config store).
    monkeypatch.setattr(session_mod, "_secret_dir", lambda: tmp_path)
    assert secret_is_default() is True
    assert session_mod.ensure_real_secret() is True
    # A real secret is now in force (no longer the dev default) and persisted.
    assert secret_is_default() is False
    assert (tmp_path / session_mod.SECRET_FILE_NAME).exists()
    # A second call is idempotent (reuses the persisted secret).
    first = (tmp_path / session_mod.SECRET_FILE_NAME).read_text()
    assert session_mod.ensure_real_secret() is True
    assert (tmp_path / session_mod.SECRET_FILE_NAME).read_text() == first


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
def test_generated_session_secret_is_owner_only(tmp_path, monkeypatch):
    import stat

    monkeypatch.delenv(session_mod.SECRET_ENV_VAR, raising=False)
    monkeypatch.setattr(session_mod, "_secret_dir", lambda: tmp_path)
    assert session_mod.ensure_real_secret() is True
    mode = stat.S_IMODE(
        (tmp_path / session_mod.SECRET_FILE_NAME).stat().st_mode)
    assert mode == 0o600


def test_configure_provider_arms_interlock_only_with_method(monkeypatch, tmp_path):
    """``configure_provider_from_auth`` arms the interlock when a method is on and
    a real secret can't be made; disarms it under the open default."""
    from astrodeck.auth.deps import configure_provider_from_auth
    monkeypatch.delenv(session_mod.SECRET_ENV_VAR, raising=False)
    # Force secret generation to FAIL so the guard must arm fail-closed.
    monkeypatch.setattr(session_mod, "ensure_real_secret", lambda: False)
    monkeypatch.setattr(session_mod, "secret_is_default", lambda: True)
    configure_provider_from_auth(AuthConfig(methods=["local"]))
    assert session_mod.require_real_secret() is True
    # Open default => interlock disarmed (byte-for-byte today's behavior).
    configure_provider_from_auth(AuthConfig(methods=[]))
    assert session_mod.require_real_secret() is False


# ---- Fix 3 (medium): unknown-user timing (anti-enumeration) -----------------

def test_dummy_verify_runs_a_real_bcrypt_compare():
    """``dummy_verify`` must execute a genuine cost-12 bcrypt compare (so an
    unknown username is timing-indistinguishable from a known one), unlike
    ``verify_password(pw, None)`` which short-circuits before bcrypt runs."""
    # The short-circuit path is effectively instant.
    t0 = time.perf_counter()
    for _ in range(3):
        verify_password("whatever", None)
    short_circuit = time.perf_counter() - t0

    # A real bcrypt compare against a real hash.
    real_hash = hash_password("anchor-password")
    t0 = time.perf_counter()
    verify_password("anchor-password", real_hash)
    real = time.perf_counter() - t0

    # dummy_verify must be in the same order of magnitude as a REAL compare,
    # and decisively slower than the no-bcrypt short-circuit path.
    t0 = time.perf_counter()
    assert dummy_verify("whatever") is False
    dummy = time.perf_counter() - t0
    assert dummy > short_circuit * 10  # not the instant short-circuit
    assert dummy >= real * 0.4         # genuinely a bcrypt-cost operation


def test_verify_unknown_user_burns_a_real_compare(tmp_path, monkeypatch):
    """``UserStore.verify`` on an unknown user takes ~bcrypt time (it routes
    through ``dummy_verify``), not the instant short-circuit."""
    calls = {"n": 0}
    real_dummy = dummy_verify

    def _spy(pw):
        calls["n"] += 1
        return real_dummy(pw)

    monkeypatch.setattr("astrodeck.auth.users.dummy_verify", _spy)
    s = _store(tmp_path)
    s.create(username="real", password="correct-horse", role="admin")
    assert s.verify("ghost", "anything") is None
    assert calls["n"] == 1  # the unknown-user branch burned a real compare


# ---- Fix 4 (medium): no empty/blank-password user ---------------------------

def test_hash_rejects_blank_password():
    """``hash_password`` rejects blank/whitespace-only passwords on EVERY write
    path (closing the empty-password-admin hole)."""
    for bad in ("", "   ", "\t", "\n"):
        with pytest.raises(PasswordTooShortError):
            hash_password(bad)
    # It's a ValueError subclass so a route ``except ValueError`` still catches it.
    assert isinstance(PasswordTooShortError(), ValueError)


def test_hash_rejects_human_password_below_policy_minimum():
    with pytest.raises(PasswordTooShortError, match="at least 12"):
        hash_password("short-pass")


def test_weak_configured_session_secrets_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(session_mod.SECRET_ENV_VAR, "guessable")
    with pytest.raises(InsecureSessionSecretError, match="at least 32 bytes"):
        session_mod.session_secret()

    monkeypatch.delenv(session_mod.SECRET_ENV_VAR, raising=False)
    monkeypatch.setattr(session_mod, "_secret_dir", lambda: tmp_path)
    (tmp_path / session_mod.SECRET_FILE_NAME).write_text(
        "still-guessable", encoding="utf-8")
    with pytest.raises(InsecureSessionSecretError, match="at least 32 bytes"):
        session_mod.secret_is_default()


def test_corrupt_user_store_is_not_treated_as_empty(tmp_path):
    path = tmp_path / "users.json"
    original = b'{"users": ['
    path.write_bytes(original)
    store = UserStore(path=path)

    with pytest.raises(RuntimeError, match="no valid backup"):
        store.is_empty()

    assert path.read_bytes() == original


def test_store_create_with_no_password_makes_a_google_only_account(tmp_path):
    """The store now ACCEPTS a blank password — it means "Google sign-in only".

    This inverts an older test that required PasswordTooShortError here. The
    guard did not disappear, it moved to where it belongs: the unauthenticated
    first-run route still refuses a blank password (that one mints an admin from
    the LAN), while an authenticated admin may deliberately create a
    password-less account for somebody who signs in with Google.

    What must never change is that the account cannot be logged into locally."""
    s = _store(tmp_path)
    u = s.create(username="oidc-only", password="", role="viewer")
    assert u.password_hash == ""          # no hash, not a hash of ""
    assert u.can_sign_in_locally is False
    assert s.verify("oidc-only", "") is None
    assert s.verify("oidc-only", "anything") is None


def test_the_banner_distinguishes_minting_from_reusing_a_stored_secret(
        tmp_path, monkeypatch):
    """#212. The line said "was generated and persisted" every single time.

    ``ensure_real_secret`` is idempotent — it returns early when a secret is
    already on disk — but the banner that reported it did not know that, and
    fired the same past-tense sentence on every boot AND on every POST
    /api/auth/config. On 2026-08-09 it appeared four times in one night log,
    twice mid-run with no restart beside them, and reading it as "a new key was
    just written" produced a confident wrong diagnosis of why sessions were
    dropping. The file's mtime settled it: 25 June, forty-five days untouched.

    A log line that reports an EVENT which did not occur is the same defect
    class as a comment that promises behaviour the code does not have."""
    monkeypatch.delenv(session_mod.SECRET_ENV_VAR, raising=False)
    monkeypatch.setattr(session_mod, "_secret_dir", lambda: tmp_path)
    monkeypatch.setattr(session_mod, "_minted_this_process", False)
    assert session_mod.secret_was_minted() is False, (
        "precondition: nothing has been minted yet")

    assert session_mod.ensure_real_secret() is True
    assert session_mod.secret_was_minted() is True, (
        "the first call really did write a new secret, and may say so")

    # Simulate the next boot / the next auth-config POST: same file on disk,
    # fresh process state.
    monkeypatch.setattr(session_mod, "_minted_this_process", False)
    assert session_mod.ensure_real_secret() is True, (
        "precondition: the existing secret is accepted")
    assert session_mod.secret_was_minted() is False, (
        "NOTHING was written this time, so nothing may claim it was — this is "
        "the whole bug")
