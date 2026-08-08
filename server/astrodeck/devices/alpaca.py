"""ASCOM Alpaca backend.

Speaks the Alpaca HTTP/REST device protocol — the open standard supported by
ZWO, Pegasus Astro, QHY, PrimaLuceLab and (via ASCOM Remote) every classic
ASCOM driver. Includes UDP discovery (port 32227) and the binary ImageBytes
protocol for fast image downloads with JSON fallback.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import struct
import time
from typing import Any

import httpx
import numpy as np

from .base import (
    Camera,
    CameraFrame,
    CoverCalibrator,
    CoverState,
    DeviceError,
    Dome,
    DomeShutterState,
    FilterWheel,
    Focuser,
    PierSide,
    Rotator,
    SafetyMonitor,
    Switch,
    SwitchPort,
    Telescope,
    TRACKING_RATES,
)

DISCOVERY_PORT = 32227
DISCOVERY_MSG = b"alpacadiscovery1"

# A3 (final-branch-review tonight-risk #5): overall deadline for the
# AlpacaCamera.expose imageready poll = exposure_s + this margin. Guards a
# responsive-but-stuck camera (driver answers imageready=false forever) that
# would otherwise hang the guide loop indefinitely; expiry raises
# DeviceError("imageready timeout"), which the native guider's retry envelope
# then handles in guiding contexts (imaging surfaces the DeviceError path).
_IMAGEREADY_POLL_MARGIN_S = 30.0

# Home control (2026-07-30). ASCOM FindHome is asynchronous on most drivers, so
# the PUT returning proves nothing; we poll AtHome. Homing sweeps the full range
# on some mounts, hence a park-sized budget rather than a slew-sized one.
FIND_HOME_TIMEOUT_S = 180.0
FIND_HOME_POLL_S = 1.0

# --- focuser move completion ------------------------------------------------
# An absolute move succeeds when the drawtube REACHES the target, not when the
# device stops claiming to move: an idle motor reads not-moving on the first
# poll, so a move the driver silently refused is indistinguishable from one that
# finished. That mistake was found on the EAF (2026-07-31, refused every move
# past a mechanical stop and reported success every time) and again on the
# ASIAIR (audit finding #15); the same numbers are used here so the three cannot
# drift. ARRIVAL_TOLERANCE_STEPS matches zwo_usb.py and ui/src/lib/focusMove.ts.
FOCUS_POLL_S = 0.25
FOCUS_MOVE_TIMEOUT_S = 180.0
ARRIVAL_TOLERANCE_STEPS = 2

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


class AlpacaScanError(DeviceError):
    """A manual Alpaca host/port scan failed in a differentiated way.

    ``kind`` is one of ``"unreachable" | "not_alpaca" | "timeout"`` so the UI
    can give a beginner who typo'd the IP a useful, specific message instead of
    a generic "network error".
    """

    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind


# A bare hostname only — no scheme, path, query, userinfo, or an embedded
# ``:port``. Labels are RFC-952/1123-ish. IP literals never reach this regex:
# ``validate_scan_host`` parses them with ``ipaddress`` first, so a bare IPv6
# literal IS accepted (and ``_authority`` brackets it) — only a BRACKETED one
# ``[::1]`` is rejected, since that is URL syntax rather than a host.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


def _resolved_ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if a *resolved* IP is one an unauthenticated scan must never reach.

    Checking the resolved IP (not just the literal) is half of the DNS-rebinding
    defence — ``evil.com`` resolving to ``127.0.0.1`` / ``169.254.169.254`` is
    rejected here even though the literal looked public. The other half is that
    the caller must then DIAL the address this approved (see ``validate_scan_host``
    and ``query_server``); re-resolving the name would hand the attacker a second
    answer. ``169.254.0.0/16`` (link-local, incl. the cloud-metadata IP) is
    covered by ``is_link_local``.
    """
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _authority(host: str, port: int) -> str:
    """``host:port`` for a URL, bracketing an IPv6 literal as RFC 3986 requires.

    Without the brackets ``2606:4700::1111`` + port 11111 concatenates into
    ``2606:4700::1111:11111`` — a different (and unvalidated) address.
    """
    try:
        if ipaddress.ip_address(host).version == 6:
            return f"[{host}]:{port}"
    except ValueError:
        pass
    return f"{host}:{port}"


