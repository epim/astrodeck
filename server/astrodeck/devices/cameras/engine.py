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


def _egain_from_caps(caps) -> float | None:
    """e-/ADU from the adapter's capability extras (Player One + ZWO both set
    extra['egain']); None when unavailable."""
    eg = caps.extra.get("egain") if (caps and getattr(caps, "extra", None)) else None
    return float(eg) if eg else None


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
        applied = await asyncio.to_thread(self._a.applied_roi)
        roi = self._layout_roi(roi, applied)
        data = self._shape(raw, roi, caps)
        temp = await self.get_temperature()
        return CameraFrame(
            data=data, exposure_s=seconds, gain=gain, offset=offset,
            binning=roi.bin, bayer_pattern=caps.bayer_pattern,
            temperature_c=temp, timestamp=time.time(),
            full_well=caps.max_adu, data_is_linear=True,
            egain_e_per_adu=_egain_from_caps(caps))

    @staticmethod
    def _geometry(r: ROI) -> tuple[int, int, int, int, int]:
        """The numbers that decide the layout: everything in binned pixels."""
        return (r.w // r.bin, r.h // r.bin, r.bin, r.x // r.bin, r.y // r.bin)

    @classmethod
    def _layout_roi(cls, requested: ROI, applied: ROI | None) -> ROI:
        """The geometry the download must be laid out at.

        The request is what we asked the sensor for; ``applied`` is what the
        sensor says it did. Where they differ, the sensor is right — the buffer
        is full of ITS rows, and laying them out at our width is what shears and
        repeats the picture. The disagreement is announced rather than quietly
        absorbed, because a frame that is not the requested geometry is also not
        the requested field of view: the FITS header, the plate solve, the star
        marks and the mosaic tiling are all placed from these numbers."""
        if applied is None or cls._geometry(applied) == cls._geometry(requested):
            return requested
        from ...events import bus
        aw, ah, ab, ax, ay = cls._geometry(applied)
        rw, rh, rb, rx, ry = cls._geometry(requested)
        bus.log("warning",
                f"camera applied {aw}x{ah} bin {ab} at ({ax},{ay}) after being "
                f"asked for {rw}x{rh} bin {rb} at ({rx},{ry}); the frame is laid "
                "out at the size the camera reports, so the picture is intact, "
                "but it is not the field of view that was requested.", "camera")
        return applied

    @staticmethod
    def _shape(raw: bytes, roi: ROI, caps) -> np.ndarray:
        """Raw sensor bytes -> 2-D uint16 [height, width]. The adapters ALWAYS
        download RAW16 (little-endian, w*h*2 bytes) regardless of the sensor's ADC
        depth — a <=8-bit sensor still arrives in a 16-bit container — so we always
        decode as little-endian uint16 (``.astype`` normalizes byte order to native
        uint16). If a future adapter downloads RAW8 it must signal the format so
        this decode can match; today none do (both request RAW16)."""
        w, h = roi.w // roi.bin, roi.h // roi.bin
        want = w * h * 2

        # THE ROW LENGTH MUST BE THE ONE WE THINK IT IS — AND THIS CHECK CANNOT
        # BE THE ONE THAT ESTABLISHES IT.
        #
        # `count=w*h` silently takes a PREFIX of the buffer. Lay rows of one
        # width out at another and every row starts a constant offset into the
        # last: a picture sheared diagonally, wrapping into repeats, returned
        # with no error and looking enough like an image that the preview, the
        # star detector and the FITS writer all accept it. That is the reported
        # 2026-07-31 artefact ("distorted and stretched and shown at an angle.
        # And tiled"), rendered from a real sky frame in
        # tests/test_camera_roi_shear.py.
        #
        # But the LENGTH cannot detect it, and this is worth being blunt about
        # because it looks like it can. Both vendor adapters allocate the
        # download buffer themselves and hand its size to the SDK; the SDK fills
        # the front of it and succeeds for any buffer that is big enough, and
        # read_frame returns the whole allocation. So `len(raw)` equals the size
        # those adapters computed, by construction, whatever the sensor actually
        # did — a wrong row length arrives at exactly the right byte count. The
        # geometry read-back in the adapters (`applied_roi`) is what catches it;
        # this check only catches an adapter whose buffer size is not its own.
        #
        # It still earns its place: a short buffer is refused outright (where
        # np.frombuffer would raise a ValueError naming neither number), and a
        # LONG one is used — refusing a frame over trailing padding would be
        # worse than the bug — but announced with every number needed to name
        # the culprit.
        if len(raw) < want:
            raise DeviceError(
                f"camera returned {len(raw)} bytes but a {w}x{h} 16-bit frame "
                f"needs {want} (ROI {roi.w}x{roi.h} bin {roi.bin}). Reshaping "
                "it would produce a sheared, tiled image that looks like a real "
                "picture of the sky.")
        if len(raw) > want:
            from ...events import bus
            bus.log("warning",
                    f"camera returned {len(raw)} bytes for a {w}x{h} 16-bit "
                    f"frame that needs {want} (ROI {roi.w}x{roi.h} bin "
                    f"{roi.bin}); using the first {want} and the rest is "
                    "unexplained. If the image looks sheared or repeated, the "
                    "sensor's real row length is not the one requested.",
                    "camera")
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
