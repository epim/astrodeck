#!/usr/bin/env python3
"""Unprivileged network frontend for AstroDeck WiFi onboarding.

This process parses hostile HTTP/DNS traffic and never executes network tools,
writes system configuration, reads setup identity state, or talks to systemd.
All privileged effects are fixed operations on a credential-checked local
broker.  The broker independently owns the one-shot authorization deadline.
"""

import argparse
import errno
import html
import json
import math
import re
import secrets
import signal
import socket
import socketserver
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AP_IP = "10.42.0.1"
PORTAL_LIFETIME_S = 15 * 60
# networkd assigns the hotspot address a beat after the beacon starts; bind
# attempts before that fail with EADDRNOTAVAIL. Bounded by the broker's own
# window deadline, which is the real limit.
ADDRESS_WAIT_S = 20.0
MAX_FORM_BYTES = 4096
MAX_SSID_BYTES = 32
MAX_HTTP_WORKERS = 16
MAX_BROKER_REQUEST_BYTES = 8192
MAX_BROKER_RESPONSE_BYTES = 64 * 1024
BROKER_SOCKET = "/run/astrodeck-provision-broker.sock"

STATUS = {"phase": "portal", "error": "", "ssid": ""}
SCAN_CACHE: list[dict] = []
DONE = threading.Event()
SERVER_FAILED = threading.Event()
JOIN_LOCK = threading.Lock()
SCAN_LOCK = threading.Lock()
CSRF_TOKEN = secrets.token_urlsafe(32)


def log(message: str) -> None:
    # The frontend has no writable durable path. systemd captures stdout and
    # applies the journal's retention/rate policy.
    print(f"[provision-frontend] {message}", flush=True)


def valid_ssid(ssid: str) -> bool:
    if not isinstance(ssid, str):
        return False
    try:
        encoded = ssid.encode("utf-8")
    except UnicodeError:
        return False
    return (
        bool(encoded)
        and len(encoded) <= MAX_SSID_BYTES
        and all(ord(ch) >= 0x20 and ord(ch) != 0x7F for ch in ssid)
    )


def valid_psk(psk: str) -> bool:
    if not isinstance(psk, str):
        return False
    if psk == "":
        return True
    try:
        encoded = psk.encode("utf-8")
    except UnicodeError:
        return False
    return (
        8 <= len(encoded) <= 63
        and all(ord(ch) >= 0x20 and ord(ch) != 0x7F for ch in psk)
    )


def needs_sae(ssid: str, networks: list[dict]) -> bool:
    for network in networks:
        if network.get("ssid") == ssid:
            suites = set(network.get("akm", ()))
            return "SAE" in suites and "PSK" not in suites
    return False


def _json_object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _validated_networks(value) -> list[dict]:
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("invalid broker network list")
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"ssid", "signal", "akm"}:
            raise ValueError("invalid broker network entry")
        ssid = item["ssid"]
        signal_value = item["signal"]
        suites = item["akm"]
        if not valid_ssid(ssid):
            raise ValueError("invalid broker SSID")
        if (
            isinstance(signal_value, bool)
            or not isinstance(signal_value, (int, float))
            or not math.isfinite(float(signal_value))
            or not -200 <= float(signal_value) <= 100
        ):
            raise ValueError("invalid broker signal")
        if (
            not isinstance(suites, list)
            or len(suites) > 16
            or any(
                not isinstance(suite, str)
                or len(suite) > 32
                or not re.fullmatch(r"[A-Z0-9_-]+", suite)
                for suite in suites
            )
        ):
            raise ValueError("invalid broker authentication suite")
        result.append({"ssid": ssid, "signal": float(signal_value), "akm": list(suites)})
    return result


