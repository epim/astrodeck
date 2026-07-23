"""NativeCamera: the vendor-blind engine that turns a CameraAdapter into a
Camera. Owns the exposure lifecycle (generalized from alpaca.py:expose), buffer
assembly, cooling, ROI/binning, and cancellation — written once for every brand.

Concurrency: adapter hooks run on the threadpool via ``asyncio.to_thread``. The
CONFIG/write hooks (``start_exposure``, ``set_read_mode``, ``set_cooler``,
``set_target_temp``, ``set_dew_heater``) are additionally serialized under a
per-device lock via ``_run``. ``abort`` and the exposure-poll reads
(``image_ready``, ``read_frame``) run WITHOUT the lock on purpose, so a cancel
can interrupt a blocked ``read_frame``; the Player One SDK binding is internally
thread-safe for its per-call argtype selection (player_one_sdk.PlayerOneSdk)."""
from __future__ import annotations

import asyncio
import time

import numpy as np

from ..base import Camera, CameraFrame, DeviceError
from .adapter import ROI, CameraAdapter


class NativeCamera(Camera):
    #: extra wall-clock beyond the requested exposure before imageready is a
    #: timeout (download + USB latency). Matches the Alpaca margin family.
    EXPOSURE_POLL_MARGIN_S = 15.0

    def __init__(self, adapter: CameraAdapter, index: int = 0, name: str = ""):
        super().__init__(name or "Native Camera")
        self._a = adapter
        self._index = index
        self._lock = asyncio.Lock()
        self._caps = None
        self._exposing = False

    async def _run(self, fn, *a):
        """Run a sync adapter hook off the event loop, serialized per device."""
        async with self._lock:
            return await asyncio.to_thread(fn, *a)

    async def connect(self) -> None:
        # Idempotent: the orchestrator connects via get_device AND the hub's
        # _apply_connect_result re-connects (its documented contract). A second
        # SDK open() would error, so no-op when already connected.
        if self.connected:
            return
        await asyncio.to_thread(self._a.open, self._index)
        caps = self._a.capabilities()
        self._caps = caps
        self.sensor_width = caps.sensor_width
        self.sensor_height = caps.sensor_height
        self.pixel_size_um = caps.pixel_size_um
        self.max_gain = caps.gain_range[1]
        if caps.bin_modes:
            self.max_bin = max(caps.bin_modes)  # UX-27: real supported ceiling
        self.can_cool = caps.has_cooler
        self.has_dew_heater = caps.has_dew_heater
        self.bayer_pattern = caps.bayer_pattern
        # photometry/SNR design §1.2/Task 7: brand-unique egain (e-/ADU) rides in
        # caps.extra; absent/falsy on brands that don't report it (stays 0.0/unknown).
        self.egain = float(caps.extra.get("egain", 0.0) or 0.0)
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False
        try:
            await asyncio.to_thread(self._a.close)
        except Exception:  # noqa: BLE001 - teardown is best-effort
            pass

    async def expose(self, seconds: float, gain: int, offset: int, binning: int = 1,
                     light: bool = True, save: bool = False,
                     target: str = "", *, read_mode: str | None = None,
                     roi: ROI | None = None) -> CameraFrame:
        caps = self._caps
        if caps is None:
            raise DeviceError("camera not connected")
        if roi is None:
            roi = ROI(x=0, y=0, w=caps.sensor_width, h=caps.sensor_height,
                      bin=binning)
        if read_mode is not None:
            await self._run(lambda: self._a.set_read_mode(read_mode))
        await self._run(lambda: self._a.start_exposure(
            seconds=seconds, gain=gain, offset=offset, roi=roi, light=light))
        self._exposing = True
        deadline = time.monotonic() + seconds + self.EXPOSURE_POLL_MARGIN_S
        try:
            while not await asyncio.to_thread(self._a.image_ready):
                if time.monotonic() > deadline:
                    raise DeviceError("exposure imageready timeout")
                await asyncio.sleep(min(0.5, max(0.05, seconds / 20)))
        except asyncio.CancelledError:
            await asyncio.to_thread(self._a.abort)
            raise
        finally:
            self._exposing = False
        raw = await asyncio.to_thread(self._a.read_frame)
        data = self._shape(raw, roi, caps)
        temp = await self.get_temperature()
        return CameraFrame(
            data=data, exposure_s=seconds, gain=gain, offset=offset,
            binning=binning, bayer_pattern=caps.bayer_pattern,
            temperature_c=temp, timestamp=time.time(),
            full_well=caps.max_adu, data_is_linear=True)

    @staticmethod
    def _shape(raw: bytes, roi: ROI, caps) -> np.ndarray:
        """Raw sensor bytes -> 2-D uint16 [height, width]. The adapters ALWAYS
        download RAW16 (little-endian, w*h*2 bytes) regardless of the sensor's ADC
        depth — a <=8-bit sensor still arrives in a 16-bit container — so we always
        decode as little-endian uint16 (``.astype`` normalizes byte order to native
        uint16). If a future adapter downloads RAW8 it must signal the format so
        this decode can match; today none do (both request RAW16)."""
        w, h = roi.w // roi.bin, roi.h // roi.bin
        arr = np.frombuffer(raw, dtype="<u2", count=w * h)
        return arr.reshape((h, w)).astype(np.uint16)

    async def abort_exposure(self) -> None:
        await asyncio.to_thread(self._a.abort)

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        if not self._caps or not self._caps.has_cooler:
            raise DeviceError(f"{self.name} has no cooler")
        if target_c is not None:
            await self._run(lambda: self._a.set_target_temp(target_c))
        await self._run(lambda: self._a.set_cooler(on))

    async def get_temperature(self) -> float | None:
        return await asyncio.to_thread(self._a.get_temperature)

    async def cooler_power(self) -> int | None:
        return await asyncio.to_thread(self._a.get_cooler_power)

    async def set_dew_heater(self, power: int) -> None:
        if not self._caps or not self._caps.has_dew_heater:
            raise DeviceError(f"{self.name} has no dew heater")
        await self._run(lambda: self._a.set_dew_heater(int(power)))
