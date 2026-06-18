"""§T7 principal-token minting: home-verifiable, viewer-link can't carry admin.

Asserts the relay produces a home-verifiable principal token (NOT a shared HS256
secret in production -- the dev-HMAC path is the off-wire test fallback) and that
a viewer link is viewer-ONLY (forbidden caps rejected at mint time)."""
from __future__ import annotations

import pytest

from relay.principal import (
    KIND_OIDC,
    KIND_VIEWER,
    VIEWER_LINK_CAPS,
    PrincipalError,
    PrincipalSigner,
    ViewerLinkSpec,
    mint_oidc_principal,
    mint_viewer_link_token,
    viewer_link_caps,
)


def _signer():
    return PrincipalSigner.dev_hmac(b"k", kid="t")


def test_oidc_principal_round_trips():
    s = _signer()
    tok = mint_oidc_principal(s, sub="g1", email="a@b", role="operator",
                              caps=["control", "view.status"], ttl_s=60, now=0)
    claims = s.verify(tok, now=10)
    assert claims["kind"] == KIND_OIDC
    assert claims["sub"] == "g1"
    assert claims["role"] == "operator"
    assert set(claims["caps"]) == {"control", "view.status"}


def test_expired_principal_rejected():
    s = _signer()
    tok = mint_oidc_principal(s, sub="g1", email="a@b", role="viewer",
                              caps=["view.status"], ttl_s=60, now=0)
    with pytest.raises(PrincipalError, match="expired"):
        s.verify(tok, now=1000)


def test_tampered_token_rejected():
    s = _signer()
    tok = mint_oidc_principal(s, sub="g1", email="a@b", role="viewer",
                              caps=["view.status"], ttl_s=60, now=0)
    # Flip a payload byte.
    h, p, sig = tok.split(".")
    bad = ".".join([h, p[:-1] + ("A" if p[-1] != "A" else "B"), sig])
    with pytest.raises(PrincipalError):
        s.verify(bad)


def test_separate_key_cannot_verify_other_keys_token():
    """OIDC and viewer-link keys are SEPARATE: an OIDC-signed token does not
    verify under the viewer signer (so a link-minting bug can't forge admin)."""
    oidc_signer = PrincipalSigner.dev_hmac(b"oidc-key")
    viewer_signer = PrincipalSigner.dev_hmac(b"viewer-key")
    tok = mint_oidc_principal(oidc_signer, sub="g", email="a@b", role="admin",
                              caps=["admin.users"], ttl_s=60, now=0)
    with pytest.raises(PrincipalError):
        viewer_signer.verify(tok, now=1)


def test_viewer_link_default_caps_are_read_only():
    s = _signer()
    tok, jti = mint_viewer_link_token(s, ViewerLinkSpec(label="friend"), now=0)
    claims = s.verify(tok, now=1)
    assert claims["kind"] == KIND_VIEWER
    assert claims["role"] == "viewer"
    assert set(claims["caps"]) == set(VIEWER_LINK_CAPS)
    assert claims["jti"] == jti


def test_viewer_link_opt_in_media_and_precise():
    s = _signer()
    spec = ViewerLinkSpec(extra_caps=("view.media", "view.site_precise"))
    tok, _ = mint_viewer_link_token(s, spec, now=0)
    caps = set(s.verify(tok, now=1)["caps"])
    assert "view.media" in caps
    assert "view.site_precise" in caps


def test_viewer_link_cannot_carry_admin_config_control():
    for forbidden in ("admin.users", "config", "config.site_optics",
                      "control.mount", "control.power"):
        with pytest.raises(PrincipalError, match="privileged"):
            viewer_link_caps((forbidden,))


def test_viewer_link_token_mint_rejects_forbidden_caps():
    s = _signer()
    spec = ViewerLinkSpec(extra_caps=("control.mount",))
    with pytest.raises(PrincipalError):
        mint_viewer_link_token(s, spec, now=0)


def test_dev_signer_flagged_insecure():
    assert _signer().is_dev() is True
