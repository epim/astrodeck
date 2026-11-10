# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1222: the guide-RMS reject gate does not judge a frame on one guider sample.

`_check_quality` judged a frame on whatever guider samples were stamped inside
its exposure, even one. A guider that stalled over a short light left one large
post-gap sample and the gate rejected a frame that was guided well (8 of 90
frames on the paced simulator). The pre-shutter guard and the settle already
treated fewer than ``GUIDE_MIN_SAMPLES`` samples as no verdict; the gate now
asks the same question of the same constant (``GuideVerdict.judged``). A
frame the gate could not judge is kept, and says so: the ledger entry's guide
verdict reads "unjudged" with the sample count beside it, and one line per run
tells the operator the reject did not judge it.

Every night runs the real frame loop on the clocked simulator
(tests/_group_harness.py); only the guider's sample times are scripted
(tests/_w8_1_guider.py).

Named mutant (``_check_quality`` judges ``verdict.rms`` whatever the sample
count): RED, observed verbatim below on the lone-sample case.
"""
from __future__ import annotations

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.hub import Hub
from astrodeck.sequence import SequencePlan
from astrodeck.sequence.engine import GUIDE_MIN_SAMPLES, GuideVerdict, SequenceEngine
from astrodeck.sequence.session import session_store

from _group_harness import (  # noqa: F401
    Night, group_hub, group_store, single)
from _w8_1_guider import StampedGuider

CEILING = 5.0
GOOD = 0.5
BAD = 8.0
EXPOSURE_S = 30.0


def _plan(**over) -> SequencePlan:
    kw = dict(name="w81", guide=True, recover_guiding=True,
              max_guide_rms=CEILING, dither_every=0, autofocus_every=0,
              meridian_flip=False, park_when_done=False,
              warm_cooler_when_done=False, count_mode="accepted",
              max_consecutive_rejects=2)
    kw.update(over)
    return SequencePlan(targets=[single("Alpha", count=1)], **kw)


async def _night_with_n_samples_in_the_first_exposure(
        hub, monkeypatch, n: int):
    """One frame whose own exposure holds exactly ``n`` samples, every one of
    them over the ceiling, after a settled stretch of good one-second
    samples. Returns the night and its saved ledger."""
    state: dict[str, float] = {}

    def err(t: float) -> float:
        opened = state.get("open")
        return BAD if opened is not None and opened <= t <= opened + EXPOSURE_S \
            else GOOD

    def stamps(now: float) -> list[float]:
        steady = [float(t) for t in range(int(now) - 200, int(now) + 1)]
        opened = state.get("open")
        if opened is None:
            return steady
        outside = [t for t in steady
                   if not opened <= t <= opened + EXPOSURE_S]
        return outside + [opened + 1.0 + k for k in range(n)]

    def at_shutter(rec) -> None:
        state.setdefault("open", rec["t"])

    guider = StampedGuider(err, stamps)
    hub.guider = guider
    night = Night(hub, monkeypatch)
    guider.bind(night)
    night.on_capture = at_shutter
    try:
        night.done = await night.run(_plan(), wall_s=60.0)
    finally:
        await night.close()
    assert night.done, night.trace[-3:]
    return night, list(session_store.load(night.session_id).frames)


async def test_a_frame_with_one_over_ceiling_sample_in_its_exposure_is_kept_and_says_it_was_not_judged(
        group_hub, group_store, monkeypatch):
    """THE PIN (#1222). One sample, 8 arcsec out, against a 5 arcsec ceiling:
    the frame was rejected. It is now kept; its ledger entry reads "unjudged"
    with the one sample counted and no guide RMS figure, and the operator is
    told once.

    MUTANT (``_check_quality`` judges ``verdict.rms`` whatever the sample
    count): RED - ``assert False is True`` on ``auto_accepted``: the frame is
    rejected with reason guide_rms.
    """
    night, frames = await _night_with_n_samples_in_the_first_exposure(
        group_hub, monkeypatch, 1)
    assert [f.auto_accepted for f in frames] == [True], [
        (f.auto_accepted, f.reject_reason) for f in frames]
    (frame,) = frames
    assert frame.reject_reason is None
    assert frame.guide_verdict == "unjudged"
    assert frame.metrics["guide_samples"] == 1.0
    assert "guide_rms" not in frame.metrics, frame.metrics
    assert night.engine._rejected == 0
    said = night.said("did not judge a frame")
    assert len(said) == 1, said
    assert "1 sample" in said[0] and f"{GUIDE_MIN_SAMPLES} are needed" in said[0]


async def test_two_samples_are_not_a_verdict_and_three_are(
        group_hub, group_store, monkeypatch):
    """The boundary, both sides, on the engine's own constant: two over-ceiling
    samples leave the frame unjudged and kept; three judge it, over the
    ceiling, and reject it for guide RMS."""
    assert GUIDE_MIN_SAMPLES == 3, "premise: the floor the guard already uses"
    _night2, two = await _night_with_n_samples_in_the_first_exposure(
        group_hub, monkeypatch, GUIDE_MIN_SAMPLES - 1)
    assert two[0].auto_accepted is True
    assert two[0].guide_verdict == "unjudged"
    assert two[0].metrics["guide_samples"] == float(GUIDE_MIN_SAMPLES - 1)


async def test_three_over_ceiling_samples_reject_the_frame(
        group_hub, group_store, monkeypatch):
    night, frames = await _night_with_n_samples_in_the_first_exposure(
        group_hub, monkeypatch, GUIDE_MIN_SAMPLES)
    first = frames[0]
    assert first.auto_accepted is False
    assert first.reject_reason == "guide_rms"
    assert first.guide_verdict == "over_ceiling"
    assert first.metrics["guide_samples"] == float(GUIDE_MIN_SAMPLES)
    assert first.metrics["guide_rms"] == pytest.approx(BAD, abs=0.01)
    assert not night.said("did not judge a frame")


def test_the_floor_is_the_one_the_pre_shutter_guard_uses(monkeypatch):
    """The verdict's ``judged`` and the guard's ``_guide_unfit_now`` read the
    same constant: with the floor raised to five, a four-sample window over
    the ceiling is neither judged nor held; with five it is both."""
    monkeypatch.setattr(engine_mod, "GUIDE_MIN_SAMPLES", 5)
    eng = SequenceEngine(Hub())
    monkeypatch.setattr(eng, "_guide_reject_armed", lambda calibration=False: True)
    for samples, expect in ((4, False), (5, True)):
        verdict = GuideVerdict(BAD, True, samples, CEILING)
        monkeypatch.setattr(eng, "_trailing_guide_verdict", lambda v=verdict: v)
        assert verdict.judged is expect
        assert eng._guide_unfit_now() is expect


def test_a_guider_with_no_sample_list_is_still_judged_on_its_figure():
    """The floor does not reach a guider that publishes no sample list at all:
    it has only its own figure (``samples`` 0), judged as the gate always did."""
    verdict = GuideVerdict(8.0, True, 0, CEILING)
    assert verdict.judged and verdict.over
    assert verdict.word == "over_ceiling"


def test_a_verdict_with_no_figure_is_not_judged():
    assert GuideVerdict(None, True, 0, CEILING).judged is False
    assert GuideVerdict(None, True, 0, CEILING).word == "unjudged"
    assert GuideVerdict(8.0, True, 2, CEILING).word == "unjudged"
    assert GuideVerdict(8.0, True, 2, CEILING).over is False


async def test_an_unjudged_frame_does_not_count_toward_the_reject_streak(
        group_hub, group_store, monkeypatch):
    night, _frames = await _night_with_n_samples_in_the_first_exposure(
        group_hub, monkeypatch, 1)
    assert night.engine._night_rejects == 0
