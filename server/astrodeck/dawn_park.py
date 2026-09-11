"""The rig's wall-clock safety tick. Two duties; the dawn park is the older.

ONE TIMER, TWO HAZARDS, and they keep different clocks:

  * THE DAWN PARK (everything below `tick`'s Sun-altitude gate): a mount left
    tracking into sunrise. Fires once, at dawn.
  * UNATTENDED GUIDING (`_check_unattended_guiding`, ABOVE that gate): a guide
    loop driving the mount with no sequence and nobody at the controls. Fires
    any time of night, and deliberately runs before the site check as well,
    because a rig that never configured a site is exactly the rig most likely
    to be left guiding.

They share this task rather than growing a second one, but they must not share
a clock. Putting the guiding check below the Sun gate would make it a dawn
check, which is precisely the defect it exists to fix: on 2026-09-11 the loop
that needed stopping had been running since 00:50.

THE RULE BOTH OF THEM ENCODE: a safety check must be driven by the clock of the
HAZARD it guards, never by the progress of the work.

WHY THIS EXISTS. Every park AstroDeck performs lives inside the sequence
engine's run lifecycle (``engine._wind_down``): park-at-end, the safety
escalation's ``abort_park_warm``, the meridian and altitude aborts. All of them
presuppose A RUN. The sun-exclusion cone is not a second net — it refuses a SLEW
whose destination is inside the cone and parks nothing.

So the one shape with no park anywhere in it is the night that never got a run
going, which is exactly what a failed session looks like: the operator slewed
somewhere, focus or guiding never came good, and they went to bed. The mount
tracks its target across the sky and keeps tracking through sunrise, holding an
uncapped aperture on the Sun a few hours later. Verified on the rig 2026-08-05
(backlog finding K / task #139).

WHAT IT DOES. One asyncio task started with the app — the AlertDispatcher /
ResumeArm lifespan-task pattern — that wakes every 60 s and, when the Sun has
climbed above the end-of-night threshold and NOTHING is running, parks the
mount. Once. Loudly.

ON SUN ALTITUDE, NEVER THE CLOCK. "Park at 06:00" is wrong twice a year at any
site, wrong by hours at the edges of a timezone, and meaningless above the
Arctic circle, where the trigger has to be able to never fire (polar night) or
fire once and stay quiet for weeks (polar day). Altitude is the physical
quantity the hazard is actually made of, and ``coords.sun_altaz`` needs nothing
but the site and the time to compute it — no network, no ephemeris file.

WHAT IT WILL NOT DO. It is a net under an EMPTY room; it must never wrestle
somebody who is standing in it. It stays out of the way of a RUNNING sequence
(the engine owns wind-down then, and racing it is worse than not acting), of a
live polar alignment or slew, of a rig deliberately configured for solar work,
and of an operator who unparks after it has acted — the latch is not re-armed
until the Sun sets again.

A PAUSED RUN IS NOT SOMEBODY STANDING IN THE ROOM. ``engine.running`` stays
True across a pause, so this net used to defer to a run that would never
progress and never wind down. On 2026-09-11 at 06:19:55 it stood down for a run
paused since 00:40 and the mount sat unparked at 10 degrees altitude, camera
holding -9.9 C, until 09:41 — four hours past sunrise, with the warm ramp never
started because it hangs off this same path. A run only holds this net off while
it is actually RUNNING; see ``_hands_off_reason``.
"""
from __future__ import annotations

import asyncio
import os
import time

from .catalog.coords import sun_altaz
from .config import config_store
from .events import bus

#: Tick cadence. The threshold sits ~20 minutes ahead of sunrise, so a minute of
#: granularity is far finer than the decision needs.
CHECK_INTERVAL_S = 60.0

#: Bounds on the two device calls, matching the sequence engine's wind-down so
#: the two parks agree about what "wedged" means.
PARK_TIMEOUT_S = 240.0
MOUNT_QUERY_TIMEOUT_S = 30.0

#: The floor under the trigger. See :func:`park_threshold_deg`.
CIVIL_TWILIGHT_DEG = -6.0

