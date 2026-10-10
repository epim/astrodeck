# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Sun watch — the net under a tube the Sun is coming TO (task #150).

WHY THIS EXISTS. ``Hub._check_solar`` is a PRE-SLEW GATE. Its three callers are
goto, sequence-start and the TPPA rotation, and all it can ever do is refuse a
destination. Nothing in this codebase samples where the tube is pointing against
where the Sun is, on a timer. ``dawn_park`` says so in its own header — "the
sun-exclusion cone is not a second net — it refuses a SLEW" — and the daemon it
introduces answers a DIFFERENT question: dawn park triggers on the Sun's
ALTITUDE at sunrise, i.e. "the night ended", and it stands down the moment a
sequence is running or the mount reports itself parked.

Neither covers the hazard the owner asked for on 2026-08-06 (readiness §"Owner
decisions" 5b): the tube is left somewhere, nobody slews anything, and the Sun
arrives. Consequence: a cooked sensor, a melted focuser, or a fire. The refusal
of a slew INTO the cone is not the same feature as noticing the Sun coming to a
stationary tube, and only one of the two was built.

WHAT IT DOES. One asyncio task started with the app — the DawnPark / ResumeArm /
AlertDispatcher lifespan-task pattern — that wakes every 60 s, reads the mount's
CURRENT pointing, and asks whether the Sun will be inside the exclusion cone
within :data:`LEAD_TIME_S`. If it will be, it parks the mount and says so at
error level.

PREDICTIVE, NOT REACTIVE. Acting when the Sun is already in the cone is acting
after the damage has started; the whole point is to move the tube BEFORE the
Sun gets there. So every tick projects the geometry forward and decides on the
projection — see :func:`closest_approach` for the two ways a tube moves and
:data:`LEAD_TIME_S` for how far ahead is far enough.

SITE-INDEPENDENT, like the gate it complements. The whole decision is RA/Dec:
the Sun's apparent place from the date, the mount's pointing from the mount. No
latitude, no longitude, no horizon. That is deliberate — ``_check_solar`` works
on a rig whose site has never been set, and a rig that has not been told where
it is is exactly the rig most likely to be left pointing somewhere at dawn.

WHAT IT WILL NOT DO. It must not wrestle somebody who is standing in the room:
it stays out of the way of a sequence run, of a live slew / polar alignment /
dome move, and of a rig deliberately configured for solar work
(``safety.solar_avoidance`` False — the SAME disarm as the pre-slew gate, and
the one the owner insisted on, because solar astronomy is a legitimate use of
this software). It never commands a park at a mount that already reports itself
parked, and it is silent — completely silent — on every tick where the sky is
somewhere else.

WHEN IT CANNOT SEE THE MOUNT (issue #137). The Sun does not stop closing on a
tube because the link to it dropped, so a mount that cannot be read is BLIND,
not safe and not somebody else's problem. A telescope object whose link is down
and a telescope that is connected but will not answer are one outage on one
clock (:meth:`SunWatch._blind`), loud on a repetition count rather than latched
silent. While blind the net:

* makes ONE bounded reopen per tick when nobody else has the mount (a dropped
  link is the one thing a reopen fixes, and nothing else was going to try);
* projects the LAST position it read, both as a tube that has since stopped and
  as one that is still tracking, and parks if either reaches the cone inside
  the lead window, through the same park block as the live path and under the
  same hands-off rules;
* publishes that it is blind (``state()``, on ``/api/safety/state``) as times
  and booleans only. The last position is NEVER logged or published: a mount's
  pointing is a latitude oracle (#140), so only separations and ages leave this
  module;
* says ONE error the first blind tick the Sun is above the dawn-park threshold
  (``dawn_park.park_threshold_deg``, read where dawn park reads it), because
  from then on a mount that is not parked may be tracking toward the Sun with
  nothing watching it, whatever the fallback projects. Once per blind streak
  and per Sun-up stretch, never with a position or the Sun's altitude in it.

OTHER PATHS PARK THE MOUNT TOO (#696). The fallback projects from the last
position THIS net read, and the engine's wind-down and ``dawn_park`` move the
tube without this net seeing it. They tell it through :meth:`SunWatch.
note_parked`, reached as ``getattr(hub, "sun_watch", None)``: this class sets
``hub.sun_watch`` itself, as ``DewController`` sets ``hub.dew_controller``. A
noted park is "no motion possible" to the blind projection until the next live
read, so a link that drops right after a park does not turn the pre-park
pointing into a false "Parking now".

WHEN THE POSITION IS UNKNOWN (#888, owner ruling 4B, 2026-10-09). With the
rig's position latch set (``devices.base.position_known_for_motion`` False)
this net parks nothing, because a park is a goto to the model's home aimed
from a position the system does not know, and it changes no tracking. Its
projection reads the same untrusted position, so it judges no approach: the
latched state is the blind state for the alert, without its park. While the
Sun is above the dawn-park threshold it says one error line in fixed words
(cover the tube; Trust position only for a tube really at home, else bring
it home by eye with a pad key first), repeated on the blind cadence, and
publishes the state beside the blind fields; below it, one info hold in the
same safe order. With no site set the Sun cannot be judged at all (#895), so
the net cannot tell day from night and takes the fail-safe answer: a warning
in the same safe order on the same cadence, never the quiet night hold.
Trust position clears the latch and the net resumes on the next tick
(`SunWatch._position_unknown_tick`).

A telescope object that does not exist at all (``tel is None``) is a rig that
was never connected, or was deliberately disconnected. That stays a latched info
hold: it must not page, and it ends a blind streak rather than extending one.
"""
from __future__ import annotations

import asyncio
import os
import time

from .aio import reap
from .catalog.coords import angular_sep_deg, sun_altaz, sun_radec
from .config import config_store
from .dawn_park import park_threshold_deg
from .devices.base import position_known_for_motion
from .events import bus
from .mount_offset import POSITION_UNKNOWN_SAFE_ORDER

#: Tick cadence, matching ``dawn_park``. The decision is made on a projection
#: 30 minutes wide, so a minute of granularity gives ~30 chances to act before
#: the projected entry — and the mount's pointing is already polled far harder
#: than this by the 2 s status loop.
CHECK_INTERVAL_S = 60.0

#: HOW FAR AHEAD WE DECIDE. 30 minutes.
#:
#: Derived from how long getting safe actually takes, against the fastest way
#: the Sun closes on a tube. The fast case is a mount that is NOT tracking (see
#: :func:`closest_approach`): the sky turns under it at the sidereal rate, so
#: the Sun sweeps toward the tube at 15.04°/h = 0.25°/min. 30 minutes of lead is
#: therefore 7.5° of extra sky between the tube and the cone at the moment we
#: act.
#:
#: 7.5° buys: one tick of granularity (60 s = 0.25°), a park that is allowed to
#: take PARK_TIMEOUT_S (240 s = 1°), and still four more whole park attempts
#: after the first one fails — plus time for the error to reach a phone and a
#: human to reach the roof. Against the slow case (a TRACKING tube, which the
#: Sun approaches at only ~1°/day of ecliptic motion) 30 minutes is enormously
#: more than needed, and costs nothing: the projection just agrees with the
#: present.
#:
#: Longer is not free. Every extra minute of lead is sky the rig refuses to
#: point at, and a lead measured in hours would start parking mounts imaging
#: perfectly good dawn targets that the Sun never actually reaches.
LEAD_TIME_S = 1800.0

#: Sampling step across the lead window. The minimum separation is wanted over
#: the whole interval, not just at its far end: with a small configured cone a
#: fast (non-tracking) approach can enter AND leave inside one window, and a
#: two-endpoint check would see 40° at both ends and miss the 2° in the middle.
#: 5 minutes = 1.25° of relative motion in the fast case, fine against cones
#: that are degrees wide, and it is 7 evaluations of cheap trig per tick.
PROJECTION_STEP_S = 300.0

#: Bounds on the device calls, matching ``dawn_park`` and the engine's
#: wind-down so every park path in the codebase agrees what "wedged" means.
PARK_TIMEOUT_S = 240.0
MOUNT_QUERY_TIMEOUT_S = 30.0

#: Bound on the one reopen attempted per tick when the telescope's link is down
#: (#137). The same figure and the same reasoning as ``dawn_park``'s: a reopen
#: that takes longer than this is not going to save this tick. Defined here
#: rather than imported so this net does not depend on dawn_park's internals.
RECONNECT_TIMEOUT_S = 30.0

#: Escalation for the unreadable-mount hold (issue #137). The Sun does not
#: stop closing on the tube because the position feed did, so that hold must
#: not latch silent for the rest of an outage the way every OTHER ``_hold``
#: reason is allowed to. Counted in TICKS: at the production ``CHECK_INTERVAL_S``
#: of 60 s a tick and a minute are the same thing, so these numbers double as
#: minutes. Same shape as ``dawn_park._fail`` -- quiet on the first tick (a
#: single lost read must not page anybody), one warning once the outage has
#: run long enough to matter, then a repeating error for as long as it lasts.
BLIND_WARN_AFTER = 10
BLIND_ERROR_AFTER = 30

#: Repeat cadence once escalated to error, matching ``dawn_park.FAIL_LOG_EVERY``
#: for the same reason: on 2026-08-09 a different net without this cap wrote
#: 417 near-identical lines and flushed the 200-entry ring. An outage that
#: outlives BLIND_ERROR_AFTER must keep saying so, not go quiet again -- that
#: silence is exactly what #137 measured -- but it must say so on a cadence,
#: not once per tick.
BLIND_LOG_EVERY = 30

#: Sidereal hours per solar hour. A mount with tracking OFF holds its HOUR
#: ANGLE, and RA = LST - HA, so its RA advances with local sidereal time.
SIDEREAL_HOURS_PER_SOLAR_HOUR = 1.0027379

#: Operator off-switch, mirroring ``ASTRODECK_NO_DAWN_PARK``. Announced at boot:
#: a disabled safety net that says nothing is indistinguishable from a working
#: one. NOT the primary disarm — that is ``safety.solar_avoidance``, which this
#: honours because it is the same flag the pre-slew gate honours and the owner
#: asked for exactly one switch.
NO_SUN_WATCH_ENV_VAR = "ASTRODECK_NO_SUN_WATCH"

#: Lanes whose presence means somebody else has their hands on the mount right
#: now. Same set as ``dawn_park.HANDS_OFF_LANES`` and for the same reasons:
#: ``capture`` and ``looping`` are deliberately absent, because a frame taken
#: while the Sun closes in is worth nothing next to the mount moving.
HANDS_OFF_LANES = frozenset({"goto", "dome", "polar"})

#: The error line while the mount's position is unknown and the Sun is above
#: the dawn-park threshold (#888, owner ruling 4B, 2026-10-09). Fixed words:
#: no figure, no position. Cover the tube, then the shared safe order
#: (``mount_offset.POSITION_UNKNOWN_SAFE_ORDER``: Trust position only for a
#: tube really at home, else home by eye with a pad key first), never a goto.
#: 45 + 86 = 131 characters to the end of the action, inside the UI's
#: 137-character cut.
POSITION_UNKNOWN_DAYLIGHT = (
    f"sun watch blind, position unknown: cover it; "
    f"{POSITION_UNKNOWN_SAFE_ORDER} It will not park: a park is aimed from "
    f"that position")
#: The info hold while the position is unknown and the Sun is below the
#: dawn-park threshold, said through ``_hold`` with its "sun watch held off: "
#: prefix (#888 round 3). The same safe order, never a goto: 20 + 27 + 86 = 133
#: characters to the end of the action.
POSITION_UNKNOWN_NIGHT_HOLD = (
    f"position unknown, no park: {POSITION_UNKNOWN_SAFE_ORDER}")
#: The warning while the position is unknown and this net CANNOT JUDGE THE SUN:
#: no site is set, so it cannot tell day from night (#895; the helper's other
#: "cannot tell", a Sun that will not compute, lands here too, so the cause is
#: said as both). Not knowing is not night: the fail-safe answer is the daylight
#: one, so it covers the tube as the daylight line does, at warning level (the
#: AlertDispatcher routes warning and error) on the blind cadence. Fixed words,
#: no figure: with no site there is none to carry. 39 + 86 = 125 characters to
#: the end of the action, inside the UI's 137-character cut. The cause and
#: the remedy (set the site) follow it, so they are in the paged message and the
#: durable log but not in the UI's cut of the line.
POSITION_UNKNOWN_SUN_UNKNOWN = (
    f"sun watch: position unknown, cover it; {POSITION_UNKNOWN_SAFE_ORDER} "
    f"It cannot judge the Sun (no site set, or the Sun cannot be computed), "
    f"so it cannot tell day from night. It will not park: a park is aimed "
    f"from that position. If no site is set, set one and the net can judge "
    f"the Sun")


def sun_watch_disabled() -> bool:
    """True when the operator has turned the net off via env."""
    val = (os.environ.get(NO_SUN_WATCH_ENV_VAR) or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def project_pointing(ra_hours: float, dec_deg: float, *, tracking: bool,
                     dt_s: float) -> tuple[float, float]:
    """Where the tube will be pointing ``dt_s`` from now, in RA/Dec.

    TWO WAYS A TUBE MOVES, and they differ by three orders of magnitude:

    * TRACKING ON — the mount holds RA/Dec, so the pointing is a constant and
      the only thing closing the gap is the Sun's own ~0.9856°/day along the
      ecliptic. Hours of tracking barely move the separation.
    * TRACKING OFF — the mount holds its hour angle (it is bolted to the Earth
      and the Earth is turning), so its RA advances at the sidereal rate:
      15.04°/h. This is the dangerous one, and it is what a tube left after a
      failed session, a power blip or an aborted slew is doing.
    """
    if tracking:
        return ra_hours, dec_deg
    ra = ra_hours + (dt_s / 3600.0) * SIDEREAL_HOURS_PER_SOLAR_HOUR
    return ra % 24.0, dec_deg


def closest_approach(ra_hours: float, dec_deg: float, *, tracking: bool,
                     now: float, lead_s: float = LEAD_TIME_S,
                     step_s: float = PROJECTION_STEP_S) -> tuple[float, float]:
    """Closest the Sun gets to the tube over ``[now, now + lead_s]``.

    Returns ``(min_separation_deg, seconds_from_now)``. ``now`` itself is always
    one of the samples, so a Sun that is ALREADY in the cone is reported with an
    offset of 0 rather than being missed by a projection that only looks ahead.
    """
    steps = max(1, int(round(lead_s / step_s)))
    offsets = [i * (lead_s / steps) for i in range(steps + 1)]
    # PRECONDITION: a bad lead/step pair must not silently reduce this to "the
    # Sun is never anywhere", which is a watchdog that always says safe.
    assert offsets and offsets[0] == 0.0, "the projection must include now"
    best_sep = 360.0
    best_off = 0.0
    for off in offsets:
        sun_ra, sun_dec = sun_radec(now + off)
        ra, dec = project_pointing(ra_hours, dec_deg, tracking=tracking,
                                   dt_s=off)
        sep = angular_sep_deg(ra, dec, sun_ra, sun_dec)
        if sep < best_sep:
            best_sep, best_off = sep, off
    return best_sep, best_off


class SunWatch:
    """The lifespan task. One instance, created in ``api.app`` beside the other
    services and started/stopped by the app lifespan."""

    def __init__(self, hub, engine, *, clock=None,
                 interval_s: float = CHECK_INTERVAL_S,
                 lead_s: float = LEAD_TIME_S) -> None:
        self.hub = hub
        self.engine = engine
        # clock=None, NOT clock=time.time. A default argument is evaluated at
        # IMPORT and holds the original builtin, so monkeypatching time.time
        # never reached it -- and production builds this WITHOUT a clock
        # (api/app.py:147-185). A simulated night would tick this hundreds of
        # times at one frozen instant with every assertion green.
        self._clock = clock or (lambda: time.time())
        self._interval_s = interval_s
        self._lead_s = lead_s
        self._task: asyncio.Task | None = None
        # Set once a park has been COMMANDED AND ACCEPTED for the approach in
        # progress. Cleared the moment the projection comes back clear (or the
        # mount reports itself parked). It exists to stop the daemon shouting
        # and re-commanding every 60 s at a mount that took the park and did not
        # move — not to disarm the net: an operator who unparks back into the
        # Sun gets parked again, because the disarm for that is
        # ``safety.solar_avoidance``, not a latch.
        self._acted = False
        # The last "why nothing happened" said for this approach, so a held-off
        # net says it once instead of every minute.
        self._held: str | None = None
        # Consecutive-tick bookkeeping for the unreadable-mount hold, mirroring
        # dawn_park._fail_reason/_fail_count/_fail_since: unlike every other
        # _hold reason, this one must not latch silent, because the hazard
        # (the Sun) keeps closing for as long as the outage lasts. See
        # _blind() and BLIND_WARN_AFTER/BLIND_ERROR_AFTER above.
        self._blind_reason: str | None = None
        self._blind_count = 0
        self._blind_since = 0.0
        # THE LAST POSITION THIS NET READ AND CAN TRUST: (RA hours, Dec deg,
        # read_at). The fallback projects from it while the mount is dark. It is
        # deliberately NOT cleared by _clear_blind: the whole point is that it
        # outlives the outage that wants it. None when nothing has been read, or
        # when the last read was taken while a goto/dome/polar lane had the
        # mount in motion -- a tube in transit has no pointing to project from,
        # and the reading the mount gave is not where it will be.
        self._last_good: tuple[float, float, float] | None = None
        # When ANY position was last read, trusted or not. Published as
        # ``last_position_at``, which must stay honest ("a read did happen")
        # even on the transit read that _last_good refuses.
        self._last_read_at: float | None = None
        # Consecutive blind park ATTEMPTS, so a park that fails every tick on a
        # dead link is logged on a cadence rather than twice a minute (see
        # _blind_projection). Not a latch: the park is retried every tick.
        self._blind_park_tries = 0
        # When ANOTHER path (dawn park, the engine's wind-down) last told this
        # net it had parked the mount and confirmed it, or None. Ended by the
        # next live read that began after it (``_remember``); until then the
        # blind projection takes the mount as parked. See ``note_parked``.
        self._parked_noted_at: float | None = None
        # Has this blind streak already said "blind in daylight" for the Sun-up
        # stretch it is in? Released when the Sun goes back below the dawn-park
        # threshold and when the streak ends, so each dawn of an outage and
        # each new outage says it once (see ``_blind_in_daylight``).
        self._blind_daylight_said = False
        # THE POSITION LATCH (#888, owner ruling 4B): since when this net has
        # seen the rig's position unknown (``position_known_for_motion``
        # False), or None. While it is set the net parks nothing and changes
        # no tracking, and ``_unknown_daylight_ticks`` counts the ticks the
        # Sun has been above the dawn-park threshold or could not be judged
        # (no site), for the alert line's cadence (`_position_unknown_tick`).
        # ``_unknown_judged`` is whether the last latched tick could judge the
        # Sun at all: the count belongs to ONE of the two lines (the no-site
        # warning or the daylight error), so it restarts when that changes.
        self._unknown_since: float | None = None
        self._unknown_daylight_ticks = 0
        self._unknown_judged: bool | None = None
        # The other park paths reach this net through the hub, so hub.py and
        # api/app.py carry no wiring for it: the attach-yourself shape
        # ``DewController`` uses for ``hub.dew_controller``. Total: a hub that
        # refuses attributes is a test double, and a net that cannot be found
        # costs the false-park protection, never a failed boot.
        try:
            hub.sun_watch = self
        except Exception:       # pragma: no cover - an exotic hub double
            pass

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if sun_watch_disabled():
            bus.log("warning",
                    f"sun watch is DISABLED ({NO_SUN_WATCH_ENV_VAR} is set) — "
                    "nothing will move the tube out of the way if the Sun "
                    "tracks onto it", "safety")
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await reap(self._task)
            self._task = None

    def note_parked(self) -> None:
        """Another path has just parked the mount and CONFIRMED it (#696).

        Called by ``dawn_park`` after its park (or after finding the mount
        already parked) and by the engine's wind-down after a park it read
        back. Those move the tube without this net seeing it, and the blind
        fallback projects from ``_last_good``, the last position THIS net read:
        a link that dropped before the next tick left it projecting the
        pre-park pointing, so it logged a false "Parking now" error (which
        pages the owner) and sent a park to a mount that was already parked.

        A noted park is "no motion possible" to the blind projection, and it
        suppresses the daylight error (a parked tube is not tracking toward
        the Sun), but it does NOT quiet the ordinary blind lines: the net is
        still blind. It lasts until the next live read of the mount that began
        after it (``_remember``), when the net's own position is true again.
        A time, never a position."""
        self._parked_noted_at = self._clock()

    def state(self) -> dict:
        """What ``/api/safety/state`` publishes about this net (#137).

        TIMES AND BOOLEANS ONLY. ``blind`` says the net cannot currently see
        the mount; ``blind_since`` is when that streak began (None unless blind);
        ``last_position_at`` is when any position was last read (None if none
        has been since boot); ``armed`` is whether the task is alive;
        ``position_unknown`` says the rig's position latch is set, so the net
        cannot judge an approach and parks nothing (#888), and
        ``position_unknown_since`` is when it first saw that (None unless
        set). The
        position itself is never part of this, and neither is anything derived
        from it: a mount's pointing is a latitude oracle (#140), and the route
        this feeds is readable by a viewer."""
        blind = self._blind_reason is not None
        return {
            "blind": blind,
            "blind_since": self._blind_since if blind else None,
            # #888: the rig's position latch is set, so this net cannot judge
            # an approach and will not park; since when it has seen that.
            "position_unknown": self._unknown_since is not None,
            "position_unknown_since": self._unknown_since,
            "last_position_at": self._last_read_at,
            "armed": self._task is not None and not self._task.done(),
        }

    async def _run(self) -> None:
        while True:
            # SLEEP FIRST, like dawn park: a tick at t=0 lands mid-boot, where
            # the mount is still connecting and ``get_position`` fails for a
            # reason that has nothing to do with the sky. One interval is free —
            # the decision is made 30 minutes ahead of the hazard.
            await asyncio.sleep(self._interval_s)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:      # noqa: BLE001 — the net must outlive its own bugs
                # A bookkeeping task that dies takes the safety net with it, and
                # takes it silently, which is the worst of both.
                bus.log("warning", f"sun-watch tick failed: {e} — the net is "
                                   "still running and will retry", "safety")

    # ------------------------------------------------------------------ tick

    async def tick(self) -> None:
        """One decision. Safe to call as often as you like."""
        cfg = config_store.cfg()
        safety = getattr(cfg, "safety", None)
        if not getattr(safety, "solar_avoidance", True):
            # THE DISARM. Same flag ``_check_solar`` and ``dawn_park`` read, so
            # a rig set up for solar work turns off one switch, not three.
            self._hold("this rig is configured for a solar session (sun "
                       "avoidance is off), so somebody means to be pointing "
                       "at a daytime sky", None)
            self._acted = False
            # Deliberately not watching is not BLIND: publishing ``blind: true``
            # for a rig whose owner switched the net off would nag about a
            # mount nobody asked this net to look at.
            self._drop_blind()
            self._drop_unknown()
            return
        cone = float(getattr(safety, "solar_exclusion_deg", 30.0) or 0.0)
        if cone <= 0:
            self._hold("the sun-exclusion cone is set to 0°, which disarms "
                       "every solar guard including this one", None)
            self._acted = False
            self._drop_blind()
            self._drop_unknown()
            return

        tel = self.hub.devices.get("telescope")
        if tel is None:
            # Not a hazard this can fix, but worth saying once: if that mount is
            # powered and stopped, the Sun is still coming and nothing here can
            # see it. Info level -- a rig that was never connected, or that an
            # operator deliberately disconnected (hub teardown empties
            # ``hub.devices``), must not page anybody. THIS is the only branch
            # that stays quiet; a telescope object that exists but is dark is
            # BLIND (below), because the hub only holds a device it once opened.
            self._hold("no telescope is connected, so nothing here can see "
                       "where the tube is pointing", None)
            self._acted = False
            # A deliberate disconnect ends a blind streak; leaving it set would
            # publish ``blind: true`` for a mount that is gone on purpose.
            self._drop_blind()
            self._drop_unknown()
            return

        if not position_known_for_motion(self.hub, tel):
            # BEFORE THE LINK CHECK AND THE READ (#888, owner ruling 4B): the
            # blind fallback below would park too, and a park is aimed from
            # the position nobody knows. Synchronous, touches no device.
            self._position_unknown_tick(cfg)
            return
        if self._unknown_since is not None:
            # Trust position (or a sync proved away from the pole) cleared
            # it: the normal net resumes on this very tick, its hold latch
            # forgotten so a hold it then meets is said.
            self._held = None
            self._drop_unknown()

        if not getattr(tel, "connected", False):
            # PRESENT BUT DROPPED (#137). This used to share the branch above,
            # so a mount whose link died went quiet after one info line -- the
            # outage the issue measured at 8h24m. It is blind, and a dropped
            # link is the one blindness a reopen can end, so try ONE bounded
            # reopen per tick, but only when nobody else has the mount: a run
            # or a goto/dome/polar lane owns the port, and a second task
            # reopening it under them is the race the hands-off rule exists for.
            if self._hands_off_reason(cfg) is None:
                await self._reopen(tel)
            if not getattr(tel, "connected", False):
                self._blind("the mount's link is down, so this net cannot tell "
                            "whether the Sun is closing on it", cfg)
                await self._blind_projection(tel, cfg, cone)
                return
            # The reopen worked. Carry on and read it in THIS tick: the net was
            # not blind for a single one, so it counts nothing and says nothing.

        # When this read BEGAN, not when it ended: a park confirmed while it is
        # in flight is not undone by a position the mount gave from before it
        # (``_remember``, #696).
        read_started = self._clock()
        pos = await self._position(tel)
        if pos is None:
            # A mount that cannot say where it is pointing cannot be judged, and
            # guessing would either park a working rig or bless a cooking one.
            # UNLIKE every other hold in this method, this one runs through
            # _blind(), not _hold(): the Sun keeps closing on the tube for as
            # long as the outage lasts, so the hold has to escalate on ITS OWN
            # clock rather than latch silent on the mount's (issue #137 -- an
            # 8h24m outage produced exactly one info line under the old _hold).
            #
            # NO reopen here: the link claims to be up, and every real
            # ``Telescope.connect()`` returns at once when it already reports
            # ``connected``, so the call would buy nothing.
            self._blind("the mount will not report its position, so this net "
                        "cannot tell whether the Sun is closing on it", cfg)
            await self._blind_projection(tel, cfg, cone)
            return
        self._clear_blind()
        ra_hours, dec_deg = pos
        now = self._clock()
        self._remember(ra_hours, dec_deg, now, read_started)
        tracking = await self._tracking(tel)
        sep, lead = closest_approach(ra_hours, dec_deg, tracking=tracking,
                                     now=now, lead_s=self._lead_s)

        if sep >= cone:
            # NOTHING TO DO. Say nothing at all — except the one line that
            # closes a story already told, so an operator watching the log sees
            # the alarm end rather than just stop.
            if self._acted or self._held is not None:
                bus.log("info", f"sun watch: clear again — the Sun stays "
                                f"{sep:.0f}° away for the next "
                                f"{self._lead_s / 60:.0f} min", "safety")
            self._acted = False
            self._held = None
            return

        when = "already" if lead <= 0 else f"in {lead / 60:.0f} min"
        drift = ("" if tracking else
                 ", tracking is OFF so the sky is turning the tube toward it "
                 "at 15°/h")
        approach = (f"the tube is pointing where the Sun will be {when} "
                    f"({sep:.0f}° at closest, exclusion {cone:.0f}°{drift})")

        if self._acted:
            # We commanded a park for this approach and it was accepted, and the
            # tube is STILL projected into the cone. That is the "driver returns
            # SUCCESS on absence of motion" shape (audit #15). Commanding it
            # again will not help; saying it once, loudly, might.
            self._hold(f"a park was already commanded and accepted, yet "
                       f"{approach} — THE MOUNT HAS NOT MOVED", sep,
                       level="error")
            return

        def hold(held: str) -> None:
            # The ONE wording of "somebody else has the mount": the check below
            # and the re-check inside ``_park`` (#695) both write it.
            self._hold(f"{held}; {approach}", sep, level="warning")

        held = self._hands_off_reason(cfg)
        if held is not None:
            hold(held)
            return

        # THE AWAIT BELOW IS NOT FREE (#695). ``_is_parked`` is bounded by
        # MOUNT_QUERY_TIMEOUT_S (30 s), and a run, a goto, a dome move or a
        # polar lane can start inside it, so the hands-off check above is stale
        # by the time the answer comes back. (This comment used to say that
        # nothing awaits between that check and the fence, which was false: the
        # same window ``dawn_park`` re-checks for after ITS 30 s park query.)
        # ``_park`` therefore asks again immediately before it bumps the motion
        # fence, the bump being what abandons a motion already in flight.
        if await self._is_parked(tel) is True:
            # A parked mount inside the cone means the park position itself is
            # wrong, not that the tube needs moving — parking it again is a
            # no-op that would hide the real problem. Normal park points at the
            # celestial pole, which is >= 66.5° from the Sun at every site and
            # every date (the Sun's declination never leaves +-23.44°), so this
            # branch should never fire on a correctly-configured mount.
            self._hold(f"the mount reports itself PARKED and yet {approach} — "
                       f"the park position is not safe", sep, level="error")
            return

        await self._park(tel, approach, cfg=cfg, hold=hold)

    # --------------------------------------------------------------- the park

    async def _park(self, tel, approach: str, *, cfg, hold,
                    loud: bool = True) -> bool | None:
        """THE park block, shared by the live path and the blind fallback so
        there is exactly one implementation of "get the tube out of the way".
        True when the mount accepted the park, False when it did not, None when
        the net stood aside because another job has the mount (#695).

        THE LAST LOOK, before anything is said or fenced (#695). Both callers
        decided on a hands-off check and then awaited ``_is_parked`` for up to
        MOUNT_QUERY_TIMEOUT_S, so a run, a goto, a dome move or a polar lane
        that began inside that await has not been looked for. Nothing awaits
        between this check and the fence bump below, and the bump is what
        abandons a motion in flight, so the check is made here, where it is the
        last thing before the bump. It comes BEFORE the "Parking now" line,
        which is an error and pages: a held park must not announce itself as a
        park. ``hold(reason)`` is the caller's own warning hold line for a
        hands-off reason, so the held park reads as the earlier check's does.

        ``loud`` False silences the "Parking now" and the FAILURE lines (never
        the success line): the blind fallback retries every tick against a link
        that may be dead for hours and passes it on a cadence, so a failing
        park is a heartbeat there instead of the 417-line flood that flushed
        the ring on 2026-08-09."""
        held = self._hands_off_reason(cfg)
        if held is not None:
            hold(held)
            return None
        # THE GATE AGAIN, AT THE PARK (#888, owner ruling 4B). ``tick`` asked
        # it at the top, then awaited the reopen, the position, tracking and
        # parked reads (each up to MOUNT_QUERY_TIMEOUT_S), and a latch set in
        # those awaits (an AM5 reopen that read the home pole) must not get
        # the park. Asked before the "Parking now" line, so a held park does
        # not page as a park, and again under the motion lock below.
        if self._latched_at_park(tel, cfg):
            return None
        if loud:
            bus.log("error", f"SUN WATCH: {approach}. Parking now.", "safety")
        try:
            # The same discipline as dawn park and every other park path: bump
            # the motion fence so anything that slipped in behind the checks
            # above is abandoned rather than resumed under us, then take the
            # hub's motion lock so this and any other device-touching motion
            # path are serialized on the wire.
            bump = getattr(self.hub, "bump_motion_epoch", None)
            if callable(bump):
                bump()
            lock = getattr(self.hub, "_motion_lock", None)
            if lock is not None:
                async with lock:
                    # The park can wait here behind another motion while the
                    # latch is set: asked under the lock, as every seam asks.
                    if self._latched_at_park(tel, cfg):
                        return None
                    await asyncio.wait_for(tel.park(), PARK_TIMEOUT_S)
            else:
                await asyncio.wait_for(tel.park(), PARK_TIMEOUT_S)
        except asyncio.TimeoutError:
            # No latch: a park that did not happen must be retried next tick.
            if loud:
                bus.log("error", f"SUN WATCH PARK FAILED: the mount did not "
                                 f"park within {PARK_TIMEOUT_S:.0f}s and the "
                                 f"Sun is still coming. Retrying every "
                                 f"{self._interval_s:.0f}s", "safety")
            return False
        except Exception as e:      # noqa: BLE001 — a refusal to park is news, not a crash
            if loud:
                bus.log("error", f"SUN WATCH PARK FAILED: {e} — the Sun is "
                                 f"still coming. Retrying every "
                                 f"{self._interval_s:.0f}s", "safety")
            return False
        self._acted = True
        self._held = None
        # Logged AFTER the await, so the line means "parked", not "asked to" —
        # the rule /api/mount/park and dawn park both follow.
        bus.log("error", "SUN WATCH: mount parked, pointing at the celestial "
                         "pole. Check the optics and the dust cap before the "
                         "next session", "safety")
        return True

    async def _reopen(self, tel) -> None:
        """One bounded, QUIET attempt to bring a dropped link back (#137).

        Quiet because the driver rate-limits its own reopens and logs a failed
        one itself (``zwo_am5._relink``); a second line here per minute for a
        mount that is simply switched off would be noise, and a success needs no
        line of its own because the tick carries on and reads the position."""
        try:
            await asyncio.wait_for(tel.connect(), RECONNECT_TIMEOUT_S)
        except Exception:       # noqa: BLE001 — includes the timeout
            pass

    # -------------------------------------------------------- the blind fallback

    def _remember(self, ra_hours: float, dec_deg: float, now: float,
                  started: float) -> None:
        """Record a successful read for the blind fallback to project from.

        Stored only when no mount-motion lane is busy: a position read while a
        goto, a dome move or a polar run has the tube in transit is not where
        the tube will be, and projecting from it would park (or fail to park)
        on a number that was already wrong. ``capture`` and ``looping`` are NOT
        excluded -- a frame in flight is exactly when a tube is holding still,
        and excluding them would disable the fallback for most of every night.

        A LIVE READ ENDS A NOTED PARK (#696), but only a read that began at or
        after it (``started``): a read in flight when the park was confirmed
        reports the pre-park pointing, and it is slow in exactly the
        circumstance this exists for (a dying link, up to
        MOUNT_QUERY_TIMEOUT_S), so letting it end the note would hand the
        projection the stale position the note was there to refuse."""
        self._last_read_at = now
        noted = self._parked_noted_at
        if noted is not None and noted <= started:
            self._parked_noted_at = None
        if self._busy_lanes() & HANDS_OFF_LANES:
            self._last_good = None
        else:
            self._last_good = (ra_hours, dec_deg, now)

    async def _blind_projection(self, tel, cfg, cone: float) -> None:
        """While blind, project the LAST position read and park if the Sun is
        closing on it (#137).

        BOTH ways a tube moves are projected from that position, because the
        net cannot know which it was doing when the link went: STOPPED (RA
        advances at the sidereal rate for the whole time since the read, then
        over the lead window as usual) and TRACKING (it holds). The smaller
        separation wins, which is the conservative one. A last position older
        than a sidereal day is still valid: ``closest_approach`` wraps RA, and
        age only ever advances the stopped case.

        UNDER THE LIVE PATH'S RULES, not looser ones: ``_acted`` holds it,
        a run or goto/dome/polar lane makes it a warning hold, and the park is
        the same ``_park``. What differs is only what it cannot know: it cannot
        verify a park moved the tube, so after an accepted blind park it stops
        (the position it holds is stale after it) instead of calling the tube
        unmoved, and recovery (``_clear_blind``) hands the next live read a
        fresh decision.

        A PARK NOTED BY ANOTHER PATH (#696) is "no motion possible": the tube
        is at the park point, which no projection from the pre-park pointing
        describes, so nothing is projected and nothing is parked until a live
        read ends the note. Silent, like a clear projection: the blind
        escalation is already speaking, and says so (``_fallback_note``).

        Logs separations in degrees and ages in minutes. NEVER the position."""
        if self._parked_noted_at is not None:
            self._held = None
            self._blind_park_tries = 0
            return
        good = self._last_good
        if good is None:
            # Nothing to project from, and the blind line already says why.
            return
        ra_hours, dec_deg, read_at = good
        now = self._clock()
        age_s = max(0.0, now - read_at)
        ra_stopped, _ = project_pointing(ra_hours, dec_deg, tracking=False,
                                         dt_s=age_s)
        sep_stopped, lead_stopped = closest_approach(
            ra_stopped, dec_deg, tracking=False, now=now, lead_s=self._lead_s)
        sep_tracking, lead_tracking = closest_approach(
            ra_hours, dec_deg, tracking=True, now=now, lead_s=self._lead_s)
        if sep_stopped <= sep_tracking:
            sep, lead, how = sep_stopped, lead_stopped, "stopped"
        else:
            sep, lead, how = sep_tracking, lead_tracking, "tracking"

        if sep >= cone:
            # Clear under both projections: say nothing (the blind escalation
            # is already speaking) and forget any hold, so a later approach is
            # announced rather than swallowed by a stale latch.
            self._held = None
            self._blind_park_tries = 0
            return

        when = "already" if lead <= 0 else f"in {lead / 60:.0f} min"
        drift = (", the sky turning a stopped tube toward it at 15°/h"
                 if how == "stopped" else "")
        approach = (f"the mount cannot be read, and its last position, read "
                    f"{age_s / 60:.0f} min ago, projected as {how}, has the "
                    f"tube pointing where the Sun will be {when} "
                    f"({sep:.0f}° at closest, exclusion {cone:.0f}°{drift})")

        if self._acted:
            # A park was accepted while blind and this net cannot see whether
            # the tube moved: the position it holds is stale after it. Say so
            # once; do not command it again, and do not claim it has not moved.
            self._hold("a park was commanded and accepted while this net was "
                       "blind, so it cannot check that the tube moved; the "
                       "last-known position is stale after it", None)
            return

        def hold(held: str) -> None:
            # The reason carries no ages or separations on purpose: _hold
            # dedupes on the text, and a number that changes every minute would
            # log this warning once a minute for the whole run. Written by the
            # check below and by the re-check inside ``_park`` (#695).
            self._hold(f"{held}; the last-known pointing is projected to reach "
                       f"the {cone:.0f}° exclusion cone", None, level="warning")

        held = self._hands_off_reason(cfg)
        if held is not None:
            hold(held)
            return

        if await self._is_parked(tel) is True:
            # The mount can answer that it is parked, so the pointing this net
            # last read is stale: parking a parked mount is a no-op, and the
            # live path's "park position is not safe" verdict would be an
            # accusation made on a position the mount has since left.
            self._hold("the mount reports itself parked, so the last-known "
                       "position no longer says where the tube is (it "
                       "projected into the exclusion cone)", None)
            return

        tries = self._blind_park_tries
        parked = await self._park(tel, approach, cfg=cfg, hold=hold,
                                  loud=tries % BLIND_LOG_EVERY == 0)
        if parked is None:
            # Stood aside for another job: that is not an attempt, and counting
            # it would make the first real park's "Parking now" silent.
            return
        self._blind_park_tries = 0 if parked else tries + 1

    # -------------------------------------------------------------- internals

    def _hands_off_reason(self, cfg) -> str | None:
        """Why this rig is somebody else's right now, or None if it is nobody's.

        Deliberately narrower than the hazard: a run that is imaging near the
        Sun is a run whose own safety escalation and altitude aborts are live,
        and two things parking the same mount from two tasks is worse than one
        of them waiting. The difference from ``dawn_park`` is the VOLUME — a
        hold here is logged at warning, because unlike a dawn that the engine
        genuinely owns, nothing else in the codebase is watching this."""
        eng = self.engine
        if eng is not None and getattr(eng, "running", False):
            return ("a sequence run is in progress and owns its own aborts — "
                    "racing it would be worse than waiting")
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

    async def _position(self, tel) -> tuple[float, float] | None:
        """(RA hours, Dec deg) of the tube, or None if the mount will not say.

        None is NOT treated as safe and NOT treated as dangerous: it is treated
        as unknown, which is the only honest answer and the one the requirement
        asks for. A mount that has been unreadable for a while is reported by
        the caller's own escalation (see ``_blind``) -- NOT, as an earlier
        version of this docstring claimed, by "the telemetry-stale path":
        that path is the UI's ``ConnectionBanner``, which is UI-only (never
        logged, never alerted) and fires on a WebSocket stall, which does not
        happen when the status stream keeps arriving without a mount block.
        Issue #137 measured exactly that gap: an 8h24m outage the banner never
        saw, because the stream never stalled."""
        try:
            ra, dec = await asyncio.wait_for(tel.get_position(),
                                             MOUNT_QUERY_TIMEOUT_S)
            ra, dec = float(ra), float(dec)
        except Exception:       # noqa: BLE001 — includes the timeout
            return None
        # A NaN from a half-initialised driver would make every separation NaN,
        # and NaN >= cone is False — i.e. it would read as "inside the cone" and
        # park the rig on a driver bug. Refuse it as unknown instead.
        if ra != ra or dec != dec:
            return None
        return ra, dec

    async def _tracking(self, tel) -> bool:
        """Is the mount tracking? A query failure answers NO, deliberately.

        The two answers are not symmetric. Assuming "tracking" when the mount is
        stopped projects a pointing that does not move and misses a 15°/h
        approach entirely — the exact hazard this module exists for. Assuming
        "stopped" when the mount is tracking projects a drift that will not
        happen, and at worst costs one unnecessary park of a rig whose owner is
        not there (a run or an active lane holds this off anyway)."""
        try:
            return bool(await asyncio.wait_for(tel.get_tracking(),
                                               MOUNT_QUERY_TIMEOUT_S))
        except Exception:       # noqa: BLE001 — includes the timeout
            return False

    async def _is_parked(self, tel) -> bool | None:
        """Does the mount say it is parked? None when it will not say.

        Only ever consulted on the unsafe branch, and only to decide whether to
        SHOUT instead of commanding a park — so an unreadable answer must fall
        through to commanding the park, which is the action that helps."""
        try:
            return bool(await asyncio.wait_for(tel.is_parked(),
                                               MOUNT_QUERY_TIMEOUT_S))
        except Exception:       # noqa: BLE001 — includes the timeout
            return None

    def _hold(self, reason: str, sep: float | None,
              level: str = "info") -> None:
        """Say why nothing happened — once per approach, per reason.

        Silence here would be indistinguishable from a net that fired and
        worked, which is the failure mode this whole module is a fix for."""
        if self._held == reason:
            return
        self._held = reason
        where = ": " if sep is None else f" (Sun {sep:.0f}° away): "
        bus.log(level, f"sun watch held off{where}{reason}", "safety")

    def _blind(self, reason: str, cfg) -> None:
        """One more tick with an unreadable position. Escalates by REPETITION
        COUNT, exactly ``dawn_park._fail``'s shape: quiet on the first tick,
        one warning at ``BLIND_WARN_AFTER``, then a repeating error every
        ``BLIND_LOG_EVERY`` ticks from ``BLIND_ERROR_AFTER`` on, and on its own
        clock, the Sun's, ONE error the first tick the Sun is above the
        dawn-park threshold (``_blind_in_daylight``).

        Deliberately NOT routed through ``_hold``: ``_hold`` latches on the
        reason string and stays silent for as long as that reason holds,
        which is correct for "nobody needs telling twice that solar avoidance
        is off" but wrong here, because the hazard (the Sun) keeps closing on
        the tube for the whole outage (#137).

        ONE CLOCK PER OUTAGE. The streak is keyed on "is there an outage", never
        on ``reason``: the reason text only says which kind of dark this tick
        was ("the link is down" or "connected but will not answer"), and a link
        that flaps between the two is still one outage. Keying on the text
        restarted the count on every flip, so a flapping link never reached its
        warning. ``_blind_reason`` holds the LATEST text, for the log line."""
        if self._blind_reason is None:
            self._blind_count = 0
            self._blind_since = self._clock()
        self._blind_reason = reason
        self._blind_count += 1
        n = self._blind_count
        if n == 1:
            level = "info"
        elif n == BLIND_WARN_AFTER:
            level = "warning"
        elif n >= BLIND_ERROR_AFTER and (n - BLIND_ERROR_AFTER) % BLIND_LOG_EVERY == 0:
            level = "error"
        else:
            level = None
        mins = (self._clock() - self._blind_since) / 60.0
        if level is not None:
            tail = "" if n == 1 else f" (blind for {mins:.0f} min, tick {n})"
            note = self._fallback_note()
            bus.log(level, f"sun watch held off: {reason}{note}{tail}", "safety")
        self._blind_in_daylight(reason, cfg, mins, n)

    def _blind_in_daylight(self, reason: str, cfg, mins: float, n: int) -> None:
        """While blind, ONE error the first tick the Sun is above the dawn-park
        threshold (#137, the part of the issue's item 1 that was left).

        THE SUN KEEPS ITS OWN CLOCK. The count-driven lines above say "blind"
        at tick 10 and tick 30 whatever the sky is doing, which is right for a
        night and not enough for a morning: once the Sun is past the threshold
        dawn park acts on, a mount that is not parked may be tracking toward it
        and nothing can see it, and "10 minutes blind" does not say so. This
        line does, and it is an ERROR because the alert sinks route only
        warning and error. It is deliberately NOT flagged ``site_derived`` for
        the same reason: such a line never reaches a sink (``alerting``), and
        the page is the point (dawn park's own lines are unflagged too).

        The threshold is ``dawn_park.park_threshold_deg``, read where dawn park
        reads it, and the Sun's altitude is computed for ``hub.site`` as dawn
        park computes it. A rig whose site was never set has no altitude to
        compute (the placeholder is not a location), so this says nothing for
        it, like dawn park.

        Debounced like the blind lines: once per streak per Sun-up stretch.
        The latch is released when the Sun is back below the threshold (the
        next dawn of a long outage speaks again) and when the streak ends
        (``_drop_blind``). A PARK NOTED by another path suppresses it: the tube
        is at the park point, so nothing is tracking toward the Sun, and a page
        for that would teach the owner to ignore this one.

        The line carries the configured threshold and NO position: not the
        mount's pointing (#140) and not the Sun's altitude, which is a site
        computation."""
        verdict = self._sun_against_dawn_threshold(cfg)
        if verdict is None:
            return
        above, threshold = verdict
        if not above:
            self._blind_daylight_said = False
            return
        if self._blind_daylight_said or self._parked_noted_at is not None:
            return
        self._blind_daylight_said = True
        bus.log("error", f"sun watch BLIND IN DAYLIGHT: {reason}; the Sun is "
                         f"above the dawn-park threshold ({threshold:+.0f}°), so "
                         f"a mount that is not parked may be tracking toward it "
                         f"with nothing watching{self._fallback_note()} "
                         f"(blind for {mins:.0f} min, tick {n})", "safety")

    def _sun_against_dawn_threshold(self, cfg) -> tuple[bool, float] | None:
        """``(is the Sun at or above the dawn-park threshold, that threshold)``,
        or None when this net cannot tell: no site has been set, or the Sun
        cannot be computed. Never raises: a bookkeeping failure must not cost
        the blind line it rides on."""
        try:
            site = self.hub.site
            if site.get("is_default", True):
                return None
            alt, _az = sun_altaz(site["latitude"], site["longitude"],
                                 self._clock())
            threshold = park_threshold_deg(cfg)
            return alt >= threshold, threshold
        except Exception:       # noqa: BLE001 - see the docstring
            return None

    def _fallback_note(self) -> str:
        """Why the blind fallback has nothing to project from, or "" when it
        has. Said on every blind line, not just the first: the line an operator
        reads at warning or error is the one that must not leave them believing
        a last-known position is covering for the dark mount."""
        if self._parked_noted_at is not None:
            return ("; a park was confirmed after the last position was read, "
                    "so the tube is taken as parked until the mount can be "
                    "read again")
        if self._last_read_at is None:
            return ("; no position has been read since this server started, "
                    "so there is no last-known pointing to project from")
        if self._last_good is None:
            return ("; the last position read was taken while the mount was "
                    "being moved, so it cannot be projected from")
        return ""

    def _position_unknown_tick(self, cfg) -> None:
        """One tick while the rig's position latch is set (#888, OWNER RULING
        4B, 2026-10-09).

        NO PARK, NO TRACKING CHANGE. A park is a goto to the model's home,
        aimed from a position the system does not know, so this net must not
        send one, and it does not touch tracking for this reason either.
        Its approach projection reads the same untrusted position, so no
        approach is judged at all: the latched state is treated like the
        blind state for the alert, without the blind state's park. The last
        position is forgotten, so a blind fallback after Trust position never
        projects from a reading taken before or during the doubt; ``_acted``
        goes too, so the first live tick after it decides afresh.

        THE ALERT, on the Sun's clock. While the Sun is at or above the
        dawn-park threshold (``dawn_park.park_threshold_deg``, read where the
        blind path reads it, `_sun_against_dawn_threshold`), ONE error line in
        fixed words (``POSITION_UNKNOWN_DAYLIGHT``) says this net cannot
        protect the tube and gives the safe order, and it is said again every
        ``BLIND_LOG_EVERY`` ticks while that lasts, the blind escalation's
        cadence. Error level, source "safety", never flagged site_derived:
        the AlertDispatcher routes warning and error logs to every configured
        sink, the channel the blind-in-daylight line pages through. The Sun
        going back below the threshold resets the count, so the next dawn
        says it at once. Below the threshold one info hold says why the net
        stands down.

        WITH NO SITE THE SUN CANNOT BE JUDGED (#895), and a net that cannot
        tell day from night must not resolve the doubt to night: a mount whose
        position is unknown in full daylight on a rig with no site (a first run,
        a reset) got one info line, which the AlertDispatcher does not route,
        and never a page. It takes the daylight answer instead, on the same
        cadence, one level down because it does not know the Sun is up:
        ``POSITION_UNKNOWN_SUN_UNKNOWN`` at warning level, which the dispatcher
        does route. There is no figure in it and none to give. The cadence
        count is shared with the daylight error, so it restarts whenever the
        net goes from unable to able to judge the Sun, or back (a site set or
        lost mid-latch): the daylight error is said on the first tick the Sun
        is known to be up, not when the warning's cadence next comes round.

        Published on ``/api/safety/state`` and the status frame (``state()``),
        times and booleans only. Synchronous, touches no device, never raises."""
        if self._unknown_since is None:
            self._unknown_since = self._clock()
            self._unknown_daylight_ticks = 0
            self._unknown_judged = None
        self._drop_blind()
        self._last_good = None
        self._acted = False
        verdict = self._sun_against_dawn_threshold(cfg)
        # The no-site warning and the daylight error share ONE count, so a
        # change of what the net can judge (a site set or lost mid-latch)
        # starts the new line at once instead of waiting out the other's
        # cadence: the daylight error is the page, and it must not be late
        # because the warning had been counting.
        judged = verdict is not None
        if judged != self._unknown_judged:
            self._unknown_judged = judged
            self._unknown_daylight_ticks = 0
        if verdict is not None and not verdict[0]:
            self._unknown_daylight_ticks = 0
            self._hold(POSITION_UNKNOWN_NIGHT_HOLD, None)
            return
        n = self._unknown_daylight_ticks
        self._unknown_daylight_ticks = n + 1
        if n % BLIND_LOG_EVERY == 0:
            if verdict is None:
                bus.log("warning", POSITION_UNKNOWN_SUN_UNKNOWN, "safety")
            else:
                bus.log("error", POSITION_UNKNOWN_DAYLIGHT, "safety")

    def _latched_at_park(self, tel, cfg) -> bool:
        """The one gate, asked again at the park itself (#888, owner ruling
        4B): True, with the latched tick's hold and alert
        (`_position_unknown_tick`), when the rig's position latch was set
        after ``tick`` asked it. The park is then not sent and ``_park``
        answers None, the "stood aside" answer, so the blind fallback does
        not count it as a failed attempt. Synchronous, touches no device."""
        if position_known_for_motion(self.hub, tel):
            return False
        self._position_unknown_tick(cfg)
        return True

    def _drop_unknown(self) -> None:
        """The latch is clear (or the net is not watching): forget the
        position-unknown state, with no line (#888)."""
        self._unknown_since = None
        self._unknown_daylight_ticks = 0
        self._unknown_judged = None

    def _clear_blind(self) -> None:
        """Forget a blindness streak once the position is readable again, so
        an unrelated outage later in the night starts its own count instead of
        resuming mid-escalation.

        Coming out of a streak also forgets ``_acted``: a park commanded while
        blind was accepted but never SEEN to work, so the next live read has to
        decide afresh rather than read "a park was already commanded" as proof
        the tube moved. Only on the way out of a streak, never on an ordinary
        tick: ``_acted`` is also the live path's "the mount accepted a park and
        did not move" memory (audit #15), and wiping it every tick would
        disarm that."""
        if self._blind_reason is not None:
            if self._blind_count > 1:
                mins = (self._clock() - self._blind_since) / 60.0
                bus.log("info", f"sun watch: the mount is reporting its "
                                f"position again after {mins:.0f} min blind",
                        "safety")
            self._acted = False
        self._drop_blind()

    def _drop_blind(self) -> None:
        """End a blindness streak without claiming recovery (no log line)."""
        self._blind_reason = None
        self._blind_count = 0
        self._blind_park_tries = 0
        self._blind_daylight_said = False
