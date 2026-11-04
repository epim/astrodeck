# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1096 defect 2: the pre-shutter guard covers guiding that is running but
over the ceiling, not only a stopped guider.

#873's comment promised "NO FRAME IS SHOT THAT THE GUIDE-RMS REJECT WILL
REFUSE", and the guard refused only a stopped guider. A guider that was
guiding but over `max_guide_rms` passed it; each frame was shot in full and
rejected, and under a persistent fault ten of them set the step aside and
twenty ended the night, all of them exposures the engine knew it would refuse.

The guard now looks at the guider's last GUIDE_TRAIL_S seconds of samples. Over
the ceiling it holds and looks again (bounded by GUIDE_OVER_HOLD_S); past the
bound a mosaic panel is deferred and any other target takes one of the retry
spells #873 built (capped per target per night), so the reject guards are never
reached through frames the guard could have refused.

Every case runs the real frame loop on the clocked simulator
(tests/_group_harness.py) with a guider that publishes timestamped samples
(tests/_w1_2_guider.py).
"""
from __future__ import annotations

import math

from astrodeck.sequence import SequencePlan
from astrodeck.sequence.engine import (
    GUIDE_OVER_HOLD_S, GUIDE_OVER_RETRIES_SPENT, GUIDE_RETRY_WAIT_S,
    GUIDE_SETTLE_MAX_S, GUIDE_SETTLE_POLL_S, GUIDE_TRAIL_S,
    MAX_GUIDE_RETRIES_PER_TARGET_NIGHT)

from _group_harness import (  # noqa: F401
    Night, grid_plan, group_hub, group_store, single)
from _w1_2_guider import SampledGuider

CEILING = 5.0
GOOD = 0.5
SPELL_WORDS = "is checked again in 10 min"


def _plan(*, count: int = 3, **over) -> SequencePlan:
    kw = dict(name="w12hold", guide=True, recover_guiding=True,
              max_guide_rms=CEILING, dither_every=0, autofocus_every=0,
              meridian_flip=False, park_when_done=False,
              warm_cooler_when_done=False, count_mode="accepted")
    kw.update(over)
    return SequencePlan(targets=[single("Alpha", count=count)], **kw)


async def _night(hub, monkeypatch, plan, guider, *, on_capture=None,
                 wall_s: float = 60.0) -> Night:
    hub.guider = guider
    night = Night(hub, monkeypatch)
    guider.bind(night)
    night.on_capture = on_capture
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    return night


def _trailing(err, now: float) -> float:
    ts = [float(t) for t in range(math.ceil(now - GUIDE_TRAIL_S),
                                  math.floor(now) + 1)]
    return math.sqrt(sum(err(t) ** 2 for t in ts) / len(ts))


def _spell_lines(night: Night) -> list[tuple[float, str]]:
    return [(t, m) for t, _lvl, m in night.lines if SPELL_WORDS in m]


async def test_a_guider_stuck_over_the_ceiling_shoots_nothing_and_takes_spells_not_a_night_stop(
        group_hub, group_store, monkeypatch):
    """#1096 validation 2. The loop sits at 7" against a 5" ceiling. Before:
    every frame was shot and rejected until the reject guards stopped the
    step. Now no exposure opens, no frame is rejected, the target takes the six
    retry spells of the per-night cap and stops with the over-ceiling reason;
    the reject guards never speak.

    MUTANT (the guard ignores over-ceiling guiding): RED - three exposures
    open and are rejected.
    """
    night = await _night(group_hub, monkeypatch, _plan(count=3),
                         SampledGuider(lambda t: 7.0))
    assert night.done, night.trace[-3:]
    assert night.captures == [], (
        f"a frame was shot over the ceiling: {night.shots()}")
    assert night.engine._rejected == 0
    spells = _spell_lines(night)
    assert len(spells) == MAX_GUIDE_RETRIES_PER_TARGET_NIGHT, spells
    assert night.said(GUIDE_OVER_RETRIES_SPENT), night.lines[-4:]
    assert not night.said("consecutive rejects")
    assert not night.said("Rejected:")


async def test_the_spell_cap_ends_the_target_after_six_spells(
        group_hub, group_store, monkeypatch):
    """The same stuck loop, graded on the cap alone: the spells are at least
    the wait apart, and the seventh never comes.

    MUTANT (the spell cap removed from the over-ceiling hold): RED - more than
    six spell lines and no stop.
    """
    night = await _night(group_hub, monkeypatch, _plan(count=1),
                         SampledGuider(lambda t: 7.0))
    assert night.done, night.trace[-3:]
    spells = _spell_lines(night)
    gaps = [b[0] - a[0] for a, b in zip(spells, spells[1:])]
    assert len(spells) == MAX_GUIDE_RETRIES_PER_TARGET_NIGHT
    assert all(g >= GUIDE_RETRY_WAIT_S for g in gaps), gaps
    assert list(night.engine._guide_retry_spells.values()) == [
        MAX_GUIDE_RETRIES_PER_TARGET_NIGHT]


async def test_a_guider_decaying_from_eight_to_two_arcsec_is_waited_for_and_costs_no_reject(
        group_hub, group_store, monkeypatch):
    """#1096 validation 1. After the start the error decays from 8" to 2" over
    three minutes. No exposure opens until the trailing window is under the
    ceiling, and no frame is rejected on guide RMS.

    MUTANT (settle wait and guard both removed): RED - the first exposure
    opens at once and is rejected.
    """
    guider = SampledGuider(lambda t: err(t))

    def err(t: float) -> float:
        if not guider.started_at:
            return 8.0
        into = t - guider.started_at[0]
        return max(2.0, 8.0 - 6.0 * into / 180.0)

    night = await _night(group_hub, monkeypatch, _plan(count=3), guider)
    assert night.done, night.trace[-3:]
    assert len(night.captures) == 3, night.shots()
    assert night.engine._rejected == 0
    assert not night.said("above ceiling")
    for rec in night.captures:
        assert _trailing(err, rec["t"]) <= CEILING + 1e-9, (
            rec["t"] - guider.started_at[0])
    assert not _spell_lines(night)
    # The wait ended within one poll of the first moment the window was good.
    first = night.captures[0]["t"]
    good_from = next(t for t in range(int(guider.started_at[0]),
                                      int(guider.started_at[0]) + 400)
                     if _trailing(err, float(t)) <= CEILING)
    assert first <= good_from + GUIDE_SETTLE_POLL_S + 1.0, (
        first - guider.started_at[0], good_from - guider.started_at[0])


async def test_a_burst_that_clears_inside_the_hold_resumes_without_a_spell(
        group_hub, group_store, monkeypatch):
    """The loop is good, goes bad for two minutes during the second exposure
    (so that frame is rejected on its own window), then recovers. The guard
    holds the next frame until the trailing window is under the ceiling: no
    exposure opens over the ceiling, the hold is inside GUIDE_OVER_HOLD_S so
    no spell is taken, and the night completes.

    MUTANT (the hold takes its spell at once instead of re-checking): RED - a
    spell line appears for a burst that cleared inside the bound.
    """
    burst: dict[str, float] = {}

    def err(t: float) -> float:
        lo = burst.get("from")
        return 8.0 if lo is not None and lo <= t < lo + 120.0 else GOOD

    shutters = {"n": 0}

    def at_shutter(rec) -> None:
        shutters["n"] += 1
        if shutters["n"] == 2:
            burst["from"] = rec["t"] + 5.0

    guider = SampledGuider(err)
    night = await _night(group_hub, monkeypatch, _plan(count=2), guider,
                         on_capture=at_shutter)
    assert night.done, night.trace[-3:]
    assert GUIDE_OVER_HOLD_S > 120.0 + GUIDE_TRAIL_S, "premise"
    assert not _spell_lines(night), _spell_lines(night)
    later = night.captures[2:]
    assert later, night.shots()
    for rec in later:
        assert _trailing(err, rec["t"]) <= CEILING + 1e-9, rec["t"]
    assert night.engine._rejected == 1, night.engine._rejected


async def test_a_mosaic_panel_over_the_ceiling_is_deferred_not_held_for_spells(
        group_hub, group_store, monkeypatch):
    """A panel of a mosaic group whose guiding stays over the ceiling is
    deferred to the next pass, the group's own answer to a guider that will
    not hold (#303), not held through a retry spell that would idle the whole
    night over one panel.

    MUTANT (the member exception removed from the over-ceiling hold): RED -
    the panel takes a spell first.
    """
    plan = grid_plan(rows=1, cols=2,
                     panel_kw={"filters": ("L",), "count": 2, "per_visit": 2},
                     guide=True, recover_guiding=True, count_mode="accepted",
                     max_guide_rms=CEILING)
    night = await _night(group_hub, monkeypatch, plan,
                         SampledGuider(lambda t: 7.0))
    assert night.done, night.trace[-3:]
    assert night.captures == [], night.shots()
    deferred = [t for t, _l, m in night.lines
                if "the visit stopped on 1-1" in m
                and "retried on the next pass" in m]
    assert deferred, [m for _t, _l, m in night.lines]
    assert not [t for t, _m in _spell_lines(night) if t <= deferred[0]], (
        "a panel was held for a retry spell before its deferral")
    assert deferred[0] - night.t0 < (
        GUIDE_SETTLE_MAX_S + GUIDE_OVER_HOLD_S + 2 * GUIDE_RETRY_WAIT_S)
