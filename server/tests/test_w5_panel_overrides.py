# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Per-panel exposure overrides inside one mosaic (#186, WP 5.4).

A TARGET block's `panelExposure` text gives a panel its own exposure and,
optionally, its own count: "1-1:30" is panel 1-1 at 30 s, "2-3:120x20" is
panel 2-3 at 120 s and 20 frames. Every panel not named keeps its lane's steps,
and a block without the text compiles to exactly the plan it always did.

WHAT THE STEP ID DOES. A step id is keyed on its recipe (``identity.
step_signature``: frame type, filter, exposure, gain, binning), and the ledger
takes a frame's integration time from its step (``session_files.
_frame_exposure``), so an id that stayed put under a new exposure would
re-label frames shot at the old one as frames at the new one. An override
therefore moves the overridden step's id exactly as a change of the lane's
exposure does (frames banked at the old exposure stay on disk under the old
step and CONTINUE asks about them as it does for any exposure change), and
moves no other id: not the three other panels', and not any panel's under a
count-only override, since the count is not in the id.

THE MUTANT, NAMED. "Ignore the override in to_plan" (``_expand_mosaic`` hands
every panel the lane's steps): ``test_the_overridden_panel_shoots_at_its_own_
exposure`` fails, the compiled panel 1-1 steps carry the lane's 120 s.

``fixtures/w5_panel_exposure_cases.json`` is shared with the modal's mirror
(``w5PanelOverridesModel.test.ts``).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from astrodeck.flows import doctor, identity
from astrodeck.flows.compile import compile_plan, parse_panel_exposure
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.nodes import NODE_DEFS
from astrodeck.flows.readouts import readouts
from astrodeck.flows.tonight import _budget
from astrodeck.flows.to_plan import GraphNotRunnable, to_sequence_plan
from test_flows_compile_never_raises import ODD

CASES = json.loads((Path(__file__).parent / "fixtures"
                    / "w5_panel_exposure_cases.json").read_text("utf-8"))["cases"]

LANE_EXPOSURE_S = 120.0


def _flow(**target_over) -> FlowGraph:
    """A framed 2x2 of M31 at PA 30 owning a CAPTURE of 10 L subs of 120 s,
    the loop wire back to the block: four panels, one lane."""
    params = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09",
              "rows": 2, "cols": 2, "overlap": 25, "fovX": 2.0, "fovY": 1.33,
              "angle": "Rotate to PA", "rotation": 30}
    params.update(target_over)
    return FlowGraph.model_validate({
        "nodes": [{"id": "t", "type": "target", "params": params},
                  {"id": "c", "type": "capture", "x": 200,
                   "params": {"filter": "L", "exposure": LANE_EXPOSURE_S,
                              "count": 10}}],
        "edges": [{"from": "t", "fromPort": "target", "to": "c",
                   "toPort": "run"},
                  {"from": "c", "fromPort": "pass", "to": "t",
                   "toPort": "next"}]})


def _plan(g: FlowGraph):
    compiled = compile_plan(g, "w5")
    plan, unmapped = to_sequence_plan(compiled, g, flow_id="w5-flow")
    return plan, unmapped


def _panels(plan) -> dict[str, list]:
    """Each panel's steps by its row-column label."""
    return {t.name.split()[-1]: list(t.steps) for t in plan.targets}


def _ids(plan) -> dict[str, list[str]]:
    return {label: [s.id for s in steps]
            for label, steps in _panels(plan).items()}


