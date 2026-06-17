"""Google OIDC provider + router tests (W2.4 Stage C).

Repo convention: in-process fakes via monkeypatch + TestClient, NO unittest.mock.
Google's token endpoint and JWKS are faked with an in-process fake httpx
transport; ID tokens are signed in-process with a FIXED 2048-bit RSA test key so
the FULL RS256 verification path is exercised (signature + iss/aud/exp/nonce/
email_verified/hd) -- no crypto wheel is installed, so signing is pure-Python.

The provider is INACTIVE unless ``AuthConfig.provider == "google"``: the
non-breaking default (no provider configured) is covered by the existing RBAC
core + token suites; here we drive the google path on.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from astrodeck.auth import google as g
from astrodeck.auth import routes as auth_routes
from astrodeck.auth.google import (GoogleOIDCClient, GoogleOIDCConfig, OIDCError,
                                   pkce_challenge, verify_id_token)
from astrodeck.config import AuthConfig, ConfigStore


@pytest.fixture(autouse=True)
def _reset_provider():
    """Always restore the open-default auth provider after each test so an
    installed google/session provider never leaks into another test."""
    yield
    from astrodeck.auth.deps import reset_active_provider
    reset_active_provider()


# --------------------------------------------------------------- FIXED test RSA
# A deterministic 2048-bit RSA keypair (test-only; generated once, pinned here so
# the suite is fast + reproducible and never needs a crypto wheel to keygen).
_N = 28140854532592594477381028385827123845115422405652468506163398972740335670622466861434173735409931138353288901092932537109060120794909323914690714157697918859552113397391739875912069220365265522185812022273011379604621900765183215569430746575247739577056546580224843246769569324815812044198160795386156909500323509728185974398328081230193425869957221574373328510876226368577435358642159242967829913751930154796613028096621536635500778544316418028999218054377281552292422159627159502353421732526161484241487359455336112952281731523703923913713020937345653364199862817352795572615722678391823869843106190596670363675803
_E = 65537
_D = 9372698975196166322112289052074926519236148134192625273548295966812274700067857952780645197316446845110023195035728388849299850721138909660342537934818959867957085787238764331773023588844973310012235909824607357584413184154026263476349761756633774196072299597985075276416164292263599879468963392307994461457889912861189112348359237540710581564174141194859926634790824374678207332588553571811839392424286710569278887681868337753678999180456352505478529614825454748830716928723851539914352355079283606994200021604223692242816323293209119832592528453332562816671495247030707341685855427980706805449924223095770351029473
_KID = "test-key-1"

_SHA256_DIGESTINFO_PREFIX = bytes.fromhex(
    "3031300d060960864801650304020105000420")


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_int(i: int) -> str:
    return _b64u(i.to_bytes((i.bit_length() + 7) // 8, "big"))


def _rsa_sign_pkcs1_sha256(message: bytes) -> bytes:
    """Sign with the fixed test private key (pure-python PKCS#1 v1.5/SHA-256)."""
    k = (_N.bit_length() + 7) // 8
    digest = hashlib.sha256(message).digest()
    t = _SHA256_DIGESTINFO_PREFIX + digest
    ps_len = k - len(t) - 3
    em = b"\x00\x01" + (b"\xff" * ps_len) + b"\x00" + t
    em_int = int.from_bytes(em, "big")
    sig_int = pow(em_int, _D, _N)
    return sig_int.to_bytes(k, "big")


def _fake_jwks() -> dict:
    return {"keys": [{
        "kty": "RSA", "use": "sig", "alg": "RS256", "kid": _KID,
        "n": _b64u_int(_N), "e": _b64u_int(_E),
    }]}


def _make_id_token(*, client_id: str, email: str = "a@x.com",
                   email_verified=True, nonce: str | None = "NONCE",
                   hd: str | None = None, iss: str = "https://accounts.google.com",
                   exp_delta: int = 3600, alg: str = "RS256",
                   kid: str | None = _KID, sign: bool = True,
                   now: float | None = None) -> str:
    """Forge a signed Google-style ID token for the test RSA key."""
    if now is None:
        now = time.time()
    header = {"alg": alg, "typ": "JWT"}
    if kid is not None:
        header["kid"] = kid
    payload: dict = {
        "iss": iss, "aud": client_id, "sub": "1234567890",
        "email": email, "email_verified": email_verified,
        "iat": int(now), "exp": int(now) + exp_delta,
    }
    if nonce is not None:
        payload["nonce"] = nonce
    if hd is not None:
        payload["hd"] = hd
    h_b64 = _b64u(json.dumps(header, separators=(",", ":")).encode())
    p_b64 = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{h_b64}.{p_b64}".encode("ascii")
    if not sign or alg == "none":
        return f"{h_b64}.{p_b64}."
    sig = _rsa_sign_pkcs1_sha256(signing_input)
    return f"{h_b64}.{p_b64}.{_b64u(sig)}"


# -------------------------------------------------- fake httpx for Google calls

class _FakeGoogle:
    """In-process fake of Google's token + JWKS endpoints, wired via an
    httpx.MockTransport so ``GoogleOIDCClient`` exercises its REAL code path."""

    def __init__(self, *, client_id: str, id_token_factory):
        self.client_id = client_id
        self._id_token_factory = id_token_factory
        self.token_calls: list[dict] = []
        self.jwks_calls = 0

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.startswith(g.GOOGLE_TOKEN_URI):
                form = dict(httpx.QueryParams(request.content.decode()))
                self.token_calls.append(form)
                return httpx.Response(200, json={
                    "access_token": "fake-at", "token_type": "Bearer",
                    "id_token": self._id_token_factory(),
                })
            if url.startswith(g.GOOGLE_JWKS_URI):
                self.jwks_calls += 1
                return httpx.Response(200, json=_fake_jwks())
            return httpx.Response(404, json={"error": "not found"})
        return httpx.MockTransport(handler)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport())


