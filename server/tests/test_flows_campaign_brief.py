"""The CAMPAIGN tab and the STORY tab's generated brief.

Both are new in the 2026-08-14 export and both are specified in PROSE only - the
export changed no screenshot, so there is no reference render for either. That
makes the tests the only thing standing between the spec and a plausible
invention, and two inventions were available here:

* the prototype hard-codes each pool member's progress (`fr = [1, 0.51, 0.1, …]`)
  and the completion estimate ("~6 clear nights"). The README says production
  reads the first from the session ledger and the second from the scheduler.
  The ledger exists. THE PROJECTION DOES NOT - nothing on this server forecasts
  clear nights - so the number is not printed at all.
* the brief is prose, and prose is the easiest thing in this codebase to write
  confidently and wrongly. Every sentence here is asserted against the params it
  claims to quote, so a sentence that stops tracking its param fails rather than
  merely reading well.
"""
from __future__ import annotations

import pytest

from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.tonight import (_campaign, brief,
                                     frames_by_target_from_reports)


def _ex(flow_id: str):
    return next(e for e in examples() if e.id == flow_id).graph


class _FB:
    def __init__(self, filter, frames):        # noqa: A002
        self.filter, self.frames = filter, frames


class _TB:
    def __init__(self, name, by_filter):
        self.name, self.by_filter = name, by_filter


class _Rep:
    def __init__(self, targets):
        self.targets = targets


# ============================================================== the brief

class TestTheBriefQuotesTheGraph:
    def test_every_number_comes_from_a_param(self):
        """"Edit a param, the sentence changes" is the export's own rule, so a
        changed param must be visible in the prose and the OLD value gone."""
        g = _ex("example-cycle")
        before = brief(g)
        assert "40%" in before, before

        cw = next(n for n in g.nodes if n.type == "cloudwatch")
        cw.params["threshold"] = 25
        after = brief(g)
        assert "25%" in after and "40%" not in after, after

    def test_the_cycle_table_is_spelled_out_slot_by_slot(self):
        b = brief(_ex("example-cycle"))
        for slot in ("L 60 s × 45", "Ha 180 s × 45", "SII 180 s × 45"):
            assert slot in b, f"{slot!r} missing from: {b}"

    def test_the_cooler_gate_is_named_in_the_resume_checklist(self):
        """The operator's way of noticing the gate is missing. It is the first
        step of the checklist and the export puts it there deliberately."""
        b = brief(_ex("example-campaign"))
        assert "re-cools the sensor to setpoint and waits for it to stabilize" in b
        i_cool = b.index("re-cools the sensor")
        assert i_cool < b.index("restores the filter") < b.index("re-centers")

    def test_skipping_the_cooler_check_removes_the_sentence(self):
        g = _ex("example-campaign")
        hold = next(n for n in g.nodes if n.type == "holdresume")
        hold.params["cooler"] = "Skip check"
        assert "re-cools the sensor" not in brief(g)

    def test_a_campaign_says_it_comes_back_and_a_single_night_does_not(self):
        camp, one = brief(_ex("example-campaign")), brief(_ex("example-m16"))
        assert "re-arms at the next dusk" in camp
        assert "re-arms" not in one

    def test_the_advance_wire_changes_what_the_report_sentence_says(self):
        assert "the pool advances to the next best" in brief(_ex("example-campaign"))
        assert "A session report is appended when the run ends." in brief(_ex("example-m16"))

    def test_a_sentence_appears_only_when_its_node_does(self):
        """EAA has no dusk, no guider, no cloud watch, no safety monitor."""
        b = brief(_ex("example-eaa"))
        for absent in ("arms at astronomical dusk", "guides with", "cloud cover",
                       "Rain, wind, or power"):
            assert absent not in b, f"{absent!r} in a flow with no such node: {b}"

    def test_the_paragraph_never_opens_with_a_dangling_it_then(self):
        """PROTOTYPE DEFECT, fixed. `brief()` opens the target sentence with a
        fixed "It then", which reads correctly after the arming sentence and is
        broken English without one. EAA has no DUSK WINDOW, so its brief began
        "It then arms M27 - Dumbbell." with nothing for "it" to refer to."""
        b = brief(_ex("example-eaa"))
        assert not b.startswith("It then"), b
        assert b.startswith("This flow arms M27"), b

    def test_an_empty_graph_is_an_empty_brief_not_a_sentence_about_nothing(self):
        assert brief(FlowGraph(nodes=[], edges=[])) == ""
        assert brief(None) == ""

    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_no_shipped_brief_carries_an_em_dash(self, ex):
        b = brief(ex.graph)
        assert "—" not in b and "–" not in b, b

    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_every_brief_is_whole_sentences(self, ex):
        b = brief(ex.graph)
        if not b:
            return
        assert b[0].isupper(), b
        assert b.endswith("."), b
        assert "  " not in b, f"double space in: {b}"
        assert "None" not in b, f"an unset param reached the prose: {b}"


