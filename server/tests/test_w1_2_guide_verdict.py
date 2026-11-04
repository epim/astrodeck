# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1107 items 3 and 4 (#1096, #1095, #1078, #831's lazy attribute): one
guide verdict per exposure, a settle before the first shutter, a ledger that
keeps the verdict, and a recovery line that says why guiding is not running.

2026-10-19 (0.3.43), the first panel of a mosaic: a fresh calibration, then an
exposure at once, three frames rejected on guide RMS and a fourth accepted.
Three defects, each pinned below on the clocked simulator
(tests/_group_harness.py) with a guider that publishes timestamped samples
(tests/_w1_2_guider.py):

* nothing waited for the loop to settle after a calibration;
* the gate judged a frame on the guider's rolling window (about 150 s) and not
  on the exposure, so one bad minute rejected neighbours whose own exposures
  were fine and a good stretch carried a bad frame through;
* the ledger kept no eccentricity, no reject reason, and a guide RMS read
  whenever the guider was connected, stale or not.

Every case runs the real scheduler, setup, frame loop, grader and ledger; only
the guider's samples and the sky are scripted.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.sequence import SequencePlan
from astrodeck.sequence.engine import GUIDE_SETTLE_MAX_S, GUIDE_SETTLE_POLL_S
from astrodeck.sequence.session import session_store

from _group_harness import (  # noqa: F401
    Night, group_hub, group_store, single)
from _w1_2_guider import HISTORY, SampledGuider

CEILING = 5.0
GOOD = 0.5


def _plan(*, count: int = 1, guide: bool = True, **over) -> SequencePlan:
    kw = dict(name="w12", guide=guide, recover_guiding=True,
              max_guide_rms=CEILING, dither_every=0, autofocus_every=0,
              meridian_flip=False, park_when_done=False,
              warm_cooler_when_done=False, count_mode="accepted")
    kw.update(over)
    return SequencePlan(targets=[single("Alpha", count=count)], **kw)


async def _night(hub, monkeypatch, plan, guider, *, on_capture=None,
                 info=None, wall_s: float = 60.0, **night_kw) -> Night:
    """Run ``plan`` with ``guider`` on the hub. ``info(n, info)`` may add
    measurements to the ``n``-th light frame's info dict."""
    hub.guider = guider
    night = Night(hub, monkeypatch, **night_kw)
    guider.bind(night)
    night.on_capture = on_capture
    if info is not None:
        real = hub.capture
        count = {"n": 0}

        async def capture(*a, **kw):
            out = await real(*a, **kw)
            if str(kw.get("frame_type", "Light")).lower() == "light":
                count["n"] += 1
                info(count["n"], out)
            return out

        monkeypatch.setattr(hub, "capture", capture)
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    return night


def _rms(err, lo: float, hi: float) -> float:
    ts = [float(t) for t in range(math.ceil(lo), math.floor(hi) + 1)]
    return math.sqrt(sum(err(t) ** 2 for t in ts) / len(ts))


def _ledger(night: Night):
    """The saved session's frames: the engine drops its own copy at the end."""
    return list(session_store.load(night.session_id).frames)


# --------------------------------------------------------------- the window

async def test_a_frame_over_the_ceiling_inside_its_window_is_rejected_where_the_rolling_window_passes(
        group_hub, group_store, monkeypatch):
    """THE PIN (#1107 "done when"). The guiding is good for the first half of
    the first exposure and bad (8") for the second half. Over the exposure's
    own window the RMS is above the 5" ceiling, so the frame is rejected with
    reason guide_rms. The SAME samples over the guider's rolling window
    (HISTORY seconds, which is what `stats().rms_total` reads) are below the
    ceiling, so the old gate passed the frame: both facts are asserted from
    the one set of samples. The next frame, shot after the burst, is judged on
    its own window and kept.

    MUTANT (the verdict window widened back to the rolling window): RED - the
    first frame is accepted and its ledger entry has no reject reason.
    """
    burst: dict[str, float] = {}

    def err(t: float) -> float:
        lo = burst.get("from")
        return 8.0 if lo is not None and lo <= t <= lo + 15.0 else GOOD

    def at_shutter(rec) -> None:
        burst.setdefault("open", rec["t"])
        burst.setdefault("from", rec["t"] + 15.0)

    night = await _night(group_hub, monkeypatch, _plan(count=1),
                         SampledGuider(err), on_capture=at_shutter)
    assert night.done, night.trace[-3:]
    frames = _ledger(night)
    assert len(frames) == 2, [(f.auto_accepted, f.reject_reason)
                              for f in frames]
    opened, closed = burst["open"], burst["open"] + 30.0
    within = _rms(err, opened, closed)
    rolling = _rms(err, closed - HISTORY + 1, closed)
    assert within > CEILING, within
    assert rolling < CEILING, (
        "the premise: the old rolling window would have passed this frame",
        rolling)
    assert frames[0].auto_accepted is False
    assert frames[0].reject_reason == "guide_rms"
    assert frames[0].guide_verdict == "over_ceiling"
    assert frames[0].metrics["guide_rms"] == pytest.approx(within, abs=0.05)
    assert [m for m in night.said('above ceiling 5.00"')], night.lines[-6:]
    assert frames[1].auto_accepted is True
    assert frames[1].reject_reason is None


