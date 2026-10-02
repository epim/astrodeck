# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The slew gate's pier-guard reads are bounded, and the mount's own side is
read once per selection (#314, S3 orchestrator ruling 2; spec 5.1 item 1,
section 7's P0-2 dead-link policy).

`_mount_floor_verdict`'s pier half asks the mount two things when
``safety.enforce_pier_limits`` is on and the mount reports a destination
side: which side a slew to the target lands on (``destination_pier_side``)
and which side it is on now (``pier_side``). They were the engine's only
unbounded mount awaits, and S2 moved them onto the scheduler's selection,
once per live member at every selection (`_eligibility_now`). A wedged link
then hung the selection, and with it the safety gate and the idle park-hold
that only the scheduler's wait runs.

Now each read is bounded by ``MOUNT_QUERY_TIMEOUT_S``, a read past it is the
dead-link SafetyAbort that ends the run (P0-2), and the side the mount is on
now, the same answer for every member, is read once per selection and
shared. Each member's destination side is its own, and is read for each.

THE TELESCOPE DOUBLE. The simulator's mount with its two pier reads replaced
by counting doubles that answer WEST for every question, so no destination
ever differs from the side the mount is on and the guard refuses nothing:
what is under test is how often it asks and how long it may wait, not what it
decides. The plan has meridian flips off, which is when the pier guard
matters (it refuses only then) and keeps the meridian rule, which reads the
side for itself, out of the count. Everything else is the real scheduler on
the clocked simulator (tests/_group_harness.py).

THE BOUND IS REAL TIME. `_bounded` is ``asyncio.wait_for``, which the harness
leaves on the event loop's own clock, so a hung read is cut after
``MOUNT_QUERY_TIMEOUT_S`` REAL seconds. The hung-read cases set it to 1 s
(the engine's module constant, read at the call), so a bounded read ends the
run in about a second and an unbounded one meets the harness's wall-clock
bound instead.

Each case names the mutant it was shown RED under, with the failure observed,
verbatim (long lines wrapped). Every mutant was applied in a private scratch
copy of server/ (scratchpad/s3-eb-mut-p8w3), never in the shared tree
(#254).
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import Night, grid_plan, group_hub, group_store
from astrodeck.config import SafetyConfig
from astrodeck.devices.base import PierSide
from astrodeck.sequence.session import session_store


class _CountingPier:
    """The two pier reads of the simulator's mount, counted, answering WEST;
    a read named in ``hang`` never answers at all."""

    def __init__(self, tel, monkeypatch, *, hang: str | None = None):
        self.reads = {"pier_side": 0, "destination": 0}
        self.hang = hang

        async def pier_side():
            self.reads["pier_side"] += 1
            if self.hang == "pier_side":
                await asyncio.Event().wait()
            return PierSide.WEST

        async def destination_pier_side(ra_hours, dec_deg):
            self.reads["destination"] += 1
            if self.hang == "destination":
                await asyncio.Event().wait()
            return PierSide.WEST

        assert tel.reports_destination_pier_side
        monkeypatch.setattr(tel, "pier_side", pier_side)
        monkeypatch.setattr(tel, "destination_pier_side",
                            destination_pier_side)


def _count_selections(night: Night, pier: _CountingPier, monkeypatch):
    """Wrap the engine's selection so each call records the reads made
    inside it: ``(pier_side reads, destination reads, live members)``."""
    real = night.engine._eligibility_now
    per_selection: list[tuple[int, int, int]] = []

    async def counted(remaining, *a, **kw):
        live = sum(1 for t in remaining
                   if night.engine._group_of(t) is not None)
        before = dict(pier.reads)
        try:
            return await real(remaining, *a, **kw)
        finally:
            per_selection.append(
                (pier.reads["pier_side"] - before["pier_side"],
                 pier.reads["destination"] - before["destination"], live))

    monkeypatch.setattr(night.engine, "_eligibility_now", counted)
    return per_selection


async def _run(hub, monkeypatch, *, hang: str | None = None,
               wall_s: float = 60.0):
    pier = _CountingPier(hub.devices["telescope"], monkeypatch, hang=hang)
    night = Night(hub, monkeypatch)
    selections = _count_selections(night, pier, monkeypatch)
    t0 = time.monotonic()
    try:
        night.done = await night.run(grid_plan(), wall_s=wall_s)
    finally:
        night.real_s = time.monotonic() - t0
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night, pier, selections


async def test_four_live_members_read_the_mounts_side_once_per_selection(
        group_hub, group_store, monkeypatch):
    """The pier guard armed, a 2x2: the first selection asks the pier half
    for all four live members, and reads the side the mount is on ONCE,
    with one destination read for each member. Every later selection that
    asks the pier half at all reads the side once too. The run completes.

    MUTANT "per-member read" (the shared side dropped: ``pier_now`` never
    written, so each member reads ``pier_side`` itself). RED (observed):
        AssertionError: (4, 4, 4)
        assert (4, 4, 4) == (1, 4, 4)
    """
    group_store.set_safety(SafetyConfig(enabled=False,
                                        enforce_pier_limits=True))
    night, pier, selections = await _run(group_hub, monkeypatch)
    assert night.done, night.trace[-3:]
    assert night.engine.state.get("end_reason") == "complete"
    assert selections[0] == (1, 4, 4), selections[0]
    for side_reads, dest_reads, _live in selections:
        assert side_reads == (1 if dest_reads else 0), selections


@pytest.mark.parametrize("hang", ["destination", "pier_side"])
async def test_a_hung_pier_read_ends_the_run_within_its_bound(
        group_hub, group_store, monkeypatch, hang):
    """The pier guard armed, and one of its reads never answers. The first
    selection's read is cut at ``MOUNT_QUERY_TIMEOUT_S`` (1 s here) and the
    run ends through the dead-link SafetyAbort (P0-2) in about that long,
    before any hop, with the read named. Never a hang, and never a guard
    that lets the slew through as though the side were merely unknown.

    MUTANT "unbounded read" (`_pier_guard_read` awaiting the read with no
    bound). The selection hangs on the read. The loop is idle, not
    spinning, so the spin watchdog (#319, which catches a loop that never
    yields) stays quiet and the harness's own wall-clock bound ends it. RED
    on each (observed):
        AssertionError: the run did not end: the selection hung on the pier
        read for 15 s
    """
    monkeypatch.setattr(engine_mod, "MOUNT_QUERY_TIMEOUT_S", 1.0)
    group_store.set_safety(SafetyConfig(enabled=False,
                                        enforce_pier_limits=True))
    night, pier, selections = await _run(group_hub, monkeypatch, hang=hang,
                                         wall_s=15.0)
    assert night.done, (
        f"the run did not end: the selection hung on the pier read for "
        f"{night.real_s:.0f} s")
    what = {"destination": "the mount's destination pier side",
            "pier_side": "the mount's pier side"}[hang]
    assert night.engine.state.get("end_reason") == "unsafe", (
        night.engine.state.get("end_reason"), night.engine.state.get("detail"))
    assert night.engine.state.get("detail") == f"{what} timed out after 1s"
    assert night.said(f"{what} timed out after 1s \u2014 aborting"), night.lines
    assert night.gotos == [] and night.shots() == []
    assert len(selections) == 1, selections
    assert night.real_s < 10.0, night.real_s


async def test_control_with_the_pier_guard_off_the_pier_is_never_read(
        group_hub, group_store, monkeypatch):
    """Control: with ``enforce_pier_limits`` off, no selection reads either
    side, and the destination side is never read at all. (The hop's own pier
    check, `_group_pier_check`, still reads the side the mount landed on
    after each goto; that is outside the selection and is not the guard.)

    MUTANT "the reads ignore the guard" (``pier and`` dropped from the pier
    half's condition in `_mount_floor_verdict`). RED (observed):
        AssertionError: (1, 4, 4)
        assert (1, 4, 4) == (0, 0, 4)
    """
    group_store.set_safety(SafetyConfig(enabled=False,
                                        enforce_pier_limits=False))
    night, pier, selections = await _run(group_hub, monkeypatch)
    assert night.done, night.trace[-3:]
    assert night.engine.state.get("end_reason") == "complete"
    assert selections[0] == (0, 0, 4), selections[0]
    assert all(s[:2] == (0, 0) for s in selections), selections
    assert pier.reads["destination"] == 0, pier.reads
