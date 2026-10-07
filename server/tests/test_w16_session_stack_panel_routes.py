# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""API: ``?panel=`` on /api/sequence/stack and /api/sequence/stack/preview.jpg
(#172 part A, WP-121).

Every current client sends no ``panel``, so the first thing pinned is that it
gets the answer it always got (the same bytes, the same header set) and the
second is that a panel is a real selector: it changes the picture, it 404s in
its own words when the panel is unknown, and it composes with ``?channel=``.

Mutants (one-line changes, run from a byte backup inside the worktree, restored
byte-identically and grepped gone; failing assertion quoted verbatim):

``M14-route-ignores-panel``  the preview route drops ``panel`` before it reaches
    the stacker (app.py).
    ``test_the_preview_takes_a_panel``:  ``assert '2' == '1'`` (panel B's
    request answered with the foreground panel's count).
``M13-ghost-is-the-foreground``  the stacker answers an unknown panel with the
    foreground instead of None.
    ``test_an_unknown_panel_preview_is_a_404_in_its_own_words``:
    ``assert 200 == 404`` (``r.status_code``), and
    ``test_an_unknown_panel_on_status_reads_empty_not_an_error``:
    ``assert (2 == 0)`` (the frame count of a panel that is not there).
``M15-panel-header-always``  the route always sets ``X-Stack-Panel``.
    ``test_no_panel_is_the_response_this_app_has_always_given``:
    ``AssertionError: a client that has never heard of panels got a header it
    did not get`` (extra item ``'x-stack-panel'``).
``M16-status-route-ignores-panel``  the status route calls ``status()`` with no
    panel.
    ``test_status_takes_a_panel_and_lists_every_panel``:
    ``assert ('Alpha' == 'Beta'`` (asked for Beta, described Alpha).
``M26-panel-header-not-encoded``  ``X-Stack-Panel`` carries the raw key.
    ``test_a_panel_named_in_unicode_does_not_break_the_header``:
    ``UnicodeEncodeError: 'latin-1' codec can't encode character`` U+0394 ``in
    position 5: ordinal not in range(256)`` (on the rig this is a 500 for any
    target whose name leaves latin-1).
"""
import io
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore

RUN = "run-1"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")

    app = app_module.create_app()
    with TestClient(app) as c:
        try:
            yield c
        finally:
            app_module.hub.stop_session_stack()


def _field(dx=0.0, dy=0.0, *, scale=1.0, seed=3, shape=(400, 480)):
    rng = np.random.default_rng(seed)
    h, w = shape
    img = rng.normal(400.0, 6.0, shape)
    xs = np.arange(w) - (240 + dx)
    ys = (np.arange(h) - (200 + dy))[:, None]
    img += scale * 3000.0 * np.exp(-(xs ** 2 + ys ** 2) / (2 * 110.0 ** 2))
    for x, y, b in [(60, 70, 1.0), (180, 120, 0.7), (300, 200, 0.55),
                    (420, 90, 0.4), (250, 330, 0.35)]:
        xs = np.arange(w) - (x + dx)
        ys = (np.arange(h) - (y + dy))[:, None]
        img += b * 900_000.0 * np.exp(-(xs ** 2 + ys ** 2) / (2 * 3.0 ** 2)) \
            / (2 * math.pi * 9.0)
    return np.clip(img, 0, 65535).astype(np.uint16)


def _stack_two_panels():
    """A, B, A on the live hub's stacker: panel A holds two subs, B one."""
    st = app_module.hub.session_stack
    assert st.add(_field(seed=1), "R", 60.0, target="Alpha", target_id="t-a",
                  session=RUN)
    assert st.add(_field(60.0, 20.0, seed=2), "R", 60.0, target="Beta",
                  target_id="t-b", session=RUN)
    assert st.add(_field(1.5, -2.0, seed=3), "R", 60.0, target="Alpha",
                  target_id="t-a", session=RUN)


# ------------------------------------------------------------------- status
def test_status_takes_a_panel_and_lists_every_panel(client):
    assert client.post("/api/sequence/stack/start").status_code == 200
    _stack_two_panels()

    whole = client.get("/api/sequence/stack").json()
    assert whole["target"] == "Alpha" and whole["frames"] == 2
    assert [p["key"] for p in whole["panels"]] == ["t-a", "t-b"]
    assert whole["evicted"] == []

    b = client.get("/api/sequence/stack?panel=t-b").json()
    assert b["target"] == "Beta" and b["frames"] == 1
    assert [p["key"] for p in b["panels"]] == ["t-a", "t-b"], \
        "the panel list is the stack's, whichever panel was asked about"
    # A name finds it too (the hand-typed URL), and the reply carries the same
    # progress block every other reply does, so the client has one shape.
    by_name = client.get("/api/sequence/stack?panel=Beta").json()
    assert by_name["frames"] == 1
    for k in ("running", "total", "done", "added", "skipped", "failed",
              "available"):
        assert k in b["backfill"], k


def test_an_unknown_panel_on_status_reads_empty_not_an_error(client):
    # The status route is polled. A panel that was released to stay under the
    # memory budget between two polls must read as "nothing there", not turn the
    # poll into a failure.
    assert client.post("/api/sequence/stack/start").status_code == 200
    _stack_two_panels()
    r = client.get("/api/sequence/stack?panel=ghost")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["frames"] == 0 and body["has_image"] is False
    assert len(body["panels"]) == 2


# ------------------------------------------------------------------ preview
def test_the_preview_takes_a_panel(client):
    assert client.post("/api/sequence/stack/start").status_code == 200
    _stack_two_panels()
    a = client.get("/api/sequence/stack/preview.jpg?panel=t-a")
    b = client.get("/api/sequence/stack/preview.jpg?panel=t-b")
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert a.headers["x-stack-frames"] == "2"
    assert b.headers["x-stack-frames"] == "1"
    assert a.headers["x-stack-panel"] == "t-a"
    assert b.headers["x-stack-panel"] == "t-b"
    assert a.content != b.content, "two panels, one picture"
    assert a.content[:2] == b"\xff\xd8"
    # asked by name, answered by key
    by_name = client.get("/api/sequence/stack/preview.jpg?panel=Beta")
    assert by_name.headers["x-stack-panel"] == "t-b"
    assert by_name.content == b.content


def test_an_unknown_panel_preview_is_a_404_in_its_own_words(client):
    assert client.post("/api/sequence/stack/start").status_code == 200
    _stack_two_panels()
    r = client.get("/api/sequence/stack/preview.jpg?panel=ghost")
    assert r.status_code == 404, r.text
    assert "nothing stacked for that panel yet" in r.text
    # Three different 404s, three different sentences: an empty stack, a panel
    # with nothing in it, and a channel with nothing in it.
    app_module.hub.stop_session_stack()
    app_module.hub.start_session_stack()
    empty = client.get("/api/sequence/stack/preview.jpg")
    assert empty.status_code == 404
    assert "for that panel" not in empty.text and "in that channel" not in empty.text
    # ghost + a channel is still the PANEL's sentence: the panel is asked first.
    _stack_two_panels()
    both = client.get("/api/sequence/stack/preview.jpg?panel=ghost&channel=R")
    assert both.status_code == 404 and "for that panel" in both.text


def test_no_panel_is_the_response_this_app_has_always_given(client):
    assert client.post("/api/sequence/stack/start").status_code == 200
    _stack_two_panels()
    plain = client.get("/api/sequence/stack/preview.jpg")
    assert plain.status_code == 200, plain.text
    # The route's own headers, not the whole set: the app's security middleware
    # adds its own to every response and is not what is under test.
    assert {h for h in plain.headers if h.startswith("x-stack-")} == {
        "x-stack-seq", "x-stack-frames", "x-stack-channel",
    }, "a client that has never heard of panels got a header it did not get"
    assert plain.headers["cache-control"] == "no-store"
    assert plain.headers["content-type"] == "image/jpeg"
    assert plain.headers["x-stack-frames"] == "2"
    # No panel IS the foreground panel, byte for byte, and an empty `?panel=`
    # means the same thing it means for `?channel=`.
    foreground = client.get("/api/sequence/stack/preview.jpg?panel=t-a")
    assert foreground.content == plain.content
    assert client.get("/api/sequence/stack/preview.jpg?panel=").content \
        == plain.content
    assert plain.headers["x-stack-seq"] == foreground.headers["x-stack-seq"]


def test_a_panel_and_a_channel_compose(client):
    assert client.post("/api/sequence/stack/start").status_code == 200
    _stack_two_panels()
    r = client.get("/api/sequence/stack/preview.jpg?panel=t-b&channel=R")
    assert r.status_code == 200, r.text
    assert r.headers["x-stack-channel"] == "R"
    assert r.headers["x-stack-frames"] == "1", \
        "the caption under one panel's one channel counts THAT panel's frames"
    assert r.headers["x-stack-panel"] == "t-b"
    a = client.get("/api/sequence/stack/preview.jpg?panel=t-a&channel=R")
    assert a.headers["x-stack-frames"] == "2" and a.content != r.content
    # a channel panel B never shot is the channel's sentence, not the panel's
    miss = client.get("/api/sequence/stack/preview.jpg?panel=t-b&channel=Sii")
    assert miss.status_code == 404 and "in that channel" in miss.text


def test_a_panel_named_in_unicode_does_not_break_the_header(client):
    # Header values are latin-1 on the wire. A target named with a character
    # outside it must still be served, with the name percent-encoded.
    assert client.post("/api/sequence/stack/start").status_code == 200
    name = "M 31 " + chr(0x394) + " north"      # Greek capital delta
    assert any(ord(c) > 255 for c in name), \
        "the name is latin-1 safe, so this test would prove nothing"
    assert app_module.hub.session_stack.add(
        _field(seed=9), "R", 60.0, target=name, session=RUN)
    r = client.get("/api/sequence/stack/preview.jpg", params={"panel": name})
    assert r.status_code == 200, r.text
    from urllib.parse import unquote
    assert unquote(r.headers["x-stack-panel"]) == name
    assert Image.open(io.BytesIO(r.content)).width > 0


def test_reset_over_the_wire_releases_every_panel(client):
    assert client.post("/api/sequence/stack/start").status_code == 200
    _stack_two_panels()
    body = client.post("/api/sequence/stack/reset").json()
    assert body["enabled"] is True and body["frames"] == 0
    assert body["panels"] == []
    assert body["session"] == RUN, "the run id survives the operator's Reset"
    assert client.get(
        "/api/sequence/stack/preview.jpg?panel=t-a").status_code == 404
