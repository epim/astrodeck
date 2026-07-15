"""Auto-resume-at-dusk service (sessions spec §5).

One asyncio task started with the app (pattern: the AlertDispatcher lifespan
task), 60s cadence. Arms via ``Session.auto_resume`` (the PATCH route enforces
the singleton). When the engine is idle, exactly one armed dormant session
exists, and tonight's window for any of its targets has opened (reusing
``schedule.resolve_window``), it attempts the SAME code path as a manual
resume — every existing safety gate (sun avoidance, safety monitor, horizon
preflight) runs inside ``engine.start`` / the run itself. A refusal alerts and
retries every 10 minutes; when the window closes mid-backoff (dawn) it alerts
one give-up and stays quiet until the window reopens (the next night). The
run_start alert on success comes free from the AlertDispatcher's sequence
state machine. ``resume_veto()`` is the sub-project-C weather-gate hook — v1
always returns None.

HARD REQUIREMENT (Task 4 review carry-in): ResumeArm is a THIRD ``engine.start``
path, and ``engine.start`` is deliberately unguarded — the route-level gates
that reject an unbounded accepted-quota plan do NOT cover this path. So the
service calls ``quota_unbounded(plan)`` itself before every attempt and refuses
(alert + backoff, stay dormant) when it would run forever under persistent
rejects.
"""
from __future__ import annotations

import asyncio
import time

from ..config import config_store
from ..events import bus
from . import schedule
from .models import quota_unbounded
from .session import Session, session_store

CHECK_INTERVAL_S = 60.0
RETRY_INTERVAL_S = 600.0


class ResumeArm:
    def __init__(self, engine, hub, *, clock=time.time):
        self.engine = engine
        self.hub = hub
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._retry_at: float = 0.0        # refusal backoff: no attempt before this
        self._gave_up_for: str | None = None   # session id we give-up-alerted on

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:      # noqa: BLE001 — service must never die
                bus.log("warning", f"resume-arm tick failed: {e}", "sequence")
            await asyncio.sleep(CHECK_INTERVAL_S)

    def resume_veto(self) -> str | None:
        """Veto hook (spec §5). v1: no veto — sub-project C plugs the
        cloud/precip forecast gate in here. Non-None = human-readable reason."""
        return None

    def _window_open(self, session: Session, now: float) -> bool:
        """True when tonight's window for ANY of the session's targets is open
        (calibration targets shoot any time). Reuses schedule.resolve_window —
        the same resolution a run's scheduler freezes at start."""
        cfg = config_store.cfg()
        site = self.hub.site
        twilight = cfg.safety.twilight_deg if cfg else -12.0
        for t in session.plan.targets:
            if t.calibration:
                return True
            start, stop = schedule.resolve_window(t.schedule, site, twilight, now)
            if start is not None and start <= now and (stop is None or now < stop):
                return True
        return False

    async def tick(self) -> None:
        now = self._clock()
        if self.engine.running:
            return                          # anything running = no interest
        armed = session_store.armed()
        if armed is None:
            self._retry_at = 0.0            # disarmed from the UI: stop instantly
            self._gave_up_for = None
            return
        if not self._window_open(armed, now):
            if self._retry_at and self._gave_up_for != armed.id:
                # the window closed while we were mid-backoff: dawn beat us.
                self._gave_up_for = armed.id
                self._retry_at = 0.0
                bus.log("error", f"auto-resume gave up for tonight: "
                                 f"'{armed.name}' window closed before a "
                                 "successful start", "sequence")
            return
        self._gave_up_for = None            # window open (again): fresh night
        if now < self._retry_at:
            return
        # HARD REQUIREMENT: engine.start is unguarded here, so refuse an
        # accepted-quota plan that could loop forever (spec §3 / Task 4 review).
        if quota_unbounded(armed.plan):
            bus.log("warning", f"auto-resume refused: accepted-quota plan "
                               f"'{armed.name}' is unbounded (no reject guard "
                               f"or stop boundary) — retrying in "
                               f"{int(RETRY_INTERVAL_S / 60)} min", "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            return
        veto = self.resume_veto()
        if veto is not None:
            bus.log("warning", f"auto-resume vetoed: {veto} — retrying in "
                               f"{int(RETRY_INTERVAL_S / 60)} min", "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            return
        try:
            self.hub.require("camera")
            self.engine.start(armed.plan, session=armed)
        except Exception as e:              # noqa: BLE001 — refusal, not a crash
            bus.log("warning", f"auto-resume refused: {e} — retrying in "
                               f"{int(RETRY_INTERVAL_S / 60)} min", "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            return
        self._retry_at = 0.0
        bus.log("info", f"auto-resume: '{armed.name}' resumed at dusk", "sequence")
