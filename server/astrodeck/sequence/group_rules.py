"""The mosaic group driver's pure decisions (mosaic spec 5.1, 5.3, 5.6 steps 4
and 7, 5.7, 1.6, Appendix A.4; U-01, U-04, #189).

The S2 group driver in ``_run_scheduled`` hops a mosaic's panels in passes.
Every decision it makes that needs no device, no clock and no engine lives
here, so each can be pinned by a test that runs in milliseconds and names the
mutation it catches, and so the engine's own suite can spend its simulator
time on the wiring instead of the arithmetic:

* :class:`PanelDeferred`: the one exception a hop raises when a panel cannot be
  shot now but may be on the next pass (5.6 steps 4, 5 and 7).
* :class:`VisitBound`: when a visit ends, at a round boundary and before each
  frame (5.3).
* :class:`GroupRun`: the spec's ``_GroupRun``, and what each visit's outcome
  does to the panel and its counters (the 5.1 table), with the guide-start
  pass rule (5.6 step 7) and the pass boundary (5.1).
* :func:`meridian_eligibility`: which panels the one-pier-change rule lets
  shoot (5.7).
* the pre-flip idle of 5.7 cost 1 and Appendix A.4.
* :func:`forward_clear_ts`: the 60 s forward scan behind ``group_ready_ts``
  (1.6).
* :func:`angle_decision`: what an angle verdict means for the panel (5.6 step
  4).

NO ENGINE IMPORT. The engine imports this module, never the reverse, and the
constants the engine owns (``FLIP_FRAME_MARGIN_S``, the overhead EMA, the hop
EMA, the plan's flip lead) arrive as arguments. A decision table that reached
into the engine for a number would grade whatever the engine happened to hold,
and its tests would need a hub.

NOTHING HERE IS SITE DATA, AND NOTHING HERE SAYS ANY. The meridian rule takes
hours to each panel's flip point, which are site-derived; its reasons are
words and carry none of those numbers (6.9). The pre-flip idle scales with the
hop and the lead, not with the site (A.4), so it may be shown to every role.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

#: Seconds an all-deferred pass waits before the next one (5.1 pass boundary,
#: item 2). Long enough for the transient faults a deferral names (a cloud
#: over the guide star, a centring solve on a thin field) to have a chance to
#: clear, short enough that a 3x2 at the default cycle loses under a third of
#: one pass. ``max_failed_visits`` bounds how often it can happen per panel.
#: The members wait it out as waiters (`GroupRun.defer_next_pass`), so a
#: ready follower may fill it (#304).
DEFER_WAIT_S = 300.0

#: Seconds between re-evaluations of a panel the reachability verdict tagged
#: ``wait`` (5.1 selection, item 1). A CADENCE, NEVER A DEADLINE: it is how
#: often the scheduler asks again, and the idle-clock park-hold, not this
#: number, bounds how long the mount tracks unwatched.
REACH_RECHECK_S = 60.0

#: Solar seconds per sidereal second: the sidereal day (86164.0905 s) over the
#: solar day. An hour of RA passes the meridian in one sidereal hour, which is
#: this much shorter than a clock hour, so an RA span converts to clock time by
#: this factor (A.4).
SOLAR_PER_SIDEREAL = 0.9972696

#: Seconds after which a CENTRING set-aside expires and the panel is tried
#: once more tonight (#534, H4 orchestrator ruling 2). Three passes over a
#: 3x2 take about 18 minutes, so three centring strikes can land inside the
#: one early hour a target spends low behind a tree or in the haze near the
#: horizon, and before this the rest of the night was lost to that hour (the
#: first rig mosaic, NGC 1499 on 2026-09-29, lost every panel in 18 min
#: although it transited near 04:00). 45 minutes is long enough for the sky
#: to have changed under a panel that failed for the sky's reasons, and a
#: panel that fails again after it is set aside for the rest of the night.
SET_ASIDE_EXPIRY_S = 2700.0

#: Degrees a centring set-aside's panel must have RISEN since it was set
#: aside for the set-aside to expire before ``SET_ASIDE_EXPIRY_S`` (#534).
#: A panel that has climbed this far has left the obstruction or the thick
#: air it failed in, whatever the clock says. The altitudes it compares are
#: SITE-DERIVED: the engine computes both when it asks, from the time the
#: panel was set aside and its coordinates, stores neither, and never says
#: either (6.9). At 40 degrees of latitude nothing rises faster than about
#: 11.5 degrees an hour, so there this half can never beat the 45 minutes;
#: nearer the equator a panel rising in the east can.
SET_ASIDE_RISE_DEG = 10.0

#: Seconds after a set-aside before the rise half of :func:`set_aside_expiry`
#: may answer at all (#564). No latitude rises faster than 15 degrees a
#: clock hour (the line above: a body on the celestial equator, right at the
#: horizon; every other latitude and declination is slower), so
#: ``SET_ASIDE_RISE_DEG`` of climb needs at least this long ANYWHERE.
#: Answering "rise" before it would not be a real sky: it would be a caller
#: handing in two altitudes physics cannot connect that fast, and the rule
#: refuses to trust that instead of reading it as an equatorial site. This
#: also holds the earliest a "rise" answer can ever come to one fixed
#: instant, the same whatever the site, so that instant alone says nothing
#: about it (6.9); an H4-DOC finding the ticket that opened this left open
#: (docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-design.md,
#: 5.1) is that "rise" answering AT ALL, ahead of ``SET_ASIDE_EXPIRY_S``,
#: still means the site is within about 28 degrees of the equator; closing
#: that needs the branch gone outright, which a sibling ticket (#564) leaves
#: to a later call because two tests outside this file (H4's rise case at
#: 5 N, and the spec-claims read of that same 28 degree number) are built on
#: the branch answering early there.
SET_ASIDE_RISE_FLOOR_S = SET_ASIDE_RISE_DEG / 15.0 * 3600.0

#: Seconds a group waits after a pass in which EVERY panel attempted (at
#: least two) failed centring, before it starts the next pass (#534, H4
#: orchestrator ruling 2). That pass says the sky or the geometry is to
#: blame, not the panels, as "when every member rejects, the sky is to
#: blame" (5.1), so no panel is struck, and the group holds, the way a cloud
#: hold waits for the sky rather than giving up on it. Twice
#: ``DEFER_WAIT_S``: whatever hid every panel at once (a cloud bank, a
#: target low behind the trees) takes longer to clear than one panel's
#: guide star.
CENTRING_HOLD_RETRY_S = 600.0


def _finite(name: str, value: Any) -> float:
    """``value`` as a finite float, or ``ValueError``.

    Every comparison with NaN is false, so a NaN reaching any rule below would
    pass or fail it silently, in whichever direction that comparison happened
    to face. A non-finite argument is a caller bug and says so.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a finite number, got {value!r}") from None
    if not math.isfinite(f):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return f


def _nonneg(name: str, value: Any) -> float:
    f = _finite(name, value)
    if f < 0.0:
        raise ValueError(f"{name} must not be negative, got {value!r}")
    return f


