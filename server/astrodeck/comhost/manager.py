"""ComHostManager (COM-T6): the server-owned lifecycle for the bundled COM host.

Server-spawned (plan lifecycle decision): a process-lifetime singleton spawns
`python -m astrodeck.comhost --port 0 --portfile <pf>` on first ascom-local need,
learns its ephemeral loopback port from the portfile, health-checks the
management API, and tears it down on app shutdown. Restart is LAZY — a crashed
child is detected and respawned on the NEXT `ensure()` (there is no background
monitor loop; spawn is driven by a user rig-open, not a poller), throttled by a
short spawn-backoff so a caller retry loop cannot thrash a crash-looping child.

No-orphan discipline (astrotown dedup lesson): the pidfile is the dedup key —
before spawning, kill any process recorded in a stale portfile and confirm it is
gone (the bind is ephemeral, so there is no fixed port to free; the PID is). A
stale portfile only exists after an UNCLEAN crash (a clean `stop()` removes it),
so before killing we VERIFY the recorded PID is genuinely our comhost (its command
line names `astrodeck.comhost`) — an OS PID-recycle must never make us force-kill
an innocent, unrelated process.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

_DEFAULT_PORTFILE = str(Path(tempfile.gettempdir()) / "astrodeck-comhost.json")
_HEALTH_TIMEOUT_S = 15.0
_HEALTH_POLL_S = 0.2
#: Lazy-restart throttle: after a failed spawn/health, refuse to respawn again
#: until this window elapses (a crash-looping child under a retry loop can't thrash).
_SPAWN_BACKOFF_S = 2.0
#: Post-kill "confirm it is gone" budget.
_CONFIRM_GONE_TIMEOUT_S = 3.0
_CONFIRM_GONE_POLL_S = 0.1


def _kill_pid(pid: int) -> None:  # seam: patched in tests
    """Best-effort terminate a (possibly stale) comhost PID. Windows uses
    taskkill; POSIX uses SIGTERM. Never raises."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=10)
        else:
            os.kill(pid, 15)
    except Exception:
        pass


def _pid_cmdline(pid: int) -> str:
    """The command line of `pid`, or "" on any failure / if the process is gone.
    Windows queries CIM (the modern replacement for the deprecated `wmic`); POSIX
    reads /proc (absent on macOS -> ""). Never raises."""
    try:
        if sys.platform == "win32":
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}')"
                 ".CommandLine"],
                capture_output=True, text=True, timeout=10)
            return (proc.stdout or "").strip()
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except Exception:
        return ""


def _pid_is_comhost(pid: int) -> bool:  # seam: patched in tests
    """Identity guard (fix-round, Medium): confirm `pid` is genuinely OUR bundled
    comhost before killing it. A stale post-crash portfile PID may have been
    RECYCLED by the OS to an unrelated process; force-killing it would be a silent
    wrong-kill. True only when the live command line names `astrodeck.comhost`; on
    ANY doubt (process gone, query failed, no match) False -> caller does NOT kill
    and treats the portfile as stale. Never raises."""
    return "astrodeck.comhost" in _pid_cmdline(pid)


def _pid_alive(pid: int) -> bool:
    """Whether `pid` currently exists. Never raises."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                                  capture_output=True, text=True, timeout=10)
            return str(int(pid)) in (out.stdout or "")
        os.kill(int(pid), 0)  # signal 0 = existence probe
        return True
    except PermissionError:  # exists but not ours -> still alive
        return True
    except Exception:
        return False


def _confirm_gone(pid: int) -> bool:  # seam: patched in tests
    """Poll briefly until `pid` is gone after a kill (obligation: "confirm it is
    gone" before spawning). True when confirmed gone within the budget. Never
    raises."""
    deadline = time.monotonic() + _CONFIRM_GONE_TIMEOUT_S
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(_CONFIRM_GONE_POLL_S)
    return not _pid_alive(pid)


def _default_spawn(portfile: str) -> subprocess.Popen:  # pragma: no cover - real
    return subprocess.Popen(
        [sys.executable, "-m", "astrodeck.comhost",
         "--port", "0", "--portfile", portfile])


class ComHostManager:
    def __init__(self, *, spawn=None, portfile: str = _DEFAULT_PORTFILE):
        self._portfile = portfile
        self._spawn = spawn or (lambda: _default_spawn(portfile))
        self._proc = None
        self._port: "int | None" = None
        self._lock = asyncio.Lock()
        self._fail_at: "float | None" = None  # last failed-spawn time (backoff)

    async def ensure(self) -> int:
        async with self._lock:
            if self._proc is not None and self._proc.poll() is None and self._port:
                return self._port
            # Lazy restart is throttled (fix-round, Low): after a recent failed
            # spawn/health, refuse to respawn until the backoff elapses, so a
            # caller retry loop cannot thrash a crash-looping child.
            now = asyncio.get_event_loop().time()
            if self._fail_at is not None and now - self._fail_at < _SPAWN_BACKOFF_S:
                raise RuntimeError(
                    "comhost spawn backing off after a recent failure "
                    f"(retry in ~{_SPAWN_BACKOFF_S:.0f}s)")
            self._reap_stale()               # no-orphan gate
            self._clear_portfile()
            try:
                self._proc = self._spawn()
                self._port = await self._await_port_healthy()
            except Exception:
                self._fail_at = asyncio.get_event_loop().time()
                raise
            self._fail_at = None
            return self._port

    def _reap_stale(self) -> None:
        """No-orphan reap WITH a PID-reuse identity guard (fix-round, Medium).

        A leftover portfile exists only after an UNCLEAN crash (clean stop()
        removes it). Kill the recorded PID ONLY if it is genuinely our comhost —
        an OS PID-recycle must never make us force-kill an innocent process — then
        confirm it is gone before spawning. If identity can't be confirmed, DO NOT
        kill: treat the portfile as stale and proceed (the ephemeral bind makes a
        lingering old host harmless anyway)."""
        try:
            rec = json.loads(Path(self._portfile).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        pid = rec.get("pid")
        if not isinstance(pid, int) or pid == os.getpid():
            return
        if not _pid_is_comhost(pid):     # identity guard: never kill a recycled PID
            return
        _kill_pid(pid)
        _confirm_gone(pid)               # "confirm it is gone" before we spawn

    def _clear_portfile(self) -> None:
        try:
            Path(self._portfile).unlink()
        except OSError:
            pass

    async def _await_port_healthy(self) -> int:
        deadline = asyncio.get_event_loop().time() + _HEALTH_TIMEOUT_S
        while asyncio.get_event_loop().time() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError("comhost exited before becoming healthy")
            port = self._read_port()
            if port is not None and await self._healthy(port):
                return port
            await asyncio.sleep(_HEALTH_POLL_S)
        self.stop()
        raise TimeoutError("comhost did not become healthy in time")

    def _read_port(self) -> "int | None":
        try:
            rec = json.loads(Path(self._portfile).read_text(encoding="utf-8"))
            return int(rec["port"])
        except (OSError, ValueError, KeyError):
            return None

    async def _healthy(self, port: int) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(
                    f"http://127.0.0.1:{port}/management/v1/configureddevices")
            return r.status_code == 200
        except Exception:
            return False

    def stop(self) -> None:
        proc, self._proc, self._port = self._proc, None, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                pass
        self._clear_portfile()


_MANAGER: "ComHostManager | None" = None


def get_manager() -> ComHostManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ComHostManager()
    return _MANAGER


def reset_manager_for_tests() -> None:
    global _MANAGER
    _MANAGER = None
