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

# Pre-update ``pip freeze`` snapshot the SERVER writes under state/ before it
# mutates the shared venv; we pip-sync back to it on rollback so a rolled-back
# version runs against the deps it shipped with. Same literal is mirrored in
# ``astrodeck.update.service.ROLLBACK_FREEZE_FILE`` -- keep them in lockstep.
ROLLBACK_FREEZE_FILE = "rollback-freeze.txt"


class Supervisor:
    def __init__(self, root, *, launch_fn, health_fn, restore_fn=None,
                 sleep_fn=time.sleep, now_fn=time.time,
                 health_timeout_s: float = 60.0, health_poll_s: float = 1.0,
                 max_backoff_s: float = 30.0,
                 initial_version: "str | None" = None, log=None) -> None:
        self.layout = P.Layout(root)
        self.layout.ensure()
        self.launch_fn = launch_fn
        self.health_fn = health_fn
        # restore_fn(freeze_text) pip-syncs the shared venv back to the pre-update
        # snapshot on rollback; None => no venv restore (unit tests / no snapshot).
        self.restore_fn = restore_fn
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

    # -- venv rollback ---------------------------------------------------------
    def _freeze_path(self) -> Path:
        return self.layout.state / ROLLBACK_FREEZE_FILE

    def _restore_venv(self) -> None:
        """On rollback, pip-sync the shared venv back to the snapshot the server
        took before it upgraded deps -- otherwise the rolled-back last-good version
        runs against the new version's deps and can crash on its next import. Best
        effort: a restore failure is logged, never fatal."""
        path = self._freeze_path()
        try:
            freeze = path.read_text(encoding="utf-8")
        except OSError:
            return  # no snapshot (freeze skipped, or nothing to restore)
        if self.restore_fn is not None:
            try:
                self.restore_fn(freeze)
                self._log("restored venv to pre-update snapshot")
            except Exception as e:  # noqa: BLE001 - restore is best effort
                self._log(f"venv restore failed: {e!r}")
        self._clear_freeze()

    def _clear_freeze(self) -> None:
        try:
            self._freeze_path().unlink()
        except OSError:
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

            # The watchdog's whole job is to keep the server alive; it must not die
            # on a transient OS failure -- a Windows sharing violation (WinError 32)
            # from a scanner holding a state file open during os.replace, a momentary
            # FileNotFoundError from Popen, disk-full on a state write. Contain those,
            # back off, and retry rather than leaving the rig unsupervised all night.
            try:
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
                        self._clear_freeze()  # committed: no rollback snapshot needed
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
                        # Revert the shared venv before relaunching last-good so it
                        # doesn't inherit the failed version's upgraded deps.
                        self._restore_venv()
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
                    # An apply was REQUESTED (server did a full download+verify+stage
                    # and exited 92) but the pending version is invalid -- most often
                    # a version already marked failed, never retried. Surface WHY via
                    # update-result so the server reports the failure on boot instead
                    # of a silent no-op restart loop that keeps offering the update.
                    if pend:
                        if not P.is_safe_version(nv):
                            reason = "invalid version string in pending update"
                        elif self.layout.is_failed(nv):
                            reason = "version previously failed a health check"
                        elif not self.layout.release(nv).exists():
                            reason = "release not staged"
                        else:  # pragma: no cover - defensive
                            reason = "pending update rejected"
                        self.layout.write_result({
                            "ok": False, "version": nv, "from": target,
                            "reason": reason, "ts": self.now_fn()})
                        self._clear_freeze()  # not applied: drop any stale snapshot
                    self._log(f"apply requested but no valid pending update; relaunching ({nv})")
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
            except (OSError, subprocess.SubprocessError) as e:
                self._log(f"supervisor loop error: {e!r}; retrying in {backoff:.0f}s")
                self.sleep_fn(backoff)
                backoff = min(self.max_backoff_s, backoff * 2)
                continue


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


def _health_url(host: str, port: int) -> str:
    """Build the /healthz URL, dialing loopback when the server is bound to a
    wildcard. A wildcard bind address (0.0.0.0 / ::) is NOT connectable as a peer:
    on Windows ``connect(0.0.0.0)`` fails with WSAEADDRNOTAVAIL, so probing the
    verbatim bind address would make every update look unhealthy and roll back a
    perfectly good release. The docs deploy with --host 0.0.0.0 for LAN access."""
    h = (host or "").strip()
    if h in ("", "0.0.0.0", "*"):
        h = "127.0.0.1"
    elif h in ("::", "[::]", "::0"):
        h = "::1"
    # bracket IPv6 literals for the URL authority.
    if ":" in h and not h.startswith("["):
        h = f"[{h}]"
    return f"http://{h}:{port}/healthz"


def _real_health(host: str, port: int):
    url = _health_url(host, port)

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


def _real_restore(python_exe: str):
    """Pip-sync the shared venv back to a pre-update ``pip freeze`` snapshot. Run
    only on rollback, and best-effort: the supervisor logs a failure but keeps
    going (a partial restore still beats a wedged venv)."""
    import tempfile

    def restore(freeze_text: str) -> None:
        if not freeze_text.strip():
            return
        fd, tmp = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(freeze_text)
            subprocess.run(
                [python_exe, "-m", "pip", "install", "-r", tmp],
                check=True, capture_output=True, timeout=600)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return restore


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
        restore_fn=_real_restore(a.python),
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
