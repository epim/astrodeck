"""Every pass is re-ordered at its boundary, so a panel that fell behind goes
first (#189 S2, #318; spec 5.1 item 3, 5.2).

The group's order is decided from one snapshot per pass (`_order_snapshot`),
and `_close_group_pass` re-sorts the group's slice of ``remaining`` before
the next pass begins. Under ``least_complete`` that is what lets a panel
that lost a visit, to a deferral or a cloud, catch up first on the next
pass instead of waiting its turn in last pass's order.

Nothing held the re-sort. The S2 review deleted the ``self._resort_group(
group, remaining)`` call at the end of `_close_group_pass`, in a private
scratch copy of server/ (#254), and every S2 test still passed (396 passed):
the cases that re-sort at all do it from the ledger at the run's start, and
with even progress last pass's order, which the requeue keeps, is already the
least-complete order.

THE NIGHT, on the clocked simulator (tests/_group_harness.py): a 2x2 of L
and R, count 3, one pass a visit. 2-2's first hop does not centre, so it is
deferred on pass 1 and ends the pass with nothing banked while the other
three hold a round each. Pass 2 therefore starts on 2-2, the least complete,
then the others by their last visit, oldest first; pass 3 the same, since
2-2 is still a round behind; and 2-2 alone finishes on pass 4.

MUTANT "no re-sort at the pass boundary" (that call deleted): RED
(observed, ``-n0``):
    E       AssertionError: ['1-1', '1-2', '2-2', '2-1', '1-1', '1-2', ...]
    E       assert ['1-1', '1-2'...', '1-2', ...] == ['1-1', '1-2'...', '1-1',
            ...]
    E         At index 4 diff: '1-1' != '2-2'

The control is pass 1 itself: with nothing banked and nothing visited, the
first pass is the snake (1-1, 1-2, 2-2, 2-1), which the case asserts.
"""
from __future__ import annotations

from _group_harness import (GROUP_NAME, Night, grid_plan, group_hub,  # noqa: F401
                            group_store)
from astrodeck.sequence.session import session_store


def _label(target: str) -> str:
    prefix = GROUP_NAME + " "
    return target[len(prefix):] if target.startswith(prefix) else target


async def test_a_panel_deferred_on_one_pass_goes_first_on_the_next(
        group_hub, monkeypatch):
    def goto(who, n, result):
        if who == f"{GROUP_NAME} 2-2" and n == 1:
            return {**result, "centered": False, "error_arcmin": None}
        return result

    night = Night(group_hub, monkeypatch, goto=goto)
    try:
        done = await night.run(grid_plan())
    finally:
        await night.close()
    assert done, night.trace[-3:]
    visits = night.visits()
    labels = [_label(t) for t, _f in visits]
    assert visits[2] == (f"{GROUP_NAME} 2-2", ()), (
        f"premise: 2-2 was deferred on pass 1: {visits[:4]}")
    assert labels == ["1-1", "1-2", "2-2", "2-1",
                      "2-2", "1-1", "1-2", "2-1",
                      "2-2", "1-1", "1-2", "2-1",
                      "2-2"], labels
    assert session_store.load(night.session_id).owed() == 0
