"""Update service: poll GitHub, surface availability, and run the
``download -> verify -> stage -> signal-supervisor`` apply pipeline.

The poller is OPT-IN (started from the app lifespan; idle unless
``UpdateConfig.auto_check``). Apply is admin-gated + rig-idle-gated at the route
layer and re-checked here. The actual restart is a GRACEFUL handoff: after
staging, the service asks uvicorn to shut down and arms exit code 92, so the
supervisor swaps ``current`` and relaunches into the new version.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from ..events import bus
from . import download, github, stage, verify
from .protocol import (EXIT_APPLY_UPDATE, InstallLayout, supervised_install_root)
from .state import update_state

# --- graceful-exit handoff (the running uvicorn.Server, set by __main__) ------
_server = None
_exit_code = 0


def bind_server(server) -> None:
    """Called by ``__main__._cmd_run`` so the service can request a graceful
    shutdown that exits with code 92 (apply-update) for the supervisor."""
    global _server
    _server = server


def request_apply_exit() -> None:
    global _exit_code
    _exit_code = EXIT_APPLY_UPDATE
    if _server is not None:
        _server.should_exit = True


def consume_exit_code() -> int:
    return _exit_code


def reset_exit_state() -> None:  # test seam
    global _server, _exit_code
    _server = None
    _exit_code = 0


class UpdateError(Exception):
    """A precondition/pipeline failure surfaced to the API (-> 409) and WS."""


class UpdateService:
    def __init__(self, *, cfg_getter, hub, install_root: "Path | None" = None,
                 server_signal=request_apply_exit, now=time.time,
                 initial_delay_s: float = 30.0):
        self._cfg = cfg_getter            # () -> UpdateConfig
        self._hub = hub
        self._root = install_root if install_root is not None else supervised_install_root()
        self._signal = server_signal
        self._now = now
        self._initial_delay_s = initial_delay_s
        self._apply_lock = asyncio.Lock()
        self._poll_task: "asyncio.Task | None" = None

    @property
    def supervised(self) -> bool:
        return self._root is not None

    # -- WS / state ------------------------------------------------------------
    def _publish(self, phase: str, *, progress: float = 0.0,
                 error: "str | None" = None, **extra) -> None:
        # snapshot() already carries ``phase`` (set just above), so we never pass
        # phase= separately -- the WS event is the whole snapshot.
        update_state.set_phase(phase, progress, error)
        snap = update_state.snapshot()
        snap.update(extra)
        bus.publish("update", **snap)

    # -- check -----------------------------------------------------------------
    async def check(self) -> dict:
        cfg = self._cfg()
        try:
            rel = await github.latest_release(
                cfg.repo, channel=cfg.channel, current=update_state.current)
        except Exception as e:  # noqa: BLE001 - network/parse; never crash the poller
            update_state.set_phase("idle", error=f"check failed: {e}")
            bus.publish("update", **update_state.snapshot())
            return update_state.snapshot()
        ts = self._now()
        if rel is None:
            update_state.set_available(None, "", ts=ts, channel=cfg.channel)
        else:
            update_state.set_available(rel.version, rel.notes_md, ts=ts,
                                       channel=cfg.channel)
        self._publish("checked")
        return update_state.snapshot()

    # -- preconditions ---------------------------------------------------------
    def apply_preconditions(self) -> tuple[bool, str]:
        cfg = self._cfg()
        if not cfg.enabled:
            return False, "update subsystem disabled"
        if self._root is None:
            return False, "not running under the supervisor (apply unavailable)"
        if not (cfg.signing_pubkey or "").strip():
            return False, "no signing public key pinned (cannot verify a release)"
        if not (update_state.update_available and update_state.latest):
            return False, "no update available"
        blocker = self._hub.restart_blocker  # str | None
        if blocker:
            return False, blocker
        return True, ""

    # -- apply pipeline --------------------------------------------------------
    async def apply(self) -> dict:
        ok, reason = self.apply_preconditions()
        if not ok:
            raise UpdateError(reason)
        if self._apply_lock.locked():
            raise UpdateError("an update is already in progress")
        async with self._apply_lock:
            cfg = self._cfg()
            assert self._root is not None
            layout = InstallLayout(self._root)
            try:
                rel = await github.latest_release(
                    cfg.repo, channel=cfg.channel, current=update_state.current)
                if rel is None:
                    raise UpdateError("update no longer available")

                workdir = layout.state / "download"
                artifact = workdir / f"astrodeck-{rel.version}.tar.gz"
                self._publish("downloading", progress=0.0)
                await download.download(
                    rel.artifact_url, artifact,
                    on_progress=lambda p: self._publish("downloading", progress=p))
                sha_text = (await download.fetch_text(rel.sha256_url)
                            if rel.sha256_url else None)
                sig_text = (await download.fetch_text(rel.sig_url)
                            if rel.sig_url else None)

                self._publish("verifying", progress=1.0)
                vok, vreason = verify.verify_download(
                    artifact, cfg.signing_pubkey,
                    sha256_text=sha_text, signature_text=sig_text)
                if not vok:
                    raise UpdateError(f"verification failed: {vreason}")

                self._publish("staging")
                staged = stage.stage_release(artifact, layout.releases, rel.version)
                _ok, deps_reason = stage.reconcile_deps(staged, sys.executable)

                layout.write_pending(rel.version, ts=self._now())
                self._publish("applying", progress=1.0)
                bus.log("warning",
                        f"applying update {update_state.current} -> {rel.version}; "
                        "restarting via supervisor", "update")
                self._signal()
                return {"applying": rel.version, "deps": deps_reason}
            except UpdateError as e:
                self._publish("idle", error=str(e))
                raise
            except Exception as e:  # noqa: BLE001 - surface, then fail the op
                self._publish("idle", error=f"apply failed: {e}")
                raise UpdateError(f"apply failed: {e}") from e

    # -- boot: surface the last attempt's outcome ------------------------------
    def load_boot_result(self) -> None:
        if self._root is None:
            return
        layout = InstallLayout(self._root)
        res = layout.read_result()
        if res is not None:
            update_state.set_result(res)
            layout.clear_result()

    # -- poller (opt-in) -------------------------------------------------------
    async def run_poller(self) -> None:
        await asyncio.sleep(self._initial_delay_s)
        while True:
            cfg = self._cfg()
            if cfg.enabled and cfg.auto_check:
                try:
                    await self.check()
                except Exception as e:  # noqa: BLE001
                    bus.log("error", f"update check failed: {e}", "update")
            await asyncio.sleep(max(1, self._cfg().check_interval_hours) * 3600)

    def start_poller(self) -> "asyncio.Task":
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poller_guard())
        return self._poll_task

    async def _poller_guard(self) -> None:
        try:
            await self.run_poller()
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001 - a poller crash must not matter
            bus.log("error", f"update poller crashed: {e}", "update")

    def stop_poller(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()


# --------------------------------------------------- module singleton (lazy)
_service: "UpdateService | None" = None


def get_service() -> UpdateService:
    global _service
    if _service is None:
        from ..config import config_store
        from ..hub import hub
        _service = UpdateService(
            cfg_getter=lambda: config_store.cfg().update, hub=hub)
    return _service


def reset_service() -> None:  # test seam
    global _service
    _service = None
