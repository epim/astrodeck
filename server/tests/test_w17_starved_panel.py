# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A mosaic panel set aside night after night is NAMED on the Campaign (#180
part A; backlog WP-131, wave 17).

A panel that cannot centre or start guiding is deferred, then set aside for
the night, and the next night is tried again with the same result. Every
night then looks like a transient failure and a mosaic can sit at 5 of 6
panels for weeks. The chain this file grades, end to end:

* ``Session.set_aside_streak(target_id)``: on how many CONSECUTIVE observing
  nights (ending with the newest the session ran) the session holds a
  whole-panel set-aside record of a STARVING kind for the target AND banked
  no effective frame of it that night, and the kind of the newest record;
* ``GroupRun._count_failure``: a streak of guide-start failures and nothing
  else is a ``guide_start`` set-aside, as a streak of centring misses is a
  ``centring`` one (it was ``deferred``), so the Campaign can say "no guide
  star" and not only "failed to centre or guide";
* ``progress._starved``: ``{nights, kind}`` on the panel's entry, only where
  the streak has reached ``STARVED_AFTER_NIGHTS`` (every other answer is
  byte-identical to the answer before);
* ``tonight._panel_rows`` / ``_panels_clause`` / ``_campaign``: the row, one
  sentence per starved panel, and the ``starved`` list.

THE RECORD'S FREE-TEXT ``reason`` NEVER REACHES THE ANSWER. It can carry a
solver's error text, and progress is served to a VIEWER; the sentence takes
its words from the KIND alone, three fixed phrases.

