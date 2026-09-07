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
                tail = proc.output_tail()
                raise SystemExit(
                    f"the binary exited during startup (code {code})"
                    + (f"\n--- what it wrote ---\n{tail}" if tail else ""))
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
#: The de-elevated child gets its OWN profile environment, so these travel in
#: the batch wrapper as `set` lines rather than in a Popen env= dict it would
#: never see.
SMOKE_ENV_KEYS = ("ASTRODECK_CONFIG_DIR", "ASTRODECK_CAPTURE_DIR")
#: Local SAM account names are capped at 20 characters, so the prefix is short
#: on purpose: "astrodeck-smoke-<6 hex>" would be 22 and `net user` rejects it.
SMOKE_USER_PREFIX = "astrodeck-sm-"
LOGON_WITH_PROFILE = 0x1
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x400
STILL_ACTIVE = 259
#: The three ways this fails on a hosted runner, named so the log diagnoses
#: itself instead of printing a bare number.
LOGON_ERROR_HINTS = {
    1058: "the Secondary Logon service is disabled, and CreateProcessWithLogonW needs it",
    1326: "the smoke account's credentials were rejected",
    1385: "the smoke account is not granted this logon type",
}


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


def _new_smoke_credentials() -> tuple[str, str]:
    """A name nothing else will hold and a password nothing will ever read.

    The password LEADS with a fixed "Aa1!" so it satisfies any complexity
    policy the runner image happens to carry, and so no argument can begin
    with a "-" that `net` might read as a switch.
    """
    import secrets
    return (SMOKE_USER_PREFIX + secrets.token_hex(3),
            "Aa1!" + secrets.token_urlsafe(24))


def _redact(text: str, secret: str) -> str:
    """Nothing prints or raises the password, including an error message we
    did not write ourselves."""
    return text.replace(secret, "<redacted>") if secret else text


def _windows_error_text(code: int) -> str:
    import ctypes
    buffer = ctypes.create_unicode_buffer(512)
    ctypes.windll.kernel32.FormatMessageW(
        0x1000 | 0x200,  # FORMAT_MESSAGE_FROM_SYSTEM | IGNORE_INSERTS
        None, code, 0, buffer, len(buffer), None)
    return buffer.value.strip() or "no description"


def _create_smoke_user(name: str, password: str) -> None:
    """Add the throwaway local account. Default membership is Users -- no
    administrator group, which is the entire point: `runas /trustlevel:0x20000`
    did NOT clear the elevation flag (TokenIsElevated stayed set on the hosted
    runner and the child still died in require_unprivileged_runtime), and what
    the interlock's own message asks for is a dedicated unprivileged account.
    """
    completed = subprocess.run(["net", "user", name, password, "/add", "/y"],
                               capture_output=True, text=True)
    if completed.returncode != 0:
        detail = ((completed.stdout or "") + (completed.stderr or "")).strip()
        raise SystemExit(f"could not create the unprivileged smoke account "
                         f"{name} (code {completed.returncode}): "
                         + _redact(detail, password))
    print(f"  smoke account {name} (throwaway, deleted when the smoke ends)")


def _icacls(path: Path, spec: str) -> None:
    completed = subprocess.run(["icacls", str(path), "/grant", spec],
                               capture_output=True, text=True)
    if completed.returncode != 0:
        detail = ((completed.stdout or "") + (completed.stderr or "")).strip()
        raise SystemExit(f"could not grant the smoke account access to {path} "
                         f"(code {completed.returncode}): {detail}")


def _traversal_dirs(*targets: Path) -> list[Path]:
    """Every directory from the drive down to each target, drive first. An
    access check walks the whole path, so a grant on the leaf is not enough."""
    chain: list[Path] = []
    for target in targets:
        for parent in reversed(target.parents):
            if parent not in chain:
                chain.append(parent)
    return chain


def _grant_smoke_access(name: str, exe: Path, state_dir: Path) -> list[Path]:
    """The narrowest set of grants that lets the new user run the binary.

    Never recursive over the checkout: an `icacls /T` across node_modules is
    minutes and none of it is needed. Read+execute on the binary and the
    directory it sits in, modify on the state directory it writes, and
    traverse-only (no inheritance) on the ancestors so the paths resolve.
    """
    granted: list[Path] = []
    for directory in _traversal_dirs(exe.parent, state_dir):
        _icacls(directory, f"{name}:RX")
        granted.append(directory)
    for path, rights in ((exe.parent, "(OI)(CI)RX"), (exe, "RX"),
                         (state_dir, "(OI)(CI)M")):
        _icacls(path, f"{name}:{rights}")
        granted.append(path)
    return granted


