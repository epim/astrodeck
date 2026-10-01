"""Capture the local simulator for the public site, with provenance.

Only a server_ctl-created config in this checkout's .probe directory is allowed.
No real rig, relay, remote address, survey layer, or saved real site is used.
"""
from __future__ import annotations
import argparse
import copy
import math
import runpy
import os
import socket
import sys
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


MONITOR_FILE = "monitor-desktop-light.png"
PUBLIC_SITE = {"name": "Synthetic public Siding Spring", "latitude": -31.27,
               "longitude": 149.06, "elevation_m": 0.0}
PUBLIC_SCENARIOS = {
    "siding-spring": (PUBLIC_SITE, -45.0),
    "hanle": ({"name": "Synthetic public Hanle Indian Astronomical Observatory",
               "latitude": 32.78, "longitude": 78.96, "elevation_m": 0.0}, 20.0),
}
CAPTURE_PLAN = [
    ("flows-desktop-dark.png", "/session/flows", 1440, 1000, "dark"),
    ("equipment-phone-light.png", "/rig/devices", 390, 844, "light"),
    ("equipment-phone-night.png", "/rig/devices", 390, 844, "dark"),
    (MONITOR_FILE, "/monitor/live", 1440, 1000, "light"),
]


def capture_plan(monitor_only: bool, synthetic_site: str | None):
    if bool(synthetic_site) != monitor_only or synthetic_site not in {None, *PUBLIC_SCENARIOS}:
        raise ValueError("Monitor-only capture requires a supported public synthetic scenario")
    return [item for item in CAPTURE_PLAN if item[0] == MONITOR_FILE] if monitor_only else list(CAPTURE_PLAN)


def require_simulator(status: dict):
    # The legacy connect_sim path leaves Device.backend empty. These are
    # consistency checks after fresh-process ownership, not authority on their own.
    devices = status.get("connected", {})
    expected = {"camera": "Sim Camera 533MM", "telescope": "Sim Mount EQ6-R"}
    if (status.get("mode") != "sim"
            or any(not devices.get(role, {}).get("connected")
                   or devices.get(role, {}).get("name") != name for role, name in expected.items())
            or any(item.get("connected") and (item.get("backend") not in {"", "sim"}
                   or item.get("host") or item.get("port") not in {None, 0}) for item in devices.values())):
        raise ValueError("Capture requires connected simulator devices only")
    return status


def simulator_status(request):
    response = request.get("/api/status")
    if not response.ok:
        raise ValueError("Simulator status request failed")
    return require_simulator(response.json())


def synthetic_target(unix_time: float, synthetic_site: str = "siding-spring"):
    site, declination = PUBLIC_SCENARIOS[synthetic_site]
    # Load the repository's stdlib-only coordinate module, without app/config imports.
    coords = runpy.run_path(str(ROOT / "server/astrodeck/catalog/coords.py"))
    return {"ra_hours": (coords["lst_hours"](site["longitude"], unix_time) + 1.0) % 24,
            "dec_deg": declination, "center": False, "force": False}


def above_horizon_target(status: dict, target: dict):
    mount = status.get("mount", {})
    try:
        altitude = float(mount["alt"])
        ra_error = abs((float(mount["ra_hours"]) - target["ra_hours"] + 12) % 24 - 12)
        dec_error = abs(float(mount["dec_deg"]) - target["dec_deg"])
        # The unsynced simulator deliberately lands with 0.028 degree DEC error.
        return (math.isfinite(altitude) and altitude > 30 and ra_error < 0.01 and dec_error < 0.1
                and mount.get("slewing") is False and mount.get("parked") is False)
    except (KeyError, TypeError, ValueError):
        return False


