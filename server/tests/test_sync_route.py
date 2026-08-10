"""``GET /api/sync/manifest`` — the rig's half of a pull sync.

The reconciliation RULES are tested in ``test_sync_manifest.py`` against real
manifests. What is checked here is only what the route adds: that it speaks the
gallery's night vocabulary, that it does not offer a frame that is still being
written, and that its gate matches the gate on the bytes it describes.
"""
from __future__ import annotations

import os
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck import gallery
from astrodeck.config import ConfigStore
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import save_fits


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    cap = tmp_path / "captures"
    cap.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", cap)
    gallery.clear_meta_cache()
    app_module._SYNC_HASH_CACHE.clear()
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c, cap


def _frame(cap, name, *, ts, age_s=3600.0):
    """One real FITS on disk, backdated so it counts as settled."""
    data = np.full((16, 16), 100, dtype=np.uint16)
    f = CameraFrame(data=data, exposure_s=60.0, gain=120, offset=30,
                    binning=1, bayer_pattern=None, temperature_c=-5.0,
                    timestamp=ts)
    path = save_fits(f, cap / name, target="NGC 6946", filter_name="L")
    when = time.time() - age_s
    os.utime(path, (when, when))
    return path


def test_manifest_hashes_settled_frames(env):
    client, cap = env
    _frame(cap, "a.fits", ts=time.time() - 3600)
    r = client.get("/api/sync/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["algo"] == "sha256"
    assert body["count"] == 1
    entry = body["entries"][0]
    assert entry["relpath"] == "a.fits"
    assert len(entry["sha256"]) == 64
    assert entry["final"] is True
    assert body["bytes"] > 0


def test_a_frame_still_being_written_is_reported_not_offered(env):
    """save_fits streams into the FINAL filename, so a just-appeared file may be
    partial. It must show up as unsettled — visible, but not fetchable."""
    client, cap = env
    _frame(cap, "fresh.fits", ts=time.time(), age_s=0.0)
    body = client.get("/api/sync/manifest").json()
    assert body["entries"] == []
    assert body["unsettled"] == ["fresh.fits"]
    assert body["settle_s"] > 0


def test_night_filter_uses_the_gallery_vocabulary(env):
    """The night must mean the same thing here as it does on the gallery screen,
    including the noon rollover — otherwise "sync last night" and "show me last
    night" would disagree about which frames that is."""
    client, cap = env
    late = datetime_ts(2026, 6, 15, 23, 50)
    early = datetime_ts(2026, 6, 16, 0, 10)
    _frame(cap, "late.fits", ts=late)
    _frame(cap, "early.fits", ts=early)
    body = client.get("/api/sync/manifest", params={"night": "2026-06-15"}).json()
    got = sorted(e["relpath"] for e in body["entries"])
    assert got == ["early.fits", "late.fits"], (
        "a 23:50 and a 00:10 frame belong to ONE observing night")
    assert {e["night"] for e in body["entries"]} == {"2026-06-15"}


def test_a_bad_night_is_refused(env):
    client, _ = env
    assert client.get("/api/sync/manifest",
                      params={"night": "last-tuesday"}).status_code == 422


def test_manifest_is_gated_like_the_bytes_it_describes(env):
    """The manifest exists to drive a copy of exactly the files
    /api/gallery/file gates. If the two gates ever differ, one of them is
    wrong — so pin them to EACH OTHER rather than to a literal capability name.
    Written this way the test still holds if both are deliberately raised
    together, and fails the moment they diverge, which is the actual invariant.
    """
    from astrodeck.auth.rbac import CAP_ATTR
    client, _ = env
    caps = {}
    for route in client.app.routes:
        path = getattr(route, "path", None)
        if path in ("/api/sync/manifest", "/api/gallery/file"):
            caps[path] = getattr(route.endpoint, CAP_ATTR, frozenset())
    assert len(caps) == 2, f"routes not found: {sorted(caps)}"
    assert caps["/api/sync/manifest"] == caps["/api/gallery/file"]


def datetime_ts(y, mo, d, h, mi):
    import datetime as _dt
    return _dt.datetime(y, mo, d, h, mi).timestamp()
