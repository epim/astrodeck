"""Simulator backend.

A coherent virtual rig: the sim camera renders a synthetic star field based on
where the sim mount points, with star sharpness driven by the sim focuser's
distance from true focus. This makes every higher-level routine (autofocus,
plate solving, goto-centering, sequencing, guiding) genuinely exercisable
with zero hardware.
"""
from __future__ import annotations

import asyncio
import math
import time

import numpy as np

from .base import (
    Camera,
    CameraFrame,
    FilterWheel,
    Focuser,
    PierSide,
    Switch,
    SwitchPort,
    Telescope,
)


class SimRig:
    """Shared state tying the simulated devices together."""

    def __init__(self) -> None:
        self.ra_hours = 5.59          # roughly M42 for a pretty default
        self.dec_deg = -5.39
        self.tracking = True
        self.parked = False
        self.focuser_pos = 19_200
        self.best_focus = 20_000      # the autofocus routine must find this
        self.filter_slot = 0
        self.pointing_error_deg = 0.04  # goto lands slightly off until synced
        self.sensor_temp = -9.8


class SimCamera(Camera):
    """Renders stars from a deterministic per-sky-tile catalog."""

    FOV_DEG = 1.4  # diagonal field of view at bin 1

    def __init__(self, rig: SimRig, name: str = "Sim Camera 533MM"):
        super().__init__(name)
        self.rig = rig
        self.sensor_width = 1216
        self.sensor_height = 912
        self.pixel_size_um = 3.76
        self.can_cool = True
        self.has_dew_heater = True
        self.bayer_pattern = None
        self._dew_power = 0
        self._cooler_on = False
        self._target_c = -10.0
        self._abort = asyncio.Event()

    async def connect(self) -> None:
        await asyncio.sleep(0.1)
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def abort_exposure(self) -> None:
        self._abort.set()

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        self._cooler_on = on
        if target_c is not None:
            self._target_c = target_c

    async def get_temperature(self) -> float | None:
        return self.rig.sensor_temp if self._cooler_on else 12.3

    async def set_dew_heater(self, power: int) -> None:
        self._dew_power = max(0, min(100, int(power)))

    async def expose(self, seconds: float, gain: int, offset: int, binning: int = 1,
                     light: bool = True, save: bool = False,
                     target: str = "") -> CameraFrame:
        self._abort.clear()
        # Wait out the exposure in small slices so aborts are responsive.
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._abort.is_set():
                raise asyncio.CancelledError("exposure aborted")
            await asyncio.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        data = self._render(seconds, gain, offset, binning, light)
        return CameraFrame(
            data=data,
            exposure_s=seconds,
            gain=gain,
            offset=offset,
            binning=binning,
            bayer_pattern=None,
            temperature_c=await self.get_temperature(),
            timestamp=time.time(),
        )

    # ---------------------------------------------------------------- render

    def _stars_for_tile(self, tx: int, ty: int) -> np.ndarray:
        """Deterministic stars for one 0.5°x0.5° sky tile: (ra_deg, dec_deg, mag)."""
        rng = np.random.default_rng(abs(hash((tx, ty))) % (2**32))
        n = rng.integers(12, 30)
        ras = (tx + rng.random(n)) * 0.5
        decs = (ty + rng.random(n)) * 0.5 - 90.0
        mags = 5.0 + 6.0 * rng.power(2.2, n)  # many dim, few bright
        return np.column_stack([ras, decs, mags])

    def _render(self, seconds: float, gain: int, offset: int, binning: int,
                light: bool) -> np.ndarray:
        h, w = self.sensor_height // binning, self.sensor_width // binning
        rng = np.random.default_rng()
        bias = 100.0 + offset * 2.0
        read_noise = 3.0 + gain / 80.0
        img = rng.normal(bias, read_noise, (h, w))

        if light and not self.rig.parked:
            img += self._render_stars(h, w, seconds, gain, binning, rng)
            # gentle sky background gradient
            yy = np.linspace(0, 1, h)[:, None]
            img += (8.0 + 14.0 * yy) * seconds * (1 + gain / 200.0)

        return np.clip(img, 0, 65535).astype(np.uint16)

    def _render_stars(self, h: int, w: int, seconds: float, gain: int,
                      binning: int, rng: np.random.Generator) -> np.ndarray:
        field = np.zeros((h, w), dtype=np.float64)
        ra_deg = self.rig.ra_hours * 15.0
        dec_deg = self.rig.dec_deg
        fov = self.FOV_DEG
        half = fov / 2.0
        cosd = max(0.05, math.cos(math.radians(dec_deg)))

        # focus quality: sigma grows with distance from best focus.
        # The 1.8 floor is seeing: even perfect focus never beats the sky.
        defocus = abs(self.rig.focuser_pos - self.rig.best_focus)
        sigma = (1.8 + defocus / 700.0) / binning
        seeing_jitter = rng.normal(0, 0.05)
        sigma = max(1.2 / binning, sigma + seeing_jitter)

        # narrowband filters cut star flux
        flux_scale = [1.0, 0.8, 0.8, 0.8, 0.12, 0.10, 0.10][self.rig.filter_slot % 7]

        tx0 = int((ra_deg - half / cosd) / 0.5) - 1
        tx1 = int((ra_deg + half / cosd) / 0.5) + 1
        ty0 = int((dec_deg + 90 - half) / 0.5) - 1
        ty1 = int((dec_deg + 90 + half) / 0.5) + 1
        scale_px = w / fov  # px per degree (x)

        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                for sra, sdec, mag in self._stars_for_tile(tx, ty):
                    dx = (sra - ra_deg) * cosd
                    dy = sdec - dec_deg
                    px = w / 2 + dx * scale_px
                    py = h / 2 - dy * scale_px
                    if not (-20 < px < w + 20 and -20 < py < h + 20):
                        continue
                    flux = 10 ** (-0.4 * (mag - 16.0)) * seconds * (1 + gain / 100.0)
                    flux *= flux_scale * binning * binning
                    self._add_star(field, px, py, flux, sigma)

        field += rng.poisson(np.clip(field, 0, None)) - field  # shot noise
        return field

    @staticmethod
    def _add_star(field: np.ndarray, px: float, py: float, flux: float,
                  sigma: float) -> None:
        h, w = field.shape
        r = int(max(4, sigma * 4))
        x0, x1 = max(0, int(px) - r), min(w, int(px) + r + 1)
        y0, y1 = max(0, int(py) - r), min(h, int(py) + r + 1)
        if x0 >= x1 or y0 >= y1:
            return
        xs = np.arange(x0, x1) - px
        ys = (np.arange(y0, y1) - py)[:, None]
        psf = np.exp(-(xs**2 + ys**2) / (2 * sigma**2))
        field[y0:y1, x0:x1] += flux * psf / (2 * math.pi * sigma**2)