def seed_monitor_scenario(request, synthetic_site: str = "siding-spring"):
    site_config, declination = PUBLIC_SCENARIOS[synthetic_site]
    simulator_status(request)
    response = request.put("/api/site", data={"site": site_config})
    if not response.ok:
        raise RuntimeError("Synthetic public site save refused")
    status = simulator_status(request)
    site = status.get("site", {})
    if site.get("is_default") is not False or any(site.get(key) != value for key, value in site_config.items()):
        raise RuntimeError("Synthetic public site was not saved")
    target = synthetic_target(time.time(), synthetic_site)
    response = request.post("/api/mount/goto", data=target)
    if not response.ok:
        raise RuntimeError("Simulator target slew refused")
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if above_horizon_target(simulator_status(request), target):
            return {"id": "public-" + synthetic_site, "site": dict(site_config),
                    "site_origin": "Fixed approximate public location used only in a fresh synthetic fixture.",
                    "mount": f"Simulator slewed one hour east of the meridian at declination {declination:g}; settled above 30 degrees verified before capture.",
                    "exposure_s": 1.0, "saved_frame": False}
        time.sleep(0.25)
    raise RuntimeError("Simulator did not settle on the above-horizon target")


def merged_provenance(previous: dict, records: list, context: dict, monitor_only: bool):
    # Older ledgers had global provenance. Preserve that source for untouched files.
    inherited = {k: v for k, v in previous.items() if k not in {"captures", "schema_version", "description"}}
    old = []
    for item in previous.get("captures", []):
        record = copy.deepcopy(item)
        record.setdefault("provenance", copy.deepcopy(inherited))
        old.append(record)
    if monitor_only:
        expected = {"site/assets/screenshots/" + item[0] for item in CAPTURE_PLAN}
        if {r["file"] for r in old} != expected or len(old) != len(expected):
            raise ValueError("Monitor-only capture requires a complete prior ledger")
        if [r["file"] for r in records] != ["site/assets/screenshots/" + MONITOR_FILE]:
            raise ValueError("Monitor-only capture may replace only the Monitor record")
    fresh = {r["file"]: dict(r, provenance=copy.deepcopy(context)) for r in records}
    combined = [fresh.pop(r["file"], r) for r in old] if monitor_only else []
    combined.extend(fresh.values())
    return {"schema_version": 2, "description": "Each image retains its own capture source and scenario.",
            "captures": combined}


def checked_config(config: Path, port: int):
    config = config.resolve()
    if port == 8800 or not 1024 <= port <= 65535:
        raise ValueError("Use a dedicated simulator port, never the rig port")
    if not config.is_relative_to((ROOT / ".probe").resolve()):
        raise ValueError("Config must be inside this worktree's .probe directory")
    marker = json.loads((config / ".astrodeck-probe").read_text(encoding="utf-8"))
    if marker.get("port") != port or Path(marker.get("dir", "")).resolve() != config:
        raise ValueError("Probe marker does not identify this config and port")
    pid = marker.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        raise ValueError("Probe marker has no valid process identity")
    if (config / "server.pid").read_text(encoding="utf-8").strip() != str(pid):
        raise ValueError("Probe PID records disagree")
    return config


def fresh_paths(config: Path, port: int):
    config = config.resolve()
    if port == 8800 or not 1024 <= port <= 65535:
        raise ValueError("Use a dedicated simulator port")
    if not config.is_relative_to((ROOT / ".probe").resolve()):
        raise ValueError("Config must be in this worktree's .probe directory")
    captures = config.with_name(config.name + "-captures")
    if config.exists() or captures.exists():
        raise ValueError("Capture requires new config and capture directories")
    return config, captures


def process_info(pid: int):
    # Query one numeric PID. Command lines remain private and are never logged.
    command = (
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; "
        "if($p){[pscustomobject]@{pid=$p.ProcessId;parent=$p.ParentProcessId;"
        "created=$p.CreationDate.ToUniversalTime().ToString('o');"
        "executable=$p.ExecutablePath;command=$p.CommandLine}|ConvertTo-Json -Compress}"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                            capture_output=True, text=True, check=True)
    if not result.stdout.strip():
        return None
    item = json.loads(result.stdout)
    item["created"] = datetime.fromisoformat(item["created"].replace("Z", "+00:00")).timestamp()
    return item