async def test_a_good_exposure_after_a_bad_stretch_is_not_blamed_for_it(
        group_hub, group_store, monkeypatch):
    """The converse of the pin: guiding was bad until just before the shutter
    and good inside the exposure. The frame is judged on its own samples and
    kept, though the rolling window still holds the bad stretch (above the
    ceiling). A pre-shutter guard that looks at a short trailing window lets it
    through once the loop has settled.

    MUTANT (window widened to the rolling window): RED - the good frame is
    rejected on the old stretch.
    """
    state: dict[str, float] = {}

    def err(t: float) -> float:
        start = state.get("start")
        if start is not None and t < start + 110.0:
            return 9.0
        return GOOD

    class Settling(SampledGuider):
        async def start_guiding(self) -> None:
            await super().start_guiding()
            state["start"] = self.started_at[0]

    night = await _night(group_hub, monkeypatch, _plan(count=1),
                         Settling(err))
    assert night.done, night.trace[-3:]
    frames = _ledger(night)
    assert [f.auto_accepted for f in frames] == [True], [
        (f.auto_accepted, f.reject_reason) for f in frames]
    opened = night.captures[0]["t"]
    rolling = _rms(err, opened + 30.0 - HISTORY + 1, opened + 30.0)
    assert rolling > CEILING, (
        "the premise: the rolling window still holds the bad stretch", rolling)
    assert _rms(err, opened, opened + 30.0) < CEILING


class FigureOnlyGuider(SampledGuider):
    """A guider that reports a figure and no samples at all."""

    def stats(self):
        from astrodeck.guide.base import GuideStats
        return GuideStats(guiding=self.active, rms_total=8.0, is_arcsec=True,
                          phase="guiding" if self.active else "idle")


async def test_a_guider_that_publishes_no_samples_is_judged_on_its_own_figure(
        group_hub, group_store, monkeypatch):
    """A guider with no timestamped samples cannot be windowed, so the gate
    judges it on the figure it does report, as it always did: a frame shot
    while that figure is over the ceiling is rejected, and the verdict says
    it rested on no samples.

    MUTANT (a sample-less guider judged on nothing): RED - the frame is kept.
    """
    night = await _night(group_hub, monkeypatch,
                         _plan(count=1, max_consecutive_rejects=2),
                         FigureOnlyGuider(lambda t: 0.0))
    assert night.done, night.trace[-3:]
    first = _ledger(night)[0]
    assert first.auto_accepted is False
    assert first.reject_reason == "guide_rms"
    assert first.metrics["guide_rms"] == pytest.approx(8.0)
    assert first.metrics["guide_samples"] == 0.0


# ---------------------------------------------------------------- the settle

