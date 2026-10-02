# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The clocked group harness holds the hub's meridian on the night's clock,
not on the real-time status poll's (#368; spec 5.7 as built S4, the
harness clock).

`Hub._status_loop` polls every 2 s of REAL time, and each poll writes
``hub.last_meridian``, the meridian block the engine reads for two things
that reach the published state: the ``live`` chip's ``meridian_eta_s``
while the flip is within ``meridian_flip_warn_min`` (`_live_block`), and
the ETA's flip cost while a flip is due before the run would end
(`_flip_pending`). A night of hours runs in about a second of real time,
so whether a poll landed in it, and at which fake instant, was the
scheduler's choice: the same night on two fresh hubs published a ``live``
key in one and not in the other, in two tries of three (#368). When no
later poll landed, the engine read the block the connect's first poll left
for the whole night, computed once for wherever the mount pointed before
its first goto: in one such run, 9.972 h to the flip from the first state
to the last, so a night that crossed its meridian showed no chip at all.

Now `Night` stops the hub's status poll for the night, stops it again if
anything restarts it, drops the block the poll left, and holds
``hub.last_meridian`` itself: the hub's own `_compute_meridian`, on the
night's clock, at the night's start, at every wake and after every slew,
the moments its answer can change.

THE NIGHT is the one #368 was found on: the golden flow plan from the
harness's ``T0``. NGC 7331 crosses the fixture site's meridian 180 min into
its 195, so the night enters the 15 min flip-warn window at 9960 s, holds
for the flip point, makes the lead-time attempt at 10172.28 s (which flips
nothing on a mount that takes its side from the hour angle), holds for the
retry, flips at 10785.603 s and runs on past the crossing. Until #366 the
retry fired at the crossing itself and the flip came at 10800.644 s; the
pins below moved with it (S5-ENG-FLIP; re-pinned by the S5/S6 integration,
S56-INTEG).

Secondary evidence, since #368 is intermittent and one green run proves
nothing: the crossing night run 20 times in a row on fresh hubs, each
trace compared byte for byte with the first (observed 2026-09-28, in the
scratch copy). With the harness as it was, 19 of the 20 differed from the
first, in 17 distinct traces, and only 4 of the 20 published a ``live``
key at all. With the harness as it is now, all 20 were one trace, each
with the chip. That run is not kept in the suite, at over a minute for the
20; the first case below is its deterministic form.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (long lines wrapped). Every mutant was applied in a
private scratch copy of server/ (scratchpad/S5-SIM-mut), never in the
shared tree (#254).
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.catalog.coords as coords_mod
from _group_harness import (LON, T0, TERMINAL, Night, close_night_hub,
                            group_hub, group_store, night_hub)  # noqa: F401
from astrodeck import events
from test_group_rotation import _golden_as_recorded

#: A meridian block as a poll before the night might leave it: for wherever
#: the mount pointed then, and with no plan loaded, ``flip_enabled`` false.
#: Written by the case over whatever the connect's poll left, so the case
#: does not depend on whether that poll has run yet.
_CONNECT_TIME = {"status": "counting", "hours_to_flip": 5.5,
                 "flip_enabled": False, "pier_side": "west"}


def _status_polls() -> list[asyncio.Task]:
    """Every task running `Hub._status_loop` that has not ended and has not
    been told to end, found by its coroutine rather than through
    ``hub._status_task``, so a poll started by any door is seen. A task
    cancelled before it next runs is not done until the loop turns, and its
    body never runs again, so it does not count."""
    return [t for t in asyncio.all_tasks()
            if not t.done() and not t.cancelling()
            and getattr(t.get_coro(), "__qualname__", "") == "Hub._status_loop"]


def _hours_to_flip(ra: float, t: float) -> float:
    """What the hub's poll derives for a mount pointing at ``ra`` at ``t``,
    to its four places: minus the hour angle (`Hub._compute_meridian`)."""
    return round(-coords_mod.hour_angle_h(ra, LON, t), 4)


def _chip(ttf_h: float, warn_min: float) -> int | None:
    """The ``live.meridian_eta_s`` `_live_block` builds from ``ttf_h``: in
    whole seconds while the flip is ahead and within the warn lead, and
    none otherwise."""
    if 0.0 < ttf_h and ttf_h * 60.0 <= warn_min:
        return round(ttf_h * 3600.0)
    return None


async def _crossing_night(monkeypatch, *, restart_the_poll: bool = False):
    """Run the crossing night on a fresh hub. At every ``sequence`` state
    the engine publishes, up to the first terminal one, it records the
    night's clock, where the mount points, the published chip, the block
    the engine built it from, the warn lead and how many status polls are
    running. With ``restart_the_poll``, the night's first frame calls
    ``hub.ensure_status_poller()``, the door every connect path opens.
    Returns ``(night, done, rows, the block the Night left in place)``."""
    hub, popped = await night_hub(monkeypatch)
    try:
        assert _status_polls(), (
            "premise: the connect started the hub's real-time status poll")
        hub.last_meridian = dict(_CONNECT_TIME)
        night = Night(hub, monkeypatch, t0=T0)
        left = hub.last_meridian
        tel = hub.devices["telescope"]
        rows: list[dict] = []
        inner = events.bus.publish

        def publish(topic, **payload):
            if topic == "sequence" and not (rows and rows[-1]["state"]
                                            in TERMINAL):
                mer = hub.last_meridian or {}
                rows.append({
                    "t": night.clock.t, "ra": tel.rig.ra_hours,
                    "state": payload.get("state"),
                    "detail": payload.get("detail"),
                    "chip": (payload.get("live") or {}).get("meridian_eta_s"),
                    "held": (mer.get("flip_enabled"), mer.get("hours_to_flip")),
                    "warn": night.engine._policy.meridian_flip_warn_min,
                    "polls": len(_status_polls())})
            return inner(topic, **payload)

        monkeypatch.setattr(events.bus, "publish", publish)
        if restart_the_poll:
            def on_capture(rec):
                if not getattr(night, "restarted", None):
                    night.restarted = night.rel(rec["t"])
                    hub.ensure_status_poller()
            night.on_capture = on_capture
        try:
            done = await night.run(_golden_as_recorded(), wall_s=120.0)
        finally:
            await night.close()
    finally:
        await close_night_hub(hub, popped)
    return night, done, rows, left


async def test_the_crossing_night_reads_the_meridian_on_its_own_clock(
        group_store, monkeypatch):
    """Across the crossing night no status poll runs, the one the connect
    started nor one restarted at the first frame, and the block the
    connect's poll left is dropped. At every published state
    ``hub.last_meridian`` is the hub's own answer on the night's clock at
    that instant, for where the mount points then: flip enabled, and the
    hours to the flip to the poll's four places. And every chip published
    while the flip is ahead is the one that answer gives: none before the
    window, then each second of the countdown from 820 s at 9960 s to 5 s
    at 10772.28 s.

    DELIBERATE PIN CHANGE (#366, S5-ENG-FLIP, S5 orchestrator ruling 2;
    re-pinned by the S5/S6 integration, S56-INTEG): the last chip before
    the crossing was 7 s at 10770.644 s, the instant the retry fired at the
    crossing itself. Since #366 the retry waits `MERIDIAN_SIDE_MARGIN_S`
    past the meridian, so the last state published ahead of the crossing is
    the retry hold's 5 s tick at 10772.28 s. The old pin, run against this
    tree, observed:
        assert (10772.28, 5) == (10770.644, 7)
    MUTANT "retry at the crossing" (S5-ENG-FLIP's name:
    `_flip_retry_past_s` answering 0.0), run in the private copy scratchpad
    S56-INTEG-mut: RED (observed):
        assert (10770.644, 7) == (10772.28, 5)

    UNCHANGED BY #505 (H4-ENG-C), ON PURPOSE. The gate now hands its flip
    point to the hold (`_flip_point_handed`), and with the meridian as the
    zero the hold still reads the countdown again on every tick, from the
    lead and band the gate decided, so this trace is the same to the
    millisecond. MUTANT "the handed meridian point extrapolated" (the hold
    waiting for the gate's ``at`` on the meridian path too, ``elif lon is
    not None:`` made ``elif False:`` in `_wait_for_flip_point`), run in the
    private copy H4-ENG-C-mut: RED (observed), the hour angle's sidereal
    rate carried into the wait from the gate:
        assert (10772.855, 4) == (10772.28, 5)

    MUTANT "the harness leaves the poller running" (`Night.__init__` no
    longer stopping the hub's status poll): RED (observed):
        AssertionError: the hub's real-time status poll was running at 4 of
        332 published states, the first [(0.0, "starting plan 'NGC 7331 -
        LRGB+SHO quick (SN 2026aaiv)'"), (0.0, 'slewing to NGC 7331')]
    The poll the connect started runs until the first frame's restart,
    whose stop takes it down too; before #368 it ran all night. The check
    reads the running tasks, not the trace, so it does not depend on
    whether the poll happened to land in the night.
    MUTANT "a restarted poll runs" (the stop in the harness's
    ``ensure_status_poller`` removed): RED (observed):
        AssertionError: the hub's real-time status poll was running at 328
        of 332 published states, the first [(60.0, 'NGC 7331: L 60s
        [1/15]'), (60.0, 'NGC 7331: R 60s  [1/15]')]
    MUTANT "the connect's block kept" (the drop of ``hub.last_meridian`` in
    `Night.__init__` removed): RED (observed):
        AssertionError: the Night kept the block the connect's poll left:
        {'status': 'counting', 'hours_to_flip': 5.5, 'flip_enabled': False,
        'pier_side': 'west'}
    MUTANT "no hold at a wake" (the hold in `Night._drive` removed): RED
    (observed):
        AssertionError: hub.last_meridian was not the night's at 323 of
        332 published states, the first (t, held, on the night's clock):
        [(60.0, (True, 3.0019), (True, 2.9852)), (60.0, (True, 3.0019),
        (True, 2.9852))]
    MUTANT "no hold after a slew" (the hold in the harness's ``slew``
    removed): RED (observed), the block still the one held at the night's
    start for where the mount pointed before its first goto, until the
    first wake:
        AssertionError: hub.last_meridian was not the night's at 2 of 332
        published states, the first (t, held, on the night's clock): [(0.0,
        (True, 9.972), (True, 3.0019)), (0.0, (True, 9.972), (True,
        3.0019))]
    MUTANT "no hold at the night's start" (the hold in `Night.run`
    removed): RED (observed):
        AssertionError: hub.last_meridian was not the night's at 2 of 332
        published states, the first (t, held, on the night's clock): [(0.0,
        (None, None), (True, 9.972)), (0.0, (None, None), (True, 9.972))]
    """
    night, done, rows, left = await _crossing_night(monkeypatch,
                                                    restart_the_poll=True)
    assert done, f"premise: the run ended: {night.trace[-3:]}"
    assert night.said("meridian flip complete"), "premise: the night flips"
    assert {r["warn"] for r in rows} == {15.0}, "premise: the default lead"
    assert night.restarted == 60.0, (
        "premise: the first frame restarted the poll")
    assert left is None, (
        f"the Night kept the block the connect's poll left: {left}")

    running = [(night.rel(r["t"]), r["detail"]) for r in rows if r["polls"]]
    assert running == [], (
        f"the hub's real-time status poll was running at {len(running)} of "
        f"{len(rows)} published states, the first {running[:2]}")

    def held(r):
        return (True, _hours_to_flip(r["ra"], r["t"]))

    stale = [(night.rel(r["t"]), r["held"], held(r)) for r in rows
             if r["held"] != held(r)]
    assert stale == [], (
        f"hub.last_meridian was not the night's at {len(stale)} of "
        f"{len(rows)} published states, the first (t, held, on the night's "
        f"clock): {stale[:2]}")

    ahead = [r for r in rows if _hours_to_flip(r["ra"], r["t"]) > 0]
    inside = [r for r in ahead
              if _chip(_hours_to_flip(r["ra"], r["t"]), 15.0) is not None]
    assert 0 < len(inside) < len(ahead) < len(rows), (
        "premise: the night enters the window from outside it, and crosses")
    wrong = [(night.rel(r["t"]), r["chip"],
              _chip(_hours_to_flip(r["ra"], r["t"]), 15.0))
             for r in ahead
             if r["chip"] != _chip(_hours_to_flip(r["ra"], r["t"]), 15.0)]
    assert wrong == [], (
        f"{len(wrong)} of the {len(ahead)} states published before the "
        f"crossing carried a chip the night's clock does not give, the "
        f"first (t, published, on the night's clock): {wrong[:3]}")
    assert (night.rel(inside[0]["t"]), inside[0]["chip"]) == (9960.0, 820)
    assert (night.rel(inside[-1]["t"]), inside[-1]["chip"]) == (10772.28, 5)


async def test_past_the_crossing_no_chip_is_published(group_store,
                                                      monkeypatch):
    """Past the crossing the flip is behind the mount, and `_live_block`
    builds no chip; the UI's heads-up (``scheduleStatus.ts``) trusts the
    engine to attach one only within the warn lead. `_set_state` used to add
    ``live`` to the state only when `_live_block` returned one, and merged,
    so the last chip, 5 s at 10772.28 s, rode every state published after
    it: "Meridian flip in" a few seconds, for the rest of the run. (A
    publish whose ``live`` carries a sensor temperature replaces the whole
    object; the harness's scripted capture leaves ``hub.last_frame`` unset,
    so nothing replaces it here.)

    With the harness holding the meridian on the night's clock, this is the
    half of "every published chip is the night's clock's" that the harness
    cannot reach: an engine defect, filed as #422, and fixed by S7-ENG-FLIP
    (`_set_state` pops ``live`` when `_live_block` returns None and the
    caller passed none). The strict xfail that pinned it went in the same
    change, which it said it must: with the fix this case XPASSed, and the
    strict marker failed the suite (observed, S7-ENG-FLIP):
        [XPASS(strict)] #422: the engine merges ``live`` into its state and
        never clears it, so the last chip before the crossing rides every
        later publish
    test_s7_meridian_chip_cleared.py holds the whole block, not only the
    chip, and its control.

    MUTANT "live merged, never popped" (`_set_state`'s ``if live:
    kw.setdefault("live", live)`` restored alone, the code before #422),
    run in the private copy scratchpad S7-ENG-FLIP-mut: RED (observed).
    Since #366 moved the retry and the flip it is 27 states carrying 5 s,
    where before #366 it was "25 states ... [(10800.644, 7), (10800.644,
    7), (10980.644, 7)]":
        AssertionError: 27 states published past the crossing carried a
        chip, the first (t, published): [(10777.28, 5), (10782.28, 5),
        (10785.603, 5)]
    With the harness as it was before #368, this case passed: no chip was
    published at all, so none past the crossing either (observed,
    XPASS(strict)). The premise that a chip was published fails that
    version through ``pytest.fail`` (observed):
        Failed: premise: the night published a chip before the crossing
    """
    night, done, rows, _left = await _crossing_night(monkeypatch)
    # The premises fail through pytest.fail, as they did under the strict
    # xfail this case carried until #422 was fixed, so a failed premise
    # reads as one: with the harness as it was, no chip was published at
    # all, and this case passed for that reason alone.
    if not done:
        pytest.fail(f"premise: the run ended: {night.trace[-3:]}")
    if not any(r["chip"] for r in rows):
        pytest.fail("premise: the night published a chip before the crossing")
    past = [r for r in rows if _hours_to_flip(r["ra"], r["t"]) <= 0]
    if not past:
        pytest.fail("premise: the night runs on past the crossing")
    carried = [(night.rel(r["t"]), r["chip"]) for r in past
               if r["chip"] is not None]
    assert carried == [], (
        f"{len(carried)} states published past the crossing carried a chip, "
        f"the first (t, published): {carried[:3]}")


async def test_a_meridian_read_that_never_answers_does_not_hold_up_the_night(
        group_hub, monkeypatch):
    """The harness's hold asks the mount for its side, as the poll does,
    and a mount double whose pier read never answers must not stall the
    night: in the poll that read hangs a task of its own, but the harness
    holds the meridian inside the night's driver and its slews. The hold is
    bounded (`MERIDIAN_HOLD_BOUND_S`, real seconds) and, cut short, drops
    the block, so the engine reads no meridian rather than an old one.
    test_pier_guard_reads_bounded.py's hung pier read is this at a night's
    scale.

    MUTANT "the hold is unbounded" (`Night._hold_the_meridian` awaiting
    `_compute_meridian` with no bound): RED (observed):
        Failed: the harness's meridian hold waited 5 s on a pier read that
        never answers
    and test_pier_guard_reads_bounded.py's ``[pier_side]`` case with it,
    the night's start holding in `Night.run` before its first turn:
        SpinNeverYielded: Night.run: the event loop did not come back for
        10.2 s of real time, against a bound of 10 s
    """
    night = Night(group_hub, monkeypatch)
    try:
        tel = group_hub.devices["telescope"]

        async def pier_side():
            await asyncio.Event().wait()

        monkeypatch.setattr(tel, "pier_side", pier_side)
        group_hub.last_meridian = dict(_CONNECT_TIME)
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(night._hold_the_meridian(), 5.0)
        except TimeoutError:
            pytest.fail("the harness's meridian hold waited 5 s on a pier "
                        "read that never answers")
        took = time.monotonic() - t0
        assert group_hub.last_meridian is None, (
            f"a hold cut short left {group_hub.last_meridian}")
        assert took < 2.0, took
    finally:
        await night.close()
