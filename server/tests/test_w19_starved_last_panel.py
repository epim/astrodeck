# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A starved last mosaic panel is given up after ONE held pass, not six (#835;
backlog WP-168, wave 19).

THE COST. A mosaic whose other panels are done and whose one panel cannot
centre (no solvable field, no guide star) stays in D-03's held-pass rule for
``HELD_PASS_SET_ASIDE_AT`` = 6 passes of ``CENTRING_HOLD_RETRY_S`` = 600 s
each: about an hour of sky, every night, before the panel is set aside, to
do it all again the next night. The six passes are the owner's ruling for a
panel with no history. One that was set aside whole on the three nights
before tonight (``STARVED_AFTER_NIGHTS``, the count at which the Campaign
calls it starved, #180 part A) and has still not centred is the same hour
bought a fourth time.

THE FIX, in three places that this file grades one by one:

* ``Session.earlier_starved_nights``: on how many consecutive nights BEFORE
  tonight the session set the panel aside whole for a kind a held pass
  leaves (``HELD_PASS_KINDS``), shooting none of it; zero when the operator
  brought it back tonight (D-07), and zero when the session holds a frame of
  it from tonight, so that the read a restart makes agrees with the driver
  having dropped its note when the panel shot (a recovered panel is not
  starved again by a crash-resume). ``Session.set_aside_streak`` takes the
  ``before`` and ``kinds`` it needs, and answers what it always did without
  them.
* ``GroupRun.note_starved`` and ``_apply_held_pass_rule``: the mosaic's LAST
  LIVE PANEL, told it is starved, is set aside at its first held pass
  (``HELD_PASS_STARVED_SET_ASIDE_AT``) with the same "deferred" kind (so its
  streak and the Campaign's naming go on) and a reason that says how many
  nights it had been set aside. A panel that shot a frame tonight, or that the
  operator retried, is no longer starved for the rule. Several live panels
  held together keep the six: that is the night's, a fog bank.
* ``SequenceEngine._note_starved_panels``: read at every group start, a
  restart included, from the session's record of earlier nights.

A panel that CENTRES is never held, so the bound cannot touch it: pinned at
the engine level (the fourth night where the panel centres is shot in full).

Each case names the mutant it was shown RED under, with the failure observed.
Every mutant was applied from a byte backup of the production file, restored
with a byte copy and md5-compared, never through git (#254).
"""
from __future__ import annotations

import time

import pytest

from _group_harness import (GROUP_NAME, T0, Night, grid_plan,  # noqa: F401
                            group_hub, group_store)
from astrodeck.events import night_key
from astrodeck.sequence.group_rules import (
    CENTRING,
    CENTRING_HOLD_RETRY_S,
    GUIDE_START,
    HELD_PASS_KINDS,
    HELD_PASS_SET_ASIDE_AT,
    HELD_PASS_STARVED_SET_ASIDE_AT,
    GroupRun,
    PanelDeferred,
)
from astrodeck.sequence.session import (STARVED_AFTER_NIGHTS, STARVING_KINDS,
                                        Session, SessionFrame, session_store)

PANEL = "panel-a"
DAY = 86400.0


# ===================================================================== GroupRun

def _miss() -> PanelDeferred:
    return PanelDeferred("centring failed", kind=CENTRING, last_error="")


def _hold(run: GroupRun, panels):
    """One pass in which every panel in ``panels`` misses centring (so the
    pass is held, the sky's or the geometry's), then its close. Begins the
    next pass unless the group was set aside."""
    for p in panels:
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=_miss())
    end = run.close_pass()
    if end.boundary != "set_aside_all":
        run.start_pass()
    return end


def _two() -> GroupRun:
    return GroupRun({"p0": "1-1", "p1": "1-2"}, max_failed_visits=3)


def _second_panel_alone(run: GroupRun) -> None:
    """Pass 1: p0 shoots its last frames and completes, p1 misses centring
    beside it (counted as p1's own, the ordinary three-strike floor, because
    p0 centred). p1 is then the mosaic's only live panel."""
    run.visit_outcome("p0", complete=True, exposures=2, accepted=2)
    run.visit_outcome("p1", complete=False, exposures=0, accepted=0,
                      deferred=_miss())
    end = run.close_pass()
    assert end.boundary == "next_pass", end
    run.start_pass()
    assert run.live() == ["p1"], "premise: p1 is the last live panel"
    assert run.failed["p1"] == 1, "premise: its pass-1 miss was counted"


def test_a_starved_last_panel_is_set_aside_at_its_first_held_pass():
    """The issue's mosaic: the other panel is done, p1 cannot centre and was
    set aside on four nights running. Its first lone pass is held and ends
    the matter; the set-aside is p1's own ("deferred", so its streak goes on
    and the Campaign goes on naming it), and the reason says why it was not
    held for six.

    RED under mutant "the starved bound is the ordinary one" (``limit``
    in ``_apply_held_pass_rule`` made ``HELD_PASS_SET_ASIDE_AT``
    whatever ``starved`` says), observed:

        AssertionError: PassEnd(boundary='centring_hold', set_aside=(), ...
        held_streak=1)
        assert 'centring_hold' == 'set_aside_all'
    """
    run = _two()
    run.note_starved("p1", 4)
    _second_panel_alone(run)

    end = _hold(run, ["p1"])

    assert end.boundary == "set_aside_all", end
    assert run.set_aside_kind == {"p1": "deferred"}
    assert end.set_aside == (("p1", end.reason),)
    assert run.held_streak == 0
    assert end.reason == (
        "1-2 (the mosaic's last live panel) has been held for 1 pass in a row "
        "with no panel struck and no progress made, and was set aside on 4 "
        "nights running before tonight; set aside for tonight without "
        "waiting for 6 passes"), end.reason


def test_the_same_panel_with_no_history_keeps_its_six_passes():
    """CONTROL: nothing noted starved is D-03 exactly as it was. Five held
    passes hold the group with the panel live and uncounted, the sixth sets
    it aside, and the words are the ordinary ones.

    RED under mutant "every last panel is starved" (``starved`` in
    ``_apply_held_pass_rule`` made 1 for a lone live panel whatever
    ``starved_nights`` holds), observed:

        AssertionError: assert (1, 'set_aside_all') == (1, 'centring_hold')
          At index 1 diff: 'set_aside_all' != 'centring_hold'
    """
    run = _two()
    _second_panel_alone(run)
    for n in range(1, HELD_PASS_SET_ASIDE_AT):
        end = _hold(run, ["p1"])
        assert (n, end.boundary) == (n, "centring_hold")
        assert run.is_live("p1") and end.held_streak == n
    end = _hold(run, ["p1"])
    assert end.boundary == "set_aside_all"
    assert run.set_aside_kind == {"p1": "deferred"}
    assert end.reason == (
        "1-2 (the mosaic's last live panel) has been held for 6 passes in a "
        "row with no panel struck and no progress made; set aside for "
        "tonight"), end.reason


def test_several_live_panels_held_together_keep_the_six_whatever_their_past():
    """CONTROL: the bound is for the ONE panel left. Three live panels all
    starved on earlier nights, all missing together, are a fog bank or a
    tree line, the night's and not any panel's: five held passes hold them,
    the sixth sets the mosaic aside as "group".

    RED under mutant "any starved panel shortens the bound" (the
    ``len(live_before) == 1`` test dropped from ``starved``), observed:

        AssertionError: assert (1, 'set_aside_all') == (1, 'centring_hold')
          At index 1 diff: 'set_aside_all' != 'centring_hold'
    """
    run = GroupRun({"p0": "1-1", "p1": "1-2", "p2": "2-1"},
                   max_failed_visits=3)
    for p in run.members:
        run.note_starved(p, 5)
    for n in range(1, HELD_PASS_SET_ASIDE_AT):
        end = _hold(run, ["p0", "p1", "p2"])
        assert (n, end.boundary) == (n, "centring_hold")
    end = _hold(run, ["p0", "p1", "p2"])
    assert end.boundary == "set_aside_all"
    assert set(run.set_aside_kind.values()) == {"group"}


@pytest.mark.parametrize("accepted", [2, 0], ids=["accepted", "rejected"])
def test_a_panel_that_shot_a_frame_tonight_earns_the_six_back(accepted):
    """p1 was starved on four nights and centred tonight (frames taken), so
    what its history said it could not do it has done. When it is later the
    last panel, held again (a cloud bank this time), it gets the six passes
    any panel gets. A frame the grader rejected counts: the panel was
    centred and exposed, which is all the history said it could not do
    (``exposures``, not ``accepted``, the same word the restart's read of
    the session uses, ``Session.earlier_starved_nights``).

    RED under mutant "a shot frame leaves it starved" (the
    ``starved_nights.pop`` in ``_record`` replaced by ``pass``), observed:

        AssertionError: assert 'p1' not in {'p1': 4}

    and, in the ``rejected`` case only, under mutant "only an accepted frame
    clears it" (``if exposures > 0`` in ``_record`` made ``if accepted >
    0``), observed:

        AssertionError: assert 'p1' not in {'p1': 4}
    """
    run = _two()
    run.note_starved("p1", 4)
    run.visit_outcome("p1", complete=False, exposures=2, accepted=accepted)
    run.visit_outcome("p0", complete=True, exposures=2, accepted=2)
    run.close_pass()
    run.start_pass()
    assert run.live() == ["p1"], "premise: p1 is the last live panel"
    assert "p1" not in run.starved_nights

    for n in range(1, HELD_PASS_SET_ASIDE_AT):
        end = _hold(run, ["p1"])
        assert (n, end.boundary) == (n, "centring_hold")
    assert _hold(run, ["p1"]).boundary == "set_aside_all"


def test_a_frame_shot_beside_it_does_not_clear_it():
    """CONTROL on the line above: it is the PANEL'S frame that clears its
    history. p0 shooting in the pass where starved p1 misses leaves p1
    starved, and its first lone pass gives it up.

    RED under mutant "any frame clears every panel" (the ``pop`` in
    ``_record`` made ``self.starved_nights.clear()``), observed:

        AssertionError: assert {} == {'p1': 4}
          Right contains 1 more item: {'p1': 4}
    """
    run = _two()
    run.note_starved("p1", 4)
    _second_panel_alone(run)
    assert run.starved_nights == {"p1": 4}
    assert _hold(run, ["p1"]).boundary == "set_aside_all"


def test_an_operator_retry_earns_the_six_back():
    """D-07: the operator brings a set-aside panel back. The retry zeroes
    D-03's held-pass counter so the escalation is the operator's to
    restart; a starved panel retried and set aside again by its very next
    held pass would have its retry undone before it was tried. The retry
    drops the starved note too: five held passes hold it, the sixth ends it.

    RED under mutant "a retry leaves it starved" (the ``pop`` in
    ``retry_set_aside`` deleted), observed:

        AssertionError: assert (True and 'p1' not in {'p1': 4})
         +  where True = is_live('p1')
    """
    run = _two()
    run.note_starved("p1", 4)
    _second_panel_alone(run)
    assert _hold(run, ["p1"]).boundary == "set_aside_all"

    run.retry_set_aside("p1")

    assert run.is_live("p1") and "p1" not in run.starved_nights
    for n in range(1, HELD_PASS_SET_ASIDE_AT):
        end = _hold(run, ["p1"])
        assert (n, end.boundary) == (n, "centring_hold")
    assert _hold(run, ["p1"]).boundary == "set_aside_all"


def test_note_starved_names_a_member_and_zero_withdraws():
    run = _two()
    with pytest.raises(ValueError):
        run.note_starved("nobody", 3)
    with pytest.raises(ValueError):
        run.note_starved("p0", -1)
    run.note_starved("p0", 3)
    assert run.starved_nights == {"p0": 3}
    run.note_starved("p0", 0)
    assert run.starved_nights == {}


def test_the_constants_are_the_ones_the_rule_and_the_ledger_agree_on():
    """``HELD_PASS_KINDS`` are kinds the held-pass rule or the centring
    streak write, and every one is a starving kind the ledger counts; the
    starved bound is shorter than the ordinary one (it would be no bound at
    all otherwise) and is one held pass."""
    assert set(HELD_PASS_KINDS) == {CENTRING, "deferred"}
    assert set(HELD_PASS_KINDS) <= set(STARVING_KINDS)
    assert GUIDE_START not in HELD_PASS_KINDS
    assert HELD_PASS_STARVED_SET_ASIDE_AT == 1 < HELD_PASS_SET_ASIDE_AT


# ===================================================================== Session

def _rid(day: int) -> str:
    return f"mosaic-202607{day:02d}-210000"


def _key(day: int) -> str:
    return night_key(time.mktime((2026, 7, day, 21, 0, 0, 0, 0, -1)))


def _session(days, *, aside=(), frames=()) -> Session:
    """A session that ran on July ``days``, holding whole-panel set-aside
    records ``(day, kind)`` for PANEL and banked frames ``(day, accepted)``."""
    s = Session(id="s-w19", nights=[_rid(d) for d in days])
    for day, kind in aside:
        s.note_set_aside(PANEL, "reason", night=_key(day), kind=kind,
                         ts=1_790_000_000.0 + day)
    for day, accepted in frames:
        s.frames.append(SessionFrame(night=_rid(day), target_id=PANEL,
                                     step_id="s1", auto_accepted=accepted))
    return s


def _earlier(s: Session, day: int) -> int:
    return s.earlier_starved_nights(PANEL, night=_key(day),
                                    kinds=HELD_PASS_KINDS)


class TestEarlierStarvedNights:
    def test_tonight_is_not_one_of_the_earlier_nights(self):
        """Three nights before tonight and a record already made tonight (a
        restart after the panel was set aside): the answer is the three
        nights BEFORE it, and does not move when tonight's record is made.

        RED under mutant "tonight is walked too" (the ``before`` test in
        ``set_aside_streak`` made ``if False``, so the walk starts at
        tonight), observed:

            AssertionError: assert 0 == 3
             +  where 0 = _earlier(Session(...), 17)

        (tonight has no record when the first answer is asked, so the walk
        stops at it at once; the same mutant reads 4 once tonight's own
        record is made, the second assertion.)
        """
        aside = [(14, CENTRING), (15, "deferred"), (16, CENTRING)]
        s = _session([14, 15, 16, 17], aside=aside)
        assert _earlier(s, 17) == 3
        s.note_set_aside(PANEL, "reason", night=_key(17), kind="deferred")
        assert _earlier(s, 17) == 3

    def test_tonight_with_no_record_yet_is_still_the_three_before(self):
        """The reason ``before`` exists: a started run has tonight in
        ``nights`` and no record for it yet, and the three nights before it
        are what the driver asks for at its start."""
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        assert _earlier(s, 17) == 3

    def test_only_the_kinds_a_held_pass_leaves_count(self):
        """A panel that never STARTED GUIDING is starved by the Campaign's
        count, but says nothing about whether it will centre: its nights do
        not shorten a held pass.

        RED under mutant "any starving kind counts" (``kinds`` in
        ``set_aside_streak``'s record filter replaced by ``STARVING_KINDS``),
        observed:

            AssertionError: assert 3 == 0
             +  where 3 = _earlier(Session(...), 17)
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, GUIDE_START), (15, GUIDE_START),
                            (16, GUIDE_START)])
        assert s.set_aside_streak(PANEL, before=_key(17))[0] == 3, "premise"
        assert _earlier(s, 17) == 0

    def test_the_run_of_nights_stops_at_a_night_it_was_shot(self):
        """Set aside on 14 and 16, shot on 15: one night, not two."""
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)],
                     frames=[(15, True)])
        assert _earlier(s, 17) == 1

    def test_a_panel_the_operator_cleared_tonight_is_not_starved(self):
        """D-07: the operator retried it tonight (``note_set_aside_cleared``),
        and a restart after that must give it the full six.

        RED under mutant "a cleared panel is still starved" (the ``cleared``
        test in ``earlier_starved_nights`` made ``False and``), observed:

            AssertionError: assert 3 == 0
             +  where 3 = _earlier(Session(...), 17)
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        assert _earlier(s, 17) == 3, "premise"
        s.note_set_aside(PANEL, "reason", night=_key(17), kind="deferred")
        assert [r["target_id"] for r in
                s.note_set_aside_cleared([PANEL], night=_key(17))] == [PANEL]
        assert _earlier(s, 17) == 0

    @pytest.mark.parametrize("accepted", [True, False],
                             ids=["accepted", "rejected"])
    def test_a_panel_that_shot_tonight_is_not_starved_on_a_restart(
            self, accepted):
        """The restart's half of ``GroupRun._record`` dropping the starved
        note when the panel shoots: three starved nights, a frame of the
        panel banked tonight (a rejected one too, since ``_record`` goes by
        ``exposures``), and a SECOND run of the same night (a crash-resume,
        a CONTINUE, a /recover; a second report id on the same night, so
        ``observing_nights`` still reads four). The read the restarted run
        makes at its start answers 0, as the first run's driver did once the
        panel shot; without it the panel that recovered tonight is noted
        starved again and given one held pass for the rest of the night.

        RED under mutant "tonight's frames do not count" (the frame test in
        ``earlier_starved_nights`` made ``False and``), observed:

            AssertionError: assert 3 == 0
             +  where 3 = _earlier(Session(...), 17)
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)],
                     frames=[(17, accepted)])
        s.nights.append("mosaic-20260717-233000")
        assert len(s.observing_nights()) == 4 and len(s.nights) == 5, "premise"
        assert s.set_aside_streak(PANEL, before=_key(17),
                                  kinds=HELD_PASS_KINDS)[0] == 3, "premise"
        assert _earlier(s, 17) == 0

    def test_another_panels_frame_tonight_does_not_clear_it(self):
        """CONTROL: only the PANEL'S own frame tonight lifts it, as only its
        own shooting drops the note in the driver (a frame of another panel
        is the night working, not this panel centring).

        RED under mutant "any panel's frame clears it" (the ``target_id``
        test in the frame test of ``earlier_starved_nights`` made ``True or``),
        observed:

            AssertionError: assert 0 == 3
             +  where 0 = _earlier(Session(...), 17)
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        s.frames.append(SessionFrame(night=_rid(17), target_id="other",
                                     step_id="s1", auto_accepted=True))
        assert _earlier(s, 17) == 3

    def test_a_frame_from_an_earlier_night_is_not_tonights(self):
        """CONTROL: a frame is judged by its report id's NIGHT. One banked on
        the 14th, before the panel was set aside on the 15th and 16th, ends
        nothing the walk had not already ended, and is not a frame shot
        tonight: the two nights are still the answer.

        RED under mutant "any frame of the panel clears it" (the night
        comparison in the frame test of ``earlier_starved_nights`` made
        ``True``), observed:

            AssertionError: assert 0 == 2
             +  where 0 = _earlier(Session(...), 17)
        """
        s = _session([14, 15, 16, 17],
                     aside=[(15, CENTRING), (16, CENTRING)],
                     frames=[(14, True)])
        assert _earlier(s, 17) == 2

    def test_a_clear_on_an_earlier_night_does_not_count_tonight(self):
        """CONTROL: only a clear made on THE night in question lifts it."""
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        s.set_aside[1]["cleared"] = True
        assert _earlier(s, 17) == 3

    def test_the_default_streak_is_what_it_was(self):
        """``set_aside_streak`` with neither new keyword is the answer
        progress serves, tonight included."""
        s = _session([14, 15, 16],
                     aside=[(14, CENTRING), (15, GUIDE_START),
                            (16, "deferred")])
        assert s.set_aside_streak(PANEL) == (3, "deferred")


# ====================================================================== engine

def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _hops(night, label: str) -> list[float]:
    return [night.rel(t) for t, who in night.gotos if who == _name(label)]


async def _night(hub, monkeypatch, k: int, session=None, *,
                 centres: bool = False, at: float | None = None) -> Night:
    """Night ``k`` (0 is ``T0``) of a 1x2 mosaic on one session, or a run
    starting at the instant ``at`` (a restart later the same night). Panel
    1-1 never centres unless ``centres``; 1-2 always does, shoots its six
    frames on the first night and is complete from the ledger after it, so
    from the second night 1-1 is the mosaic's last live panel from the first
    pass."""
    def goto(who, n, result):
        if who == _name("1-1") and not centres:
            return {**result, "centered": False, "error_arcmin": None}
        return result

    night = Night(hub, monkeypatch, t0=T0 + k * DAY if at is None else at,
                  goto=goto)
    try:
        night.done = await night.run(
            grid_plan(rows=1, cols=2),
            **({"session": session} if session is not None else {}))
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    assert night.done, night.lines[-4:]
    return night


async def _three_starved_nights(hub, monkeypatch):
    """The three nights the issue describes, each costing the full six passes:
    returns the session after them and their nights."""
    nights = []
    session = None
    for k in range(STARVED_AFTER_NIGHTS):
        night = await _night(hub, monkeypatch, k, session)
        session = night.stored
        nights.append(night)
    return session, nights


async def test_the_fourth_night_gives_up_on_a_starved_panel_after_one_pass(
        group_hub, monkeypatch):
    """THE ISSUE, end to end on the clocked simulator. Panel 1-1 never
    centres; 1-2 is done after the first night. Nights one to three each
    spend the full D-03 bound on 1-1 (six hops, the last at five holds of
    ``CENTRING_HOLD_RETRY_S`` = 50 minutes, and each ends with a "deferred"
    set-aside), unchanged: three nights is not yet starved. Night four finds
    three starved nights behind it: ONE hop, one pass, then 1-1 is set aside
    for the night with the reason that says why, and the panel's streak goes
    on (four nights, kind "deferred") for the Campaign.

    RED under mutant "the starved bound is the ordinary one" (``limit`` in
    ``GroupRun._apply_held_pass_rule`` made ``HELD_PASS_SET_ASIDE_AT``),
    observed:

        AssertionError: night four hopped 1-1 at [0.0, 600.0, 1200.0,
        1800.0, 2400.0, 3000.0]
        assert 6 == 1

    and RED under mutant "the engine never tells the run" (the
    ``run.note_starved`` call in ``_note_starved_panels`` deleted), with the
    same lines.
    """
    session, first = await _three_starved_nights(group_hub, monkeypatch)
    for k, night in enumerate(first):
        hops = _hops(night, "1-1")
        kinds = [r["kind"] for r in night.stored.set_aside
                 if r["night"] == night_key(night.t0)]
        if k == 0:
            # Beside 1-2's three visits it strikes out (a centring
            # set-aside, which expires 45 minutes on), then is the last
            # live panel for the six held passes.
            assert len(hops) == 3 + 6 and kinds == ["centring", "deferred"], (
                hops, kinds)
        else:
            assert len(hops) == 6 and kinds == ["deferred"], (k, hops, kinds)
        assert hops[-1] - hops[-2] == CENTRING_HOLD_RETRY_S
    assert session.set_aside_streak("p00") == (3, "deferred"), "premise"

    fourth = await _night(group_hub, monkeypatch, STARVED_AFTER_NIGHTS,
                          session)

    hops = _hops(fourth, "1-1")
    assert len(hops) == 1, f"night four hopped 1-1 at {hops}"
    assert fourth.shots() == []
    tonight = [r for r in fourth.stored.set_aside
               if r["night"] == night_key(fourth.t0)]
    assert [(r["target_id"], r["kind"], r["step_id"]) for r in tonight] == [
        ("p00", "deferred", None)], tonight
    assert ("was set aside on 3 nights running before tonight"
            in tonight[0]["reason"]), tonight
    assert fourth.said("a restart tonight does not retry it, the next night "
                       "does"), fourth.lines[-4:]
    assert not fourth.said("holding the mosaic 10 minutes"), (
        "the first held pass ended the matter")
    assert fourth.stored.set_aside_streak("p00") == (4, "deferred")


async def test_a_starved_panel_that_centres_on_the_fourth_night_is_shot(
        group_hub, monkeypatch):
    """CONTROL, a panel that centres is untouched: the same three starved
    nights, then the panel's trouble clears. Night four holds nothing, takes
    no set-aside, and shoots all six frames; the streak is over.

    RED under mutant "a starved panel is set aside before it is tried" (the
    ``run.note_starved`` call in ``_note_starved_panels`` replaced by
    ``run.set_aside_panel(t.id, "starved", kind="deferred")``, so the run
    drops the panel at its start instead of bounding the passes it is
    held), observed:

        AssertionError: [('M31 1-1', 'L'), ('M31 1-1', 'R')]
        assert 2 == 6
    """
    session, _first = await _three_starved_nights(group_hub, monkeypatch)

    fourth = await _night(group_hub, monkeypatch, STARVED_AFTER_NIGHTS,
                          session, centres=True)

    assert len(fourth.shots()) == 6, fourth.shots()
    assert [t for t, _f in fourth.shots()] == [_name("1-1")] * 6
    assert [r for r in fourth.stored.set_aside
            if r["night"] == night_key(fourth.t0)] == []
    assert fourth.stored.owed() == 0
    assert not fourth.said("nights running before tonight")
    assert fourth.stored.set_aside_streak("p00") == (0, None)


async def test_a_restart_the_same_night_does_not_note_a_recovered_panel_starved_again(
        group_hub, monkeypatch):
    """THE RESTART, end to end. The three starved nights, then night four: 1-1
    centres on its first hop and shoots (so what its history said it could
    not do it has done tonight), misses on the second (the first held pass),
    and the run is stopped inside the ten minutes that pass holds for. Run
    again 15 minutes later, the same night (a crash-resume, a CONTINUE, a
    /recover), the panel is noted starved at the new start unless the read
    knows it shot tonight: it gets its six held passes, as it would have
    without the stop, and not the one a starved panel gets.

    RED under mutant "tonight's frames do not count" (the frame test in
    ``Session.earlier_starved_nights`` made ``False and``), observed:

        AssertionError: the restart hopped 1-1 at [0.0]
        assert 1 == 6

    The same read made to need an EFFECTIVE frame (the reviewer's first
    reading, ``f.effective() and`` in front of the test) stays green here and
    is RED in the session case above for a rejected frame, which the driver
    counts as shot.
    """
    session, _first = await _three_starved_nights(group_hub, monkeypatch)
    t4 = T0 + STARVED_AFTER_NIGHTS * DAY
    tonight = night_key(t4)

    def centres_once(who, n, result):
        if who == _name("1-1") and n > 1:
            return {**result, "centered": False, "error_arcmin": None}
        return result

    stopped = Night(group_hub, monkeypatch, t0=t4, goto=centres_once)
    try:
        stopped.arm()
        stopped.engine.start(grid_plan(rows=1, cols=2), session=session)
        stopped.session_id = stopped.engine._session.id
        assert await stopped.until(t4 + 300.0), "the run ended before +300 s"
        await stopped.engine.abort()
    finally:
        await stopped.close()
    stored = session_store.load(stopped.session_id)
    hops = _hops(stopped, "1-1")
    assert len(hops) == 2 and len(stopped.shots()) == 2, (
        hops, stopped.shots())
    assert [r for r in stored.set_aside if r["night"] == tonight] == [], (
        "premise: nothing was set aside before the stop")
    assert stored.set_aside_streak(
        "p00", before=tonight, kinds=HELD_PASS_KINDS)[0] == STARVED_AFTER_NIGHTS, (
        "premise: the nights before are starved by the ledger's own count")

    again = await _night(group_hub, monkeypatch, STARVED_AFTER_NIGHTS, stored,
                         at=t4 + 900.0)

    assert len(again.stored.observing_nights()) == STARVED_AFTER_NIGHTS + 1, (
        "premise: the restart is the same night")
    hops = _hops(again, "1-1")
    assert len(hops) == HELD_PASS_SET_ASIDE_AT, (
        f"the restart hopped 1-1 at {hops}")
    assert not again.said("nights running before tonight")
    tonight_asides = [r for r in again.stored.set_aside
                      if r["night"] == tonight]
    assert [(r["target_id"], r["kind"]) for r in tonight_asides] == [
        ("p00", "deferred")], tonight_asides
    assert "has been held for 6 passes in a row" in tonight_asides[0]["reason"]
