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
from ..providers import resolve

# NINA's TPPA AzimuthError/AltitudeError/TotalError are doubles in DEGREES;
# we present arcminutes. (Single constant — easy to flip if a live TPPA run
# shows otherwise.)
_DEG_TO_MIN = 60.0


class PolarAlignSession:
    def __init__(self, hub: Any):
        self.hub = hub
        self._task: asyncio.Task | None = None
        self._ws: Any = None
        # Pause flag polled ONLY by the native driver (``polar/native.py``): the
        # NINA/sim drivers pause via their own mechanism (a ws message / ignored),
        # so this stays False for them and changes nothing about their behavior.
        self._native_paused = False
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
        self._native_paused = False
        # Resolve the driver and record the source SYNCHRONOUSLY, before we
        # return. create_task only schedules the driver; its body (and its first
        # _publish(source=...)) hasn't run when the API handler reads
        # state["source"] for the {"started": true, "source": ...} response — so
        # without this a REST client always saw source=null and could not tell
        # whether the real NINA TPPA or the simulator was started.
        #
        # THREE-WAY provider resolution (native-parity spec §5): the capability
        # resolver decides who runs polar alignment for this rig —
        #   backend   -> NINA's TPPA plugin (existing _run_nina)
        #   astrodeck -> the Rust TPPA engine (polar/native.run_native)
        #   sim / unavailable -> the built-in _run_sim
        # Since fe9abea the resolver's kind vocabulary maps 1:1 onto that split:
        # kind "astrodeck" IS the native engine (label "AstroDeck native"), and
        # the simulator fallback — as well as an explicit sim override — now
        # resolves to kind "sim", not "astrodeck". Resolution never fatally
        # fails here (any error degrades to the simulator) — polar align always
        # has a driver.
        try:
            choice = resolve("polar_align", self.hub)
        except Exception:
            choice = None

        if self.hub.nina_client is not None and (
                choice is None or choice.kind == "backend"):
            self.state["source"] = "nina"
            self._task = asyncio.create_task(self._run_nina())
        # ``choice.label != "Simulator"`` is now redundant — kind "astrodeck"
        # is exclusively the native engine (the simulator resolves to kind
        # "sim", see above) — but it's belt-and-braces harmless, so it stays.
        elif (choice is not None and choice.kind == "astrodeck"
              and choice.label != "Simulator"):
            from .native import run_native
            self.state["source"] = "native"
            self._task = asyncio.create_task(run_native(self, self.hub))
        else:
            self.state["source"] = "sim"
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
        # Native driver: raise the pause flag it polls so it stops capturing
        # (a no-op for NINA/sim, which don't read it).
        self._native_paused = True
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"Action": "pause-alignment"}))
            except Exception:
                pass
        self._publish(state="paused", message="paused")

    async def resume(self) -> None:
        self._native_paused = False
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
        got_measurement = False
        try:
            async with websockets.connect(url, open_timeout=10, ping_interval=20) as ws:
                self._ws = ws
                await ws.send(json.dumps({"Action": "start-alignment"}))
                bus.log("info", "NINA TPPA started", "polar")
                async for raw in ws:
                    try:
                        if self._handle_nina(json.loads(raw)):
                            got_measurement = True
                    except (ValueError, TypeError):
                        continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._publish(state="error", source="nina",
                          message=f"TPPA connection failed: {e}")
            bus.log("error", f"NINA TPPA: {e}", "polar")
            return
        finally:
            self._ws = None
        # The `async for` above ends WITHOUT an exception when NINA closes the
        # TPPA websocket cleanly (alignment finished/stopped on the NINA side,
        # plugin reload, or shutdown with a normal close frame). Nothing in the
        # loop publishes a terminal state, so the last event still says
        # "running" — the UI would stick on a blinking "running" with Start
        # disabled forever. Publish a terminal state now: "done" if TPPA
        # produced at least one measurement (it actually ran to a result),
        # otherwise "error" for a socket that closed before any alignment data.
        if got_measurement:
            self._publish(state="done", progress=1.0, message="alignment complete")
            bus.log("info", "NINA TPPA finished", "polar")
        else:
            self._publish(state="error",
                          message="NINA closed the TPPA connection")
            bus.log("warning",
                    "NINA TPPA connection closed before any measurement", "polar")

    def _handle_nina(self, msg: dict) -> bool:
        """Apply one NINA TPPA message. Returns True if it carried an alignment
        measurement (proof TPPA actually ran) — the caller uses that to decide
        the terminal state when the websocket later closes cleanly."""
        resp = msg.get("Response", msg) if isinstance(msg, dict) else {}
        az = pick(resp, "AzimuthError")
        alt = pick(resp, "AltitudeError")
        tot = pick(resp, "TotalError")
        got_measurement = az is not None or alt is not None or tot is not None
        if got_measurement:
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
                p = float(progress)
            except (TypeError, ValueError):
                p = None
            if p is not None:
                # NINA reports Progress as -1 while a phase is indeterminate
                # (e.g. "Solving…"). Never emit a negative progress — the UI
                # hides the bar on negatives. Hold the last known progress
                # through an indeterminate phase, and clamp to [0, 1] otherwise.
                if p < 0:
                    kw["progress"] = float(self.state.get("progress", 0.0))
                else:
                    kw["progress"] = max(0.0, min(1.0, p))
        if kw:
            self._publish(**kw)
        return got_measurement

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
