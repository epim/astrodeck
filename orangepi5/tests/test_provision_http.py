"""Raw-socket tests for the unprivileged captive portal parser and guards."""

from __future__ import annotations

import inspect
import socket
import threading
import time
import urllib.parse

import pytest


@pytest.fixture
def portal(prov, monkeypatch):
    monkeypatch.setattr(prov, "log", lambda _message: None)
    server = prov.BoundedHTTPServer(("127.0.0.1", 0), prov.Portal, max_workers=2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield prov, server
    finally:
        prov.DONE.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _exchange(server, request: bytes) -> bytes:
    with socket.create_connection(server.server_address, timeout=2) as client:
        client.settimeout(2)
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            block = client.recv(8192)
            if not block:
                break
            chunks.append(block)
    return b"".join(chunks)


def _status(response: bytes) -> int:
    return int(response.split(b"\r\n", 1)[0].split()[1])


def _request(method: str, path: str, headers=(), body: bytes = b"") -> bytes:
    lines = [f"{method} {path} HTTP/1.1", "Host: 10.42.0.1", *headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


def test_token_page_requires_the_portal_host_and_same_site_metadata(portal):
    prov, server = portal
    accepted = _exchange(server, _request("GET", "/"))
    assert _status(accepted) == 200
    assert prov.CSRF_TOKEN.encode() in accepted

    foreign_host = _exchange(
        server, b"GET / HTTP/1.1\r\nHost: attacker.example\r\n\r\n"
    )
    assert _status(foreign_host) == 421
    assert prov.CSRF_TOKEN.encode() not in foreign_host

    bad_origin = _exchange(
        server, _request("GET", "/", ["Origin: http://attacker.example"])
    )
    assert _status(bad_origin) == 403
    assert prov.CSRF_TOKEN.encode() not in bad_origin

    cross_site = _exchange(
        server, _request("GET", "/", ["Sec-Fetch-Site: cross-site"])
    )
    assert _status(cross_site) == 403
    assert prov.CSRF_TOKEN.encode() not in cross_site


def test_http_access_log_uses_only_the_unprivileged_frontend_logger(prov):
    source = inspect.getsource(prov.Portal.log_message)
    assert "log(" in source
    assert "open(" not in source


def test_missing_or_wrong_csrf_has_no_scan_join_or_state_effect(
    portal, monkeypatch
):
    prov, server = portal
    scans = []
    joins = []
    monkeypatch.setattr(prov.BROKER, "scan", lambda: scans.append(True))
    monkeypatch.setattr(prov.BROKER, "join", lambda *_args: joins.append(True))
    before = dict(prov.STATUS)

    for path, body in (
        ("/rescan", b"csrf=wrong"),
        ("/connect", b"ssid=home&psk=12345678"),
    ):
        response = _exchange(
            server,
            _request(
                "POST",
                path,
                [f"Content-Length: {len(body)}"],
                body,
            ),
        )
        assert _status(response) == 403

    assert scans == []
    assert joins == []
    assert prov.STATUS == before


def test_get_rescan_is_inert_but_valid_csrf_post_scans(portal, monkeypatch):
    prov, server = portal
    calls = []
    monkeypatch.setattr(
        prov.BROKER,
        "scan",
        lambda: calls.append(True) or [{"ssid": "safe", "signal": -10, "akm": []}],
    )
    assert _status(_exchange(server, _request("GET", "/rescan"))) == 302
    assert calls == []

    body = urllib.parse.urlencode({"csrf": prov.CSRF_TOKEN}).encode("ascii")
    response = _exchange(
        server,
        _request("POST", "/rescan", [f"Content-Length: {len(body)}"], body),
    )
    assert _status(response) == 302
    assert calls == [True]
    assert prov.SCAN_CACHE[0]["ssid"] == "safe"


@pytest.mark.parametrize(
    ("headers", "body", "expected"),
    [
        ([], b"", 411),
        (["Content-Length: +1"], b"x", 400),
        (["Content-Length: -1"], b"", 400),
        (["Content-Length: 1, 1"], b"x", 400),
        (["Content-Length: 1", "Content-Length: 1"], b"x", 400),
        (["Transfer-Encoding: chunked"], b"0\r\n\r\n", 400),
        (["Transfer-Encoding: chunked", "Content-Length: 0"], b"", 400),
        (["Content-Length: 4097"], b"", 413),
        (["Content-Length: 4"], b"x", 400),
        (["Content-Length: 1"], b"\xff", 400),
    ],
)
def test_noncanonical_or_unsafe_body_framing_is_rejected_and_closed(
    portal, headers, body, expected
):
    _prov, server = portal
    response = _exchange(server, _request("POST", "/rescan", headers, body))
    assert _status(response) == expected

    deadline = time.monotonic() + 1
    while server._slots._value != 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server._slots._value == 2


def test_too_many_form_fields_are_rejected_before_route_effects(portal, monkeypatch):
    prov, server = portal
    scans = []
    monkeypatch.setattr(prov.BROKER, "scan", lambda: scans.append(True))
    body = b"&".join(f"f{i}=x".encode("ascii") for i in range(9))
    response = _exchange(
        server,
        _request("POST", "/rescan", [f"Content-Length: {len(body)}"], body),
    )
    assert _status(response) == 400
    assert scans == []


def test_duplicate_csrf_values_are_rejected(portal, monkeypatch):
    prov, server = portal
    scans = []
    monkeypatch.setattr(prov.BROKER, "scan", lambda: scans.append(True))
    body = urllib.parse.urlencode(
        [("csrf", prov.CSRF_TOKEN), ("csrf", prov.CSRF_TOKEN)]
    ).encode("ascii")
    response = _exchange(
        server,
        _request("POST", "/rescan", [f"Content-Length: {len(body)}"], body),
    )
    assert _status(response) == 403
    assert scans == []


def test_closed_authorization_window_rejects_privileged_posts(portal, monkeypatch):
    prov, server = portal
    scans = []
    monkeypatch.setattr(prov.BROKER, "scan", lambda: scans.append(True))
    prov.DONE.set()
    body = urllib.parse.urlencode({"csrf": prov.CSRF_TOKEN}).encode("ascii")
    response = _exchange(
        server,
        _request("POST", "/rescan", [f"Content-Length: {len(body)}"], body),
    )
    assert _status(response) == 503
    assert scans == []
