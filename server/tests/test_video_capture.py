"""SER video capture (D-RIG-1): the recorder, its lane, and the route refusals.

Everything here runs against a FAKE adapter behind the real NativeCamera, so the
engine's exposure lifecycle - start / poll / read / applied_roi read-back - is
the one under test rather than a stand-in for it. What the fake removes is the
USB and the sensor, not the code path.
"""
from __future__ import annotations

import asyncio
import time

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import astrodeck.hub as hub_mod
import astrodeck.imaging.video as video_mod
import astrodeck.imaging.video_routes as routes_mod
from astrodeck.devices.cameras.adapter import CameraAdapter, CameraCapabilities
from astrodeck.devices.cameras.engine import NativeCamera
from astrodeck.events import bus
from astrodeck.imaging.ser import read_frames, read_header

hub = hub_mod.hub


class FakeVideoAdapter(CameraAdapter):
    """A sensor that honours the ROI and stamps each frame with its index.

    Stamping matters: it is how the test tells "the writer got N frames" from
    "the writer got one frame N times", which a constant fill could not."""

    def __init__(self, w=64, h=48, *, bayer=None, roi_align=(8, 2),
                 burst=False, max_fps=None):
        self._w, self._h = w, h
        self._bayer = bayer
        self._roi_align = roi_align
        self._burst = burst
        self._max_fps = max_fps
        self._roi = None
        self.frames_read = 0
        self.opened = False

    def capabilities(self):
        return CameraCapabilities(
            sensor_width=self._w, sensor_height=self._h, pixel_size_um=2.9,
            bit_depth=16, bayer_pattern=self._bayer, gain_range=(0, 600),
            offset_range=(0, 255), bin_modes=(1, 2), roi_supported=True,
            has_cooler=False, has_dew_heater=False, max_adu=65535,
            burst_supported=self._burst, max_fps=self._max_fps,
            roi_align=self._roi_align)

    def open(self, index):
        self.opened = True

    def close(self):
        self.opened = False

    def start_exposure(self, *, seconds, gain, offset, roi, light):
        self._roi = roi

    def image_ready(self):
        return True

    def read_frame(self):
        r = self._roi
        w, h = r.w // r.bin, r.h // r.bin
        self.frames_read += 1
        return np.full((h, w), self.frames_read % 4000, dtype="<u2").tobytes()

    def abort(self):
        pass

    def applied_roi(self):
        return self._roi


class NotNativeCamera:
    """A camera driven through somebody else's stack: no adapter, no ROI."""
    name = "NINA Camera"
    backend = "the NINA bridge"
    connected = True
    kind = "camera"
    sensor_width = 1000
    sensor_height = 800


