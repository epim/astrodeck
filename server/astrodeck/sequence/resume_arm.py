"""Auto-resume-at-dusk service (sessions spec §5).

One asyncio task started with the app (pattern: the AlertDispatcher lifespan
task), 60s cadence. Arms via ``Session.auto_resume`` (the PATCH route enforces
the singleton). When the engine is idle, exactly one armed dormant session
exists, and tonight's window for any of its targets has opened (reusing
``schedule.resolve_window``), it runs the recovery ladder and then starts the
run. A refusal alerts and
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

THE GATES RUN HERE, NOT ONLY IN THE RUN. This file used to say every safety
gate ran inside ``engine.start`` / the run itself. That was accurate while the
resume path WAS ``engine.start``; then ``_recover`` was added in front of it and
the sentence stayed. The ladder plate-solves and re-centers — unattended motion,
by a machine that has just rebooted — so gating only inside the run left exactly
those slews unchecked. ``_recover`` now reads the safety monitor before it moves
anything and puts the re-centering slew through the same altitude limits an
in-run slew gets. Sun avoidance was always covered: it lives at the motion
boundary inside ``goto_and_center``, not in the engine.
"""
from __future__ import annotations

import asyncio
import time

from ..config import config_store
from ..events import bus
from . import schedule
from .models import quota_unbounded
from .policy import resolve_policy
from .session import Session, session_store

CHECK_INTERVAL_S = 60.0
RETRY_INTERVAL_S = 600.0

