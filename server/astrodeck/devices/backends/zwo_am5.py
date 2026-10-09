# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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
import collections
import logging
import math
import threading
import time
from datetime import datetime, timezone

from ...catalog import coords
from ...config import config_store
from ...events import bus
from .. import lx200
from ..base import (DeviceError, GotoNotArrived, GotoRefused, PierSide,
                    SyncRefused, SyncUnverified, Telescope,
                    TRACKING_RATES, quotable_sync_reply)
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

#: Wall-clock cap on a slew settle (spec: no motion path may hang). The
#: stall window (``GOTO_STALL_S``) runs INSIDE it and never extends it: a goto
#: that is neither arrived nor stalled by this deadline is halted here.
SLEW_TIMEOUT_S = 120.0
#: Settle criterion: coord delta below this across two consecutive polls.
SETTLE_DEG = 0.05
#: Poll cadence during a slew.
SETTLE_POLL_S = 0.5
#: How close the position read back after a ``:CM#`` must be to the synced
#: coordinates (angular separation, degrees) for the sync to count as taken
#: (#850). The worst honest disagreement, summed:
#:   - ``:Sr#`` rounds the requested RA to 1 s of time: <= 0.0021 deg;
#:   - ``:Sd#`` rounds the requested Dec to 1 arcsec: <= 0.0003 deg;
#:   - ``:GR#`` reads the RA back at 1 s of time: <= 0.0021 deg (``:GD#`` at
#:     1 arcsec, negligible);
#:   - a guide pulse running concurrently moves RA by up to its 1000 ms cap
#:     at the emulated 1x sidereal rate, 1.0 s x 0.004178 deg/s = 0.0042 deg;
#:   - with tracking OFF the reported RA advances at the sidereal rate while
#:     the retries below wait, up to about 1 s x 0.004178 = 0.0042 deg.
#: About 0.013 deg in all, so 0.05 is a 4x margin. It is the same number as
#: ``SETTLE_DEG``, the criterion this driver already uses for "the position did
#: not change". The bench on 2026-10-08 read back within 0.002 deg every time.
#: A REFUSED sync smaller than this passes as taken, which is harmless: the
#: pointing is then off by less than 3 arcmin, and the centring loop's own
#: stuck check (the next solve shows the field unmoved) still sees it.
SYNC_VERIFY_DEG = 0.05
#: Further read-backs after the first, if the first disagrees. The bench read
#: back 1 s after the reply and the report had already moved, but nobody has
#: measured how SOON it moves, so a late update must not read as a refusal.
SYNC_READBACK_RETRIES = 2
#: Wait between those read-backs. Two of them span 1 s, the delay the bench
#: did measure as enough.
SYNC_READBACK_RETRY_S = 0.5
#: How far (angular separation, deg) the settled report may sit from the
#: commanded target and still count as ARRIVED, before the drift allowance
#: below (#860). The honest disagreement of a mount that did arrive, summed:
#:   - ``:Sr#`` rounds the target RA to 1 s of time:   <= 0.0021 deg
#:   - ``:Sd#`` rounds the target Dec to 1 arcsec:     <= 0.0003 deg
#:   - ``:GR#`` reads RA back at 1 s of time:          <= 0.0021 deg
#:   - ``:GD#`` reads Dec back at 1 arcsec:            <= 0.0003 deg
#:   - a guide pulse still running: 1.0 s cap x 0.004178 deg/s = 0.0042 deg
#: About 0.009 deg in all. Measured: the 2026-07-20 at-scope gotos landed with
#: a 0.000 deg residual (docs/hardware/zwo-am5-lx200-protocol.md). 0.10 is
#: 2 x SETTLE_DEG, an 11x margin over the budget. A goto that never started
#: from a position less than 0.10 deg away passes as arrived; the centring
#: solve and its stuck check see that case.
GOTO_ARRIVE_DEG = 0.10
#: Growth of that tolerance per second since ``:MS#`` was accepted, deg/s:
#: the sidereal rate, 15.041 arcsec/s / 3600 = 0.0041781 deg/s. Covers a
#: firmware that goes to a fixed hour angle with tracking off, whose reported
#: RA then walks at the sidereal rate. At the ``SLEW_TIMEOUT_S`` deadline the
#: tolerance is 0.10 + 0.0041781 x 120 = 0.60 deg.
GOTO_DRIFT_DEG_S = 0.0041781
#: How long a mount SHORT of its target must stand still AND make no progress
#: toward it before the goto is called stalled (#860). It must outlast the
#: ``:MS#`` start latency and any pause inside a healthy goto, neither
#: measured. The evidence there is: a ``:hP#`` the mount took is moving
#: within 1-2 s (see ``PARK_NOOP_DETECT_S``), and the 2026-07-20 gotos ended
#: on target under the old 1 s still rule, so those (non-flip) gotos had no
#: still pause of 1 s mid-way. 10 s is 5x the 2 s latency bound. Counted in
#: polls (ceil(10.0 / 0.5) = 20 comparisons), not wall time. Too short halts
#: a healthy goto mid-way; too long costs that many seconds on a real stall.
#: HARDWARE-PENDING (bench, DESIGN-P4 Appendix A).
GOTO_STALL_S = 10.0
#: How far (deg) the residual to the target must fall across the stall
#: window for the mount to count as still getting there (#860). A step under
#: ``SETTLE_DEG`` is "still" for ARRIVAL, but it is not "stuck": the AM5's
#: slow rate R5 is about 7.8x sidereal = 0.0326 deg/s = 0.016 deg per poll,
#: under ``SETTLE_DEG``. Judged on a step alone, any slow phase starting more
#: than 0.10 + 10 s x 0.0326 = 0.43 deg out would be halted part way.
#: The floor is what a STUCK mount can fake across one window:
#:   - tracking-off report drift: 0.0041781 deg/s x 10 s = 0.0418 deg
#:   - two residuals' read quantization (RA 0.0021 + Dec 0.0003, each):
#:     2 x 0.0024 = 0.0048 deg
#: 0.0466 deg in all; 0.10 is 2.1x that. The slowest approach still read as
#: progress is 0.10 deg per window = 0.01 deg/s; R5 makes 0.33 deg per window,
#: 3.3x. A final approach at R3 (1.9x sidereal, 0.079 deg per window) would
#: read as stuck. Nothing says a goto uses R3 (those are the jog presets,
#: ``_RATE_TABLE``); the bench measures it, and no window can tell an
#: approach under 2x sidereal from drift. The window is 20 polls of 0.5 s
#: plus the reads; the 0.10 floor holds while a window lasts under
#: (0.10 - 0.0048) / 0.0041781 = 22.8 s, i.e. a GR+GD pair under 0.64 s.
#: Past that, a drifting stuck mount can read as progress and the
#: ``SLEW_TIMEOUT_S`` deadline (halted, plain ``DeviceError``) ends it
#: instead: late, never silent.
#: HARDWARE-PENDING (the approach-speed profile, DESIGN-P4 Appendix A).
GOTO_PROGRESS_DEG = 0.10
#: Fixed words for ``GotoNotArrived.reason``. No figures (#618); each fits
#: the 137-character cut in every surface that quotes it.
GOTO_STALLED_REASON = "the mount stopped short of the target"
GOTO_STOPPED_REASON = "a stop was sent during the goto"
#: ``:MS#`` itself failed on the link (no reply, or the port went), so
#: nobody knows whether the mount took the goto. It was halted, and the
#: caller re-measures where it is: the centring loop solves there, as for
#: any other miss. Fixed words, like the two above.
GOTO_LINK_REASON = "the link to the mount failed during the goto"
#: Fixed words for ``GotoRefused.reason`` when ``:MS#`` answers ``e14``.
GOTO_E14_PARKED_REASON = "the mount is parked; unpark first"
GOTO_E14_STATE_REASON = ("not in a state to move: a limit, or a slew "
                         "already running")
