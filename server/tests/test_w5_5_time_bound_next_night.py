# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 5.5 (#1207): a target that reached its maximum time is not taken up
again tonight, and is taken up on a later night.

The wizards' and the editor's copy for ``Target.max_time_min`` says the frames
the target still owes "wait for a later night" (``MAX_TIME_HELP`` in
nodeDefs.ts). The bound sets the target aside for tonight with the same
``Session.set_aside`` record every other "tried tonight, not again" is
(``_enforce_time_bound``, kind ``time_bound``), so a restart or an auto-resume
the same night finds it set aside and shoots nothing, and the next night's run
reads none of tonight's records and shoots the rest. #1094's own tests
(test_w3_target_time_bound.py) hold the record; these two start the SAME
session again on a fresh engine, an hour later and a day later, as
test_group_set_aside_persisted.py does for the other kinds.

The claims table (test_set_aside_promises.py) cites both by name.

MUTANT "the bound is not a set-aside record" (`_enforce_time_bound` no longer
calls `_persist_set_aside`): RED, observed -
    test_a_time_bounded_target_is_not_retried_by_a_restart_tonight:
    AssertionError: a restart the same night shot a target that had reached
    its bound
"""
from __future__ import annotations

import pytest

from _group_harness import T0, Night, group_hub, group_store, single  # noqa: F401
from astrodeck.events import night_key
from astrodeck.sequence.session import session_store
from test_trigger_frame_banked import _plan

LATER_TONIGHT = T0 + 3600.0
NEXT_NIGHT = T0 + 86400.0

COUNT = 300
BOUND_MIN = 10


@pytest.fixture(autouse=True)
def _nights_are_what_this_file_says():
    """Premise, on the machine's own time zone: an hour after ``T0`` is the
    same observing night, a day after is the next."""
    assert night_key(T0) == night_key(LATER_TONIGHT) != night_key(NEXT_NIGHT)


async def _night(hub, monkeypatch, *, t0, session=None) -> Night:
    night = Night(hub, monkeypatch, t0=t0)
    try:
        night.done = await night.run(
            _plan(single("A", count=COUNT, max_time_min=BOUND_MIN)),
            **({"session": session} if session is not None else {}),
            wall_s=120.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


async def test_a_time_bounded_target_is_not_retried_by_a_restart_tonight(
        group_hub, group_store, monkeypatch):
    """A restart an hour later, on the same session: the target reached its
    bound and still owes frames, and it shoots none of them tonight."""
    first = await _night(group_hub, monkeypatch, t0=T0)
    assert first.done and first.stored.status == "dormant"
    assert first.stored.owed() > 0, "premise: the bound left frames owed"
    assert first.shots(), "premise: the first night shot until the bound"
    again = await _night(group_hub, monkeypatch, t0=LATER_TONIGHT,
                         session=first.stored)
    assert again.shots() == [], (
        "a restart the same night shot a target that had reached its bound")


async def test_a_time_bounded_target_is_taken_up_again_the_next_night(
        group_hub, group_store, monkeypatch):
    """The next night, on the same session: the record is last night's, so the
    target shoots its remaining frames."""
    first = await _night(group_hub, monkeypatch, t0=T0)
    shot_first = len(first.shots())
    assert shot_first, "premise: the first night shot until the bound"
    nxt = await _night(group_hub, monkeypatch, t0=NEXT_NIGHT,
                       session=first.stored)
    assert nxt.shots(), "the next night did not take up the frames still owed"
    assert [r["night"] for r in nxt.stored.set_aside
            if r.get("kind") == "time_bound"] == [
        night_key(T0), night_key(NEXT_NIGHT)], nxt.stored.set_aside
