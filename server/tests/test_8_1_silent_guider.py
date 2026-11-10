# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1146 part 2: a guider that is guiding and has sent no sample is not settled.

A start returns when the guide loop is running, not when the guider has
measured anything. A native guider just after it publishes ``recent=[]`` and
``rms_total=0.0``, which the verdict read as a judged figure of 0.0 from no
samples: the settle wait returned at once, the pre-shutter guard passed, and the
first exposure opened with nothing measured. The same held for a guider that
stalled, no sample in the last ``GUIDE_TRAIL_S``. With the reject armed it is
now waited for before the first shutter (``GUIDE_SETTLE_MAX_S``) and held before
any later one (``GUIDE_OVER_HOLD_S``, then the group's deferral or the retry
spells), the way a guider over its ceiling is. A guider that reports a figure
and no sample list at all cannot be waited on, and is not; one or two samples
are not silence.

Every night runs the real frame loop on the clocked simulator
(tests/_group_harness.py) with the guider's sample times scripted
(tests/_w8_1_guider.py), so every wait is on the engine's injectable clock.
"""
from __future__ import annotations

import astrodeck.sequence.engine as engine_mod
from astrodeck.hub import Hub
from astrodeck.sequence import SequencePlan
from astrodeck.sequence.engine import (
    GUIDE_OVER_HOLD_S, GUIDE_SETTLE_MAX_S, GUIDE_SETTLE_POLL_S,
    GUIDE_SILENT_RETRIES_SPENT, MAX_GUIDE_RETRIES_PER_TARGET_NIGHT,
    GuideVerdict, SequenceEngine)
from astrodeck.sequence.session import session_store

from _group_harness import (  # noqa: F401
    Night, grid_plan, group_hub, group_store, single)
from _w8_1_guider import StampedGuider

CEILING = 5.0
GOOD = 0.5
SPELL_WORDS = "is checked again in 10 min"


def _plan(*, count: int = 1, **over) -> SequencePlan:
    kw = dict(name="w81silent", guide=True, recover_guiding=True,
              max_guide_rms=CEILING, dither_every=0, autofocus_every=0,
              meridian_flip=False, park_when_done=False,
              warm_cooler_when_done=False, count_mode="accepted")
    kw.update(over)
    return SequencePlan(targets=[single("Alpha", count=count)], **kw)


async def _night(hub, monkeypatch, plan, guider, *, on_capture=None,
                 wall_s: float = 90.0) -> Night:
    hub.guider = guider
    night = Night(hub, monkeypatch)
    guider.bind(night)
    night.on_capture = on_capture
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    return night


def _late_start(guider_box: dict, delay_s: float):
    """One sample a second beginning ``delay_s`` after the guider's start."""
    def stamps(now: float) -> list[float]:
        started = guider_box["g"].started_at
        if not started:
            return []
        first = started[0] + delay_s
        return [float(t) for t in range(int(first), int(now) + 1)]
    return stamps


async def test_the_first_shutter_waits_for_a_guider_that_has_sent_nothing(
        group_hub, group_store, monkeypatch):
    """The start returns, the guider publishes nothing for 20 s, then a sample
    a second. The first exposure opens only once three samples exist, within a
    poll of that moment, and the wait is said once.

    MUTANT (the settle returns on a window with no samples, as it did): RED -
    the first exposure opens at the start, with nothing measured.
    """
    box: dict = {}
    box["g"] = guider = StampedGuider(lambda t: GOOD, _late_start(box, 20.0))
    night = await _night(group_hub, monkeypatch, _plan(), guider)
    assert night.done, night.trace[-3:]
    started = guider.started_at[0]
    opened = night.captures[0]["t"]
    assert opened >= started + 22.0, opened - started
    assert opened <= started + 22.0 + GUIDE_SETTLE_POLL_S + 1.0, opened - started
    waits = night.said("until the guider has sent samples")
    assert len(waits) == 1, waits
    (frame,) = list(session_store.load(night.session_id).frames)
    assert frame.auto_accepted is True
    assert frame.guide_verdict == "within_ceiling"


async def test_a_guider_that_reports_a_figure_and_no_samples_is_not_waited_on(
        group_hub, group_store, monkeypatch):
    guider = StampedGuider(lambda t: GOOD, lambda now: [], figure=3.0,
                           trace=False)
    night = await _night(group_hub, monkeypatch, _plan(), guider)
    assert night.done, night.trace[-3:]
    assert night.captures[0]["t"] - guider.started_at[0] < GUIDE_SETTLE_POLL_S
    assert not night.said("until the guider has sent samples")
    (frame,) = list(session_store.load(night.session_id).frames)
    assert frame.metrics["guide_rms"] == 3.0
    assert frame.metrics["guide_samples"] == 0.0


async def test_a_plan_with_the_reject_off_waits_for_nothing(
        group_hub, group_store, monkeypatch):
    box: dict = {}
    box["g"] = guider = StampedGuider(lambda t: GOOD, _late_start(box, 600.0))
    night = await _night(group_hub, monkeypatch, _plan(max_guide_rms=0.0),
                         guider)
    assert night.done, night.trace[-3:]
    assert night.captures[0]["t"] - guider.started_at[0] < GUIDE_SETTLE_POLL_S
    assert not night.said("until the guider has sent samples")