#: Fixed words for ``GotoRefused.reason`` when ``:MS#`` answers a code not in
#: ``_GOTO_REFUSALS``. The code stays in the MESSAGE, which keeps
#: ``_goto_refusal_words``'s text unchanged; the reason, which the resume
#: ladder writes into a hold reason, carries none (#618).
GOTO_UNKNOWN_REFUSAL_REASON = ("the mount refused the goto; its altitude, "
                               "meridian or park limits are the usual reasons")
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
#: How long a ``:hP#`` may leave the mount neither moving nor parked before
#: it is taken as lost and sent once more (S4 orchestrator ruling 9, #342).
#: A few seconds, far shorter than ``PARK_WAIT_S``: a park the mount took is
#: visibly slewing within a second or two. It also has a CEILING, and the
#: ceiling is computed rather than chosen: "not moving" is judged by the
#: position changing by less than ``SETTLE_DEG``, and a mount standing still
#: with its drive off sees its RA advance at the sidereal rate, 0.0042 deg/s.
#: Over this window plus one poll (6 s) that is 0.025 deg; past about 11 s it
#: would reach ``SETTLE_DEG`` and a lost park would read as a slew.
PARK_NOOP_DETECT_S = 5.0
#: How long ``_park_now`` waits for a halt window to close before it starts.
#: Bounded, and NOT a grace period: the window ends on EVIDENCE (see
#: ``_note_halt``, which refuses to invent a settling time because nobody has
#: measured how long an AM5 takes to stop from an R8 slew). This is only the
#: point at which waiting for that evidence stops being worth it — after it,
#: the park is attempted anyway, because an unparked mount is worse than a
#: slow one. Generous, because the alternative to waiting is the failure this
#: exists to prevent: the lost emergency park of 2026-08-06.
HALT_DRAIN_TIMEOUT_S = 30.0
#: How close to a celestial pole (degrees of declination) a freshly (re)opened
#: mount's read has to be for the driver to take it as the HOME read of a reset
#: mount (#144). A mount that has just powered up reports its home position,
#: counterweight down and pointing at the pole, as exactly 90 degrees; this is
#: three arcminutes, wide enough that the report's own rounding never misses it
#: and narrow enough that a mount really pointed near the pole (Polaris sits
#: more than half a degree from it) is not accused of having reset. It is a
#: window on ONE read, not a measurement of anything: the home read and a real
#: pointing at the pole are the same number, which is why the flag it sets is
#: "unknown", not "wrong".
POLE_SIGNATURE_DEG = 0.05

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


def _goto_refusal_reason(code: str) -> str:
    """Fixed words for ``GotoRefused.reason``: the known meaning, or
    ``GOTO_UNKNOWN_REFUSAL_REASON``. Never the code: the resume ladder writes
    this into a hold reason, and a hold reason carries no code (#618)."""
    return _GOTO_REFUSALS.get(code) or GOTO_UNKNOWN_REFUSAL_REASON


#: Fixed words for a sync nobody could confirm (``SyncUnverified.reason``).
#: They become a hold reason and a ``last_error`` downstream, so they carry no
#: figure, no reply and none of the transport's own words (the #618 contract;
#: a timed-out position read can hold half a coordinate).
SYNC_UNVERIFIED_READBACK = ("the mount did not answer the position read after "
                            "the sync, so whether it took is unknown")
SYNC_UNVERIFIED_LINK_DURING = ("the link failed during the sync, so whether "
                               "the mount took it is unknown")
SYNC_UNVERIFIED_LINK_BEFORE = "the link failed before the sync was sent"
#: What an ``e11`` reply gets (#850). The bench saw e11 WITH THE TUBE AT
#: HOME, and nobody knows what it means anywhere else, so neither text treats
#: the reply as proof of where the tube is. ``_sync_refused`` picks one from
#: the refusal's read-back (``residual_deg``), and both follow the safe order:
#: Trust position if the tube really is at home, otherwise bring it home by
#: eye with a pad key first, and only after that a goto and a sync away from
#: the pole. Neither tells the operator to slew or go anywhere: a goto is
#: aimed from the position the mount believes, which is the thing in doubt.
#: Each is at most 90 characters and carries no digit (the reply is quoted
#: separately), so the hub's refused line still fits in 140.
#:
#: The mount's opinion is near the sky (or unknown): the tube may well be at
#: home, so Trust position, behind its condition, comes first.
SYNC_E11_AT_HOME_REASON = ("if the tube really is at home, use Trust position; "
                           "a sync away from the pole then works")
#: The mount's opinion is more than ``SYNC_E11_ELSEWHERE_DEG`` from the sky:
#: the tube is not where the mount thinks, so it must come home by eye first.
SYNC_E11_ELSEWHERE_REASON = ("tube not where the mount thinks: bring it home "
                             "by eye with a pad key, then Trust position")
#: Above this read-back residual (deg) an ``e11`` gets
#: ``SYNC_E11_ELSEWHERE_REASON``; at or below it, or with no read-back,
#: ``SYNC_E11_AT_HOME_REASON``. It mirrors
#: ``resume_arm.RECOVERY_REFUSED_SYNC_MAX_DEG`` (the resume ladder's own
#: "close enough to go ahead" figure) and is kept here rather than imported,
#: so the driver does not depend on the sequencer. Change both together.
SYNC_E11_ELSEWHERE_DEG = 5.0


def _sync_reply(reply: str) -> tuple[str, str]:
    """``(code, words)`` for a ``:CM#`` reply already stripped of ``#`` and
    whitespace. ``code`` is what ``SyncRefused``/``SyncUnverified`` carry and
    ``words`` is how a text names it: the reply itself only when
    ``base.quotable_sync_reply`` (the one copy of the quoting rule) allows
    it, ``""``/"an empty reply" for nothing, and
    ``"unrecognised"``/"an unrecognised reply" for anything else, which is
    never quoted: a desynchronised link can hand the sync a ``:GR#``-shaped
    answer ("07:23:41"), and at the home position that is the local sidereal
    time, a site oracle (#140, #166)."""
    if not reply:
        return "", "an empty reply"
    quoted = quotable_sync_reply(reply)
    if quoted is not None:
        return quoted, f"reply {quoted!r}"
    return "unrecognised", "an unrecognised reply"


def _moved_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """How far apart two (RA hours, Dec degrees) reads are, in the units the
    ``SETTLE_DEG`` criterion uses: the larger of the RA step times 15 and the
    Dec step. RA is cyclic, so the step is taken the short way round: a mount
    standing still at 23:59:58 reads 00:00:03 five seconds later, and that is
    0.02 degrees of sidereal drift, not 360 degrees of slew."""
    d_ra = abs(a[0] - b[0]) % 24.0
    return max(min(d_ra, 24.0 - d_ra) * 15.0, abs(a[1] - b[1]))


