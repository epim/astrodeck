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
import csv
import io
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

    # A leftover server on the smoke port would answer every check below and
    # the test would grade a stranger. That happened: the previous release's
    # smoke binary was still alive from an earlier build on the same machine,
    # because a onefile bootloader's child outlives terminate(). Refuse the
    # port unless it is free, and insist the answer is THIS build's version.
    if _port_in_use(SMOKE_PORT):
        raise SystemExit(
            f"port {SMOKE_PORT} is already in use; the smoke test would talk "
            "to whatever is listening there instead of the binary just built")
    expected_version = _source_version()

    proc = _launch_smoke(exe, SMOKE_PORT, env)
    try:
        base = f"http://127.0.0.1:{SMOKE_PORT}"
        health = None
        # A frozen binary unpacks itself on first run, so first boot is slower
        # than any subsequent one. 60s is generous rather than tight.
        for _ in range(60):
            code = proc.poll()
            if code is not None:
                raise SystemExit(f"the binary exited during startup "
                                 f"(code {code})")
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=2) as r:
                    health = json.load(r)
                break
            except (urllib.error.URLError, OSError, json.JSONDecodeError):
                time.sleep(1)
        if health is None:
            raise SystemExit("the binary never answered /healthz")
        if health.get("version") != expected_version:
            raise SystemExit(
                f"the server on port {SMOKE_PORT} reports version "
                f"{health.get('version')!r}, but this tree is {expected_version!r}: "
                "that is not the binary just built")
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
        proc.stop()
        shutil.rmtree(tmp, ignore_errors=True)


