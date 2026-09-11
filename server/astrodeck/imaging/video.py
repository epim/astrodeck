"""VideoRecorder: one bounded, cancellable SER recording at a time.

HOW A FAST-CADENCE ROI LOOP IS ACHIEVED HERE - THE HONEST ANSWER.

There is no video path in this tree yet. The camera waist
(devices/cameras/adapter.py) exposes snap primitives only - start_exposure /
image_ready / read_frame / abort - and no binding declares a streaming entry
point: zwo_asi_sdk._SIGNATURES binds the four exposure calls and nothing else,
and _ASI_CAMERA_INFO.SupportedVideoFormat is read by no code. So stage 1 ships
ONE path for real, the generic fallback, and says so out loud in every recording
it produces.

The fallback drives NativeCamera.expose in a tight loop at a fixed ROI. That is
the only expose() in the tree that takes an ROI at all (engine.py:92); the base
Camera.expose (devices/base.py) has no roi parameter, so the Alpaca, NINA and
sim backends cannot subframe and are refused up front by the route rather than
recorded at full sensor size at 2 fps. Its ceiling is structural: each frame is
a complete start/poll/read cycle, and the poll sleeps
``min(0.5, max(0.05, seconds / 20))`` - a 50 ms floor for any sub-second
exposure (engine.py:109) - plus a get_temperature() and an applied_roi() per
frame. Measured on the fake-adapter harness in tests/test_video_capture.py, that
lands near 16-19 fps whatever the exposure, which is fine for the Moon and
useless for Jupiter at f/20.

Stage 1b - the part that makes this fast - is implementing
``burst_begin``/``burst_next``/``burst_end`` for ZWO (ASIStartVideoCapture /
ASIGetVideoData) and Player One (POAStartExposure(True) / POAGetImageData).
Those calls are not bound yet and binding them is a vendor-verification exercise
against the real DLLs (devices/vendor_verify.py), so it is deliberately out of
this wave. This recorder already prefers the burst hooks whenever
``caps.burst_supported`` is True, so stage 1b is an adapter change and nothing
here moves.

What the recorder will NOT do is deliver a slower file than it was asked for
without saying so. Every recording publishes ``requested_fps`` beside
``actual_fps`` with ``clamped``/``clamp_reason``, and the sidecar keeps the rate
actually achieved. A recording that silently ran at a fifth of the requested
cadence is a night of unusable data discovered at the stacking stage.

CAMERA OWNERSHIP. A recording is ONE camera-owning operation, so
``hub.exposure_guard`` is held for its whole duration rather than per frame -
the opposite of the sequence engine's usage, and on purpose: between two frames
of a 200 fps recording there is no room for another path's exposure, and letting
one in would tear a hole in the middle of the file.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..devices.base import DeviceError
from ..events import bus
from .ser import (SER_HEADER_BYTES, SerWriter, color_id_for, read_frames,
                  repair_frame_count,
                  read_header)

#: The engine's exposure-poll floor (devices/cameras/engine.py:109). Not
#: imported: it is a literal inside expose(), and duplicating it here with a
#: citation is honest about the coupling in a way a wrong import would not be.
ENGINE_POLL_FLOOR_S = 0.05
#: Per-frame work the fallback path pays outside the exposure itself: the
#: applied_roi() read-back, the get_temperature() and the reshape. Measured on
#: the fake-adapter harness; deliberately conservative, since over-promising the
#: rate is the failure this whole clamp exists to prevent.
FALLBACK_FRAME_OVERHEAD_S = 0.012
#: Free space kept back after a recording, matching the survey pack's headroom
#: (catalog/survey_pack._HEADROOM_BYTES). A card with nothing left is a night
#: log that cannot be written and a FITS that cannot be saved.
DISK_HEADROOM_BYTES = 50 * 1024 * 1024
#: How often progress reaches the bus while recording.
PROGRESS_INTERVAL_S = 0.5
#: Hard ceiling on a single recording, so a fat-fingered duration cannot own the
#: camera all night. 30 minutes is longer than any planetary run has a reason to
#: be (the seeing decorrelates in seconds and the planet rotates in minutes).
MAX_DURATION_S = 1800.0

#: Terminal states, so a caller can tell "still going" from "over".
TERMINAL_STATES = ("done", "cancelled", "failed")


class InsufficientVideoSpace(RuntimeError):
    """Disk pre-flight failed; carries the numbers for the API's 507 body."""

    def __init__(self, free: int, required: int) -> None:
        super().__init__(f"insufficient disk space: {free} free, {required} required")
        self.free = free
        self.required = required


