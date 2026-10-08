# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A panel whose set-aside expires ALONE begins a pass of its own, whatever
the closed pass still held (#729; backlog wave 16, WP-141).

``GroupRun.expire_set_aside`` has an ``alone`` branch: when no other member is
live the boundary that set the last of them aside answered ``none_live`` and
began no pass, so the counts standing are that closed pass's, and the panel
comes back into a pass of its own (``start_pass``). ``start_pass`` refuses
while any of the three held lists (guide-start, centring, transient) is
non-empty, because that means a pass was never closed. The branch cleared the
first two and not ``_held_transient``, which the held-pass streak added
(#563, #576), so a panel that expired alone while a transient deferral of
the pass was still held died with::

    RuntimeError: close_pass() first: this pass still holds guide-start,
    centring or transient deferrals that only the pass boundary can count

``retry_set_aside`` (#600) cleared all three from the start and said why
(its comment: "the transient list too, which ``expire_set_aside`` leaves");
this is the same class of defect, the second copy of one rule that did not
move with the first (the held-pass counter's own class, #563).

REACHABLE, not only latent: a member that hit a transient solve fault (held,
never counted, #532) and was then taken up again in the same pass and
COMPLETED leaves its deferral held while it is no longer live. The engine
asks for an expiry mid-pass (``_expire_or_wait``, from the selection), so
the lone panel's expiry can land exactly then. Driven here through
``visit_outcome``, the public door, for each of the three kinds of held
deferral, so the class is graded and not only the one that raised.

Each case was run against the mutants below from a byte backup of
``astrodeck/sequence/group_rules.py`` inside this worktree, restored and
sha256-compared after each, the mutant's text grepped absent (#254). The
observed failure is recorded verbatim.

THE FIX IS ONE HELPER, ``GroupRun._begin_pass_alone``, which both the expiry
and the retry call: the two held-list clears were a pair of copies of one
rule, which is how only one of them came to miss the third list. A mutant in
the helper therefore also reddens WP-104's retry case
(test_w15_retry_set_aside_records.py), which is the point of one place.

MUTANT "transient list left held" (the ``self._held_transient = []`` line of
``_begin_pass_alone`` replaced by ``pass``, the state before this fix for the
expiry). RED, observed (the transient row only; the guide-start and
centring rows still pass, which is why the class is parametrised):

    test_a_panel_that_expires_alone_ignores_whatever_the_closed_pass_held[solve_transient]:
    RuntimeError: close_pass() first: this pass still holds guide-start,
    centring or transient deferrals that only the pass boundary can count

MUTANT "held list cleared whatever the branch" (``self._held_transient = []``
added to ``expire_set_aside`` ahead of ``if alone:``, so every expiry empties
it). RED, observed, the CONTROL:

    test_an_expiry_beside_a_live_member_keeps_the_pass_it_is_in:
    AssertionError: the pass's deferrals were all transient, so the pass is
    held
    assert 0 == 1
     +  where 0 = PassEnd(boundary='defer_wait', set_aside=(), reason='pass 4
    took no exposures and deferred 2 visits; waiting 300 s before the next
    pass', counted=(), held_streak=0).held_streak
"""
from __future__ import annotations

import pytest

from astrodeck.sequence.group_rules import (
    CENTRING,
    GUIDE_START,
    SOLVE_TRANSIENT,
    GroupRun,
    PanelDeferred,
)


def _run(n: int) -> GroupRun:
    return GroupRun({f"p{i}": f"1-{i + 1}" for i in range(n)},
                    max_failed_visits=3)


def _deferral(kind: str) -> PanelDeferred:
    return PanelDeferred("the visit did not complete", kind=kind,
                         last_error="plate solve failed -- used raw GoTo")


def _set_aside_by_centring(run: GroupRun, panel: str, *, beside: str) -> None:
    """Three passes in which ``panel`` misses centring and ``beside`` shoots:
    the third strikes ``panel`` out with a CENTRING set-aside, the one kind
    that expires (``expire_set_aside`` refuses any other)."""
    for _ in range(3):
        run.visit_outcome(panel, complete=False, exposures=0, accepted=0,
                          deferred=_deferral(CENTRING))
        run.visit_outcome(beside, complete=False, exposures=2, accepted=2)
        run.close_pass()
        run.start_pass()
    assert run.set_aside_kind.get(panel) == CENTRING, "premise: set aside"


@pytest.mark.parametrize("kind", [GUIDE_START, CENTRING, SOLVE_TRANSIENT])
def test_a_panel_that_expires_alone_ignores_whatever_the_closed_pass_held(kind):
    """p0 is set aside by centring; p1, the only other member, defers once
    in the pass (the deferral is HELD until the pass closes, whichever of
    the three kinds it is) and is then taken up again in the same pass and
    completes. No member but p0 is live, and the closed-over pass still
    holds p1's deferral. p0's expiry arrives: it comes back into a pass of
    its own, and the deferral that belonged to a panel that is not live
    counts toward nothing (the boundary drops it the same way, ``is_live``).

    RED under mutant "transient list left held", for the transient row only:

        RuntimeError: close_pass() first: this pass still holds guide-start,
        centring or transient deferrals that only the pass boundary can count
    """
    run = _run(2)
    _set_aside_by_centring(run, "p0", beside="p1")
    run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                      deferred=_deferral(kind))
    run.visit_outcome("p1", complete=True, exposures=2, accepted=2)
    assert run.live() == [], "premise: p0 is set aside and p1 is complete"
    before = run.pass_no

    run.expire_set_aside("p0")

    assert run.pass_no == before + 1, "a pass of its own began"
    assert run.is_live("p0") and run.visited == set()
    # The fresh pass is p0's alone: its first visit is judged on its own
    # deferral and nothing the closed pass held leaks into it. A lone
    # centring miss reads as the sky's (D-03, #591): held streak 1, and no
    # strike against p0's own count.
    run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                      deferred=_deferral(CENTRING))
    end = run.close_pass()
    assert (end.boundary, end.held_streak) == ("centring_hold", 1)
    assert run.failed["p0"] == 0


def test_an_expiry_beside_a_live_member_keeps_the_pass_it_is_in():
    """The CONTROL: the clears belong to the ``alone`` branch only. With p2
    still live the pass is still in progress, so p0's expiry must not
    discard the transient deferrals p1 and p2 hold: the pass then closes
    held (every deferral of it was transient, #576) and the streak reads 1.

    RED under mutant "held list cleared whatever the branch":

        assert 0 == 1
         +  where 0 = PassEnd(boundary='defer_wait', ...).held_streak
    """
    run = _run(3)
    _set_aside_by_centring(run, "p0", beside="p1")
    for p in ("p1", "p2"):
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=_deferral(SOLVE_TRANSIENT))
    before = run.pass_no

    run.expire_set_aside("p0")

    assert run.pass_no == before, "no pass began: one was in progress"
    end = run.close_pass()
    assert end.held_streak == 1, (
        "the pass's deferrals were all transient, so the pass is held")
