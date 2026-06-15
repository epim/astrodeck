"""NINA (Nighttime Imaging 'N' Astronomy) backend — the transition bridge.

Talks to a running NINA instance via the **Advanced API** plugin
(``http://<host>:1888/v2/api`` REST + ``ws://<host>:1888/v2/socket`` events).
This lets AstroDeck fly an existing, fully-configured NINA rig without
re-doing ASCOM/Alpaca driver setup — the natural on-ramp before migrating
to direct Alpaca device by device.

Design note: NINA does not expose raw 16-bit frames over HTTP. It returns
*rendered, auto-stretched* images plus computed statistics (HFR, star count),
and it owns mature autofocus / plate-solve / PHD2-guider routines. So this
backend deliberately delegates the smart operations to NINA and surfaces its
results, rather than pretending NINA is a dumb sensor:

  - capture  → NINA capture, fetch the prepared (stretched) image + statistics
  - focus    → NINA native autofocus, render NINA's own V-curve
  - solve    → NINA plate-solve of the prepared image (handled in the hub)
  - guide    → NINA's guider endpoints (NINA drives PHD2)

These devices are non-intrusive: disconnecting AstroDeck never disconnects
NINA's equipment, and a role is only registered if NINA already reports it
connected.
"""
from __future__ import annotations

import asyncio
import io
import socket
import time
from typing import Any

import httpx
import numpy as np
from PIL import Image

from ..events import bus
from .base import (
    Camera,
    CameraFrame,
    DeviceError,
    FilterWheel,
    Focuser,
    PierSide,
    Switch,
    SwitchPort,
    Telescope,
)
from ..guide.base import Guider, GuideStats

DEFAULT_PORT = 1888


# --------------------------------------------------------------------- helpers

