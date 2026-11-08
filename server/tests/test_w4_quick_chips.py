# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The quick sheet's calibration and automation chips keep what they say
(#1111 C-05 sheet half: #843, #818 sheet half, #820, #845, #1219; consolidation
wave 4, WP 4.1a).

THE CLASS: a claim nothing keeps. The sheet offered a chip, the operator
ticked it, and the engine ran something else or nothing: "add dusk flats"
created the one method the engine did not run (#843), "darks after" lost its
Always policy on the way to the plan (#1219), the Tonight timeline drew the
flats with no width (#845), and four of the six automation chips were never
shown to reach the engine at all (#820).

WHAT THIS FILE HOLDS, in the order of the file:

* the CAPABILITY TABLE (``nodes.DUSK_FLATS_METHODS``, ``nodes.AUTOMATION_
  CHIPS``): the engine's methods are the table's, the node is CREATED with the
  first capable one, the doctor refuses an incapable choice as a danger, and
  the sheet cannot produce a plan the doctor would refuse;
* the DARKS POLICY: the queue's Always rides into the plan and ``_day_darks``
  honours it, while the automatic policy keeps the shortfall rule;
* the FLATS DURATION: Tonight gives a block the engine runs a length, from the
  count and the filters, and the twilight sky keeps its Sun window;
* ONE END-TO-END TEST PER CHIP: tick the chip as the sheet does, assert the
  compiled plan carries the rule, assert the engine runs it on the simulator.
  Two chips (Cloud hold, Live stack) are shown NOT to reach the engine, which
  is what their capability rows say; the day a flow carries them, the test
  that pins the row goes red and the row flips with it.

The chips' graph edits are mirrored here from ``flowGraphExtras.ts`` the way
``test_w4_1b_end_of_night_darks.py`` mirrors ``withDarksAfter``; the UI test
``w4_1aQuickChips.test.tsx`` pins the other end of the mirror.

Site: made up (the harness's fixture), never the observatory's.

Named mutant: the lens cap offered again (``nodes.DUSK_FLATS_METHODS`` marks it
engine-capable): ``test_the_doctor_refuses_a_method_the_engine_does_not_run``
fails on the missing danger, and the creation default test with it.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path

import pytest

import astrodeck.hub as hub_module
from astrodeck.flows import doctor, nodes, tonight, to_plan
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import create_params
from astrodeck.flows.to_plan import plan_extras, to_sequence_plan
from astrodeck.flows.wizard import (KIND_DEEP_SKY, OPT_CLOUD_DODGE, generate,
                                    quick)
from astrodeck.guide.base import GuideStats
from astrodeck.hub import Hub
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import (ExposureStep, Schedule, SequencePlan,
                                       Target)
from _group_harness import (close_night_hub, group_store,  # noqa: F401
                            night_hub)
from test_w15_dusk_flats_claim import JUNE, SITE, _hand, _tonight
from test_w4_1b_sky_flats import SKY_MODEL, _hi_crossing, _night

SERVER = Path(__file__).resolve().parent.parent
QUICK_TSX = (SERVER.parent / "ui" / "src" / "next" / "hubs" / "sky" / "sheets"
             / "quick.tsx")

_DEC = f"+41{chr(176)} 16' 09{chr(34)}"
_TARGET = {"name": "M31", "ra": "00h 42m 44s", "dec": _DEC}
_COORDS = ("00h 42m 44s", _DEC)
_WHEEL = ["L", "R", "G", "B"]


# ------------------------------------------------- the sheet, as the sheet is

def _quick(*, guided: bool = True, filters=("L", "R"), autofocus: bool = True,
           watchdog: bool = True, dither_every: int = 3) -> FlowGraph:
    """The graph ``POST /api/flows/quick`` makes of the sheet's answers: the
    four of them and the three automation chips that shape the graph."""
    return quick(_TARGET, subs_per_filter=10, filters=list(filters),
                 wheel=_WHEEL, guided=guided, autofocus=autofocus,
                 watchdog=watchdog, dither_every=dither_every).graph


def _edge(a: str, ap: str, b: str, bp: str) -> FlowEdge:
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def with_flats_chip(graph: FlowGraph) -> FlowGraph:
    """``flowGraphExtras.withDuskFlats``: a DUSK FLATS node, created with the
    vocabulary's Created-as column, between the window and what it fed."""
    dusk = next(n for n in graph.nodes if n.type == "dusk")
    graph.nodes.append(FlowNode(id="q-duskflats", type="duskflats", x=900.0,
                                y=60.0, params=create_params("duskflats")))
    for e in graph.edges:
        if e.from_ == dusk.id and e.fromPort == "window":
            e.from_, e.fromPort = "q-duskflats", "done"
    graph.edges.append(_edge(dusk.id, "window", "q-duskflats", "run"))
    return graph


def with_darks_chip(graph: FlowGraph, *, darks: str = "Always") -> FlowGraph:
    """``flowGraphExtras.withDarksAfter``: a CALIBRATION QUEUE started by the
    report's 'target done' event, darks and bias ``darks``, flats Skip."""
    report = next(n for n in graph.nodes if n.type == "report")
    graph.nodes.append(FlowNode(
        id="q-calib", type="calib", x=900.0, y=380.0,
        params={**create_params("calib"), "darks": darks, "bias": darks,
                "flats": "Skip"}))
    graph.edges.append(_edge(report.id, "done", "q-calib", "do"))
    return graph


def _compile(graph: FlowGraph):
    plan, unmapped = to_sequence_plan(compile_plan(graph, "quick"), graph)
    return plan, {u["key"]: u for u in unmapped}


# ----------------------------------------------------- the capability table

class TestTheCapabilityTable:
    def test_the_engines_methods_are_the_tables(self):
        """``DUSK_FLATS_RUNS`` is read off the table, so a method gains the
        stage in one place. The lens cap is the one the engine does not run."""
        capable = {m.key for m in nodes.DUSK_FLATS_METHODS if m.engine_capable}
        assert to_plan.DUSK_FLATS_RUNS == capable == {"sky", "panel"}
        cap = nodes.flats_method("Translucent lens cap")
        assert cap is not None and not cap.engine_capable and cap.reason

    def test_the_table_names_the_nodes_own_options(self):
        """The three labels are the three the compile maps to a plan method."""
        labels = {m.label.lower() for m in nodes.DUSK_FLATS_METHODS}
        assert labels == set(to_plan._DUSK_FLATS_METHODS)
        assert {to_plan._DUSK_FLATS_METHODS[label] for label in labels} == {
            m.key for m in nodes.DUSK_FLATS_METHODS}

    def test_a_new_flats_block_is_created_with_the_first_capable_method(self):
        """#843: the one-tap chip creates the block with the Created-as column,
        and that is the first method the engine runs, never the lens cap. The
        missing-key default stays the lens cap, the meaning a saved block with
        no method always had."""
        created = create_params("duskflats")["method"]
        assert created == nodes.default_flats_method() == "Twilight sky"
        assert nodes.flats_method(created).engine_capable
        assert nodes.NODE_DEFS["duskflats"].params["method"] == (
            "Translucent lens cap")

    def test_the_six_chips_are_the_sheets_six(self):
        """The table's keys are the chips ``quick.tsx`` offers, in its order,
        so a chip added to the sheet without a row fails here."""
        text = QUICK_TSX.read_text(encoding="utf-8")
        block = text[text.index("const AUTOMATION"):]
        block = block[:block.index("];")]
        keys = re.findall(r'key: "(\w+)"', block)
        assert keys == [c.key for c in nodes.AUTOMATION_CHIPS]

    def test_an_incapable_chip_says_why(self):
        for chip in nodes.AUTOMATION_CHIPS:
            assert chip.reason, chip.key
        assert {c.key for c in nodes.AUTOMATION_CHIPS if not c.engine_capable
                } == {"cloud", "stack"}


# ----------------------------------------------------- the doctor refuses it

def _danger(graph: FlowGraph) -> list[str]:
    return [i.text for i in doctor.check(graph) if i.level == "danger"]


class TestTheDoctorRefusesAnIncapableChoice:
    def test_the_doctor_refuses_a_method_the_engine_does_not_run(self):
        """THE NAMED MUTANT'S TEST. A DUSK FLATS block set to the lens cap is a
        DANGER, not a note (#843): it compiles and the night goes without
        flats. The text names the node, says why, and names what to set.

        RED when the lens cap is offered again (``Translucent lens cap``
        marked engine-capable in ``nodes.DUSK_FLATS_METHODS``): no danger."""
        issues = [i for i in doctor.check(_hand(method="Translucent lens cap"))
                  if i.level == "danger"]
        assert len(issues) == 1, issues
        text = issues[0].text
        assert text.startswith(chr(0x25B8) + " DUSK FLATS"), text
        assert "takes no flats" in text, text
        assert "Twilight sky or Flat panel" in text, text

    @pytest.mark.parametrize("method", ["Twilight sky", "Flat panel"])
    def test_a_method_the_engine_runs_is_not_refused(self, method):
        assert _danger(_hand(method=method)) == []

    def test_a_method_nobody_offers_is_refused_too(self):
        (text,) = _danger(_hand(method="Moonlight"))
        assert "Moonlight" in text and "Set Method to" in text, text

    def test_no_shipped_example_is_refused(self):
        """The library's Examples are created blocks, so the DUSK FLATS one
        takes the first capable method and the doctor has no danger for any
        of them (the M16 Example read the lens cap through the missing-key
        default until WP 4.1a)."""
        for ex in examples():
            assert _danger(ex.graph) == [], ex.id
        block = next(n for n in next(
            e for e in examples() if e.id == "example-m16").graph.nodes
            if n.type == "duskflats")
        assert block.params["method"] == "Twilight sky"

    def test_the_sheet_cannot_produce_a_plan_the_doctor_would_refuse(self):
        """THE DONE-WHEN. Every combination of the two calibration chips, on
        the sheet's graph, guided or not: no danger from the doctor, and no
        compile row saying a block takes no flats.

        RED if the chip creates the lens cap again (the Created-as column put
        back to the missing-key default): the flats-on cases have a danger."""
        for guided in (True, False):
            for flats in (False, True):
                for darks in (False, True):
                    g = _quick(guided=guided)
                    if flats:
                        g = with_flats_chip(g)
                    if darks:
                        g = with_darks_chip(g)
                    where = f"guided={guided} flats={flats} darks={darks}"
                    assert _danger(g) == [], where
                    _plan, notes = _compile(g)
                    row = notes.get("automation.dusk_flats")
                    if flats:
                        assert row is not None and row["level"] == "note", (
                            where, row)
                        assert "will not take flats" not in row["detail"]
                    else:
                        assert row is None, where


# ---------------------------------------------------------- the darks policy

def _queue(policy: str, quota=20, *, wire="on_target_complete") -> dict:
    return {"automation": {"calibration_queue": {
        "quota": quota, "policy": policy}},
        "instructions": [{"when": wire, "action": "calib", "to_port": "do"}]}


class TestTheDarksPolicyReachesThePlan:
    def test_the_chip_carries_always_into_the_plan(self):
        plan, _ = _compile(with_darks_chip(_quick()))
        assert plan.day_darks > 0
        assert plan.day_darks_always is True, (
            "the DARKS AFTER chip's Always policy was dropped on the way to "
            "the plan (#1219)")

    def test_the_automatic_policy_is_not_always(self):
        plan, _ = _compile(with_darks_chip(_quick(), darks="If library stale"))
        assert plan.day_darks > 0 and plan.day_darks_always is False

    def test_a_plan_without_the_policy_dumps_as_it_did(self):
        """The field is absent from the dump while False, so every plan that
        never asked for it is byte for byte what it was (the goldens)."""
        plan, _ = _compile(with_darks_chip(_quick(), darks="If library stale"))
        assert "day_darks_always" not in plan.model_dump()
        always, _ = _compile(with_darks_chip(_quick()))
        assert always.model_dump()["day_darks_always"] is True

    def test_the_policy_rides_only_with_the_lane(self):
        """A queue fed from a cloud watch asks for hold darks and no day
        darks, whatever its policy says."""
        assert "day_darks_always" not in plan_extras(
            _queue("always", wire="on_clouds_in"))
        assert plan_extras(_queue("always"))["day_darks_always"] is True
        assert "day_darks_always" not in plan_extras(_queue("if_stale"))

    def test_the_compile_names_the_nodes_policy(self):
        for darks, policy in (("Always", "always"),
                              ("If library stale", "if_stale"),
                              ("Skip", "skip")):
            compiled = compile_plan(with_darks_chip(_quick(), darks=darks), "q")
            assert compiled["automation"]["calibration_queue"]["policy"] == (
                policy), darks


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store, "_path",
                        tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module.config_store, "_cfg", None)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _light(exp=1.0, filt="L") -> ExposureStep:
    return ExposureStep(filter=filt, exposure_s=exp, gain=100, offset=10,
                        count=2)


def _day_dark_engine(hub, plan: SequencePlan, monkeypatch, *, held: int):
    """An engine whose library holds ``held`` darks at every recipe and which
    records each calibration frame it is asked for."""
    eng = SequenceEngine(hub)
    eng.plan = plan
    shot: list[float] = []

    async def calibration(ti, target):
        shot.append(target.steps[0].exposure_s)

    monkeypatch.setattr(eng, "_run_calibration", calibration)
    monkeypatch.setattr(eng, "_hold_darks_shortfall",
                        lambda step, quota: (max(0, quota - held), held))
    return eng, shot


def _lights_target(*steps: ExposureStep) -> Target:
    return Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                  autofocus_first=False, steps=list(steps))


class TestTheEngineHonoursThePolicy:
    async def test_always_shoots_the_quota_whatever_the_library_holds(
            self, sim_hub, monkeypatch):
        """A full library plus Always shoots the quota (#1219).

        RED if ``_day_darks`` ignores the policy: the library holds 40 and the
        shortfall rule shoots none."""
        plan = SequencePlan(targets=[_lights_target(_light())], day_darks=5,
                            day_darks_always=True)
        eng, shot = _day_dark_engine(sim_hub, plan, monkeypatch, held=40)
        await eng._day_darks()
        assert len(shot) == 5, shot

    async def test_the_automatic_policy_keeps_the_shortfall_rule(
            self, sim_hub, monkeypatch):
        """A full library plus the automatic policy shoots none."""
        plan = SequencePlan(targets=[_lights_target(_light())], day_darks=5)
        eng, shot = _day_dark_engine(sim_hub, plan, monkeypatch, held=40)
        await eng._day_darks()
        assert shot == []

    async def test_always_splits_the_quota_over_the_recipes(
            self, sim_hub, monkeypatch):
        """Two exposures, a quota of 5: three at the first and two at the
        second, the quota in all, so each exposure gets darks."""
        plan = SequencePlan(
            targets=[_lights_target(_light(10), _light(20))], day_darks=5,
            day_darks_always=True)
        eng, shot = _day_dark_engine(sim_hub, plan, monkeypatch, held=40)
        await eng._day_darks()
        assert shot == [10.0, 10.0, 10.0, 20.0, 20.0], shot


# ------------------------------------------------------ the flats duration

class TestTonightGivesTheFlatsABlockWidth:
    @pytest.fixture(autouse=True)
    def synthetic_hub(self, monkeypatch):
        """The hub on test_w15's made-up site, for these cases only: the
        night harness below runs at its own site, which this must not move."""
        monkeypatch.setattr(Hub, "site", property(lambda self: {
            "name": "synthetic", **SITE, "horizon_min_deg": 0.0}))

    def test_a_panel_block_has_a_duration_from_its_count_and_filters(self):
        """#845. The sets (one per filter) times the metering trials plus the
        count, each frame at the nominal cost."""
        out = _tonight(_hand(count=3))
        per_frame = (tonight.FLAT_NOMINAL_EXPOSURE_S
                     + tonight.FLAT_FRAME_OVERHEAD_S)
        assert out["flats"]["duration_s"] == (
            1 * (3 + tonight.FLAT_METER_FRAMES) * per_frame)

    def test_the_duration_follows_the_count(self):
        short = _tonight(_hand(count=3))["flats"]["duration_s"]
        long = _tonight(_hand(count=13))["flats"]["duration_s"]
        per_frame = (tonight.FLAT_NOMINAL_EXPOSURE_S
                     + tonight.FLAT_FRAME_OVERHEAD_S)
        assert long - short == 10 * per_frame

    def test_the_duration_follows_the_filters(self):
        """A filter cycle's slots are the sets: three filters take three times
        what one does, on the sheet's own flow."""
        two = _tonight(with_flats_chip(_quick(filters=("L", "R"))))
        three = _tonight(with_flats_chip(_quick(filters=("L", "R", "G"))))
        d2, d3 = two["flats"]["duration_s"], three["flats"]["duration_s"]
        assert d2 and d3 and d2 * 3 == d3 * 2, (d2, d3)

    def test_all_in_the_wheel_has_no_duration(self):
        """The wheel is the rig's and this answer is built without one: no
        width is better than an invented one."""
        graph = _hand(count=3)
        flats = next(n for n in graph.nodes if n.type == "duskflats")
        flats.params["filters"] = "All in wheel"
        assert _tonight(graph)["flats"]["duration_s"] is None

    def test_a_block_the_engine_does_not_run_has_no_duration(self):
        out = _tonight(_hand(method="Translucent lens cap"))
        assert out["flats"]["duration_s"] is None

    def test_the_twilight_sky_runs_and_keeps_its_window(self):
        """``runs`` is not "runs at the run's start with no window": the sky
        waits for the band, so it keeps the Sun window and is not placed at
        the start, and it has a duration (it runs)."""
        out = _tonight(_hand(method="Twilight sky", count=3))
        flats = out["flats"]
        assert flats["runs_at_start"] is False and flats["at_unix"] is None
        assert flats["start_unix"] is not None
        assert flats["start_unix"] < flats["end_unix"]
        assert flats["duration_s"] and flats["duration_s"] > 0
        (row,) = [r for r in out["story"] if "DUSK FLATS" in r["msg"]]
        assert row["tone"] == "dim" and row["t_unix"] == flats["start_unix"]
        assert "twilight sky" in row["msg"]
        assert "drawn but not run" not in row["msg"]

    def test_no_site_figure_travels_in_the_duration(self):
        """It is the operator's count and filters and fixed constants: two
        sites give the same number."""
        a = _tonight(_hand(count=3))["flats"]["duration_s"]
        elsewhere = {"latitude": -33.0, "longitude": 151.0,
                     "elevation_m": 50.0, "is_default": False}
        b = tonight.resolve_tonight(
            _hand(count=3), elsewhere, now=JUNE,
            twilight_deg=-12.0)["flats"]["duration_s"]
        assert a == b


# ------------------------------------------------ one end-to-end test per chip
#
# Tick the chip as the sheet does (the graph edit, or the answer the payload
# carries), assert the compiled plan carries the rule, assert the engine runs
# it on the simulator. The compiled plan is shortened to run (a "now" window,
# no centring, millisecond subs): what the engine is asked to do is what the
# chip compiled to, and every assertion about the rule is made on the plan
# BEFORE it is shortened.

def _runnable(plan: SequencePlan, *, count: int = 4) -> SequencePlan:
    """The compiled plan, made quick to run on the simulator: every target
    starts now, is not centred, and shoots ``count`` millisecond subs. The
    rules, the guide and dither settings, the darks lane and the flats block
    are the compile's own."""
    plan = plan.model_copy(deep=True)
    for target in plan.targets:
        target.center = False
        target.schedule = Schedule()
        for step in target.steps:
            step.exposure_s = 0.02
            step.count = count
    plan.meridian_flip = False
    plan.park_when_done = False
    plan.warm_cooler_when_done = False
    return plan


async def _finish(eng: SequenceEngine, timeout_s: float = 60.0) -> None:
    await asyncio.wait_for(eng._task, timeout_s)


class _Guider:
    """A connected guider that guides once started and records every dither."""

    name = "recording guider"

    def __init__(self) -> None:
        self.connected = True
        self.active = False
        self.dithers = 0

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def start_guiding(self) -> None:
        self.active = True

    async def stop_guiding(self) -> None:
        self.active = False

    async def is_active(self) -> bool:
        return self.active

    async def dither(self, pixels: float = 3.0, settle=None) -> None:
        self.dithers += 1

    def stats(self) -> GuideStats:
        return GuideStats()


def _focus_recorder(eng: SequenceEngine, monkeypatch) -> list[str]:
    """Replace the autofocus with one that records its label and gives the
    run a focused baseline of 2.0 arcsec, as a sweep that found focus does."""
    labels: list[str] = []

    async def autofocus(label, **_ctx):
        labels.append(label)
        eng._focus_baseline_hfr = 2.0
        return True

    monkeypatch.setattr(eng, "_autofocus", autofocus)
    return labels


def _soft_frames(monkeypatch, eng: SequenceEngine, hfr: float) -> None:
    async def capture(*a, **k):
        return {"hfr": hfr, "stats": {"median": 100}, "saved_path": None}

    monkeypatch.setattr(eng, "_capture", capture)


class TestAutofocusChip:
    @pytest.mark.parametrize("on", [True, False], ids=["on", "off"])
    async def test_the_chip_reaches_the_plan_and_the_engine_follows_it(
            self, sim_hub, monkeypatch, on):
        """AUTOFOCUS: the stage's presence is whether the run focuses at each
        target's start. Chip on, the plan focuses and the engine sweeps at the
        target's start; chip off, the graph has no AUTOFOCUS stage, the plan's
        targets do not focus first, and the sim night takes no sweep (#820).

        RED if the compile ignores the stage: the off run still sweeps."""
        graph = _quick(guided=False, autofocus=on)
        assert any(n.type == "autofocus" for n in graph.nodes) is on
        plan, _ = _compile(graph)
        assert all(t.autofocus_first is on for t in plan.targets)
        eng = SequenceEngine(sim_hub)
        labels = _focus_recorder(eng, monkeypatch)
        eng.start(_runnable(plan, count=2))
        await _finish(eng)
        assert (labels[:1] == ["initial autofocus"]) is on, labels
        if not on:
            assert labels == [], labels


class TestHfrWatchdogChip:
    @pytest.mark.parametrize("on", [True, False], ids=["on", "off"])
    async def test_the_chip_compiles_to_a_refocus_rule_the_engine_runs(
            self, sim_hub, monkeypatch, on):
        """HFR WATCHDOG: on, the CONDITION and REFOCUS pair compiles to the
        relative ``on_hfr_above`` rule at 1.3 times the focused HFR, and a soft
        frame on the sim fires it: the engine sweeps again, as a triggered
        refocus. Off, the graph draws no pair, the plan has no such rule and a
        soft frame fires nothing (#820).

        RED if the rule is dropped (on) or kept (off): the sim disagrees."""
        graph = _quick(guided=False, watchdog=on)
        plan, _ = _compile(graph)
        rules = [i for i in plan.instructions if i.trigger == "on_hfr_above"]
        if on:
            (rule,) = rules
            assert rule.action == "refocus" and rule.relative is True
            assert rule.threshold == pytest.approx(1.3)
        else:
            assert rules == []
            assert not {"condition", "refocus"} & {n.type for n in graph.nodes}
        eng = SequenceEngine(sim_hub)
        labels = _focus_recorder(eng, monkeypatch)
        _soft_frames(monkeypatch, eng, hfr=9.9)
        eng.start(_runnable(plan, count=3))
        await _finish(eng)
        assert ("triggered refocus" in labels) is on, labels


class TestGuideAndDitherChips:
    @pytest.mark.parametrize("every, dithers", [(3, 2), (5, 1), (0, 0)],
                             ids=["every-3", "every-5", "off"])
    async def test_the_cadence_reaches_the_plan_and_the_engine_dithers(
            self, sim_hub, monkeypatch, every, dithers):
        """DITHER rides the GUIDE stage: the sheet's N (0 when the chip is off)
        is the plan's ``dither_every`` and the engine dithers the guider that
        often. Seven frames make two dithers at 3, one at 5, none off (#820).

        RED if N is dropped on the way to the plan: every case dithers at the
        default 3."""
        plan, _ = _compile(_quick(guided=True, dither_every=every))
        assert plan.guide is True and plan.dither_every == every
        guider = _Guider()
        await sim_hub.guider.disconnect()
        sim_hub.guider = guider
        eng = SequenceEngine(sim_hub)
        _focus_recorder(eng, monkeypatch)
        shortened = _runnable(plan, count=7)
        for target in shortened.targets:
            target.steps = target.steps[:1]
        eng.start(shortened)
        await _finish(eng)
        assert guider.dithers == dithers, guider.dithers

    def test_dither_needs_guide(self):
        """The chip's row says so, and the plan agrees: with Guide off the
        flow carries no guider to dither, whatever N says."""
        plan, _ = _compile(_quick(guided=False, dither_every=5))
        assert plan.guide is False
        row = next(c for c in nodes.AUTOMATION_CHIPS if c.key == "dither")
        assert "needs Guide" in row.reason

    def test_the_guide_chip_is_the_plans_guide(self):
        """GUIDE: ``guided`` is the plan's ``guide``."""
        assert _compile(_quick(guided=True))[0].guide is True
        assert _compile(_quick(guided=False))[0].guide is False


class TestTheSheetsAnswersReachTheRoute:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        import astrodeck.api.app as app_module
        import astrodeck.config as config_mod
        from astrodeck.config import ConfigStore
        from astrodeck.flows.store import FlowStore
        from fastapi.testclient import TestClient
        store = ConfigStore(path=tmp_path / "astrodeck.json")
        monkeypatch.setattr(config_mod, "config_store", store)
        monkeypatch.setattr(hub_module, "config_store", store)
        monkeypatch.setattr(app_module, "config_store", store)
        monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
        monkeypatch.setattr(app_module, "flow_store",
                            FlowStore(tmp_path / "flows"))
        with TestClient(app_module.create_app()) as c:
            yield c

    def test_the_posted_chips_shape_the_saved_flow_and_its_plan(self, client):
        """The POST body the sheet sends (``autofocus``, ``watchdog``,
        ``dither_every``) is what the saved graph and the compiled plan carry;
        a body that sends none of them is the flow the four answers always
        made.

        RED if the route drops a field: the saved flow is the default one."""
        base = {"target": _TARGET, "filters": ["L", "R"], "subs": 4,
                "exposures": {"L": 60, "R": 60}}
        off = client.post("/api/flows/quick", json={
            **base, "autofocus": False, "watchdog": False,
            "dither_every": 5}).json()["flow"]["graph"]
        types = {n["type"] for n in off["nodes"]}
        assert not {"autofocus", "condition", "refocus"} & types, types
        assert off["settings"]["quickAutofocus"] is False
        assert off["settings"]["quickDitherEvery"] == 5
        plan, _ = _compile(FlowGraph.model_validate(off))
        assert plan.dither_every == 5
        assert all(not t.autofocus_first for t in plan.targets)
        default = client.post("/api/flows/quick", json=base).json()["flow"][
            "graph"]
        assert {"autofocus", "condition", "refocus"} <= {
            n["type"] for n in default["nodes"]}


def _plan_digest(graph: FlowGraph) -> tuple[str, int, list[bool]]:
    """The plan of ``graph`` with its minted ids blanked, hashed, with the two
    fields the chips move."""
    import hashlib
    import json
    plan, _ = to_sequence_plan(compile_plan(graph, "pin"), graph)
    dump = plan.model_dump(mode="json")

    def blank(x):
        if isinstance(x, dict):
            return {k: ("" if k == "id" else blank(v)) for k, v in x.items()}
        if isinstance(x, list):
            return [blank(v) for v in x]
        return x

    digest = hashlib.sha256(json.dumps(blank(dump), sort_keys=True)
                            .encode()).hexdigest()
    return digest, dump["dither_every"], [t["autofocus_first"]
                                          for t in dump["targets"]]


def _node(i: str, kind: str, **params) -> FlowNode:
    return FlowNode(id=i, type=kind, x=0.0, y=0.0, params=params)


#: The plan digest of two hand-drawn flows, TAKEN ON THE TREE BEFORE WP 4.1a
#: (the 4.1b head, ``git archive``d to a scratch directory and run there): one
#: with no AUTOFOCUS node and a default GUIDE node, one with an AUTOFOCUS node
#: and a GUIDE node whose dither is 7. They were one digest then, because
#: neither the node's absence nor its dither reached the plan, and they must
#: stay it: the chips are explicit settings only the quick sheet writes, and a
#: flow without them compiles to the plan it always did.
HAND_DRAWN_PLAN_SHA256 = (
    "d99c49910d2926c5d3f3d4215b651e4d55d028ac148baf05c8a63699a9d1fad1")


def _hand_drawn(*, autofocus: bool, dither: int | None) -> FlowGraph:
    where = dict(name="M16", ra="18h 18m 48s", dec="-13 49 00")
    lane = ["d", "t"] + (["a"] if autofocus else []) + ["g", "c"]
    nodes = [_node("d", "dusk"), _node("t", "target", **where),
             _node("g", "guide", **({} if dither is None
                                    else {"dither": dither})),
             _node("c", "capture", filter="Ha", exposure=60, gain=100,
                   bin="1", count=5, goal=0), _node("r", "report")]
    if autofocus:
        nodes.append(_node("a", "autofocus"))
    ports = {("d", "t"): ("window", "arm"), ("t", "a"): ("target", "run"),
             ("t", "g"): ("target", "run"), ("a", "g"): ("focused", "run"),
             ("g", "c"): ("guiding", "run")}
    edges = [_edge(a, ports[(a, b)][0], b, ports[(a, b)][1])
             for a, b in zip(lane, lane[1:])]
    edges.append(_edge("c", "complete", "r", "session"))
    return FlowGraph(nodes=nodes, edges=edges)


class TestAFlowTheSheetDidNotWriteCompilesAsItAlwaysDid:
    @pytest.mark.parametrize("autofocus, dither", [(False, None), (True, 7)],
                             ids=["no-af-default-guide", "af-and-dither-7"])
    def test_a_hand_drawn_flow_has_the_plan_it_had_before(self, autofocus,
                                                          dither):
        """The absence of an AUTOFOCUS node and the GUIDE node's ``dither``
        are not choices: the engine focused with or without the node and the
        plan dithers at its own default, whatever the node holds. Reading
        either would change every saved flow without a word.

        RED if the compile reads the absent node as 'off' or the node's
        dither as the cadence: the digest moves (and the field with it)."""
        digest, every, focus = _plan_digest(
            _hand_drawn(autofocus=autofocus, dither=dither))
        assert digest == HAND_DRAWN_PLAN_SHA256, (digest, every, focus)
        assert every == 3 and focus == [True]

    def test_the_sheets_flow_says_its_chips_in_its_settings_only(self):
        """The explicit way in: the quick flow carries ``quickAutofocus`` and
        ``quickDitherEvery``, and only a flow that carries them is read."""
        graph = _quick(autofocus=False, dither_every=5)
        assert graph.settings == {"quickAutofocus": False,
                                  "quickDitherEvery": 5}
        plan, _ = _compile(graph)
        assert plan.dither_every == 5
        assert all(not t.autofocus_first for t in plan.targets)
        bare = _hand_drawn(autofocus=False, dither=None)
        bare.settings["quickAutofocus"] = False
        _digest, _every, focus = _plan_digest(bare)
        assert focus == [False], "the explicit setting is honoured"

    def test_a_setting_that_is_not_a_choice_reads_as_none(self):
        """Only a real False turns autofocus off and only a whole number of
        frames is a cadence; text, a negative or a true reads as absent."""
        for key, junk in (("quickAutofocus", "no"), ("quickAutofocus", 0),
                          ("quickAutofocus", None), ("quickDitherEvery", "5"),
                          ("quickDitherEvery", -1), ("quickDitherEvery", True),
                          ("quickDitherEvery", 2.5)):
            g = _hand_drawn(autofocus=False, dither=None)
            g.settings[key] = junk
            assert _plan_digest(g)[0] == HAND_DRAWN_PLAN_SHA256, (key, junk)


class TestADarksSkipFundsNothing:
    @pytest.mark.parametrize("darks, lanes", [("Skip", False),
                                              ("If library stale", True),
                                              ("Always", True)])
    def test_a_queue_that_asks_for_no_darks_funds_no_darks_lane(
            self, darks, lanes):
        """A queue with Darks set to Skip must not top the library up on a
        cloud hold or at the end of the night because it has a quota.

        RED if ``plan_extras`` funds on the quota alone: Skip funds both."""
        plan, _ = _compile(with_darks_chip(_quick(), darks=darks))
        assert (plan.day_darks > 0) is lanes, (darks, plan.day_darks)
        assert (plan.cloud_hold_darks > 0) is lanes, (
            darks, plan.cloud_hold_darks)
        extras = plan_extras(_queue("skip"))
        assert "day_darks" not in extras and "cloud_hold_darks" not in extras


class TestCloudHoldChip:
    def test_the_chip_reaches_no_rule_and_its_row_says_so(self):
        """CLOUD HOLD: a quick flow draws no CLOUD WATCH or HOLD / RESUME, so
        the compiled plan has no cloud rule, whatever the sheet's chip says
        (the payload carries no answer for it). The capability row says the
        engine does not keep it from the sheet.

        RED the day the flow carries the rule: this assertion goes with the
        row, which flips to capable."""
        graph = _quick()
        assert {n.type for n in graph.nodes}.isdisjoint(
            {"cloudwatch", "holdresume"})
        plan, _ = _compile(graph)
        triggers = {i.trigger for i in plan.instructions}
        assert triggers.isdisjoint({"on_clouds_in", "on_clouds_clear"}), (
            triggers)
        row = next(c for c in nodes.AUTOMATION_CHIPS if c.key == "cloud")
        assert row.engine_capable is False
        assert "cloud" not in inspect.signature(quick).parameters

    async def test_the_rule_the_chip_does_not_draw_is_one_the_engine_runs(
            self, sim_hub, monkeypatch):
        """The control, and the shape a fix must emit: a flow that DOES draw
        the cloud rule (the wizard's cloud-dodge) compiles to the hold rule,
        and a cloudy verdict on the sim runs the engine's self-releasing
        hold. So the engine keeps the rule; it is the sheet that never sends
        it."""
        graph = generate(KIND_DEEP_SKY, [OPT_CLOUD_DODGE], "M31", coords=_COORDS)
        plan, _ = _compile(graph)
        assert any(i.trigger == "on_clouds_in" for i in plan.instructions)
        eng = SequenceEngine(sim_hub)
        _focus_recorder(eng, monkeypatch)
        holds: list[str] = []

        async def hold(reason, target):
            holds.append(reason)

        monkeypatch.setattr(eng, "_hold_for_clear", hold)
        monkeypatch.setattr(eng._clouds, "cloudy", lambda now: True)
        eng.start(_runnable(plan, count=2))
        await _finish(eng)
        assert holds, "a cloudy verdict never ran the hold the plan carries"


class TestLiveStackChip:
    def test_the_chip_reaches_nothing_the_flow_runs(self):
        """LIVE STACK is the rig's, not a stage of the flow: the plan has no
        field for it and the quick answers carry none. The capability row says
        so."""
        assert not [f for f in SequencePlan.model_fields if "stack" in f]
        assert "stack" not in inspect.signature(quick).parameters
        row = next(c for c in nodes.AUTOMATION_CHIPS if c.key == "stack")
        assert row.engine_capable is False


class TestDarksAfterChip:
    @pytest.mark.parametrize("darks, shot", [("Always", 3),
                                             ("If library stale", 0)])
    async def test_the_chip_funds_the_end_of_night_darks_by_its_policy(
            self, sim_hub, monkeypatch, darks, shot):
        """DARKS AFTER: the queue wired to 'target done' funds the end-of-night
        lane and carries its policy. On a night that ends normally the engine
        takes the quota with Always, and with the library full and the
        automatic policy takes none (#818, #1219).

        RED if the policy is dropped: Always takes none."""
        plan, _ = _compile(with_darks_chip(_quick(guided=False), darks=darks))
        assert plan.day_darks > 0
        assert plan.day_darks_always is (darks == "Always")
        run = _runnable(plan, count=2).model_copy(update={"day_darks": 3})
        eng = SequenceEngine(sim_hub)
        _focus_recorder(eng, monkeypatch)
        darks_shot: list[str] = []
        real = sim_hub.capture

        async def capture(exposure_s, gain, offset, binning=1, **kw):
            if kw.get("frame_type") == "Dark":
                darks_shot.append("dark")
            return await real(exposure_s, gain, offset, binning, **kw)

        monkeypatch.setattr(sim_hub, "capture", capture)
        monkeypatch.setattr(eng, "_hold_darks_shortfall",
                            lambda step, quota: (0, 40))
        eng.start(run)
        await _finish(eng)
        assert len(darks_shot) == shot, darks_shot


def _stage_alone(night, _shots) -> None:
    """Hold the flats stage to itself: where the engine opens the roof and the
    dust cover before the first slew (``_open_the_observatory``, ahead of this
    stage), that step is stubbed, so the test holds on a tree with and without
    it and never depends on the stage being the one that opens the cover."""
    if hasattr(night.engine, "_open_the_observatory"):
        async def opened(*args, **kwargs):
            return None
        night.mp.setattr(night.engine, "_open_the_observatory", opened)


class TestDuskFlatsChip:
    async def test_the_chip_compiles_to_sky_flats_and_the_engine_takes_them(
            self, group_store, monkeypatch):
        """DUSK FLATS: ticking the chip creates the block with the first
        method the engine runs, the twilight sky; the compile carries it as a
        sky block over the node's Sun window; and on the clocked simulator the
        stage waits for the window and shoots the count on each filter (#843,
        #603 job C).

        RED if the chip creates the lens cap again: the compile carries a
        ``cap`` block, which the engine does not run, and no flat is shot."""
        plan, notes = _compile(with_flats_chip(_quick()))
        assert plan.dusk_flats is not None
        assert plan.dusk_flats.method == "sky"
        assert (plan.dusk_flats.window_hi_deg, plan.dusk_flats.window_lo_deg
                ) == (-2.0, -8.0)
        assert plan.dusk_flats.filters == ["L", "R"]
        assert notes["automation.dusk_flats"]["level"] == "note"
        run = plan.model_copy(update={
            "dusk_flats": plan.dusk_flats.model_copy(update={
                "count": 6, "adu_target": 8000}),
            "guide": False, "dither_every": 0, "meridian_flip": False,
            "park_when_done": False, "warm_cooler_when_done": False})
        h, popped = await night_hub(monkeypatch)
        try:
            for dev in popped:
                if getattr(dev, "kind", "") == "filterwheel":
                    h.devices["filterwheel"] = dev
            t_hi = _hi_crossing()
            _night_run, shots = await _night(
                h, monkeypatch, run, t0=t_hi - 600.0, model=SKY_MODEL,
                before_run=_stage_alone)
            flats = shots.saved_flats()
            assert len(flats) == 12, [c["exposure_s"] for c in flats]
            assert flats[0]["t"] >= t_hi
        finally:
            await close_night_hub(h, popped)
