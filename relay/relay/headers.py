"""Bidirectional header transform (W3.2 header-transform rules).

A broken ``Set-Cookie`` rewrite is a SILENT remote-only auth break that LAN
tests cannot catch, so this is pure-function and unit-tested directly. Two
directions:

INBOUND (browser -> relay -> scope ``REQ_OPEN.headers``):
  * STRIP raw admin-token carriers (``authorization`` and ``x-auth-token``).
    Cookie forwarding is opt-in for the current home-terminated-auth mode; in
    that mode the TLS-terminating relay is explicitly a trusted bearer-token
    intermediary. A future blind relay can leave it disabled.
  * DROP hop-by-hop headers (they describe the browser<->relay hop, not the
    tunnelled request).

OUTBOUND (scope ``RESP_HEAD.headers`` -> relay -> browser):
  * DROP hop-by-hop headers and any ``content-length`` (the relay re-frames the
    body as chunked over the tunnel, so a stale length would corrupt the
    response). The browser-facing server sets its own framing.
  * REWRITE ``Set-Cookie`` so a home-issued session cookie is valid on the RELAY
    origin the browser actually talks to: always drop ``Domain`` and force
    ``Secure``. Preserve an explicit SameSite policy (the OAuth pre-auth cookie
    needs Lax; the session cookie deliberately remains Strict).

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

_NAME_SEPARATORS = frozenset('()<>@,;:\\"/[]?={} \t')


def _lower(name) -> str:
    if isinstance(name, bytes):
        name = name.decode("latin-1")
    return name.lower()


def _as_str(v) -> str:
    return v.decode("latin-1") if isinstance(v, bytes) else v


def _validated_pair(name, value) -> tuple[str, str]:
    name_text = _as_str(name)
    value_text = _as_str(value)
    if not isinstance(name_text, str) or not isinstance(value_text, str):
        raise ValueError("header names and values must be text or bytes")
    if (not name_text
            or any(ord(ch) <= 32 or ord(ch) >= 127 or ch in _NAME_SEPARATORS
                   for ch in name_text)):
        raise ValueError("invalid HTTP header name")
    if any((ord(ch) < 32 and ch != "\t") or ord(ch) == 127 or ord(ch) > 255
           for ch in value_text):
        raise ValueError("invalid HTTP header value")
    return name_text, value_text


def transform_request_headers(
        headers: Iterable, *, forward_cookie: bool = False) -> HeaderList:
    """INBOUND transform: drop hop-by-hop, STRIP raw auth carriers.

    Returns a new ``[[name, value], ...]`` list (surviving headers keep their
    original name + order). The principal_token header, if the relay added one,
    is left in place -- it is verified (not blindly trusted) at the home.

    ``forward_cookie`` exists for the current home-terminated-auth deployment:
    the relay is then explicitly a trusted bearer-token intermediary. It keeps
    only the Cookie carrier; raw admin-token headers remain forbidden."""
    pairs = [_validated_pair(*pair) for pair in headers]
    # RFC 7230 also makes every header named by ``Connection`` hop-by-hop.
    connection_named: set[str] = set()
    for name, value in pairs:
        if _lower(name) == "connection":
            connection_named.update(
                token.strip().lower() for token in _as_str(value).split(",")
                if token.strip())

    out: HeaderList = []
    for name, value in pairs:
        lname = _lower(name)
        if lname in HOP_BY_HOP or lname in connection_named:
            continue
        if lname in STRIPPED_INBOUND and not (
                forward_cookie and lname == "cookie"):
            continue
        out.append([name, value])
    return out


def transform_response_headers(headers: Iterable, *, relay_origin: str = "",
                               relay_https: bool = True) -> HeaderList:
    """OUTBOUND transform: drop hop-by-hop + ``content-length`` (chunked
    re-framing) and rewrite every ``Set-Cookie`` for the relay origin.

    ``relay_origin`` is retained for API compatibility, but cookies are always
    narrowed to host-only. ``relay_https`` (default True) forces the ``Secure``
    attribute. Returns a new header list."""
    pairs = [_validated_pair(*pair) for pair in headers]
    connection_named: set[str] = set()
    for name, value in pairs:
        if _lower(name) == "connection":
            connection_named.update(
                token.strip().lower() for token in _as_str(value).split(",")
                if token.strip())
    out: HeaderList = []
    for name, value in pairs:
        lname = _lower(name)
        if (lname in HOP_BY_HOP or lname in connection_named
                or lname == "content-length"):
            # content-length is recomputed by the browser-facing server for the
            # chunked tunnel body; transfer-encoding is hop-by-hop (dropped).
            continue
        if lname == "set-cookie":
            out.append([
                name,
                rewrite_set_cookie(value, relay_origin=relay_origin,
                                   relay_https=relay_https),
            ])
            continue
        out.append([name, value])
    return out


def rewrite_set_cookie(value: str, *, relay_origin: str = "",
                       relay_https: bool = True) -> str:
    """Rewrite one ``Set-Cookie`` value for the relay origin.

    Transforms applied:
      * always DROP ``Domain``. A host-only cookie is valid on the relay origin
        and, unlike a Domain cookie, is never sent to sibling subdomains.
      * preserve an explicit ``SameSite`` value; add ``SameSite=Strict`` if
        absent. OAuth's pre-auth cookie explicitly requests Lax, while the
        normal session remains Strict.
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
            # Never widen the credential to sibling subdomains.
            continue
        if key == "samesite":
            have_samesite = True
            out_attrs.append(attr)
            continue
        if key == "secure":
            have_secure = True
            out_attrs.append("Secure")
            continue
        out_attrs.append(attr)

    if not have_samesite:
        out_attrs.append("SameSite=Strict")
    if relay_https and not have_secure:
        out_attrs.append("Secure")

    return "; ".join([nv, *out_attrs])
