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
state machine. ``resume_veto()`` delegates to the injected WeatherService
(sub-project C, weather spec §4); with no service injected it returns None.

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

#: Exposure for the post-restart blind solve. Deliberately longer than
#: solve_and_sync's 3 s default -- see the call site for the measurement.
RECOVERY_SOLVE_EXPOSURE_S = 12.0


class ResumeArm:
    def __init__(self, engine, hub, *, clock=time.time, weather=None):
        self.engine = engine
        self.hub = hub
        self._clock = clock
        # sub-project C (weather spec §4): injected WeatherService (like clock,
        # so tests inject fakes). None = no weather gate (back-compat).
        self._weather = weather
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
        """Veto hook (sessions spec §5 / weather spec §4): delegates to the
        injected WeatherService. Non-None = human-readable reason; the caller
        (tick, :120-125) logs it and arms the 10-min retry latch BEFORE any
        device is touched. No service injected -> no veto (back-compat)."""
        if self._weather is None:
            return None
        return self._weather.veto_reason(self._clock())

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
        # STILL BOOTING IS NOT A REFUSAL.
        #
        # The boot sequence connects devices asynchronously, and this service's
        # first tick can land in the gap before that finishes. Observed on the
        # rig 2026-08-02: boot sweep at 21:50:43, this tick at 21:50:44, devices
        # connected at 21:50:46 — a two-second window in which the recovery
        # ladder's plate solve failed with "no camera connected", was read as a
        # transient inability to verify the sky, and armed the ten-minute
        # backoff. The rig then sat idle for ten minutes with clear sky, a
        # working camera and an armed session, for no reason at all.
        #
        # So: no devices yet means come back on the NEXT 60s tick, with no
        # backoff and no alarming log line. It is not a condition the operator
        # needs to know about; it is the boot finishing.
        if not self._devices_ready():
            return

        # Make the rig's beliefs true again BEFORE it is allowed to move.
        refusal = await self._recover(armed)
        if refusal is not None:
            bus.log("warning", f"auto-resume held: {refusal} — retrying in "
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
        bus.log("info", f"auto-resume: '{armed.name}' resumed", "sequence")

    async def _recover(self, session) -> str | None:
        """Re-establish what the rig cannot simply assume after a restart.

        Returns None when the rig is fit to resume, otherwise a human-readable
        reason the caller logs before arming the backoff. The session is left
        dormant AND armed either way, so the next tick retries.

        COOLING IS NOT HERE, deliberately. ``SequenceEngine._run`` already awaits
        ``_cool_and_wait(plan.cool_to, plan.cool_timeout_s)`` under the
        ``require_cooling``/``cooling_action`` policy, sharing the
        ``COOLER_AT_TARGET_C`` band with the Monitor. A resumed run reuses the
        same plan object, so it inherits that gate. A second wait here would
        double the delay and let the two bands drift apart.
        """
        from ..devices import fingerprint as _fp

        # 1. FOCUS — measure when the focuser lost count; never restore a number.
        #    A focuser that disagrees with the record has forgotten its position
        #    (the EAF does this on power loss), so its readout is a default, not
        #    a measurement. Driving it back to the remembered value would be a
        #    guess about a device that just said it does not know where it is.
        pos = None
        try:
            foc = self.hub.require("focuser")
            pos = await foc.get_position()
        except Exception:  # noqa: BLE001 — no focuser is not a refusal
            pos = None
        if pos is not None and not _fp.verdict(focuser_position=pos).focus_trusted:
            bus.log("info", "focuser lost its position across the restart — "
                            "running autofocus before resuming", "sequence")
            try:
                await self._autofocus()
            except Exception as e:  # noqa: BLE001
                return f"autofocus after restart failed: {e}"

        # 2. POINTING — ALWAYS re-measure. Never gated on the fingerprint.
        #
        #    The AM5 is a harmonic drive with NO BRAKE. A restart that preserved
        #    every byte of software state still cannot rule out that the tube
        #    sagged under gravity while the motors were unpowered, and the
        #    mount's own encoders cannot report a shift that happened while it
        #    was off. So "nothing changed in software" is not evidence about
        #    where the telescope points; only the sky is.
        #
        #    If the solve fails — too few stars, heavy cloud — DO NOT MOVE. The
        #    alternative is slewing an OTA whose true position is unknown, which
        #    is how a tube meets a pier.
        #    ONE EXCEPTION, and it is about configuration rather than conditions:
        #    a rig with no solver at all cannot verify pointing on ANY night. It
        #    slews on the mount's model every time it observes, by the owner's
        #    standing choice. Refusing to resume such a rig would not make it
        #    safer — it would just delete the feature for it, while leaving the
        #    identical blind slew in place everywhere else. So "no solver
        #    configured" degrades to the rig's normal behaviour with a warning,
        #    while "there IS a solver and it could not solve" refuses: that is
        #    cloud or too few stars, a transient inability to verify, and it is
        #    exactly the case where moving is a gamble.
        if not self._can_solve():
            bus.log("warning", "resuming after a restart WITHOUT verifying where "
                               "the telescope points — no plate solver is "
                               "configured, so the mount's own position is taken "
                               "on trust", "sequence")
        else:
            try:
                # A LONGER EXPOSURE THAN THE DEFAULT, ON PURPOSE.
                #
                # solve_and_sync defaults to 3 s at gain 200 bin 2, which suits
                # centering — there the mount is already near the target and a
                # solve happens several times per slew, so it is tuned for speed.
                # Recovery is the opposite case: it runs once, nothing else is
                # waiting on it, and failing costs a TEN MINUTE backoff.
                #
                # Measured on the rig 2026-08-02 pointing at a rich Lyra field
                # under a sky the camera confirmed clear (170 stars at 5 s /
                # gain 300): the 3 s default yielded just 16 detected stars and
                # ASTAP returned "no solution", while the same sky at a longer
                # exposure solved. Trading ten seconds against ten minutes is not
                # a close call.
                await self.hub.solve_and_sync(exposure_s=RECOVERY_SOLVE_EXPOSURE_S)
            except Exception as e:  # noqa: BLE001
                return (f"blind plate solve failed after restart ({e}) — refusing "
                        "to slew a mount whose true position is unknown")

        # 3. RE-CENTER on the first real target. Calibration-only sessions have
        #    none and never slew, so they skip this; they still got the solve
        #    above, which costs one exposure and confirms the sky is usable.
        tgt = next((t for t in session.plan.targets if not t.calibration), None)
        if tgt is not None:
            try:
                await self.hub.goto_and_center(tgt.ra_hours, tgt.dec_deg)
            except Exception as e:  # noqa: BLE001
                return f"re-centering after restart failed: {e}"
        return None

    def _devices_ready(self) -> bool:
        """Are the devices a resume needs actually connected yet?

        Checked BEFORE the recovery ladder so a half-finished boot never reads
        as a hazard. Only the camera and telescope are required: those are what
        the ladder and the run itself cannot proceed without.
        """
        for role in ("camera", "telescope"):
            dev = self.hub.devices.get(role)
            if dev is None or not getattr(dev, "connected", False):
                return False
        return True

    def _can_solve(self) -> bool:
        """Is a trustworthy plate solver available on this rig RIGHT NOW?

        Uses the same resolver the real solve path uses, so the two can never
        disagree about what this rig can do. Any failure to resolve one means
        no — the conservative reading, which degrades to a warning rather than a
        refusal (see the call site).
        """
        try:
            from .. import providers as _providers
            return _providers.pick_solver(self.hub) is not None
        except Exception:  # noqa: BLE001
            return False

    async def _autofocus(self) -> None:
        """The rig's real autofocus path (native), not the legacy numpy one."""
        from ..focus.native import run_native_autofocus
        await run_native_autofocus(self.hub.require("camera"),
                                   self.hub.require("focuser"))