async def test_a_guider_that_stays_silent_is_waited_on_for_the_bound_and_then_held(
        group_hub, group_store, monkeypatch):
    """No sample ever. The settle gives up after GUIDE_SETTLE_MAX_S and says it
    was the silence, then the pre-shutter guard holds for GUIDE_OVER_HOLD_S
    and takes the retry spells; no exposure opens and the target stops with the
    silent reason.

    MUTANT (the guard ignores a silent window): RED - the exposure opens after
    the settle.
    """
    guider = StampedGuider(lambda t: GOOD, lambda now: [])
    night = await _night(group_hub, monkeypatch, _plan(), guider,
                         wall_s=120.0)
    assert night.done, night.trace[-3:]
    assert night.captures == [], night.shots()
    assert night.said(f"sent no sample {GUIDE_SETTLE_MAX_S:.0f} s after "
                      f"guiding started")
    assert night.said("the guider sent no sample for 30 s while guiding")
    spells = [m for _t, _l, m in night.lines if SPELL_WORDS in m]
    assert len(spells) == MAX_GUIDE_RETRIES_PER_TARGET_NIGHT, spells
    assert all("no frames are shot while it sends no sample" in m.lower()
               for m in spells), spells
    assert night.said(GUIDE_SILENT_RETRIES_SPENT)
    assert not night.said("over the guide-RMS ceiling")


async def test_a_guider_that_goes_quiet_after_a_frame_is_held_before_the_next(
        group_hub, group_store, monkeypatch):
    """Samples until the first shutter, none after: the first frame is kept
    (unjudged, no samples), and the second is not shot."""
    state: dict[str, float] = {}

    def stamps(now: float) -> list[float]:
        opened = state.get("open")
        return [float(t) for t in range(int(now) - 100, int(now) + 1)
                if opened is None or t < opened]

    night = await _night(
        group_hub, monkeypatch, _plan(count=2), StampedGuider(
            lambda t: GOOD, stamps),
        on_capture=lambda rec: state.setdefault("open", rec["t"]))
    assert night.done, night.trace[-3:]
    assert len(night.captures) == 1, night.shots()
    (frame,) = list(session_store.load(night.session_id).frames)
    assert frame.auto_accepted is True
    assert frame.guide_verdict == "unjudged"
    assert frame.metrics["guide_samples"] == 0.0
    assert night.said("the guider sent no sample for 30 s while guiding")


async def test_a_silent_mosaic_panel_is_deferred_not_held_for_spells(
        group_hub, group_store, monkeypatch):
    plan = grid_plan(rows=1, cols=2,
                     panel_kw={"filters": ("L",), "count": 2, "per_visit": 2},
                     guide=True, recover_guiding=True, count_mode="accepted",
                     max_guide_rms=CEILING)
    night = await _night(group_hub, monkeypatch, plan,
                         StampedGuider(lambda t: GOOD, lambda now: []),
                         wall_s=120.0)
    assert night.done, night.trace[-3:]
    assert night.captures == [], night.shots()
    deferred = [t for t, _l, m in night.lines
                if "the visit stopped on 1-1" in m
                and "retried on the next pass" in m]
    assert deferred, [m for _t, _l, m in night.lines]
    assert not [t for t, _l, m in night.lines
                if SPELL_WORDS in m and t <= deferred[0]]
    assert deferred[0] - night.t0 < (GUIDE_SETTLE_MAX_S + GUIDE_OVER_HOLD_S
                                     + 2 * 600.0)


# ------------------------------------------------------------ the verdict itself

def test_one_or_two_samples_are_not_silence_and_a_figure_is_not_silence(
        monkeypatch):
    eng = SequenceEngine(Hub())
    monkeypatch.setattr(eng, "_guide_reject_armed",
                        lambda calibration=False: True)
    cases = [
        (GuideVerdict(None, True, 0, CEILING), True),    # nothing, no figure
        (GuideVerdict(GOOD, True, 1, CEILING), False),
        (GuideVerdict(GOOD, True, 2, CEILING), False),
        (GuideVerdict(GOOD, True, 3, CEILING), False),
        (GuideVerdict(3.0, True, 0, CEILING), False),    # a figure, no list
    ]
    for verdict, unfit in cases:
        monkeypatch.setattr(eng, "_trailing_guide_verdict",
                            lambda v=verdict: v)
        assert eng._guide_unfit_now() is unfit, verdict


def test_a_guider_with_no_figure_and_no_samples_is_silent():
    from astrodeck.guide.base import GuideStats
    quiet = GuideStats(guiding=True, rms_total=0.0, recent=[], is_arcsec=True)
    verdict = engine_mod.guide_verdict(quiet, 0.0, 10.0, CEILING)
    assert verdict.silent and not verdict.judged
    figure = GuideStats(guiding=True, rms_total=4.0, recent=[], is_arcsec=True)
    verdict = engine_mod.guide_verdict(figure, 0.0, 10.0, CEILING)
    assert not verdict.silent and verdict.judged and verdict.rms == 4.0
    pixels = GuideStats(guiding=True, rms_total=0.0, recent=[],
                        is_arcsec=False)
    assert engine_mod.guide_verdict(pixels, 0.0, 10.0, CEILING).silent
