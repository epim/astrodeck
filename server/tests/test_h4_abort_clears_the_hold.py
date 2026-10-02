# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A cloud hold that ends any way but its release takes its hold with it
(#513; spec 5.8, and the class of #285 and #422: a merge carries a field
past its fact).

THE DEFECT. `_hold_for_clear` published ``hold=None`` on its release and on
its StopTarget arm only. An Abort pressed in the hold, and a SafetyAbort out
of it (the 45-minute bound, an unsafe verdict inside it), left through its
``finally``, which published nothing, and `_set_state` merges: the aborting
state and the terminal one carried ``hold: "clouds"``, and GET
/api/sequence/state served an aborted run that still named a hold until the
next start. Found recording S7-URUNHOLD for #451, on the clocked simulator.

AS BUILT. `_hold_cleared` is the one rule: ``{"hold": None}`` while the
state names a hold, with ``_holding_for_clear`` lowered so the same
publish's ``sky.holding`` agrees. The hold's ``finally`` publishes it on
every exit that did not release, and `abort` passes it with "aborting",
which is published before the cancellation reaches the hold: the whole
wind-down, not only the terminal state, reads as a run stopping.

THE NIGHTS are test_s5_recorded_state.py's second night (S7-URUNHOLD): a
rotating 2x2 of the fixture mosaic whose 2-2 never centres, safety armed
with no monitor assigned, the frames' own cloud verdict standing in for
one, and the sky shut once 2-2 is set aside. The Abort case is that night
exactly (`_held_states`, the Abort pressed at the hold's first probe); the
SafetyAbort case is the same night with no pause and no Abort, the sky left
shut until the hold's own bound ends the run. Every state is taken as GET
/api/sequence/state would have answered it at each publish, up to and
including the terminal one.

Every mutant was applied in a private copy of ``server/`` (scratchpad
``H4-ENG-B-mut``), from a byte backup restored and sha256-checked after each,
never in the shared tree (#254). The observed failure is quoted verbatim
(the first assertion line, long lines wrapped).
"""
from __future__ import annotations

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import (Night, close_night_hub, group_hub,  # noqa: F401
                            group_store, night_hub)
from astrodeck.config import SafetyConfig
from test_s5_recorded_state import (SHUT_SKY, _held_states, _misses_2_2,
                                    _night_plan, _record, _session)

pytestmark = pytest.mark.asyncio


def _view(served: list[dict]) -> list[tuple]:
    """Each served state as (state, hold, sky.holding), for a message."""
    return [(s.get("state"), s.get("hold"), (s.get("sky") or {}).get("holding"))
            for s in served]


def _carries_a_hold(s: dict) -> bool:
    return s.get("hold") is not None or bool((s.get("sky") or {}).get("holding"))


async def test_an_abort_in_a_cloud_hold_leaves_no_hold_on_the_wind_down(
        group_store, monkeypatch):
    """S7-URUNHOLD: an Abort pressed at the cloud hold's first probe. The
    hold was published (``hold: "clouds"``, ``sky.holding`` true); every
    state from the first "aborting" on, the terminal "aborted" included,
    names no hold and says ``sky.holding`` false.

    RED under mutant "drop the clear" (`_hold_cleared` answering ``{}`` at
    once, leaving the flag up), observed:

        AssertionError: the wind-down of an Abort pressed in a cloud hold
        still carries the hold: [('aborting', 'clouds', True), ('aborted',
        'clouds', False)]

    RED under mutant "abort does not clear" (`abort`'s "aborting" publish
    without ``**self._hold_cleared()``, the hold's ``finally`` still
    clearing), observed:

        AssertionError: the wind-down of an Abort pressed in a cloud hold
        still carries the hold: [('aborting', 'clouds', True)]

    (The terminal state is clean under that one: the ``finally`` publishes
    the clear once the cancellation lands. The first "aborting" is what it
    leaves, for as long as the cancellation takes to reach the hold.)
    """
    served = await _held_states(group_store, monkeypatch)
    held = [s for s in served
            if s.get("state") == "holding" and s.get("hold") == "clouds"]
    assert held and (held[0].get("sky") or {}).get("holding") is True, (
        f"premise: a cloud hold was published: {_view(served)[-8:]}")
    first = next((k for k, s in enumerate(served)
                  if s.get("state") == "aborting"), None)
    assert first is not None and served[-1].get("state") == "aborted", (
        f"premise: the Abort wound the run down to its terminal state: "
        f"{_view(served)[-8:]}")
    stale = [v for s, v in zip(served[first:], _view(served[first:]))
             if _carries_a_hold(s)]
    assert stale == [], (
        f"the wind-down of an Abort pressed in a cloud hold still carries "
        f"the hold: {stale}")
    assert served[-1].get("end_reason") == "aborted", served[-1].get(
        "end_reason")


async def _held_to_the_bound(store, monkeypatch) -> tuple[list[dict], Night]:
    """S7-URUNHOLD's second night with no pause and no Abort: the sky shuts
    from the frame after 2-2 is set aside and stays shut, so the hold runs
    to ``CLOUD_MAX_HOLD_MIN`` and ends the run with its SafetyAbort. Every
    state, as `_record` takes them, and the night."""
    store.set_safety(SafetyConfig(enabled=True, sky_fallback_hold=True))
    hub, popped = await night_hub(monkeypatch)
    try:
        hub.mode = "native"                 # a real rig: its frames are a sky
        hub.devices.pop("safety", None)     # ...with no monitor assigned
        plan = _night_plan()
        night = Night(hub, monkeypatch, goto=_misses_2_2)
        served = _record(night, monkeypatch)
        marks = {"armed": False, "shut": False}
        harness_capture = hub.capture

        def on_capture(rec):
            if marks["armed"]:
                marks["shut"] = True
            elif (rec["group"] or {}).get("set_aside"):
                marks["armed"] = True

        async def capture(exposure_s, *a, **kw):
            info = await harness_capture(exposure_s, *a, **kw)
            if marks["shut"]:
                info = {**info, "cloud": dict(SHUT_SKY)}
            return info

        night.on_capture = on_capture
        monkeypatch.setattr(hub, "capture", capture)
        try:
            done = await night.run(plan, wall_s=120.0,
                                   session=_session(night, plan))
        finally:
            await night.close()
        assert done, f"premise: the held night ended: {night.trace[-3:]}"
        return served, night
    finally:
        await close_night_hub(hub, popped)


async def test_a_hold_ended_by_its_bound_leaves_no_hold_on_the_terminal_state(
        group_store, monkeypatch):
    """The same night, the sky never clearing: the hold runs to its
    ``CLOUD_MAX_HOLD_MIN`` bound and raises its SafetyAbort. The terminal
    state, "aborted" with ``end_reason`` "unsafe", names no hold and says
    ``sky.holding`` false; so does the clear the ``finally`` published just
    before it.

    RED under mutant "drop the clear" (`_hold_cleared` answering ``{}`` at
    once), observed (the terminal state still names the hold, so no state
    follows the last one that does, and the message shows the last two):

        AssertionError: the run the cloud hold's bound ended still carries
        the hold: [('holding', 'clouds', True), ('aborted', 'clouds', False)]

    RED under mutant "the finally does not clear" (the hold's ``finally``
    publishing nothing, `abort`'s clear kept), observed: the same line. The
    Abort case above stays green under it, since `abort` clears first.
    """
    served, night = await _held_to_the_bound(group_store, monkeypatch)
    bound = f"cloud hold exceeded {engine_mod.CLOUD_MAX_HOLD_MIN:.0f} min"
    assert night.said(bound), (
        f"premise: the hold's own bound ended the run: {night.lines[-4:]}")
    held = [k for k, s in enumerate(served) if s.get("hold") == "clouds"]
    assert held, f"premise: a cloud hold was published: {_view(served)[-6:]}"
    last = served[-1]
    assert last.get("state") == "aborted" and last.get(
        "end_reason") == "unsafe", (
        f"premise: the bound's SafetyAbort ended the run: {_view(served)[-4:]}")
    stale = [v for s, v in zip(served[held[-1] + 1:],
                               _view(served[held[-1] + 1:]))
             if _carries_a_hold(s)]
    assert served[held[-1] + 1:] and stale == [], (
        f"the run the cloud hold's bound ended still carries the hold: "
        f"{stale or _view(served[-2:])}")
