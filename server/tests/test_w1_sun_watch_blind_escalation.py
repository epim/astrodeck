"""WP-03: the unreadable-mount hold must escalate, not go quiet (issue #137).

Before this fix, the ``pos is None`` branch of ``SunWatch.tick`` went through
``_hold``, which latches on the reason string -- so a mount that never becomes
readable again produces exactly one info-level line and then silence for the
rest of the outage. On 2026-09-23 that outage lasted 8h24m through sunrise and
the only record of it was that single info line, which no alert sink even
receives (``alerting.py`` maps only warning and error). The Sun does not stop
moving because the position feed did, so this hold has to run on ITS OWN
clock instead of latching on the mount's.

The fix shape (backlog ruling D-nn does not gate this WP; it has no decision
row) is the plan's one line for WP-03: "An unreadable-mount hold repeats at
warning and then error, with a repetition count, like ``dawn_park._fail``."
That is issue #137's suggested fix item 1 exactly: quiet on the first tick,
one warning once the outage has run ``BLIND_WARN_AFTER`` ticks, then a
repeating error every ``BLIND_LOG_EVERY`` ticks from ``BLIND_ERROR_AFTER`` on.
Items 2-4 of the issue (last-known-position fallback and a park attempt while
blind, publishing ``blind_since`` on ``/api/safety/state``) touch files this
WP does not own (``hub.py``, ``api/app.py``) and are left for the issue to
stay open against.

Shares its rig/config fixtures with ``test_sun_watch.py`` (rootdir-relative
import, the pattern already used across this suite, e.g.
``test_capture_geometry.py`` imports fixtures from ``test_gallery``).
"""
from __future__ import annotations

import pytest

from astrodeck import sun_watch as sun_watch_mod
from astrodeck.sun_watch import (BLIND_ERROR_AFTER, BLIND_LOG_EVERY,
                                 BLIND_WARN_AFTER, SunWatch)

from test_sun_watch import FakeEngine, JUNE_TS, SUN_DEC, SUN_RA_H, _rig, cfg, pinned_sun  # noqa: F401

BLIND_NEEDLE = "will not report its position"


def _levels_for(lines, needle: str) -> list[str]:
    return [level for level, message, _source in lines if needle in message]


def _ticking_watch(hub) -> tuple[SunWatch, dict]:
    """A SunWatch whose clock the test drives explicitly, one 60 s tick at a
    time -- CHECK_INTERVAL_S is 60 s in production, so a tick and a minute are
    the same unit and BLIND_WARN_AFTER/BLIND_ERROR_AFTER read directly as
    minutes."""
    now = {"t": JUNE_TS}
    w = SunWatch(hub, FakeEngine(), clock=lambda: now["t"], interval_s=60.0)
    return w, now


async def _tick_n(w: SunWatch, now: dict, n: int) -> None:
    for _ in range(n):
        await w.tick()
        now["t"] += 60.0


async def test_sun_watch_escalates_when_blind(cfg, pinned_sun, bus_lines):
    """Issue #137's own acceptance test, verbatim: a telescope double that is
    ``connected`` but whose ``get_position`` always raises, ticked 40 times at
    60 s steps. Today (before this fix) the only output is the single info
    line from the first tick; a warning or error naming the blindness must
    appear by 30 minutes in.

    Named mutant: in ``_blind``, add ``if n > 1: return`` right after
    ``n = self._blind_count`` (brings back the #137 shape -- one line, then
    silence for the rest of the outage). Under that mutant this assertion
    fails with:
        AssertionError: no warning/error line named the blindness across
        40 ticks: ['info']
    """
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC)
    tel.position_error = RuntimeError("mount not answering")
    w, now = _ticking_watch(hub)

    await _tick_n(w, now, 40)

    levels = _levels_for(bus_lines, BLIND_NEEDLE)
    assert any(level in ("warning", "error") for level in levels), (
        f"no warning/error line named the blindness across 40 ticks: {levels}")


async def test_the_first_tick_is_quiet_info_not_an_immediate_alarm(
        cfg, pinned_sun, bus_lines):
    """A single lost read must not page anybody -- that quiet first line is
    the existing, correct behaviour for a momentary glitch, and this WP must
    not turn every blip into an instant warning."""
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC)
    tel.position_error = RuntimeError("mount not answering")
    w, now = _ticking_watch(hub)

    await _tick_n(w, now, 1)

    levels = _levels_for(bus_lines, BLIND_NEEDLE)
    assert levels == ["info"], levels