def pick(d: Any, *keys: str, default: Any = None) -> Any:
    """First present, non-null value among keys (defensive against NINA's
    PascalCase variations across versions)."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _maybe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _maybe_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _sep_deg(ra1_h: float, dec1: float, ra2_h: float, dec2: float) -> float:
    """Angular separation (deg) between two equatorial coords; well-behaved
    near the pole where RA differences are mechanically ill-conditioned."""
    import math
    ra1, ra2 = math.radians(ra1_h * 15), math.radians(ra2_h * 15)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    cos_sep = (math.sin(d1) * math.sin(d2)
               + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))


def _decode_gray16(data: bytes) -> np.ndarray:
    """Decode a rendered PNG/JPEG to a 2D uint16 array (for histogram/stats).
    The visual preview uses the original bytes; this is only for the readout."""
    try:
        img = Image.open(io.BytesIO(data)).convert("L")
        arr = np.asarray(img, dtype=np.uint16) * 257  # 8-bit → full 16-bit range
        return arr
    except Exception:
        return np.zeros((1, 1), dtype=np.uint16)


# ---------------------------------------------------------------------- client

class NinaClient:
    """Thin async wrapper over NINA's Advanced API.

    ``http`` may be injected (tests pass an httpx client backed by a mock
    transport); otherwise we own one.
    """

    def __init__(self, host: str, port: int = DEFAULT_PORT,
                 http: httpx.AsyncClient | None = None):
        self.host = host
        self.port = port
        self.base = f"http://{host}:{port}/v2/api"
        self.ws_url = f"ws://{host}:{port}/v2/socket"
        self._own_http = http is None
        self.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
        )

    @staticmethod
    def _params(params: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in params.items() if v is not None}

    async def get(self, path: str, *, timeout: float | None = None, **params: Any) -> Any:
        """GET a JSON endpoint and return the unwrapped ``Response`` payload."""
        r = await self.http.get(f"{self.base}{path}", params=self._params(params),
                                timeout=timeout)
        if r.status_code != 200:
            raise DeviceError(f"NINA HTTP {r.status_code} on {path}: {r.text[:160]}")
        try:
            body = r.json()
        except ValueError:
            raise DeviceError(f"NINA returned non-JSON on {path}")
        if not body.get("Success", True):
            raise DeviceError(pick(body, "Error", default=f"NINA error on {path}"))
        return body.get("Response")

    async def get_bytes(self, path: str, *, timeout: float | None = None,
                        **params: Any) -> bytes:
        """GET a binary endpoint (image stream)."""
        r = await self.http.get(f"{self.base}{path}", params=self._params(params),
                                timeout=timeout)
        if r.status_code != 200:
            raise DeviceError(f"NINA HTTP {r.status_code} on {path}: {r.text[:120]}")
        return r.content

    async def close(self) -> None:
        if self._own_http:
            await self.http.aclose()


class _NinaDevice:
    """Shared info() with a short TTL cache so a status poll doesn't fan out
    into a dozen HTTP round-trips per cycle."""

    info_path: str = ""

    def __init__(self, client: NinaClient, name: str):
        self.client = client
        self.name = name
        self.connected = False
        self._info: dict | None = None
        self._info_ts = 0.0

    async def info(self, force: bool = False) -> dict:
        now = time.monotonic()
        if not force and self._info is not None and now - self._info_ts < 0.4:
            return self._info
        self._info = await self.client.get(self.info_path) or {}
        self._info_ts = now
        return self._info

    async def disconnect(self) -> None:
        # Bridge semantics: never tear down NINA's own equipment connection.
        self.connected = False

    def describe(self) -> dict:
        return {"name": self.name, "kind": getattr(self, "kind", "device"),
                "connected": self.connected}


# ---------------------------------------------------------------------- camera

class NinaCamera(_NinaDevice, Camera):
    kind = "camera"
    info_path = "/equipment/camera/info"

    async def connect(self) -> None:
        info = await self.info(force=True)
        self.connected = bool(pick(info, "Connected", default=False))
        self.name = pick(info, "Name", "DisplayName", default=self.name)
        self.sensor_width = int(pick(info, "XSize", "CameraXSize", default=0) or 0)
        self.sensor_height = int(pick(info, "YSize", "CameraYSize", default=0) or 0)
        self.pixel_size_um = float(pick(info, "PixelSize", default=0) or 0)
        self.max_gain = int(pick(info, "GainMax", "MaxGain", default=0) or 0)
        self.can_cool = bool(pick(info, "CanSetTemperature", "HasCooler",
                                  "CanCool", default=False))
        self.has_dew_heater = bool(pick(info, "HasDewHeater", default=False))
        sensor = pick(info, "SensorType", default=None)
        self.bayer_pattern = None if sensor in (None, "Monochrome", "Mono") else sensor

    def _preview_size(self) -> str:
        w, h = self.sensor_width, self.sensor_height
        if w and h:
            tw = 1400
            return f"{tw}x{max(1, round(tw * h / w))}"
        return "1400x1050"

    async def expose(self, seconds: float, gain: int, offset: int, binning: int = 1,
                     light: bool = True, save: bool = False,
                     target: str = "") -> CameraFrame:
        if binning and binning > 1:
            try:
                await self.client.get("/equipment/camera/set-binning",
                                      binning=f"{binning}x{binning}")
            except DeviceError:
                pass
        image_type = "LIGHT" if light else "DARK"
        try:
            await self.client.get(
                "/equipment/camera/capture",
                duration=seconds,
                gain=(gain if self.max_gain else None),
                waitForResult="true", omitImage="true", imageType=image_type,
                save=("true" if save else None),
                targetName=(target or None),
                timeout=seconds + 120.0,
            )
        except asyncio.CancelledError:
            await self.abort_exposure()
            raise

        png = await self.client.get_bytes(
            "/prepared-image", stream="true", quality="90", resize="true",
            size=self._preview_size(), autoPrepare="true", timeout=60.0)

        stats: dict = {}
        try:
            stats = await self.client.get("/equipment/camera/capture/statistics") or {}
        except DeviceError:
            pass

        return CameraFrame(
            data=_decode_gray16(png),
            exposure_s=seconds, gain=gain, offset=offset, binning=binning,
            bayer_pattern=None,
            temperature_c=_maybe_float(pick(stats, "Temperature")),
            timestamp=time.time(),
            rendered_bytes=png, rendered_mime="image/jpeg",
            hfr=_maybe_float(pick(stats, "HFR")),
            stars=_maybe_int(pick(stats, "Stars", "DetectedStars")),
            saved_path=(pick(stats, "Filename", "FilePath") if save else None),
        )

    async def abort_exposure(self) -> None:
        try:
            await self.client.get("/equipment/camera/abort-exposure")
        except DeviceError:
            pass

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        if on:
            await self.client.get("/equipment/camera/cool",
                                  temperature=(target_c if target_c is not None else -10),
                                  minutes=0)
        else:
            await self.client.get("/equipment/camera/warm", minutes=0)

    async def get_temperature(self) -> float | None:
        return _maybe_float(pick(await self.info(), "Temperature"))

    async def set_dew_heater(self, power: int) -> None:
        await self.client.get("/equipment/camera/dew-heater", power=int(power))


# ----------------------------------------------------------------------- mount

class NinaTelescope(_NinaDevice, Telescope):
    kind = "telescope"
    info_path = "/equipment/mount/info"

    async def connect(self) -> None:
        info = await self.info(force=True)
        self.connected = bool(pick(info, "Connected", default=False))
        self.name = pick(info, "Name", "DisplayName", default=self.name)

    async def get_position(self) -> tuple[float, float]:
        info = await self.info()
        return (float(pick(info, "RightAscension", "RA", default=0) or 0),
                float(pick(info, "Declination", "Dec", default=0) or 0))

    async def is_slewing(self) -> bool:
        return bool(pick(await self.info(force=True), "Slewing", default=False))

    async def slew(self, ra_hours: float, dec_deg: float) -> None:
        # Fire-and-forget, then converge on position. This is robust to ASCOM
        # drivers that report Slewing=true even when idle (observed on the ASI
        # Mount), which would hang NINA's server-side waitToFinish.
        try:
            await self.client.get("/equipment/mount/slew", ra=ra_hours, dec=dec_deg,
                                  waitToFinish="false", timeout=30.0)
        except asyncio.CancelledError:
            await self.stop()
            raise
        last: tuple[float, float] | None = None
        stable = 0
        for _ in range(240):  # ~120s ceiling
            await asyncio.sleep(0.5)
            try:
                info = await self.info(force=True)
            except DeviceError:
                continue
            ra = float(pick(info, "RightAscension", "RA", default=0) or 0)
            dec = float(pick(info, "Declination", "Dec", default=0) or 0)
            if _sep_deg(ra, dec, ra_hours, dec_deg) < 0.25:
                return
            if last is not None and _sep_deg(ra, dec, last[0], last[1]) < 0.02:
                stable += 1
                if stable >= 4:   # stopped moving for ~2s — settled close enough
                    return
            else:
                stable = 0
            last = (ra, dec)

    async def sync(self, ra_hours: float, dec_deg: float) -> None:
        await self.client.get("/equipment/mount/sync", ra=ra_hours, dec=dec_deg)

    async def set_tracking(self, on: bool) -> None:
        await self.client.get("/equipment/mount/set-tracking",
                              enabled=("true" if on else "false"))

    async def get_tracking(self) -> bool:
        return bool(pick(await self.info(), "TrackingEnabled", "Tracking", default=False))

    async def park(self) -> None:
        await self.client.get("/equipment/mount/park", timeout=300.0)

    async def unpark(self) -> None:
        await self.client.get("/equipment/mount/unpark")

    async def is_parked(self) -> bool:
        return bool(pick(await self.info(), "AtPark", "Parked", default=False))

    async def move_axis(self, axis: str, rate_deg_s: float) -> None:
        raise DeviceError("manual nudging isn't available through NINA — use Goto")

    async def pier_side(self) -> PierSide:
        # NINA/ASCOM report e.g. "pierEast"/"pierWest" (or "East"/"West").
        side = str(pick(await self.info(), "SideOfPier", default="")).lower()
        if "east" in side:
            return PierSide.EAST
        if "west" in side:
            return PierSide.WEST
        return PierSide.UNKNOWN

    async def time_to_meridian_flip(self) -> float | None:
        # force-fresh: a stale value here could miss/duplicate a flip
        v = pick(await self.info(force=True), "TimeToMeridianFlip")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    async def stop(self) -> None:
        await self.client.get("/equipment/mount/slew-stop")


# --------------------------------------------------------------------- focuser

class NinaFocuser(_NinaDevice, Focuser):
    kind = "focuser"
    info_path = "/equipment/focuser/info"
    supports_native_autofocus = True

    async def connect(self) -> None:
        info = await self.info(force=True)
        self.connected = bool(pick(info, "Connected", default=False))
        self.name = pick(info, "Name", "DisplayName", default=self.name)
        self.max_position = int(pick(info, "MaxStep", "MaxPosition", default=100_000) or 100_000)

    async def get_position(self) -> int:
        return int(pick(await self.info(), "Position", default=0) or 0)

    async def get_temperature(self) -> float | None:
        return _maybe_float(pick(await self.info(), "Temperature"))

    async def move_to(self, position: int) -> None:
        await self.client.get("/equipment/focuser/move", position=int(position))
        for _ in range(800):
            if not bool(pick(await self.info(force=True), "IsMoving", "Moving", default=False)):
                return
            await asyncio.sleep(0.3)

    async def halt(self) -> None:
        await self.client.get("/equipment/focuser/stop-move")

    async def _last_af(self) -> dict | None:
        try:
            return await self.client.get("/equipment/focuser/last-af")
        except DeviceError:
            return None

    async def native_autofocus(self) -> dict:
        """Trigger NINA's native autofocus, then read its report and return
        the V-curve as ``{success, best_position, best_hfr, points}``."""
        before = pick(await self._last_af() or {}, "Timestamp", default=None)
        await self.client.get("/equipment/focuser/auto-focus", timeout=600.0)

        report: dict | None = None
        for _ in range(300):  # up to ~10 min, polling every 2s
            await asyncio.sleep(2.0)
            r = await self._last_af()
            if r and pick(r, "Timestamp", default=None) != before:
                report = r
                break
        if not report:
            return {"success": False, "best_position": await self.get_position(),
                    "best_hfr": None, "points": [],
                    "message": "no autofocus report from NINA (timed out)"}

        points = []
        for mp in pick(report, "MeasurePoints", "FocusPoints", default=[]) or []:
            p = pick(mp, "Position", "FocuserPosition")
            v = pick(mp, "Value", "HFR", "Measurement")
            if p is not None and v is not None:
                points.append({"position": int(round(float(p))), "hfr": float(v)})

        cfp = pick(report, "CalculatedFocusPoint", "FinalFocusPoint", default={}) or {}
        best = pick(cfp, "Position", default=None)
        best_hfr = _maybe_float(pick(cfp, "Value", "HFR"))
        if best is None:
            best = await self.get_position()
        return {"success": True, "best_position": int(round(float(best))),
                "best_hfr": best_hfr, "points": points, "message": "ok"}


# ------------------------------------------------------------------ filterwheel

class NinaFilterWheel(_NinaDevice, FilterWheel):
    kind = "filterwheel"
    info_path = "/equipment/filterwheel/info"

    async def connect(self) -> None:
        info = await self.info(force=True)
        self.connected = bool(pick(info, "Connected", default=False))
        self.name = pick(info, "Name", "DisplayName", default=self.name)
        avail = pick(info, "AvailableFilters", "SelectableFilters", default=[]) or []
        self.filter_names = [pick(f, "Name", default=str(i)) for i, f in enumerate(avail)]
        self._filter_ids = [pick(f, "Id", "Position", default=i) for i, f in enumerate(avail)]
        self.filter_offsets = [int(pick(f, "FocusOffset", "Offset", default=0) or 0)
                               for f in avail]

    async def get_position(self) -> int:
        sel = pick(await self.info(), "SelectedFilter", default=None)
        if isinstance(sel, dict):
            name = pick(sel, "Name")
            if name in self.filter_names:
                return self.filter_names.index(name)
            sid = pick(sel, "Id", "Position")
            if sid in self._filter_ids:
                return self._filter_ids.index(sid)
        return 0

    async def set_position(self, slot: int) -> None:
        fid = self._filter_ids[slot] if 0 <= slot < len(self._filter_ids) else slot
        await self.client.get("/equipment/filterwheel/change-filter", filterId=fid,
                              timeout=120.0)


# ----------------------------------------------------------------------- switch

class NinaSwitch(_NinaDevice, Switch):
    kind = "switch"
    info_path = "/equipment/switch/info"

    async def connect(self) -> None:
        info = await self.info(force=True)
        self.connected = bool(pick(info, "Connected", default=False))
        self.name = pick(info, "Name", "DisplayName", default=self.name)

    async def get_ports(self) -> list[SwitchPort]:
        info = await self.info(force=True)
        ports: list[SwitchPort] = []
        for s in pick(info, "WritableSwitches", default=[]) or []:
            lo = float(pick(s, "Minimum", "Min", default=0) or 0)
            hi = float(pick(s, "Maximum", "Max", default=1) or 1)
            ports.append(SwitchPort(
                id=int(pick(s, "Id", "Index", default=len(ports))),
                name=pick(s, "Name", default=f"Switch {len(ports)}"),
                can_write=True, is_boolean=(lo == 0 and hi == 1),
                value=float(pick(s, "Value", default=0) or 0), min=lo, max=hi,
                unit=pick(s, "Unit", default="")))
        for s in pick(info, "ReadonlySwitches", "UnreachableSwitches", default=[]) or []:
            ports.append(SwitchPort(
                id=int(pick(s, "Id", "Index", default=len(ports))),
                name=pick(s, "Name", default=f"Sensor {len(ports)}"),
                can_write=False, is_boolean=False,
                value=float(pick(s, "Value", default=0) or 0),
                min=float(pick(s, "Minimum", default=0) or 0),
                max=float(pick(s, "Maximum", default=0) or 0),
                unit=pick(s, "Unit", default="")))
        return ports

    async def set_port(self, port_id: int, value: float) -> None:
        await self.client.get("/equipment/switch/set-switch-value",
                              index=port_id, value=value)


# ------------------------------------------------------------------------ guide

class NinaGuider(Guider):
    name = "NINA Guider"

    def __init__(self, client: NinaClient):
        self.client = client
        self.connected = False
        self._guiding = False
        self._pixel_scale = 1.0
        self._recent: list[dict] = []
        self._poll_task: asyncio.Task | None = None

    async def connect(self) -> None:
        info = await self.client.get("/equipment/guider/info")
        self.connected = bool(pick(info, "Connected", default=False))
        self.name = pick(info, "Name", default="NINA Guider")
        self._pixel_scale = float(pick(info, "PixelScale", default=1.0) or 1.0)

    async def disconnect(self) -> None:
        self.connected = False
        self._guiding = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = None

    async def start_guiding(self) -> None:
        await self.client.get("/equipment/guider/start", calibrate="false", timeout=180.0)
        for _ in range(120):
            info = await self.client.get("/equipment/guider/info")
            if str(pick(info, "State", default="")).lower().startswith("guid"):
                break
            await asyncio.sleep(1.0)
        self._guiding = True
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_graph())
        bus.log("info", "NINA guider started", "guide")

    async def stop_guiding(self) -> None:
        try:
            await self.client.get("/equipment/guider/stop")
        finally:
            self._guiding = False
            bus.publish("guide", **self.stats().__dict__)

    async def is_active(self) -> bool:
        try:
            info = await self.client.get("/equipment/guider/info")
            return str(pick(info, "State", default="")).lower().startswith("guid")
        except DeviceError:
            return self._guiding

    async def dither(self, pixels: float = 3.0) -> None:
        await self.client.get("/equipment/guider/dither", timeout=180.0)
        await asyncio.sleep(2.0)  # NINA performs its own settle

    async def _poll_graph(self) -> None:
        while self._guiding and self.connected:
            try:
                g = await self.client.get("/equipment/guider/graph") or {}
                steps = pick(g, "GuideSteps", "Steps", default=[]) or []
                recent = []
                for s in steps[-120:]:
                    ra = pick(s, "RADistanceRawDisplay", "RADistanceRaw",
                              "RADistanceArcsec", "RADistance", default=0) or 0
                    dec = pick(s, "DECDistanceRawDisplay", "DECDistanceRaw",
                               "DECDistanceArcsec", "DECDistance", default=0) or 0
                    recent.append({"t": time.time(),
                                   "ra": round(float(ra) * self._pixel_scale, 3),
                                   "dec": round(float(dec) * self._pixel_scale, 3)})
                self._recent = recent
                bus.publish("guide", **self.stats().__dict__)
            except Exception:
                pass
            await asyncio.sleep(1.5)

    def stats(self) -> GuideStats:
        import math
        recent = self._recent
        if recent:
            ras = [s["ra"] for s in recent[-100:]]
            decs = [s["dec"] for s in recent[-100:]]
            rms_ra = math.sqrt(sum(r * r for r in ras) / len(ras))
            rms_dec = math.sqrt(sum(d * d for d in decs) / len(decs))
        else:
            rms_ra = rms_dec = 0.0
        return GuideStats(
            guiding=self._guiding,
            rms_ra=round(rms_ra, 2), rms_dec=round(rms_dec, 2),
            rms_total=round(math.hypot(rms_ra, rms_dec), 2),
            snr=0.0, recent=recent[-120:])


# ------------------------------------------------------------------ rig builder

_ROLE_CLASSES = {
    "camera": (NinaCamera, "/equipment/camera/info"),
    "telescope": (NinaTelescope, "/equipment/mount/info"),
    "focuser": (NinaFocuser, "/equipment/focuser/info"),
    "filterwheel": (NinaFilterWheel, "/equipment/filterwheel/info"),
    "switch": (NinaSwitch, "/equipment/switch/info"),
}


async def build_nina_rig(host: str, port: int = DEFAULT_PORT,
                         http: httpx.AsyncClient | None = None) -> dict:
    """Probe a NINA instance and build device objects for every role NINA
    reports as connected. Returns ``{client, devices: {role: dev}, guider}``."""
    client = NinaClient(host, port, http=http)
    # Fail fast with a clear error if NINA / the plugin isn't reachable.
    try:
        await client.get("/version")
    except DeviceError:
        raise
    except Exception as e:
        await client.close()
        raise DeviceError(f"cannot reach NINA at {host}:{port} — is the "
                          f"Advanced API plugin running? ({e})")

    devices: dict[str, Any] = {}
    for role, (cls, _path) in _ROLE_CLASSES.items():
        dev = cls(client, role)
        try:
            await dev.connect()
        except DeviceError:
            continue
        if dev.connected:
            devices[role] = dev

    guider: NinaGuider | None = NinaGuider(client)
    try:
        await guider.connect()
        if not guider.connected:
            guider = None
    except DeviceError:
        guider = None

    return {"client": client, "devices": devices, "guider": guider}


# -------------------------------------------------------------------- discovery

def _local_subnets() -> list[str]:
    """The /24 prefixes this machine is on (e.g. '192.168.250')."""
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))           # no packets sent; just resolves the route
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    subnets = set()
    for ip in ips:
        if ip.startswith("127."):
            continue
        parts = ip.split(".")
        if len(parts) == 4:
            subnets.add(".".join(parts[:3]))
    return sorted(subnets)


async def _probe_version(client: httpx.AsyncClient, host: str, port: int) -> str | None:
    """Return the Advanced-API version if host:port speaks NINA, else None."""
    try:
        r = await client.get(f"http://{host}:{port}/v2/api/version")
        if r.status_code != 200:
            return None
        body = r.json()
        if body.get("Type") == "API" and body.get("Success"):
            return str(body.get("Response"))
    except Exception:
        return None
    return None


async def _detail(client: httpx.AsyncClient, host: str, port: int) -> dict:
    base = f"http://{host}:{port}/v2/api"
    out: dict[str, Any] = {"nina_version": None, "devices": {}}
    try:
        r = await client.get(f"{base}/version/nina")
        out["nina_version"] = r.json().get("Response")
    except Exception:
        pass
    role_paths = [("camera", "camera"), ("telescope", "mount"),
                  ("focuser", "focuser"), ("filterwheel", "filterwheel"),
                  ("guider", "guider")]
    for role, path in role_paths:
        try:
            r = await client.get(f"{base}/equipment/{path}/info")
            d = r.json().get("Response", {})
            if isinstance(d, dict) and d.get("Connected"):
                out["devices"][role] = pick(d, "Name", "DisplayName", default=role)
        except Exception:
            pass
    return out


async def discover_nina(port: int = DEFAULT_PORT, timeout: float = 0.6,
                        extra_hosts: list[str] | None = None) -> list[dict]:
    """Find NINA Advanced API instances on the local network.

    Sweeps this machine's own /24 subnet(s) plus any explicitly named hosts,
    probing ``<host>:<port>/v2/api/version`` concurrently. Connection-refused
    is fast, so only firewalled hosts cost the full timeout. Returns a list of
    ``{host, hostname, port, url, api_version, nina_version, devices}``.
    """
    candidates: list[str] = []
    for sub in _local_subnets():
        candidates += [f"{sub}.{i}" for i in range(1, 255)]
    for h in extra_hosts or []:
        try:
            candidates.append(socket.gethostbyname(h))
        except OSError:
            pass
    candidates = list(dict.fromkeys(candidates))  # de-dupe, preserve order

    sem = asyncio.Semaphore(128)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async def guarded(ip: str):
            async with sem:
                ver = await _probe_version(client, ip, port)
                return (ip, ver) if ver else None

        probed = await asyncio.gather(*[guarded(ip) for ip in candidates])
        found = [p for p in probed if p]

        results = []
        for ip, api_version in found:
            detail = await _detail(client, ip, port)
            try:
                hostname = socket.gethostbyaddr(ip)[0]
            except OSError:
                hostname = None
            results.append({
                "host": ip, "hostname": hostname, "port": port,
                "url": f"http://{ip}:{port}", "api_version": api_version,
                **detail,
            })
    results.sort(key=lambda r: r["host"])
    return results
