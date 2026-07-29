#!/usr/bin/env python3
"""Build the single-file AstroDeck binary for the machine you run this on.

    python packaging/build_binary.py

PyInstaller cannot cross-compile: the binary it produces runs on the OS and
architecture that built it. So each platform's artefact is built on that
platform — locally, or by the release workflow's build matrix.

Steps, in order, because each depends on the last:

  1. build the web UI (npm)
  2. copy it to server/astrodeck/webui, so it is package data rather than a
     sibling directory the packaged app has no way to find
  3. install the server into the CURRENT interpreter, so PyInstaller sees real
     installed metadata — the entry points that register every native device
     backend live in that metadata, not in any import
  4. run PyInstaller against packaging/astrodeck.spec
  5. smoke-test the result by RUNNING it, because a binary that builds and does
     not start is the normal failure here, not the exotic one

Flags:
  --skip-ui       reuse an existing server/astrodeck/webui (fast rebuilds)
  --skip-install  the server is already installed in this interpreter
  --no-smoke      skip step 5 (not recommended; it is the only real check)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
SERVER = ROOT / "server"
WEBUI = SERVER / "astrodeck" / "webui"
SPEC = ROOT / "packaging" / "astrodeck.spec"
DIST = ROOT / "dist"
#: A port nothing else is likely to hold, so the smoke test does not collide
#: with a dev server or the QA instance.
SMOKE_PORT = 8811


def run(cmd: list[str], cwd: Path | None = None, **kw) -> None:
    print(f"\n$ {' '.join(cmd)}" + (f"   (in {cwd})" if cwd else ""))
    subprocess.run(cmd, cwd=cwd, check=True, **kw)


def npm() -> str:
    """npm is a .cmd shim on Windows, which subprocess will not find bare."""
    found = shutil.which("npm") or shutil.which("npm.cmd")
    if not found:
        raise SystemExit("npm not found — install Node 20+ to build the UI")
    return found


def build_ui() -> None:
    if not (UI / "node_modules").is_dir():
        run([npm(), "ci"], cwd=UI)
    run([npm(), "run", "build"], cwd=UI)
    dist = UI / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit(f"{dist}/index.html missing after the UI build")
    # Replace rather than merge: a stale asset from a previous build would be
    # served forever, since the index only ever names the current hashes.
    if WEBUI.exists():
        shutil.rmtree(WEBUI)
    shutil.copytree(dist, WEBUI)
    print(f"UI -> {WEBUI}")


def install_server() -> None:
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    run([sys.executable, "-m", "pip", "install", "pyinstaller>=6.0"])
    run([sys.executable, "-m", "pip", "install", str(SERVER)])


def build_binary() -> Path:
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build" / "pyi"),
         str(SPEC)])
    exe = DIST / ("astrodeck.exe" if sys.platform == "win32" else "astrodeck")
    if not exe.is_file():
        raise SystemExit(f"expected {exe} — PyInstaller produced nothing")
    return exe


def smoke(exe: Path) -> None:
    """Start the binary and check it actually serves. Building is not evidence.

    Checks the three things that fail independently and silently:
    the server answers at all, the SPA is bundled, and the device backends
    registered (i.e. the entry-point metadata survived packaging).
    """
    env = dict(os.environ)
    # Never let the smoke test touch a real config or capture directory.
    tmp = ROOT / "build" / "smoke-state"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    env["ASTRODECK_CONFIG_DIR"] = str(tmp / "config")
    env["ASTRODECK_CAPTURE_DIR"] = str(tmp / "captures")

    print(f"\n$ {exe} run --host 127.0.0.1 --port {SMOKE_PORT}   (smoke test)")
    proc = subprocess.Popen([str(exe), "run", "--host", "127.0.0.1",
                             "--port", str(SMOKE_PORT)], env=env)
    try:
        base = f"http://127.0.0.1:{SMOKE_PORT}"
        health = None
        # A frozen binary unpacks itself on first run, so first boot is slower
        # than any subsequent one. 60s is generous rather than tight.
        for _ in range(60):
            if proc.poll() is not None:
                raise SystemExit(f"the binary exited during startup "
                                 f"(code {proc.returncode})")
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=2) as r:
                    health = json.load(r)
                break
            except (urllib.error.URLError, OSError, json.JSONDecodeError):
                time.sleep(1)
        if health is None:
            raise SystemExit("the binary never answered /healthz")
        print(f"  healthz     ok (version {health.get('version')})")

        with urllib.request.urlopen(base + "/", timeout=5) as r:
            body = r.read(2048).decode("utf-8", "replace")
        if "<!doctype html" not in body.lower():
            raise SystemExit(
                "the root path did not serve the SPA — the UI was not bundled, "
                "so this binary is an API with no interface")
        print("  web ui      ok")

        # Backends register through entry-point metadata, which is the piece
        # most easily lost in packaging: the app still runs, finds no hardware,
        # and gives no reason.
        with urllib.request.urlopen(f"{base}/api/backends", timeout=5) as r:
            backends = json.load(r)
        names = {b.get("name") for b in (backends if isinstance(backends, list)
                                         else backends.get("backends", []))}
        if not names:
            raise SystemExit(
                "no device backends registered — the packaged build lost the "
                "entry-point metadata, so every native driver is missing")
        print(f"  backends    ok ({len(names)} registered)")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-ui", action="store_true")
    ap.add_argument("--skip-install", action="store_true")
    ap.add_argument("--no-smoke", action="store_true")
    args = ap.parse_args()

    if args.skip_ui:
        if not (WEBUI / "index.html").is_file():
            raise SystemExit(f"--skip-ui, but {WEBUI}/index.html is not there")
        print(f"reusing {WEBUI}")
    else:
        build_ui()

    if not args.skip_install:
        install_server()

    exe = build_binary()
    size_mb = exe.stat().st_size / 1e6
    print(f"\nbuilt {exe}  ({size_mb:.0f} MB)")

    if not args.no_smoke:
        smoke(exe)
        print("\nsmoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