def _connect(adapter) -> NativeCamera:
    """Connect off the test's loop: NativeCamera.connect touches no lock, so the
    per-device lock stays unbound until the test's own loop acquires it."""
    cam = NativeCamera(adapter, name="Fake Planetary Cam")
    asyncio.run(cam.connect())
    return cam


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Captures redirected, a native camera on the hub, the lane left clean."""
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    adapter = FakeVideoAdapter()
    cam = _connect(adapter)
    prev_cam = hub.devices.get("camera")
    prev_engine = hub.engine
    hub.devices["camera"] = cam
    hub.engine = None
    hub._busy.pop("video", None)
    hub._busy.pop("video_stack", None)
    routes_mod.recorder._current = None
    yield cam
    task = hub._busy.pop("video", None)
    if task is not None and not task.done():
        task.cancel()
    hub._busy.pop("video_stack", None)
    routes_mod.recorder._current = None
    if prev_cam is None:
        hub.devices.pop("camera", None)
    else:
        hub.devices["camera"] = prev_cam
    hub.engine = prev_engine


@pytest.fixture
def client(rig):
    app = FastAPI()
    app.include_router(routes_mod.router)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def video_events(monkeypatch):
    """Every ``video`` event the recorder publishes, in order."""
    seen: list[dict] = []
    original = bus.publish

    def spy(type_, **data):
        if type_ == "video":
            seen.append(dict(data))
        return original(type_, **data)

    monkeypatch.setattr(bus, "publish", spy)
    return seen


def _body(**over) -> dict:
    body = {"roi": {"x": 0, "y": 0, "w": 32, "h": 16, "bin": 1},
            "fps": 30.0, "exposure_ms": 1.0, "gain": 100,
            "duration_s": 0.6, "format": "ser"}
    body.update(over)
    return body


def _wait_idle(client, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = client.get("/api/capture/video").json()
        if not status["active"]:
            return status
        time.sleep(0.05)
    raise AssertionError(f"recording never finished: {status}")


# --------------------------------------------------------------- the recording

def test_a_recording_writes_a_valid_ser_the_frames_can_be_read_back(client, rig):
    started = client.post("/api/capture/video", json=_body(duration_s=2.0))
    assert started.status_code == 202, started.text
    payload = started.json()
    assert payload["started"] == "video"
    assert payload["target_frames"] >= 1
    assert payload["est_bytes"] > 178

    final = _wait_idle(client)
    assert final["state"] == "done", final
    assert final["error"] is None
    assert final["frames"] >= 5, (
        f"a 2 s recording produced only {final['frames']} frames")

    path = video_mod.video_dir() / f"{payload['id']}.ser"
    head = read_header(path)
    assert head["frames"] == final["frames"]
    assert (head["width"], head["height"]) == (32, 16)
    assert head["bit_depth"] == 16
    assert head["color_id"] == 0, "the fake sensor is mono"
    assert head["instrument"] == "Fake Planetary Cam"
    assert path.stat().st_size == 178 + head["frames"] * 32 * 16 * 2 + head["frames"] * 8

    stamps = [int(f[0, 0]) for f in read_frames(path)]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps), (
        "each frame carries its own read index, so a repeated or reordered "
        f"stamp means the writer did not get distinct frames: {stamps}")

    side = path.with_suffix(".json")
    assert side.is_file()
    import json
    meta = json.loads(side.read_text(encoding="utf-8"))
    assert meta["state"] == "done"
    assert meta["camera"] == "Fake Planetary Cam"
    assert meta["roi"] == {"x": 0, "y": 0, "w": 32, "h": 16, "bin": 1}
    assert meta["achieved_fps"] > 0
    assert meta["path_source"] == "snap-fallback"


def test_a_running_recording_publishes_its_lane(client):
    """SABOTAGE TARGET: remove ``hub._busy["video"] = task`` in VideoRecorder.start.

    The lane is not decoration. ``hub._live_lanes`` reads ``hub._busy``, and
    everything that asks "is the rig too busy to restart / reset / start another
    exposure" reads that - so a recording with no lane is a recording the
    updater will happily restart the process in the middle of."""
    assert "video" not in hub.busy_lanes(), "precondition: nothing recording"

    started = client.post("/api/capture/video", json=_body(duration_s=2.0))
    assert started.status_code == 202
    # Today ``video`` is an UNLABELLED lane: busy_lanes() lists it (that is a
    # plain read of hub._busy) but busy_label does not, so it does not yet block
    # a restart. S7c's BUSY_LANE_LABELS entry ("video", "recording") is what
    # makes it blocking; this asserts what is true before that lands.
    assert "video" in hub.busy_lanes()

    final = _wait_idle(client)
    assert final["state"] == "done"
    assert "video" not in hub.busy_lanes(), "the lane must clear when it ends"


def test_progress_reaches_the_bus_with_frames_that_only_go_up(client, video_events):
    started = client.post("/api/capture/video", json=_body(duration_s=2.0))
    assert started.status_code == 202
    _wait_idle(client)

    assert video_events, "a recording that publishes nothing is invisible in the UI"
    states = [e["state"] for e in video_events]
    assert states[0] == "arming"
    assert states[-1] == "done"
    assert "recording" in states
    assert "finalising" in states

    frames = [e["frames"] for e in video_events]
    assert frames == sorted(frames), f"frame counts went backwards: {frames}"
    assert frames[-1] > 0
    for ev in video_events:
        assert set(ev) == {"id", "state", "frames", "target_frames", "elapsed_s",
                           "fps", "dropped", "bytes", "path"}
    assert video_events[-1]["path"] == f"video/{started.json()['id']}.ser"


def test_a_cancel_mid_recording_leaves_a_valid_short_file(client):
    started = client.post("/api/capture/video", json=_body(duration_s=20.0))
    assert started.status_code == 202
    rec_id = started.json()["id"]
    target = started.json()["target_frames"]

    # Let a few frames land before pulling the plug.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if client.get("/api/capture/video").json()["frames"] >= 3:
            break
        time.sleep(0.05)

    stopped = client.post("/api/capture/video/stop").json()
    assert stopped["cancelled"] is True
    assert stopped["id"] == rec_id
    assert 0 < stopped["frames"] < target

    status = client.get("/api/capture/video").json()
    assert status["state"] == "cancelled"
    assert status["active"] is False

    path = video_mod.video_dir() / f"{rec_id}.ser"
    head = read_header(path)
    assert head["frames"] == stopped["frames"]
    assert path.stat().st_size == (178 + head["frames"] * 32 * 16 * 2
                                   + head["frames"] * 8)
    assert len(list(read_frames(path))) == head["frames"], (
        "a cancelled recording must still be a file every frame can be read out of")


def test_a_second_recording_while_one_runs_is_refused(client):
    assert client.post("/api/capture/video",
                       json=_body(duration_s=20.0)).status_code == 202
    second = client.post("/api/capture/video", json=_body(duration_s=1.0))
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "lane_busy"
    client.post("/api/capture/video/stop")


# ----------------------------------------------------------------- the clamp

def test_the_fallback_rate_is_clamped_and_says_why(client):
    """A recorder that quietly delivers a fifth of the requested cadence hands
    back a night of unusable data, discovered at the stacking stage."""
    started = client.post("/api/capture/video",
                          json=_body(fps=200.0, duration_s=0.6)).json()
    assert started["clamped"] is True
    assert started["actual_fps"] < 200.0
    assert "burst" in started["clamp_reason"] and "ms" in started["clamp_reason"]
    final = _wait_idle(client)
    # The measured rate must be near the PROMISED one, not near the requested
    # one - that is the whole point of publishing both.
    assert final["fps"] <= started["actual_fps"] * 1.6


def test_a_declared_max_fps_clamps_below_the_engine_floor(rig):
    caps = video_mod.camera_capabilities(rig)
    from dataclasses import replace
    slow = replace(caps, max_fps=5.0)
    fps, clamped, reason = video_mod.plan_rate(
        requested_fps=100.0, exposure_s=0.001, use_burst=False, caps=slow)
    assert (fps, clamped) == (5.0, True)
    assert "ceiling of 5 fps" in reason


def test_the_roi_is_rounded_onto_the_sensor_alignment_before_the_request(rig):
    caps = video_mod.camera_capabilities(rig)
    assert caps.roi_align == (8, 2), "precondition: the fake declares ZWO's rule"
    roi = video_mod.align_roi(0, 0, 30, 15, 1, caps, 64, 48)
    assert (roi.w, roi.h) == (24, 14)
    # At bin 2 the step is lcm(align, bin), so the BINNED row length is whole.
    roi2 = video_mod.align_roi(0, 0, 30, 15, 2, caps, 64, 48)
    assert roi2.w % 8 == 0 and roi2.w % 2 == 0
    assert (roi2.w // 2) * 2 == roi2.w


# ------------------------------------------------------------------ refusals

def test_a_camera_with_no_video_path_is_told_which_backend_is_in_the_way(client):
    hub.devices["camera"] = NotNativeCamera()
    r = client.post("/api/capture/video", json=_body())
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "no_video_path"
    assert "NINA Camera" in detail["detail"]
    assert "the NINA bridge" in detail["detail"]
    assert "natively-driven camera" in detail["detail"]


def test_no_camera_is_the_same_bare_string_409_that_post_api_capture_gives(client):
    hub.devices.pop("camera", None)
    r = client.post("/api/capture/video", json=_body())
    assert r.status_code == 409
    assert r.json()["detail"] == "no camera connected"


def test_the_live_loop_owning_the_camera_is_refused_by_name(client, monkeypatch):
    monkeypatch.setattr(type(hub), "looping", property(lambda self: True))
    r = client.post("/api/capture/video", json=_body())
    assert r.status_code == 409
    assert r.json()["detail"] == "the live loop owns the camera"


def test_a_running_sequence_is_refused_by_name(client):
    class Engine:
        owns_camera = True
    hub.engine = Engine()
    r = client.post("/api/capture/video", json=_body())
    assert r.status_code == 409
    assert r.json()["detail"] == "a sequence is running"


def test_a_full_disk_is_a_507_carrying_both_numbers(client, monkeypatch):
    monkeypatch.setattr(video_mod, "free_bytes", lambda _p: 1234)
    r = client.post("/api/capture/video", json=_body())
    assert r.status_code == 507
    detail = r.json()["detail"]
    assert detail["detail"] == "insufficient disk space"
    assert detail["free_bytes"] == 1234
    assert detail["required_bytes"] > 1234


# ------------------------------------------------------------------- library

def test_the_library_lists_a_finished_recording_and_serves_its_bytes(client):
    started = client.post("/api/capture/video", json=_body(duration_s=0.6)).json()
    _wait_idle(client)

    listing = client.get("/api/captures/video").json()["recordings"]
    assert [r["id"] for r in listing] == [started["id"]]
    row = listing[0]
    assert row["camera"] == "Fake Planetary Cam"
    assert row["frames"] > 0
    assert row["has_stack"] is False
    assert row["roi"]["w"] == 32

    raw = client.get(f"/api/captures/video/{started['id']}.ser")
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "application/octet-stream"
    assert raw.content[:14] == b"LUCAM-RECORDER"
    assert len(raw.content) == row["bytes"]

    assert client.delete(f"/api/captures/video/{started['id']}").status_code == 200
    assert client.get("/api/captures/video").json()["recordings"] == []
    assert client.get(f"/api/captures/video/{started['id']}.ser").status_code == 404


@pytest.mark.parametrize("bad", ["..", "../secret", r"..\..\secret",
                                 "con", "a:b", "with/slash"])
def test_a_traversal_id_404s_instead_of_reading_the_disk(client, bad):
    r = client.get(f"/api/captures/video/{bad}.ser")
    assert r.status_code == 404, r.text
    assert client.delete(f"/api/captures/video/{bad}").status_code == 404


def test_an_unknown_recording_404s_rather_than_500s(client):
    assert client.get("/api/captures/video/nope.ser").status_code == 404
    assert client.get("/api/captures/video/nope/stack.png").status_code == 404
    assert client.post("/api/captures/video/nope/stack",
                       json={"keep_pct": 25.0}).status_code == 404


# ------------------------------------------------------- stage 2: quick stack

def test_a_recording_can_be_lucky_stacked_and_the_png_served(client):
    started = client.post("/api/capture/video", json=_body(duration_s=1.0)).json()
    _wait_idle(client)
    rec_id = started["id"]

    assert client.get(f"/api/captures/video/{rec_id}/stack.png").status_code == 404

    spawned = client.post(f"/api/captures/video/{rec_id}/stack",
                          json={"keep_pct": 50.0})
    assert spawned.status_code == 202
    assert spawned.json() == {"started": "video_stack", "id": rec_id}

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        task = hub._busy.get("video_stack")
        if task is None or task.done():
            break
        time.sleep(0.05)

    png = client.get(f"/api/captures/video/{rec_id}/stack.png")
    assert png.status_code == 200, png.text
    assert png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get("/api/captures/video").json()["recordings"][0]["has_stack"] is True
