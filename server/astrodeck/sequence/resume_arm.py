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
rejects. ``plan_identity_errors(plan)`` is the second gate on the same terms
(#156): repeated target or step ids, or a rule naming a repeated target.

THE GATES RUN HERE, NOT ONLY IN THE RUN. This file used to say every safety
gate ran inside ``engine.start`` / the run itself. That was accurate while the
resume path WAS ``engine.start``; then ``_recover`` was added in front of it and
the sentence stayed. The ladder plate-solves and re-centers — unattended motion,
by a machine that has just rebooted — so gating only inside the run left exactly
those slews unchecked. ``_recover`` now reads the safety monitor before it moves
anything and puts the re-centering slew through the same altitude limits an
in-run slew gets. Sun avoidance was always covered: it lives at the motion
boundary inside ``goto_and_center``, not in the engine.

ONE STARTER PER SESSION (#211, spec 5.9). The ladder takes minutes, and other
starters (Run CONTINUE, /resume) can take the same session while it runs. So
``recovering`` says when the ladder is running, the ladder stops before its
next move once a run has started, and the start that follows it re-reads the
session under the store's write lock instead of trusting the copy read before
the ladder. See ``tick``.

AN OPERATOR CAN STOP THE LADDER (#220, spec 5.9 and 6.15). Abort, and a
disarm, abandon or delete of the session being recovered, call
``stop_recovery``: the ladder stops before its next step, the step it is
awaiting is cancelled, and nothing starts. ``recovery`` says which step it is
on, for ``GET /api/sequence/resume-arm``.
"""
from __future__ import annotations

import asyncio
import time

from ..config import config_store
from ..devices.base import GotoRefused
from ..events import bus
from . import schedule
from .models import (Target, duplicate_name_warning, plan_identity_errors,
                     quota_unbounded, replan_cooling)
from .policy import resolve_policy
from .session import Session, SessionUnreadable, session_store

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

#: The words ``recovery`` reports for the ladder's current step, in ladder
#: order (#220). "starting" covers the moment between the tick raising
#: ``recovering`` and the ladder's first step.
#:
#: WORDS, NEVER NUMBERS. ``GET /api/sequence/resume-arm`` is CAP_VIEW_STATUS,
#: which a viewer holds, and a step that carried the target's altitude or the
#: mount's position would hand that viewer the site: a known object's
#: altitude at a known time is a latitude (#140).
LADDER_STEPS = ("starting", "safety", "focus", "autofocus", "solve", "limits",
                "recentre")


def window_open(session: Session, site, twilight_deg: float, now: float) -> bool:
    """True when tonight's window for ANY of the session's targets is open
    (calibration targets shoot any time). Reuses schedule.resolve_window —
    the same resolution a run's scheduler freezes at start.

    A DARK SKY IS THE OUTER BOUND, and it has to be checked here rather
    than left to the per-target schedule. The default ``Schedule`` is
    ``start_mode="now"`` / ``stop_mode="none"``, so ``resolve_window``
    returns ``(now, None)`` and the test below reduces to ``now <= now and
    True`` — open, unconditionally, forever. On 2026-08-11 that had
    auto-resume burning a 4 s exposure and a full ASTAP run every ten
    minutes well after sunrise, on a mount the dawn daemon had already
    parked. The refusal it kept logging was correct; the
    retrying was not.

    Calibration is exempt on purpose and stays first: darks and flats are
    SUPPOSED to be shot in daylight with the mount parked, and the dark-plan
    test harness depends on passing this gate at any hour.

    MODULE-LEVEL BECAUSE TWO CALLERS NEED THE SAME ANSWER. The resume tick asks
    it to decide whether to start; the end-of-run wind-down asks it to decide
    whether the cooler may warm. Two copies of "is the night still on" would
    drift, and the way they would drift is silent.
    """
    for t in session.plan.targets:
        if t.calibration:
            return True
    if not schedule.dark_enough(site, twilight_deg, now):
        return False
    for t in session.plan.targets:
        start, stop = schedule.resolve_window(t.schedule, site, twilight_deg, now)
        if start is not None and start <= now and (stop is None or now < stop):
            return True
    return False


def resume_expected_tonight(hub, now: float | None = None) -> Session | None:
    """The session that is going to be resumed TONIGHT, if there is one.

    ``session_store.armed()`` says what is armed; ``window_open`` says whether
    the tick would act on it before the sky closes. Both, and only both, mean
    "this rig is going to image again in a few minutes".

    WHAT ASKS. The end-of-run wind-down, before it warms the camera. On
    2026-09-08 the NGC 7331 run ended and warmed the sensor while the
    NGC 604 session sat armed behind it; the resumed run then sat waiting for
    the TEC to walk back from -7 to -10 before it could take its first frame.

    AN UNKNOWN SITE ANSWERS None, which is the one place this deliberately does
    NOT match the tick. ``dark_enough`` fails OPEN for a default site — "we
    cannot tell where you are" must not stand auto-resume down — but the
    failure modes here are not symmetric: warming a camera that is about to
    shoot again costs a few minutes of cooling, while holding a TEC at -10 all
    day because we could not tell whether it was still night costs power, dew
    and an unattended cooler nobody asked to leave running. So this withholds
    the warm only when it can show the night is still on.
    """
    try:
        armed = session_store.armed()
    except Exception:      # noqa: BLE001 - bookkeeping must not block a warm
        return None
    if armed is None:
        return None
    from ..site_gate import site_is_set
    site = getattr(hub, "site", None) or {}
    if not site_is_set(site):
        return None
    cfg = config_store.cfg()
    twilight = cfg.safety.twilight_deg if cfg else -12.0
    if not window_open(armed, site, twilight,
                       time.time() if now is None else now):
        return None
    return armed


class ResumeArm:
    def __init__(self, engine, hub, *, clock=None, weather=None):
        self.engine = engine
        self.hub = hub
        # clock=None, NOT clock=time.time. A default argument is evaluated at
        # IMPORT and holds the original builtin, so monkeypatching time.time
        # never reached it -- and production builds this WITHOUT a clock
        # (api/app.py:147-185). A simulated night would tick this hundreds of
        # times at one frozen instant with every assertion green.
        self._clock = clock or (lambda: time.time())
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
        #: True while ``_recover`` runs. Written only by ``tick``; read
        #: through ``recovering``.
        self._recovering = False
        #: The target this tick's ladder left the mount tracking, or None
        #: (calibration-only, or no ladder yet). Cleared by ``tick`` before
        #: each ladder, set by ``_recover`` once its slew succeeds, and read
        #: by ``_tracking_for`` to hand the engine's idle clock (#202).
        self._recentred: Target | None = None
        #: THE LADDER WHILE IT RUNS, and what an operator needs to stop it
        #: (#220). ``tick`` sets the first three as the ladder begins, only
        #: ``stop_recovery`` sets the fourth, and ``tick``'s ``finally``
        #: clears all four. ``_ladder`` is the ladder's own task,
        #: so a stop can cancel the step it is awaiting; ``_ladder_session``
        #: is the session it is recovering, as read before it;
        #: ``_ladder_step`` is one of ``LADDER_STEPS``; ``_stop_why`` is the
        #: reason a stop was asked for, or None, and it is what the ladder
        #: reads between steps.
        self._ladder: asyncio.Task | None = None
        self._ladder_session: Session | None = None
        self._ladder_step: str | None = None
        self._stop_why: str | None = None

    @property
    def recovering(self) -> bool:
        """True from just before the recovery ladder's first await until it
        returns or raises.

        FOR THE START ROUTES (#211). The ladder blind-solves and re-centres
        the mount for minutes, and a manual start of any session in that time
        puts a run on a rig the ladder is still moving. A route that reads
        this and ``engine.running`` and starts, all with no await between,
        cannot land inside a ladder: ``tick`` raises the flag in the same
        synchronous stretch as its own ``engine.running`` check, so the route
        runs either wholly before that check (and the tick then finds the
        engine running) or wholly after the flag went up.

        Read-only, because only this service can say whether it is
        recovering: a writable flag is one a caller could leave raised, and
        every start that consults it would be refused until the restart.
        """
        return self._recovering

    @property
    def recovery(self) -> dict | None:
        """What the ladder is doing, for ``GET /api/sequence/resume-arm``:
        ``{step, session_id, session_name}`` while it runs, else None.

        THE 409 POINTS HERE (#220). A start refused ``resume_recovering``
        used to tell the operator to wait for something no route showed, so
        there was no way to tell a ladder that was solving from one that had
        wedged. ``step`` is a word from ``LADDER_STEPS`` and nothing else
        about the step is reported, because the route is CAP_VIEW_STATUS
        (see ``LADDER_STEPS``)."""
        s = self._ladder_session
        if not self._recovering or s is None:
            return None
        return {"step": self._ladder_step, "session_id": s.id,
                "session_name": s.name}

    def stop_recovery(self, why: str, *, session_id: str | None = None,
                      disarm: bool = False) -> str | None:
        """Stop the recovery ladder before its next step, and cancel the
        step it is awaiting (#220). Returns the id of the session whose
        ladder was stopped, or None when there was nothing to stop: no ladder
        running, or ``session_id`` given and the ladder recovering another
        session.

        ``why`` finishes the stand-down line ``tick`` logs ("auto-resume
        stood down for '<name>': <why>"). ``disarm`` also disarms the
        session, for Abort (below).

        WHO CALLS THIS. ``POST /api/sequence/abort``, with ``disarm``, for
        any session: an abort stops whatever is moving the rig. ``PATCH
        /api/sessions/{id}`` when it disarms or abandons the session being
        recovered, or arms another one (the singleton disarms this one), and
        ``DELETE /api/sessions/{id}`` of it, each with ``session_id``, so
        withdrawing a DIFFERENT session leaves this ladder alone. Before
        #220 none of them reached the ladder: an abort had no run to abort,
        so the ladder slewed on and started the session seconds later, and a
        disarm was read only after the ladder, by ``_still_startable``, so
        the mount was re-centred for a session nobody wanted any more.

        TWO MECHANISMS, because either alone leaves a gap. The flag is read
        at the ladder's between-step points (``_must_stop``, the same points
        ``_a_run_took_over`` guards), which stops a step that runs to its end
        whatever its caller does. The cancel cuts short the await the ladder
        is in, so an operator does not wait out a twelve-second solve, an
        autofocus or a slew before the ladder notices.

        A GOTO ALREADY COMMANDED MAY RUN TO ITS END ON THE MOUNT. Cancelling
        cancels the coroutine awaiting ``goto_and_center``; nothing in the
        Telescope contract says that stops the slew, because the contract
        has no abort_slew (``Telescope.stop`` zeroes ``move_axis`` motion,
        which a goto is not). Some drivers do halt on the cancel - the AM5's
        ``slew`` sends :Q# and Alpaca's sends abortslew - but the ladder is
        written for the contract, not for the drivers it has met: with any
        other driver the mount finishes the slew it was sent and tracks
        there. The ladder has returned by then, so ``recovering`` is down
        and a start is no longer refused, and nothing further moves the
        mount on the ladder's behalf.

        NOT A REFUSAL AND NOT A CRASH. ``tick`` stands the attempt down with
        one info line: no backoff (the operator's decision is not a fault to
        wait out) and no crash counted, and no hold is left on the Monitor.

        ABORT DISARMS, as Abort disarms a running session (spec 6.15,
        ``SequenceEngine._finalize_report``). Without it the next tick, 60 s
        later, would find the session still dormant and armed and run the
        ladder again, and the operator's abort would have bought one minute.
        Written here, synchronously, under the store's write
        lock on a fresh read, so the route's 200 means the file is disarmed
        and a stale copy is never saved over it. PATCH and DELETE make their
        own write and do not ask for this.

        Synchronous on purpose, and so is every caller's path to it: the
        flag is up before the route's next await, so the ladder cannot take
        a step between the operator's request and the stop."""
        s = self._ladder_session
        if not self._recovering or s is None:
            return None
        if session_id is not None and s.id != session_id:
            return None
        if self._stop_why is None:
            self._stop_why = why
        ladder = self._ladder
        if ladder is not None and not ladder.done():
            ladder.cancel()
        if disarm:
            self._disarm_stopped(s)
        return s.id

    def _disarm_stopped(self, session: Session) -> None:
        """Disarm the session whose ladder Abort stopped, and say so in the
        words the engine uses when Abort disarms a run. Best-effort: a store
        that cannot be written must not fail the abort, but it is said
        loudly, because an unsaved disarm lets the next tick try again."""
        try:
            with session_store.write_locked():
                fresh = session_store.load(session.id)
                if fresh.auto_resume:
                    fresh.auto_resume = False
                    session_store.save(fresh)
        except (KeyError, SessionUnreadable):
            return                          # nothing left to disarm
        except Exception as e:              # noqa: BLE001 - never fail an abort
            bus.log("error", f"could not disarm '{session.name}' after the "
                             f"abort - auto-resume may start it again on its "
                             f"next tick: {e}", "sequence")
            return
        bus.log("info",
                f"'{fresh.name}': stopped by hand while auto-resume was "
                f"re-centring the mount, so auto-resume is disarmed for it. "
                f"Arm it from the session list to pick it up again.",
                "sequence")

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
        """``window_open`` against the LIVE site and twilight setting.

        Kept as a method because the suite monkeypatches it to drive the tick
        without a sky; the predicate itself is module-level so the end-of-run
        wind-down can ask the same question (see ``resume_expected_tonight``).
        """
        cfg = config_store.cfg()
        twilight = cfg.safety.twilight_deg if cfg else -12.0
        return window_open(session, self.hub.site, twilight, now)

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
            # restart left session 810d46ee dormant with 165 frames and
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
        # The same identity check as the four HTTP start paths (#156), and for
        # the same reason as the quota refusal below: engine.start is
        # unguarded. HERE, ahead of `_recover`, because the ladder blind-solves
        # and re-centres - it moves the mount - and a plan that cannot start
        # must not cost a slew to find that out. Not a crash: the counter is
        # untouched, and the session stays dormant and armed for a fix.
        identity = plan_identity_errors(armed.plan)
        if identity:
            bus.log("warning", f"auto-resume refused: plan '{armed.name}' "
                               f"cannot start: {'; '.join(identity)} — "
                               f"retrying in {int(RETRY_INTERVAL_S / 60)} min",
                    "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            self._set_hold(armed, "this plan cannot start: "
                                  + "; ".join(identity), self._retry_at)
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
        # rig 2026-08-02: this tick one second after the boot sweep, devices
        # connected two seconds after that — a two-second window in which the recovery
        # ladder's plate solve failed with "no camera connected", was read as a
        # transient inability to verify the sky, and armed the ten-minute
        # backoff. The rig then sat idle for ten minutes with clear sky, a
        # working camera and an armed session, for no reason at all.
        #
        # So: no devices yet means come back on the NEXT 60s tick, with no
        # backoff and no alarming log line. It is not a condition the operator
        # needs to know about; it is the boot finishing.
        dusk = getattr(self.hub, "dusk_arm", None)
        if dusk is not None and (reason := dusk.resume_veto()):
            self._set_hold(armed, reason)
            return
        if not self._devices_ready():
            return

        # Make the rig's beliefs true again BEFORE it is allowed to move.
        #
        # ``recovering`` goes up HERE, and where matters: nothing between the
        # ``engine.running`` check at the top of this method and this line
        # awaits (every await above sits on a branch that returns), so no turn
        # of the loop separates the two. That is what lets a start route
        # refuse on the flag without racing it (see ``recovering``). An await
        # added anywhere above breaks that; put it on a returning branch or
        # re-check ``engine.running`` after it. Down in a ``finally``, because
        # a ladder that raised is not still recovering.
        #
        # ``_recentred`` is cleared first, so an earlier tick's slew cannot
        # be handed to this start: only this ladder's slew sets it.
        #
        # THE LADDER RUNS AS ITS OWN TASK so ``stop_recovery`` can cancel the
        # step it is awaiting (#220) without cancelling this tick, which has
        # to go on and stand the attempt down. Creating the task does not
        # await, so the flag still goes up in the same synchronous stretch as
        # the ``engine.running`` check.
        self._recentred = None
        self._stop_why = None
        self._ladder_session = armed
        self._ladder_step = "starting"
        self._recovering = True
        self._ladder = asyncio.ensure_future(self._recover(armed))
        try:
            refusal = await self._ladder
        except asyncio.CancelledError:
            # TWO THINGS CANCEL THIS AWAIT, and only one is ours to absorb.
            # ``stop_recovery`` cancels the ladder task alone, and the tick
            # goes on to stand down. The service's own ``stop`` (the app
            # shutting down) cancels THIS task, which cancels the ladder
            # with it, and that must propagate or the service would not
            # stop. ``cancelling()`` counts the requests to cancel this task,
            # so it tells the two apart.
            me = asyncio.current_task()
            if self._stop_why is None or (me is not None and me.cancelling()):
                raise
            refusal = None
        finally:
            self._recovering = False
            self._ladder = None
            self._ladder_session = None
            self._ladder_step = None
            stopped, self._stop_why = self._stop_why, None
        if stopped is not None:
            # AN OPERATOR STOPPED IT (#220), whether the ladder returned early
            # at a between-step point, was cancelled mid-step, or finished a
            # step that ran to its end and then found the flag. Checked ahead
            # of the refusal, because a step cut short can still return a
            # reason ("the solve failed"), and that is not why nothing
            # started. Not a refusal (no backoff: the operator's decision is
            # not a fault to wait out), not a crash (nothing counted), and not
            # a hold, so an earlier refusal's reason comes off the Monitor.
            # The session is never re-read or started here: whatever the
            # operator did to it (disarm, abandon, delete, abort) is theirs.
            self._clear_hold()
            bus.log("info", f"auto-resume stood down for '{armed.name}': "
                            f"{stopped}", "sequence")
            return
        if refusal is not None:
            bus.log("warning", f"auto-resume held: {refusal} — retrying in "
                               f"{int(RETRY_INTERVAL_S / 60)} min", "sequence")
            self._retry_at = now + RETRY_INTERVAL_S
            self._set_hold(armed, refusal, self._retry_at)
            return
        # WHAT WAS READ BEFORE THE LADDER IS STALE AFTER IT (#211).
        #
        # ``armed`` was read at the top of this tick, and the ladder between
        # there and here is minutes of awaits. Run CONTINUE or /resume can
        # start the same session in that time. If that run also ENDED inside
        # the ladder (an abort, a safety stop, a short flow), the session is
        # dormant and armed again, holding frames ``armed`` has never seen.
        # Starting ``armed`` handed the engine that stale ledger, and
        # ``engine.start``'s own save wrote it over the file: every frame the
        # other run banked stopped being counted. The FITS stay on disk, but
        # nothing counts them. If the other run was still going, the start
        # was refused "already running": no frames lost, but a healthy run was
        # logged as a refusal and got a ten-minute backoff.
        #
        # So the session is read again, checked, and started in one section
        # with no await, under the store's write lock: the section Run
        # CONTINUE uses (spec 5.9, ``SessionStore.write_locked``). Nothing on
        # the loop can move the session between this read and the start, and
        # the lock holds off a worker thread's save until ``engine.start``'s
        # own save is done. A session that is no longer this tick's to start
        # is neither a refusal nor a crash: one info line, no backoff, no crash
        # counted, and the next tick looks again.
        with session_store.write_locked():
            fresh, why_not = self._still_startable(armed)
            if fresh is None:
                # Not a hold either, so an earlier refusal's reason must not
                # stay on the Monitor: a live run is not a hold (the top of
                # this method says the same), and a session that is no longer
                # dormant, armed or on disk is not one this service holds.
                self._clear_hold()
                bus.log("info", f"auto-resume stood down for '{armed.name}': "
                                f"{why_not}", "sequence")
                return
            # THE GATES AGAIN, ON THE PLAN THAT STARTS. The identity and
            # quota refusals near the top of this method read ``armed.plan``,
            # and ``fresh.plan`` need not be that plan: PATCH
            # /api/sessions/{id} replaces a dormant session's plan with
            # neither gate, the operator's reject guards live in config that
            # can change too, and the ladder is minutes long. ``engine.start``
            # is unguarded (this module's header), so without this the one
            # plan no gate had seen would be the one started. Refused on the
            # same terms as up there: a hold and the backoff, not a crash,
            # and the next tick's own gates then refuse it before the ladder.
            gate = self._plan_refusal(fresh)
            if gate is not None:
                bus.log("warning", f"auto-resume refused: {gate} — retrying "
                                   f"in {int(RETRY_INTERVAL_S / 60)} min",
                        "sequence")
                self._retry_at = now + RETRY_INTERVAL_S
                self._set_hold(fresh, gate, self._retry_at)
                return
            # Logged HERE, once per resume, and not beside the refusal above:
            # the early returns in between (devices still booting, a dusk
            # hold) come back every 60 s tick, and the warning would repeat
            # with them. Below the re-check, and about the plan that is
            # actually started, so a stood-down resume does not warn about a
            # start that never happened.
            name_warning = duplicate_name_warning(fresh.plan)
            if name_warning:
                bus.log("warning", name_warning, "sequence")
            try:
                self.hub.require("camera")
                # A resume is a NEW run and re-reads the standing setpoint,
                # exactly as the two /api resume entries do (replan_cooling).
                # Without it a multi-night session that began with no
                # setpoint warns every single night with no reachable way to
                # act on the advice.
                #
                # ``tracking``: the ladder just left the mount tracking a
                # target, and the engine's idle clock watches only what it
                # acquired itself, so without this nothing watched that
                # mount's idle time, floor or flip point until the run set
                # a target up (#202). See ``_tracking_for``.
                self.engine.start(replan_cooling(
                    fresh.plan, config_store.cfg().cooling.setpoint_c),
                    session=fresh, tracking=self._tracking_for(fresh))
            except Exception as e:          # noqa: BLE001 — refusal, not a crash
                bus.log("warning", f"auto-resume refused: {e} — retrying in "
                                   f"{int(RETRY_INTERVAL_S / 60)} min",
                        "sequence")
                self._retry_at = now + RETRY_INTERVAL_S
                self._set_hold(fresh, str(e), self._retry_at)
                return
        self._retry_at = 0.0
        self._clear_hold()
        bus.log("info", f"auto-resume: '{fresh.name}' resumed", "sequence")

    def _still_startable(self, armed: Session) -> tuple[Session | None, str]:
        """The armed session as it is NOW, if this tick may still start it,
        or None and the reason it may not.

        Called inside ``tick``'s locked section, after the ladder: see the
        comment there. Four ways the session stops being this tick's:

        * a run is going. Usually the same session (then it is active as
          well), but not always: the operator can arm this dormant session
          while another run is live, which the PATCH route allows, and then
          only this check sees it.
        * the file is gone or no longer validates. Starting the copy read
          before the ladder would put a deleted ledger back on disk, running,
          or overwrite the corrupt file someone needs to look at.
        * it is not dormant. A run inside the ladder that banked everything
          ends ``complete`` and completion does not disarm, so ``complete``
          and armed is a real state; starting it would reopen a finished
          session.
        * it was disarmed. ``engine.start`` arms what it starts, so starting
          it would also undo the operator's disarm.
        """
        if self.engine.running:
            return None, "a run started while the recovery ladder was working"
        try:
            fresh = session_store.load(armed.id)
        except (KeyError, SessionUnreadable):
            return None, ("the session was deleted or became unreadable while "
                          "the recovery ladder was working")
        if fresh.status != "dormant":
            return None, f"it is {fresh.status} now, not dormant"
        if not fresh.auto_resume:
            return None, "it was disarmed while the recovery ladder was working"
        return fresh, ""

    def _tracking_for(self, fresh: Session) -> Target | None:
        """The target to hand ``engine.start`` as ``tracking``: the one this
        tick's ladder left the mount tracking, or None when it slewed nowhere
        (a calibration-only session).

        FROM THE RE-READ SESSION BY ID, so the engine is handed a target of
        the plan it runs, which is ``fresh``'s and not the copy the ladder
        read (``_still_startable`` says why that copy is stale).

        UNLESS THAT COPY SAYS SOMEWHERE ELSE. PATCH may replace a dormant
        session's plan while the ladder runs, and the re-read target by that
        id can then sit at other coordinates, or be gone. The mount is still
        tracking where the ladder pointed it, and that is what the idle
        clock's floor and flip checks must read; a target elsewhere would
        have them watch a place the mount is not, and None would leave the
        mount unwatched (#202). So the target as re-centred is handed over
        instead: same id, same coordinates, and nothing in the idle watch
        reads the rest."""
        tgt = self._recentred
        if tgt is None:
            return None
        for t in fresh.plan.targets:
            if (t.id == tgt.id and t.ra_hours == tgt.ra_hours
                    and t.dec_deg == tgt.dec_deg):
                return t
        return tgt

    def _plan_refusal(self, session: Session) -> str | None:
        """The identity and quota gates asked of ``session``'s plan: the
        hold reason when this service may not start it, else None.

        Only the re-check after the ladder calls this. The gates near the top
        of ``tick`` keep their own wording, because their log lines are what
        the operator and the suite already read; the rules themselves live in
        ``models`` and ``policy``, so the two sites cannot disagree about a
        plan, only phrase it differently.
        """
        identity = plan_identity_errors(session.plan)
        if identity:
            return "this plan cannot start: " + "; ".join(identity)
        if quota_unbounded(session.plan,
                           resolve_policy(session.plan, config_store.cfg())):
            return ("this plan counts accepted frames with no reject guard "
                    "and no stop boundary, so it could run forever")
        return None

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

        It also returns None, early, when a run starts while it works (#211)
        or an operator asks it to stop (#220): before each step that would
        focus, expose or slew it asks ``_must_stop`` and stops there. That
        is not a refusal - nothing is wrong with the rig, and a backoff would
        only delay the next resume - so it does not say why; ``tick`` finds
        the stop request or the running engine and stands the attempt down
        with the one line that does. A stop also cancels whatever step this
        is awaiting (``stop_recovery``), so the question matters most for a
        step that ran to its end regardless.

        It names each step in ``_ladder_step`` as it reaches it, for
        ``recovery``: words from ``LADDER_STEPS``, never a number.

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
            self._ladder_step = "safety"
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
        self._ladder_step = "focus"
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
                if self._must_stop():
                    return None
                bus.log("info", "focuser lost its position across the restart — "
                                "running autofocus before resuming", "sequence")
                self._ladder_step = "autofocus"
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
        if self._must_stop():
            return None
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
                self._ladder_step = "solve"
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
            # THE TARGET'S OWN START FLOOR, and it is asked FIRST.
            #
            # The gate below is the MOUNT's floor - config, horizon, wedges,
            # pier. The plan carries a second, usually higher one:
            # ``Schedule.min_altitude_deg``, the altitude at which this target
            # is worth shooting, which the engine enforces when a run starts a
            # target (``_enforce_altitude_floor``). The re-centre happens
            # before any of that, so on 2026-09-06 the ladder asked
            # the AM5 to slew to NGC 604 at nine degrees - a target its own
            # plan would not have started for two more hours. The mount refused
            # (e6, its own horizon limit) and the resume held on a ten-minute
            # retry until the target rose, which is the right outcome reached
            # by the wrong route: the plan already knew the answer.
            #
            # A refusal, not a wait, because RETRY_INTERVAL_S is already the
            # cadence for exactly this - come back in ten minutes and ask the
            # sky again.
            floor = float(getattr(getattr(tgt, "schedule", None),
                                  "min_altitude_deg", 0.0) or 0.0)
            if floor > 0:
                from .engine import _frame_altitude
                alt = _frame_altitude(tgt, self.hub.site, self._clock())
                # ``None`` is "nobody can say" - an unset site, a bad
                # coordinate - and it must not read as "below the floor". The
                # engine's own floor gate makes the same tri-state distinction,
                # and for the same reason: refusing on an unreadable altitude
                # would strand every rig whose site is not configured.
                if alt is not None and alt < floor:
                    return (f"{tgt.name} is at {alt:.0f} deg, below its "
                            f"{floor:.0f} deg start floor; not slewing yet"
                            + self._floor_eta_note(tgt, floor))
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
                self._ladder_step = "limits"
                await self.engine.check_slew_limits(tgt, cfg=cfg,
                                                    plan=session.plan)
            except Exception as e:  # noqa: BLE001 — SafetyAbort or a bad target
                return f"re-centering after restart refused: {e}"
            # BELOW the limit check, not above it: that check awaits too, and
            # the slew is the step that must never land on a live run, nor
            # follow an operator's stop (#220). A stop that arrives once the
            # goto is under way cancels this await; see ``stop_recovery`` for
            # what that does and does not do to the mount.
            if self._must_stop():
                return None
            self._ladder_step = "recentre"
            try:
                await self.hub.goto_and_center(tgt.ra_hours, tgt.dec_deg)
            except GotoRefused as e:
                # THE MOUNT SAID NO, which is a different thing from the slew
                # failing, and the operator can act on the difference: a
                # refusal names a limit to wait out or clear, a failure names
                # something broken. The driver's own words, not the wire code
                # the log used to carry alone.
                return f"re-centering after restart refused by the mount: {e.reason}"
            except Exception as e:  # noqa: BLE001
                return f"re-centering after restart failed: {e}"
            # The mount is tracking this target now: ``tick`` hands it to the
            # engine's idle clock with the start (#202).
            self._recentred = tgt
        return None

    def _a_run_took_over(self) -> bool:
        """Has a run started since ``tick`` checked ``engine.running``?

        ``tick`` checks once, before the ladder, and the ladder then awaits
        a safety read, a focuser, an autofocus, a solve and a limit check. Any
        of those awaits lets a manual start in, and from then on the camera,
        focuser and mount belong to that run: an autofocus would move the
        focuser under its exposures, a solve would take the camera, and the
        re-centring slew would drag the mount off the run's target. So the
        ladder asks this right before each of those three steps, with no
        await between the question and the step.
        """
        return bool(self.engine.running)

    def _must_stop(self) -> bool:
        """Should the ladder stop before its next step? When an operator
        asked it to (``stop_recovery``, #220) or a run has taken over
        (``_a_run_took_over``, #211). Asked at the same three points, with no
        await between the question and the step.

        The operator's flag matters most after a step that ran to its end
        despite the cancel ``stop_recovery`` sent it: without this read the
        ladder would carry on from that step to the next as if nothing had
        been asked."""
        return self._stop_why is not None or self._a_run_took_over()

    def _floor_eta_note(self, target, floor: float) -> str:
        """How long the wait above is, as a parenthetical, or empty when
        nobody can say.

        Reuses the scheduler's own gate-crossing search - the one that fills
        ``gating_status``'s ``eta_s`` for a target waiting on altitude - so the
        hold and the Tonight page cannot quote different numbers for the same
        wait. Best-effort throughout: no site, no crossing inside a sidereal
        day, or any arithmetic failure simply means no note."""
        try:
            lat, lon = schedule._lat_lon(self.hub.site)
            eta = schedule._time_to_gate(target, lat, lon, floor,
                                         self._clock(), None)
        except Exception:  # noqa: BLE001 - a missing ETA is not a failure
            return ""
        if eta is None or eta <= 0:
            return ""
        mins = eta / 60.0
        return (f" (it reaches {floor:.0f} deg in about "
                + (f"{mins:.0f} min)" if mins < 90 else f"{mins / 60.0:.1f} h)"))

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
