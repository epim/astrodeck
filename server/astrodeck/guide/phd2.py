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
    #: PHD2 can flip its calibration for a meridian flip (P1-6).
    can_flip_calibration = True

    # Reconnect backoff schedule (seconds) after an unexpected socket drop.
    _RECONNECT_BACKOFF = (1, 2, 5, 10, 20, 30)

    def __init__(self, host: str = "127.0.0.1", port: int = 4400,
                 pixel_scale_arcsec: float = 2.0):
        self.host, self.port = host, port
        self.pixel_scale = pixel_scale_arcsec
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._rpc_id = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._listen_task: asyncio.Task | None = None
        # The auto-reconnect loop, tracked so disconnect() can cancel it — an
        # untracked reconnect could otherwise revive a guider the hub has
        # already discarded and republish duplicate guide events forever (P1-6).
        self._reconnect_task: asyncio.Task | None = None
        self._settle_done: asyncio.Event = asyncio.Event()
        self._settle_error: str | None = None
        self._app_state = "Stopped"
        self._samples: deque[dict] = deque(maxlen=300)
        self._snr = 0.0
        # Set True by disconnect() so a deliberate close does NOT trigger the
        # auto-reconnect loop (only an unexpected EOF/error does).
        self._closing = False

    # ------------------------------------------------------------- transport

    async def _open(self) -> None:
        """Open the socket (no listener). Shared by connect() and reconnect."""
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=5
        )

    async def connect(self) -> None:
        self._closing = False
        await self._open()
        self._listen_task = asyncio.create_task(self._listen())
        self.connected = True
        await self._refresh_app_state()
        bus.log("info", f"connected to PHD2 at {self.host}:{self.port}", "guide")

    async def _refresh_app_state(self) -> None:
        """Query PHD2's real state instead of assuming it. PHD2 sends an
        ``AppState`` event only in its initial burst when a client connects;
        after that, state changes arrive as discrete events. On a (re)connect
        that lands mid-session we must not assume "Stopped", so we ask PHD2
        directly via ``get_app_state``. Best-effort: a backend that doesn't
        answer just keeps the last known state (never raises out of connect)."""
        try:
            state = await self._rpc("get_app_state", timeout=5)
        except Exception:
            return
        if isinstance(state, str) and state:
            self._app_state = state

    async def disconnect(self) -> None:
        self._closing = True
        self.connected = False
        # Cancel any in-flight reconnect loop FIRST. Setting _closing alone is
        # not enough: _reconnect can be parked inside `await self._open()`, and
        # if the open succeeds it would spawn a fresh listener + set connected on
        # a guider the hub has already discarded (duplicate-events race, P1-6).
        rtask = self._reconnect_task
        self._reconnect_task = None
        if rtask:
            rtask.cancel()
            try:
                await rtask
            except (asyncio.CancelledError, Exception):
                pass
        task = self._listen_task
        self._listen_task = None
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
        self._fail_pending("PHD2 disconnected")

    def _fail_pending(self, reason: str) -> None:
        """Fail every in-flight RPC future so a caller awaiting a response after a
        socket drop fails fast instead of hanging until its own timeout, and the
        ``_pending`` map never leaks (P1-6). Also unblock any settle waiter so a
        ``start_guiding``/``dither`` in progress raises a clear error."""
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError(reason))
        # A drop mid-settle is a failed settle, not a 90s hang.
        if not self._settle_done.is_set():
            self._settle_error = self._settle_error or reason
            self._settle_done.set()

    async def _listen(self) -> None:
        """Read+demux the PHD2 stream. On an unexpected EOF/error, fail pending
        RPCs, mark not-guiding, and (unless deliberately closing) hand off to the
        reconnect loop so a dropped socket self-heals instead of leaving the
        guider permanently stuck reporting stale "Guiding" (P1-6)."""
        try:
            assert self._reader is not None
            while True:
                line = await self._reader.readline()
                if not line:
                    raise ConnectionError("PHD2 connection closed (EOF)")
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "jsonrpc" in msg and "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(
                                msg["error"].get("message", "PHD2 error")))
                        else:
                            fut.set_result(msg.get("result"))
                else:
                    self._handle_event(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Unexpected drop. Mark not-connected/not-guiding and fail pending so
            # stats() stops claiming "Guiding" and no future leaks.
            self.connected = False
            self._app_state = "Stopped"
            self._fail_pending(f"PHD2 connection lost: {e}")
            bus.publish("guide", **self.stats().__dict__)
            if self._closing:
                return
            bus.log("error", f"PHD2 connection lost: {e}; reconnecting", "guide")
            # Track the reconnect task so disconnect() can cancel it (P1-6).
            self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _reconnect(self) -> None:
        """Re-open the socket with capped exponential backoff and restart the
        listener. Runs until reconnected or until disconnect() flips _closing."""
        for i in range(1000):
            if self._closing:
                return
            delay = self._RECONNECT_BACKOFF[min(i, len(self._RECONNECT_BACKOFF) - 1)]
            await asyncio.sleep(delay)
            if self._closing:
                return
            try:
                await self._open()
            except Exception as e:
                bus.log("warning",
                        f"PHD2 reconnect attempt {i + 1} failed: {e}", "guide")
                continue
            # disconnect() may have flipped _closing while we were parked in the
            # TCP connect above. Re-check AFTER the open succeeds: if we're
            # closing, throw away the freshly opened socket instead of reviving
            # an abandoned guider (would double-connect + duplicate events, P1-6).
            if self._closing:
                if self._writer is not None:
                    try:
                        self._writer.close()
                    except Exception:
                        pass
                return
            self._listen_task = asyncio.create_task(self._listen())
            self.connected = True
            await self._refresh_app_state()
            bus.log("info", f"reconnected to PHD2 at {self.host}:{self.port}",
                    "guide")
            return

    def _handle_event(self, ev: dict[str, Any]) -> None:
        kind = ev.get("Event")
        if kind == "GuideStep":
            # A GuideStep is emitted ONLY while PHD2 is actively guiding. Real
            # PHD2 sends AppState only in the initial connect burst, never on a
            # later StartGuiding, so treat an arriving GuideStep as proof of
            # guiding — otherwise is_active()/stats().guiding would stay False
            # all night even as the graph updates (breaking per-frame recovery
            # and meridian-flip guiding restart).
            self._app_state = "Guiding"
            ra = float(ev.get("RADistanceRaw", 0)) * self.pixel_scale
            dec = float(ev.get("DECDistanceRaw", 0)) * self.pixel_scale
            self._snr = float(ev.get("SNR", 0))
            sample = {"t": time.time(), "ra": ra, "dec": dec}
            self._samples.append(sample)
            bus.publish("guide", **self.stats().__dict__)
        elif kind in ("StartGuiding", "GuidingDithered"):
            # Explicit guiding-start / dither-resume state changes PHD2 sends
            # after the initial burst. Reach "Guiding" without waiting for the
            # first GuideStep.
            self._app_state = "Guiding"
            bus.publish("guide", **self.stats().__dict__)
        elif kind == "Paused":
            self._app_state = "Paused"
            bus.publish("guide", **self.stats().__dict__)
        elif kind == "Resumed":
            self._app_state = "Guiding"
            bus.publish("guide", **self.stats().__dict__)
        elif kind == "LoopingExposures":
            self._app_state = "Looping"
        elif kind == "LoopingExposuresStopped":
            self._app_state = "Stopped"
            bus.publish("guide", **self.stats().__dict__)
        elif kind == "AppState":
            self._app_state = ev.get("State", "Stopped")
        elif kind == "SettleDone":
            self._settle_error = ev.get("Error") or None
            if ev.get("Status", 0) != 0 and not self._settle_error:
                self._settle_error = "settle failed"
            self._settle_done.set()
        elif kind == "CalibrationFailed":
            # P1-6: surface a calibration failure immediately as a friendly
            # message instead of letting start_guiding sit out its ~90s settle
            # timeout. Unblock any settle waiter with the reason.
            reason = ev.get("Reason") or "calibration failed"
            self._settle_error = f"calibration failed: {reason}"
            bus.log("error", f"PHD2 {self._settle_error}", "guide")
            self._settle_done.set()
        elif kind == "Alert":
            # P1-6: PHD2 raises Alert for many operational problems (calibration
            # could not start, star saturated, etc.). An error/warning Alert
            # arriving while we're waiting to settle is treated as a settle
            # failure so the caller gets the real reason, not a timeout.
            msg = ev.get("Msg") or "PHD2 alert"
            atype = str(ev.get("Type", "info")).lower()
            level = "error" if atype in ("error", "alert") else "warning"
            bus.log(level, f"PHD2 alert: {msg}", "guide")
            if level == "error" and not self._settle_done.is_set():
                self._settle_error = msg
                self._settle_done.set()
        elif kind in ("GuidingStopped", "StarLost"):
            if kind == "GuidingStopped":
                self._app_state = "Stopped"
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

    async def flip_calibration(self) -> bool:
        """Flip PHD2's calibration data for a meridian flip (P1-6).

        After a GEM flips to the far side of the pier the guide directions are
        reversed; PHD2's ``flip_calibration`` RPC mirrors the stored calibration
        so guiding does not run the mount away from the star. ``hub.meridian_flip``
        calls this between stopping and restarting guiding.

        Returns True on success. A guider not connected, or a PHD2 with no usable
        calibration to flip, is a logged NO-OP returning False (never raises) so
        the meridian flip still completes — guiding then re-calibrates on restart.
        """
        if not self.connected or self._writer is None:
            bus.log("warning", "PHD2 not connected; cannot flip calibration; "
                    "will rely on a fresh calibration after the flip", "guide")
            return False
        try:
            await self._rpc("flip_calibration", timeout=30)
            bus.log("info", "PHD2 calibration flipped for the meridian flip",
                    "guide")
            return True
        except Exception as e:
            # e.g. PHD2 has no calibration to flip, or is mid-calibration. Warn
            # and let the flip proceed; a fresh calibration on restart is safe.
            bus.log("warning", f"PHD2 flip_calibration failed ({e}); will rely "
                    "on a fresh calibration after the flip", "guide")
            return False

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