#: Consecutive crashes of ONE session before auto-resume stops trying and stows
#: the rig. Three, because two is inside the range of genuinely transient faults
#: this ladder already recovers from - a USB re-enumeration, a driver that
#: dropped its link between frames - and the point is to distinguish those from
#: a fault that will still be there on the next attempt. At ten minutes a retry
#: this gives roughly half an hour of trying before the night is called.
RESUME_GIVE_UP_AFTER = 3

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
        #: session id we have already said "dormant but not armed" about, so the
        #: notice appears once per session rather than once per minute.
        self._quiet_note_for: str | None = None
        #: session id we have already stowed the rig for. The disarm below is
        #: the real latch - it is what stops the next tick reaching the
        #: give-up branch at all - and this is the belt to its braces, so a
        #: session whose disarm failed to save cannot park the mount once a
        #: minute for the rest of the night.
        self._stowed_for: str | None = None
        #: WHY NOTHING IS HAPPENING, for anything that wants to say so.
        #:
        #: Every refusal below was formatted into a log line and dropped. The
        #: log ring holds ~40 minutes of a ten-hour night, and the standing-by
        #: branch latches per session and logs EXACTLY ONCE - so scraping the
        #: ring for it works by luck on a weather veto that re-logs every ten
        #: minutes, and not at all on the more common hold. Monitor therefore
        #: said "No run active - plan a session" over a session that was armed
        #: and waiting, which is an instruction to do the one thing that
        #: strands it (a fresh start disarms every other session).
        #:
        #: ``None`` = not holding. Otherwise {reason, since, retry_at,
        #: session_id, session_name, owed}.
        self.hold: dict | None = None

    def _set_hold(self, session, reason: str, retry_at: float = 0.0) -> None:
        """Record the current refusal, preserving ``since`` while the reason
        stands so the UI can say how long it has been waiting."""
        prior = self.hold or {}
        same = prior.get("reason") == reason and prior.get("session_id") == getattr(session, "id", "")
        self.hold = {
            "reason": reason,
            "since": prior.get("since") if same else self._clock(),
            "retry_at": retry_at or None,
            "session_id": getattr(session, "id", ""),
            "session_name": getattr(session, "name", ""),
            "owed": session.owed() if hasattr(session, "owed") else 0,
        }

    def _clear_hold(self) -> None:
        self.hold = None

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
        the same resolution a run's scheduler freezes at start.

        A DARK SKY IS THE OUTER BOUND, and it has to be checked here rather
        than left to the per-target schedule. The default ``Schedule`` is
        ``start_mode="now"`` / ``stop_mode="none"``, so ``resolve_window``
        returns ``(now, None)`` and the test below reduces to ``now <= now and
        True`` — open, unconditionally, forever. On 2026-08-11 that had
        auto-resume burning a 4 s exposure and a full ASTAP run every ten
        minutes at 07:36, ninety minutes after sunrise, on a mount the dawn
        daemon had already parked. The refusal it kept logging was correct; the
        retrying was not.

        Calibration is exempt on purpose and stays first: darks and flats are
        SUPPOSED to be shot in daylight with the mount parked, and the dark-plan
        test harness depends on passing this gate at any hour.
        """
        cfg = config_store.cfg()
        site = self.hub.site
        twilight = cfg.safety.twilight_deg if cfg else -12.0
        for t in session.plan.targets:
            if t.calibration:
                return True
        if not schedule.dark_enough(site, twilight, now):
            return False
        for t in session.plan.targets:
            start, stop = schedule.resolve_window(t.schedule, site, twilight, now)
            if start is not None and start <= now and (stop is None or now < stop):
                return True
        return False

    async def tick(self) -> None:
        now = self._clock()
        if self.engine.running:
            self._clear_hold()              # a live run is not a hold
            return                          # anything running = no interest
        armed = session_store.armed()
        if armed is None:
            self._retry_at = 0.0            # disarmed from the UI: stop instantly
            self._clear_hold()
            self._gave_up_for = None
            # SAY SO WHEN THERE IS AN INTERRUPTED RUN NOBODY WILL RESTART.
            #
            # This used to be a bare return, and on 2026-08-11 that cost 25
            # minutes of a clear night and most of an hour of diagnosis. A
            # restart at 01:42 left session 810d46ee dormant with 165 frames and
            # 15 still to shoot; ``armed()`` ANDs status=="dormant" with
            # auto_resume, the second was False, and the tick returned without a
            # word. Nothing on any screen or in any log said the night was over.
            #
            # An unarmed dormant session is a legitimate state — it is what
            # "disarmed from the UI" looks like — so this is not a warning. But
            # it must be VISIBLE, once, or the difference between "deliberately
            # not resuming" and "silently broken" cannot be told apart at 2am.
            stalled = [s for s in session_store.load_all()
                       if s.status == "dormant" and not s.auto_resume]
            if stalled:
                newest = max(stalled, key=lambda s: s.updated_ts)
                if self._quiet_note_for != newest.id:
                    self._quiet_note_for = newest.id
                    bus.log("info",
                            f"auto-resume is NOT armed: '{newest.name}' is "
                            f"dormant with auto-resume off, so nothing will "
                            f"restart it. Arm it from the session list to "
                            f"resume tonight.", "sequence")
            else:
                self._quiet_note_for = None
            return
        if not self._window_open(armed, now):
            # THE MOST COMMON HOLD, and the one the log ring cannot answer for:
            # the branch below latches per session and logs exactly ONCE, so
            # forty minutes later there is nothing left to read. Recorded every
            # tick regardless of whether anything is logged.
            owed = armed.owed()
            self._set_hold(armed,
                           "it is not dark enough yet"
                           + (f", and this session still owes {owed} frame"
                              f"{'' if owed == 1 else 's'}" if owed else ""))
            if self._retry_at and self._gave_up_for != armed.id:
                # the window closed while we were mid-backoff: dawn beat us.
                self._gave_up_for = armed.id
                self._retry_at = 0.0
                bus.log("error", f"auto-resume gave up for tonight: "
                                 f"'{armed.name}' window closed before a "
                                 "successful start", "sequence")
            elif self._gave_up_for != armed.id:
                # THE RUN DID NOT FINISH AND NOTHING ELSE WOULD SAY SO. The
                # give-up line above only fires mid-backoff — a session vetoed
                # all night by cloud, or one that simply never got its chance,
                # went quiet at dawn with frames still owed and no line
                # anywhere admitting it.
                #
                # Worded to be true at ANY not-dark hour rather than claiming
                # "the night is over": this also fires on an afternoon boot,
                # where it is a useful thing to read (the rig knows it has work
                # pending) and where "the night is over" would be a small lie.
                # Latched per session and cleared when the window opens, so it
                # is at most one line per session per side of the night.
                self._gave_up_for = armed.id
                self._retry_at = 0.0
                owed = sum(armed.remaining().values())
                if owed:
                    bus.log("warning",
                            f"auto-resume is standing by: it is not dark, and "
                            f"'{armed.name}' still owes {owed} frame"
                            f"{'' if owed == 1 else 's'}. It stays armed and "
                            f"starts when the window opens.", "sequence")
                else:
                    bus.log("info",
                            f"auto-resume is standing by: '{armed.name}' has "
                            f"every frame it asked for.", "sequence")
            return
        self._gave_up_for = None            # window open (again): fresh night
        # A SESSION THAT KEEPS CRASHING IS NOT A SESSION TO KEEP RESTARTING.
        #
        # Continuity is the right default and it is what the rest of this tick
        # is for: a run that dies should come back and finish the night. But
        # restarting into the same fault forever is not continuity, it is a
        # loop - and the whole time it runs, the mount is tracking, the camera
        # is cold and nothing is being recorded.
        #
        # So after RESUME_GIVE_UP_AFTER consecutive crashes we stop, and we do
        # not merely stop: we put the rig away. Leaving it disarmed and live
        # would trade a crash loop for an idle mount tracking into whatever is
        # east of it until dawn-park notices at -6 degrees, which is the failure
        # this whole ladder exists to avoid.
        #
        # The counter lives on the SESSION, not here, because a crash can take
        # the process with it and a counter in memory would reset on exactly the
        # restart it is counting. It counts crashes only - a veto, a recovery
        # hold or a refusal to start leaves it untouched.
        if armed.crash_resumes >= RESUME_GIVE_UP_AFTER:
            await self._give_up_and_stow(armed)
            return
        if now < self._retry_at:
            return
        # HARD REQUIREMENT: engine.start is unguarded here, so refuse an
        # accepted-quota plan that could loop forever (spec §3 / Task 4 review).
        if quota_unbounded(armed.plan,
                           resolve_policy(armed.plan, config_store.cfg())):
            bus.log("warning", f"auto-resume refused: accepted-quota plan "
                               f"'{armed.name}' is unbounded (no reject guard "
                               f"or stop boundary) — retrying in "
                               f"{int(RETRY_INTERVAL_S / 60)} min", "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            self._set_hold(armed, "this plan counts accepted frames with no "
                                  "reject guard and no stop boundary, so it "
                                  "could run forever", self._retry_at)
            return
        veto = self.resume_veto()
        if veto is not None:
            bus.log("warning", f"auto-resume vetoed: {veto} — retrying in "
                               f"{int(RETRY_INTERVAL_S / 60)} min", "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            self._set_hold(armed, veto, self._retry_at)
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
            self._set_hold(armed, refusal, self._retry_at)
            return
        try:
            self.hub.require("camera")
            self.engine.start(armed.plan, session=armed)
        except Exception as e:              # noqa: BLE001 — refusal, not a crash
            bus.log("warning", f"auto-resume refused: {e} — retrying in "
                               f"{int(RETRY_INTERVAL_S / 60)} min", "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            self._set_hold(armed, str(e), self._retry_at)
            return
        self._retry_at = 0.0
        self._clear_hold()
        bus.log("info", f"auto-resume: '{armed.name}' resumed", "sequence")

    async def _give_up_and_stow(self, session) -> None:
        """Stop resuming this session, and PUT THE RIG AWAY.

        The stowing is the point. Disarming alone would end the crash loop and
        leave the mount tracking, the camera cold and the cover open until
        dawn-park notices at -6 degrees - which on a fault at 22:00 is eight
        hours of an unattended telescope following a sky nobody is recording.
        Trading a loop for a silent idle is not a fix.

        Idempotent by construction: disarming is what stops the next tick
        reaching here, and it is written to disk before the wind-down so a
        crash DURING the stow cannot re-enter the loop.
        """
        if self._stowed_for == session.id:
            return
        self._stowed_for = session.id
        bus.log("error",
                f"auto-resume GIVING UP on '{session.name}': "
                f"{session.crash_resumes} consecutive crashes. Something is "
                f"wrong that restarting does not fix. Parking and warming the "
                f"rig; resume it by hand once the cause is found.", "sequence")
        session.auto_resume = False
        try:
            session_store.save(session)
        except Exception as e:
            # Say so LOUDLY: an unsaved disarm means the next tick tries again,
            # and the operator needs to know the latch did not hold.
            bus.log("error", f"could not disarm '{session.name}' — auto-resume "
                             f"may retry the crash loop: {e}", "sequence")
        try:
            # The LIVE config, not a run's frozen snapshot: there is no run here
            # to have taken one, and the operator's current roof setting is the
            # one that should decide whether the shutter moves.
            cfg = config_store.cfg()
            await self.engine._wind_down(
                park=True, warm=True,
                close_dome=bool(cfg and cfg.safety.close_dome_when_done))
        except Exception as e:
            bus.log("error", f"could not stow the rig after giving up: {e} — "
                             f"THE MOUNT MAY STILL BE TRACKING", "sequence")

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

        cfg = config_store.cfg()

        # 0. IS IT SAFE TO BE OUT AT ALL — before anything moves.
        #
        #    This module's header once said every safety gate "runs inside
        #    engine.start / the run itself", and that was true when the resume
        #    path WAS engine.start. The ladder below was added in front of it and
        #    the sentence was not revisited: steps 2 and 3 plate-solve and
        #    re-center, which is real unattended motion, ahead of every gate the
        #    claim named. A rig that rebooted during the rain it had already
        #    stopped for would slew back out into it.
        #
        #    Configuration versus conditions, the same split as the solver and
        #    focus steps below: NO safety monitor is a standing choice and must
        #    not delete auto-resume for that rig, so it proceeds. A monitor that
        #    is present and says unsafe — or has gone stale, which is not
        #    evidence of safety — holds, and the ten-minute backoff is exactly
        #    right here because the sky may well clear.
        if cfg.safety.enabled and self.hub.devices.get("safety") is not None:
            reading = await self.engine.current_safety()
            if reading is None:
                return ("the safety monitor has not reported yet — not moving "
                        "the mount on an unknown verdict")
            if reading.stale:
                return ("the safety monitor's reading is stale — a reading that "
                        "stopped arriving is not evidence that it is safe")
            if not reading.is_safe:
                return (f"the safety monitor says it is not safe to observe "
                        f"({reading.reason or 'no reason given'})")

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
            # SAME CONFIGURATION-VERSUS-CONDITIONS SPLIT AS THE SOLVER BELOW, and
            # it was missing here until CI found it. A rig with no autofocus
            # provider at all — no native engine, no NINA — cannot autofocus on
            # ANY night; its owner focuses by hand and images anyway. Treating
            # that as a refusal did not make it safer, it deleted auto-resume for
            # that rig entirely and silently: the tick refused, armed the
            # ten-minute backoff, and did it again forever, with the real reason
            # only in a log line nobody was reading.
            #
            # So "no autofocus provider configured" degrades to a warning and the
            # run resumes at the focuser's current position, which is precisely
            # what that rig would have been doing unattended anyway. "There IS a
            # provider and it failed" still refuses — that is a real inability to
            # recover focus, and resuming a night that will produce nothing but
            # bloated stars is worse than waiting.
            if not self._can_autofocus():
                bus.log("warning",
                        "the focuser lost its position across the restart and no "
                        "autofocus provider is configured — resuming at its "
                        "current position, so check focus before trusting "
                        "tonight's frames", "sequence")
            else:
                bus.log("info", "focuser lost its position across the restart — "
                                "running autofocus before resuming", "sequence")
                try:
                    await self._autofocus()
                except Exception as e:  # noqa: BLE001
                    return f"autofocus after restart failed: {e}"
                # An autofocus is the MEASUREMENT the fingerprint could not
                # make, so record where it left the drawtube. Without this the
                # next step's refusal (cloud, no solve) sends the whole ladder
                # back through autofocus on every ten-minute retry, because
                # nothing else can re-establish trust once a gap has opened.
                try:
                    _fp.vouch(focuser_position=await foc.get_position())
                except Exception:  # noqa: BLE001 — bookkeeping, never a refusal
                    pass

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
                # A longer exposure than solve_and_sync's 3 s default, because
                # recovery runs once, nothing waits on it, and failing costs a
                # ten-minute backoff. More stars is the cheapest lever there is.
                #
                # It KEEPS the mount's pointing hint. Dropping it was tried on
                # 2026-08-02 and was a regression: the hint bounds ASTAP's search
                # to a 15-degree radius, which comfortably covers the ~4 degrees
                # of error a sagged or slipped mount showed that night, while
                # dropping it forces a true all-sky search that failed outright
                # on a sparse field. Bounded-and-generous beats blind.
                await self.hub.solve_and_sync(
                    exposure_s=RECOVERY_SOLVE_EXPOSURE_S)
            except Exception as e:  # noqa: BLE001
                return (f"blind plate solve failed after restart ({e}) — refusing "
                        "to slew a mount whose true position is unknown")

        # 3. RE-CENTER on the first real target. Calibration-only sessions have
        #    none and never slew, so they skip this; they still got the solve
        #    above, which costs one exposure and confirms the sky is usable.
        tgt = next((t for t in session.plan.targets if not t.calibration), None)
        if tgt is not None:
            # The altitude floor, horizon, no-go wedges, pier limits and the
            # zenith keep-out — the SAME gate every in-run slew passes. It lived
            # only inside the run, so this slew, the one made unattended by a
            # machine that just rebooted, was the single slew nothing checked.
            # ``cfg`` is passed explicitly: with no run in flight the engine's
            # own config snapshot is None, and the gate would no-op in silence.
            try:
                # ``plan`` as well as ``cfg``: the pier-collision branch reads
                # plan.meridian_flip, and in this fresh post-reboot process the
                # engine's own plan is still None — so without it the pier half
                # of the gate was inert while the altitude half ran. Same object
                # engine.start receives below, so both gates read one setting.
                await self.engine.check_slew_limits(tgt, cfg=cfg,
                                                    plan=session.plan)
            except Exception as e:  # noqa: BLE001 — SafetyAbort or a bad target
                return f"re-centering after restart refused: {e}"
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

    def _can_autofocus(self) -> bool:
        """Can this rig autofocus at all RIGHT NOW?

        The counterpart to :meth:`_can_solve`, and asked the same way: through
        the provider resolver, so this and the real autofocus path can never
        disagree about what the rig can do. ``_autofocus`` runs the NATIVE
        engine, so the question reduces to whether that engine is importable —
        a host without the Rust wheel has no autofocus, and answering "yes"
        there would send the ladder into a refusal it can never clear.

        Any failure to resolve reads as no, matching ``_can_solve``: the
        conservative answer degrades to a warning at the call site, never to a
        refusal.
        """
        try:
            from .. import providers as _providers
            if not getattr(_providers, "NATIVE_AVAILABLE", False):
                return False
            self.hub.require("focuser")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _autofocus(self) -> None:
        """The rig's real autofocus path (native), not the legacy numpy one."""
        from ..focus.native import run_native_autofocus
        await run_native_autofocus(self.hub.require("camera"),
                                   self.hub.require("focuser"))
