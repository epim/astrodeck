# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The Campaign's starved flag survives the start of tonight's run (#942;
backlog WP-180, wave 20).

THE DEFECT. ``Session.set_aside_streak`` walked the observing nights from the
newest and stopped at the first that had no whole-panel starving record.
``engine.start`` appends tonight's report id to ``Session.nights`` the moment
a run begins, and tonight's record is written only when the panel is set
aside, which is minutes to an hour later on the unfixed six-pass path. For
that whole stretch tonight was the newest night with no record, the walk
stopped at it at once, and a panel set aside whole on the three nights before
answered ``(0, None)``: ``flows.progress._starved`` and with it the
Campaign's starved line and list dropped out as the run started and came back
when the set-aside landed.

THE FIX (``Session.set_aside_streak``, ``open_night``): the newest night is
left out of the walk when it IS the night the caller's clock is in and the
session holds nothing of the target from it, no set-aside record of any kind
and no frame of any grade; never when ``before`` is given. A record landing
turns the night into one the walk counts, so tonight is counted once; a panel
shot tonight, or set aside tonight for a kind that is not starving, ends the
streak as it always did.

THE CLOCK, NOT THE LEDGER, SAYS WHICH NIGHT IS OPEN. The first version of the
fix left the newest night out whenever it held nothing, and so could not tell
a night still running from one that ran and closed without reaching the panel
(clouded out first). That night then stayed "left out" for as long as it was
the newest, the card kept saying "set aside on 3 nights running" for every
day after it, and the flag dropped to nothing the moment the next run started,
which is the defect again in another scenario, and the group driver
(``earlier_starved_nights``) already read 0 at that start. The progress
answer hands in the key of ``now``'s night (``flow_progress(now=...)``, which
the route reads once per request), and only a newest night equal to it is left
out; with no clock nothing is.

