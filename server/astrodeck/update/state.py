"""In-memory snapshot of the update subsystem, read by ``GET /api/version`` and
``GET /api/update/status`` and written by the update service poller (Phase 3).

Kept in its own tiny module so the Phase-1 version/health endpoints don't import
the heavier service. All fields are NON-SECRET (the signing key here is public),
so the snapshot is safe to broadcast over WS/REST — including the apply gate
(:meth:`UpdateState.bind_gate`), whose blocked reason is the same
``view.status``-level phrase ``/api/update/status`` already returns ("rig is
capturing", "not running under the supervisor")."""
from __future__ import annotations

import threading
from typing import Any

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
        # Answers the APPLY GATE for :meth:`snapshot` — see :meth:`bind_gate`.
        # None until the update service is built (or after it is torn down).
        self._gate_provider: Any = None

    def bind_gate(self, provider: Any) -> None:
        """Register what answers the APPLY GATE, or ``None`` to unbind.

        ``provider`` needs ``.supervised`` (bool) and ``.apply_preconditions()``
        -> ``(ok, reason)`` — i.e. the :class:`~.service.UpdateService`, bound by
        ``service.get_service()``.

        WHY the gate belongs in the snapshot at all: ``supervised`` /
        ``can_apply`` / ``apply_blocked_reason`` used to be stamped on by GET
        ``/api/update/status`` alone, while every ``update`` WS event is this
        snapshot verbatim and the client REPLACES its whole update slice on that
        event. Every phase tick and every poller check therefore arrived with the
        three keys absent and erased them — re-arming the Upgrade button in the
        middle of a sequence and deleting both the blocked-reason line and the
        unsupervised warning. It is the partial-overwrite class
        ``Hub.publish_safety`` documents for the ``safety`` event; the cure is
        the same, one layer down — publish the whole payload, so there is nothing
        left to clobber.

        The gate is not state we own (it is the install layout + the update
        config + the rig's live idle check), so it is asked, not stored: a
        photograph of ``hub.restart_blocker`` goes stale the moment the run it
        names ends."""
        self._gate_provider = provider

    def _gate(self) -> dict:
        """The apply gate right now, or ``{}`` when nobody can answer.

        ``{}`` — unbound, or a provider that raised — is deliberate. Absence is
        the one answer the client already handles (``UpdatePanel`` re-asks the
        route on a frame with no gate). The two alternatives both lie: a made-up
        ``can_apply: false`` parks Upgrade behind a block nothing can explain,
        and ``null`` reads as present-but-unknown in JS and re-arms it."""
        provider = self._gate_provider
        if provider is None:
            return {}
        try:
            ok, reason = provider.apply_preconditions()
            return {"supervised": bool(provider.supervised),
                    "can_apply": bool(ok),
                    "apply_blocked_reason": "" if ok else str(reason or "")}
        except Exception:   # noqa: BLE001 - a hub mid-teardown, a half-built
            # service: the gate is one field group on a status payload and must
            # never take /api/version (or a WS frame) down with it.
            return {}

    def snapshot(self) -> dict:
        # Asked OUTSIDE the lock: the provider reads the config store and the
        # hub, neither of which may run under this mutex.
        gate = self._gate()
        with self._lock:
            snap = {
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
        snap.update(gate)
        return snap

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
