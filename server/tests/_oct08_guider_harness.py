# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Shared doubles for the Oct-08 guider-safety tests (#848, #849, #856).

Only the Rust ENGINE is replaced here (``ScriptedEngine``): the host code under
test -- ``NativeGuider._guide_loop``, ``_judge_frame``, ``_pulse``,
``_build_engine_config``, ``start_guiding``'s probation wait -- is the real
code. The mount double has the real ``pulse_guide(direction, ms)`` signature
and publishes a 1000 ms ``max_pulse_ms``, so the axis limits come from a real
``_build_engine_config`` (never assigned by a test).

Rig figures (DESIGN-P1 section 3): image scale 5.5 arcsec/px (known), RA rate
``x_rate = 0.002735`` px/ms walked at dec 0 (so one capped RA pulse moves the
star ``2.735 cos(dec)`` px, the AM5N's 15.04 arcsec/s at 5.5 arcsec/px), Dec
rate ``y_rate = 0.001367`` px/ms (1.367 px per capped pulse).
"""
from __future__ import annotations

import asyncio
import json
import math
import uuid

import numpy as np

from astrodeck.events import bus
from astrodeck.guide import native
from astrodeck.guide.native import NativeGuider

X_RATE = 0.002735
Y_RATE = 0.001367
CAP_MS = 1000


def profile_id(tag: str) -> str:
    return f"test-oct08-{tag}-{uuid.uuid4().hex[:8]}"


def cal_dict(*, x_rate=X_RATE, y_rate=Y_RATE, dec_rad=0.0, pier="east",
             scale=5.5) -> dict:
    """A calibration dict in the shape ``dump_calibration`` returns (plus the
    ``image_scale_arcsec`` sidecar the persisted file carries). Orthogonal
    axes; a made-up declination, not a measurement of anything real."""
    return {"x_rate": x_rate, "y_rate": y_rate, "x_angle": 0.0,
            "y_angle": math.pi / 2, "y_angle_error": 0.0,
            "declination": dec_rad, "pier_side": pier,
            "ra_parity": "unknown", "dec_parity": "unknown",
            "rotator_angle": 0.0, "binning": 1, "is_valid": True,
            "image_scale_arcsec": scale}


def plant_cal(root, profile: str, cal: dict | None = None) -> object:
    d = root / "guider"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{profile}.json"
    p.write_text(json.dumps(cal or cal_dict()), encoding="utf-8")
    return p


def cal_path(root, profile: str):
    return root / "guider" / f"{profile}.json"


def pulse(direction: str, ms: int) -> dict:
    return {"action": "pulse", "dir": direction, "ms": int(ms)}


def pair(ra=None, dec=None) -> dict:
    a: dict = {"action": "pulse_pair"}
    if ra:
        a["ra"] = {"dir": ra[0], "ms": int(ra[1])}
    if dec:
        a["dec"] = {"dir": dec[0], "ms": int(dec[1])}
    return a


IDLE = {"action": "idle"}


class ScriptedEngine:
    """A stand-in for ``astrodeck_native.GuideEngine`` that replays a script.

    Each ``process()`` pops the next ``(action, errs)``: ``errs`` is the
    frame's per-axis error ``(ra_px, dec_px)`` in ENGINE px, appended to
    ``recent`` as ``[t, ra, dec]`` with ``t`` advancing 1.0 per measured
    frame (so the guider's measurement mark always moves), or None for a
    frame the engine did not measure. When the script runs out, ``tail``
    (default: idle and unmeasured) is returned for every further frame."""

    def __init__(self, script, *, cal=None, tail=None, settle_on_dither=True):
        self.script = list(script)
        self.cal = dict(cal or cal_dict())
        self.recent: list[list[float]] = []
        self.t = 100.0
        self.settling = False
        self.settle_on_dither = settle_on_dither
        self.dithers: list[tuple[float, float]] = []
        self.tail = tail or (IDLE, None)
        self.frames = 0
        self.raise_on_frame: int | None = None

    def process(self, data, ts, exp):
        self.frames += 1
        if self.raise_on_frame is not None and self.frames >= self.raise_on_frame:
            raise RuntimeError("scripted engine failure")
        action, errs = self.script.pop(0) if self.script else self.tail
        if errs is not None:
            self.t += 1.0
            self.recent.append([self.t, float(errs[0]), float(errs[1])])
            del self.recent[:-100]
        return dict(action)

    def stats(self):
        return {"guiding": True, "settling": self.settling,
                "recent": [list(r) for r in self.recent],
                "rms_ra": 0.0, "rms_dec": 0.0, "rms_total": 0.0, "snr": 20.0}

    def dump_calibration(self):
        return dict(self.cal)

    def calibration_advisories(self):
        return []

    def dither(self, dx, dy):
        self.dithers.append((dx, dy))
        if self.settle_on_dither:
            self.settling = True

    def dump_gp_window(self):
        return []

    def begin_guiding(self):
        pass

    def load_calibration(self, cal):
        self.cal = dict(cal)

    def set_scope_pointing(self, *a, **k):
        pass

    def restore_gp_window(self, *a, **k):
        return False

    def flip_calibration(self, *a, **k):
        return True


class Frame:
    def __init__(self):
        self.data = np.zeros((8, 8), dtype=np.uint16)
        self.timestamp = 0.0


class Camera:
    """A guide camera that returns a blank frame after a short real sleep, so
    a guide loop on it yields and runs at a bounded rate."""

    name = "scripted guide camera"
    pixel_size_um = 3.76

    def __init__(self, dwell_s: float = 0.001):
        self.dwell_s = dwell_s
        self.exposures: list[float] = []

    async def expose(self, exposure_s, gain=None, offset=None, binning=1):
        await asyncio.sleep(self.dwell_s)
        import time as _t
        self.exposures.append(_t.monotonic())
        return Frame()


class PierSide:
    def __init__(self, value):
        self.value = value


class Mount:
    """A mount that records the pulses it is handed, with a published cap."""

    name = "scripted mount"
    can_pulse_guide = True

    def __init__(self, *, cap=CAP_MS, dec_deg=0.0, pier="east"):
        self.max_pulse_ms = cap
        self.pulses: list[tuple[str, int]] = []
        self.dec_deg = dec_deg
        self.pier = pier

    async def pulse_guide(self, direction, ms):
        self.pulses.append((direction, int(ms)))

    async def guide_rates(self):
        return (15.04 / 3600.0, 7.52 / 3600.0)

    async def get_position(self):
        return 1.0, self.dec_deg

    async def pier_side(self):
        return PierSide(self.pier)

    async def is_parked(self):
        return False

    async def is_slewing(self):
        return False


class Logs:
    """Every ``bus.log`` line, with its level, in order."""

    def __init__(self, monkeypatch):
        self.lines: list[tuple[str, str]] = []
        real = bus.log

        def _spy(level, message, source="hub"):
            self.lines.append((str(level), str(message)))
            return real(level, message, source)

        monkeypatch.setattr(bus, "log", _spy)

    def at(self, level=None, needle=""):
        return [m for lv, m in self.lines
                if (level is None or lv == level) and needle in m]

    def has(self, needle):
        return any(needle in m for _lv, m in self.lines)


def make_guider(*, profile=None, mount=None, camera=None, config=None,
                scale=5.5, scale_known=True) -> NativeGuider:
    """A real ``NativeGuider`` on the doubles above, with the axis limits and
    BLC set by a REAL ``_build_engine_config`` against the mount's cap."""
    cfg = {"image_scale_arcsec": scale, "image_scale_known": scale_known,
           "exposure_s": 0.01}
    cfg.update(config or {})
    g = NativeGuider(camera or Camera(), mount or Mount(), config=cfg,
                     profile_id=profile)
    g.connected = True
    g._build_engine_config((15.04 / 3600.0, 7.52 / 3600.0))
    return g


def arm_loop(g: NativeGuider, engine: ScriptedEngine, *, scope_dec_deg=0.0,
             probation=False) -> asyncio.Task:
    """Install ``engine`` and start the REAL guide loop on it, as
    ``start_guiding`` leaves it (active, steady lock, stop flag clear)."""
    g._engine = engine
    g._active = True
    g._lost = False
    g._stop.clear()
    g._lock_xy = (0.0, 0.0)
    g._streaks = {"ra": None, "dec": None}
    g._scope_dec_rad = math.radians(scope_dec_deg)
    g._caps = None
    if probation:
        g._probation = native._Probation(discard_epoch=g._discard_epoch,
                                         armed_at=__import__("time").monotonic())
    task = asyncio.create_task(g._guide_loop())
    g._loop_task = task
    return task


async def wait_until(predicate, timeout: float, interval: float = 0.005) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())


async def stop_loop(g: NativeGuider) -> None:
    """End a test's loop without the PPEC save path (no profile writes)."""
    task = g._loop_task
    g._active = False
    g._stop.set()
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except BaseException:   # noqa: BLE001 - the loop's own end
            pass
    g._loop_task = None
