"""ZWO AM5/AM5N native serial mount driver (sub-project B).

Speaks Meade LX200 ASCII over the mount's USB CDC serial port — no ZWO software,
no ASCOM. Wire truth: docs/hardware/zwo-am5-lx200-protocol.md. The mount powers
up PARKED and refuses all motion with ``e14#`` until the ZWO-specific ``:Spu#``
unpark; connect() deliberately does NOT auto-unpark (honest parked UX).

Registered through the driver framework's entry-point path
(``[project.entry-points."astrodeck.backends"] zwo_am5 = ...:register_all``) —
the first real citizen of sub-project A's discovery mechanism.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone

from ...events import bus
from .. import lx200
from ..base import (DeviceError, GotoRefused, PierSide, Telescope,
                    TRACKING_RATES)
from ..serial_link import LinkError, SerialLink

#: Seam for tests: the link factory used by ZwoAm5Session.
_make_link = SerialLink

#: Logger for the pulse watchdog THREAD. Not ``bus.log``: EventBus.publish
#: hands each event to ``asyncio.Queue.put_nowait``, which sets futures and
#: calls ``loop.call_soon`` — loop-affine, not thread-safe (and its night-log
#: append does file I/O). Anything the watchdog needs to say to the operator is
#: recorded on the object and logged to the bus by the coroutine afterwards,
#: on the loop thread.
_log = logging.getLogger(__name__)

#: Wall-clock cap on a slew settle (spec: no motion path may hang).
SLEW_TIMEOUT_S = 120.0
#: Settle criterion: coord delta below this across two consecutive polls.
SETTLE_DEG = 0.05
#: Poll cadence during a slew.
SETTLE_POLL_S = 0.5
#: Floor between attempts to reopen a dropped link. An abandoned exchange can
#: leave a worker thread parked inside a blocking read on the OS handle, and
#: Windows refuses a second open of a COM port while that handle lives — so the
#: first attempts are EXPECTED to fail, and without a floor the recovery becomes
#: a hot loop hammering the port on every status poll.
RELINK_MIN_INTERVAL_S = 5.0
#: Poll cadence while waiting for a park to complete.
PARK_POLL_S = 1.0
#: Per-attempt cap on the park poll. Two attempts, so the worst case is twice
#: this. Was a bare 60.0 inline; named because the retry has to quote it.
PARK_WAIT_S = 60.0
#: How long ``_park_now`` waits for a halt window to close before it starts.
#: Bounded, and NOT a grace period: the window ends on EVIDENCE (see
#: ``_note_halt``, which refuses to invent a settling time because nobody has
#: measured how long an AM5 takes to stop from an R8 slew). This is only the
#: point at which waiting for that evidence stops being worth it — after it,
#: the park is attempted anyway, because an unparked mount is worse than a
#: slow one. Generous, because the alternative to waiting is the failure this
#: exists to prevent: the lost emergency park of 2026-08-06.
HALT_DRAIN_TIMEOUT_S = 30.0

#: |rate deg/s| upper bound -> LX200 rate index command.
#: CALIBRATED ON HARDWARE 2026-07-20 (dec-axis nudges): the AM5 R-indices are
#: sidereal-multiple presets, roughly R1=0.3x, R3=1.9x, R5=7.8x, R7=60x,
#: R8~344x (1.44 deg/s; R8/R9 measurements were acceleration-ramp-limited).
#: Bounds sit between adjacent measured rates so a request maps to the nearest
#: preset at or above it.
_RATE_TABLE = ((0.004, "R1"), (0.02, "R3"), (0.1, "R5"), (0.7, "R7"),
               (float("inf"), "R8"))
#: (axis, positive?) -> move command; stop is Q + same letter.
_MOVE_CMD = {("ra", True): "Me", ("ra", False): "Mw",
             ("dec", True): "Mn", ("dec", False): "Ms"}

#: rate name (TRACKING_RATES) -> classic LX200 drive-rate select command.
_TRACKING_RATE_CMD = {"sidereal": "TQ", "lunar": "TL", "solar": "TS"}

#: What an ``eN`` reply to ``:MS#`` means, AS FAR AS ANYBODY HAS EVIDENCE.
#:
#: ZWO publishes no e-code table, and this driver is written from capture, so
#: this holds only codes that have actually been seen on this wire. Anything
#: else gets the generic sentence below - which names the usual causes without
#: claiming to know which one it was. Guessing here would be worse than the
#: bare code it replaces: an operator who reads "parked" and unparks a mount
#: that was never parked has been sent the wrong way by their own logs.
#:
#: e6 was observed on the rig on 2026-09-06 at 20:47 PDT: the post-restart
#: re-centre asked for NGC 604 at ~9 degrees altitude and got e6, while the
#: solve-and-sync's re-slew to the pole region a minute earlier was accepted -
#: and the identical goto succeeded later the same night once the target had
#: risen. That is the mount's own horizon/slew limit, not our safety floor.
_GOTO_REFUSALS: dict[str, str] = {
    "e14": "the mount refused in its current state - parked, a slew already "
           "running, or no target set",
    "e6": "the target is outside the mount's slew limits (below its horizon "
          "limit)",
}


def _goto_refusal_words(code: str) -> str:
    """Plain words for an ``:MS#`` refusal code; honest when it is unknown."""
    return _GOTO_REFUSALS.get(code) or (
        f"the mount refused the goto (code {code}); its altitude, meridian or "
        f"park limits are the usual reasons")

#: Pulse-guide emulation (fw 1.8.8, all verified at scope 2026-07-20):
#: - The LX200 :Mg*# pulse commands PARSE but are INERT over serial.
#: - :M<dir># during tracking REPLACES the tracking drive (does not
#:   superimpose): Me at R1(~0.5x) reads +1.5x sid on GR; Mw reads +0.5x —
#:   both eastward! So each direction gets its own strategy:
#:   east  = suspend tracking (:Td# ... :Te#): drifts east at EXACTLY 1x sid;
#:   west  = R2 + Mw: measured EXACTLY -1x sid during tracking;
#:   north/south = R1 + Mn/Ms: +/-0.5x sid (dec has no tracking to fight).
#: Sky sign of N/S depends on pier side — guider calibration owns that, as
#: with every ASCOM mount.
#:
#: A PULSE IS A TIMED MOVE, and that is the whole hazard (GN-02, rig
#: 2026-09-06). There is no "move for N ms" command on this wire: the driver
#: starts the axis, waits, and stops it, so the wait IS the move. Two defences,
#: both below: the stop is armed on a thread timer at pulse start so a blocked
#: event loop cannot delay it, and every pulse is capped at _PULSE_MAX_MS.
_PULSE_DEC_RATE_CMD = "R1"
_PULSE_WEST_RATE_CMD = "R2"
#: measured pulse rates, deg/s (10s GR/GD deltas, 2026-07-20): ra = 1.0x
#: sidereal BOTH directions (+150/-150 arcsec per 10s), dec = 0.5x.
_PULSE_RA_RATE_DEG_S = 0.004178
_PULSE_DEC_RATE_DEG_S = 0.002089
_PULSE_MOVE = {"n": "Mn", "s": "Ms", "e": "Me", "w": "Mw"}
_PULSE_STOP = {"n": "Qn", "s": "Qs", "e": "Qe", "w": "Qw"}