# =========================================================== the campaign tab

class TestWhatTheCampaignTabWillSay:
    def test_no_pool_means_campaigns_need_one(self):
        c = _campaign(_ex("example-m16"), None)
        assert c["has_pool"] is False and c["is_campaign"] is False
        assert "campaigns need one" in c["note"]

    def test_a_pool_without_repeat_is_told_how_to_become_a_campaign(self):
        c = _campaign(_ex("example-pool"), None)
        assert c["has_pool"] is True and c["is_campaign"] is False
        assert "set DUSK WINDOW → Repeat" in c["note"]

    def test_the_members_are_the_pools_own_names_and_quota(self):
        c = _campaign(_ex("example-campaign"), None)
        assert [m["name"] for m in c["members"]] == [
            "M33", "NGC 7331", "IC 1396", "M45"]
        assert all(m["quota"] == 45 for m in c["members"])

    def test_no_ledger_reports_None_and_never_zero(self):
        """"0 of 45 banked" and "nobody has looked" are different sentences, and
        only one of them should make an operator re-plan a month."""
        c = _campaign(_ex("example-campaign"), None)
        assert c["has_ledger"] is False
        assert all(m["banked"] is None and m["pct"] is None for m in c["members"])
        assert "nothing here claims a banked figure" in c["note"]

    def test_a_ledger_that_raises_is_no_ledger_rather_than_a_crash(self):
        def boom():
            raise OSError("captures/ is not mounted")
        c = _campaign(_ex("example-campaign"), boom)
        assert c["has_ledger"] is False
        assert all(m["banked"] is None for m in c["members"])

    def test_a_cycle_is_complete_only_when_EVERY_slot_has_its_sub(self):
        """THE ONE THAT MATTERS. 45 L and no Ha is zero complete cycles of an
        LRGBSHO table; counting total frames would report "45/45 · DONE" and
        retire a target holding one channel out of seven."""
        lots_of_L = {"M33": {"L": 45}}
        c = _campaign(_ex("example-campaign"), lambda: lots_of_L)
        m33 = c["members"][0]
        assert m33["banked"] == 0, f"a one-channel target counted as {m33}"
        assert m33["done"] is False

    def test_the_minimum_slot_sets_the_count(self):
        bank = {"M33": {"L": 20, "R": 20, "G": 20, "B": 20,
                        "Ha": 12, "OIII": 20, "SII": 20}}
        c = _campaign(_ex("example-campaign"), lambda: bank)
        assert c["members"][0]["banked"] == 12

    def test_a_member_that_meets_its_quota_is_done(self):
        full = {f: 45 for f in ("L", "R", "G", "B", "Ha", "OIII", "SII")}
        c = _campaign(_ex("example-campaign"), lambda: {"M45": full})
        by_name = {m["name"]: m for m in c["members"]}
        assert by_name["M45"]["banked"] == 45
        assert by_name["M45"]["done"] is True and by_name["M45"]["pct"] == 100
        assert by_name["M33"]["banked"] == 0, "an unshot member borrowed progress"

    def test_subs_per_pass_above_one_divides_the_count(self):
        g = _ex("example-campaign")
        next(n for n in g.nodes if n.type == "cycle").params["perCycle"] = 3
        bank = {"M33": {f: 30 for f in ("L", "R", "G", "B", "Ha", "OIII", "SII")}}
        c = _campaign(g, lambda: bank)
        assert c["members"][0]["banked"] == 10, "30 subs at 3 per pass is 10 passes"

    def test_no_completion_DATE_is_ever_claimed(self):
        """The prototype prints "pool complete in ~6 clear nights". Nothing here
        forecasts clear nights, and an invented number would be the most
        quotable thing on the screen and the least true."""
        bank = {"M33": {f: 45 for f in ("L", "R", "G", "B", "Ha", "OIII", "SII")}}
        c = _campaign(_ex("example-campaign"), lambda: bank)
        note = c["note"]
        assert "clear nights" not in note or "not forecast" in note, note
        assert "~" not in note, f"a tilde-estimate crept in: {note}"
        assert "135 cycles left" in note, note      # 3 members x 45
        assert "not forecast" in note, note

    def test_the_remaining_work_is_stated_exactly(self):
        bank = {"M33": {f: 45 for f in ("L", "R", "G", "B", "Ha", "OIII", "SII")}}
        c = _campaign(_ex("example-campaign"), lambda: bank)
        # 3 members x 45 cycles x 1 sub x 7 slots
        assert "945 subs" in c["note"], c["note"]