class TestTheParser:
    @pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
    def test_the_worked_cases(self, case):
        """Every worked case reads as the file says, the same file the
        modal's mirror is graded against."""
        got, unread = parse_panel_exposure(case["text"], case["rows"],
                                           case["cols"])
        assert [[o["row"], o["col"], o["exposure_s"], o["count"]]
                for o in got] == case["panels"]
        assert unread == case["unread"]

    def test_seconds_too_large_to_be_a_number_are_unread(self):
        """400 nines is the float ``inf``: no exposure, so the entry is
        unread and never reaches the plan as an infinity."""
        got, unread = parse_panel_exposure("1-1:" + "9" * 400, 2, 2)
        assert got == [] and len(unread) == 1

    def test_a_digit_run_int_refuses_is_unread_not_raised(self):
        """Past CPython's 4300-digit integer limit ``int()`` raises
        ``ValueError`` (#441): a count, a row or a column of that length is
        unread, as ``parse_skip`` reads it."""
        for text in ("1-1:30x" + "9" * 4301, "1" * 4301 + "-1:30",
                     "1-" + "1" * 4301 + ":30"):
            got, unread = parse_panel_exposure(text, 2, 2)
            assert got == [] and unread == [text]


class TestTheCompile:
    def test_the_entry_carries_the_overrides_for_a_mosaic(self):
        (entry,) = compile_plan(_flow(panelExposure="1-1:30x4"), "w5")["targets"]
        assert entry["mosaic"]["panel_exposure"] == [
            {"row": 1, "col": 1, "exposure_s": 30.0, "count": 4}]

    def test_a_flow_without_the_param_carries_no_key(self):
        """No key at all, not an empty list: a flow saved before the param
        compiles to the entry it always did."""
        (entry,) = compile_plan(_flow(), "w5")["targets"]
        assert "panel_exposure" not in entry["mosaic"]
        (blank,) = compile_plan(_flow(panelExposure=""), "w5")["targets"]
        assert blank == entry

    def test_a_single_panel_block_ignores_the_param(self):
        """A block of one panel has no PANELS list to set it from; the text
        changes nothing about its plan, as `skip` changes nothing."""
        one = {"rows": 1, "cols": 1}
        base = compile_plan(_flow(**one), "w5")
        with_text = compile_plan(_flow(panelExposure="1-1:30", **one), "w5")
        assert with_text["targets"] == base["targets"]

    def test_an_entry_nobody_can_read_is_a_note_naming_the_block(self):
        """The compile says so, as it does for an unread skip, with the node
        id: nothing is overridden for it."""
        out = compile_plan(_flow(panelExposure="1-1:30, 9-9:30, 2-2:abc"), "w5")
        (entry,) = out["targets"]
        assert [o["row"] for o in entry["mosaic"]["panel_exposure"]] == [1]
        (note,) = [n for n in out["notes"] if n.get("node_id") == "t"
                   and "panel exposure" in n["text"]]
        assert note["level"] == "warn"
        assert "'9-9:30'" in note["text"] and "'2-2:abc'" in note["text"]
        assert "'1-1:30'" not in note["text"]


