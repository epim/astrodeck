"""Preview /crop + /render.png (the former "Pass 2" stubs): a sensor-1:1 ROI
zoom and a full-resolution baked-stretch export, both cut from the LINEAR frame
array held for the latest 1-2 previews. 404 when that linear array is gone.
"""
from __future__ import annotations

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import astrodeck.api.app as app_module


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    from astrodeck.config import ConfigStore
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    app = app_module.create_app()
    from astrodeck.hub import hub
    hub.previews.clear()
    with TestClient(app) as c:
        yield c
    hub.previews.clear()


def _seed(pid: int, arr: "np.ndarray | None") -> None:
    from astrodeck.hub import PreviewEntry, hub
    hub.previews[pid] = PreviewEntry(
        display=b"", mime="image/png", thumb=b"", lossless=None, linear=arr,
        meta={})


def _png_dims(body: bytes) -> tuple[int, int]:
    im = Image.open(io.BytesIO(body))
    return im.width, im.height


# --------------------------------------------------------------------- /crop

def test_crop_returns_1to1_png_of_roi(client):
    arr = np.arange(200 * 300, dtype=np.uint16).reshape(200, 300)  # H=200, W=300
    _seed(1, arr)
    r = client.get("/api/preview/1/crop",
                   params={"x": 50, "y": 40, "w": 80, "h": 60})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert _png_dims(r.content) == (80, 60)   # 1:1, no downscale


def test_crop_clamps_out_of_range_roi(client):
    arr = np.zeros((100, 120), dtype=np.uint16)
    _seed(2, arr)
    # x past the sensor + huge w/h -> clamped inside, never over-reads.
    r = client.get("/api/preview/2/crop",
                   params={"x": 110, "y": 0, "w": 9999, "h": 9999})
    assert r.status_code == 200
    assert _png_dims(r.content) == (120 - 110, 100)


def test_crop_defaults_to_full_frame(client):
    arr = np.zeros((64, 96), dtype=np.uint16)
    _seed(3, arr)
    r = client.get("/api/preview/3/crop")   # no params -> whole frame at 1:1
    assert r.status_code == 200
    assert _png_dims(r.content) == (96, 64)


def test_crop_404_when_linear_unavailable(client):
    _seed(4, None)                                  # entry exists, linear gone
    assert client.get("/api/preview/4/crop").status_code == 404
    assert client.get("/api/preview/999/crop").status_code == 404   # no entry


# ----------------------------------------------------------------- /render.png

def test_render_bakes_at_full_resolution(client):
    arr = np.full((70, 90), 30000, dtype=np.uint16)
    _seed(5, arr)
    r = client.get("/api/preview/5/render.png",
                   params={"black": 0.0, "mid": 0.5, "white": 1.0})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert _png_dims(r.content) == (90, 70)   # full native resolution


def test_render_levels_change_the_baked_output(client):
    # a horizontal gradient so different clip levels produce different bytes.
    arr = np.tile(np.linspace(0, 65535, 128, dtype=np.uint16), (40, 1))
    _seed(6, arr)
    a = client.get("/api/preview/6/render.png",
                   params={"black": 0.0, "mid": 0.5, "white": 1.0}).content
    b = client.get("/api/preview/6/render.png",
                   params={"black": 0.3, "mid": 0.5, "white": 0.7}).content
    assert a != b


def test_render_404_when_linear_unavailable(client):
    _seed(7, None)
    assert client.get("/api/preview/7/render.png").status_code == 404
    assert client.get("/api/preview/54321/render.png").status_code == 404
