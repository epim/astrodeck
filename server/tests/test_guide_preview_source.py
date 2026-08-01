"""Where the "Guide cam" panel's picture actually comes from.

Field report, 2026-07-31: turning on Guide cam under Capture did nothing. The
rig's ZWO ASI guide camera was connected and idle the whole time — the route
just never asked it. ``/api/guide/frame.png`` gated on ``hub.guider``, and
``hub.guider`` is None on a native rig unless a profile explicitly overrides the
``guider`` role: ``NativeBackend.roles`` deliberately excludes it, and the
Equipment screen even tells the user there is "no guider device to assign here".
So the panel 404'd forever on a rig that had everything it needed.

``Hub.guide_preview_png`` is the source-selection rule, and these are its edges.
Nothing here needs hardware.

NOTE ON OWNERSHIP: this is a NEW file rather than an addition to
tests/test_guide_frame.py, which belongs to another lane this run.
"""
from __future__ import annotations

import pytest

from astrodeck.devices.base import Camera, CameraFrame
from astrodeck.hub import GUIDE_PREVIEW_EXPOSURE_S, Hub


class _FakeGuideCam(Camera):
    """The smallest thing that can hand back a frame."""

    def __init__(self, name="ZWO ASI guide"):
        super().__init__(name)
        self.connected = True
        self.exposures: list[float] = []
        self.fail: str | None = None

    async def connect(self) -> None: self.connected = True
    async def disconnect(self) -> None: self.connected = False
    async def abort_exposure(self) -> None: pass
    async def get_temperature(self): return None
    async def set_temperature(self, c): pass
    async def set_cooler(self, on): pass

    async def expose(self, seconds, gain, offset, binning=1, light=True,
                     save=False, target="") -> CameraFrame:
        import numpy as np
        if self.fail:
            raise RuntimeError(self.fail)
        self.exposures.append(seconds)
        data = np.full((64, 64), 300, dtype="uint16")
        data[32, 32] = 40000
        return CameraFrame(data=data, exposure_s=seconds, gain=gain,
                           offset=offset, binning=binning, bayer_pattern=None,
                           temperature_c=None, timestamp=0.0)


class _Guider:
    name = "PHD2"
    connected = True

    def __init__(self, png: bytes | None): self.png = png
    async def guide_frame(self): return self.png


async def test_a_connected_guide_camera_serves_the_preview_with_no_guider():
    """THE bug. No guider role assigned — which is the normal state of a native
    rig — and the panel must still show the guide field."""
    hub = Hub()
    cam = _FakeGuideCam()
    hub.devices["guide_camera"] = cam

    png, reason = await hub.guide_preview_png()
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n", reason
    assert cam.exposures == [GUIDE_PREVIEW_EXPOSURE_S]


async def test_a_running_guider_wins_over_the_camera():
    """While a guide loop owns the sensor, its own last frame is the truth and
    exposing underneath it would be a bug, not a preview."""
    hub = Hub()
    cam = _FakeGuideCam()
    hub.devices["guide_camera"] = cam
    hub.guider = _Guider(b"\x89PNG\r\n\x1a\nfrom-the-guider")

    png, _ = await hub.guide_preview_png()
    assert png == b"\x89PNG\r\n\x1a\nfrom-the-guider"
    assert cam.exposures == [], "must not touch a camera the guider is driving"


async def test_a_guider_with_no_image_says_which_guider():
    """PHD2 in NINA mode exposes no raw frame. That is a fact about the guider,
    so name it — "unavailable" was the entire complaint."""
    hub = Hub()
    hub.guider = _Guider(None)
    png, reason = await hub.guide_preview_png()
    assert png is None
    assert "PHD2" in reason


async def test_one_camera_in_both_roles_is_left_to_the_imaging_train():
    """An OAG rig can assign the same physical camera to both roles. Grabbing a
    preview frame there would take the sensor out from under a running
    sequence — so refuse, and say where its frames DO appear."""
    hub = Hub()
    cam = _FakeGuideCam("Player One")
    hub.devices["guide_camera"] = cam
    hub.devices["camera"] = cam

    png, reason = await hub.guide_preview_png()
    assert png is None
    assert cam.exposures == []
    assert "imaging camera" in reason and "Player One" in reason


async def test_a_busy_rig_names_what_is_holding_the_camera(monkeypatch):
    """A guide camera being driven by an autofocus sweep or a capture loop is a
    different nothing from a guide camera that is not there, and the panel is
    entitled to know which."""
    hub = Hub()
    cam = _FakeGuideCam()
    hub.devices["guide_camera"] = cam
    monkeypatch.setattr(type(hub), "busy_label", property(lambda self: "focusing"))

    png, reason = await hub.guide_preview_png()
    assert png is None and "focusing" in reason
    assert cam.exposures == []


async def test_a_camera_that_refuses_reports_the_refusal_not_a_shrug():
    """A blank "unavailable" note is what made this look like nothing happened.
    The camera's own error is the only thing that tells the user whether to
    re-seat a USB cable or go and look at the sky."""
    hub = Hub()
    cam = _FakeGuideCam()
    cam.fail = "camera not responding"
    hub.devices["guide_camera"] = cam
    png, reason = await hub.guide_preview_png()
    assert png is None
    assert "camera not responding" in reason


async def test_nothing_connected_says_what_to_connect():
    hub = Hub()
    png, reason = await hub.guide_preview_png()
    assert png is None
    # A dead end with no exit is the thing house style forbids: name the screen.
    assert "Equipment" in reason or "PHD2" in reason


@pytest.mark.parametrize("detail", ["Equipment", "guide camera"])
def test_the_endpoint_passes_the_reason_through_as_the_404_detail(detail, tmp_path,
                                                                 monkeypatch):
    """Still 404, never 500 — but a 404 that says which of the several possible
    nothings this is."""
    import astrodeck.api.app as app_module
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_module
    from astrodeck.config import ConfigStore
    from fastapi.testclient import TestClient

    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_module, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")

    app = app_module.create_app()
    with TestClient(app) as c:
        r = c.get("/api/guide/frame.png")
        assert r.status_code == 404
        assert detail in r.json()["detail"]
