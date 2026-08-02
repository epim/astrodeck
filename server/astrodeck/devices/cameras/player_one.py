"""Player One camera adapter — maps the Player One SDK onto the CameraAdapter
waist. Full imaging train for the Poseidon-M Pro: cooling, dew heater, ROI, and
the two read-noise controls this camera earns its keep on —

  * LRN (Low Read Noise) sampling mode — a DISCRETE mode (Normal <-> LowNoise),
    surfaced as read_modes[] and applied per-exposure via set_read_mode;
  * HCG (High Conversion Gain) — engages automatically at gain >= 125, dropping
    read noise from ~3.96e- toward ~1.36e-; surfaced as hcg_threshold_gain=125
    (informational, not a toggle).

Vendor-specific logic lives here only; the engine owns the exposure/cooling loop."""
from __future__ import annotations

from ..base import DeviceError
from .adapter import ROI, CameraAdapter, CameraCapabilities
from . import registry
from .player_one_sdk import (
    PoaProperty, make_player_one, POA_EXPOSURE, POA_GAIN, POA_OFFSET,
    POA_TARGET_TEMP, POA_COOLER, POA_COOLER_POWER, POA_TEMPERATURE,
    POA_HEATER_POWER, POA_RAW16,
)

__all__ = ["PlayerOneAdapter", "PoaProperty"]

#: gain at/above which the IMX571 conversion gain lowers read noise (HCG).
HCG_THRESHOLD_GAIN = 125

#: POASetConfig's non-float value is a 32-bit c_int; clamp exposure microseconds
#: so a very long exposure can't OverflowError at the ctypes boundary (the SDK
#: caps at 2_000_000_000 us / 2000 s of its own accord). 2**31-1 us ~= 2147 s.
_MAX_EXPOSURE_US = 2_147_483_647


