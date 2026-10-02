# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A panel set aside for tonight is marked skipped in the session report
(#189 S2, #318; spec 6.7; the docstring of `SequenceEngine._set_panel_aside`).

`_set_panel_aside` says four things happen to a member set aside tonight: it
is recorded, saved, said with a warning, and "marked skipped in the report".
The first three are held by test_group_rotation.py and
test_group_set_aside_persisted.py. The fourth was a claim nothing kept: the
S2 review deleted the ``reporter.mark_skipped(target)`` call in a private
scratch copy of server/ and every S2 test still passed (396 passed). The
report's timeline is the morning-after record of the night, and a panel that
drops out of it reads as a panel that was never in the plan.

Each case runs the real engine on the clocked simulator
(tests/_group_harness.py) and reads the report the run built, through
`SessionReporter.build`, the shape the report route serves.

MUTANT "set aside not marked skipped in report" (the ``if self.reporter:
self.reporter.mark_skipped(target)`` lines at the end of `_set_panel_aside`
deleted), run in a private scratch copy of server/ (#254): RED on both
cases (observed, ``-n0``):
    test_a_panel_set_aside_is_marked_skipped_once:
        E       AssertionError: []
        E       assert [] == ['skipped M31 2-2']
        E         Right contains one more item: 'skipped M31 2-2'
    test_a_mosaic_set_aside_whole_marks_every_panel_skipped:
        E       AssertionError: []
        E       assert [] == ['skipped M31...pped M31 2-2']
        E         Right contains 4 more items, first extra item: 'skipped M31 1-1'
"""
from __future__ import annotations

import asyncio

import astrodeck.sequence.engine as engine_mod
from _group_harness import GROUP_NAME, Night, grid_plan, group_hub, group_store  # noqa: F401
from astrodeck.sequence.session import session_store


async def _night(hub, monkeypatch, plan, **kw) -> Night:
    night = Night(hub, monkeypatch, **kw)
    try:
        night.done = await night.run(plan)
    finally:
        await night.close()
    return night


def _skips(night: Night) -> list[str]:
    """The report timeline's skip entries, in order."""
    events = night.engine.reporter.build().safety_events
    return [e["reason"] for e in events if e.get("action") == "skip"]


async def test_a_panel_set_aside_is_marked_skipped_once(group_hub, monkeypatch):
    """Panel 2-2 never centres and is set aside at its third deferral (the
    case test_group_rotation.py grades for the alert and the record). The
    report's timeline marks it skipped exactly once. The three panels that
    complete are the control: none of them is marked, so the entry says
    which panel dropped out, not that the night had panels.

    RE-PINNED FOR H4 (#534, H4 orchestrator ruling 2): 2-2's first streak
    of centring misses now sets it aside "for now", which expires 45 minutes
    on; its second streak sets it aside for the night. So the session holds
    two records, the first marked expired, where it held one before H4, and
    the report still marks 2-2 skipped once: only the set-aside for the
    night is a skip (5.1). RED under H4-ENG-A's mutant "report marks a
    for-now set-aside skipped" (`_set_panel_aside`'s ``if self.reporter and
    not expires:`` made ``if self.reporter:``), re-run by the H4 integration
    in a private copy of server/ (scratchpad H4-INTEG-mut), observed:

        AssertionError: ['skipped M31 2-2', 'skipped M31 2-2']
        assert ['skipped M31...pped M31 2-2'] == ['skipped M31 2-2']
          Left contains one more item: 'skipped M31 2-2'

    RE-PINNED FOR WP-44 (#524, 2026-09-30, deliberate): ``mark_skipped`` now
    takes the reason `_set_panel_aside` already held and appends it after a
    colon (spec 6.7, "the report names every set-aside panel and its
    reason"), so the bare "skipped {name}" text this test pinned before is
    gone on purpose; the assertion below pins the full reason-suffixed line
    the last set-aside (the mosaic's anti-spin, out of passes) actually
    writes.
    """
    def goto(who, n, result):
        if who == f"{GROUP_NAME} 2-2":
            return {**result, "centered": False, "error_arcmin": None}
        return result

    night = await _night(group_hub, monkeypatch, grid_plan(), goto=goto)
    assert night.done, night.trace[-3:]
    stored = session_store.load(night.session_id)
    assert [(r["target_id"], bool(r.get("expired")))
            for r in stored.set_aside] == [("p11", True), ("p11", False)], (
        "premise: 2-2 was set aside for now, and then for the night")
    assert _skips(night) == [
        f"skipped {GROUP_NAME} 2-2: 2-2 (the mosaic's last live panel) has "
        f"been held for 6 passes in a row with no panel struck and no "
        f"progress made; set aside for tonight",
    ], _skips(night)


async def test_a_mosaic_set_aside_whole_marks_every_panel_skipped(
        group_hub, monkeypatch):
    """A pass that takes no exposure sets every panel aside at the pass
    boundary (the group anti-spin, `_close_group_pass`), with one warning for
    the mosaic and no line per panel. The report still marks each of the
    four panels skipped, once: the quiet path is quiet in the log, not in the
    report."""
    async def exposes_nothing(self, ti, si, target, step, max_frames=None):
        await asyncio.sleep(0)

    monkeypatch.setattr(engine_mod.SequenceEngine, "_run_step", exposes_nothing)
    night = await _night(group_hub, monkeypatch, grid_plan())
    assert night.done, night.trace[-3:]
    # RE-PINNED FOR WP-44 (#524, 2026-09-30, deliberate): same reason-suffixed
    # text as above, this time the anti-spin's own line ("a full pass over 4
    # panels took no exposures; setting the mosaic aside for tonight"), once
    # per panel.
    reason = ("a full pass over 4 panels took no exposures; setting the "
              "mosaic aside for tonight")
    assert sorted(_skips(night)) == [f"skipped {GROUP_NAME} {lb}: {reason}"
                                     for lb in ("1-1", "1-2", "2-1", "2-2")], (
        _skips(night))
