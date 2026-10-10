# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The Campaign's starved flag survives the start of tonight's run (#942;
backlog WP-180, wave 20), and needs no clock to (#970; WP-192, wave 21).

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

THE FIX (``Session.set_aside_streak``): a night that holds nothing of the
target, no set-aside record of any kind or step and no frame of any grade, is
STEPPED OVER, neither counted nor a break. Tonight before its record lands is
such a night, so the earlier nights are counted once and tonight once when
the record lands; a panel shot tonight, or set aside tonight for a kind that
is not starving, is a night the panel was reached on and ends the streak as
it always did.

#942 shipped this as ``open_night``: the caller's clock named the one night
that was still going on and only that night was left out, because a night
that had run and closed without reaching the panel (clouded out first) was
read as a night the panel was spared. #970 replaced that with the rule above:
a closed night that never reached the panel is evidence of nothing either
way, so it is stepped over as tonight is, and the clock is not needed. Its
own cases are in ``test_w21_cloud_out_streak.py``; this file keeps what #942
set out to hold, the flag from the start of the run to the set-aside.

Each case names the mutant it was shown RED under, with the failure observed.
Every mutant was applied from a byte backup of the production file, restored
with a byte copy and md5-compared, never through git (#254). The mutants are
named in ``test_w21_cloud_out_streak.py``'s header, where each is spelled out
against the code.

THE CLOCKS ARE PINNED (#682), as in the w17 file whose helpers this reuses:
the nights are stamped report ids at 21:00 of July dates, each record's night
is the ``night_key`` of that same instant, and each read is made at a pinned
instant of its own (``_clock``). Nothing here is a site.
"""
from __future__ import annotations

import time

import pytest

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


def _streak(s: Session):
    """``set_aside_streak``, which asks no clock."""
    return s.set_aside_streak(PANEL)


# ===================================================================== Session

class TestTonightIsOpen:
    def test_a_started_run_with_no_record_yet_counts_the_nights_before(self):
        """The issue's own example: records on nights 14 to 16, the run for
        night 17 has just started (``nights`` holds it) and has set nothing
        aside. The answer is the three nights before tonight.

        RED under mutant "an untouched night breaks the walk" (the
        ``reached`` test of ``set_aside_streak`` made ``if False``, the walk
        before #942), observed:

            AssertionError: assert (0, None) == (3, 'centring')
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        assert _streak(s) == (3, CENTRING)

    def test_the_kind_is_the_newest_earlier_nights_while_tonight_is_open(self):
        """The kind named is the newest counted record's, as it always was:
        with tonight untouched that is last night's.

        RED under mutant "an untouched night breaks the walk", observed:

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

        RED under mutant "an untouched night counts" (the step-over
        ``continue`` made ``streak += 1; continue``), observed, at the
        premise, where tonight holds nothing yet:

            AssertionError: premise: untouched
            assert (4, 'centring') == (3, 'centring')

        and under "an untouched night breaks the walk", at the same line:

            AssertionError: premise: untouched
            assert (0, None) == (3, 'centring')
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        assert _streak(s) == (3, CENTRING), "premise: untouched"
        s.note_set_aside(PANEL, REASON, night=_key(17), kind=CENTRING)
        assert _streak(s) == (4, CENTRING)
        s.note_set_aside(PANEL, REASON, night=_key(17), kind=CENTRING)
        s.nights.append(_rid(17, 23, 30))
        assert len(s.observing_nights()) == 4 and len(s.nights) == 5, "premise"
        assert _streak(s) == (4, CENTRING)

    def test_one_session_read_through_the_night_boundaries(self):
        """The streak is read at every moment of three nights of one session,
        the way the progress card is: a run starts, the panel is set aside,
        the run is resumed at 01:40 (still the same night: ``night_key``
        rolls over at noon), the next night's run starts, its panel is set
        aside. The flag's count must never fall to zero between a record and
        the next record.

        RED under mutant "an untouched night breaks the walk", observed:

            AssertionError: assert [0, 3, 3, 0, 4] == [2, 3, 3, 3, 4]
        """
        s = Session(id="s-w20", nights=[_rid(14), _rid(15)])
        for d in (14, 15):
            s.note_set_aside(PANEL, REASON, night=_key(d), kind=CENTRING)

        reads: list[int] = []
        s.nights.append(_rid(16))                  # night 16: the run starts
        reads.append(_streak(s)[0])
        s.note_set_aside(PANEL, REASON, night=_key(16), kind=CENTRING)
        reads.append(_streak(s)[0])                # ... and sets it aside
        s.nights.append(_rid(17, 1, 40))           # 01:40: a resume, night 16
        assert len(s.observing_nights()) == 3, "premise: the same night"
        reads.append(_streak(s)[0])
        s.nights.append(_rid(17))                  # night 17: the run starts
        assert len(s.observing_nights()) == 4, "premise: a new night"
        reads.append(_streak(s)[0])
        s.note_set_aside(PANEL, REASON, night=_key(17), kind=CENTRING)
        reads.append(_streak(s)[0])                # ... and sets it aside
        assert reads == [2, 3, 3, 3, 4]

    @pytest.mark.parametrize("auto_accepted", [True, False],
                             ids=["accepted", "rejected"])
    def test_a_panel_shot_tonight_is_not_starved(self, auto_accepted):
        """A night is untouched for a panel that has not been reached, not
        for one that is being exposed: a frame of any grade, a rejected one
        too (it was centred and exposed, the word ``earlier_starved_nights``
        and the driver use), means the panel is not starved tonight.

        RED under mutant "a shot night is stepped over too" (the frames half
        of ``reached`` dropped), observed, both ids:

            AssertionError: assert (3, 'centring') == (0, None)

        and the rejected id alone under "only an effective frame reaches
        the night" (``f.effective()`` added to that half), the same line.
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)],
                     frames=[(17, PANEL, auto_accepted)])
        assert _streak(s) == (0, None)

    def test_another_panels_frame_tonight_does_not_close_it(self):
        """CONTROL: only the panel's own frame says it was reached. The
        night working on its neighbours is the case the flag exists for.

        RED under mutant "any panel's frame reaches the night" (the
        ``target_id`` test of the frames half of ``reached`` dropped),
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

        RED under mutant "an untouched night breaks the walk", observed:

            AssertionError: assert (0, None) == (2, 'centring')

        and under mutant "an untouched night counts", observed:

            AssertionError: assert (3, 'centring') == (2, 'centring')
        """
        s = _session([14, 15, 16, 17], aside=[(15, CENTRING), (16, CENTRING)],
                     frames=[(14, PANEL, True)])
        assert _streak(s) == (2, CENTRING)

    def test_a_set_aside_for_another_reason_tonight_ends_it(self):
        """Tonight the panel WAS reached and set aside, for a reason that is
        the sky's or the night's (``floor``), or for one step only, or by a
        record with no kind: it is not starved of a star tonight, and a night
        it was set aside for something else is not a night the walk may
        step over. Any record of the target from the night reaches it.

        RED under mutant "only a starving record reaches the night" (the
        set-aside half of ``reached`` filtered to ``r.get("kind") in
        STARVING_KINDS``), observed:

            AssertionError: assert (3, 'centring') == (0, None)

        and under mutant "only a whole-panel record reaches the night" (the
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

        RED under mutant "any panel's record reaches the night" (the
        ``target_id`` test of the set-aside half of ``reached`` dropped),
        observed:

            AssertionError: assert (0, None) == (3, 'centring')
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        s.note_set_aside("panel-b", REASON, night=_key(17), kind=CENTRING)
        assert _streak(s) == (3, CENTRING)

    def test_the_driver_still_reads_the_nights_before_tonight(self):
        """CONTROL on the engine's read: with tonight untouched and with it
        recorded, ``earlier_starved_nights`` is three either way."""
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        read = lambda: s.earlier_starved_nights(  # noqa: E731
            PANEL, night=_key(17), kinds=HELD_PASS_KINDS)
        assert read() == 3
        s.note_set_aside(PANEL, REASON, night=_key(17), kind="deferred")
        assert read() == 3

    def test_a_session_with_no_record_is_still_nothing(self):
        """CONTROL: a walk over nights that hold nothing of the panel counts
        none of them.

        RED under mutant "an untouched night counts", observed:

            AssertionError: assert (4, None) == (0, None)
        """
        assert _streak(_session([14, 15, 16, 17])) == (0, None)
        assert _streak(Session(id="s-w20")) == (0, None)


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

        RED under mutant "an untouched night breaks the walk", observed:

            AssertionError: assert {'M16 1-1': N...16 2-2': None} == {...}
              Differing items:
              {'M16 1-2': None} != {'M16 1-2': {'kind': 'centring',
              'nights': 3}}

        and under mutant "an untouched night counts", observed, at the first
        read:

            AssertionError: assert {'M16 1-1': N...16 2-2': None} == {...}
              Differing items:
              {'M16 1-2': {'kind': 'centring', 'nights': 4}} != {'M16 1-2':
              {'kind': 'centring', 'nights': 3}}
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

    def test_the_flag_does_not_depend_on_a_clock(self):
        """``flow_progress`` asked with no ``now`` reads the same ledger and
        gives the same flag: the clock the route hands in keys other things
        (``continue_night``, a dormant session's ``set_aside`` list), and
        since #970 not the starved count. Asked with it, at the night the run
        is in, at a later one and at an earlier one, the answer is the same.

        RED under mutant "an untouched night breaks the walk", observed:

            AssertionError: assert {'M16 1-1': N...16 2-2': None} == {...}
              Differing items:
              {'M16 1-2': None} != {'M16 1-2': {'kind': 'centring',
              'nights': 3}}
        """
        graph, compiled, plan, s = _world()
        s.status = "active"
        _aside(s, _cell(plan, 0, 1), [14, 15, 16])
        bare = flow_progress(compiled, plan, s, flow_id=FLOW)
        want = {"M16 1-1": None, "M16 1-2": STARVED_ENTRY,
                "M16 2-1": None, "M16 2-2": None}
        assert _entries(bare) == want
        for day in (10, 17, 19):
            assert _entries(_card(compiled, plan, s, day)) == want, day

    def test_a_panel_shot_tonight_drops_the_flag(self):
        """A started run whose panel centred and shot: it is not starved,
        however many nights it was set aside before (the frame, accepted or
        not, is the evidence).

        RED under mutant "a shot night is stepped over too" (the frames half
        of ``reached`` iterating over no frames), observed:

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
        """CONTROL: stepping tonight over does not lower the bar. Two nights
        set aside and a run just started is a streak of two, short of
        ``STARVED_AFTER_NIGHTS``, and the answer carries nothing.

        RED under mutant "an untouched night counts", observed:

            AssertionError: {'M16 1-1': None, 'M16 1-2': {'kind':
            'centring', 'nights': 3}, 'M16 2-1': None, 'M16 2-2': None}
            assert False
        """
        graph, compiled, plan, s = _world(days=(15, 16, 17))
        s.status = "active"
        _aside(s, _cell(plan, 0, 1), [15, 16])
        entries = _entries(_card(compiled, plan, s, 17))
        assert all(v is None for v in entries.values()), entries


def _two_member_pool():
    return _pool_graph("M31, M42")


class TestEveryBlockKind:
    @pytest.mark.parametrize("make", [_single_graph, _two_member_pool],
                             ids=["target", "pool"])
    def test_a_target_and_a_pool_member_keep_the_flag_too(self, make):
        """The mosaic panels are read in ``_mosaic``; a single target and a
        pool member are read in ``flow_progress`` itself, through the same
        ``_starved``. A started run with nothing set aside keeps the flag on
        the panel that was set aside whole on the three nights before, and
        on no other panel, asked with a clock or without one.

        RED under mutant "an untouched night breaks the walk", observed,
        both ids, at the first read (with a clock):

            AssertionError: {'now': 1784354400.0}
            assert None == {'kind': 'centring', 'nights': 3}

        and under "an untouched night counts", observed, both ids:

            AssertionError: {'now': 1784354400.0}
            assert {'kind': 'cen..., 'nights': 4} == {'kind': 'cen...,
            'nights': 3}
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

        for kw in ({"now": _clock(17, 23)}, {}):
            live = starved(**kw)
            assert live[ids[0]] == STARVED_ENTRY, kw
            assert [v for k, v in live.items() if k != ids[0]] == [None] * (
                len(ids) - 1)