def owned_process(config: Path, port: int, python: Path, started: float):
    checked_config(config, port)
    pid = int((config / "server.pid").read_text(encoding="utf-8"))
    info = process_info(pid)
    if info is None:
        return None
    if (info["created"] < started - 1
            or Path(info["executable"]).resolve() != python.resolve()
            or not re.search(rf"(?:^|\s)-m\s+astrodeck\s+run\s+--port\s+{port}\s*$", info["command"])):
        raise ValueError("Fresh probe process identity could not be verified")
    return info


def reaches_owner(pid: int, owner: dict):
    # Windows venv Python may start a child that owns the socket.
    # A parent's creation time cannot be later than its child (PID reuse).
    seen = set()
    child_created = float("inf")
    while pid and pid not in seen:
        seen.add(pid)
        info = process_info(pid)
        if info is None or info["created"] > child_created:
            return False
        if pid == owner["pid"]:
            return info["created"] == owner["created"]
        child_created = info["created"]
        pid = info["parent"]
    return False


def owned_listener(config: Path, port: int, python: Path, started: float):
    owner = owned_process(config, port, python, started)
    if owner is None:
        raise ValueError("Fresh probe process has exited")
    command = (
        f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction Stop | "
        "Select-Object LocalAddress,OwningProcess | ConvertTo-Json -Compress"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                            capture_output=True, text=True, check=True)
    listeners = json.loads(result.stdout)
    if isinstance(listeners, dict):
        listeners = [listeners]
    if not listeners or any(item["LocalAddress"] not in {"127.0.0.1", "::1"}
                            or not reaches_owner(item["OwningProcess"], owner)
                            for item in listeners):
        raise ValueError("The fresh probe process tree does not own the loopback port")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--ui-dir", type=Path, required=True)
    parser.add_argument("--venv-python", type=Path, required=True)
    parser.add_argument("--monitor-only", action="store_true")
    parser.add_argument("--synthetic-site", choices=list(PUBLIC_SCENARIOS))
    args = parser.parse_args()
    capture_plan(args.monitor_only, args.synthetic_site)
    if os.name != "nt":
        raise ValueError("Capture process ownership check currently supports Windows only")
    config, captures_dir = fresh_paths(args.config_dir, args.port)
    with socket.socket() as port_check:
        port_check.bind(("127.0.0.1", args.port))
    env = dict(os.environ, PYTHONPATH=str(ROOT / "server"))
    launcher = ROOT / "tools/ui_probe/server_ctl.py"
    command = [sys.executable, str(launcher), "start", "--fresh", "--port", str(args.port),
               "--config-dir", str(config), "--capture-dir", str(captures_dir),
               "--ui-dir", str(args.ui_dir.resolve()), "--venv-python", str(args.venv_python.resolve())]
    started = time.time()
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("Fresh simulator launch failed; inspect this private probe directory")
        owned_listener(config, args.port, args.venv_python, started)
        capture(args, config)
    finally:
        # A failed health check can leave the freshly spawned server alive.
        # Only stop a matching marker created within this fresh launch.
        if (config / ".astrodeck-probe").is_file():
            if owned_process(config, args.port, args.venv_python, started) is not None:
                subprocess.run([sys.executable, str(launcher), "stop", "--config-dir", str(config)],
                               env=env, capture_output=True, text=True, check=True)