class SimTelescope(Telescope):
    SLEW_RATE_DEG_S = 4.0

    def __init__(self, rig: SimRig, name: str = "Sim Mount EQ6-R"):
        super().__init__(name)
        self.rig = rig
        self._slewing = False
        self._move_rates = {"ra": 0.0, "dec": 0.0}
        self._move_task: asyncio.Task | None = None

    async def connect(self) -> None:
        await asyncio.sleep(0.1)
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self) -> tuple[float, float]:
        return self.rig.ra_hours, self.rig.dec_deg

    async def is_slewing(self) -> bool:
        return self._slewing

    async def slew(self, ra_hours: float, dec_deg: float) -> None:
        if self.rig.parked:
            raise RuntimeError("mount is parked")
        self._slewing = True
        try:
            # Land near the target with a small pointing error (until synced).
            err = self.rig.pointing_error_deg
            tgt_ra = ra_hours + err / 15.0 * 0.7
            tgt_dec = dec_deg + err * 0.7
            dist = math.hypot((tgt_ra - self.rig.ra_hours) * 15, tgt_dec - self.rig.dec_deg)
            duration = min(8.0, max(0.5, dist / self.SLEW_RATE_DEG_S))
            steps = max(2, int(duration / 0.2))
            ra0, dec0 = self.rig.ra_hours, self.rig.dec_deg
            for i in range(1, steps + 1):
                await asyncio.sleep(duration / steps)
                f = i / steps
                self.rig.ra_hours = ra0 + (tgt_ra - ra0) * f
                self.rig.dec_deg = dec0 + (tgt_dec - dec0) * f
        finally:
            self._slewing = False

    async def sync(self, ra_hours: float, dec_deg: float) -> None:
        self.rig.ra_hours = ra_hours
        self.rig.dec_deg = dec_deg
        self.rig.pointing_error_deg = 0.003  # synced: pointing is now tight

    async def set_tracking(self, on: bool) -> None:
        self.rig.tracking = on

    async def get_tracking(self) -> bool:
        return self.rig.tracking

    async def park(self) -> None:
        await self.slew(0.0, 89.5)
        self.rig.parked = True
        self.rig.tracking = False

    async def unpark(self) -> None:
        self.rig.parked = False

    async def is_parked(self) -> bool:
        return self.rig.parked

    async def pier_side(self) -> PierSide:
        return PierSide.WEST

    async def pulse_guide(self, direction: str, ms: int) -> None:
        nudge = ms / 1000.0 * 0.0002
        if direction in ("east", "west"):
            self.rig.ra_hours += nudge if direction == "east" else -nudge
        else:
            self.rig.dec_deg += nudge if direction == "north" else -nudge
        await asyncio.sleep(ms / 1000.0)

    async def move_axis(self, axis: str, rate_deg_s: float) -> None:
        self._move_rates[axis] = rate_deg_s
        if rate_deg_s != 0 and (self._move_task is None or self._move_task.done()):
            self._move_task = asyncio.create_task(self._move_loop())

    async def _move_loop(self) -> None:
        while any(r != 0 for r in self._move_rates.values()):
            self.rig.ra_hours += self._move_rates["ra"] * 0.1 / 15.0
            self.rig.dec_deg = min(90, max(-90, self.rig.dec_deg + self._move_rates["dec"] * 0.1))
            await asyncio.sleep(0.1)