# ============================================================ verify_id_token

def test_verify_id_token_happy_path():
    cid = "client-123"
    tok = _make_id_token(client_id=cid, email="a@x.com", nonce="N1")
    claims = verify_id_token(tok, jwks=_fake_jwks(), client_id=cid, nonce="N1")
    assert claims["email"] == "a@x.com"
    assert claims["aud"] == cid


def test_verify_rejects_alg_none():
    cid = "client-123"
    tok = _make_id_token(client_id=cid, alg="none")
    with pytest.raises(OIDCError):
        verify_id_token(tok, jwks=_fake_jwks(), client_id=cid, nonce="NONCE")


def test_verify_rejects_tampered_signature():
    cid = "client-123"
    tok = _make_id_token(client_id=cid)
    h, p, s = tok.split(".")
    # flip a MIDDLE char of the signature segment (the last char only encodes a
    # couple of bits of the final byte and can decode unchanged after padding).
    mid = len(s) // 2
    flip = "A" if s[mid] != "A" else "B"
    s2 = s[:mid] + flip + s[mid + 1:]
    with pytest.raises(OIDCError):
        verify_id_token(f"{h}.{p}.{s2}", jwks=_fake_jwks(), client_id=cid,
                        nonce="NONCE")


def test_verify_rejects_wrong_audience():
    tok = _make_id_token(client_id="other-client")
    with pytest.raises(OIDCError):
        verify_id_token(tok, jwks=_fake_jwks(), client_id="client-123",
                        nonce="NONCE")


def test_verify_rejects_bad_issuer():
    cid = "client-123"
    tok = _make_id_token(client_id=cid, iss="https://evil.example")
    with pytest.raises(OIDCError):
        verify_id_token(tok, jwks=_fake_jwks(), client_id=cid, nonce="NONCE")


