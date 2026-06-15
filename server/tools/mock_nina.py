"""A mock of NINA's Advanced API, backed by AstroDeck's own simulator rig.

This serves the subset of NINA endpoints the AstroDeck NINA bridge uses,
wrapping responses in NINA's ``{Response, Error, StatusCode, Success, Type}``
envelope and rendering real (sim) star-field images. Because it is driven by
the same SimRig, pointing affects the rendered stars and focuser position
affects HFR — so autofocus, plate-solving and centering genuinely converge.

Two uses:
  * tests import ``create_mock_nina()`` and drive it via an httpx ASGI transport
  * ``python -m tools.mock_nina`` runs it on :1888 so the real AstroDeck server
    can be pointed at it (NINA mode) with no hardware.
"""
from __future__ import annotations

import io
import time

import numpy as np
from fastapi import FastAPI, Request, Response
from PIL import Image

from astrodeck.devices.sim import build_sim_rig
from astrodeck.imaging.processing import auto_stretch
from astrodeck.imaging.stars import median_hfr


def _env(resp, success=True, error=""):
    return {"Response": resp, "Error": error, "StatusCode": 200 if success else 500,
            "Success": success, "Type": "API"}


class MockNinaState:
    def __init__(self):
        rig = build_sim_rig()
        self.rig = rig.pop("_rig")
        self.guide_cam = rig.pop("guide_camera")
        self.dev = rig                      # camera, telescope, focuser, filterwheel, switch
        self.last_frame = None
        self.last_af = None
        self.af_counter = 0
        self.guiding = False


async def _ensure_connected(state: MockNinaState):
    for d in list(state.dev.values()) + [state.guide_cam]:
        if not d.connected:
            await d.connect()


