"""Bidirectional header transform (W3.2 header-transform rules).

A broken ``Set-Cookie`` rewrite is a SILENT remote-only auth break that LAN
tests cannot catch, so this is pure-function and unit-tested directly. Two
directions:

INBOUND (browser -> relay -> scope ``REQ_OPEN.headers``):
  * STRIP every auth/identity carrier -- ``authorization``, ``x-auth-token``,
    and the session ``cookie`` -- so a compromised relay can NEVER forward a
    forged admin credential as a trusted header. The principal rides the signed
    ``principal_token`` (a SEPARATE REQ_OPEN header), never a forwardable auth
    header. ``ASTRODECK_TOKEN`` header auth is NOT a valid tunnel carrier.
  * DROP hop-by-hop headers (they describe the browser<->relay hop, not the
    tunnelled request).

OUTBOUND (scope ``RESP_HEAD.headers`` -> relay -> browser):
  * DROP hop-by-hop headers and any ``content-length`` (the relay re-frames the
    body as chunked over the tunnel, so a stale length would corrupt the
    response). The browser-facing server sets its own framing.
  * REWRITE ``Set-Cookie`` so a home-issued session cookie is valid on the RELAY
    origin the browser actually talks to: drop the home ``Domain``, force
    ``Secure``, and flip ``SameSite=Strict -> Lax`` (the cross-origin relay web
    case, W2.3). Without this, cookie auth silently breaks remotely.

Header lists are ``[[name, value], ...]`` (the ASGI / tunnel shape: repeated
headers preserved, order preserved). Name matching is case-insensitive; the
ORIGINAL casing of surviving headers is preserved on the way through.
"""
from __future__ import annotations

from typing import Iterable

# Hop-by-hop headers (RFC 7230 6.1) -- meaningful only for a single transport
# hop, never forwarded across the tunnel in EITHER direction.
HOP_BY_HOP = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
})

# Inbound auth/identity carriers stripped before the scope builds its request.
# The principal is injected ONLY from the signed principal_token; none of these
# is a valid tunnel auth carrier (W3.3.2 step 1).
STRIPPED_INBOUND = frozenset({
    "authorization",
    "x-auth-token",
    "cookie",
})

HeaderList = list  # list[list[str]] -- [[name, value], ...]


def _lower(name) -> str:
    if isinstance(name, bytes):
        name = name.decode("latin-1")
    return name.lower()


def _as_str(v) -> str:
    return v.decode("latin-1") if isinstance(v, bytes) else v


def transform_request_headers(headers: Iterable) -> HeaderList:
    """INBOUND transform: drop hop-by-hop, STRIP auth/identity carriers.

    Returns a new ``[[name, value], ...]`` list (surviving headers keep their
    original name + order). The principal_token header, if the relay added one,
    is left in place -- it is the ONLY trusted identity carrier and is verified
    (not stripped) at the home."""
    out: HeaderList = []
    for name, value in headers:
        lname = _lower(name)
        if lname in HOP_BY_HOP or lname in STRIPPED_INBOUND:
            continue
        out.append([_as_str(name), _as_str(value)])
    return out


def transform_response_headers(headers: Iterable, *, relay_origin: str = "",
                               relay_https: bool = True) -> HeaderList:
    """OUTBOUND transform: drop hop-by-hop + ``content-length`` (chunked
    re-framing) and rewrite every ``Set-Cookie`` for the relay origin.

    ``relay_origin`` is the relay's host (e.g. ``relay.example``); when given it
    REPLACES any ``Domain=`` in a ``Set-Cookie`` with that host. ``relay_https``
    (default True) forces the ``Secure`` attribute. Returns a new header list."""
    out: HeaderList = []
    for name, value in headers:
        lname = _lower(name)
        if lname in HOP_BY_HOP or lname == "content-length":
            # content-length is recomputed by the browser-facing server for the
            # chunked tunnel body; transfer-encoding is hop-by-hop (dropped).
            continue
        if lname == "set-cookie":
            out.append([
                _as_str(name),
                rewrite_set_cookie(_as_str(value), relay_origin=relay_origin,
                                   relay_https=relay_https),
            ])
            continue
        out.append([_as_str(name), _as_str(value)])
    return out


def rewrite_set_cookie(value: str, *, relay_origin: str = "",
                       relay_https: bool = True) -> str:
    """Rewrite one ``Set-Cookie`` value for the relay origin.

    Transforms applied:
      * ``Domain=home.local`` -> ``Domain=<relay_origin>`` (or DROP Domain if no
        relay_origin is supplied -- a host-only cookie is valid on the relay
        origin the browser talks to).
      * ``SameSite=Strict`` -> ``SameSite=Lax`` (cross-origin relay web case);
        add ``SameSite=Lax`` if absent.
      * force ``Secure`` when ``relay_https`` (the relay terminates HTTPS).
    The cookie name=value pair and ``Path``/``Max-Age``/``HttpOnly``/``Expires``
    are preserved. Attribute order: name=value first, then the surviving/edited
    attributes."""
    # Split into the name=value pair (first part) and attributes.
    parts = [p.strip() for p in value.split(";")]
    if not parts:
        return value
    nv = parts[0]
    attrs = parts[1:]

    out_attrs: list[str] = []
    have_samesite = False
    have_secure = False
    for attr in attrs:
        if not attr:
            continue
        key = attr.split("=", 1)[0].strip().lower()
        if key == "domain":
            if relay_origin:
                out_attrs.append(f"Domain={relay_origin}")
            # else: drop Domain entirely (host-only cookie on the relay origin)
            continue
        if key == "samesite":
            have_samesite = True
            sval = attr.split("=", 1)[1].strip().lower() if "=" in attr else ""
            if sval == "strict":
                out_attrs.append("SameSite=Lax")
            else:
                out_attrs.append(attr)
            continue
        if key == "secure":
            have_secure = True
            out_attrs.append("Secure")
            continue
        out_attrs.append(attr)

    if not have_samesite:
        out_attrs.append("SameSite=Lax")
    if relay_https and not have_secure:
        out_attrs.append("Secure")

    return "; ".join([nv, *out_attrs])
