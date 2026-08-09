#!/usr/bin/env python3
"""AstroDeck WiFi provisioning hotspot.

Runs at every boot on the appliance. If the board reaches a network, exits
quietly. If not, raises a WPA2 hotspot (SSID AstroDeck-XXXX) with a captive
portal; the user submits home WiFi credentials from a phone, the board writes
a netplan config and joins. See
docs/superpowers/specs/2026-08-08-wifi-provisioning-design.md.

Stdlib only — the image this ships on cannot install packages.
"""

import argparse
import html
import json
import os
import re
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AP_IP = "10.42.0.1"
AP_CIDR = "10.42.0.1/24"
AP_FREQ = 2437  # 2.4 GHz channel 6: phones universally see it
AP_PSK = "astrodeck"
IFACE = "wlan0"
RUN_DIR = "/run/astrodeck"
STATE_DIR = "/var/lib/astrodeck"
STATE_PATH = os.path.join(STATE_DIR, "provision-state.json")
NETPLAN_PATH = "/etc/netplan/30-astrodeck-wifi.yaml"
# 05- so it outranks netplan's generated 10-netplan-wlan0.network: systemd-networkd
# applies the first file (lexicographically) that matches an interface, and after a
# reboot with a netplan yaml present both files exist while the AP is up.
NETWORKD_PATH = "/run/systemd/network/05-astrodeck-ap.network"
PERSIST_LOG = "/var/lib/astrodeck/provision.log"
WPA_CONF = os.path.join(RUN_DIR, "ap.conf")
WPA_PID = os.path.join(RUN_DIR, "wpa-ap.pid")
ONLINE_WAIT_S = 90
JOIN_WAIT_S = 45

# Portal state shared between the join thread and request handlers.
STATUS = {"phase": "portal", "error": "", "ssid": ""}
SCAN_CACHE: list[dict] = []
DONE = threading.Event()