def _count(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative int, got {value!r}")
    return value


def _visits(n: int, adjective: str = "") -> str:
    """A count of visits in words, "3 consecutive visits" or "1 consecutive
    visit": ``max_failed_visits`` may be 1, and an alert is read by the
    operator."""
    return f"{n} {adjective}visit{'' if n == 1 else 's'}"


# ---------------------------------------------------------------- PanelDeferred

#: The kind a failed guider start carries. It is the one kind a rule reads:
#: the guide-start pass rule (5.6 step 7) asks whether every attempted panel
#: failed with it.
GUIDE_START = "guide_start"

#: The kind a member's visit carries when its guiding was lost mid-visit and
#: the #72 recovery bound gave up on it (#303, S3 orchestrator ruling 6; spec
#: 5.6 step 7). A single target takes the rig's ``guiding_action`` there:
#: abort ends the run, skip drops it, warn shoots on unguided. Each leaves a
#: hole in a mosaic, or tiles it with trailed frames, and the next hop
#: restarts guiding anyway, so a panel defers instead, whatever the action
#: says, as a failed guide start does. It is NOT counted by the guide-start
#: pass rule: the hop's start worked, so the guider was not dead, and the
#: visit is counted at once like any other deferral.
GUIDE_LOST = "guide_lost"

#: The kind a member's visit carries when a gate ended it the way it ends a
#: single target, with a plain ``StopTarget`` (#316, S3 orchestrator ruling
#: 5): an autofocus that failed under ``af_failure_action = skip``, a mount
#: that would not track again after its one recovery, a flip still owed at
#: the end of its hold, the mount's floor reached during a cloud hold. (A
#: guider the #72 recovery bound gave up on under skip was one until #303:
#: the recovery now defers a member itself, as :data:`GUIDE_LOST`, under
#: every action.) A single target is dropped for the run on any of them. A
#: panel is not: each can clear by the next pass, a rotation later (a sweep
#: beaten by a passing cloud, a limit the sky has carried the panel away
#: from), and dropping the panel leaves a hole in the mosaic for the rest of
#: the night, which the owner's ruling 5 says not to do when we do not have
#: to. So it defers, shares
#: ``max_failed_visits`` with every other kind, and is set aside tonight,
#: with the warning alert, only after that many consecutive passes. Two
#: stops never defer and are not this kind: the frozen stop window (every
#: panel shares it, and the all-closed path ends them all) and a panel below
#: its own floor (``FloorStop``, set aside tonight at once).
TARGET_STOP = "target_stop"

#: The kind a member's hop carries when it did not centre under
#: ``require_centred`` (5.6 step 4). Since H4 a rule reads it, as the guide
#: rule reads :data:`GUIDE_START`: the centring pass rule
#: (:func:`centring_pass_verdict`) asks whether every attempted panel failed
#: with it, and a streak made only of it is the one set-aside that expires
#: (:func:`set_aside_expiry`), both #534.
CENTRING = "centring"

#: The kind a member's hop carries when a solve it needed could not RUN, for
#: a reason that clears by itself: the centring result carries
#: ``solve_transient`` (#532, contract 1 of the H4 orchestrator; the hub
#: sets it, for instance, when another process held the solve frame's file
#: open, WinError 32), beside a centring miss or beside the rotate loop's
#: ``rotation_skipped``. Nothing about the panel or the sky failed, so it
#: counts toward neither ``failed`` nor the centring pass rule. It is still
#: a deferral for the pass boundary (``deferred_this_pass``): a pass whose
#: only visits were these waits ``DEFER_WAIT_S`` and tries again, where a
#: pass of no exposures and no deferrals would set the whole mosaic aside.
SOLVE_TRANSIENT = "solve_transient"

#: Every kind a deferral may carry. A closed set, because the guide rule
#: matches on a spelling: a deferral spelt ``"centering"`` or ``"guide-start"``
#: by a later author would otherwise pass as an unknown kind the rule silently
#: ignores. The hop raises:
#:
#: - ``"centring"`` (:data:`CENTRING`): ``centered: False`` under
#:   ``require_centred`` (5.6 step 4)
#: - ``"solve_transient"`` (:data:`SOLVE_TRANSIENT`): the same miss, or a
#:   ``rotation_skipped``, when the centring result says a solve could not
#:   run for a transient reason (#532)
#: - ``"rotation"``: ``rotation_skipped`` or ``rotation_unavailable`` with
#:   ``rotate`` set (5.6 step 4)
#: - ``"angle"``: the sky angle is off, or was not measured, in a case that
#:   defers (:func:`angle_decision`)
#: - ``"pier_side"``: the side read after the goto contradicts the group's
#:   (5.6 step 5)
#: - ``"guide_start"``: the plan asks for guiding and the start failed or no
#:   guider is connected (5.6 step 7)
#: - ``"guide_lost"`` (:data:`GUIDE_LOST`): a guiding loss mid-visit that the
#:   #72 recovery bound gave up on, raised by the frame loop's recovery
#:   (5.6 step 7, S3 orchestrator ruling 6)
#:
#: and the group driver makes ``"target_stop"`` (:data:`TARGET_STOP`) from a
#: plain ``StopTarget`` raised anywhere in the visit, at the hop or in the
#: frame loop.
DEFERRAL_KINDS = frozenset({CENTRING, "rotation", "angle", "pier_side",
                            GUIDE_START, GUIDE_LOST, TARGET_STOP,
                            SOLVE_TRANSIENT})


class PanelDeferred(Exception):
    """This panel cannot be shot now; try it again on the next pass (5.1 table,
    row 4).

    Raised by the hop's group checks, and made by the group driver from a
    plain ``StopTarget`` a gate raised in the visit (:data:`TARGET_STOP`,
    #316). It is NOT a :class:`StopTarget` (which drops a single target for
    the run) and never a safety abort: the group driver catches it per visit,
    marks the panel visited, and counts it toward ``max_failed_visits``
    (:meth:`GroupRun.visit_outcome`). The driver must catch it BEFORE any
    broad ``except Exception`` on the visit path, or a deferral becomes an
    error.

    ``reason`` is the cause in words, as the alert will read it ("guiding did
    not start"). ``last_error`` is what the failing call reported, verbatim,
    because the set-aside alert must carry it (5.1: "names the panel, the
    reason and the last error"). ``kind`` is one of :data:`DEFERRAL_KINDS`.
    """

    def __init__(self, reason: str, *, kind: str, last_error: str = ""):
        if kind not in DEFERRAL_KINDS:
            raise ValueError(
                f"PanelDeferred kind must be one of {sorted(DEFERRAL_KINDS)}, "
                f"got {kind!r}")
        if not str(reason).strip():
            raise ValueError("a PanelDeferred must say why in words")
        self.reason = str(reason)
        self.kind = kind
        self.last_error = str(last_error or "")
        super().__init__(f"{self.reason}: {self.last_error}"
                         if self.last_error else self.reason)


# ------------------------------------------------------------------ VisitBound

@dataclass(frozen=True)
class VisitBound:
    """How long one visit to a panel may last (5.3). Engine-internal, never
    persisted.

    ``passes``: rounds of the panel's cycle per visit, for a group member
    (``TargetGroup.visit_passes``). ``None`` for a bounded follower (1.6),
    whose visit ends only at its deadline or its completion.

    ``min_s``: the visit lasts at least this long, checked at round
    boundaries (``TargetGroup.visit_min_s``). It extends a visit and never
    shortens one: a visit that has run its minimum still owes its passes.

    ``deadline_ts``: end the visit at the first frame boundary that cannot fit
    the next frame. The panel's flip point (5.7) or ``group_ready_ts`` (1.6).

    Both checks sit at step boundaries in ``_run_steps``, never inside
    ``_run_step``, so an exposure is never cut short.
    """

    passes: int | None
    min_s: float = 0.0
    deadline_ts: float | None = None

    def __post_init__(self) -> None:
        if self.passes is not None and (
                isinstance(self.passes, bool) or not isinstance(self.passes, int)
                or self.passes < 1):
            raise ValueError(
                f"passes must be None or an int of at least 1, got {self.passes!r}")
        _nonneg("min_s", self.min_s)
        if self.deadline_ts is not None:
            _finite("deadline_ts", self.deadline_ts)

    def ends_at_round(self, *, rounds: int, elapsed_s: float,
                      complete: bool) -> bool:
        """Whether the visit ends at this round boundary.

        ``rounds`` is the number of rounds finished this visit; ``elapsed_s``
        is measured from the visit's first exposure, so the hop does not eat
        into the visit (5.3). A complete panel always ends its visit.

        BOTH the passes and the minimum must be met. With either alone,
        ``visit_min_s`` would cut a two-pass visit short after one long pass,
        or one pass would end a visit the plan asked to last 20 minutes.
        """
        _count("rounds", rounds)
        elapsed = _finite("elapsed_s", elapsed_s)
        if complete:
            return True
        if self.passes is None:
            # A follower: the round rule never ends it; its deadline does.
            return False
        return rounds >= self.passes and elapsed >= self.min_s

    def next_frame_fits(self, *, now: float, exposure_s: float,
                        overhead_s: float, margin_s: float) -> bool:
        """Whether the next frame ends before the deadline (5.3).

        ``now + exposure + overhead + margin <= deadline``. ``overhead_s`` is
        the engine's per-frame overhead EMA and ``margin_s`` its
        ``FLIP_FRAME_MARGIN_S``: the same budget ``_maybe_meridian_flip`` uses
        for its frame window, so a visit bounded by a flip point ends where
        the flip gate would have drawn the line. The WHOLE frame is counted,
        because the deadline is the moment the frame must be over by, not the
        moment it may start.
        """
        t = _finite("now", now)
        need = (_nonneg("exposure_s", exposure_s)
                + _nonneg("overhead_s", overhead_s)
                + _nonneg("margin_s", margin_s))
        if self.deadline_ts is None:
            return True
        return t + need <= self.deadline_ts


# -------------------------------------------------------------------- GroupRun

VisitActionKind = Literal["remove", "requeue", "set_aside"]


@dataclass(frozen=True)
class VisitAction:
    """What :meth:`GroupRun.visit_outcome` decided for the panel.

    ``remove``: the panel is complete; take it out of ``remaining`` and run its
    ``on_target_complete`` rules. ``requeue``: move it behind the group's
    unvisited members. ``set_aside``: skip it tonight, with a WARNING alert
    carrying ``reason``, a ``Session.set_aside`` record and
    ``reporter.mark_skipped``; it is retried the next night. ``reason`` is the
    decision in words, for the log or the alert.
    """

    action: VisitActionKind
    reason: str


PassBoundary = Literal["set_aside_all", "defer_wait", "next_pass"]


@dataclass(frozen=True)
class PassEnd:
    """What :meth:`GroupRun.close_pass` decided at a pass boundary.

    ``boundary``:

    - ``"guiding_action"``: every panel attempted this pass (at least two)
      failed to start guiding. The rig is to blame for THAT, not a panel
      (#575: a centring miss held from the same pass, above, may still have
      counted or set a panel aside, and ``reason`` says so when it did), and
      the plan's ``guiding_action`` decides, exactly as for a single target:
      abort ends the run, skip sets the group aside
      (:meth:`GroupRun.set_aside_all`), warn shoots the panels unguided. Then
      :meth:`GroupRun.start_pass`.
    - ``"set_aside_all"``: the group anti-spin set every live member aside
      tonight.
    - ``"none_live"``: no member is live any more; every one is complete or
      set aside tonight, the last of them by a failure bound this boundary
      counted. There is nothing to wait for and no pass to start. Without
      this, a group whose last panel was just set aside would read as an
      all-deferred pass and wait ``DEFER_WAIT_S`` for nothing.
    - ``"defer_wait"``: :meth:`GroupRun.defer_next_pass` holds every live
      member until ``now + DEFER_WAIT_S``, and :meth:`GroupRun.start_pass`
      begins the next pass behind that wait (#304: the wait is a state the
      scheduler waits on, never a sleep inside the boundary).
    - ``"next_pass"``: :meth:`GroupRun.start_pass` and re-sort the group's
      slice of ``remaining``.
    - ``"centring_hold"``: every panel attempted this pass (at least two)
      failed centring (#534, :func:`centring_pass_verdict`). The sky or the
      geometry is to blame, no panel is struck, and
      :meth:`GroupRun.defer_next_pass` holds every live member for
      ``CENTRING_HOLD_RETRY_S`` before :meth:`GroupRun.start_pass` begins the
      next pass behind the hold. Decided before the guide rule: a pass in
      which no panel centred made no guide attempt.

    ``set_aside``: ``(target_id, reason)`` for every panel set aside at this
    boundary, each owed its own warning alert and ``Session.set_aside``
    record. ``reason``: the boundary in words, for the log. ``counted``:
    ``(target_id, reason)`` for every held centring failure this boundary
    counted without setting its panel aside, "(1 of 3 consecutive)": the
    count the visit's own line said before centring failures were held
    (#534), said now where it is made.
    """

    boundary: PassBoundary | Literal["guiding_action", "none_live",
                                     "centring_hold"]
    set_aside: tuple[tuple[str, str], ...]
    reason: str
    counted: tuple[tuple[str, str], ...] = ()


def guide_start_pass_verdict(attempted: int, failed: int) -> Literal["rig", "panel"]:
    """Whose fault is a pass's failed guide starts (5.6 step 7)?

    ``attempted`` counts the panels whose hop reached the guider start this
    pass, and ``failed`` those whose start failed. At least two attempted and
    every one failed is ``"rig"``: a guider that fails on every panel is the
    rig's fault, not a panel's, the same reasoning as "when every member
    rejects, the sky is to blame" (5.1). Anything else is ``"panel"``: one
    failure out of one attempt proves nothing about the guider, and a failure
    beside a start that worked is that panel's.

    WHY IT MATTERS: charging a dead guider to the panels costs three passes of
    hops (about 109 min on a 3x2) before the panel rule sets them all aside;
    charging it to the rig costs one pass (about 33 min), after which
    ``guiding_action`` decides.
    """
    _count("attempted", attempted)
    _count("failed", failed)
    if failed > attempted:
        raise ValueError(
            f"failed ({failed}) cannot exceed attempted ({attempted})")
    if attempted >= 2 and failed == attempted:
        return "rig"
    return "panel"


def centring_pass_verdict(attempted: int,
                          failed: int) -> Literal["sky", "panel"]:
    """Whose fault is a pass's failed centrings (#534, H4 orchestrator ruling
    2)? The guide-start rule's shape (:func:`guide_start_pass_verdict`), for
    the hop's first check.

    ``attempted`` counts the panels VISITED this pass, each once, whatever
    their visit came to, save one whose solve could not run
    (:data:`SOLVE_TRANSIENT`, #532), which the rule leaves out; ``failed`` counts
    those whose hop did not centre (:data:`CENTRING`). A visit that shot a
    frame, or deferred for anything that comes after the centring (the
    rotator, the angle, the pier side, the guider), centred, or its hop
    would have stopped at the miss. At least two attempted and every one
    failed is ``"sky"``: the sky or the geometry is to blame, not the
    panels (a cloud bank, a target low behind the trees, the haze near the
    horizon), the same reasoning as "when every member rejects, the sky is
    to blame" (5.1). Anything else is ``"panel"``: one miss out of one
    attempt proves nothing about the sky, and a miss beside a panel that
    centred is that panel's.

    WHY IT MATTERS: the first rig mosaic (NGC 1499, 2026-09-29) started low
    in the east behind an obstruction, every panel failed centring, and the
    three-strike rule set all six aside in 18 minutes, although the target
    rose clear within the hour. Charged to the sky, such a pass strikes no
    panel and the group waits ``CENTRING_HOLD_RETRY_S`` and tries again.
    """
    _count("attempted", attempted)
    _count("failed", failed)
    if failed > attempted:
        raise ValueError(
            f"failed ({failed}) cannot exceed attempted ({attempted})")
    if attempted >= 2 and failed == attempted:
        return "sky"
    return "panel"


ExpiryCause = Literal["time", "rise"]


def set_aside_expiry(*, now: float, set_at: float,
                     alt_at_set: float | None = None,
                     alt_now: float | None = None,
                     expiries: int = 0) -> ExpiryCause | None:
    """Has a CENTRING set-aside expired, and why (#534, H4 orchestrator
    ruling 2)? ``None`` while it holds.

    - ``"time"``: ``SET_ASIDE_EXPIRY_S`` have passed since ``set_at``.
    - ``"rise"``: the panel centre has risen at least ``SET_ASIDE_RISE_DEG``
      since ``set_at`` (``alt_now - alt_at_set``), before the time is up, and
      not before ``SET_ASIDE_RISE_FLOOR_S`` have passed (#564): no latitude
      rises that far that fast, so an earlier answer would be a bad altitude
      pair, not a real sky. Asked only when both altitudes are given: with no
      site saved there is no altitude, and the time half alone applies. A
      panel that is setting never expires this way.
    - never, when ``expiries`` is at least 1: AT MOST ONE EXPIRY PER PANEL
      PER NIGHT. A panel that is tried again after its expiry and struck out
      again failed after the sky had its chance to change, so the second
      set-aside is for the rest of the night; expired again it would be
      retried every 45 minutes all night, three hops each time, which is the
      starving panel S2's rule exists to stop (#180).

    The time half wins when both hold, because it is the one that is not
    site-derived: a line saying why the set-aside expired then says nothing
    about the site (6.9).

    Only a centring set-aside asks this. The engine decides that (a streak
    of centring failures and nothing else, :meth:`GroupRun.set_aside_kind`);
    a floor, a reject guard, a pier refusal or any other set-aside never
    expires, since waiting does not change what set it aside, or the night
    already waited for it.
    """
    t = _finite("now", now)
    t0 = _finite("set_at", set_at)
    if _count("expiries", expiries) >= 1:
        return None
    # The same sum the engine wakes at (``set_at + SET_ASIDE_EXPIRY_S``), so
    # a wake at that instant finds the set-aside expired: written as
    # ``now - set_at``, the float difference of two unix times can come out
    # an ulp short of the constant and the wake would find nothing to do.
    if t >= t0 + SET_ASIDE_EXPIRY_S:
        return "time"
    if alt_at_set is not None and alt_now is not None:
        # Both altitudes are checked for finite-ness whether or not the
        # floor has passed: a NaN is a caller bug the moment it arrives, not
        # only once the clock would otherwise let the rise half answer.
        rise = _finite("alt_now", alt_now) - _finite("alt_at_set", alt_at_set)
        if t >= t0 + SET_ASIDE_RISE_FLOOR_S and rise >= SET_ASIDE_RISE_DEG:
            return "rise"
    return None


def no_guider_defers(*, live: int, require_guiding: bool) -> bool:
    """Does a member whose plan asks for guiding, finding NO GUIDER CONNECTED
    at its hop, defer (5.6 step 7), or follow the plain-target rule (#315, S3
    orchestrator ruling 6)?

    ``live`` counts the members THIS PASS CAN VISIT, this one included, so it
    is at least 1 (:meth:`GroupRun.visitable`, the engine's
    ``_live_panels``): the live members visited in the pass or let through
    by one of its selections. A live member held at every selection of the
    pass, behind a limit, by the meridian rule or by its own gating, makes
    no attempt in it and is not counted (the #315 follow-up, S3 orchestrator
    ruling 6). ``require_guiding`` is the rig's escalation setting.

    Two or more, or guiding required: defer, S2's rule. With one and guiding
    optional: no, the panel is shot unguided with the plain target's
    warning, exactly as the same target outside a group is.

    WHY THE LAST PANEL IS DIFFERENT. A deferral is a bet that the guider is
    the panel's problem, and the guide-start pass rule settles the bet: at
    least two panels tried and every one failed is the rig's fault, and
    ``guiding_action`` decides (:func:`guide_start_pass_verdict`). One live
    panel can never make two attempts in a pass, so that verdict can never
    come. With guiding optional and no guider there at all, such a panel
    deferred on every hop, waited ``DEFER_WAIT_S`` after each pass of
    nothing, and was set aside after ``max_failed_visits`` passes: about 15
    minutes, then the night, lost to a guider the rig said it could do
    without. A missing guider is a fact about the rig, not about the panel.

    THE SAME HOLDS FOR THE LAST REACHABLE PANEL. A member waiting all pass
    below the floor, past the meridian or before its own start gate is live,
    and makes no attempt either, so counted it held the one panel the pass
    could visit to the same three deferrals and the same lost night (the
    #315 follow-up, found in S3's safety review).

    A guider that is connected and fails to start still defers, whatever
    ``live`` is: that failure can be the panel's guide star. Required
    guiding still defers too: the ruling is for guiding the rig marked
    optional, where the plain-target rule shoots unguided.
    """
    live = _count("live", live)
    if live < 1:
        raise ValueError("the member asking is live, so live is at least 1")
    return bool(require_guiding) or live >= 2


def pass_boundary(exposures: int, deferrals: int) -> PassBoundary:
    """What follows the end of a pass (5.1 pass boundary).

    - No exposures and no deferrals: ``"set_aside_all"``. A full pass shot
      nothing and nothing said why it could not, so another pass would spin.
    - No exposures, some deferrals: ``"defer_wait"`` (``DEFER_WAIT_S``), then a
      new pass. ``max_failed_visits`` bounds this per panel.
    - Otherwise ``"next_pass"``.

    EXPOSURES, accepted plus rejected, never accepted frames: a clouded pass of
    rejects is a pass that shot, and ending the mosaic on it would hand one
    cloud the rest of the night. The reject guards own clouds.
    """
    _count("exposures", exposures)
    _count("deferrals", deferrals)
    if exposures == 0 and deferrals == 0:
        return "set_aside_all"
    if exposures == 0:
        return "defer_wait"
    return "next_pass"


class GroupRun:
    """The group driver's per-group state for one run: the spec's
    ``_GroupRun`` (5.1). Never persisted; a resume recomputes it from the
    ledger, except ``set_aside``, which the engine also writes to
    ``Session.set_aside`` so a same-night crash-resume does not retry those
    panels, and ``flipped``, which it writes with the group's side to
    ``Session.group_pier`` so the same restart keeps one pier change a night
    (#312).

    ``members`` maps each panel's target id to its label ("1-2"), in the
    group's order; the label is what every sentence names.

    Fields (the spec's, plus the bookkeeping they need):

    - ``pass_no``, ``visited``: the pass and the panels visited in it.
    - ``exposures_this_pass``, ``deferred_this_pass``: the two counts the pass
      boundary reads. The spec keeps ``exposures_at_pass_start`` and diffs the
      ledger; summing the visits' own counts is the same number without a
      ledger walk.
    - ``failed``, ``reject_visits``: the two consecutive-failure counters.
    - ``set_aside``: target id to reason, for tonight.
    - ``flipped``, ``acquired``, ``angle_verified``: set by the engine (5.7,
      5.6 steps 4 and 6). ``flipped`` is also read back at a same-night
      restart from ``Session.group_pier`` (#312), so it is the one field here
      a resume does not recompute from the ledger.
    - ``defer_until``: the end of the deferral wait an all-deferred pass
      began (:meth:`defer_next_pass`, #304), or None. A clock time the engine
      hands in; nothing here reads a clock. ``defer_why``: what began it,
      ``"deferred"`` (every visit deferred, ``DEFER_WAIT_S``) or
      ``"centring"`` (the centring hold, ``CENTRING_HOLD_RETRY_S``, #534), for
      the words the wait is published in.
    - ``let_through``: the members a selection of this pass found it may
      visit (:meth:`note_let_through`), for :meth:`visitable`.
    - ``set_aside_kind``: what kind of set-aside each entry of ``set_aside``
      is (#534): ``"centring"`` for a streak of centring failures and nothing
      else, the one kind that expires (:func:`set_aside_expiry`), and a word
      for each other cause. The engine records it with the set-aside.
    - ``expired``: the panels whose centring set-aside expired this run
      (:meth:`expire_set_aside`). The engine keeps the night's count, which a
      restart reads back from ``Session.set_aside``.
    """

    def __init__(self, members: Mapping[str, str], *, max_failed_visits: int):
        if not members:
            raise ValueError("a group needs at least one member")
        if (isinstance(max_failed_visits, bool)
                or not isinstance(max_failed_visits, int)
                or max_failed_visits < 1):
            raise ValueError(
                f"max_failed_visits must be an int of at least 1, "
                f"got {max_failed_visits!r}")
        self.members: dict[str, str] = {str(k): str(v) for k, v in members.items()}
        self.max_failed_visits = max_failed_visits
        self.pass_no = 1
        self.visited: set[str] = set()
        self.exposures_this_pass = 0
        self.deferred_this_pass = 0
        self.failed: dict[str, int] = {p: 0 for p in self.members}
        self.reject_visits: dict[str, int] = {p: 0 for p in self.members}
        self.set_aside: dict[str, str] = {}
        self.completed: set[str] = set()
        self.flipped = False
        self.acquired = False
        self.angle_verified = False
        self.defer_until: float | None = None
        self.defer_why: Literal["deferred", "centring"] = "deferred"
        self.let_through: set[str] = set()
        self.set_aside_kind: dict[str, str] = {}
        self.expired: set[str] = set()
        # The reject rule's window, "since this panel's previous visit", on a
        # visit counter rather than the clock: two visits can share a clock
        # second on a fake clock, and the order of visits is what the rule
        # is about.
        self._seq = 0
        self._last_visit: dict[str, int] = {}
        self._last_accept: dict[str, int] = {}
        # The kinds in each panel's current failure streak, so the set-aside
        # sentence does not claim three failures of the last one's kind.
        self._streak_kinds: dict[str, set[str]] = {p: set() for p in self.members}
        # The guide-start pass rule's ledger for the current pass.
        self.guide_attempts = 0
        self.guide_failures = 0
        self._held: list[tuple[str, PanelDeferred]] = []
        # The centring pass rule's ledger for the current pass (#534): the
        # panels visited, each once, and those whose hop did not centre. Sets,
        # because a no-op jump takes a panel up again in the same pass, and
        # the rule is about panels, not visits.
        self.centring_attempted: set[str] = set()
        self.centring_failed: set[str] = set()
        self._held_centring: list[tuple[str, PanelDeferred]] = []

    # -- membership

    def is_live(self, panel: str) -> bool:
        """Still in play tonight: a member, not complete, not set aside."""
        return (panel in self.members and panel not in self.completed
                and panel not in self.set_aside)

    def live(self) -> list[str]:
        """The live members, in the group's order."""
        return [p for p in self.members if self.is_live(p)]

    def note_let_through(self, panels: Iterable[str]) -> None:
        """Record the members one selection found it may visit now: ready by
        their gating, reachable, let by the meridian rule, and outside any
        deferral wait (the engine's ``_eligibility_now``). Kept for the pass,
        for :meth:`visitable`; :meth:`start_pass` clears it. A name that is
        no member is ignored."""
        self.let_through.update(p for p in panels if p in self.members)

    def visitable(self) -> list[str]:
        """The live members THIS PASS CAN VISIT, in the group's order: those
        visited in it, and those one of its selections let through
        (:meth:`note_let_through`). The count :func:`no_guider_defers` reads
        (the #315 follow-up, S3 orchestrator ruling 6).

        A live member held at every selection of the pass so far, behind a
        limit, by the meridian rule or by its own gating, is not one: it
        makes no guide attempt in this pass, so it cannot be the second
        attempt the rig verdict needs. One a selection let through and that
        is held now is: the pass could have visited it, and the order alone
        put another first."""
        return [p for p in self.members if self.is_live(p)
                and (p in self.visited or p in self.let_through)]

    def set_aside_panel(self, panel: str, reason: str, *,
                        kind: str = "panel") -> None:
        """Set one panel aside tonight for a cause the engine decided: its
        floor under ``on_floor = advance``, a pier-side change with flips off
        (5.1 selection, item 1), or a record a restart read back, whose
        ``kind`` it passes on (#534: a centring one may still expire)."""
        self._check_live(panel)
        self.set_aside[panel] = str(reason)
        self.set_aside_kind[panel] = str(kind)

    def set_aside_all(self, reason: str, *, kind: str = "group") -> list[str]:
        """Set every live member aside tonight (``guiding_action`` skip after a
        rig-fault pass, a fixed camera's angle beyond tolerance). Returns the
        panels it set aside."""
        panels = self.live()
        for p in panels:
            self.set_aside[p] = str(reason)
            self.set_aside_kind[p] = str(kind)
        return panels

    def expire_set_aside(self, panel: str) -> None:
        """A panel's CENTRING set-aside has expired (#534,
        :func:`set_aside_expiry`): it is live again, to be visited once more
        tonight, with a clean slate.

        ``failed``, ``reject_visits`` and the streak's kinds start again at
        nothing, so it is set aside once more only after ``max_failed_visits``
        further failures, never at its first; and it is not visited in this
        pass, so the pass takes it up. Only a centring set-aside expires;
        asked of any other, or of a panel that is not set aside, this is a
        caller bug and says so. The engine keeps the one-expiry-a-night
        count (``expired`` holds this run's)."""
        if panel not in self.members:
            raise ValueError(f"{panel!r} is not a member of this group")
        if panel not in self.set_aside:
            raise ValueError(f"{self.members[panel]} is not set aside")
        if self.set_aside_kind.get(panel) != CENTRING:
            raise ValueError(
                f"{self.members[panel]}'s set-aside is not a centring one "
                f"({self.set_aside_kind.get(panel)!r}), and only a centring "
                f"set-aside expires")
        # NO PASS IS IN PROGRESS WHEN NO OTHER MEMBER IS LIVE. The boundary
        # that set the last of them aside answered ``none_live`` and started
        # no pass, so the counts still standing are that closed pass's: read
        # by the next boundary, its exposures and its centring ledger would
        # decide a pass this panel was never part of. So the panel comes
        # back into a pass of its own, as :meth:`start_pass` would begin it.
        alone = not any(self.is_live(q) for q in self.members if q != panel)
        del self.set_aside[panel]
        self.set_aside_kind.pop(panel, None)
        self.failed[panel] = 0
        self.reject_visits[panel] = 0
        self._streak_kinds[panel] = set()
        self.visited.discard(panel)
        self.expired.add(panel)
        if alone:
            # Anything still held belongs to panels that are not live, the
            # only kind there is here, and a count for them would set aside
            # nothing: the boundary drops those too (``is_live``).
            self._held = []
            self._held_centring = []
            self.start_pass()

    def _check_live(self, panel: str) -> None:
        if panel not in self.members:
            raise ValueError(f"{panel!r} is not a member of this group")
        if not self.is_live(panel):
            raise ValueError(f"{self.members[panel]} is not live: it is "
                             f"complete or set aside tonight")

    # -- one visit

    def visit_outcome(self, panel: str, *, complete: bool, exposures: int,
                      accepted: int, deferred: PanelDeferred | None = None,
                      guide_started: bool = False) -> VisitAction:
        """Apply one visit's outcome to the panel (the 5.1 table).

        ``complete``: every step of the panel met its count. ``exposures``:
        frames taken this visit, accepted plus rejected. ``accepted``: frames
        accepted this visit. ``deferred``: the ``PanelDeferred`` that ended the
        visit, if one did. ``guide_started``: the plan asks for guiding and
        this hop's guider start succeeded (the guide rule counts it as an
        attempt that worked).

        - complete: remove it.
        - at least one accepted frame: requeue, and reset ``failed`` and
          ``reject_visits``. The bounds are on CONSECUTIVE failures; a panel
          that banks a frame has shown it can be shot tonight.
        - exposures taken, none accepted, while another live member accepted
          a frame since this panel's previous visit: requeue and add one to
          ``reject_visits``; at ``max_failed_visits`` set it aside. When
          every member rejects, the sky is to blame and nothing moves: the
          night guard and the cloud hold own that case.
        - ``PanelDeferred``: mark it visited and add one to ``failed`` and to
          ``deferred_this_pass``; at ``max_failed_visits`` set it aside with a
          reason naming the panel, the cause and the last error. A failed
          guide start is counted when the pass closes instead, because only
          the whole pass says whether it was the panel's fault or the rig's
          (:meth:`close_pass`). A deferral after banked frames (a guiding loss
          mid-visit) resets first and then counts, so it starts a new streak
          of one.
        - a CENTRING miss (#534) is held until the pass closes, as a failed
          guide start is, and for the same reason: only the whole pass says
          whether it was the panel's fault or the sky's
          (:func:`centring_pass_verdict`).
        - a solve that could not run (:data:`SOLVE_TRANSIENT`, #532) is
          marked visited and counted toward ``deferred_this_pass`` only: no
          ``failed``, no reset, and no place in the centring pass rule.

        A plain StopTarget reaches here as a ``PanelDeferred`` of kind
        :data:`TARGET_STOP`, made by the driver (#316). A floor stop, a
        JumpTarget, a SafetyAbort, a NightQualityStop and cancellation never
        do: they are handled or propagate as they do today, a floor stop's
        counts handed over by :meth:`note_visit` (#288), and a jumped visit's
        too (#322).
        """
        guide_failed = deferred is not None and deferred.kind == GUIDE_START
        centring_failed = deferred is not None and deferred.kind == CENTRING
        transient = deferred is not None and deferred.kind == SOLVE_TRANSIENT
        previous = self._record(panel, exposures=exposures, accepted=accepted,
                                guide_failed=guide_failed,
                                guide_started=guide_started,
                                centring_failed=centring_failed,
                                transient=transient)
        label = self.members[panel]

        if complete:
            # Completion wins over a deferral raised after the last frame: the
            # panel owes nothing, so there is nothing to retry.
            self.completed.add(panel)
            return VisitAction("remove", f"panel {label} complete")

        self.visited.add(panel)
        if accepted > 0:
            self.failed[panel] = 0
            self.reject_visits[panel] = 0
            self._streak_kinds[panel] = set()

        if deferred is not None:
            self.deferred_this_pass += 1
            if guide_failed:
                self._held.append((panel, deferred))
                return VisitAction(
                    "requeue",
                    f"{self._deferral_words(label, deferred)}; retried on the "
                    f"next pass (counted when the pass ends: a guider that "
                    f"fails on every panel is the rig's fault)")
            if centring_failed:
                self._held_centring.append((panel, deferred))
                return VisitAction(
                    "requeue",
                    f"{self._deferral_words(label, deferred)}; retried on the "
                    f"next pass (counted when the pass ends: a centring that "
                    f"fails on every panel is the sky's or the geometry's, "
                    f"not a panel's)")
            if transient:
                return VisitAction(
                    "requeue",
                    f"{self._deferral_words(label, deferred)}; retried on the "
                    f"next pass (not counted as a failed visit: the solve "
                    f"could not run, and nothing about the panel failed)")
            return self._count_failure(panel, deferred)

        if accepted > 0:
            return VisitAction(
                "requeue", f"{label}: {accepted} of {exposures} accepted this visit")

        if exposures > 0:
            if self._another_live_member_accepted_since(panel, previous):
                self.reject_visits[panel] += 1
                n = self.reject_visits[panel]
                if n >= self.max_failed_visits:
                    reason = (f"{label} rejected every frame for {_visits(n)} "
                              f"while the other panels were accepted")
                    self.set_aside[panel] = reason
                    self.set_aside_kind[panel] = "rejects"
                    return VisitAction("set_aside", reason)
                return VisitAction(
                    "requeue",
                    f"{label} rejected every frame this visit while another "
                    f"panel was accepted ({n} of {self.max_failed_visits})")
            return VisitAction(
                "requeue",
                f"{label} rejected every frame, and no other panel was "
                f"accepted since its last visit: the sky, not the panel")

        return VisitAction("requeue", f"{label} took no exposures this visit")

    def note_visit(self, panel: str, *, exposures: int, accepted: int,
                   guide_started: bool = False) -> None:
        """Count a visit whose outcome the ENGINE decided, before it acts on
        it (#288): a panel that sank below its own floor mid-visit
        (``FloorStop``), which the engine sets aside tonight at once, and a
        visit a ``JumpTarget`` ended (#322), which the scheduler then either
        drops from the group (the jump consumed the panel) or takes up again
        in the same pass (a no-op jump); one whose banked trigger frame
        completed the panel is then made complete (:meth:`note_complete`,
        #373). The panel is NOT marked visited here:
        a visited panel is one the pass is done with, and only
        :meth:`visit_outcome` makes one.

        The visit's frames were shot this pass all the same, so they are
        counted as any visit's are: its exposures toward the pass boundary,
        a guider start that worked toward the guide-start pass rule, and its
        accepted frames into the reject rule's record (which reads only live
        members, so for a panel set aside at once they change no verdict).
        Nothing is decided here, and the panel is left live for the engine
        to set aside (:meth:`set_aside_panel`).

        WHY IT MATTERS: before it, a pass whose only exposures came from such
        a visit read as a pass of none: a ``DEFER_WAIT_S`` wait when the other
        visits deferred, the group anti-spin (every live member set aside
        tonight) when they took nothing; and a guider that started on the
        floor-stopped panel was missing from a pass that could then read as
        the guider's fault. A visit that shot frames centred, so it is a
        centring attempt that worked (#534).
        """
        self._record(panel, exposures=exposures, accepted=accepted,
                     guide_failed=False, guide_started=guide_started,
                     centring_failed=False, transient=False)

    def note_complete(self, panel: str) -> None:
        """A panel the engine found complete after a visit whose outcome it
        did not hand here: the visit a ``JumpTarget`` ended, when the frame
        that fired the jump was the last the panel owed (#373). That frame
        is banked before the jump acts, so such a visit can complete its
        panel; its counts reach :meth:`note_visit` first, and this makes it
        complete as :meth:`visit_outcome` makes a complete panel, without
        marking it visited (the scheduler decides by the jump).

        Left live, a complete panel is one the pass rules still count and
        the group can never finish with: whatever waits for the mosaic
        reads it as set aside."""
        self._check_live(panel)
        self.completed.add(panel)

    def _record(self, panel: str, *, exposures: int, accepted: int,
                guide_failed: bool, guide_started: bool,
                centring_failed: bool, transient: bool) -> int:
        """The bookkeeping every visit makes, whoever decides its outcome
        (:meth:`visit_outcome`, :meth:`note_visit`): one definition, so the
        two cannot come to count a visit differently. Returns the sequence
        number of the panel's previous visit, the reject rule's window.

        Every visit but a transient solve's is a centring attempt (#534):
        the hop checks the centring first, so a visit that got past it
        centred. A transient solve is left out of the rule entirely (#532),
        whichever solve could not run: a centring solve that could not run
        tried nothing, and the key the hub sets does not say which solve it
        was."""
        self._check_live(panel)
        exposures = _count("exposures", exposures)
        accepted = _count("accepted", accepted)
        if accepted > exposures:
            raise ValueError(
                f"accepted ({accepted}) cannot exceed exposures ({exposures})")
        if guide_failed and guide_started:
            raise ValueError("a guide start cannot both succeed and fail")
        self._seq += 1
        previous = self._last_visit.get(panel, 0)
        self._last_visit[panel] = self._seq
        self.exposures_this_pass += exposures
        if accepted > 0:
            self._last_accept[panel] = self._seq
        if guide_failed:
            self.guide_attempts += 1
            self.guide_failures += 1
        elif guide_started:
            self.guide_attempts += 1
        if not transient:
            self.centring_attempted.add(panel)
        if centring_failed:
            self.centring_failed.add(panel)
        return previous

    def _another_live_member_accepted_since(self, panel: str,
                                            previous: int) -> bool:
        return any(
            q != panel and self.is_live(q) and self._last_accept.get(q, 0) > previous
            for q in self.members)

    @staticmethod
    def _deferral_words(label: str, deferred: PanelDeferred) -> str:
        words = f"{deferred.reason} on {label}"
        return f"{words}: {deferred.last_error}" if deferred.last_error else words

    def _count_failure(self, panel: str, deferred: PanelDeferred) -> VisitAction:
        label = self.members[panel]
        self.failed[panel] += 1
        self._streak_kinds[panel].add(deferred.kind)
        n = self.failed[panel]
        if n < self.max_failed_visits:
            return VisitAction(
                "requeue",
                f"{self._deferral_words(label, deferred)}; retried on the next "
                f"pass ({n} of {self.max_failed_visits} consecutive)")
        tail = f": {deferred.last_error}" if deferred.last_error else ""
        if len(self._streak_kinds[panel]) == 1:
            reason = (f"{deferred.reason} on {label} on {_visits(n, 'consecutive ')}"
                      f"{tail}")
        else:
            reason = (f"{label} was deferred on {_visits(n, 'consecutive ')}, "
                      f"the last because {deferred.reason}{tail}")
        self.set_aside[panel] = reason
        # A STREAK OF CENTRING FAILURES AND NOTHING ELSE is the one set-aside
        # that expires (#534, :func:`set_aside_expiry`): what hid the panel
        # (an obstruction low in the east, the haze near the horizon) is the
        # kind of cause the passing hour clears. A streak with any other
        # failure in it failed for a reason waiting does not change, so it is
        # set aside for the night as before.
        self.set_aside_kind[panel] = (
            CENTRING if self._streak_kinds[panel] == {CENTRING} else "deferred")
        return VisitAction("set_aside", reason)

    # -- the pass

    def close_pass(self) -> PassEnd:
        """Decide the pass boundary: the centring pass rule and the
        guide-start rule first, then the held deferrals, then
        :func:`pass_boundary`.

        Call it when no unvisited member is eligible but a visited one is
        (5.1). The two pass rules come first because a pass the sky or the
        rig is to blame for must move no counter, and such a pass is exactly
        the one whose zero exposures and all-deferred visits would otherwise
        read as a deferral wait. They cannot both hold: a pass in which no
        panel centred made no guide attempt, since the hop checks the
        centring first (5.6 steps 4 and 7). Nothing is cleared here;
        :meth:`start_pass` begins the next pass.
        """
        held, self._held = self._held, []
        held_centring, self._held_centring = self._held_centring, []
        tried = len(self.centring_attempted)
        if centring_pass_verdict(tried, len(self.centring_failed)) == "sky":
            # THE SKY OR THE GEOMETRY, NOT THE PANELS (#534, H4 orchestrator
            # ruling 2). Every panel tried failed to centre, so what failed
            # is what they share: the target low behind an obstruction, a
            # cloud bank, the haze near the horizon. No panel is struck, and
            # the group waits and tries again, as a cloud hold waits for the
            # sky rather than giving up on it.
            return PassEnd(
                "centring_hold", (),
                f"centring failed on every one of the {tried} panels tried in "
                f"pass {self.pass_no}: the sky or the geometry is to blame, "
                f"not a panel, so no panel's failure count moved; holding the "
                f"mosaic {CENTRING_HOLD_RETRY_S / 60:.0f} minutes before the "
                f"next pass")

        set_aside: list[tuple[str, str]] = []
        counted: list[tuple[str, str]] = []
        # A centring miss in a pass where another panel centred is that
        # panel's, and counts as every other deferral does, what the visit's
        # own line counted before centring misses were held (#534). Counted
        # before the guide rule decides, which a rig-fault pass may end
        # early: a panel centred there too, so its misses are the panels'.
        for panel, deferred in held_centring:
            if not self.is_live(panel):
                continue
            act = self._count_failure(panel, deferred)
            if act.action == "set_aside":
                set_aside.append((panel, act.reason))
            else:
                counted.append((panel, act.reason))

        attempts, failures = self.guide_attempts, self.guide_failures
        if guide_start_pass_verdict(attempts, failures) == "rig":
            # THE SENTENCE MUST NOT CLAIM WHAT THE SAME PASS JUST DID (#575).
            # A centring miss held above can be counted, or even set a panel
            # aside, before this verdict is reached (a pass can hold both a
            # rig-fault guide start and a panel's own centring miss), and
            # "no panel's failure count moved" is false exactly then. Say
            # what moved instead of a blanket claim, so the alert this
            # reason reaches at warning never contradicts the info lines
            # ``close_pass``'s own caller logs for ``counted`` beside it.
            moved = tuple(counted) + tuple(set_aside)
            if moved:
                noun = "panel" if len(moved) == 1 else "panels"
                also = (f"{len(moved)} {noun} in the same pass had a "
                        f"centring miss counted or set aside, charged to "
                        f"the panel, not the guider")
            else:
                also = "no panel's centring miss was counted in the same pass"
            return PassEnd(
                "guiding_action", tuple(set_aside),
                f"guiding did not start on any of the {attempts} panels tried "
                f"this pass: the guider's fault, not a panel's; {also}, and "
                f"the plan's guiding_action decides",
                tuple(counted))

        for panel, deferred in held:
            if not self.is_live(panel):
                continue
            act = self._count_failure(panel, deferred)
            if act.action == "set_aside":
                set_aside.append((panel, act.reason))

        live = self.live()
        if not live:
            return PassEnd("none_live", tuple(set_aside),
                           "no panel is left to shoot tonight: every one is "
                           "complete or set aside", tuple(counted))
        boundary = pass_boundary(self.exposures_this_pass, self.deferred_this_pass)
        if boundary == "set_aside_all":
            reason = (f"a full pass over {len(live)} panels took no exposures; "
                      f"setting the mosaic aside for tonight")
            for p in live:
                self.set_aside[p] = reason
                self.set_aside_kind[p] = "group"
                set_aside.append((p, reason))
            return PassEnd(boundary, tuple(set_aside), reason, tuple(counted))
        if boundary == "defer_wait":
            return PassEnd(
                boundary, tuple(set_aside),
                f"pass {self.pass_no} took no exposures and deferred "
                f"{self.deferred_this_pass} visits; waiting "
                f"{DEFER_WAIT_S:.0f} s before the next pass", tuple(counted))
        return PassEnd(
            boundary, tuple(set_aside),
            f"pass {self.pass_no} ended with {self.exposures_this_pass} "
            f"exposures and {self.deferred_this_pass} deferrals",
            tuple(counted))

    def start_pass(self) -> None:
        """Begin the next pass: clear ``visited``, ``let_through`` and the
        pass's counts, the two pass rules' ledgers included.

        Refuses while guide or centring deferrals are held, because that
        means the pass was never closed and those failures would be dropped
        uncounted.
        """
        if self._held or self._held_centring:
            raise RuntimeError(
                "close_pass() first: this pass still holds guide-start or "
                "centring deferrals that only the pass boundary can count")
        self.pass_no += 1
        self.visited.clear()
        self.let_through.clear()
        self.exposures_this_pass = 0
        self.deferred_this_pass = 0
        self.guide_attempts = 0
        self.guide_failures = 0
        self.centring_attempted.clear()
        self.centring_failed.clear()

    # -- the deferral wait (#304)

    def defer_next_pass(self, now: float, *, wait_s: float = DEFER_WAIT_S,
                        why: Literal["deferred", "centring"] = "deferred"
                        ) -> float:
        """Hold every live member until ``now + wait_s``, the wait a
        ``defer_wait`` boundary begins (5.1 pass boundary, item 2), and
        return that time. ``wait_s`` is ``DEFER_WAIT_S`` there, and
        ``CENTRING_HOLD_RETRY_S`` for the hold a ``centring_hold`` boundary
        begins (#534), which ``why`` names (``defer_why``) for the words the
        wait is published in. One wait, whichever began it: the scheduler
        holds the members as waiters until it ends, and a follower may fill
        it.

        THE WAIT IS A STATE, NOT A SLEEP (#304). S2 slept it out inside the
        pass boundary, so for five minutes the scheduler could choose
        nothing: a follower that could have filled the gap waited it out
        with the group, which is exactly the idle the owner's default ("shoot
        later targets, then come back") exists to spend. Held here, the
        members are waiters with a wake time like any other, the scheduler's
        own wait runs (the safety gate and the idle watch with it), and a
        ready follower may take the gap in a visit bounded by this time.

        ``max_failed_visits`` still bounds how often a deferral wait happens
        per panel: nothing here touches the failure counts. A centring hold
        is not bounded that way, by the ruling: it holds the group within
        the night for as long as every panel tried fails, and the night's
        window ends it."""
        wait = _nonneg("wait_s", wait_s)
        if why not in ("deferred", "centring"):
            raise ValueError(f"why must be 'deferred' or 'centring', got {why!r}")
        self.defer_until = _finite("now", now) + wait
        self.defer_why = why
        return self.defer_until

    def deferring(self, now: float) -> bool:
        """Is the group inside a deferral wait at ``now``? Its end is
        exclusive: at ``defer_until`` the members are free again, the moment
        the wait path wakes on."""
        return (self.defer_until is not None
                and _finite("now", now) < self.defer_until)


# ------------------------------------------------------- meridian (5.7), hours

@dataclass(frozen=True)
class PanelMeridian:
    """One panel's facts for the meridian rule, in hours.

    ``h_p``: ``schedule.hours_to_meridian_flip(panel.ra_hours, lon)``, positive
    east of the meridian and at or below 0 after the crossing. ``f_h``: that
    panel's first owed frame, exposure plus the per-frame overhead EMA plus
    ``FLIP_FRAME_MARGIN_S``. ``flip_can_be_skipped``: what
    ``schedule.flip_can_be_skipped(dec, lat, side)`` says for the panel.
    """

    h_p: float
    f_h: float
    flip_can_be_skipped: bool = False


@dataclass(frozen=True)
class MeridianVerdict:
    """The meridian rule's answer for one panel.

    ``eligible``: the rule lets it shoot now. ``deadline_h``: hours from now
    to its flip point, the visit's ``deadline_ts`` (5.3); ``None`` when no
    flip point bounds the visit. ``wake_h``: hours from now until a waiting
    panel is worth asking again (its crossing); ``None`` when eligible.
    ``reason``: words only, never the hours (6.9).
    """

    eligible: bool
    deadline_h: float | None
    wake_h: float | None
    reason: str


def meridian_eligibility(panels: Mapping[str, PanelMeridian], *, lead_h: float,
                         hop_h: float, flipped: bool,
                         meridian_flip: bool,
                         crossed_h: float = 0.0) -> dict[str, MeridianVerdict]:
    """At most one pier change per group per night: the 5.7 hysteresis table.

    ``lead_h`` is the PLAN's flip lead in hours (``_plan_flip_lead_s() /
    3600``), NEVER the learned one. ``_flip_lead_s`` returns 0 for a mount that
    cannot flip early, which is right for when to attempt a flip and wrong as a
    margin: the AM5 stops tracking 4.7 to 7.6 min before transit, so a
    pre-flip visit measured against a zero lead runs into the mount's own
    limit. ``hop_h`` is the hop EMA (150 s until measured). ``flipped``: the
    group changed pier side tonight. ``meridian_flip``: the plan's flag.

    Not flipped:

    - a pre-flip panel (``h_p > 0``) with room for a hop and one frame before
      its flip point, ``h_p - lead_h >= hop_h + f_h``, is eligible, with
      ``deadline_h = h_p - lead_h``: the visit ends at a frame boundary
      before the flip point.
    - a pre-flip panel without that room waits, waking at its crossing
      (``h_p``).
    - a post-meridian panel (``h_p <= 0``) is eligible only while no pre-flip
      panel is. Shooting it first would flip the group, and going back to a
      pre-flip panel would be a second pier change.

    Flipped: only post-meridian panels are eligible; every other panel waits
    for its crossing.

    The rule is off for a panel whose flip ``flip_can_be_skipped``, and for
    every panel when ``meridian_flip`` is off: eligible, with no deadline.
    Such a panel does not hold the post-meridian panels back, since it never
    changes pier side.

    ``crossed_h`` (T18) is how far past its crossing a panel must be before
    it counts as past the meridian (``h_p <= -crossed_h``); in the band just
    past the crossing it waits until it leaves it, and a panel with no room
    before its flip point wakes there too. A hop right AT the crossing is a
    coin toss for the mount's side: the mount judges the hour angle with its
    own clock, longitude and pointing model, and the simulator's goto alone
    lands 7 s of RA off until it is synced, enough to put the target back
    east and the tube on the pre-flip side, which the hop's side check then
    defers. The default 0 is the rule as the spec writes it.

    THE HOURS ARE THE COUNTDOWN'S HOURS. ``h_p`` counts hour angle, which runs
    0.27% faster than the clock, and ``deadline_h`` and ``wake_h`` are hours
    of that countdown. A caller that turns ``deadline_h`` into a clock time
    converts it (``* SOLAR_PER_SIDEREAL``; `SequenceEngine._meridian_now`
    does). Taken as clock hours without conversion, as T18 first did, the
    deadline drifts past the flip gate's line: the frame loop's gate
    re-reads the countdown at every frame (``ttf_h * 3600 - lead_s``), so the
    two part by 0.27% of the visit, under 10 s an hour, and at some phases
    the gate, not the deadline, ended a long visit with a flip attempt and a
    frame past the flip point (T18 verifier). A wake taken as clock hours
    lands late by the same fraction, the safe side of a crossing.
    """
    lead = _nonneg("lead_h", lead_h)
    hop = _nonneg("hop_h", hop_h)
    crossed = _nonneg("crossed_h", crossed_h)
    facts = {k: (_finite(f"h_p of {k}", v.h_p), _nonneg(f"f_h of {k}", v.f_h),
                 bool(v.flip_can_be_skipped))
             for k, v in panels.items()}

    out: dict[str, MeridianVerdict] = {}
    post: list[str] = []
    # When each pre-flip-eligible panel runs out of room, for the wake of a
    # post-meridian panel it holds back.
    pre_room_left: list[float] = []
    for k, (h_p, f_h, skippable) in facts.items():
        if not meridian_flip or skippable:
            out[k] = MeridianVerdict(
                True, None, None,
                "the meridian rule is off" + (
                    " for this panel: its flip can be skipped" if skippable
                    and meridian_flip else ": meridian flips are off"))
            continue
        if flipped:
            if h_p <= -crossed:
                out[k] = MeridianVerdict(
                    True, None, None,
                    "past the meridian, on the side the group flipped to")
            else:
                out[k] = MeridianVerdict(
                    False, None, h_p + crossed,
                    "the group has flipped; this panel waits for its meridian "
                    "crossing, since going back would be a second pier change")
            continue
        if h_p <= -crossed:
            post.append(k)
            continue
        if h_p <= 0.0:
            out[k] = MeridianVerdict(
                False, None, h_p + crossed,
                "just past its meridian crossing; waiting until the mount's "
                "side there is not in doubt")
            continue
        room = h_p - lead
        if room >= hop + f_h:
            out[k] = MeridianVerdict(
                True, room, None,
                "before the meridian with room for a hop and a frame; the "
                "visit ends before its flip point")
            pre_room_left.append(room - hop - f_h)
        else:
            out[k] = MeridianVerdict(
                False, None, h_p + crossed,
                "too little room before its flip point for a hop and one "
                "frame; waiting for its meridian crossing")

    for k in post:
        if pre_room_left:
            out[k] = MeridianVerdict(
                False, None, max(pre_room_left),
                "past the meridian, held while a panel before the meridian "
                "can still shoot, so the group changes pier side once")
        else:
            out[k] = MeridianVerdict(
                True, None, None,
                "past the meridian, and no panel before the meridian can "
                "shoot")
    return {k: out[k] for k in facts}


# ------------------------------------------------ the pre-flip idle (5.7, A.4)

def ra_span_sidereal_h(ra_hours: Iterable[float]) -> float:
    """The RA span of the panel centres, in hours of RA (sidereal hours).

    The shortest arc of the 24 h circle that holds every centre: a mosaic at
    RA 0 h has centres either side of 24 h, and ``max - min`` would call a
    12-minute grid 23.8 hours wide.
    """
    ras = sorted(_finite("ra_hours", r) % 24.0 for r in ra_hours)
    if not ras:
        raise ValueError("ra_span_sidereal_h needs at least one RA")
    # The largest gap between neighbours, the wrap included, is the part of
    # the circle the grid does not cover.
    gaps = [b - a for a, b in zip(ras, ras[1:])]
    gaps.append(ras[0] + 24.0 - ras[-1])
    return 24.0 - max(gaps)


def ra_span_solar_h(ra_hours: Iterable[float]) -> float:
    """The RA span of the panel centres in clock hours: how long the meridian
    takes to sweep from the first centre to the last (A.4)."""
    return ra_span_sidereal_h(ra_hours) * SOLAR_PER_SIDEREAL


def whole_visit_s(*, passes: int, pass_shutter_s: float, frames_per_pass: int,
                  overhead_s: float, hop_s: float) -> float:
    """One visit that must fit whole, in seconds (A.4): ``passes x (shutter +
    frames x overhead) + hop``. On the shipped default cycle (780 s of
    shutter, 7 frames) at 10 s of overhead and a 150 s hop: 1000 s for one
    pass."""
    if isinstance(passes, bool) or not isinstance(passes, int) or passes < 1:
        raise ValueError(f"passes must be an int of at least 1, got {passes!r}")
    frames = _count("frames_per_pass", frames_per_pass)
    return (passes * (_nonneg("pass_shutter_s", pass_shutter_s)
                      + frames * _nonneg("overhead_s", overhead_s))
            + _nonneg("hop_s", hop_s))


def preflip_idle_cut_h(*, lead_h: float, hop_h: float, f_h: float,
                       span_h: float) -> float:
    """The pre-flip idle with cut visits (5.7 cost 1), in hours:
    ``max(0, lead + hop + f - span)``.

    Nothing in the group is eligible from the moment the last pre-flip panel
    runs out of room (a hop and one frame before its flip point) until the
    first panel crosses; the panels' spread in RA covers part of that gap.
    ``span_h`` is :func:`ra_span_solar_h` of the live panel centres.
    """
    return max(0.0, _nonneg("lead_h", lead_h) + _nonneg("hop_h", hop_h)
               + _nonneg("f_h", f_h) - _nonneg("span_h", span_h))


def preflip_idle_whole_h(*, visit_h: float, lead_h: float,
                         span_h: float) -> float:
    """The pre-flip idle if a visit had to fit whole (no deadline), in hours:
    ``max(0, visit + lead - span)`` (A.4). What the deadline saves."""
    return max(0.0, _nonneg("visit_h", visit_h) + _nonneg("lead_h", lead_h)
               - _nonneg("span_h", span_h))


# ----------------------------------------------- forward_clear_ts (1.6), 60 s

def forward_clear_ts(clear: Callable[[float], bool], now: float, horizon_s: float,
                     step_s: float = REACH_RECHECK_S) -> float | None:
    """The first instant on a ``step_s`` grid from ``now`` at which
    ``clear(t)`` is true, or ``None`` if none is within ``horizon_s``.

    ``group_ready_ts`` (1.6) takes, for a panel blocked by its floor or the
    mask, the projected time it clears, scanned in 60 s steps over the same
    predicate as ``_mount_floor_verdict``, in the shape of
    ``schedule._time_to_gate``. ``now`` itself is asked first, so a panel
    clear now answers ``now``. A panel with no clearing time tonight answers
    ``None`` and does not bound ``group_ready_ts``.

    A BLOCKED PANEL NEVER ANSWERS ``now``. That would make ``group_ready_ts``
    the present, hand a bounded follower a deadline it has already reached,
    and turn the wait into a hot loop of selections that shoot nothing.
    """
    t0 = _finite("now", now)
    horizon = _nonneg("horizon_s", horizon_s)
    step = _finite("step_s", step_s)
    if step <= 0.0:
        raise ValueError(f"step_s must be positive, got {step_s!r}")
    # A tiny tolerance so a horizon that is a whole number of steps includes
    # its last step despite float division.
    steps = int(horizon / step + 1e-9)
    for i in range(steps + 1):
        t = t0 + i * step
        if clear(t):
            return t
    return None


# ------------------------------------------------ angle_decision (5.6 step 4)

AngleAction = Literal["shoot", "shoot_logged", "warn", "defer", "set_group_aside"]


@dataclass(frozen=True)
class AngleDecision:
    """What an angle verdict means for this panel.

    ``shoot``: carry on. ``shoot_logged``: carry on, and log ``why``.
    ``warn``: carry on with a warning alert. ``defer``: raise
    ``PanelDeferred(kind="angle")``. ``set_group_aside``: set the whole group
    aside tonight with a warning alert that carries the verdict's numbers.
    ``why``: the policy in words; the verdict's own reason carries the
    angles, and the engine joins the two.
    """

    action: AngleAction
    why: str


def angle_decision(kind: str, *, rotate: bool, angle_verified: bool,
                   rotator_evidence: bool, shoot_anyway: bool,
                   single_panel: bool) -> AngleDecision:
    """Every 5.6 step 4 case, from ``angle_check.angle_verdict``'s ``kind``.

    ``rotate``: ``TargetGroup.rotate``, a rotator turns the camera to the
    layout angle (False: the camera is fixed). ``angle_verified``:
    ``GroupRun.angle_verified``, a hop earlier tonight measured this fixed
    camera within tolerance. ``rotator_evidence``: rotate mode with a
    calibrated rotator that reports its target position and no
    ``rotation_skipped``; meaningless for a fixed camera, which has no rotator
    to read. ``shoot_anyway``: the group's "Shoot anyway". ``single_panel``: a
    1x1 block with a planned angle.

    - ``ok``: shoot.
    - ``off``: a fixed camera sets the group aside at once, because it cannot
      fix itself; a rotator defers the panel.
    - ``no_measurement``, which is neither ``ok`` nor ``off``: a fixed camera
      verified tonight shoots and logs, since it cannot turn between hops; one
      never verified defers, because tiles are never laid blind; a rotator
      whose own read is the evidence shoots with a warning; any other rotator
      defers.
    - "Shoot anyway" turns every refusal into a warning, and a 1x1 block only
      ever warns.
    """
    if kind not in ("ok", "off", "no_measurement"):
        raise ValueError(f"unknown angle verdict kind {kind!r}")
    if kind == "ok":
        return AngleDecision("shoot", "the camera is at the mosaic's angle")

    refusal: AngleDecision
    if kind == "off":
        if rotate:
            refusal = AngleDecision(
                "defer", "the rotator did not bring the camera to the "
                         "mosaic's angle; the panel is retried on the next "
                         "pass")
        else:
            refusal = AngleDecision(
                "set_group_aside", "a fixed camera cannot turn itself: turn "
                                   "the camera or re-frame at the measured "
                                   "angle")
    elif not rotate:
        if angle_verified:
            return AngleDecision(
                "shoot_logged", "angle not re-measured on this hop; the "
                                "camera is fixed and was measured at the "
                                "mosaic's angle earlier tonight")
        refusal = AngleDecision(
            "defer", "angle not measured on this hop, and this fixed camera "
                     "has not been measured tonight: tiles are never laid "
                     "blind")
    elif rotator_evidence:
        return AngleDecision(
            "warn", "angle not measured on this hop; shooting on the "
                    "calibrated rotator's own report that it reached the "
                    "mosaic's angle")
    else:
        refusal = AngleDecision(
            "defer", "angle not measured on this hop, and the rotator gave "
                     "no evidence that it reached the mosaic's angle")

    if shoot_anyway:
        return AngleDecision("warn", f"{refusal.why} (shooting anyway, as "
                                     f"this mosaic asks)")
    if single_panel:
        return AngleDecision("warn", f"{refusal.why} (a single panel has no "
                                     f"neighbours to leave a hole beside, so "
                                     f"it only warns)")
    return refusal