def _goto_residual_deg(ra_hours: float, dec_deg: float,
                       read_ra: float, read_dec: float) -> float | None:
    """Angular separation (deg) between a goto's commanded target and a
    position read, or ``None`` when a value is not finite
    (``coords.angular_sep_deg`` refuses those). Angular, so a report near the
    pole that differs by hours of RA is still measured as the small distance
    it is. The coordinates never leave this function (#140, #166)."""
    try:
        return coords.angular_sep_deg(ra_hours, dec_deg, read_ra, read_dec)
    except ValueError:
        return None


def _made_progress(window) -> bool:
    """True when the goto residual fell by at least ``GOTO_PROGRESS_DEG``
    from the oldest to the newest entry of ``window`` (#860). An
    unmeasurable end (``None``) is no progress: a stuck mount must not be
    excused by a read nobody could measure."""
    first, last = window[0], window[-1]
    if first is None or last is None:
        return False
    return first - last >= GOTO_PROGRESS_DEG

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


def _site_latlon() -> tuple[float, float] | None:
    """Site (lat, lon_east) from config, or None when no site is saved;
    injectable for tests.

    NONE, NOT 0,0 (#24). `connect` writes this into the mount's own firmware on
    every open, so at the default site every connect used to tell the AM5 it
    stood in the Gulf of Guinea - and the mount then makes its own hour-angle
    decisions (meridian limits, pier side on a GoTo) from that, where nothing
    in this tree can see them. Whatever site the mount already holds, set from
    its handset or a previous session, is a better answer than a made-up one.
    """
    from ...config import config_store
    from ...site_gate import site_lat_lon
    return site_lat_lon(config_store.cfg().site)


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
    #: The pre-slew pier-collision guard is armed for this mount. Not because
    #: the AM5 answers ``DestinationSideOfPier`` -- it has no such command --
    #: but because ``destination_pier_side`` below predicts it from hour-angle
    #: geometry and CHECKS the prediction against ``:Gm#`` before returning it,
    #: falling back to UNKNOWN (which the guard passes) whenever the two
    #: disagree. Until this flag went True, ``safety.enforce_pier_limits`` was
    #: a switch the operator could turn on that guarded nothing on the only
    #: mount this rig owns.
    reports_destination_pier_side = True
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
        #: Count of whole-mount halts this driver has sent (``_note_halt``).
        #: ``slew`` compares it across a goto, so a halt from ``stop()``, which
        #: the hub's manual-move deadman also calls, ends the goto as stopped
        #: instead of reading the halted mount as arrived (#860). In memory
        #: only.
        self._halt_gen = 0
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
        #: Latched True when a (re)open read the home pole (#144): the mount
        #: reports where it BELIEVES the tube is, and after a reset that belief
        #: is the home position wherever the tube physically is. Cleared only by
        #: evidence, a successful ``sync`` or the operator's ``trust_position``,
        #: and deliberately NOT by a goto (see ``position_known``).
        self._position_untrusted = False
        #: Serializes `_park_now` against `pulse_guide` (WP-18, #342). Both
        #: issue motion commands on the one serial link, and a park landing
        #: while a pulse is mid-flight can interleave with it on the wire, or
        #: race the east strategy's tracking-suspend/resume pair: a pulse's
        #: own `:Te#` arriving right after park's `:Td#` would put tracking
        #: back on just before the `:hP#` that is a documented silent no-op
        #: against (see `_park_now`'s "STOP TRACKING FIRST" note). One lock
        #: makes "park" and "pulse" mutually exclusive instead of merely
        #: unlikely to collide.
        self._pulse_park_lock = asyncio.Lock()

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

    @property
    def position_known(self) -> bool:
        """False from a (re)open that read the home pole until a sync or
        ``trust_position`` (#144; the contract is ``Telescope.position_known``).

        THE AM5 CANNOT TELL US. It has no home sensor and no brake, so after a
        power cycle (reproduced on the rig 2026-09-23) it reports its home
        position, pointing at the pole, wherever the tube is, and nothing on the
        wire says it restarted. A pole-signature read at the handshake is the
        evidence available; the cost of the false positive (a mount really
        parked at home reads the same) is one sync, and a nudge at the pole is
        already clamped by ``mount_offset``.

        NOT CLEARED BY A GOTO, a slew's settle or a reopen that reads somewhere
        else. A goto from a wrong model lands wherever the model sends it, the
        settle poll reads back the mount's own opinion of the arrival, and
        docs/hardware/zwo-am5-lx200-protocol.md records this mount's reported
        coordinates walking 12.9 arcmin per minute while the tube held its
        field, so none of it is a measurement of where the tube is."""
        return not self._position_untrusted

    # ------------------------------------------------------------ helpers

    async def _request(self, cmd: str, *, reply: str = "hash",
                       timeout: float = 1.5, retry: bool = True) -> str | None:
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
        order to close it is not a recovery, it is a loop.

        ``retry=False`` sends once and lets a ``LinkError`` out without the
        reopen-and-resend. For a command whose meaning depends on state the
        mount may have lost with the link: ``:CM#`` syncs to the target set
        just before it, and a mount that restarted has lost that target, so a
        bare resend after the reopen would sync it to whatever target it holds
        (#850, found by a review probe). ``sync`` re-sends the target itself."""
        try:
            return await self._link.request(cmd, reply=reply, timeout=timeout)
        except LinkError:
            # "We believe we are connected, but the port is shut." Keyed on
            # _connected (the raw flag, not the derived property) rather than on
            # the link's abandoned flag, because a FAILED reopen has already
            # been through close() and cleared that flag — keying on it would
            # give up after exactly one attempt, which is one better than the
            # zero attempts of the bug and still not a recovery.
            if (not retry or self._relinking or not self._connected
                    or self._link.is_open):
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

    def _not_arrived(self, reason: str,
                     residual: float | None) -> GotoNotArrived:
        """``GotoNotArrived`` for ``slew`` (#860): the fixed ``reason`` and
        the separation from the target, never a coordinate (#140, #166)."""
        where = ("" if residual is None else
                 f"; it reports {residual:.2f} deg from the target")
        return GotoNotArrived(
            f"{self.name}: goto did not arrive ({reason}){where}",
            reason=reason, residual_deg=residual)

    async def _goto_refused_e14(self) -> GotoRefused:
        """``e14`` to ``:MS#`` as ``GotoRefused`` (#860), with the parked
        probe ``_refused_error`` uses (best effort: a failed probe must never
        mask the refusal). ``_refused_error`` itself is unchanged for every
        other command. The code stays in the message, never in ``reason``."""
        parked = False
        try:
            parked = await self.is_parked()
        except Exception:  # noqa: BLE001 - probe is advisory only
            pass
        words = GOTO_E14_PARKED_REASON if parked else GOTO_E14_STATE_REASON
        return GotoRefused(
            f"{self.name}: goto rejected ({words}; reply 'e14')",
            code=lx200.REFUSED, reason=words)

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
        later error carrying a stop that had nothing to do with it.

        Every call also bumps ``_halt_gen``, which ``slew`` reads to tell a
        goto that was halted part way from one that arrived (#860)."""
        self._halt_gen += 1
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
            latlon = _site_latlon()
            if latlon is not None:
                await self._cmd_ack(lx200.smge(*latlon), "site init")
            else:
                _log.warning(
                    "%s: no observing site is saved, so the mount keeps the "
                    "site it already holds rather than being told 0,0. Save "
                    "the site in settings and reconnect to push it.", self.name)
            await self._get("Gps")   # prime state (parked flag)
            await self._get("GU")
            await self._note_reset_signature()
        except Exception:
            await self._link.close()
            self.connected = False
            raise
        self.connected = True

    async def _note_reset_signature(self) -> None:
        """Latch ``position_known`` False when the mount, just (re)opened, reads
        its home pole (#144).

        Shared by ``connect`` and ``_relink``, which is where a reset is noticed:
        a power cycle looks like a dropped link, and the reopen that follows is
        the first chance to read what the mount now believes. Declination only,
        one read: the signature is the pole, and a second command in every
        handshake would be wire traffic with no use (and an RA read would also
        feed the halt-window bookkeeping ``get_position`` owns).

        ADVISORY. A mount that cannot be asked, or answers garbage, has shown no
        evidence of a reset, so it is not accused of one and the connect carries
        on; failing the handshake over a diagnostic read would turn an
        inconvenience into an outage.

        A LATCH, SET HERE AND NEVER CLEARED HERE. A later reopen that reads
        somewhere else says only that the tube has been moved since, and a goto
        from the wrong model moves it somewhere. Clearing is ``sync`` and
        ``trust_position``.

        NO COORDINATES IN THE LINE. The home position IS the pole, so any angle
        read from it is a latitude oracle (#140); the line says what the driver
        concluded and what clears it."""
        try:
            dec = lx200.parse_dec(await self._get("GD"))
        except Exception:    # noqa: BLE001 - advisory read; no answer, no evidence
            return
        at_pole = abs(abs(dec) - 90.0) <= POLE_SIGNATURE_DEG
        was_untrusted = self._position_untrusted
        self._position_untrusted = was_untrusted or at_pole
        if self._position_untrusted and not was_untrusted:
            # THE ACTION FIRST, and never a goto (#850). A goto is aimed from
            # the position the mount believes, so with the tube elsewhere it
            # lands somewhere unknown; holding a pad key computes no
            # destination. The WHOLE action, both branches and the pad key,
            # ends inside the UI's 137-char cut with the default name "ZWO
            # AM5" (135 chars). The no-sync-at-home fact is what the bench
            # showed WITH THE TUBE AT HOME, and is said as that.
            bus.log("warning",
                    f"{self.name}: position is unknown. Tube really at home: "
                    "Trust position. If not, hold a pad key to bring it home "
                    "by eye, then Trust position. After that, a sync from a "
                    "solved frame away from the pole refines the pointing. A "
                    "reset makes the mount report home after (re)connecting, "
                    "wherever the tube is; with the tube at home it refused "
                    "every sync on the bench",
                    "mount")

    async def trust_position(self) -> None:
        """The operator says the tube is physically where the mount reports it
        (in practice: "I drove it to its home position by eye"), which clears
        ``position_known`` without a sync. See ``Telescope.trust_position``."""
        if self._position_untrusted:
            bus.log("info",
                    f"{self.name}: position trusted on the operator's word",
                    "mount")
        self._position_untrusted = False

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
            # Wrap-safe (#860): a halted mount reading 23:59:59 then 00:00:00
            # is still, not 360 degrees of slew.
            if prev is not None and _moved_deg(prev, (ra, dec)) < SETTLE_DEG:
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
        if self._position_untrusted:
            # #133's second finding, reproduced 2026-09-23: after a reset the
            # mount believes it is already home, so ``:hP#`` completed in about
            # two seconds with no slew and ``POST /api/mount/home`` logged
            # "mount homed" regardless. Homing does not re-establish a position
            # the mount does not know (only a sync or the operator can), so this
            # says so rather than leaving the success line unchallenged. The
            # same order as the latch line (#850): the whole action first and
            # inside the 137-char cut (125 chars with "ZWO AM5"), and no goto
            # while the position is unknown.
            bus.log("warning",
                    f"{self.name}: home sent. Tube really at home: Trust "
                    "position. If not, hold a pad key to bring it home by "
                    "eye, then Trust position. After that, a sync from a "
                    "solved frame away from the pole refines the pointing. "
                    "The tube may not have moved: a reset mount believes it "
                    "is already at home, so home moves nothing and its "
                    "position stays unknown; with the tube at home it refused "
                    "every sync on the bench",
                    "mount")

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
        #
        # THE WHOLE ATTEMPT TAKES _pulse_park_lock (WP-18, #342's other open
        # item). pulse_guide takes the same lock, so a park can never land
        # while a pulse is mid-flight on the wire, and a pulse can never start
        # once a park has begun. Without it a guide pulse's own :Te# (the east
        # strategy's tracking resume) could land in the exact window this
        # method just spent clearing with :Td#, putting tracking back on right
        # before :hP# -- the one state :hP# is a documented silent no-op
        # against -- and the two could equally just interleave their bytes on
        # one shared serial port.
        async with self._pulse_park_lock:
            await self._drain_halt()

            # STOP TRACKING, AND VERIFY IT. The old code wrapped this in a bare
            # ``except Exception: pass``. The instinct was right — a mount that
            # cannot stop tracking must still get its park attempt, because
            # this path is the last thing standing between the sun and the
            # optics — but swallowing the failure ALSO threw away the
            # knowledge that the park about to be sent was the known-silent
            # one. Retry, then let the failure inform the error at the end
            # rather than vanish.
            tracking_off = await self._tracking_off_verified()
            # COMPLETION SIGNAL: the parked flag, which is the
            # hardware-verified one (:Gps# flips to '2' ~1s after :hP#). It is
            # trustworthy at every call site because no call site reaches here
            # with the mount already parked: ``park`` short-circuits on that,
            # and ``find_home`` unparks first because a parked AM5 refuses
            # :hP# outright.
            #
            # An earlier version tried to handle a call with the mount already
            # parked by watching for the position to stop changing instead.
            # That was solving a problem the wrong ordering had created, and
            # it could not work: a mount that never moved reports a perfectly
            # stable position, so a refused :hP# read as a completed home.
            if await self._send_park_and_wait(PARK_WAIT_S):
                return

            # ONE RETRY, and only because the first failure is diagnostic
            # rather than mysterious: a :hP# that goes unanswered for
            # PARK_WAIT_S with tracking still on IS the documented silent
            # no-op. Re-assert tracking-off now that the halt window is long
            # over, and send it again. A mount that ignores the second one has
            # a real problem worth reporting; a mount that only ever needed
            # the drive stopped is parked.
            still_tracking = not await self._tracking_off_verified()
            if await self._send_park_and_wait(PARK_WAIT_S):
                return
            why = (" — tracking is still on, and this mount accepts :hP# and "
                   "does nothing while it is" if still_tracking or not
                   tracking_off else "")
            raise DeviceError(
                f"{self.name}: park did not complete within "
                f"{PARK_WAIT_S * 2:.0f}s across two attempts (mount still "
                f"reports unparked){why}")

    async def _stop_tracking_for_park(self) -> None:
        """Send ``:Td#`` unconditionally, right before every ``:hP#`` (WP-18,
        #342's other open item, backlog ruling: "Send `:Td#` every time
        before `:hP#`").

        NOT GATED on a prior ``get_tracking()`` read, unlike
        ``_tracking_off_verified`` above this method's only caller. A
        read-then-send has a window, and ``_send_park_and_wait``'s own
        re-send (S4 orchestrator ruling 9) sits right inside it: a guide
        pulse's own ``:Te#`` (the east strategy's resume, see ``pulse_guide``)
        can land between the last tracking check and the ``:hP#`` that
        follows, putting tracking back on right before the one command
        ``:hP#`` is a documented silent no-op against. Paying for an
        idempotent ``:Td#`` on every send — the first and the re-send alike —
        removes that window instead of narrowing it. ``_pulse_park_lock``
        (held by the whole of ``_park_now``) closes the other half: a NEW
        pulse cannot start once a park has begun, so this is the defence for
        a pulse that was already mid-flight when the lock was taken.

        Best-effort, like ``_tracking_off_verified`` beside it: a mount that
        will not ack ``:Td#`` must still get its park attempt, because this
        is the last thing standing between the sun and the optics."""
        try:
            await self._cmd_ack("Td", "tracking off")
        except Exception:  # noqa: BLE001 - the park attempt must still go out
            pass

    async def _send_park_and_wait(self, timeout_s: float) -> bool:
        """One ``:hP#`` and a bounded poll of the parked flag. True when parked.

        A LOST ``:hP#`` IS SENT AGAIN WITHIN SECONDS (S4 orchestrator ruling
        9, #342). ``:hP#`` is fire-and-forget, so a park the mount throws away
        (a guide pulse's move still on the wire when it lands, #311) looks
        exactly like one it took, and this poll used to find out only at
        ``timeout_s``: 60 s before the retry in ``_park_now``, with the roof
        waiting on this park in the rain, and 60 s inside ``park()`` where the
        engine's "park again at once" (S3 orchestrator ruling 3) can never
        reach it. So for its first ``PARK_NOOP_DETECT_S`` the poll also
        watches for motion, and a mount still neither moving nor parked by
        then gets the command once more. The attempt then runs on to the SAME
        deadline: the re-send buys time inside the attempt and never lengthens
        it, so a mount that ignores everything still fails in ``_park_now``'s
        two ``PARK_WAIT_S`` attempts, as it always did.

        THE RE-SEND IS ONLY THE COMMAND AGAIN, which is all ruling 9 asks. It
        recovers a park lost to something that has since passed. It does NOT
        recover one lost because tracking is back on (an east pulse ends with
        ``:Te#``): the second ``:hP#`` meets the same documented no-op, and
        the recovery there is still ``_park_now``'s second attempt, which stops
        tracking first. #311's other two fixes, an unconditional ``:Td#``
        before the park and a park that takes the pulse lock, are not part of
        the ruling and are not built here.

        "MOVING" IS THE POSITION CHANGING, from the read taken as the command
        goes out, by more than ``SETTLE_DEG``: the one observable this driver
        already trusts to mean motion (``slew()``'s settle, ``_note_halt``).
        NOT ``is_slewing()``, which is ``slew()``'s own flag around a goto and
        is never set by ``:hP#`` -- polling it would call every real park a
        no-op and re-send into a moving mount. NOT ``:GU#`` either: the
        protocol doc records its slewing and parked bits as unverified against
        live states.

        A RE-SEND NEEDS EVIDENCE. Only two position reads that both answered
        and agree can prove the mount still; a read that fails proves nothing,
        so a mount whose position will not come back is never re-sent, and
        falls back to the retry ``_park_now`` has always made. A failed read
        does not fail the park either: the parked flag decides that.

        EVERY ``:hP#`` HERE IS PRECEDED BY AN UNCONDITIONAL ``:Td#``
        (``_stop_tracking_for_park``, #342's other open item) — including the
        re-send below, which used to send only the bare command again."""
        await self._stop_tracking_for_park()
        await self._request("hP", reply="none")
        loop = asyncio.get_running_loop()
        sent_at = loop.time()
        deadline = sent_at + timeout_s
        ref = await self._park_probe()      # where the mount stood at :hP#
        watching = True                     # until it moves or is re-sent
        while loop.time() <= deadline:
            await asyncio.sleep(PARK_POLL_S)
            if await self.is_parked():
                return True
            if not watching:
                continue
            now = await self._park_probe()
            if now is None:
                continue                    # no evidence either way
            if ref is None:
                ref = now                   # the first read that answered
            elif _moved_deg(ref, now) >= SETTLE_DEG:
                watching = False            # slewing: the park took
            elif loop.time() - sent_at >= PARK_NOOP_DETECT_S:
                watching = False
                bus.log("warning",
                        f"{self.name}: the park command has not moved the "
                        f"mount in {loop.time() - sent_at:.0f}s and it does "
                        f"not report parked, so the mount looks to have "
                        f"dropped it; sending it once more", "mount")
                await self._stop_tracking_for_park()
                await self._request("hP", reply="none")
        return False

    async def _park_probe(self) -> tuple[float, float] | None:
        """The mount's position for the park's motion check, or None when it
        will not say. Only the driver's own error is absorbed: a mount that
        will not answer still gets its park, and anything else is a bug."""
        try:
            return await self.get_position()
        except DeviceError:
            return None

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

    async def destination_pier_side(self, ra_hours: float,
                                    dec_deg: float) -> PierSide:
        """Which side this mount would land on after slewing to ``ra_hours``.

        WHY THIS EXISTS AT ALL. ``safety.enforce_pier_limits`` was switched on
        by the operator on 2026-09-11 and guarded NOTHING, because
        ``engine._enforce_mount_floor`` gates the whole pier check on
        ``reports_destination_pier_side`` and only the simulator and the Alpaca
        bridge ever set it. The toggle was in the Settings panel, answering
        True over the API, and inert on the one mount this rig owns. A safety
        control that reports itself armed while doing nothing is worse than an
        absent one, so either this method exists or that toggle should not.

        The AM5 has no ``DestinationSideOfPier`` command -- ``:Gm#`` reports
        where the tube is NOW and nothing more -- so the destination side is
        predicted from hour-angle geometry
        (:func:`coords.pier_side_for_hour_angle`), the same single rule the
        engine and the simulator read.

        AND IT IS CHECKED BEFORE IT IS TRUSTED. A prediction from a convention
        is only worth as much as the convention, and an INVERTED one would hand
        the pre-slew guard a confident wrong answer -- the failure mode that
        costs a tube. So the rule is first applied to where the mount is
        pointing right now and compared against what ``:Gm#`` actually reports.
        They agree: the convention holds for this mount at this moment, and the
        same rule is applied to the destination. They disagree: NOBODY KNOWS
        which of the two is right, and the honest answer is ``UNKNOWN``, which
        the guard passes.

        That disagreement is not a hypothetical. It is exactly the state of a
        mount that has crossed the meridian and has not flipped -- 2026-09-10/11
        for four hours -- where geometry says "east", the mount says "west",
        and a slew guard's opinion is worth nothing anyway. The invariant that
        handles THAT is the engine's flip-owed gate, not this.
        """
        try:
            measured = await self.pier_side()
        except DeviceError:
            return PierSide.UNKNOWN
        if measured is PierSide.UNKNOWN:
            return PierSide.UNKNOWN     # never predict from a mount that will not say
        # The import sits OUTSIDE the try, deliberately. It was inside, spelt
        # `..config` instead of `...config`, and the `except Exception` below
        # turned that ImportError into a perfectly plausible UNKNOWN on every
        # single call -- a prediction that never worked, reported as a mount
        # that would not say. A wrong import is a programming error and must
        # crash; only the DEVICE reads below it are allowed to degrade.
        # NO SITE, NO PREDICTION (#24). The self-check below compares the
        # rule against `:Gm#` at the CURRENT position, and a constant hour-angle
        # error from the wrong longitude can pass it there and still flip the
        # answer for a destination across the meridian. UNKNOWN is what the
        # guard already passes.
        from ...site_gate import site_lat_lon
        latlon = site_lat_lon(config_store.cfg().site)
        if latlon is None:
            return PierSide.UNKNOWN
        lon = latlon[1]
        try:
            ra_now, _dec_now = await self.get_position()
        except Exception:               # noqa: BLE001 - a failed probe is UNKNOWN
            return PierSide.UNKNOWN
        now = time.time()
        predicted_now = coords.pier_side_for_hour_angle(
            coords.hour_angle_h(ra_now, lon, now))
        if predicted_now != measured.value:
            return PierSide.UNKNOWN
        return PierSide(coords.pier_side_for_hour_angle(
            coords.hour_angle_h(float(ra_hours), lon, now)))

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
        """Goto, and wait until the mount has ARRIVED (#860).

        AM5 moves are fire-and-forget, so polling is the only completion
        signal. ARRIVED is two things at once: the report is still (two
        consecutive poll-to-poll steps under ``SETTLE_DEG``, wrap-safe through
        ``_moved_deg``) AND it is within ``GOTO_ARRIVE_DEG + GOTO_DRIFT_DEG_S
        x elapsed`` of the commanded target by angular separation. Still but
        short is not an arrival: polling goes on until the mount arrives, or
        has been STUCK for ``GOTO_STALL_S`` (``GotoNotArrived``, stalled).
        Stuck is two things at once: every step in the window under
        ``SETTLE_DEG`` AND the residual fallen by less than
        ``GOTO_PROGRESS_DEG`` across it. A slow final approach is progress;
        a fast excursion away from the target (a pier-flip swing) is not
        still. A whole-mount halt sent while the goto runs (``_halt_gen``)
        raises ``GotoNotArrived`` (stopped) unless the mount is already at
        the target. The ``SLEW_TIMEOUT_S`` deadline is unchanged and still a
        plain ``DeviceError``. Cancel-safe: any abnormal exit, these raises
        included, halts the mount with ``:Q#`` first.

        ``:MS#`` is sent once, never re-sent after a reopen: a mount that took
        the goto and is slewing would answer a re-send ``e14`` and leave the
        first goto unwatched. A link failure on it halts best-effort and
        raises ``GotoNotArrived`` (``GOTO_LINK_REASON``) with the same
        message as before: before ``retry=False`` a dropped exchange was
        re-sent and the goto went on, so a caller that re-measures (the
        centring loop) must not lose the run to one dropped reply now.

        ``e14`` is ``GotoRefused`` with the parked probe's words, like ``e6``.
        No message carries a coordinate; a separation is allowed."""
        await self._set_target(ra_hours, dec_deg)
        # Snapshot BEFORE :MS#: a stop that lands while :MS# is in flight
        # belongs to this goto.
        halts_at_start = self._halt_gen
        try:
            # retry=False: a resend after a reopen could reach a mount that is
            # already slewing to the first :MS# (it answers e14 and the first
            # goto goes unwatched) - the #850 precedent for :CM#.
            reply = await self._request("MS", reply="ack", retry=False)
        except LinkError as exc:
            # This raw ``request`` was the one ack-class send in the driver that
            # bypassed _cmd_ack, so a silent mount here escaped as a LinkError —
            # not a DeviceError — and the API's ``except DeviceError`` handlers
            # let it through as a 500 instead of a 409. A second goto fired
            # while the first is still settling lands exactly here.
            #
            # Built BEFORE the halt is noted, so the words are today's (the
            # wire truth when idle): ``_link_error`` reads ``_halting``.
            err = self._link_error("goto", exc)
            # The goto may have started before the link went: halt it. The
            # :Q# send keeps the default retry, so a dropped port is reopened
            # for it; :Q# on an idle mount is harmless.
            self._note_halt()
            try:
                await self._request("Q", reply="none")
            except Exception:  # noqa: BLE001 - halt is best-effort here
                pass
            # A DeviceError subclass with today's message, so every existing
            # handler and its text are unchanged; the type lets the centring
            # loop solve where the mount is instead of ending the run.
            raise GotoNotArrived(str(err), reason=GOTO_LINK_REASON,
                                 residual_deg=None) from exc
        if reply == lx200.REFUSED:
            raise await self._goto_refused_e14()
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
            # The MESSAGE keeps the words and the code; the REASON is fixed
            # words only, because the resume ladder makes it a hold reason.
            raise GotoRefused(
                f"{self.name}: goto rejected ({words}; reply {reply!r})",
                code=reply, reason=_goto_refusal_reason(reply))
        self._slewing = True
        try:
            loop = asyncio.get_running_loop()
            started = loop.time()
            deadline = started + SLEW_TIMEOUT_S
            # Read the module constants per call so a test can patch them.
            stall_polls = max(2, math.ceil(GOTO_STALL_S / SETTLE_POLL_S))
            # The residuals across the last ``stall_polls`` steps (one more
            # residual than steps). A ``None`` (unmeasurable) is no progress.
            window: collections.deque[float | None] = collections.deque(
                maxlen=stall_polls + 1)
            prev: tuple[float, float] | None = None
            stable = 0
            while True:
                if loop.time() > deadline:
                    raise DeviceError(
                        f"{self.name}: slew failed to settle within "
                        f"{SLEW_TIMEOUT_S:.0f}s — halted (:Q#)")
                await asyncio.sleep(SETTLE_POLL_S)
                ra, dec = await self.get_position()
                off = _goto_residual_deg(ra_hours, dec_deg, ra, dec)
                allow = (GOTO_ARRIVE_DEG
                         + GOTO_DRIFT_DEG_S * (loop.time() - started))
                arrived = off is not None and off <= allow
                if prev is not None:
                    stable = (stable + 1
                              if _moved_deg(prev, (ra, dec)) < SETTLE_DEG
                              else 0)
                    # Still AND at the target. An arrived mount returns even
                    # if a halt landed this very poll: the report is there.
                    if stable >= 2 and arrived:
                        return
                prev = (ra, dec)
                window.append(off)
                if self._halt_gen != halts_at_start:
                    # Someone else halted the mount (stop(), the manual-move
                    # deadman): no wait for stillness, it will not arrive.
                    raise self._not_arrived(GOTO_STOPPED_REASON, off)
                # stable >= stall_polls means the window is full and every
                # step in it was small; it is a stall only if the residual
                # did not fall by GOTO_PROGRESS_DEG across it.
                if stable >= stall_polls and not _made_progress(window):
                    raise self._not_arrived(GOTO_STALLED_REASON, off)
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
        """Tell the mount it points at ``(ra_hours, dec_deg)``, and PROVE it
        took it by reading the position back (#850).

        WHY A READ-BACK. On 2026-10-07 three centring syncs of 2.2 to 2.8 deg
        "changed nothing": this method treated any reply but ``e14`` as
        success, the hub logged "solved & synced", the next goto was zero
        length, and the run imaged the wrong field for hours (#852). The reply
        itself was thrown away, so what the mount said that night is unknown.
        On the bench the next day (real AM5N, fw 1.8.8) the AM5 answered
        ``N/A`` to every sync away from the pole and its ``:GR#``/``:GD#`` then
        read the synced coordinates within 0.002 deg; with the tube at the
        HOME position every sync was refused: all but one answered ``e11``,
        and one 5 deg
        sync answered ``N/A`` and moved nothing. So the reply is a hint and
        the read-back is the test.

        THE RULES. Any ``eNN`` reply is a refusal (``SyncRefused``), and the
        position is still read back once, best effort, so the refusal can say
        how far the mount's opinion is from the sky (``residual_deg``; the
        resume ladder decides on it). Every other reply, ``N/A`` or anything a
        firmware might say instead, is judged by the read-back alone: up to
        ``1 + SYNC_READBACK_RETRIES`` reads, ``SYNC_READBACK_RETRY_S`` apart,
        because how soon the report updates is unmeasured. A read that FAILS
        (no answer, an unreadable answer, a non-finite position) is retried
        exactly like one that disagrees: the first read goes out milliseconds
        after the reply, a delay the bench never tried, and one link timeout
        must not turn a sync the mount took into an error. A read within
        ``SYNC_VERIFY_DEG`` ends it: taken. Otherwise the LAST read decides:
        it succeeded and still disagrees, ``SyncRefused`` with the reply; it
        failed, ``SyncUnverified``. "Accept only N/A" would be the same
        mistake inverted: a reply standing in for a measurement.

        WHAT IS NOT A REFUSAL. ``SyncUnverified`` is "the mount did not say
        no, and nobody could confirm it said yes", in fixed words:
          - the link fails while the target is set ("before the sync was
            sent"). An ``eNN`` or any other answer but ``1`` to the target is
            a refusal, ``e14`` with the same parked probe as everywhere else,
            and an ``eNN`` there is read back once like one to ``:CM#``;
          - the link fails on ``:CM#``. ``:CM#`` is never resent blind after a
            reopen (``_request(retry=False)``): a mount that restarted under
            the dropped link has lost its target and would sync to whatever it
            holds. So the target is set again and ``:CM#`` sent once more,
            which is harmless if the first one was taken and only its reply
            was lost (same target, same sync). A second failure is
            unverified;
          - the read-back ends on a failed read.
        None of them clears ``position_known``. So this method raises only
        ``SyncRefused`` or ``SyncUnverified``, never a plain ``DeviceError``,
        and ``slew()``'s own use of ``_set_target`` is untouched.

        NO COORDINATES AND NO LINK BYTES IN ANY TEXT. At home the read-back is
        the pole and its RA follows local sidereal time, so either one in a
        message is a site oracle (#140, #166), and a timed-out read can hold
        half of one. Messages carry fixed words, the separation, and the reply
        only when it has the safe short shape (``_sync_reply``). No transport
        error's words are interpolated, and every such raise is ``from None``
        and made outside the handler, so neither ``__cause__`` nor
        ``__context__`` carries them either."""
        await self._set_sync_target(ra_hours, dec_deg)
        raw = await self._send_sync(ra_hours, dec_deg)
        reply = (raw or "").strip().rstrip("#").strip()
        code, said = _sync_reply(reply)

        if lx200.is_error_reply(reply):
            raise await self._sync_refused(
                reply, await self._refused_residual_deg(ra_hours, dec_deg))

        residual = 0.0
        # Why the LAST read failed, in fixed words, or None when it succeeded.
        last_failed: str | None = None
        for attempt in range(SYNC_READBACK_RETRIES + 1):
            if attempt:
                await asyncio.sleep(SYNC_READBACK_RETRY_S)
            try:
                residual = await self._sync_residual_deg(ra_hours, dec_deg)
            except DeviceError as exc:
                # The error's own text is NOT kept: a timed-out GR/GD names
                # what it had received, and an unparseable one quotes it.
                last_failed = ("no answer" if isinstance(exc.__cause__,
                                                         LinkError)
                               else "an unreadable answer")
                continue
            except ValueError:
                # angular_sep_deg refuses a non-finite read, and its own text
                # quotes all four coordinates.
                last_failed = "a position that was not finite"
                continue
            last_failed = None
            if residual <= SYNC_VERIFY_DEG:
                break
        else:
            if last_failed is not None:
                raise SyncUnverified(
                    f"{self.name}: sync not confirmed ({said}): "
                    f"{SYNC_UNVERIFIED_READBACK} (the last read got "
                    f"{last_failed})",
                    code=code, reason=SYNC_UNVERIFIED_READBACK) from None
            raise SyncRefused(
                f"{self.name}: sync refused ({said}) — the mount answered as "
                "if it took the sync, but its reported position is still "
                f"{residual:.2f} deg from the synced coordinates",
                code=code,
                reason=("the mount answered as if it took the sync, but its "
                        "reported position did not move to the synced "
                        "coordinates"),
                residual_deg=residual)

        if reply != lx200.SYNC_ACCEPTED:
            # A firmware that accepts with some other word: the read-back says
            # it worked, and the word is recorded so the next one is known
            # (only when it is safe to quote; see _sync_reply).
            bus.log("info",
                    f"{self.name}: the mount took a sync with {said} (not the "
                    "usual 'N/A'); the position read-back confirmed it",
                    "mount")
        # The one measurement of where the tube really points: a sync is only
        # ever asked with a solved position (or the operator's own), so it
        # re-establishes the frame a reset took away (#144). Only on a sync the
        # read-back PROVED: every refusal above raised before this line.
        self._position_untrusted = False

    async def _set_sync_target(self, ra_hours: float, dec_deg: float) -> None:
        """``:Sr#``/``:Sd#`` for ``sync``, classified the way ``sync`` must
        report (#850): a link failure is ``SyncUnverified`` ("before the sync
        was sent"), an ``eNN`` answer is ``SyncRefused`` through
        ``_sync_refused`` (``e14`` with the parked probe ``_refused_error``
        uses) after the same one best-effort read-back the ``eNN`` answer to
        ``:CM#`` gets, and any other answer but ``1`` is a ``SyncRefused``
        too: the mount would not take the target. ``slew()`` keeps
        ``_set_target``.

        Through ``_request`` with its usual reopen: setting a target twice is
        harmless, so a link dropped before the sync is simply recovered."""
        for cmd in (f"Sr{lx200.format_ra(ra_hours)}",
                    f"Sd{lx200.format_dec(dec_deg)}"):
            ack: str | None = None
            try:
                ack = await self._request(cmd, reply="ack")
            except LinkError:
                pass
            if ack is None:
                raise SyncUnverified(
                    f"{self.name}: sync not confirmed: "
                    f"{SYNC_UNVERIFIED_LINK_BEFORE}",
                    code="", reason=SYNC_UNVERIFIED_LINK_BEFORE) from None
            # Any answer ends a halt window, as in _cmd_ack.
            self._halting = False
            ack = ack.strip().rstrip("#").strip()
            if ack == lx200.ACK_OK:
                continue
            if lx200.is_error_reply(ack):
                # Read back like the eNN answer to :CM#, so the refusal says
                # how far off the mount is when it can, and a caller is never
                # told the position "could not be read back" when nobody
                # tried to read it.
                raise await self._sync_refused(
                    ack, await self._refused_residual_deg(ra_hours, dec_deg))
            code, said = _sync_reply(ack)
            reason = "the mount would not take the sync's target coordinates"
            raise SyncRefused(f"{self.name}: sync refused ({said}) — {reason}",
                              code=code, reason=reason)

    async def _send_sync(self, ra_hours: float, dec_deg: float) -> str | None:
        """``:CM#``, never resent blind after a reopen (#850).

        A review probe dropped the link on ``:CM#`` with a fake that lost its
        target: the old auto-retry reopened, sent ``:CM#`` again with no
        target, and the mount synced to whatever it held. So the first send is
        ``retry=False``; on a link failure the target is set again (which
        reopens a dropped port through ``_request``) and ``:CM#`` goes once
        more, also ``retry=False``, so a third, blind one can never happen.
        If either of those fails, ``SyncUnverified``."""
        try:
            return await self._request("CM", reply="hash", retry=False)
        except LinkError:
            pass
        bus.log("warning",
                f"{self.name}: the link failed during the sync; setting the "
                "target again and sending the sync once more", "mount")
        raw: str | None = None
        failed = False
        try:
            await self._set_target(ra_hours, dec_deg)
            raw = await self._request("CM", reply="hash", retry=False)
        except (LinkError, DeviceError):
            # DeviceError is not optional: ``_set_target`` goes through
            # ``_cmd_ack``, which turns a timeout, a failed reopen and an
            # ``e14`` into a DeviceError carrying the transport's words.
            failed = True
        if failed:
            raise SyncUnverified(
                f"{self.name}: sync not confirmed: "
                f"{SYNC_UNVERIFIED_LINK_DURING}",
                code="", reason=SYNC_UNVERIFIED_LINK_DURING) from None
        return raw

    async def _sync_residual_deg(self, ra_hours: float, dec_deg: float) -> float:
        """Angular separation (deg) between the coordinates a sync asked for
        and the position the mount reports now. Angular, not per-axis: near
        the pole a tiny offset reads as hours of RA, and across 0 h the RA
        wraps. Raises ``DeviceError`` (read failed) or ``ValueError``
        (non-finite read); the RA/Dec themselves never leave this method."""
        read_ra, read_dec = await self.get_position()
        return coords.angular_sep_deg(ra_hours, dec_deg, read_ra, read_dec)

    async def _refused_residual_deg(self, ra_hours: float,
                                    dec_deg: float) -> float | None:
        """One best-effort read-back for an ``eNN`` refusal (to the target or
        to ``:CM#``): ``_sync_residual_deg``, or ``None`` when the read fails.
        A failed read must never mask the refusal we came to report, so it
        leaves the residual unknown and nothing else. No retries: the mount
        has already said no."""
        try:
            return await self._sync_residual_deg(ra_hours, dec_deg)
        except (DeviceError, ValueError):
            return None

    async def _sync_refused(self, reply: str,
                            residual: float | None) -> SyncRefused:
        """Words for an ``eNN`` reply to a sync's commands. Only the codes seen
        on the wire get words: ``e14`` ("refused in current state", with the
        same parked probe ``_refused_error`` uses) and ``e11`` (seen with the
        tube at home on 2026-10-08; the words never say the tube IS at home,
        and are chosen from ``residual``: Trust position first, behind its
        condition, when the mount's opinion is within
        ``SYNC_E11_ELSEWHERE_DEG`` of the sky or unknown, and home by eye
        first when it is further out). Any other
        code is quoted and nothing is invented for it. The reason comes
        straight after the name, so it survives a 140-character log line."""
        lowered = reply.lower()
        if lowered == lx200.REFUSED:
            parked = False
            try:
                parked = await self.is_parked()
            except Exception:  # noqa: BLE001 - probe is advisory only
                pass
            reason = ("mount is parked; unpark first" if parked else
                      "refused in current state — check limits / that a slew "
                      "isn't already running")
        elif lowered == "e11":
            # The read-back was taken before these words (the caller passes
            # it in), so a tube the mount has badly wrong is told to come home
            # by eye, never to Trust position first.
            reason = (SYNC_E11_ELSEWHERE_REASON
                      if residual is not None
                      and residual > SYNC_E11_ELSEWHERE_DEG
                      else SYNC_E11_AT_HOME_REASON)
        else:
            reason = ("the mount refused the sync; no meaning is known for "
                      "this reply")
        code, said = _sync_reply(reply)
        where = ("" if residual is None else
                 f"; its reported position is {residual:.2f} deg from the "
                 "synced coordinates")
        return SyncRefused(
            f"{self.name}: sync refused — {reason} ({said}){where}",
            code=code, reason=reason, residual_deg=residual)

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
            rate = (_PULSE_DEC_RATE_DEG_S if direction.lower().startswith(("n", "s"))
                    else _PULSE_RA_RATE_DEG_S)
            arcsec = rate * 3600 * _PULSE_MAX_MS / 1000
            bus.log("warning",
                    f"{self.name}: pulse {direction} {ms} ms capped to "
                    f"{_PULSE_MAX_MS} ms (~{arcsec:.1f} arcsec at this axis's guide rate)",
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
        deliberately cannot.

        SHARES ``_pulse_park_lock`` WITH ``_park_now`` (WP-18, #342's other
        open item). A pulse and a park are both motion commands on the one
        serial link, and letting them land together risks interleaved bytes
        on the wire, or a pulse's own tracking resume undoing the tracking-off
        a concurrent park just sent. Taking the same lock here means this
        pulse either runs to completion before a waiting park begins, or
        waits here for an already-running park to finish first; direction
        validation stays outside it so a bad direction fails at once."""
        d = direction.lower()[0]
        if d not in "nsew":
            raise DeviceError(f"{self.name}: bad guide direction {direction!r}")
        async with self._pulse_park_lock:
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
                # The thread is still inside abort.wait: tell it to stop the
                # mount NOW, and wait for that OFF the loop (bounded) before
                # propagating.
                pulse.abort.set()
                await asyncio.to_thread(pulse.done.wait,
                                        secs + _PULSE_CANCEL_JOIN_S)
                if pulse.answered:
                    self._halting = False
                if pulse.started and not pulse.stop_sent:
                    # The cancel is not the emergency here; a mount still
                    # moving is.
                    try:
                        await self._send_stop_on_the_loop(stop, what_stop)
                    except Exception:   # noqa: BLE001 - the cancel still wins
                        pass
                raise
            # An ack-class command was ANSWERED: the same evidence _cmd_ack
            # acts on.
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
                # reopen it, so the stop goes out from here -- the mount is
                # moving.
                bus.log("warning",
                        f"{self.name}: could not stop the pulse from the "
                        f"pulse thread (:{stop[0]}# -- "
                        f"{pulse.error or 'no reply'}); sending it again now",
                        "mount")
                await self._send_stop_on_the_loop(stop, what_stop)
                return
            if pulse.stop_reply is not None and pulse.stop_reply != lx200.ACK_OK:
                # ANSWERED, and the answer was no. For the east strategy that
                # means tracking did not resume: the star now drifts east at
                # sidereal rate, which the guider must be told rather than
                # left to infer.
                bus.log("warning",
                        f"{self.name}: could not stop the pulse -- the mount "
                        f"refused :{stop[0]}# (reply {pulse.stop_reply!r})",
                        "mount")
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