def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _source_version() -> str:
    """The version this tree declares, read from the file rather than an
    import, so a stale install in the build interpreter cannot answer for it."""
    import re
    text = (ROOT / "server" / "astrodeck" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("could not read __version__ from server/astrodeck/__init__.py")
    return m.group(1)


def _stop_tree(proc: subprocess.Popen) -> None:
    """Stop the smoke server AND its children. A PyInstaller onefile binary is
    a bootloader that runs the real program as a child; on Windows terminating
    the parent leaves that child serving the port for the next build to find."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def _is_elevated() -> bool:
    """Whether THIS process holds an administrator/root token.

    Hosted GitHub Windows runners do, which is why the smoke test needed this:
    the server refuses to start elevated (runtime_security, OPEN-005) and that
    refusal is deliberate and has no override.

    Detection never raises. A failure to detect means "assume not elevated",
    which takes the plain launch — and if the process really was elevated the
    server still refuses, loudly, exactly as it does today. Guessing the other
    way would put every ordinary build through the de-elevating path.
    """
    try:
        if sys.platform == "win32":
            try:
                from astrodeck.runtime_security import is_elevated_runtime
                return bool(is_elevated_runtime())
            except Exception:
                # The server is installed into the build interpreter by step 3,
                # so the import normally works; --skip-install may mean it does
                # not. shell32 answers the same question well enough here.
                import ctypes
                return bool(ctypes.windll.shell32.IsUserAnAdmin())
        try:
            from astrodeck.runtime_security import _posix_is_elevated
            return bool(_posix_is_elevated())
        except Exception:
            return False
    except Exception:
        return False


#: The directories the smoke test redirects so it never touches real state.
#: runas hands the child the CALLER's environment, so these have to be in
#: os.environ, not merely in a Popen env= dict the child will never see.
SMOKE_ENV_KEYS = ("ASTRODECK_CONFIG_DIR", "ASTRODECK_CAPTURE_DIR")
#: A basic-user token: no administrator group, no elevation. Needs no password.
RUNAS_TRUSTLEVEL = "/trustlevel:0x20000"


def _run_text(cmd: list[str]) -> str:
    """stdout of a short helper command (netstat/tasklist/taskkill), or "" if
    it could not run. These are probes: a failed probe is "found nothing"."""
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return ""
    return completed.stdout or ""


def _netstat_listener_pid(text: str, port: int) -> int | None:
    """The PID LISTENING on 127.0.0.1:<port> in `netstat -ano` output.

    Only IPv4 loopback/wildcard lines count: the smoke server binds 127.0.0.1,
    and an unrelated IPv6 listener on the same port number is not it. Connected
    sockets to the port (ESTABLISHED) are somebody's client, not the server.
    """
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() not in ("TCP", "TCPV6"):
            continue
        if not any(p.upper() == "LISTENING" for p in parts):
            continue
        local = parts[1]
        head, _, tail = local.rpartition(":")
        if tail != str(port) or head not in ("127.0.0.1", "0.0.0.0"):
            continue
        try:
            return int(parts[-1])
        except ValueError:
            continue
    return None


def _tasklist_pids(text: str, image: str = "astrodeck.exe") -> list[int]:
    """PIDs of an image in `tasklist /FO CSV /NH` output. The "INFO: No tasks"
    line parses as a one-field row and is ignored like any other non-match."""
    pids: list[int] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or row[0].strip().lower() != image.lower():
            continue
        try:
            pids.append(int(row[1].strip()))
        except ValueError:
            continue
    return pids


class SmokeProcess:
    """A handle on the smoke server, however it was started.

    Two shapes behind one interface. The ordinary one wraps the Popen we own.
    The runas one owns nothing: `runas` returns the moment it has spawned the
    child, so there is no handle to wait on and the server has to be FOUND —
    by the port it listens on, and by image name so the onefile bootloader
    parent can be reaped along with the child that actually serves.
    """

    def __init__(self, port: int, popen: subprocess.Popen | None = None,
                 runner=None, find_timeout: float = 60.0):
        self.port = port
        self._popen = popen
        self._run = runner or _run_text
        self._find_timeout = find_timeout
        self._started = time.monotonic()
        self._listener_pid: int | None = None
        self._image_pids: list[int] = []
        self._seen = False

    @property
    def pid(self) -> int | None:
        if self._popen is not None:
            return self._popen.pid
        if self._listener_pid is not None:
            return self._listener_pid
        return self._image_pids[0] if self._image_pids else None

    def _refresh(self) -> bool:
        """Look for the server. True if anything of it is running now."""
        pid = _netstat_listener_pid(self._run(["netstat", "-ano"]), self.port)
        images = _tasklist_pids(self._run(
            ["tasklist", "/FI", "IMAGENAME eq astrodeck.exe", "/FO", "CSV", "/NH"]))
        if pid is not None:
            self._listener_pid = pid
        for extra in images:
            if extra not in self._image_pids:
                self._image_pids.append(extra)
        alive = pid is not None or bool(images)
        if alive:
            self._seen = True
        return alive

    def poll(self) -> int | None:
        if self._popen is not None:
            return self._popen.poll()
        if self._refresh():
            return None
        if self._seen:
            return 1  # it was there and now it is not: it died starting up
        if time.monotonic() - self._started > self._find_timeout:
            return 1  # runas spawned something that never listened
        return None

    def stop(self) -> None:
        if self._popen is not None:
            _stop_tree(self._popen)
            return
        # /healthz may have answered before poll() ever saw the listener, so
        # look once more rather than kill nothing.
        self._refresh()
        targets: list[int] = []
        if self._listener_pid is not None:
            targets.append(self._listener_pid)
        targets += [p for p in self._image_pids if p not in targets]
        for pid in targets:
            self._run(["taskkill", "/PID", str(pid), "/T", "/F"])
        for _ in range(15):
            if not _port_in_use(self.port):
                return
            time.sleep(1)


def _launch_smoke(exe: Path, port: int, env: dict) -> SmokeProcess:
    """Start the binary for the smoke test, de-elevating if we have to."""
    argv = [str(exe), "run", "--host", "127.0.0.1", "--port", str(port)]
    if not (sys.platform == "win32" and _is_elevated()):
        print(f"\n$ {exe} run --host 127.0.0.1 --port {port}   (smoke test)")
        return SmokeProcess(port, popen=subprocess.Popen(argv, env=env))

    print("this process is elevated and the server refuses to run elevated, "
          "so the smoke test launches the binary de-elevated via runas")
    # runas gives the child the caller's environment; a variable that exists
    # only in `env` would never reach it, and the smoke server would write to
    # the real config and capture directories.
    for key in SMOKE_ENV_KEYS:
        if key in env:
            os.environ[key] = env[key]
    command = subprocess.list2cmdline(argv)
    print(f"\n$ runas {RUNAS_TRUSTLEVEL} \"{command}\"   (smoke test)")
    completed = subprocess.run(["runas", RUNAS_TRUSTLEVEL, command],
                               capture_output=True, text=True)
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise SystemExit(f"runas could not start the binary de-elevated "
                         f"(code {completed.returncode}): {output.strip()}")
    if output.strip():
        print("  " + output.strip().replace("\n", "\n  "))
    return SmokeProcess(port)


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
