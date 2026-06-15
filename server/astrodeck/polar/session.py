"""Polar alignment session.

Drives NINA's Three-Point Polar Alignment (TPPA) over its ``/tppa`` websocket
on the live rig, or a simulator otherwise, and publishes a single ``polar``
event the UI's reticle consumes. Errors are normalized to arcminutes.
"""
from __future__ import annotations

import asyncio
import json
import math
import random
from typing import Any

from ..devices.nina import pick
from ..events import bus

# NINA's TPPA AzimuthError/AltitudeError/TotalError are doubles in DEGREES;
# we present arcminutes. (Single constant — easy to flip if a live TPPA run
# shows otherwise.)
_DEG_TO_MIN = 60.0


class PolarAlignSession:
    def __init__(self, hub: Any):
        self.hub = hub
        self._task: asyncio.Task | None = None
        self._ws: Any = None
        self.state: dict[str, Any] = self._idle()

    @staticmethod
    def _idle() -> dict[str, Any]:
        return {"state": "idle", "az_error": 0.0, "alt_error": 0.0,
                "total_error": 0.0, "progress": 0.0, "message": "", "source": None}

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _publish(self, **kw: Any) -> None:
        self.state = {**self.state, **kw}
        if "az_error" in kw or "alt_error" in kw:
            self.state["total_error"] = round(
                math.hypot(self.state["az_error"], self.state["alt_error"]), 2)
        bus.publish("polar", **self.state)

    # ----------------------------------------------------------------- control

    async def start(self) -> None:
        if self.running:
            raise RuntimeError("polar alignment is already running")
        self.state = self._idle()
        if self.hub.nina_client is not None:
            self._task = asyncio.create_task(self._run_nina())
        else:
            self._task = asyncio.create_task(self._run_sim())

    async def stop(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"Action": "stop-alignment"}))
            except Exception:
                pass
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._ws = None
        self._publish(state="idle", message="stopped", progress=0.0)

    async def pause(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"Action": "pause-alignment"}))
            except Exception:
                pass
        self._publish(state="paused", message="paused")

    async def resume(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"Action": "resume-alignment"}))
            except Exception:
                pass
        self._publish(state="running", message="resumed")

    # -------------------------------------------------------------- NINA driver

    async def _run_nina(self) -> None:
        try:
            import websockets
        except ImportError:
            self._publish(state="error", source="nina",
                          message="websockets library unavailable")
            return
        client = self.hub.nina_client
        url = f"ws://{client.host}:{client.port}/v2/tppa"
        self._publish(state="running", source="nina", progress=0.0,
                      message="connecting to NINA TPPA…")
        try:
            async with websockets.connect(url, open_timeout=10, ping_interval=20) as ws:
                self._ws = ws
                await ws.send(json.dumps({"Action": "start-alignment"}))
                bus.log("info", "NINA TPPA started", "polar")
                async for raw in ws:
                    try:
                        self._handle_nina(json.loads(raw))
                    except (ValueError, TypeError):
                        continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._publish(state="error", source="nina",
                          message=f"TPPA connection failed: {e}")
            bus.log("error", f"NINA TPPA: {e}", "polar")
        finally:
            self._ws = None

    def _handle_nina(self, msg: dict) -> None:
        resp = msg.get("Response", msg) if isinstance(msg, dict) else {}
        az = pick(resp, "AzimuthError")
        alt = pick(resp, "AltitudeError")
        tot = pick(resp, "TotalError")
        if az is not None or alt is not None or tot is not None:
            self._publish(
                state="running",
                az_error=round(float(az or 0) * _DEG_TO_MIN, 2),
                alt_error=round(float(alt or 0) * _DEG_TO_MIN, 2),
                message="adjust the mount",
            )
        status = pick(resp, "Status")
        progress = pick(resp, "Progress")
        kw: dict[str, Any] = {}
        if status is not None:
            kw["message"] = str(status)
        if progress is not None:
            try:
                kw["progress"] = float(progress)
            except (TypeError, ValueError):
                pass
        if kw:
            self._publish(**kw)

    # --------------------------------------------------------------- sim driver

    async def _run_sim(self) -> None:
        try:
            self._publish(state="running", source="sim", progress=0.05,
                          message="slewing to first point")
            await asyncio.sleep(1.2)
            for i in range(1, 4):
                self._publish(message=f"measuring point {i}/3", progress=0.1 + 0.2 * i)
                await asyncio.sleep(1.0)

            az = random.uniform(4.0, 9.0) * random.choice((-1, 1))
            alt = random.uniform(3.0, 8.0) * random.choice((-1, 1))
            self._publish(state="running", progress=0.8, message="adjust the mount",
                          az_error=round(az, 2), alt_error=round(alt, 2))

            # Converge (as if the user were turning the bolts) so the whole
            # reticle/vector/auto-zoom flow is visible end to end.
            while math.hypot(az, alt) > 0.4:
                await asyncio.sleep(1.0)
                az = az * 0.82 + random.uniform(-0.2, 0.2)
                alt = alt * 0.82 + random.uniform(-0.2, 0.2)
                self._publish(az_error=round(az, 2), alt_error=round(alt, 2),
                              message="adjust the mount")

            self._publish(state="done", progress=1.0, message="polar aligned",
                          az_error=round(az, 2), alt_error=round(alt, 2))
            bus.log("info", "sim polar alignment complete", "polar")
        except asyncio.CancelledError:
            raise