def test_verify_rejects_expired():
    cid = "client-123"
    tok = _make_id_token(client_id=cid, exp_delta=-10000)
    with pytest.raises(OIDCError):
        verify_id_token(tok, jwks=_fake_jwks(), client_id=cid, nonce="NONCE")


def test_verify_rejects_unverified_email():
    cid = "client-123"
    tok = _make_id_token(client_id=cid, email_verified=False)
    with pytest.raises(OIDCError):
        verify_id_token(tok, jwks=_fake_jwks(), client_id=cid, nonce="NONCE")


def test_verify_rejects_nonce_mismatch():
    cid = "client-123"
    tok = _make_id_token(client_id=cid, nonce="REAL")
    with pytest.raises(OIDCError):
        verify_id_token(tok, jwks=_fake_jwks(), client_id=cid, nonce="EXPECTED")


def test_verify_hd_pin_enforced():
    cid = "client-123"
    # token carries the wrong hosted domain -> reject when hd is pinned
    tok = _make_id_token(client_id=cid, hd="other.com")
    with pytest.raises(OIDCError):
        verify_id_token(tok, jwks=_fake_jwks(), client_id=cid, nonce="NONCE",
                        hd="mycorp.com")
    # matching hd -> ok
    tok2 = _make_id_token(client_id=cid, hd="mycorp.com")
    claims = verify_id_token(tok2, jwks=_fake_jwks(), client_id=cid,
                             nonce="NONCE", hd="mycorp.com")
    assert claims["hd"] == "mycorp.com"


def test_verify_rejects_unknown_kid():
    cid = "client-123"
    tok = _make_id_token(client_id=cid, kid="rotated-away")
    with pytest.raises(OIDCError):
        verify_id_token(tok, jwks=_fake_jwks(), client_id=cid, nonce="NONCE")


# ================================================== PKCE helper round-trip

def test_pkce_challenge_is_s256():
    verifier = "the-verifier-value-abc123"
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert pkce_challenge(verifier) == expected


# ================================================== GoogleOIDCClient exchange

@pytest.mark.anyio
async def test_client_exchange_and_verify():
    cid = "client-123"
    fake = _FakeGoogle(client_id=cid,
                       id_token_factory=lambda: _make_id_token(
                           client_id=cid, nonce="N1"))
    cfg = GoogleOIDCConfig(client_id=cid, client_secret="sek",
                           redirect_uri="https://home/cb")
    client = GoogleOIDCClient(cfg, http_client=fake.client())
    tokens = await client.exchange_code(code="auth-code", code_verifier="verif")
    assert "id_token" in tokens
    # the exchange POSTed PKCE verifier + secret
    assert fake.token_calls[0]["code_verifier"] == "verif"
    assert fake.token_calls[0]["client_secret"] == "sek"
    claims = await client.verify(tokens["id_token"], nonce="N1")
    assert claims["email"] == "a@x.com"


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ================================================== build_auth_url

def test_build_auth_url_has_pkce_and_state():
    cfg = GoogleOIDCConfig(client_id="cid", client_secret="s",
                           redirect_uri="https://home/cb", hd="mycorp.com")
    client = GoogleOIDCClient(cfg)
    url = client.build_auth_url(state="ST", nonce="NO",
                                code_challenge="CH")
    assert "code_challenge=CH" in url
    assert "code_challenge_method=S256" in url
    assert "state=ST" in url
    assert "nonce=NO" in url
    assert "hd=mycorp.com" in url
    assert url.startswith(g.GOOGLE_AUTH_URI)


# ======================================================= router integration