Each case names the mutant it was shown RED under, with the failure observed.
Every mutant was applied from a byte backup of the production file, restored
with a byte copy and md5-compared, never through git (#254).

THE CLOCKS ARE PINNED (#682), as in the w17 file whose helpers this reuses:
the nights are stamped report ids at 21:00 of July dates, each record's night
is the ``night_key`` of that same instant, and each read is made at a pinned
instant of its own (``_clock``). Nothing here is a site.
"""
from __future__ import annotations

import time

import pytest

from astrodeck.events import night_key
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import _campaign
from astrodeck.sequence.group_rules import (CENTRING, GUIDE_START,
                                            HELD_PASS_KINDS)
from astrodeck.sequence.session import Session, SessionFrame

from test_flows_progress import FLOW as PLAIN_FLOW
from test_flows_progress import _graph as _single_graph
from test_flows_progress import _pool_graph
from test_w17_starved_panel import (FLOW, PANEL, REASON, _aside, _block,
                                    _cell, _key, _rid, _session, _world)

STARVED_ENTRY = {"nights": 3, "kind": "centring"}


def _clock(day: int, hour: int = 21, minute: int = 0) -> float:
    """The pinned instant July ``day`` 2026 at ``hour:minute`` local."""
    return time.mktime((2026, 7, day, hour, minute, 0, 0, 0, -1))


def _at(day: int, hour: int = 21, minute: int = 0) -> str:
    """The night key a read made at that instant is in (``night_key`` itself,
    as ``flow_progress`` computes it from the ``now`` it is handed)."""
    return night_key(_clock(day, hour, minute))


def _streak(s: Session, open_day: int | None = 17):
    """``set_aside_streak`` read while July ``open_day``'s night is open."""
    return s.set_aside_streak(
        PANEL, open_night=None if open_day is None else _at(open_day, 23))


# ===================================================================== Session

class TestTonightIsOpen:
    def test_a_started_run_with_no_record_yet_counts_the_nights_before(self):
        """The issue's own example: records on nights 14 to 16, the run for
        night 17 has just started (``nights`` holds it) and has set nothing
        aside. The answer is the three nights before tonight.

        RED under mutant "tonight breaks the walk" (the open-night branch in
        ``set_aside_streak`` made unreachable: ``and nights[-1] ==
        open_night`` replaced by ``and False``), observed:

            AssertionError: assert (0, None) == (3, 'centring')
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        assert _streak(s) == (3, CENTRING)

    def test_the_kind_is_the_newest_earlier_nights_while_tonight_is_open(self):
        """The kind named is the newest counted record's, as it always was:
        with tonight open that is last night's.

        RED under mutant "tonight breaks the walk", observed:

            AssertionError: assert (0, None) == (3, 'guide_start')
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING),
                            (16, GUIDE_START)])
        assert _streak(s) == (3, GUIDE_START)

    def test_tonights_record_landing_counts_tonight_once(self):
        """Earlier nights plus tonight, and not tonight twice: the record
        landing is one more night (4), a second record the same night (the
        panel set aside again after its expiry) and a crash-resume's second
        run id on the same night are not.

        RED under mutant "tonight is always left out" (``if open_night not in
        held:`` made ``if True:``), observed:

            AssertionError: assert (3, 'centring') == (4, 'centring')
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        assert _streak(s) == (3, CENTRING), "premise: open"
        s.note_set_aside(PANEL, REASON, night=_key(17), kind=CENTRING)
        assert _streak(s) == (4, CENTRING)
        s.note_set_aside(PANEL, REASON, night=_key(17), kind=CENTRING)
        s.nights.append(_rid(17, 23, 30))
        assert len(s.observing_nights()) == 4 and len(s.nights) == 5, "premise"
        assert _streak(s) == (4, CENTRING)

    def test_one_session_read_through_the_night_boundaries(self):
        """The streak is read at every moment of three nights of one session,
        the way the progress card is, each read with the clock of its own
        moment: a run starts, the panel is set aside, the run is resumed at
        01:40 (still the same night: ``night_key`` rolls over at noon), the
        next night's run starts, its panel is set aside. The flag's count
        must never fall to zero between a record and the next record.

        RED under mutant "tonight breaks the walk", observed:

            AssertionError: assert [0, 3, 3, 0, 4] == [2, 3, 3, 3, 4]
        """
        s = Session(id="s-w20", nights=[_rid(14), _rid(15)])
        for d in (14, 15):
            s.note_set_aside(PANEL, REASON, night=_key(d), kind=CENTRING)

        def read(day, hour, minute=0):
            return s.set_aside_streak(
                PANEL, open_night=_at(day, hour, minute))[0]

        reads: list[int] = []
        s.nights.append(_rid(16))                  # night 16: the run starts
        reads.append(read(16, 21))
        s.note_set_aside(PANEL, REASON, night=_key(16), kind=CENTRING)
        reads.append(read(16, 21, 30))             # ... and sets it aside
        s.nights.append(_rid(17, 1, 40))           # 01:40: a resume, night 16
        assert len(s.observing_nights()) == 3, "premise: the same night"
        reads.append(read(17, 1, 45))
        s.nights.append(_rid(17))                  # night 17: the run starts
        assert len(s.observing_nights()) == 4, "premise: a new night"
        reads.append(read(17, 21))
        s.note_set_aside(PANEL, REASON, night=_key(17), kind=CENTRING)
        reads.append(read(17, 22))                 # ... and sets it aside
        assert reads == [2, 3, 3, 3, 4]

    @pytest.mark.parametrize("auto_accepted", [True, False],
                             ids=["accepted", "rejected"])
    def test_a_panel_shot_tonight_is_not_starved(self, auto_accepted):
        """The night is open for a panel that has not been reached, not for
        one that is being exposed: a frame of any grade, a rejected one too
        (it was centred and exposed, the word ``earlier_starved_nights``
        and the driver use), means the panel is not starved tonight.

        RED under mutant "a shot night is left out too" (the frames half of
        ``held`` dropped), observed:

            AssertionError: assert (3, 'centring') == (0, None)
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)],
                     frames=[(17, PANEL, auto_accepted)])
        assert _streak(s) == (0, None)

    def test_another_panels_frame_tonight_does_not_close_it(self):
        """CONTROL: only the panel's own frame says it was reached. The
        night working on its neighbours is the case the flag exists for.

        RED under mutant "any panel's frame closes the night" (the
        ``target_id`` test of the frames half of ``held`` dropped),
        observed:

            AssertionError: assert (0, None) == (3, 'centring')
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)],
                     frames=[(17, "panel-b", True)])
        assert _streak(s) == (3, CENTRING)

    def test_a_frame_from_an_earlier_night_does_not_close_tonight(self):
        """CONTROL: a frame is judged by its report id's NIGHT. One banked on
        the 14th, before the panel was set aside on the 15th and 16th, ends
        nothing the walk had not ended, and is not a frame of tonight.

        RED under mutant "any frame of the panel closes the night" (the
        frame's night in ``held`` replaced by ``open_night``), observed:

            AssertionError: assert (0, None) == (2, 'centring')
        """
        s = _session([14, 15, 16, 17], aside=[(15, CENTRING), (16, CENTRING)],
                     frames=[(14, PANEL, True)])
        assert _streak(s) == (2, CENTRING)

    def test_a_set_aside_for_another_reason_tonight_ends_it(self):
        """Tonight the panel WAS reached and set aside, for a reason that is
        the sky's or the night's (``floor``), or for one step only, or by a
        record with no kind: it is not starved of a star tonight, and a night
        it was set aside for something else is not a night the walk may
        step over. Any record of the target from the night closes it.

        RED under mutant "only a starving record closes the night" (the set
        aside half of ``held`` filtered to ``r.get("kind") in kinds``),
        observed:

            AssertionError: assert (3, 'centring') == (0, None)

        and under mutant "only a whole-panel record closes the night" (the
        same half filtered to ``r.get("step_id") is None``), observed, in
        the step-level case only: the same assertion.
        """
        aside = [(14, CENTRING), (15, CENTRING), (16, CENTRING)]
        floor = _session([14, 15, 16, 17], aside=aside)
        floor.note_set_aside(PANEL, REASON, night=_key(17), kind="floor")
        assert _streak(floor) == (0, None)
        step = _session([14, 15, 16, 17], aside=aside)
        step.note_set_aside(PANEL, REASON, night=_key(17), step_id="s1",
                            kind=CENTRING)
        assert _streak(step) == (0, None)
        bare = _session([14, 15, 16, 17], aside=aside)
        bare.note_set_aside(PANEL, REASON, night=_key(17))
        assert "kind" not in bare.set_aside[-1], "premise: no kind"
        assert _streak(bare) == (0, None)

    def test_another_panels_record_tonight_does_not_close_it(self):
        """CONTROL: the panel next door being set aside tonight (the
        ordinary case in a mosaic) says nothing of this one.

        RED under mutant "any panel's record closes the night" (the
        ``target_id`` test of the set aside half of ``held`` dropped),
        observed:

            AssertionError: assert (0, None) == (3, 'centring')
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        s.note_set_aside("panel-b", REASON, night=_key(17), kind=CENTRING)
        assert _streak(s) == (3, CENTRING)

    def test_only_the_newest_night_is_left_out(self):
        """CONTROL: a night in the MIDDLE with no record is still the end of
        the streak (a panel not set aside on the 16th was not set aside
        night after night); tonight being open does not make every
        untouched night a gap to step over.

        RED under mutant "every untouched night is left out" (the newest
        night's test made a filter over all of ``nights``), observed:

            AssertionError: assert (2, 'centring') == (0, None)
        """
        s = _session([14, 15, 16, 17], aside=[(14, CENTRING), (15, CENTRING)])
        assert _streak(s) == (0, None)

    def test_before_names_the_nights_wanted_and_nothing_is_left_out(self):
        """CONTROL: the group driver asks for the nights BEFORE tonight
        (``before``, #835). The night before that, 16, ran and set nothing
        aside, so it ends the walk; it is not an open night to step over.
        The answer is the one it was, and when a caller gives BOTH a
        ``before`` and an ``open_night`` that names the night just before it,
        ``before`` has said where the walk ends and nothing is left out.

        RED under mutant "an open night is left out after ``before`` too"
        (``elif (before is None and open_night is not None and nights`` made
        ``if (open_night is not None and nights``, so the skip also runs
        after the ``before`` truncation), observed:

            AssertionError: assert (2, 'centring') == (0, None)
        """
        s = _session([14, 15, 16, 17], aside=[(14, CENTRING), (15, CENTRING)])
        assert s.set_aside_streak(PANEL, before=_key(17)) == (0, None)
        assert s.set_aside_streak(PANEL, before=_key(17),
                                  open_night=_key(16)) == (0, None)
        assert s.earlier_starved_nights(PANEL, night=_key(17),
                                        kinds=HELD_PASS_KINDS) == 0

    def test_the_driver_still_reads_the_nights_before_tonight(self):
        """CONTROL on the engine's read: with tonight open and with it
        recorded, ``earlier_starved_nights`` is three either way."""
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        read = lambda: s.earlier_starved_nights(  # noqa: E731
            PANEL, night=_key(17), kinds=HELD_PASS_KINDS)
        assert read() == 3
        s.note_set_aside(PANEL, REASON, night=_key(17), kind="deferred")
        assert read() == 3

    def test_a_session_with_no_record_is_still_nothing(self):
        """CONTROL: the walk over nights that hold nothing stays empty."""
        assert _streak(_session([14, 15, 16, 17])) == (0, None)
        assert _streak(Session(id="s-w20")) == (0, None)


class TestOnlyTheNightThatIsOpenIsLeftOut:
    """The reviewer's finding on the first version of the fix: a newest night
    that holds nothing is not always an OPEN one."""

    def test_a_night_that_closed_untouched_ends_the_streak(self):
        """Records on nights 14 to 16; night 17 runs and clouds out before the
        panel's turn; the next run starts on night 20. Read at each moment
        with that moment's clock, and beside the group driver's own read at
        night 20's start, the number the Campaign must agree with:

            night 17, open      3   the panel has not been reached yet
            day of the 18th     0   night 17 ran and set nothing aside
            night 20, open      0   the same 0 the driver acts on

        RED under mutant "the clock is not consulted" (the open-night test
        ``and nights[-1] == open_night`` made ``and True``, which is the
        first version of the fix), observed:

            AssertionError: assert [3, 3, 0, 0] == [3, 0, 0, 0]
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        read = lambda day, hour: s.set_aside_streak(  # noqa: E731
            PANEL, open_night=_at(day, hour))[0]
        reads = [read(17, 23), read(18, 14)]
        s.nights.append(_rid(20))
        reads.append(read(20, 22))
        driver = s.earlier_starved_nights(PANEL, night=_key(20),
                                          kinds=HELD_PASS_KINDS)
        reads.append(driver)
        assert reads == [3, 0, 0, 0]

    def test_the_closed_night_answers_nothing_not_the_earlier_streak(self):
        """The same history as a whole answer, kind included: after night 17
        closed, the streak is ``(0, None)``, not the three nights before it.

        RED under mutant "the clock is not consulted", observed:

            AssertionError: assert (3, 'centring') == (0, None)
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        assert s.set_aside_streak(PANEL, open_night=_at(19, 14)) == (0, None)

    def test_no_clock_leaves_nothing_out(self):
        """Without an ``open_night`` there is no way to say which night is
        open, so none is left out: the answer is the walk's own, which ends
        at a started run that has set nothing aside. This is what every caller
        that has no clock (``flow_progress`` with no ``now``) gets.

        RED under mutant "no clock means the newest night is open" (the gate
        ``open_night is not None and nights and nights[-1] == open_night``
        made ``nights and (open_night is None or nights[-1] ==
        open_night)``), observed:

            AssertionError: assert (3, 'centring') == (0, None)
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        assert _streak(s, open_day=None) == (0, None)
        assert s.set_aside_streak(PANEL) == (0, None)

    def test_a_run_id_with_no_stamp_is_never_the_open_night(self):
        """A hand-made or legacy session whose newest run id carries no stamp
        is keyed by the id itself (``observing_nights``), which no night key
        equals: it is not left out, and the earlier streak does not show
        through it. The answer is the one before #942.

        RED under mutant "the clock is not consulted", observed:

            AssertionError: assert (3, 'centring') == (0, None)
        """
        s = _session([14, 15, 16],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        s.nights.append("hand-made-run")
        assert s.observing_nights()[-1] == "hand-made-run", "premise"
        assert _streak(s) == (0, None)


# ====================================================== the progress answer

def _entries(answer):
    return {e["name"]: e.get("starved") for e in _block(answer)["panels"]}


def _card(compiled, plan, session, day, hour=23, minute=0):
    """The progress answer asked at July ``day`` ``hour``:``minute``."""
    return flow_progress(compiled, plan, session, flow_id=FLOW,
                         now=_clock(day, hour, minute))


class TestTheCampaignWhileTheRunIsLive:
    def test_the_flag_persists_from_the_run_start_to_the_set_aside(self):
        """The issue's validation, through the real chain (a session,
        ``flow_progress``, the Campaign): three nights set aside, the run for
        the fourth has started and has set nothing aside. The panel carries
        ``starved`` with the three nights, the Campaign names it, and the
        set-aside landing makes it four, never dropping out between.

        RED under mutant "tonight breaks the walk", observed:

            AssertionError: assert {'M16 1-1': N...16 2-2': None} == {...}
              Differing items:
              {'M16 1-2': None} != {'M16 1-2': {'kind': 'centring',
              'nights': 3}}

        and under mutant "tonight is always left out", observed, at the
        record landing:

            AssertionError: assert {'kind': 'cen..., 'nights': 3} ==
            {'kind': 'cen..., 'nights': 4}
        """
        graph, compiled, plan, s = _world()
        s.status = "active"
        p = _cell(plan, 0, 1)
        _aside(s, p, [14, 15, 16])
        assert _entries(_card(compiled, plan, s, 17)) == {
            "M16 1-1": None, "M16 1-2": STARVED_ENTRY,
            "M16 2-1": None, "M16 2-2": None}
        c = _campaign(graph, None, lambda: _card(compiled, plan, s, 17))
        assert c["starved"] == [{"block": "m", "name": "M16 1-2",
                                 **STARVED_ENTRY}]
        assert ("Panel M16 1-2 has been set aside on 3 nights running"
                in c["note"]), c["note"]
        assert "RSN-7731" not in str(c), "the reason never reaches the answer"

        _aside(s, p, [17])
        assert _entries(_card(compiled, plan, s, 17))["M16 1-2"] == {
            "nights": 4, "kind": "centring"}

    def test_without_a_clock_the_answer_is_the_one_before(self):
        """``flow_progress`` asked with no ``now`` cannot name the open
        night, and leaves none out: the started run with nothing set aside
        reads as it did before #942 (no ``starved``), which is also the
        shape every older caller and test of it gets.

        RED under mutant "no clock means the newest night is open" (in
        ``set_aside_streak``), observed:

            assert False
             +  where False = all(<generator object ...<genexpr> at 0x...>)
        """
        graph, compiled, plan, s = _world()
        s.status = "active"
        _aside(s, _cell(plan, 0, 1), [14, 15, 16])
        answer = flow_progress(compiled, plan, s, flow_id=FLOW)
        assert all(v is None for v in _entries(answer).values())

    def test_a_panel_shot_tonight_drops_the_flag(self):
        """A started run whose panel centred and shot: it is not starved,
        however many nights it was set aside before (the frame, accepted or
        not, is the evidence).

        RED under mutant "a shot night is left out too" (the frames half of
        ``held`` iterating over no frames), observed:

            AssertionError: assert {'M16 1-1': N...16 2-2': None} == {...}
              Differing items:
              {'M16 1-2': {'kind': 'centring', 'nights': 3}} != {'M16 1-2':
              None}
        """
        graph, compiled, plan, s = _world()
        s.status = "active"
        p = _cell(plan, 0, 1)
        _aside(s, p, [14, 15, 16])
        s.frames.append(SessionFrame(night=_rid(17), target_id=p.id,
                                     step_id=p.steps[0].id,
                                     auto_accepted=False))
        assert _entries(_card(compiled, plan, s, 17)) == {
            "M16 1-1": None, "M16 1-2": None, "M16 2-1": None,
            "M16 2-2": None}
        c = _campaign(graph, None, lambda: _card(compiled, plan, s, 17))
        assert "starved" not in c and "running" not in c["note"]

    def test_two_earlier_nights_are_still_not_starvation(self):
        """CONTROL: tonight being left out does not lower the bar. Two
        nights set aside and a run just started is a streak of two, short of
        ``STARVED_AFTER_NIGHTS``, and the answer carries nothing."""
        graph, compiled, plan, s = _world(days=(15, 16, 17))
        s.status = "active"
        _aside(s, _cell(plan, 0, 1), [15, 16])
        entries = _entries(_card(compiled, plan, s, 17))
        assert all(v is None for v in entries.values()), entries

    def test_a_night_that_closed_untouched_drops_the_flag_on_the_card(self):
        """The reviewer's scenario through the real chain: records on nights
        14 to 16, night 17 runs and clouds out before the panel's turn, and
        the next run starts on night 20. While night 17 is open the Campaign
        names the panel (3 nights); once it has closed (the 18th, the 19th)
        and when night 20's run starts it says nothing, the same 0 the group
        driver reads at that start, and the flag never reappears between.

        RED under mutant "the clock is not consulted" (``and nights[-1] ==
        open_night`` made ``and True``), observed:

            AssertionError: assert [3, 3, 3, 0] == [3, 0, 0, 0]
        """
        graph, compiled, plan, s = _world()
        s.status = "active"
        p = _cell(plan, 0, 1)
        _aside(s, p, [14, 15, 16])

        def nights_named(day, hour):
            e = _entries(_card(compiled, plan, s, day, hour))["M16 1-2"]
            return 0 if e is None else e["nights"]

        reads = [nights_named(17, 23)]
        s.status = "dormant"                        # the run ended
        reads += [nights_named(18, 14), nights_named(19, 14)]
        s.nights.append(_rid(20))                   # night 20: a run starts
        s.status = "active"
        reads.append(nights_named(20, 22))
        assert reads == [3, 0, 0, 0]
        assert s.earlier_starved_nights(
            p.id, night=_key(20), kinds=HELD_PASS_KINDS) == 0
        c = _campaign(graph, None, lambda: _card(compiled, plan, s, 20, 22))
        assert "starved" not in c and "running" not in c["note"]


def _two_member_pool():
    return _pool_graph("M31, M42")


class TestEveryBlockKindReadsTheClock:
    @pytest.mark.parametrize("make", [_single_graph, _two_member_pool],
                             ids=["target", "pool"])
    def test_a_target_and_a_pool_member_keep_the_flag_too(self, make):
        """The mosaic panels are read in ``_mosaic``; a single target and a
        pool member are read in ``flow_progress`` itself, through the same
        ``_starved``. A started run with nothing set aside keeps the flag on
        the panel that was set aside whole on the three nights before, and
        on no other panel; with no ``now`` it does not.

        RED under mutant "target path ignores the clock" (``flow_progress``'s
        own ``_starved(session, target, open_night=open_night)`` made
        ``_starved(session, target)``), observed, both ids:

            AssertionError: assert None == {'kind': 'centring', 'nights': 3}

        and under "no clock means the newest night is open" (in
        ``set_aside_streak``), observed, both ids, at the no-clock read:

            AssertionError: no clock, no night left out
            assert {'kind': 'centring', 'nights': 3} is None
        """
        graph = make()
        compiled = compile_plan(graph, "n")
        plan, _ = to_sequence_plan(compiled, graph, flow_id=PLAIN_FLOW)
        plan = plan.model_copy(update={"count_mode": "attempts"})
        bare = flow_progress(compiled, plan, None, flow_id=PLAIN_FLOW)
        ids = [p["target_id"] for b in bare["blocks"] for p in b["panels"]
               if p["target_id"]]
        assert ids, "premise: the plan names a panel"
        s = Session(id="s-w20-plain", status="active", plan=plan,
                    nights=[_rid(d) for d in (14, 15, 16, 17)],
                    origin="flow", origin_id=PLAIN_FLOW)
        for d in (14, 15, 16):
            s.note_set_aside(ids[0], REASON, night=_key(d), kind=CENTRING)

        def starved(**kw):
            answer = flow_progress(compiled, plan, s, flow_id=PLAIN_FLOW, **kw)
            return {p["target_id"]: p.get("starved")
                    for b in answer["blocks"] for p in b["panels"]
                    if p["target_id"]}

        live = starved(now=_clock(17, 23))
        assert live[ids[0]] == STARVED_ENTRY
        assert [v for k, v in live.items() if k != ids[0]] == [None] * (
            len(ids) - 1)
        assert starved()[ids[0]] is None, "no clock, no night left out"
