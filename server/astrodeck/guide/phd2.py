"""PHD2 guider client.

Talks to PHD2's JSON event socket (default localhost:4400): newline-delimited
JSON events in, JSON-RPC requests out. GuideStep events are republished to the
AstroDeck event bus for the live guide graph.
"""
from __future__ import annotations

import asyncio
import base64
import io
import itertools
import json
import math
import time
from collections import deque
from typing import Any

from ..events import bus
from .base import Guider, GuideStats

SETTLE = {"pixels": 1.5, "time": 8, "timeout": 60}


def star_image_to_png(result: dict) -> bytes | None:
    """Decode a PHD2 ``get_star_image`` result into an auto-stretched PNG.

    PHD2 returns ``{width, height, pixels: <base64 16-bit little-endian>,
    star_pos: [x, y]}``. We decode the raw 16-bit pixels, auto-stretch them
    (reusing the main display pipeline so the guide thumbnail matches the rest
    of the UI), and encode a small PNG. Returns ``None`` on any malformed
    payload rather than raising — the caller answers 404 on None."""
    try:
        import numpy as np

        from ..imaging.processing import to_png

        w = int(result.get("width") or 0)
        h = int(result.get("height") or 0)
        b64 = result.get("pixels")
        if w <= 0 or h <= 0 or not b64:
            return None
        raw = base64.b64decode(b64)
        arr = np.frombuffer(raw, dtype="<u2")
        if arr.size < w * h:
            return None
        img = arr[: w * h].reshape((h, w)).astype(np.uint16)
        # to_png auto-stretches; cap the width so a 15px star tile still scales
        # up to a visible thumbnail but a larger subframe stays modest.
        return to_png(img, stretch=True, max_width=max(w, 256))
    except Exception:
        return None


class PHD2Guider(Guider):
    name = "PHD2"

    def __init__(self, host: str = "127.0.0.1", port: int = 4400,
                 pixel_scale_arcsec: float = 2.0):
        self.host, self.port = host, port
        self.pixel_scale = pixel_scale_arcsec
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._rpc_id = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._listen_task: asyncio.Task | None = None
        self._settle_done: asyncio.Event = asyncio.Event()
        self._settle_error: str | None = None
        self._app_state = "Stopped"
        self._samples: deque[dict] = deque(maxlen=300)
        self._snr = 0.0

    # ------------------------------------------------------------- transport

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=5
        )
        self._listen_task = asyncio.create_task(self._listen())
        self.connected = True
        bus.log("info", f"connected to PHD2 at {self.host}:{self.port}", "guide")

    async def disconnect(self) -> None:
        self.connected = False
        if self._listen_task:
            self._listen_task.cancel()
        if self._writer:
            self._writer.close()

    async def _listen(self) -> None:
        assert self._reader is not None
        while True:
            line = await self._reader.readline()
            if not line:
                self.connected = False
                bus.log("error", "PHD2 connection lost", "guide")
                return
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "jsonrpc" in msg and "id" in msg:
                fut = self._pending.pop(msg["id"], None)
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_exception(RuntimeError(msg["error"].get("message", "PHD2 error")))
                    else:
                        fut.set_result(msg.get("result"))
            else:
                self._handle_event(msg)

    def _handle_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("Event")
        if kind == "GuideStep":
            ra = float(ev.get("RADistanceRaw", 0)) * self.pixel_scale
            dec = float(ev.get("DECDistanceRaw", 0)) * self.pixel_scale
            self._snr = float(ev.get("SNR", 0))
            sample = {"t": time.time(), "ra": ra, "dec": dec}
            self._samples.append(sample)
            bus.publish("guide", **self.stats().__dict__)
        elif kind == "AppState":
            self._app_state = ev.get("State", "Stopped")
        elif kind == "SettleDone":
            self._settle_error = ev.get("Error") or None
            if ev.get("Status", 0) != 0 and not self._settle_error:
                self._settle_error = "settle failed"
            self._settle_done.set()
        elif kind in ("GuidingStopped", "StarLost"):
            bus.publish("guide", **self.stats().__dict__)
            if kind == "StarLost":
                bus.log("warning", "PHD2 lost the guide star", "guide")

    async def _rpc(self, method: str, params: list | None = None,
                   timeout: float = 30) -> Any:
        assert self._writer is not None
        rpc_id = next(self._rpc_id)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rpc_id] = fut
        req = {"method": method, "params": params or [], "id": rpc_id}
        self._writer.write((json.dumps(req) + "\r\n").encode())
        await self._writer.drain()
        return await asyncio.wait_for(fut, timeout=timeout)

    # -------------------------------------------------------------- commands

    async def start_guiding(self) -> None:
        self._settle_done.clear()
        self._settle_error = None
        await self._rpc("guide", [SETTLE, False])
        await asyncio.wait_for(self._settle_done.wait(), timeout=SETTLE["timeout"] + 30)
        if self._settle_error:
            raise RuntimeError(f"PHD2 settle failed: {self._settle_error}")
        bus.log("info", "PHD2 guiding and settled", "guide")

    async def stop_guiding(self) -> None:
        await self._rpc("stop_capture")

    async def dither(self, pixels: float = 3.0) -> None:
        self._settle_done.clear()
        self._settle_error = None
        await self._rpc("dither", [pixels, False, SETTLE])
        await asyncio.wait_for(self._settle_done.wait(), timeout=SETTLE["timeout"] + 30)
        if self._settle_error:
            raise RuntimeError(f"dither settle failed: {self._settle_error}")

    def stats(self) -> GuideStats:
        recent = list(self._samples)
        if recent:
            ras = [s["ra"] for s in recent[-100:]]
            decs = [s["dec"] for s in recent[-100:]]
            rms_ra = math.sqrt(sum(r * r for r in ras) / len(ras))
            rms_dec = math.sqrt(sum(d * d for d in decs) / len(decs))
        else:
            rms_ra = rms_dec = 0.0
        return GuideStats(
            guiding=self._app_state == "Guiding",
            rms_ra=round(rms_ra, 2), rms_dec=round(rms_dec, 2),
            rms_total=round(math.hypot(rms_ra, rms_dec), 2),
            snr=self._snr, recent=recent[-120:],
        )

    async def guide_frame(self) -> bytes | None:
        """Auto-stretched PNG of the current guide star via PHD2's
        ``get_star_image`` RPC. Returns None if not connected, no star is
        selected, or the RPC errors — never raises."""
        if not self.connected or self._writer is None:
            return None
        try:
            # size 15 = PHD2's default star-image subframe edge (px).
            result = await self._rpc("get_star_image", [15], timeout=5)
        except Exception:
            return None
        if not isinstance(result, dict):
            return None
        return star_image_to_png(result)