class SimFocuser(Focuser):
    MOVE_RATE = 4000  # steps/s

    def __init__(self, rig: SimRig, name: str = "Sim Focuser EAF"):
        super().__init__(name)
        self.rig = rig
        self.max_position = 60_000
        self.step_size_um = 1.2
        self._halt = asyncio.Event()

    async def connect(self) -> None:
        await asyncio.sleep(0.05)
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self) -> int:
        return self.rig.focuser_pos

    async def get_temperature(self) -> float | None:
        return 4.2

    async def halt(self) -> None:
        self._halt.set()

    async def move_to(self, position: int) -> None:
        position = max(0, min(self.max_position, position))
        self._halt.clear()
        step = 200 if position > self.rig.focuser_pos else -200
        while self.rig.focuser_pos != position and not self._halt.is_set():
            remaining = position - self.rig.focuser_pos
            self.rig.focuser_pos += step if abs(remaining) >= abs(step) else remaining
            await asyncio.sleep(abs(step) / self.MOVE_RATE)


class SimFilterWheel(FilterWheel):
    def __init__(self, rig: SimRig, name: str = "Sim Filter Wheel 7x36"):
        super().__init__(name)
        self.rig = rig
        self.filter_names = ["L", "R", "G", "B", "Ha", "OIII", "SII"]
        self.filter_offsets = [0, 12, 10, 15, 120, 110, 115]  # focuser steps

    async def connect(self) -> None:
        await asyncio.sleep(0.05)
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self) -> int:
        return self.rig.filter_slot

    async def set_position(self, slot: int) -> None:
        moves = abs(slot - self.rig.filter_slot)
        await asyncio.sleep(0.4 * min(moves, len(self.filter_names) - moves or 1))
        self.rig.filter_slot = slot


class SimSwitch(Switch):
    """Mimics a Pegasus UPBv2-style power box."""

    def __init__(self, name: str = "Sim PowerBox UPBv2"):
        super().__init__(name)
        self._ports = [
            SwitchPort(0, "Mount 12V", True, True, 1),
            SwitchPort(1, "Camera 12V", True, True, 1),
            SwitchPort(2, "Focuser 12V", True, True, 0),
            SwitchPort(3, "Accessory 12V", True, True, 0),
            SwitchPort(4, "Dew Heater A", True, False, 35, 0, 100, "%"),
            SwitchPort(5, "Dew Heater B", True, False, 0, 0, 100, "%"),
            SwitchPort(6, "Input Voltage", False, False, 13.7, 0, 15, "V"),
            SwitchPort(7, "Total Current", False, False, 2.4, 0, 10, "A"),
        ]

    async def connect(self) -> None:
        await asyncio.sleep(0.05)
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_ports(self) -> list[SwitchPort]:
        # readings wiggle a little
        self._ports[6].value = round(13.6 + 0.2 * math.sin(time.time() / 7), 2)
        self._ports[7].value = round(2.2 + 0.4 * abs(math.sin(time.time() / 11)), 2)
        return list(self._ports)

    async def set_port(self, port_id: int, value: float) -> None:
        port = self._ports[port_id]
        if not port.can_write:
            raise RuntimeError(f"port {port.name} is read-only")
        port.value = max(port.min, min(port.max, value))


def build_sim_rig() -> dict[str, object]:
    """One coherent simulated observatory."""
    rig = SimRig()
    return {
        "camera": SimCamera(rig),
        "guide_camera": SimCamera(rig, name="Sim Guide Cam 220MM"),
        "telescope": SimTelescope(rig),
        "focuser": SimFocuser(rig),
        "filterwheel": SimFilterWheel(rig),
        "switch": SimSwitch(),
        "_rig": rig,
    }