#: Hard ceiling on ONE pulse, ms. At the measured 1x-sidereal RA emulation this
#: is 15 arcsec — an error a guider recovers from in a frame or two. The night
#: of 2026-09-06 produced +83, +128 and +35 arcsec jumps from stalls of 5-8 s;
#: with this cap the same stall costs 15 arcsec, whatever else goes wrong. A
#: guider that wants a longer correction must issue several pulses (and can see
#: the ceiling coming: ``ZwoAm5Telescope.max_pulse_ms``).
_PULSE_MAX_MS = 1000
#: How long the coroutine waits for the pulse thread to stop the mount after a
#: cancellation, on top of the pulse itself. Bounded: a thread wedged on a dead
#: port must not hold up the cancel it is answering.
_PULSE_CANCEL_JOIN_S = 2.0
#: Rate limit on the "capped" warning: a mis-tuned guider asks for oversized
#: pulses every correction, and one line per correction would bury the night.
_PULSE_CAP_WARN_S = 60.0
#: Monotonic stamp of the last "capped" warning (module-level: the rate limit is
#: about the operator's log, not about one telescope object).
_cap_warn_last = 0.0


class _Pulse:
    """One whole emulated pulse — start, wait, stop — on ONE worker thread.

    WHY THE WHOLE THING AND NOT JUST THE STOP (GN-02, rig 2026-09-06). There is
    no "move for N ms" command on this wire: the driver starts the axis, waits,
    and stops it, so the wait IS the move and whatever measures it decides how
    far the mount travels. Measuring it on the event loop made the loop part of
    the mount's control path, and the loop is regularly blocked for seconds by a
    synchronous frame readout, a filter-wheel move or thumbnail generation:
    +83, +128 and +35 arcsec of unwanted travel in one night.

    Handing the STOP to a timer armed by the coroutine was not enough, because
    the arming itself waits for the loop: between the worker thread putting
    ``:Mn#`` on the wire and the coroutine resuming to arm anything, the loop
    runs whatever was already queued — and if that is the readout, the move is
    running with no watchdog behind it. So the start, the wait and the stop all
    happen here, on the thread, with nothing between them that a busy loop can
    delay. The coroutine just awaits the thread.

    Nothing in here touches the event loop or ``bus`` (``EventBus.publish``
    feeds ``asyncio.Queue`` and is loop-affine): failures are recorded on the
    object for the coroutine to report, and logged to stdlib ``logging`` here.
    ``run`` never raises."""

    def __init__(self, link, name: str, start: list[tuple[str, str]],
                 stop: tuple[str, str], secs: float):
        self.link = link
        self.name = name
        self.start = start          # [(cmd, reply)] — the move, in order
        self.stop = stop            # (cmd, reply)
        self.secs = secs
        #: Set by the coroutine on cancellation: stop the mount NOW, don't wait
        #: out the duration. (Which is why the wait is an Event, not a sleep.)
        self.abort = threading.Event()
        #: Set when the thread is finished, however it finished.
        self.done = threading.Event()
        #: True once every start command is out and any ack was ACK_OK — i.e.
        #: the mount is moving and the stop is owed.
        self.started = False
        #: A start command's non-ACK_OK ack ("0", "e14"): the mount said no.
        self.start_reply: str | None = None
        #: The start command that refused or raised (for the error message).
        self.start_cmd: str | None = None
        self.stop_sent = False
        self.stop_reply: str | None = None
        #: Any ack-class command got an ANSWER. Mirrors ``_cmd_ack``: a mount
        #: that answered has disproven the halt window (cleared on the loop).
        self.answered = False
        self.error: BaseException | None = None

    def run(self) -> None:
        """Worker thread. Total: never raises, always sets ``done``."""
        try:
            self._run()
        except BaseException as exc:    # noqa: BLE001 - a thread must not raise
            self.error = exc
            _log.warning("%s: pulse thread failed (%s)", self.name, exc)
        finally:
            self.done.set()

    def _run(self) -> None:
        for cmd, reply in self.start:
            try:
                out = self.link.request_sync(cmd, reply=reply)
            except BaseException as exc:  # noqa: BLE001 - reported, not raised
                self.error = exc
                self.start_cmd = cmd
                return                  # nothing started, so nothing to stop
            if reply == "ack":
                self.answered = True
                if out != lx200.ACK_OK:
                    self.start_cmd, self.start_reply = cmd, out
                    return
        self.started = True
        try:
            # NOT time.sleep: a cancelled pulse must stop the mount at once.
            self.abort.wait(self.secs)
        finally:
            self._send_stop()

    def _send_stop(self) -> None:
        cmd, reply = self.stop
        try:
            out = self.link.request_sync(cmd, reply=reply)
        except BaseException as exc:    # noqa: BLE001 - the loop re-sends
            self.error = exc
            _log.warning("%s: the pulse thread could not send :%s# (%s)",
                         self.name, cmd, exc)
            return
        self.stop_sent = True
        if reply == "ack":
            self.answered = True
            self.stop_reply = out


def _utcnow() -> datetime:
    """Injectable clock (tests monkeypatch this)."""
    return datetime.now(timezone.utc)


def _site_latlon() -> tuple[float, float]:
    """Site (lat, lon_east) from config; injectable for tests."""
    from ...config import config_store
    site = config_store.cfg().site
    return float(site.latitude), float(site.longitude)


