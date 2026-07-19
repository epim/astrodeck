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

_ALPACA_DRIVER_ERROR = 1024  # generic ASCOM driver error number
_txn_lock = threading.Lock()
_server_txn = 0


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
        except (ComTimeoutError, KeyError) as e:
            # Timeout / unknown route -> HTTP 500 so the client's _unwrap raises
            # DeviceError immediately (fast, honest failure; spec §4).
            return 500, {"Value": None, "ErrorNumber": _ALPACA_DRIVER_ERROR,
                         "ErrorMessage": str(e),
                         "ClientTransactionID": ctid,
                         "ServerTransactionID": _next_server_txn()}, "application/json"
        except Exception as e:  # a COM/driver exception -> Alpaca ErrorNumber
            return 200, {"Value": None, "ErrorNumber": _ALPACA_DRIVER_ERROR,
                         "ErrorMessage": str(e),
                         "ClientTransactionID": ctid,
                         "ServerTransactionID": _next_server_txn()}, "application/json"

    def _dispatch(self, verb: str, dev_type: str, dev_num: int, method: str,
                  params: dict) -> Any:
        if method == "connected":
            return self._connected(verb, dev_type, dev_num, params)
        table = DEVICE_API.get(dev_type, {}).get(verb, {})
        fn = table.get(method)
        if fn is None:
            raise KeyError(f"unsupported {verb} {dev_type}/{method}")
        dev = self._get_or_create(dev_type, dev_num)
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