THE CLOCKS ARE PINNED (#682): the nights are stamped report ids at 21:00 of
four July dates, and each record's night is the ``night_key`` of that same
instant, so no hour of the day the suite runs at moves a record across the
noon rollover. Nothing here is a site, and no number is derived from one.

Each behaviour was run against the mutants named in its case, from a byte
backup of the source file inside this worktree, restored and sha256-compared
after each, the mutant's marker grepped absent (#254). The observed failure
is recorded in the case.
"""
from __future__ import annotations

import json
import time

import pytest

from _group_harness import (GROUP_NAME, T0, Night, grid_plan,  # noqa: F401
                            group_hub, group_store)
from astrodeck.events import night_key
from astrodeck.flows import progress as progress_mod
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import _campaign
from astrodeck.sequence.group_rules import (CENTRING, GUIDE_START, GroupRun,
                                            PanelDeferred)
from astrodeck.sequence.session import (STARVED_AFTER_NIGHTS, STARVING_KINDS,
                                        Session, SessionFrame, session_store)

#: Free text a solver's error could carry: it must reach no answer.
REASON = "RSN-7731 plate solve failed near the third marker"
PANEL = "panel-a"
FLOW = "flow-w17-starved"
FOV = (2.0, 1.33)


# ----------------------------------------------------------------- the nights

def _rid(day: int, hour: int = 21, minute: int = 0) -> str:
    """A report id for a run started at ``hour:minute`` local on July ``day``
    2026: ``SessionReporter._make_id``'s shape."""
    return f"mosaic-202607{day:02d}-{hour:02d}{minute:02d}00"


def _key(day: int) -> str:
    """The observing night July ``day``'s 21:00 run belongs to, computed here
    from ``night_key`` itself and not through ``report_night``."""
    return night_key(time.mktime((2026, 7, day, 21, 0, 0, 0, 0, -1)))


def _session(days, *, aside=(), frames=()) -> Session:
    """A session that ran on ``days``, holding whole-panel set-aside records
    ``(day, kind)`` for PANEL and ``(day, target_id)`` frames."""
    s = Session(id="s-w17", nights=[_rid(d) for d in days])
    for day, kind in aside:
        s.note_set_aside(PANEL, REASON, night=_key(day), kind=kind,
                         ts=1_790_000_000.0 + day)
    for day, tid, accepted in frames:
        s.frames.append(SessionFrame(night=_rid(day), target_id=tid,
                                     step_id="s1", auto_accepted=accepted))
    return s


# ====================================================== Session.set_aside_streak

class TestTheStreak:
    def test_three_nights_of_centring_records_is_a_streak_of_three(self):
        s = _session([14, 15, 16], aside=[(14, CENTRING), (15, CENTRING),
                                          (16, CENTRING)])
        assert s.set_aside_streak(PANEL) == (3, "centring")

    def test_no_record_is_no_streak(self):
        assert _session([14, 15]).set_aside_streak(PANEL) == (0, None)

    def test_the_streak_is_the_trailing_run_of_nights(self):
        """Nights 14 and 15 set the panel aside, 16 shot it, 17 set it aside
        again: the streak is the one night since it was last shot, not the
        three nights it was set aside in all."""
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (17, CENTRING)],
                     frames=[(16, PANEL, True)])
        assert s.set_aside_streak(PANEL) == (1, "centring")

    def test_a_night_it_banked_a_frame_ends_the_streak(self):
        """A panel set aside on all three nights that nevertheless banked an
        accepted frame on the middle one (it was set aside AFTER a visit
        that shot, say) has not been starved of anything that night.

        Mutant "the total, not the trailing run" (the loop ``break`` at the
        first non-counting night replaced by ``continue``, so every night
        that counts is added up). RED, observed:
        AssertionError: assert (2, 'centring') == (1, 'centring')
        At index 0 diff: 2 != 1
        Use -v to get more diff

        Mutant "no frame banked test" (the ``night in banked`` test dropped
        from the night's verdict). RED, observed:
        AssertionError: assert (3, 'centring') == (1, 'centring')
        At index 0 diff: 3 != 1
        Use -v to get more diff
        """
        s = _session([14, 15, 16],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)],
                     frames=[(15, PANEL, True)])
        assert s.set_aside_streak(PANEL) == (1, "centring")

    def test_only_an_effective_frame_ends_it(self):
        """A frame the quality gate rejected banked nothing (``effective``);
        one the operator overrode to accept did.

        Mutant "any frame banks" (``f.effective()`` dropped from the banked
        set). RED, observed:
        AssertionError: assert (1, 'centring') == (3, 'centring')
        At index 0 diff: 1 != 3
        Use -v to get more diff
        """
        aside = [(14, CENTRING), (15, CENTRING), (16, CENTRING)]
        rejected = _session([14, 15, 16], aside=aside,
                            frames=[(15, PANEL, False)])
        assert rejected.set_aside_streak(PANEL) == (3, "centring")
        rescued = _session([14, 15, 16], aside=aside,
                           frames=[(15, PANEL, False)])
        rescued.frames[0].override = "accept"
        assert rescued.set_aside_streak(PANEL) == (1, "centring")

    def test_another_panels_frame_does_not_end_it(self):
        s = _session([14, 15, 16],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)],
                     frames=[(15, "panel-b", True)])
        assert s.set_aside_streak(PANEL) == (3, "centring")

    def test_a_floor_set_aside_is_not_starvation(self):
        """The panel sank below its floor on the newest night: it is not
        starved of a star, and the nights before it are not a streak that
        reaches tonight.

        Mutant "floor counts" (``"floor"`` added to ``STARVING_KINDS``).
        RED, observed:
        AssertionError: assert (3, 'floor') == (0, None)
        At index 0 diff: 3 != 0
        Use -v to get more diff
        """
        s = _session([14, 15, 16],
                     aside=[(14, CENTRING), (15, CENTRING), (16, "floor")])
        assert s.set_aside_streak(PANEL) == (0, None)
        mid = _session([14, 15, 16],
                       aside=[(14, CENTRING), (15, "rejects"),
                              (16, CENTRING)])
        assert mid.set_aside_streak(PANEL) == (1, "centring")

    def test_a_step_level_record_is_not_the_whole_panel(self):
        """The reject guard set one STEP aside: the panel's other steps were
        shot, so no line may call the panel set aside.

        Mutant "step records count" (the ``step_id is None`` test dropped).
        RED, observed:
        AssertionError: assert (3, 'centring') == (0, None)
        At index 0 diff: 3 != 0
        Use -v to get more diff
        """
        s = _session([14, 15, 16], aside=[(14, CENTRING), (15, CENTRING)])
        s.note_set_aside(PANEL, REASON, night=_key(16), step_id="s1",
                         kind=CENTRING, ts=1_790_000_016.0)
        assert s.set_aside_streak(PANEL) == (0, None)

    def test_a_record_with_no_kind_is_not_starving(self):
        """A hand-edited record, or one written before kinds existed, says
        nothing about why the panel was set aside.

        Mutant "no kind reads as centring" (``rec.get("kind")`` made
        ``rec.get("kind", "centring")``). RED, observed:
        KeyError: 'kind'
        """
        s = _session([14, 15, 16], aside=[(14, CENTRING), (15, CENTRING)])
        s.note_set_aside(PANEL, REASON, night=_key(16))
        assert "kind" not in s.set_aside[-1], "premise: no kind"
        assert s.set_aside_streak(PANEL) == (0, None)

    def test_an_expired_or_cleared_record_still_counts(self):
        """Both stay as history of what the night did: the centring set-aside
        that expired and was tried once more, the one the operator cleared.
        The night is a night the panel was set aside, whichever way it went.
        """
        s = _session([14, 15, 16],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        s.set_aside[1]["expired"] = True
        s.set_aside[2]["cleared"] = True
        assert s.set_aside_streak(PANEL) == (3, "centring")

    def test_a_night_with_no_record_ends_it(self):
        s = _session([14, 15, 16], aside=[(14, CENTRING), (16, CENTRING)])
        assert s.set_aside_streak(PANEL) == (1, "centring")

    def test_a_night_the_session_did_not_run_is_not_a_night(self):
        """Records exist for 14, 15 and 16 but the session ran only on 14
        and 16: the walk is over the nights it RAN (``observing_nights``),
        so 15 is no night at all, neither counted nor a break."""
        s = _session([14, 16], aside=[(14, CENTRING), (15, CENTRING),
                                      (16, CENTRING)])
        assert s.set_aside_streak(PANEL) == (2, "centring")

    def test_a_restart_the_same_night_counts_once(self):
        """Three nights, each with a crash-resume at 23:30: three nights, not
        six runs."""
        s = Session(id="s-w17", nights=[
            _rid(d, h, m) for d in (14, 15, 16) for h, m in ((21, 0), (23, 30))
        ])
        for d in (14, 15, 16):
            s.note_set_aside(PANEL, REASON, night=_key(d), kind=CENTRING)
            s.note_set_aside(PANEL, REASON, night=_key(d), kind=CENTRING)
        assert s.set_aside_streak(PANEL) == (3, "centring")

    def test_another_panels_records_do_not_count(self):
        s = _session([14, 15, 16])
        for d in (14, 15, 16):
            s.note_set_aside("panel-b", REASON, night=_key(d), kind=CENTRING)
        assert s.set_aside_streak(PANEL) == (0, None)

    def test_the_kind_is_the_newest_records(self):
        """The streak began as centring misses and has become guide-start
        failures: it is named for what it is NOW. A later record of a kind
        that is not starving (``_end_pending_expiries`` writes the group's
        own after a centring one) does not replace it."""
        s = _session([14, 15, 16], aside=[(14, CENTRING), (15, CENTRING),
                                          (16, GUIDE_START)])
        assert s.set_aside_streak(PANEL) == (3, "guide_start")
        s.note_set_aside(PANEL, REASON, night=_key(16), kind="group")
        assert s.set_aside_streak(PANEL) == (3, "guide_start")

    def test_the_kinds_are_the_group_rules_words(self):
        """``session.py`` spells the kinds as words and does not import
        ``group_rules``, so its spellings are pinned here to the constants
        the driver writes records with."""
        assert set(STARVING_KINDS) == {CENTRING, GUIDE_START, "deferred"}
        assert STARVED_AFTER_NIGHTS == 3


# ======================================================== GroupRun's kind word

def _run() -> GroupRun:
    return GroupRun({"p0": "1-1", "p1": "1-2"}, max_failed_visits=3)


def _fail(kind: str) -> PanelDeferred:
    return PanelDeferred("the visit did not complete", kind=kind,
                         last_error=REASON)


def _strike(run: GroupRun, kinds) -> None:
    """One pass per kind in which p0 fails that way and p1 guides and shoots,
    so the failure is p0's own, not the rig's or the sky's."""
    for kind in kinds:
        run.visit_outcome("p0", complete=False, exposures=0, accepted=0,
                          deferred=_fail(kind))
        run.visit_outcome("p1", complete=False, exposures=2, accepted=2,
                          guide_started=True)
        run.close_pass()
        run.start_pass()


class TestTheGuideStartKind:
    def test_a_streak_of_guide_start_failures_is_a_guide_start_set_aside(self):
        """Mutant "a guide streak stays deferred" (the GUIDE_START arm of
        ``_count_failure``'s kind removed). RED, observed:
        AssertionError: assert {'p0': 'deferred'} == {'p0': 'guide_start'}
        Differing items:
        {'p0': 'deferred'} != {'p0': 'guide_start'}
        """
        run = _run()
        _strike(run, [GUIDE_START] * 3)
        assert "p0" in run.set_aside, "premise: set aside"
        assert run.set_aside_kind == {"p0": GUIDE_START}

    def test_a_mixed_streak_is_still_deferred(self):
        """CONTROL: a guide failure with anything else in the streak is not
        one cause, and is named as it always was."""
        run = _run()
        _strike(run, [GUIDE_START, "pier_side", GUIDE_START])
        assert run.set_aside_kind == {"p0": "deferred"}

    def test_a_centring_streak_is_still_centring(self):
        run = _run()
        _strike(run, [CENTRING] * 3)
        assert run.set_aside_kind == {"p0": CENTRING}


def _lone() -> GroupRun:
    return GroupRun({"p0": "2-2"}, max_failed_visits=3)


def _hold(run: GroupRun, panels, error: str = ""):
    """One pass in which every panel in ``panels`` misses centring (so the
    pass is held, the sky's or the geometry's) with ``error``, then its
    close. Returns the pass end, and begins the next pass unless the group
    was set aside."""
    for p in panels:
        run.visit_outcome(p, complete=False, exposures=0, accepted=0,
                          deferred=PanelDeferred("centring failed",
                                                 kind=CENTRING,
                                                 last_error=error))
    end = run.close_pass()
    if end.boundary != "set_aside_all":
        run.start_pass()
    return end


class TestTheLastLivePanelIsNamedItself:
    def test_the_last_live_panel_held_six_passes_is_deferred_not_group(self):
        """Mutant "the last live panel is the group's" (``kind=("deferred"
        if ... else "group")`` made ``kind="group"``). RED, observed:
        AssertionError: assert {'p0': 'group'} == {'p0': 'deferred'}
        Differing items:
        {'p0': 'group'} != {'p0': 'deferred'}
        """
        run = _lone()
        end = None
        for _ in range(6):
            end = _hold(run, ["p0"])
        assert end.boundary == "set_aside_all", end
        assert run.set_aside_kind == {"p0": "deferred"}

    def test_several_panels_held_together_are_the_groups(self):
        """CONTROL: six held passes over three panels is the night's (a fog
        bank, a tree line), not any panel's: nobody is named starved.

        Mutant "any held panel is the panel's" (the ``len(live_before) == 1``
        test dropped). RED, observed:
        AssertionError: assert {'deferred'} == {'group'}
        Extra items in the left set:
        'deferred'
        """
        run = GroupRun({"p0": "1-1", "p1": "1-2", "p2": "2-1"},
                       max_failed_visits=3)
        end = None
        for _ in range(6):
            end = _hold(run, ["p0", "p1", "p2"])
        assert end.boundary == "set_aside_all", end
        assert set(run.set_aside_kind.values()) == {"group"}
        assert set(run.set_aside_kind) == {"p0", "p1", "p2"}

    def test_a_rig_side_fault_is_the_groups_even_for_one_panel(self):
        """CONTROL: two held passes in a row giving the identical reason are
        a rig-side fault ("not the sky"), set aside at once. That is not the
        panel's framing, so the lone panel is not named for it.

        Mutant "a rig fault is the panel's" (the ``not same_as_last`` test
        dropped). RED, observed:
        AssertionError: assert {'p0': 'deferred'} == {'p0': 'group'}
        Differing items:
        {'p0': 'deferred'} != {'p0': 'group'}
        """
        run = _lone()
        _hold(run, ["p0"], error="plate solver executable not found")
        end = _hold(run, ["p0"], error="plate solver executable not found")
        assert end.boundary == "set_aside_all", end
        assert run.set_aside_kind == {"p0": "group"}


# ============================================================ the progress answer

def _n(nid, ntype, x=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _graph() -> FlowGraph:
    return FlowGraph(
        nodes=[_n("m", "target", x=100, name="M16", ra="18h 18m 48s",
                  dec="-13 49 00", rotation=30, angle="Rotate to PA",
                  rows=2, cols=2, overlap=25, fovX=FOV[0], fovY=FOV[1],
                  counts="Accepted subs"),
               _n("mc", "capture", x=150, filter="Ha", exposure=300,
                  gain=100, bin="1", count=4, goal=0)],
        edges=[_e("m", "target", "mc", "run")])


def _world(days=(14, 15, 16, 17)):
    graph = _graph()
    compiled = compile_plan(graph, "n")
    plan, _ = to_sequence_plan(compiled, graph, flow_id=FLOW)
    plan = plan.model_copy(update={"count_mode": "accepted"})
    session = Session(id="s-w17", status="dormant", plan=plan,
                      nights=[_rid(d) for d in days], origin="flow",
                      origin_id=FLOW)
    return graph, compiled, plan, session


def _cell(plan, row, col):
    return next(t for t in plan.targets
                if t.mosaic_group and (t.panel_row, t.panel_col) == (row, col))


def _aside(session, target, days, kind=CENTRING):
    for d in days:
        session.note_set_aside(target.id, REASON, night=_key(d), kind=kind,
                               ts=1_790_000_000.0 + d)


def _block(answer):
    return next(b for b in answer["blocks"] if "grid" in b)


def _answer(compiled, plan, session):
    return flow_progress(compiled, plan, session, flow_id=FLOW)


class TestTheProgressAnswer:
    def test_a_panel_set_aside_on_four_nights_carries_starved(self):
        graph, compiled, plan, s = _world()
        p = _cell(plan, 0, 1)
        _aside(s, p, [14, 15, 16, 17])
        block = _block(_answer(compiled, plan, s))
        starved = {e["name"]: e.get("starved") for e in block["panels"]}
        assert starved == {"M16 1-1": None,
                           "M16 1-2": {"nights": 4, "kind": "centring"},
                           "M16 2-1": None, "M16 2-2": None}
        assert set(next(e for e in block["panels"]
                        if e["name"] == "M16 1-2")["starved"]) == {
            "nights", "kind"}

    def test_two_nights_is_not_yet_starvation(self):
        """Every answer short of the threshold is byte-identical to the
        answer for a session that holds no record at all.

        Mutant "threshold of one night" (``_starved``'s ``n <
        STARVED_AFTER_NIGHTS`` made ``n < 1``). RED, observed: the two
        answers' JSON dumps differ (``AssertionError: assert '{"blocks": [...
        "dormant"}}' == '{"blocks": [...: "dormant"}}'``, 517 identical leading
        characters, then the panel's added ``starved`` entry).
        """
        graph, compiled, plan, s = _world(days=(16, 17))
        _aside(s, _cell(plan, 0, 1), [16, 17])
        clean = _world(days=(16, 17))
        assert (json.dumps(_answer(compiled, plan, s), sort_keys=True)
                == json.dumps(_answer(clean[1], clean[2], clean[3]),
                              sort_keys=True))

    def test_a_panel_that_banked_a_frame_is_not_starved(self):
        graph, compiled, plan, s = _world()
        p = _cell(plan, 0, 1)
        _aside(s, p, [14, 15, 16, 17])
        s.frames.append(SessionFrame(night=_rid(16), target_id=p.id,
                                     step_id=p.steps[0].id))
        block = _block(_answer(compiled, plan, s))
        assert all("starved" not in e for e in block["panels"])

    def test_no_session_no_starved(self):
        graph, compiled, plan, s = _world()
        answer = flow_progress(compiled, plan, None, flow_id=FLOW)
        assert all("starved" not in e for b in answer["blocks"]
                   for e in b["panels"])

    def test_the_free_text_reason_never_reaches_the_answer(self):
        """The answer is served to a viewer. The record's reason can carry a
        solver's error; nothing of it is in the answer, and the entry holds
        a count and one of three fixed words, no time."""
        graph, compiled, plan, s = _world()
        _aside(s, _cell(plan, 0, 1), [14, 15, 16, 17])
        dumped = json.dumps(_answer(compiled, plan, s))
        assert "RSN-7731" not in dumped and "marker" not in dumped
        assert "1790000" not in dumped, "no record time is served"

    def test_the_helper_names_one_of_the_three_kinds(self):
        graph, compiled, plan, s = _world()
        p = _cell(plan, 1, 1)
        _aside(s, p, [14, 15], kind=CENTRING)
        _aside(s, p, [16, 17], kind=GUIDE_START)
        assert progress_mod._starved(s, p) == {"nights": 4,
                                               "kind": "guide_start"}
        assert progress_mod._starved(None, p) is None
        assert progress_mod._starved(s, None) is None


# =============================================================== the Campaign

def _rows_progress(*panels, node="m"):
    """A progress answer of the shape ``flow_progress`` gives, one mosaic
    block, for the Campaign's own half to read."""
    return {"blocks": [{"node_id": node, "panels": [
        {"name": n, "row": 0, "col": i, "banked": 0, "owed": 4, "total": 4,
         **({"starved": s} if s is not None else {})}
        for i, (n, s) in enumerate(panels)], "skipped": []}]}


def _mosaic_only() -> FlowGraph:
    return _graph()


def _with_pool() -> FlowGraph:
    g = _graph()
    g.nodes.append(_n("pool", "pool", x=0, members="M31,M33", quota=5))
    return g


SENTENCE = ("Panel M16 1-2 has been set aside on 4 nights running (no star "
            "to solve on), so it will not finish without a change to its "
            "framing or this block's setting.")


class TestTheCampaignNamesIt:
    def test_the_note_names_the_panel_and_the_nights(self):
        """Mutant "no sentence" (``_panels_clause`` appends nothing for a
        starved row). RED, observed:
        AssertionError: 0 of 2 mosaic panels done, 0 of 8 subs captured.
          Nights to finish are not forecast - clear-sky prediction that far
          out is not somet...
        assert "Panel M16 1-2 has been set aside on 4 nights running (no
          star to solve on), so it will not finish without a change to its
          framing or this b...

        Mutant "row without starved" (``_panel_rows`` does not copy the
        panel's ``starved`` onto its row). RED, observed:
        AssertionError: 0 of 2 mosaic panels done, 0 of 8 subs captured.
          Nights to finish are not forecast - clear-sky prediction that far
          out is not somet...
        assert "Panel M16 1-2 has been set aside on 4 nights running (no
          star to solve on), so it will not finish without a change to its
          framing or this b...
        """
        prog = _rows_progress(("M16 1-1", None),
                              ("M16 1-2", {"nights": 4, "kind": "centring"}))
        c = _campaign(_mosaic_only(), None, lambda: prog)
        assert SENTENCE in c["note"], c["note"]
        assert c["starved"] == [{"block": "m", "name": "M16 1-2",
                                 "nights": 4, "kind": "centring"}]

    @pytest.mark.parametrize("kind, phrase", [
        ("centring", "no star to solve on"),
        ("guide_start", "no guide star"),
        ("deferred", "failed to centre or guide"),
    ])
    def test_each_kind_has_its_fixed_phrase(self, kind, phrase):
        prog = _rows_progress(("M16 1-2", {"nights": 3, "kind": kind}))
        note = _campaign(_mosaic_only(), None, lambda: prog)["note"]
        assert (f"Panel M16 1-2 has been set aside on 3 nights running "
                f"({phrase}), so it will not finish") in note, note

    def test_a_flow_with_nothing_starved_is_unchanged(self):
        prog = _rows_progress(("M16 1-1", None), ("M16 1-2", None))
        c = _campaign(_mosaic_only(), None, lambda: prog)
        assert "starved" not in c
        assert "running" not in c["note"] and "set aside" not in c["note"]
        assert all("starved" not in r for r in c["panels"])

    def test_each_starved_panel_has_its_own_sentence_in_grid_order(self):
        prog = _rows_progress(
            ("M16 1-1", {"nights": 5, "kind": "guide_start"}),
            ("M16 1-2", None),
            ("M16 2-2", {"nights": 3, "kind": "centring"}))
        c = _campaign(_mosaic_only(), None, lambda: prog)
        first = c["note"].index("Panel M16 1-1 has been set aside on 5")
        second = c["note"].index("Panel M16 2-2 has been set aside on 3")
        assert first < second and "M16 1-2 has been set aside" not in c["note"]
        assert [s["name"] for s in c["starved"]] == ["M16 1-1", "M16 2-2"]

    def test_a_pool_beside_a_mosaic_says_it_after_its_own_note(self):
        prog = _rows_progress(("M16 1-2", {"nights": 4, "kind": "centring"}))
        c = _campaign(_with_pool(), None, lambda: prog)
        assert c["has_pool"] is True
        assert c["note"].endswith(SENTENCE), c["note"]
        assert c["starved"][0]["name"] == "M16 1-2"

    def test_an_entry_the_server_would_never_send_is_ignored(self):
        """A kind that is none of the three fixed words, a count that is no
        number or below one: nothing is said, and a free-text kind cannot
        reach the sentence."""
        for bad in ({"nights": 4, "kind": REASON}, {"nights": "four",
                    "kind": "centring"}, {"nights": 0, "kind": "centring"},
                    {"kind": "centring"}, "centring", 4):
            prog = _rows_progress(("M16 1-2", bad))
            c = _campaign(_mosaic_only(), None, lambda: prog)
            assert "starved" not in c, bad
            assert "RSN-7731" not in c["note"], bad

    def test_a_skipped_panel_is_never_starved(self):
        prog = _rows_progress(("M16 1-1", None))
        prog["blocks"][0]["skipped"] = [
            {"name": "M16 1-2", "row": 0, "col": 1, "banked": 0,
             "starved": {"nights": 9, "kind": "centring"}}]
        c = _campaign(_mosaic_only(), None, lambda: prog)
        assert "starved" not in c and "running" not in c["note"]

    def test_no_progress_claims_nothing(self):
        c = _campaign(_mosaic_only(), None, None)
        assert "starved" not in c and c["has_progress"] is False

    def test_end_to_end_from_the_session(self):
        """The real chain: a session's records, ``flow_progress``, the
        Campaign. Four nights set aside names the panel; the panel banking a
        frame on the second of them (tonight is the fourth) leaves it a
        streak of two, which names nothing."""
        graph, compiled, plan, s = _world()
        p = _cell(plan, 0, 1)
        _aside(s, p, [14, 15, 16, 17])
        c = _campaign(graph, None, lambda: _answer(compiled, plan, s))
        assert SENTENCE in c["note"], c["note"]
        assert c["starved"] == [{"block": "m", "name": "M16 1-2",
                                 "nights": 4, "kind": "centring"}]
        assert "RSN-7731" not in json.dumps(c)

        s.frames.append(SessionFrame(night=_rid(15), target_id=p.id,
                                     step_id=p.steps[0].id))
        again = _campaign(graph, None, lambda: _answer(compiled, plan, s))
        assert "starved" not in again and "running" not in again["note"]


# ================================================ the clocked simulator, 3 nights

def _never_centres(label):
    def goto(who, n, result):
        if who == f"{GROUP_NAME} {label}":
            return {**result, "centered": False, "error_arcmin": None}
        return result
    return goto


async def _night(hub, monkeypatch, plan, *, t0, session=None, **kw) -> Night:
    night = Night(hub, monkeypatch, t0=t0, **kw)
    try:
        night.done = await night.run(
            plan, **({"session": session} if session is not None else {}))
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _sim_progress(plan, session):
    """The progress answer's shape for the harness plan's panels, with the
    entry the real ``_starved`` gives each."""
    panels = []
    for t in plan.targets:
        entry = {"name": t.name, "row": t.panel_row, "col": t.panel_col,
                 "banked": 0, "owed": 1, "total": 1}
        starved = progress_mod._starved(session, t)
        if starved is not None:
            entry["starved"] = starved
        panels.append(entry)
    return {"blocks": [{"node_id": "m", "panels": panels, "skipped": []}]}


async def test_a_panel_that_fails_its_solve_three_nights_is_named(
        group_hub, monkeypatch):
    """The issue's own test, on the clocked simulator: panel 2-2 never
    centres on three simulated nights, the real engine sets it aside each
    night and records it, and the Campaign names it. After two nights it
    names nothing; the third makes three.

    WHAT THE ENGINE WRITES, observed, and why the kind is "deferred" and not
    "centring". Night one: 2-2's own three strikes (a ``centring`` record,
    which then expires and is tried once more) and, 2-2 being the only panel
    left live, the held-pass rule's set-aside. Nights two and three: the
    other panels are complete, so 2-2 is the mosaic's LAST LIVE PANEL from the
    first visit, its misses are held and not struck, and the held-pass rule
    (six passes) is the ONLY record the night writes. That record was kind
    ``group`` until this work, the word for the whole mosaic going quiet, and
    a streak read off it would have named the panel on night one and never
    again: the mosaic sitting at five panels of six, which is the case the
    issue is about, was invisible.

    Mutant "the last live panel is the group's" (``_apply_held_pass_rule``
    recording ``kind="group"`` whatever was live). RED, observed:
    AssertionError: [{'expired': True, 'kind': 'centring', 'night':
          '2026-09-01', 'reason': 'centring failed on 2-2 on 3 consecutive
          visit...en held fo...
        assert ['centring', ...oup', 'group'] == ['centring', ...',
          'deferred']
        At index 1 diff: 'group' != 'deferred'
    """
    session = None
    notes = []
    for day in range(3):
        night = await _night(group_hub, monkeypatch, grid_plan(),
                             t0=T0 + day * 86400.0, session=session,
                             goto=_never_centres("2-2"))
        assert night.done
        session = night.stored
        plan = grid_plan()
        notes.append(_campaign(_graph(), None,
                               lambda: _sim_progress(plan, session)))
    assert len(session.observing_nights()) == 3, session.nights
    assert [r["kind"] for r in session.set_aside] == [
        "centring", "deferred", "deferred", "deferred"], session.set_aside
    assert session.set_aside_streak("p11") == (3, "deferred")
    assert "starved" not in notes[0] and "starved" not in notes[1]
    assert notes[2]["starved"] == [{"block": "m", "name": "M31 2-2",
                                    "nights": 3, "kind": "deferred"}]
    assert ("Panel M31 2-2 has been set aside on 3 nights running (failed to "
            "centre or guide)") in notes[2]["note"], notes[2]["note"]
    assert all(session.set_aside_streak(t) == (0, None)
               for t in ("p00", "p01", "p10")), "the others were shot"