class RecordingBusy(RuntimeError):
    """A recording is already in flight (single-flight, one camera)."""


def video_dir():
    """``CAPTURE_DIR/video``, resolved at CALL time.

    Never cached and never imported as a constant: the whole test suite
    redirects captures by monkeypatching ``hub.CAPTURE_DIR``, and a module-level
    copy would write a real recording into the developer's live capture tree.
    """
    from .. import hub as hub_module
    return Path(hub_module.CAPTURE_DIR) / "video"


def camera_capabilities(cam):
    """The connected camera's ``CameraCapabilities``, or None for a backend that
    has none (Alpaca/NINA/sim all report through Camera attributes instead)."""
    return getattr(cam, "_caps", None)


def new_recording_id(now: float | None = None, *, existing=()) -> str:
    """``2026-09-10T2312-ser01`` - minute-resolution, then a counter.

    No colons anywhere, and that is load-bearing rather than cosmetic: the id
    becomes a filename and comes back through ``persist.safe_subpath``, which
    refuses ``:`` outright (NTFS alternate data streams). An ISO timestamp with
    its colons would 404 its own download route.
    """
    stamp = time.strftime("%Y-%m-%dT%H%M", time.localtime(now or time.time()))
    taken = set(existing)
    for n in range(1, 100):
        candidate = f"{stamp}-ser{n:02d}"
        if candidate not in taken:
            return candidate
    return f"{stamp}-ser{int(time.time()) % 100000:05d}"


def align_roi(x: int, y: int, w: int, h: int, binning: int, caps,
              sensor_w: int, sensor_h: int):
    """Round a requested subframe onto what the sensor will actually apply.

    Two constraints, both in unbinned pixels: the brand's own alignment
    (``caps.roi_align`` - ZWO wants width % 8 == 0, height % 2 == 0) and the
    binning factor, since the download is laid out at ``w // bin`` and a width
    that is not a whole number of bins is a row length nobody agrees on. The
    least common multiple satisfies both.

    Rounding here rather than letting the sensor do it is the point:
    ``applied_roi`` exists because a camera that quietly rounds a width hands
    back rows of a different length than the caller laid them out at, which is
    the sheared, tiled picture in tests/test_camera_roi_shear.py. If we round
    first, there is nothing left for the sensor to disagree with.
    """
    from ..devices.cameras.adapter import ROI
    b = max(1, int(binning))
    aw, ah = getattr(caps, "roi_align", (1, 1)) or (1, 1)
    step_w = math.lcm(max(1, int(aw)), b)
    step_h = math.lcm(max(1, int(ah)), b)
    x = max(0, min(int(x), max(0, sensor_w - step_w)))
    y = max(0, min(int(y), max(0, sensor_h - step_h)))
    w = max(step_w, min(int(w), sensor_w - x))
    h = max(step_h, min(int(h), sensor_h - y))
    w -= w % step_w
    h -= h % step_h
    return ROI(x=x, y=y, w=max(step_w, w), h=max(step_h, h), bin=b)


def plan_rate(*, requested_fps: float, exposure_s: float, use_burst: bool,
              caps) -> tuple[float, bool, str]:
    """(actual_fps, clamped, clamp_reason) for this camera and exposure."""
    ceiling = float(requested_fps)
    reason = ""
    if not use_burst:
        period = exposure_s + ENGINE_POLL_FLOOR_S + FALLBACK_FRAME_OVERHEAD_S
        ceiling = 1.0 / period if period > 0 else requested_fps
        reason = (
            "this camera has no burst path, so every frame is a full "
            "start/poll/read cycle and the exposure engine polls no faster than "
            f"{int(ENGINE_POLL_FLOOR_S * 1000)} ms - about {ceiling:.1f} fps at "
            f"{exposure_s * 1000:.0f} ms")
    declared = getattr(caps, "max_fps", None)
    if declared and float(declared) < ceiling:
        ceiling = float(declared)
        reason = f"the camera declares a ceiling of {float(declared):g} fps"
    if ceiling >= requested_fps:
        return float(requested_fps), False, ""
    return max(0.1, float(ceiling)), True, reason