class BrokerClient:
    """One-request-per-connection client for the fixed-operation root broker."""

    def __init__(self, path: str = BROKER_SOCKET):
        self.path = path

    def _request(self, message: dict, *, timeout: float = 240.0) -> dict:
        encoded = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_BROKER_REQUEST_BYTES:
            raise RuntimeError("broker request is too large")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(self.path)
            client.sendall(encoded)
            client.shutdown(socket.SHUT_WR)
            chunks = []
            total = 0
            while True:
                block = client.recv(8192)
                if not block:
                    break
                total += len(block)
                if total > MAX_BROKER_RESPONSE_BYTES:
                    raise RuntimeError("broker response is too large")
                chunks.append(block)
        raw = b"".join(chunks)
        try:
            response = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_json_object_without_duplicates,
            )
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("broker returned an invalid response") from exc
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise RuntimeError("broker returned an invalid response")
        if response["ok"] is not True:
            raise RuntimeError("privileged WiFi operation was rejected")
        return response

    def open_window(self) -> dict:
        response = self._request({"version": 1, "operation": "open"}, timeout=360.0)
        authorized = response.get("authorized")
        if not isinstance(authorized, bool):
            raise RuntimeError("broker returned an invalid authorization state")
        if not authorized:
            if set(response) != {"ok", "authorized"}:
                raise RuntimeError("broker returned an invalid closed response")
            return response
        if set(response) != {"ok", "authorized", "ssid", "remaining_s", "networks"}:
            raise RuntimeError("broker returned an invalid open response")
        if not valid_ssid(response["ssid"]):
            raise RuntimeError("broker returned an invalid setup SSID")
        remaining = response["remaining_s"]
        if (
            isinstance(remaining, bool)
            or not isinstance(remaining, (int, float))
            or not math.isfinite(float(remaining))
            or not 0 < float(remaining) <= PORTAL_LIFETIME_S
        ):
            raise RuntimeError("broker returned an invalid setup deadline")
        response["remaining_s"] = float(remaining)
        response["networks"] = _validated_networks(response["networks"])
        return response

    def scan(self) -> list[dict]:
        response = self._request({"version": 1, "operation": "scan"}, timeout=45.0)
        if set(response) != {"ok", "networks"}:
            raise RuntimeError("broker returned an invalid scan response")
        return _validated_networks(response["networks"])

    def join(self, ssid: str, psk: str, sae: bool) -> bool:
        response = self._request(
            {
                "version": 1,
                "operation": "join",
                "ssid": ssid,
                "psk": psk,
                "sae": sae,
            },
            timeout=600.0,
        )
        if set(response) != {"ok", "joined"} or not isinstance(response["joined"], bool):
            raise RuntimeError("broker returned an invalid join response")
        return response["joined"]

    def close(self) -> None:
        response = self._request({"version": 1, "operation": "close"}, timeout=30.0)
        if set(response) != {"ok", "closed"} or response["closed"] is not True:
            raise RuntimeError("broker returned an invalid close response")


BROKER = BrokerClient()


PAGE_CSS = (
    "body{font-family:system-ui;margin:0;background:#101418;color:#e8e6e3}"
    ".w{max-width:26rem;margin:0 auto;padding:1.5rem}"
    "h1{font-size:1.3rem}h1 span{color:#e8a33d}"
    "ul{list-style:none;padding:0}li{margin:.25rem 0}"
    "li a{display:block;padding:.6rem .8rem;background:#1a2027;border-radius:.5rem;"
    "color:#e8e6e3;text-decoration:none}"
    "li a small{float:right;opacity:.6}"
    "input{width:100%;box-sizing:border-box;padding:.6rem;margin:.3rem 0 .8rem;"
    "border-radius:.5rem;border:1px solid #333;background:#1a2027;color:#e8e6e3}"
    "button{width:100%;padding:.7rem;border:0;border-radius:.5rem;"
    "background:#e8a33d;color:#101418;font-weight:600;font-size:1rem}"
    ".err{background:#4a1f1f;border-radius:.5rem;padding:.6rem .8rem;margin:.8rem 0}"
)