def create_mock_nina() -> tuple[FastAPI, MockNinaState]:
    app = FastAPI(title="Mock NINA Advanced API")
    state = MockNinaState()
    api = "/v2/api"

    @app.middleware("http")
    async def _connect_first(request: Request, call_next):
        # Works under both uvicorn (lifespan) and httpx ASGI transport (no
        # lifespan) — cheap after the first request since connect() no-ops.
        await _ensure_connected(state)
        return await call_next(request)

    # ----------------------------------------------------------- application
    @app.get(api + "/version")
    async def version():
        return _env("2.2.13-mock")

    @app.get(api + "/version/nina")
    async def nina_version():
        return _env("3.1.0.9001")

    # --------------------------------------------------------------- camera
    @app.get(api + "/equipment/camera/info")
    async def camera_info():
        cam = state.dev["camera"]
        return _env({
            "Connected": cam.connected, "Name": cam.name,
            "XSize": cam.sensor_width, "YSize": cam.sensor_height,
            "PixelSize": cam.pixel_size_um, "GainMax": cam.max_gain,
            "CanSetTemperature": cam.can_cool, "SensorType": "Monochrome",
            "Temperature": state.rig.sensor_temp,
        })

    @app.get(api + "/equipment/camera/set-binning")
    async def camera_set_binning(binning: str = "1x1"):
        return _env({"binning": binning})

    @app.get(api + "/equipment/camera/capture")
    async def camera_capture():
        # Render at robust params so the sim star field reliably yields HFR/star
        # counts (as NINA's own pipeline would). The requested duration/gain ride
        # on the AstroDeck-side CameraFrame metadata, not this mock render.
        cam = state.dev["camera"]
        state.last_frame = await cam.expose(0.1, 200, 30, binning=2)
        return _env({})

    @app.get(api + "/equipment/camera/capture/statistics")
    async def camera_statistics():
        frame = state.last_frame
        if frame is None:
            return _env({}, success=False, error="no capture yet")
        hfr, stars = median_hfr(frame.data)
        return _env({
            "HFR": round(hfr, 3) if hfr else None, "Stars": stars,
            "Mean": float(frame.data.mean()), "Median": int(np.median(frame.data)),
            "Temperature": state.rig.sensor_temp,
            "Filename": f"D:\\\\NINA\\\\Light_{int(time.time())}.fits",
        })

    @app.get(api + "/prepared-image")
    async def prepared_image(request: Request):
        frame = state.last_frame
        if frame is None:
            cam = state.dev["camera"]
            frame = await cam.expose(0.05, 120, 30, binning=1)
            state.last_frame = frame
        arr = (auto_stretch(frame.data) * 255).astype(np.uint8)
        pil = Image.fromarray(arr, "L")
        size = request.query_params.get("size")
        if size and "x" in size:
            try:
                w, h = (int(v) for v in size.lower().split("x"))
                if w and h:
                    pil = pil.resize((w, h), Image.BILINEAR)
            except ValueError:
                pass
        buf = io.BytesIO()
        pil.save(buf, "JPEG", quality=int(float(request.query_params.get("quality", 90)) or 90))
        return Response(buf.getvalue(), media_type="image/jpeg")

    @app.get(api + "/prepared-image/solve")
    async def prepared_solve():
        return _env({"Success": True,
                     "Coordinates": {"RA": state.rig.ra_hours, "Dec": state.rig.dec_deg},
                     "Pixscale": 1.55, "Orientation": 0.0})

    @app.get(api + "/equipment/camera/abort-exposure")
    async def camera_abort():
        await state.dev["camera"].abort_exposure()
        return _env({})

    @app.get(api + "/equipment/camera/cool")
    async def camera_cool(temperature: float = -10, minutes: float = 0):
        await state.dev["camera"].set_cooler(True, temperature)
        return _env({})

    @app.get(api + "/equipment/camera/warm")
    async def camera_warm(minutes: float = 0):
        await state.dev["camera"].set_cooler(False)
        return _env({})

    # ---------------------------------------------------------------- mount
    @app.get(api + "/equipment/mount/info")
    async def mount_info():
        tel = state.dev["telescope"]
        ra, dec = await tel.get_position()
        return _env({
            "Connected": tel.connected, "Name": tel.name,
            "RightAscension": ra, "Declination": dec,
            "Slewing": await tel.is_slewing(), "TrackingEnabled": await tel.get_tracking(),
            "AtPark": await tel.is_parked(), "SideOfPier": "West",
        })

    @app.get(api + "/equipment/mount/slew")
    async def mount_slew(ra: float, dec: float, waitToFinish: str = "true"):
        await state.dev["telescope"].slew(ra, dec)
        return _env({})

    @app.get(api + "/equipment/mount/sync")
    async def mount_sync(ra: float, dec: float):
        await state.dev["telescope"].sync(ra, dec)
        return _env({})

    @app.get(api + "/equipment/mount/set-tracking")
    async def mount_tracking(enabled: str = "true"):
        await state.dev["telescope"].set_tracking(enabled.lower() == "true")
        return _env({})

    @app.get(api + "/equipment/mount/park")
    async def mount_park():
        await state.dev["telescope"].park()
        return _env({})

    @app.get(api + "/equipment/mount/unpark")
    async def mount_unpark():
        await state.dev["telescope"].unpark()
        return _env({})

    @app.get(api + "/equipment/mount/slew-stop")
    async def mount_slew_stop():
        await state.dev["telescope"].stop()
        return _env({})

    # -------------------------------------------------------------- focuser
    @app.get(api + "/equipment/focuser/info")
    async def focuser_info():
        foc = state.dev["focuser"]
        return _env({
            "Connected": foc.connected, "Name": foc.name,
            "Position": await foc.get_position(), "Temperature": await foc.get_temperature(),
            "IsMoving": False, "MaxStep": foc.max_position,
        })

    @app.get(api + "/equipment/focuser/move")
    async def focuser_move(position: int):
        await state.dev["focuser"].move_to(position)
        return _env({})

    @app.get(api + "/equipment/focuser/stop-move")
    async def focuser_stop():
        await state.dev["focuser"].halt()
        return _env({})

    @app.get(api + "/equipment/focuser/auto-focus")
    async def focuser_autofocus(cancel: str = "false"):
        # Simulate a NINA autofocus run against the sim V-curve.
        foc, cam = state.dev["focuser"], state.dev["camera"]
        best = state.rig.best_focus
        points = []
        for pos in range(best - 1400, best + 1401, 350):
            await foc.move_to(max(0, pos))
            frame = await cam.expose(0.05, 200, 30, binning=2)
            hfr, _ = median_hfr(frame.data)
            if hfr:
                points.append({"Position": pos, "Value": round(hfr, 3)})
        await foc.move_to(best)
        state.af_counter += 1
        state.last_af = {
            "Timestamp": f"2026-06-14T00:00:{state.af_counter:02d}Z",
            "Method": "STARHFR", "Filter": "L", "Temperature": 4.2,
            "MeasurePoints": points,
            "CalculatedFocusPoint": {"Position": best, "Value":
                                     min((p["Value"] for p in points), default=1.6)},
        }
        return _env({})

    @app.get(api + "/equipment/focuser/last-af")
    async def focuser_last_af():
        if state.last_af is None:
            return _env({}, success=False, error="no autofocus report")
        return _env(state.last_af)

    # ----------------------------------------------------------- filterwheel
    @app.get(api + "/equipment/filterwheel/info")
    async def filterwheel_info():
        fw = state.dev["filterwheel"]
        pos = await fw.get_position()
        return _env({
            "Connected": fw.connected, "Name": fw.name,
            "SelectedFilter": {"Name": fw.filter_names[pos], "Id": pos},
            "AvailableFilters": [{"Name": n, "Id": i} for i, n in enumerate(fw.filter_names)],
        })

    @app.get(api + "/equipment/filterwheel/change-filter")
    async def filterwheel_change(filterId: int):
        await state.dev["filterwheel"].set_position(filterId)
        return _env({})

    # --------------------------------------------------------------- switch
    @app.get(api + "/equipment/switch/info")
    async def switch_info():
        sw = state.dev["switch"]
        ports = await sw.get_ports()
        return _env({
            "Connected": sw.connected, "Name": sw.name,
            "WritableSwitches": [
                {"Id": p.id, "Name": p.name, "Value": p.value,
                 "Minimum": p.min, "Maximum": p.max, "Unit": p.unit}
                for p in ports if p.can_write],
            "ReadonlySwitches": [
                {"Id": p.id, "Name": p.name, "Value": p.value,
                 "Minimum": p.min, "Maximum": p.max, "Unit": p.unit}
                for p in ports if not p.can_write],
        })

    @app.get(api + "/equipment/switch/set-switch-value")
    async def switch_set(index: int, value: float):
        await state.dev["switch"].set_port(index, value)
        return _env({})

    # ---------------------------------------------------------------- guider
    @app.get(api + "/equipment/guider/info")
    async def guider_info():
        return _env({"Connected": True, "Name": "Mock PHD2",
                     "State": "Guiding" if state.guiding else "Stopped",
                     "PixelScale": 1.0,
                     "RMSError": {"RA": {"Arcseconds": 0.4},
                                  "Dec": {"Arcseconds": 0.35},
                                  "Total": {"Arcseconds": 0.53}}})

    @app.get(api + "/equipment/guider/start")
    async def guider_start(calibrate: str = "false"):
        state.guiding = True
        return _env({})

    @app.get(api + "/equipment/guider/stop")
    async def guider_stop():
        state.guiding = False
        return _env({})

    @app.get(api + "/equipment/guider/dither")
    async def guider_dither():
        return _env({})

    @app.get(api + "/equipment/guider/graph")
    async def guider_graph():
        rng = np.random.default_rng(int(time.time()) % 9999)
        steps = [{"RADistanceRaw": float(rng.normal(0, 0.4)),
                  "DECDistanceRaw": float(rng.normal(0, 0.35))} for _ in range(60)]
        return _env({"GuideSteps": steps, "RMS": {"RA": 0.4, "Dec": 0.35, "Total": 0.53}})

    return app, state


app, _state = create_mock_nina()


if __name__ == "__main__":
    import uvicorn
    print("Mock NINA Advanced API on http://127.0.0.1:1888  (point AstroDeck NINA mode here)")
    uvicorn.run(app, host="127.0.0.1", port=1888, log_level="warning")