class TestThePlan:
    def test_the_overridden_panel_shoots_at_its_own_exposure(self):
        """The issue's test: a 2x2 with panel 1-1 at 30 s. Its steps carry
        30 s and the lane's count; the other three the lane's exposure.

        RED under mutant "ignore the override in to_plan" (``_expand_mosaic``
        keeps the lane's steps for every panel): panel 1-1 shoots 120 s.
        """
        plan, _ = _plan(_flow(panelExposure="1-1:30"))
        panels = _panels(plan)
        assert {label: [(s.exposure_s, s.count) for s in steps]
                for label, steps in panels.items()} == {
            "1-1": [(30.0, 10)], "1-2": [(LANE_EXPOSURE_S, 10)],
            "2-1": [(LANE_EXPOSURE_S, 10)], "2-2": [(LANE_EXPOSURE_S, 10)]}

    def test_a_count_rides_with_the_exposure(self):
        plan, _ = _plan(_flow(panelExposure="2-2:240x25"))
        assert [(s.exposure_s, s.count) for s in _panels(plan)["2-2"]] == [
            (240.0, 25)]
        assert [(s.exposure_s, s.count) for s in _panels(plan)["1-1"]] == [
            (LANE_EXPOSURE_S, 10)]

    def test_every_panel_may_have_its_own(self):
        plan, _ = _plan(_flow(
            panelExposure="1-1:30x40, 1-2:60, 2-1:90x20, 2-2:180x5"))
        assert {label: [(s.exposure_s, s.count) for s in steps]
                for label, steps in _panels(plan).items()} == {
            "1-1": [(30.0, 40)], "1-2": [(60.0, 10)],
            "2-1": [(90.0, 20)], "2-2": [(180.0, 5)]}

    def test_no_other_panels_step_id_moves(self):
        """The three panels with no override keep every id the base compile
        gives them, and the overridden one keeps its panel and its stage."""
        base = _ids(_plan(_flow())[0])
        plan, _ = _plan(_flow(panelExposure="1-1:30"))
        got = _ids(plan)
        for label in ("1-2", "2-1", "2-2"):
            assert got[label] == base[label], label
        assert got["1-1"] != base["1-1"]

    def test_the_overridden_step_is_keyed_as_an_exposure_change_keys_it(self):
        """Its id is the id the same panel and stage would have at 30 s in
        the lane, because the ledger takes a frame's integration from its
        step and one id cannot stand for two exposures (module docstring)."""
        plan, _ = _plan(_flow(panelExposure="1-1:30"))
        target = next(t for t in plan.targets if t.name.endswith("1-1"))
        (step,) = target.steps
        assert step.id == identity.step_id(
            target.id, "c", frame_type="Light", filter="L", exposure_s=30,
            gain=step.gain, binning=step.binning)
        assert step.stage == "c"

    def test_a_count_only_change_moves_no_id(self):
        """A count is not in the id: an override that changes only the
        count, set or changed mid-campaign, keeps every id, so the ledger
        keeps counting."""
        base = _ids(_plan(_flow())[0])
        for text in ("1-1:120x40", "1-1:120x3"):
            assert _ids(_plan(_flow(panelExposure=text))[0]) == base, text

    def test_a_count_beside_the_exposure_moves_no_id(self):
        a = _ids(_plan(_flow(panelExposure="1-1:30"))[0])
        b = _ids(_plan(_flow(panelExposure="1-1:30x7"))[0])
        assert a == b

    def test_a_skipped_panel_with_an_override_is_still_skipped(self):
        plan, _ = _plan(_flow(skip="1-1", panelExposure="1-1:30, 2-2:45"))
        panels = _panels(plan)
        assert "1-1" not in panels
        assert [s.exposure_s for s in panels["2-2"]] == [45.0]

    def test_an_exposure_the_plan_refuses_is_named_by_its_block(self):
        """The plan's own bound (at most 3600 s) is read where every other
        value meets it, and names the block."""
        with pytest.raises(GraphNotRunnable) as e:
            _plan(_flow(panelExposure="1-1:9000"))
        assert "M31" in str(e.value)

    def test_a_cycle_lane_takes_the_override_on_every_filter(self):
        """A FILTER CYCLE's slots each become a step, and each takes the
        panel's exposure; the block's other panels keep their slots."""
        g = FlowGraph.model_validate({
            "nodes": [{"id": "t", "type": "target", "params": {
                "name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09",
                "rows": 2, "cols": 2, "overlap": 25, "fovX": 2.0,
                "fovY": 1.33, "angle": "Rotate to PA", "rotation": 30,
                "panelExposure": "1-2:30x6"}},
                {"id": "cy", "type": "cycle", "x": 200,
                 "params": {"plan": "L 60, R 90"}}],
            "edges": [{"from": "t", "fromPort": "target", "to": "cy",
                       "toPort": "run"},
                      {"from": "cy", "fromPort": "pass", "to": "t",
                       "toPort": "next"}]})
        plan, _ = _plan(g)
        panels = _panels(plan)
        assert [(s.filter, s.exposure_s, s.count) for s in panels["1-2"]] == [
            ("L", 30.0, 6), ("R", 30.0, 6)]
        assert [(s.filter, s.exposure_s) for s in panels["1-1"]] == [
            ("L", 60.0), ("R", 90.0)]

    def test_a_lane_of_two_exposures_says_the_override_flattens_it(self):
        """One exposure for a panel whose lane shoots two is said, as a
        note, so the operator who meant one filter reads it before the
        night; a lane of one exposure says nothing."""
        cycle = {"nodes": [{"id": "t", "type": "target", "params": {
            "name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09",
            "rows": 2, "cols": 2, "overlap": 25, "fovX": 2.0, "fovY": 1.33,
            "angle": "Rotate to PA", "rotation": 30,
            "panelExposure": "1-2:30"}},
            {"id": "cy", "type": "cycle", "x": 200,
             "params": {"plan": "L 60, R 90"}}],
            "edges": [{"from": "t", "fromPort": "target", "to": "cy",
                       "toPort": "run"},
                      {"from": "cy", "fromPort": "pass", "to": "t",
                       "toPort": "next"}]}
        _, unmapped = _plan(FlowGraph.model_validate(cycle))
        (note,) = [u for u in unmapped if "panel_exposure" in u["key"]]
        assert note["level"] == "note" and "1-2" in note["detail"]
        assert "60" in note["detail"] and "90" in note["detail"]
        _, quiet = _plan(_flow(panelExposure="1-2:30"))
        assert not [u for u in quiet if "panel_exposure" in u["key"]]


