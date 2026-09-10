"""HTTP surface for SER video capture (D-RIG-1) and its lucky-imaging stack.

A router rather than more lines in api/app.py, following catalog/visibility.py:
the module owns its own refusals and app.py owns one ``include_router`` line.

REFUSAL ORDER MIRRORS ``POST /api/capture``, DELIBERATELY AND IN THE SAME ORDER:
polar, then a running sequence, then the live loop, then "no camera", then the
one refusal that is ours alone (no video path on this backend). The order is not
cosmetic - it is the order of decreasing cost to the operator, so the answer
they get names the thing they have to stop first. The ``_err``-shaped 409 for a
missing camera is reimplemented here rather than imported because ``_err`` is
private to app.py; the SHAPE (a bare string detail) is what clients parse, so it
is reproduced exactly.

RELAY FENCE - A DECISION, NOT AN OVERSIGHT. These routes do NOT join
``_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES``. That fence exists for configuration:
changes that outlive the session and that a remote operator could make
irreversibly wrong (site, profiles, auth, update). A recording is a science
operation with the same shape as ``POST /api/capture`` - bounded, cancellable,
and pointless to make from anywhere except where the sky is. Fencing it off
would make the relay useless for exactly the person it is for: someone watching
Jupiter from inside the house.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ..auth import (CAP_CONTROL_CAPTURE, CAP_VIEW_MEDIA, CAP_VIEW_PREVIEW,
                    CAP_VIEW_STATUS, require)
from ..auth.rbac import declare
from ..devices.base import DeviceError
from ..devices.cameras.engine import NativeCamera
from ..hub import hub
from ..persist import safe_subpath
from . import video as video_mod
from .video import (InsufficientVideoSpace, RecordingBusy, VideoRecorder,
                    camera_capabilities)

router = APIRouter()

#: One recorder for the process, on the hub singleton - the camera is one
#: device and a recording owns it.
recorder = VideoRecorder(hub)


class RoiBody(BaseModel):
    x: int = 0
    y: int = 0
    w: int | None = None
    h: int | None = None
    bin: int = Field(default=1, ge=1, le=8)


class VideoBody(BaseModel):
    roi: RoiBody | None = None
    fps: float = Field(default=30.0, gt=0.0, le=10_000.0)
    exposure_ms: float = Field(default=8.0, gt=0.0, le=60_000.0)
    gain: int = 0
    offset: int = 0
    duration_s: float = Field(default=10.0, gt=0.0)
    format: str = "ser"


class StackBody(BaseModel):
    keep_pct: float = Field(default=25.0, gt=0.0, le=100.0)


def _conflict(detail: str, code: str) -> HTTPException:
    """The nested 409 shape ``lib/apiError.ts`` parses, so a client can branch on
    ``code`` instead of matching a naked conflict."""
    return HTTPException(409, detail={"detail": detail, "code": code})


def _recording_path(rec_id: str) -> Path:
    """The .ser for ``rec_id``, or a 404 - containment first.

    ``safe_subpath`` refuses separators, ``..``, NUL, ``:`` and reserved device
    names before the filesystem is touched, so a traversal id 404s rather than
    reading somebody's private key. The suffix and ``is_file`` checks then make
    "exists" mean "is a recording", mirroring ``GET /api/gallery/file``."""
    try:
        target = safe_subpath(video_mod.video_dir(), f"{rec_id}.ser")
    except KeyError:
        raise HTTPException(404, "recording not found")
    if target.suffix.lower() != ".ser" or not target.is_file():
        raise HTTPException(404, "recording not found")
    return target


# ------------------------------------------------------------------ recording

@router.post("/api/capture/video",
             dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
@declare(CAP_CONTROL_CAPTURE)
async def start_video(body: VideoBody):
    if (body.format or "ser").lower() != "ser":
        raise HTTPException(400, f"unsupported video format '{body.format}'; "
                                 "this rig writes SER")
    if hub.polar.running:
        raise HTTPException(409, "polar alignment in progress")
    engine = hub.engine
    if engine is not None and getattr(engine, "owns_camera", False):
        # `owns_camera` and not `running`: a PAUSED run still has a live task
        # but no exposure in flight. Same reasoning as POST /api/capture.
        raise HTTPException(409, "a sequence is running")
    if hub.looping:
        raise HTTPException(409, "the live loop owns the camera")
    try:
        cam = hub.require("camera")
    except DeviceError as e:
        # ``_err``'s shape (a bare string detail), reproduced - see the module
        # docstring for why it is not imported.
        raise HTTPException(409, str(e))

    caps = camera_capabilities(cam)
    if not getattr(caps, "burst_supported", False) and not isinstance(cam, NativeCamera):
        brand = getattr(cam, "name", None) or "this camera"
        backend = getattr(cam, "backend", None) or "an external driver"
        raise _conflict(
            f"{brand} cannot record video through AstroDeck yet: this camera is "
            f"driven through {backend}, which has no subframe or burst path. "
            "Video capture needs a natively-driven camera.",
            "no_video_path")
    # OUR OWN LANE FIRST, then the generic camera-busy. Order matters here for
    # once: a running recording HOLDS the exposure guard, so checking the guard
    # first would answer "camera_busy" for the one case the caller can already
    # name - and a client that wants to offer "stop the recording?" cannot tell
    # its own recording from an autofocus sweep out of that code.
    if recorder.active and recorder.current is not None:
        raise _conflict(f"a recording is already running ({recorder.current.id})",
                        "lane_busy")
    if hub._capture_lock.locked():
        raise _conflict(
            f"camera is busy ({hub._capture_busy or 'exposing'}); "
            "video recording refused", "camera_busy")

    roi = body.roi.model_dump() if body.roi is not None else None
    try:
        rec = recorder.prepare(cam=cam, roi=roi, fps=body.fps,
                               exposure_ms=body.exposure_ms, gain=body.gain,
                               offset=body.offset, duration_s=body.duration_s)
    except RecordingBusy as e:
        raise _conflict(str(e), "lane_busy")
    except InsufficientVideoSpace as e:
        raise HTTPException(507, detail={"detail": "insufficient disk space",
                                         "free_bytes": e.free,
                                         "required_bytes": e.required})
    except DeviceError as e:
        raise HTTPException(409, str(e))
    return JSONResponse(recorder.start(rec), status_code=202)


@router.get("/api/capture/video",
            dependencies=[Depends(require(CAP_VIEW_STATUS))])
@declare(CAP_VIEW_STATUS)
async def video_status():
    return recorder.status()


@router.post("/api/capture/video/stop",
             dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
@declare(CAP_CONTROL_CAPTURE)
async def stop_video():
    return await recorder.stop()


# ------------------------------------------------------------------- library

@router.get("/api/captures/video",
            dependencies=[Depends(require(CAP_VIEW_STATUS))])
@declare(CAP_VIEW_STATUS)
async def list_video():
    return {"recordings": video_mod.list_recordings()}


@router.get("/api/captures/video/{rec_id}.ser",
            dependencies=[Depends(require(CAP_VIEW_MEDIA))])
@declare(CAP_VIEW_MEDIA)
async def download_video(rec_id: str):
    """The raw recording. ``view.media``, not ``view.preview``: these are the
    original bytes off the sensor, the same class of thing a FITS download is."""
    target = _recording_path(rec_id)
    return FileResponse(target, media_type="application/octet-stream",
                        filename=f"{rec_id}.ser")


@router.delete("/api/captures/video/{rec_id}",
               dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
@declare(CAP_CONTROL_CAPTURE)
async def delete_video(rec_id: str):
    if recorder.active and recorder.current is not None and recorder.current.id == rec_id:
        raise _conflict("that recording is still being written", "lane_busy")
    target = _recording_path(rec_id)
    video_mod.delete_recording(target)
    return {"deleted": rec_id}


# --------------------------------------------------------------- quick stack

@router.post("/api/captures/video/{rec_id}/stack",
             dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
@declare(CAP_CONTROL_CAPTURE)
async def stack_video(rec_id: str, body: StackBody | None = None):
    target = _recording_path(rec_id)
    existing = hub._busy.get("video_stack")
    if existing is not None and not existing.done():
        raise _conflict("a stack is already running", "lane_busy")
    keep = body.keep_pct if body is not None else 25.0
    video_mod.spawn_stack(hub, target, keep_pct=keep)
    return JSONResponse({"started": "video_stack", "id": rec_id},
                        status_code=202)


@router.get("/api/captures/video/{rec_id}/stack.png",
            dependencies=[Depends(require(CAP_VIEW_PREVIEW))])
@declare(CAP_VIEW_PREVIEW)
async def video_stack_png(rec_id: str):
    """The rendered stack. ``view.preview``, not ``view.media``: a stretched PNG
    is a picture, not the raw bytes."""
    _recording_path(rec_id)                 # containment + "is a recording"
    png = video_mod.stack_path(rec_id)
    if not png.is_file():
        raise HTTPException(404, "no stack for that recording")
    return FileResponse(png, media_type="image/png",
                        filename=f"{rec_id}.stack.png")
