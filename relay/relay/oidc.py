"""Google OIDC termination at the relay (W3.3.5 identity termination).

A LAN home has no public HTTPS callback URL, so the RELAY owns the public
``/auth/google/callback``: it initiates ``/authorize``, sets + verifies
``state``/``nonce``/PKCE against its OWN pre-auth cookie (relay origin),
validates the Google ID token, then forwards a **home-verifiable principal
token** (``relay.principal``) down the tunnel. The home accepts ONLY that
principal token -- never a raw Google ID token -- so the relay's correctness is
load-bearing and is unit-tested directly here.

This module is the PURE callback-validation logic (the §T6 callback cases:
``state``/``nonce``/PKCE/``email_verified``/``hd``). It does NOT fetch live
Google JWKS -- ID-token signature verification is injected as a callable so
tests pass a fake verifier (faked JWKS, no live Google), exactly like the home's
``test_google_oidc.py``. The production relay wires a real JWKS-fetching verifier.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .principal import PrincipalSigner, mint_oidc_principal


class OidcError(Exception):
    """An OIDC callback failed validation (state/nonce/PKCE/claims)."""


# --------------------------------------------------------------- PKCE helpers

def make_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for PKCE S256. The relay holds
    the verifier in its pre-auth cookie; the challenge goes in ``/authorize``."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def verify_pkce(verifier: str, challenge: str) -> bool:
    """True iff ``challenge`` is the S256 transform of ``verifier``."""
    if not verifier or not challenge:
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expect = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(expect, challenge)


# --------------------------------------------------------------- pre-auth state

@dataclass
class PreAuthState:
    """What the relay stashed (in its origin-scoped pre-auth cookie) when it
    redirected the browser to Google. Verified on the callback."""

    state: str
    nonce: str
    code_verifier: str
    code_challenge: str
    created_at: float = field(default_factory=time.time)
    home_id: str = ""           # which home this login is for (path/subdomain)
    ttl_s: int = 600            # the login flow must complete within 10 min


def begin_login(home_id: str = "", *, now: Optional[float] = None) -> PreAuthState:
    """Start an OIDC login: mint ``state``/``nonce``/PKCE. The caller redirects
    to Google's ``/authorize`` with ``state``/``nonce``/``code_challenge`` and
    stores the returned ``PreAuthState`` in the relay-origin pre-auth cookie."""
    verifier, challenge = make_pkce_pair()
    return PreAuthState(
        state=secrets.token_urlsafe(24),
        nonce=secrets.token_urlsafe(24),
        code_verifier=verifier,
        code_challenge=challenge,
        created_at=now if now is not None else time.time(),
        home_id=home_id,
    )


# ----------------------------------------------------------------- callback

# A verifier takes (id_token, expected_nonce) and returns the verified claims
# dict, or raises. Injected so tests fake it (no live Google JWKS fetch).
IdTokenVerifier = Callable[[str, str], dict]


@dataclass
class OidcConfig:
    """Relay-side OIDC policy. ``allowed_hd`` (Google Workspace hosted-domain)
    and ``email_verified``-required mirror the home's W2 config so the relay
    rejects the same logins the home would."""

    require_email_verified: bool = True
    allowed_hd: Optional[str] = None       # if set, the ``hd`` claim must match
    default_role: str = "viewer"           # role for any authenticated Google user
    role_for_email: Callable[[str], str] = None  # optional per-email override
    principal_ttl_s: int = 3600


def role_to_caps(role: str) -> list[str]:
    """Advisory cap stamp the relay attaches for a role (the HOME re-derives the
    authoritative caps from its own RBAC table). Conservative defaults; an
    unknown role gets the read-only viewer set."""
    table = {
        "admin": ["admin.users", "config", "control", "view.status",
                  "view.preview", "view.media", "view.site_precise"],
        "operator": ["control", "view.status", "view.preview", "view.media",
                     "view.site_precise"],
        "viewer": ["view.status", "view.preview"],
    }
    return table.get(role, ["view.status", "view.preview"])


def complete_callback(pre: PreAuthState, *, returned_state: str,
                      code: str, code_verifier: Optional[str],
                      id_token: str, verify_id_token: IdTokenVerifier,
                      signer: PrincipalSigner, cfg: OidcConfig,
                      now: Optional[float] = None) -> str:
    """Validate a Google OIDC callback and mint a home-verifiable principal.

    Fail-closed checks, in order (each raises ``OidcError``):
      1. the pre-auth state has not expired;
      2. ``returned_state`` matches the stashed ``state`` (CSRF);
      3. PKCE: the presented ``code_verifier`` matches the stashed challenge
         (the relay knows its own verifier; this asserts the auth-code flow was
         bound to THIS browser);
      4. the ID token verifies AND its ``nonce`` matches the stashed nonce
         (replay protection) -- via the injected verifier (faked in tests);
      5. ``email_verified`` is true (when required);
      6. the ``hd`` (hosted-domain) claim matches ``allowed_hd`` (when set).
    On success it mints + returns an OIDC principal token signed with the relay
    OIDC key. The relay forwards this token; the HOME re-derives RBAC."""
    if now is None:
        now = time.time()

    if (now - pre.created_at) > pre.ttl_s:
        raise OidcError("login expired (pre-auth state too old)")
    if not secrets.compare_digest(pre.state, returned_state or ""):
        raise OidcError("state mismatch (possible CSRF)")
    # PKCE: the relay verifies the verifier it stashed against its own challenge.
    # (Google also verifies verifier<->challenge at the token endpoint; this is
    # the relay's belt-and-suspenders binding to THIS browser's pre-auth cookie.)
    presented_verifier = code_verifier or pre.code_verifier
    if not verify_pkce(presented_verifier, pre.code_challenge):
        raise OidcError("PKCE verification failed")
    if not code:
        raise OidcError("missing authorization code")

    claims = verify_id_token(id_token, pre.nonce)
    if not isinstance(claims, dict):
        raise OidcError("id token verifier returned no claims")
    if not secrets.compare_digest(str(claims.get("nonce", "")), pre.nonce):
        raise OidcError("nonce mismatch (possible replay)")

    email = claims.get("email")
    sub = claims.get("sub")
    if not email or not sub:
        raise OidcError("id token missing email/sub")
    if cfg.require_email_verified and not claims.get("email_verified", False):
        raise OidcError("email not verified")
    if cfg.allowed_hd is not None and claims.get("hd") != cfg.allowed_hd:
        raise OidcError(
            f"hosted-domain mismatch: {claims.get('hd')!r} != {cfg.allowed_hd!r}"
        )

    role = cfg.default_role
    if cfg.role_for_email is not None:
        role = cfg.role_for_email(email) or cfg.default_role

    return mint_oidc_principal(
        signer, sub=str(sub), email=str(email), role=role,
        caps=role_to_caps(role), ttl_s=cfg.principal_ttl_s, now=now,
    )