def _delete_smoke_account(name: str, granted: list[Path]) -> None:
    """Always, even on a failed smoke. ACEs first: `icacls /remove` cannot
    resolve a name that no longer exists and would leave orphaned SIDs."""
    for path in granted:
        _run_text(["icacls", str(path), "/remove", name])
    # LOGON_WITH_PROFILE created a profile for the account (that is what gave
    # the bootloader a TEMP it could write). `net user /delete` leaves that
    # C:\Users\<name> tree behind; the profile object is what removes it, so
    # a maintainer's box that ran the elevated path is not left with a stray
    # home directory. Best-effort, like the rest of the cleanup.
    _run_text(["powershell", "-NoProfile", "-NonInteractive", "-Command",
               "Get-CimInstance Win32_UserProfile | Where-Object { "
               f"$_.LocalPath -like '*\\{name}' }} | Remove-CimInstance"])
    _run_text(["net", "user", name, "/delete"])
    print(f"  smoke account {name} deleted")


def _create_process_as_user(name: str, password: str, cmdline: str,
                            cwd: Path) -> tuple[int, int]:
    """CreateProcessWithLogonW: the one seam that touches the Win32 API.

    LOGON_WITH_PROFILE matters more than it looks. It loads the new user's
    profile, which is what gives the PyInstaller onefile bootloader a %TEMP%
    it can extract itself into; lpEnvironment=NULL then means "the environment
    of that profile", not the caller's.
    """
    import ctypes
    from ctypes import wintypes

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CreateProcessWithLogonW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.LPVOID,
        wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    advapi32.CreateProcessWithLogonW.restype = wintypes.BOOL

    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    info = PROCESS_INFORMATION()
    # The API is documented as possibly writing to the command line, so it
    # gets a mutable buffer rather than an interned literal.
    buffer = ctypes.create_unicode_buffer(cmdline, len(cmdline) + 1)
    ctypes.set_last_error(0)
    ok = advapi32.CreateProcessWithLogonW(
        name, ".", password, LOGON_WITH_PROFILE, None, buffer,
        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT, None, str(cwd),
        ctypes.byref(startup), ctypes.byref(info))
    if not ok:
        code = ctypes.get_last_error()
        hint = LOGON_ERROR_HINTS.get(code)
        raise SystemExit(
            f"CreateProcessWithLogonW could not start the smoke binary as "
            f"{name} (error {code}: {_windows_error_text(code)})"
            + (f" -- {hint}" if hint else ""))
    ctypes.windll.kernel32.CloseHandle(info.hThread)
    return int(info.hProcess), int(info.dwProcessId)


def _process_exit_code(handle: int) -> int | None:
    """None while the process is alive, its exit code once it is not."""
    import ctypes
    from ctypes import wintypes
    code = wintypes.DWORD()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel32.GetExitCodeProcess(wintypes.HANDLE(handle),
                                       ctypes.byref(code)):
        return 1  # the handle cannot be queried: treat it as gone
    return None if code.value == STILL_ACTIVE else int(code.value)


def _close_handle(handle: int) -> None:
    try:
        import ctypes
        from ctypes import wintypes
        ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(handle))
    except Exception:
        pass


