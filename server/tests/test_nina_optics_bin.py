"""Whole-branch review follow-up: NINA bridge honesty for UX-15 (guide RMS
units) and UX-27 (camera bin ceiling).

The review found two incompletely-applied fixes on the NINA path:
  * NinaGuider.stats() omitted is_arcsec, so genuine arcsec RMS was mislabeled
    "px" whenever NINA reported a PixelScale.
  * NinaCamera.max_bin fell back to the CURRENT bin field (BinX, default 1),
    collapsing the UI bin ceiling to 1 when MaxBinX was absent.
These tests exercise the previously-untested runtime branches directly.
"""
from __future__ import annotations

from astrodeck.devices.nina import NinaCamera, NinaGuider


class _FakeClient:
    """Minimal NinaClient stand-in: serves a canned dict per path."""

    def __init__(self, responses: dict):
        self.host = "nina.test"
        self.port = 1888
        self._responses = responses

    async def get(self, path, **kw):
        return self._responses.get(path)


# ---- UX-27: NINA camera bin ceiling -------------------------------------

async def test_nina_camera_max_bin_from_maxbinx():
    cam = NinaCamera(
        _FakeClient({"/equipment/camera/info": {"Connected": True, "MaxBinX": 3}}), "cam")
    await cam.connect()
    assert cam.max_bin == 3


async def test_nina_camera_max_bin_ignores_current_bin():
    # NINA reports the CURRENT bin (BinX=1) but no MaxBinX -> the ceiling must NOT
    # collapse to 1; it falls back to the base default 4.
    cam = NinaCamera(
        _FakeClient({"/equipment/camera/info": {"Connected": True, "BinX": 1}}), "cam")
    await cam.connect()
    assert cam.max_bin == 4


async def test_nina_camera_max_bin_defaults_when_absent():
    cam = NinaCamera(
        _FakeClient({"/equipment/camera/info": {"Connected": True}}), "cam")
    await cam.connect()
    assert cam.max_bin == 4


# ---- UX-15: NINA guide RMS units ----------------------------------------

async def test_nina_guider_reports_arcsec_when_pixelscale_known():
    g = NinaGuider(
        _FakeClient({"/equipment/guider/info": {"Connected": True, "PixelScale": 1.3}}))
    await g.connect()
    s = g.stats()
    assert s.is_arcsec is True
    assert s.image_scale == 1.3


async def test_nina_guider_reports_px_when_pixelscale_absent():
    g = NinaGuider(
        _FakeClient({"/equipment/guider/info": {"Connected": True}}))
    await g.connect()
    s = g.stats()
    assert s.is_arcsec is False
    assert s.image_scale == 0.0
