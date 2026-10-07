# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``GET /`` landing page (#686): redirect / list / none, never a 404.

Before this, the relay's root URL 404'd and a remote user had to already know
and type the full ``/h/<home_id>/`` path. These tests drive the real Starlette
app through ``TestClient`` (off-wire; nothing is deployed) and register a
"connected" home the same way the production ``/scope`` handshake would --
``ScopeConnection.handle_hello`` against the app's own ``RelayState`` -- which
is the pattern ``tests/test_token_lifecycle.py`` already uses for the same
reason: it exercises the real registration path instead of poking the
registry's private dict directly.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from relay import protocol  # noqa: E402
from relay.config import RelayConfig  # noqa: E402
from relay.connection import ScopeConnection  # noqa: E402
from relay.server import RelayState, create_app  # noqa: E402

from conftest import FakeScopeTunnel  # noqa: E402

_ENV_VARS = (
    "RELAY_DEVICE_TOKENS_FILE", "RELAY_DEVICE_TOKENS",
    "RELAY_HOME_LABELS_FILE", "RELAY_HOME_LABELS",
)


def _clean_env(monkeypatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _connect_home(state: RelayState, token: str, home_id: str, *,
                   generation: int = 1, extra_hello_fields: dict | None = None
                   ) -> ScopeConnection:
    """Register ``home_id`` as LIVE on ``state`` -- what ``_scope_endpoint``
    does once a real ``/scope`` HELLO is ACKed. ``extra_hello_fields`` lets a
    test prove a HELLO cannot smuggle a display label (the home_id the relay
    uses comes from the provisioned token map, not from anything the HELLO
    claims -- see ``HomeRegistry.validate_token``)."""
    state.registry.provision(token, home_id)
    tunnel = FakeScopeTunnel(f"tunnel-{home_id}")
    conn = ScopeConnection(state.registry, tunnel)
    hello = protocol.hello(token, home_id, generation=generation)
    if extra_hello_fields:
        hello = dataclasses.replace(
            hello, header={**hello.header, **extra_hello_fields})
    ack = conn.handle_hello(hello)
    assert ack.header["ok"] is True, ack.header
    state.connections[home_id] = conn
    return conn


def _app(monkeypatch, **env: str):
    _clean_env(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return create_app(RelayConfig(bind_host="127.0.0.1", origin="relay.test"))


# --------------------------------------------------------------- one home


def test_one_home_connected_redirects_to_its_path(monkeypatch):
    app = _app(monkeypatch)
    _connect_home(app.state.relay, "t" * 32, "home-1")

    r = TestClient(app).get("/", follow_redirects=False)

    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/h/home-1/"
    assert r.headers.get("cache-control") == "no-store"


def test_single_home_redirect_path_quotes_the_id(monkeypatch):
    """A home id outside the normal URL-safe set still lands in the path
    unchanged by the redirect (defense in depth; production ids are
    URL-safe-only per ``load_device_tokens``, but the registry itself does
    not enforce that, and a space/slash must not corrupt the Location)."""
    app = _app(monkeypatch)
    _connect_home(app.state.relay, "t" * 32, "home one/two")

    r = TestClient(app).get("/", follow_redirects=False)

    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/h/home%20one%2Ftwo/"


# -------------------------------------------------------------- many homes


def test_two_homes_connected_lists_both_in_sorted_order(monkeypatch):
    app = _app(monkeypatch)
    # Registered out of alphabetical order on purpose.
    _connect_home(app.state.relay, "t" * 32, "home-b")
    _connect_home(app.state.relay, "u" * 32, "home-a")

    r = TestClient(app).get("/", follow_redirects=False)

    assert r.status_code == 200, r.text
    body = r.text
    assert "/h/home-a/" in body
    assert "/h/home-b/" in body
    assert body.index("/h/home-a/") < body.index("/h/home-b/"), (
        "home-a must be listed before home-b (stable sorted order)")


def test_zero_homes_lists_none_and_leaks_no_provisioned_id(monkeypatch):
    app = _app(monkeypatch)
    state = app.state.relay
    # Tokens are PROVISIONED for two homes, but neither ever dialed in. The
    # page must say so was never reached for connections -- and, crucially,
    # never print "home-a" or "home-b" anywhere.
    state.registry.provision("t" * 32, "home-a")
    state.registry.provision("u" * 32, "home-b")

    r = TestClient(app).get("/", follow_redirects=False)

    assert r.status_code == 200, r.text
    assert "home-a" not in r.text
    assert "home-b" not in r.text
    assert "connected" in r.text.lower()


# -------------------------------------------------------------------- labels


def test_label_shown_only_when_set_in_relay_config(monkeypatch):
    app = _app(monkeypatch, RELAY_HOME_LABELS=json.dumps(
        {"home-a": "Front Yard Scope"}))
    _connect_home(app.state.relay, "t" * 32, "home-a")
    _connect_home(app.state.relay, "u" * 32, "home-b")

    r = TestClient(app).get("/", follow_redirects=False)

    assert "Front Yard Scope" in r.text
    # home-b has no config entry -> falls back to its own id as the label.
    assert ">home-b<" in r.text


def test_home_labels_file_takes_precedence_over_inline(monkeypatch, tmp_path):
    f = tmp_path / "labels.json"
    f.write_text(json.dumps({"home-a": "From File"}), encoding="utf-8")
    app = _app(
        monkeypatch,
        RELAY_HOME_LABELS_FILE=str(f),
        RELAY_HOME_LABELS=json.dumps({"home-a": "From Inline"}),
    )
    _connect_home(app.state.relay, "t" * 32, "home-a")
    _connect_home(app.state.relay, "u" * 32, "home-b")  # force the list page

    r = TestClient(app).get("/", follow_redirects=False)

    assert "From File" in r.text
    assert "From Inline" not in r.text


def test_hello_cannot_set_a_label(monkeypatch):
    """A HELLO carrying name/label-like fields must not change anything the
    page shows: the relay never reads a label from the tunnel (site privacy
    rule -- a rig's own name can carry the observing site's label)."""
    app = _app(monkeypatch)
    _connect_home(
        app.state.relay, "t" * 32, "home-a",
        extra_hello_fields={
            "name": "Backyard Observatory, 12.3N 45.6W",
            "label": "Backyard Observatory, 12.3N 45.6W",
            "site_name": "Backyard Observatory, 12.3N 45.6W",
        },
    )
    _connect_home(app.state.relay, "u" * 32, "home-b")  # force the list page

    r = TestClient(app).get("/", follow_redirects=False)

    assert "Backyard Observatory" not in r.text
    assert ">home-a<" in r.text


# ------------------------------------------------------------------ escaping


def test_a_home_id_containing_script_renders_escaped(monkeypatch):
    app = _app(monkeypatch)
    payload_id = "<script>alert(1)</script>"
    _connect_home(app.state.relay, "t" * 32, payload_id)
    _connect_home(app.state.relay, "u" * 32, "home-b")  # force the list page

    r = TestClient(app).get("/", follow_redirects=False)

    assert r.status_code == 200, r.text
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_a_label_containing_script_renders_escaped(monkeypatch):
    app = _app(monkeypatch, RELAY_HOME_LABELS=json.dumps(
        {"home-a": "<script>alert(1)</script>"}))
    _connect_home(app.state.relay, "t" * 32, "home-a")
    _connect_home(app.state.relay, "u" * 32, "home-b")  # force the list page

    r = TestClient(app).get("/", follow_redirects=False)

    assert r.status_code == 200, r.text
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


# ------------------------------------------------------------------- headers


def test_root_is_no_store_and_carries_a_strict_policy(monkeypatch):
    """"/" is the relay's only HTML and shares its origin with every tunnelled
    home app (and that app's session cookie), so on top of the escaping it
    carries a policy that forbids script outright: every response shape (the
    list, the redirect) is no-store and carries ``_ROOT_HEADERS``."""
    app = _app(monkeypatch)
    client = TestClient(app)

    for home_count in (0, 1, 2):
        state = app.state.relay
        state.connections.clear()
        state.registry = state.registry.__class__()
        for n in range(home_count):
            _connect_home(state, chr(ord("t") + n) * 32, f"home-{n}")
        r = client.get("/", follow_redirects=False)
        assert r.headers.get("cache-control") == "no-store", home_count
        csp = r.headers.get("content-security-policy", "")
        assert "default-src 'none'" in csp, (home_count, csp)
        assert "script-src" not in csp, (home_count, csp)
        assert "frame-ancestors 'none'" in csp, (home_count, csp)
        assert r.headers.get("x-content-type-options") == "nosniff", home_count
        assert r.headers.get("x-frame-options") == "DENY", home_count


def test_root_shares_the_same_per_ip_http_rate_limiter_as_browser_routes(
        monkeypatch):
    """#686 requires the EXISTING per-IP HTTP rate limit to apply to "/" the
    same way it applies to ``/h/{home_id}/...`` -- i.e. the same
    ``state.http_limiter`` bucket, not a second, independent one."""
    _clean_env(monkeypatch)
    cfg = RelayConfig(bind_host="127.0.0.1", origin="relay.test",
                      http_rate=0.0, http_burst=1.0)
    app = create_app(cfg)
    client = TestClient(app)

    first = client.get("/", follow_redirects=False)
    assert first.status_code == 200, first.text  # the one token in the bucket

    second = client.get("/", follow_redirects=False)
    assert second.status_code == 429, second.text
    assert second.text == "rate limited"


# ------------------------------------------------- /healthz and /h/... unchanged


def test_healthz_is_unaffected(monkeypatch):
    app = _app(monkeypatch)
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_h_path_behaviour_is_unaffected_when_home_not_connected(monkeypatch):
    app = _app(monkeypatch)
    r = TestClient(app).get("/h/home-1/anything", headers={
        "origin": "https://relay.test", "host": "relay.test"})
    assert r.status_code == 502
    assert r.text == "home not connected"