def validate_scan_host(host: str, port: int) -> list[str]:
    """Validate a user-supplied Alpaca/NINA scan target before any network I/O.

    Raises :class:`AlpacaScanError` (kind ``"invalid"``) if:

    - ``host`` is not a bare hostname/IP literal (blocks ``evil.com/x?``,
      ``user@host``, an embedded ``host:port``, schemes, IPv6 brackets);
    - ``port`` is not an integer in ``1..65535``;
    - the host **resolves** to a loopback/private/link-local/reserved/metadata
      address (the SSRF guard);

    or kind ``"unreachable"`` if the name does not resolve to anything usable.

    Returns the approved address literals, and the caller MUST connect to one of
    them rather than to ``host``. That is the DNS-rebinding half of the guard:
    resolving here and then letting the HTTP client resolve the name again gives
    an attacker-controlled record a second answer (public first, ``127.0.0.1``
    second) between the check and the connection. This is the single chokepoint
    shared by the Alpaca and NINA manual-scan entry points.
    """
    host = (host or "").strip()
    if not host:
        raise AlpacaScanError("invalid", "no host given")

    # Reject anything that is not a plain hostname/IPv4: catches user@, :port,
    # path/query, scheme, IPv6 brackets — all in one shot.
    try:
        ip_literal: ipaddress._BaseAddress | None = ipaddress.ip_address(host)
    except ValueError:
        ip_literal = None
    if ip_literal is None and not _HOSTNAME_RE.match(host):
        raise AlpacaScanError(
            "invalid", f"'{host}' is not a valid hostname or IP address")

    # Port must be a real, in-range integer (rejects non-numeric/oversized).
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        raise AlpacaScanError("invalid", f"invalid port: {port!r}")
    if not (1 <= port_i <= 65535):
        raise AlpacaScanError("invalid", f"port out of range: {port_i}")

    # Resolve ONCE and reject the resolved IP(s). A literal IP resolves to
    # itself; a hostname is resolved via getaddrinfo. The survivors are returned
    # so the caller dials them — this lookup is the only one that ever happens.
    resolved: list[str] = []
    if ip_literal is not None:
        resolved = [str(ip_literal)]
    else:
        try:
            infos = socket.getaddrinfo(host, port_i, proto=socket.IPPROTO_TCP)
        except OSError:
            raise AlpacaScanError("unreachable", f"could not resolve host '{host}'")
        resolved = [info[4][0] for info in infos]

    approved: list[str] = []
    for addr in resolved:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            # Not an address we can reason about, so it was never really checked
            # — drop it rather than hand it back as approved.
            continue
        if _resolved_ip_blocked(ip):
            raise AlpacaScanError(
                "invalid",
                f"host '{host}' resolves to a blocked address ({addr}); "
                f"only routable LAN/Internet hosts may be scanned")
        if str(ip) not in approved:      # getaddrinfo repeats an address per family
            approved.append(str(ip))

    if not approved:
        raise AlpacaScanError("unreachable", f"could not resolve host '{host}'")
    return approved


async def query_server(host: str, port: int) -> dict:
    """Server-side fetch of an Alpaca server's configured devices.

    The browser cannot do this scan directly (CORS); the backend proxies it.
    Raises :class:`AlpacaScanError` with a differentiated ``kind`` so the UI can
    tell "wrong IP" from "right IP but not an Alpaca server".
    """
    # Dial the address the guard approved, NOT the name: putting `host` back in
    # the URL authority would let httpx resolve it a second time, which is the
    # whole DNS-rebinding window. The user's original host:port rides in the Host
    # header so a vhosted Alpaca server still answers, and redirects stay OFF
    # (httpx's default) — following one would re-dial an unvalidated target.
    addrs = validate_scan_host(host, port)
    url = f"http://{_authority(addrs[0], port)}/management/v1/configureddevices"
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(url, headers={"Host": _authority(host, port)})
    except httpx.TimeoutException:
        # Host reachable but the request (connect or read) timed out — distinct
        # from "can't connect at all"; the copy must not say "device is on?".
        raise AlpacaScanError("timeout", f"{host}:{port} did not respond in time")
    except (httpx.ConnectError, httpx.TransportError):
        raise AlpacaScanError(
            "unreachable",
            f"could not connect to {host}:{port} — check the IP, port and that "
            f"the device is on")
    if r.status_code != 200:
        # No upstream status in the message: this route is viewer-reachable, and
        # echoing 401-vs-404-vs-503 turns it into a port/host scan oracle (the
        # 502 handler in the API layer says it does not leak one — this is where
        # that promise is actually kept).
        raise AlpacaScanError(
            "not_alpaca", f"{host}:{port} answered but is not an Alpaca server")
    try:
        devices = r.json().get("Value", [])
    except ValueError:
        raise AlpacaScanError(
            "not_alpaca", f"{host}:{port} answered but did not return Alpaca JSON")
    return {"address": host, "port": port, "devices": devices}


