"""Auto-resume leaves out a group its member's gate holds (#330; spec 1.6,
5.9).

THE DEFECT. `recentre_candidates` placed each ``plan.groups`` entry's live
panels through its group branch and went on to the next target before the
``after_group`` test, which it asked of targets in no group only. So a
downstream mosaic whose panels wait for an upstream one ("Wait for the
mosaic", written on every panel by the compile) was offered for the
re-centre while the upstream mosaic still owed frames, a slew to a mosaic the
run then holds or skips.

THE RULE NOW, the run's (`SequenceEngine._group_gate`): a member's gate holds
its WHOLE group. While a group any member waits for owes frames (live, or set
aside tonight), none of the group's panels is a candidate; once that group is
complete they are. The same rule the ladder already applied to a follower in
no group, and the same "owes frames" reading of the group waited for.

`recentre_candidates` is pure, so the cases call it on a session and read its
answer: no site and no clock, which applies no gating (#283), so every answer
is about the gates alone. The run's side of the rule is
test_group_member_after_group.py's.

MUTANT "members placed before the after_group test": the new test in the
group branch moved below ``out.extend(ordered)``, so the group's panels are
placed whatever it waits for. Applied in a private scratch copy of server/
(scratchpad s4-engb-mut), never in the shared tree (#254); each case says
what it observed under it, verbatim. Run again on the finished S4 code in
scratchpad s4-engb-resume-mut, it failed as recorded, and the two cases with
no mutant noted passed under it.
"""
from __future__ import annotations

from astrodeck.events import night_key
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       TargetGroup, plan_identity_errors)
from astrodeck.sequence.resume_arm import (nothing_to_shoot_tonight,
                                           recentre_candidates)
from astrodeck.sequence.session import Session, SessionFrame

UP, DOWN = "g-m31", "g-m33"
#: Any instant: nothing here reads the sky.
T = 1_700_000_000.0
NIGHT = night_key(T)


def _target(tid: str, name: str, *, group: str, row: int, col: int,
            after: str | None = None) -> Target:
    return Target(id=tid, name=name, ra_hours=0.7 + 0.06 * col,
                  dec_deg=41.0 - 0.4 * row, center=False,
                  autofocus_first=False, mosaic_group=group, panel_row=row,
                  panel_col=col, after_group=after,
                  steps=[ExposureStep(id=f"s-{tid}", filter="L",
                                      exposure_s=0.05, count=2)])


def _plan(gates=(UP, UP)) -> SequencePlan:
    """An upstream 2x2 M31, then a downstream 1x2 M33 whose two panels wait
    for the groups in ``gates`` (None: no gate on that panel)."""
    up = [_target(f"p-{r}{c}", f"M31 {r + 1}-{c + 1}", group=UP, row=r, col=c)
          for r in range(2) for c in range(2)]
    down = [_target(f"q-0{c}", f"M33 1-{c + 1}", group=DOWN, row=0, col=c,
                    after=g) for c, g in enumerate(gates)]
    plan = SequencePlan(
        name="two mosaics", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False, targets=[*up, *down],
        groups=[TargetGroup(id=UP, name="M31", geometry={"rows": 2, "cols": 2}),
                TargetGroup(id=DOWN, name="M33",
                            geometry={"rows": 1, "cols": 2})])
    assert plan_identity_errors(plan) == [], plan_identity_errors(plan)
    return plan


def _session(plan: SequencePlan, *, banked=(), aside=()) -> Session:
    """A dormant session of ``plan`` with both frames of every target in
    ``banked`` in the ledger and every target in ``aside`` set aside for
    tonight."""
    frames = [SessionFrame(ts=T - 3600.0, night="n1", target_id=t.id,
                           step_id=t.steps[0].id)
              for t in plan.targets if t.id in banked for _ in range(2)]
    s = Session(id="s-gates", name="gates", status="dormant", plan=plan,
                frames=frames, auto_resume=True)
    for tid in aside:
        s.note_set_aside(tid, "a full pass took no exposures", night=NIGHT)
    return s


def _ids(session: Session) -> list[str]:
    return [t.id for t in recentre_candidates(session, NIGHT)]


UP_IDS = ["p-00", "p-01", "p-10", "p-11"]


