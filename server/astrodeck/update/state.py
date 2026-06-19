"""In-memory snapshot of the update subsystem, read by ``GET /api/version`` and
``GET /api/update/status`` and written by the update service poller (Phase 3).

Kept in its own tiny module so the Phase-1 version/health endpoints don't import
the heavier service. All fields are NON-SECRET (the signing key here is public),
so the snapshot is safe to broadcast over WS/REST."""
from __future__ import annotations

import threading

from .. import __version__


class UpdateState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current: str = __version__
        self.latest: str | None = None
        self.update_available: bool = False
        self.notes_md: str = ""
        self.channel: str = "stable"
        self.last_check_ts: float | None = None
        # phase: idle | checking | downloading | verifying | staging | applying
        self.phase: str = "idle"
        self.progress: float = 0.0
        self.error: str | None = None
        # outcome of the LAST apply attempt, surfaced on boot from update-result.json
        self.last_result: dict | None = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "current": self.current,
                "latest": self.latest,
                "update_available": self.update_available,
                "notes_md": self.notes_md,
                "channel": self.channel,
                "last_check_ts": self.last_check_ts,
                "phase": self.phase,
                "progress": self.progress,
                "error": self.error,
                "last_result": self.last_result,
            }

    def set_available(self, latest: str | None, notes_md: str, *,
                      ts: float | None = None, channel: str | None = None) -> None:
        with self._lock:
            self.latest = latest
            self.notes_md = notes_md or ""
            self.update_available = bool(
                latest is not None and latest != self.current)
            if ts is not None:
                self.last_check_ts = ts
            if channel is not None:
                self.channel = channel

    def set_phase(self, phase: str, progress: float = 0.0,
                  error: str | None = None) -> None:
        with self._lock:
            self.phase = phase
            self.progress = progress
            self.error = error

    def set_result(self, result: dict | None) -> None:
        with self._lock:
            self.last_result = result


update_state = UpdateState()