def free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists():           # disk_usage needs an existing path
        probe = probe.parent
    return shutil.disk_usage(probe).free


@dataclass
class Recording:
    """One recording's whole public story, in one place."""
    id: str
    path: Path
    roi: dict
    requested_fps: float
    actual_fps: float
    clamped: bool
    clamp_reason: str
    exposure_s: float
    gain: int
    offset: int
    duration_s: float
    target_frames: int
    frame_bytes: int
    est_bytes: int
    camera: str
    use_burst: bool
    state: str = "arming"
    frames: int = 0
    dropped: int = 0
    bytes: int = SER_HEADER_BYTES
    started_ts: float = 0.0
    finished_ts: float | None = None
    error: str | None = None
    task: object = field(default=None, repr=False)

    @property
    def elapsed_s(self) -> float:
        end = self.finished_ts if self.finished_ts is not None else time.time()
        return max(0.0, end - self.started_ts) if self.started_ts else 0.0

    @property
    def fps(self) -> float:
        """The rate ACHIEVED so far - measured, never the plan."""
        el = self.elapsed_s
        return (self.frames / el) if el > 0 and self.frames else 0.0

    def event(self) -> dict:
        return {
            "id": self.id, "state": self.state, "frames": self.frames,
            "target_frames": self.target_frames,
            "elapsed_s": round(self.elapsed_s, 3), "fps": round(self.fps, 2),
            "dropped": self.dropped, "bytes": self.bytes,
            "path": (f"video/{self.path.name}"
                     if self.state == "done" else None),
        }

    def status(self) -> dict:
        return {
            "active": self.state not in TERMINAL_STATES,
            "id": self.id, "state": self.state, "frames": self.frames,
            "target_frames": self.target_frames,
            "elapsed_s": round(self.elapsed_s, 3), "fps": round(self.fps, 2),
            "dropped": self.dropped, "bytes": self.bytes, "roi": dict(self.roi),
            "requested_fps": self.requested_fps, "actual_fps": self.actual_fps,
            "clamped": self.clamped, "clamp_reason": self.clamp_reason,
            "camera": self.camera, "started_ts": self.started_ts or None,
            "finished_ts": self.finished_ts, "error": self.error,
        }


IDLE_STATUS = {
    "active": False, "id": None, "state": "idle", "frames": 0,
    "target_frames": 0, "elapsed_s": 0.0, "fps": 0.0, "dropped": 0,
    "bytes": 0, "roi": None, "started_ts": None, "finished_ts": None,
    "error": None,
}