class PlayerOneAdapter(CameraAdapter):
    def __init__(self, sdk=None, index: int = 0):
        self._sdk = sdk if sdk is not None else make_player_one()
        self._index = index
        self._cam_id = 0
        self._prop: PoaProperty | None = None
        self._gain_max = 0
        self._offset_max = 0
        self._modes: tuple[str, ...] = ()
        self._egain = 0.0
        self._nbytes = 0
        self._applied: ROI | None = None

    def _basic(self) -> PoaProperty:
        # count() first: enumerate before get_properties(index) (at-scope lesson).
        self._sdk.count()
        if self._prop is None:
            self._prop = self._sdk.get_properties(self._index)
            self._cam_id = self._prop.camera_id
        return self._prop

    def capabilities(self) -> CameraCapabilities:
        p = self._basic()
        return CameraCapabilities(
            sensor_width=p.width, sensor_height=p.height,
            pixel_size_um=p.pixel_size_um, bit_depth=p.bit_depth,
            bayer_pattern=p.bayer, gain_range=(0, self._gain_max),
            offset_range=(0, self._offset_max), bin_modes=tuple(range(1, p.max_bin + 1)),
            roi_supported=True, has_cooler=p.is_cooled, has_dew_heater=True,
            max_adu=65535, read_modes=self._modes,
            hcg_threshold_gain=HCG_THRESHOLD_GAIN,
            extra={"egain": self._egain} if self._egain else {})

    def open(self, index: int) -> None:
        self._index = index
        p = self._basic()
        self._sdk.open(p.camera_id)
        # ranges + sensor modes (LRN: 'Normal'/'Low Noise') + egain all require
        # the camera OPEN (verified at-scope 2026-07-21).
        self._gain_max = self._sdk.config_range(self._cam_id, POA_GAIN)[1]
        self._offset_max = self._sdk.config_range(self._cam_id, POA_OFFSET)[1]
        self._modes = tuple(self._sdk.sensor_modes(self._cam_id))
        try:
            self._egain = float(self._sdk.get_egain(self._cam_id))
        except Exception:  # noqa: BLE001 - egain optional
            self._egain = 0.0

    def close(self) -> None:
        if self._prop is not None:
            self._sdk.close(self._cam_id)

    def start_exposure(self, *, seconds: float, gain: int, offset: int,
                       roi: ROI, light: bool) -> None:
        us = min(int(round(seconds * 1e6)), _MAX_EXPOSURE_US)
        self._sdk.set_config(self._cam_id, POA_EXPOSURE, us)
        self._sdk.set_config(self._cam_id, POA_GAIN, int(gain))
        self._sdk.set_config(self._cam_id, POA_OFFSET, int(offset))
        w, h = roi.w // roi.bin, roi.h // roi.bin
        self._sdk.set_image_format(self._cam_id, w, h, roi.bin, POA_RAW16)
        self._place(roi)
        # THE DOWNLOAD IS SIZED FROM WHAT THE CAMERA APPLIED, NOT WHAT WE ASKED.
        # Sizing it from the request is what makes a mismatch invisible: the
        # buffer is ours, POAGetImageData fills the front of it and returns OK
        # for any buffer that is big ENOUGH, and read_frame hands back exactly
        # len == request. So the length can never disagree with the request no
        # matter what the sensor did, and the engine lays rows of the applied
        # width out at the requested one — the sheared, tiled picture of
        # 2026-07-31. Reading the geometry back is the only thing that sees it.
        self._applied = self._read_back(roi)
        aw, ah = self._binned(self._applied or roi)
        self._nbytes = aw * ah * 2
        self._sdk.start_exposure(self._cam_id, True)

    @staticmethod
    def _binned(r: ROI) -> tuple[int, int]:
        return r.w // r.bin, r.h // r.bin

    def _place(self, roi: ROI) -> None:
        """Put the subframe where the caller asked. set_image_format parks the
        origin at (0, 0), so without this an (x, y) request is discarded and the
        frame is of a different patch of sky than the one requested."""
        sp = getattr(self._sdk, "set_start_pos", None)
        if sp is None:
            if roi.x or roi.y:
                raise DeviceError(
                    f"this Player One SDK cannot place a subframe, so the "
                    f"requested origin ({roi.x}, {roi.y}) could not be applied; "
                    "the frame would be of a different part of the sensor than "
                    "requested")
            return
        sp(self._cam_id, roi.x // roi.bin, roi.y // roi.bin)

    def _read_back(self, roi: ROI) -> ROI | None:
        """What POAGetImageSize/Bin/StartPos say the camera settled on, in the
        request's unbinned units. None when the injected SDK cannot be asked."""
        get = getattr(self._sdk, "get_roi", None)
        if get is None:
            return None
        w, h, b, fmt = get(self._cam_id)
        if fmt != POA_RAW16:
            # _shape decodes little-endian uint16 unconditionally. A RAW8 frame
            # decoded that way pairs adjacent pixels into one — refuse it here
            # rather than deliver a half-width picture of the same sky.
            raise DeviceError(
                f"camera applied image format {fmt}, not RAW16 ({POA_RAW16}); "
                "the download would be decoded as 16-bit and come out wrong")
        b = b or roi.bin
        x, y = 0, 0
        gsp = getattr(self._sdk, "get_start_pos", None)
        if gsp is not None:
            x, y = gsp(self._cam_id)
        return ROI(x=x * b, y=y * b, w=w * b, h=h * b, bin=b)

    def applied_roi(self) -> ROI | None:
        return self._applied

    def image_ready(self) -> bool:
        return self._sdk.image_ready(self._cam_id)

    def read_frame(self) -> bytes:
        return self._sdk.get_image_data(self._cam_id, self._nbytes)

    def abort(self) -> None:
        self._sdk.stop_exposure(self._cam_id)

    # --- read modes (LRN) -------------------------------------------------
    def set_read_mode(self, mode: str) -> None:
        self._sdk.set_sensor_mode(self._cam_id, mode)

    # --- cooling / dew ----------------------------------------------------
    def set_target_temp(self, celsius: float) -> None:
        self._sdk.set_config(self._cam_id, POA_TARGET_TEMP, float(celsius))

    def set_cooler(self, on: bool) -> None:
        self._sdk.set_config(self._cam_id, POA_COOLER, bool(on))

    def get_temperature(self) -> float | None:
        try:
            return float(self._sdk.get_config(self._cam_id, POA_TEMPERATURE))
        except Exception:  # noqa: BLE001
            return None

    def get_cooler_power(self) -> int | None:
        try:
            return int(self._sdk.get_config(self._cam_id, POA_COOLER_POWER))
        except Exception:  # noqa: BLE001
            return None

    def set_dew_heater(self, power: int) -> None:
        self._sdk.set_config(self._cam_id, POA_HEATER_POWER, int(power))


try:
    registry.register_adapter("player-one", PlayerOneAdapter)
except ValueError:
    pass
