"""A3 (final-branch-review tonight-risk #5): AlpacaCamera.expose enforces an
overall imageready poll deadline (exposure_s + 30 s) so a responsive-but-stuck
camera cannot hang the guide loop forever."""
import asyncio

import pytest

import astrodeck.devices.alpaca as alpaca
from astrodeck.devices.alpaca import AlpacaCamera, AlpacaConnection
from astrodeck.devices.base import DeviceError


def _stub_camera() -> AlpacaCamera:
    cam = AlpacaCamera(AlpacaConnection("127.0.0.1", 11111), 0, "stub")
    cam.sensor_width = 640
    cam.sensor_height = 480
    cam.max_gain = 0
    return cam


@pytest.mark.asyncio
async def test_imageready_never_true_times_out(monkeypatch):
    # Zero margin so the deadline is exposure_s past start; a never-ready poll
    # must raise DeviceError("imageready timeout"), not loop forever.
    monkeypatch.setattr(alpaca, "_IMAGEREADY_POLL_MARGIN_S", 0.0)
    cam = _stub_camera()

    async def _put(method, **params):
        return None

    async def _get(method, **params):
        return False  # imageready is never true

    monkeypatch.setattr(cam, "_put", _put)
    monkeypatch.setattr(cam, "_get", _get)

    with pytest.raises(DeviceError, match="imageready timeout"):
        await cam.expose(0.01, 100, 30, binning=1)
    assert cam._exposing is False  # the finally: cleared it


@pytest.mark.asyncio
async def test_imageready_true_first_poll_no_timeout(monkeypatch):
    # Happy path unaffected: imageready true on the first poll returns a frame.
    monkeypatch.setattr(alpaca, "_IMAGEREADY_POLL_MARGIN_S", 30.0)
    cam = _stub_camera()

    async def _put(method, **params):
        return None

    async def _get(method, **params):
        return True

    async def _download_image():
        import numpy as np
        return np.zeros((480, 640), dtype=np.uint16)

    async def _temp():
        return -10.0

    monkeypatch.setattr(cam, "_put", _put)
    monkeypatch.setattr(cam, "_get", _get)
    monkeypatch.setattr(cam, "_download_image", _download_image)
    monkeypatch.setattr(cam, "get_temperature", _temp)

    frame = await cam.expose(0.01, 100, 30, binning=1)
    assert frame.data.shape == (480, 640)
    assert cam._exposing is False