class AlpacaConnection:
    """One Alpaca server endpoint; shared by its devices."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
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
    backend = "alpaca"
    hardware = True

    def __init__(self, conn: AlpacaConnection, dev_num: int, name: str):
        self.conn = conn
        self.dev_num = dev_num
        self.name = name
        self.connected = False
        # connection identity — lets Profiles replay host:port + which device
        self.host = conn.host
        self.port = conn.port
        self.role = ""

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": getattr(self, "kind", "device"),
            "connected": self.connected,
            "host": self.host,
            "port": self.port,
            "dev_type": self.dev_type,
            "dev_num": self.dev_num,
            "role": self.role,
            "backend": self.backend,
        }

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

    #: driver-derived saturation ADU (MaxADU). None until proven; the clip mask
    #: stays disabled while it is unknown (live-preview spec finding #3).
    full_well: int | None = None
    #: whether ``coolerpower`` is readable (probed once at connect); drives the
    #: Monitor ThermometerBar power bar vs. on/off degrade (monitor spec §6.2).
    can_report_cooler_power: bool = False

    def __init__(self, conn: AlpacaConnection, dev_num: int, name: str):
        _AlpacaDevice.__init__(self, conn, dev_num, name)
        self._exposing = False
        self.full_well = None
        self.can_report_cooler_power = False

    async def connect(self) -> None:
        await _AlpacaDevice.connect(self)
        self.sensor_width = await self._get("cameraxsize")
        self.sensor_height = await self._get("cameraysize")
        self.pixel_size_um = await self._get("pixelsizex")
        try:
            self.max_gain = await self._get("gainmax")
        except DeviceError:
            self.max_gain = 0
        # ASCOM MaxBinX → the UI's bin ceiling (UX-27). Keep the base default
        # (4) if the driver doesn't report it or reports a nonsense <1.
        try:
            mb = int(await self._get("maxbinx"))
            if mb >= 1:
                self.max_bin = mb
        except (DeviceError, TypeError, ValueError):
            pass
        try:
            self.can_cool = await self._get("cansetccdtemperature")
        except DeviceError:
            self.can_cool = False
        try:
            self.bayer_pattern = _SENSOR_TYPES.get(await self._get("sensortype"))
        except DeviceError:
            self.bayer_pattern = None
        # MaxADU → full_well. Many CMOS deliver 12/14-bit in a 16-bit container,
        # so true saturation is well below 65535; reading it makes the clip mask
        # honest. A driver that doesn't expose it (or reports 0) leaves full_well
        # None → clip mask disabled, never a wrong "approximate" overlay.
        try:
            mx = await self._get("maxadu")
            self.full_well = int(mx) if mx and int(mx) > 0 else None
        except DeviceError:
            self.full_well = None
        # Probe coolerpower once so the Monitor can show a real power bar where
        # the camera supports it and degrade to ON/OFF where it doesn't.
        if self.can_cool:
            try:
                await self._get("coolerpower")
                self.can_report_cooler_power = True
            except DeviceError:
                self.can_report_cooler_power = False

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
        deadline = time.monotonic() + seconds + _IMAGEREADY_POLL_MARGIN_S
        try:
            while not await self._get("imageready"):
                if time.monotonic() > deadline:
                    raise DeviceError("imageready timeout")
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
            # Alpaca returns raw linear sensor data; carry the probed MaxADU →
            # full_well so the clip/saturation overlay is honest (clip mask stays
            # disabled while full_well is None).
            full_well=self.full_well, data_is_linear=True,
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
        # ImageBytes is the flat memory dump of the .NET row-major array
        # int[dim1, dim2(, dim3)] — the RIGHTMOST index varies fastest, so
        # buf[k] = pixel(x = k // dim2, y = k % dim2). C-order reshape gives
        # arr[x][y] (matching the JSON ImageArray convention).
        shape = (dim1, dim2) if rank == 2 else (dim1, dim2, dim3)
        arr = arr.reshape(shape, order="C")
        if rank == 3:
            arr = arr[:, :, 0]
        # Alpaca image arrays are [x][y]; transpose to row-major [y][x].
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

    async def get_cooler(self) -> dict | None:
        """``{on, power, target_c}`` for the Monitor thermal cell, or None when
        the camera has no cooler. ``power`` is None when the driver can't report
        it (``can_report_cooler_power`` was False at connect). ``at_target`` is
        computed by the hub against the shared ``COOLER_AT_TARGET_C``."""
        if not self.can_cool:
            return None
        try:
            on = bool(await self._get("cooleron"))
        except DeviceError:
            on = False
        power: float | None = None
        if self.can_report_cooler_power:
            try:
                power = float(await self._get("coolerpower"))
            except DeviceError:
                power = None
        target: float | None = None
        try:
            target = float(await self._get("setccdtemperature"))
        except DeviceError:
            target = None
        return {"on": on, "power": power, "target_c": target,
                "can_report_power": self.can_report_cooler_power}


_PULSE_DIRS = {"north": 0, "south": 1, "east": 2, "west": 3}

#: name (TRACKING_RATES) <-> ASCOM DriveRates enum (Sidereal=0/Lunar=1/Solar=2;
#: King=3 is out of scope -- YAGNI).
_TRACKING_RATE_ENUM = {"sidereal": 0, "lunar": 1, "solar": 2}
_TRACKING_RATE_NAME = {v: k for k, v in _TRACKING_RATE_ENUM.items()}


class AlpacaTelescope(_AlpacaDevice, Telescope):
    dev_type = "telescope"

    async def connect(self) -> None:
        await _AlpacaDevice.connect(self)
        # Probe DestinationSideOfPier ONCE at connect so the pier-collision
        # capability flag is set before the FIRST slew — the engine's pier guard
        # gates on the flag, so a lazy first-call probe would leave it inert on
        # slew #1. Best-effort: a mount that doesn't support the call (or any
        # transport error) leaves the flag False and stays correctly ungated.
        try:
            ra, dec = await self.get_position()
            await self.destination_pier_side(ra, dec)
        except Exception:
            pass
        # Probe CanPulseGuide ONCE at connect — same rationale as the pier
        # probe above: the native guider's start-gate (spec §4) checks the
        # flag before the FIRST pulse, so a lazy first-call probe would leave
        # it inert. Best-effort: a mount that doesn't support the property (or
        # any transport error) leaves the flag at its False default.
        try:
            self.can_pulse_guide = bool(await self._get("canpulseguide"))
        except Exception:
            pass
        # Probe TrackingRates ONCE at connect (multi-rate mount tracking,
        # 2026-07-21) -- same rationale as the two probes above. A mount that
        # only advertises Sidereal (or doesn't support the property at all)
        # leaves the flag at its False default and the UI's rate control
        # stays hidden; only a driver reporting BOTH lunar(1) and solar(2)
        # gets the capability.
        try:
            rates = await self._get("trackingrates")
            offered = {int(v) for v in (rates or [])}
            if {1, 2} <= offered:
                self.can_set_tracking_rate = True
        except Exception:
            pass

        # Probe CanFindHome ONCE at connect (Home control, 2026-07-30) — same
        # rationale as the probes above, and the same honesty rule: the UI only
        # offers Home when the mount says it can, so a scope with no home sensor
        # never shows a button that would 400.
        try:
            self.can_find_home = bool(await self._get("canfindhome"))
        except Exception:
            pass

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

    async def set_tracking_rate(self, rate: str) -> None:
        if rate not in TRACKING_RATES:
            raise DeviceError(f"{self.name}: unknown tracking rate {rate!r}")
        await self._put("trackingrate", TrackingRate=_TRACKING_RATE_ENUM[rate])

    async def get_tracking_rate(self) -> str:
        value = await self._get("trackingrate")
        return _TRACKING_RATE_NAME.get(int(value), "sidereal")

    async def find_home(self) -> None:
        """ASCOM ``FindHome``. Asynchronous on most drivers, so poll ``AtHome``
        rather than trusting the PUT to have finished — a caller that returns
        early would report "homed" while the mount is still swinging."""
        await self._put("findhome")
        deadline = asyncio.get_running_loop().time() + FIND_HOME_TIMEOUT_S
        while True:
            if asyncio.get_running_loop().time() > deadline:
                raise DeviceError(
                    f"{self.name}: home did not complete within "
                    f"{FIND_HOME_TIMEOUT_S:.0f}s")
            await asyncio.sleep(FIND_HOME_POLL_S)
            try:
                if await self._get("athome"):
                    return
            except Exception:  # noqa: BLE001
                # A driver without AtHome cannot confirm; fall back to "not
                # slewing" so we still return rather than hanging to the cap.
                if not await self.is_slewing():
                    return

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

    async def guide_rates(self) -> tuple[float, float] | None:
        """Read ``GuideRateRightAscension``/``GuideRateDeclination`` (ASCOM
        deg/s at 1x guide speed). Mirrors ``pier_side``'s try/except: any
        driver that doesn't support the properties (or a transport error)
        yields None, and calibration falls back to advisories (dossier §17)."""
        try:
            ra = await self._get("guideraterightascension")
            dec = await self._get("guideratedeclination")
            return float(ra), float(dec)
        except (DeviceError, httpx.HTTPError, OSError):
            return None

    async def pier_side(self) -> PierSide:
        try:
            side = await self._get("sideofpier")
            return {0: PierSide.EAST, 1: PierSide.WEST}.get(side, PierSide.UNKNOWN)
        except DeviceError:
            return PierSide.UNKNOWN

    async def destination_pier_side(self, ra_hours: float, dec_deg: float) -> PierSide:
        """Probe the mount's ``DestinationSideOfPier`` for the pre-slew pier guard
        (Batch 4b). On the first successful probe set the capability flag True so
        the UI offers pier-limit enforcement; any failure (driver doesn't support
        the call, transport error) returns UNKNOWN and leaves the flag untouched."""
        try:
            side = await self._get("destinationsideofpier",
                                   RightAscension=ra_hours, Declination=dec_deg)
        except (DeviceError, httpx.HTTPError, OSError):
            return PierSide.UNKNOWN
        self.reports_destination_pier_side = True
        return {0: PierSide.EAST, 1: PierSide.WEST}.get(side, PierSide.UNKNOWN)

    async def stop(self) -> None:
        # Emergency stop: abort any slew AND zero both manual-motion axes, so a
        # mid-nudge STOP halts the mount rather than leaving an axis driving.
        #
        # F-A3 (DURABLE HALT): a failed abortslew must NOT skip the MoveAxis
        # zeroing — on real drivers AbortSlew alone does not reliably stop a
        # MoveAxis-driven manual slew, and the deadman fires precisely when the
        # network is flaky. Catch the httpx transport family (not just
        # DeviceError) around abortslew so we always still attempt the 0/0 zero.
        # Re-raise the original error afterward so the hub watchdog sees the
        # failure and RETRIES on its next tick rather than disarming.
        abort_err: Exception | None = None
        try:
            await self._put("abortslew")
        except (DeviceError, httpx.HTTPError, OSError) as e:
            abort_err = e
        try:
            await self.move_axis("ra", 0)
            await self.move_axis("dec", 0)
        except (DeviceError, httpx.HTTPError, OSError) as e:
            # Zeroing failed too — surface it so the watchdog retries.
            raise e if abort_err is None else abort_err
        if abort_err is not None:
            raise abort_err


class AlpacaFocuser(_AlpacaDevice, Focuser):
    dev_type = "focuser"

    #: ASCOM ``Absolute``. On a RELATIVE focuser ``Move(Position)`` is a step
    #: COUNT, not a destination, and ``Position`` may not be readable at all —
    #: so the arrival check below does not apply to one. Assumed True when the
    #: driver will not say: absolute is overwhelmingly the common case, and
    #: assuming relative would silently disable the check for everyone.
    absolute: bool = True

    async def connect(self) -> None:
        await _AlpacaDevice.connect(self)
        self.max_position = await self._get("maxstep")
        try:
            self.step_size_um = await self._get("stepsize")
        except DeviceError:
            self.step_size_um = None
        try:
            self.absolute = bool(await self._get("absolute"))
        except DeviceError:
            self.absolute = True

    async def get_position(self) -> int:
        return await self._get("position")

    async def _position_or_none(self) -> int | None:
        """Where the drawtube is, or None when this focuser cannot say.

        None is not a failure — it is the honest answer for a relative focuser,
        and for a driver whose ``Position`` read errors. The caller then has
        only absence-of-motion to go on and says so, rather than reading a
        missing number as position 0 and reporting a jam that is not there."""
        if not self.absolute:
            return None
        try:
            return int(await self._get("position"))
        except (DeviceError, TypeError, ValueError):
            return None

    async def move_to(self, position: int) -> None:
        """Absolute move; returns only once the drawtube has ARRIVED.

        This loop used to be ``while ismoving: sleep`` — no arrival test and no
        deadline. Both halves were wrong on real hardware: a driver that ignores
        the move reports not-moving immediately and this returned SUCCESS, and a
        driver that never clears ``IsMoving`` held the caller forever, which on
        the autofocus path means the sweep never ends and the night is over.
        The shape below is ``EafFocuser._move_to_locked``'s, which is the one
        that has been proven against a focuser that really did refuse to move.
        """
        target = int(position)
        start = await self._position_or_none()
        await self._put("move", Position=target)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + FOCUS_MOVE_TIMEOUT_S
        last, idle_polls = start, 0
        try:
            while True:
                await asyncio.sleep(FOCUS_POLL_S)
                moving = bool(await self._get("ismoving"))
                pos = await self._position_or_none()
                if pos is None:
                    # Nothing to compare against: absence of motion is all the
                    # evidence this focuser can give.
                    if not moving:
                        return
                elif abs(pos - target) <= ARRIVAL_TOLERANCE_STEPS:
                    return                                   # actually arrived
                elif pos != last:
                    last, idle_polls = pos, 0                # making progress
                else:
                    # Not at the target and not advancing. Two polls of that
                    # with the device idle before it counts, so a slow motor
                    # start cannot cry wolf.
                    idle_polls = idle_polls + 1 if not moving else 0
                    if idle_polls >= 2:
                        raise DeviceError(
                            f"{self.name}: move to {target} did not happen — "
                            f"the focuser stopped at {pos} (started from "
                            f"{start}) and is no longer moving. It is probably "
                            "at a mechanical limit or the drawtube is jammed; "
                            "try a smaller move in the other direction.")
                if loop.time() > deadline:
                    raise DeviceError(
                        f"{self.name}: move to {target} did not settle within "
                        f"{FOCUS_MOVE_TIMEOUT_S:.0f}s — halted, stopped at "
                        f"{pos if pos is not None else 'an unknown position'} "
                        f"(started from {start})")
        except BaseException:
            # Halt on ANY abnormal exit (cancel / timeout / transport failure):
            # a driver that raises while the motor is still commanded leaves the
            # drawtube travelling with nobody waiting for it.
            try:
                await self._put("halt")
            except Exception:  # noqa: BLE001 — halt is best-effort
                pass
            raise

    async def halt(self) -> None:
        await self._put("halt")

    async def is_moving(self) -> bool:
        return bool(await self._get("ismoving"))

    async def get_temperature(self) -> float | None:
        try:
            return await self._get("temperature")
        except DeviceError:
            return None


class AlpacaRotator(_AlpacaDevice, Rotator):
    dev_type = "rotator"

    async def connect(self) -> None:
        await _AlpacaDevice.connect(self)
        try:
            self.can_reverse = bool(await self._get("canreverse"))
        except DeviceError:
            self.can_reverse = False

    async def get_mechanical_position(self) -> float:
        return float(await self._get("mechanicalposition"))

    async def is_moving(self) -> bool:
        return bool(await self._get("ismoving"))

    async def move_mechanical(self, mech_deg: float) -> None:
        await self._put("movemechanical", Position=float(mech_deg))
        waited = 0.0
        try:
            while await self._get("ismoving"):
                await asyncio.sleep(0.25)
                waited += 0.25
                if waited >= self.MOVE_TIMEOUT_S:
                    await self._put("halt")
                    raise DeviceError(
                        f"rotator move timed out after {self.MOVE_TIMEOUT_S:.0f}s")
        except asyncio.CancelledError:
            await self._put("halt")
            raise

    async def halt(self) -> None:
        await self._put("halt")

    async def get_reverse(self) -> bool:
        if not self.can_reverse:
            return False
        try:
            return bool(await self._get("reverse"))
        except DeviceError:
            return False

    async def set_reverse(self, value: bool) -> None:
        if not self.can_reverse:
            raise DeviceError("this rotator does not support reverse")
        await self._put("reverse", Reverse=bool(value))


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

    async def is_moving(self) -> bool:
        """ASCOM reports a turning wheel by returning ``Position == -1``
        (IFilterWheelV2 §Position) — the one wire fact that says "between
        slots".

        ``get_position`` above clamps that -1 to 0 so no caller ever sees a
        negative slot, and that clamp is precisely what hid the turn: mid-move
        the status poll read 0 and the Capture screen calmly named slot 1's
        filter. Read the RAW value here instead, so the sentinel survives.
        """
        return int(await self._get("position")) < 0


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


class AlpacaSafetyMonitor(_AlpacaDevice, SafetyMonitor):
    """ASCOM SafetyMonitor — a cloud/rain/roof sensor exposing a single
    ``IsSafe`` boolean (Batch 4b). The base ``reading()`` wraps this into a
    ``SafetyReading``; the hub polls it on its own cadence."""

    dev_type = "safetymonitor"

    async def is_safe(self) -> bool:
        return bool(await self._get("issafe"))


#: ASCOM ShutterState enum -> AstroDeck DomeShutterState (PRO-4). An out-of-range
#: or unreadable value degrades to UNKNOWN in ``shutter_state`` (a state read must
#: never raise).
_DOME_SHUTTER_STATE = {
    0: DomeShutterState.OPEN,
    1: DomeShutterState.CLOSED,
    2: DomeShutterState.OPENING,
    3: DomeShutterState.CLOSING,
    4: DomeShutterState.ERROR,
}


class AlpacaDome(_AlpacaDevice, Dome):
    """ASCOM IDomeV2 roll-off roof / dome — the observatory-close role (PRO-4).

    Mirrors ``AlpacaRotator`` (motion + halt) and ``AlpacaSafetyMonitor`` (a
    single tolerant state read): ``shutter_state`` NEVER raises out of the read,
    degrading any error/unreachable driver to ``UNKNOWN``. ``set_slaved`` is
    gated on the probed ``CanSlave`` exactly like ``AlpacaRotator.set_reverse``.

    ``requires_park_before_close`` is intentionally LEFT at its fail-safe True
    default: ASCOM exposes no roof-through-mount geometry, so we must assume a
    close could crush an unparked OTA and let ``sequence/roof.close_observatory``
    enforce park-first. ``close_shutter`` does NOT itself park — the close-order
    guard owns that.
    """

    dev_type = "dome"

    async def connect(self) -> None:
        await _AlpacaDevice.connect(self)
        # Probe CanSlave ONCE at connect so set_slaved is correctly gated before
        # the first call. Best-effort: a roll-off roof that can't slave (or any
        # transport error) leaves the flag at its False default.
        try:
            self.can_slave = bool(await self._get("canslave"))
        except (DeviceError, httpx.HTTPError, OSError):
            self.can_slave = False

    async def shutter_state(self) -> DomeShutterState:
        # A state read must NEVER raise (dossier: like PierSide/CoverState): any
        # driver error, unreachable host, or non-numeric value -> UNKNOWN.
        try:
            raw = int(await self._get("shutterstatus"))
        except (DeviceError, httpx.HTTPError, OSError, TypeError, ValueError):
            return DomeShutterState.UNKNOWN
        return _DOME_SHUTTER_STATE.get(raw, DomeShutterState.UNKNOWN)

    async def open_shutter(self) -> None:
        await self._put("openshutter")

    async def close_shutter(self) -> None:
        # SAFETY: this does NOT park. sequence/roof.close_observatory owns the
        # park-first ordering (requires_park_before_close); here we only command
        # the shutter closed.
        await self._put("closeshutter")

    async def abort(self) -> None:
        await self._put("abortslew")

    async def get_slaved(self) -> bool:
        try:
            return bool(await self._get("slaved"))
        except (DeviceError, httpx.HTTPError, OSError):
            return False

    async def set_slaved(self, on: bool) -> None:
        if not self.can_slave:
            raise DeviceError(f"{self.name} cannot slave to the mount")
        await self._put("slaved", Slaved=bool(on))


#: ASCOM CoverStatus enum -> AstroDeck CoverState (PRO-5). Same integer order.
_COVER_STATE = {
    0: CoverState.NOT_PRESENT,
    1: CoverState.CLOSED,
    2: CoverState.MOVING,
    3: CoverState.OPEN,
    4: CoverState.UNKNOWN,
    5: CoverState.ERROR,
}

#: ASCOM CalibratorStatus enum -> AstroDeck calibrator-state string. NotReady(2)
#: and Unknown(4) both collapse to "unknown" (the panel is not settled/known).
_CALIBRATOR_STATE = {
    0: "not_present",
    1: "off",
    2: "unknown",
    3: "ready",
    4: "unknown",
    5: "error",
}


class AlpacaCoverCalibrator(_AlpacaDevice, CoverCalibrator):
    """ASCOM ICoverCalibratorV1 flat panel (+ optional motorized cover) — the
    F-F role (PRO-5).

    ``get_cover_state``/``get_calibrator_state`` are tolerant state reads that
    degrade to UNKNOWN/"unknown" rather than raising. ``calibrator_on`` clamps
    the requested level into ``0..max_brightness`` (both probed at connect), and
    ``close_cover`` is gated on the probed ``has_cover`` exactly like
    ``AlpacaRotator.set_reverse`` gates on ``can_reverse``.
    """

    dev_type = "covercalibrator"

    async def connect(self) -> None:
        await _AlpacaDevice.connect(self)
        # MaxBrightness -> clamp ceiling. Keep the base default (1, an on/off-only
        # panel) if the driver doesn't report it or reports a nonsense <1.
        try:
            mb = int(await self._get("maxbrightness"))
            if mb >= 1:
                self.max_brightness = mb
        except (DeviceError, httpx.HTTPError, OSError, TypeError, ValueError):
            pass
        # Derive has_cover: CoverState NotPresent(0) => no motorized cover, so the
        # cover methods correctly raise. Any error leaves has_cover at its False
        # default (fail-safe: don't command a cover we can't confirm).
        try:
            self.has_cover = int(await self._get("coverstate")) != 0
        except (DeviceError, httpx.HTTPError, OSError, TypeError, ValueError):
            self.has_cover = False

    async def get_brightness(self) -> int:
        return int(await self._get("brightness"))

    async def get_calibrator_state(self) -> str:
        try:
            raw = int(await self._get("calibratorstate"))
        except (DeviceError, httpx.HTTPError, OSError, TypeError, ValueError):
            return "unknown"
        return _CALIBRATOR_STATE.get(raw, "unknown")

    async def calibrator_on(self, brightness: int) -> None:
        level = max(0, min(self.max_brightness, int(brightness)))
        await self._put("calibratoron", Brightness=level)

    async def calibrator_off(self) -> None:
        await self._put("calibratoroff")

    async def get_cover_state(self) -> CoverState:
        try:
            raw = int(await self._get("coverstate"))
        except (DeviceError, httpx.HTTPError, OSError, TypeError, ValueError):
            return CoverState.UNKNOWN
        return _COVER_STATE.get(raw, CoverState.UNKNOWN)

    async def open_cover(self) -> None:
        await self._put("opencover")

    async def close_cover(self) -> None:
        if not self.has_cover:
            raise DeviceError(f"{self.name} has no cover")
        await self._put("closecover")


DEVICE_CLASSES = {
    "camera": AlpacaCamera,
    "telescope": AlpacaTelescope,
    "focuser": AlpacaFocuser,
    "rotator": AlpacaRotator,
    "filterwheel": AlpacaFilterWheel,
    "switch": AlpacaSwitch,
    "safetymonitor": AlpacaSafetyMonitor,
    "dome": AlpacaDome,
    "covercalibrator": AlpacaCoverCalibrator,
}


def make_device(host: str, port: int, dev_type: str, dev_num: int, name: str):
    cls = DEVICE_CLASSES.get(dev_type.lower())
    if cls is None:
        raise DeviceError(f"unsupported Alpaca device type: {dev_type}")
    return cls(AlpacaConnection(host, port), dev_num, name)
