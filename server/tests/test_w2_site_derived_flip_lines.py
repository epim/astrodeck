# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Two of the flip gate's own lines carry ``site_derived`` (#166, #302,
backlog WP-11 (a), owner-approved 2026-09-30): a completed flip is taken at
the computed crossing (#127), and the idle park-hold's "has reached its
meridian flip point" line is said at the same moment, for a target the
flip gate could not flip right then.

THE THIRD NAMED LINE (hub.py's "meridian flip: stopping guiding and
re-slewing") is NOT flagged here. Flagging it breaks
``tests/test_flip_retry_margin.py::test_the_hub_says_complete_only_for_a_flip``
and ``tests/test_the_flip_bound_covers_a_calibration.py``'s ``bus_lines``
cases: both intercept ``events.bus.log`` with a narrower-than-``**kw``
spy (one is ``tests/conftest.py``'s ``bus_lines`` fixture itself), and a
call passing a keyword such a spy does not accept raises ``TypeError``
through it before the test's own assertion runs. Neither file is owned by
this work package, so the fix stops there (see the WP-11 return's
``blocked_on``).

Each case below builds its own ``bus.log`` spy that DOES accept ``**kw``
(unlike ``bus_lines``), so the exact failure this file's own mutants
produce is visible rather than a TypeError from an unrelated fixture.
"""
from __future__ import annotations

import pytest

from _simhub import sim_hub  # noqa: F401

from astrodeck import events
from astrodeck.devices.base import PierSide
from astrodeck.sequence import SequenceEngine, SequencePlan, schedule
from astrodeck.sequence.models import ExposureStep, Target


async def _noop_gate(context="", target=None):
    return None


def _spy(monkeypatch):
    """Every ``bus.log`` call as ``(level, message, source, site_derived)``,
    the flag read from ``kw`` so a call that never passes it records
    ``False`` rather than raising -- unlike ``tests/conftest.py``'s
    ``bus_lines``, which cannot take the keyword at all (see the module
    docstring)."""
    out: list[tuple[str, str, str, bool]] = []
    real = events.bus.log

    def log(level, message, source="hub", **kw):
        out.append((level, message, source, bool(kw.get("site_derived"))))
        return real(level, message, source, **kw)

    monkeypatch.setattr(events.bus, "log", log)
    return out


def _flip_engine(sim_hub, monkeypatch, *, ttf_s: float, flips: bool):
    """An engine armed for a target ``ttf_s`` seconds of hour angle before
    its transit (negative: past it already), on the real
    `_maybe_meridian_flip` (the shape of test_flip_retry_margin.py's
    ``_engine`` helper, reproduced here rather than imported so this file
    owns everything it mutates)."""
    tel = sim_hub.devices["telescope"]
    lon = sim_hub.site["longitude"]
    ra = (schedule.lst_hours(lon) + ttf_s / 3600.0) % 24.0
    t = Target(name="T", ra_hours=ra, dec_deg=66.11, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t], meridian_flip=True, guide=False,
                          meridian_flip_lead_min=10.0)
    e._cfg = None
    e._flip_armed = True
    e._safety_gate = _noop_gate
    side = {"v": "west"}

    async def pier():
        return {"east": PierSide.EAST,
                "west": PierSide.WEST}.get(side["v"], PierSide.UNKNOWN)

    async def no_countdown():
        return None

    async def goto(ra_h, dec_d, **kw):
        if flips:
            side["v"] = "east" if side["v"] == "west" else "west"
        return {"centered": True, "error_arcmin": 0.2}

    async def hold(target, lead_s=0.0):
        return None

    async def autofocus(label, **kw):
        return True

    monkeypatch.setattr(tel, "pier_side", pier)
    monkeypatch.setattr(tel, "time_to_meridian_flip", no_countdown)
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    e._wait_for_flip_point = hold
    e._autofocus = autofocus
    return e, t


# ------------------------------------------------- the completed-flip line


async def test_a_completed_flip_is_flagged_site_derived(sim_hub, monkeypatch):
    """A flip that actually moves the pier is taken at the crossing (#127:
    the AM5 cannot flip early, so the lead-time attempt no-ops and the one
    retry, past the meridian, is where a flip happens). The engine's own
    "meridian flip complete" line is said at that instant, whatever its
    words, so it is flagged for a holder of the site-derived view only.

    MUTANT "the completion line unflagged" (the ``site_derived=True``
    keyword removed from the success branch's ``bus.log`` call): RED
    (observed):
        AssertionError: the completed flip's line was not flagged:
        [('info', 'T: meridian flip complete (pier side west -> east)',
        'sequence', False)]
    """
    lines = _spy(monkeypatch)
    e, t = _flip_engine(sim_hub, monkeypatch, ttf_s=-5.0, flips=True)
    e._flip_no_op.add(t.id)  # this attempt is the one retry, past the band
    await e._maybe_meridian_flip(t, next_exposure_s=60.0)
    # Two lines match "meridian flip complete": the hub's own (unflagged;
    # see the module docstring) and the engine's, named by the target and
    # the pier sides -- the one this fix flags.
    complete = [row for row in lines
               if row[1].startswith(f"{t.name}: meridian flip complete")]
    assert complete and complete[0][3] is True, (
        f"the completed flip's line was not flagged: {complete}")


async def test_a_no_op_flip_is_not_flagged(sim_hub, monkeypatch):
    """Control: a re-slew that flips nothing is a no-op, not a completed
    flip (#366), and its own warning is not site-timed in the way the
    completion line is -- its words already say nothing moved, with no
    site fact added by when the line landed. Pins that the fix above did
    not flag the no-op branches too, which would break
    ``test_the_no_op_warning_names_the_lead_it_dropped`` in
    tests/test_flip_retry_margin.py (a ``bus_lines`` case)."""
    lines = _spy(monkeypatch)
    e, t = _flip_engine(sim_hub, monkeypatch, ttf_s=540.0, flips=False)
    await e._maybe_meridian_flip(t, next_exposure_s=60.0)
    nothing = [row for row in lines if "nothing flipped" in row[1]]
    assert nothing, "premise: the lead-time no-op attempt logged"
    assert all(row[3] is False for row in nothing), (
        f"a no-op line was flagged site_derived: {nothing}")


# -------------------------------------------- the idle park-hold's flip line


async def test_the_idle_flip_hold_is_flagged_site_derived(sim_hub,
                                                          monkeypatch):
    """`_hold_park("flip", ...)` stops tracking at a target's own flip
    point when the flip gate could not take it right then (#203); that
    moment IS the computed crossing (#302's class), so the warning it logs
    is flagged for a holder of the site-derived view only.

    MUTANT "the flip hold unflagged" (``why_kind == "flip"`` branch
    removed, `_hold_park` always taking the unflagged call): RED
    (observed):
        AssertionError: the flip park-hold's line was not flagged:
        [('warning', 'T has reached its meridian flip point and the flip
        cannot be taken now - stopping tracking; the cloud hold goes on
        but judges no sky until the mount can track the target again',
        'sequence', False)]
    """
    lines = _spy(monkeypatch)
    e = SequenceEngine(sim_hub)
    e._cfg = None
    await e._hold_park(
        "flip", "T has reached its meridian flip point and the flip "
                "cannot be taken now")
    held = [row for row in lines if "has reached its meridian flip point"
            in row[1]]
    assert held and held[0][3] is True, (
        f"the flip park-hold's line was not flagged: {held}")


async def test_an_elsewhere_hold_is_not_flagged(sim_hub, monkeypatch):
    """Control: `_hold_park`'s other reasons ("elsewhere", "ceiling") are
    not timed by the meridian -- a lost pointing or a zenith keep-out is
    not a function of the site -- so only ``why_kind == "flip"`` takes the
    flagged branch. Pins that the conditional did not widen to every
    reason, which would break several ``bus_lines`` cases in
    tests/test_idle_park_hold.py that read this exact warning."""
    lines = _spy(monkeypatch)
    e = SequenceEngine(sim_hub)
    e._cfg = None
    await e._hold_park("elsewhere", "the mount is not on T, and it refused")
    held = [row for row in lines if "the mount is not on T" in row[1]]
    assert held and held[0][3] is False, (
        f"an unrelated hold reason was flagged site_derived: {held}")