class TestTheDoctor:
    def _issues(self, **over):
        return [i for i in doctor.check(_flow(**over))
                if "panel exposure" in i.text]

    @pytest.mark.parametrize("entry", ["9-9:30", "1-1:abc"])
    def test_a_bad_entry_is_an_error_naming_the_block(self, entry):
        (issue,) = self._issues(panelExposure=f"2-2:60, {entry}")
        assert issue.level == "danger"
        assert "TARGET M31" in issue.text and f"'{entry}'" in issue.text
        assert "'2-2:60'" not in issue.text

    def test_a_readable_text_says_nothing(self):
        assert self._issues(panelExposure="1-1:30, 2-2:60x10") == []
        assert self._issues() == []


class TestTheReadoutsPriceEveryPanel:
    """The modal's RUN numbers are the sum over the panels the plan shoots,
    not the first panel's steps times the panel count (``readouts._block``).

    RED under mutant "price from members[0]" (``total_s``, ``subs_total`` and
    the visits taken from the first member, as ``_block`` read them before
    #186):

        AssertionError: block totals: {'node_id': 't', 'mode': 'rotate',
        'panels': 4, ..., 'subs_total': 16, ..., 'total_s': 480.0, ...,
        'visits_total': 16, ...}
        (two of the file's three readouts tests fail)
    """

    @staticmethod
    def _block(**over):
        g = _flow(**over)
        compiled = compile_plan(g, "w5")
        plan, _ = to_sequence_plan(compiled, g, flow_id="w5-flow")
        return readouts(compiled, plan, None)["t"]

    def test_a_block_with_one_panel_overridden_is_priced_as_the_sum(self):
        """1-1 at 30 s x 4 (120 s of shutter) beside three panels of
        10 subs of 120 s: 34 subs, 3720 s of shutter, and a rotating visit
        for each of 4 + 10 + 10 + 10 rounds. The per-panel figures stay the
        lane's, from a panel without the override."""
        b = self._block(panelExposure="1-1:30x4")
        assert {"subs_total": b["subs_total"], "total_s": b["total_s"],
                "visits_total": b["visits_total"]} == {
            "subs_total": 34, "total_s": 3720.0, "visits_total": 34}, (
            f"block totals: {b}")
        assert (b["subs_per_panel"], b["panel_s"], b["rounds"],
                b["visits_per_panel"]) == (10, 1200.0, 10, 10)

    def test_a_block_with_no_override_is_the_lane_times_its_panels(self):
        b = self._block()
        assert (b["subs_total"], b["total_s"], b["visits_total"]) == (
            40, 4800.0, 40)
        assert (b["subs_per_panel"], b["panel_s"]) == (10, 1200.0)

    def test_a_block_whose_panels_all_differ_takes_the_first_for_the_lane(self):
        b = self._block(panelExposure="1-1:30x4, 1-2:60x5, 2-1:90x6, 2-2:20x7")
        assert (b["subs_total"], b["total_s"]) == (22, 120 + 300 + 540 + 140)
        assert (b["subs_per_panel"], b["panel_s"]) == (4, 120.0)


