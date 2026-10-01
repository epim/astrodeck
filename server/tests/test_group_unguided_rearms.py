"""A guider that starts again takes a mosaic out of unguided mode (owner
ruling 5; spec 5.6 step 7).

Built in #189 S2. The S2 review found this rule had no test that could fail,
and filed it as #318.

When every attempted panel of a pass fails to start guiding, the guider is
blamed, not the panels, and the rig's ``guiding_action`` decides for the
group; under ``warn`` the group carries on unguided (`_close_group_pass` adds
it to ``_group_unguided``), and while it is unguided a failed start is
escalated as a single target's is, which under ``warn`` means "continuing
unguided". `_setup_target` takes the group back out of that mode on the
first start that works: "a guider that starts is not dead, so a group that
went unguided defers failures again". Without that, one dead spell early in
the night leaves every later failed start on that mosaic shot unguided, for
the rest of the night, although the guider came back.

Nothing held it. The S2 review deleted the ``_group_unguided.discard(...)``
in `_setup_target`'s successful-start branch, in a private scratch copy of
server/ (#254), and every S2 test still passed (396 passed).

THE NIGHT, on the clocked simulator (tests/_group_harness.py), a 2x2 of L
and R, count 3, one pass a visit, ``require_guiding`` with ``warn``:

* pass 1: every panel's start fails (attempt 1 of each), so the pass is the
  rig's fault and ``warn`` sends the group on unguided;
* pass 2: every start works (attempt 2), which ends the unguided mode;
* pass 3: 1-2's start fails once more (its attempt 3), the others work.

1-2's third visit must be a DEFERRAL, retried on the next pass, and never a
visit shot unguided.

MUTANT "unguided flag not cleared by a start" (``self._group_unguided.
discard(member.id)`` in `_setup_target` replaced by ``pass``): RED (observed,
``-n0``):
    E       AssertionError: 1-2's third visit, whose start failed after the
            guider came back, was shot unguided: [(), ('L', 'R'), ('L', 'R'),
            ('L', 'R')]
    E       assert ('L', 'R') == ()
    E         Left contains 2 more items, first extra item: 'L'

The control is the case itself up to pass 2: the same failures in pass 1
send the group unguided (asserted as the premise), and a start that works
is what takes it back.
"""
from __future__ import annotations

from _group_harness import (GROUP_NAME, Night, grid_plan, group_hub,  # noqa: F401
                            group_store)
from astrodeck.config import EscalationConfig
from astrodeck.sequence.session import session_store


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


async def test_a_start_that_works_ends_the_unguided_mode(group_hub, group_store,
                                                        monkeypatch):
    group_store.set_escalation(EscalationConfig(require_guiding=True,
                                                guiding_action="warn"))

    def guide(who, n):
        if n == 1:
            return False                       # pass 1: the guider is dead
        return not (who == _name("1-2") and n == 3)

    night = Night(group_hub, monkeypatch, guide=guide)
    try:
        done = await night.run(grid_plan(guide=True))
    finally:
        await night.close()
    assert done, night.trace[-3:]
    visits = night.visits()
    # Premise: pass 1 was the rig's fault and the group went on unguided.
    assert [f for _t, f in visits[:4]] == [()] * 4, visits[:4]
    assert night.said("continuing the panels unguided"), night.lines[:12]
    one_two = [f for t, f in visits if t == _name("1-2")]
    assert one_two[2] == (), (
        f"1-2's third visit, whose start failed after the guider came back, "
        f"was shot unguided: {one_two}")
    assert len(night.said("guiding did not start on 1-2: no guide star found "
                          "(attempt 3); retried on the next pass")) == 1, (
        night.said("1-2"))
    assert not night.said("guiding failed to start"), night.said(
        "guiding failed to start")
    stored = session_store.load(night.session_id)
    assert stored.owed() == 0 and stored.set_aside == [], (
        stored.owed(), stored.set_aside)
