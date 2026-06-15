"""ASCOM Alpaca backend.

Speaks the Alpaca HTTP/REST device protocol — the open standard supported by
ZWO, Pegasus Astro, QHY, PrimaLuceLab and (via ASCOM Remote) every classic
ASCOM driver. Includes UDP discovery (port 32227) and the binary ImageBytes
protocol for fast image downloads with JSON fallback.
"""
from __future__ import annotations

import asyncio
import json
import socket
import struct
import time
from typing import Any

import httpx
import numpy as np

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

DISCOVERY_PORT = 32227
DISCOVERY_MSG = b"alpacadiscovery1"

_client_id = 4242
_txn = 0


def _next_txn() -> int:
    global _txn
    _txn += 1
    return _txn


async def discover(timeout: float = 2.0) -> list[dict[str, Any]]:
    """Broadcast Alpaca discovery and return [{address, port, devices:[...]}]."""
    loop = asyncio.get_running_loop()
    found: dict[str, int] = {}

    class Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            try:
                port = json.loads(data.decode())["AlpacaPort"]
                found[addr[0]] = port
            except (ValueError, KeyError):
                pass

    transport, _ = await loop.create_datagram_endpoint(
        Proto, local_addr=("0.0.0.0", 0), allow_broadcast=True
    )
    try:
        for dest in ("255.255.255.255", "127.0.0.1"):
            try:
                transport.sendto(DISCOVERY_MSG, (dest, DISCOVERY_PORT))
            except OSError:
                pass
        await asyncio.sleep(timeout)
    finally:
        transport.close()

    servers = []
    async with httpx.AsyncClient(timeout=3.0) as http:
        for host, port in found.items():
            base = f"http://{host}:{port}"
            try:
                r = await http.get(f"{base}/management/v1/configureddevices")
                devices = r.json().get("Value", [])
            except Exception:
                devices = []
            servers.append({"address": host, "port": port, "devices": devices})
    return servers


