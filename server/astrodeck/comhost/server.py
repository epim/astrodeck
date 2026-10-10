# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Minimal Alpaca device server (COM-T2): ThreadingHTTPServer on 127.0.0.1
serving /management/v1/configureddevices + /api/v1/<type>/<n>/<method>, with the
per-device STA ComDevice cache and the Alpaca envelope the EXISTING AstroDeck
Alpaca client parses (spec §3.1). Device-type method tables (DEVICE_API) are
filled by COM-T3/T4/T5; this module owns the dispatch + envelope + lifecycle.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from typing import Any, Callable

from ..devices import ascom_registry
from ..devices.ascom_registry import AscomDriver
from .device import ComDevice, ComTimeoutError

# DEVICE_API[dev_type]["get"|"put"][method] = fn(obj, params) -> Value.
# Registered by the device-type tasks (COM-T3/T4/T5); "connected" is universal.
DEVICE_API: dict[str, dict[str, dict[str, Callable[[Any, dict], Any]]]] = {}

# Alpaca ErrorNumbers (#872). 0x400 is NotImplemented and nothing else: a client
# reads it as "this driver cannot do that", so a driver FAILURE must never wear
# it. 0x500 is the first driver-specific number, the honest answer for a driver
# exception that names nothing more specific.
_ALPACA_NOT_IMPLEMENTED = 0x400
_ALPACA_NOT_CONNECTED = 0x407
_ALPACA_DRIVER_ERROR = 0x500

# An ASCOM driver raises its error as HRESULT 0x80040000 + the Alpaca number, so
# the low 12 bits of 0x80040400..0x80040FFF ARE the ErrorNumber: 0x401
# InvalidValue, 0x402 ValueNotSet, 0x407 NotConnected, 0x408 InvalidWhileParked,
# 0x409 InvalidWhileSlaved, 0x40B InvalidOperation, 0x500..0xFFF a driver's own.
_ASCOM_HRESULT_FIRST = 0x80040400
_ASCOM_HRESULT_LAST = 0x80040FFF
# IDispatch::Invoke reports a driver's exception as this HRESULT and carries the
# driver's own HRESULT in the EXCEPINFO scode (comtypes COMError.details[4]).
_DISP_E_EXCEPTION = 0x80020009
# Generic HRESULTs a driver (or the .NET interop under it) raises for the same
# conditions the ASCOM codes name.
_GENERIC_HRESULT_NUMBER = {
    0x80004001: _ALPACA_NOT_IMPLEMENTED,  # E_NOTIMPL
    0x80020003: _ALPACA_NOT_IMPLEMENTED,  # DISP_E_MEMBERNOTFOUND
    0x80070057: 0x401,  # E_INVALIDARG
    0x80131502: 0x401,  # COR_E_ARGUMENTOUTOFRANGE
    0x80131509: 0x40B,  # COR_E_INVALIDOPERATION
}
_txn_lock = threading.Lock()
_server_txn = 0


def _com_hresult(exc: BaseException) -> "int | None":
    """The unsigned HRESULT a COM exception (comtypes COMError: hresult, text,
    details) carries, or None for an exception that is not a COM one. A
    DISP_E_EXCEPTION is unwrapped to the driver's own HRESULT when it gave one."""
    hr = getattr(exc, "hresult", None)
    if isinstance(hr, bool) or not isinstance(hr, int):
        return None
    hr &= 0xFFFFFFFF
    if hr == _DISP_E_EXCEPTION:
        try:
            scode = exc.details[4]  # type: ignore[attr-defined]
        except (AttributeError, TypeError, IndexError):
            return hr
        if isinstance(scode, int) and not isinstance(scode, bool) and scode:
            return scode & 0xFFFFFFFF
    return hr


class NotConnectedError(Exception):
    """A request reached a device that has no live COM object: it was never
    connected, or its slot was fault-evicted and rebuilt (#937)."""


def _alpaca_error_number(exc: BaseException) -> int:
    """The Alpaca ErrorNumber for an exception a COM driver call raised."""
    if isinstance(exc, NotImplementedError):
        return _ALPACA_NOT_IMPLEMENTED
    if isinstance(exc, NotConnectedError):
        return _ALPACA_NOT_CONNECTED
    hr = _com_hresult(exc)
    if hr is None:
        return _ALPACA_DRIVER_ERROR
    if _ASCOM_HRESULT_FIRST <= hr <= _ASCOM_HRESULT_LAST:
        return hr & 0xFFF
    return _GENERIC_HRESULT_NUMBER.get(hr, _ALPACA_DRIVER_ERROR)


def _next_server_txn() -> int:
    global _server_txn
    with _txn_lock:
        _server_txn += 1
        return _server_txn