def test_without_a_gate_both_mosaics_are_candidates():
    """CONTROL: with no gate on M33, both mosaics' panels are candidates,
    M31's first as the run walks them. So the held cases below are graded on
    the gate, not on anything else about M33."""
    ids = _ids(_session(_plan(gates=(None, None))))
    assert set(ids) == {*UP_IDS, "q-00", "q-01"}, ids
    assert ids.index("q-00") > max(ids.index(p) for p in UP_IDS), ids


def test_a_downstream_mosaic_is_no_candidate_while_the_upstream_owes():
    """M31 owes every frame: the run holds M33 behind it, so the re-centre
    offers M31's panels and none of M33's.

    MUTANT "members placed before the after_group test": RED (observed):
        AssertionError: ['p-00', 'p-01', 'p-11', 'p-10', 'q-00', 'q-01']
        assert ['p-00', 'p-0...q-00', 'q-01'] == ['p-00', 'p-0...p-11', 'p-10']
          Left contains 2 more items, first extra item: 'q-00'
    """
    ids = _ids(_session(_plan()))
    assert ids == ["p-00", "p-01", "p-11", "p-10"], ids


def test_one_gated_member_holds_its_whole_group():
    """Only M33 1-1 waits for M31. A member's gate holds its whole group, as
    the run reads it, so M33 1-2 is no candidate either.

    MUTANT "members placed before the after_group test": RED (observed):
        AssertionError: ['p-00', 'p-01', 'p-11', 'p-10', 'q-00', 'q-01']
        assert not ({'q-00', 'q-01'} & {'p-00', 'p-01', 'p-10', 'p-11',
        'q-00', 'q-01'})
         +  where {'p-00', 'p-01', 'p-10', 'p-11', 'q-00', 'q-01'} =
        set(['p-00', 'p-01', 'p-11', 'p-10', 'q-00', 'q-01'])
    """
    ids = _ids(_session(_plan(gates=(UP, None))))
    assert not {"q-00", "q-01"} & set(ids), ids
    assert set(ids) == set(UP_IDS), ids


def test_a_gate_on_the_last_member_holds_its_whole_group():
    """The case above with the gate moved to M33 1-2, the LAST member;
    1-1, the first, carries none. The group's gates are read from every
    member, so M33 is no candidate either way. The case above alone cannot
    tell every member from the first one (#396).

    MUTANT "the group's gates read from its first member only" (``waits_for``
    built from ``members[:1]``): RED (observed):
        AssertionError: ['p-00', 'p-01', 'p-11', 'p-10', 'q-00', 'q-01']
        assert not ({'q-00', 'q-01'} & {'p-00', 'p-01', 'p-10', 'p-11',
        'q-00', 'q-01'})
         +  where {'p-00', 'p-01', 'p-10', 'p-11', 'q-00', 'q-01'} =
        set(['p-00', 'p-01', 'p-11', 'p-10', 'q-00', 'q-01'])
    Every other case here passed under it. Applied by a second verifier in
    its own scratch copy (scratchpad s4-engb-verify-r2-mut), where this case
    was added for it; "members placed before the after_group test" is RED
    here too, with the same lines.
    """
    ids = _ids(_session(_plan(gates=(None, UP))))
    assert not {"q-00", "q-01"} & set(ids), ids
    assert set(ids) == set(UP_IDS), ids


def test_a_downstream_mosaic_is_skipped_with_an_upstream_set_aside_tonight():
    """Every M31 panel is set aside tonight: M31 still owes its frames, so
    the run skips M33 for the night, not done, and the ladder has nothing to
    shoot tonight at all: `nothing_to_shoot_tonight` refuses before anything
    moves, as the run would end with nothing shot.

    MUTANT "members placed before the after_group test": RED (observed):
        AssertionError: ['q-00', 'q-01']
        assert ['q-00', 'q-01'] == []
          Left contains 2 more items, first extra item: 'q-00'
    """
    s = _session(_plan(), aside=UP_IDS)
    ids = _ids(s)
    assert ids == [], ids
    assert nothing_to_shoot_tonight(s, recentre_candidates(s, NIGHT))


def test_a_downstream_mosaic_is_a_candidate_once_the_upstream_is_complete():
    """Every M31 frame is banked: M31 owes nothing, so M33 is let go, in the
    run as here, and its panels are the candidates."""
    ids = _ids(_session(_plan(), banked=UP_IDS))
    assert ids == ["q-00", "q-01"], ids