class VideoRecorder:
    """One at a time, owning its own asyncio.Task and the ``video`` lane."""

    def __init__(self, hub) -> None:
        self._hub = hub
        self._current: Recording | None = None

    # -------------------------------------------------------------- queries
    @property
    def current(self) -> Recording | None:
        return self._current

    @property
    def active(self) -> bool:
        rec = self._current
        return bool(rec and rec.state not in TERMINAL_STATES)

    def status(self) -> dict:
        rec = self._current
        return rec.status() if rec is not None else dict(IDLE_STATUS)

    # ---------------------------------------------------------------- start
    def prepare(self, *, cam, roi: dict | None, fps: float, exposure_ms: float,
                gain: int, offset: int, duration_s: float) -> Recording:
        """Everything that can be refused, done BEFORE a task exists.

        Raises RecordingBusy / InsufficientVideoSpace / DeviceError. Nothing here
        touches the camera: a refusal costs the rig nothing.
        """
        if self.active:
            raise RecordingBusy(f"a recording is already running ({self._current.id})")
        caps = camera_capabilities(cam)
        sensor_w = int(getattr(caps, "sensor_width", 0)
                       or getattr(cam, "sensor_width", 0) or 0)
        sensor_h = int(getattr(caps, "sensor_height", 0)
                       or getattr(cam, "sensor_height", 0) or 0)
        if sensor_w <= 0 or sensor_h <= 0:
            raise DeviceError(
                "the camera has not reported a sensor size, so a subframe "
                "cannot be placed on it")
        r = roi or {}
        binning = int(r.get("bin", 1) or 1)
        aligned = align_roi(int(r.get("x", 0) or 0), int(r.get("y", 0) or 0),
                            int(r.get("w", sensor_w) or sensor_w),
                            int(r.get("h", sensor_h) or sensor_h),
                            binning, caps, sensor_w, sensor_h)

        exposure_s = max(1e-6, float(exposure_ms) / 1000.0)
        duration_s = max(0.1, min(float(duration_s), MAX_DURATION_S))
        use_burst = bool(getattr(caps, "burst_supported", False))
        actual_fps, clamped, reason = plan_rate(
            requested_fps=max(0.1, float(fps)), exposure_s=exposure_s,
            use_burst=use_burst, caps=caps)
        target_frames = max(1, int(duration_s * actual_fps))

        # The engine downloads RAW16 on every adapter in this tree (the reason is
        # spelled out at engine._shape), so the container is 16-bit even for an
        # 8-bit sensor. SerWriter can write 8-bit; the recorder never asks it to,
        # because narrowing here would be a guess about data we did not measure.
        bw, bh = aligned.w // aligned.bin, aligned.h // aligned.bin
        frame_bytes = bw * bh * 2
        est_bytes = SER_HEADER_BYTES + target_frames * (frame_bytes + 8)

        directory = video_dir()
        directory.mkdir(parents=True, exist_ok=True)
        free = free_bytes(directory)
        required = est_bytes + DISK_HEADROOM_BYTES
        if free < required:
            raise InsufficientVideoSpace(free, required)

        rec_id = new_recording_id(existing={p.stem for p in directory.glob("*.ser")})
        return Recording(
            id=rec_id, path=directory / f"{rec_id}.ser",
            roi={"x": aligned.x, "y": aligned.y, "w": aligned.w,
                 "h": aligned.h, "bin": aligned.bin},
            requested_fps=round(float(fps), 3), actual_fps=round(actual_fps, 3),
            clamped=clamped, clamp_reason=reason, exposure_s=exposure_s,
            gain=int(gain), offset=int(offset), duration_s=duration_s,
            target_frames=target_frames, frame_bytes=frame_bytes,
            est_bytes=est_bytes, camera=getattr(cam, "name", "camera"),
            use_burst=use_burst)

    def start(self, rec: Recording) -> dict:
        """Spawn the recording and publish the ``video`` lane."""
        self._current = rec
        task = asyncio.create_task(self._run(rec))
        rec.task = task
        # THE LANE. Registered here rather than inside the task so it is live
        # before this function returns: a client that POSTs and immediately GETs
        # the busy lanes must not see a gap the size of one event-loop tick.
        self._hub._busy["video"] = task
        return {
            "started": "video", "id": rec.id, "target_frames": rec.target_frames,
            "actual_fps": rec.actual_fps, "clamped": rec.clamped,
            "clamp_reason": rec.clamp_reason, "est_bytes": rec.est_bytes,
            "roi": dict(rec.roi),
        }

    # ----------------------------------------------------------------- stop
    async def stop(self) -> dict:
        rec = self._current
        if rec is None or rec.state in TERMINAL_STATES:
            return {"cancelled": False, "id": rec.id if rec else None,
                    "frames": rec.frames if rec else 0,
                    "bytes": rec.bytes if rec else 0}
        task = rec.task
        if task is not None:
            task.cancel()
            # Wait for the unwind, so the numbers we answer with are the ones in
            # the finished file rather than the ones in flight when we asked.
            with contextlib.suppress(Exception):
                await asyncio.wait({task}, timeout=10.0)
        return {"cancelled": True, "id": rec.id, "frames": rec.frames,
                "bytes": rec.bytes}

    # ------------------------------------------------------------ the drive
    def _publish(self, rec: Recording) -> None:
        bus.publish("video", **rec.event())

    def _set_state(self, rec: Recording, state: str) -> None:
        rec.state = state
        self._publish(rec)

    async def _run(self, rec: Recording) -> None:
        hub = self._hub
        cam = hub.devices.get("camera")
        rec.started_ts = time.time()
        self._set_state(rec, "arming")
        writer = None
        try:
            async with hub.exposure_guard(f"recording video ({rec.id})"):
                telescope = ""
                with contextlib.suppress(Exception):
                    telescope = hub.effective_optics().get("telescope_name") or ""
                caps = camera_capabilities(cam)
                writer = SerWriter(
                    rec.path,
                    width=rec.roi["w"] // rec.roi["bin"],
                    height=rec.roi["h"] // rec.roi["bin"],
                    bit_depth=16,
                    color_id=color_id_for(getattr(caps, "bayer_pattern", None)
                                          or getattr(cam, "bayer_pattern", None),
                                          rec.roi["bin"]),
                    instrument=getattr(cam, "name", "camera"),
                    telescope=telescope, start_unix=rec.started_ts)
                with writer:
                    self._set_state(rec, "recording")
                    if rec.use_burst:
                        await self._burst_loop(rec, writer, cam)
                    else:
                        await self._snap_loop(rec, writer, cam)
                self._set_state(rec, "finalising")
                rec.finished_ts = time.time()
                self._write_sidecar(rec, "done")
                self._set_state(rec, "done")
                bus.log("info",
                        f"video {rec.id}: {rec.frames} frames at "
                        f"{rec.fps:.1f} fps ({rec.bytes / 1e6:.1f} MB)", "capture")
        except asyncio.CancelledError:
            # The writer's context manager has already patched FrameCount and
            # written the trailer, so what is on disk is a playable file of the
            # frames that arrived. Only the bookkeeping is left, and it is all
            # synchronous - awaiting anything here would be cancelled again.
            rec.finished_ts = time.time()
            self._write_sidecar(rec, "cancelled")
            self._set_state(rec, "cancelled")
            bus.log("warning",
                    f"video {rec.id} stopped at {rec.frames} frames; the file is "
                    "valid at that count", "capture")
            raise
        except Exception as exc:                       # noqa: BLE001
            rec.finished_ts = time.time()
            rec.error = str(exc)
            self._write_sidecar(rec, "failed")
            self._set_state(rec, "failed")
            bus.log("error", f"video {rec.id} failed: {exc}", "capture")

    def _account(self, rec: Recording, writer: SerWriter) -> None:
        rec.frames = writer.frames
        # The size the file WILL have once closed (header + frames + trailer),
        # which is the size it does have the instant the writer closes.
        rec.bytes = writer.size_bytes

    async def _snap_loop(self, rec: Recording, writer: SerWriter, cam) -> None:
        """The generic fallback: NativeCamera.expose at a fixed ROI, in a loop.

        Runs as the recording task rather than on a thread of its own because
        ``expose`` is a coroutine - the blocking parts of it (start_exposure,
        image_ready, read_frame) are already handed to the threadpool inside the
        engine, and driving a coroutine from a foreign thread would only add a
        hop back to this loop. What that buys us is cancellation: a cancel lands
        on the await and the engine aborts the exposure in-camera.
        """
        from ..devices.cameras.adapter import ROI
        roi = ROI(x=rec.roi["x"], y=rec.roi["y"], w=rec.roi["w"],
                  h=rec.roi["h"], bin=rec.roi["bin"])
        period = 1.0 / rec.actual_fps if rec.actual_fps > 0 else 0.0
        last_pub = 0.0
        while rec.frames < rec.target_frames:
            if time.time() - rec.started_ts >= rec.duration_s:
                break
            frame = await cam.expose(rec.exposure_s, rec.gain, rec.offset,
                                     binning=rec.roi["bin"], light=True,
                                     roi=roi)
            writer.add_frame(frame.data, ts=getattr(frame, "timestamp", None))
            self._account(rec, writer)
            now = time.time()
            if now - last_pub >= PROGRESS_INTERVAL_S:
                last_pub = now
                self._publish(rec)
            # Pace only when we are RUNNING AHEAD of the planned rate. The snap
            # path is normally the bottleneck, in which case this never sleeps
            # and the shortfall shows up honestly in the measured fps.
            due = rec.started_ts + rec.frames * period
            if period and now < due:
                await asyncio.sleep(min(due - now, 0.5))

    async def _burst_loop(self, rec: Recording, writer: SerWriter, cam) -> None:
        """Stage 1b's path: the adapter's own stream, one frame per call.

        Unimplemented by every bundled adapter today (see adapter.burst_begin),
        so this runs only for a camera that DECLARES burst_supported. Kept
        symmetrical with the snap loop so the recorder's behaviour - lane,
        progress, cancel, valid short file - does not change when a brand
        gains it."""
        adapter = getattr(cam, "_a", None)
        if adapter is None:
            raise DeviceError("this camera declares a burst path but exposes no "
                              "adapter to drive it")
        from ..devices.cameras.adapter import ROI
        roi = ROI(x=rec.roi["x"], y=rec.roi["y"], w=rec.roi["w"],
                  h=rec.roi["h"], bin=rec.roi["bin"])
        timeout_s = max(0.5, 5.0 / max(rec.actual_fps, 0.1))
        last_pub = 0.0
        await asyncio.to_thread(
            adapter.burst_begin, roi=roi, gain=rec.gain, offset=rec.offset,
            exposure_s=rec.exposure_s)
        try:
            while rec.frames < rec.target_frames:
                if time.time() - rec.started_ts >= rec.duration_s:
                    break
                raw = await asyncio.to_thread(adapter.burst_next, timeout_s)
                if raw is None:
                    # A missed frame under USB contention is normal on a stream
                    # and is counted, not fatal. Only an exception ends a run.
                    rec.dropped += 1
                    continue
                writer.add_frame(raw)
                self._account(rec, writer)
                now = time.time()
                if now - last_pub >= PROGRESS_INTERVAL_S:
                    last_pub = now
                    self._publish(rec)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.shield(asyncio.to_thread(adapter.burst_end))

    # ------------------------------------------------------------- sidecars
    def _write_sidecar(self, rec: Recording, state: str) -> None:
        """The .json beside the .ser: what was asked for, what was delivered.

        Written on every terminal path, cancel included - a cancelled recording
        is still a file somebody will stack, and "which ROI is this?" is not
        answerable from a SER header that carries no gain, no exposure and no
        clamp story."""
        payload = {
            "id": rec.id, "state": state, "camera": rec.camera,
            "ts": rec.started_ts, "finished_ts": rec.finished_ts,
            "roi": dict(rec.roi),
            "request": {
                "fps": rec.requested_fps, "exposure_ms": round(rec.exposure_s * 1000, 3),
                "gain": rec.gain, "offset": rec.offset,
                "duration_s": rec.duration_s, "format": "ser",
            },
            "actual_fps": rec.actual_fps, "clamped": rec.clamped,
            "clamp_reason": rec.clamp_reason,
            "achieved_fps": round(rec.fps, 3),
            "frames": rec.frames, "dropped": rec.dropped, "bytes": rec.bytes,
            "path_source": "burst" if rec.use_burst else "snap-fallback",
            "error": rec.error,
        }
        with contextlib.suppress(Exception):
            rec.path.with_suffix(".json").write_text(
                json.dumps(payload, indent=2), encoding="utf-8")


