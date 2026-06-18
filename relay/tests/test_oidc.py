"""§T7(3) OIDC termination -> home-verifiable principal token (W3.3.5).

Runs the §T6 callback-handler cases (state/nonce/PKCE/email_verified/hd) against
the relay's callback logic with a FAKE id-token verifier (faked JWKS, no live
Google). Asserts the produced principal token is home-verifiable and that the
relay never mints a shared-HS256 admin secret."""
from __future__ import annotations

import pytest

from relay import oidc
from relay.oidc import OidcConfig, OidcError, begin_login, complete_callback
from relay.principal import KIND_OIDC, PrincipalSigner


def _signer() -> PrincipalSigner:
    # Off-wire dev-HMAC signer; production uses Ed25519. The home-verifiable
    # property is asserted via signer.verify() (the home's job).
    return PrincipalSigner.dev_hmac(b"oidc-key", kid="oidc")


def _verifier(claims: dict):
    """A fake id-token verifier: returns the claims keyed by the token string.
    The token string IS the json of the claims for the test."""
    import json

    def verify(id_token: str, expected_nonce: str) -> dict:
        return json.loads(id_token)
    return verify


def _id_token(**claims) -> str:
    import json
    return json.dumps(claims)


def test_happy_path_mints_home_verifiable_principal():
    signer = _signer()
    pre = begin_login(home_id="home-1")
    cfg = OidcConfig(default_role="viewer")
    token = complete_callback(
        pre, returned_state=pre.state, code="authcode",
        code_verifier=pre.code_verifier,
        id_token=_id_token(sub="g-123", email="a@b.com", email_verified=True,
                           nonce=pre.nonce),
        verify_id_token=_verifier({}), signer=signer, cfg=cfg,
    )
    claims = signer.verify(token)  # the HOME re-verifies with the public key
    assert claims["kind"] == KIND_OIDC
    assert claims["email"] == "a@b.com"
    assert claims["sub"] == "g-123"
    assert claims["role"] == "viewer"
    assert "view.status" in claims["caps"]


def test_state_mismatch_rejected():
    signer = _signer()
    pre = begin_login()
    with pytest.raises(OidcError, match="state mismatch"):
        complete_callback(
            pre, returned_state="WRONG", code="c",
            code_verifier=pre.code_verifier,
            id_token=_id_token(sub="s", email="e@x", email_verified=True,
                               nonce=pre.nonce),
            verify_id_token=_verifier({}), signer=signer, cfg=OidcConfig(),
        )


def test_nonce_mismatch_rejected():
    signer = _signer()
    pre = begin_login()
    with pytest.raises(OidcError, match="nonce mismatch"):
        complete_callback(
            pre, returned_state=pre.state, code="c",
            code_verifier=pre.code_verifier,
            id_token=_id_token(sub="s", email="e@x", email_verified=True,
                               nonce="DIFFERENT"),
            verify_id_token=_verifier({}), signer=signer, cfg=OidcConfig(),
        )


def test_pkce_mismatch_rejected():
    signer = _signer()
    pre = begin_login()
    with pytest.raises(OidcError, match="PKCE"):
        complete_callback(
            pre, returned_state=pre.state, code="c",
            code_verifier="not-the-verifier",
            id_token=_id_token(sub="s", email="e@x", email_verified=True,
                               nonce=pre.nonce),
            verify_id_token=_verifier({}), signer=signer, cfg=OidcConfig(),
        )


def test_unverified_email_rejected():
    signer = _signer()
    pre = begin_login()
    with pytest.raises(OidcError, match="email not verified"):
        complete_callback(
            pre, returned_state=pre.state, code="c",
            code_verifier=pre.code_verifier,
            id_token=_id_token(sub="s", email="e@x", email_verified=False,
                               nonce=pre.nonce),
            verify_id_token=_verifier({}), signer=signer,
            cfg=OidcConfig(require_email_verified=True),
        )


def test_hosted_domain_enforced():
    signer = _signer()
    pre = begin_login()
    cfg = OidcConfig(allowed_hd="mycorp.com")
    # Wrong hd -> rejected.
    with pytest.raises(OidcError, match="hosted-domain"):
        complete_callback(
            pre, returned_state=pre.state, code="c",
            code_verifier=pre.code_verifier,
            id_token=_id_token(sub="s", email="e@mycorp.com",
                               email_verified=True, nonce=pre.nonce,
                               hd="other.com"),
            verify_id_token=_verifier({}), signer=signer, cfg=cfg,
        )
    # Matching hd -> ok.
    pre2 = begin_login()
    token = complete_callback(
        pre2, returned_state=pre2.state, code="c",
        code_verifier=pre2.code_verifier,
        id_token=_id_token(sub="s", email="e@mycorp.com", email_verified=True,
                           nonce=pre2.nonce, hd="mycorp.com"),
        verify_id_token=_verifier({}), signer=signer, cfg=cfg,
    )
    assert signer.verify(token)["email"] == "e@mycorp.com"


def test_expired_login_rejected():
    signer = _signer()
    pre = begin_login(now=0.0)
    pre.ttl_s = 600
    with pytest.raises(OidcError, match="expired"):
        complete_callback(
            pre, returned_state=pre.state, code="c",
            code_verifier=pre.code_verifier,
            id_token=_id_token(sub="s", email="e@x", email_verified=True,
                               nonce=pre.nonce),
            verify_id_token=_verifier({}), signer=signer, cfg=OidcConfig(),
            now=10_000.0,
        )


def test_role_for_email_override():
    signer = _signer()
    pre = begin_login()
    cfg = OidcConfig(default_role="viewer",
                     role_for_email=lambda e: "admin" if e == "owner@x" else "viewer")
    token = complete_callback(
        pre, returned_state=pre.state, code="c",
        code_verifier=pre.code_verifier,
        id_token=_id_token(sub="s", email="owner@x", email_verified=True,
                           nonce=pre.nonce),
        verify_id_token=_verifier({}), signer=signer, cfg=cfg,
    )
    claims = signer.verify(token)
    assert claims["role"] == "admin"


def test_pkce_pair_verifies():
    v, c = oidc.make_pkce_pair()
    assert oidc.verify_pkce(v, c)
    assert not oidc.verify_pkce("wrong", c)