class SmokeProcess:
    """A handle on the smoke server, however it was started.

    Two shapes behind one interface. The ordinary one wraps the Popen we own.
    The de-elevated one wraps a real process handle from
    CreateProcessWithLogonW: `cmd /c wrapper.cmd` waits for the onefile
    bootloader, which waits for the server, so the wrapper's exit IS the
    server's. It also carries the throwaway account, which has to be deleted
    whatever happens to the smoke test.
    """

    def __init__(self, port: int, popen: subprocess.Popen | None = None,
                 runner=None, log_path: Path | None = None,
                 handle: int | None = None, pid: int | None = None,
                 cleanup=None):
        self.port = port
        self._popen = popen
        self._run = runner or _run_text
        #: Where the wrapper redirected the child's output, if anywhere.
        self.log_path = log_path
        self._handle = handle
        self._pid = pid
        self._cleanup = cleanup
        self._image_pids: list[int] = []
        self._stopped = False

    @property
    def pid(self) -> int | None:
        if self._popen is not None:
            return self._popen.pid
        return self._pid

    def listener_pid(self) -> int | None:
        """Whoever holds the smoke port. The wrapper PID is cmd.exe; the
        listener is the bootloader's child, two processes further down."""
        return _netstat_listener_pid(self._run(["netstat", "-ano"]), self.port)

    def output_tail(self, lines: int = 40) -> str:
        """The last lines the child wrote, or "" when nothing was captured
        (the Popen path inherits the console, so there is nothing to read)."""
        if self.log_path is None or not self.log_path.exists():
            return ""
        text = self.log_path.read_text(encoding="utf-8", errors="replace")
        return "\n".join(text.splitlines()[-lines:])

    def poll(self) -> int | None:
        if self._popen is not None:
            return self._popen.poll()
        if self._handle is not None:
            return _process_exit_code(self._handle)
        return None

    def stop(self) -> None:
        if self._popen is not None:
            _stop_tree(self._popen)
            return
        if self._stopped:
            return
        self._stopped = True
        try:
            # /T reaps the tree: cmd.exe -> onefile bootloader -> the server.
            if self._pid:
                self._run(["taskkill", "/PID", str(self._pid), "/T", "/F"])
            # Backstop. A onefile bootloader's child has outlived its parent
            # here before, and a survivor would answer the NEXT build's checks.
            for pid in _tasklist_pids(self._run(
                    ["tasklist", "/FI", "IMAGENAME eq astrodeck.exe",
                     "/FO", "CSV", "/NH"])):
                if pid != self._pid and pid not in self._image_pids:
                    self._image_pids.append(pid)
                    self._run(["taskkill", "/PID", str(pid), "/T", "/F"])
            if self._handle is not None:
                _close_handle(self._handle)
            for _ in range(15):
                if not _port_in_use(self.port):
                    break
                time.sleep(1)
        finally:
            if self._cleanup is not None:
                self._cleanup()


def _write_smoke_wrapper(wrapper: Path, log_path: Path, argv: list[str],
                         env: dict) -> None:
    """The wrapper carries the environment as well as the log capture.

    The de-elevated child runs under its own profile, so the redirected state
    directories have to be set inside it; and its output has to go to a file,
    because the first hosted proof run died eight seconds in with its console
    somewhere nobody could read it.
    """
    lines = ["@echo off"]
    lines += [f"set {key}={env[key]}" for key in SMOKE_ENV_KEYS if key in env]
    lines.append(f"{subprocess.list2cmdline(argv)} > \"{log_path}\" 2>&1")
    wrapper.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def _launch_smoke(exe: Path, port: int, env: dict) -> SmokeProcess:
    """Start the binary for the smoke test, de-elevating if we have to."""
    argv = [str(exe), "run", "--host", "127.0.0.1", "--port", str(port)]
    if not (sys.platform == "win32" and _is_elevated()):
        print(f"\n$ {exe} run --host 127.0.0.1 --port {port}   (smoke test)")
        return SmokeProcess(port, popen=subprocess.Popen(argv, env=env))

    print("this process is elevated and the server refuses to run elevated, so "
          "the smoke test runs the binary as a throwaway unprivileged account")
    state_dir = Path(env.get("ASTRODECK_CONFIG_DIR",
                             str(ROOT / "build" / "smoke-state" / "config"))).parent
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "smoke.log"
    wrapper = state_dir / "smoke.cmd"
    _write_smoke_wrapper(wrapper, log_path, argv, env)

    name, password = _new_smoke_credentials()
    _create_smoke_user(name, password)
    granted: list[Path] = []
    try:
        granted = _grant_smoke_access(name, exe, state_dir)
        cmdline = f'cmd /c "{wrapper}"'
        print(f"\n$ {cmdline}   (smoke test, as {name})")
        print(f"  wrapper: {subprocess.list2cmdline(argv)} > {log_path}")
        handle, pid = _create_process_as_user(name, password, cmdline, state_dir)
    except BaseException:
        _delete_smoke_account(name, granted)
        raise
    print(f"  started pid {pid}")
    return SmokeProcess(port, log_path=log_path, handle=handle, pid=pid,
                        cleanup=lambda: _delete_smoke_account(name, granted))


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
