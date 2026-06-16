"""Guide-camera backend: vendor-neutral guide_frame() + the /api/guide/frame.png
endpoint + the capture-during-polar 409 guard (Lane D).

- The sim guider synthesizes a real PNG so the guide-cam preview is demoable
  offline; the PHD2 decode path turns a get_star_image payload into a PNG.
- /api/guide/frame.png answers 200 with image/png on sim, and 404 (never 500)
  when no guider is connected.
- POST /api/capture (and /capture/loop) return 409 while a polar-alignment
  session is running.
"""
from __future__ import annotations

import asyncio
import base64

import numpy as np
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.config import ConfigStore
from astrodeck.guide.phd2 import star_image_to_png
from astrodeck.guide.simguider import SimGuider


# --------------------------------------------------------------- unit: guiders

async def test_sim_guider_guide_frame_returns_png_bytes():
    g = SimGuider()
    await g.connect()
    png = await g.guide_frame()
    assert isinstance(png, (bytes, bytearray))
    assert len(png) > 0
    assert bytes(png[:8]) == b"\x89PNG\r\n\x1a\n"   # PNG magic


def test_star_image_to_png_decodes_phd2_payload():
    # A 16x16 frame with a bright blob → PHD2 returns base64 16-bit LE pixels.
    w = h = 16
    img = np.full((h, w), 800, dtype="<u2")
    img[8, 8] = 60000
    payload = {"width": w, "height": h,
               "pixels": base64.b64encode(img.tobytes()).decode(),
               "star_pos": [8, 8]}
    png = star_image_to_png(payload)
    assert isinstance(png, bytes) and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_star_image_to_png_bad_payload_is_none():
    assert star_image_to_png({}) is None
    assert star_image_to_png({"width": 0, "height": 0, "pixels": ""}) is None
    # too-short pixel buffer → None, not a crash
    assert star_image_to_png(
        {"width": 16, "height": 16, "pixels": base64.b64encode(b"\x00\x00").decode()}
    ) is None


# --------------------------------------------------------------- API fixture

@pytest.fixture
def client(tmp_path, monkeypatch):
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)

    app = app_module.create_app()
    with TestClient(app) as c:
        yield c
    # tear the singleton hub back down so the next test starts clean. The
    # TestClient's lifespan loop is gone here, so spin a throwaway loop.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(app_module.hub.disconnect_all())
    finally:
        loop.close()


# --------------------------------------------------------------- API: frame.png

def test_guide_frame_png_404_when_no_guider(client):
    # Fresh app, nothing connected → no guider → 404 (never 500).
    r = client.get("/api/guide/frame.png")
    assert r.status_code == 404


def test_guide_frame_png_200_on_sim(client):
    assert client.post("/api/connect/sim").status_code == 200
    r = client.get("/api/guide/frame.png")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_guide_camera_surfaced_in_status_on_sim(client):
    """status.guide_camera is derived so ConnectView shows a guide cam in every
    backend, not just sim's dedicated device."""
    assert client.post("/api/connect/sim").status_code == 200
    st = client.get("/api/status").json()
    gc = st.get("guide_camera")
    assert gc is not None and gc["connected"] is True and gc["name"]


# --------------------------------------------------------- API: polar 409 guard

def _capture_body():
    return {"exposure_s": 0.05, "gain": 100, "offset": 30, "binning": 1}


def test_capture_409_during_polar_run(client, monkeypatch):
    assert client.post("/api/connect/sim").status_code == 200

    # Simulate an active polar session without driving the real NINA/sim loop:
    # force the session's `running` property True for the duration of the test.
    hub = app_module.hub
    monkeypatch.setattr(type(hub.polar), "running",
                        property(lambda self: True))
    assert hub.polar.running is True

    r = client.post("/api/capture", json=_capture_body())
    assert r.status_code == 409
    assert "polar" in r.json()["detail"].lower()

    r2 = client.post("/api/capture/loop", json=_capture_body())
    assert r2.status_code == 409
    assert "polar" in r2.json()["detail"].lower()


def test_capture_allowed_when_polar_idle(client):
    assert client.post("/api/connect/sim").status_code == 200
    assert app_module.hub.polar.running is False
    r = client.post("/api/capture", json=_capture_body())
    assert r.status_code == 200, r.text
    assert r.json() == {"started": "capture"}