class TestTheLedgerFold:
    def test_accepted_frames_are_summed_per_target_and_filter(self):
        reps = [
            _Rep([_TB("M33", [_FB("Ha", 5), _FB("L", 2)]),
                  _TB("M45", [_FB("L", 9)])]),
            _Rep([_TB("M33", [_FB("Ha", 3)])]),
        ]
        assert frames_by_target_from_reports(reps) == {
            "M33": {"Ha": 8, "L": 2}, "M45": {"L": 9}}

    def test_an_unnamed_target_is_skipped_rather_than_bucketed_under_blank(self):
        assert frames_by_target_from_reports([_Rep([_TB("", [_FB("L", 4)])])]) == {}

    def test_no_reports_is_an_empty_mapping(self):
        assert frames_by_target_from_reports([]) == {}
        assert frames_by_target_from_reports(None) == {}


class TestTheCampaignBlockIsNotDroppedInSilence:
    """`SequencePlan` has nowhere to put `campaign`, so the run images ONE night.

    That is a fine interim state; dropping it WITHOUT SAYING SO is not. An
    operator who drew a month-long campaign and got a single night with no
    warning is the defect the whole unmapped list exists to prevent, and this
    entry was missing when the compile first started emitting the block.
    """

    def test_a_campaign_flow_reports_what_it_will_not_do(self):
        from astrodeck.flows.compile import compile_plan
        from astrodeck.flows.to_plan import to_sequence_plan
        g = _ex("example-campaign")
        _, un = to_sequence_plan(compile_plan(g, "camp"), g)
        hits = [u for u in un if u["key"] == "campaign"]
        assert hits, [u["key"] for u in un]
        detail = hits[0]["detail"]
        assert "images ONE night" in detail, detail
        assert "will not re-arm" in detail, detail
        assert hits[0]["level"] == "danger", hits[0]

    def test_a_single_night_flow_says_nothing_about_campaigns(self):
        from astrodeck.flows.compile import compile_plan
        from astrodeck.flows.to_plan import to_sequence_plan
        g = _ex("example-m16")
        _, un = to_sequence_plan(compile_plan(g, "m16"), g)
        assert not [u for u in un if u["key"] == "campaign"]
