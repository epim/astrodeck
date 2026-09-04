"""§T7(2) bidirectional header transform (pure-function, fully off-wire).

A broken Set-Cookie rewrite is a SILENT remote-only auth break LAN tests cannot
catch, so it is asserted directly."""
from __future__ import annotations

from relay.headers import (
    rewrite_set_cookie,
    transform_request_headers,
    transform_response_headers,
)

import pytest


def test_inbound_strips_auth_carriers():
    headers = [
        ["host", "relay.example"],
        ["Authorization", "Bearer forged-admin"],
        ["X-Auth-Token", "also-forged"],
        ["Cookie", "ad_session=stolen"],
        ["Accept", "text/html"],
    ]
    out = transform_request_headers(headers)
    names = {n.lower() for n, _ in out}
    assert "authorization" not in names
    assert "x-auth-token" not in names
    assert "cookie" not in names
    # Non-auth headers survive with original casing.
    assert ["Accept", "text/html"] in out
    assert ["host", "relay.example"] in out


def test_inbound_drops_hop_by_hop():
    headers = [["Connection", "keep-alive, X-Remove"],
               ["Upgrade", "websocket"], ["X-Remove", "hop-only"],
               ["X-Real", "v"]]
    out = transform_request_headers(headers)
    names = {n.lower() for n, _ in out}
    assert "connection" not in names
    assert "upgrade" not in names
    assert "x-remove" not in names
    assert ["X-Real", "v"] in out


@pytest.mark.parametrize("value", ["ok\x00bad", "ok\x1fbad", "ok\x7fbad"])
def test_inbound_rejects_non_http_control_characters(value):
    with pytest.raises(ValueError, match="header value"):
        transform_request_headers([["X-Test", value]])


@pytest.mark.parametrize("value", ["ok\x00bad", "ok\x1fbad", "ok\x7fbad"])
def test_outbound_rejects_non_http_control_characters(value):
    with pytest.raises(ValueError, match="header value"):
        transform_response_headers([["X-Test", value]])


def test_current_home_auth_mode_forwards_only_cookie_bearer():
    headers = [["Cookie", "ad_session=signed"],
               ["Authorization", "Bearer raw-admin"],
               ["X-Auth-Token", "raw-admin"]]
    out = transform_request_headers(headers, forward_cookie=True)
    names = {n.lower() for n, _ in out}
    assert "cookie" in names
    assert "authorization" not in names
    assert "x-auth-token" not in names


def test_inbound_principal_token_survives():
    """The relay's signed principal_token is the ONE trusted identity carrier --
    it is NOT stripped (the home verifies it)."""
    headers = [["principal_token", "h.p.s"], ["authorization", "x"]]
    out = transform_request_headers(headers)
    names = {n.lower() for n, _ in out}
    assert "principal_token" in names
    assert "authorization" not in names


def test_outbound_drops_content_length_and_hop_by_hop():
    headers = [
        ["Content-Length", "1234"],
        ["Transfer-Encoding", "chunked"],
        ["Connection", "close"],
        ["Content-Type", "image/png"],
    ]
    out = transform_response_headers(headers, relay_origin="relay.example")
    names = {n.lower() for n, _ in out}
    assert "content-length" not in names
    assert "transfer-encoding" not in names
    assert "connection" not in names
    assert ["Content-Type", "image/png"] in out


def test_set_cookie_domain_is_removed_for_a_host_only_cookie():
    sc = "ad_session=abc; Domain=home.local; Path=/; HttpOnly; SameSite=Strict"
    out = rewrite_set_cookie(sc, relay_origin="relay.example", relay_https=True)
    assert "Domain=relay.example" not in out
    assert "Domain=home.local" not in out
    # The normal session stays Strict; only the OAuth pre-auth cookie explicitly
    # asks for Lax.
    assert "SameSite=Strict" in out
    assert "SameSite=Lax" not in out
    # Secure forced (relay terminates HTTPS).
    assert "Secure" in out
    # Name=value + Path + HttpOnly preserved.
    assert out.startswith("ad_session=abc")
    assert "Path=/" in out
    assert "HttpOnly" in out


def test_set_cookie_without_domain_stays_host_only_and_gets_strict_secure():
    sc = "ad_session=abc; Path=/; HttpOnly"
    out = rewrite_set_cookie(sc, relay_origin="relay.example")
    assert "Domain" not in out  # host-only on the relay origin
    assert "SameSite=Strict" in out
    assert "Secure" in out


def test_set_cookie_no_relay_origin_drops_domain():
    sc = "ad_session=abc; Domain=home.local; Path=/"
    out = rewrite_set_cookie(sc, relay_origin="")
    assert "Domain" not in out


def test_outbound_rewrites_set_cookie_in_list():
    headers = [["Set-Cookie", "ad_session=abc; Domain=home.local; SameSite=Strict"]]
    out = transform_response_headers(headers, relay_origin="relay.example")
    assert len(out) == 1
    name, value = out[0]
    assert name.lower() == "set-cookie"
    assert "Domain" not in value
    assert "SameSite=Strict" in value


def test_set_cookie_non_https_does_not_force_secure():
    sc = "ad_session=abc; Path=/"
    out = rewrite_set_cookie(sc, relay_origin="relay.example", relay_https=False)
    assert "Secure" not in out
