"""RBAC core unit tests (W2) -- the auth package + AuthConfig.

Repo convention: in-process fakes + FastAPI ``TestClient`` + ``monkeypatch``,
NO ``unittest.mock``. These cover the CORE only (capabilities, Principal,
require()/get_principal, providers, session sign/verify, AuthConfig
round-trip + redaction + default_role validation). The per-route wiring in
api/app.py is covered by the app-level RBAC suite (separate owner).
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from astrodeck.auth import (ALL_CAPS, CAP_ADMIN_USERS, CAP_CONTROL_MOUNT,
                            CAP_VIEW_PREVIEW, CAP_VIEW_STATUS, ROLES, ROLES_CAP,
                            VIEWER_LINK_CAPS, NoneAuthProvider, Principal,
                            admin_principal, build_provider, caps_for_role,
                            configure_provider_from_auth, decode_session,
                            get_principal, has_capability, principal_for_role,
                            require, reset_active_provider, resolve_principal,
                            role_rank, set_active_provider, sign_session,
                            verify_session)
from astrodeck.auth.providers import TokenAdminProvider
from astrodeck.config import AppConfig, AuthConfig, ConfigStore, redacted


@pytest.fixture(autouse=True)
def _open_default():
    """Every test starts and ends with the open-default (none) provider so a
    leftover provider from one test never leaks into the next."""
    reset_active_provider()
    yield
    reset_active_provider()


# ---------------------------------------------------- has_capability matrix

def test_has_capability_matrix():
    # viewer: read-only, NO media / NO mount / NO admin
    assert has_capability("viewer", CAP_VIEW_STATUS)
    assert has_capability("viewer", CAP_VIEW_PREVIEW)
    assert not has_capability("viewer", "view.media")
    assert not has_capability("viewer", CAP_CONTROL_MOUNT)
    assert not has_capability("viewer", CAP_ADMIN_USERS)
    # operator: imaging+guiding+mount (2026-07-17 decisions wave I1: operators
    # run sequences), NOT power/config/media
    assert has_capability("operator", "control.capture")
    assert has_capability("operator", "control.guide")
    assert has_capability("operator", CAP_CONTROL_MOUNT)
    assert not has_capability("operator", "control.power")
    assert not has_capability("operator", "view.media")
    assert not has_capability("operator", "config.backend")
    # admin: everything
    for cap in ALL_CAPS:
        assert has_capability("admin", cap)
    # unknown role / unknown cap -> fail closed
    assert not has_capability("nobody", CAP_VIEW_STATUS)
    assert not has_capability("admin", "made.up.cap")


def test_role_map_pins():
    assert ROLES_CAP["viewer"] == VIEWER_LINK_CAPS
    assert ROLES_CAP["admin"] == ALL_CAPS
    assert ROLES == ("viewer", "syncer", "operator", "admin")
    assert "view.media" not in ROLES_CAP["viewer"]
    assert "view.site_precise" not in ROLES_CAP["viewer"]
    # operator excludes the escalation surface
    assert role_rank("viewer") < role_rank("syncer") < role_rank("operator") \
        < role_rank("admin")
    assert role_rank("nobody") == -1


def test_syncer_can_take_the_data_and_do_nothing_else():
    """The whole point of the role is its absences (2026-08-11 owner ruling: a
    viewer must never pull raw science data, and a fetch script must never get
    an admin token). Each assertion below is a thing a compromised sync process
    would otherwise be able to do."""
    caps = ROLES_CAP["syncer"]
    assert caps == frozenset({CAP_VIEW_STATUS, "view.media"})
    # it can subscribe to /ws and download the bytes...
    assert CAP_VIEW_STATUS in caps and "view.media" in caps
    # ...and that is ALL. No motion, no imaging, no settings.
    assert not any(c.startswith("control.") for c in caps), caps
    assert not any(c.startswith("config.") for c in caps), caps
    assert not any(c.startswith("admin.") for c in caps), caps
    assert "system.update" not in caps
    # A process that ships every frame off-site never learns where the site is.
    assert "view.site_precise" not in caps
    assert "view.site_derived" not in caps


def test_a_viewer_still_cannot_pull_raw_data():
    """The rule the syncer role exists to protect. If view.media ever lands in
    the viewer set, the link you hand a stranger downloads the science."""
    assert "view.media" not in ROLES_CAP["viewer"]
    assert "view.media" not in VIEWER_LINK_CAPS


def test_syncer_is_not_a_default_role_without_a_pinned_domain():
    """role_rank's only job is gating `default_role`. A syncer can download
    every raw frame, so auto-granting it to a whole Google population must be
    refused exactly as operator is — narrow is not the same as safe to give
    away."""
    from astrodeck.config import AuthConfig, validate_auth_config
    with pytest.raises(ValueError, match="google_hd"):
        validate_auth_config(AuthConfig(provider="google", default_role="syncer",
                                        google_hd=""))
    # ...and IS allowed once a domain is pinned (otherwise the assertion above
    # would pass for the wrong reason — e.g. "syncer" not being a known role).
    validate_auth_config(AuthConfig(provider="google", default_role="syncer",
                                    google_hd="example.com"))


def test_caps_for_role_fail_closed():
    assert caps_for_role("admin") == ALL_CAPS
    assert caps_for_role("ghost") == frozenset()


# ---------------------------------------------------- Principal

def test_principal_has_and_public():
    p = principal_for_role("viewer", email="v@x")
    assert p.has(CAP_VIEW_STATUS)
    assert not p.has(CAP_CONTROL_MOUNT)
    pub = p.to_public()
    assert pub["role"] == "viewer"
    assert pub["email"] == "v@x"
    assert sorted(VIEWER_LINK_CAPS) == pub["caps"]
    # frozen
    with pytest.raises(Exception):
        p.role = "admin"  # type: ignore[misc]


def test_admin_principal_holds_all():
    p = admin_principal()
    assert p.role == "admin"
    assert p.caps == ALL_CAPS
    assert p.email is None


# ---------------------------------------------------- require() / get_principal

def _client_with_dep(dep) -> TestClient:
    app = FastAPI()

    @app.get("/guarded")
    async def guarded(principal: Principal = Depends(dep)):
        return {"role": principal.role}

    return TestClient(app)


def test_require_allows_all_when_open():
    # none provider (default) -> every caller is admin -> any cap passes.
    c = _client_with_dep(require(CAP_CONTROL_MOUNT))
    r = c.get("/guarded")
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_require_enforces_with_limited_role():
    class FakeViewerProvider:
        name = "fake"
        async def resolve(self, request):
            return principal_for_role("viewer", email="v@x")

    set_active_provider(FakeViewerProvider())
    # viewer holds view.status...
    assert _client_with_dep(require(CAP_VIEW_STATUS)).get("/guarded").status_code == 200
    # ...but NOT control.mount -> 403 (not 401: identified, just not allowed)
    assert _client_with_dep(require(CAP_CONTROL_MOUNT)).get("/guarded").status_code == 403


def test_require_401_when_unauthenticated():
    class DenyProvider:
        name = "deny"
        async def resolve(self, request):
            return None

    set_active_provider(DenyProvider())
    assert _client_with_dep(require(CAP_VIEW_STATUS)).get("/guarded").status_code == 401
    assert _client_with_dep(get_principal).get("/guarded").status_code == 401


def test_get_principal_returns_resolved_identity():
    class FakeOpProvider:
        name = "fake"
        async def resolve(self, request):
            return principal_for_role("operator", email="o@x")

    set_active_provider(FakeOpProvider())
    r = _client_with_dep(get_principal).get("/guarded")
    assert r.status_code == 200
    assert r.json()["role"] == "operator"


# ---------------------------------------------------- declare() route markers

def test_declare_stamps_caps_reaches_and_identity():
    """``declare`` is the label the boot assertion reads back. All three marker
    attrs must land on the endpoint function, and ``identity`` must default to
    False so an ordinary route is not swept into invariant (4)."""
    from astrodeck.auth.rbac import (CAP_ATTR, IDENTITY_ATTR, REACHES_ATTR,
                                     declare)

    @declare(CAP_CONTROL_MOUNT, reaches={"Telescope.slew"})
    async def slew():
        return None

    assert getattr(slew, CAP_ATTR) == frozenset({CAP_CONTROL_MOUNT})
    assert getattr(slew, REACHES_ATTR) == frozenset({"Telescope.slew"})
    assert getattr(slew, IDENTITY_ATTR) is False

    @declare(CAP_VIEW_STATUS, identity=True)
    async def whoami():
        return None

    assert getattr(whoami, IDENTITY_ATTR) is True
    # identity is orthogonal to reaches -- an identity route reaches nothing.
    assert getattr(whoami, REACHES_ATTR) == frozenset()


def test_require_dependency_carries_the_cap_the_boot_assertion_reads():
    """The boot assertion's enforcement truth is ``_rbac_cap`` on the dependency
    callable, and each ``require()`` must stamp its OWN cap (not a shared one)."""
    a, b = require(CAP_VIEW_STATUS), require(CAP_CONTROL_MOUNT)
    assert a._rbac_cap == CAP_VIEW_STATUS
    assert b._rbac_cap == CAP_CONTROL_MOUNT


# ---------------------------------------------------- providers

async def test_none_provider_always_admin():
    p = await NoneAuthProvider().resolve(None)  # request unused by none
    assert p == admin_principal()


def test_remote_interlock_none_provider_hard_denies():
    # The W3 seam: provider == none + remote=True -> hard-deny (None), NOT admin.
    app = FastAPI()

    @app.get("/lan")
    async def lan(request: Request):
        p = await resolve_principal(request, remote=False)
        return {"role": None if p is None else p.role}

    @app.get("/wan")
    async def wan(request: Request):
        p = await resolve_principal(request, remote=True)
        return {"role": None if p is None else p.role}

    c = TestClient(app)
    assert c.get("/lan").json()["role"] == "admin"   # LAN-direct: open default
    assert c.get("/wan").json()["role"] is None       # remote: hard-deny


def test_build_provider_selection():
    # none + no token -> NoneAuthProvider
    assert build_provider(AuthConfig()).name == "none"
    # none + admin_token -> TokenAdminProvider
    p = build_provider(AuthConfig(admin_token="s3cret"))
    assert isinstance(p, TokenAdminProvider)
    assert p.name == "token"
    # google -> GoogleAuthProvider
    assert build_provider(AuthConfig(provider="google")).name == "google"


def test_token_admin_provider_via_app():
    cfg = AuthConfig(admin_token="s3cret")
    configure_provider_from_auth(cfg)
    app = FastAPI()

    @app.get("/g")
    async def g(principal: Principal = Depends(require(CAP_CONTROL_MOUNT))):
        return {"role": principal.role}

    c = TestClient(app)
    # no token -> provider yields None -> 401
    assert c.get("/g").status_code == 401
    # valid token (any carrier) -> admin -> 200
    assert c.get("/g", headers={"X-Auth-Token": "s3cret"}).status_code == 200
    assert c.get("/g", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/g?token=s3cret").status_code == 200
    # wrong token -> 401
    assert c.get("/g", headers={"X-Auth-Token": "nope"}).status_code == 401


# ---------------------------------------------------- session sign/verify

def test_session_roundtrip():
    tok = sign_session("operator", email="o@x", jti="abc")
    claims = verify_session(tok)
    assert claims is not None
    assert claims["role"] == "operator"
    assert claims["email"] == "o@x"
    assert claims["jti"] == "abc"


def test_session_tamper_rejected():
    tok = sign_session("viewer", email="v@x")
    # flip a char in the payload segment -> MAC fails -> None
    head, payload, sig = tok.split(".")
    bad_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    tampered = f"{head}.{bad_payload}.{sig}"
    assert verify_session(tampered) is None
    # wrong secret -> None
    assert verify_session(tok, secret=b"other-secret") is None
    # garbage shape -> None
    assert verify_session("not-a-token") is None
    assert verify_session("") is None


def test_session_expiry():
    # exp in the past -> rejected
    tok = sign_session("admin", ttl_s=10, now=1000.0)
    assert verify_session(tok, now=1005.0) is not None   # still valid
    assert verify_session(tok, now=2000.0) is None        # expired


def test_session_alg_none_rejected():
    # A forged token claiming alg=none with no signature must not verify.
    import base64
    import json as _json

    def b64u(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    head = b64u(_json.dumps({"alg": "none", "typ": "ADSESS"}).encode())
    payload = b64u(_json.dumps({"role": "admin"}).encode())
    forged = f"{head}.{payload}."
    assert verify_session(forged) is None
    assert decode_session(forged) is None


# ---------------------------------------------------- AuthConfig round-trip + redaction

def test_authconfig_roundtrip_in_appconfig():
    cfg = AppConfig(auth=AuthConfig(provider="google", admin_token="tok",
                                    google_client_id="cid",
                                    google_client_secret="csecret",
                                    role_allowlist={"a@x": "admin"},
                                    revoked_jti=["j1"]))
    dumped = cfg.model_dump()
    back = AppConfig(**dumped)
    assert back.auth.provider == "google"
    assert back.auth.admin_token == "tok"
    assert back.auth.role_allowlist == {"a@x": "admin"}
    assert back.auth.revoked_jti == ["j1"]
    # old config WITHOUT an auth block still loads (additive)
    legacy = AppConfig(version=3, site={"name": "X"})
    assert legacy.auth.provider == "none"


def test_redaction_scrubs_auth_secrets():
    cfg = AppConfig(auth=AuthConfig(
        provider="google", admin_token="SECRET-TOK",
        google_client_id="cid", google_client_secret="SECRET-CS",
        session_private_key="SECRET-PRIV", session_public_key="PUBKEY",
        role_allowlist={"a@x": "admin"}, revoked_jti=["j1"]))
    out = redacted(cfg)
    a = out["auth"]
    # secrets blanked
    assert a["admin_token"] == ""
    assert a["google_client_secret"] == ""
    assert a["session_private_key"] == ""
    # "configured" booleans surfaced
    assert a["admin_token_configured"] is True
    assert a["google_configured"] is True
    assert a["session_signing_configured"] is True
    # non-secret fields pass through
    assert a["role_allowlist"] == {"a@x": "admin"}
    assert a["revoked_jti"] == ["j1"]
    assert a["session_public_key"] == "PUBKEY"
    # source cfg NEVER mutated
    assert cfg.auth.admin_token == "SECRET-TOK"
    assert cfg.auth.google_client_secret == "SECRET-CS"
    assert cfg.auth.session_private_key == "SECRET-PRIV"


def test_redaction_unconfigured_auth():
    out = redacted(AppConfig())
    a = out["auth"]
    assert a["admin_token_configured"] is False
    assert a["google_configured"] is False
    assert a["session_signing_configured"] is False


# ---------------------------------------------------- set_auth + default_role validation

def _store(tmp_path):
    return ConfigStore(path=tmp_path / "astrodeck.json")


def test_set_auth_roundtrip(tmp_path):
    store = _store(tmp_path)
    v0 = store.cfg().version
    # A client SECRET as well as an id, because `_migrate_legacy_provider` turns
    # `provider="google"` into `methods == ["google"]`, and an id with no secret
    # is a config nobody can sign in through — which `set_auth` now refuses
    # (#205: that exact state locked the rig out twice). What this test is about
    # is the round-trip and the version bump, so it gets a COMPLETE credential
    # rather than a weakened guard.
    store.set_auth(AuthConfig(provider="google", google_client_id="cid",
                              google_client_secret="csecret"))
    assert store.cfg().auth.provider == "google"
    assert store.cfg().version == v0 + 1
    # persisted: a fresh store reads it back
    assert _store(tmp_path).cfg().auth.provider == "google"


def test_default_role_ceiling(tmp_path):
    store = _store(tmp_path)
    # default_role="admin" with empty hd -> rejected
    with pytest.raises(ValueError):
        store.set_auth(AuthConfig(default_role="admin"))
    with pytest.raises(ValueError):
        store.set_auth(AuthConfig(default_role="operator"))
    # default_role="viewer" -> accepted
    store.set_auth(AuthConfig(default_role="viewer"))
    assert store.cfg().auth.default_role == "viewer"
    # default_role="operator" WITH a pinned hd -> accepted
    store.set_auth(AuthConfig(default_role="operator", google_hd="example.com"))
    assert store.cfg().auth.default_role == "operator"


def test_unknown_role_and_provider_rejected(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.set_auth(AuthConfig(provider="facebook"))
    with pytest.raises(ValueError):
        store.set_auth(AuthConfig(role_allowlist={"a@x": "superuser"}))


def test_revoked_jti_append_only(tmp_path):
    store = _store(tmp_path)
    store.set_auth(AuthConfig(revoked_jti=["x", "y"]))
    # adding is fine
    store.set_auth(AuthConfig(revoked_jti=["x", "y", "z"]))
    assert set(store.cfg().auth.revoked_jti) == {"x", "y", "z"}
    # shrinking is rejected (the deny registry cannot be reduced via config)
    with pytest.raises(ValueError):
        store.set_auth(AuthConfig(revoked_jti=["x"]))
    assert set(store.cfg().auth.revoked_jti) == {"x", "y", "z"}
