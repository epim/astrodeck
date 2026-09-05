"""OPEN-011: single-use WS tickets replace a long-lived token in the query.

A browser cannot set a WS Authorization header, so a shared token had to ride
``?token=`` and leak into history/logs. An authenticated caller now mints a
one-time, short-TTL ticket over a POST and connects with ``?ticket=``; a leaked
ticket is inert.
"""
from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect
from fastapi.testclient import TestClient

from astrodeck.auth.ws_ticket import WsTicketStore
from test_auth import _make_client, TOKEN


# ---------------------------------------------------------------- store unit

def test_ticket_is_single_use():
    s = WsTicketStore()
    t = s.issue()
    assert s.consume(t) is True
    assert s.consume(t) is False  # already redeemed


def test_ticket_expires():
    s = WsTicketStore(ttl_s=30.0)
    t = s.issue(now=1000.0)
    assert s.consume(t, now=1029.0) is True  # within TTL
    t2 = s.issue(now=1000.0)
    assert s.consume(t2, now=1031.0) is False  # past TTL


def test_ticket_unknown_or_empty_rejected():
    s = WsTicketStore()
    assert s.consume("never-issued") is False
    assert s.consume("") is False


def test_store_stays_bounded_under_a_flood():
    s = WsTicketStore(max_outstanding=8)
    issued = [s.issue() for _ in range(50)]
    # never grows past the bound
    assert len(s._tickets) <= 8
    # the most recent ticket still works
    assert s.consume(issued[-1]) is True


# --------------------------------------------------- endpoint + WS integration

def test_mint_ticket_needs_no_query_and_returns_a_ticket(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=TOKEN)
    with TestClient(app) as c:
        # authenticate the mint via the HEADER carrier, never a query string
        r = c.post("/api/auth/ws-ticket", headers={"X-Auth-Token": TOKEN})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ticket"] and body["expires_in"] > 0


def test_ws_accepts_a_valid_ticket(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=TOKEN)
    with TestClient(app) as c:
        ticket = c.post("/api/auth/ws-ticket",
                        headers={"X-Auth-Token": TOKEN}).json()["ticket"]
        with c.websocket_connect(f"/ws?ticket={ticket}") as ws:
            assert ws.receive_json()["type"] == "hello"


def test_ws_rejects_a_reused_ticket(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=TOKEN)
    with TestClient(app) as c:
        ticket = c.post("/api/auth/ws-ticket",
                        headers={"X-Auth-Token": TOKEN}).json()["ticket"]
        with c.websocket_connect(f"/ws?ticket={ticket}") as ws:
            assert ws.receive_json()["type"] == "hello"
        # the ticket is spent; a replay with the same value is refused
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(f"/ws?ticket={ticket}") as ws:
                ws.receive_json()


def test_ws_rejects_an_unknown_ticket(tmp_path, monkeypatch):
    app = _make_client(tmp_path, monkeypatch, token=TOKEN)
    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws?ticket=bogus-never-issued") as ws:
                ws.receive_json()
