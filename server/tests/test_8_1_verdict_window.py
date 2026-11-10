# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1146 part 1: an exposure is judged on every sample stamped inside it.

The verdict window is the exposure, but its samples came from
``GuideStats.recent``: 120 at most (the engine's ring is 100), about 150 s at
the native cadence. An exposure of five minutes was judged on its last 150 s,
so one that tracked badly early and well late passed. ``stats().recent`` rides
the bus on every guide cycle and cannot grow, and the engine's ring is the
window the live ``rms_total`` is computed over (``RECENT_CAP`` stays 100), so
the native and PHD2 guiders now keep a trace of ``GUIDE_TRACE_S`` of their own
(``Guider.guide_samples``) and the verdict reads that.

The engine cases run the real frame loop on the clocked simulator
(tests/_group_harness.py). The guider cases drive the real
``NativeGuider._guide_loop`` over a scripted engine, and ``PHD2Guider`` over
GuideStep events.
"""
from __future__ import annotations

import math
import time

import pytest

import astrodeck.guide.native as nativemod
from astrodeck.guide.base import GUIDE_TRACE_S
from astrodeck.guide.native import NativeGuider
from astrodeck.guide.phd2 import PHD2Guider
from astrodeck.sequence import SequencePlan
from astrodeck.sequence.models import ExposureStep
from astrodeck.sequence.session import session_store

from _group_harness import (  # noqa: F401
    Night, group_hub, group_store, single)
from _w8_1_guider import StampedGuider
from test_native_guider_relocks import _Cam, _FakeNative, _Tel

CEILING = 5.0
GOOD = 0.5
BAD = 8.0
EXPOSURE_S = 300.0
BAD_FOR_S = 150.0


def _plan() -> SequencePlan:
    target = single("Alpha", count=1)
    target.steps[0].exposure_s = EXPOSURE_S
    return SequencePlan(targets=[target], name="w81long", guide=True,
                        recover_guiding=True, max_guide_rms=CEILING,
                        dither_every=0, autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        count_mode="accepted")


async def _long_night(hub, monkeypatch, *, trace: bool):
    state: dict[str, float] = {}

    def err(t: float) -> float:
        opened = state.get("open")
        return BAD if opened is not None \
            and opened <= t <= opened + BAD_FOR_S else GOOD

    guider = StampedGuider(
        err, lambda now: [float(t) for t in range(int(now) - 400, int(now) + 1)],
        trace=trace)
    hub.guider = guider
    night = Night(hub, monkeypatch)
    guider.bind(night)
    night.on_capture = lambda rec: state.setdefault("open", rec["t"])
    try:
        night.done = await night.run(_plan(), wall_s=90.0)
    finally:
        await night.close()
    assert night.done, night.trace[-3:]
    return list(session_store.load(night.session_id).frames)


async def test_a_long_exposure_that_tracked_badly_early_is_judged_on_all_of_it(
        group_hub, group_store, monkeypatch):
    """A 300 s exposure, 8 arcsec out for its first 150 s and 0.5 after. Over
    the whole exposure the RMS is above the 5 arcsec ceiling and the frame is
    rejected; the last 120 samples alone (what the published list holds) are
    all good.

    MUTANT (the verdict reads ``stats().recent`` and not the guider's trace):
    RED - the first frame is accepted.
    """
    frames = await _long_night(group_hub, monkeypatch, trace=True)
    assert frames[0].auto_accepted is False
    assert frames[0].reject_reason == "guide_rms"
    assert frames[0].metrics["guide_samples"] > 250
    assert frames[0].metrics["guide_rms"] > CEILING


async def test_the_same_exposure_through_a_guider_with_no_trace_passes_on_its_tail(
        group_hub, group_store, monkeypatch):
    """The premise the test above rests on, from the same samples: a guider
    that keeps no trace is judged on its clipped list, which holds only the
    good end of the exposure."""
    frames = await _long_night(group_hub, monkeypatch, trace=False)
    assert frames[0].auto_accepted is True
    assert frames[0].metrics["guide_samples"] <= 120


def test_the_trace_covers_the_longest_exposure_the_plan_accepts():
    ExposureStep(filter="L", exposure_s=3600.0, count=1)
    with pytest.raises(ValueError):
        ExposureStep(filter="L", exposure_s=3601.0, count=1)
    assert GUIDE_TRACE_S > 3600.0


# ------------------------------------------------------------ the native trace

class _RingEngine:
    """A ``GuideEngine`` stand-in with the real ring's behaviour: every frame
    pushes ``(t, ra_px, dec_px)``, the ring holds 100 and ``clear_at`` empties
    it as an engine reset boundary does."""

    def __init__(self, frames: int, clear_at: int | None = None) -> None:
        self.ring: list[tuple[float, float, float]] = []
        self.n = 0
        self.frames = frames
        self.clear_at = clear_at

    def process(self, data, ts, exposure_s):
        self.n += 1
        if self.clear_at == self.n:
            self.ring.clear()
        self.ring.append((float(ts) + self.n * 1e-3, 1.0, 0.0))
        self.ring = self.ring[-100:]
        return {"action": "idle"}

    def stats(self):
        return {"guiding": True, "settling": False, "recent": list(self.ring)}

    def dump_calibration(self):
        return None

    def begin_guiding(self):
        self.ring.clear()


def _native(monkeypatch, frames: int, **kw):
    monkeypatch.setattr(nativemod, "_native", _FakeNative([(30.0, 30.0)]))
    engine = _RingEngine(frames, **kw)
    g = NativeGuider(None, _Tel(), config={"image_scale_arcsec": 2.0,
                                           "image_scale_known": True,
                                           "exposure_s": 0.01}, profile_id=None)
    g.cam = _Cam(frames, g._stop)
    g._engine = engine
    g._active = True
    g._stop.clear()
    return g, engine


async def test_the_native_trace_outlives_the_published_list(monkeypatch):
    """Two hundred and fifty measured frames: the engine's ring and the
    published list are clipped at 100 and 120; the trace holds every one, in
    arcsec, and answers a window."""
    g, engine = _native(monkeypatch, 250)
    await g._guide_loop()
    stats = g.stats()
    assert len(engine.ring) == 100
    assert len(stats.recent) == 100
    everything = g.guide_samples(0.0, float("inf"))
    assert len(everything) == 250
    assert everything[0]["ra"] == pytest.approx(2.0)     # 1 px at 2 arcsec/px
    mid = everything[100]["t"]
    assert [s["t"] for s in g.guide_samples(mid, everything[109]["t"])] == [
        s["t"] for s in everything[100:110]]


async def test_the_native_trace_clears_when_the_engine_clears_its_ring(
        monkeypatch):
    """The engine resets its ring at its own boundaries (a calibration that
    completes). The trace goes with it, or the settle wait would pass early on
    samples from before the reset."""
    g, engine = _native(monkeypatch, 60, clear_at=41)
    await g._guide_loop()
    assert len(g.guide_samples(0.0, float("inf"))) == 20


def test_the_native_trace_is_pruned_by_time(monkeypatch):
    g, engine = _native(monkeypatch, 5)
    g._trace.extend([(1.0, 1.0, 0.0), (2.0, 1.0, 0.0)])
    newest = 2.0 + GUIDE_TRACE_S + 1.0
    engine.ring = [(newest, 1.0, 0.0)]
    g._note_sample()
    assert [s["t"] for s in g.guide_samples(0.0, float("inf"))] == [newest]


# --------------------------------------------------------------- the PHD2 trace

def test_the_phd2_trace_outlives_the_published_list_and_the_rms_window():
    g = PHD2Guider(pixel_scale_arcsec=1.0)
    now = time.time()
    for i in range(500):
        sample = {"t": now - 500 + i, "ra": 1.0, "dec": 0.0}
        g._samples.append(sample)
    g._handle_event({"Event": "GuideStep", "RADistanceRaw": 3.0,
                     "DECDistanceRaw": 4.0, "SNR": 20.0})
    stats = g.stats()
    assert len(stats.recent) == 120
    assert stats.rms_total == pytest.approx(
        math.sqrt(1.24), abs=0.01)    # RMS over the last 100: 99 x (1, 0), 1 x (3, 4)
    window = g.guide_samples(now - 500, now - 401)
    assert len(window) == 100
    assert len(g.guide_samples(0.0, float("inf"))) == 501
