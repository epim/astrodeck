"""API tests for the Batch 4b automation surface (api/app.py §1.10).

Covers the endpoints owned by the Api lane:

* GET/POST ``/api/config`` round-trips the appended automation keys
  (safety/escalation/alerts/deadman_url) and **blanks alert tokens** on the way
  out (the Telegram bot token is the only at-rest secret).
* ``/api/safety/simulate`` flips the simulated SafetyMonitor (sim-only — 404 off
  sim) and ``/api/safety/state`` reflects the cached verdict.
* ``/api/alerts`` upsert/delete/test: upsert resets ``verified`` on a url change,
  delete removes by id, test reports the round-trip result.
* POST ``/api/sequence/preflight`` warns for an always-below-floor target and is
  distinct from the existing GET single-target verdict.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore, Site


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated app: config + reports redirected to tmp, no real rig touched."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    # reports land under tmp (report.py resolves hub.CAPTURE_DIR live)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")

    app = app_module.create_app()
    with TestClient(app) as c:
        try:
            yield c, temp_store
        finally:
            # the hub is a module singleton — disconnect so a sim connected in
            # one test never leaks into the next.
            try:
                c.post("/api/disconnect")
            except Exception:
                pass


# --------------------------------------------------------------- config round-trip

def test_get_config_includes_automation_blocks(client):
    c, _ = client
    r = c.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    for key in ("safety", "escalation", "alerts", "deadman_url", "site",
                "optics_computed"):
        assert key in body, key
    assert body["safety"]["preset"] == "backyard"
    assert body["alerts"] == []


def test_post_config_partial_merge_persists_and_blanks_tokens(client):
    c, store = client
    # POST only the alerts + deadman_url blocks; site/safety untouched.
    patch = {
        "deadman_url": "https://hc-ping.com/abc",
        "alerts": [{
            "id": "tg1", "kind": "telegram", "url": "",
            "token": "SECRET-BOT-TOKEN", "chat_id": "42",
            "events": ["safety", "run_end"],
        }],
    }
    r = c.post("/api/config", json=patch)
    assert r.status_code == 200, r.text
    body = r.json()
    # deadman persisted server-side but REDACTED outbound (P2-12): the url can
    # carry a per-ping secret, so the redacted union exposes only a boolean marker.
    assert body["deadman_url"] == ""
    assert body["deadman_configured"] is True
    # token + chat_id blanked in the returned (redacted) union.
    assert body["alerts"][0]["token"] == ""
    assert body["alerts"][0]["chat_id"] == ""
    # but the real secrets ARE persisted server-side.
    assert store.cfg().alerts[0].token == "SECRET-BOT-TOKEN"
    assert store.cfg().alerts[0].chat_id == "42"
    assert store.cfg().deadman_url == "https://hc-ping.com/abc"
    # a fresh GET still blanks it.
    assert c.get("/api/config").json()["alerts"][0]["token"] == ""
    # echoing the redacted (empty) deadman_url back must NOT wipe the stored one.
    c.post("/api/config", json={"deadman_url": ""})
    assert store.cfg().deadman_url == "https://hc-ping.com/abc"


def test_post_config_safety_preset_round_trips(client):
    c, store = client
    # Every field SAFETY_PRESETS["remote"] names, because `preset` is DERIVED
    # from the numerics on read (2026-08-04) rather than stored as sent. This
    # body is what the fixed SafetyLimitsPanel posts; the version without
    # close_dome_on_unsafe was copied from the panel's table back when the table
    # was missing it, and would now correctly come back labelled "custom".
    patch = {"safety": {
        "enabled": True, "preset": "remote", "min_alt_deg": 10.0,
        "twilight_deg": -15.0, "on_unsafe": "abort_park_warm",
        "unsafe_consecutive": 2, "resume_when_safe": False,
        "resume_safe_consecutive": 3, "max_pause_min": 0,
        "close_dome_on_unsafe": True,
    }}
    r = c.post("/api/config", json=patch)
    assert r.status_code == 200, r.text
    assert store.cfg().safety.preset == "remote"
    assert store.cfg().safety.twilight_deg == -15.0
    # version bumped (optimistic-concurrency token).
    assert store.cfg().version >= 2


# ------------------------------------------------------------------ safety (sim)

def test_safety_simulate_flips_state_on_sim(client):
    c, _ = client
    assert c.post("/api/connect/sim").status_code == 200

    # baseline: connected + safe.
    st = c.get("/api/safety/state").json()
    assert st["connected"] is True

    # inject unsafe.
    r = c.post("/api/safety/simulate", json={"unsafe": True, "reason": "clouds"})
    assert r.status_code == 200, r.text
    rd = r.json()["reading"]
    assert rd["is_safe"] is False
    assert "clouds" in rd["reason"]

    # back to safe.
    r2 = c.post("/api/safety/simulate", json={"unsafe": False})
    assert r2.status_code == 200
    assert r2.json()["reading"]["is_safe"] is True


def test_safety_simulate_404_when_not_sim(client):
    c, _ = client
    # no rig connected => mode == "none" => 404.
    r = c.post("/api/safety/simulate", json={"unsafe": True})
    assert r.status_code == 404


# ------------------------------------------------------------------------ alerts

def test_alerts_upsert_delete_and_token_blanking(client):
    c, store = client
    # upsert a new ntfy sink.
    sink = {"id": "ntfy1", "kind": "ntfy", "url": "https://ntfy.sh/mytopic",
            "events": ["run_end", "safety"]}
    r = c.post("/api/alerts", json=sink)
    assert r.status_code == 200, r.text
    listed = r.json()
    assert len(listed) == 1
    assert listed[0]["id"] == "ntfy1"
    assert listed[0]["token"] == ""           # always blanked outbound

    # GET /api/alerts mirrors it.
    g = c.get("/api/alerts").json()
    assert g[0]["url"] == "https://ntfy.sh/mytopic"

    # mark it verified server-side, then change the url -> verified resets.
    store.cfg().alerts[0].verified = True
    sink_moved = dict(sink, url="https://ntfy.sh/othertopic")
    r2 = c.post("/api/alerts", json=sink_moved)
    assert r2.status_code == 200
    assert store.cfg().alerts[0].verified is False
    assert store.cfg().alerts[0].url == "https://ntfy.sh/othertopic"

    # delete by id.
    d = c.delete("/api/alerts/ntfy1")
    assert d.status_code == 200
    assert d.json() == {"deleted": "ntfy1"}
    assert c.get("/api/alerts").json() == []


def test_saving_a_sink_with_the_blanked_token_keeps_it_verified(client):
    """The client never holds the token — every read blanks it — so pressing Save
    on ANY other field POSTs ``token: ""``. The identity comparison ran BEFORE
    the token was restored, so `"" != "BOT-SECRET"` read as a re-point and
    cleared the badge on every save of a credential-bearing sink. The sibling
    helper ``_merge_alert_verified`` already had the ordering right; this route
    had a second copy of the rule that did not."""
    c, store = client
    sink = {"id": "tg1", "kind": "telegram", "token": "BOT-SECRET",
            "chat_id": "12345", "events": ["run_end"]}
    assert c.post("/api/alerts", json=sink).status_code == 200
    store.cfg().alerts[0].verified = True

    # exactly what the panel round-trips (AlertsPanel `{...s, token: ""}`): the
    # sink it was served, verified badge and all, with one unrelated edit and
    # the token as the blank the server handed it.
    r = c.post("/api/alerts", json=dict(sink, token="", verified=True,
                                        events=["run_end", "safety"]))
    assert r.status_code == 200, r.text
    after = store.cfg().alerts[0]
    assert after.token == "BOT-SECRET", "the stored secret was blanked"
    assert after.events == ["run_end", "safety"], "the actual edit must land"
    assert after.verified is True, (
        "nothing was re-pointed, but the verified badge was cleared — the user "
        "must re-run the delivery test after every unrelated save")


def test_alert_test_roundtrip(client, monkeypatch):
    c, _ = client
    c.post("/api/alerts", json={"id": "w1", "kind": "webhook",
                                "url": "https://example.invalid/hook"})

    # stub the dispatcher's real send so the test never hits the network.
    async def fake_test(sink_id):
        return {"ok": True, "error": None, "verified": True}
    monkeypatch.setattr(app_module.dispatcher, "test", fake_test)

    r = c.post("/api/alerts/w1/test")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "error": None, "verified": True}

    # unknown id -> 404.
    assert c.post("/api/alerts/nope/test").status_code == 404


# --------------------------------------------------------------- plan preflight

def _below_floor_plan() -> dict:
    """A southern target invisible from a far-northern site, with a 10° gate."""
    return {
        "name": "preflight",
        "targets": [{
            "name": "FarSouth",
            "ra_hours": 6.0, "dec_deg": -80.0,
            "schedule": {"min_altitude_deg": 10.0},
            "steps": [{"exposure_s": 60.0, "count": 5}],
        }],
    }


def test_post_preflight_warns_for_always_below_floor_target(client):
    c, store = client
    # configure a far-northern site so the dec -80 target never clears 10°.
    store.set_site(Site(name="Tromso", latitude=69.6, longitude=18.9))
    assert store.cfg().site.is_default is False

    r = c.post("/api/sequence/preflight", json=_below_floor_plan())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert len(body["warnings"]) == 1
    w = body["warnings"][0]
    assert w["target"] == "FarSouth"
    assert w["kind"] == "never_rises"


def test_post_preflight_no_warnings_on_default_site(client):
    c, _ = client
    # default (un-configured) site => never warn (we don't trust the location).
    r = c.post("/api/sequence/preflight", json=_below_floor_plan())
    assert r.status_code == 200
    # ``blocked`` is the additive UX-review flag (a blocking warning is present);
    # nothing to warn about here, so it is False alongside the original shape.
    assert r.json() == {"ok": True, "warnings": [], "blocked": False}


def test_get_and_post_preflight_coexist(client):
    """The 4a GET single-target verdict and the 4b POST plan-wide warnings share
    the path but differ by verb — both must resolve."""
    c, _ = client
    g = c.get("/api/sequence/preflight",
              params={"ra_hours": 5.5, "dec_deg": -5.4})
    assert g.status_code == 200
    assert "verdict" in g.json()

    p = c.post("/api/sequence/preflight", json={"name": "x", "targets": []})
    assert p.status_code == 200
    assert p.json() == {"ok": True, "warnings": [], "blocked": False}
