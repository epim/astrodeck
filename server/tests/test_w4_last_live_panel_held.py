# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#591 (backlog ruling D-03, owner-approved 2026-09-30): a mosaic's last
live panel is held, not charged.

WP-21 (wave 3) built D-03's held-pass streak and its two-stage escalation
(the alert at ``HELD_PASS_ALERT_AT`` held passes, the set-aside at
``HELD_PASS_SET_ASIDE_AT``) but deliberately left `centring_pass_verdict`'s
floor of two attempts alone (#563, #576). A pass that visits only one
panel -- because it is the grid's last live member, the others already
complete or set aside tonight -- could never meet that floor, so its
single miss was charged to the panel by the ordinary three-strike rule
instead of held under the escalation D-03 built to cap exactly this cost
(#591).

THIS WP (WP-33). ``GroupRun.close_pass`` now reads its own live member
count and hands it to ``centring_pass_verdict`` as ``live``: a pass in
which every LIVE member was tried and every one missed is the sky's, down
to a group of one. A caller with no ``live`` to give -- every caller
outside ``GroupRun`` itself, this file's sibling
``test_h4_group_rules_centring.py`` and the spec-claims pin included --
keeps asking the ORIGINAL question, the floor of two attempts, unwidened:
D-03 only widens what the group driver itself asks at its own boundary.
"""
from __future__ import annotations

from astrodeck.sequence.group_rules import (
    CENTRING,
    HELD_PASS_ALERT_AT,
    GroupRun,
    PanelDeferred,
    centring_pass_verdict,
)


def _run(n: int = 2) -> GroupRun:
    labels = ["1-1", "1-2", "1-3"][:n]
    return GroupRun({f"p{i}": lab for i, lab in enumerate(labels)},
                    max_failed_visits=3)


def _miss() -> PanelDeferred:
    return PanelDeferred("centring failed", kind=CENTRING,
                         last_error="plate solve failed — used raw GoTo")


# --------------------------------------------------------- GroupRun, live


def test_the_last_live_panel_s_lone_miss_is_held_not_charged():
    """p0 completes, leaving p1 the group's only live member. p1 then
    misses centring alone, ``HELD_PASS_ALERT_AT`` passes running: each must
    hold the group (``centring_hold``), strike nothing, and never set p1
    aside -- D-03's escalation (the alert at ``HELD_PASS_ALERT_AT`` held
    passes) is what bounds this cost now, not the three-strike panel rule
    that used to set a lone-attempt panel aside before any alert could fire.

    RED under mutant "the floor stays two attempts" (``live=len(self.live())``
    taken out of ``GroupRun._decide_pass_end``'s call to
    ``centring_pass_verdict``), observed:

        AssertionError: (1, 'defer_wait')
        assert 'defer_wait' == 'centring_hold'
    """
    run = _run(2)
    run.visit_outcome("p0", complete=True, exposures=2, accepted=2)
    run.close_pass()
    run.start_pass()
    assert not run.is_live("p0") and run.live() == ["p1"], "premise"
    for n in range(1, HELD_PASS_ALERT_AT + 1):
        run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                          deferred=_miss())
        end = run.close_pass()
        assert end.boundary == "centring_hold", (n, end.boundary)
        assert run.failed["p1"] == 0, "held, not charged to the panel"
        assert end.held_streak == n
        assert run.is_live("p1"), "never struck out by this miss"
        run.start_pass()
    assert run.set_aside == {}


def test_a_panel_struck_out_leaving_one_live_peer_is_also_held():
    """CONTROL on the group side: with 3 members, p0 and p2 struck out over
    three passes (each beside p1 centring), p1 is left the group's only
    live member. p1's own next miss, alone, now holds instead of charging
    toward p1's own three-strike count, exactly as the two-member case
    above."""
    run = _run(3)
    for _ in range(3):
        run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                          deferred=_miss())
        run.visit_outcome("p2", complete=False, exposures=0, accepted=0,
                          deferred=_miss())
        run.visit_outcome("p1", complete=False, exposures=2, accepted=2)
        run.close_pass()
        run.start_pass()
    assert run.live() == ["p1"], "premise: p0 and p2 are struck out"
    run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                      deferred=_miss())
    end = run.close_pass()
    assert end.boundary == "centring_hold"
    assert run.failed["p1"] == 0


# --------------------------------------------------- the pure verdict table


def test_the_verdict_needs_every_live_member_tried():
    """``centring_pass_verdict`` with ``live`` given: one of one live is the
    sky's even alone; one of two, a live peer this pass never reached, is
    still the panel's; and a caller with no ``live`` at all -- the original
    signature -- asks only the pre-D-03 question.

    RED under mutant "the live floor drops to zero" (``attempted >= 1``
    deleted from the ``live is not None`` branch, so ``live=0`` would also
    answer "sky" for a pass with nothing to judge), observed:

        AssertionError: assert 'sky' == 'panel'
    """
    assert centring_pass_verdict(1, 1, live=1) == "sky"
    assert centring_pass_verdict(1, 1, live=2) == "panel"
    assert centring_pass_verdict(0, 0, live=0) == "panel"
    assert centring_pass_verdict(1, 1) == "panel", (
        "a caller with no live count asks only the pre-D-03 question")


def test_a_panel_completed_the_same_pass_is_not_a_caller_bug():
    """A panel that shoots its last frame and completes in the SAME pass
    still counted toward ``attempted`` before ``live`` excludes it
    (``GroupRun.visit_outcome`` records before checking ``complete``): the
    function sees ``attempted`` exceed ``live`` and must still answer, not
    raise, since this is an ordinary pass, not a caller error."""
    assert centring_pass_verdict(2, 1, live=1) == "panel"
