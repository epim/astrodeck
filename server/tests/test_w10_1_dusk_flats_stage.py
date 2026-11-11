# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A flow's DUSK FLATS node is named in live stage status (#1231, WP 10.1
part c).

DUSK FLATS compiles to a plan-level block, ``SequencePlan.dusk_flats``, with no
step and no canvas node id, so nothing carried its name and the canvas showed no
word for it through a run, however long the flats took. The rule (design.md,
10.1c, with the coordinator's decision on open question 9):

* the plan carries the node's id, ``DuskFlatsPlan.node``, excluded from every
  dump so no stored plan, plan response or plan hash moves;
* the session record carries it too (``Session.flow_dusk_flats``), so a resumed
  night names the node again; a file that has none names nothing and is not
  rewritten by loading it;
* the engine publishes ``running`` while the stage is shot and an honest final
  word. ``SequenceEngine._dusk_flats`` returns what it did, from every exit,
  because it reports its own failures without raising and a frame-count delta
  cannot tell "nothing was owed" from "it failed and shot nothing":

      shot                   `done`     (a set was shot)
      nothing owed           `done`     (no block, tonight's latch, the library
                                         already held every set)
      not possible tonight   `skipped`  (the lens cap, no panel, no camera, a
                                         light already taken, no site, a Sun
                                         window that is not tonight's, a refusal,
                                         a sky too dark for the first set)
      failed                 `failed`   (an exception out of the stage, a set
                                         the exposure would not meter)

  The word is a function of the return value and nothing else, and the return
  value changes nothing the stage does or logs (the existing dusk-flats tests,
  which are not edited, hold that).

Site: made up, wherever a test needs one. No test reads the Sun at a real
site, a target's window or the PC's zone.

MUTANTS, each from a byte backup of the file named:

  M1  ``engine.py``: every exit of ``_dusk_flats`` maps to ``done`` (the word
      table says `done` for every outcome): the stage that failed and shot
      nothing, and the one that was not possible, read `done`.
  M2  ``session.py``: ``note_stages`` records no ``flow_dusk_flats``: the
      resumed night names no DUSK FLATS node.
  M3  ``engine.py``: ``_lane_node_ids`` leaves the node out, so the stage
      refresh and the terminal settle read it off the steps it does not own
      (the `skipped` of a stage that was not possible turns `done`).
  M4  ``engine.py``: the unmetered count no longer makes the outcome `failed`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.sequence import SequencePlan
from astrodeck.sequence.engine import SafetyAbort, SequenceEngine
from astrodeck.sequence.models import DuskFlatsPlan
from astrodeck.sequence.session import Session, SessionFrame, session_store

from test_1231_resumed_night_names_its_stages import (  # noqa: F401
    Rig, rig, staged_plan, store, stored_file, wait_for)
from astrodeck.calibration.library import CalibrationLibrary
from test_w17_dusk_flats_panel import (  # noqa: F401  (sim_hub is a fixture)
    _flats, _lights, _plan, _stamp_with_the_clock, sim_hub)
from test_w4_1b_sky_flats import (  # noqa: F401  (sky_hub is a fixture)
    SKY_MODEL, _hi_crossing, _night as _sky_night, _plan as _sky_plan,
    sky_hub)
from test_w4_2_stage_status import transitions
from _group_harness import T0, group_store  # noqa: F401

NODE = "nF"
LIGHT_STAGE = "n6"
SHOT, NOTHING_OWED = "shot", "nothing owed"
NOT_TONIGHT, FAILED = "not possible tonight", "failed"
WHEN = 1788313689.0


def flats_flow(method: str = "Flat panel",
               filters: str = "Tonight's plan only") -> dict:
    """A DUSK FLATS node armed ahead of a TARGET, one CAPTURE LOOP and a
    REPORT. Goal 0 so the compile has no estimate to warn about."""
    return {
        "nodes": [
            {"id": NODE, "type": "duskflats", "x": 30, "y": 0,
             "params": {"method": method,
                        "window": "Sun 0\u00b0 \u2026 \u22126\u00b0",
                        "filters": filters, "adu": 8000, "count": 2}},
            {"id": "n2", "type": "target", "x": 30, "y": 60,
             "params": {"name": "M31", "ra": "00h 42m 44s",
                        "dec": "+41 16 09"}},
            {"id": LIGHT_STAGE, "type": "capture", "x": 990, "y": 60,
             "params": {"filter": "L", "exposure": 10, "count": 2,
                        "goal": 0}},
            {"id": "n12", "type": "report", "x": 1230, "y": 60, "params": {}},
        ],
        "edges": [
            {"id": "e0", "from": NODE, "fromPort": "done", "to": "n2",
             "toPort": "arm"},
            {"id": "e1", "from": "n2", "fromPort": "target",
             "to": LIGHT_STAGE, "toPort": "run"},
            {"id": "e5", "from": LIGHT_STAGE, "fromPort": "complete",
             "to": "n12", "toPort": "session"},
        ],
        "settings": {},
    }


def compiled_plan(graph: dict, *, with_graph: bool = True,
                  flow_id: str = "f-flats") -> SequencePlan:
    g = FlowGraph.model_validate(graph)
    plan, _ = to_sequence_plan(compile_plan(g, "x"), g if with_graph else None,
                               flow_id=flow_id, when=WHEN)
    return plan


# ------------------------------------------------------------ the compile

def test_the_compile_puts_the_node_id_on_the_plan():
    """The DUSK FLATS node's canvas id reaches ``DuskFlatsPlan.node``.

    RED on the unchanged compile, observed:

        AttributeError: 'DuskFlatsPlan' object has no attribute 'node'
    """
    plan = compiled_plan(flats_flow())
    assert plan.dusk_flats is not None, "premise: the panel block compiles"
    assert plan.dusk_flats.node == NODE
    sky = compiled_plan(flats_flow("Twilight sky", "All in wheel"))
    assert sky.dusk_flats is not None and sky.dusk_flats.node == NODE


def test_a_plan_compiled_without_its_graph_names_no_node():
    """The graph is optional to ``to_sequence_plan`` (a preview has none) and
    is the only place a node id is: without it the plan names nothing, as it
    names no lane and no block."""
    plan = compiled_plan(flats_flow(), with_graph=False)
    assert plan.dusk_flats is not None
    assert plan.dusk_flats.node is None


def test_the_node_stays_out_of_every_dump():
    """The id rides the plan the engine runs and nothing else: the dump is the
    dump of the same plan with the id cleared, so no stored plan, plan
    response or plan hash moves."""
    plan = compiled_plan(flats_flow())
    assert plan.dusk_flats is not None and plan.dusk_flats.node == NODE, (
        "premise: the plan names the node")
    dumped = plan.model_dump(mode="json")
    text = plan.model_dump_json()
    bare = plan.model_copy(deep=True)
    bare.dusk_flats.node = None
    assert dumped == bare.model_dump(mode="json")
    assert text == bare.model_dump_json()
    assert "node" not in dumped["dusk_flats"]


#: Taken on the tree BEFORE this part was written (main at 1d522721): the
#: sha256 of the canonical JSON (sorted keys, instruction ids blanked, floats
#: rounded to 9 decimals, no negative zero, as test_w6_1_rule14_pin.py does) of
#: ``compile_plan`` and of ``to_sequence_plan(..., flow_id="f-flats",
#: when=WHEN)`` for the two flows below. Rule 14: a flow with a DUSK FLATS
#: node compiles to the plan it compiled to before the node was named.
PINS = {
    "panel": ("6b9c16aaf458decd05c95638a8461009c4b8507e5d0aa7b793c316491daf885b",
              "7e6decbdf187705b2cc3ff9bd7a4ea48b314b50621b9a4705b436f62bcc6dbd7"),
    "sky_all": ("a446894b6aeaf056898cabca8f40e9e1f31a37a3a9c74073cc55d39a7e190959",
                "d8e3f5bf10a5a945a62825e17a04cf1b3712cf5e004b6eacd708e1a371bcf8a4"),
}


def _rounded(value):
    if isinstance(value, float):
        return round(value, 9) + 0.0
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    return value


def _hash(obj) -> str:
    return hashlib.sha256(
        json.dumps(_rounded(obj), sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("name,graph", [
    ("panel", flats_flow()),
    ("sky_all", flats_flow("Twilight sky", "All in wheel"))])
def test_the_compile_and_the_plan_hash_did_not_move(name, graph):
    g = FlowGraph.model_validate(graph)
    compiled = compile_plan(g, "x")
    plan, _ = to_sequence_plan(compiled, g, flow_id="f-flats", when=WHEN)
    assert plan.dusk_flats is not None and plan.dusk_flats.node == NODE, (
        "premise: the plan names the node, so the pin is taken over it")
    dumped = plan.model_dump(mode="json")
    for ins in dumped.get("instructions", []):
        ins["id"] = ""
    assert (_hash(compiled), _hash(dumped)) == PINS[name]


# ----------------------------------------------------------- the session

def flats_plan(name: str = "Flatted", node: str | None = NODE) -> SequencePlan:
    plan = staged_plan(name, 3)
    plan.dusk_flats = DuskFlatsPlan(method="panel", filters=["L"],
                                    adu_target=8000, count=2, node=node)
    return plan


def test_the_session_file_carries_the_node_and_the_plan_dump_does_not(store):
    """The id is written beside the plan from the plan being saved, and the
    plan inside the file is exactly what it was before any id existed.

    RED under M2 (``note_stages`` records no ``flow_dusk_flats``), observed:

        AssertionError: assert None == 'nF'
    """
    s = Session(name="n", created_ts=1.0, status="dormant", plan=flats_plan())
    store.save(s)
    raw = stored_file(s.id)
    assert raw["flow_dusk_flats"] == NODE
    assert "node" not in raw["plan"]["dusk_flats"]


def test_a_stored_session_is_built_with_its_plan_naming_the_node(store):
    s = Session(name="n", created_ts=1.0, status="dormant", plan=flats_plan())
    store.save(s)
    assert store.load(s.id).plan.dusk_flats.node == NODE


def test_hand_edited_values_cannot_make_the_session_unreadable(store):
    """A value that is not a node id is ignored, as a hand edit of
    ``flow_blocks`` is: a file that fails validation vanishes from every
    scan, ledger and all."""
    s = Session(name="n", created_ts=1.0, status="dormant", plan=flats_plan())
    store.save(s)
    for bad in (7, ["nF"], {"id": "nF"}, "", None, True):
        raw = stored_file(s.id)
        raw["flow_dusk_flats"] = bad
        store._path(s.id).write_text(json.dumps(raw), encoding="utf-8")
        loaded = store.load(s.id)
        assert loaded is not None, bad
        assert loaded.plan.dusk_flats.node is None, bad


def test_a_record_for_a_plan_with_no_flats_block_is_ignored(store):
    """The plan is the truth: an id recorded for a plan that carries no DUSK
    FLATS block has nothing to name."""
    plan = staged_plan("Plain", 3)
    s = Session(name="n", created_ts=1.0, status="dormant", plan=plan)
    store.save(s)
    raw = stored_file(s.id)
    raw["flow_dusk_flats"] = NODE
    store._path(s.id).write_text(json.dumps(raw), encoding="utf-8")
    loaded = store.load(s.id)
    assert loaded is not None and loaded.plan.dusk_flats is None


def test_a_plan_replaced_by_one_with_no_node_forgets_the_node(store):
    """The Plan editor's PATCH replaces the stored plan with one no flow
    compiled: the record is rewritten from the plan it now holds."""
    s = Session(name="n", created_ts=1.0, status="dormant", plan=flats_plan())
    store.save(s)
    held = store.load(s.id)
    held.plan = flats_plan(node=None)
    store.save(held)
    assert stored_file(s.id)["flow_dusk_flats"] is None
    assert store.load(s.id).plan.dusk_flats.node is None


@pytest.mark.parametrize("written_by", ["part b", "0.3.49"])
def test_an_older_session_file_loads_and_is_not_rewritten(store, written_by):
    """R: a file written before this part has no ``flow_dusk_flats``: one from
    part b has ``flow_lanes`` and the stage and block records, one from 0.3.49
    has the stage and block records only. Each loads, its plan names its stage
    and block as before and no DUSK FLATS node, reading it rewrites nothing,
    and saving it writes the new key as null.

    RED if loading demanded the key, or rewrote the file, or the new key broke
    the names the old record carries."""
    s = Session(name="n", created_ts=1.0, status="dormant", plan=flats_plan())
    store.save(s)
    raw = stored_file(s.id)
    del raw["flow_dusk_flats"]
    if written_by == "0.3.49":
        del raw["flow_lanes"]
    path = store._path(s.id)
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()

    loaded = store.load(s.id)
    assert loaded is not None
    assert loaded.plan.dusk_flats is not None
    assert loaded.plan.dusk_flats.node is None
    assert loaded.plan.targets[0].block == s.plan.targets[0].block
    assert {x.stage for x in loaded.plan.targets[0].steps} == {
        x.stage for x in s.plan.targets[0].steps}
    assert store.load_all() and path.read_bytes() == before, (
        "reading an older session must not rewrite it")

    store.save(loaded)
    after = stored_file(s.id)
    assert after["flow_dusk_flats"] is None
    assert after["flow_blocks"] == raw["flow_blocks"]
    assert after["flow_stages"] == raw["flow_stages"]


async def test_a_resumed_night_names_the_node(rig):
    """Night one runs a plan with a DUSK FLATS block and is stopped by hand
    after its first light; night two is ResumeArm's auto-resume of the stored
    session on a fresh engine. Night two publishes the node too. A light frame
    has been taken tonight, so the flats are not possible and the node reads
    `skipped`, the word the stage's own log line uses.

    RED under M2 (``note_stages`` records no ``flow_dusk_flats``), observed:

        AssertionError: the resumed night named no DUSK FLATS node: {...}
    """
    sid, first = await rig.first_night(flats_plan())
    assert first.get(NODE) == "done", (
        f"premise: night one shot its flats and names the node: {first}")
    engine = rig.next_engine()
    await rig.resume_arm(engine).tick()
    assert await wait_for(
        lambda: session_store.load(sid).status == "complete"), (
        "premise: night two ran the plan to its end")
    assert await wait_for(lambda: not engine.running)
    stages = engine.state.get("stages") or {}
    assert NODE in stages, (
        f"the resumed night named no DUSK FLATS node: {stages}")
    assert stages[NODE] == "skipped", stages


# ------------------------------------------- what the stage returns, by exit

def spy_outcomes(eng: SequenceEngine, monkeypatch) -> list:
    """The values ``_dusk_flats`` returns to the run, one per call. The stage
    itself is the real one."""
    real = eng._dusk_flats
    seen: list = []

    async def spy():
        out = await real()
        seen.append(out)
        return out

    monkeypatch.setattr(eng, "_dusk_flats", spy)
    return seen


async def outcomes_of(hub, plan, monkeypatch, *, engine=None) -> list:
    """``plan`` run to its end on the simulator with the scheduler stubbed to a
    no-op (the first light is not the question) and the run start's open of
    the observatory stubbed out (the stage meets the cover as it was left):
    what the stage returned."""
    eng = engine or SequenceEngine(hub)

    async def scheduled(_plan):
        return None

    async def nothing():
        return None

    monkeypatch.setattr(eng, "_run_scheduled", scheduled)
    monkeypatch.setattr(eng, "_open_the_observatory", nothing)
    seen = spy_outcomes(eng, monkeypatch)
    eng.start(plan)
    await eng._task
    return seen


async def test_a_run_that_shoots_the_flats_reports_shot(sim_hub, monkeypatch):
    """Exit: the stage ran to its end and shot a set.

    RED on the unchanged stage, observed:

        AssertionError: assert [None] == ['shot']
    """
    assert await outcomes_of(sim_hub, _plan(_flats()), monkeypatch) == [SHOT]


async def test_a_plan_with_no_block_has_nothing_owed(sim_hub, monkeypatch):
    """Exit: ``df is None``."""
    assert await outcomes_of(sim_hub, _plan(None), monkeypatch) == [
        NOTHING_OWED]


async def test_the_second_run_the_same_night_has_nothing_owed(
        sim_hub, monkeypatch):
    """Exit: the engine's per-night latch. A resume the same night on the same
    engine owes nothing."""
    eng = SequenceEngine(sim_hub)
    first = await outcomes_of(sim_hub, _plan(_flats()), monkeypatch,
                              engine=eng)
    again = await outcomes_of(sim_hub, _plan(_flats()), monkeypatch,
                              engine=eng)
    assert first[0] == SHOT, first
    assert again == [NOTHING_OWED], again


async def test_a_library_that_holds_every_set_has_nothing_owed(
        sim_hub, tmp_path, monkeypatch):
    """Exit: every job was fresh in the library, so nothing was shot and
    nothing was left undone."""
    monkeypatch.setattr(sim_hub, "master_library",
                        CalibrationLibrary(lambda: tmp_path), raising=False)
    assert await outcomes_of(sim_hub, _plan(_flats()), monkeypatch) == [SHOT]
    _stamp_with_the_clock(tmp_path)
    assert await outcomes_of(sim_hub, _plan(_flats()), monkeypatch,
                             engine=SequenceEngine(sim_hub)) == [NOTHING_OWED]


async def test_the_lens_cap_is_not_possible_tonight(sim_hub, monkeypatch):
    """Exit: ``df.method == "cap"``, carried and not run."""
    assert await outcomes_of(sim_hub, _plan(_flats(method="cap")),
                             monkeypatch) == [NOT_TONIGHT]


async def test_no_flat_panel_is_not_possible_tonight(sim_hub, monkeypatch):
    """Exit: no flat source for the panel method."""
    del sim_hub.devices["covercalibrator"]
    assert await outcomes_of(sim_hub, _plan(_flats()), monkeypatch) == [
        NOT_TONIGHT]


async def test_no_camera_is_not_possible_tonight(sim_hub, monkeypatch):
    """Exit: no camera is connected. A run needs one to start, so the stage is
    asked directly."""
    eng = SequenceEngine(sim_hub)
    eng.plan = _plan(_flats())
    del sim_hub.devices["camera"]
    assert await eng._dusk_flats() == NOT_TONIGHT


async def test_a_light_already_taken_is_not_possible_tonight(
        sim_hub, monkeypatch):
    """Exit: a light frame has been taken tonight, and the flats come before
    the first one."""
    plan = _plan(_flats())
    eng = SequenceEngine(sim_hub)
    eng.plan = plan

    class _Session:
        frames = [SessionFrame(ts=time.time(),
                               step_id=plan.targets[0].steps[0].id)]

    eng._session = _Session()
    assert await eng._dusk_flats() == NOT_TONIGHT


async def test_an_angle_the_stage_cannot_name_is_not_possible_tonight(
        sim_hub, monkeypatch):
    """Exit: ``_dusk_flat_angles`` refuses (a rotator that is not trusted for
    a plan with several panel angles): no flats are shot and the night goes
    on."""
    eng = SequenceEngine(sim_hub)
    eng.plan = _plan(_flats())
    monkeypatch.setattr(eng, "_dusk_flat_angles",
                        lambda: ([None], "the rotator is not trusted"))
    assert await eng._dusk_flats() == NOT_TONIGHT


async def test_a_wheel_with_none_of_the_filters_is_not_possible_tonight(
        sim_hub, monkeypatch):
    """Exit: the end of the stage with no job to run (every named filter is
    missing from the wheel): nothing could be shot, and nothing was."""
    plan = _plan(_flats(filters=["no such filter"]),
                 lights=_lights(("no such filter",)))
    eng = SequenceEngine(sim_hub)
    eng.plan = plan
    assert await eng._dusk_flats() == NOT_TONIGHT


async def test_an_exception_out_of_the_stage_is_failed(sim_hub, monkeypatch):
    """Exit: the stage's ``except Exception``: the night goes on and the
    stage failed. A failure before the first frame shot nothing, and one after
    a set shot something: both fail.

    RED on the unchanged stage, observed:

        AssertionError: assert [None] == ['failed']
    """
    eng = SequenceEngine(sim_hub)

    async def boom(ti, target):
        raise RuntimeError("camera said no")

    monkeypatch.setattr(eng, "_run_calibration", boom)
    assert await outcomes_of(sim_hub, _plan(_flats()), monkeypatch,
                             engine=eng) == [FAILED]
    late = SequenceEngine(sim_hub)
    real = late._run_calibration
    calls = {"n": 0}

    async def second_set_fails(ti, target):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("camera said no")
        return await real(ti, target)

    monkeypatch.setattr(late, "_run_calibration", second_set_fails)
    assert await outcomes_of(sim_hub, _plan(_flats()), monkeypatch,
                             engine=late) == [FAILED]


async def test_a_set_the_exposure_would_not_meter_is_failed(
        sim_hub, monkeypatch):
    """Exit: sets that were owed and not shot because the exposure would not
    meter (#841), with nothing else shot: the stage failed. A lamp that is
    never lit gives a source that does not brighten with exposure.

    RED under M4 (the unmetered count no longer makes the outcome `failed`),
    observed:

        AssertionError: assert ['not possible tonight'] == ['failed']
    """
    assert await outcomes_of(sim_hub, _plan(_flats(panel_brightness=0)),
                             monkeypatch) == [FAILED]


async def test_a_safety_abort_returns_nothing_and_ends_the_night(
        sim_hub, monkeypatch):
    """A ``SafetyAbort`` is the night's end and propagates: the stage
    returns no outcome and the run is aborted unsafe."""
    eng = SequenceEngine(sim_hub)
    calls = {"n": 0}
    real_gate = eng._safety_gate

    async def gate(*, context, target=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise SafetyAbort("test: rain on the second flat")
        return await real_gate(context=context, target=target)

    monkeypatch.setattr(eng, "_safety_gate", gate)
    assert await outcomes_of(sim_hub, _plan(_flats()), monkeypatch,
                             engine=eng) == []
    assert eng.state.get("end_reason") == "unsafe", eng.state


def a_made_up_site(monkeypatch, answer) -> None:
    """The twilight-sky stage asks ``site_gate.site_lat_lon`` once; ``answer``
    stands in for the saved site (a made-up pair, or None for no site)."""
    import astrodeck.site_gate as site_gate
    monkeypatch.setattr(site_gate, "site_lat_lon", lambda _site: answer)


def sky_plan_now() -> SequencePlan:
    return _plan(_flats(method="sky", window_hi_deg=None, window_lo_deg=None))


async def test_a_sky_stage_with_no_site_is_not_possible_tonight(
        sim_hub, monkeypatch):
    """Exit: ``site_lat_lon`` finds no site."""
    a_made_up_site(monkeypatch, None)
    assert await outcomes_of(sim_hub, sky_plan_now(), monkeypatch) == [
        NOT_TONIGHT]


async def test_a_sun_window_that_is_not_tonights_is_not_possible_tonight(
        sim_hub, monkeypatch):
    """Exit: the wait for the Sun's window says the flats may not start (the
    Sun is already below the window's lower edge, or never reaches the upper
    one)."""
    a_made_up_site(monkeypatch, (10.0, 20.0))
    eng = SequenceEngine(sim_hub)

    async def never(_df):
        return False

    monkeypatch.setattr(eng, "_wait_for_the_sky_flats_window", never)
    assert await outcomes_of(sim_hub, sky_plan_now(), monkeypatch,
                             engine=eng) == [NOT_TONIGHT]


async def test_a_refused_sky_is_not_possible_tonight(sim_hub, monkeypatch):
    """Exit: ``_sky_flats_ready`` refuses (a roof that does not read open, a
    point the gate or the mount's limits refuse)."""
    a_made_up_site(monkeypatch, (10.0, 20.0))
    eng = SequenceEngine(sim_hub)

    async def refused():
        return "the roof does not read open", None, None

    monkeypatch.setattr(eng, "_sky_flats_ready", refused)
    assert await outcomes_of(sim_hub, sky_plan_now(), monkeypatch,
                             engine=eng) == [NOT_TONIGHT]


async def _sky_outcomes(sky_hub, monkeypatch, plan, *, t0, model) -> list:
    """The twilight-sky stage on the clocked night harness: what the stage
    returned."""
    seen: list = []

    def spy(night, _shots):
        eng = night.engine
        real = eng._dusk_flats

        async def wrapped():
            out = await real()
            seen.append(out)
            return out

        monkeypatch.setattr(eng, "_dusk_flats", wrapped)

    await _sky_night(sky_hub, monkeypatch, plan, t0=t0, model=model,
                     before_run=spy)
    return seen


async def test_a_sky_too_dark_for_the_first_set_is_not_possible_tonight(
        sky_hub, monkeypatch):
    """Exit: the sky stage stopped, saying why, with no set shot (the opening
    solve finds the sky too dark for the longest exposure)."""
    plan = _sky_plan(lo=None, hi=None)
    assert await _sky_outcomes(sky_hub, monkeypatch, plan, t0=T0,
                               model=SKY_MODEL) == [NOT_TONIGHT]


async def test_a_sky_that_darkens_under_a_shot_set_reports_shot(
        sky_hub, monkeypatch):
    """Exit: the stage stopped early (the sky went too dark) after shooting
    some of a set: what it shot is shot."""
    plan = _sky_plan(lo=-12.0, count=100)
    out = await _sky_outcomes(sky_hub, monkeypatch, plan,
                              t0=_hi_crossing() - 60.0,
                              model={"r0": 2000.0, "per_deg": 1.0})
    assert out == [SHOT]


# ------------------------------------------------------- the pure mapping

@pytest.mark.parametrize("kw,want", [
    (dict(shot=1), SHOT),
    (dict(shot=2, skipped=1), SHOT),
    (dict(shot=1, stopped="the sky is too dark"), SHOT),
    (dict(skipped=2), NOTHING_OWED),
    (dict(), NOT_TONIGHT),
    (dict(stopped="the sky is too dark"), NOT_TONIGHT),
    (dict(skipped=1, stopped="the Sun has fallen"), NOT_TONIGHT),
    (dict(unmetered=1), FAILED),
    (dict(shot=1, unmetered=1), FAILED),
    (dict(failed=True), FAILED),
    (dict(failed=True, shot=1), FAILED),
])
def test_the_end_of_the_stage_maps_what_it_counted_to_one_outcome(kw, want):
    args = dict(shot=0, skipped=0, unmetered=0, failed=False, stopped="")
    args.update(kw)
    assert SequenceEngine._dusk_flats_outcome(**args) == want


# ------------------------------------------------------ the stream, a night

class Stream:
    """Every ``sequence`` payload the engine publishes that carries
    ``stages``, in order, as the WS client and the snapshot read it."""

    def __init__(self, monkeypatch):
        self.events: list[dict] = []
        real = engine_mod.bus.publish

        def publish(topic, **payload):
            if topic == "sequence" and "stages" in payload:
                self.events.append({"stages": dict(payload["stages"]),
                                    "state": payload.get("state")})
            return real(topic, **payload)

        monkeypatch.setattr(engine_mod.bus, "publish", publish)

    def moves(self) -> list[tuple[str, str]]:
        return transitions(self.events)

    def words_of(self, node: str) -> list[str]:
        return [w for n, w in self.moves() if n == node]


def night_plan(method: str = "Flat panel") -> SequencePlan:
    """The compiled flow of ``flats_flow`` made safe to run on the simulator:
    no guide, no park, no flip."""
    plan = compiled_plan(flats_flow(method))
    plan.guide = False
    plan.meridian_flip = False
    plan.park_when_done = False
    plan.warm_cooler_when_done = False
    plan.dither_every = 0
    return plan


async def run_with_first_light(hub, plan, monkeypatch, *, setup=None,
                               engine=None):
    """``plan`` through ``engine.start`` with the scheduler stubbed to enter
    the first light's stage, the way the real one does, and stop: the stream
    then shows what the node said before the first light."""
    eng = engine or SequenceEngine(hub)
    stream = Stream(monkeypatch)
    if setup is not None:
        setup(eng)

    async def scheduled(p):
        target = p.targets[0]
        eng._say_stages(target, target.steps[0])

    async def nothing():
        return None

    monkeypatch.setattr(eng, "_run_scheduled", scheduled)
    monkeypatch.setattr(eng, "_open_the_observatory", nothing)
    eng.start(plan)
    await eng._task
    return eng, stream


async def test_a_panel_night_names_the_node_running_then_done_before_the_first_light(
        sim_hub, monkeypatch):
    """The compiled flow's DUSK FLATS node is `running` while the flats are
    shot and `done` before the first light's stage is `running`, and the
    terminal payload still names it `done`.

    RED on the unchanged engine, observed:

        AssertionError: the node was never named: [('n6', 'running'), ...]
    """
    eng, stream = await run_with_first_light(sim_hub, night_plan(),
                                             monkeypatch)
    moves = stream.moves()
    assert (NODE, "running") in moves, f"the node was never named: {moves}"
    at = [moves.index(p) for p in ((NODE, "running"), (NODE, "done"),
                                   (LIGHT_STAGE, "running"))]
    assert at == sorted(at), f"out of order, {at}: {moves}"
    assert stream.words_of(NODE) == ["running", "done"], stream.words_of(NODE)
    assert eng.state["stages"][NODE] == "done"
    assert stream.events[-1]["state"] != "running", "premise: the terminal"
    assert stream.events[-1]["stages"][NODE] == "done"


async def test_a_stage_with_nothing_owed_reads_done(sim_hub, monkeypatch):
    """The engine's latch: a second run the same night, on the same engine,
    owes no flats, and the node reads `done` (nothing was owed is not a
    stage that was skipped: skipped draws amber).

    RED under the own mutant "nothing owed reads skipped" (the word table
    saying `skipped` for it), observed:

        AssertionError: ['running', 'skipped']
    """
    eng = SequenceEngine(sim_hub)
    await run_with_first_light(sim_hub, night_plan(), monkeypatch,
                               engine=eng)
    _eng, again = await run_with_first_light(sim_hub, night_plan(),
                                             monkeypatch, engine=eng)
    assert again.words_of(NODE) == ["running", "done"], again.words_of(NODE)
    assert again.events[-1]["stages"][NODE] == "done"


async def test_a_night_that_cannot_shoot_reads_skipped_to_the_end(
        sim_hub, monkeypatch):
    """No flat panel: the stage says why in its log and the node reads
    `skipped`, and stays `skipped` through the first light's stage refresh and
    the terminal settle (the node owns no step, so nothing reads it as
    `done`).

    RED under M3 (the node left out of ``_lane_node_ids``), observed:

        AssertionError: ['running', 'skipped', 'done']
    """
    del sim_hub.devices["covercalibrator"]
    eng, stream = await run_with_first_light(sim_hub, night_plan(),
                                             monkeypatch)
    assert stream.words_of(NODE) == ["running", "skipped"], (
        stream.words_of(NODE))
    assert eng.state["stages"][NODE] == "skipped"
    assert stream.events[-1]["stages"][NODE] == "skipped"


async def test_a_stage_that_failed_and_shot_nothing_reads_failed(
        sim_hub, monkeypatch):
    """The stage's own failure does not raise: the night goes on to its
    lights. The node says `failed`, and keeps it to the terminal payload.

    RED under M1 (every exit maps to `done`), observed:

        AssertionError: ['running', 'done'] == ['running', 'failed']
    """
    def broken(eng):
        def boom(df):
            raise KeyError("wheel")

        eng._dusk_flat_recipes = boom

    eng, stream = await run_with_first_light(sim_hub, night_plan(),
                                             monkeypatch, setup=broken)
    assert stream.words_of(NODE) == ["running", "failed"], (
        stream.words_of(NODE))
    assert (LIGHT_STAGE, "running") in stream.moves(), (
        "premise: the night went on to its first light")
    assert stream.events[-1]["stages"][NODE] == "failed"


async def test_a_run_cut_off_mid_flats_settles_the_node_deferred(
        sim_hub, monkeypatch):
    """The run is aborted by hand while the flats are being shot: the node was
    cut off mid-action, so it reads `deferred` in the terminal payload, never
    the `running` it was last published as."""
    eng = SequenceEngine(sim_hub)
    stream = Stream(monkeypatch)
    plan = night_plan()

    async def held(ti, target):
        # the abort is the operator's, from outside the run's own task
        asyncio.ensure_future(eng.abort())
        await asyncio.sleep(30)

    monkeypatch.setattr(eng, "_run_calibration", held)
    eng.start(plan)
    assert await wait_for(lambda: eng._task.done(), timeout=20.0)
    assert not eng.running
    last = stream.events[-1]["stages"] if stream.events else {}
    assert last.get(NODE) == "deferred", (last, stream.moves())
    assert "running" not in last.values(), last


async def test_a_plan_that_names_no_node_publishes_no_stages(
        sim_hub, monkeypatch):
    """A plan no flow compiled (the Plan editor's) has a flats block and no
    node id: the flats run as they did and no ``stages`` key is published."""
    stream = Stream(monkeypatch)
    plan = _plan(_flats())
    assert plan.dusk_flats.node is None
    seen = await outcomes_of(sim_hub, plan, monkeypatch)
    assert seen == [SHOT]
    assert stream.events == [], stream.events
