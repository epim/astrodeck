"""Whole-branch review follow-up: NINA bridge honesty for UX-15 (guide RMS
units) and UX-27 (camera bin ceiling).

The review found two incompletely-applied fixes on the NINA path:
  * NinaGuider.stats() omitted is_arcsec, so genuine arcsec RMS was mislabeled
    "px" whenever NINA reported a PixelScale.
  * NinaCamera.max_bin fell back to the CURRENT bin field (BinX, default 1),
    collapsing the UI bin ceiling to 1 when MaxBinX was absent.
These tests exercise the previously-untested runtime branches directly.

A later audit found the third one on the same path: expose() sent set-binning
only when ``binning > 1``, so a bin-1 request after a bin-2 one left NINA at
bin 2 while the frame claimed bin 1 (see the ``restore``/``stamp`` tests below).
"""
from __future__ import annotations

import pytest

from astrodeck.devices.base import DeviceError
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


# ---- bin is SET every exposure, and STAMPED from what NINA reports -------

class _RecordingClient(_FakeClient):
    """Records every call, and can fail a chosen path the way an older NINA
    (no /equipment/camera/set-binning) does — with a DeviceError."""

    def __init__(self, responses: dict | None = None, fail: str = ""):
        super().__init__(responses or {})
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    async def get(self, path, **kw):
        self.calls.append((path, kw))
        if path == self.fail:
            raise DeviceError(f"NINA HTTP 404 on {path}")
        return self._responses.get(path)

    async def get_bytes(self, path, **kw):
        # _decode_gray16 degrades an undecodable body to a 1x1 zero array, which
        # is all these assertions need — no PIL round-trip.
        return b""

    def binnings(self) -> list[str]:
        return [kw.get("binning") for p, kw in self.calls
                if p == "/equipment/camera/set-binning"]


@pytest.mark.parametrize("requested,wire", [(1, "1x1"), (2, "2x2"), (3, "3x3")])
async def test_expose_sets_binning_every_time(requested, wire):
    # The defect: bin 1 was never sent, so NINA stayed at whatever bin the last
    # raised-bin exposure left it at while the frame claimed bin 1.
    cam = NinaCamera(_RecordingClient(), "cam")
    await cam.expose(1.0, 100, 30, binning=requested)
    assert cam.client.binnings() == [wire]


@pytest.mark.parametrize("requested", [0, None])
async def test_expose_normalises_a_bogus_bin_to_one(requested):
    cam = NinaCamera(_RecordingClient(), "cam")
    frame = await cam.expose(1.0, 100, 30, binning=requested)
    assert cam.client.binnings() == ["1x1"]
    assert frame.binning == 1


async def test_expose_stamps_the_bin_nina_reports_not_the_one_asked_for():
    # Older NINA 404s set-binning; the bridge tolerates that (the exposure must
    # still happen) but must not then claim a bin it failed to set. NINA reports
    # the CURRENT bin as BinX, so that is what the frame carries.
    client = _RecordingClient({"/equipment/camera/info": {"Connected": True,
                                                          "BinX": 2}},
                              fail="/equipment/camera/set-binning")
    cam = NinaCamera(client, "cam")
    frame = await cam.expose(1.0, 100, 30, binning=1)
    assert frame.binning == 2


async def test_expose_falls_back_to_the_requested_bin_when_nina_is_silent():
    # No BinX in the info payload (the mock and older builds) -> stamp what was
    # asked for rather than inventing a bin.
    cam = NinaCamera(_RecordingClient({"/equipment/camera/info": {"Connected": True}}),
                     "cam")
    frame = await cam.expose(1.0, 100, 30, binning=2)
    assert frame.binning == 2


async def test_expose_survives_a_set_binning_failure():
    # MUST NOT CHANGE: the DeviceError bridge tolerance. A NINA without the
    # endpoint still produces a frame.
    client = _RecordingClient(fail="/equipment/camera/set-binning")
    frame = await NinaCamera(client, "cam").expose(1.0, 100, 30, binning=2)
    assert frame is not None
    assert ("/equipment/camera/capture", ) == tuple(
        p for p, _ in client.calls if p == "/equipment/camera/capture")


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