class TestTonightsBudgetPricesEveryPanel:
    """Tonight's budget row for a mosaic is the sum over its live panels, and
    its hops are each panel's visits, not the lane's figure times the panel
    count (``tonight._budget``, #186).

    RED under mutant "budget from the lane" (``_panel_step_lists`` answering
    None, so every panel is the entry's steps):

        AssertionError: assert (1.67, 1.67) == (1.32, 1.42)
    """

    @staticmethod
    def _row(**over):
        g = FlowGraph.model_validate({
            "nodes": [{"id": "t", "type": "target", "params": {
                "name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09",
                "rows": 2, "cols": 2, "overlap": 25, "fovX": 2.0,
                "fovY": 1.33, "angle": "Rotate to PA", "rotation": 30,
                **over}},
                {"id": "cy", "type": "cycle", "x": 200,
                 "params": {"plan": "L 60, R 90", "cycles": 10,
                            "perCycle": 1}}],
            "edges": [{"from": "t", "fromPort": "target", "to": "cy",
                       "toPort": "run"},
                      {"from": "cy", "fromPort": "pass", "to": "t",
                       "toPort": "next"}]})
        (row,) = _budget(compile_plan(g, "w5"), None, hop_cost_s=150.0)
        return row

    def test_a_row_is_the_sum_over_the_panels(self):
        """Three panels of (60 + 90) s x 10 = 1500 s, and 1-1 at 30 s x 4 on
        both filters = 240 s: 4740 s is 1.32 h. The hops are 4 + 10 + 10 + 10
        visits of 150 s: 5100 s is 1.42 h, where the lane's 40 visits would
        be 1.67 h."""
        row = self._row(panelExposure="1-1:30x4")
        assert (row["tonight_h"], row["hop_h"]) == (1.32, 1.42), row

    def test_a_row_with_no_override_is_the_lane_times_its_panels(self):
        row = self._row()
        assert (row["tonight_h"], row["hop_h"]) == (1.67, 1.67), row


class TestAnyValueCompiles:
    """The corpus of ``test_flows_compile_never_raises.py`` leaves this param
    out of its draw (``NOT_DRAWN``), so every odd value it draws for a param
    is given here to `panelExposure` on a framed 2x2: the compile, the plan and
    the doctor answer, and nothing leaves the plan but ``GraphNotRunnable``."""

    @pytest.mark.parametrize("value", ODD, ids=[repr(v)[:24] for v in ODD])
    def test_an_odd_value_never_raises(self, value):
        g = _flow(panelExposure=value)
        compiled = compile_plan(g, "w5")
        try:
            to_sequence_plan(compiled, g, flow_id="w5-flow")
        except GraphNotRunnable:
            pass
        doctor.check(g)


class TestTheVocabulary:
    def test_the_target_carries_the_param_blank(self):
        """The row for it is nodeDefs.ts's, held to this table by
        ``nodeDefs.test.ts``."""
        assert NODE_DEFS["target"].params["panelExposure"] == ""


class TestAFlowWithoutTheParamIsUnchanged:
    """COMMON.md rule 14. The plan-hash pin taken on the base tree is in the
    work package's report; this is the standing half: a flow that never
    carried the param, and one that carries it empty, are the same plan."""

    def test_blank_and_absent_are_one_plan(self):
        absent, _ = _plan(_flow())
        blank, _ = _plan(_flow(panelExposure=""))
        assert absent.model_dump_json() == blank.model_dump_json()

    def test_a_param_naming_no_panel_changes_nothing(self):
        absent, _ = _plan(_flow())
        unread, _ = _plan(_flow(panelExposure="9-9:30"))
        assert absent.model_dump_json() == unread.model_dump_json()
