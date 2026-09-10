"""API: /api/sequence/stack {start,stop,reset}, the status GET and preview.jpg.

Mirrors the in-process app + sim-rig harness from test_bahtinov_route.py. The
capability wiring is checked by the boot assertion (auth/rbac.py) when the app
is created, so what is left to test here is the behaviour: the switch is real
and survives, the image 404s until there is one, and it is served as a JPEG
that is never cached (the same URL is a different picture every frame).
"""
import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore


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


def _stack_two_filters():
    """Feed the live hub's stacker directly: this file is about the routes, and
    the engine hand-off has its own test."""
    st = app_module.hub.session_stack
    st.add(_field(seed=1), "R", 60.0, target="M42")
    st.add(_field(1.5, -2.0, seed=2, scale=0.5), "G", 60.0, target="M42")


def test_start_flips_the_switch_and_stop_clears_it(client):
    r = client.post("/api/sequence/stack/start")
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True
    assert client.get("/api/sequence/stack").json()["enabled"] is True

    r = client.post("/api/sequence/stack/stop")
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    assert client.get("/api/sequence/stack").json()["enabled"] is False


def test_status_reports_per_filter_counts_and_integration(client):
    assert client.post("/api/sequence/stack/start").status_code == 200
    _stack_two_filters()
    body = client.get("/api/sequence/stack").json()
    assert body["frames"] == 2
    assert body["integrated_s"] == 120.0
    assert body["target"] == "M42"
    assert {c["channel"] for c in body["channels"]} == {"R", "G"}
    assert body["mode"] == "rgb"
    assert body["downsample"] >= 2


def test_preview_404s_until_something_is_stacked(client):
    assert client.post("/api/sequence/stack/start").status_code == 200
    assert client.get("/api/sequence/stack/preview.jpg").status_code == 404
    _stack_two_filters()
    r = client.get("/api/sequence/stack/preview.jpg")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"
    assert r.headers["x-stack-frames"] == "2"


def test_the_composite_is_never_cached(client):
    # The same URL returns a different picture after every accepted sub, so a
    # max-age here would freeze the monitor page on the first frame of the run.
    assert client.post("/api/sequence/stack/start").status_code == 200
    _stack_two_filters()
    r = client.get("/api/sequence/stack/preview.jpg")
    assert r.headers.get("cache-control") == "no-store"


def test_reset_empties_the_stack_but_leaves_the_switch_on(client):
    assert client.post("/api/sequence/stack/start").status_code == 200
    _stack_two_filters()
    r = client.post("/api/sequence/stack/reset")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True and body["frames"] == 0
    assert client.get("/api/sequence/stack/preview.jpg").status_code == 404


def test_the_requested_size_is_clamped_not_trusted(client):
    assert client.post("/api/sequence/stack/start").status_code == 200
    _stack_two_filters()
    # A caller asking for a 40000px render must not be able to spend the box's
    # memory on it.
    r = client.get("/api/sequence/stack/preview.jpg?size=40000")
    assert r.status_code == 200, r.text
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(r.content)).width <= 4096


# ---- the backfill option ------------------------------------------------
# Turning the stack on can also fold in the subs the run has already accepted.
# The route surface is small: a flag on /start, a separate /backfill for a stack
# that is already on, and a progress block on every reply so the panel can show
# a counter without a second call.

def test_every_reply_carries_the_progress_block(client):
    # One shape from all four calls, or the client needs to know which call its
    # status came from -- and the panel's counter blinks out on whichever reply
    # forgot it.
    for r in (client.post("/api/sequence/stack/start"),
              client.get("/api/sequence/stack"),
              client.post("/api/sequence/stack/reset"),
              client.post("/api/sequence/stack/stop")):
        assert r.status_code == 200, r.text
        block = r.json().get("backfill")
        assert isinstance(block, dict), r.json()
        for k in ("running", "total", "done", "added", "skipped", "failed",
                  "available"):
            assert k in block, (k, block)


def test_starting_without_the_flag_backfills_nothing(client):
    # The default, and it has to stay the default: the pass is minutes of disk
    # on a full night.
    body = client.post("/api/sequence/stack/start").json()
    assert body["enabled"] is True
    assert body["backfill"]["total"] == 0
    assert body["backfill"]["running"] is False


def test_starting_with_the_flag_is_accepted_and_reports_a_pass(client):
    # No live run in this harness, so there is nothing in any ledger to fold in
    # -- what is under test is that the flag is plumbed and the counter comes
    # back finished rather than stuck at "running, 0 of 0".
    body = client.post("/api/sequence/stack/start?backfill=true").json()
    assert body["enabled"] is True
    assert body["backfill"]["running"] is False
    assert body["backfill"]["done"] == body["backfill"]["total"] == 0


def test_the_backfill_route_exists_and_needs_the_stack_on(client):
    r = client.post("/api/sequence/stack/backfill")
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    assert client.post("/api/sequence/stack/start").status_code == 200
    r = client.post("/api/sequence/stack/backfill")
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True