async def test_the_first_shutter_waits_for_the_loop_to_settle_after_a_calibration(
        group_hub, group_store, monkeypatch):
    """#1096 defect 1. A start clears the guider's samples (as the native
    engine does), so for a moment the trailing window holds two good samples
    and nothing else: too few to judge. Then the loop is pulled in at 8" for
    forty seconds. A shutter opened at once is over the ceiling inside its
    exposure; the engine waits for the loop to produce enough samples and to
    be under the ceiling before the first exposure, so the first frame opens
    after the burst and is kept.

    The pre-shutter guard alone cannot do this: with two samples it has
    nothing to judge and passes.

    MUTANT (the settle wait removed): RED - the first exposure opens at once,
    inside the burst, and is rejected.
    """
    def err(t: float) -> float:
        started = guider.started_at
        if started and started[0] + 2.0 < t <= started[0] + 42.0:
            return 8.0
        return GOOD

    guider = SampledGuider(err, cleared_by_start=True)
    night = await _night(group_hub, monkeypatch, _plan(count=1), guider)
    assert night.done, night.trace[-3:]
    started = guider.started_at[0]
    first_open = night.captures[0]["t"]
    assert first_open >= started + 42.0, (
        f"the first exposure opened {first_open - started:.0f} s after the "
        f"start, inside the burst")
    frames = _ledger(night)
    assert [f.auto_accepted for f in frames] == [True], [
        (f.auto_accepted, f.reject_reason) for f in frames]
    assert night.engine._rejected == 0


async def test_a_guider_that_never_settles_does_not_hold_the_first_frame_forever(
        group_hub, group_store, monkeypatch):
    """The settle is bounded: a loop stuck over the ceiling is waited on for
    GUIDE_SETTLE_MAX_S, said once, and handed to the pre-shutter guard (the
    hold tests own what follows). The wait is polled, never one long sleep.
    """
    night = await _night(group_hub, monkeypatch, _plan(count=1),
                         SampledGuider(lambda t: 7.0))
    assert night.done, night.trace[-3:]
    said = night.said("had not settled")
    assert len(said) == 1, said
    assert f"{GUIDE_SETTLE_MAX_S:.0f} s" in said[0], said
    assert 0 < GUIDE_SETTLE_POLL_S < GUIDE_SETTLE_MAX_S


# ----------------------------------------------------------- the ledger rows

def _ecc_info(median: float, elongated: int, n: int = 10):
    def add(_n: int, info: dict) -> None:
        info["ecc"] = median
        info["star_list"] = [{"ecc": 0.3} for _ in range(n - elongated)] + [
            {"ecc": 0.9} for _ in range(elongated)]
    return add


async def test_an_accepted_frame_keeps_its_eccentricity_and_its_guide_verdict(
        group_hub, group_store, monkeypatch):
    """#1095. The ledger kept neither eccentricity nor why a frame was
    rejected, so pier-side elongation could not be compared. An accepted frame
    now carries its median and the fraction of stars above 0.80, and the guide
    verdict of its own exposure (within the ceiling, the windowed RMS).

    MUTANT (the eccentricity metrics line dropped): RED - the metrics have no
    ecc_median.
    """
    night = await _night(group_hub, monkeypatch,
                         _plan(count=1, max_eccentricity=0.65),
                         SampledGuider(lambda t: 1.0),
                         info=_ecc_info(0.40, elongated=2))
    assert night.done, night.trace[-3:]
    (frame,) = _ledger(night)
    assert frame.auto_accepted is True
    assert frame.metrics["ecc_median"] == pytest.approx(0.40)
    assert frame.metrics["ecc_frac_080"] == pytest.approx(0.2)
    assert frame.metrics["guide_rms"] == pytest.approx(1.0, abs=0.01)
    assert frame.guide_verdict == "within_ceiling"
    assert frame.reject_reason is None


async def test_a_rejected_frame_still_carries_its_reason_after_the_next_frame(
        group_hub, group_store, monkeypatch):
    """#1095: the reason sat on the engine and was overwritten by the next
    frame. The first frame fails the eccentricity ceiling, the second passes;
    the ledger keeps the first one's reason and the second's none.

    MUTANT (reject_reason not written to the ledger): RED.
    """
    night = await _night(
        group_hub, monkeypatch, _plan(count=1, max_eccentricity=0.65),
        SampledGuider(lambda t: 1.0),
        info=lambda n, info: _ecc_info(0.80 if n == 1 else 0.40, 2)(n, info))
    assert night.done, night.trace[-3:]
    first, second = _ledger(night)
    assert first.auto_accepted is False
    assert first.reject_reason == "eccentricity"
    assert first.metrics["ecc_median"] == pytest.approx(0.80)
    assert second.auto_accepted is True
    assert second.reject_reason is None


