# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 6.2 (#180 part B, owner ruling D-E): ResumeArm re-centres on the panel
the engine will visit first.

After a restart the engine orders a group's panels with the panels it is
skipping last (`OrderSnapshot.skipped`). ResumeArm's ladder
(`resume_arm.recentre_candidates`, `_group_order`) builds its own snapshot
from the ledger, and until it read the session's standing skips it put a
skipped panel wherever its fraction banked and the snake put it, so the mount
was pointed at a panel the run then ordered last: one wasted slew. The two
callers of the order now agree.

Nothing here reads a clock or a site: the night is a fixed key.
"""
from __future__ import annotations

from _group_harness import GROUP_NAME, T0, grid_plan
from astrodeck.events import night_key
from astrodeck.sequence.resume_arm import recentre_candidates
from astrodeck.sequence.session import Session, SessionFrame


def _session() -> Session:
    plan = grid_plan()
    return Session(name=plan.name, created_ts=T0 - 3600.0, status="dormant",
                   plan=plan)


def _order(session: Session) -> list[str]:
    return [t.name[len(GROUP_NAME) + 1:]
            for t in recentre_candidates(session, night_key(T0))]


def test_with_no_skip_the_ladder_is_the_snake():
    """The premise: nothing banked, nothing skipped, 2-2 is third."""
    assert _order(_session()) == ["1-1", "1-2", "2-2", "2-1"]


def test_a_standing_skip_puts_the_panel_last():
    """2-2 was skipped earlier tonight and has banked nothing since: the
    ladder puts it last, as the engine's first round does.

    MUTANT "the ladder ignores skips" (``skipped`` not passed to the
    snapshot in `_group_order`). Observed:
        AssertionError: assert ['1-1', '1-2', '2-2', '2-1'] == ['1-1',
        '1-2', '2-1', '2-2']
    """
    s = _session()
    s.note_panel_skipped("p11", night=night_key(T0), kind="centring",
                         visits=3, ts=T0 - 60.0)
    assert _order(s) == ["1-1", "1-2", "2-1", "2-2"]


def test_a_skip_a_frame_has_since_ended_does_not_demote():
    """2-2 banked a frame after its skip, so it left the rotation and is
    ordered by its fraction like any panel: the least complete, first, here.

    MUTANT "a skip never ends" (`standing_skip` ignores frames). Observed:
        AssertionError: assert ['1-1', '1-2', '2-1', '2-2'] == ['2-2', '1-1',
        '1-2', '2-1']
    """
    s = _session()
    s.note_panel_skipped("p11", night=night_key(T0), kind="centring",
                         visits=3, ts=T0 - 60.0)
    for tid in ("p00", "p01", "p10"):
        for k in range(2):
            s.frames.append(SessionFrame(
                ts=T0 - 600.0 + k, night="n0", target_id=tid,
                step_id=f"{tid}-L", auto_accepted=True))
    s.frames.append(SessionFrame(ts=T0 - 30.0, night="n0", target_id="p11",
                                 step_id="p11-L", auto_accepted=True))
    assert _order(s) == ["2-2", "1-1", "1-2", "2-1"]


def test_an_earlier_nights_skip_does_not_demote():
    """The engine restores only tonight's skip (`_restore_panel_skips`), so
    the ladder reads the same night only.

    MUTANT "night not compared". Observed:
        AssertionError: assert ['1-1', '1-2', '2-1', '2-2'] == ['1-1', '1-2',
        '2-2', '2-1']
    """
    s = _session()
    s.note_panel_skipped("p11", night=night_key(T0 - 86400.0),
                         kind="centring", visits=3, ts=T0 - 86400.0)
    assert _order(s) == ["1-1", "1-2", "2-2", "2-1"]
