"""S7L drop-in: POST /api/capture must refuse while a video recording runs.

Harness-validated against the UNPATCHED app.py (it fails with 200 != 409, which
proves the request reaches the route and the refusal is genuinely absent).
"""
from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_mod
import astrodeck.imaging.video_routes as video_routes
from astrodeck.config import ConfigStore
from astrodeck.devices.cameras.adapter import CameraAdapter, CameraCapabilities
from astrodeck.devices.cameras.engine import NativeCamera

hub = hub_mod.hub


class _FakeAdapter(CameraAdapter):
    def __init__(self):
        self._roi = None
        self.n = 0

    def capabilities(self):
        return CameraCapabilities(
            sensor_width=64, sensor_height=48, pixel_size_um=2.9, bit_depth=16,
            bayer_pattern=None, gain_range=(0, 600), offset_range=(0, 255),
            bin_modes=(1, 2), roi_supported=True, has_cooler=False,
            has_dew_heater=False, max_adu=65535, roi_align=(8, 2))

    def open(self, index): pass
    def close(self): pass

    def start_exposure(self, *, seconds, gain, offset, roi, light):
        self._roi = roi

    def image_ready(self): return True

    def read_frame(self):
        r = self._roi
        self.n += 1
        return np.full((r.h // r.bin, r.w // r.bin), self.n % 4000,
                       dtype="<u2").tobytes()

    def abort(self): pass
    def applied_roi(self): return self._roi


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")

    cam = NativeCamera(_FakeAdapter(), name="Fake Planetary Cam")
    asyncio.run(cam.connect())
    prev = hub.devices.get("camera")
    hub.devices["camera"] = cam
    hub._busy.pop("video", None)
    video_routes.recorder._current = None

    app = app_module.create_app()
    with TestClient(app) as c:
        try:
            yield c
        finally:
            try:
                c.post("/api/capture/video/stop")
            except Exception:
                pass
    task = hub._busy.pop("video", None)
    if task is not None and not task.done():
        task.cancel()
    video_routes.recorder._current = None
    if prev is None:
        hub.devices.pop("camera", None)
    else:
        hub.devices["camera"] = prev


def _refusal(response) -> tuple[str, str]:
    """(sentence, code) out of a structured 4xx body.

    Every 4xx on this server carries a machine ``code``; these three used to
    answer a bare string, so a client had nothing but the status to branch on
    and could not tell "a recording owns the camera" from any other 409."""
    detail = response.json()["detail"]
    return detail["detail"], detail["code"]


def _record_and_wait(client, duration_s=20.0):
    """Start a recording and return once it is really running."""
    started = client.post("/api/capture/video", json={
        "roi": {"x": 0, "y": 0, "w": 32, "h": 16, "bin": 1},
        "fps": 30.0, "exposure_ms": 1.0, "gain": 100, "duration_s": duration_s})
    assert started.status_code == 202, started.text
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if client.get("/api/capture/video").json()["frames"] >= 2:
            return
        time.sleep(0.05)
    raise AssertionError("the recording never started producing frames")


def test_a_single_capture_is_refused_while_a_recording_owns_the_camera(client):
    """The mirror of the video route's own refusals.

    Without it the 409 still happens - hub.exposure_guard raises inside the
    spawned task - but the ROUTE answers 200 {"started": "capture"} and the
    failure only shows up as a log line after the fact. The operator sees a
    capture start and no frame arrive."""
    started = client.post("/api/capture/video", json={
        "roi": {"x": 0, "y": 0, "w": 32, "h": 16, "bin": 1},
        "fps": 30.0, "exposure_ms": 1.0, "gain": 100, "duration_s": 20.0})
    assert started.status_code == 202, started.text

    # Let the recording actually get going.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if client.get("/api/capture/video").json()["frames"] >= 2:
            break
        time.sleep(0.05)

    refused = client.post("/api/capture", json={"exposure_s": 1.0, "gain": 100})
    assert refused.status_code == 409, refused.text
    assert _refusal(refused) == (app_module._VIDEO_OWNS_CAMERA,
                                 "video_owns_camera")

    stopped = client.post("/api/capture/video/stop").json()
    assert stopped["cancelled"] is True
    # ...and once it is over, a single capture is allowed again.
    assert client.post("/api/capture",
                       json={"exposure_s": 0.01, "gain": 100}).status_code == 200


def test_the_loop_and_the_live_stack_are_refused_too(client):
    """The same hole, in the two routes S7d found and did not fix.

    ``/api/capture/loop`` and ``/api/capture/livestack/start`` take the camera
    exactly the way ``/api/capture`` does; they carried the sequence guard and
    the polar guard and not this one. The live stack is the worse of the two -
    it arms a stacker as well, so the operator gets a Live View panel counting
    frames that will never arrive."""
    started = client.post("/api/capture/video", json={
        "roi": {"x": 0, "y": 0, "w": 32, "h": 16, "bin": 1},
        "fps": 30.0, "exposure_ms": 1.0, "gain": 100, "duration_s": 20.0})
    assert started.status_code == 202, started.text

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if client.get("/api/capture/video").json()["frames"] >= 2:
            break
        time.sleep(0.05)

    for path in ("/api/capture/loop", "/api/capture/livestack/start"):
        r = client.post(path, json={"exposure_s": 1.0, "gain": 100})
        assert r.status_code == 409, f"{path}: {r.text}"
        assert _refusal(r) == (app_module._VIDEO_OWNS_CAMERA,
                               "video_owns_camera"), path

    # The refusal came BEFORE the arming, which is the half a status code
    # cannot show: hub.start_live_stack sits under the guard in the handler,
    # so a guard placed after it would 409 with the stacker already running.
    assert hub.live_stacker is None, (
        "the refused live stack armed the stacker anyway")
    assert hub.looping is False, "the refused loop started the camera anyway"

    assert client.post("/api/capture/video/stop").json()["cancelled"] is True


# ---------------------------------------- and the eight routes that had no guard
#
# The three capture routes above said the sentence inline; every OTHER route
# that takes the camera said nothing, so the refusal happened inside the
# spawned lane after a 202 had already gone back. Two of them are worse than
# that: ``/api/sequence/start`` and ``/api/polar/start`` SLEW. A plan or an
# alignment started under a planetary recording takes the mount away and leaves
# the recorder writing empty sky for the rest of the file, with nothing in
# either UI saying the two had met.

_CAMERA_ROUTES = (
    ("/api/sequence/start", {"name": "t", "targets": [
        {"name": "M42", "ra_hours": 5.5, "dec_deg": -5.0,
         "steps": [{"filter": None, "exposure_s": 1.0, "count": 1}]}]}),
    ("/api/polar/start", None),
    ("/api/focuser/autofocus", {"exposure_s": 1.0, "gain": 100}),
    ("/api/focuser/coarse", {"exposure_s": 1.0, "gain": 100}),
    ("/api/mount/solve_sync", None),
    ("/api/align/guide-offset/measure", None),
    ("/api/rotator/sync-to-sky", {"exposure_s": 1.0}),
    ("/api/rotator/rotate-to-pa", {"target_pa_deg": 90.0, "exposure_s": 1.0}),
)


@pytest.mark.parametrize("path,body", _CAMERA_ROUTES,
                         ids=[p for p, _ in _CAMERA_ROUTES])
def test_every_route_that_takes_the_camera_refuses_a_live_recording(
        client, path, body):
    """SABOTAGE (run red, restored): delete the ``_refuse_if_camera_owned()``
    call from any one of these handlers. That route answers 202 (or 200) with
    the recording still running - and for the two that slew, the mount moves."""
    _record_and_wait(client)
    r = client.post(path, json=body)
    assert r.status_code == 409, f"{path} answered {r.status_code}: {r.text[:200]}"
    assert _refusal(r) == (app_module._VIDEO_OWNS_CAMERA,
                           "video_owns_camera"), path
    # The refusal came BEFORE anything was spawned: a 409 with the lane already
    # running is the shape this whole file is about.
    assert not [name for name, task in hub._busy.items()
                if name != "video" and task is not None and not task.done()], (
        f"{path} was refused and started a lane anyway: {sorted(hub._busy)}")
    assert client.post("/api/capture/video/stop").json()["cancelled"] is True


@pytest.mark.parametrize("path,body", _CAMERA_ROUTES,
                         ids=[p for p, _ in _CAMERA_ROUTES])
def test_the_same_routes_are_not_refused_with_no_recording(client, path, body):
    """THE OTHER HALF, and without it the parametrised test above would pass
    against a guard wired to ``True``. None of these can SUCCEED on a rig with
    no mount, focuser or rotator connected - but none of them may answer the
    recording's 409 either."""
    r = client.post(path, json=body)
    if r.status_code == 409:
        detail = r.json().get("detail")
        code = detail.get("code") if isinstance(detail, dict) else None
        assert code != "video_owns_camera", (
            f"{path} blamed a recording that is not running: {r.text[:200]}")