async def test_it_is_silent_between_the_first_tick_and_the_warning(
        cfg, pinned_sun, bus_lines):
    """No repetition-count spam every tick -- only the two escalation points
    say anything, matching ``dawn_park._fail``'s "loud once, then quiet"
    shape."""
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC)
    tel.position_error = RuntimeError("mount not answering")
    w, now = _ticking_watch(hub)

    await _tick_n(w, now, BLIND_WARN_AFTER - 1)

    assert _levels_for(bus_lines, BLIND_NEEDLE) == ["info"], (
        "a tick short of the warning threshold must not have escalated yet")


async def test_it_warns_once_at_the_threshold_then_escalates_to_error(
        cfg, pinned_sun, bus_lines):
    """The two-tier escalation, in order, with a repetition count in the
    text -- the ``dawn_park._fail`` shape the plan names."""
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC)
    tel.position_error = RuntimeError("mount not answering")
    w, now = _ticking_watch(hub)

    await _tick_n(w, now, BLIND_WARN_AFTER)
    assert _levels_for(bus_lines, BLIND_NEEDLE) == ["info", "warning"], (
        "exactly one warning at the threshold tick")

    await _tick_n(w, now, BLIND_ERROR_AFTER - BLIND_WARN_AFTER - 1)
    assert _levels_for(bus_lines, BLIND_NEEDLE) == ["info", "warning"], (
        "still quiet in between -- one tick short of the error threshold")

    await _tick_n(w, now, 1)
    levels = _levels_for(bus_lines, BLIND_NEEDLE)
    assert levels == ["info", "warning", "error"], levels
    # The repetition count is IN the text, the same discipline dawn_park's
    # tail (" (attempt N, still failing after M min)") follows.
    error_line = next(m for l, m, _s in bus_lines
                      if l == "error" and BLIND_NEEDLE in m)
    assert str(BLIND_ERROR_AFTER) in error_line, error_line


async def test_the_error_repeats_on_a_cadence_not_every_tick(
        cfg, pinned_sun, bus_lines):
    """An outage that outlives the error threshold must keep saying so --
    the exact bug in #137 was a net that fell silent for 8h24m -- but at
    ``BLIND_LOG_EVERY``'s cadence, not once per tick, or a long outage floods
    the log the way dawn_park's pre-``FAIL_LOG_EVERY`` behaviour once did."""
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC)
    tel.position_error = RuntimeError("mount not answering")
    w, now = _ticking_watch(hub)

    await _tick_n(w, now, BLIND_ERROR_AFTER + BLIND_LOG_EVERY)

    errors = _levels_for(bus_lines, BLIND_NEEDLE).count("error")
    assert errors == 2, (
        f"expected exactly two error lines (at {BLIND_ERROR_AFTER} and "
        f"{BLIND_ERROR_AFTER + BLIND_LOG_EVERY} ticks), got {errors}")


async def test_recovery_clears_the_streak_and_a_fresh_outage_restarts_it(
        cfg, pinned_sun, bus_lines):
    """Once the position is readable again the count must not carry over --
    otherwise a rig with two short, unrelated outages a night apart would
    reach ``error`` on the second one's very first tick. The rig is parked
    antisolar and tracking, so the recovery tick's real position read does
    not also trigger an (unrelated) park -- this test is about the blind
    counter, not the parking decision."""
    hub, tel = _rig(ra_hours=(SUN_RA_H + 12.0) % 24.0, dec_deg=0.0,
                    tracking=True)
    tel.position_error = RuntimeError("mount not answering")
    w, now = _ticking_watch(hub)

    await _tick_n(w, now, BLIND_WARN_AFTER)
    assert _levels_for(bus_lines, BLIND_NEEDLE) == ["info", "warning"]

    tel.position_error = None            # the read recovers
    await _tick_n(w, now, 1)

    tel.position_error = RuntimeError("mount not answering again")
    await _tick_n(w, now, 1)
    assert _levels_for(bus_lines, BLIND_NEEDLE)[-1] == "info", (
        "a fresh outage must start over at info, not resume mid-escalation")