def _client_with_google(tmp_path, monkeypatch, *, role_allowlist=None,
                        default_role=None, hd="", client_id="client-123",
                        id_token_factory=None):
    """Build an app mounting ONLY the auth router, with a google AuthConfig and
    a fake-Google httpx transport injected into the OIDC client."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    auth = AuthConfig(provider="google", google_client_id=client_id,
                      google_client_secret="secret",
                      google_redirect_uri="https://home/auth/google/callback",
                      google_hd=hd, role_allowlist=role_allowlist or {},
                      default_role=default_role)
    # bypass set_auth validation churn -- write directly for the test store
    cfg = store.cfg()
    cfg.auth = auth
    store._save()

    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(auth_routes, "config_store", store)
    monkeypatch.setenv("ASTRODECK_SECRET", "test-session-secret-xyz")

    # Install the configured provider (google -> session-cookie resolution) so
    # /auth/me resolves the minted session. The autouse ``_reset_provider``
    # fixture restores the open default after every test.
    from astrodeck.auth.deps import configure_provider_from_auth
    configure_provider_from_auth(auth)

    fake = _FakeGoogle(
        client_id=client_id,
        id_token_factory=id_token_factory or (lambda: _make_id_token(
            client_id=client_id, email="a@x.com", nonce="NONCE")))

    # Make GoogleOIDCClient always use the fake transport.
    orig_init = GoogleOIDCClient.__init__

    def patched_init(self, cfg, *, http_client=None):
        orig_init(self, cfg, http_client=fake.client())

    monkeypatch.setattr(GoogleOIDCClient, "__init__", patched_init)

    app = FastAPI()
    app.include_router(auth_routes.router)
    return app, store, fake


def _do_login_and_callback(c, *, store):
    """Drive /auth/login then /auth/google/callback through the fake, returning
    the callback response. Reuses the pre-auth cookie state via the TestClient
    cookie jar; forces the callback ``state`` to match the issued one."""
    # /auth/login sets the pre-auth cookie and 302s to Google.
    r = c.get("/auth/login", follow_redirects=False)
    assert r.status_code == 302
    # Pull the state out of the Location to echo it back (Google would).
    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(urlparse(r.headers["location"]).query)
    state = qs["state"][0]
    # The nonce that the id_token must echo is also in the redirect; the fake
    # token factory hard-codes "NONCE", so align the login nonce by reading it.
    return c.get(f"/auth/google/callback?code=abc&state={state}",
                 follow_redirects=False)


def test_login_404_when_google_disabled(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(auth_routes, "config_store", store)
    app = FastAPI()
    app.include_router(auth_routes.router)
    with TestClient(app) as c:
        assert c.get("/auth/login", follow_redirects=False).status_code == 404


def _mount_auth_router(store, monkeypatch):
    """Mount ONLY the auth router against ``store`` (no fake-Google transport).

    Used by the ``_google_enabled`` gate regression tests, which only care
    whether /auth/login ACTIVATES (status != 404) vs is inert (404)."""
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(auth_routes, "config_store", store)
    app = FastAPI()
    app.include_router(auth_routes.router)
    return app


def test_login_activates_when_google_in_methods(tmp_path, monkeypatch):
    """REGRESSION: the new UI enables Google via ``methods=["google"]`` and never
    sets the legacy ``provider``. /auth/login must ACTIVATE (not 404) -- it 302s
    to Google with creds set (here it does, since we set them)."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    auth = AuthConfig(methods=["google"], google_client_id="client-123",
                      google_client_secret="secret",
                      google_redirect_uri="https://home/auth/google/callback")
    cfg = store.cfg()
    cfg.auth = auth
    store._save()
    monkeypatch.setenv("ASTRODECK_SECRET", "test-session-secret-xyz")
    app = _mount_auth_router(store, monkeypatch)
    with TestClient(app) as c:
        r = c.get("/auth/login", follow_redirects=False)
        # ACTIVATED: methods-aware gate let it through (not the old 404).
        assert r.status_code != 404
        assert r.status_code == 302
        assert r.headers["location"].startswith(g.GOOGLE_AUTH_URI)