# ------------------------------------------------------------------- library

def stack_path(rec_id: str) -> Path:
    """Where a lucky-imaging stack of this recording lands."""
    return video_dir() / f"{rec_id}.stack.png"


def list_recordings() -> list[dict]:
    """Every finished recording on disk, newest first.

    Reads the sidecar when there is one and falls back to the SER header when
    there is not, so a recording whose sidecar was lost is still listed and
    still downloadable rather than disappearing from the UI."""
    directory = video_dir()
    if not directory.is_dir():
        return []
    out: list[dict] = []
    for ser in sorted(directory.glob("*.ser")):
        rec_id = ser.stem
        meta: dict = {}
        side = ser.with_suffix(".json")
        if side.is_file():
            with contextlib.suppress(Exception):
                meta = json.loads(side.read_text(encoding="utf-8"))
        head: dict = {}
        with contextlib.suppress(Exception):
            head = _repaired_header(ser)
        try:
            size = ser.stat().st_size
            mtime = ser.stat().st_mtime
        except OSError:
            continue
        out.append({
            "id": rec_id,
            "ts": meta.get("ts") or mtime,
            "bytes": size,
            "frames": meta.get("frames", head.get("frames", 0)),
            "fps": meta.get("achieved_fps", meta.get("actual_fps", 0.0)),
            "roi": meta.get("roi") or ({"x": 0, "y": 0, "w": head.get("width", 0),
                                        "h": head.get("height", 0), "bin": 1}
                                       if head else None),
            "camera": meta.get("camera") or head.get("instrument", ""),
            "has_stack": stack_path(rec_id).is_file(),
        })
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out