class AlpacaConnection:
    """One Alpaca server endpoint; shared by its devices."""

    def __init__(self, host: str, port: int):
        self.base = f"http://{host}:{port}/api/v1"
        self.http = httpx.AsyncClient(timeout=30.0)

    async def get(self, dev_type: str, dev_num: int, method: str, **params: Any) -> Any:
        params |= {"ClientID": _client_id, "ClientTransactionID": _next_txn()}
        r = await self.http.get(f"{self.base}/{dev_type}/{dev_num}/{method}", params=params)
        return self._unwrap(r)

    async def put(self, dev_type: str, dev_num: int, method: str, **params: Any) -> Any:
        data = {k: v for k, v in params.items()}
        data |= {"ClientID": _client_id, "ClientTransactionID": _next_txn()}
        r = await self.http.put(f"{self.base}/{dev_type}/{dev_num}/{method}", data=data)
        return self._unwrap(r)

    @staticmethod
    def _unwrap(r: httpx.Response) -> Any:
        if r.status_code != 200:
            raise DeviceError(f"Alpaca HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        if body.get("ErrorNumber", 0) != 0:
            raise DeviceError(body.get("ErrorMessage", "Alpaca error"))
        return body.get("Value")

    async def close(self) -> None:
        await self.http.aclose()


class _AlpacaDevice:
    """Mixin: shared connect/disconnect over the Alpaca 'connected' property."""

    dev_type: str

    def __init__(self, conn: AlpacaConnection, dev_num: int, name: str):
        self.conn = conn
        self.dev_num = dev_num
        self.name = name
        self.connected = False

    async def _get(self, method: str, **params: Any) -> Any:
        return await self.conn.get(self.dev_type, self.dev_num, method, **params)

    async def _put(self, method: str, **params: Any) -> Any:
        return await self.conn.put(self.dev_type, self.dev_num, method, **params)

    async def connect(self) -> None:
        await self._put("connected", Connected=True)
        self.connected = True

    async def disconnect(self) -> None:
        try:
            await self._put("connected", Connected=False)
        finally:
            self.connected = False


_SENSOR_TYPES = {0: None, 1: None, 2: "RGGB", 3: "CMYG", 4: "CMYG2", 5: "LRGB"}


class AlpacaCamera(_AlpacaDevice, Camera):
    dev_type = "camera"

    def __init__(self, conn: AlpacaConnection, dev_num: int, name: str):
        _AlpacaDevice.__init__(self, conn, dev_num, name)
        self._exposing = False

    async def connect(self) -> None:
        await _AlpacaDevice.connect(self)
        self.sensor_width = await self._get("cameraxsize")
        self.sensor_height = await self._get("cameraysize")
        self.pixel_size_um = await self._get("pixelsizex")
        try:
            self.max_gain = await self._get("gainmax")
        except DeviceError:
            self.max_gain = 0
        try:
            self.can_cool = await self._get("cansetccdtemperature")
        except DeviceError:
            self.can_cool = False
        try:
            self.bayer_pattern = _SENSOR_TYPES.get(await self._get("sensortype"))
        except DeviceError:
            self.bayer_pattern = None

    async def expose(self, seconds: float, gain: int, offset: int, binning: int = 1,
                     light: bool = True, save: bool = False,
                     target: str = "") -> CameraFrame:
        if self.max_gain:
            await self._put("gain", Gain=gain)
        try:
            await self._put("offset", Offset=offset)
        except DeviceError:
            pass
        await self._put("binx", BinX=binning)
        await self._put("biny", BinY=binning)
        await self._put("numx", NumX=self.sensor_width // binning)
        await self._put("numy", NumY=self.sensor_height // binning)
        await self._put("startexposure", Duration=seconds, Light=light)
        self._exposing = True
        try:
            while not await self._get("imageready"):
                await asyncio.sleep(min(0.5, max(0.1, seconds / 20)))
        except asyncio.CancelledError:
            await self.abort_exposure()
            raise
        finally:
            self._exposing = False
        data = await self._download_image()
        temp = await self.get_temperature()
        return CameraFrame(
            data=data, exposure_s=seconds, gain=gain, offset=offset,
            binning=binning, bayer_pattern=self.bayer_pattern,
            temperature_c=temp, timestamp=time.time(),
        )

    async def _download_image(self) -> np.ndarray:
        """Prefer the binary ImageBytes protocol; fall back to JSON arrays."""
        url = f"{self.conn.base}/camera/{self.dev_num}/imagearray"
        params = {"ClientID": _client_id, "ClientTransactionID": _next_txn()}
        r = await self.conn.http.get(
            url, params=params, headers={"Accept": "application/imagebytes"}
        )
        ctype = r.headers.get("content-type", "")
        if "imagebytes" in ctype:
            return self._parse_imagebytes(r.content)
        body = r.json()
        if body.get("ErrorNumber", 0) != 0:
            raise DeviceError(body.get("ErrorMessage", "image download failed"))
        arr = np.array(body["Value"], dtype=np.int32)  # Alpaca arrays are [x][y]
        return np.clip(arr.T, 0, 65535).astype(np.uint16)

    @staticmethod
    def _parse_imagebytes(buf: bytes) -> np.ndarray:
        (meta_ver, err_no, _ctxn, _stxn, data_start, _img_type, tx_type,
         rank, dim1, dim2, dim3) = struct.unpack_from("<11i", buf, 0)
        if err_no != 0:
            raise DeviceError(f"ImageBytes error {err_no}")
        dtypes = {1: np.int16, 2: np.int32, 3: np.float64, 4: np.float32,
                  6: np.uint8, 8: np.uint16, 9: np.uint32}
        dt = dtypes.get(tx_type)
        if dt is None or rank not in (2, 3):
            raise DeviceError(f"unsupported ImageBytes format (type={tx_type}, rank={rank})")
        arr = np.frombuffer(buf, dtype=dt, offset=data_start)
        shape = (dim1, dim2) if rank == 2 else (dim1, dim2, dim3)
        arr = arr.reshape(shape, order="F" if rank == 2 else "C")
        if rank == 3:
            arr = arr[:, :, 0]
        # Alpaca image arrays are [x][y]; transpose to row-major [y][x].
        if rank == 2:
            arr = arr.T
        return np.clip(arr.astype(np.int32), 0, 65535).astype(np.uint16)

    async def abort_exposure(self) -> None:
        try:
            await self._put("abortexposure")
        except DeviceError:
            await self._put("stopexposure")

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        if target_c is not None:
            await self._put("setccdtemperature", SetCCDTemperature=target_c)
        await self._put("cooleron", CoolerOn=on)

    async def get_temperature(self) -> float | None:
        try:
            return await self._get("ccdtemperature")
        except DeviceError:
            return None


_PULSE_DIRS = {"north": 0, "south": 1, "east": 2, "west": 3}


class AlpacaTelescope(_AlpacaDevice, Telescope):
    dev_type = "telescope"

    async def get_position(self) -> tuple[float, float]:
        ra = await self._get("rightascension")
        dec = await self._get("declination")
        return ra, dec

    async def is_slewing(self) -> bool:
        return await self._get("slewing")

    async def slew(self, ra_hours: float, dec_deg: float) -> None:
        await self._put("slewtocoordinatesasync", RightAscension=ra_hours,
                        Declination=dec_deg)
        try:
            while await self._get("slewing"):
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            await self._put("abortslew")
            raise

    async def sync(self, ra_hours: float, dec_deg: float) -> None:
        await self._put("synctocoordinates", RightAscension=ra_hours,
                        Declination=dec_deg)

    async def set_tracking(self, on: bool) -> None:
        await self._put("tracking", Tracking=on)

    async def get_tracking(self) -> bool:
        return await self._get("tracking")

    async def park(self) -> None:
        await self._put("park")

    async def unpark(self) -> None:
        await self._put("unpark")

    async def is_parked(self) -> bool:
        return await self._get("atpark")

    async def move_axis(self, axis: str, rate_deg_s: float) -> None:
        await self._put("moveaxis", Axis=0 if axis == "ra" else 1, Rate=rate_deg_s)

    async def pulse_guide(self, direction: str, ms: int) -> None:
        await self._put("pulseguide", Direction=_PULSE_DIRS[direction], Duration=ms)

    async def pier_side(self) -> PierSide:
        try:
            side = await self._get("sideofpier")
            return {0: PierSide.EAST, 1: PierSide.WEST}.get(side, PierSide.UNKNOWN)
        except DeviceError:
            return PierSide.UNKNOWN

    async def stop(self) -> None:
        await self._put("abortslew")


class AlpacaFocuser(_AlpacaDevice, Focuser):
    dev_type = "focuser"

    async def connect(self) -> None:
        await _AlpacaDevice.connect(self)
        self.max_position = await self._get("maxstep")
        try:
            self.step_size_um = await self._get("stepsize")
        except DeviceError:
            self.step_size_um = None

    async def get_position(self) -> int:
        return await self._get("position")

    async def move_to(self, position: int) -> None:
        await self._put("move", Position=int(position))
        try:
            while await self._get("ismoving"):
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            await self._put("halt")
            raise

    async def halt(self) -> None:
        await self._put("halt")

    async def get_temperature(self) -> float | None:
        try:
            return await self._get("temperature")
        except DeviceError:
            return None


class AlpacaFilterWheel(_AlpacaDevice, FilterWheel):
    dev_type = "filterwheel"

    async def connect(self) -> None:
        await _AlpacaDevice.connect(self)
        self.filter_names = await self._get("names")

    async def get_position(self) -> int:
        pos = await self._get("position")
        return max(0, pos)  # -1 means moving

    async def set_position(self, slot: int) -> None:
        await self._put("position", Position=int(slot))
        while await self._get("position") == -1:
            await asyncio.sleep(0.3)


class AlpacaSwitch(_AlpacaDevice, Switch):
    dev_type = "switch"

    async def get_ports(self) -> list[SwitchPort]:
        n = await self._get("maxswitch")
        ports = []
        for i in range(n):
            name = await self._get("getswitchname", Id=i)
            can_write = await self._get("canwrite", Id=i)
            lo = await self._get("minswitchvalue", Id=i)
            hi = await self._get("maxswitchvalue", Id=i)
            value = await self._get("getswitchvalue", Id=i)
            ports.append(SwitchPort(
                id=i, name=name, can_write=can_write,
                is_boolean=(lo == 0 and hi == 1), value=value, min=lo, max=hi,
            ))
        return ports

    async def set_port(self, port_id: int, value: float) -> None:
        await self._put("setswitchvalue", Id=port_id, Value=value)


DEVICE_CLASSES = {
    "camera": AlpacaCamera,
    "telescope": AlpacaTelescope,
    "focuser": AlpacaFocuser,
    "filterwheel": AlpacaFilterWheel,
    "switch": AlpacaSwitch,
}


def make_device(host: str, port: int, dev_type: str, dev_num: int, name: str):
    cls = DEVICE_CLASSES.get(dev_type.lower())
    if cls is None:
        raise DeviceError(f"unsupported Alpaca device type: {dev_type}")
    return cls(AlpacaConnection(host, port), dev_num, name)