def capture(args, config):
    from playwright.sync_api import sync_playwright
    base = f"http://127.0.0.1:{args.port}"
    out = ROOT / (".probe/site-screenshots" if args.inspect else "site/assets/screenshots")
    out.mkdir(parents=True, exist_ok=True)
    captures = capture_plan(args.monitor_only, args.synthetic_site)
    ledger_path = ROOT / "tools/site/screenshot-provenance.json"
    previous = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    if args.monitor_only:
        merged_provenance(previous, [{"file": "site/assets/screenshots/" + MONITOR_FILE}], {}, True)
    scenario = {"id": "default-site", "site_origin": "Fresh simulator default site untouched."}
    records = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, ignore_default_args=["--hide-scrollbars"])
        request = pw.request.new_context(base_url=base)
        simulator_status(request)
        if args.monitor_only:
            scenario = seed_monitor_scenario(request, args.synthetic_site)
        else:
            # Use the synthetic M31 mosaic fixture, only saved, never run.
            spec = json.loads((ROOT / "tools/ui_probe/routes_s5_s6.json").read_text(encoding="utf-8"))
            seed = next(item["save_flow"] for item in spec["seed"] if "save_flow" in item)
            flow = {"id": "site-m31-mosaic", "name": "M31 mosaic", "folder": "My flows", "graph": seed["graph"]}
            saved = request.post("/api/flows", data={"flow": flow})
            if not saved.ok:
                raise RuntimeError(f"Could not save synthetic example flow: HTTP {saved.status}")
        exposure = request.post("/api/capture", data={"exposure_s": 1.0, "save": False})
        if not exposure.ok:
            raise RuntimeError(f"Simulator exposure refused: HTTP {exposure.status}")
        time.sleep(3)
        for name, route, width, height, theme in captures:
            context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme,
                                          device_scale_factor=1, has_touch=width < 600)
            context.add_init_script(f"localStorage.setItem('astrodeck-night', '{'1' if theme == 'dark' else '0'}');")
            page = context.new_page()
            page.route("**/*", lambda r: r.continue_() if r.request.url.startswith(base + "/") or r.request.url.startswith(("data:", "blob:")) else r.abort())
            page.goto(base + "/#" + route, wait_until="domcontentloaded")
            page.get_by_test_id("next-app").wait_for()
            page.wait_for_timeout(2200)
            notices = page.get_by_role('button', name='Dismiss this notice')
            while notices.count():
                notices.first.click()
            if route == "/session/flows":
                page.get_by_role("button", name=re.compile(r"M31 mosaic")).first.click()
                page.wait_for_timeout(900)
                page.get_by_role('button', name='FIT', exact=True).click()
                page.wait_for_timeout(200)
            if args.monitor_only:
                visible = page.locator("body").inner_text().lower()
                if "no dark tonight" in visible or "mount is pointing below the horizon" in visible:
                    raise RuntimeError("Monitor still shows the invalid default-site scenario")
                if any(str(scenario["site"][key]) in visible for key in ("latitude", "longitude")):
                    raise RuntimeError("Synthetic site coordinates appeared in the capture view")
            if args.inspect:
                print(f"Captured preview {name}")
            path = out / name
            page.screenshot(path=str(path))
            records.append({"file": "site/assets/screenshots/" + name, "route": "#" + route,
                            "viewport": [width, height], "browser_color_scheme": theme,
                            "app_mode": "red night" if theme == "dark" else "day",
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            context.close()
        browser.close()
    if not args.inspect:
        context = {"captured_utc": datetime.now(timezone.utc).isoformat(),
                   "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                   "config_dir": config.relative_to(ROOT).as_posix(), "port": args.port,
                   "launcher": "capture_sim.py owns server_ctl.py start --fresh and stop",
                   "ownership": "New config and captures; fresh probe process tree owns loopback listener; directories never reused.",
                   "source": "Fresh local simulator; no rig or relay; scenario records the synthetic site.",
                   "scenario": scenario,
                   "survey": "No Atlas/Sky survey view captured; external browser requests blocked.",
                   "visual_review": "Pending: inspect the new image before committing."}
        ledger = merged_provenance(previous, records, context, args.monitor_only)
        ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(records)} simulator views in {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
