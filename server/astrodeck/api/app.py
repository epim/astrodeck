"""FastAPI application: REST command surface + WebSocket event stream.

Quick queries answer inline. Long operations (slews, autofocus, sequences,
centering) start a named background task and stream progress over the
WebSocket — the UI is event-driven.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..catalog import search_catalog
from ..devices import alpaca as alpaca_backend
from ..devices.base import DeviceError
from ..devices.nina import discover_nina
from ..events import bus
from ..focus import run_autofocus
from ..hub import hub
from ..sequence import SequenceEngine, SequencePlan

engine = SequenceEngine(hub)

UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"


def _spawn(name: str, coro) -> dict:
    """Run a long operation as a named background task (one per name)."""
    existing = hub._busy.get(name)
    if existing and not existing.done():
        raise HTTPException(409, f"'{name}' is already running")

    async def wrapped():
        try:
            await coro
        except asyncio.CancelledError:
            bus.log("warning", f"{name} cancelled", name)
        except (DeviceError, Exception) as e:
            bus.log("error", f"{name} failed: {e}", name)

    hub._busy[name] = asyncio.create_task(wrapped())
    return {"started": name}


def _err(e: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(e))


# ------------------------------------------------------------ request models

class AlpacaConnectBody(BaseModel):
    role: str
    host: str
    port: int
    dev_type: str
    dev_num: int
    name: str = ""


class CaptureBody(BaseModel):
    exposure_s: float = 1.0
    gain: int = 100
    offset: int = 30
    binning: int = 1
    save: bool = False
    target: str = ""
    frame_type: str = "Light"


class GotoBody(BaseModel):
    ra_hours: float
    dec_deg: float
    center: bool = True


class MoveAxisBody(BaseModel):
    axis: str
    rate_deg_s: float


class FocuserMoveBody(BaseModel):
    position: int


class AutofocusBody(BaseModel):
    exposure_s: float = 2.0
    gain: int = 120
    step: int = 350
    steps_each_side: int = 4


class FilterBody(BaseModel):
    position: int


class SwitchBody(BaseModel):
    port_id: int
    value: float


class CoolerBody(BaseModel):
    on: bool
    target_c: float | None = None


class DewBody(BaseModel):
    power: int = 0


class PHD2Body(BaseModel):
    host: str = "127.0.0.1"
    port: int = 4400


class NinaConnectBody(BaseModel):
    host: str = "127.0.0.1"
    port: int = 1888


class DitherBody(BaseModel):
    pixels: float = 3.0


class SiteBody(BaseModel):
    latitude: float
    longitude: float


def create_app() -> FastAPI:
    app = FastAPI(title="AstroDeck", version="0.1.0")

    # ------------------------------------------------------------ equipment

    @app.get("/api/discover")
    async def discover():
        return await alpaca_backend.discover()

    @app.get("/api/discover/nina")
    async def discover_nina_instances(host: str = "", port: int = 1888):
        extra = [host] if host else None
        return await discover_nina(port=port, extra_hosts=extra)

    @app.post("/api/connect/sim")
    async def connect_sim():
        return await hub.connect_sim()

    @app.post("/api/connect/alpaca")
    async def connect_alpaca(body: AlpacaConnectBody):
        try:
            return await hub.connect_alpaca_device(
                body.role, body.host, body.port, body.dev_type, body.dev_num,
                body.name or f"{body.dev_type} #{body.dev_num}")
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/connect/phd2")
    async def connect_phd2(body: PHD2Body):
        try:
            await hub.connect_phd2(body.host, body.port)
            return {"connected": True}
        except Exception as e:
            raise _err(e)

    @app.post("/api/connect/nina")
    async def connect_nina(body: NinaConnectBody):
        try:
            return await hub.connect_nina(body.host, body.port)
        except DeviceError as e:
            raise _err(e)
        except Exception as e:
            raise HTTPException(502, f"NINA connection failed: {e}")

    @app.post("/api/disconnect")
    async def disconnect():
        if engine.running:
            await engine.abort()
        await hub.disconnect_all()
        return {"ok": True}

    @app.get("/api/status")
    async def status():
        return await hub.poll_status()

    @app.get("/api/summary")
    async def summary():
        return hub.summary()

    @app.post("/api/site")
    async def set_site(body: SiteBody):
        hub.site = {"latitude": body.latitude, "longitude": body.longitude}
        return hub.site

    # -------------------------------------------------------------- capture

    @app.post("/api/capture")
    async def capture(body: CaptureBody):
        try:
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("capture", hub.capture(
            body.exposure_s, body.gain, body.offset, body.binning,
            save=body.save, target=body.target, frame_type=body.frame_type))

    @app.post("/api/capture/loop")
    async def capture_loop(body: CaptureBody):
        try:
            hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        hub.start_loop(body.exposure_s, body.gain, body.offset, body.binning)
        return {"looping": True}

    @app.post("/api/capture/stop")
    async def capture_stop():
        hub.stop_loop()
        task = hub._busy.get("capture")
        if task and not task.done():
            task.cancel()
        cam = hub.devices.get("camera")
        if cam and cam.connected:
            try:
                await cam.abort_exposure()
            except Exception:
                pass
        return {"looping": False}

    @app.get("/api/preview/{preview_id}.png")
    async def preview(preview_id: int):
        entry = hub.previews.get(preview_id)
        if entry is None:
            raise HTTPException(404, "preview expired")
        png, mime = entry
        return Response(png, media_type=mime,
                        headers={"Cache-Control": "max-age=3600"})

    @app.post("/api/camera/cooler")
    async def cooler(body: CoolerBody):
        try:
            cam = hub.require("camera")
            await cam.set_cooler(body.on, body.target_c)
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/camera/dew-heater")
    async def dew_heater(body: DewBody):
        try:
            cam = hub.require("camera")
            await cam.set_dew_heater(body.power)
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    # ---------------------------------------------------------------- mount

    @app.post("/api/mount/goto")
    async def goto(body: GotoBody):
        try:
            hub.require("telescope")
        except DeviceError as e:
            raise _err(e)
        if body.center:
            return _spawn("goto", hub.goto_and_center(body.ra_hours, body.dec_deg))

        async def plain_goto():
            tel = hub.require("telescope")
            if await tel.is_parked():
                await tel.unpark()
            await tel.set_tracking(True)
            await tel.slew(body.ra_hours, body.dec_deg)
            bus.publish("mount", action="slew_complete")
        return _spawn("goto", plain_goto())

    @app.post("/api/mount/solve_sync")
    async def solve_sync():
        try:
            hub.require("telescope"), hub.require("camera")
        except DeviceError as e:
            raise _err(e)
        return _spawn("solve", hub.solve_and_sync())

    @app.post("/api/mount/move")
    async def move_axis(body: MoveAxisBody):
        try:
            tel = hub.require("telescope")
            await tel.move_axis(body.axis, body.rate_deg_s)
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/mount/stop")
    async def mount_stop():
        try:
            tel = hub.require("telescope")
        except DeviceError as e:
            raise _err(e)
        for name in ("goto", "solve"):
            t = hub._busy.get(name)
            if t and not t.done():
                t.cancel()
        await tel.stop()
        return {"ok": True}

    @app.post("/api/mount/tracking")
    async def tracking(on: bool):
        try:
            tel = hub.require("telescope")
            await tel.set_tracking(on)
            return {"tracking": on}
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/mount/park")
    async def park():
        try:
            hub.require("telescope")
        except DeviceError as e:
            raise _err(e)
        return _spawn("goto", hub.require("telescope").park())

    @app.post("/api/mount/unpark")
    async def unpark():
        try:
            tel = hub.require("telescope")
            await tel.unpark()
            return {"ok": True}
        except DeviceError as e:
            raise _err(e)

    # -------------------------------------------------------------- focuser

    @app.post("/api/focuser/move")
    async def focuser_move(body: FocuserMoveBody):
        try:
            foc = hub.require("focuser")
        except DeviceError as e:
            raise _err(e)
        return _spawn("focuser", foc.move_to(body.position))

    @app.post("/api/focuser/autofocus")
    async def autofocus(body: AutofocusBody):
        try:
            cam = hub.require("camera")
            foc = hub.require("focuser")
        except DeviceError as e:
            raise _err(e)
        return _spawn("autofocus", run_autofocus(
            cam, foc, exposure_s=body.exposure_s, gain=body.gain,
            step=body.step, steps_each_side=body.steps_each_side))

    @app.post("/api/focuser/halt")
    async def focuser_halt():
        try:
            foc = hub.require("focuser")
        except DeviceError as e:
            raise _err(e)
        for name in ("focuser", "autofocus"):
            t = hub._busy.get(name)
            if t and not t.done():
                t.cancel()
        await foc.halt()
        return {"ok": True}

    # ---------------------------------------------------------- filterwheel

    @app.post("/api/filterwheel/position")
    async def set_filter(body: FilterBody):
        try:
            fw = hub.require("filterwheel")
        except DeviceError as e:
            raise _err(e)
        return _spawn("filterwheel", fw.set_position(body.position))

    # --------------------------------------------------------------- switch

    @app.get("/api/switch/ports")
    async def switch_ports():
        try:
            sw = hub.require("switch")
            return [p.__dict__ for p in await sw.get_ports()]
        except DeviceError as e:
            raise _err(e)

    @app.post("/api/switch/set")
    async def switch_set(body: SwitchBody):
        try:
            sw = hub.require("switch")
            await sw.set_port(body.port_id, body.value)
            return [p.__dict__ for p in await sw.get_ports()]
        except (DeviceError, RuntimeError) as e:
            raise _err(e)

    # ---------------------------------------------------------------- guide

    @app.post("/api/guide/start")
    async def guide_start():
        if not hub.guider or not hub.guider.connected:
            raise HTTPException(409, "no guider connected")
        return _spawn("guide", hub.guider.start_guiding())

    @app.post("/api/guide/stop")
    async def guide_stop():
        if not hub.guider:
            raise HTTPException(409, "no guider connected")
        await hub.guider.stop_guiding()
        return {"ok": True}

    @app.post("/api/guide/dither")
    async def guide_dither(body: DitherBody):
        if not hub.guider or not hub.guider.connected:
            raise HTTPException(409, "no guider connected")
        return _spawn("dither", hub.guider.dither(body.pixels))

    # ------------------------------------------------------------- sequence

    @app.post("/api/sequence/start")
    async def sequence_start(plan: SequencePlan):
        if not plan.targets or plan.total_frames() == 0:
            raise HTTPException(422, "plan has no frames")
        try:
            hub.require("camera")
            engine.start(plan)
        except DeviceError as e:
            raise _err(e)
        return {"started": True, "frames": plan.total_frames()}

    @app.post("/api/sequence/pause")
    async def sequence_pause():
        engine.pause()
        return {"paused": True}

    @app.post("/api/sequence/resume")
    async def sequence_resume():
        engine.resume()
        return {"paused": False}

    @app.post("/api/sequence/abort")
    async def sequence_abort():
        await engine.abort()
        return {"aborted": True}

    @app.get("/api/sequence/state")
    async def sequence_state():
        return engine.state | {"running": engine.running, "paused": engine.paused}

    @app.get("/api/sequence/recoverable")
    async def sequence_recoverable():
        data = engine.load_resume()
        if not data:
            return {"recoverable": False}
        plan = SequencePlan(**data["plan"])
        done = sum(data.get("done", {}).values())
        return {"recoverable": True, "name": plan.name, "frames_done": done,
                "frames_total": plan.total_frames(), "ts": data.get("ts")}

    @app.post("/api/sequence/recover")
    async def sequence_recover():
        data = engine.load_resume()
        if not data:
            raise HTTPException(404, "no resumable sequence found")
        plan = SequencePlan(**data["plan"])
        try:
            hub.require("camera")
            engine.start(plan, resume_done=data.get("done", {}))
        except DeviceError as e:
            raise _err(e)
        done = sum(data.get("done", {}).values())
        return {"resumed": True, "frames_remaining": plan.total_frames() - done}

    # -------------------------------------------------------------- polar align

    @app.post("/api/polar/start")
    async def polar_start():
        try:
            await hub.polar.start()
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        return {"started": True, "source": hub.polar.state["source"]}

    @app.post("/api/polar/stop")
    async def polar_stop():
        await hub.polar.stop()
        return {"ok": True}

    @app.post("/api/polar/pause")
    async def polar_pause():
        await hub.polar.pause()
        return {"ok": True}

    @app.post("/api/polar/resume")
    async def polar_resume():
        await hub.polar.resume()
        return {"ok": True}

    @app.get("/api/polar/state")
    async def polar_state():
        return hub.polar.state | {"running": hub.polar.running}

    # -------------------------------------------------------------- catalog

    @app.get("/api/catalog")
    async def catalog(q: str = ""):
        from ..catalog import altaz
        results = search_catalog(q)
        for r in results:
            alt, az = altaz(r["ra_hours"], r["dec_deg"],
                            hub.site["latitude"], hub.site["longitude"])
            r["alt"] = round(alt, 1)
            r["az"] = round(az, 1)
        return results

    @app.get("/api/logs")
    async def logs():
        return bus.log_history

    # ------------------------------------------------------------ websocket

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        q = bus.subscribe()
        try:
            await websocket.send_json({"type": "hello", "data": hub.summary(), "ts": 0})
            while True:
                ev = await q.get()
                await websocket.send_json(ev.to_json())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            bus.unsubscribe(q)

    # ------------------------------------------------------------ static UI

    if UI_DIST.exists():
        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            target = UI_DIST / path
            if path and target.is_file():
                return FileResponse(target)
            return FileResponse(UI_DIST / "index.html")

    return app