#: Consecutive identical failures logged in full before the line starts
#: coalescing. On 2026-08-09 this net failed 139 times in a row and emitted
#: THREE lines per attempt — 417 near-identical lines that flushed the 200-entry
#: live ring, so the drawer an operator actually opens showed two hours of
#: retry spam and had lost the 02:38:26 line naming the root cause. A failing
#: safety net has to stay legible, and legible means one line per fault plus a
#: periodic heartbeat, not one line per tick.
FAIL_LOG_EVERY = 30

#: Bound on the reconnect attempt below. Same order as the park-state read: a
#: reopen that takes longer than this is not going to save this tick.
RECONNECT_TIMEOUT_S = 30.0

#: Operator off-switch (mirrors ``ASTRODECK_NO_AUTOCONNECT``). Setting it is
#: announced at boot: a disabled safety net that says nothing is indistinguishable
#: from a working one.
NO_DAWN_PARK_ENV_VAR = "ASTRODECK_NO_DAWN_PARK"

#: Lanes whose presence means the rig is under somebody else's control right
#: now. ``polar`` is in here because a polar alignment at dawn is a person
#: standing at the tripod with a hand on an altitude bolt.
#:
#: ``capture`` and ``looping`` deliberately are NOT: a live loop at sunrise is
#: the abandoned-session case itself, not evidence of a human, and a frame taken
#: into a brightening sky is worth nothing next to the mount stopping.
HANDS_OFF_LANES = frozenset({"goto", "dome", "polar"})


