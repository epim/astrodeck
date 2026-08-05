"""The manual-scan SSRF guard must dial the address it approved.

``validate_scan_host`` resolved the host, checked the resulting IPs and then
threw them away: ``query_server`` put the NAME back in the URL and ``discover_nina``
called ``socket.gethostbyname`` a second time, so both re-resolved. An attacker's
DNS record that answers public on the first lookup and ``127.0.0.1`` on the second
walked straight through -- the exact DNS-rebinding attack the guard's own comment
claimed to defeat, on routes a VIEWER can reach.

The resolver stub below flips to loopback on its second answer. Every test here
asserts the connection still targets the FIRST (approved) address.
"""
from __future__ import annotations

import socket

import pytest

from astrodeck.devices import alpaca as alpaca_module
from astrodeck.devices import nina as nina_module
from astrodeck.devices.alpaca import AlpacaScanError, query_server, validate_scan_host

_SAFE_IP = "93.184.216.34"    # routable + public: what the guard approves
_REBOUND_IP = "127.0.0.1"     # what the attacker's SECOND answer would be
_SAFE_IP6 = "2606:4700:4700::1111"   # public IPv6 literal (bracketing check)


class _FlipFlopResolver:
    """``getaddrinfo`` stand-in: public on the first call, loopback after.

    Any second resolution of the same name therefore lands somewhere the guard
    would have rejected -- which is what makes "the connection went to the first
    address" a real assertion and not a tautology.
    """

    def __init__(self, first: str = _SAFE_IP, then: str = _REBOUND_IP):
        self.first, self.then = first, then
        self.calls = 0

    def __call__(self, host, port, *a, **kw):
        self.calls += 1
        addr = self.first if self.calls == 1 else self.then
        family = socket.AF_INET6 if ":" in addr else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (addr, port))]


class _Resp:
    def __init__(self, status: int = 200, body=None):
        self.status_code = status
        self._body = {"Value": []} if body is None else body

    def json(self):
        return self._body


class _RecordingHttp:
    """``httpx.AsyncClient`` stand-in recording what the scan actually dialled."""

    calls: list[dict] = []
    status: int = 200

    def __init__(self, *a, **kw):
        self.ctor_kwargs = kw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kw):
        _RecordingHttp.calls.append(
            {"url": url, "kwargs": kw, "ctor": self.ctor_kwargs})
        return _Resp(_RecordingHttp.status)


@pytest.fixture()
def recorded(monkeypatch):
    """Capture the outbound scan request instead of making it."""
    _RecordingHttp.calls = []
    _RecordingHttp.status = 200
    monkeypatch.setattr(alpaca_module.httpx, "AsyncClient", _RecordingHttp)
    return _RecordingHttp.calls


@pytest.fixture()
def flipflop(monkeypatch):
    r = _FlipFlopResolver()
    monkeypatch.setattr(alpaca_module.socket, "getaddrinfo", r)
    return r


# ---- the guard hands back what it approved --------------------------------

def test_validate_returns_the_addresses_it_approved(flipflop):
    assert validate_scan_host("rebind.test", 11111) == [_SAFE_IP]
    assert flipflop.calls == 1


def test_validate_returns_the_literal_for_an_ip_literal():
    assert validate_scan_host(_SAFE_IP, 11111) == [_SAFE_IP]


def test_validate_still_rejects_a_host_resolving_to_loopback(monkeypatch):
    monkeypatch.setattr(alpaca_module.socket, "getaddrinfo",
                        _FlipFlopResolver(first=_REBOUND_IP))
    with pytest.raises(AlpacaScanError) as e:
        validate_scan_host("evil.test", 11111)
    assert e.value.kind == "invalid"


def test_validate_rejects_a_bad_port_before_resolving(flipflop):
    with pytest.raises(AlpacaScanError) as e:
        validate_scan_host("rebind.test", 0)
    assert e.value.kind == "invalid"
    assert flipflop.calls == 0


# ---- Alpaca manual scan ----------------------------------------------------

async def test_query_server_dials_the_validated_address_not_the_name(
        flipflop, recorded):
    out = await query_server("rebind.test", 11111)
    url = recorded[0]["url"]
    assert url == f"http://{_SAFE_IP}:11111/management/v1/configureddevices"
    assert "rebind.test" not in url      # the name in the URL WAS the defect
    assert _REBOUND_IP not in url
    assert flipflop.calls == 1           # resolved once, by the guard
    assert out["address"] == "rebind.test"   # UI still echoes what was typed


async def test_query_server_sends_the_original_host_header(flipflop, recorded):
    # A vhosted Alpaca server keys on Host, so pinning the IP must not orphan it.
    await query_server("rebind.test", 11111)
    assert recorded[0]["kwargs"]["headers"]["Host"] == "rebind.test:11111"


async def test_query_server_never_follows_redirects(flipflop, recorded):
    # A 30x would re-resolve and re-dial whatever Location names -- pinning void.
    await query_server("rebind.test", 11111)
    assert recorded[0]["kwargs"].get("follow_redirects", False) is False
    assert recorded[0]["ctor"].get("follow_redirects", False) is False


async def test_query_server_brackets_an_ipv6_literal(recorded):
    await query_server(_SAFE_IP6, 11111)
    assert recorded[0]["url"] == (
        f"http://[{_SAFE_IP6}]:11111/management/v1/configureddevices")
    assert recorded[0]["kwargs"]["headers"]["Host"] == f"[{_SAFE_IP6}]:11111"


async def test_query_server_error_kinds_are_unchanged(flipflop, recorded):
    _RecordingHttp.status = 404
    with pytest.raises(AlpacaScanError) as e:
        await query_server("rebind.test", 11111)
    assert e.value.kind == "not_alpaca"
    # ...and the message must not echo the upstream status: that turned the 502
    # into a port/host scan oracle (the route's own comment says it is closed).
    assert "404" not in str(e.value)


# ---- NINA discovery extra_hosts -------------------------------------------

@pytest.fixture()
def nina_probe(monkeypatch):
    """No subnet sweep, no real probing -- just record which IPs were reached."""
    monkeypatch.setattr(nina_module, "_local_subnets", lambda: [])
    probed: list[str] = []

    async def fake_probe(client, ip, port):
        probed.append(ip)
        return None

    monkeypatch.setattr(nina_module, "_probe_version", fake_probe)
    return probed


async def test_discover_nina_probes_the_validated_address(
        flipflop, nina_probe, monkeypatch):
    def _boom(host):
        raise AssertionError("a second resolution reopens the rebinding window")

    monkeypatch.setattr(nina_module.socket, "gethostbyname", _boom)
    assert await nina_module.discover_nina(port=1888,
                                           extra_hosts=["rebind.test"]) == []
    assert nina_probe == [_SAFE_IP]
    assert flipflop.calls == 1


async def test_discover_nina_still_skips_a_blocked_extra_host(
        monkeypatch, nina_probe):
    monkeypatch.setattr(alpaca_module.socket, "getaddrinfo",
                        _FlipFlopResolver(first=_REBOUND_IP))
    # Discovery is best-effort: one bad host is skipped, never a 500.
    assert await nina_module.discover_nina(port=1888,
                                           extra_hosts=["evil.test"]) == []
    assert nina_probe == []
