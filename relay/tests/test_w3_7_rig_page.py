# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 3.7 (#606): an outage leaves a record off the rig.

The home answers each relay PING with a PONG that carries the status beacon
(``rig_beacon``). The relay keeps the last ten per home, in memory, beside the
tunnel registry (so the record outlives the tunnel), and serves them on
``GET /rig/{home_id}``. That page is the only thing an owner can read once the
rig PC is dark: the night log is on the dead disk.

The page is opt-in and keyed: with no ``RELAY_RIG_PAGE_KEY`` it does not exist,
and with one it asks for it (HTTP Basic, any user name), because a public page
of when a rig is online, imaging and away is a surface nobody asked for.

The beacon text is built from the same closed vocabulary on both sides, and
the relay re-checks it: a home is a device the relay does not run, so what its
PONG carries is parsed against the grammar and dropped unless it matches.
"""
from __future__ import annotations

import base64

import pytest

from relay import protocol
from relay.connection import ScopeConnection
from relay.registry import HomeRegistry
from relay.rig_status import RECENT_PINGS, PingLog, parse_beacon, render_rig_page

from conftest import FakeScopeTunnel

NOW = 1_800_000_000.0   # 2027-01-15 08:00:00 UTC


def _beacon(state="running", frames=42, level="none", up=3600, boot="normal",
            log=1_799_999_940, pending="no") -> str:
    return (f"v=1 state={state} frames={frames} level={level} up={up} "
            f"log={log} pending={pending} boot={boot}")


# ------------------------------------------------------------------ parsing


def test_the_extended_beacon_parses_into_typed_fields():
    got = parse_beacon(_beacon(boot="unexpected", pending="yes"))
    assert got == {"state": "running", "frames": 42, "level": "none", "up": 3600,
                   "boot": "unexpected", "log": 1_799_999_940, "pending": True}


def test_a_six_key_beacon_from_an_older_rig_parses_with_the_two_unreported():
    got = parse_beacon("v=1 state=idle frames=0 level=none up=5 boot=unread")
    assert got["log"] is None and got["pending"] is None
    assert got["boot"] == "unread"


@pytest.mark.parametrize("text", [
    None, 7, b"v=1", "", "lat 11.1111 lon 22.2222",
    "v=2 state=idle frames=0 level=none up=5 boot=unread",
    "v=1 state=<script> frames=0 level=none up=5 boot=unread",
    "v=1 state=idle frames=0 level=none up=5 boot=Fictional Hilltop",
    "v=1 state=idle frames=0 level=none up=5 boot=unread\nextra",
    "v=1 state=idle frames=0 level=none up=5 boot=unread lat 11.1111",
    "v=1 state=idle frames=0 level=none up=5 log=x pending=no boot=unread",
    "v=1 state=idle frames=0 level=none up=5 pending=maybe boot=unread",
])
def test_anything_outside_the_grammar_is_not_a_beacon(text):
    assert parse_beacon(text) is None


# ----------------------------------------------------------------- the ring


def test_the_log_keeps_the_last_ten_pings_only():
    log = PingLog()
    for i in range(15):
        log.record(parse_beacon(_beacon(frames=i)), NOW + i * 120.0)
    entries = log.entries()
    assert RECENT_PINGS == 10 and len(entries) == 10
    assert [e["frames"] for e in entries] == list(range(5, 15))
    assert [e["at"] for e in entries] == [NOW + i * 120.0 for i in range(5, 15)]


def test_pings_within_a_minute_of_the_same_words_share_one_slot():
    """The home answers every ten seconds, so ten raw pings would be a hundred
    seconds of history. A repeat inside a minute refreshes the newest slot."""
    log = PingLog()
    for i in range(6):
        log.record(parse_beacon(_beacon(frames=i)), NOW + i * 10.0)
    entries = log.entries()
    assert len(entries) == 1
    assert entries[0]["frames"] == 5 and entries[0]["at"] == NOW + 50.0


def test_a_ping_after_a_minute_opens_a_new_slot():
    log = PingLog()
    log.record(parse_beacon(_beacon()), NOW)
    log.record(parse_beacon(_beacon()), NOW + 61.0)
    assert len(log.entries()) == 2


@pytest.mark.parametrize("change", [{"state": "holding"}, {"level": "warning"},
                                    {"boot": "unexpected"}, {"pending": "yes"}])
def test_a_changed_word_opens_a_new_slot_at_once(change):
    log = PingLog()
    log.record(parse_beacon(_beacon()), NOW)
    log.record(parse_beacon(_beacon(**change)), NOW + 5.0)
    assert len(log.entries()) == 2


# ----------------------------------------------- registry and the PONG frame


def _registry():
    return HomeRegistry(token_to_home={"t" * 32: "home-1"})


def _live(reg) -> ScopeConnection:
    conn = ScopeConnection(reg, FakeScopeTunnel("t1"))
    ack = conn.handle_hello(protocol.hello("t" * 32, "home-1", generation=1))
    assert ack.header["ok"] is True
    return conn


async def test_a_pong_carrying_a_beacon_is_recorded_and_outlives_the_tunnel():
    reg = _registry()
    conn = _live(reg)
    await conn.on_frame(protocol.Frame(
        protocol.FrameType.PONG, protocol.CONTROL_STREAM_ID,
        {"ts": NOW, "beacon": _beacon(boot="unexpected", pending="yes")}))
    await conn.close()
    assert reg.get("home-1") is None, "the tunnel is gone"
    (entry,) = reg.ping_entries("home-1")
    assert entry["boot"] == "unexpected" and entry["pending"] is True


@pytest.mark.parametrize("header", [
    {"ts": NOW},
    {"ts": NOW, "beacon": "lat 11.1111 lon 22.2222"},
    {"ts": NOW, "beacon": "<script>alert(1)</script>"},
    {"ts": NOW, "beacon": 7},
])
async def test_a_pong_without_a_valid_beacon_records_nothing_but_is_still_a_pong(header):
    reg = _registry()
    conn = _live(reg)
    conn.tunnel.last_pong = 0.0
    await conn.on_frame(protocol.Frame(
        protocol.FrameType.PONG, protocol.CONTROL_STREAM_ID, header))
    assert reg.ping_entries("home-1") == []
    assert conn.tunnel.last_pong > 0.0, "the PONG still counts as liveness"


def test_an_unprovisioned_home_has_no_record_and_cannot_be_given_one():
    reg = _registry()
    reg.record_ping("home-2", _beacon(), NOW)
    assert reg.ping_entries("home-2") == []
    assert reg.is_provisioned("home-1") and not reg.is_provisioned("home-2")


# ---------------------------------------------------------------- the page


def test_the_page_lists_pings_newest_first_with_the_three_fields():
    log = PingLog()
    log.record(parse_beacon(_beacon(boot="normal", pending="no", log=1_799_990_000)), NOW)
    log.record(parse_beacon(_beacon(state="idle", boot="unexpected", pending="yes",
                                    log=1_799_999_940)), NOW + 300.0)
    html = render_rig_page("home-1", "Home One", log.entries(), connected=False,
                           now=NOW + 400.0)
    assert html.index("2027-01-15 08:05:00 UTC") < html.index("2027-01-15 08:00:00 UTC")
    assert "unexpected" in html and "restart pending" in html
    assert "2027-01-15 07:59:00 UTC" in html, "the last night-log line, as a time"
    assert "Not connected" in html


def test_a_home_with_no_pings_says_so():
    html = render_rig_page("home-1", "Home One", [], connected=True, now=NOW)
    assert "No ping has been recorded" in html
    assert "Connected" in html


def test_the_page_escapes_the_label_and_the_id():
    html = render_rig_page("home-1", "<img src=x onerror=alert(1)>", [],
                           connected=True, now=NOW)
    assert "<img" not in html and "&lt;img" in html


# ----------------------------------------------------------- the route (HTTP)

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from relay.config import RelayConfig  # noqa: E402
from relay.server import create_app  # noqa: E402

KEY = "k" * 40


def _auth(password: str = KEY, user: str = "owner") -> dict:
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def _app(monkeypatch, *, key: str | None = KEY, **cfg):
    for name in ("RELAY_DEVICE_TOKENS_FILE", "RELAY_DEVICE_TOKENS",
                 "RELAY_HOME_LABELS_FILE", "RELAY_HOME_LABELS", "RELAY_RIG_PAGE_KEY"):
        monkeypatch.delenv(name, raising=False)
    if key is not None:
        monkeypatch.setenv("RELAY_RIG_PAGE_KEY", key)
    monkeypatch.setenv("RELAY_DEVICE_TOKENS", '{"%s": "home-1"}' % ("t" * 32))
    app = create_app(RelayConfig(bind_host="127.0.0.1", origin="relay.test", **cfg))
    return app, app.state.relay


def test_with_no_key_configured_the_page_does_not_exist(monkeypatch):
    app, state = _app(monkeypatch, key=None)
    state.registry.record_ping("home-1", _beacon(), NOW)
    assert TestClient(app).get("/rig/home-1", headers=_auth()).status_code == 404


@pytest.mark.parametrize("headers", [{}, _auth("wrong" * 10), _auth(""),
                                     {"Authorization": "Bearer " + KEY},
                                     {"Authorization": "Basic !!!notbase64"}])
def test_without_the_key_the_page_asks_for_it_and_shows_nothing(monkeypatch, headers):
    app, state = _app(monkeypatch)
    state.registry.record_ping("home-1", _beacon(boot="unexpected"), NOW)
    r = TestClient(app).get("/rig/home-1", headers=headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")
    assert "unexpected" not in r.text and "home-1" not in r.text


def test_an_unknown_home_is_a_404_even_with_the_key(monkeypatch):
    app, _state = _app(monkeypatch)
    assert TestClient(app).get("/rig/home-9", headers=_auth()).status_code == 404


def test_the_page_shows_the_last_ten_pings_of_a_rig_that_has_gone_dark(monkeypatch):
    app, state = _app(monkeypatch)
    for i in range(14):
        state.registry.record_ping(
            "home-1", _beacon(frames=100 + i, boot="unexpected" if i == 13 else "normal"),
            NOW + i * 120.0)
    r = TestClient(app).get("/rig/home-1", headers=_auth())
    assert r.status_code == 200, r.text
    assert r.text.count("<tr class=\"ping\"") == 10
    assert "Not connected" in r.text, "no tunnel is registered: the rig is dark"
    assert "<td>113</td>" in r.text and "<td>104</td>" in r.text
    assert "<td>103</td>" not in r.text, "the oldest four are gone"
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_the_page_says_connected_while_the_tunnel_is_up(monkeypatch):
    app, state = _app(monkeypatch)
    _live(state.registry)
    state.registry.record_ping("home-1", _beacon(), NOW)
    r = TestClient(app).get("/rig/home-1", headers=_auth())
    assert "Connected" in r.text and "Not connected" not in r.text


def test_the_label_is_the_owner_set_one_and_never_the_rigs_own(monkeypatch):
    app, state = _app(monkeypatch)
    state.home_labels = {"home-1": "Back garden"}
    r = TestClient(app).get("/rig/home-1", headers=_auth())
    assert "Back garden" in r.text


def test_the_page_shares_the_per_ip_http_rate_limit(monkeypatch):
    app, _state = _app(monkeypatch, http_rate=0.0, http_burst=1.0)
    client = TestClient(app)
    assert client.get("/rig/home-1", headers=_auth()).status_code == 200
    assert client.get("/rig/home-1", headers=_auth()).status_code == 429