class ZwoAm5Telescope(Telescope):
    """The AM5 as an AstroDeck Telescope: LX200 over one SerialLink."""

    backend = "zwo-am5"
    hardware = True
    can_pulse_guide = True    # EMULATED: timed R1 moves (native :Mg*# is inert)
    #: Published so a guider can size its corrections to what this mount will
    #: actually perform, instead of having them silently truncated.
    max_pulse_ms = _PULSE_MAX_MS
    can_set_tracking_rate = True
    can_find_home = True      # :hP# homes (and parks); find_home unparks after
    #: GN-09: this is a harmonic drive with large periodic error. Measured on
    #: 2026-09-06: switched to UNGUIDED at 03:04 after the guider misbehaved,
    #: and every unguided 60 s sub afterward trailed by ~15 px (fixture
    #: pedrift_L60.fits.gz). The flow doctor's own "will this trail" rule only
    #: fired at 120 s and up, so a 60 s unguided cycle on this mount read clean.
    needs_guiding = True
    #: The fastest MANUAL rate this mount has been MEASURED to deliver: R8, the
    #: top row of ``_RATE_TABLE``, whose calibration note (2026-07-20, dec-axis
    #: nudges) records "R8~344x (1.44 deg/s; R8/R9 measurements were
    #: acceleration-ramp-limited)". R9 is NOT published here even though the
    #: mount has one: that same note says the measurement was ramp-limited, so
    #: nobody knows what R9 sustains. A ceiling with no measurement behind it is
    #: a number, not a limit - and this field is what the server's touch-pad
    #: clamp hands the operator.
    max_rate_deg_s = 1.44

    def __init__(self, link, name: str = "ZWO AM5"):
        super().__init__(name)
        self._link = link
        self.firmware = ""
        self._last_pos: tuple[float, float] | None = None
        #: True only between ":MS#" being accepted and the settle poll ending.
        #: Initialized here (it used to exist only as a getattr default) because
        #: it is now read on an ERROR path -- see _link_error, where an
        #: AttributeError would replace an honest refusal with a crash.
        self._slewing = False
        #: "We told a mount that may still be moving to HALT, and it has not yet
        #: proven it came to rest." Set by _note_halt (the :Q# senders), cleared
        #: only by evidence -- never by a clock. See _note_halt for the failure
        #: this exists for and _link_error for what it changes.
        self._halting = False
        #: Position at the previous read taken DURING a halt window; the pair
        #: (this, the next read) is what proves the mount stopped moving.
        #: Separate from _last_pos so the proof only ever uses post-halt reads.
        self._halt_ref: tuple[float, float] | None = None
        #: cache of the last-set drive rate -- the AM5's rate read-back is
        #: unreliable (no verified LX200 query for it), so get_tracking_rate
        #: returns this rather than round-tripping the mount.
        self._tracking_rate = "sidereal"
        #: Monotonic deadline before which no further reopen will be attempted,
        #: and a re-entrancy guard so the reopen's own handshake commands do not
        #: recurse back into the reopen. See _relink.
        self._relink_after = 0.0
        self._relinking = False

    # --------------------------------------------------------------- health

    @property
    def connected(self) -> bool:
        """True only when the handshake completed AND the port is still usable.

        DERIVED, NOT REMEMBERED. This used to be a plain attribute meaning
        "connect() returned once". On 2026-08-09 the link was abandoned at
        02:38:26 and that attribute stayed True for the next five and a half
        hours: ``/api/status`` and ``backend_links`` both reported a healthy
        mount while every single command failed at the transport, so the UI had
        no way to show what was wrong. Worse, ``connect()``'s ``if
        self.connected: return`` guard READ that stale True, which is precisely
        why no reconnect path ever reopened the port — the one flag that should
        have driven recovery was the flag that suppressed it.

        Deriving it means the whole system inherits the fix at once: the status
        surface goes honest, ``connect()`` becomes willing to re-handshake, and
        the dawn-park net can finally tell a dropped link from a rig that was
        never plugged in."""
        # getattr: Device.__init__ assigns `connected = False` through this
        # setter BEFORE __init__ binds _link, so the getter must survive it.
        link = getattr(self, "_link", None)
        if link is not None and not link.is_open:
            return False
        return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        self._connected = bool(value)

    # ------------------------------------------------------------ helpers

    async def _request(self, cmd: str, *, reply: str = "hash",
                       timeout: float = 1.5) -> str | None:
        """One wire exchange, reopening a link that was dropped under us.

        EVERY command goes through here rather than straight to the link,
        because the command that most needs the reopen is the one issued by a
        safety net at dawn: on 2026-08-09 ``park`` failed 139 times against a
        link that a single reopen would have fixed. A read-only recovery (only
        ``_get`` retrying) would have left exactly that case broken.

        The retry is deliberately SINGLE and non-recursive: ``_relink`` raises
        if it cannot reopen, and its own handshake is fenced by ``_relinking``,
        so a mount that is genuinely gone produces one failed reopen and one
        honest error rather than a retry storm.

        ``disconnect()`` deliberately does NOT use this — reopening a link in
        order to close it is not a recovery, it is a loop."""
        try:
            return await self._link.request(cmd, reply=reply, timeout=timeout)
        except LinkError:
            # "We believe we are connected, but the port is shut." Keyed on
            # _connected (the raw flag, not the derived property) rather than on
            # the link's abandoned flag, because a FAILED reopen has already
            # been through close() and cleared that flag — keying on it would
            # give up after exactly one attempt, which is one better than the
            # zero attempts of the bug and still not a recovery.
            if self._relinking or not self._connected or self._link.is_open:
                raise
            await self._relink()        # raises LinkError if it cannot
            return await self._link.request(cmd, reply=reply, timeout=timeout)

    async def _relink(self) -> None:
        """Reopen a dropped port and bring the mount back up. Raises LinkError.

        Rate-limited (see RELINK_MIN_INTERVAL_S) because the failure that drops
        a link can leave a worker thread holding the OS handle, and Windows will
        refuse the reopen until it lets go — those early refusals are normal and
        must not turn every status poll into an open() attempt."""
        now = time.monotonic()
        if now < self._relink_after:
            raise LinkError(
                f"the link to {self.name} is down; the last reopen failed and "
                f"the next attempt is in {self._relink_after - now:.0f}s")
        self._relink_after = now + RELINK_MIN_INTERVAL_S
        self._relinking = True
        try:
            await self._open_and_handshake()
        except Exception as exc:    # noqa: BLE001 - one link error out
            bus.log("warning",
                    f"{self.name}: could not reopen the dropped serial link "
                    f"({exc}) — retrying in {RELINK_MIN_INTERVAL_S:.0f}s", "mount")
            raise LinkError(str(exc)) from exc
        finally:
            self._relinking = False
        # Success clears the floor: it exists to space out FAILED attempts, and
        # leaving it armed would make a second drop five seconds later wait for
        # no reason.
        self._relink_after = 0.0
        bus.log("warning",
                f"{self.name}: serial link reopened after it was dropped — the "
                f"mount is answering again", "mount")

    async def _refused_error(self, what: str) -> DeviceError:
        """Compose an HONEST error for the mount's ``e14#`` refusal.

        ``e14`` means "refused in current state" — parked is only ONE cause
        (below-horizon limit, a motion already running, an un-set target all
        produce it too), so we probe the actual park flag before blaming park.
        The probe runs only on this already-exceptional path and is best-effort:
        a failed ``:Gps#`` must never mask the refusal we came here to report."""
        parked = False
        try:
            parked = await self.is_parked()
        except Exception:  # noqa: BLE001 - probe is advisory only
            pass
        if parked:
            return DeviceError(
                f"{self.name}: {what} refused — mount is parked; unpark first "
                "(AM5 e14)")
        return DeviceError(
            f"{self.name}: {what} refused in current state — check limits / "
            "that a slew isn't already running (AM5 e14)")

    def _note_halt(self) -> None:
        """Record that this driver just sent a whole-mount halt (``:Q#``).

        THE HOLE THIS FILLS (carry-in from the review of 797588b). ``_slewing``
        below is cleared by ``slew()``'s ``finally`` the instant the settle poll
        exits — but on a CANCEL the mount is still physically decelerating when
        that happens, because ``:Q#`` is fire-and-forget and the axes have mass.
        The rig log shows exactly that ordering: "goto cancelled" at 00:50:45,
        and the ``timeout waiting for ack on COM3`` 409 AFTER it. So the window
        where the mount is least able to answer was the one window with no
        explanation attached.

        NO GRACE PERIOD IS INVENTED HERE, deliberately. Nobody has measured how
        long an AM5 takes to stop from an R8 slew, and a made-up number would
        either expire early (leaving the bug) or swallow a genuine COM timeout
        for that long (much worse). The AM5's ``:GU#`` status word is not an
        option either: docs/hardware/zwo-am5-lx200-protocol.md explicitly says
        its slewing/parked bits are UNVERIFIED against live states, so decoding
        one would be inventing knowledge rather than a number.

        So the window ends on EVIDENCE, from the one observable this driver
        already trusts to mean "the mount stopped": its reported position no
        longer changing (``get_position``, same ``SETTLE_DEG`` criterion the
        settle poll uses to call a goto finished). The hub's 2 s status loop
        reads position continuously, so the flag clears on its own within a poll
        or two of the mount actually coming to rest — no extra serial traffic,
        no timer. A successful ack clears it too (``_cmd_ack``): a mount that
        answers is by definition no longer too busy to answer.

        WHOLE-MOUNT HALTS ONLY. ``:Q#`` (slew cancel/timeout, ``stop()``) sets
        this; the per-direction stops do not. That exclusion is load-bearing for
        ``pulse_guide``, which fires ``:Qn#``/``:Qw#``/... every few seconds all
        night — pinning the flag on through a guided session would leave every
        later error carrying a stop that had nothing to do with it."""
        self._halting = True
        self._halt_ref = None      # only reads taken AFTER this halt may prove rest

    def _link_error(self, what: str, exc: LinkError) -> DeviceError:
        """Turn a transport failure into a refusal the OWNER can act on.

        WHAT WENT WRONG (rig, same night as the capture/solve collision). The
        user pressed tracking-on while a goto was still running and got:

            409 - ZWO AM5 (native serial): tracking on failed: timeout waiting
                  for ack on COM3

        Every word of that is true and none of it is useful. It names a serial
        port and an ack byte, so it reads as "your mount is broken / your cable
        is bad" — when what actually happened is that the AM5 was busy driving
        the axes and did not answer within the 1.5 s exchange window. The
        mount's OTHER way of saying no (the ``e14#`` refusal) already gets an
        honest sentence from ``_refused_error``; silence during a slew was the
        one refusal channel still leaking the transport layer.

        Note this is a REAL possibility only because ``/api/mount/tracking``
        (and the other one-shot mount routes) do not take ``hub._motion_lock`` —
        they can legitimately land in the middle of a slew this driver is still
        settling.

        DO NOT SWALLOW GENUINE FAULTS. The rewrite is gated on ``_slewing``,
        which this driver sets only between an accepted ``:MS#`` and the end of
        its settle poll. An unplugged cable, a dead port or a wedged mount with
        NO slew in flight still surfaces the transport text verbatim, because
        that text is the diagnosis in that case. The original ``LinkError`` is
        chained either way, so the log/traceback keeps the wire detail.

        THE THIRD CASE (the cancel window, added on the review of 797588b) sits
        between the two: see ``_note_halt``. It gets its own sentence AND KEEPS
        THE WIRE TEXT, which is the difference that matters. While ``_slewing``
        is true the settle poll is reading GR/GD every 500 ms and succeeding, so
        we have live proof the link is healthy and "busy" is the only remaining
        explanation — the transport text there would be pure noise. After a halt
        we have no such proof: the last thing we know is that we told the mount
        to stop. It is probably decelerating, and it might equally have died at
        that moment, so the honest form is the context PLUS what the wire
        said."""
        if self._slewing:
            return DeviceError(
                f"{self.name}: {what} refused — the mount is slewing and will "
                "not answer until it stops. Wait for the goto to finish, or "
                "press Stop.")
        if self._halting:
            return DeviceError(
                f"{self.name}: {what} refused — the mount was told to stop and "
                "has not yet reported coming to rest, so it is probably still "
                "slowing down. Try again in a moment; if it keeps failing the "
                f"link itself may be down (the link said: {exc})")
        return DeviceError(f"{self.name}: {what} failed: {exc}")

    async def _cmd_ack(self, cmd: str, what: str) -> None:
        """Send an ack-class command; map e14 to the honest refusal error."""
        try:
            reply = await self._request(cmd, reply="ack")
        except LinkError as exc:
            raise self._link_error(what, exc) from exc
        # ANY answer — even the e14 refusal below — ends a halt window on the
        # spot: the flag's whole claim is "the mount may be too busy to answer",
        # and a mount that just answered has disproven it. Cheaper and stronger
        # evidence than waiting for the position to settle, so it is checked
        # first; the position path in get_position covers the case where nothing
        # sends the mount a command at all.
        self._halting = False
        if reply == lx200.REFUSED:
            raise await self._refused_error(what)
        if reply != lx200.ACK_OK:
            raise DeviceError(f"{self.name}: {what} rejected (reply {reply!r})")

    async def _get(self, cmd: str) -> str:
        # NOT routed through _link_error, deliberately. The busiest caller of
        # this method IS the settle poll inside slew() -- it reads GR/GD every
        # 500 ms with _slewing True -- and that loop needs the transport truth
        # to decide whether to halt the mount. Rewriting its own reads into "the
        # mount is slewing" would be circular and would hide a link that died
        # mid-goto. Reads keep reporting what the wire did.
        try:
            return await self._request(cmd, reply="hash")
        except LinkError as exc:
            raise DeviceError(f"{self.name}: {cmd} read failed: {exc}") from exc

    # ---------------------------------------------------------- lifecycle

    async def connect(self) -> None:
        if self.connected:            # idempotent: hub re-connects (double-open would fail)
            return
        await self._open_and_handshake()

    async def _open_and_handshake(self) -> None:
        """Open the port and bring the mount up: identify it, then assert the
        clock and site. Shared by ``connect`` and ``_relink``.

        THE RE-ASSERT IS NOT REDUNDANT ON A REOPEN. A link drops for two very
        different reasons — our side stalled, or the MOUNT restarted — and they
        look identical from here. If the mount restarted it is back at its
        power-up defaults, and a bare port reopen would leave a driver happily
        issuing coordinates against the wrong clock and site: pointing errors
        with no error message anywhere. Four commands buys the certainty."""
        # Only when a handle actually exists — a reopen after an abandon has
        # none, and closing there would clear the abandoned flag for nothing.
        if self._link.is_open:
            await self._link.close()
        await self._link.open()
        try:
            ident = await self._get("GVP")
            if "AM5" not in ident:
                raise DeviceError(
                    f"{self.name}: device on port is not an AM5 (GVP={ident!r})")
            self.firmware = await self._get("GV")
            for cmd in lx200.utc_init_cmds(_utcnow()):
                await self._cmd_ack(cmd, f"clock init {cmd}")
            lat, lon = _site_latlon()
            await self._cmd_ack(lx200.smge(lat, lon), "site init")
            await self._get("Gps")   # prime state (parked flag)
            await self._get("GU")
        except Exception:
            await self._link.close()
            self.connected = False
            raise
        self.connected = True

    async def disconnect(self) -> None:
        try:
            await self._link.request("Q", reply="none")   # never leave motion running
        except Exception:  # noqa: BLE001 - best-effort on teardown
            pass
        await self._link.close()
        self.connected = False

    def describe(self) -> dict:
        d = super().describe()
        d["firmware"] = self.firmware
        return d

    # -------------------------------------------------------------- state

    async def get_position(self) -> tuple[float, float]:
        raw_ra = await self._get("GR")
        raw_dec = await self._get("GD")
        try:
            ra = lx200.parse_ra(raw_ra)
            dec = lx200.parse_dec(raw_dec)
        except ValueError as exc:   # mount garbage -> the driver's error type
            raise DeviceError(
                f"{self.name}: unparseable position reply "
                f"(RA={raw_ra!r} Dec={raw_dec!r})") from exc
        self._last_pos = (ra, dec)
        # END OF A HALT WINDOW, measured rather than timed (see _note_halt).
        # Two reads taken after the halt, one SETTLE_DEG apart or less, are the
        # same evidence slew() accepts as "the mount stopped" — reusing that
        # criterion means no new constant and no new claim about the hardware.
        # ``_halt_ref`` starts at None so the comparison can never reach back to
        # a pre-halt position. One stable pair is enough here (the settle poll
        # demands two in a row because it is deciding whether a GOTO SUCCEEDED;
        # this is only deciding how to word an error, and clearing early merely
        # falls back to the plain wire text).
        if self._halting:
            prev, self._halt_ref = self._halt_ref, (ra, dec)
            if prev is not None and max(abs(ra - prev[0]) * 15.0,
                                        abs(dec - prev[1])) < SETTLE_DEG:
                self._halting = False
        return ra, dec

    async def is_parked(self) -> bool:
        return (await self._get("Gps")).startswith("2")

    async def unpark(self) -> None:
        # Idempotent (at-scope finding 2026-07-20): :Spu# on an ALREADY-unparked
        # mount replies '0', which is not a failure — there is simply no park to
        # cancel. Check state first; only a parked mount gets the command.
        if not await self.is_parked():
            return
        await self._cmd_ack("Spu", "unpark")

    async def find_home(self) -> None:
        """Go to the home position and come back USABLE.

        The AM5 has ONE wire command for this: ``:hP#`` homes and parks
        together, which is why ``park()`` below sends the same thing. So homing
        is park-then-unpark — the mount ends physically at home with tracking
        off and the motors released, ready to slew.

        Leaving it parked would be the trap: the button would look like it
        worked and the next slew would be refused.

        IT COMMANDS THE MOUNT UNCONDITIONALLY, and must: this used to call
        ``park()``, which short-circuits when the mount already reports parked.
        Home on a parked mount therefore only UNPARKED it — no motion, no
        error, and this docstring claiming otherwise. On a harmonic drive with
        no brake that is the exact case that matters: after a power cut the
        tube was found 50° out while the mount still reported parked, and Home
        is the button you reach for then.

        UNPARK FIRST, and this is the whole reason the order is three steps.
        A PARKED AM5 REFUSES ``:hP#``. That is captured wire truth for this
        mount, not an inference — docs/hardware/zwo-am5-lx200-protocol.md
        records the entire motion class, ``:hP#`` included, answering ``e14#``
        while parked, and unparking with the ZWO-specific ``:Spu#`` clearing
        every one of them. So commanding home on a parked mount without
        unparking sends a command the mount throws away, and because ``:hP#``
        is fire-and-forget there is no ack to notice it by. The first version
        of this fix did exactly that and reported success."""
        await self.unpark()        # a parked mount refuses :hP# outright
        await self._park_now()     # never skipped — "parked" is not "at home"
        await self.unpark()        # and leave it usable, not parked

    async def park(self) -> None:
        # Idempotent, mirroring unpark: re-parking a parked mount would cost a
        # 60s poll on the dawn path for no motion. ``find_home`` deliberately
        # bypasses this guard — see its docstring.
        if await self.is_parked():
            return
        await self._park_now()

    async def _park_now(self) -> None:
        # The actual park, with no idempotence guard.
        # VERIFIED ON HARDWARE 2026-07-20: :hP# is in the AM5's
        # fire-and-forget motion class (NO ack; an ack-read times out) and the
        # mount reports parked (:Gps# '2') ~1s later. Send-and-poll, with a
        # wall-clock bound.
        # STOP TRACKING FIRST. Verified on hardware 2026-07-30: with tracking
        # ON, :hP# is accepted and silently does nothing — the mount never
        # moves and never reports parked, so every park times out at 60s.
        # Tracking off first, and the identical park lands in ~15s.
        #
        # This is the single most important ordering in the driver. A night
        # ALWAYS ends with the mount tracking, so "park at dawn" — the thing
        # standing between the sun and the optics — hit exactly this case and
        # failed. It was found by test-firing a dawn failsafe rather than
        # trusting that it would work.
        # DRAIN A HALT WINDOW FIRST. Reproduced on hardware 2026-08-06:
        # goto -> stop -> park left the mount unparked and TRACKING, with
        # "park did not complete within 60s". The identical park from an idle
        # mount succeeded in ~12 s. ``stop`` sends :Q# and the axes have mass,
        # so the tracking-off command below lands in the window ``_note_halt``
        # exists to describe — where the mount is least able to answer, and an
        # ack-class send (:Td#) times out. That timeout used to be swallowed
        # whole (see below), leaving tracking ON for the :hP# that follows,
        # which is the one state this driver KNOWS makes park a silent no-op.
        #
        # stop-then-park is the EMERGENCY shape: safety abort, dawn park, the
        # sun watchdog, any aborted session. So it is the sequence that must
        # work, not the one to leave as an at-scope runbook item.
        await self._drain_halt()

        # STOP TRACKING, AND VERIFY IT. The old code wrapped this in a bare
        # ``except Exception: pass``. The instinct was right — a mount that
        # cannot stop tracking must still get its park attempt, because this
        # path is the last thing standing between the sun and the optics — but
        # swallowing the failure ALSO threw away the knowledge that the park
        # about to be sent was the known-silent one. Retry, then let the
        # failure inform the error at the end rather than vanish.
        tracking_off = await self._tracking_off_verified()
        # COMPLETION SIGNAL: the parked flag, which is the hardware-verified
        # one (:Gps# flips to '2' ~1s after :hP#). It is trustworthy at every
        # call site because no call site reaches here with the mount already
        # parked: ``park`` short-circuits on that, and ``find_home`` unparks
        # first because a parked AM5 refuses :hP# outright.
        #
        # An earlier version tried to handle a call with the mount already
        # parked by watching for the position to stop changing instead. That
        # was solving a problem the wrong ordering had created, and it could
        # not work: a mount that never moved reports a perfectly stable
        # position, so a refused :hP# read as a completed home.
        if await self._send_park_and_wait(PARK_WAIT_S):
            return

        # ONE RETRY, and only because the first failure is diagnostic rather
        # than mysterious: a :hP# that goes unanswered for PARK_WAIT_S with
        # tracking still on IS the documented silent no-op. Re-assert
        # tracking-off now that the halt window is long over, and send it
        # again. A mount that ignores the second one has a real problem worth
        # reporting; a mount that only ever needed the drive stopped is parked.
        still_tracking = not await self._tracking_off_verified()
        if await self._send_park_and_wait(PARK_WAIT_S):
            return
        why = (" — tracking is still on, and this mount accepts :hP# and does "
               "nothing while it is" if still_tracking or not tracking_off
               else "")
        raise DeviceError(
            f"{self.name}: park did not complete within "
            f"{PARK_WAIT_S * 2:.0f}s across two attempts (mount still reports "
            f"unparked){why}")

    async def _send_park_and_wait(self, timeout_s: float) -> bool:
        """One ``:hP#`` and a bounded poll of the parked flag. True when parked."""
        await self._request("hP", reply="none")
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() <= deadline:
            await asyncio.sleep(PARK_POLL_S)
            if await self.is_parked():
                return True
        return False

    async def _drain_halt(self) -> None:
        """Wait out a halt this driver opened, before commanding anything that
        needs the mount to answer.

        Best-effort and bounded. ``_halting`` is cleared by EVIDENCE inside
        ``get_position`` — two post-halt reads within ``SETTLE_DEG`` — so this
        only has to keep asking. It invents no grace period, for the same
        reason ``_note_halt`` refuses to: nobody has measured how long an AM5
        takes to stop from an R8 slew. If the window never closes, fall through
        and try the park anyway: an unparked mount is worse than a slow one."""
        if not self._halting:
            return
        deadline = asyncio.get_running_loop().time() + HALT_DRAIN_TIMEOUT_S
        while self._halting and asyncio.get_running_loop().time() <= deadline:
            try:
                await self.get_position()
            except Exception:  # noqa: BLE001 — a mount mid-halt may not answer
                pass
            if self._halting:
                await asyncio.sleep(PARK_POLL_S)

    async def _tracking_off_verified(self) -> bool:
        """Stop the drive and CONFIRM it stopped. True when tracking reads off.

        Never raises: every caller is on the path that protects the optics, and
        a mount that will not talk still gets its park attempt."""
        for attempt in range(2):
            try:
                if not await self.get_tracking():
                    return True
                await self.set_tracking(False)
                # The mount needs a moment to actually stop before it will
                # honour a park; polling Gps immediately reads the old state.
                await asyncio.sleep(1.0)
                if not await self.get_tracking():
                    return True
            except Exception:  # noqa: BLE001 — see the docstring
                await asyncio.sleep(1.0 if attempt == 0 else 0.0)
        return False

    async def get_tracking(self) -> bool:
        return (await self._get("GAT")).startswith("1")

    async def pier_side(self) -> PierSide:
        side = await self._get("Gm")
        if side.startswith("E"):
            return PierSide.EAST
        if side.startswith("W"):
            return PierSide.WEST
        return PierSide.UNKNOWN

    #: sidereal rate in deg/s (15.041"/s) — the unit :GdG# is a fraction of.
    _SIDEREAL_DEG_S = 0.004178074

    async def guide_rates(self) -> tuple[float, float] | None:
        """The rates our emulated pulses ACTUALLY deliver (hardware-measured):
        ra ~1x sidereal (tracking-suspend east / R2-west), dec ~0.5x (R1).
        (The mount's :GdG# setting governs only the inert :Mg*# path.)"""
        return (_PULSE_RA_RATE_DEG_S, _PULSE_DEC_RATE_DEG_S)

    # ------------------------------------------------------------- motion

    async def set_tracking(self, on: bool) -> None:
        await self._cmd_ack("Te" if on else "Td",
                            "tracking on" if on else "tracking off")

    async def set_tracking_rate(self, rate: str) -> None:
        """Select the drive rate via the classic LX200 ``:TQ#``/``:TL#``/
        ``:TS#`` commands. Unlike ``:Te#``/``:Td#`` (verified ACK ``1`` at
        scope 2026-07-20, see docs/hardware/zwo-am5-lx200-protocol.md), these
        rate-select commands are NOT in the captured wire truth -- classic
        LX200 firmwares reply to them with nothing at all, and the AM5's other
        rate-index commands (``:R0#``..``:R9#``) are confirmed fire-and-forget.
        So this sends fire-and-forget (``reply="none"``) rather than assuming
        an ack; whether the AM5N actually ACKs ``:TL#``/``:TS#`` is an
        at-scope validation item (plan Task 7)."""
        if rate not in TRACKING_RATES:
            raise DeviceError(f"{self.name}: unknown tracking rate {rate!r}")
        await self._request(_TRACKING_RATE_CMD[rate], reply="none")
        self._tracking_rate = rate

    async def get_tracking_rate(self) -> str:
        return self._tracking_rate

    async def _set_target(self, ra_hours: float, dec_deg: float) -> None:
        await self._cmd_ack(f"Sr{lx200.format_ra(ra_hours)}", "set target RA")
        await self._cmd_ack(f"Sd{lx200.format_dec(dec_deg)}", "set target Dec")

    async def slew(self, ra_hours: float, dec_deg: float) -> None:
        """Goto and wait until settled. AM5 moves are fire-and-forget, so the
        ONLY completion signal is polling: settled when the coordinate delta
        stays under SETTLE_DEG across two consecutive polls. Cancel-safe: a
        CancelledError (or timeout) halts the mount with :Q# first."""
        await self._set_target(ra_hours, dec_deg)
        try:
            reply = await self._request("MS", reply="ack")
        except LinkError as exc:
            # This raw ``request`` was the one ack-class send in the driver that
            # bypassed _cmd_ack, so a silent mount here escaped as a LinkError —
            # not a DeviceError — and the API's ``except DeviceError`` handlers
            # let it through as a 500 instead of a 409. A second goto fired
            # while the first is still settling lands exactly here.
            raise self._link_error("goto", exc) from exc
        if reply == lx200.REFUSED:
            raise await self._refused_error("goto")
        # LX200 :MS# convention: '0' = slew accepted; anything else = refused.
        #
        # THE CODE ALONE IS NOT A SENTENCE. This used to raise the bare reply,
        # and on 2026-09-06 the operator's whole explanation for a held resume
        # was "goto rejected (reply 'e6')" - a string that appears nowhere in
        # this repo or in any ZWO document. The words come from
        # ``_GOTO_REFUSALS``, the code is kept verbatim beside them because it
        # is the only part a firmware can be searched for, and the class says
        # "the mount said no" so a caller need not read the prose to know it.
        if reply != "0":
            words = _goto_refusal_words(reply)
            raise GotoRefused(
                f"{self.name}: goto rejected ({words}; reply {reply!r})",
                code=reply, reason=words)
        self._slewing = True
        try:
            deadline = asyncio.get_running_loop().time() + SLEW_TIMEOUT_S
            prev: tuple[float, float] | None = None
            stable = 0
            while True:
                if asyncio.get_running_loop().time() > deadline:
                    raise DeviceError(
                        f"{self.name}: slew failed to settle within "
                        f"{SLEW_TIMEOUT_S:.0f}s — halted (:Q#)")
                await asyncio.sleep(SETTLE_POLL_S)
                ra, dec = await self.get_position()
                if prev is not None:
                    d_deg = max(abs(ra - prev[0]) * 15.0, abs(dec - prev[1]))
                    stable = stable + 1 if d_deg < SETTLE_DEG else 0
                    if stable >= 2:
                        return
                prev = (ra, dec)
        except BaseException:
            # ANY abnormal settle exit — cancel, timeout, link/parse failure —
            # halts the mount before propagating (B review M3). :Q# is
            # write-only (cannot hang) and harmless if the goto already ended.
            # Noted BEFORE the send, not after: the send is best-effort inside a
            # try/except, and a mount that was mid-slew is decelerating (or not
            # stopping at all) whether or not the halt byte got out — either way
            # the next command has every reason to time out.
            self._note_halt()
            try:
                await self._request("Q", reply="none")
            except Exception:  # noqa: BLE001 - halt is best-effort on teardown
                pass
            raise
        finally:
            # Cleared here even on the cancel path, and that is what opens the
            # window _note_halt covers: a cancelled goto stops being "slewing"
            # by this driver's bookkeeping several seconds before the mount
            # stops being slewing by physics.
            self._slewing = False

    async def sync(self, ra_hours: float, dec_deg: float) -> None:
        await self._set_target(ra_hours, dec_deg)
        try:
            reply = await self._request("CM", reply="hash")
        except LinkError as exc:
            raise DeviceError(f"{self.name}: sync failed: {exc}") from exc
        if reply == lx200.REFUSED:
            raise await self._refused_error("sync")

    # --- rotate_axis: NOT offered on this mount, and here is why --------------
    #
    # ``Telescope.can_rotate_axis`` stays False, so a polar-alignment arc on
    # this mount keeps using gotos and says so in the log. That is a deliberate
    # refusal, not an omission, and it is the honest answer to the 2026-09-09
    # failures rather than a fix for them: this mount is the one that produced
    # them, and it is the one mount here that cannot be given the fix without a
    # session at the scope.
    #
    # A single-axis rotation of a known angle over this wire would be ``:R<n>#``
    # + ``:M<e|w>#``, a wait, then ``:Q<e|w>#`` — the driver times the move, so
    # the timing IS the angle. Three things have to be known before that is a
    # measurement rather than a hope, and none of them is:
    #
    #  1. THE RATE. ``_RATE_TABLE`` is preset indices with approximate
    #     sidereal multiples (R7 ~ 60x). The one calibration on record is a
    #     single 1 s sample at a requested 0.25 deg/s reading 0.250
    #     (docs/hardware/zwo-am5-lx200-protocol.md), which cannot separate the
    #     steady rate from the acceleration ramp inside it. A 12 degree leg at
    #     R7 runs 48 s, where a 2% rate error is 0.24 degrees — a quarter of
    #     ``polar/native.py``'s whole MAX_ROTATION_DISAGREEMENT_DEG budget, from
    #     an unknown.
    #
    #  2. WHETHER IT SUPERIMPOSES ON TRACKING. The same capture records that it
    #     does NOT, in words: "``:M<dir>#`` during tracking does NOT cleanly
    #     superimpose (Me@R1 read +1.5x sid, Mw@R1 read +0.5x — both eastward)".
    #     That is why the emulated pulse guide suspends tracking to go east
    #     instead of using ``:Me#``. The behaviour at R7 has never been
    #     measured, and the whole geometry downstream depends on knowing whether
    #     the commanded turn is on top of tracking or instead of it.
    #
    #  3. WHETHER THE STOP DISTURBS TRACKING. Open runbook item, same document:
    #     "Whether ``:Q#`` disturbs tracking on this firmware".
    #
    # And the hazard is not academic. ``move_axis`` below is fire-and-forget
    # with no thread-side stop, unlike ``_Pulse``, which exists because a
    # blocked event loop turned guide pulses into +83, +128 and +35 arcsec of
    # unwanted travel on 2026-09-06. At R7 the same stall moves the telescope
    # 250 times further per second. Shipping a 48 s timed move on that, written
    # against a mount nobody can put a hand on tonight, is how the next incident
    # gets written.
    #
    # TO FLIP THIS ON, one bench session with the guider idle and tracking on:
    #   a. ``:R7#`` + ``:Mw#``, hold 30 s by the wall clock, ``:Qw#``; read
    #      ``:GR#`` before and after AND plate solve before and after. Repeat
    #      east. The solved separation over the measured dwell is the rate; the
    #      east/west difference is the tracking superposition.
    #   b. Repeat with tracking off, to separate the two.
    #   c. Read ``:GAT#`` after each ``:Q<dir>#`` to settle item 3.
    # With a rate good to 1% and a known tracking rule, ``rotate_axis`` here is
    # a start, a thread-timed wait (the ``_Pulse`` shape, not ``asyncio.sleep``)
    # and a stop, returning rate x measured dwell.

    async def move_axis(self, axis: str, rate_deg_s: float) -> None:
        if axis not in ("ra", "dec"):
            raise DeviceError(f"{self.name}: unknown axis {axis!r}")
        if rate_deg_s == 0.0:
            # stop both directions of this axis (fire-and-forget)
            for d in ("e", "w") if axis == "ra" else ("n", "s"):
                await self._request(f"Q{d}", reply="none")
            return
        for bound, rate_cmd in _RATE_TABLE:
            if abs(rate_deg_s) <= bound:
                break
        await self._request(rate_cmd, reply="none")
        await self._request(_MOVE_CMD[(axis, rate_deg_s > 0)], reply="none")

    def _capped_ms(self, direction: str, ms: int) -> int:
        """Bound one pulse to ``_PULSE_MAX_MS``, warning (rarely) when it bites.

        The cap is not a tuning knob, it is the blast radius: 15 arcsec is what
        a stall of ANY length can cost once the move itself is bounded."""
        global _cap_warn_last
        ms = max(0, int(ms))
        if ms <= _PULSE_MAX_MS:
            return ms
        now = time.monotonic()
        if now - _cap_warn_last >= _PULSE_CAP_WARN_S:
            _cap_warn_last = now
            bus.log("warning",
                    f"{self.name}: pulse {direction} {ms} ms capped to "
                    f"{_PULSE_MAX_MS} ms (one move may not exceed ~15 arcsec)",
                    "mount")
        return _PULSE_MAX_MS

    def _pulse_plan(self, d: str):
        """(start commands, stop command, what-started, what-stopped) for one
        direction. ``d`` == "e" here means east WITHOUT the tracking suspend --
        the caller decides that, since it costs a :GAT# read."""
        rate_cmd = _PULSE_WEST_RATE_CMD if d == "w" else _PULSE_DEC_RATE_CMD
        return ([(rate_cmd, "none"), (_PULSE_MOVE[d], "none")],
                (_PULSE_STOP[d], "none"),
                f"pulse {d}", f"pulse {d} (stop)")

    async def _pulse_on_the_loop(self, start, stop, secs, what_start,
                                 what_stop) -> None:
        """The pre-GN-02 shape: start, ``asyncio.sleep``, stop, all on the loop.

        Kept for any transport with no thread-side door (an older test double,
        a link class that predates ``request_sync``). It carries the defect this
        row is about -- a blocked loop lengthens the move -- so it is a
        compatibility path, not a choice: every real link has ``request_sync``."""
        for cmd, reply in start:
            if reply == "ack":
                await self._cmd_ack(cmd, what_start)
            else:
                await self._request(cmd, reply="none")
        try:
            await asyncio.sleep(secs)
        finally:
            await self._send_stop_on_the_loop(stop, what_stop)

    async def _send_stop_on_the_loop(self, stop, what_stop) -> None:
        """The stop through the async path -- the one that can relink a dropped
        port (``_request``) and the one that raises on a refusal (``_cmd_ack``).
        The pulse thread can do neither, so every failure lands back here."""
        if stop[1] == "ack":
            await self._cmd_ack(stop[0], what_stop)
        else:
            await self._request(stop[0], reply="none")

    async def pulse_guide(self, direction: str, ms: int) -> None:
        """EMULATED pulse guide (native :Mg*# is inert; :M<dir># during
        tracking REPLACES the drive -- see the module notes). Strategies:
        east = tracking-suspend (exact 1x sidereal drift), falling back to
        R1+Me at 0.5x sidereal if tracking is already off; west = R2+Mw
        (measured exactly 1x sidereal west); n/s = R1 moves.

        THE EVENT LOOP IS NOT IN THE TIMING PATH (GN-02). The whole pulse --
        start, wait, stop -- runs on one worker thread (``_Pulse``), so a loop
        blocked by a frame readout or a filter-wheel move cannot lengthen the
        move: on 2026-09-06 it lengthened three of them into +83, +128 and +35
        arcsec jumps that took the guider 12-30 s each to walk back. The
        coroutine only awaits that thread and reports what it found, and ``ms``
        is capped at ``_PULSE_MAX_MS`` so even a stop that fails outright costs
        ~15 arcsec.

        Cancellation still stops the mount immediately (the thread waits on an
        Event, not a sleep), and a stop that could not be written is re-sent
        through the async path, which can reopen a dropped port -- the thread
        deliberately cannot."""
        d = direction.lower()[0]
        if d not in "nsew":
            raise DeviceError(f"{self.name}: bad guide direction {direction!r}")
        secs = self._capped_ms(direction, ms) / 1000.0
        if d == "e" and await self.get_tracking():
            start = [("Td", "ack")]
            stop = ("Te", "ack")
            what_start = "pulse east (suspend tracking)"
            what_stop = "pulse east (resume tracking)"
        else:
            start, stop, what_start, what_stop = self._pulse_plan(d)
        if not hasattr(self._link, "request_sync"):
            await self._pulse_on_the_loop(start, stop, secs, what_start,
                                          what_stop)
            return

        pulse = _Pulse(self._link, self.name, start, stop, secs)
        try:
            await asyncio.to_thread(pulse.run)
        except asyncio.CancelledError:
            # The thread is still inside abort.wait: tell it to stop the mount
            # NOW, and wait for that OFF the loop (bounded) before propagating.
            pulse.abort.set()
            await asyncio.to_thread(pulse.done.wait, secs + _PULSE_CANCEL_JOIN_S)
            if pulse.answered:
                self._halting = False
            if pulse.started and not pulse.stop_sent:
                # The cancel is not the emergency here; a mount still moving is.
                try:
                    await self._send_stop_on_the_loop(stop, what_stop)
                except Exception:   # noqa: BLE001 - the cancel still wins
                    pass
            raise
        # An ack-class command was ANSWERED: the same evidence _cmd_ack acts on.
        if pulse.answered:
            self._halting = False
        if not pulse.started:
            if pulse.start_reply is not None:
                if pulse.start_reply == lx200.REFUSED:
                    raise await self._refused_error(what_start)
                raise DeviceError(f"{self.name}: {what_start} rejected "
                                  f"(reply {pulse.start_reply!r})")
            exc = pulse.error
            if isinstance(exc, LinkError):
                raise self._link_error(what_start, exc) from exc
            if exc is not None:
                raise exc
            raise DeviceError(f"{self.name}: {what_start} did not start")
        if not pulse.stop_sent:
            # The port died mid-pulse. The async path is the one that can
            # reopen it, so the stop goes out from here -- the mount is moving.
            bus.log("warning",
                    f"{self.name}: could not stop the pulse from the pulse "
                    f"thread (:{stop[0]}# -- {pulse.error or 'no reply'}); "
                    "sending it again now", "mount")
            await self._send_stop_on_the_loop(stop, what_stop)
            return
        if pulse.stop_reply is not None and pulse.stop_reply != lx200.ACK_OK:
            # ANSWERED, and the answer was no. For the east strategy that means
            # tracking did not resume: the star now drifts east at sidereal
            # rate, which the guider must be told rather than left to infer.
            bus.log("warning",
                    f"{self.name}: could not stop the pulse -- the mount "
                    f"refused :{stop[0]}# (reply {pulse.stop_reply!r})", "mount")
            if pulse.stop_reply == lx200.REFUSED:
                raise await self._refused_error(what_stop)
            raise DeviceError(f"{self.name}: {what_stop} rejected "
                              f"(reply {pulse.stop_reply!r})")

    async def is_slewing(self) -> bool:
        # DELIBERATELY not widened to include the halt window. "_halting" means
        # "not yet proven at rest", which is not the same claim as "moving", and
        # this flag feeds the status badge, the guider and the resume logic —
        # they should not start treating an unproven state as motion on the
        # strength of a wording fix.
        return bool(getattr(self, "_slewing", False))

    async def stop(self) -> None:
        """Emergency halt: :Q# goes out FIRST, no preamble. Whether :Q# also
        disturbs tracking on this firmware is an at-scope runbook item."""
        # The rig's own sequence: POST /api/mount/stop cancels the goto task AND
        # calls this. Both halt paths open the same window, so both note it.
        self._note_halt()
        await self._request("Q", reply="none")