def render_portal(networks: list[dict], error: str = "", ssid: str = "") -> str:
    items = "".join(
        f'<li><a href="/?ssid={urllib.parse.quote(network["ssid"])}">'
        f'{html.escape(network["ssid"])}<small>{int(network["signal"])} dBm</small></a></li>'
        for network in networks[:12]
    )
    rendered_error = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AstroDeck setup</title><style>{PAGE_CSS}</style></head><body><div class="w">
<h1><span>AstroDeck</span> WiFi setup</h1>
<p>Pick your home network and enter its password. The device will join it and
this hotspot will disappear.</p>{rendered_error}
<form method="post" action="/connect">
<input type="hidden" name="csrf" value="{html.escape(CSRF_TOKEN, quote=True)}">
<label>Network</label>
<input name="ssid" required value="{html.escape(ssid, quote=True)}" placeholder="Your WiFi name">
<label>Password</label>
<input name="psk" type="password" placeholder="WiFi password (blank if open)">
<button type="submit">Connect</button></form>
<h1>Nearby networks</h1><ul>{items or "<li>none seen yet</li>"}</ul>
<form method="post" action="/rescan">
<input type="hidden" name="csrf" value="{html.escape(CSRF_TOKEN, quote=True)}">
<button type="submit">Rescan</button></form>
</div></body></html>"""


def render_joining(ssid: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AstroDeck setup</title><style>{PAGE_CSS}</style></head><body><div class="w">
<h1><span>AstroDeck</span> is joining {html.escape(ssid)}</h1>
<p>This hotspot will now switch off. If the join works you will find the device
on your network through its configured AstroDeck address or your router's device list.</p>
<p>If the password was wrong, reconnect to the same setup hotspot and try again
before the setup window expires.</p>
</div></body></html>"""


