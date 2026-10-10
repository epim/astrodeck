# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A night the session ran but never reached a starved panel on does not reset
its streak (#970; backlog WP-192, wave 21).

THE DEFECT. ``Session.set_aside_streak`` is the count behind the Campaign's
"panel 1-3 has been set aside on 3 nights running" and, through
``earlier_starved_nights``, behind the group driver's held-pass shortening
(#835). It walked the observing nights from the newest and stopped at the
first that had no whole-panel starving record. A night the session ran but on
which the panel was never visited (clouded out before its turn, cut by the
dawn, spent on the mosaic's other panels) holds no record for it, so it
stopped the walk: a panel set aside on three nights and then not reached on
the fourth read ``(0, None)``, the Campaign dropped it, and the next start
gave it the full six held passes (an hour of sky) again. Absence of evidence
was being read as evidence of recovery. #942 had closed the same gap for
tonight alone, with a clock; a closed night that never reached the panel was
left a break on purpose, and that is what this reverses.

THE FIX. A night that holds nothing of the panel, no set-aside record of any
kind or step and no frame of any grade, is STEPPED OVER: it neither counts
nor ends the streak. The streak is a run of the nights the panel was tried
on. Any night it WAS reached on and did not count on (a frame banked, a
record of a kind outside the walk's, one step set aside, no kind) still ends
it, as before. That is one rule for tonight before its record lands and for
a night that closed untouched, so the card, the Campaign and the driver read
one number and none of them needs a clock.

THE MUTANTS, each applied to ``Session.set_aside_streak`` from a byte backup
of ``astrodeck/sequence/session.py``, restored with a byte copy and
md5-compared, never through git (#254). The cases name them by letter.

    A  "an untouched night breaks the walk": the ``reached`` test made
       ``if False`` (the strict walk of #970, the defect itself).
    B  "an untouched night counts": the step-over ``continue`` made
       ``streak += 1`` then ``continue``.
    C  "a shot night is stepped over too": the frames half of ``reached``
       made empty.
    D  "only an effective frame reaches the night": ``f.effective()`` added
       to that half.
    E  "any panel's record reaches the night": the ``target_id`` test of
       the set-aside half dropped.
    F  "any panel's frame reaches the night": the ``target_id`` test of the
       frames half dropped.
    G  "only a starving record reaches the night": the set-aside half
       filtered to ``r.get("kind") in STARVING_KINDS``.
    H  "only a whole-panel record reaches the night": the same half
       filtered to ``r.get("step_id") is None``.
    I  "a night that does not count is stepped over too": the loop's
       ``break`` made ``continue``.
    J  "a frame's night is its report id": ``report_night`` dropped from
       the frames half of ``reached``.

THE CLOCKS ARE PINNED (#682), as in the w17 and w20 files whose helpers this
reuses: the nights are stamped report ids at 21:00 of July dates and each
record's night is the ``night_key`` of that same instant. Nothing here is a
site.
"""
from __future__ import annotations

import json
import time

import pytest

import astrodeck.api.app as app_module
from _group_harness import T0, group_hub, group_store  # noqa: F401
from astrodeck.auth import principal_for_role, set_active_provider
from astrodeck.config import Site
from astrodeck.events import night_key
from astrodeck.flows.progress import flow_progress
from astrodeck.flows.tonight import _campaign
from astrodeck.persist import write_json_atomic
from astrodeck.sequence.group_rules import CENTRING, HELD_PASS_KINDS
from astrodeck.sequence.session import (STARVED_AFTER_NIGHTS, Session,
                                        SessionFrame, session_store)
from test_flows_progress_route import (SITE_A, SITE_B,
                                       TARGET_POOL_AND_MOSAIC, _Clock,
                                       _compiled, _Fixed, _local)
from test_flows_progress_route import (_restore_provider,  # noqa: F401
                                       api)  # noqa: F401 (fixtures)
from test_w17_starved_panel import (FLOW, PANEL, REASON, _aside, _block,
                                    _cell, _key, _rid, _session, _world)
from test_w19_starved_last_panel import (DAY, _hops, _name, _night,
                                         _three_starved_nights)

STARVED = {"nights": 3, "kind": "centring"}


def _clock(day: int, hour: int = 14, minute: int = 0) -> float:
    """The pinned instant July ``day`` 2026 at ``hour:minute`` local."""
    return time.mktime((2026, 7, day, hour, minute, 0, 0, 0, -1))


def _earlier(s: Session, day: int) -> int:
    return s.earlier_starved_nights(PANEL, night=_key(day),
                                    kinds=HELD_PASS_KINDS)


# ===================================================================== Session

class TestACloudOutIsNotARecovery:
    def test_the_issues_scenario_a_cloud_out_after_three_starved_nights(self):
        """Records on nights 14 to 16; night 17 runs and clouds out before
        the panel's turn (the session holds its run id and nothing of the
        panel); the next run starts on night 20. The panel is still set
        aside on three nights, however long ago the third was, and the group
        driver reads the same three at night 20's start: the number the
        Campaign must agree with. The record landing makes it four.

        RED under mutant A, observed, at the first read:

            AssertionError: assert (0, None) == (3, 'centring')

        and under mutant B, observed at the same line:

            AssertionError: assert (4, 'centring') == (3, 'centring')
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        assert s.set_aside_streak(PANEL) == (3, CENTRING)
        s.nights.append(_rid(20))
        assert s.set_aside_streak(PANEL) == (3, CENTRING), "night 20 started"
        assert _earlier(s, 20) == 3, "the driver agrees at that start"
        s.note_set_aside(PANEL, REASON, night=_key(20), kind=CENTRING)
        assert s.set_aside_streak(PANEL) == (4, CENTRING)

    def test_a_cloud_out_between_two_set_asides_is_stepped_over(self):
        """The middle night is the one that was clouded out: the panel was
        set aside on the nights it was tried, three of them.

        RED under mutant A, observed:

            AssertionError: assert (1, 'centring') == (3, 'centring')

        and under mutant B, observed:

            AssertionError: assert (4, 'centring') == (3, 'centring')
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (17, CENTRING)])
        assert s.set_aside_streak(PANEL) == (3, CENTRING)

    def test_several_cloud_outs_in_a_row_are_all_stepped_over(self):
        """A week of weather: three set-aside nights, then four nights the
        session ran and reached nothing of the panel on.

        RED under mutant A, observed:

            AssertionError: assert (0, None) == (3, 'centring')

        and under mutant B, observed:

            AssertionError: assert (7, 'centring') == (3, 'centring')
        """
        s = _session([14, 15, 16, 17, 18, 19, 20],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        assert s.set_aside_streak(PANEL) == (3, CENTRING)
        assert _earlier(s, 20) == 3

    def test_a_cloud_out_does_not_extend_the_count(self):
        """Neither extend nor reset: two set-aside nights with any number of
        cloud-outs around them are two, short of the bar, and the third
        night the panel is actually set aside on makes three.

        RED under mutant B, observed:

            AssertionError: assert (6, 'centring') == (2, 'centring')

        and under mutant A, observed at the same line:

            AssertionError: assert (0, None) == (2, 'centring')
        """
        s = _session([14, 15, 16, 17, 18, 19],
                     aside=[(15, CENTRING), (16, CENTRING)])
        assert s.set_aside_streak(PANEL) == (2, CENTRING)
        assert 2 < STARVED_AFTER_NIGHTS, "premise: two is not starved"
        s.nights.append(_rid(20))
        s.note_set_aside(PANEL, REASON, night=_key(20), kind=CENTRING)
        assert s.set_aside_streak(PANEL) == (3, CENTRING)

    @pytest.mark.parametrize("auto_accepted", [True, False],
                             ids=["accepted", "rejected"])
    def test_a_night_it_was_shot_on_still_ends_it_across_a_cloud_out(
            self, auto_accepted):
        """CONTROL for the other direction: the panel was set aside on 14
        and 15, shot on 16 (a frame and no record; the rejected one was
        centred and exposed, which is reaching it), clouded out on 17 and set
        aside on 18. The streak is the one night since it was shot. Stepping
        over the cloud-out must not carry the walk through the night it
        shot.

        RED under mutant C (both ids) and under mutant D (the rejected id),
        observed:

            AssertionError: assert (3, 'centring') == (1, 'centring')

        and under mutant J (both ids), the same line; under mutant B (both
        ids), observed:

            AssertionError: assert (2, 'centring') == (1, 'centring')
        """
        s = _session([14, 15, 16, 17, 18],
                     aside=[(14, CENTRING), (15, CENTRING), (18, CENTRING)],
                     frames=[(16, PANEL, auto_accepted)])
        assert s.set_aside_streak(PANEL) == (1, CENTRING)

    def test_the_night_spent_on_the_neighbours_is_not_a_night_it_was_reached(
            self):
        """A mosaic's night can be spent entirely on its other panels: the
        neighbour is set aside and shot on the 17th and this panel is not
        reached. The neighbour's records and frames say nothing of this one,
        which is the night the flag exists for.

        RED under mutant E (the neighbour's record), observed:

            AssertionError: panel-b's record
            assert (0, None) == (3, 'centring')

        and under mutant F (the neighbour's frame), observed:

            AssertionError: panel-b's frame
            assert (0, None) == (3, 'centring')
        """
        s = _session([14, 15, 16, 17],
                     aside=[(14, CENTRING), (15, CENTRING), (16, CENTRING)])
        s.note_set_aside("panel-b", REASON, night=_key(17), kind=CENTRING)
        assert s.set_aside_streak(PANEL) == (3, CENTRING), "panel-b's record"
        s.frames.append(SessionFrame(night=_rid(17), target_id="panel-b",
                                     step_id="s1", auto_accepted=True))
        assert s.set_aside_streak(PANEL) == (3, CENTRING), "panel-b's frame"

    @pytest.mark.parametrize("why", ["floor", "step", "no kind"])
    def test_a_night_it_was_reached_on_for_another_reason_still_ends_it(
            self, why):
        """A night the panel WAS reached on and set aside for the sky's
        reason (``floor``), for one step only, or by a record that names no
        kind, is not a night to step over, and a cloud-out after it changes
        nothing: set aside on 14 and 15, reached on 16, clouded out on 17,
        set aside on 18 is one night of starvation.

        RED under mutant I (all three ids), observed:

            AssertionError: assert (3, 'centring') == (1, 'centring')

        and under mutant G (``floor`` and ``no kind``) and mutant H
        (``step``), the same line; under mutant B (all three ids), observed:

            AssertionError: assert (2, 'centring') == (1, 'centring')
        """
        s = _session([14, 15, 16, 17, 18],
                     aside=[(14, CENTRING), (15, CENTRING), (18, CENTRING)])
        extra = {"floor": dict(kind="floor"),
                 "step": dict(kind=CENTRING, step_id="s1"),
                 "no kind": {}}[why]
        s.note_set_aside(PANEL, REASON, night=_key(16), **extra)
        assert s.set_aside_streak(PANEL) == (1, CENTRING)

    def test_the_driver_steps_over_the_cloud_out_too(self):
        """The group driver reads ``earlier_starved_nights`` at every start.
        Records on nights 14, 15 and 17, a cloud-out on the 16th and another
        on the 18th: at night 19's start it reads the three nights the panel
        was tried on, and it reads nothing from the night it is starting.

        RED under mutant A, observed:

            AssertionError: assert 0 == 3
             +  where 0 = _earlier(Session(...), 19)

        and under mutant B, observed:

            AssertionError: assert 5 == 3
             +  where 5 = _earlier(Session(...), 19)
        """
        s = _session([14, 15, 16, 17, 18, 19],
                     aside=[(14, CENTRING), (15, CENTRING), (17, CENTRING)])
        assert _earlier(s, 19) == 3
        s.note_set_aside(PANEL, REASON, night=_key(19), kind=CENTRING)
        assert _earlier(s, 19) == 3, "tonight's own record is not earlier"


# =========================================================== the progress card

def _named(answer, name="M16 1-2"):
    entry = next(e for e in _block(answer)["panels"] if e["name"] == name)
    return entry.get("starved")


class TestTheCampaignAfterACloudOut:
    def test_the_flag_and_the_sentence_stay_until_the_panel_is_reached(self):
        """Through the real chain (a session, ``flow_progress``, the
        Campaign): records on nights 14 to 16, night 17 runs and clouds out,
        and the next run starts on night 20. Read with the clock of each
        moment (the route hands one in) the card names the panel at three
        nights while night 17 is open, on the 18th, on the 19th and when
        night 20's run starts, so the flag never drops out and never
        reappears; the set-aside landing makes it four, and a frame ends it.

        RED under mutant A, observed:

            AssertionError: assert [0, 0, 0, 0] == [3, 3, 3, 3]

        (#942's open-night rule, which this replaced, read [3, 0, 0, 0]
        here: the night still open kept the flag and the closed one dropped
        it), and under mutant B, observed:

            AssertionError: assert [4, 4, 4, 5] == [3, 3, 3, 3]

        and under mutant C, at the last line, observed:

            AssertionError: it shot, so it is not starved
            assert {'kind': 'centring', 'nights': 3} is None
        """
        graph, compiled, plan, s = _world()
        p = _cell(plan, 0, 1)
        _aside(s, p, [14, 15, 16])

        def card(day, hour=14):
            return flow_progress(compiled, plan, s, flow_id=FLOW,
                                 now=_clock(day, hour))

        def nights(day, hour=14):
            e = _named(card(day, hour))
            return 0 if e is None else e["nights"]

        s.status = "active"
        reads = [nights(17, 23)]
        s.status = "dormant"                        # night 17 ended unreached
        reads += [nights(18), nights(19)]
        s.nights.append(_rid(20))                   # night 20: a run starts
        s.status = "active"
        reads.append(nights(20, 22))
        assert reads == [3, 3, 3, 3]
        bare = flow_progress(compiled, plan, s, flow_id=FLOW)
        assert _named(bare) == STARVED, "and it needs no clock"
        c = _campaign(graph, None, lambda: card(20, 22))
        assert c["starved"] == [{"block": "m", "name": "M16 1-2", **STARVED}]
        assert ("Panel M16 1-2 has been set aside on 3 nights running"
                in c["note"]), c["note"]
        assert "RSN-7731" not in str(c), "the reason never reaches the answer"

        _aside(s, p, [20])
        assert _named(card(20, 23)) == {"nights": 4, "kind": "centring"}
        s.frames.append(SessionFrame(night=_rid(20), target_id=p.id,
                                     step_id=p.steps[0].id))
        assert _named(card(21)) is None, "it shot, so it is not starved"

    def test_two_set_asides_and_a_cloud_out_are_still_not_starvation(self):
        """CONTROL: a cloud-out does not lower the bar. Two nights set aside
        with one clouded out around them is a streak of two and the card
        carries no ``starved`` at all.

        RED under mutant B, observed:

            AssertionError: {'M16 1-1': None, 'M16 1-2': {'kind':
            'centring', 'nights': 3}, 'M16 2-1': None, 'M16 2-2': None}
            assert False
        """
        graph, compiled, plan, s = _world(days=(15, 16, 17))
        _aside(s, _cell(plan, 0, 1), [15, 16])
        answer = flow_progress(compiled, plan, s, flow_id=FLOW,
                               now=_clock(19))
        entries = {e["name"]: e.get("starved")
                   for e in _block(answer)["panels"]}
        assert all(v is None for v in entries.values()), entries


# ======================================================================= route

def _seed_cloud_out(fid: str) -> Session:
    """The TARGET + POOL + mosaic flow's session, dormant, which set the
    mosaic's panel 1-2 aside on nights 14 to 16 and ran night 17 without
    reaching it, written as the file ``SessionStore.save`` writes."""
    _c, plan = _compiled(TARGET_POOL_AND_MOSAIC, fid)
    panel = next(t for t in plan.targets if t.mosaic_group
                 and (t.panel_row, t.panel_col) == (0, 1))
    s = Session(name=f"{fid}@100", created_ts=100.0, updated_ts=100.0,
                status="dormant", plan=plan,
                nights=[_rid(d) for d in (14, 15, 16, 17)],
                origin="flow", origin_id=fid)
    for d in (14, 15, 16):
        s.note_set_aside(panel.id, REASON, night=_key(d), kind=CENTRING,
                         ts=1_790_000_000.0 + d)
    path = session_store._path(s.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, s.model_dump(), backup=False)
    return s


class TestTheRouteAfterACloudOut:
    @pytest.mark.parametrize("role", ["viewer", "admin"])
    async def test_it_names_the_panel_and_nothing_moves_with_the_site(
            self, api, role, monkeypatch):
        """The wire, read the day after the cloud-out under two synthetic
        sites: the mosaic's panel 1-2 carries the three nights, a count and
        one word, and no reason and no time of the record reaches the body,
        which is byte-identical when the site moves. The route's clock keys
        a night (``continue_night``) and the starved count is read from the
        ledger alone, so a viewer may read it (spec 6.9, #19, #971).

        RED under mutant A, for both roles, observed:

            AssertionError: the viewer reads the flag
            assert {'M16 1-1': N...16 2-1': None} == {'M16 1-1': N...16 2-1':
            None}
              Omitting 2 identical items, use -vv to show

        (the panel's entry is None where the three nights should be), and
        under mutant B, for both roles, the same lines with 4 nights.
        """
        fid = await api.save_flow(TARGET_POOL_AND_MOSAIC)
        _seed_cloud_out(fid)
        monkeypatch.setattr(app_module, "time",
                            _Clock(_local(2026, 7, 18, 14)))
        set_active_provider(_Fixed(principal_for_role(role)))
        bodies = []
        for lat, lon in (SITE_A, SITE_B):
            api.store.set_site(Site(name="fixture", latitude=lat,
                                    longitude=lon, elevation_m=10.0,
                                    is_default=False))
            assert app_module.hub.site["latitude"] == lat, (
                "premise: the hub reads the site this test configured")
            r = await api.progress(fid)
            assert r.status_code == 200, r.text
            bodies.append(r.content)
        got = json.loads(bodies[0])
        mosaic = next(b for b in got["blocks"] if "grid" in b)
        flags = {p["name"]: p.get("starved") for p in mosaic["panels"]}
        assert flags == {"M16 1-1": None, "M16 1-2": STARVED,
                         "M16 2-1": None}, "the viewer reads the flag"
        assert got["session"]["nights"] == 4, "premise: the cloud-out ran"
        text = bodies[0].decode("utf-8")
        for needle in ("RSN-7731", "1790000", "reason"):
            assert needle not in text, f"the wire carries {needle!r}"
        assert bodies[0] == bodies[1], "the body moved with the site"


# ====================================================================== engine

def _cloud_out_id(k: int) -> str:
    """The report id a run started on the harness's night ``k`` is minted
    with (``SessionReporter._make_id``'s shape: a slug and the local start
    stamp), for a run that is then clouded out before it reaches anything."""
    return "mosaic-" + time.strftime("%Y%m%d-%H%M%S",
                                     time.localtime(T0 + k * DAY))


async def test_a_cloud_out_does_not_give_a_starved_panel_its_six_passes_back(
        group_hub, monkeypatch):
    """THE COST, end to end on the clocked simulator. Panel 1-1 never
    centres; 1-2 is done after the first night; nights one to three each
    spend the full six held passes on 1-1 and set it aside. Night four runs
    and clouds out before the panel's turn: the session holds its run id and
    nothing of the panel. Night five finds three starved nights behind it
    (the cloud-out is none of them and breaks nothing): ONE hop, one pass,
    then 1-1 is set aside for the night, as on the fourth night when no
    cloud-out intervened (test_w19_starved_last_panel). Read as a break, the
    cloud-out gave the panel the full hour of sky again.

    RED under mutant A, observed:

        AssertionError: night five hopped 1-1 at [0.0, 600.0, 1200.0,
        1800.0, 2400.0, 3000.0]
        assert 6 == 1

    and under mutant B (the cloud-out counted as a set-aside night), at the
    last line, observed:

        AssertionError: the streak goes on, with the cloud-out neither
        counted nor a break
        assert (5, 'deferred') == (4, 'deferred')
    """
    session, _first = await _three_starved_nights(group_hub, monkeypatch)
    session.nights.append(_cloud_out_id(STARVED_AFTER_NIGHTS))
    assert len(session.observing_nights()) == STARVED_AFTER_NIGHTS + 1, (
        "premise: the cloud-out is a night the session ran")
    cloud_out = night_key(T0 + STARVED_AFTER_NIGHTS * DAY)
    assert [r for r in session.set_aside if r["night"] == cloud_out] == [], (
        "premise: and it holds nothing of the panel")

    fifth = await _night(group_hub, monkeypatch, STARVED_AFTER_NIGHTS + 1,
                         session)

    hops = _hops(fifth, "1-1")
    assert len(hops) == 1, f"night five hopped 1-1 at {hops}"
    assert fifth.shots() == []
    assert fifth.said("a restart tonight does not retry it, the next night "
                      "does"), fifth.lines[-4:]
    assert fifth.stored.set_aside_streak("p00") == (4, "deferred"), (
        "the streak goes on, with the cloud-out neither counted nor a break")


async def test_a_starved_panel_that_centres_after_a_cloud_out_is_shot(
        group_hub, monkeypatch):
    """CONTROL: stepping over a cloud-out does not touch a panel that
    centres. The same history, then the panel's trouble clears on night five:
    it is shot in full, takes no set-aside, and the streak is over.

    RED under mutant C (a shot night stepped over too), observed, at the
    streak the shot should have ended:

        AssertionError: assert (3, 'deferred') == (0, None)

    and under mutants I and J, the same line. The panel is shot either way;
    what the case holds is that a cloud-out earlier in the history does not
    carry the streak through the night the panel recovered on.
    """
    session, _first = await _three_starved_nights(group_hub, monkeypatch)
    session.nights.append(_cloud_out_id(STARVED_AFTER_NIGHTS))

    fifth = await _night(group_hub, monkeypatch, STARVED_AFTER_NIGHTS + 1,
                         session, centres=True)

    assert [t for t, _f in fifth.shots()] == [_name("1-1")] * 6
    assert [r for r in fifth.stored.set_aside
            if r["night"] == night_key(fifth.t0)] == []
    assert fifth.stored.owed() == 0
    assert fifth.stored.set_aside_streak("p00") == (0, None)