def _make_com_device(progid: str) -> ComDevice:  # seam: tests patch this
    return ComDevice(progid)


class ComHost:
    """Enumeration table + per-(type,dev_num) ComDevice cache + dispatch."""

    def __init__(self, drivers: "list[AscomDriver] | None" = None):
        self.drivers = drivers if drivers is not None else ascom_registry.enumerate()
        self._devices: dict[tuple[str, int], ComDevice] = {}
        self._lock = threading.Lock()

    # ---- management API ----
    def configured_devices(self) -> list[dict]:
        return [{"DeviceType": d.ascom_type, "DeviceNumber": d.dev_num,
                 "DeviceName": d.name, "UniqueID": d.progid}
                for d in self.drivers]

    # ---- device lifecycle ----
    def _get_or_create(self, dev_type: str, dev_num: int) -> ComDevice:
        key = (dev_type, dev_num)
        with self._lock:
            dev = self._devices.get(key)
            if dev is None:
                progid = ascom_registry.progid_for(dev_type, dev_num, self.drivers)
                if progid is None:
                    raise KeyError(f"no ASCOM driver for {dev_type} #{dev_num}")
                dev = _make_com_device(progid)
                self._devices[key] = dev
            return dev

    def _drop(self, dev_type: str, dev_num: int) -> None:
        with self._lock:
            self._devices.pop((dev_type, dev_num), None)

    def _evict(self, dev_type: str, dev_num: int, dev: ComDevice) -> None:
        """Fault-evict a wedged device (COM-T6 obligation 1): pop its slot and
        abandon its (blocked) STA thread. The next _get_or_create reconstructs a
        fresh ComDevice on a fresh thread, so a single timeout does not brick the
        device for the host's lifetime. Best-effort — abandon() never raises.

        Only if the slot still holds ``dev``, the device that timed out (#988).
        Two calls queued behind one wedge time out a deadline apart; the first
        evicts, the client reconnects into a fresh slot, and the second's late
        timeout is about the OLD object. Popping by key would tear down the
        healthy device that replaced it. A device that is no longer in its slot
        was already abandoned (evicted) or disconnected, so there is nothing
        left to do for it."""
        key = (dev_type, dev_num)
        with self._lock:
            if self._devices.get(key) is not dev:
                return
            del self._devices[key]
        try:
            dev.abandon()
        except Exception:  # pragma: no cover - abandon is already best-effort
            pass

    def close(self) -> None:
        with self._lock:
            devs = list(self._devices.values())
            self._devices.clear()
        for dev in devs:
            try:
                dev.disconnect()
            except Exception:
                pass

    # ---- request handling (returns HTTP status, body, content-type) ----
    def handle(self, verb: str, dev_type: str, dev_num: int, method: str,
               params: dict) -> tuple[int, dict, str]:
        ctid = _coerce_int(params.get("ClientTransactionID"))
        try:
            value = self._dispatch(verb, dev_type, dev_num, method, params)
            return 200, self._ok(value, ctid), "application/json"
        except ComTimeoutError as e:
            # Fault-eviction (COM-T2 review, Medium; assigned to COM-T6): a wedged
            # COM call leaks its STA thread AND head-of-line-blocks every future
            # call to this device (a new call would queue behind the wedge). Drop
            # the device slot + abandon its thread so the NEXT call builds a FRESH
            # ComDevice on a FRESH thread — recovery is per-device, NOT "restart
            # the whole host". Then surface the fault as HTTP 500 (client _unwrap
            # -> DeviceError).
            self._evict(dev_type, dev_num, e.device)
            return 500, self._err(str(e), ctid), "application/json"
        except KeyError as e:
            # Unknown route / no driver -> HTTP 500 so the client's _unwrap raises
            # DeviceError immediately (fast, honest failure; spec §4). No device
            # slot to evict (either none was created or the method is unmapped).
            # This is the host's OWN route table saying it serves no such method,
            # so NotImplemented is true here (unlike a driver exception below).
            return 500, self._err(str(e), ctid, _ALPACA_NOT_IMPLEMENTED), \
                "application/json"
        except Exception as e:  # a COM/driver exception -> Alpaca ErrorNumber
            return 200, self._err(str(e), ctid, _alpaca_error_number(e)), \
                "application/json"

    def _dispatch(self, verb: str, dev_type: str, dev_num: int, method: str,
                  params: dict) -> Any:
        if method == "connected":
            return self._connected(verb, dev_type, dev_num, params)
        table = DEVICE_API.get(dev_type, {}).get(verb, {})
        fn = table.get(method)
        if fn is None:
            raise KeyError(f"unsupported {verb} {dev_type}/{method}")
        dev = self._get_or_create(dev_type, dev_num)
        if not dev.connected:
            # The COM object does not exist until Connected=true, so the handler
            # would run against None and answer an AttributeError as 0x500.
            raise NotConnectedError(f"{dev_type} #{dev_num} is not connected")
        return dev.submit(lambda obj: fn(obj, params))

    def _connected(self, verb: str, dev_type: str, dev_num: int,
                   params: dict) -> Any:
        if verb == "get":
            key = (dev_type, dev_num)
            dev = self._devices.get(key)
            return bool(dev and dev.connected)
        want = _coerce_bool(params.get("Connected"))
        if want:
            self._get_or_create(dev_type, dev_num).connect()
        else:
            dev = self._devices.get((dev_type, dev_num))
            if dev is not None:
                dev.disconnect()
                self._drop(dev_type, dev_num)
        return None

    @staticmethod
    def _ok(value: Any, ctid: int) -> dict:
        return {"Value": value, "ErrorNumber": 0, "ErrorMessage": "",
                "ClientTransactionID": ctid,
                "ServerTransactionID": _next_server_txn()}

    @staticmethod
    def _err(message: str, ctid: int, number: int = _ALPACA_DRIVER_ERROR) -> dict:
        return {"Value": None, "ErrorNumber": number,
                "ErrorMessage": message, "ClientTransactionID": ctid,
                "ServerTransactionID": _next_server_txn()}