async def test_an_idle_guider_leaves_no_guide_rms_in_the_ledger(
        group_hub, group_store, monkeypatch):
    """#1095: guide_rms was stored whenever the guider was connected, so a
    stale figure from a stopped guider sat beside a frame it never guided.
    The plan does not guide here and the guider holds a high stale figure; the
    ledger carries no guide_rms and no verdict.

    MUTANT (guide_rms read from stats() whenever connected): RED.
    """
    guider = SampledGuider(lambda t: 9.0)
    night = await _night(group_hub, monkeypatch,
                         _plan(count=1, guide=False), guider)
    assert night.done, night.trace[-3:]
    assert guider.started_at == [], "premise: the plan never started it"
    (frame,) = _ledger(night)
    assert "guide_rms" not in frame.metrics, frame.metrics
    assert frame.guide_verdict is None
    assert frame.auto_accepted is True


# ----------------------------------------------- the recovery line's wording

def _attempt_lines(night: Night) -> list[str]:
    return [m for _t, _l, m in night.lines if "attempting recovery" in m]


def _no_lost_beside_guiding(night: Night) -> None:
    offenders = [m for _t, _l, m in night.lines
                 if "guid" in m.lower() and "lost" in m.lower()]
    assert not offenders, offenders


async def test_a_guider_that_never_started_is_not_said_to_have_lost_a_star(
        group_hub, group_store, monkeypatch):
    """#1078. The start fails twice and the third attempt works; the attempt
    lines say guiding never started, not that a star was lost (the UI
    humanizer pairs "guid" with "lost" and rewrites it as "Guiding was lost").

    MUTANT (the old fixed wording restored): RED.
    """
    guider = SampledGuider(lambda t: 1.0)
    night = await _night(group_hub, monkeypatch, _plan(count=1), guider,
                         guide=lambda who, n: n >= 3)
    assert night.done, night.trace[-3:]
    lines = _attempt_lines(night)
    assert lines and all(m.startswith("guiding never started - ")
                         for m in lines), lines
    assert lines[0].endswith("attempting recovery (1/2)"), lines
    _no_lost_beside_guiding(night)


async def test_a_lost_star_is_worded_as_a_missing_guide_star(
        group_hub, group_store, monkeypatch):
    """#1078. Guiding runs, then the guider reports a lost star (phase
    "lost"): the line says the guide star went missing.

    MUTANT (the old fixed wording restored): RED.
    """
    guider = SampledGuider(lambda t: 1.0)

    def lose_the_star(rec) -> None:
        guider.active = False
        guider.lost = True

    night = await _night(group_hub, monkeypatch, _plan(count=1), guider,
                         on_capture=lose_the_star)
    assert night.done, night.trace[-3:]
    lines = _attempt_lines(night)
    assert lines and lines[0].startswith(
        "the guide star went missing - attempting recovery (1/2)"), lines
    _no_lost_beside_guiding(night)


async def test_guiding_stopped_after_running_is_worded_as_stopped(
        group_hub, group_store, monkeypatch):
    """#1078. Guiding ran and was then stopped (the operator's Stop; the
    guider's phase is idle): the line says it was stopped, which is neither
    "never started" nor a lost star.

    MUTANT (the old fixed wording restored): RED.
    """
    guider = SampledGuider(lambda t: 1.0)
    night = await _night(group_hub, monkeypatch, _plan(count=1), guider,
                         on_capture=lambda rec: setattr(guider, "active",
                                                        False))
    assert night.done, night.trace[-3:]
    lines = _attempt_lines(night)
    assert lines and lines[0].startswith(
        "guiding was stopped - attempting recovery (1/2)"), lines
    _no_lost_beside_guiding(night)


# ---------------------------------------------------------------- #831

async def test_the_stand_down_mark_is_declared_not_created_lazily(
        group_hub, group_store, monkeypatch):
    """#831: ``_stand_down_said_at`` was created with ``getattr`` at its first
    use. It is an attribute of the engine from construction, None until a
    stand-down is said."""
    from astrodeck.sequence import SequenceEngine
    engine = SequenceEngine(group_hub)
    assert "_stand_down_said_at" in vars(engine)
    assert engine._stand_down_said_at is None
