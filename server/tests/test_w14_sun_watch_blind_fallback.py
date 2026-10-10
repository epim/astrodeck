# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-87 (#137): a dropped mount link is BLIND, the net projects from the last
position it read, and the blindness is published on ``/api/safety/state``.

WP-03 made an unreadable mount escalate (warning at 10 minutes, error at 30,
repeating). Three things from the same issue were left open and are built here:

  A. A telescope object whose link is down (``connected`` False) went through
     ``_hold`` and latched silent after one info line, the exact #137 shape,
     because only the ``get_position`` failure had been moved onto the
     escalating clock. It is blind now, on ONE clock with the unreadable case
     (a link that flaps between the two must not restart the count), and the
     net makes one bounded reopen per tick when nobody else has the mount.
  B. While blind the net remembers the last position it read and projects
     BOTH ways a tube moves from it (stopped: the sky turns it toward the Sun
     at 15 deg/h; tracking: it holds). If either reaches the exclusion cone
     within the lead window it logs at error and parks, under exactly the live
     path's hands-off and ``_acted`` rules, through the SAME park block.
  C. ``/api/safety/state`` carries ``sun_watch: {blind, blind_since,
     last_position_at, armed}``: booleans and times only, never a position
     (the #140 class: a mount's pointing is a latitude oracle).

Backlog ruling (owner-approved 2026-09-30, re-triaged 2026-10-07): ``tel is
None`` stays a latched INFO hold. A rig that was never connected must not
page, and a hub teardown (an operator disconnect) lands there too.

Every geometry a decision turns on is COMPUTED here with ``closest_approach``
and asserted as a precondition first: a "did not park" that passes because the
tube was 90 degrees away, or a "parked" that passes because it was already in
the cone at read time, proves nothing.

MUTANTS RUN. Each is a one-line (or one-call) source change, applied from a
byte backup, restored byte-identically (sha256 compared) and grepped gone. The
case that failed, and the first assertion it printed, verbatim:

  m1  the present-but-disconnected branch goes back through ``_hold``:
      ``test_a_dropped_link_is_blind_and_escalates_on_the_same_clock``:
      ``assert ['info'] == ['info', 'war...ror', 'error']`` (one info line,
      then silence: the #137 shape).
  m2  the ``tel.connect()`` reopen is deleted:
      ``test_the_net_reopens_a_dropped_link_once_per_tick_when_it_is_free``:
      ``AssertionError: hands-off=None: 0 reopen attempts`` / ``assert 0 == 3``.
  m3  ``_blind_projection`` is skipped (fallback off):
      ``test_the_last_known_position_parks_a_mount_the_sun_is_closing_on``:
      ``assert 0 == 1`` (park_calls).
  m4  the projection only considers ``tracking=True``: same case, same
      assertion, because the tube was STOPPED when last read.
  m5  ``SunWatch.state()`` returns ``blind`` False constant:
      ``test_the_route_publishes_blindness_and_never_a_position``:
      ``assert False is True`` on ``sun_watch.blind``.
  m6  ``_clear_blind`` forgets to reset ``_acted``:
      ``test_a_park_made_blind_does_not_latch_past_the_recovery``:
      ``the next live read decides afresh: parked again, not 'has not moved'``
      / ``assert 1 == 2``.
  m7  the blind streak is keyed on the reason text again:
      ``test_a_link_that_flaps_between_down_and_unreadable_keeps_one_clock``:
      ``assert ['info', 'inf..., 'info', ...] == ['info', 'warning']``.
  m8  ``tel is None`` stops ending a blind streak:
      ``test_a_rig_with_no_telescope_is_not_blind_and_clears_a_stale_streak``:
      ``assert True is False``.
  m9  a failed blind park logs every tick (no coalescing):
      ``test_a_park_that_keeps_failing_while_blind_does_not_flood_the_ring``:
      ``first attempt and one heartbeat in 40 ticks, got 40`` / ``40 == 2``.
  m10 the ``_is_parked`` question is skipped before a blind park:
      ``test_a_mount_that_says_it_is_parked_is_not_parked_again_while_blind``:
      ``assert 1 == 0`` (park_calls).
  m11 the stopped case ignores the age (``dt_s=0``): the headline case,
      ``assert 0 == 1`` (park_calls): the aged projection is what reaches the
      cone.
  m12 a position read under a busy goto lane is stored as good:
      ``test_a_position_read_mid_slew_is_not_projected_from``: ``assert 1 == 0``.
  m13 the approach line carries the RA:
      ``test_the_fallback_never_logs_or_publishes_the_position``:
      ``the pointing appeared in a log line or state: ['19.6', '19.63']``.
  m14 the route omits ``sun_watch``:
      ``test_the_route_publishes_blindness_and_never_a_position``:
      ``KeyError: 'sun_watch'``.
  m15 the blind projection ignores ``_acted``:
      ``test_a_blind_park_the_mount_accepts_but_ignores_is_not_re_commanded``:
      ``a park the net cannot verify is not repeated`` / ``assert 4 == 1``.
  m16 the blind projection ignores the hands-off rule:
      ``test_a_run_owns_the_mount_so_the_blind_projection_only_warns``:
      ``assert 1 == 0`` (park_calls).
  m17 the reopen ignores the hands-off rule (``if True:``):
      ``test_the_net_reopens_a_dropped_link_once_per_tick_when_it_is_free``:
      ``AssertionError: hands-off='running': 3 reopen attempts`` /
      ``assert 3 == 0``.
  m18 the reopen is unbounded (no ``wait_for``):
      ``test_a_reopen_that_hangs_is_bounded``: ``TimeoutError`` from the
      test's own 10 s bound on the tick.
  m19 ``last_position_at`` is never recorded:
      ``test_state_is_times_and_booleans_only``:
      ``assert None == 1780272000.0``.
  m20 the first blind line stops saying nothing was read since boot:
      ``test_a_mount_never_read_since_boot_says_so_and_does_not_park``:
      ``assert 'no position has been read since this server started' in ...``.
  m21 ``state()['armed']`` is a constant False:
      ``test_state_is_times_and_booleans_only``: ``assert False is True``.
  m22 ``blind_since`` is published even when not blind:
      ``test_the_route_publishes_blindness_and_never_a_position``:
      ``assert (False is False and 0.0 is None)``.
  m23 a clear projection does not release the hold latch:
      ``test_a_hold_is_announced_again_on_the_next_approach``:
      ``assert ['warning'] == ['warning', 'warning']``.
  m24 turning sun avoidance off leaves the blind streak published, m25 a zero
      cone does the same: both halves of
      ``test_disarming_the_net_ends_a_blind_streak_instead_of_publishing_it``:
      ``assert True is False``.

Two more, added by the independent verifier. m26 SURVIVED the whole of the
original suite; m27 was caught only by the headline case, never by the pole
case written for it (which could not fail):

  m26 the projection always takes the STOPPED separation (``if True`` for
      ``if sep_stopped <= sep_tracking`` in ``_blind_projection``):
      ``test_a_tracking_tube_inside_the_cone_is_projected_as_tracking``:
      ``assert 0 == 1`` (park_calls). No earlier case had a tube whose
      tracking projection was the one inside the cone.
  m27 the clear check inverted (``if sep < cone`` for ``if sep >= cone`` in
      ``_blind_projection``): green on the pole case while the mount answered
      ``is_parked()`` True (that answer alone held the park off), red once the
      pole case also runs with ``says_parked`` False:
      ``test_a_mount_last_seen_at_the_pole_never_parks_however_long_blind
      [False]``: ``assert 1 == 0`` (park_calls).
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from astrodeck import sun_watch as sun_watch_mod
from astrodeck.sun_watch import (BLIND_ERROR_AFTER, BLIND_LOG_EVERY,
                                 BLIND_WARN_AFTER, LEAD_TIME_S, SunWatch,
                                 closest_approach, project_pointing)

from test_sun_watch import (FakeEngine, FakeHub, JUNE_TS, SUN_DEC, SUN_RA_H,  # noqa: F401
                            _rig, cfg, pinned_sun)

#: The two ways the mount goes dark, as the log names them.
LINK_DOWN = "link is down"
UNREADABLE = "will not report its position"
HELD_OFF = "sun watch held off"

# A made-up pointing: west of the pinned Sun and a long way off the equator,
# with a declination no other number in a log line is likely to equal.
WEST_H = 4.37
DEC = 17.3


def _driven(hub, engine=None) -> tuple[SunWatch, dict]:
    """A SunWatch on a clock the test drives, 60 s ticks like production."""
    now = {"t": JUNE_TS}
    w = SunWatch(hub, engine or FakeEngine(), clock=lambda: now["t"],
                 interval_s=60.0)
    return w, now


async def _tick_n(w: SunWatch, now: dict, n: int) -> None:
    for _ in range(n):
        await w.tick()
        now["t"] += 60.0


def _levels(lines, needle: str) -> list[str]:
    return [level for level, message, _source in lines if needle in message]


def _first_age_inside_cone(ra_hours: float, dec_deg: float, cone: float,
                           limit_h: float = 24.0) -> float:
    """Seconds after the read at which a STOPPED mount's projection first
    reaches the cone, computed the way the test reads it rather than
    hard-coded: the tube is aged as a stopped mount for ``age`` seconds, then
    the whole lead window is searched from there."""
    for k in range(1, int(limit_h * 60)):
        age = k * 60.0
        ra, dec = project_pointing(ra_hours, dec_deg, tracking=False, dt_s=age)
        sep, _ = closest_approach(ra, dec, tracking=False,
                                  now=JUNE_TS + age, lead_s=LEAD_TIME_S)
        if sep < cone:
            return age
    raise AssertionError("the stopped projection never reaches the cone")


def _west_stopped_rig(cfg):
    """A STOPPED mount well west of the Sun: clear at read time under both
    projections, and carried into the cone by the sky some time later."""
    cone = cfg.safety.solar_exclusion_deg
    ra = (SUN_RA_H - WEST_H) % 24.0
    hub, tel = _rig(ra_hours=ra, dec_deg=DEC, tracking=False)
    sep_stopped, _ = closest_approach(ra, DEC, tracking=False, now=JUNE_TS,
                                      lead_s=LEAD_TIME_S)
    sep_tracking, _ = closest_approach(ra, DEC, tracking=True, now=JUNE_TS,
                                       lead_s=LEAD_TIME_S)
    assert sep_stopped > cone and sep_tracking > cone, (
        f"precondition: clear at read time under BOTH projections "
        f"({sep_stopped:.1f} deg stopped, {sep_tracking:.1f} deg tracking, "
        f"cone {cone:.0f})")
    return hub, tel, ra, _first_age_inside_cone(ra, DEC, cone)


# ----------------------------------------------------------------- A: blind

async def test_a_dropped_link_is_blind_and_escalates_on_the_same_clock(
        cfg, pinned_sun, bus_lines):
    """A telescope object with ``connected`` False, 60 ticks of 60 s on a clear
    sky: the mirror of WP-03's escalation test. Info on the first tick, ONE
    warning at tick 10, an error at tick 30 and again at tick 60. Before this
    WP the branch went through ``_hold`` and said one thing, once.

    The tube is parked at the pole and nothing was ever read, so there is no
    last-known pointing: the hold must not park anything."""
    hub, tel = _rig(ra_hours=0.0, dec_deg=90.0, tracking=False, parked=True)
    tel.connected = False
    w, now = _driven(hub)

    await _tick_n(w, now, 2 * BLIND_ERROR_AFTER)

    assert _levels(bus_lines, LINK_DOWN) == [
        "info", "warning", "error", "error"], _levels(bus_lines, LINK_DOWN)
    assert tel.park_calls == 0, "a clear sky and no last position: no park"
    warn = next(m for lv, m, _s in bus_lines
                if lv == "warning" and LINK_DOWN in m)
    assert str(BLIND_WARN_AFTER) in warn, warn


async def test_a_link_that_flaps_between_down_and_unreadable_keeps_one_clock(
        cfg, pinned_sun, bus_lines):
    """The streak used to be keyed on the reason TEXT, so a link that alternates
    "not connected" and "connected but will not answer" restarted its count on
    every flip and never reached the warning. One outage is one clock."""
    hub, tel = _rig(ra_hours=0.0, dec_deg=90.0, tracking=False, parked=True)
    tel.position_error = RuntimeError("mount not answering")
    w, now = _driven(hub)

    for i in range(BLIND_WARN_AFTER):
        tel.connected = bool(i % 2)       # down, up-but-mute, down, ...
        await w.tick()
        now["t"] += 60.0

    assert _levels(bus_lines, HELD_OFF) == ["info", "warning"], (
        _levels(bus_lines, HELD_OFF))


@pytest.mark.parametrize("hands_off,expect_calls", [
    (None, 3),
    ("capture", 3),     # a frame in flight is NOT somebody on the mount
    ("running", 0),
    ("goto", 0),
    ("dome", 0),
    ("polar", 0),
])
async def test_the_net_reopens_a_dropped_link_once_per_tick_when_it_is_free(
        cfg, pinned_sun, hands_off, expect_calls):
    """ONE bounded reopen per tick, and only when the hands-off check is clear:
    a sequence run or a goto/dome/polar lane has its hands on the mount, and a
    second task reopening the port under them is the race the rule exists for.
    ``capture`` is deliberately not hands-off (a frame in flight is worth
    nothing next to a mount that moves out of the Sun)."""
    hub, tel = _rig(ra_hours=0.0, dec_deg=90.0, tracking=False, parked=True)
    tel.connected = False
    engine = FakeEngine(running=hands_off == "running")
    if hands_off in ("capture", "goto", "dome", "polar"):
        hub.lanes = [hands_off]
    w, now = _driven(hub, engine)

    await _tick_n(w, now, 3)

    assert tel.connect_calls == expect_calls, (
        f"hands-off={hands_off!r}: {tel.connect_calls} reopen attempts")


async def test_a_reopened_link_is_read_in_the_same_tick(cfg, pinned_sun,
                                                        bus_lines):
    """If the reopen works the tick carries on and reads the position: the net
    was blind for no tick at all, so it says nothing and publishes no
    blindness."""
    hub, tel = _rig(ra_hours=0.0, dec_deg=90.0, tracking=False, parked=True)
    tel.connected = False
    tel.reconnects = True
    w, now = _driven(hub)

    await w.tick()

    assert tel.connect_calls == 1 and tel.connected is True
    assert w.state()["blind"] is False
    assert w.state()["last_position_at"] == JUNE_TS, (
        "the position was read in the same tick as the reopen")
    assert bus_lines == [], bus_lines


async def test_a_reopen_that_hangs_is_bounded(cfg, pinned_sun, bus_lines,
                                              monkeypatch):
    """The reopen runs under ``wait_for``: a mount that accepts the port and
    never answers must cost one bounded wait, not the tick."""
    monkeypatch.setattr(sun_watch_mod, "RECONNECT_TIMEOUT_S", 0.05)
    hub, tel = _rig(ra_hours=0.0, dec_deg=90.0, tracking=False, parked=True)
    tel.connected = False

    async def _hang() -> None:
        tel.connect_calls += 1
        await asyncio.sleep(3600)

    tel.connect = _hang
    w, now = _driven(hub)

    await asyncio.wait_for(w.tick(), timeout=10.0)

    assert tel.connect_calls == 1
    assert _levels(bus_lines, LINK_DOWN) == ["info"], (
        "a hung reopen is still a blind tick")


# ------------------------------------------------------- B: last-known park

@pytest.mark.parametrize("failure", ["unreadable", "link_down"])
async def test_the_last_known_position_parks_a_mount_the_sun_is_closing_on(
        cfg, pinned_sun, bus_lines, failure):
    """The headline case. The mount is read once, STOPPED and clear under both
    projections; then it goes dark. Aged as a stopped mount the sky carries it
    into the cone, and the net must park it BEFORE the Sun arrives although it
    can no longer see the tube. The threshold is computed, not guessed, and
    the tick before it must still be quiet (so this is not a net that parks
    the moment it goes blind).

    ``link_down`` additionally proves the reopen came first, in the same tick:
    the last two calls the mount saw were a connect and then the park."""
    hub, tel, ra, threshold_s = _west_stopped_rig(cfg)
    assert threshold_s >= 10 * 60.0, (
        f"precondition: the cone is {threshold_s / 60:.0f} min away, so a net "
        f"that parked on going blind would be caught")
    w, now = _driven(hub)

    await w.tick()                                    # the last good read
    assert tel.park_calls == 0 and bus_lines == [], "quiet at read time"
    read_at = now["t"]
    if failure == "unreadable":
        tel.position_error = RuntimeError("mount not answering")
    else:
        tel.connected = False

    steps = int(threshold_s // 60)
    for k in range(1, steps):
        now["t"] = read_at + k * 60.0
        await w.tick()
    assert tel.park_calls == 0, (
        f"one tick short of the threshold ({steps - 1} min aged) must not park")

    now["t"] = read_at + steps * 60.0
    await w.tick()

    assert tel.park_calls == 1
    assert hub.epoch_bumps == 1, "the motion fence is bumped like every park"
    assert tel.locked_during_park == [True], (
        "the blind park runs under the hub motion lock, the same block as the "
        "live park")
    park_lines = [m for lv, m, _s in bus_lines
                  if lv == "error" and "last position, read" in m]
    assert len(park_lines) == 1, bus_lines
    assert "projected as stopped" in park_lines[0], park_lines[0]
    assert f"read {steps} min ago" in park_lines[0], park_lines[0]
    assert any("mount parked" in m for _lv, m, _s in bus_lines), (
        "the 'parked' line is logged after the await, like the live path")
    if failure == "link_down":
        assert tel.calls[-2:] == ["connect", "park"], (
            f"the reopen must come first: {tel.calls[-3:]}")

    await _tick_n(w, now, 3)
    assert tel.park_calls == 1, "a second tick must not park again"


async def test_a_tracking_tube_inside_the_cone_is_projected_as_tracking(
        cfg, pinned_sun, bus_lines):
    """The other half of "project BOTH ways". A TRACKING tube holds its
    pointing, so a tube read 10 degrees east of the Sun is still 10 degrees from
    it however long the link is down, while the same position aged as a STOPPED
    mount has been carried east by the sky and is nowhere near it. A net that
    only ever projected the stopped case would call this tube clear after three
    hours and leave it pointing at the Sun; the smaller separation wins.

    The tube is read under a sequence run (a warning hold, no park) and the run
    ends while the mount is dark, so the park is the blind fallback's. Both
    separations are computed and asserted first: tracking inside the cone,
    stopped clear of it.

    Verifier mutant: ``if True`` for ``if sep_stopped <= sep_tracking`` (always
    the stopped projection) survived every case before this one; with it this
    case fails ``assert 0 == 1`` on ``park_calls``."""
    cone = cfg.safety.solar_exclusion_deg
    ra = (SUN_RA_H + 10.0 / 15.0) % 24.0
    age_s = 3 * 3600.0
    ra_aged, _ = project_pointing(ra, SUN_DEC, tracking=False, dt_s=age_s)
    sep_stopped, _ = closest_approach(ra_aged, SUN_DEC, tracking=False,
                                      now=JUNE_TS + age_s, lead_s=LEAD_TIME_S)
    sep_tracking, _ = closest_approach(ra, SUN_DEC, tracking=True,
                                       now=JUNE_TS + age_s, lead_s=LEAD_TIME_S)
    assert sep_tracking < cone <= sep_stopped, (
        f"precondition: tracking {sep_tracking:.1f} deg is inside the "
        f"{cone:.0f} deg cone and stopped {sep_stopped:.1f} deg is clear of it")
    hub, tel = _rig(ra_hours=ra, dec_deg=SUN_DEC, tracking=True)
    engine = FakeEngine(running=True)
    w, now = _driven(hub, engine)

    await w.tick()                  # read under a run: held, but remembered
    assert tel.park_calls == 0, "precondition: the run owns the mount"
    tel.position_error = RuntimeError("mount not answering")
    engine.running = False
    now["t"] = JUNE_TS + age_s
    await w.tick()

    assert tel.park_calls == 1
    park_line = next(m for lv, m, _s in bus_lines
                     if lv == "error" and "last position, read" in m)
    assert "projected as tracking" in park_line, park_line
    assert f"read {age_s / 60:.0f} min ago" in park_line, park_line


async def test_the_fallback_never_logs_or_publishes_the_position(
        cfg, pinned_sun, bus_lines):
    """#140: a mount's pointing is a latitude oracle. Separations in degrees and
    ages in minutes are fine; the RA/Dec it was last read at are not, in any
    log line or in the published state."""
    hub, tel, ra, threshold_s = _west_stopped_rig(cfg)
    w, now = _driven(hub)
    await w.tick()
    read_at = now["t"]
    tel.position_error = RuntimeError("mount not answering")
    now["t"] = read_at + threshold_s
    await w.tick()
    assert tel.park_calls == 1, "precondition: the fallback did fire"

    forbidden = set()
    for value in (ra, DEC, ra * 15.0):
        forbidden |= {f"{value:.1f}", f"{value:.2f}", f"{value:.3f}", repr(value)}
    haystack = "\n".join(m for _l, m, _s in bus_lines) + json.dumps(w.state())
    leaked = sorted(f for f in forbidden if f in haystack)
    assert leaked == [], f"the pointing appeared in a log line or state: {leaked}"


@pytest.mark.parametrize("says_parked", [True, False])
async def test_a_mount_last_seen_at_the_pole_never_parks_however_long_blind(
        cfg, pinned_sun, bus_lines, says_parked):
    """Edge: a mount last read at the pole projects clear in both cases at any
    age (the sky turning under a pole-pointing tube does not move it), so 26
    hours of blindness is loud (the blind escalation) and parks nothing.

    ``says_parked`` False is the arm with teeth. A mount that answers
    ``is_parked()`` True is held off by that answer ALONE, so with it the
    projection could be wrong about the pole at every age and this case would
    still pass (verifier mutant: ``if sep < cone`` for ``if sep >= cone`` in
    ``_blind_projection`` stayed green on the parked arm and went red on the
    other: ``assert 1 == 0`` on ``park_calls``)."""
    hub, tel = _rig(ra_hours=0.0, dec_deg=90.0, tracking=False,
                    parked=says_parked)
    worst = min(closest_approach(0.0, 90.0, tracking=t,
                                 now=JUNE_TS + h * 3600.0)[0]
                for t in (False, True) for h in range(0, 27))
    assert worst > cfg.safety.solar_exclusion_deg, (
        f"precondition: the pole is clear at every age ({worst:.1f} deg)")
    w, now = _driven(hub)
    await w.tick()
    tel.position_error = RuntimeError("mount not answering")

    for _ in range(26 * 12):
        now["t"] += 300.0
        await w.tick()

    assert tel.park_calls == 0
    assert "error" in _levels(bus_lines, UNREADABLE), "still escalated"


async def test_a_mount_never_read_since_boot_says_so_and_does_not_park(
        cfg, pinned_sun, bus_lines):
    """No position since the server started means nothing to project from.
    Parking on a guess would park a working rig, so the net does not, and the
    FIRST blind line says why it is not doing more."""
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC, tracking=False)
    tel.position_error = RuntimeError("mount not answering")
    w, now = _driven(hub)

    await _tick_n(w, now, 5)

    assert tel.park_calls == 0
    first = next(m for _lv, m, _s in bus_lines if UNREADABLE in m)
    assert "no position has been read since this server started" in first, first
    assert w.state()["last_position_at"] is None


async def test_a_run_owns_the_mount_so_the_blind_projection_only_warns(
        cfg, pinned_sun, bus_lines):
    """Under exactly the live path's hands-off rule: a sequence run owns its own
    aborts, so a last-known pointing inside the cone is a warning hold (said
    ONCE, not every minute for the whole run) and no park. The same tube is
    parked as soon as the run is over: the hold is not a latch."""
    ra = (SUN_RA_H - 10.0 / 15.0) % 24.0          # 10 deg west, tracking
    hub, tel = _rig(ra_hours=ra, dec_deg=SUN_DEC, tracking=True)
    sep, _ = closest_approach(ra, SUN_DEC, tracking=True, now=JUNE_TS)
    assert sep < cfg.safety.solar_exclusion_deg, "precondition: inside the cone"
    engine = FakeEngine(running=True)
    w, now = _driven(hub, engine)

    await w.tick()                      # live read under the run: a warning
    tel.position_error = RuntimeError("mount not answering")
    holds_before = len(_levels(bus_lines, "run is in progress"))
    await _tick_n(w, now, 5)

    assert tel.park_calls == 0
    run_holds = _levels(bus_lines, "run is in progress")
    assert run_holds[holds_before:] == ["warning"], (
        f"one warning hold for the whole blind stretch, got {run_holds}")

    engine.running = False
    await w.tick()
    assert tel.park_calls == 1, "the hold must not latch past the run"


async def test_a_position_read_mid_slew_is_not_projected_from(
        cfg, pinned_sun, bus_lines):
    """A position read while a goto lane is busy is a tube in transit: its
    pointing is unknown, not the number the mount reported. The fallback is
    skipped (and says why) instead of projecting from a transit position."""
    ra = (SUN_RA_H - 10.0 / 15.0) % 24.0
    hub, tel = _rig(ra_hours=ra, dec_deg=SUN_DEC, tracking=True)
    sep, _ = closest_approach(ra, SUN_DEC, tracking=True, now=JUNE_TS)
    assert sep < cfg.safety.solar_exclusion_deg, (
        "precondition: projected from this position the net WOULD park")
    hub.lanes = ["goto"]
    w, now = _driven(hub)
    await w.tick()                      # read mid-slew: held off, stored as unsure
    hub.lanes = []
    tel.position_error = RuntimeError("mount not answering")

    await _tick_n(w, now, 3)

    assert tel.park_calls == 0
    first = next(m for _lv, m, _s in bus_lines if UNREADABLE in m)
    assert "while the mount was being moved" in first, first
    assert w.state()["last_position_at"] == JUNE_TS, (
        "a read did happen, and the published time says so")


async def test_a_mount_that_says_it_is_parked_is_not_parked_again_while_blind(
        cfg, pinned_sun, bus_lines):
    """The last-known pointing is stale the moment the mount can answer that it
    is PARKED: parking a parked mount is a no-op, and the live path's "park
    position is not safe" verdict would be an accusation made on a stale
    position. It holds at info instead."""
    hub, tel, ra, threshold_s = _west_stopped_rig(cfg)
    w, now = _driven(hub)
    await w.tick()
    read_at = now["t"]
    tel.position_error = RuntimeError("mount not answering")
    tel.parked = True                   # it answers is_parked(), not position
    now["t"] = read_at + threshold_s

    await w.tick()

    assert tel.park_calls == 0
    assert "error" not in _levels(bus_lines, "PARK"), bus_lines
    assert any("reports itself parked" in m for _lv, m, _s in bus_lines)


async def test_a_park_made_blind_does_not_latch_past_the_recovery(
        cfg, pinned_sun, bus_lines):
    """A blind park is accepted but UNVERIFIED: the net could not read the tube
    afterwards. So recovery must let the next live read decide afresh. Park
    blind, recover, and when the live read shows the tube back in the cone (an
    operator unparked, or the park never took) the net must park again rather
    than call it "THE MOUNT HAS NOT MOVED" on the strength of a park it never
    saw happen."""
    hub, tel, ra, threshold_s = _west_stopped_rig(cfg)
    w, now = _driven(hub)
    await w.tick()
    read_at = now["t"]
    tel.position_error = RuntimeError("mount not answering")
    now["t"] = read_at + threshold_s
    await w.tick()
    assert tel.park_calls == 1, "precondition: the blind park happened"

    tel.position_error = None           # the link recovers
    tel.parked = False
    tel.ra_hours, tel.dec_deg = SUN_RA_H, SUN_DEC     # ... pointing at the Sun
    tel.tracking = False
    now["t"] += 60.0
    await w.tick()

    assert tel.park_calls == 2, (
        "the next live read decides afresh: parked again, not 'has not moved'")
    assert not any("HAS NOT MOVED" in m for _lv, m, _s in bus_lines)


async def test_a_blind_park_the_mount_accepts_but_ignores_is_not_re_commanded(
        cfg, pinned_sun, bus_lines):
    """Audit #15's shape, blind: the park is ACCEPTED and the mount does not
    even claim to be parked. The net cannot read the tube to see that it did
    not move, so it must not re-command the park every minute for the rest of
    the outage (``_acted`` holds it), and it says ONCE that it can no longer
    check. Without the guard the only thing between this and a park every tick
    is the mount answering ``is_parked``, which a lying driver does not."""
    hub, tel, ra, threshold_s = _west_stopped_rig(cfg)

    async def _lying_park() -> None:
        tel.park_calls += 1
        tel.calls.append("park")
        tel.locked_during_park.append(True)

    tel.park = _lying_park
    w, now = _driven(hub)
    await w.tick()
    read_at = now["t"]
    tel.position_error = RuntimeError("mount not answering")
    now["t"] = read_at + threshold_s
    await w.tick()
    assert tel.park_calls == 1 and tel.parked is False, (
        "precondition: accepted, and the mount does not say it is parked")

    await _tick_n(w, now, 3)

    assert tel.park_calls == 1, "a park the net cannot verify is not repeated"
    unverifiable = [m for _lv, m, _s in bus_lines
                    if "cannot check that the tube moved" in m]
    assert len(unverifiable) == 1, unverifiable
    assert not any("HAS NOT MOVED" in m for _lv, m, _s in bus_lines), (
        "blind, the net has not SEEN the mount fail to move")


async def test_a_hold_is_announced_again_on_the_next_approach(
        cfg, pinned_sun, bus_lines):
    """A stopped tube sweeps the whole sky once per sidereal day, so a
    last-known pointing is projected INTO the cone, out of it, and into it
    again a day later. While a run owns the mount each approach is a warning
    hold, said once per approach: 30 hours blind is two approaches, not one
    (a latch the clear sky never released would swallow the second) and not a
    warning per tick."""
    hub, tel, ra, threshold_s = _west_stopped_rig(cfg)
    engine = FakeEngine(running=True)
    w, now = _driven(hub, engine)
    await w.tick()                          # clear at read time: silent
    read_at = now["t"]
    tel.position_error = RuntimeError("mount not answering")

    for k in range(1, 30 * 12 + 1):         # 30 hours in 5 minute steps
        now["t"] = read_at + k * 300.0
        await w.tick()

    holds = _levels(bus_lines, "run is in progress")
    assert holds == ["warning", "warning"], holds
    assert tel.park_calls == 0, "a run owns the mount for the whole stretch"


async def test_a_park_that_keeps_failing_while_blind_does_not_flood_the_ring(
        cfg, pinned_sun, bus_lines):
    """A dead link fails every park. The net still RETRIES every tick (the Sun
    keeps closing), but a sweep through the cone is hours of ticks, and two
    error lines a minute is the 417-line flood that flushed the 200-entry ring
    on 2026-08-09 (see ``BLIND_LOG_EVERY``). The failure is logged on the
    first attempt and on a cadence."""
    ra = (SUN_RA_H - 10.0 / 15.0) % 24.0
    hub, tel = _rig(ra_hours=ra, dec_deg=SUN_DEC, tracking=True)
    engine = FakeEngine(running=True)
    w, now = _driven(hub, engine)
    await w.tick()                      # read under a run: held, stored
    engine.running = False
    tel.position_error = RuntimeError("mount not answering")
    tel.park_error = RuntimeError("serial port is gone")
    ticks = BLIND_LOG_EVERY + 10

    await _tick_n(w, now, ticks)

    assert tel.park_calls == ticks, "retried every tick, quietly"
    failures = [m for _lv, m, _s in bus_lines if "PARK FAILED" in m]
    assert len(failures) == 2, (
        f"first attempt and one heartbeat in {ticks} ticks, got "
        f"{len(failures)}")


# ----------------------------------------------------------- tel is None

async def test_a_rig_with_no_telescope_is_not_blind_and_clears_a_stale_streak(
        cfg, pinned_sun, bus_lines):
    """Backlog ruling: ``tel is None`` stays a latched INFO hold. A rig that was
    never connected must not page, however long it sits there. And an operator
    disconnect (which empties ``hub.devices``) ends a blind streak instead of
    leaving ``blind: true`` published for a mount that is deliberately gone."""
    hub = FakeHub()                     # no telescope, never was
    w, now = _driven(hub)
    await _tick_n(w, now, 2 * BLIND_ERROR_AFTER)
    assert _levels(bus_lines, "no telescope is connected") == ["info"], (
        "one info line, latched, for the whole stretch")
    assert not any(lv in ("warning", "error") for lv, _m, _s in bus_lines)
    assert w.state()["blind"] is False

    hub2, tel = _rig(ra_hours=0.0, dec_deg=90.0, tracking=False, parked=True)
    tel.connected = False
    w2, now2 = _driven(hub2)
    await _tick_n(w2, now2, 3)
    assert w2.state()["blind"] is True, "precondition: it was blind"
    hub2.devices.clear()                # the operator disconnected the rig
    await w2.tick()
    assert w2.state()["blind"] is False
    assert w2.state()["blind_since"] is None


@pytest.mark.parametrize("disarm", ["solar_avoidance_off", "zero_cone"])
async def test_disarming_the_net_ends_a_blind_streak_instead_of_publishing_it(
        cfg, pinned_sun, bus_lines, disarm):
    """``safety.solar_avoidance`` False and a 0 degree cone are the owner's
    deliberate off-switches. A net that is switched off is not blind, and
    ``blind: true`` published for it would be a standing complaint about a mount
    nobody asked this net to look at."""
    hub, tel = _rig(ra_hours=0.0, dec_deg=90.0, tracking=False, parked=True)
    tel.connected = False
    w, now = _driven(hub)
    await _tick_n(w, now, 3)
    assert w.state()["blind"] is True, "precondition: it was blind"

    if disarm == "solar_avoidance_off":
        cfg.safety.solar_avoidance = False
    else:
        cfg.safety.solar_exclusion_deg = 0.0
    await w.tick()

    assert w.state()["blind"] is False
    assert w.state()["blind_since"] is None


# ------------------------------------------------------------ C: published

async def test_state_is_times_and_booleans_only(cfg, pinned_sun):
    """The shape that goes on the wire: no position of any kind, and
    ``blind_since`` is null unless the net is blind."""
    hub, tel = _rig(ra_hours=0.0, dec_deg=90.0, tracking=False, parked=True)
    w, now = _driven(hub)
    assert w.state() == {"blind": False, "blind_since": None,
                         "position_unknown": False,
                         "position_unknown_since": None,
                         "last_position_at": None, "armed": False}

    await w.tick()
    assert w.state()["last_position_at"] == JUNE_TS

    tel.position_error = RuntimeError("mount not answering")
    now["t"] = JUNE_TS + 600.0
    await w.tick()
    st = w.state()
    assert st["blind"] is True
    assert st["blind_since"] == JUNE_TS + 600.0
    assert st["last_position_at"] == JUNE_TS, (
        "the last GOOD read, not the last tick")
    assert set(st) == {"blind", "blind_since", "position_unknown",
                       "position_unknown_since", "last_position_at", "armed"}
    assert all(isinstance(v, (bool, float, type(None))) for v in st.values())

    w2 = SunWatch(hub, FakeEngine(), clock=lambda: JUNE_TS, interval_s=3600.0)
    w2.start()
    try:
        assert w2.state()["armed"] is True
    finally:
        await w2.stop()
    assert w2.state()["armed"] is False


def _viewer_client(tmp_path, monkeypatch):
    """The app as a VIEWER, the least-privileged role, with no lifespan (so the
    module singleton's task never starts). Mirrors the pattern in
    test_no_route_leaks_the_site_coordinates.py."""
    import astrodeck.api.app as app_module
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.auth import (principal_for_role, reset_active_provider,
                                set_active_provider)
    from astrodeck.config import ConfigStore

    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())

    class _Fixed:
        name = "fake"

        async def resolve(self, request):
            return principal_for_role("viewer")

    reset_active_provider()
    set_active_provider(_Fixed())
    return app_module, TestClient(app_module.create_app()), reset_active_provider


def test_the_route_publishes_blindness_and_never_a_position(
        tmp_path, monkeypatch, cfg, pinned_sun):
    """GET /api/safety/state, as a VIEWER, carries ``sun_watch`` with the blind
    flag, a non-null ``blind_since`` while blind and ``false``/null after one
    good read, and nothing that locates the tube. The role gating is the
    route's own (``view.status``); times and booleans need no redaction, and
    this proves the response holds no position-shaped key."""
    app_module, client, reset = _viewer_client(tmp_path, monkeypatch)
    try:
        hub, tel = _rig(ra_hours=0.0, dec_deg=90.0, tracking=False, parked=True)
        w, now = _driven(hub)
        monkeypatch.setattr(app_module, "sun_watch", w)

        asyncio.run(w.tick())
        body = client.get("/api/safety/state")
        assert body.status_code == 200, body.text
        sw = body.json()["sun_watch"]
        assert sw["blind"] is False and sw["blind_since"] is None
        assert sw["last_position_at"] == JUNE_TS

        tel.position_error = RuntimeError("mount not answering")
        now["t"] = JUNE_TS + 120.0
        asyncio.run(w.tick())
        sw = client.get("/api/safety/state").json()["sun_watch"]
        assert sw["blind"] is True, sw
        assert sw["blind_since"] == JUNE_TS + 120.0
        assert sw["last_position_at"] == JUNE_TS

        # Times and booleans, exactly these keys (#888 added the two
        # position-unknown ones): nothing that locates the tube can ride
        # along without this key set (or the type check) changing.
        assert set(sw) == {"blind", "blind_since", "position_unknown",
                           "position_unknown_since", "last_position_at",
                           "armed"}, sw
        assert all(isinstance(v, (bool, float, type(None)))
                   for v in sw.values()), sw

        tel.position_error = None
        now["t"] = JUNE_TS + 180.0
        asyncio.run(w.tick())
        sw = client.get("/api/safety/state").json()["sun_watch"]
        assert sw["blind"] is False and sw["blind_since"] is None, sw
        assert sw["last_position_at"] == JUNE_TS + 180.0
    finally:
        reset()
