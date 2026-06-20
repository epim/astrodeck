"""AstroDeck supervisor: launch the server, apply staged updates on exit-code 92
with health-check + automatic rollback to last-good.

Standalone (stdlib only) so it keeps working when the server is broken. The core
state machine takes injected ``launch_fn`` / ``health_fn`` / ``sleep_fn`` so it is
deterministically unit-testable without real processes or sockets; ``main()``
wires the real subprocess launcher + ``/healthz`` probe.

State machine per launch:
  * first launch of the trusted ``current`` version -> just run it
  * server exits 0          -> stop (supervisor exits)
  * server exits 92         -> read pending-update, swap ``current``, relaunch as a
                               PROBE; if the new version is healthy commit it as
                               last-good, else roll back to last-good + mark failed
  * server crashes (other)  -> relaunch the SAME version with capped backoff
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:  # work both as ``python -m supervisor.supervisor`` and as a loose script
    from . import protocol as P
except ImportError:  # pragma: no cover - script-mode fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import protocol as P  # type: ignore


class Supervisor:
    def __init__(self, root, *, launch_fn, health_fn,
                 sleep_fn=time.sleep, now_fn=time.time,
                 health_timeout_s: float = 60.0, health_poll_s: float = 1.0,
                 max_backoff_s: float = 30.0,
                 initial_version: "str | None" = None, log=None) -> None:
        self.layout = P.Layout(root)
        self.layout.ensure()
        self.launch_fn = launch_fn
        self.health_fn = health_fn
        self.sleep_fn = sleep_fn
        self.now_fn = now_fn
        self.health_timeout_s = health_timeout_s
        self.health_poll_s = health_poll_s
        self.max_backoff_s = max_backoff_s
        self.initial_version = initial_version
        self._log = log or (lambda m: print(f"[supervisor] {m}", flush=True))
        # per-update health-check window from pending-update.json (None => default).
        self._probe_timeout: "float | None" = None

    # -- helpers ---------------------------------------------------------------
    def _resolve_target(self) -> str:
        cur = self.layout.read_current()
        if cur:
            return cur
        if self.initial_version:
            self.layout.set_current(self.initial_version)
            return self.initial_version
        raise SystemExit("supervisor: no 'current' pointer and no --initial-version")

    def _await_health(self, proc, version: str) -> bool:
        """Poll until the probed version is healthy, the child dies, or timeout."""
        deadline = self.now_fn() + (self._probe_timeout or self.health_timeout_s)
        while self.now_fn() < deadline:
            if proc.poll() is not None:
                return False  # child exited before becoming healthy
            if self.health_fn(version):
                return True
            self.sleep_fn(self.health_poll_s)
        return False

    def _safe_terminate(self, proc) -> None:
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        try:
            proc.wait()
        except Exception:
            pass

    # -- main loop -------------------------------------------------------------
    def run_forever(self, max_iterations: "int | None" = None):
        target = self._resolve_target()
        if self.layout.read_last_good() is None:
            # the starting version is trusted (it's what's installed/running today).
            self.layout.set_last_good(target)
        last_good = self.layout.read_last_good() or target

        probing = False
        prev_version: "str | None" = None
        backoff = 1.0
        iters = 0

        while True:
            iters += 1
            if max_iterations is not None and iters > max_iterations:
                return ("max-iterations", target)

            self._log(f"launching {target}")
            proc = self.launch_fn(target)
            launch_ts = self.now_fn()

            if probing:
                if self._await_health(proc, target):
                    self._log(f"update {prev_version} -> {target} healthy; committing")
                    self.layout.set_last_good(target)
                    last_good = target
                    self.layout.write_result({
                        "ok": True, "version": target, "from": prev_version,
                        "reason": "", "ts": self.now_fn()})
                    probing = False
                    self._probe_timeout = None
                    backoff = 1.0
                    # fall through to wait for the next clean exit / crash
                else:
                    self._log(f"update -> {target} UNHEALTHY; rolling back to {last_good}")
                    self._safe_terminate(proc)
                    self.layout.mark_failed(target, "health check failed")
                    self.layout.write_result({
                        "ok": False, "version": target, "from": prev_version,
                        "reason": "health check failed; rolled back",
                        "ts": self.now_fn()})
                    self.layout.set_current(last_good)
                    target = last_good
                    probing = False
                    self._probe_timeout = None
                    continue  # relaunch the rolled-back version

            code = proc.wait()

            if code == P.EXIT_STOP:
                self._log("server stopped cleanly; supervisor exiting")
                return ("stopped", target)

            if code == P.EXIT_APPLY_UPDATE:
                pend = self.layout.read_pending()
                self.layout.clear_pending()
                nv = (pend or {}).get("version")
                if (pend and P.is_safe_version(nv) and not self.layout.is_failed(nv)
                        and self.layout.release(nv).exists()):
                    prev_version = target
                    self.layout.set_current(nv)
                    target = nv
                    probing = True
                    self._probe_timeout = pend.get("health_timeout_s")
                    self._log(f"applying staged update {prev_version} -> {nv}")
                    continue
                self._log("apply requested but no valid pending update; relaunching")
                continue

            # any other exit code => crash; relaunch the SAME version with backoff.
            # Reset the backoff if this version had run stably (longer than the cap)
            # before crashing, so a long-healthy version isn't penalized by an old
            # crash series.
            if self.now_fn() - launch_ts > self.max_backoff_s:
                backoff = 1.0
            self._log(f"server exited {code}; relaunching in {backoff:.0f}s")
            self.sleep_fn(backoff)
            backoff = min(self.max_backoff_s, backoff * 2)


# ---------------------------------------------------------- real wiring (main)

def _real_launcher(layout: "P.Layout", python_exe: str, host: str, port: int):
    def launch(version: str):
        server_dir = layout.release(version) / "server"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(server_dir) + os.pathsep + env.get("PYTHONPATH", "")
        # Tell the server it is supervised so self-update apply is available and it
        # writes pending-update.json / reads update-result.json under this root.
        env["ASTRODECK_INSTALL_ROOT"] = str(layout.root)
        # Persist config/profiles/plans + captured images OUTSIDE the versioned
        # release dir so they survive an update that swaps it.
        env["ASTRODECK_CONFIG_DIR"] = str(layout.root / "config")
        env["ASTRODECK_CAPTURE_DIR"] = str(layout.root / "captures")
        cmd = [python_exe, "-m", "astrodeck", "run", "--host", host, "--port", str(port)]
        return subprocess.Popen(cmd, env=env)
    return launch


def _real_health(host: str, port: int):
    url = f"http://{host}:{port}/healthz"

    def check(version: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310 - localhost
                if getattr(r, "status", 200) != 200:
                    return False
                data = json.loads(r.read().decode("utf-8"))
        except Exception:
            return False
        return bool(data.get("ok")) and data.get("version") == version
    return check


def main(argv: "list[str] | None" = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="astrodeck-supervisor")
    ap.add_argument("--root", required=True, help="install root (holds current/, releases/, state/)")
    ap.add_argument("--python", default=sys.executable, help="python that runs the server (venv)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--initial-version", default=None,
                    help="seed 'current' on first run if the pointer is absent")
    ap.add_argument("--health-timeout", type=float, default=60.0)
    a = ap.parse_args(argv)

    layout = P.Layout(a.root)
    sup = Supervisor(
        a.root,
        launch_fn=_real_launcher(layout, a.python, a.host, a.port),
        health_fn=_real_health(a.host, a.port),
        health_timeout_s=a.health_timeout,
        initial_version=a.initial_version,
    )
    try:
        kind, _version = sup.run_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return 130
    return 0 if kind == "stopped" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
