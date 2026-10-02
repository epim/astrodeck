# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""`SequenceEngine.measured_cost(kind)`: what the engine has MEASURED an event
to cost, ``(mean_s, samples)``, or None when it has measured none (S3, for
the doctor's hop note M10, spec 1.8: "only when a measured cost is
injected").

The finish clock and the meridian rule read `_event_cost`, which falls back
to an assumed seed (``HOP_COST_S``, the spec's A.3 figure, timed on no rig)
because a clock needs some number. A caller that tells the OPERATOR what a
hop costs must be able to tell a measurement from that guess, or it advises
on a number nobody timed. So the public accessor never returns the seed.

Each case names the mutant it was shown RED under, with the failure observed,
verbatim. Every mutant was applied in a private scratch copy of server/
(scratchpad/s3-eb-mut-p8w3), never in the shared tree (#254).
"""
from __future__ import annotations

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import Night, grid_plan, group_hub, group_store
from astrodeck.hub import Hub
from astrodeck.sequence.engine import SequenceEngine


def test_an_unmeasured_kind_is_none_never_the_seed():
    """A fresh engine has measured nothing: every kind is None, while the
    clocks still get their seeds from `_event_cost`.

    MUTANT "return the seed HOP_COST_S when unmeasured" (``measured_cost``
    answering ``(HOP_COST_S, 0)`` for an unmeasured hop). RED (observed):
        AssertionError: hop
        assert (150.0, 0) is None
         +  where (150.0, 0) = measured_cost('hop')
    """
    eng = SequenceEngine(Hub())
    for kind in ("hop", "dither", "autofocus", "flip"):
        assert eng.measured_cost(kind) is None, kind
    assert eng._event_cost("hop", engine_mod.HOP_COST_S) == engine_mod.HOP_COST_S


def test_measured_samples_are_the_rolling_mean_and_its_count():
    """Two hops of 100 s and 200 s are ``(150.0, 2)``, the mean the clocks
    use. The mean is over the last ten, as `_event_cost`'s is, so twelve
    samples report ten. A non-positive duration is no measurement (the
    recorder drops it), and one kind's samples say nothing of another's.

    MUTANT "the window never cut" (`_record_event_cost` keeps every sample,
    so the mean and the count run over all of them). RED (observed):
        assert (77.14285714285714, 14) == (75.0 +- 7.5e-05, 10)
          At index 0 diff: 77.14285714285714 != 75.0 +- 7.5e-05
    (pytest prints the approx tolerance with a plus-minus sign, spelt +-
    here.)
    """
    eng = SequenceEngine(Hub())
    eng._record_event_cost("hop", 100.0)
    eng._record_event_cost("hop", 200.0)
    assert eng.measured_cost("hop") == (150.0, 2)
    assert eng._event_cost("hop", engine_mod.HOP_COST_S) == 150.0
    eng._record_event_cost("dither", 0.0)
    eng._record_event_cost("dither", -3.0)
    assert eng.measured_cost("dither") is None
    for s in range(10, 130, 10):          # twelve more: 10 .. 120
        eng._record_event_cost("hop", float(s))
    assert eng.measured_cost("hop") == (pytest.approx(75.0), 10)
    assert eng.measured_cost("flip") is None


async def test_a_nights_hops_are_what_it_reports(group_hub, group_store,
                                                 monkeypatch):
    """On the clocked simulator, each hop's goto takes 90 s of the night's
    time. After a 2x2's twelve visits the accessor reports the hops
    `_setup_target` timed, not the seed: a mean of 90 s over the ten most
    recent. An engine that has run nothing has measured none.

    MUTANT "return the seed HOP_COST_S when unmeasured": the night's own
    answer is unchanged (it measured its hops), and the engine that ran
    nothing reads the seed. RED (observed):
        AssertionError: assert (150.0, 0) is None
    MUTANT "the window never cut": RED (observed):
        AssertionError: (90.0, 12)
        assert (90.0, 12) == (90.0, 10)
    """
    box: dict = {}

    def goto(who, n, result):
        box["night"].clock.t += 90.0
        return result

    night = Night(group_hub, monkeypatch, goto=goto)
    box["night"] = night
    try:
        done = await night.run(grid_plan())
        measured = night.engine.measured_cost("hop")
    finally:
        await night.close()
    assert done, night.trace[-3:]
    hops = len(night.gotos)
    assert hops == 12, hops
    assert measured == (90.0, 10), measured
    fresh = SequenceEngine(group_hub)
    assert fresh.measured_cost("hop") is None