class Portal(BaseHTTPRequestHandler):
    server_version = "AstroDeckSetup/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, fmt, *args):
        log("http " + (fmt % args))

    def _send(self, body: str, code: int = 200, ctype: str = "text/html"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _reject(self, body: str, code: int) -> None:
        self.close_connection = True
        self._send(body, code=code, ctype="text/plain")

    def _trusted_request_metadata(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0].lower() not in {AP_IP, f"{AP_IP}:80"}:
            self._reject("Misdirected request", 421)
            return False
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or (
            origins and origins[0].lower() not in {f"http://{AP_IP}", f"http://{AP_IP}:80"}
        ):
            self._reject("Forbidden", 403)
            return False
        fetch_sites = self.headers.get_all("Sec-Fetch-Site", [])
        if len(fetch_sites) > 1 or (
            fetch_sites and fetch_sites[0].lower() not in {"same-origin", "none"}
        ):
            self._reject("Forbidden", 403)
            return False
        return True

    def _form(self) -> dict[str, list[str]] | None:
        if self.headers.get_all("Transfer-Encoding", []):
            self._reject("Transfer-Encoding is not supported", 400)
            return None
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            self._reject("Length required", 411)
            return None
        if len(lengths) != 1 or re.fullmatch(r"(?:0|[1-9][0-9]*)", lengths[0]) is None:
            self._reject("Invalid Content-Length", 400)
            return None
        length = int(lengths[0])
        if length > MAX_FORM_BYTES:
            self._reject("Request too large", 413)
            return None
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("short request body")
            return urllib.parse.parse_qs(
                raw.decode("utf-8"),
                keep_blank_values=True,
                max_num_fields=8,
                strict_parsing=True,
            )
        except (OSError, UnicodeError, ValueError):
            self._reject("Invalid form", 400)
            return None

    def _csrf_ok(self, form: dict[str, list[str]]) -> bool:
        values = form.get("csrf", [])
        return len(values) == 1 and bool(values[0]) and secrets.compare_digest(
            values[0], CSRF_TOKEN
        )

    def _redirect_to_portal(self):
        self.send_response(302)
        self.send_header("Location", f"http://{AP_IP}/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._trusted_request_metadata():
            return
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self._send(
                render_portal(SCAN_CACHE, STATUS["error"], query.get("ssid", [""])[0])
            )
        elif path == "/rescan":
            self._redirect_to_portal()
        elif path == "/status":
            self._send(json.dumps(STATUS), ctype="application/json")
        else:
            self._redirect_to_portal()

    def do_POST(self):
        global SCAN_CACHE
        if not self._trusted_request_metadata():
            return
        if DONE.is_set():
            self._reject("Setup window closed", 503)
            return
        path = urllib.parse.urlparse(self.path).path
        if path not in {"/connect", "/rescan"}:
            self._reject("Not found", 404)
            return
        form = self._form()
        if form is None:
            return
        if not self._csrf_ok(form):
            self._send("Forbidden", code=403, ctype="text/plain")
            return
        if path == "/rescan":
            if SCAN_LOCK.acquire(blocking=False):
                try:
                    SCAN_CACHE = BROKER.scan() or SCAN_CACHE
                except Exception:
                    STATUS["error"] = "Could not scan for networks; try again."
                finally:
                    SCAN_LOCK.release()
            self._redirect_to_portal()
            return
        ssids = form.get("ssid", [])
        psks = form.get("psk", [])
        if len(ssids) != 1 or len(psks) != 1:
            self._reject("Invalid form", 400)
            return
        ssid = ssids[0].strip()
        psk = psks[0]
        if not valid_ssid(ssid):
            STATUS["error"] = "Network name must be 1-32 bytes without control characters."
            self._redirect_to_portal()
            return
        if not valid_psk(psk):
            STATUS["error"] = (
                "WiFi passwords are 8-63 bytes without control characters "
                "(or blank for open networks)."
            )
            self._redirect_to_portal()
            return
        if not JOIN_LOCK.acquire(blocking=False):
            self._send("A connection attempt is already running.", code=409, ctype="text/plain")
            return
        sae = needs_sae(ssid, SCAN_CACHE)
        STATUS.update(phase="joining", ssid=ssid, error="")
        try:
            self._send(render_joining(ssid))
            threading.Thread(
                target=self._join,
                args=(ssid, psk, sae),
                daemon=True,
            ).start()
        except BaseException:
            JOIN_LOCK.release()
            raise

    def _join(self, ssid: str, psk: str, sae: bool):
        try:
            time.sleep(1.5)
            if DONE.is_set():
                return
            try:
                if BROKER.join(ssid, psk, sae):
                    DONE.set()
                    return
                STATUS.update(
                    phase="portal",
                    error=f"Could not join {ssid!r} — check the password.",
                )
            except Exception:
                STATUS.update(phase="portal", error="WiFi join failed; try again.")
        finally:
            JOIN_LOCK.release()


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = MAX_HTTP_WORKERS

    def __init__(self, *args, max_workers: int = MAX_HTTP_WORKERS, **kwargs):
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        self.request_queue_size = max_workers
        self._slots = threading.BoundedSemaphore(max_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class CaptiveDNS(socketserver.UDPServer):
    allow_reuse_address = True


class DNSHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, response_socket = self.request
        if len(data) < 12:
            return
        transaction_id = data[:2]
        question_data = data[12:]
        end = question_data.find(b"\x00")
        if end == -1 or len(question_data) < end + 5:
            return
        question = question_data[: end + 5]
        query_type = int.from_bytes(question_data[end + 1: end + 3], "big")
        if query_type not in (1, 255):
            response = (
                transaction_id
                + b"\x84\x00\x00\x01\x00\x00\x00\x00\x00\x00"
                + question
            )
        else:
            answer = (
                b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x0a"
                + b"\x00\x04"
                + socket.inet_aton(AP_IP)
            )
            response = (
                transaction_id
                + b"\x84\x00\x00\x01\x00\x01\x00\x00\x00\x00"
                + question
                + answer
            )
        response_socket.sendto(response, self.client_address)


def _serve_until_stopped(server, label: str) -> None:
    try:
        server.serve_forever()
    except BaseException:
        log(f"{label} server stopped unexpectedly")
        SERVER_FAILED.set()
        DONE.set()


def _bind_when_addressed(factory, label: str):
    """Construct a server once the hotspot address exists on the interface.

    The broker reports the AP up when the radio beacons; systemd-networkd
    assigns 10.42.0.1 shortly after that, and binding it earlier raises
    EADDRNOTAVAIL (seen on the appliance 2026-09-03). Any other error, or the
    address never arriving, propagates unchanged.
    """
    deadline = time.monotonic() + ADDRESS_WAIT_S
    waited = False
    while True:
        try:
            server = factory()
        except OSError as exc:
            if exc.errno != errno.EADDRNOTAVAIL or time.monotonic() >= deadline:
                raise
            if not waited:
                log(f"{label}: waiting for the hotspot address")
                waited = True
            time.sleep(0.5)
            continue
        return server


def main_run() -> int:
    global SCAN_CACHE
    DONE.clear()
    SERVER_FAILED.clear()
    STATUS.update(phase="portal", error="", ssid="")
    try:
        opened = BROKER.open_window()
    except Exception:
        log("privileged WiFi broker is unavailable")
        return 1
    if not opened["authorized"]:
        log("no setup authorization is armed; provisioning remains closed")
        return 0

    SCAN_CACHE = opened["networks"]
    deadline = time.monotonic() + opened["remaining_s"]
    httpd = None
    dns = None
    http_started = False
    dns_started = False
    old_handlers = {}
    result = 0
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, lambda _signum, _frame: DONE.set())
        httpd = _bind_when_addressed(lambda: BoundedHTTPServer((AP_IP, 80), Portal), "HTTP")
        threading.Thread(
            target=_serve_until_stopped, args=(httpd, "HTTP"), daemon=True
        ).start()
        http_started = True
        dns = _bind_when_addressed(lambda: CaptiveDNS((AP_IP, 53), DNSHandler), "DNS")
        threading.Thread(
            target=_serve_until_stopped, args=(dns, "DNS"), daemon=True
        ).start()
        dns_started = True
        log(f"portal serving on http://{AP_IP}/ for SSID {opened['ssid']}")
        while not DONE.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log("portal authorization window expired")
                DONE.set()
                break
            DONE.wait(min(remaining, 1.0))
        if SERVER_FAILED.is_set():
            result = 1
    except Exception as exc:
        # class and errno only: enough to diagnose a bind or broker failure on
        # a headless board, never a value that could carry a credential.
        log(f"provisioning portal failed: {type(exc).__name__} errno={getattr(exc, 'errno', None)}")
        result = 1
    finally:
        DONE.set()
        for server, started, label in (
            (dns, dns_started, "DNS"),
            (httpd, http_started, "HTTP"),
        ):
            if server is None:
                continue
            try:
                if started:
                    server.shutdown()
            except Exception:
                log(f"{label} server shutdown failed")
                result = 1
            try:
                server.server_close()
            except Exception:
                log(f"{label} socket close failed")
                result = 1
        try:
            BROKER.close()
        except Exception:
            # The broker's independent deadline remains the final fail-safe.
            log("privileged WiFi broker close failed")
            result = 1
        for signum, handler in old_handlers.items():
            try:
                signal.signal(signum, handler)
            except Exception:
                result = 1
        log("provisioning portal closed")
    return result


def main_selftest() -> int:
    assert valid_psk("example88") and valid_psk("") and not valid_psk("short")
    assert valid_ssid("home") and not valid_ssid("x" * 33)
    for page in (
        render_portal([{"ssid": "x<y", "signal": -40, "akm": []}], "err", 'a"b'),
        render_joining("net<script>"),
    ):
        assert "<script>" not in page.replace("&lt;script&gt;", "")
    print("frontend self-test OK")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", default="run", choices=["run", "self-test"])
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_test or arguments.mode == "self-test":
        sys.exit(main_selftest())
    sys.exit(main_run())