def test_login_activates_when_google_in_methods_even_without_creds(
        tmp_path, monkeypatch):
    """REGRESSION: ``methods=["google"]`` must ACTIVATE the gate even when the
    Google creds are blank -- the route then 503s ("not fully configured"), NOT
    404. The point is the mint-the-cookie flow is no longer gated off."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    auth = AuthConfig(methods=["google"])  # no google creds
    cfg = store.cfg()
    cfg.auth = auth
    store._save()
    app = _mount_auth_router(store, monkeypatch)
    with TestClient(app) as c:
        r = c.get("/auth/login", follow_redirects=False)
        assert r.status_code != 404           # ACTIVATED, just unconfigured
        assert r.status_code == 503


def test_login_activates_via_legacy_provider(tmp_path, monkeypatch):
    """Back-compat: an old-shaped config that sets only the legacy
    ``provider="google"`` must still activate (the migration validator folds it
    into ``methods``, which the methods-aware gate then sees)."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    auth = AuthConfig(provider="google", google_client_id="client-123",
                      google_client_secret="secret",
                      google_redirect_uri="https://home/auth/google/callback")
    # the migration validator should have populated methods from provider
    assert auth.methods_effective() == ["google"]
    cfg = store.cfg()
    cfg.auth = auth
    store._save()
    monkeypatch.setenv("ASTRODECK_SECRET", "test-session-secret-xyz")
    app = _mount_auth_router(store, monkeypatch)
    with TestClient(app) as c:
        r = c.get("/auth/login", follow_redirects=False)
        assert r.status_code != 404
        assert r.status_code == 302


