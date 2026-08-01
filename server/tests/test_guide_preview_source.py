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
from astrodeck.hub import (GUIDE_PREVIEW_EXPOSURE_S, GUIDE_PREVIEW_GAIN,
                           GUIDE_PREVIEW_NOTE_TTL_S, Hub)


class _FakeGuideCam(Camera):
    """The smallest thing that can hand back a frame."""

    def __init__(self, name="ZWO ASI guide"):
        super().__init__(name)
        self.connected = True
        self.exposures: list[float] = []
        self.gains: list[int] = []
        self.fail: str | None = None
        #: incremented the moment expose() is ENTERED, before any await — a
        #: second start is the overlap we must never produce, and counting on
        #: return would miss it.
        self.started = 0
        #: when set, expose blocks on it: a real 1s exposure + USB readout + PNG
        #: encode can outlast the panel's 2.5s poll, and instant fakes are the
        #: reason that overlap was invisible.
        self.gate: "asyncio.Event | None" = None

    async def connect(self) -> None: self.connected = True
    async def disconnect(self) -> None: self.connected = False
    async def abort_exposure(self) -> None: pass
    async def get_temperature(self): return None
    async def set_temperature(self, c): pass
    async def set_cooler(self, on): pass

    async def expose(self, seconds, gain, offset, binning=1, light=True,
                     save=False, target="") -> CameraFrame:
        import numpy as np
        self.started += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError(self.fail)
        self.exposures.append(seconds)
        self.gains.append(gain)
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


async def test_a_running_sequence_does_not_block_the_guide_preview(monkeypatch):
    """``busy_label`` is a HUB-WIDE label for the IMAGING train, and by the time
    we get here the guide camera is known to be a DIFFERENT device from the
    imaging one — so refusing on it printed "ZWO ASI is busy (capturing)" about a
    camera that was sitting idle, which is the instrument-lying-about-itself
    defect this whole run exists to remove.

    It also disabled the panel at the one moment it is wanted: you check the
    guide field for dew or cloud precisely WHILE a sequence is running."""
    hub = Hub()
    cam = _FakeGuideCam()
    hub.devices["guide_camera"] = cam
    hub.devices["camera"] = _FakeGuideCam("imaging cam")     # a different sensor
    monkeypatch.setattr(type(hub), "busy_label", property(lambda self: "capturing"))

    png, reason = await hub.guide_preview_png()
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n", reason
    assert cam.exposures == [GUIDE_PREVIEW_EXPOSURE_S]


async def test_the_preview_gain_is_clamped_to_what_the_camera_accepts():
    """GUIDE_PREVIEW_GAIN is a brightness wish, not a hardware fact. An ASI120MM
    Mini — the commonest ZWO guide camera there is — tops out at 100, and the ZWO
    SDK RAISES on an out-of-range control value instead of clipping it. Sending
    200 unclamped turns the preview back into "could not deliver a frame" on
    exactly the rigs this fix is for."""
    hub = Hub()
    cam = _FakeGuideCam("ASI120MM Mini")
    cam.max_gain = 100                       # what the SDK's gain_range reported
    hub.devices["guide_camera"] = cam

    png, reason = await hub.guide_preview_png()
    assert png, reason
    assert cam.gains == [100]


async def test_a_camera_that_reports_no_gain_ceiling_gets_the_wish_unchanged():
    """0 means "this backend does not report a ceiling" (NINA with no GainMax, an
    Alpaca camera without gainmax) — clamping to it would send gain 0, and
    inventing a limit is the guess the codebase's own rule forbids. Let the
    driver refuse it by name instead."""
    hub = Hub()
    cam = _FakeGuideCam()
    cam.max_gain = 0
    hub.devices["guide_camera"] = cam

    png, reason = await hub.guide_preview_png()
    assert png, reason
    assert cam.gains == [GUIDE_PREVIEW_GAIN]


async def test_two_overlapping_polls_share_one_exposure():
    """The panel swaps a cache-busted <img src> every 2.5s whether or not the
    previous one landed, and a 1s exposure plus a full-sensor USB readout plus a
    PNG encode can outlast that. A second ``start_exposure`` on a camera that is
    already exposing races two readouts over one buffer — torn or errored frames,
    which the panel renders as the same uninformative "unavailable", sending the
    next debugging session back to the beginning."""
    import asyncio

    hub = Hub()
    cam = _FakeGuideCam()
    cam.gate = asyncio.Event()
    hub.devices["guide_camera"] = cam

    first = asyncio.create_task(hub.guide_preview_png())
    await asyncio.sleep(0.02)
    second = asyncio.create_task(hub.guide_preview_png())
    await asyncio.sleep(0.02)
    assert cam.started == 1, "the second poll must JOIN the exposure in flight"

    cam.gate.set()
    (png1, _), (png2, _) = await asyncio.gather(first, second)
    assert png1 and png1 == png2
    assert cam.started == 1 and cam.exposures == [GUIDE_PREVIEW_EXPOSURE_S]


async def test_a_client_that_walks_away_does_not_abort_the_exposure():
    """The request task is cancelled when the browser drops the <img> load. That
    must cancel the WAIT, not the exposure: killing a camera mid-readout to serve
    a panel nobody is watching is how a guide camera ends up wedged for the run
    that follows."""
    import asyncio

    hub = Hub()
    cam = _FakeGuideCam()
    cam.gate = asyncio.Event()
    hub.devices["guide_camera"] = cam

    first = asyncio.create_task(hub.guide_preview_png())
    await asyncio.sleep(0.02)
    first.cancel()
    second = asyncio.create_task(hub.guide_preview_png())
    await asyncio.sleep(0.02)
    cam.gate.set()

    png, reason = await second
    assert png, reason
    assert cam.started == 1, "the surviving poll rode the exposure already running"


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


# --------------------------------------------------- the reason reaches the UI

async def test_the_refusal_reaches_the_panel_through_status():
    """The 404 detail is careful and specific and NOBODY READS IT: the preview
    panel assigns an ``<img src>``, so all it can observe is that the load
    errored, and every named refusal collapses into one generic sentence on the
    way. So the reason also rides status.guide_camera, the same additive way
    filterwheel.moving does."""
    hub = Hub()
    cam = _FakeGuideCam()
    cam.fail = "camera not responding"
    hub.devices["guide_camera"] = cam

    await hub.guide_preview_png()
    st = await hub.poll_status()
    assert "camera not responding" in st["guide_camera"]["preview_reason"]


async def test_a_working_preview_publishes_no_reason():
    """Absent, not empty: a key that is always there invites a UI that always
    renders a line."""
    hub = Hub()
    hub.devices["guide_camera"] = _FakeGuideCam()

    png, _ = await hub.guide_preview_png()
    assert png
    st = await hub.poll_status()
    assert "preview_reason" not in st["guide_camera"]


async def test_the_reason_expires_rather_than_describing_a_fixed_camera(monkeypatch):
    """A reason survives only as long as the panel that produced it is plausibly
    still polling. Left to sit, it would describe a camera that has since been
    reconnected — a confident stale claim, which is the class of bug this run is
    about, not a fix for it."""
    import astrodeck.hub as hub_mod

    hub = Hub()
    cam = _FakeGuideCam()
    cam.fail = "USB read failed"
    hub.devices["guide_camera"] = cam
    await hub.guide_preview_png()
    assert hub.guide_preview_note()

    base = hub_mod.time.monotonic()
    monkeypatch.setattr(hub_mod.time, "monotonic",
                        lambda: base + GUIDE_PREVIEW_NOTE_TTL_S + 1)
    assert hub.guide_preview_note() == ""


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