def log(msg: str) -> None:
    print(f"[provision] {msg}", flush=True)
    # journald is volatile under armbian-ramlog and dies with hard resets;
    # keep our own breadcrumb trail somewhere that survives power loss.
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(PERSIST_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    log("+ " + " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------- pure logic

def derive_ssid(mac: str) -> str:
    """AstroDeck-XXXX from the last 4 hex digits of a MAC address."""
    digits = re.sub(r"[^0-9a-fA-F]", "", mac)
    return "AstroDeck-" + digits[-4:].upper()


def yaml_dq(s: str) -> str:
    """Escape a string into a YAML double-quoted scalar."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def valid_psk(psk: str) -> bool:
    """WPA2 passphrase length rule; empty means an open network."""
    return psk == "" or 8 <= len(psk) <= 63


def emit_netplan(ssid: str, psk: str, sae: bool = False) -> str:
    if "\n" in ssid or "\n" in psk:
        raise ValueError("newline in credentials")
    if psk and sae:
        # WPA3-only network: plain `password:` emits WPA-PSK, which a
        # WPA3-only AP rejects regardless of the password being right.
        body = (f"          {yaml_dq(ssid)}:\n"
                "            auth:\n"
                "              key-management: sae\n"
                f"              password: {yaml_dq(psk)}\n")
    elif psk:
        body = (f"          {yaml_dq(ssid)}:\n"
                f"            password: {yaml_dq(psk)}\n")
    else:
        # an open network needs an explicit empty mapping value
        body = f"          {yaml_dq(ssid)}: {{}}\n"
    return (
        "# Written by astrodeck-provision. Do not hand-edit; rerun setup instead.\n"
        "network:\n"
        "  version: 2\n"
        "  renderer: networkd\n"
        "  wifis:\n"
        f"    {IFACE}:\n"
        "      dhcp4: true\n"
        "      dhcp6: true\n"
        "      access-points:\n"
        f"{body}"
    )


def emit_wpa_ap_conf(ssid: str, psk: str, freq: int = AP_FREQ) -> str:
    return (
        f"ctrl_interface=DIR=/run/wpa_supplicant\n"
        "ap_scan=1\n"
        "network={\n"
        f'    ssid="{ssid}"\n'
        "    mode=2\n"
        "    key_mgmt=WPA-PSK\n"
        f'    psk="{psk}"\n'
        "    proto=RSN\n"
        "    pairwise=CCMP\n"
        "    group=CCMP\n"
        f"    frequency={freq}\n"
        "}\n"
    )


def emit_networkd_ap() -> str:
    return (
        "[Match]\n"
        f"Name={IFACE}\n"
        "\n"
        "[Network]\n"
        f"Address={AP_CIDR}\n"
        "DHCPServer=yes\n"
        "\n"
        "[DHCPServer]\n"
        "PoolOffset=10\n"
        "PoolSize=64\n"
        "EmitDNS=yes\n"
        f"DNS={AP_IP}\n"
    )


# ------------------------------------------------------------------- network

def wlan_mac() -> str | None:
    try:
        with open(f"/sys/class/net/{IFACE}/address") as f:
            return f.read().strip()
    except OSError:
        return None


def have_default_route() -> bool:
    try:
        p = run(["ip", "route", "show", "default"], timeout=10)
        return bool(p.stdout.strip())
    except Exception:
        return False


def wait_route(seconds: int) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if have_default_route():
            return True
        time.sleep(3)
    return have_default_route()


def parse_scan(text: str) -> list[dict]:
    """Parse `iw dev wlan0 scan` output → [{ssid, signal, akm}] strongest first.

    akm collects RSN authentication suites across all BSSes broadcasting the
    SSID (e.g. {"PSK"}, {"SAE"}, {"PSK","SAE"} for WPA2/WPA3 mixed mode).
    """
    nets: dict[str, dict] = {}

    def commit(ssid, sig, akm):
        if not ssid or "\\x00" in ssid:
            return
        e = nets.setdefault(ssid, {"signal": -100.0, "akm": set()})
        if sig is not None and sig > e["signal"]:
            e["signal"] = sig
        e["akm"] |= akm

    ssid, sig, akm = None, None, set()
    for raw in text.splitlines():
        line = raw.strip()
        if raw.startswith("BSS "):
            commit(ssid, sig, akm)
            ssid, sig, akm = None, None, set()
        elif line.startswith("signal:"):
            m = re.search(r"(-?\d+(?:\.\d+)?)", line)
            sig = float(m.group(1)) if m else None
        elif line.startswith("SSID:"):
            ssid = line[5:].strip()
        elif line.startswith("* Authentication suites:"):
            akm |= set(line.split(":", 1)[1].split())
    commit(ssid, sig, akm)
    return [
        {"ssid": s, "signal": v["signal"], "akm": sorted(v["akm"])}
        for s, v in sorted(nets.items(), key=lambda kv: -kv[1]["signal"])
    ]


def needs_sae(ssid: str, nets: list[dict]) -> bool:
    """True when the scanned network offers SAE but not plain PSK (WPA3-only)."""
    for n in nets:
        if n["ssid"] == ssid:
            akm = set(n.get("akm", ()))
            return "SAE" in akm and "PSK" not in akm
    return False


def scan_networks() -> list[dict]:
    """Best-effort scan; returns [{ssid, signal, akm}] sorted strongest first."""
    run(["ip", "link", "set", IFACE, "up"], timeout=10)
    try:
        p = run(["iw", "dev", IFACE, "scan"], timeout=25)
    except subprocess.TimeoutExpired:
        return SCAN_CACHE
    if p.returncode != 0:
        log(f"scan failed: {p.stderr.strip()[:200]}")
        return SCAN_CACHE
    return parse_scan(p.stdout)


def ap_up(ssid: str) -> None:
    os.makedirs(RUN_DIR, exist_ok=True)
    # netplan's own supplicant fights us for wlan0 whenever a netplan wifi yaml
    # exists (i.e. after any previous provisioning attempt). Mask it for the
    # lifetime of the AP so systemd cannot restart it mid-handoff, then kill
    # whatever is currently attached to the interface.
    run(["systemctl", "mask", "--runtime", f"netplan-wpa-{IFACE}.service"],
        timeout=20)
    run(["systemctl", "stop", f"netplan-wpa-{IFACE}.service"], timeout=20)
    run(["pkill", "-F", WPA_PID], timeout=10)
    with open(WPA_CONF, "w") as f:
        f.write(emit_wpa_ap_conf(ssid, AP_PSK))
    os.makedirs(os.path.dirname(NETWORKD_PATH), exist_ok=True)
    with open(NETWORKD_PATH, "w") as f:
        f.write(emit_networkd_ap())
    run(["networkctl", "reload"], timeout=20)
    run(["ip", "link", "set", IFACE, "up"], timeout=10)
    last_err = ""
    for attempt in range(3):
        p = run(["wpa_supplicant", "-B", "-i", IFACE, "-c", WPA_CONF,
                 "-P", WPA_PID], timeout=20)
        if p.returncode == 0:
            break
        last_err = p.stderr.strip()
        log(f"wpa_supplicant AP start attempt {attempt + 1} failed: {last_err}")
        run(["pkill", "-f", f"wpa_supplicant.*{IFACE}"], timeout=10)
        time.sleep(2)
    else:
        raise RuntimeError(f"wpa_supplicant AP start failed: {last_err}")
    for _ in range(20):
        info = run(["iw", "dev", IFACE, "info"], timeout=10)
        if "type AP" in info.stdout:
            log(f"AP up: {ssid}")
            return
        time.sleep(1)
    log("warning: interface never reported type AP; continuing anyway")


def ap_down() -> None:
    run(["pkill", "-F", WPA_PID], timeout=10)
    try:
        os.remove(NETWORKD_PATH)
    except OSError:
        pass
    run(["systemctl", "unmask", "--runtime", f"netplan-wpa-{IFACE}.service"],
        timeout=20)
    run(["networkctl", "reload"], timeout=20)
    run(["ip", "addr", "flush", "dev", IFACE], timeout=10)


def try_join(ssid: str, psk: str, sae: bool = False) -> bool:
    log(f"joining {ssid!r} (sae={sae})")
    content = emit_netplan(ssid, psk, sae)
    fd = os.open(NETPLAN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    ap_down()
    run(["netplan", "apply"], timeout=60)
    if wait_route(JOIN_WAIT_S):
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATE_PATH, "w") as f:
            json.dump({"ssid": ssid, "joined_at": time.time(),
                       "result": "ok"}, f)
        log("joined; provisioning complete")
        return True
    log("no route after join; reverting to hotspot")
    try:
        os.remove(NETPLAN_PATH)
    except OSError:
        pass
    run(["netplan", "apply"], timeout=60)
    return False


# -------------------------------------------------------------------- portal

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


def render_portal(nets: list[dict], error: str = "", ssid: str = "") -> str:
    items = "".join(
        f'<li><a href="/?ssid={urllib.parse.quote(n["ssid"])}">'
        f'{html.escape(n["ssid"])}<small>{int(n["signal"])} dBm</small></a></li>'
        for n in nets[:12]
    )
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AstroDeck setup</title><style>{PAGE_CSS}</style></head><body><div class="w">
<h1><span>AstroDeck</span> WiFi setup</h1>
<p>Pick your home network and enter its password. The device will join it and
this hotspot will disappear.</p>{err}
<form method="post" action="/connect">
<label>Network</label>
<input name="ssid" required value="{html.escape(ssid, quote=True)}" placeholder="Your WiFi name">
<label>Password</label>
<input name="psk" type="password" placeholder="WiFi password (blank if open)">
<button type="submit">Connect</button></form>
<h1>Nearby networks</h1><ul>{items or "<li>none seen yet</li>"}</ul>
<p><a style="color:#e8a33d" href="/rescan">Rescan</a></p>
</div></body></html>"""


def render_joining(ssid: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AstroDeck setup</title><style>{PAGE_CSS}</style></head><body><div class="w">
<h1><span>AstroDeck</span> is joining {html.escape(ssid)}</h1>
<p>This hotspot will now switch off. If the join works you will find the device
on your network as <b>astropi</b> (try <b>http://astropi.local</b> or your
router's device list).</p>
<p>If the password was wrong, the <b>AstroDeck</b> hotspot reappears in about a
minute — reconnect to it and try again.</p>
</div></body></html>"""


class Portal(BaseHTTPRequestHandler):
    server_version = "AstroDeckSetup/1"

    def log_message(self, fmt, *args):  # journald, not stderr spam
        log("http " + (fmt % args))

    def _send(self, body: str, code: int = 200, ctype: str = "text/html"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _redirect_to_portal(self):
        self.send_response(302)
        self.send_header("Location", f"http://{AP_IP}/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        global SCAN_CACHE
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self._send(render_portal(SCAN_CACHE, STATUS["error"],
                                     q.get("ssid", [""])[0]))
        elif path == "/rescan":
            SCAN_CACHE = scan_networks() or SCAN_CACHE
            self._redirect_to_portal()
        elif path == "/status":
            self._send(json.dumps(STATUS), ctype="application/json")
        else:
            # captive-portal probes from every OS land here
            self._redirect_to_portal()

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/connect":
            self._redirect_to_portal()
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        ssid = form.get("ssid", [""])[0].strip()
        psk = form.get("psk", [""])[0]
        if not ssid or "\n" in ssid or "\n" in psk:
            STATUS["error"] = "Network name is required."
            self._redirect_to_portal()
            return
        if not valid_psk(psk):
            STATUS["error"] = "WiFi passwords are 8-63 characters (or blank for open networks)."
            self._redirect_to_portal()
            return
        sae = needs_sae(ssid, SCAN_CACHE)
        STATUS.update(phase="joining", ssid=ssid, error="")
        self._send(render_joining(ssid))
        threading.Thread(target=self._join, args=(ssid, psk, sae),
                         daemon=True).start()

    def _join(self, ssid: str, psk: str, sae: bool):
        time.sleep(1.5)  # let the response reach the phone before the AP drops
        try:
            if try_join(ssid, psk, sae):
                DONE.set()
                return
            STATUS.update(phase="portal",
                          error=f"Could not join {ssid!r} — check the password.")
        except Exception as e:
            STATUS.update(phase="portal", error=f"Join failed: {e}")
        try:
            ap_up(derive_ssid(wlan_mac() or "000000000000"))
        except Exception as e:
            log(f"FATAL: could not re-raise AP after failed join: {e}")
            DONE.set()


class CaptiveDNS(socketserver.ThreadingUDPServer):
    allow_reuse_address = True


class DNSHandler(socketserver.BaseRequestHandler):
    """Answer every A query with the portal IP so phones open the sheet."""

    def handle(self):
        data, sock = self.request
        if len(data) < 12:
            return
        # copy ID, set QR|AA, echo question, one answer pointing at us
        tid = data[:2]
        flags = b"\x84\x00"
        q = data[12:]
        end = q.find(b"\x00")
        if end == -1 or len(q) < end + 5:
            return
        question = q[: end + 5]
        qtype = int.from_bytes(q[end + 1: end + 3], "big")
        if qtype not in (1, 255):  # A or ANY; stay silent otherwise
            resp = tid + b"\x84\x00" + b"\x00\x01\x00\x00\x00\x00\x00\x00" + question
            sock.sendto(resp, self.client_address)
            return
        answer = (b"\xc0\x0c" + b"\x00\x01\x00\x01" + b"\x00\x00\x00\x0a"
                  + b"\x00\x04" + socket.inet_aton(AP_IP))
        resp = (tid + flags + b"\x00\x01\x00\x01\x00\x00\x00\x00"
                + question + answer)
        sock.sendto(resp, self.client_address)


# ---------------------------------------------------------------------- main

def main_run() -> int:
    log("astrodeck-provision starting")
    if wlan_mac() is None:
        log(f"no {IFACE} present — WiFi driver missing? exiting without blocking boot")
        return 0
    if wait_route(ONLINE_WAIT_S):
        log("network already up; nothing to do")
        return 0
    log("no network after wait; entering hotspot mode")
    global SCAN_CACHE
    SCAN_CACHE = scan_networks()
    log(f"scanned {len(SCAN_CACHE)} networks")
    ssid = derive_ssid(wlan_mac() or "000000000000")
    try:
        ap_up(ssid)
    except Exception as e:
        log(f"FATAL: cannot raise AP: {e}")
        return 0
    httpd = ThreadingHTTPServer(("0.0.0.0", 80), Portal)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        dns = CaptiveDNS((AP_IP, 53), DNSHandler)
        threading.Thread(target=dns.serve_forever, daemon=True).start()
    except OSError as e:
        log(f"captive DNS unavailable ({e}); portal reachable at http://{AP_IP}/")
    log(f"portal serving on http://{AP_IP}/ (SSID {ssid}, password {AP_PSK})")
    DONE.wait()
    httpd.shutdown()
    log("exiting")
    return 0


def main_selftest() -> int:
    assert derive_ssid("aa:bb:cc:dd:ee:ff") == "AstroDeck-EEFF"
    assert valid_psk("astrodeck") and valid_psk("") and not valid_psk("short")
    y = emit_netplan('Cafe "42"\\home', "pass word 8")
    assert '"Cafe \\"42\\"\\\\home"' in y and "password:" in y
    assert emit_netplan("open-net", "").strip().endswith("{}")
    assert "key-management: sae" in emit_netplan("w3", "12345678", sae=True)
    scan = parse_scan(
        "BSS aa:bb(on wlan0)\n\tsignal: -40.0 dBm\n\tSSID: W3Net\n"
        "\tRSN:\n\t\t * Authentication suites: SAE\n"
        "BSS cc:dd(on wlan0)\n\tsignal: -50.0 dBm\n\tSSID: Mixed\n"
        "\tRSN:\n\t\t * Authentication suites: PSK SAE\n")
    assert needs_sae("W3Net", scan) and not needs_sae("Mixed", scan)
    c = emit_wpa_ap_conf("AstroDeck-BEEF", AP_PSK)
    assert "mode=2" in c and f"frequency={AP_FREQ}" in c
    n = emit_networkd_ap()
    assert "DHCPServer=yes" in n and AP_CIDR in n
    for page in (render_portal([{"ssid": "x<y", "signal": -40}], "err", 'a"b'),
                 render_joining("net<script>")):
        assert "<script>" not in page.replace("&lt;script&gt;", "")
    print("self-test OK")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="run", choices=["run", "self-test"])
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test or args.mode == "self-test":
        sys.exit(main_selftest())
    sys.exit(main_run())