def _coerce_int(v) -> int:
    try:
        return int(v[0] if isinstance(v, list) else v)
    except (TypeError, ValueError):
        return 0


def _coerce_bool(v) -> bool:
    s = (v[0] if isinstance(v, list) else v)
    return str(s).strip().lower() in ("true", "1", "yes")


def _make_handler(host: ComHost):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # silence stdlib access logging
            pass

        def _send(self, status: int, body, ctype: str):
            if isinstance(body, (dict, list)):
                payload = json.dumps(body).encode()
            else:
                payload = body
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            if parsed.path == "/management/v1/configureddevices":
                return self._send(200, {"Value": host.configured_devices(),
                                        "ErrorNumber": 0, "ErrorMessage": "",
                                        "ClientTransactionID": _coerce_int(
                                            params.get("ClientTransactionID")),
                                        "ServerTransactionID": _next_server_txn()},
                                  "application/json")
            route = _parse_device_route(parsed.path)
            if route is None:
                return self._send(400, {"Value": None, "ErrorNumber": 1,
                                        "ErrorMessage": "bad route"},
                                  "application/json")
            dev_type, dev_num, method = route
            status, body, ctype = host.handle("get", dev_type, dev_num, method, params)
            self._send(status, body, ctype)

        def do_PUT(self):
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length).decode() if length else ""
            params = parse_qs(raw)
            route = _parse_device_route(parsed.path)
            if route is None:
                return self._send(400, {"Value": None, "ErrorNumber": 1,
                                        "ErrorMessage": "bad route"},
                                  "application/json")
            dev_type, dev_num, method = route
            status, body, ctype = host.handle("put", dev_type, dev_num, method, params)
            self._send(status, body, ctype)

    return Handler


def _parse_device_route(path: str):
    # /api/v1/<type>/<n>/<method>
    parts = [p for p in path.split("/") if p]
    if len(parts) == 5 and parts[0] == "api" and parts[1] == "v1":
        try:
            return parts[2].lower(), int(parts[3]), parts[4].lower()
        except ValueError:
            return None
    return None


def serve(port: int = 0, portfile: "str | None" = None, *,
          drivers=None) -> ThreadingHTTPServer:
    """Bind 127.0.0.1:port (0 = ephemeral), serve in a daemon thread, and (if
    given) write {'pid','port'} to portfile. Returns the running server; the
    caller stops it with .shutdown()."""
    import os
    host = ComHost(drivers=drivers)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(host))
    httpd.com_host = host  # so __main__ / tests can reach it
    chosen = httpd.server_address[1]
    if portfile:
        tmp = f"{portfile}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "port": chosen}, f)
        os.replace(tmp, portfile)  # atomic publish (server reads it)
    t = threading.Thread(target=httpd.serve_forever, name="comhost-http",
                         daemon=True)
    t.start()
    return httpd


# Register the device-type method tables (COM-T3+): importing each module runs
# its register() into DEVICE_API. Kept at module end to avoid an import cycle
# (handlers import DEVICE_API from here).
from . import handlers_telescope as _ht  # noqa: E402,F401
from . import handlers_camera as _hc  # noqa: E402,F401
from . import handlers_misc as _hm  # noqa: E402,F401
from . import handlers_aux as _ha  # noqa: E402,F401
