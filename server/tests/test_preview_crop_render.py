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


# --------------------------------------------------- hostile stretch levels
# `/render.png` takes black/mid/white straight off the query string as plain
# floats, and FastAPI accepts `?black=nan` for a `float` param. `levels_to_mtf`
# guarded the range with `np.clip`, which PROPAGATES NaN instead of clamping it
# — so a NaN survived every guard, poisoned the whole stretched array, and
# encoded as a *valid* all-black PNG served with HTTP 200. The user asked for a
# baked export and got a blank frame with no error.
#
# The contract is now that `levels_to_mtf` is TOTAL: three finite numbers out,
# whatever goes in. That is asserted directly below, because it is the property
# that was violated — an endpoint-level assertion on PNG bytes does NOT catch
# this (a uniform frame and a smooth gradient both compress to a few hundred
# bytes, so no byte-length threshold separates them).

_HOSTILE_LEVELS = [
    (float("nan"), 0.5, 1.0),                       # NaN in each slot
    (0.0, float("nan"), 1.0),
    (0.0, 0.5, float("nan")),
    (float("nan"), float("nan"), float("nan")),     # all three
    (float("-inf"), 0.5, float("inf")),             # infinities saturate
    (0.9, 0.5, 0.1),                                # inverted order
    (-5.0, 0.5, 12.0),                              # far out of range
    (0.0, 0.0, 1.0),                                # mid at the MTF pole
]


@pytest.mark.parametrize("black,mid,white", _HOSTILE_LEVELS)
def test_levels_to_mtf_is_total(black, mid, white):
    """Every triple normalizes to three FINITE numbers in the documented
    order/range. Fails on the pre-fix normalizer for all four NaN rows."""
    import math

    from astrodeck.imaging.processing import levels_to_mtf

    b, m, w = levels_to_mtf(black, mid, white)
    assert math.isfinite(b) and math.isfinite(m) and math.isfinite(w), (b, m, w)
    assert 0.0 <= b < w <= 1.0, f"order/range violated: {(b, m, w)}"
    assert 0.0 < m < 1.0, f"midtones outside the open unit interval: {m}"


@pytest.mark.parametrize("black,mid,white", _HOSTILE_LEVELS)
def test_stretch_never_emits_nan(black, mid, white):
    """The array handed to the encoder is NaN-free, so the cast to uint8 is
    defined. Fails on the pre-fix normalizer for all four NaN rows (numpy also
    raises `RuntimeWarning: invalid value encountered in cast` there)."""
    from astrodeck.imaging.processing import stretch_with

    arr = np.tile(np.linspace(0, 65535, 128, dtype=np.uint16), (40, 1))
    out = stretch_with(arr, black, mid, white)
    assert not np.isnan(out).any(), f"NaN reached the encoder for {(black, mid, white)}"


def test_render_serves_a_real_image_for_hostile_levels(client):
    """Integration end of the same contract: the endpoint still answers 200
    with a full-resolution PNG when handed a NaN off the query string."""
    arr = np.tile(np.linspace(0, 65535, 128, dtype=np.uint16), (40, 1))
    _seed(8, arr)
    r = client.get("/api/preview/8/render.png",
                   params={"black": "nan", "mid": "nan", "white": "nan"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert _png_dims(r.content) == (128, 40)
    # The gradient must survive as a gradient — the poisoned path produced a
    # uniform frame, which this separates from a real stretch (byte length
    # does not: both compress to a few hundred bytes).
    px = np.asarray(Image.open(io.BytesIO(r.content)).convert("L"))
    assert len(np.unique(px)) > 2, f"degenerate frame: {np.unique(px)[:8]}"
