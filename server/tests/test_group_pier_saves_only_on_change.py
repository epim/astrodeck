# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A group's pier record is saved only when it moves (#353 item 6; #312, S3
orchestrator ruling 4; spec 5.7 "only when the state has moved", 3.4).

WHAT IS HELD. `_persist_group_pier` writes ``Session.group_pier`` and saves
the session at once, so a restart tonight keeps the group's one pier change.
`_group_pier_check` calls it on EVERY hop of a flipped group past the
meridian, because one of those hops may be the first readable one after a
change that could not be read, and only then does the record move. So the
save is guarded: the state last written, ``(flipped, side, verified)``, is
kept per group (``_group_pier_saved``), and an unchanged state returns before
anything is written or saved. Without the guard every such hop saved the
whole session file (#97's cost class), and nothing but a source-text check in
test_mosaic_spec_claims.py noticed its removal (#353 item 6).

THE COUNT. Every ``session_store.save_run_state`` call the night makes is
recorded with the function that made it, and only `_persist_group_pier`'s
count: the ledger's own per-frame saves are not the rule under test.

THE NIGHTS, on the clocked simulator (tests/_group_harness.py), a 1x2 with
flips on, one L frame a visit:

* PAST THE MERIDIAN FROM THE START. Both panels stand west of the meridian
  when the night begins, so the group's first hop reads its side, marks the
  group as on the side it takes after the meridian, and saves: the one save
  the rule allows. The mount never changes side after it, and every later hop
  calls `_persist_group_pier` with the same state.
* A STRADDLE: 1-1 completes before the meridian, and 1-2 waits for its
  crossing and makes the group's one pier change there, so the record moves
  exactly twice (the first side read, then the change) and each move is
  saved, while the seven hops after the change save nothing.
* THE CONTROL, a night wholly before the meridian: `_group_pier_check` calls
  `_persist_group_pier` at the first side read only, so there is one save
  with the guard or without it. It shows the count reads the rule and not the
  harness.

MUTANT "delete the only-when-moved early return" (the ``if
self._group_pier_saved.get(group.id) == state: return`` in
`_persist_group_pier` removed), applied in a private scratch copy of server/
(scratchpad s4-engb-mut), never in the shared tree (#254). Each case says
what it observed under it, verbatim. Run again on the finished S4 code in
scratchpad s4-engb-resume-mut, it failed as recorded and the control passed.
"""
from __future__ import annotations

import sys

from _group_harness import Night, grid_plan, group_hub, group_store
from astrodeck.sequence.session import session_store


def _count_pier_saves(night: Night, monkeypatch) -> list[tuple]:
    """Every save `_persist_group_pier` makes, as ``(fake time, the group
    record it saved)``, with the save itself made as always."""
    saves: list[tuple] = []
    real = session_store.save_run_state

    def save_run_state(session):
        if sys._getframe(1).f_code.co_name == "_persist_group_pier":
            saves.append((round(night.clock.t - night.t0, 3),
                          {gid: {k: v for k, v in rec.items() if k != "night"}
                           for gid, rec in session.group_pier.items()}))
        return real(session)

    monkeypatch.setattr(session_store, "save_run_state", save_run_state)
    return saves


async def _night(hub, monkeypatch, plan) -> tuple[Night, list[tuple]]:
    night = Night(hub, monkeypatch)
    saves = _count_pier_saves(night, monkeypatch)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    return night, saves


def _hops(night: Night) -> list[str]:
    return [who for _t, who in night.gotos]


async def test_a_night_with_no_pier_change_saves_the_record_once(group_hub,
                                                                  monkeypatch):
    """Both panels are an hour past the meridian when the night starts, four
    L frames each, one a visit: eight hops on one side. The record is saved
    once, at the first hop's side read, and never again.

    MUTANT "delete the only-when-moved early return": RED, a save on every
    hop (observed):
        AssertionError: 8 saves over 8 hops: [(0.0, {'m31-mosaic':
        {'flipped': True, 'side': 'east', 'verified': False}}), (30.0,
        {'m31-mosaic': {'flipped': True, 'side': 'east', 'verified': False}}),
        (60.0, {'m31-mosaic': {'flipped': True, 'side': 'east', 'verified':
        False}}), (90.0, {'m31-mosaic': {'flipped': True, 'side': 'east',
        'verified': False}}), (120.0, {'m31-mosaic': {'flipped': True, 'side':
        'east', 'verified': False}}), (150.0, {'m31-mosaic': {'flipped': True,
        'side': 'east', 'verified': False}}), (180.0, {'m31-mosaic':
        {'flipped': True, 'side': 'east', 'verified': False}}), (210.0,
        {'m31-mosaic': {'flipped': True, 'side': 'east', 'verified': False}})]
        assert 8 == 1
    """
    plan = grid_plan(rows=1, cols=2, meridian_flip=True,
                     panel_kw={"filters": ("L",), "count": 4, "ha_h": 1.0,
                               "ha_step_h": 0.03})
    night, saves = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-4:]
    hops = _hops(night)
    assert len(hops) == 8, hops
    assert night.said("is past the meridian, so the mosaic starts on the side "
                      "it takes after the meridian"), night.lines[:6]
    assert not night.said("the mosaic changed pier side"), night.lines
    assert len(saves) == 1, (
        f"{len(saves)} saves over {len(hops)} hops: {saves}")
    t, record = saves[0]
    assert t == 0.0, saves
    assert record[next(iter(record))]["flipped"] is True, record


async def test_a_pier_change_is_saved_as_it_happens(group_hub, monkeypatch):
    """A straddle, twelve L frames a panel: 1-1 at 1.5 h east of the meridian
    completes on the pre-flip side; 1-2 at 0.3 h east shoots until it runs
    out of room before its flip point, waits for its crossing, and its hop
    there is the group's one pier change. Two saves, the first side read and
    the change, verified, and none for the seven hops after it.

    MUTANT "delete the only-when-moved early return": RED, a save on each of
    the seven hops after the change (observed):
        AssertionError: [(0.0, {'m31-mosaic': {'flipped': False, 'side':
        'west', 'verified': False}}), (1094.686, {'m31-mosaic': {'flipped':
        T...de': 'east', 'verified': True}}), (1214.686, {'m31-mosaic':
        {'flipped': True, 'side': 'east', 'verified': True}}), ...]
        assert [(0.0, False,...e, True), ...] == [(0.0, False,..., True, True)]
          Left contains 7 more items, first extra item: (1124.686, True, True)
    """
    plan = grid_plan(rows=1, cols=2, meridian_flip=True,
                     panel_kw={"filters": ("L",), "count": 12, "ha_h": -1.5,
                               "ha_step_h": 1.2})
    night, saves = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-4:]
    assert night.said("the mosaic changed pier side at 1-2"), night.lines[-8:]
    change_t = next(t for t, _lv, m in night.lines
                    if "the mosaic changed pier side" in m)
    after = [who for t, who in night.gotos if t >= change_t]
    assert len(after) == 8, after
    moves = [(t, rec[next(iter(rec))]["flipped"],
              rec[next(iter(rec))]["verified"]) for t, rec in saves]
    assert moves == [(0.0, False, False),
                     (night.rel(change_t), True, True)], saves


async def test_a_night_before_the_meridian_saves_once_either_way(
        group_hub, monkeypatch):
    """CONTROL: both panels two hours east of the meridian, four L frames
    each: eight hops, all before the meridian. Before the meridian only the
    first side read asks `_persist_group_pier` at all, so this night saves the
    record once whether or not the guard is there (GREEN under the mutant,
    observed): the count grades the saves the rule decides, not the calls a
    harness happens to make."""
    plan = grid_plan(rows=1, cols=2, meridian_flip=True,
                     panel_kw={"filters": ("L",), "count": 4, "ha_h": -2.0,
                               "ha_step_h": 0.03})
    night, saves = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-4:]
    assert len(_hops(night)) == 8, _hops(night)
    assert len(saves) == 1, saves
    assert saves[0][0] == 0.0, saves
    assert saves[0][1][next(iter(saves[0][1]))]["flipped"] is False, saves
