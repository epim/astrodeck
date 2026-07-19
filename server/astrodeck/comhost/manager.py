"""ComHostManager (COM-T6): the server-owned lifecycle for the bundled COM host.

Server-spawned + monitored (plan lifecycle decision): a process-lifetime
singleton spawns `python -m astrodeck.comhost --port 0 --portfile <pf>` on first
ascom-local need, learns its ephemeral loopback port from the portfile,
health-checks the management API, and tears it down on app shutdown. No-orphan
discipline (astrotown dedup lesson): the pidfile is the dedup key — before
spawning, loop-kill any process recorded in a stale portfile and confirm it is
gone (the bind is ephemeral, so there is no fixed port to free; the PID is).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

_DEFAULT_PORTFILE = str(Path(tempfile.gettempdir()) / "astrodeck-comhost.json")
_HEALTH_TIMEOUT_S = 15.0
_HEALTH_POLL_S = 0.2


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

    async def ensure(self) -> int:
        async with self._lock:
            if self._proc is not None and self._proc.poll() is None and self._port:
                return self._port
            self._reap_stale()               # no-orphan gate
            self._clear_portfile()
            self._proc = self._spawn()
            self._port = await self._await_port_healthy()
            return self._port

    def _reap_stale(self) -> None:
        """Kill a child recorded in a leftover portfile (crashed prior server)."""
        try:
            rec = json.loads(Path(self._portfile).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        pid = rec.get("pid")
        if isinstance(pid, int) and pid != os.getpid():
            _kill_pid(pid)

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
