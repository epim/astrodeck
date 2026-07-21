"""NativeCamera: the vendor-blind engine that turns a CameraAdapter into a
Camera. Owns the exposure lifecycle (generalized from alpaca.py:expose), buffer
assembly, cooling, ROI/binning, and cancellation — written once for every
brand. Every adapter hook runs under asyncio.to_thread + a per-device lock."""
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
        await asyncio.to_thread(self._a.open, self._index)
        caps = self._a.capabilities()
        self._caps = caps
        self.sensor_width = caps.sensor_width
        self.sensor_height = caps.sensor_height
        self.pixel_size_um = caps.pixel_size_um
        self.max_gain = caps.gain_range[1]
        self.can_cool = caps.has_cooler
        self.has_dew_heater = caps.has_dew_heater
        self.bayer_pattern = caps.bayer_pattern

    async def disconnect(self) -> None:
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
        """Raw little-endian sensor bytes -> 2-D uint16 [height, width]. 8-bit
        readout is promoted to uint16 so the frame dtype is uniform."""
        w, h = roi.w // roi.bin, roi.h // roi.bin
        dt = np.uint8 if caps.bit_depth <= 8 else "<u2"
        arr = np.frombuffer(raw, dtype=dt, count=w * h)
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