def _repaired_header(ser: Path) -> dict:
    """``read_header`` with FrameCount repaired from the file size first.

    THE HEADER IS WRITTEN WITH 0 AND PATCHED AT CLOSE, so a recording whose
    process never got to close - a power cut, a kill, the rig PC's thermal
    reset - is a file full of frames that every reader calls empty. The frames
    are on disk and only the four bytes that count them are wrong, so they are
    rewritten from the length before anything reads the count.

    TOTAL: a file that is not a SER, or one on a read-only archive, falls
    through to the plain header rather than costing the caller its listing.
    """
    with contextlib.suppress(Exception):
        repair_frame_count(ser)
    return read_header(ser)


def run_stack(ser: Path, *, keep_pct: float) -> dict:
    """Read a .ser, lucky-stack it, write ``<id>.stack.png``. BLOCKING.

    Called on a worker thread (see ``spawn_stack``): a 1800-frame file is read
    twice - once to score every frame, once to stack the keepers - and neither
    pass belongs on the event loop.

    TWO STREAMS, NOT A LIST. This used to open with ``list(read_frames(ser))``
    while the sentence above claimed otherwise, and the gap between the two was
    the whole recording: 1800 full-ROI frames is 7.6 GB of pixels and a long
    4k burst runs to a hundred, so the observatory's 6 GB box could not stack
    the file it had just written and said so with a MemoryError out of a worker
    thread. ``read_frames`` was already a generator holding one frame at a
    time; both passes now consume it as one, and what survives pass 1 is two
    numbers per frame (``lucky.FrameScan``) rather than the frame.
    """
    from .lucky import lucky_stack, scan_frames, select_frames
    from .processing import to_png
    # FrameCount first: ``read_frames`` yields ``head["frames"]`` frames, so a
    # recording whose process was killed stacks as empty until the count is
    # recovered from the file's own size. Suppressed rather than required: a
    # file we cannot rewrite is still a file we can try to stack.
    with contextlib.suppress(Exception):
        repair_frame_count(ser)
    try:
        scan = scan_frames(read_frames(ser))            # pass 1: measure
    except ValueError as exc:
        if "at least one frame" in str(exc):
            raise ValueError(f"{ser.name} has no frames to stack") from exc
        raise
    sel = select_frames(scan, keep_pct=keep_pct)
    result = lucky_stack(read_frames(ser), scan=scan, sel=sel)  # pass 2: stack
    out = stack_path(ser.stem)
    out.write_bytes(to_png(result.image.astype("float64")))
    return {"id": ser.stem, "frames": len(scan.scores), "kept": len(result.kept),
            "keep_pct": result.keep_pct, "aligned": result.aligned,
            "path": f"video/{out.name}"}