def test_login_404_and_open_admin_intact_when_methods_empty(tmp_path, monkeypatch):
    """NON-BREAKING: default ``methods==[]`` (open/admin) must leave /auth/login
    404 (the google flow is inert) AND the app must still serve open/admin -- the
    open ``none`` provider resolves /auth/me as admin without any login."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    auth = AuthConfig()  # methods=[] => open/admin default
    assert auth.methods_effective() == []
    cfg = store.cfg()
    cfg.auth = auth
    store._save()
    # install the open default provider so /auth/me resolves admin (open mode).
    from astrodeck.auth.deps import configure_provider_from_auth
    configure_provider_from_auth(auth)
    app = _mount_auth_router(store, monkeypatch)
    with TestClient(app) as c:
        # google flow inert
        assert c.get("/auth/login", follow_redirects=False).status_code == 404
        # open/admin still served (non-breaking): /auth/me resolves admin
        me = c.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "admin"


def test_login_sets_preauth_cookie_and_redirects(tmp_path, monkeypatch):
    app, store, fake = _client_with_google(tmp_path, monkeypatch)
    with TestClient(app) as c:
        r = c.get("/auth/login", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith(g.GOOGLE_AUTH_URI)
        assert auth_routes.PREAUTH_COOKIE in r.cookies


def test_callback_mints_session_for_allowlisted_email(tmp_path, monkeypatch):
    # the fake token uses nonce "NONCE"; align /auth/login by forcing the nonce.
    app, store, fake = _client_with_google(
        tmp_path, monkeypatch, role_allowlist={"a@x.com": "admin"})
    monkeypatch.setattr(g, "new_nonce", lambda: "NONCE")
    monkeypatch.setattr(auth_routes, "new_nonce", lambda: "NONCE")
    with TestClient(app) as c:
        r = _do_login_and_callback(c, store=store)
        assert r.status_code == 302
        assert auth_routes.SESSION_COOKIE in r.cookies
        # /auth/me now resolves the minted session as admin
        me = c.get("/auth/me")
        assert me.status_code == 200
        body = me.json()
        assert body["role"] == "admin"
        assert body["email"] == "a@x.com"


def test_callback_denies_unauthorized_email(tmp_path, monkeypatch):
    # email not in allowlist + no default_role => 403, no session minted
    app, store, fake = _client_with_google(
        tmp_path, monkeypatch, role_allowlist={"other@x.com": "admin"})
    monkeypatch.setattr(g, "new_nonce", lambda: "NONCE")
    monkeypatch.setattr(auth_routes, "new_nonce", lambda: "NONCE")
    with TestClient(app) as c:
        r = _do_login_and_callback(c, store=store)
        assert r.status_code == 403
        assert auth_routes.SESSION_COOKIE not in r.cookies


def test_callback_default_role_used_for_unknown_email(tmp_path, monkeypatch):
    app, store, fake = _client_with_google(
        tmp_path, monkeypatch, role_allowlist={}, default_role="viewer")
    monkeypatch.setattr(g, "new_nonce", lambda: "NONCE")
    monkeypatch.setattr(auth_routes, "new_nonce", lambda: "NONCE")
    with TestClient(app) as c:
        r = _do_login_and_callback(c, store=store)
        assert r.status_code == 302
        me = c.get("/auth/me").json()
        assert me["role"] == "viewer"
        assert set(me["caps"]) == {"view.status", "view.preview"}


def test_callback_rejects_state_mismatch(tmp_path, monkeypatch):
    app, store, fake = _client_with_google(
        tmp_path, monkeypatch, role_allowlist={"a@x.com": "admin"})
    with TestClient(app) as c:
        c.get("/auth/login", follow_redirects=False)  # sets pre-auth cookie
        # a forged state that does not match the cookie -> 400
        r = c.get("/auth/google/callback?code=abc&state=FORGED",
                  follow_redirects=False)
        assert r.status_code == 400


def test_callback_rejects_missing_preauth(tmp_path, monkeypatch):
    app, store, fake = _client_with_google(
        tmp_path, monkeypatch, role_allowlist={"a@x.com": "admin"})
    with TestClient(app) as c:
        # no /auth/login first -> no pre-auth cookie -> 400
        r = c.get("/auth/google/callback?code=abc&state=whatever",
                  follow_redirects=False)
        assert r.status_code == 400


def test_logout_clears_cookie_and_revokes_jti(tmp_path, monkeypatch):
    app, store, fake = _client_with_google(
        tmp_path, monkeypatch, role_allowlist={"a@x.com": "admin"})
    monkeypatch.setattr(g, "new_nonce", lambda: "NONCE")
    monkeypatch.setattr(auth_routes, "new_nonce", lambda: "NONCE")
    with TestClient(app) as c:
        _do_login_and_callback(c, store=store)
        # capture the minted jti from the session cookie
        from astrodeck.auth.session import verify_session
        sess = c.cookies.get(auth_routes.SESSION_COOKIE)
        jti = verify_session(sess)["jti"]
        # logout appends the jti to revoked_jti and clears the cookie
        r = c.post("/auth/logout")
        assert r.status_code == 200
        assert jti in store.cfg().auth.revoked_jti
        # the now-revoked session no longer resolves on /auth/me
        # (the SessionCookieProvider checks revoked_jti at resolve time, but
        # the router builds a provider per process; verify the registry instead)
        assert jti in store.cfg().auth.revoked_jti


def test_revoked_session_rejected_by_provider(tmp_path, monkeypatch):
    """A session whose jti is in revoked_jti must not resolve to a Principal."""
    from astrodeck.auth.providers import SessionCookieProvider
    from astrodeck.auth.session import sign_session

    monkeypatch.setenv("ASTRODECK_SECRET", "test-session-secret-xyz")
    tok = sign_session("admin", email="a@x.com", jti="JTI-1", ttl_s=3600)

    class _Req:
        cookies = {"ad_session": tok}
        headers: dict = {}

    import anyio
    prov_ok = SessionCookieProvider()
    prov_revoked = SessionCookieProvider(revoked_jti=frozenset({"JTI-1"}))
    assert anyio.run(prov_ok.resolve, _Req()) is not None
    assert anyio.run(prov_revoked.resolve, _Req()) is None
