"""The surface Phase 2 was missing: config in, status out, push on demand.

The core algorithm has its own tests (``test_sync_push.py``) and the runner has
its own (``test_sync_runner.py``). What is pinned here is everything a person
touches — the write rule that makes the panel honest, the caps, and the fact
that ``/api/sync/push/now`` returns what the pass DID rather than "started".
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore, SyncPushConfig


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    captures = tmp_path / "captures"
    captures.mkdir()
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", captures)
    # A fresh runner per test: the module singleton would carry pass counts
    # between tests and make one of them pass for the previous one's reasons.
    from astrodeck.sync.runner import PushRunner
    monkeypatch.setattr(app_module, "sync_push_runner", PushRunner())
    c = TestClient(app_module.create_app())
    c.store = store
    c.captures = captures
    return c


def _frame(root: Path, rel: str, *, age_s: float = 60.0) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"FITS" * 200)
    old = time.time() - age_s
    os.utime(p, (old, old))
    return p


class TestConfigRoute:
    def test_round_trips(self, client, tmp_path):
        nas = tmp_path / "nas"
        r = client.post("/api/config/sync", json={"sync_push": {
            "enabled": True, "kind": "local_dir", "path": str(nas),
            "label": "processing box", "limit_per_pass": 25}})
        assert r.status_code == 200, r.text
        saved = client.store.cfg().sync_push
        assert saved.enabled is True
        assert saved.path == str(nas)
        assert saved.limit_per_pass == 25
        assert r.json()["sync_push"]["label"] == "processing box"

    def test_enabled_with_no_path_is_refused(self, client):
        """The honesty rule. A destination that is on and goes nowhere would let
        the panel claim it syncs while the night sits on the rig."""
        r = client.post("/api/config/sync", json={"sync_push": {
            "enabled": True, "path": "   "}})
        assert r.status_code == 422
        assert client.store.cfg().sync_push.enabled is False

    def test_off_with_no_path_is_fine(self, client):
        """Turning it off must not require a path — that is how you turn it off
        after clearing the field."""
        r = client.post("/api/config/sync", json={"sync_push": {
            "enabled": False, "path": ""}})
        assert r.status_code == 200

    def test_the_path_is_not_redacted(self, client, tmp_path):
        """A local path carries no credential, and a panel that may not say
        where frames are going cannot honestly say they are going anywhere."""
        nas = tmp_path / "nas"
        client.post("/api/config/sync", json={"sync_push": {
            "enabled": True, "path": str(nas)}})
        assert client.get("/api/config").json()["sync_push"]["path"] == str(nas)

    def test_default_is_off(self, client):
        cfg = client.get("/api/config").json()
        assert cfg["sync_push"]["enabled"] is False
        assert cfg["sync_push"]["path"] == ""


class TestStatusRoute:
    def test_reports_off_before_anything_is_configured(self, client):
        s = client.get("/api/sync/push").json()
        assert s["enabled"] is False and s["configured"] is False
        assert s["passes"] == 0 and s["last"] is None

    def test_follows_the_config(self, client, tmp_path):
        nas = tmp_path / "nas"
        client.post("/api/config/sync", json={"sync_push": {
            "enabled": True, "path": str(nas)}})
        s = client.get("/api/sync/push").json()
        assert s["configured"] is True and s["path"] == str(nas)


class TestPushNowRoute:
    def test_returns_what_the_pass_did(self, client, tmp_path):
        nas = tmp_path / "nas"
        _frame(client.captures, "NGC 6946/a.fits")
        _frame(client.captures, "NGC 6946/b.fits")
        client.post("/api/config/sync", json={"sync_push": {
            "enabled": True, "path": str(nas)}})

        r = client.post("/api/sync/push/now")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["last"]["sent"] == 2, body
        assert (nas / "NGC 6946/a.fits").exists()
        assert (nas / "NGC 6946/b.fits").exists()

    def test_a_second_pass_sends_nothing_new(self, client, tmp_path):
        """Reconciliation, not a queue: what is already there is not re-sent,
        and that is derived from a fresh hash rather than a 'sent' flag."""
        nas = tmp_path / "nas"
        _frame(client.captures, "NGC 6946/a.fits")
        client.post("/api/config/sync", json={"sync_push": {
            "enabled": True, "path": str(nas)}})
        client.post("/api/sync/push/now")
        body = client.post("/api/sync/push/now").json()
        assert body["last"]["sent"] == 0
        assert body["last"]["already_there"] == 1

    def test_refuses_when_sync_is_off(self, client):
        _frame(client.captures, "NGC 6946/a.fits")
        body = client.post("/api/sync/push/now").json()
        assert body["ok"] is False
        assert body["passes"] == 0

    def test_an_unreachable_destination_is_a_result_not_a_500(
            self, client, tmp_path):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("I am a file, not a directory")
        _frame(client.captures, "NGC 6946/a.fits")
        client.post("/api/config/sync", json={"sync_push": {
            "enabled": True, "path": str(blocker)}})
        r = client.post("/api/sync/push/now")
        assert r.status_code == 200
        assert r.json()["last"]["error"] or r.json()["last"]["failed"]


class TestCaps:
    """config.site_optics writes the block; view.media reads the status and
    moves the bytes. Both admin-only in the shipped role map — asked for
    separately so the split survives a future role change."""

    @pytest.fixture
    def as_viewer(self, client, monkeypatch):
        from astrodeck.auth import (Principal, reset_active_provider,
                                    set_active_provider)
        monkeypatch.setattr(app_module, "configure_provider_from_auth",
                            lambda _auth: app_module.get_active_provider())

        class _Fake:
            name = "fake"

            async def resolve(self, request):
                return Principal(role="custom", email=None,
                                 caps=frozenset({"view.status"}), jti=None)

        reset_active_provider()
        set_active_provider(_Fake())
        try:
            with TestClient(app_module.create_app()) as c:
                yield c
        finally:
            reset_active_provider()

    def test_config_write_needs_site_optics(self, as_viewer, tmp_path):
        r = as_viewer.post("/api/config/sync", json={"sync_push": {
            "enabled": True, "path": str(tmp_path / "nas")}})
        assert r.status_code == 403

    def test_status_needs_view_media(self, as_viewer):
        assert as_viewer.get("/api/sync/push").status_code == 403

    def test_push_now_needs_view_media(self, as_viewer):
        """A principal who may not FETCH the bytes must not be able to make the
        rig ship them somewhere — the same 'plan and perform behind ONE
        capability' rule /api/sync/manifest is gated by."""
        assert as_viewer.post("/api/sync/push/now").status_code == 403