def spawn_stack(hub, ser: Path, *, keep_pct: float):
    """Run ``run_stack`` off the event loop in the ``video_stack`` lane.

    A separate lane from ``video`` on purpose: stacking touches no device, so it
    must not read as "the camera is busy" - and it is legitimate to stack last
    night's recording while tonight's is running.
    """
    async def _work():
        try:
            result = await asyncio.to_thread(run_stack, ser, keep_pct=keep_pct)
            bus.publish("video_stack", state="done", **result)
            bus.log("info",
                    f"video stack {result['id']}: kept {result['kept']} of "
                    f"{result['frames']} frames", "capture")
        except asyncio.CancelledError:
            bus.publish("video_stack", state="cancelled", id=ser.stem)
            raise
        except Exception as exc:                       # noqa: BLE001
            bus.publish("video_stack", state="failed", id=ser.stem,
                        error=str(exc))
            bus.log("error", f"video stack {ser.stem} failed: {exc}", "capture")

    task = asyncio.create_task(_work())
    hub._busy["video_stack"] = task
    bus.publish("video_stack", state="running", id=ser.stem, keep_pct=keep_pct)
    return task


def delete_recording(ser: Path) -> bool:
    """Remove a recording and everything derived from it. True if it existed."""
    if not ser.is_file():
        return False
    ser.unlink()
    for extra in (ser.with_suffix(".json"), stack_path(ser.stem)):
        with contextlib.suppress(OSError):
            if extra.is_file():
                extra.unlink()
    return True