def dawn_park_disabled() -> bool:
    """True when the operator has turned the net off via env."""
    val = (os.environ.get(NO_DAWN_PARK_ENV_VAR) or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def park_threshold_deg(cfg) -> float:
    """The Sun altitude at or above which an idle mount gets parked.

    THE LATER OF the operator's own end-of-night (``safety.twilight_deg`` — the
    same number that closes a target's observing window in
    ``schedule.resolve_window``) AND civil twilight.

    Taking the later of the two is what keeps this from fighting anyone. A rig
    configured for astronomical dark (-18) is still twelve degrees of Sun away
    from being parked when its windows close, so somebody squeezing the last
    frames out of a dawn comet is left alone. A rig whose owner observes down to
    -3 is parked at -3, which is that rig's own answer to when the night ends.

    Civil twilight is the floor rather than sunrise itself because parking is
    not instantaneous and the Sun does not stop to wait: it climbs ~0.25°/min,
    so -6° buys roughly twenty minutes before the disc clears the horizon at
    mid-latitudes — enough for a slow park, a retry, and somebody to read the
    alert. Sky brightness at -6° has swamped deep-sky imaging for a while
    already, so nothing is being taken away.
    """
    twilight = getattr(getattr(cfg, "safety", None), "twilight_deg", None)
    if twilight is None:
        return CIVIL_TWILIGHT_DEG
    return max(float(twilight), CIVIL_TWILIGHT_DEG)


class DawnPark:
    """The lifespan task. One instance, created in ``api.app`` beside the other
    services and started/stopped by the app lifespan."""

    def __init__(self, hub, engine, *, clock=None,
                 interval_s: float = CHECK_INTERVAL_S) -> None:
        self.hub = hub
        self.engine = engine
        # clock=None, NOT clock=time.time. A default argument is evaluated at
        # IMPORT and holds the original builtin, so monkeypatching time.time
        # never reached it -- and production builds this WITHOUT a clock
        # (api/app.py:147-185). A simulated night would tick this hundreds of
        # times at one frozen instant with every assertion green.
        self._clock = clock or (lambda: time.time())
        self._interval_s = interval_s
        self._task: asyncio.Task | None = None
        # Has this dawn been dealt with? Set when the mount is parked (or found
        # already parked), cleared when the Sun goes back below the threshold.
        # It is what stops the net re-parking a mount an operator deliberately
        # unparked at 10 a.m. to work on it.
        self._settled = False
        # The last "why not" logged for this dawn, so a held-off net says so
        # once instead of every minute.
        self._held: str | None = None
        self._warned_no_site = False
        #: When the guide loop was first seen running with nobody driving it,
        #: or None. NOT a dawn thing: this one runs all night. See
        #: `_check_unattended_guiding`.
        self._guiding_alone_since: float | None = None
        # Consecutive-failure bookkeeping, so a net that is failing says so
        # once and then keeps a heartbeat instead of shouting every minute.
        # See _fail and FAIL_LOG_EVERY.
        self._fail_reason: str | None = None
        self._fail_count = 0
        self._fail_since = 0.0
        # Same latch for the park-state read, which was the second of the three
        # lines per tick.
        self._read_warned: str | None = None

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if dawn_park_disabled():
            bus.log("warning",
                    f"dawn park is DISABLED ({NO_DAWN_PARK_ENV_VAR} is set) — "
                    "nothing will stop an idle mount tracking into the Sun if a "
                    "night ends without a sequence", "safety")
            return
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
            # SLEEP FIRST. A tick at t=0 lands in the middle of boot, where the
            # mount is still connecting and ``is_parked`` fails for a reason
            # that has nothing to do with the sky — the same trap that cost
            # ResumeArm a ten-minute backoff on a clear night. One interval is
            # free here: the threshold is already twenty minutes ahead of the
            # hazard.
            await asyncio.sleep(self._interval_s)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:      # noqa: BLE001 — the net must outlive its own bugs
                # A bookkeeping task that dies takes the safety net with it, and
                # takes it silently, which is the worst of both.
                bus.log("warning", f"dawn-park tick failed: {e} — the net is "
                                   "still running and will retry", "safety")

    # ------------------------------------------------------------------ tick

    async def tick(self) -> None:
        """One decision. Safe to call as often as you like."""
        cfg = config_store.cfg()
        # BEFORE THE SITE GATE AND BEFORE THE SUN, because this hazard keeps a
        # different clock from the dawn park. A guider running with nobody
        # driving it is dangerous at midnight, and every line below this one is
        # about sunrise. See `_check_unattended_guiding`.
        await self._check_unattended_guiding(cfg)
        site = self.hub.site
        if site.get("is_default", True):
            # An unconfigured site is not a location, it is a placeholder — and
            # a sunrise computed from it would fire in the middle of somebody's
            # night. Refusing is the only honest option, and it is worth saying
            # out loud exactly once.
            if not self._warned_no_site:
                self._warned_no_site = True
                bus.log("info", "dawn park is inert: this rig's observing site "
                                "has never been set, so it cannot know when its "
                                "own sun rises", "safety")
            return
        self._warned_no_site = False

        threshold = park_threshold_deg(cfg)
        alt, _az = sun_altaz(site["latitude"], site["longitude"], self._clock())

        if alt < threshold:
            if self._settled:
                # Re-arming is the one routine line this thing emits; it is how
                # an operator can see the net is live before trusting a night to
                # it.
                bus.log("info", f"dawn park re-armed for tonight (Sun back down "
                                f"to {alt:+.1f}°)", "safety")
            self._settled = False
            self._held = None
            # Silently, not through _clear_failure: last dawn's failure streak
            # is over because the Sun set, not because anything recovered, and
            # saying "recovered" here would be a lie a night later.
            self._fail_reason, self._fail_count = None, 0
            self._read_warned = None
            return
        if self._settled:
            return

        held = self._hands_off_reason(cfg)
        if held is not None:
            self._hold(held, alt)
            return

        tel = self.hub.devices.get("telescope")
        if tel is None:
            # Nothing connected is not a hazard this can fix, but it IS worth
            # saying: if that mount is powered and tracking, it is doing it
            # where nothing can see it. Info level — a rig that is simply off
            # every morning must not page anybody.
            self._hold("no telescope is connected, so if the mount is powered "
                       "and tracking, nothing here can stop it", alt)
            return
        if not getattr(tel, "connected", False) and not await self._reopen(tel, alt):
            return

        if await self._is_parked(tel):
            self._settled = True
            self._clear_failure()
            bus.log("info", f"dawn: the Sun has reached {alt:+.1f}° and the "
                            f"mount is already parked", "safety")
            await self._release_cooler(alt)
            return

        # ASK AGAIN, because the answer above cost up to MOUNT_QUERY_TIMEOUT_S
        # (30 s) and the next thing this does is bump the motion fence — which
        # ABANDONS an in-flight motion. A run or a slew that started inside that
        # window was hands-off when we looked and is not now, and fencing it
        # while its owner is standing there is precisely what this module
        # promises never to do. Re-reading is free; the window is not.
        held = self._hands_off_reason(cfg)
        if held is not None:
            self._hold(held, alt)
            return

        if self._fail_count == 0:
            # Only on the first attempt of a streak. This line announces an
            # INTENT, and repeating an intent the previous 138 attempts already
            # announced is what turned the log into wallpaper.
            bus.log("info", f"dawn park: the Sun is at {alt:+.1f}° (parking "
                            f"above {threshold:+.0f}°), no run is in progress "
                            f"and the mount is unparked — parking it now",
                    "safety")
        try:
            # The same discipline as every other park path: bump the motion
            # fence so anything that slipped in behind the checks above is
            # abandoned rather than resumed under us, then take the hub's motion
            # lock so this and any other device-touching motion path are
            # serialized on the wire.
            bump = getattr(self.hub, "bump_motion_epoch", None)
            if callable(bump):
                bump()
            lock = getattr(self.hub, "_motion_lock", None)
            if lock is not None:
                async with lock:
                    await asyncio.wait_for(tel.park(), PARK_TIMEOUT_S)
            else:
                await asyncio.wait_for(tel.park(), PARK_TIMEOUT_S)
        except asyncio.TimeoutError:
            # No latch: a park that did not happen must be retried next tick.
            self._fail(f"the mount did not park within {PARK_TIMEOUT_S:.0f}s")
            return
        except Exception as e:      # noqa: BLE001 — a refusal to park is news, not a crash
            self._fail(str(e))
            return
        self._settled = True
        self._clear_failure()
        # Logged AFTER the await, so the line means "parked", not "asked to" —
        # the same rule /api/mount/park follows. Warning level ON PURPOSE: the
        # AlertDispatcher routes warning and error to every configured sink, and
        # "your rig was left tracking through sunrise" is worth one message.
        bus.log("warning", "dawn park: mount parked. The night ended without a "
                           "sequence wind-down, so nothing else was going to "
                           "stop it tracking into the Sun", "safety")
        await self._release_cooler(alt)

    # ------------------------------------------- guiding with nobody driving

    async def _check_unattended_guiding(self, cfg) -> None:
        """Stop a guide loop that has been running with nobody driving it.

        THE HAZARD KEEPS ITS OWN CLOCK, which is the whole point of putting
        this here rather than inside the sequence engine. On 2026-09-11 the
        guider was started at 00:50:37 on a run that had been PAUSED since
        00:40. Every guard that could have caught what followed -- the re-lock
        rate gate, the pointing checks, the tracking enforcement -- lives in
        the engine's per-frame loop, and there were no frames. So a guider
        chasing a washed-out star at dawn walked the mount sixty-four degrees,
        one re-lock at a time, down to ten degrees of altitude, and nothing in
        the system was looking.

        A guide loop is only ever a servant of something else: a sequence
        taking frames, or a person at the Guide screen calibrating. When
        neither is true for ``safety.unattended_guide_min``, the loop is
        driving the mount on nobody's behalf, and the safe thing is to stop it.

        WHAT COUNTS AS SOMEBODY DRIVING is the same rule the dawn park already
        uses for its own veto, and deliberately so: a run that is ``running``
        (not merely paused, which owns no wind-down), or one of the hands-off
        lanes -- a goto, a dome move, a polar alignment -- which is a person or
        a job with a hand on the mount.

        THE TIMER IS WHY THIS IS NOT RUDE. Ten minutes is longer than any
        calibration, longer than a slew-and-solve, and longer than the gap
        between two frames of any plan this rig runs; an operator who walks
        away mid-calibration gets their calibration. What they do not get is
        four and a half hours.
        """
        limit_min = float(getattr(getattr(cfg, "safety", None),
                                  "unattended_guide_min", 0.0) or 0.0)
        if limit_min <= 0:
            self._guiding_alone_since = None
            return                          # 0 is off, as everywhere else here
        if not await self._guider_is_guiding():
            self._guiding_alone_since = None
            return
        if self._somebody_is_driving(cfg):
            self._guiding_alone_since = None
            return

        now = self._clock()
        if self._guiding_alone_since is None:
            self._guiding_alone_since = now
            return
        if now - self._guiding_alone_since < limit_min * 60.0:
            return

        # Re-armed rather than cleared, so a stop that FAILS is retried one
        # interval later instead of never. A guider that will not stop is worse
        # news than one that was left running, and it has to keep being news.
        self._guiding_alone_since = now
        g = getattr(self.hub, "guider", None)
        if g is None:
            return
        bus.log("warning",
                f"guiding has been running for {limit_min:.0f} min with no "
                f"sequence and nobody at the controls -- stopping it before it "
                f"walks the mount somewhere nobody chose", "safety")
        try:
            await asyncio.wait_for(g.stop_guiding(), MOUNT_QUERY_TIMEOUT_S)
        except Exception as e:      # noqa: BLE001 - a refusal is news, not a crash
            bus.log("error",
                    f"could not stop the unattended guide loop ({e}); it is "
                    f"still running and this will try again in "
                    f"{limit_min:.0f} min", "safety")

    async def _guider_is_guiding(self) -> bool:
        """Is a guide loop actually closed on a star right now?

        Never raises and never blocks the tick: an unreadable guider reads as
        "not guiding", because acting on a guider nobody can talk to would mean
        this net stopping something it cannot see."""
        g = getattr(self.hub, "guider", None)
        if g is None or not getattr(g, "connected", False):
            return False
        try:
            return bool(await asyncio.wait_for(g.is_active(),
                                               MOUNT_QUERY_TIMEOUT_S))
        except Exception:           # noqa: BLE001
            return False

    def _somebody_is_driving(self, cfg) -> bool:
        """Is a sequence or a person using this guide loop right now?

        Deliberately NOT ``_hands_off_reason``: that one also stands down for a
        rig configured for solar work and it LOGS when it overrides a paused
        run, neither of which belongs on a check that runs every minute all
        night long. The two conditions that matter here are shared with it: a
        run that is genuinely running, and a hands-off lane in flight."""
        eng = self.engine
        if eng is not None and getattr(eng, "running", False) \
                and not getattr(eng, "paused", False):
            return True
        return bool(self._busy_lanes() & HANDS_OFF_LANES)

    async def _release_cooler(self, alt: float) -> None:
        """Let the camera warm, now that the night is definitively over.

        THE NET UNDER A PROMISE MADE ELSEWHERE. Since 2026-09-08 the sequence
        wind-down SKIPS its warm ramp when another session is armed and
        tonight's window is still open, so the resumed run does not have to
        wait for the TEC to walk back down (measured: NGC 604 sat waiting for
        -7 to reach -10). That is right whenever the resume happens. When it
        does not — a weather veto that lasts the rest of the night, a rig
        nobody comes back to — the cooler is left holding a setpoint with
        nothing on the horizon that would ever release it, and on a 40 C day
        a sensor held at -10 inside a warm enclosure is a condensation risk,
        not just wasted power.

        So the same tick that decides "the night ended and nobody is using
        this rig" releases the cooler as well as parking the mount. Reached
        only after the hands-off checks above, and it uses the ordinary warm
        path, so the operator's SETPOINT survives (warming stops the cooler;
        it does not change what temperature this rig images at) and the ramp
        config is honoured.

        Never raises. This runs at the end of a watchdog whose whole value is
        that it cannot fail loudly enough to matter: a rig with no camera, no
        cooler, or a camera that will not answer must leave the parked mount
        parked and the log honest, not raise into the tick.
        """
        try:
            cam = self.hub.devices.get("camera")
            if cam is None or not getattr(cam, "connected", False):
                return
            if not getattr(cam, "can_cool", False):
                return
            armed = self._armed_session()
            if armed is not None:
                # MEASURED ON THE FIRST LIVE TICK, 2026-09-08 16:59. The rig
                # restarted for a deploy with the Sun up, this warmed the
                # camera "because no run is going to use it tonight" -- and
                # NGC 604 was armed to resume at dusk, so that run would have
                # spent its first quarter hour walking the TEC back down. An
                # armed session is a run that is going to use this camera; the
                # cooler it wants is the one it already has.
                bus.log("info", f"dawn: leaving the cooler at its setpoint: "
                                f"'{getattr(armed, 'name', '?')}' is armed to "
                                f"resume tonight and will want the camera cold",
                        "safety")
                return
            state = await self.hub.warm_camera(source="dawn")
        except Exception as e:      # noqa: BLE001 - see the docstring
            bus.log("warning", f"dawn: could not release the cooler ({e}); it "
                               f"is still holding its setpoint with the night "
                               f"over", "safety")
            return
        note = (state or {}).get("note") if isinstance(state, dict) else None
        if note:
            # "already at ambient", "no cooler" and friends: nothing happened,
            # and saying so beats a line claiming a warm that did not start.
            bus.log("info", f"dawn: the cooler needed no action ({note})",
                    "safety")
        else:
            bus.log("info", f"dawn: the Sun is at {alt:+.1f}° and nothing is "
                            f"armed to use this camera tonight — warming it "
                            f"rather than leaving the cooler holding its "
                            f"setpoint through the day", "safety")

    @staticmethod
    def _armed_session():
        """The session armed to resume, or None. Never raises: the answer
        decides whether a cooler is left cold, and a bookkeeping failure must
        read as "nothing armed", which is the behaviour before 2026-09-08."""
        try:
            from .sequence.session import session_store
            return session_store.armed()
        except Exception:      # noqa: BLE001
            return None

    # -------------------------------------------------------------- internals

    def _hands_off_reason(self, cfg) -> str | None:
        """Why this rig is somebody else's right now, or None if it is nobody's."""
        eng = self.engine
        if eng is not None and getattr(eng, "running", False):
            # A PAUSED RUN OWNS NO WIND-DOWN. ``running`` stays True across a
            # pause, so this deferred to a run that would never progress: on
            # 2026-09-11 at 06:19:55 the dawn park stood down for a run paused
            # since 00:40, and the mount sat unparked at 10 degrees altitude
            # with the camera at -9.9 C until 09:41 — four hours past sunrise,
            # with the warm ramp never started because it rides this same path.
            #
            # A run that is RUNNING still gets the veto even when it is holding
            # for a flip or recovering from a limit: that is a run actively
            # managing itself, and racing it really would be worse. The
            # distinction is whether anything is driving, not whether a frame
            # happens to be open.
            if not getattr(eng, "paused", False):
                return ("a sequence run is in progress and owns its own "
                        "wind-down — racing it would be worse than waiting")
            bus.log("warning",
                    "dawn: a sequence run is PAUSED, which owns no wind-down "
                    "— parking and warming anyway rather than leaving the rig "
                    "out for the day", "safety")
        solar = getattr(getattr(cfg, "safety", None), "solar_avoidance", True)
        if not solar:
            return ("this rig is configured for a solar session (sun avoidance "
                    "is off), so somebody means to be pointing at a daytime sky")
        lanes = self._busy_lanes()
        moving = sorted(lanes & HANDS_OFF_LANES)
        if moving:
            return (f"the rig is busy ({', '.join(moving)}) — that is a person "
                    f"or a job with its hands on the mount")
        return None

    def _busy_lanes(self) -> set[str]:
        """The rig's own list of long operations in flight. Read through
        ``hub.busy_lanes`` rather than ``_busy`` directly so this and the UI can
        never disagree about what counts as running."""
        fn = getattr(self.hub, "busy_lanes", None)
        if not callable(fn):
            return set()
        try:
            return set(fn() or ())
        except Exception:       # noqa: BLE001 — bookkeeping must not block a park
            return set()

    async def _reopen(self, tel, alt: float) -> bool:
        """Try to bring a dropped mount link back. True if the tick may proceed.

        A telescope OBJECT that reports not-connected is a link that died under
        us, not a rig that was never plugged in — the hub only holds a device it
        once opened. Reopening it is the single action that fixes that state,
        and it is the action nothing took on 2026-08-09, when this net retried a
        dead link 139 times across two hours and eighteen minutes while the one
        command that would have worked was never sent. A manual reconnect
        cleared it instantly.

        A failure here is an ordinary failed attempt: it goes through ``_fail``
        and is retried next tick, so a rig that is simply switched off produces
        one line and then a half-hourly heartbeat rather than silence OR spam.
        """
        try:
            await asyncio.wait_for(tel.connect(), RECONNECT_TIMEOUT_S)
        except Exception as e:      # noqa: BLE001 — includes the timeout
            self._fail(f"the mount's link is down and reopening it failed ({e})")
            return False
        bus.log("warning", f"dawn park reopened the mount's link, which had "
                           f"dropped — continuing with the park at Sun "
                           f"{alt:+.1f}°", "safety")
        return True

    def _fail(self, why: str) -> None:
        """One failed park attempt. Loud once, then a heartbeat.

        Escalation is by REPETITION COUNT, not by severity: the first failure
        and every FAIL_LOG_EVERY-th after it are logged at error, which is what
        the AlertDispatcher routes to every configured sink. The ones between
        are counted and swallowed. A net that has failed for two hours must
        still be visible in a 200-line ring — and on 2026-08-09 it was not,
        because it had written 417 lines and pushed everything else out."""
        if why != self._fail_reason:
            self._fail_reason = why
            self._fail_count = 0
            self._fail_since = self._clock()
        self._fail_count += 1
        n = self._fail_count
        if n != 1 and n % FAIL_LOG_EVERY:
            return
        mins = (self._clock() - self._fail_since) / 60.0
        tail = "" if n == 1 else (f" (attempt {n}, still failing after "
                                  f"{mins:.0f} min)")
        bus.log("error", f"DAWN PARK FAILED: {why} — the mount may still be "
                         f"tracking toward the Sun. Retrying every "
                         f"{self._interval_s:.0f}s{tail}", "safety")

    def _clear_failure(self) -> None:
        """Forget a failure streak once something worked."""
        if self._fail_reason is not None and self._fail_count > 1:
            bus.log("info", f"dawn park recovered after {self._fail_count} "
                            f"failed attempts", "safety")
        self._fail_reason = None
        self._fail_count = 0
        self._read_warned = None

    async def _is_parked(self, tel) -> bool:
        """Is the mount parked? A query failure answers NO, deliberately.

        Parking a parked mount is a no-op at every backend, so a wrong "no"
        costs one redundant command while a wrong "yes" costs the whole point of
        this module. That asymmetry is the entire reason the check is allowed to
        be this cheap.
        """
        try:
            return bool(await asyncio.wait_for(tel.is_parked(),
                                               MOUNT_QUERY_TIMEOUT_S))
        except Exception as e:  # noqa: BLE001 — includes the timeout
            # Latched per reason, like _hold: this was the second of the three
            # lines this net wrote every single minute of the 2026-08-09
            # outage, and repeating it added nothing after the first.
            if self._read_warned != str(e):
                self._read_warned = str(e)
                bus.log("warning", f"dawn park could not read the mount's park "
                                   f"state ({e}) — parking anyway, since "
                                   f"parking a parked mount does nothing",
                        "safety")
            return False

    def _hold(self, reason: str, alt: float) -> None:
        """Say why nothing happened — once per dawn, per reason.

        Silence here would be indistinguishable from a net that fired and
        worked, which is the failure mode this whole module is a fix for."""
        if self._held == reason:
            return
        self._held = reason
        bus.log("info", f"dawn park held off at Sun {alt:+.1f}°: {reason}",
                "safety")