# ------------------------------------------------------------------ session

class ZwoAm5Session:
    """One live serial connection to one AM5. Fills only the telescope role."""

    name = "zwo-am5"

    def __init__(self, port_path: str):
        self._port_path = port_path
        self._tel: ZwoAm5Telescope | None = None

    async def get_device(self, role: str, conn) -> ZwoAm5Telescope:
        if role != "telescope":
            raise DeviceError(f"zwo-am5 backend fills only 'telescope' (asked {role!r})")
        if self._tel is None:
            name = (getattr(conn, "extra", None) or {}).get("name") or "ZWO AM5"
            tel = ZwoAm5Telescope(_make_link(self._port_path), name=name)
            tel.role = role
            await tel.connect()
            self._tel = tel
        return self._tel

    def native_guider(self):
        return None

    def guide_camera(self):
        return None

    def native_solver(self):
        return None

    async def health(self) -> dict | None:
        if self._tel is None or not self._tel.connected:
            return None
        return {"port": self._port_path, "firmware": self._tel.firmware}

    async def close(self) -> None:
        tel, self._tel = self._tel, None
        if tel is not None:
            await tel.disconnect()      # sends :Q# then closes the link


# ------------------------------------------------------------------ backend

def _app_version() -> str:
    from ... import __version__
    return __version__


class ZwoAm5Backend:
    """The AM5 native serial backend — first real citizen of the entry-point
    driver framework (spec 2026-07-20, sub-project B)."""

    name = "zwo-am5"
    label = "ZWO AM5 (native serial)"
    roles = ("telescope",)
    discoverable = True
    hostless = False
    version = "0"                # set to the app version in register_all()
    author = ""
    min_app_version = "0"
    transport = "serial"
    hardware = True
    driver_type = "zwo-am5"

    async def open(self, conn) -> ZwoAm5Session:
        port = getattr(conn, "port_path", None)
        if not port:
            raise DeviceError(
                "zwo-am5 backend needs a serial port_path (e.g. COM3)")
        return ZwoAm5Session(port)

    async def discover(self) -> list[dict]:
        """Enumerate serial ports; AM5s match VID:PID 03C3:4001."""
        try:
            from serial.tools import list_ports
            ports = await asyncio.to_thread(list_ports.comports)
        except Exception:  # noqa: BLE001 - discovery must never raise
            return []
        found = []
        for p in ports:
            if getattr(p, "vid", None) == 0x03C3 and getattr(p, "pid", None) == 0x4001:
                found.append({"role": "telescope", "name": "ZWO AM5 (USB)",
                              "port_path": p.device, "verified": True})
        return found


def register_all() -> None:
    """Entry-point target: [project.entry-points."astrodeck.backends"]."""
    from ..backend import register
    b = ZwoAm5Backend()
    b.version = _app_version()
    register(b)
